from __future__ import annotations

import asyncio
import importlib
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from ..utils.communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_participant_binaries,
)
from ..utils.execution_monitor import DynamicExecutionMonitor
from ..utils.judge_runner import InteractiveRunner, JudgeResult
from ..utils.platform import get_exe_extension
from ..utils.ratio_analyzer import EmpiricalRatioAnalyzer, normalize_complexity_expression
from ..utils.scale_sampler import MultiScaleSampler
from ..workflow import load_manifest, manifest_is_communication, manifest_is_interactive
from .base import Tool, ToolResult, input_schema_from_model
from .mixins import resolve_source
from .schemas import SolutionAnalyzeInput


class ComplexityLevel:
    CONSTANT = "O(1)"
    LOG_N = "O(log n)"
    LINEAR = "O(n)"
    N_LOG_N = "O(n log n)"
    N_SQRT_N = "O(n sqrt n)"
    QUADRATIC = "O(n^2)"
    CUBIC = "O(n^3)"
    EXPONENTIAL = "O(2^n)"
    FACTORIAL = "O(n!)"


COMPLEXITY_TO_N_MAX = {
    ComplexityLevel.CONSTANT: 10**9,
    ComplexityLevel.LOG_N: 10**9,
    ComplexityLevel.LINEAR: 10**7,
    ComplexityLevel.N_LOG_N: 10**6,
    ComplexityLevel.N_SQRT_N: 10**5,
    ComplexityLevel.QUADRATIC: 5000,
    ComplexityLevel.CUBIC: 500,
    ComplexityLevel.EXPONENTIAL: 20,
    ComplexityLevel.FACTORIAL: 12,
}

COMPLEXITY_TO_TIME_LIMIT = {
    ComplexityLevel.CONSTANT: 1000,
    ComplexityLevel.LOG_N: 1000,
    ComplexityLevel.LINEAR: 1000,
    ComplexityLevel.N_LOG_N: 2000,
    ComplexityLevel.N_SQRT_N: 2500,
    ComplexityLevel.QUADRATIC: 3000,
    ComplexityLevel.CUBIC: 5000,
    ComplexityLevel.EXPONENTIAL: 10000,
    ComplexityLevel.FACTORIAL: 10000,
}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    val = float(value)
    return val if math.isfinite(val) and val > 0 else None


def extract_constraint_numbers(constraints: dict[str, Any] | None) -> list[int]:
    """提取 constraints 中所有正整数上限（排除 bool 与非正数）。"""
    if not isinstance(constraints, dict):
        return []
    values: list[int] = []
    for value in constraints.values():
        parsed = _positive_int(value)
        if parsed is not None:
            values.append(parsed)
        elif isinstance(value, dict):
            for key in ("max", "maximum", "max_value", "n_max", "sum_max"):
                nested = _positive_int(value.get(key))
                if nested is not None:
                    values.append(nested)
    return values


def extract_dominant_var_max(
    constraints: dict[str, Any] | None,
    dominant_var: str = "n",
) -> int | None:
    """按指定主导变量名（如 n / T / k / m）从 constraints 中提取对应上限。"""
    if not isinstance(constraints, dict):
        return None
    var = (dominant_var or "n").strip() or "n"
    if var.lower() in ("n", "n_max"):
        return extract_n_max_from_constraints(constraints)

    var_forms = [var]
    if var.lower().endswith("_max") and len(var) > 4:
        var_forms.append(var[:-4])

    candidates: list[str] = []
    for v in var_forms:
        for cand in (
            f"{v}_max",
            f"{v.lower()}_max",
            f"{v.upper()}_max",
            f"sum_{v}_max",
            f"sum_{v.lower()}_max",
            f"sum_{v.upper()}_max",
            v,
            v.lower(),
            v.upper(),
        ):
            if cand not in candidates:
                candidates.append(cand)
    found: list[int] = []
    for key in candidates:
        raw = constraints.get(key)
        parsed = _positive_int(raw)
        if parsed is not None:
            found.append(parsed)
        elif isinstance(raw, dict):
            for subkey in ("max", "maximum", "max_value", "n_max", "sum_max"):
                nested = _positive_int(raw.get(subkey))
                if nested is not None:
                    found.append(nested)
    if found:
        return max(found)
    return None


def extract_n_max_from_constraints(constraints: dict[str, Any] | None) -> int | None:
    """优先按显式规模字段（n_max/sum_n_max/n，其次 m/q/k）提取主输入规模，避免被值域上限（如 a_i<=1e9）污染。"""
    if not isinstance(constraints, dict):
        return None

    def _collect(keys: tuple[str, ...]) -> list[int]:
        found: list[int] = []
        for key in keys:
            raw = constraints.get(key)
            parsed = _positive_int(raw)
            if parsed is not None:
                found.append(parsed)
            elif isinstance(raw, dict):
                for subkey in ("max", "maximum", "max_value", "n_max", "sum_max"):
                    nested = _positive_int(raw.get(subkey))
                    if nested is not None:
                        found.append(nested)
        return found

    primary_values = _collect(("n_max", "N_max", "sum_n_max", "sum_N_max", "n", "N"))
    if primary_values:
        return max(primary_values)
    secondary_values = _collect(
        ("m_max", "M_max", "q_max", "Q_max", "m", "M", "q", "Q", "k_max", "K_max", "k", "K")
    )
    if secondary_values:
        return max(secondary_values)
    fallback = extract_constraint_numbers(constraints)
    return max(fallback) if fallback else None


_COMPLEXITY_ORDER = [
    ComplexityLevel.CONSTANT,
    ComplexityLevel.LOG_N,
    ComplexityLevel.LINEAR,
    ComplexityLevel.N_LOG_N,
    ComplexityLevel.N_SQRT_N,
    ComplexityLevel.QUADRATIC,
    ComplexityLevel.CUBIC,
    ComplexityLevel.EXPONENTIAL,
    ComplexityLevel.FACTORIAL,
]


def _higher_complexity(current: str | None, candidate: str) -> str:
    if current is None:
        return candidate
    if current not in _COMPLEXITY_ORDER:
        return candidate
    if candidate not in _COMPLEXITY_ORDER:
        return current
    return candidate if _COMPLEXITY_ORDER.index(candidate) > _COMPLEXITY_ORDER.index(current) else current


_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def _strip_cpp_comments_and_strings(code: str) -> str:
    """Strip C++ comments and string/char literals while preserving line structure and C++14/20 digit separators."""
    result: list[str] = []
    i = 0
    n = len(code)
    in_block = False
    in_line = False
    in_str = False
    in_char = False
    while i < n:
        ch = code[i]
        nxt = code[i + 1] if i + 1 < n else ""
        if in_line:
            if ch == "\n":
                in_line = False
                result.append("\n")
            i += 1
        elif in_block:
            if ch == "*" and nxt == "/":
                in_block = False
                result.append(" ")
                i += 2
            else:
                if ch == "\n":
                    result.append("\n")
                i += 1
        elif in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
            elif ch == '"':
                in_str = False
                result.append('""')
                i += 1
            elif ch == "\n":
                in_str = False
                result.append("\n")
                i += 1
            else:
                i += 1
        elif in_char:
            if ch == "\\" and i + 1 < n:
                i += 2
            elif ch == "'":
                in_char = False
                result.append("''")
                i += 1
            elif ch == "\n":
                in_char = False
                result.append("\n")
                i += 1
            else:
                i += 1
        else:
            if ch == "/" and nxt == "/":
                in_line = True
                i += 2
            elif ch == "/" and nxt == "*":
                in_block = True
                i += 2
            elif ch == '"':
                in_str = True
                i += 1
            elif ch == "'":
                prev = code[i - 1] if i > 0 else ""
                if prev in _HEX_CHARS and nxt in _HEX_CHARS:
                    # C++14/20 digit separator (e.g. 1'000'000'007, 0xFF'FF)
                    result.append(ch)
                    i += 1
                else:
                    in_char = True
                    i += 1
            else:
                result.append(ch)
                i += 1
    return "".join(result)


_TOKEN_RE = re.compile(r"[A-Za-z_]\w*|[{}();]")

_DECL_TOKEN_RE = re.compile(r"::|->|[A-Za-z_]\w*|[{}();<>\[\]=,:]")

_CPP_CONTROL_KEYWORDS = frozenset(
    {
        "if",
        "for",
        "while",
        "switch",
        "catch",
        "else",
        "do",
        "return",
        "sizeof",
        "alignof",
        "decltype",
        "static_assert",
        "typedef",
        "using",
        "throw",
        "new",
        "delete",
    }
)

_CPP_CONTAINER_KEYWORDS = frozenset({"struct", "class", "namespace", "union", "enum"})

_STL_NON_USER_METHODS = frozenset(
    {
        "push",
        "pop",
        "push_back",
        "pop_back",
        "push_front",
        "pop_front",
        "emplace",
        "emplace_back",
        "top",
        "front",
        "back",
        "begin",
        "end",
        "rbegin",
        "rend",
        "size",
        "empty",
        "clear",
        "resize",
        "reserve",
        "assign",
        "insert",
        "erase",
        "find",
        "count",
        "lower_bound",
        "upper_bound",
        "binary_search",
        "tie",
        "sync_with_stdio",
        "sort",
        "stable_sort",
        "partial_sort",
        "reverse",
        "unique",
        "min",
        "max",
        "swap",
        "fill",
    }
)

_BSEARCH_CALLEES = frozenset({"lower_bound", "upper_bound", "binary_search"})

_LOG_STEP_RE = re.compile(
    r"(?:\+=|-=)\s*(?:lowbit\s*\([^)]+\)|([A-Za-z_]\w*)\s*&\s*-\s*\1\b)"
    r"|(?:<<=|>>=|\*=|/=)\s*(?:1|2|3|[2-9]\d*)"
    r"|&\s*=\s*\(?\s*[A-Za-z_]\w*\s*-\s*1\s*\)?"
    r"|\b([A-Za-z_]\w*)\s*=\s*(?:\(\s*\2\s*-\s*1\s*\)\s*&\s*\2|\2\s*&\s*\(\s*\2\s*-\s*1\s*\)|\2\s*(?:/|>>|<<|\*)\s*[12])"
)

_LOG_WHILE_COND_RE = re.compile(
    r"^(?:[A-Za-z_]\w*(?:\s*(?:>|!=|>=)\s*0)?|\(?\s*[A-Za-z_]\w*\s*<=?\s*[A-Za-z_]\w*\s*\)?)$"
)

_LOWBIT_BODY_RE = re.compile(
    r"(?:\+=|-=)\s*(?:lowbit\s*\([^)]+\)|([A-Za-z_]\w*)\s*&\s*-\s*\1\b)"
    r"|(?:>>=|/=|<<=|\*=)\s*(?:1|2)"
    r"|&\s*=\s*\(?\s*[A-Za-z_]\w*\s*-\s*1\s*\)?"
    r"|\b([A-Za-z_]\w*)\s*=\s*(?:\(\s*\2\s*-\s*1\s*\)\s*&\s*\2|\2\s*&\s*\(\s*\2\s*-\s*1\s*\))"
)

_BINARY_SEARCH_BODY_RE = re.compile(
    r"\b(?:mid|m)\b\s*=[^;\n]*(?:\+\s*[A-Za-z_]\w*\)?\s*(?:/\s*2|>>\s*1)|-\s*[A-Za-z_]\w*\)?\s*(?:/\s*2|>>\s*1))"
)


@dataclass(frozen=True)
class _CallSite:
    callee: str
    loop_depth: int
    for_depth: int
    any_loop_depth: int


@dataclass
class _FunctionAnalysis:
    name: str
    body: str
    own_max_depth: int = 0
    own_for_depth: int = 0
    own_saw_loop: bool = False
    own_has_nested_for: bool = False
    own_has_log_loop: bool = False
    has_log_loop_in_linear_loop: bool = False
    has_bsearch_in_loop: bool = False
    has_bsearch_top: bool = False
    calls: list[_CallSite] = field(default_factory=list)
    has_container_pop: bool = False
    is_divide_and_conquer: bool = False
    is_tree_graph_dfs: bool = False
    is_dsu_find: bool = False
    has_exp_recursion: bool = False


@dataclass(frozen=True)
class _InterproceduralSummary:
    backend: str
    max_depth: int
    saw_loop: bool
    has_nested_for: bool
    has_log_with_linear: bool
    has_only_log_loop: bool
    has_bsearch_in_loop: bool
    has_bsearch_top: bool
    has_container_pop: bool
    has_exponential_recursion: bool
    function_count: int
    call_graph_depth: int


_MULTITEST_HEADER_RE = re.compile(
    r"^(?:"
    r"(?:(?:--|\+\+)?\s*(?:T|t|tc|tt|tests?|cases?|q_cases?|t_cases?|test_cases?)\s*(?:--|\+\+)?(?:\s*(?:>|!=|>=)\s*0)?)"
    r"|(?:(?:int|long(?:\s+long)?|ll|size_t)\s+(?:tc|tt|cas|case_id|_)\s*=\s*[01]\s*;\s*(?:tc|tt|cas|case_id|_)\s*<=?\s*[A-Za-z_]\w*\s*;.*)"
    r"|(?:(?:int|long(?:\s+long)?|ll|size_t)\s+[A-Za-z_]\w*\s*=\s*[01]\s*;\s*[A-Za-z_]\w*\s*<=?\s*(?:T|tc|tt|tests?|cases?|q_cases?|t_cases?|test_cases?)\s*;.*)"
    r"|(?:(?:std::)?cin\s*>>.*)"
    r")$"
)

