"""Deterministic C++ AST/semantic mutation testing for problem test-data strength checks.

Generates targeted competitive-programming semantic and boundary mutations on
``solutions/sol.cpp`` (using optional ``tree-sitter-cpp`` CST when installed and a
pure-Python fallback otherwise), filters deterministic equivalent mutants, hooks
into optional ``mull-runner`` when available, compiles each mutant inside an isolated
temporary directory (never touching ``problem_dir``), and verifies whether the test
dataset kills every compiled mutant.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .. import TEMPLATES_DIR
from ..workflow import (
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
)
from .checker_judge import checker_exe_path, run_testlib_checker
from .communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_grader_source_existing,
    safe_resolve_under_root,
)
from .compiler import compile_cpp, resolve_effective_compiler, run_binary
from .judge_runner import InteractiveRunner
from .platform import get_exe_extension
from .problem_layout import discover_test_pairs
from .process import terminate_pid_tree

_MUTATION_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("relational_le_to_lt", re.compile(r"(?<![<>!=])<=(?![=>])"), "<"),
    ("relational_ge_to_gt", re.compile(r"(?<![<>!=])>=(?!=)"), ">"),
    ("equality_eq_to_ne", re.compile(r"(?<![<>!=])==(?!=)"), "!="),
    ("equality_ne_to_eq", re.compile(r"!=(?!=)"), "=="),
    ("off_by_one_plus1", re.compile(r"(?<![+eE])\+\s*1(?![.\d])\b"), "+ 0"),
    ("off_by_one_minus1", re.compile(r"(?<![-eE])-\s*1(?![.\d])\b"), "- 0"),
    ("relational_lt_to_le", re.compile(r"(?<=\s)<(?=\s)"), "<="),
    ("relational_gt_to_ge", re.compile(r"(?<=\s)>(?=\s)"), ">="),
]

# Round-robin priority order: key boundary checks + CP semantic operators first,
# then remaining equality/off-by-one rules.
_CATEGORY_PRIORITY: list[str] = [
    "relational_le_to_lt",
    "relational_ge_to_gt",
    "type_narrowing_ll_to_int",
    "mod_removal",
    "loop_bound_off_by_one",
    "init_extreme_swap",
    "priority_queue_comparator_flip",
    "multitest_clear_deletion",
    "off_by_one_plus1",
    "off_by_one_minus1",
    "equality_eq_to_ne",
    "equality_ne_to_eq",
    "relational_lt_to_le",
    "relational_gt_to_ge",
]

_SEMANTIC_KINDS = frozenset(
    {
        "type_narrowing_ll_to_int",
        "mod_removal",
        "loop_bound_off_by_one",
        "init_extreme_swap",
        "priority_queue_comparator_flip",
        "multitest_clear_deletion",
    }
)

_RELATIONAL_KINDS = frozenset(
    {
        "relational_le_to_lt",
        "relational_lt_to_le",
        "relational_ge_to_gt",
        "relational_gt_to_ge",
    }
)

_SKIP_LINE_RE = re.compile(
    r"^\s*(?:#|//|/\*|\*|using\s+namespace\b|struct\b|class\b|enum\b|namespace\b)"
)

_TEMPLATE_PREFIX_RE = re.compile(
    r"\b(?:vector|pair|tuple|set|multiset|map|multimap|unordered_map|unordered_set|"
    r"queue|stack|array|priority_queue|deque|bitset|greater|less|"
    r"static_cast|dynamic_cast|const_cast|reinterpret_cast|function|optional|variant|span)\s*(?:<[^>]*|$)"
)

_IO_LINE_RE = re.compile(r"\b(?:cin|cout|cerr|scanf|printf|ostream|istream)\b")
_LL_DECL_RE = re.compile(r"\b(?:unsigned\s+)?long\s+long(?:\s+int)?\b")
_INT64_DECL_RE = re.compile(r"\b(?:u?int64_t)\b")
_EXTREME_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])("
    r"INT_MAX|INT_MIN|LLONG_MAX|LLONG_MIN|LONG_LONG_MAX|LONG_LONG_MIN|INT64_MAX|INT64_MIN|INT32_MAX|INT32_MIN|"
    r"0x3f3f3f3f3f3f3f3f(?:LL|ll)?|0x3f3f3f3f|0x7fffffff|"
    r"1e18|1e9|1000000000000000000(?:LL|ll)?|1000000000(?:LL|ll)?|1'000'000'000(?:LL|ll)?|"
    r"INF|inf"
    r")(?![A-Za-z0-9_])"
)
_STD_LIMITS_RE = re.compile(
    r"\b(?:std::)?numeric_limits\s*<[^>]+>\s*::\s*(?:max|min|infinity)\s*\(\s*\)"
)
_EXTREME_ZERO_INIT_RE = re.compile(
    r"\b(ans|res|best|mn|mx|min_val|max_val|minv|maxv|dist|cost)\s*=\s*0\b"
)
_PQ_RE = re.compile(r"\b(?:(std)\s*::\s*)?priority_queue\s*<")
_CLEAR_STMT_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\s*\[[^\]]+\]|\.[A-Za-z_]\w*|->[A-Za-z_]\w*)*)\s*\.clear\s*\(\s*\)\s*;")
_MOD_COMPOUND_RE = re.compile(r"%=\s*(\([^()]+\)|[A-Za-z_]\w*|\d+(?:'\d+)*(?:LL|ll|ULL|ull)?)")
_CP_MOD_NAME_RE = re.compile(r"^(?:[A-Za-z_]*mod[A-Za-z0-9_]*|M|P|p)$", re.IGNORECASE)
_IF0_DIRECTIVE_RE = re.compile(r"^\s*#\s*if\s+0\b")
_IF_DIRECTIVE_RE = re.compile(r"^\s*#\s*if(?:def|ndef)?\b")
_ENDIF_DIRECTIVE_RE = re.compile(r"^\s*#\s*endif\b")
_ELSE_DIRECTIVE_RE = re.compile(r"^\s*#\s*(?:else|elif)\b")

_MAX_MUTATION_FILE_BYTES = 16 * 1024 * 1024
_MAX_MUTATION_BUDGET = 64


@dataclass(frozen=True)
class _CandidateEdit:
    start: int
    end: int
    replacement: str
    mutation_type: str
    category: str
    ast_backend: str
    equivalent_reason: str | None = None


def _natural_sort_key(path: Path) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def _is_contained_non_symlink(problem_dir: str, candidate: Path | str) -> Path | None:
    """Return resolved Path strictly inside ``problem_dir`` with no symlink on the target itself."""
    cand_path = Path(candidate)
    if cand_path.is_symlink():
        return None
    try:
        root = Path(problem_dir).resolve()
        abs_cand = cand_path if cand_path.is_absolute() else (root / cand_path)
        rel = abs_cand.relative_to(root) if abs_cand.is_absolute() else cand_path
    except ValueError:
        try:
            root = Path(problem_dir).resolve()
            resolved = cand_path.resolve()
            resolved.relative_to(root)
            rel = resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
    resolved_str = safe_resolve_under_root(str(problem_dir), str(rel))
    if resolved_str is None:
        return None
    return Path(resolved_str)


def _strip_line_comments_and_strings(
    line: str, in_block_comment: bool = False
) -> tuple[list[tuple[int, int]], bool]:
    """Return ``(spans, new_in_block_comment)`` for character spans outside C++ comments/literals."""
    spans: list[tuple[int, int]] = []
    n = len(line)
    i = 0
    span_start: int | None = None
    in_str = False
    in_char = False
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if in_char:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                in_char = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            if span_start is not None:
                spans.append((span_start, i))
                span_start = None
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            if span_start is not None:
                spans.append((span_start, i))
                span_start = None
            in_str = True
            i += 1
            continue
        if ch == "'":
            prev = line[i - 1] if i > 0 else ""
            if prev.isdigit() and nxt.isdigit():
                if span_start is None:
                    span_start = i
                i += 1
                continue
            if span_start is not None:
                spans.append((span_start, i))
                span_start = None
            in_char = True
            i += 1
            continue
        if span_start is None:
            span_start = i
        i += 1
    if span_start is not None:
        spans.append((span_start, i))
    return spans, in_block_comment


def _load_tree_sitter_cpp() -> tuple[Any, Any, Any] | None:
    """Dynamically load optional ``tree_sitter`` and ``tree_sitter_cpp`` without static imports."""
    try:
        ts_mod = importlib.import_module("tree_sitter")
        tscpp_mod = importlib.import_module("tree_sitter_cpp")
        language_cls = getattr(ts_mod, "Language", None)
        parser_cls = getattr(ts_mod, "Parser", None)
        lang_fn = getattr(tscpp_mod, "language", None)
        if language_cls is None or parser_cls is None or lang_fn is None:
            return None
        return language_cls, parser_cls, lang_fn
    except Exception:
        return None


def _compute_masked_code_and_if0(code: str) -> tuple[str, list[bool]]:
    """Blank comments, literals, and ``#if 0`` inactive regions while preserving char indices."""
    n = len(code)
    active = [False] * n
    masked = list(code)
    lines = code.splitlines(keepends=True)

    # Track #if 0 ... #else/#endif dead preprocessor blocks first
    if_stack: list[bool] = []  # True when this #if level is disabled (#if 0)
    offset = 0
    in_block_comment = False
    for raw_line in lines:
        line_len = len(raw_line)
        currently_disabled = any(if_stack)
        if _IF0_DIRECTIVE_RE.match(raw_line):
            if_stack.append(True)
            for k in range(offset, offset + line_len):
                if masked[k] != "\n":
                    masked[k] = " "
            offset += line_len
            continue
        if _IF_DIRECTIVE_RE.match(raw_line):
            if_stack.append(False)
        elif _ELSE_DIRECTIVE_RE.match(raw_line):
            if if_stack:
                if_stack[-1] = not if_stack[-1]
            for k in range(offset, offset + line_len):
                if masked[k] != "\n":
                    masked[k] = " "
            offset += line_len
            continue
        elif _ENDIF_DIRECTIVE_RE.match(raw_line):
            if if_stack:
                if_stack.pop()
            for k in range(offset, offset + line_len):
                if masked[k] != "\n":
                    masked[k] = " "
            offset += line_len
            continue

        spans, in_block_comment = _strip_line_comments_and_strings(raw_line, in_block_comment)
        if currently_disabled:
            for k in range(offset, offset + line_len):
                if masked[k] != "\n":
                    masked[k] = " "
            offset += line_len
            continue

        line_active = [False] * line_len
        for sp_s, sp_e in spans:
            for j in range(sp_s, sp_e):
                line_active[j] = True
                active[offset + j] = True
        for j in range(line_len):
            if not line_active[j] and masked[offset + j] != "\n":
                masked[offset + j] = " "
        offset += line_len

    return "".join(masked), active


def _find_matching_delim(text: str, open_idx: int, open_ch: str, close_ch: str) -> int | None:
    """Return index of matching ``close_ch`` for ``open_ch`` at ``open_idx``."""
    depth = 0
    n = len(text)
    for i in range(open_idx, n):
        ch = text[i]
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
    return None


def _split_top_level_commas(text: str) -> list[str]:
    """Split ``text`` on commas outside ``<>`` / ``()`` / ``[]`` nesting."""
    parts: list[str] = []
    depth_angle = 0
    depth_paren = 0
    depth_bracket = 0
    start = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth_paren += 1
        elif ch == ")" and depth_paren > 0:
            depth_paren -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]" and depth_bracket > 0:
            depth_bracket -= 1
        elif ch == "<" and depth_paren == 0 and depth_bracket == 0:
            depth_angle += 1
        elif ch == ">" and depth_paren == 0 and depth_bracket == 0 and depth_angle > 0:
            depth_angle -= 1
        elif ch == "," and depth_angle == 0 and depth_paren == 0 and depth_bracket == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _collect_dead_regions(masked_code: str) -> list[tuple[int, int]]:
    """Find character ranges inside ``if (0)``, ``if (false)``, ``while (0)``, ``while (false)``, or post-return dead statements."""
    dead_spans: list[tuple[int, int]] = []
    n = len(masked_code)

    # 1. Dead condition blocks: if (0) / if (false) / while (0) / while (false)
    for m in re.finditer(r"\b(?:if|while)\s*\(\s*(?:0|false)\s*\)", masked_code):
        idx = m.end()
        while idx < n and masked_code[idx].isspace():
            idx += 1
        if idx >= n:
            continue
        if masked_code[idx] == "{":
            close_b = _find_matching_delim(masked_code, idx, "{", "}")
            if close_b is not None:
                dead_spans.append((idx, close_b + 1))
        else:
            semi = masked_code.find(";", idx)
            if semi != -1:
                dead_spans.append((idx, semi + 1))

    # 2. Unreachable statements immediately following return/break/continue before '}' or case/default/label
    for m in re.finditer(r"(?:^|[;{}])\s*(?:return(?:\s+[^;{}]*)?|break|continue)\s*;", masked_code):
        pos = m.end()
        # Walk within the same brace block until closing '}' or label/case/default
        depth = 0
        i = pos
        while i < n:
            ch = masked_code[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0:
                    if i > pos and masked_code[pos:i].strip():
                        dead_spans.append((pos, i))
                    break
                depth -= 1
            elif depth == 0:
                tail = masked_code[i:]
                if re.match(r"\s*(?:case\b|default\s*:|[A-Za-z_]\w*\s*:(?!:))", tail):
                    break
            i += 1
    return dead_spans


def _in_any_span(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)


def _norm_ws(expr: str) -> str:
    return " ".join(expr.split())


def _is_side_effect_free(expr: str) -> bool:
    if not expr or not expr.strip():
        return False
    if "++" in expr or "--" in expr:
        return False
    if re.search(r"(?<![<>!=])=(?![=>])", expr):
        return False
    # Disallow function calls except simple array/member indexing
    if "(" in expr:
        return False
    return True


def _strip_outer_parens(expr: str) -> str:
    s = expr.strip()
    while s.startswith("(") and s.endswith(")"):
        close_idx = _find_matching_delim(s, 0, "(", ")")
        if close_idx == len(s) - 1:
            s = s[1:-1].strip()
        else:
            break
    return s


def _extract_stmt_at(masked: str, start_idx: int) -> tuple[str, int] | None:
    """Return ``(body_text, next_pos_after_stmt)`` starting at ``start_idx``."""
    n = len(masked)
    i = start_idx
    while i < n and masked[i].isspace():
        i += 1
    if i >= n:
        return None
    if masked[i] == "{":
        close_b = _find_matching_delim(masked, i, "{", "}")
        if close_b is None:
            return None
        return masked[i + 1 : close_b], close_b + 1
    # Single statement up to terminating ';' (respecting parentheses)
    paren = 0
    for j in range(i, n):
        ch = masked[j]
        if ch == "(":
            paren += 1
        elif ch == ")" and paren > 0:
            paren -= 1
        elif ch == ";" and paren == 0:
            return masked[i : j + 1], j + 1
    return None


def _normalize_single_stmt(stmt: str) -> str:
    s = " ".join(stmt.strip().split())
    if s.startswith("{") and s.endswith("}"):
        s = " ".join(s[1:-1].strip().split())
    return s


def _parse_simple_assign(stmt: str) -> tuple[str, str] | None:
    """Parse ``target = value;`` if side-effect-free."""
    norm = _normalize_single_stmt(stmt)
    if norm.endswith(";"):
        norm = norm[:-1].strip()
    if not norm or ";" in norm:
        return None
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_.\[\]]*)\s*=\s*(.+)$", norm)
    if not m:
        return None
    lhs = _norm_ws(m.group(1))
    rhs = _norm_ws(m.group(2))
    if not _is_side_effect_free(lhs) or not _is_side_effect_free(rhs):
        return None
    return lhs, rhs


def _parse_simple_return(stmt: str) -> str | None:
    norm = _normalize_single_stmt(stmt)
    if norm.endswith(";"):
        norm = norm[:-1].strip()
    m = re.match(r"^return\s+(.+)$", norm)
    if not m:
        return None
    val = _norm_ws(m.group(1))
    return val if _is_side_effect_free(val) else None