_ADJ_LIST_FOR_HEADER_RE = re.compile(
    r"^(?:"
    r"[^:;]+:\s*(?:g|adj|graph|tree|edges?|children|ch|son|nei|nbrs?)\s*\[\s*[A-Za-z_]\w*\s*\]"
    r"|[^;]*;\s*[A-Za-z_]\w*\s*<\s*(?:\(\s*int\s*\)\s*)?(?:g|adj|graph|tree|edges?|children|ch|son|nei|nbrs?)\s*\[\s*[A-Za-z_]\w*\s*\]\s*\.size\s*\(\s*\)\s*;[^;]*"
    r"|[^;]*=\s*head\s*\[\s*[A-Za-z_]\w*\s*\]\s*;[^;]*;\s*[A-Za-z_]\w*\s*=\s*nxt\s*\[\s*[A-Za-z_]\w*\s*\]"
    r")$"
)

_BITMASK_ASSIGN_RE = re.compile(
    r"(?:^|[;{}])\s*(?:(?:static|const|constexpr)\s+)*(?:(?:unsigned|signed)\s+)?"
    r"(?:(?:int|long\s+long|long|size_t|ll|auto|uint32_t|uint64_t|int32_t|int64_t)\s+)?"
    r"([A-Za-z_]\w*)\s*=\s*\(?\s*1(?:[uUlL]{0,3})\s*<<\s*\(?\s*([A-Za-z_]\w*|\d+)\s*\)?"
    r"(?:\s*[+-]\s*1)?\s*\)?\s*;"
)