def _extract_preceding_init_rhs(masked: str, if_start: int, target_var: str) -> str | None:
    """Check if the immediately preceding statement in the same block assigns/initializes ``target_var``."""
    # Look back up to 300 chars in the same block before ``if_start``
    prefix = masked[max(0, if_start - 300) : if_start]
    # Cut at enclosing '{' or prior '}' so we stay in the same basic block
    last_brace = max(prefix.rfind("{"), prefix.rfind("}"))
    if last_brace != -1:
        prefix = prefix[last_brace + 1 :]
    stmts = [s.strip() for s in prefix.split(";") if s.strip()]
    if not stmts:
        return None
    prev = _norm_ws(stmts[-1])
    esc_var = re.escape(target_var)
    # Match either `T target = expr` or `target = expr`
    m = re.match(
        rf"^(?:(?:const\s+)?(?:int|long\s+long|long|double|float|auto|ll|int64_t|size_t)\s+)?{esc_var}\s*=\s*(.+)$",
        prev,
    )
    if not m:
        return None
    rhs = _norm_ws(m.group(1))
    return rhs if _is_side_effect_free(rhs) else None


def _check_relational_equivalence(
    masked: str, op_start: int, op_end: int, kind: str
) -> str | None:
    """Detect deterministic ``<`` vs ``<=`` (and ``>`` vs ``>=``) identities and identical branch arms."""
    # 1. Check enclosing `if (...)` statement
    # Find nearest unmatched '(' before op_start
    paren_depth = 0
    open_p: int | None = None
    for i in range(op_start - 1, -1, -1):
        ch = masked[i]
        if ch == ")":
            paren_depth += 1
        elif ch == "(":
            if paren_depth == 0:
                open_p = i
                break
            paren_depth -= 1
    if open_p is not None:
        close_p = _find_matching_delim(masked, open_p, "(", ")")
        before_p = masked[max(0, open_p - 20) : open_p]
        if close_p is not None and re.search(r"\bif\s*$", before_p):
            if_match = re.search(r"\bif\s*$", before_p)
            if_pos = max(0, open_p - 20) + (if_match.start() if if_match else 0)
            cond_text = masked[open_p + 1 : close_p]
            rel_s = op_start - (open_p + 1)
            rel_e = op_end - (open_p + 1)
            lhs_raw = _strip_outer_parens(cond_text[:rel_s])
            rhs_raw = _strip_outer_parens(cond_text[rel_e:])

            then_parsed = _extract_stmt_at(masked, close_p + 1)
            if then_parsed is not None:
                then_body, after_then = then_parsed
                else_body: str | None = None
                after_else = after_then
                rest = masked[after_then:]
                else_m = re.match(r"^\s*else\b", rest)
                if else_m:
                    else_parsed = _extract_stmt_at(masked, after_then + else_m.end())
                    if else_parsed is not None:
                        else_body, after_else = else_parsed

                # Identical branch arms inside `if (cond) A else A`
                if else_body is not None and _normalize_single_stmt(then_body) == _normalize_single_stmt(
                    else_body
                ):
                    if _is_side_effect_free(cond_text):
                        return "identical_branch_arms"

                # `if (cond) return X; return X;`
                ret_then = _parse_simple_return(then_body)
                if else_body is None and ret_then is not None:
                    next_parsed = _extract_stmt_at(masked, after_else)
                    if next_parsed is not None:
                        ret_next = _parse_simple_return(next_parsed[0])
                        if ret_next is not None and ret_next == ret_then and _is_side_effect_free(cond_text):
                            return "identical_branch_arms"

                # `<` vs `<=` and `>` vs `>=` identities
                if _is_side_effect_free(lhs_raw) and _is_side_effect_free(rhs_raw):
                    lhs = _norm_ws(lhs_raw)
                    rhs = _norm_ws(rhs_raw)
                    # Only inspect pure single-comparison condition
                    if not re.search(r"&&|\|\|", cond_text):
                        assign_then = _parse_simple_assign(then_body)
                        assign_else = _parse_simple_assign(else_body) if else_body is not None else None
                        if assign_then is not None:
                            t_var, t_val = assign_then
                            if t_var == lhs and t_val == rhs:
                                return "relational_boundary_identity"
                            if t_var == rhs and t_val == lhs:
                                return "relational_boundary_identity"
                            if assign_else is not None and assign_else[0] == t_var:
                                e_val = assign_else[1]
                                if {t_val, e_val} == {lhs, rhs}:
                                    return "relational_boundary_identity"
                            if else_body is None and t_val in (lhs, rhs):
                                other = rhs if t_val == lhs else lhs
                                prev_rhs = _extract_preceding_init_rhs(masked, if_pos, t_var)
                                if prev_rhs == other:
                                    return "relational_boundary_identity"

                        if ret_then is not None:
                            ret_else = _parse_simple_return(else_body) if else_body is not None else None
                            if ret_else is not None and {ret_then, ret_else} == {lhs, rhs}:
                                return "relational_boundary_identity"
                            if else_body is None:
                                next_parsed = _extract_stmt_at(masked, after_else)
                                if next_parsed is not None:
                                    ret_next = _parse_simple_return(next_parsed[0])
                                    if ret_next is not None and {ret_then, ret_next} == {lhs, rhs}:
                                        return "relational_boundary_identity"

    # 2. Check enclosing ternary expression `L op R ? A : B`
    # Scan forward for '?' at same paren depth
    p_depth = 0
    q_pos: int | None = None
    for j in range(op_end, min(len(masked), op_end + 160)):
        ch = masked[j]
        if ch in ";{}":
            break
        if ch == "(":
            p_depth += 1
        elif ch == ")":
            if p_depth == 0:
                break
            p_depth -= 1
        elif ch == "?" and p_depth == 0:
            q_pos = j
            break
    if q_pos is not None:
        # Find matching ':'
        colon_pos: int | None = None
        t_depth = 0
        for j in range(q_pos + 1, min(len(masked), q_pos + 160)):
            ch = masked[j]
            if ch in ";{}":
                break
            if ch in "([":
                t_depth += 1
            elif ch in ")]":
                if t_depth == 0:
                    break
                t_depth -= 1
            elif ch == ":" and t_depth == 0:
                colon_pos = j
                break
        if colon_pos is not None:
            # Find start of condition expression
            c_start = op_start - 1
            c_depth = 0
            while c_start >= 0:
                ch = masked[c_start]
                if ch in ";{}?=":
                    if ch == "=" and c_start > 0 and masked[c_start - 1] in "<>!=":
                        c_start -= 1
                        continue
                    c_start += 1
                    break
                if ch == ")":
                    c_depth += 1
                elif ch == "(":
                    if c_depth == 0:
                        c_start += 1
                        break
                    c_depth -= 1
                c_start -= 1
            c_start = max(0, c_start)
            cond_expr = masked[c_start:q_pos]
            arm_a = _norm_ws(_strip_outer_parens(masked[q_pos + 1 : colon_pos]))
            # End of arm_b at ';' or ')' or ','
            b_end = colon_pos + 1
            b_depth = 0
            while b_end < len(masked):
                ch = masked[b_end]
                if ch in ";{}":
                    break
                if ch in "([":
                    b_depth += 1
                elif ch in ")]":
                    if b_depth == 0:
                        break
                    b_depth -= 1
                elif ch == "," and b_depth == 0:
                    break
                b_end += 1
            arm_b = _norm_ws(_strip_outer_parens(masked[colon_pos + 1 : b_end]))
            if arm_a and arm_a == arm_b and _is_side_effect_free(cond_expr):
                return "identical_branch_arms"
            lhs_t = _norm_ws(_strip_outer_parens(masked[c_start:op_start]))
            rhs_t = _norm_ws(_strip_outer_parens(masked[op_end:q_pos]))
            if (
                _is_side_effect_free(lhs_t)
                and _is_side_effect_free(rhs_t)
                and {arm_a, arm_b} == {lhs_t, rhs_t}
            ):
                return "relational_boundary_identity"

    return None


def _is_overwritten_before_read(masked: str, var_name: str, stmt_end: int) -> bool:
    """Return True if ``var_name`` is immediately overwritten by ``cin >> var`` or ``scanf`` before read."""
    tail = masked[stmt_end : stmt_end + 160]
    first_semi = tail.find(";")
    if first_semi == -1:
        return False
    next_stmt = tail[:first_semi]
    esc = re.escape(var_name)
    if re.search(rf"\bcin\s*(?:>>\s*[A-Za-z_]\w*\s*)*>>\s*{esc}\b", next_stmt):
        return True
    if re.search(rf"\bscanf\s*\([^)]*&\s*{esc}\b", next_stmt):
        return True
    return False


def _collect_for_header_spans(
    code: str, masked: str, *, cst_root: Any | None = None, byte_to_char: list[int] | None = None
) -> list[tuple[int, int, int, int]]:
    """Return ``(hdr_open_char, hdr_close_char, body_start_char, body_end_char)`` for ``for`` loops."""
    results: list[tuple[int, int, int, int]] = []
    if cst_root is not None and byte_to_char is not None:
        stack = [cst_root]
        while stack:
            node = stack.pop()
            if getattr(node, "type", "") == "for_statement":
                body_node = node.child_by_field_name("body")
                hdr_end_b = body_node.start_byte if body_node is not None else node.end_byte
                s_c = byte_to_char[node.start_byte]
                e_c = byte_to_char[hdr_end_b]
                open_p = masked.find("(", s_c, e_c)
                close_p = masked.rfind(")", s_c, e_c)
                if open_p != -1 and close_p != -1 and close_p > open_p:
                    b_s = byte_to_char[body_node.start_byte] if body_node is not None else close_p + 1
                    b_e = byte_to_char[body_node.end_byte] if body_node is not None else close_p + 1
                    results.append((open_p, close_p, b_s, b_e))
            for ch in getattr(node, "children", ()):
                stack.append(ch)
        return results

    for m in re.finditer(r"\bfor\s*\(", masked):
        open_p = m.end() - 1
        matched_close = _find_matching_delim(masked, open_p, "(", ")")
        if matched_close is not None:
            results.append((open_p, matched_close, matched_close + 1, matched_close + 1))
    return results


def _split_for_clauses(masked: str, open_p: int, close_p: int) -> list[tuple[int, int]]:
    """Return exact ``(start, end)`` char spans for ``init; cond; step`` inside a ``for (...)`` header."""
    clauses: list[tuple[int, int]] = []
    cur_start = open_p + 1
    paren = 0
    for i in range(open_p + 1, close_p):
        ch = masked[i]
        if ch == "(":
            paren += 1
        elif ch == ")" and paren > 0:
            paren -= 1
        elif ch == ";" and paren == 0:
            clauses.append((cur_start, i))
            cur_start = i + 1
    clauses.append((cur_start, close_p))
    return clauses


def _collect_identical_arm_if_cond_spans(masked: str) -> list[tuple[int, int]]:
    """Find condition spans ``(cond)`` for ``if (cond) A else A`` or ``if (cond) return X; return X;``."""
    spans: list[tuple[int, int]] = []
    for m in re.finditer(r"\bif\s*\(", masked):
        open_p = m.end() - 1
        close_p = _find_matching_delim(masked, open_p, "(", ")")
        if close_p is None:
            continue
        cond = masked[open_p + 1 : close_p]
        if not _is_side_effect_free(cond):
            continue
        then_parsed = _extract_stmt_at(masked, close_p + 1)
        if then_parsed is None:
            continue
        then_body, after_then = then_parsed
        rest = masked[after_then:]
        else_m = re.match(r"^\s*else\b", rest)
        if else_m:
            else_parsed = _extract_stmt_at(masked, after_then + else_m.end())
            if else_parsed is not None:
                if _normalize_single_stmt(then_body) == _normalize_single_stmt(else_parsed[0]):
                    spans.append((open_p + 1, close_p))
                    continue
        ret_then = _parse_simple_return(then_body)
        if else_m is None and ret_then is not None:
            next_parsed = _extract_stmt_at(masked, after_then)
            if next_parsed is not None:
                ret_next = _parse_simple_return(next_parsed[0])
                if ret_next is not None and ret_next == ret_then:
                    spans.append((open_p + 1, close_p))
    return spans


def _build_byte_to_char_map(code: str) -> list[int]:
    """Map every UTF-8 byte offset ``0..len(code_bytes)`` to Python char offset."""
    mapping: list[int] = []
    for ch_idx, ch in enumerate(code):
        b_len = len(ch.encode("utf-8", errors="replace"))
        mapping.extend([ch_idx] * b_len)
    mapping.append(len(code))
    return mapping