def _extract_paren_expr(code: str, open_idx: int) -> tuple[str, int]:
    """Return (inner_text, index_after_closing_paren) for balanced parens starting at open_idx."""
    depth = 0
    i = open_idx
    n = len(code)
    while i < n:
        ch = code[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return code[open_idx + 1 : i].strip(), i + 1
        i += 1
    return code[open_idx + 1 :].strip(), n


def _extract_brace_expr(code: str, open_idx: int) -> tuple[str, int]:
    """Return (inner_text, index_after_closing_brace) for balanced braces starting at open_idx."""
    depth = 0
    i = open_idx
    n = len(code)
    while i < n:
        ch = code[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return code[open_idx + 1 : i], i + 1
        i += 1
    return code[open_idx + 1 :], n


def _is_multitest_driver_header(
    header: str,
    *,
    preceding_text: str = "",
    body_preview: str = "",
) -> bool:
    s = " ".join(header.strip().split())
    if not s:
        return False
    if _MULTITEST_HEADER_RE.match(s):
        return True
    # Contextual detection for lowercase/custom test-count variable read right before top-level loop
    if preceding_text and body_preview:
        _scale_names = {"n", "N", "m", "M", "k", "K", "q", "Q", "len", "sz", "size", "cnt", "num"}
        loop_var: str | None = None
        is_while_dec = False
        m_while = re.match(r"^(?:--|\+\+)?\s*([A-Za-z_]\w*)\s*(?:--|\+\+)?(?:\s*(?:>|!=|>=)\s*0)?$", s)
        if m_while:
            loop_var = m_while.group(1)
            is_while_dec = "--" in s
        else:
            m_for = re.match(
                r"^(?:int|long(?:\s+long)?|ll|size_t)\s+[A-Za-z_]\w*\s*=\s*[01]\s*;\s*[A-Za-z_]\w*\s*<=?\s*([A-Za-z_]\w*)\s*;",
                s,
            )
            if m_for:
                loop_var = m_for.group(1)
        if loop_var and loop_var not in _scale_names:
            esc = re.escape(loop_var)
            tail = preceding_text[-180:]
            was_read = bool(
                re.search(rf"\b(?:std::)?cin\s*(?:>>\s*[A-Za-z_]\w*\s*)*>>\s*{esc}\b", tail)
                or re.search(rf"\bscanf\s*\([^)]*&\s*{esc}\b", tail)
            )
            body_invokes_case = bool(re.search(r"\b(?:solve|work|run|process)\s*\(", body_preview))
            body_reads_new_scale = bool(
                re.search(r"\b(?:int|long(?:\s+long)?|ll)\s+[A-Za-z_]\w*\s*;[^;]*(?:cin\s*>>|scanf\s*\()", body_preview)
            )
            if was_read and (body_invokes_case or body_reads_new_scale or is_while_dec):
                return True
    return False


def _is_adj_list_for_header(header: str) -> bool:
    s = " ".join(header.strip().split())
    return bool(s and _ADJ_LIST_FOR_HEADER_RE.match(s))


def _is_small_constant_for_header(header: str) -> bool:
    """Detect O(1) fixed-bound loops like `for (int d = 0; d < 4; d++)` or `for (int b = 29; b >= 0; b--)`."""
    parts = [p.strip() for p in header.split(";")]
    if len(parts) != 3:
        return False
    cond_clauses = [c.strip() for c in parts[1].split("&&") if c.strip()]
    for clause in cond_clauses:
        m_asc = re.match(r"^[A-Za-z_]\w*\s*(<=?)\s*(\d+)$", clause)
        if m_asc:
            bound = int(m_asc.group(2))
            if 0 < bound <= 32:
                return True
    m_desc_init = re.search(r"=\s*(\d+)\s*$", parts[0])
    if m_desc_init:
        bound = int(m_desc_init.group(1))
        if 0 < bound <= 32 and any(
            re.match(r"^[A-Za-z_]\w*\s*(>=?)\s*0$", clause) for clause in cond_clauses
        ):
            return True
    return False


def _is_logarithmic_loop(kind: str, header: str, body_preview: str) -> bool:
    """Detect logarithmic loops (Fenwick lowbit, halving/doubling, binary search)."""
    normalized_hdr = " ".join(header.split())
    if kind == "for":
        parts = [p.strip() for p in normalized_hdr.split(";")]
        if len(parts) == 3 and _LOG_STEP_RE.search(parts[2]):
            return True
        if len(parts) == 3 and not parts[2] and _LOWBIT_BODY_RE.search(body_preview):
            return True
        return False
    if kind == "while" and _LOG_WHILE_COND_RE.match(normalized_hdr):
        if _BINARY_SEARCH_BODY_RE.search(body_preview):
            return True
        cond_var_m = re.match(r"^\(?\s*([A-Za-z_]\w*)\b", normalized_hdr)
        if cond_var_m and _LOWBIT_BODY_RE.search(body_preview):
            return True
    return False


def _extract_loop_body_preview(code: str, start_idx: int) -> str:
    """Extract the immediate statement/block text following a loop header for heuristic inspection."""
    n = len(code)
    idx = start_idx
    while idx < n and code[idx].isspace():
        idx += 1
    if idx >= n:
        return ""
    if code[idx] == "{":
        inner, _ = _extract_brace_expr(code, idx)
        return inner[:400]
    semi = code.find(";", idx)
    if semi != -1:
        return code[idx : min(semi + 1, idx + 400)]
    return code[idx : idx + 200]


def _classify_function_recursion_role(name: str, body: str) -> tuple[bool, bool, bool, bool]:
    """Return (is_dnc_segtree, is_tree_graph_dfs, is_dsu_find, has_exp_recursion) for a function body."""
    non_exp_name_re = re.compile(
        r"(?:merge|sort|qsort|quick|cdq|build|query|update|modify|push|seg|tarjan|scc|lca)",
        re.IGNORECASE,
    )
    self_calls = re.findall(rf"\b{re.escape(name)}\s*\(", body)
    if not self_calls:
        return False, False, False, False

    is_dsu_find = bool(
        re.search(
            rf"\b(?:fa|f|p|par|parent)\s*\[[^\]]+\]\s*==[^;?]*\?[^:;]*:\s*(?:fa|f|p|par|parent)\s*\[[^\]]+\]\s*=\s*{re.escape(name)}\s*\(",
            body,
        )
        or (
            name.lower() in {"find", "getf", "get_root", "find_set"}
            and not re.search(r"\b(?:for|while)\s*\(", body)
        )
    )
    is_dnc_segtree = bool(
        non_exp_name_re.search(name)
        or re.search(r"\b(?:mid|dp|memo|cache|lson|rson)\b", body)
        or re.search(r"(?:/\s*2|>>\s*1|<<\s*1)\b", body)
        or re.search(r"\b(?:ch|son|lc|rc|ls|rs|low|dfn)\s*\[", body)
        or re.search(r"(?:->|\.)\s*(?:left|right)\b", body)
    )
    has_tree_or_tarjan_guard = bool(
        re.search(r"!=\s*(?:p|fa|par|parent|prev)\b", body)
        or re.search(r"\bin_?stack\b", body)
    )
    is_tree_graph_dfs = bool(
        (
            has_tree_or_tarjan_guard
            or re.search(r"\b(?:vis|visited|adj|edges?|head|nxt|tree)\b|\bg\s*\[", body)
        )
        and re.search(r"\b(?:for|while)\s*\(", body)
    )

    has_exp_recursion = False
    if not is_dnc_segtree and not is_dsu_find and name not in {"main", "if", "for", "while", "switch", "catch", "else"}:
        has_backtrack_undo = bool(
            re.search(
                r"\b(?:used|vis|visited|chosen|picked|mark)\s*\[[^\]]+\]\s*=\s*(?:0|false)\b",
                body,
            )
            or (re.search(r"\.pop_back\s*\(\s*\)", body) and not has_tree_or_tarjan_guard)
        )
        if has_backtrack_undo and re.search(r"\b(?:for|while)\s*\(", body):
            has_exp_recursion = True
        elif (
            len(self_calls) >= 2
            and not has_tree_or_tarjan_guard
            and not re.search(r"\b(?:vis|visited|adj|edges?|head|left|right)\b", body)
        ):
            has_exp_recursion = True

    return is_dnc_segtree, is_tree_graph_dfs, is_dsu_find, has_exp_recursion


def _extract_named_lambdas_python(body: str) -> tuple[str, list[tuple[str, str]]]:
    """Extract `auto name = [...](...) { ... }` lambdas from a body and blank out their bodies."""
    tok_iter = list(_DECL_TOKEN_RE.finditer(body))
    total = len(tok_iter)
    spans: list[tuple[int, int]] = []
    extracted: list[tuple[str, str]] = []
    i = 0
    while i + 3 < total:
        tok = tok_iter[i].group(0)
        if (
            tok.isidentifier()
            and tok not in _CPP_CONTROL_KEYWORDS
            and tok_iter[i + 1].group(0) == "="
            and tok_iter[i + 2].group(0) == "["
        ):
            j = i + 2
            bracket_depth = 0
            while j < total:
                t = tok_iter[j].group(0)
                if t == "[":
                    bracket_depth += 1
                elif t == "]":
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        j += 1
                        break
                j += 1
            if j < total and tok_iter[j].group(0) == "(":
                paren_depth = 0
                while j < total:
                    t = tok_iter[j].group(0)
                    if t == "(":
                        paren_depth += 1
                    elif t == ")":
                        paren_depth -= 1
                        if paren_depth == 0:
                            j += 1
                            break
                    j += 1
            while j < total and tok_iter[j].group(0) not in ("{", ";"):
                j += 1
            if j < total and tok_iter[j].group(0) == "{":
                open_char = tok_iter[j].start()
                lambda_body, end_char = _extract_brace_expr(body, open_char)
                extracted.append((tok, lambda_body))
                spans.append((open_char, end_char))
                while j < total and tok_iter[j].start() < end_char:
                    j += 1
                i = j
                continue
        i += 1

    if not spans:
        return body, []
    chars = list(body)
    for start_c, end_c in spans:
        for pos in range(start_c, min(end_c, len(chars))):
            if chars[pos] != "\n":
                chars[pos] = " "
    return "".join(chars), extracted


def _analyze_body_into_function(
    name: str,
    body: str,
    *,
    ignore_multitest: bool,
    ignore_small_const: bool,
) -> _FunctionAnalysis:
    """Analyze loops, logarithmic loops, and call sites inside a single function body."""
    tok_iter = list(_TOKEN_RE.finditer(body))
    total = len(tok_iter)

    info = _FunctionAnalysis(
        name=name,
        body=body,
        has_container_pop=bool(re.search(r"\.(?:pop|pop_front|pop_back)\s*\(", body)),
    )
    (
        info.is_divide_and_conquer,
        info.is_tree_graph_dfs,
        info.is_dsu_find,
        info.has_exp_recursion,
    ) = _classify_function_recursion_role(name, body)

    # Stack entries: (kind, counts_toward_depth, is_for, is_log)
    frames: list[tuple[str, bool, bool, bool]] = []
    expecting_do_while = False

    def _active_nonlog_depth() -> int:
        return sum(
            1
            for k, counts, _, is_log in frames
            if counts and not is_log and k in ("brace_loop", "do_brace", "single_loop", "single_do")
        )

    def _active_any_depth() -> int:
        return sum(
            1
            for k, counts, _, _ in frames
            if counts and k in ("brace_loop", "do_brace", "single_loop", "single_do")
        )

    def _active_for_depth() -> int:
        return sum(
            1
            for k, counts, is_f, is_log in frames
            if counts and not is_log and is_f and k in ("brace_loop", "single_loop")
        )

    def _has_active_log_loop() -> bool:
        return any(
            counts and is_log and k in ("brace_loop", "do_brace", "single_loop", "single_do")
            for k, counts, _, is_log in frames
        )

    def _unwind_single_loops(next_tok: str | None) -> None:
        nonlocal expecting_do_while
        if next_tok == "else":
            return
        while frames:
            top_k, _, _, _ = frames[-1]
            if top_k == "single_loop":
                frames.pop()
            elif top_k == "single_do":
                frames.pop()
                expecting_do_while = True
                break
            else:
                break

    def _skip_parens_idx(pos: int) -> int:
        depth = 0
        while pos < total:
            t = tok_iter[pos].group(0)
            if t == "(":
                depth += 1
            elif t == ")":
                depth -= 1
                if depth == 0:
                    return pos + 1
            pos += 1
        return total

    i = 0
    while i < total:
        tok = tok_iter[i].group(0)

        if tok in ("for", "while"):
            if tok == "while" and expecting_do_while:
                expecting_do_while = False
                i += 1
                if i < total and tok_iter[i].group(0) == "(":
                    i = _skip_parens_idx(i)
                if i < total and tok_iter[i].group(0) == ";":
                    i += 1
                _unwind_single_loops(tok_iter[i].group(0) if i < total else None)
                continue

            if i + 1 < total and tok_iter[i + 1].group(0) == "(":
                info.own_saw_loop = True
                is_for = tok == "for"
                header_str, after_hdr_char = _extract_paren_expr(body, tok_iter[i + 1].start())
                body_preview = _extract_loop_body_preview(body, after_hdr_char)
                preceding = body[max(0, tok_iter[i].start() - 180) : tok_iter[i].start()]
                counts = True
                if (
                    ignore_multitest
                    and _active_any_depth() == 0
                    and _is_multitest_driver_header(
                        header_str,
                        preceding_text=preceding if name in ("main", "<toplevel>") else "",
                        body_preview=body_preview,
                    )
                ):
                    counts = False
                elif ignore_small_const and is_for and _is_small_constant_for_header(header_str):
                    counts = False
                elif (
                    ignore_small_const
                    and is_for
                    and _active_nonlog_depth() == 1
                    and _is_adj_list_for_header(header_str)
                ):
                    counts = False
                is_log = bool(counts and ignore_small_const and _is_logarithmic_loop(tok, header_str, body_preview))
                if counts:
                    if is_log:
                        info.own_has_log_loop = True
                        if _active_nonlog_depth() >= 1:
                            info.has_log_loop_in_linear_loop = True
                    else:
                        current_depth = _active_nonlog_depth() + 1
                        info.own_max_depth = max(info.own_max_depth, current_depth)
                        if is_for:
                            current_for = _active_for_depth() + 1
                            info.own_for_depth = max(info.own_for_depth, current_for)
                            if _active_for_depth() >= 1:
                                info.own_has_nested_for = True
                        if _has_active_log_loop():
                            info.has_log_loop_in_linear_loop = True
                i = _skip_parens_idx(i + 1)
                if i < total and tok_iter[i].group(0) == ";":
                    i += 1
                    _unwind_single_loops(tok_iter[i].group(0) if i < total else None)
                elif i < total and tok_iter[i].group(0) == "{":
                    frames.append(("brace_loop", counts, is_for, is_log))
                    i += 1
                else:
                    frames.append(("single_loop", counts, is_for, is_log))
                continue

        elif tok == "do":
            if i + 1 < total and tok_iter[i + 1].group(0) != "(":
                info.own_saw_loop = True
                current_depth = _active_nonlog_depth() + 1
                info.own_max_depth = max(info.own_max_depth, current_depth)
                i += 1
                if i < total and tok_iter[i].group(0) == "{":
                    frames.append(("do_brace", True, False, False))
                    i += 1
                else:
                    frames.append(("single_do", True, False, False))
                continue

        elif tok in ("if", "switch"):
            if i + 1 < total and tok_iter[i + 1].group(0) == "(":
                i = _skip_parens_idx(i + 1)
                continue

        elif tok == "{":
            frames.append(("brace_other", False, False, False))
            i += 1
            continue

        elif tok == "}":
            popped_kind = None
            while frames:
                top_k, _, _, _ = frames.pop()
                if top_k in ("brace_loop", "do_brace", "brace_other"):
                    popped_kind = top_k
                    break
            i += 1
            if popped_kind == "do_brace":
                expecting_do_while = True
            else:
                _unwind_single_loops(tok_iter[i].group(0) if i < total else None)
            continue

        elif tok == ";":
            i += 1
            _unwind_single_loops(tok_iter[i].group(0) if i < total else None)
            continue

        elif (
            tok.isidentifier()
            and tok not in _CPP_CONTROL_KEYWORDS
            and i + 1 < total
            and tok_iter[i + 1].group(0) == "("
        ):
            cur_nonlog = _active_nonlog_depth()
            cur_any = _active_any_depth()
            cur_for = _active_for_depth()
            if tok in _BSEARCH_CALLEES:
                if cur_any > 0:
                    info.has_bsearch_in_loop = True
                else:
                    info.has_bsearch_top = True
            elif tok not in _STL_NON_USER_METHODS:
                info.calls.append(
                    _CallSite(
                        callee=tok,
                        loop_depth=cur_nonlog,
                        for_depth=cur_for,
                        any_loop_depth=cur_any,
                    )
                )

        i += 1

    return info


def _extract_functions_python(
    cleaned: str,
    *,
    ignore_multitest: bool,
    ignore_small_const: bool,
) -> dict[str, _FunctionAnalysis]:
    """Extract function and named-lambda definitions from comment-stripped C++ using brace scopes."""
    tok_iter = list(_DECL_TOKEN_RE.finditer(cleaned))
    total = len(tok_iter)
    functions: dict[str, _FunctionAnalysis] = {}
    scope_stack: list[int] = []  # brace depth of container scopes
    brace_depth = 0
    pending_container = False

    def _register_fn(fn_name: str, raw_body: str) -> None:
        stripped_body, lambdas = _extract_named_lambdas_python(raw_body)
        fn_analysis = _analyze_body_into_function(
            fn_name,
            stripped_body,
            ignore_multitest=ignore_multitest,
            ignore_small_const=ignore_small_const,
        )
        key = fn_name
        if key in functions:
            suffix = 2
            while f"{key}#{suffix}" in functions:
                suffix += 1
            key = f"{key}#{suffix}"
            fn_analysis.name = key
        functions[key] = fn_analysis
        for lam_name, lam_body in lambdas:
            _register_fn(lam_name, lam_body)

    i = 0
    while i < total:
        tok = tok_iter[i].group(0)
        if tok in _CPP_CONTAINER_KEYWORDS:
            pending_container = True
            i += 1
            continue
        if tok == "{":
            brace_depth += 1
            if pending_container:
                scope_stack.append(brace_depth)
                pending_container = False
            i += 1
            continue
        if tok == "}":
            if scope_stack and scope_stack[-1] == brace_depth:
                scope_stack.pop()
            brace_depth = max(0, brace_depth - 1)
            i += 1
            continue
        if tok == ";":
            pending_container = False
            i += 1
            continue

        in_decl_scope = brace_depth == (scope_stack[-1] if scope_stack else 0)
        if (
            in_decl_scope
            and not pending_container
            and tok.isidentifier()
            and tok not in _CPP_CONTROL_KEYWORDS
            and i + 1 < total
            and tok_iter[i + 1].group(0) == "("
        ):
            # Look back to avoid matching variable initialization inside stray expressions
            prev_tok = tok_iter[i - 1].group(0) if i > 0 else ""
            if prev_tok not in {"=", "return", ",", "("}:
                j = i + 1
                pdepth = 0
                while j < total:
                    t = tok_iter[j].group(0)
                    if t == "(":
                        pdepth += 1
                    elif t == ")":
                        pdepth -= 1
                        if pdepth == 0:
                            j += 1
                            break
                    j += 1
                # Skip cv/noexcept/trailing-return qualifiers or constructor initializer list
                saw_colon = False
                while j < total:
                    t = tok_iter[j].group(0)
                    if t == "{":
                        break
                    if t in {";", "=", ","}:
                        j = -1
                        break
                    if t == ":" and scope_stack:
                        saw_colon = True
                    elif t == "(" and not saw_colon:
                        # Likely something like macro/call, skip parens safely
                        sub_d = 0
                        while j < total:
                            st = tok_iter[j].group(0)
                            if st == "(":
                                sub_d += 1
                            elif st == ")":
                                sub_d -= 1
                                if sub_d == 0:
                                    break
                            j += 1
                    j += 1
                if j != -1 and j < total and tok_iter[j].group(0) == "{":
                    open_char = tok_iter[j].start()
                    body_text, end_char = _extract_brace_expr(cleaned, open_char)
                    _register_fn(tok, body_text)
                    while i < total and tok_iter[i].start() < end_char:
                        i += 1
                    continue

        i += 1

    if not functions and cleaned.strip():
        _register_fn("<toplevel>", cleaned)
    return functions


def _load_tree_sitter_cpp() -> tuple[Any, Any, Any] | None:
    """Dynamically load optional `tree_sitter` and `tree_sitter_cpp` without static import errors."""
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


def _cst_extract_leaf_name(node: Any, code_bytes: bytes) -> str | None:
    """Extract simple identifier name from a tree-sitter declarator or callee node."""
    if node is None:
        return None
    ntype = getattr(node, "type", "")
    if ntype in {"identifier", "field_identifier", "type_identifier"}:
        return code_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace").strip()
    if ntype == "qualified_identifier":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _cst_extract_leaf_name(name_node, code_bytes)
    if ntype == "field_expression":
        field_node = node.child_by_field_name("field")
        if field_node is not None:
            return _cst_extract_leaf_name(field_node, code_bytes)
    if ntype == "template_function":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _cst_extract_leaf_name(name_node, code_bytes)
    if ntype in {"function_declarator", "pointer_declarator", "reference_declarator"}:
        decl_node = node.child_by_field_name("declarator")
        if decl_node is not None:
            return _cst_extract_leaf_name(decl_node, code_bytes)
    for child in getattr(node, "children", ()):
        leaf = _cst_extract_leaf_name(child, code_bytes)
        if leaf:
            return leaf
    return None


def _cst_node_text(node: Any, code_bytes: bytes) -> str:
    if node is None:
        return ""
    return code_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _analyze_functions_tree_sitter(
    code: str,
    *,
    ignore_multitest: bool,
    ignore_small_const: bool,
) -> dict[str, _FunctionAnalysis] | None:
    """Analyze C++ functions & call graph using optional `tree-sitter-cpp` CST."""
    loaded = _load_tree_sitter_cpp()
    if loaded is None:
        return None
    language_cls, parser_cls, lang_fn = loaded
    try:
        cpp_lang = language_cls(lang_fn())
        parser = parser_cls(cpp_lang)
        code_bytes = code.encode("utf-8", errors="replace")
        tree = parser.parse(code_bytes)
        root = tree.root_node
        if getattr(root, "has_error", False):
            return None
    except Exception:
        return None

    functions: dict[str, _FunctionAnalysis] = {}

    def _walk_body_cst(
        node: Any,
        info: _FunctionAnalysis,
        *,
        nonlog_depth: int,
        any_depth: int,
        for_depth: int,
        has_log_ancestor: bool,
    ) -> None:
        ntype = getattr(node, "type", "")
        if ntype in {"for_statement", "for_range_loop", "while_statement", "do_statement"}:
            info.own_saw_loop = True
            is_for = ntype in {"for_statement", "for_range_loop"}
            body_node = node.child_by_field_name("body")
            body_text = _cst_node_text(body_node, code_bytes)
            counts = True
            is_log = False

            if ntype == "for_statement":
                hdr_end = body_node.start_byte if body_node is not None else node.end_byte
                raw_hdr = code_bytes[node.start_byte : hdr_end].decode("utf-8", errors="replace")
                open_p = raw_hdr.find("(")
                close_p = raw_hdr.rfind(")")
                hdr_str = raw_hdr[open_p + 1 : close_p] if open_p != -1 and close_p > open_p else ""
                prec_text = code_bytes[max(0, node.start_byte - 180) : node.start_byte].decode(
                    "utf-8", errors="replace"
                )
                if (
                    ignore_multitest
                    and any_depth == 0
                    and _is_multitest_driver_header(
                        hdr_str,
                        preceding_text=prec_text if info.name == "main" else "",
                        body_preview=body_text[:400],
                    )
                ):
                    counts = False
                elif ignore_small_const and _is_small_constant_for_header(hdr_str):
                    counts = False
                elif ignore_small_const and nonlog_depth == 1 and _is_adj_list_for_header(hdr_str):
                    counts = False
                is_log = bool(counts and ignore_small_const and _is_logarithmic_loop("for", hdr_str, body_text))
            elif ntype == "for_range_loop":
                hdr_end = body_node.start_byte if body_node is not None else node.end_byte
                raw_hdr = code_bytes[node.start_byte : hdr_end].decode("utf-8", errors="replace")
                open_p = raw_hdr.find("(")
                close_p = raw_hdr.rfind(")")
                hdr_str = raw_hdr[open_p + 1 : close_p] if open_p != -1 and close_p > open_p else ""
                if ignore_small_const and nonlog_depth == 1 and _is_adj_list_for_header(hdr_str):
                    counts = False
            elif ntype == "while_statement":
                cond_node = node.child_by_field_name("condition")
                cond_str = _cst_node_text(cond_node, code_bytes).strip()
                if cond_str.startswith("(") and cond_str.endswith(")"):
                    cond_str = cond_str[1:-1].strip()
                prec_text = code_bytes[max(0, node.start_byte - 180) : node.start_byte].decode(
                    "utf-8", errors="replace"
                )
                if (
                    ignore_multitest
                    and any_depth == 0
                    and _is_multitest_driver_header(
                        cond_str,
                        preceding_text=prec_text if info.name == "main" else "",
                        body_preview=body_text[:400],
                    )
                ):
                    counts = False
                is_log = bool(counts and ignore_small_const and _is_logarithmic_loop("while", cond_str, body_text))

            next_nonlog = nonlog_depth
            next_any = any_depth
            next_for = for_depth
            next_log_anc = has_log_ancestor
            if counts:
                next_any = any_depth + 1
                if is_log:
                    info.own_has_log_loop = True
                    next_log_anc = True
                    if nonlog_depth >= 1:
                        info.has_log_loop_in_linear_loop = True
                else:
                    next_nonlog = nonlog_depth + 1
                    info.own_max_depth = max(info.own_max_depth, next_nonlog)
                    if is_for:
                        next_for = for_depth + 1
                        info.own_for_depth = max(info.own_for_depth, next_for)
                        if for_depth >= 1:
                            info.own_has_nested_for = True
                    if has_log_ancestor:
                        info.has_log_loop_in_linear_loop = True

            for child in getattr(node, "children", ()):
                _walk_body_cst(
                    child,
                    info,
                    nonlog_depth=next_nonlog,
                    any_depth=next_any,
                    for_depth=next_for,
                    has_log_ancestor=next_log_anc,
                )
            return

        if ntype == "init_declarator":
            val_node = node.child_by_field_name("value")
            decl_node = node.child_by_field_name("declarator")
            if val_node is not None and getattr(val_node, "type", "") == "lambda_expression":
                lam_name = _cst_extract_leaf_name(decl_node, code_bytes)
                lam_body_node = val_node.child_by_field_name("body")
                if lam_name and lam_body_node is not None:
                    _register_cst_fn(lam_name, lam_body_node)
                    return

        if ntype == "call_expression":
            fn_node = node.child_by_field_name("function")
            callee = _cst_extract_leaf_name(fn_node, code_bytes)
            if callee:
                if callee in _BSEARCH_CALLEES:
                    if any_depth > 0:
                        info.has_bsearch_in_loop = True
                    else:
                        info.has_bsearch_top = True
                elif callee not in _STL_NON_USER_METHODS:
                    info.calls.append(
                        _CallSite(
                            callee=callee,
                            loop_depth=nonlog_depth,
                            for_depth=for_depth,
                            any_loop_depth=any_depth,
                        )
                    )

        for child in getattr(node, "children", ()):
            _walk_body_cst(
                child,
                info,
                nonlog_depth=nonlog_depth,
                any_depth=any_depth,
                for_depth=for_depth,
                has_log_ancestor=has_log_ancestor,
            )

    def _register_cst_fn(fn_name: str, body_node: Any) -> None:
        body_text = _strip_cpp_comments_and_strings(_cst_node_text(body_node, code_bytes))
        info = _FunctionAnalysis(
            name=fn_name,
            body=body_text,
            has_container_pop=bool(re.search(r"\.(?:pop|pop_front|pop_back)\s*\(", body_text)),
        )
        (
            info.is_divide_and_conquer,
            info.is_tree_graph_dfs,
            info.is_dsu_find,
            info.has_exp_recursion,
        ) = _classify_function_recursion_role(fn_name, body_text)
        _walk_body_cst(
            body_node,
            info,
            nonlog_depth=0,
            any_depth=0,
            for_depth=0,
            has_log_ancestor=False,
        )
        key = fn_name
        if key in functions:
            suffix = 2
            while f"{key}#{suffix}" in functions:
                suffix += 1
            key = f"{key}#{suffix}"
            info.name = key
        functions[key] = info

    def _collect_top_functions(node: Any) -> None:
        ntype = getattr(node, "type", "")
        if ntype == "function_definition":
            decl_node = node.child_by_field_name("declarator")
            body_node = node.child_by_field_name("body")
            fn_name = _cst_extract_leaf_name(decl_node, code_bytes)
            if fn_name and body_node is not None:
                _register_cst_fn(fn_name, body_node)
                return
        if ntype == "init_declarator":
            val_node = node.child_by_field_name("value")
            decl_node = node.child_by_field_name("declarator")
            if val_node is not None and getattr(val_node, "type", "") == "lambda_expression":
                lam_name = _cst_extract_leaf_name(decl_node, code_bytes)
                lam_body_node = val_node.child_by_field_name("body")
                if lam_name and lam_body_node is not None:
                    _register_cst_fn(lam_name, lam_body_node)
                    return
        for child in getattr(node, "children", ()):
            _collect_top_functions(child)

    _collect_top_functions(root)
    if not functions:
        return None
    return functions


def _solve_call_graph(
    functions: dict[str, _FunctionAnalysis],
    *,
    backend: str,
) -> _InterproceduralSummary:
    """Propagate loop depths, logarithmic factors, and binary-search call context over the call graph."""
    if not functions:
        return _InterproceduralSummary(
            backend=backend,
            max_depth=0,
            saw_loop=False,
            has_nested_for=False,
            has_log_with_linear=False,
            has_only_log_loop=False,
            has_bsearch_in_loop=False,
            has_bsearch_top=False,
            has_container_pop=False,
            has_exponential_recursion=False,
            function_count=0,
            call_graph_depth=0,
        )

    # Map base function names (supporting overloaded `#2` suffixes cleanly)
    base_to_keys: dict[str, list[str]] = {}
    for key in functions:
        base = key.split("#", 1)[0]
        base_to_keys.setdefault(base, []).append(key)

    def _resolve_callees(callee_name: str) -> list[str]:
        if callee_name in functions:
            return [callee_name]
        return base_to_keys.get(callee_name, [])

    adj: dict[str, set[str]] = {fn: set() for fn in functions}
    for fn, info in functions.items():
        for call in info.calls:
            for target in _resolve_callees(call.callee):
                adj[fn].add(target)

    # Tarjan SCC decomposition (returns SCCs in reverse topological order: callees first)
    index_counter = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []

    def _strongconnect(node: str) -> None:
        nonlocal index_counter
        indices[node] = index_counter
        lowlink[node] = index_counter
        index_counter += 1
        stack.append(node)
        on_stack.add(node)
        for nxt in adj[node]:
            if nxt not in indices:
                _strongconnect(nxt)
                lowlink[node] = min(lowlink[node], lowlink[nxt])
            elif nxt in on_stack:
                lowlink[node] = min(lowlink[node], indices[nxt])
        if lowlink[node] == indices[node]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                comp.append(w)
                if w == node:
                    break
            sccs.append(comp)

    for fn in functions:
        if fn not in indices:
            _strongconnect(fn)

    scc_of: dict[str, int] = {}
    for scc_idx, comp in enumerate(sccs):
        for fn in comp:
            scc_of[fn] = scc_idx

    eff_depth: dict[str, int] = {}
    eff_for_depth: dict[str, int] = {}
    eff_nested_for: dict[str, bool] = {}
    eff_log_loop: dict[str, bool] = {}
    eff_log_with_linear: dict[str, bool] = {}
    eff_bsearch_any: dict[str, bool] = {}
    eff_bsearch_in_loop: dict[str, bool] = {}
    eff_chain_height: dict[str, int] = {}

    for scc_idx, comp in enumerate(sccs):
        comp_set = set(comp)
        is_cyclic = len(comp) > 1 or any(comp[0] in adj[comp[0]] for _ in (0,))

        base_depth = max(functions[f].own_max_depth for f in comp)
        base_for_depth = max(functions[f].own_for_depth for f in comp)
        base_nested_for = any(functions[f].own_has_nested_for for f in comp)
        base_log = any(functions[f].own_has_log_loop for f in comp)
        base_log_linear = any(functions[f].has_log_loop_in_linear_loop for f in comp)
        base_bsearch_any = any(functions[f].has_bsearch_top or functions[f].has_bsearch_in_loop for f in comp)
        base_bsearch_in_loop = any(functions[f].has_bsearch_in_loop for f in comp)
        max_callee_chain = 0

        for fn in comp:
            info = functions[fn]
            for call in info.calls:
                for target in _resolve_callees(call.callee):
                    if scc_of[target] == scc_idx:
                        if call.any_loop_depth > 0 and eff_bsearch_any.get(target, False):
                            base_bsearch_in_loop = True
                        continue
                    t_depth = eff_depth[target]
                    t_for = eff_for_depth[target]
                    max_callee_chain = max(max_callee_chain, 1 + eff_chain_height[target])
                    if t_depth > 0:
                        base_depth = max(base_depth, call.loop_depth + t_depth)
                    if t_for > 0:
                        base_for_depth = max(base_for_depth, call.for_depth + t_for)
                        if call.for_depth >= 1:
                            base_nested_for = True
                    if eff_nested_for[target]:
                        base_nested_for = True
                    if eff_log_loop[target]:
                        base_log = True
                        if call.loop_depth >= 1:
                            base_log_linear = True
                    if call.any_loop_depth > call.loop_depth and t_depth >= 1:
                        # Linear helper called inside a logarithmic loop (e.g. binary search on answer)
                        base_log_linear = True
                    if eff_log_with_linear[target]:
                        base_log_linear = True
                    if eff_bsearch_any[target]:
                        base_bsearch_any = True
                        if call.any_loop_depth >= 1:
                            base_bsearch_in_loop = True
                    if eff_bsearch_in_loop[target]:
                        base_bsearch_in_loop = True

        if is_cyclic:
            any_dnc = any(functions[f].is_divide_and_conquer for f in comp)
            any_dfs = any(functions[f].is_tree_graph_dfs for f in comp)
            any_dsu = any(functions[f].is_dsu_find for f in comp)
            if any_dnc or any_dsu:
                pass
            elif any_dfs:
                # Tree/graph DFS visits each node once (own edge-loop counts as depth 1, not 1+1),
                # but calling an external O(n) helper inside DFS runs O(n) per node -> depth >= 2.
                ext_callee_depth = 0
                for fn in comp:
                    for call in functions[fn].calls:
                        for target in _resolve_callees(call.callee):
                            if scc_of[target] != scc_idx:
                                ext_callee_depth = max(ext_callee_depth, eff_depth[target])
                if ext_callee_depth >= 1:
                    base_depth = max(base_depth, 1 + ext_callee_depth)
                    if base_for_depth >= 1 or any(eff_for_depth[t] >= 1 for fn in comp for c in functions[fn].calls for t in _resolve_callees(c.callee) if scc_of[t] != scc_idx):
                        base_nested_for = True
                else:
                    base_depth = max(base_depth, 1)
            else:
                # Generic recursive helper chain (non-D&C, non-DFS, non-DSU)
                if base_depth >= 1:
                    base_depth = base_depth + 1
                    if base_for_depth >= 1:
                        base_for_depth = base_for_depth + 1
                        base_nested_for = True
                else:
                    # Pure linear chain recursion without loops
                    base_depth = 1
                if base_log:
                    base_log_linear = True
                if base_bsearch_any:
                    base_bsearch_in_loop = True

            # Check internal self-recursion inside a loop for bsearch propagation
            for fn in comp:
                for call in functions[fn].calls:
                    if call.callee in comp_set or any(t in comp_set for t in _resolve_callees(call.callee)):
                        if call.any_loop_depth >= 1 and base_bsearch_any:
                            base_bsearch_in_loop = True

        for fn in comp:
            eff_depth[fn] = base_depth
            eff_for_depth[fn] = base_for_depth
            eff_nested_for[fn] = base_nested_for
            eff_log_loop[fn] = base_log
            eff_log_with_linear[fn] = base_log_linear
            eff_bsearch_any[fn] = base_bsearch_any
            eff_bsearch_in_loop[fn] = base_bsearch_in_loop
            eff_chain_height[fn] = max_callee_chain + (1 if is_cyclic else 0)

    max_depth = max(eff_depth.values(), default=0)
    saw_loop = any(info.own_saw_loop for info in functions.values()) or max_depth > 0
    has_nested_for = any(eff_nested_for.values())
    has_log_with_linear = any(eff_log_with_linear.values())
    has_any_log = any(eff_log_loop.values())
    has_only_log_loop = has_any_log and max_depth == 0 and not has_log_with_linear
    has_bsearch_in_loop = any(eff_bsearch_in_loop.values())
    has_bsearch_top = any(info.has_bsearch_top for info in functions.values())
    has_container_pop = any(info.has_container_pop for info in functions.values())
    has_exp_rec = any(info.has_exp_recursion for info in functions.values())
    call_graph_depth = max(eff_chain_height.values(), default=0)

    return _InterproceduralSummary(
        backend=backend,
        max_depth=max_depth,
        saw_loop=saw_loop,
        has_nested_for=has_nested_for,
        has_log_with_linear=has_log_with_linear,
        has_only_log_loop=has_only_log_loop,
        has_bsearch_in_loop=has_bsearch_in_loop,
        has_bsearch_top=has_bsearch_top,
        has_container_pop=has_container_pop,
        has_exponential_recursion=has_exp_rec,
        function_count=len(functions),
        call_graph_depth=call_graph_depth,
    )


def analyze_cpp_interprocedural(
    code: str,
    *,
    ignore_multitest: bool = True,
    ignore_small_const: bool = True,
) -> _InterproceduralSummary:
    """Analyze C++ loop depth and call graph using optional tree-sitter-cpp CST or pure-Python fallback."""
    cst_functions = _analyze_functions_tree_sitter(
        code,
        ignore_multitest=ignore_multitest,
        ignore_small_const=ignore_small_const,
    )
    if cst_functions is not None:
        return _solve_call_graph(cst_functions, backend="tree_sitter_cpp")

    cleaned = _strip_cpp_comments_and_strings(code)
    py_functions = _extract_functions_python(
        cleaned,
        ignore_multitest=ignore_multitest,
        ignore_small_const=ignore_small_const,
    )
    return _solve_call_graph(py_functions, backend="python_fallback")


def _analyze_loop_nesting(
    cleaned: str,
    *,
    ignore_multitest: bool = False,
    ignore_small_const: bool = False,
) -> tuple[int, bool, bool]:
    """Compute (max_loop_depth, saw_any_loop, has_nested_for_loops) across function call graphs."""
    functions = _extract_functions_python(
        cleaned,
        ignore_multitest=ignore_multitest,
        ignore_small_const=ignore_small_const,
    )
    summary = _solve_call_graph(functions, backend="python_fallback")
    return summary.max_depth, summary.saw_loop, summary.has_nested_for


def _depth_to_complexity(max_nesting: int, saw_loop: bool) -> str:
    if not saw_loop or max_nesting == 0:
        return ComplexityLevel.CONSTANT
    if max_nesting <= 1:
        return ComplexityLevel.LINEAR
    if max_nesting == 2:
        return ComplexityLevel.QUADRATIC
    if max_nesting == 3:
        return ComplexityLevel.CUBIC
    return ComplexityLevel.EXPONENTIAL


def analyze_loop_complexity(code: str) -> str:
    summary = analyze_cpp_interprocedural(code, ignore_multitest=False, ignore_small_const=False)
    if summary.has_only_log_loop:
        return ComplexityLevel.LOG_N
    return _depth_to_complexity(summary.max_depth, summary.saw_loop)


def has_effective_nested_loops(code: str) -> bool:
    """Return True when algorithmic loop depth (ignoring multi-test drivers & small constant loops) is >= 2."""
    summary = analyze_cpp_interprocedural(code, ignore_multitest=True, ignore_small_const=True)
    return summary.max_depth >= 2


def _has_exponential_recursion(cleaned: str) -> bool:
    """Detect exponential branching / backtracking recursion while ignoring tree DFS, Tarjan, and divide-and-conquer."""
    functions = _extract_functions_python(cleaned, ignore_multitest=True, ignore_small_const=True)
    return any(info.has_exp_recursion for info in functions.values())


def detect_algorithm_patterns(code: str) -> tuple[str | None, list[str]]:
    cleaned = _strip_cpp_comments_and_strings(code)
    patterns: list[str] = []
    complexity: str | None = None

    if re.search(r"\bbinary_search\b|\blower_bound\b|\bupper_bound\b", cleaned):
        patterns.append("binary_search")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    if re.search(r"\bsort\b|\bstable_sort\b|\bpartial_sort\b", cleaned):
        patterns.append("sorting")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    if re.search(r"\bmerge(_sort|_count)?\b|\bdivide_and_conquer\b", cleaned, re.IGNORECASE):
        patterns.append("divide_and_conquer")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    # Fenwick tree: English names OR Chinese CP idioms (`lowbit`, `x & -x`)
    has_fenwick = bool(
        re.search(r"\bfenwick\b", cleaned, re.IGNORECASE)
        or re.search(r"\bBIT\b", cleaned)
        or re.search(r"\blowbit\s*\(", cleaned)
        or re.search(r"\b([A-Za-z_]\w*)\s*&\s*-\s*\1\b", cleaned)
    )
    if has_fenwick:
        patterns.append("fenwick_tree")

    # Segment tree: English names OR Chinese CP idioms (`ls`/`rs`/`lson`/`rson`/`pushup`/`pushdown`/midpoint + `p << 1`)
    has_midpoint = bool(
        re.search(
            r"\b(?:mid|m)\b\s*=\s*\(?\s*[A-Za-z_]\w*\s*\+\s*[A-Za-z_]\w*\s*\)?\s*(?:/\s*2|>>\s*1)",
            cleaned,
        )
    )
    has_child_pairs = bool(
        (re.search(r"\bls\b", cleaned) and re.search(r"\brs\b", cleaned))
        or (re.search(r"\blson\b", cleaned) and re.search(r"\brson\b", cleaned))
        or (re.search(r"\blc\b", cleaned) and re.search(r"\brc\b", cleaned))
        or (
            re.search(r"\b([A-Za-z_]\w*)\s*<<\s*1\b", cleaned)
            and re.search(r"\b([A-Za-z_]\w*)\s*<<\s*1\s*\|\s*1\b", cleaned)
        )
    )
    has_segtree = bool(
        re.search(r"\b(?:segtree|segment_tree)\b", cleaned, re.IGNORECASE)
        or re.search(r"\b(?:push_?up|push_?down)\s*\(", cleaned, re.IGNORECASE)
        or (has_midpoint and has_child_pairs)
    )
    if has_segtree:
        patterns.append("segment_tree")

    if has_fenwick or has_segtree:
        patterns.append("tree_data_structure")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    # Binary lifting / sparse table (`fa[i][j]` / `f[i][j]` / `st[j][i]` with `1 << j` or chained parent jump)
    has_2d_lift_table = bool(
        re.search(
            r"\b(?:f|fa|up|anc|st|p|nxt|jump|par)\s*\[[^\]]+\]\s*\[[^\]]+\]",
            cleaned,
        )
    )
    has_lift_recurrence = bool(
        re.search(
            r"\b([A-Za-z_]\w*)\s*\[\s*\1\s*\[[^\]]+\]\s*\[[^\]]+-\s*1\s*\]\s*\]\s*\[[^\]]+-\s*1\s*\]",
            cleaned,
        )
        or (
            has_2d_lift_table
            and re.search(r"\b1\s*<<\s*(?:\(\s*)?[a-zA-Z_]\w*\s*(?:-\s*1)?", cleaned)
            and re.search(r"\bfor\s*\([^;]*;\s*[A-Za-z_]\w*\s*(?:<|<=)\s*(?:1\d|2\d|30|31|LOG|lg)\b", cleaned)
        )
        or (
            has_2d_lift_table
            and re.search(r">>\s*[A-Za-z_]\w*\s*&\s*1\b|&\s*\(\s*1\s*<<\s*[A-Za-z_]\w*\s*\)", cleaned)
        )
    )
    if has_lift_recurrence:
        patterns.append("binary_lifting")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    # DSU (Disjoint Set Union)
    has_dsu = bool(
        re.search(r"\b(?:dsu|union_?find)\b", cleaned, re.IGNORECASE)
        or re.search(
            r"\b([A-Za-z_]\w*)\s*\[\s*([A-Za-z_]\w*)\s*\]\s*==\s*\2\s*\?\s*\2\s*:\s*\1\s*\[\s*\2\s*\]\s*=",
            cleaned,
        )
        or (
            re.search(r"\b(?:find|getf|get_root|find_set)\s*\(", cleaned)
            and re.search(r"\b(?:fa|f|p|par|parent)\s*\[", cleaned)
            and re.search(r"\bsz\s*\[[^\]]+\]\s*\+=", cleaned)
        )
    )
    if has_dsu:
        patterns.append("dsu")
        complexity = _higher_complexity(complexity, ComplexityLevel.LINEAR)

    has_pq = bool(re.search(r"\b(?:std::)?priority_queue\s*<|\bmake_heap\b|\bpush_heap\b", cleaned))
    if has_pq:
        patterns.append("heap")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    has_graph_context = bool(
        re.search(r"\b(?:adj|graph|edges?|head|nxt|vis|visited|dist|dis|deg|indeg|tree)\b|\bg\s*\[", cleaned)
    )
    has_dist_array = bool(re.search(r"\b(?:dist|dis|d)\s*\[", cleaned))
    if re.search(r"\bdijkstra\b", cleaned, re.IGNORECASE) or (
        has_pq and has_dist_array and has_graph_context
    ):
        patterns.append("dijkstra")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    if re.search(r"\bnext_permutation\b|\bprev_permutation\b", cleaned):
        patterns.append("permutation_enumeration")
        complexity = _higher_complexity(complexity, ComplexityLevel.FACTORIAL)

    has_explicit_bfs = bool(re.search(r"\bbfs\b", cleaned, re.IGNORECASE))
    has_explicit_dfs = bool(re.search(r"\bdfs\b", cleaned, re.IGNORECASE))
    has_queue_stl = bool(re.search(r"(?<![a-zA-Z0-9_])(?:std::)?queue\s*<", cleaned))
    has_stack_stl = bool(re.search(r"(?<![a-zA-Z0-9_])(?:std::)?stack\s*<", cleaned))
    has_deque_stl = bool(re.search(r"(?<![a-zA-Z0-9_])(?:std::)?deque\s*<", cleaned))
    has_queue_stack = has_queue_stl or has_stack_stl

    # Monotonic stack & monotonic queue (STL or array-simulated top/hh/tt)
    has_mono_stack = bool(
        (
            has_stack_stl
            and re.search(r"\bwhile\s*\([^)]*\.empty\s*\(\s*\)[^)]*\.top\s*\(", cleaned)
            and re.search(r"\.pop\s*\(\s*\)", cleaned)
        )
        or (
            re.search(r"\b(?:top|tp)\b", cleaned)
            and re.search(r"\bwhile\s*\([^)]*\b(?:top|tp)\b[^)]*\)", cleaned)
            and re.search(r"(?:--(?:top|tp)|(?:top|tp)--)", cleaned)
            and re.search(r"\[\s*(?:\+\+(?:top|tp)|(?:top|tp)\+\+)\s*\]", cleaned)
        )
    )
    if has_mono_stack:
        patterns.append("monotonic_stack")
        complexity = _higher_complexity(complexity, ComplexityLevel.LINEAR)

    has_mono_queue = bool(
        (
            has_deque_stl
            and re.search(r"\bwhile\s*\([^)]*\.empty\s*\(\s*\)", cleaned)
            and re.search(r"\.(?:pop_front|pop_back)\s*\(\s*\)", cleaned)
        )
        or (
            re.search(r"\b(?:hh\s*<=\s*tt|head\s*<=\s*tail)\b", cleaned)
            and re.search(r"\bwhile\s*\([^)]*(?:hh\s*<=\s*tt|head\s*<=\s*tail)[^)]*\)", cleaned)
            and re.search(r"(?:tt--|--tt|tail--|--tail)", cleaned)
        )
    )
    if has_mono_queue:
        patterns.append("monotonic_queue")
        complexity = _higher_complexity(complexity, ComplexityLevel.LINEAR)

    # Two pointers (dual-index scan excluding binary search midpoints and container pops)
    has_two_pointers = bool(
        not has_midpoint
        and (
            re.search(
                r"\bfor\s*\(\s*(?:(?:int|long\s+long|ll|size_t)\s+)?([lrLRij])\s*=[^,;]+,\s*([lrLRij])\s*=",
                cleaned,
            )
            or (
                re.search(r"\bwhile\s*\(\s*([lrLRij]|left|right)\s*<=?\s*([lrLRij]|left|right)\b", cleaned)
                and (
                    (re.search(r"(?:\bl\+\+|\+\+l|\bleft\+\+|\+\+left)", cleaned) and re.search(r"(?:\br--|--r|\bright--|--right|\br\+\+|\+\+r)", cleaned))
                    or (re.search(r"(?:\bi\+\+|\+\+i)", cleaned) and re.search(r"(?:\bj--|--j|\bj\+\+|\+\+j)", cleaned))
                )
            )
            or (
                re.search(r"\bfor\s*\([^;]*;[^;]*;\s*(?:[ri]\+\+|\+\+[ri])", cleaned)
                and re.search(r"\bwhile\s*\([^)]*\b[lj]\s*<=?\s*[ri]\b", cleaned)
                and re.search(r"\b(?:l\+\+|\+\+l|j\+\+|\+\+j)\b", cleaned)
            )
        )
    )
    if has_two_pointers:
        patterns.append("two_pointers")
        complexity = _higher_complexity(complexity, ComplexityLevel.LINEAR)

    if has_explicit_bfs or (
        has_queue_stl and has_graph_context and not has_pq
    ) or (
        re.search(r"\b(?:hh\s*<=\s*tt|head\s*<=\s*tail)\b", cleaned)
        and has_graph_context
        and not has_mono_queue
    ):
        patterns.append("bfs")

    has_tree_dfs_sig = bool(
        re.search(r"!=\s*(?:p|fa|par|parent|prev)\b", cleaned)
        and re.search(r"\b(?:adj|g|graph|edges?|head|nxt)\b", cleaned)
    )
    if has_explicit_dfs or has_tree_dfs_sig:
        patterns.append("dfs")

    if (
        has_explicit_bfs
        or has_explicit_dfs
        or has_tree_dfs_sig
        or (has_queue_stack and has_graph_context and "heap" not in patterns)
    ):
        patterns.append("graph_traversal")
        complexity = _higher_complexity(complexity, ComplexityLevel.LINEAR)
    elif has_queue_stack or has_mono_queue:
        patterns.append("stack_queue")
        complexity = _higher_complexity(complexity, ComplexityLevel.LINEAR)

    if re.search(r"\bdp\[|\bmemo\[|\bmemoization\b", cleaned):
        patterns.append("dynamic_programming")
        complexity = _higher_complexity(complexity, ComplexityLevel.QUADRATIC)

    if re.search(r"unordered_map|unordered_set|hash_map", cleaned):
        patterns.append("hash_table")

    if re.search(r"\breturn\s+\w+\s*\([^)]*\)", cleaned) and re.search(
        r"\b\w+\s*\([^)]*\)\s*{", cleaned
    ):
        patterns.append("recursion")

    has_submask_enum = any(
        m.group(1) != m.group(2)
        for m in re.finditer(r"\(\s*([a-zA-Z_]\w*)\s*-\s*1\s*\)\s*&\s*([a-zA-Z_]\w*)", cleaned)
    )
    bitmask_bound_vars: set[str] = set()
    for m_bm in _BITMASK_ASSIGN_RE.finditer(cleaned):
        var_name = m_bm.group(1)
        exp_tok = m_bm.group(2)
        if exp_tok.isdigit() and int(exp_tok) < 12:
            continue
        bitmask_bound_vars.add(var_name)
    if bitmask_bound_vars:
        for m_alias in re.finditer(
            r"(?:^|[;{}])\s*(?:(?:static|const|constexpr)\s+)*(?:(?:unsigned|signed)\s+)?"
            r"(?:(?:int|long\s+long|long|size_t|ll|auto)\s+)?([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*(?:[+-]\s*1)?\s*;",
            cleaned,
        ):
            if m_alias.group(2) in bitmask_bound_vars:
                bitmask_bound_vars.add(m_alias.group(1))
    has_bitmask_var_usage = any(
        re.search(
            rf"(?:(?:<|<=)\s*{re.escape(v)}\b|\(\s*{re.escape(v)}\b|\[\s*{re.escape(v)}\b)",
            cleaned,
        )
        for v in bitmask_bound_vars
    )
    has_bitmask_loop_bound = bool(
        re.search(r"(?:<|<=)\s*\(?\s*1(?:[uUlL]{0,3})\s*<<\s*(?:\(\s*)?[a-zA-Z_]\w*", cleaned)
        or has_bitmask_var_usage
    )
    has_bitmask_dp_array = bool(
        re.search(r"\b(?:dp|f|memo|mask|state|sub|bit|subset)\w*\s*\[\s*1\s*<<\s*(?:1[2-9]|2\d)\s*\]", cleaned)
    )
    if (
        has_bitmask_loop_bound
        or has_submask_enum
        or has_bitmask_dp_array
        or re.search(r"\bbitmask\b", cleaned)
    ):
        patterns.append("bitmask")
        if (
            re.search(r"\b(?:dp|f|memo|dist)\s*\[", cleaned)
            or has_bitmask_dp_array
            or has_bitmask_var_usage
        ):
            patterns.append("bitmask_dp")
        complexity = _higher_complexity(complexity, ComplexityLevel.EXPONENTIAL)

    if _has_exponential_recursion(cleaned):
        if "backtracking" not in patterns:
            patterns.append("backtracking")
        complexity = _higher_complexity(complexity, ComplexityLevel.EXPONENTIAL)

    if re.search(r"\bbitset\s*<", cleaned):
        patterns.append("bitset")

    return complexity, patterns


def _has_nested_for_loops(cleaned: str) -> bool:
    """判断是否存在真正的两层及以上 `for` 嵌套循环（排除 amortized `for+while(pop)` 或 `while+adj_for`）。"""
    _, _, has_nested_for = _analyze_loop_nesting(cleaned, ignore_multitest=True, ignore_small_const=True)
    return has_nested_for


def _has_2d_dp_table(cleaned: str) -> bool:
    """Check whether DP indexing/declaration genuinely uses two non-trivial dimensions."""
    small_2nd_dim_vars: set[str] = set()
    # e.g. `vector<array<long long, 7>> dp(n + 1);` or `long long dp[MAXN][7];`
    for m_arr in re.finditer(
        r"\b(?:std::)?array\s*<[^;{}<>]+,\s*(\d+)\s*>\s*>+\s*([A-Za-z_]\w*)\b",
        cleaned,
    ):
        if int(m_arr.group(1)) <= 16:
            small_2nd_dim_vars.add(m_arr.group(2))
    for m_carr in re.finditer(
        r"\b([A-Za-z_]\w*)\s*\[[^\]]+\]\s*\[\s*(\d+)\s*\]\s*(?:;|=|,)",
        cleaned,
    ):
        if int(m_carr.group(2)) <= 16:
            small_2nd_dim_vars.add(m_carr.group(1))
    for m_vec in re.finditer(
        r"\b([A-Za-z_]\w*)\s*\([^;{}]+,\s*(?:std::)?vector\s*<[^>]+>\s*\(\s*(\d+)\s*(?:,|\))",
        cleaned,
    ):
        if int(m_vec.group(2)) <= 16:
            small_2nd_dim_vars.add(m_vec.group(1))

    for m in re.finditer(r"\b(dp|memo)\s*\[([^\]]+)\]\s*\[([^\]]+)\]", cleaned):
        name = m.group(1)
        dim1 = m.group(2).strip()
        dim2 = m.group(3).strip()
        if name in small_2nd_dim_vars:
            continue
        if (dim1.isdigit() and int(dim1) <= 16) or (dim2.isdigit() and int(dim2) <= 16):
            continue
        return True
    return False


def estimate_static_complexity(code: str) -> tuple[str, list[str]]:
    """综合跨函数调用图、循环结构与竞赛算法模式识别静态复杂度。"""
    cleaned = _strip_cpp_comments_and_strings(code)
    summary = analyze_cpp_interprocedural(code, ignore_multitest=True, ignore_small_const=True)
    algo_depth = summary.max_depth
    saw_loop = summary.saw_loop
    has_nested_for = summary.has_nested_for

    loop_complexity = _depth_to_complexity(algo_depth, saw_loop)
    if summary.has_only_log_loop and algo_depth == 0:
        loop_complexity = ComplexityLevel.LOG_N
    elif summary.has_log_with_linear and algo_depth <= 1:
        loop_complexity = ComplexityLevel.N_LOG_N

    pattern_complexity, patterns = detect_algorithm_patterns(code)

    # Re-evaluate contextual pattern complexity without blindly bumping single top-level lower_bound or 1D DP
    effective_pattern_comp: str | None = None
    for pat in patterns:
        if pat == "binary_search":
            cand = ComplexityLevel.N_LOG_N if summary.has_bsearch_in_loop else ComplexityLevel.LOG_N
        elif pat in {"sorting", "divide_and_conquer", "tree_data_structure", "fenwick_tree", "segment_tree", "binary_lifting", "heap", "dijkstra"}:
            # Pure Fenwick/SegTree definitions without outer linear loop still advertise O(n log n) when used with loops,
            # or O(log n) only if literally no loops exist except log loops; keep O(n log n) when algo_depth >= 1 or no log-only loop
            cand = ComplexityLevel.N_LOG_N
        elif pat == "permutation_enumeration":
            cand = ComplexityLevel.FACTORIAL
        elif pat in {"bitmask", "bitmask_dp", "backtracking"}:
            cand = ComplexityLevel.EXPONENTIAL
        elif pat == "dynamic_programming":
            cand = (
                ComplexityLevel.QUADRATIC
                if (algo_depth >= 2 or _has_2d_dp_table(cleaned))
                else ComplexityLevel.LINEAR
            )
        elif pat in {"graph_traversal", "bfs", "dfs", "stack_queue", "monotonic_stack", "monotonic_queue", "two_pointers", "dsu"}:
            cand = ComplexityLevel.LINEAR
        else:
            cand = None
        if cand is not None:
            effective_pattern_comp = _higher_complexity(effective_pattern_comp, cand)

    if effective_pattern_comp is None and pattern_complexity is not None and "binary_search" not in patterns:
        effective_pattern_comp = pattern_complexity

    effective_loop = loop_complexity
    if loop_complexity == ComplexityLevel.QUADRATIC and not has_nested_for:
        has_container_pop = summary.has_container_pop
        if "heap" in patterns and (has_container_pop or re.search(r"\bpriority_queue\b", cleaned)):
            effective_loop = ComplexityLevel.N_LOG_N
        elif (
            has_container_pop
            or "graph_traversal" in patterns
            or "stack_queue" in patterns
            or "monotonic_stack" in patterns
            or "monotonic_queue" in patterns
            or "two_pointers" in patterns
        ) and not _has_2d_dp_table(cleaned):
            effective_loop = ComplexityLevel.LINEAR

    final = (
        _higher_complexity(effective_loop, effective_pattern_comp)
        if effective_pattern_comp is not None
        else effective_loop
    )
    return final, patterns


def detect_complexity_conflicts(
    *,
    claimed: str | None,
    static_estimated: str | None,
    empirical: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Detect material disagreements across claimed, static, and confident empirical complexities."""
    conflicts: list[str] = []
    norm_claimed = normalize_complexity_expression(claimed) if claimed else None
    norm_static = normalize_complexity_expression(static_estimated) if static_estimated else None

    emp_fitted: str | None = None
    _non_fit_verdicts = {
        "verified_within_noise_floor",
        "verified_with_cache_jump",
        "insufficient_samples",
        "no_valid_samples",
        "timeout_at_extreme",
        "mle_at_scale",
        "runtime_error_at_scale",
        "query_budget_exceeded",
    }
    if isinstance(empirical, dict):
        verdict = str(empirical.get("verdict") or "")
        status = str(empirical.get("status") or "")
        r2 = empirical.get("r_squared")
        raw_fit = empirical.get("fitted_complexity")
        r2_ok = (
            isinstance(r2, int | float)
            and not isinstance(r2, bool)
            and math.isfinite(float(r2))
            and float(r2) >= 0.5
        )
        confident = (
            status not in ("skipped", "pending_generator", "pending_interactor", "by_design_slow", "generator_error")
            and verdict not in _non_fit_verdicts
            and isinstance(raw_fit, str)
            and bool(raw_fit.strip())
            and r2_ok
        )
        if confident and isinstance(raw_fit, str):
            norm_fit = normalize_complexity_expression(raw_fit)
            if norm_fit in _COMPLEXITY_ORDER:
                emp_fitted = norm_fit

    def _order_gap(a: str, b: str) -> int:
        if a in _COMPLEXITY_ORDER and b in _COMPLEXITY_ORDER:
            return abs(_COMPLEXITY_ORDER.index(a) - _COMPLEXITY_ORDER.index(b))
        return 0 if a == b else 1

    if norm_claimed and norm_static and norm_claimed != norm_static:
        conflicts.append(f"claimed ({norm_claimed}) != static_estimated ({norm_static})")
    if norm_claimed and emp_fitted and norm_claimed != emp_fitted and _order_gap(norm_claimed, emp_fitted) >= 1:
        conflicts.append(f"claimed ({norm_claimed}) != empirical_fitted ({emp_fitted})")
    if norm_static and emp_fitted and norm_static != emp_fitted and _order_gap(norm_static, emp_fitted) >= 1:
        conflicts.append(f"static_estimated ({norm_static}) != empirical_fitted ({emp_fitted})")

    return bool(conflicts), conflicts


def recommend_time_limit_ms(
    complexity: str, constraints: dict[str, Any] | None = None
) -> int:
    """结合算法复杂度与 n_max 规模给出更贴合竞赛惯例的时间限制推荐值（毫秒）。"""
    n_max = extract_n_max_from_constraints(constraints)
    if complexity in {ComplexityLevel.CONSTANT, ComplexityLevel.LOG_N}:
        return 1000
    if complexity == ComplexityLevel.LINEAR:
        if n_max is not None and n_max >= 5_000_000:
            return 2000
        if n_max is not None and n_max >= 1_000_000:
            return 1500
        return 1000
    if complexity == ComplexityLevel.N_LOG_N:
        if n_max is not None:
            if n_max <= 100_000:
                return 1000
            if n_max <= 300_000:
                return 1500
            if n_max <= 1_000_000:
                return 2000
            return 2500
        return COMPLEXITY_TO_TIME_LIMIT.get(complexity, 2000)
    return COMPLEXITY_TO_TIME_LIMIT.get(complexity, 1000)


def extract_claimed_complexity(code: str) -> str | None:
    comment_blocks = re.findall(r"//[^\n]*|/\*.*?\*/", code, re.DOTALL)
    o_pat = r"\b(O\([^()\n]*(?:\([^()\n]*\)[^()\n]*)*\))"
    # 1. Explicit time complexity label takes highest priority
    for blk in comment_blocks:
        for line in blk.splitlines():
            m = re.search(
                r"(?:time\s*complexity|时间复杂度)[^\n]*?" + o_pat,
                line,
                re.IGNORECASE,
            )
            if m:
                return m.group(1)
    # 2. Generic complexity label, excluding explicit space complexity lines
    for blk in comment_blocks:
        for line in blk.splitlines():
            if re.search(r"space\s*complexity|空间复杂度", line, re.IGNORECASE):
                continue
            m = re.search(
                r"(?:complexity|复杂度)[^\n]*?" + o_pat,
                line,
                re.IGNORECASE,
            )
            if m:
                return m.group(1)
    # 3. Fallback: first O(...) outside explicit space-complexity lines
    for line in code.splitlines():
        if re.search(r"space\s*complexity|空间复杂度", line, re.IGNORECASE):
            continue
        m = re.search(o_pat, line)
        if m:
            return m.group(1)
    return None


def build_risk_notes(
    estimated: str, constraints: dict[str, Any] | None, warnings: list[str]
) -> list[str]:
    notes = list(warnings)
    if estimated in {ComplexityLevel.QUADRATIC, ComplexityLevel.CUBIC}:
        notes.append("高复杂度实现对 n 上限敏感，建议强化极限对拍。")
    if estimated in {ComplexityLevel.EXPONENTIAL, ComplexityLevel.FACTORIAL}:
        notes.append("指数级复杂度通常不适合作为标准解，请核对题面约束。")
    if constraints and constraints.get("n_max", 0) >= 10**6 and estimated not in {
        ComplexityLevel.LINEAR,
        ComplexityLevel.N_LOG_N,
        ComplexityLevel.LOG_N,
        ComplexityLevel.CONSTANT,
    }:
        notes.append("n_max 较大，复杂度分析过高，存在超时风险，建议参考。")
    return notes


def _is_small_const_dim(expr: str) -> bool:
    s = expr.strip()
    return s.isdigit() and int(s) <= 64


def estimate_memory_usage(code: str) -> tuple[str, int]:
    cleaned = _strip_cpp_comments_and_strings(code)
    type_kw = r"(?:(?:unsigned|signed|long|short)\s+)*(?:int|long|short|char|bool|double|float|ll|ull|int\d+_t|uint\d+_t|size_t)"
    array_patterns = [
        rf"\b{type_kw}\s+\w+\s*\[(\d+)\](?!\s*\[)",
        r"\bvector\s*<\s*[^<>]+\s*>\s+\w+\s*\((\d+)(?:\s*,|\s*\))",
        r"\barray\s*<\s*[^,>]+,\s*(\d+)\s*>",
    ]

    total_elements = 0
    for pattern in array_patterns:
        matches = re.findall(pattern, cleaned)
        for match in matches:
            try:
                size = int(match[-1] if isinstance(match, tuple) else match)
                if size > 1:
                    total_elements += size
            except (ValueError, IndexError):
                pass

    # Detect genuine 2D allocations (declarations, NOT ordinary 2D element accesses like adj[u][i] or dp[i][0])
    has_2d_dynamic = False
    for m in re.finditer(
        r"\bvector\s*<\s*vector\s*<[^>]+(?:<[^>]+>)?[^>]*>\s*>\s+\w+\s*\(([^,()]+),\s*vector\s*<[^>]+(?:<[^>]+>)?[^>]*>\s*\(([^,()]+)",
        cleaned,
    ):
        d1, d2 = m.group(1).strip(), m.group(2).strip()
        if not (_is_small_const_dim(d1) or _is_small_const_dim(d2)):
            has_2d_dynamic = True
            break
    for m in re.finditer(
        rf"\b{type_kw}\s+\w+\s*\[([^\]]+)\]\s*\[([^\]]+)\]",
        cleaned,
    ):
        d1, d2 = m.group(1).strip(), m.group(2).strip()
        if d1.isdigit() and d2.isdigit():
            total_elements += int(d1) * int(d2)
        if not (_is_small_const_dim(d1) or _is_small_const_dim(d2)):
            has_2d_dynamic = True

    memory_bytes = total_elements * 4
    memory_mb = max(1, memory_bytes // (1024 * 1024))

    if has_2d_dynamic:
        return "O(n^2)", max(memory_mb, 128) if total_elements == 0 else memory_mb

    if total_elements > 0:
        if total_elements < 1_000_000:
            return "O(n)", memory_mb
        return "O(n) - large", memory_mb

    # 识别动态尺寸一维 STL 容器 / 变长数组 / 读入或动态增长的 string
    has_dynamic_linear = bool(
        re.search(
            r"\bvector\s*<"
            r"|\b(?:unordered_)?(?:set|multiset|map|multimap)\s*<"
            r"|\b(?:deque|queue|priority_queue|stack|list)\s*<"
            r"|\bnew\s+\w+(?:\s*<[^>]*>)?\s*\["
            rf"|\b{type_kw}\s+\w+\s*\[[^0-9\]\n]+\]",
            cleaned,
        )
        or (
            re.search(r"\bstring\s+\w+", cleaned)
            and re.search(
                r"\bcin\s*>>|\bgetline\b|\.(?:resize|reserve|push_back|append)\s*\(|\+=",
                cleaned,
            )
        )
    )
    if has_dynamic_linear:
        return "O(n)", 64

    return "O(1)", 64


async def run_empirical_verification(
    problem_dir: str,
    solution_type: str,
    claimed_complexity: str | None,
    constraints: dict[str, Any] | None,
) -> dict[str, Any]:
    if solution_type == "brute":
        return {
            "status": "by_design_slow",
            "passed": True,
            "skipped": True,
            "message": "Skipping full-scale empirical complexity fitting for brute solution (slow by design).",
        }

    exe_ext = get_exe_extension()
    gen_path = os.path.join(problem_dir, "files", f"gen{exe_ext}")
    sol_path = os.path.join(problem_dir, "solutions", f"{solution_type}{exe_ext}")

    actual_n_max = 10000
    time_limit_ms = 2000.0
    try:
        manifest_model = load_manifest(problem_dir)
    except (OSError, ValueError) as exc:
        return {
            "status": "manifest_error",
            "passed": False,
            "message": f"invalid or unreadable manifest.json: {exc}",
        }

    if (
        not claimed_complexity
        and manifest_model is not None
        and manifest_model.empirical_complexity is not None
        and isinstance(manifest_model.empirical_complexity.claimed_complexity, str)
        and manifest_model.empirical_complexity.claimed_complexity.strip()
    ):
        claimed_complexity = manifest_model.empirical_complexity.claimed_complexity.strip()

    has_sol = os.path.isfile(sol_path)
    if (
        not has_sol
        and manifest_model is not None
        and manifest_is_communication(manifest_model)
        and manifest_model.protocol is not None
        and manifest_model.protocol.participant_solutions
        and solution_type == "sol"
    ):
        parts = resolve_participant_binaries(problem_dir, manifest_model, None, exe_ext)
        if parts and all(os.path.isfile(p) for p in parts):
            has_sol = True

    if not os.path.isfile(gen_path) or not has_sol:
        return {
            "status": "pending_generator",
            "message": "files/gen or binary not built yet, empirical verification will execute after generator_build",
        }

    if not claimed_complexity:
        return {
            "status": "skipped",
            "passed": True,
            "message": "claimed_complexity not provided; skipping empirical verification to avoid false assumptions",
        }
    emp_cfg = manifest_model.empirical_complexity if manifest_model is not None else None
    dominant_var = emp_cfg.dominant_var if emp_cfg is not None else "n"
    scale_mode = emp_cfg.scale_mode if emp_cfg is not None else "auto"
    metric_mode = emp_cfg.metric if emp_cfg is not None else "auto"
    args_template = emp_cfg.generator_args_template if emp_cfg is not None else None
    extra_vars: dict[str, Any] = dict(emp_cfg.extra_vars) if emp_cfg is not None else {}

    if manifest_model is not None:
        manifest_tl = _positive_float(manifest_model.time_limit_ms)
        if manifest_tl is not None:
            time_limit_ms = manifest_tl
        if emp_cfg is not None and emp_cfg.max_value is not None:
            actual_n_max = emp_cfg.max_value
        else:
            dom_max = extract_dominant_var_max(manifest_model.constraints, dominant_var)
            if dom_max is not None:
                actual_n_max = dom_max
            else:
                manifest_n = extract_n_max_from_constraints(manifest_model.constraints)
                if manifest_n is not None:
                    actual_n_max = manifest_n
    if constraints:
        if emp_cfg is None or (emp_cfg.max_value is None and dominant_var.lower() == "n"):
            explicit_n = _positive_int(constraints.get("n_max"))
            if explicit_n is not None:
                actual_n_max = explicit_n
        explicit_tl = _positive_float(constraints.get("time_limit_ms"))
        if explicit_tl is not None:
            time_limit_ms = explicit_tl

    # 若以 T 作为主导变量且未提供自定义模板，默认保持 n 为合理固定值而非随 T 一起变动
    if (
        dominant_var.lower() in ("t", "t_max")
        and args_template is None
        and "n_min" not in extra_vars
        and "n_max" not in extra_vars
        and manifest_model is not None
    ):
        fixed_n = extract_n_max_from_constraints(manifest_model.constraints)
        if fixed_n is not None:
            extra_vars["n_min"] = fixed_n
            extra_vars["n_max"] = fixed_n

    is_interactive = manifest_is_interactive(manifest_model)
    is_communication = manifest_is_communication(manifest_model)

    effective_complexity = claimed_complexity
    norm_claimed = normalize_complexity_expression(
        effective_complexity, dominant_var=dominant_var
    )
    effective_scale_mode = scale_mode
    if (
        effective_scale_mode == "auto"
        and is_interactive
        and norm_claimed == "O(log n)"
        and actual_n_max >= 100
    ):
        effective_scale_mode = "log"

    if emp_cfg is not None and emp_cfg.scale_points:
        scale_points = list(emp_cfg.scale_points)
    else:
        scale_points = MultiScaleSampler.compute_scale_points(
            actual_n_max,
            effective_complexity,
            scale_mode=effective_scale_mode,
            dominant_var=dominant_var,
        )

    empirical_dir = os.path.join(problem_dir, ".autocode", "empirical_tests")
    os.makedirs(empirical_dir, exist_ok=True)

    baseline_overhead = await DynamicExecutionMonitor.measure_baseline_overhead(cwd=problem_dir)

    interactor_path = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
    if is_interactive and not os.path.isfile(interactor_path):
        fallback_int = os.path.join(problem_dir, f"interactor{exe_ext}")
        if os.path.isfile(fallback_int):
            interactor_path = fallback_int
    if is_interactive and not os.path.isfile(interactor_path):
        return {
            "status": "pending_interactor",
            "message": "Interactive empirical verification requires compiled files/interactor",
        }
    if is_communication and manifest_model is not None:
        comm_mode = manifest_model.protocol.mode if manifest_model.protocol else "run_twice"
        if comm_mode != "grader_linked" and not resolve_communicator_binary(
            problem_dir, manifest_model, exe_ext
        ):
            return {
                "status": "pending_interactor",
                "message": f"Communication empirical verification ({comm_mode}) requires compiled communicator",
            }
    memory_limit_mb = (
        float(manifest_model.memory_limit_mb)
        if (manifest_model is not None and manifest_model.memory_limit_mb > 0)
        else 512.0
    )

    samples: list[dict[str, Any]] = []
    for idx, pt in enumerate(scale_points):
        in_file = os.path.join(empirical_dir, f"scale_{pt}.in")
        try:
            gen_cmd = MultiScaleSampler.format_generator_command(
                gen_path,
                pt,
                42 + idx,
                template=args_template,
                extra_vars=extra_vars or None,
                dominant_var=dominant_var,
            )
            await asyncio.to_thread(
                MultiScaleSampler.generate_scale_input_file,
                gen_cmd,
                in_file,
                5.0,
            )
        except Exception as e:
            return {
                "status": "generator_error",
                "message": f"Failed generating scale point {pt}: {e}",
            }

        if is_interactive or (is_communication and manifest_model is not None):
            with open(in_file, encoding="utf-8", errors="replace") as inf:
                scale_input = inf.read()

            async def _run_protocol_once(_input_data: str = scale_input) -> JudgeResult:
                if is_communication and manifest_model is not None:
                    return await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest_model,
                        solution_exe=sol_path,
                        input_data=_input_data,
                        time_limit_ms=time_limit_ms,
                        memory_limit_mb=memory_limit_mb,
                    )
                return await InteractiveRunner.run(
                    interactor_exe=interactor_path,
                    solution_exe=sol_path,
                    input_data=_input_data,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    cwd=problem_dir,
                )

            if idx == 0:
                await _run_protocol_once()

            jres = await _run_protocol_once()
            status_map = {
                "AC": "ok",
                "PC": "ok",
                "TLE": "timeout",
                "DEADLOCK": "timeout",
                "MLE": "mle",
            }
            sample_status = status_map.get(jres.verdict, "runtime_error")
            adjusted_cpu = None
            proto_mem = jres.memory_mb
            if sample_status == "ok":
                raw_cpu = jres.cpu_time_ms if jres.cpu_time_ms is not None else jres.wall_time_ms
                adjusted_cpu = round(max(0.1, float(raw_cpu) - baseline_overhead), 2)
                if float(jres.wall_time_ms or 0.0) < 80.0:
                    proto_cpu_runs = [float(adjusted_cpu)]
                    proto_mem_runs = [float(jres.memory_mb or 0.0)]
                    for _ in range(2):
                        proto_rep = await _run_protocol_once()
                        if status_map.get(proto_rep.verdict) == "ok":
                            rep_raw = (
                                proto_rep.cpu_time_ms
                                if proto_rep.cpu_time_ms is not None
                                else proto_rep.wall_time_ms
                            )
                            proto_cpu_runs.append(round(max(0.1, float(rep_raw) - baseline_overhead), 2))
                            proto_mem_runs.append(float(proto_rep.memory_mb or 0.0))
                    adjusted_cpu = round(statistics.median(proto_cpu_runs), 2)
                    proto_mem = round(max(proto_mem_runs), 2)
            sample_entry: dict[str, Any] = {
                "n": pt,
                "cpu_time_ms": adjusted_cpu,
                "memory_mb": proto_mem,
                "status": sample_status,
                "verdict": jres.verdict,
            }
            if is_interactive:
                explicit_q = jres.metrics.get("queries")
                fallback_q = jres.metrics.get("contestant_messages")
                if isinstance(explicit_q, int) and not isinstance(explicit_q, bool) and explicit_q > 0:
                    sample_entry["queries"] = explicit_q
                    sample_entry["queries_source"] = "judge_metrics"
                elif isinstance(fallback_q, int) and not isinstance(fallback_q, bool) and fallback_q > 0:
                    sample_entry["queries"] = fallback_q
                    sample_entry["queries_source"] = "contestant_messages"
            samples.append(sample_entry)
            if sample_status in ("timeout", "mle"):
                break
        else:
            monitored_metric = "cpu_time" if metric_mode == "cpu_time_ms" else None
            if idx == 0:
                # 首次冷启动预热：消除二进制冷加载/杀软扫描对最小规模采样的歪斜。
                await DynamicExecutionMonitor.run_monitored_process(
                    [sol_path],
                    in_file,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    cwd=problem_dir,
                    baseline_overhead_ms=baseline_overhead,
                    metric=monitored_metric,
                )
            res = await DynamicExecutionMonitor.run_monitored_process(
                [sol_path],
                in_file,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                cwd=problem_dir,
                baseline_overhead_ms=baseline_overhead,
                metric=monitored_metric,
            )
            chosen_cpu = res.get("cpu_time_ms")
            chosen_wall = res.get("wall_time_ms")
            chosen_mem = res.get("memory_mb")
            chosen_status = res.get("status")
            chosen_inst = res.get("instruction_count")
            chosen_backend = res.get("metric_backend")
            if (
                chosen_status == "ok"
                and chosen_cpu is not None
                and chosen_inst is None
                and float(res.get("wall_time_ms") or 0.0) < 50.0
            ):
                # 对超短耗时的尺度点额外复测 2 次取中位耗时与峰值内存，平抑抖动。
                cpu_runs: list[float] = [float(chosen_cpu)]
                mem_runs: list[float] = [float(chosen_mem or 0.0)]
                for _ in range(2):
                    rep = await DynamicExecutionMonitor.run_monitored_process(
                        [sol_path],
                        in_file,
                        time_limit_ms=time_limit_ms,
                        memory_limit_mb=memory_limit_mb,
                        cwd=problem_dir,
                        baseline_overhead_ms=baseline_overhead,
                        metric=monitored_metric,
                    )
                    if rep.get("status") == "ok" and rep.get("cpu_time_ms") is not None:
                        cpu_runs.append(float(rep["cpu_time_ms"]))
                        mem_runs.append(float(rep.get("memory_mb") or 0.0))
                chosen_cpu = round(statistics.median(cpu_runs), 2)
                chosen_mem = round(max(mem_runs), 2)
            sample_item: dict[str, Any] = {
                "n": pt,
                "cpu_time_ms": chosen_cpu,
                "wall_time_ms": chosen_wall,
                "memory_mb": chosen_mem,
                "status": chosen_status,
            }
            if isinstance(chosen_inst, int) and not isinstance(chosen_inst, bool):
                sample_item["instruction_count"] = chosen_inst
            if isinstance(chosen_backend, str):
                sample_item["metric_backend"] = chosen_backend
            samples.append(sample_item)
            if chosen_status in ("timeout", "mle"):
                break

    ok_query_samples = [
        s
        for s in samples
        if s.get("status") == "ok"
        and isinstance(s.get("queries"), int)
        and not isinstance(s.get("queries"), bool)
        and int(s["queries"]) > 0
    ]

    use_query_metric = metric_mode == "queries" or (
        metric_mode == "auto"
        and is_interactive
        and len(ok_query_samples) >= 2
        and norm_claimed in ("O(log n)", "O(1)")
    )

    if use_query_metric:
        max_q_limit = (
            manifest_model.protocol.max_queries
            if (manifest_model is not None and manifest_model.protocol is not None)
            else None
        )
        verdict_dict = EmpiricalRatioAnalyzer.verify_query_complexity(
            effective_complexity,
            samples,
            max_queries=max_q_limit,
            time_limit_ms=time_limit_ms,
            dominant_var=dominant_var,
        )
    else:
        verdict_dict = EmpiricalRatioAnalyzer.verify_complexity(
            effective_complexity,
            samples,
            time_limit_ms=time_limit_ms,
            dominant_var=dominant_var,
        )

    verdict_dict["dominant_var"] = dominant_var
    verdict_dict["scale_mode"] = effective_scale_mode
    if (
        emp_cfg is None
        and manifest_model is not None
        and len(manifest_model.constraints) >= 2
        and verdict_dict.get("verdict") in ("ratio_mismatch", "verified_within_noise_floor")
    ):
        verdict_dict["multi_param_hint"] = (
            "Problem declares multiple constraints; if runtime is dominated by a variable other than "
            "`n` (e.g. T or k) or requires custom gen flags, configure `empirical_complexity` "
            "(dominant_var, scale_points, generator_args_template) in .autocode/manifest.json."
        )
    return verdict_dict


class SolutionAnalyzeTool(Tool):
    @property
    def name(self) -> str:
        return "solution_analyze"

    @property
    def description(self) -> str:
        return "分析 C++ 解法代码的时间与空间复杂度，结合经验数据拟合输出实证分析证据。"

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(SolutionAnalyzeInput)

    async def execute(
        self,
        code: str | None = None,
        problem_dir: str | None = None,
        solution_type: str = "sol",
        source_path: str | None = None,
        constraints: dict | None = None,
        claimed_complexity: str | None = None,
    ) -> ToolResult:
        if solution_type not in {"sol", "brute"}:
            return ToolResult.fail("solution_type must be 'sol' or 'brute'")
        if code is None and source_path is None and not problem_dir:
            return ToolResult.fail("Either 'code', 'source_path', or 'problem_dir' must be provided")

        resolved, err = resolve_source(
            problem_dir or ".",
            code,
            source_path,
            default_source_path=os.path.join("solutions", f"{solution_type}.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None
        code = resolved.code

        static_complexity, patterns = estimate_static_complexity(code)
        final_complexity = static_complexity

        manifest_claimed: str | None = None
        m_loaded = None
        if problem_dir:
            try:
                m_loaded = load_manifest(problem_dir)
            except (OSError, ValueError):
                m_loaded = None
            if (
                m_loaded is not None
                and m_loaded.empirical_complexity is not None
                and isinstance(m_loaded.empirical_complexity.claimed_complexity, str)
                and m_loaded.empirical_complexity.claimed_complexity.strip()
            ):
                manifest_claimed = m_loaded.empirical_complexity.claimed_complexity.strip()
        if constraints is None and m_loaded is not None and m_loaded.constraints:
            constraints = dict(m_loaded.constraints)

        explicit_or_manifest_claimed = claimed_complexity or manifest_claimed
        if explicit_or_manifest_claimed:
            norm_claimed = normalize_complexity_expression(explicit_or_manifest_claimed)
            final_complexity = norm_claimed

        space_complexity, memory_mb = estimate_memory_usage(code)

        recommended_n_max = COMPLEXITY_TO_N_MAX.get(final_complexity, 10000)
        recommended_time_ms = recommend_time_limit_ms(final_complexity, constraints)
        detected_claimed = explicit_or_manifest_claimed or extract_claimed_complexity(code)

        warnings: list[str] = []
        constraint_n_max = extract_n_max_from_constraints(constraints)
        if constraints:
            if constraint_n_max is not None and constraint_n_max > recommended_n_max:
                warnings.append(
                    f"Warning: n_max={constraint_n_max} may cause TLE "
                    f"for {final_complexity} algorithm. Recommended: {recommended_n_max}"
                )
            if constraints.get("time_limit_ms"):
                if constraints["time_limit_ms"] < recommended_time_ms:
                    warnings.append(
                        f"Warning: time_limit={constraints['time_limit_ms']}ms may be too tight "
                        f"for {final_complexity} algorithm. Recommended: {recommended_time_ms}ms"
                    )

        effective_constraints = dict(constraints) if isinstance(constraints, dict) else None
        if effective_constraints is not None and constraint_n_max is not None and "n_max" not in effective_constraints:
            effective_constraints["n_max"] = constraint_n_max
        suggested_test_configs = self._generate_test_configs(recommended_n_max, constraints)
        stress_profiles = self._recommended_stress_profiles(
            final_complexity=final_complexity,
            recommended_n_max=recommended_n_max,
            constraints=effective_constraints,
        )

        empirical_verification: dict[str, Any] = {"status": "skipped"}
        if problem_dir:
            empirical_verification = await run_empirical_verification(
                problem_dir,
                solution_type,
                claimed_complexity or detected_claimed,
                constraints,
            )

        has_conflict, conflict_details = detect_complexity_conflicts(
            claimed=claimed_complexity or detected_claimed,
            static_estimated=static_complexity,
            empirical=empirical_verification,
        )
        if has_conflict:
            warnings.append(f"Complexity estimate conflict: {'; '.join(conflict_details)}")
            static_rec_n_max = COMPLEXITY_TO_N_MAX.get(static_complexity, 10000)
            if constraint_n_max is not None and constraint_n_max > static_rec_n_max:
                warnings.append(
                    f"Warning: static complexity {static_complexity} is likely TLE for n_max={constraint_n_max} "
                    f"(recommended <= {static_rec_n_max})."
                )
        risk_notes = build_risk_notes(final_complexity, effective_constraints, warnings)

        return ToolResult.ok(
            claimed_complexity=claimed_complexity or detected_claimed,
            estimated_complexity=final_complexity,
            static_estimated_complexity=static_complexity,
            complexity_conflict=has_conflict,
            complexity_match=not has_conflict,
            conflict_details=conflict_details,
            worst_case_complexity=final_complexity,
            average_case_complexity=final_complexity,
            time_complexity=final_complexity,
            space_complexity=space_complexity,
            memory_estimate={"space_complexity": space_complexity, "estimated_memory_mb": memory_mb},
            estimated_memory_mb=memory_mb,
            detected_patterns=patterns,
            recommended_n_max=recommended_n_max,
            recommended_time_limit_ms=recommended_time_ms,
            warnings=warnings,
            risk_notes=risk_notes,
            suggested_test_configs=suggested_test_configs,
            recommended_stress_params=stress_profiles,
            empirical_verification=empirical_verification,
            message=f"Analyzed complexity: {final_complexity}",
        )

    def _generate_test_configs(
        self, n_max: int, constraints: dict | None
    ) -> list[dict]:
        raw_n = extract_n_max_from_constraints(constraints) if constraints else None
        actual_n_max = max(1, raw_n if raw_n is not None else n_max)
        mid_small = max(1, min(actual_n_max, max(10, actual_n_max // 10)))
        mid_half = max(1, min(actual_n_max, actual_n_max // 2))
        return [
            {"type": "1", "n_min": 1, "n_max": 1, "t_min": 1, "t_max": 1},
            {"type": "1", "n_min": 1, "n_max": min(10, actual_n_max), "t_min": 1, "t_max": 1},
            {"type": "2", "n_min": 1, "n_max": mid_small, "t_min": 1, "t_max": 1},
            {"type": "3", "n_min": min(mid_small, mid_half), "n_max": mid_half, "t_min": 1, "t_max": 1},
            {"type": "3", "n_min": mid_half, "n_max": actual_n_max, "t_min": 1, "t_max": 1},
            {"type": "4", "n_min": actual_n_max, "n_max": actual_n_max, "t_min": 1, "t_max": 1},
        ]

    def _recommended_stress_profiles(
        self,
        final_complexity: str,
        recommended_n_max: int,
        constraints: dict | None,
    ) -> list[dict]:
        constraint_n = extract_n_max_from_constraints(constraints) if constraints else None
        n_cap = constraint_n if constraint_n is not None else recommended_n_max
        brute_n = min(max(20, n_cap // 50), 2000)
        if constraint_n is not None:
            brute_n = min(brute_n, max(1, constraint_n))
        tiny_n = min(8, max(1, constraint_n)) if constraint_n is not None else 8
        trials = 300 if final_complexity in {ComplexityLevel.QUADRATIC, ComplexityLevel.CUBIC} else 1000
        return [
            {
                "name": "tiny_exhaustive",
                "trials": min(200, trials),
                "types": ["1"],
                "generator_args": {"type": "1", "n_min": 1, "n_max": tiny_n, "t_min": 1, "t_max": 1},
            },
            {
                "name": "random_small",
                "trials": trials,
                "types": ["2"],
                "generator_args": {"type": "2", "n_min": 1, "n_max": brute_n, "t_min": 1, "t_max": 1},
            },
            {
                "name": "edge_small",
                "trials": max(100, trials // 3),
                "types": ["3", "4"],
                "generator_args": {"type": "3", "n_min": max(1, brute_n // 2), "n_max": brute_n, "t_min": 1, "t_max": 1},
            },
        ]