def _extract_candidates(
    code: str,
) -> tuple[list[_CandidateEdit], str]:
    """Extract raw candidate mutations using ``tree-sitter-cpp`` when installed or Python fallback."""
    masked, active = _compute_masked_code_and_if0(code)
    dead_spans = _collect_dead_regions(masked)
    ident_cond_spans = _collect_identical_arm_if_cond_spans(masked)

    cst_root: Any | None = None
    byte_to_char: list[int] | None = None
    ast_backend = "python_fallback"
    loaded = _load_tree_sitter_cpp()
    if loaded is not None:
        language_cls, parser_cls, lang_fn = loaded
        try:
            cpp_lang = language_cls(lang_fn())
            parser = parser_cls(cpp_lang)
            code_bytes = code.encode("utf-8", errors="replace")
            tree = parser.parse(code_bytes)
            if not getattr(tree.root_node, "has_error", False):
                cst_root = tree.root_node
                byte_to_char = _build_byte_to_char_map(code)
                ast_backend = "tree_sitter_cpp"
        except Exception:
            cst_root = None
            byte_to_char = None
            ast_backend = "python_fallback"

    candidates: list[_CandidateEdit] = []
    lines = code.splitlines(keepends=True)
    line_starts: list[int] = []
    acc = 0
    for raw_line in lines:
        line_starts.append(acc)
        acc += len(raw_line)

    def _dead_or_ident_reason(pos: int) -> str | None:
        if pos < 0 or pos >= len(active) or not active[pos]:
            return "comment_or_inactive"
        if _in_any_span(pos, dead_spans):
            return "dead_code"
        if _in_any_span(pos, ident_cond_spans):
            return "identical_branch_arms"
        return None

    # 1. Existing boundary/operator rules (keeping original per-(line, kind) cap and IO/template guards)
    seen_line_kind: set[tuple[int, str]] = set()
    for line_idx, raw_line in enumerate(lines):
        if not raw_line.strip() or _SKIP_LINE_RE.match(raw_line):
            continue
        l_start = line_starts[line_idx]
        l_end = l_start + len(raw_line)
        masked_line = masked[l_start:l_end]
        if not masked_line.strip():
            continue
        is_io_line = bool(_IO_LINE_RE.search(masked_line))

        for kind, pat, repl in _MUTATION_RULES:
            if is_io_line and kind in ("relational_lt_to_le", "relational_gt_to_ge"):
                continue
            for m in pat.finditer(masked_line):
                abs_s = l_start + m.start()
                abs_e = l_start + m.end()
                if not active[abs_s]:
                    continue
                if kind in ("relational_lt_to_le", "relational_gt_to_ge"):
                    prefix = masked_line[: m.start()]
                    if _TEMPLATE_PREFIX_RE.search(prefix):
                        continue
                eq_reason = _dead_or_ident_reason(abs_s)
                if eq_reason is None and kind in _RELATIONAL_KINDS:
                    eq_reason = _check_relational_equivalence(masked, abs_s, abs_e, kind)
                if (line_idx, kind) in seen_line_kind and eq_reason is None:
                    continue
                if eq_reason is None:
                    seen_line_kind.add((line_idx, kind))
                candidates.append(
                    _CandidateEdit(
                        start=abs_s,
                        end=abs_e,
                        replacement=repl,
                        mutation_type=kind,
                        category="boundary",
                        ast_backend=ast_backend,
                        equivalent_reason=eq_reason,
                    )
                )

    # 2. Semantic operator: long long -> int narrowing
    seen_ll_lines: set[int] = set()
    if cst_root is not None and byte_to_char is not None:
        stack = [cst_root]
        while stack:
            node = stack.pop()
            ntype = getattr(node, "type", "")
            if ntype in {"sized_type_specifier", "primitive_type", "type_identifier"}:
                s_c = byte_to_char[node.start_byte]
                e_c = byte_to_char[node.end_byte]
                seg = masked[s_c:e_c]
                if _LL_DECL_RE.fullmatch(seg.strip()) or _INT64_DECL_RE.fullmatch(seg.strip()):
                    repl = "int32_t" if "64" in seg else "int"
                    ln = code.count("\n", 0, s_c)
                    if ln not in seen_ll_lines and not re.search(r"\bmain\s*\(", masked[e_c : e_c + 20]):
                        seen_ll_lines.add(ln)
                        candidates.append(
                            _CandidateEdit(
                                start=s_c,
                                end=e_c,
                                replacement=repl,
                                mutation_type="type_narrowing_ll_to_int",
                                category="semantic",
                                ast_backend=ast_backend,
                                equivalent_reason=_dead_or_ident_reason(s_c),
                            )
                        )
            for ch in getattr(node, "children", ()):
                stack.append(ch)
    else:
        for pat_ll, repl_ll in ((_LL_DECL_RE, "int"), (_INT64_DECL_RE, "int32_t")):
            for m in pat_ll.finditer(masked):
                s_c, e_c = m.start(), m.end()
                ln = code.count("\n", 0, s_c)
                if ln in seen_ll_lines:
                    continue
                if re.search(r"\bmain\s*\(", masked[e_c : e_c + 20]):
                    continue
                seen_ll_lines.add(ln)
                candidates.append(
                    _CandidateEdit(
                        start=s_c,
                        end=e_c,
                        replacement=repl_ll,
                        mutation_type="type_narrowing_ll_to_int",
                        category="semantic",
                        ast_backend=ast_backend,
                        equivalent_reason=_dead_or_ident_reason(s_c),
                    )
                )

    # 3. Semantic operator: % MOD removal (both compound `%= MOD` and binary `% MOD`)
    seen_mod_lines: set[int] = set()
    for m in _MOD_COMPOUND_RE.finditer(masked):
        s_c, e_c = m.start(), m.end()
        ln = code.count("\n", 0, s_c)
        if ln in seen_mod_lines:
            continue
        seen_mod_lines.add(ln)
        candidates.append(
            _CandidateEdit(
                start=s_c,
                end=e_c,
                replacement="+= 0",
                mutation_type="mod_removal",
                category="semantic",
                ast_backend=ast_backend,
                equivalent_reason=_dead_or_ident_reason(s_c),
            )
        )

    # Binary `% <rhs>` (prioritize CP mod names / large moduli, then any identifier `% rhs`)
    mod_matches: list[tuple[int, int, int]] = []  # (priority, start, end)
    for m in re.finditer(r"(?<![%A-Za-z0-9_])%(?!=)", masked):
        pct_s = m.start()
        j = m.end()
        while j < len(masked) and masked[j].isspace():
            j += 1
        if j >= len(masked):
            continue
        rhs_end: int | None = None
        rhs_text = ""
        if masked[j] == "(":
            close_p = _find_matching_delim(masked, j, "(", ")")
            if close_p is not None:
                # Handle C-style cast like `(int)(1e9 + 7)`
                nxt = close_p + 1
                while nxt < len(masked) and masked[nxt].isspace():
                    nxt += 1
                if nxt < len(masked) and masked[nxt] == "(":
                    close_p2 = _find_matching_delim(masked, nxt, "(", ")")
                    if close_p2 is not None:
                        rhs_end = close_p2 + 1
                if rhs_end is None:
                    rhs_end = close_p + 1
                rhs_text = masked[j:rhs_end]
        else:
            m_rhs = re.match(r"[A-Za-z_]\w*|\d+(?:'\d+)*(?:[eE][+-]?\d+)?(?:LL|ll|ULL|ull)?", masked[j:])
            if m_rhs:
                rhs_end = j + m_rhs.end()
                rhs_text = m_rhs.group(0)
        if rhs_end is None or not rhs_text:
            continue
        is_cp_mod = bool(
            _CP_MOD_NAME_RE.match(rhs_text)
            or re.search(r"1e9|1000000007|998244353|1000000009|mod", rhs_text, re.IGNORECASE)
            or (rhs_text.replace("'", "").isdigit() and len(rhs_text.replace("'", "")) >= 4)
        )
        prio = 0 if is_cp_mod else 1
        mod_matches.append((prio, pct_s, rhs_end))

    mod_matches.sort(key=lambda t: (t[0], t[1]))
    for _prio, s_c, e_c in mod_matches:
        ln = code.count("\n", 0, s_c)
        if ln in seen_mod_lines:
            continue
        seen_mod_lines.add(ln)
        candidates.append(
            _CandidateEdit(
                start=s_c,
                end=e_c,
                replacement="",
                mutation_type="mod_removal",
                category="semantic",
                ast_backend=ast_backend,
                equivalent_reason=_dead_or_ident_reason(s_c),
            )
        )

    # 4. Semantic operator: loop bound +/-1
    seen_loop_lines: set[int] = set()
    for open_p, close_p, _, _ in _collect_for_header_spans(
        code, masked, cst_root=cst_root, byte_to_char=byte_to_char
    ):
        clauses = _split_for_clauses(masked, open_p, close_p)
        if len(clauses) != 3:
            continue
        init_s, init_e = clauses[0]
        cond_s, cond_e = clauses[1]
        cond_text = masked[cond_s:cond_e]
        ln = code.count("\n", 0, cond_s)
        if ln not in seen_loop_lines:
            # Upper/lower comparison bound in loop condition
            m_cond = re.match(
                r"^\s*[A-Za-z_]\w*\s*(<=|<|>=|>)\s*(.+?)\s*$", cond_text
            )
            if m_cond and not re.search(r"&&|\|\||\?", m_cond.group(2)):
                op = m_cond.group(1)
                rhs_raw = m_cond.group(2)
                rhs_off_s = cond_s + m_cond.start(2)
                rhs_off_e = cond_s + m_cond.end(2)
                # Shift upper bound by -1 (`<` / `<=`) or lower bound by +1 (`>` / `>=`)
                if op in ("<", "<="):
                    repl = f"({rhs_raw}) - 1"
                else:
                    repl = f"({rhs_raw}) + 1"
                seen_loop_lines.add(ln)
                candidates.append(
                    _CandidateEdit(
                        start=rhs_off_s,
                        end=rhs_off_e,
                        replacement=repl,
                        mutation_type="loop_bound_off_by_one",
                        category="semantic",
                        ast_backend=ast_backend,
                        equivalent_reason=_dead_or_ident_reason(rhs_off_s),
                    )
                )
                continue

        # Fallback: shift loop initializer start value (0 -> 1 or 1 -> 0)
        init_text = masked[init_s:init_e]
        m_init = re.search(r"=\s*(0|1)\b", init_text)
        if m_init and ln not in seen_loop_lines:
            val_s = init_s + m_init.start(1)
            val_e = init_s + m_init.end(1)
            repl_v = "1" if m_init.group(1) == "0" else "0"
            seen_loop_lines.add(ln)
            candidates.append(
                _CandidateEdit(
                    start=val_s,
                    end=val_e,
                    replacement=repl_v,
                    mutation_type="loop_bound_off_by_one",
                    category="semantic",
                    ast_backend=ast_backend,
                    equivalent_reason=_dead_or_ident_reason(val_s),
                )
            )

    # 5. Semantic operator: init extreme swap (INT_MAX / 1e9 / 0)
    seen_init_lines: set[int] = set()
    for_header_spans = [(op, cl) for op, cl, _, _ in _collect_for_header_spans(code, masked, cst_root=cst_root, byte_to_char=byte_to_char)]

    for m in _STD_LIMITS_RE.finditer(masked):
        s_c, e_c = m.start(), m.end()
        ln = code.count("\n", 0, s_c)
        if ln in seen_init_lines:
            continue
        seen_init_lines.add(ln)
        candidates.append(
            _CandidateEdit(
                start=s_c,
                end=e_c,
                replacement="0",
                mutation_type="init_extreme_swap",
                category="semantic",
                ast_backend=ast_backend,
                equivalent_reason=_dead_or_ident_reason(s_c),
            )
        )

    for m in _EXTREME_TOKEN_RE.finditer(masked):
        s_c, e_c = m.start(1), m.end(1)
        tok = m.group(1)
        ln = code.count("\n", 0, s_c)
        if ln in seen_init_lines:
            continue
        # Avoid breaking `#define INF ...` or `const int INF = ...` self-definition LHS
        line_prefix = masked[line_starts[ln] : s_c]
        if tok.lower() == "inf":
            if re.search(r"#\s*define\s*$", line_prefix) or re.match(r"^\s*=", masked[e_c : e_c + 16]):
                continue
        # Avoid replacing modulus constants already mutated as `% MOD`
        if re.search(r"%\s*$", line_prefix):
            continue
        # Avoid swapping `1e9` if `tok` is just an array bound `int a[...]`
        if re.search(r"\[\s*$", line_prefix):
            continue
        seen_init_lines.add(ln)
        candidates.append(
            _CandidateEdit(
                start=s_c,
                end=e_c,
                replacement="0",
                mutation_type="init_extreme_swap",
                category="semantic",
                ast_backend=ast_backend,
                equivalent_reason=_dead_or_ident_reason(s_c),
            )
        )

    for m in _EXTREME_ZERO_INIT_RE.finditer(masked):
        var_name = m.group(1)
        zero_s = m.end() - 1
        zero_e = m.end()
        ln = code.count("\n", 0, zero_s)
        if ln in seen_init_lines or _in_any_span(zero_s, for_header_spans):
            continue
        eq_reason = _dead_or_ident_reason(zero_s)
        if eq_reason is None and _is_overwritten_before_read(masked, var_name, zero_e):
            eq_reason = "dead_store_before_read"
        if eq_reason is None:
            seen_init_lines.add(ln)
        candidates.append(
            _CandidateEdit(
                start=zero_s,
                end=zero_e,
                replacement="1000000000",
                mutation_type="init_extreme_swap",
                category="semantic",
                ast_backend=ast_backend,
                equivalent_reason=eq_reason,
            )
        )

    # 6. Semantic operator: priority_queue comparator flip (`greater<>` <-> default)
    seen_pq_lines: set[int] = set()
    for m in _PQ_RE.finditer(masked):
        pq_start = m.start()
        angle_open = m.end() - 1
        angle_close = _find_matching_delim(masked, angle_open, "<", ">")
        if angle_close is None:
            continue
        ln = code.count("\n", 0, pq_start)
        if ln in seen_pq_lines:
            continue
        inner = masked[angle_open + 1 : angle_close]
        args = _split_top_level_commas(inner)
        has_std_prefix = bool(m.group(1))
        pq_prefix = "std::priority_queue" if has_std_prefix else "priority_queue"
        repl_pq: str | None = None
        if len(args) == 1 and args[0]:
            elem_t = args[0]
            repl_pq = f"{pq_prefix}<{elem_t}, std::vector<{elem_t}>, std::greater<{elem_t}>>"
        elif len(args) == 3 and re.search(r"\bgreater\s*<", args[2]):
            repl_pq = f"{pq_prefix}<{args[0]}>"
        if repl_pq is not None:
            seen_pq_lines.add(ln)
            candidates.append(
                _CandidateEdit(
                    start=pq_start,
                    end=angle_close + 1,
                    replacement=repl_pq,
                    mutation_type="priority_queue_comparator_flip",
                    category="semantic",
                    ast_backend=ast_backend,
                    equivalent_reason=_dead_or_ident_reason(pq_start),
                )
            )

    # 7. Semantic operator: multi-test container `.clear()` deletion
    seen_clear_lines: set[int] = set()
    for m in _CLEAR_STMT_RE.finditer(masked):
        s_c, e_c = m.start(), m.end()
        ln = code.count("\n", 0, s_c)
        if ln in seen_clear_lines:
            continue
        # Confirm statement boundary on the left (start of line, ';', '{', '}', or ')')
        left = masked[max(0, s_c - 64) : s_c].rstrip()
        if left and left[-1] not in (";", "{", "}", ")"):
            continue
        seen_clear_lines.add(ln)
        candidates.append(
            _CandidateEdit(
                start=s_c,
                end=e_c,
                replacement="(void)0;",
                mutation_type="multitest_clear_deletion",
                category="semantic",
                ast_backend=ast_backend,
                equivalent_reason=_dead_or_ident_reason(s_c),
            )
        )

    return candidates, ast_backend


def _materialise_candidate(code: str, cand: _CandidateEdit) -> dict[str, Any]:
    mut_code = code[: cand.start] + cand.replacement + code[cand.end :]
    line_no = code.count("\n", 0, cand.start) + 1
    orig_line_s = code.rfind("\n", 0, cand.start)
    orig_line_s = 0 if orig_line_s == -1 else orig_line_s + 1
    orig_line_e = code.find("\n", cand.end)
    orig_line_e = len(code) if orig_line_e == -1 else orig_line_e
    orig_preview = code[orig_line_s:orig_line_e].strip()
    mut_line_e = mut_code.find("\n", orig_line_s)
    mut_line_e = len(mut_code) if mut_line_e == -1 else mut_line_e
    mut_preview = mut_code[orig_line_s:mut_line_e].strip()
    entry: dict[str, Any] = {
        "line": line_no,
        "mutation_type": cand.mutation_type,
        "category": cand.category,
        "ast_backend": cand.ast_backend,
        "original": orig_preview,
        "mutated": mut_preview,
        "code": mut_code,
    }
    if cand.equivalent_reason is not None:
        entry["equivalent_reason"] = cand.equivalent_reason
    return entry


def filter_equivalent_mutants(
    code: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter deterministic equivalent mutants from ``candidates``.

    Returns ``(kept, filtered_out)`` where each filtered entry includes ``reason``.
    """
    masked_orig, _ = _compute_masked_code_and_if0(code)
    norm_orig = _norm_ws(masked_orig)
    kept: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []

    for item in candidates:
        explicit_reason = item.get("equivalent_reason")
        if isinstance(explicit_reason, str) and explicit_reason:
            filtered.append(
                {
                    "line": item.get("line"),
                    "mutation_type": item.get("mutation_type"),
                    "original": item.get("original"),
                    "mutated": item.get("mutated"),
                    "reason": explicit_reason,
                }
            )
            continue

        mut_code = str(item.get("code") or "")
        if mut_code == code:
            filtered.append(
                {
                    "line": item.get("line"),
                    "mutation_type": item.get("mutation_type"),
                    "original": item.get("original"),
                    "mutated": item.get("mutated"),
                    "reason": "no_op",
                }
            )
            continue

        masked_mut, _ = _compute_masked_code_and_if0(mut_code)
        if _norm_ws(masked_mut) == norm_orig:
            filtered.append(
                {
                    "line": item.get("line"),
                    "mutation_type": item.get("mutation_type"),
                    "original": item.get("original"),
                    "mutated": item.get("mutated"),
                    "reason": "comment_or_whitespace_only",
                }
            )
            continue

        kept.append(item)

    return kept, filtered


def generate_cpp_mutants_report(
    code: str,
    *,
    budget: int = 6,
    filter_equivalent: bool = True,
) -> dict[str, Any]:
    """Generate bounded C++ mutants plus equivalent-mutant filtering telemetry."""
    safe_budget = max(1, min(int(budget), _MAX_MUTATION_BUDGET))
    raw_edits, ast_backend = _extract_candidates(code)

    materialized = [_materialise_candidate(code, edit) for edit in raw_edits]
    if filter_equivalent:
        non_equiv, filtered_eq = filter_equivalent_mutants(code, materialized)
    else:
        non_equiv, filtered_eq = materialized, []

    candidates_by_kind: dict[str, list[dict[str, Any]]] = {}
    seen_codes: set[str] = {code}
    for entry in non_equiv:
        mut_code = str(entry["code"])
        if mut_code in seen_codes:
            continue
        seen_codes.add(mut_code)
        kind = str(entry["mutation_type"])
        candidates_by_kind.setdefault(kind, []).append(entry)

    # Round-robin across priority categories so semantic & boundary bug classes stay balanced
    selected: list[dict[str, Any]] = []
    ordered_kinds = [k for k in _CATEGORY_PRIORITY if k in candidates_by_kind]
    for extra_k in candidates_by_kind:
        if extra_k not in ordered_kinds:
            ordered_kinds.append(extra_k)
    kind_queues = [candidates_by_kind[k] for k in ordered_kinds]
    idx_per_q = [0] * len(kind_queues)

    while len(selected) < safe_budget and kind_queues:
        progressed = False
        for q_idx, queue in enumerate(kind_queues):
            pos = idx_per_q[q_idx]
            if pos < len(queue):
                item = dict(queue[pos])
                item.pop("equivalent_reason", None)
                item["id"] = f"mut_{len(selected) + 1}"
                selected.append(item)
                idx_per_q[q_idx] = pos + 1
                progressed = True
                if len(selected) >= safe_budget:
                    break
        if not progressed:
            break

    return {
        "mutants": selected,
        "equivalent_filtered": len(filtered_eq),
        "equivalent_mutants": filtered_eq,
        "ast_backend": ast_backend,
    }


def generate_cpp_mutants(
    code: str,
    *,
    budget: int = 6,
    filter_equivalent: bool = True,
    stats_out: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate up to ``budget`` boundary and CP semantic mutants of C++ source ``code``."""
    report = generate_cpp_mutants_report(
        code,
        budget=budget,
        filter_equivalent=filter_equivalent,
    )
    if stats_out is not None:
        stats_out["equivalent_filtered"] = report["equivalent_filtered"]
        stats_out["equivalent_mutants"] = report["equivalent_mutants"]
        stats_out["ast_backend"] = report["ast_backend"]
    mutants: list[dict[str, Any]] = report["mutants"]
    return mutants


def detect_mull_runner(mull_runner_bin: str | None = None) -> str | None:
    """Return path to ``mull-runner`` binary when available on host, else ``None``."""
    if mull_runner_bin:
        cand = mull_runner_bin.strip()
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
        return None
    for name in ("mull-runner", "mull-runner-18", "mull-runner-17", "mull-runner-16", "mull-runner-15"):
        found = shutil.which(name)
        if found:
            return found
    return None


async def _try_mull_runner_probe(
    mull_bin: str,
    *,
    tmp_dir: str,
    timeout_sec: float = 5.0,
) -> tuple[bool, str]:
    """Check whether ``mull-runner`` is executable and functional on host."""
    try:
        proc = await asyncio.create_subprocess_exec(
            mull_bin,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except (asyncio.TimeoutError, TimeoutError):
            if proc.pid:
                await terminate_pid_tree(proc.pid)
            return False, "mull-runner --version timed out"
        if proc.returncode != 0:
            err = (stderr or stdout).decode("utf-8", errors="replace").strip()
            return False, f"mull-runner exited with {proc.returncode}: {err[:160]}"
        return True, stdout.decode("utf-8", errors="replace").strip()
    except OSError as exc:
        return False, str(exc)


def _outputs_match_tokens(actual: str, expected: str) -> bool:
    act = actual.replace("\r\n", "\n").strip()
    exp = expected.replace("\r\n", "\n").strip()
    if act == exp:
        return True
    if [ln.rstrip() for ln in act.split("\n")] == [ln.rstrip() for ln in exp.split("\n")]:
        return True
    return act.split() == exp.split()


def _read_bounded_text(path: Path, max_bytes: int = _MAX_MUTATION_FILE_BYTES) -> str | None:
    try:
        with open(path, "rb") as fp:
            data = fp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


async def run_mutation_testing(
    problem_dir: str,
    tests_dir: str,
    *,
    manifest: AutoCodeManifest,
    sol_source: Path,
    answer_ext: str = ".ans",
    budget: int = 6,
    verify_with_checker: bool = False,
    checker_bin: str | None = None,
    interactor_bin: str | None = None,
    timeout_sec: float = 3.0,
    max_total_sec: float = 45.0,
    backend: Literal["auto", "source", "mull"] = "auto",
    mull_runner_bin: str | None = None,
) -> dict[str, Any]:
    """Run bounded AST/semantic mutation testing against ``tests_dir`` inside an isolated temp dir."""
    safe_sol = _is_contained_non_symlink(problem_dir, sol_source)
    if safe_sol is None or not safe_sol.is_file():
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "backend": "source",
            "reason": f"solution source missing, symlinked, or outside problem_dir: {sol_source}",
        }
    safe_tests = _is_contained_non_symlink(problem_dir, tests_dir)
    if safe_tests is None or not safe_tests.is_dir():
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "backend": "source",
            "reason": "tests directory missing, symlinked, or outside problem_dir",
        }

    in_files: list[Path] = []
    for p in sorted(safe_tests.iterdir(), key=_natural_sort_key):
        if p.suffix != ".in":
            continue
        resolved_in = _is_contained_non_symlink(problem_dir, p)
        if resolved_in is None or not resolved_in.is_file():
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "backend": "source",
                "reason": f"refusing symlinked or escaped test input: {p.name}",
            }
        in_files.append(resolved_in)

    discovered_answers: dict[Path, Path | None] = {}
    if not in_files:
        for p in sorted(safe_tests.iterdir(), key=_natural_sort_key):
            if p.is_symlink() and _is_contained_non_symlink(problem_dir, p) is None:
                return {
                    "executed": False,
                    "passed": True,
                    "skipped": True,
                    "backend": "source",
                    "reason": f"refusing symlinked or escaped test input: {p.name}",
                }
        discovered_pairs = discover_test_pairs(
            Path(problem_dir),
            tests_dir=safe_tests,
            answer_ext=answer_ext,
            allow_missing_answer=True,
        )
        for pair in discovered_pairs:
            resolved_in = _is_contained_non_symlink(problem_dir, pair.input_path)
            if resolved_in is None or not resolved_in.is_file():
                return {
                    "executed": False,
                    "passed": True,
                    "skipped": True,
                    "backend": "source",
                    "reason": f"refusing symlinked or escaped test input: {pair.input_path.name}",
                }
            in_files.append(resolved_in)
            discovered_answers[resolved_in] = pair.answer_path

    if not in_files:
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "backend": "source",
            "reason": "no test input files found",
        }

    code = _read_bounded_text(safe_sol, max_bytes=2 * 1024 * 1024)
    if code is None:
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "backend": "source",
            "reason": f"failed to read solution source (or exceeds 2MB): {safe_sol}",
        }

    exe_ext = get_exe_extension()
    compiler = resolve_effective_compiler(None, manifest)
    is_interactive = manifest_is_interactive(manifest)
    is_communication = manifest_is_communication(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else ""
    )

    eff_checker: str | None = None
    if verify_with_checker and not is_interactive and not is_communication:
        if checker_bin is not None:
            if not Path(checker_bin).is_symlink() and os.path.isfile(checker_bin):
                eff_checker = checker_bin
        else:
            cand_c = _is_contained_non_symlink(problem_dir, checker_exe_path(problem_dir, exe_ext))
            if cand_c is not None and cand_c.is_file():
                eff_checker = str(cand_c)
        if not eff_checker:
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "backend": "source",
                "reason": "checker mutation testing requires a compiled, contained checker binary",
            }

    eff_interactor: str | None = None
    if is_interactive:
        if interactor_bin is not None:
            if not Path(interactor_bin).is_symlink() and os.path.isfile(interactor_bin):
                eff_interactor = interactor_bin
        else:
            cand_i = _is_contained_non_symlink(problem_dir, f"files/interactor{exe_ext}")
            if cand_i is not None and cand_i.is_file():
                eff_interactor = str(cand_i)
        if not eff_interactor:
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "backend": "source",
                "reason": "interactive mutation testing requires a compiled, contained interactor binary",
            }

    eff_communicator: str | None = None
    if is_communication and comm_mode != "grader_linked":
        if interactor_bin is not None:
            if not Path(interactor_bin).is_symlink() and os.path.isfile(interactor_bin):
                eff_communicator = interactor_bin
        else:
            cand_comm = resolve_communicator_binary(problem_dir, manifest, exe_ext)
            if (
                cand_comm is not None
                and not Path(cand_comm).is_symlink()
                and os.path.isfile(cand_comm)
            ):
                eff_communicator = cand_comm
        if not eff_communicator:
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "backend": "source",
                "reason": f"communication ({comm_mode}) mutation testing requires a compiled communicator binary",
            }

    mull_path = detect_mull_runner(mull_runner_bin) if backend in ("auto", "mull") else None
    mull_info: dict[str, Any] = {
        "mull_available": mull_path is not None,
        "mull_path": mull_path,
    }
    if mull_path is not None and backend in ("auto", "mull"):
        with tempfile.TemporaryDirectory(
            prefix="autocode_mull_probe_", ignore_cleanup_errors=True
        ) as probe_dir:
            ok_mull, detail_mull = await _try_mull_runner_probe(mull_path, tmp_dir=probe_dir)
            mull_info["mull_probe_ok"] = ok_mull
            mull_info["mull_detail"] = detail_mull
            mull_info["mull_fallback_reason"] = (
                "mull-runner lacks LLVM pass plugin / per-case harness integration for arbitrary problem layouts; using deterministic source AST backend"
                if ok_mull
                else f"mull-runner probe failed: {detail_mull}"
            )
    active_backend = "source"

    mut_report = generate_cpp_mutants_report(code, budget=budget, filter_equivalent=True)
    mutants: list[dict[str, Any]] = mut_report["mutants"]
    equivalent_filtered = int(mut_report["equivalent_filtered"])
    equivalent_mutants: list[dict[str, Any]] = mut_report["equivalent_mutants"]
    ast_backend = str(mut_report["ast_backend"])

    if not mutants:
        return {
            "executed": True,
            "passed": True,
            "backend": active_backend,
            "ast_backend": ast_backend,
            "equivalent_filtered": equivalent_filtered,
            "equivalent_mutants": equivalent_mutants,
            "total_mutants": 0,
            "compiled_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "judge_error_mutants": 0,
            "kill_rate": None,
            "survivors": [],
            "details": [],
            **mull_info,
        }

    include_dirs: list[str] = []
    sol_parent = _is_contained_non_symlink(problem_dir, safe_sol.parent)
    if sol_parent is not None and sol_parent.is_dir():
        include_dirs.append(str(sol_parent))
    extra_sources: list[str] | None = None
    if is_communication and comm_mode == "grader_linked":
        grader_rel = resolve_grader_source_existing(problem_dir, manifest)
        if grader_rel:
            g_res = _is_contained_non_symlink(problem_dir, grader_rel)
            if g_res is not None and g_res.is_file():
                extra_sources = [str(g_res)]
                files_dir = _is_contained_non_symlink(problem_dir, "files")
                if files_dir is not None and files_dir.is_dir():
                    include_dirs.append(str(files_dir))
    include_dirs.append(TEMPLATES_DIR)

    # Pre-validate & cache test input/answer files once (enforcing containment & size caps)
    cached_cases: list[tuple[Path, str, Path | None, str]] = []
    for in_file in in_files:
        inp = _read_bounded_text(in_file)
        if inp is None:
            continue
        ans_path: Path | None = None
        cand_ans: Path | None = None
        if in_file in discovered_answers:
            cand_ans = discovered_answers[in_file]
        else:
            probe_ans = in_file.with_suffix(answer_ext)
            if not probe_ans.exists() and answer_ext != ".out":
                alt = in_file.with_suffix(".out")
                if alt.exists():
                    probe_ans = alt
            if not probe_ans.exists() and answer_ext != ".a":
                alt_a = in_file.with_suffix(".a")
                if alt_a.exists():
                    probe_ans = alt_a
            if probe_ans.exists():
                cand_ans = probe_ans
        if cand_ans is not None and cand_ans.exists():
            safe_ans = _is_contained_non_symlink(problem_dir, cand_ans)
            if safe_ans is None or not safe_ans.is_file():
                return {
                    "executed": False,
                    "passed": True,
                    "skipped": True,
                    "backend": active_backend,
                    "reason": f"refusing symlinked or escaped answer file: {cand_ans.name}",
                }
            ans_path = safe_ans
        ans_text = _read_bounded_text(ans_path) if ans_path is not None else ""
        if ans_text is None:
            continue
        cached_cases.append((in_file, inp, ans_path, ans_text))

    if not cached_cases:
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "backend": active_backend,
            "reason": "no readable test cases within size cap",
        }

    tl_ms = float(manifest.time_limit_ms) if manifest.time_limit_ms > 0 else 2000.0
    per_test_timeout = max(0.5, min(float(timeout_sec), (tl_ms / 1000.0) * 1.5))
    mem_mb = float(manifest.memory_limit_mb) if manifest.memory_limit_mb > 0 else 256.0
    deadline = time.monotonic() + max(5.0, float(max_total_sec))

    compiled_count = 0
    killed_count = 0
    judge_error_count = 0
    budget_exhausted = False
    survivors: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(
        prefix="autocode_mutation_", ignore_cleanup_errors=True
    ) as tmp_dir:
        for mut in mutants:
            if time.monotonic() >= deadline:
                budget_exhausted = True
                break
            mut_id = str(mut["id"])
            mut_src = Path(tmp_dir) / f"{mut_id}.cpp"
            mut_bin = Path(tmp_dir) / f"{mut_id}{exe_ext}"
            mut_src.write_text(str(mut["code"]), encoding="utf-8")

            comp = await compile_cpp(
                str(mut_src),
                str(mut_bin),
                compiler=compiler,
                include_dirs=include_dirs,
                extra_sources=extra_sources,
                use_cache=False,
            )
            if not comp.success:
                details.append(
                    {
                        "id": mut_id,
                        "line": mut["line"],
                        "mutation_type": mut["mutation_type"],
                        "category": mut.get("category", "boundary"),
                        "status": "compile_error",
                        "killed": False,
                    }
                )
                continue

            compiled_count += 1
            killed = False
            judge_error = False
            killed_by: str | None = None
            verdict = "AC"

            for in_file, inp, ans_path, ans_text in cached_cases:
                if time.monotonic() >= deadline:
                    budget_exhausted = True
                    break

                if is_communication:
                    jres = await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest,
                        solution_exe=str(mut_bin),
                        input_data=inp,
                        answer_data=ans_text,
                        time_limit_ms=per_test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        use_system_temp=True,
                        communicator_exe_override=eff_communicator,
                    )
                    if jres.verdict == "FAIL":
                        judge_error = True
                        verdict = "FAIL"
                        break
                    if not jres.accepted:
                        killed = True
                        killed_by = in_file.name
                        verdict = jres.verdict
                        break
                elif is_interactive and eff_interactor:
                    jres = await InteractiveRunner.run(
                        interactor_exe=eff_interactor,
                        solution_exe=str(mut_bin),
                        input_data=inp,
                        answer_data=ans_text,
                        time_limit_ms=per_test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        cwd=tmp_dir,
                    )
                    if jres.verdict == "FAIL":
                        judge_error = True
                        verdict = "FAIL"
                        break
                    if not jres.accepted:
                        killed = True
                        killed_by = in_file.name
                        verdict = jres.verdict
                        break
                else:
                    run = await run_binary(
                        str(mut_bin),
                        inp,
                        timeout=per_test_timeout,
                        memory_mb=mem_mb,
                        cwd=tmp_dir,
                    )
                    if run.timed_out:
                        killed = True
                        killed_by = in_file.name
                        verdict = "TLE"
                        break
                    if not run.success:
                        killed = True
                        killed_by = in_file.name
                        verdict = "RE"
                        break
                    if eff_checker and ans_path is not None:
                        out_file = Path(tmp_dir) / f"{mut_id}_out.txt"
                        out_file.write_text(run.stdout, encoding="utf-8")
                        chk_v, _ = await run_testlib_checker(
                            eff_checker,
                            str(in_file),
                            str(out_file),
                            str(ans_path),
                            timeout=int(max(5, per_test_timeout)),
                        )
                        if chk_v == "FAIL":
                            judge_error = True
                            verdict = "FAIL"
                            break
                        if chk_v != "AC":
                            killed = True
                            killed_by = in_file.name
                            verdict = chk_v
                            break
                    elif not _outputs_match_tokens(run.stdout, ans_text):
                        killed = True
                        killed_by = in_file.name
                        verdict = "WA"
                        break

            if budget_exhausted and not killed and not judge_error:
                details.append(
                    {
                        "id": mut_id,
                        "line": mut["line"],
                        "mutation_type": mut["mutation_type"],
                        "category": mut.get("category", "boundary"),
                        "status": "budget_exhausted",
                        "killed": False,
                    }
                )
                compiled_count -= 1
                break

            row = {
                "id": mut_id,
                "line": mut["line"],
                "mutation_type": mut["mutation_type"],
                "category": mut.get("category", "boundary"),
                "original": mut["original"],
                "mutated": mut["mutated"],
                "killed": killed,
                "killed_by": killed_by,
                "verdict": verdict,
                "status": "judge_error" if judge_error else ("killed" if killed else "survived"),
            }
            details.append(row)
            if judge_error:
                judge_error_count += 1
            elif killed:
                killed_count += 1
            else:
                survivors.append(row)

    evaluated_count = compiled_count - judge_error_count
    survived_count = len(survivors)
    kill_rate: float | None = (
        round(killed_count / evaluated_count, 4) if evaluated_count > 0 else None
    )
    return {
        "executed": True,
        "passed": survived_count == 0 and judge_error_count == 0,
        "backend": active_backend,
        "ast_backend": ast_backend,
        "equivalent_filtered": equivalent_filtered,
        "equivalent_mutants": equivalent_mutants,
        "total_mutants": len(mutants),
        "compiled_mutants": compiled_count,
        "killed_mutants": killed_count,
        "survived_mutants": survived_count,
        "judge_error_mutants": judge_error_count,
        "budget_exhausted": budget_exhausted,
        "kill_rate": kill_rate,
        "survivors": survivors,
        "details": details,
        **mull_info,
    }
