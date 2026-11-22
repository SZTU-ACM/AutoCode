from __future__ import annotations

import asyncio
import math
import os
import re
import statistics
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


def _skip_balanced_parens(tokens: list[str], pos: int) -> int:
    """Given tokens[pos] == '(', return the index immediately after its matching ')'."""
    depth = 0
    total = len(tokens)
    while pos < total:
        tok = tokens[pos]
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
            if depth == 0:
                return pos + 1
        pos += 1
    return total


def analyze_loop_complexity(code: str) -> str:
    cleaned = _strip_cpp_comments_and_strings(code)
    tokens = _TOKEN_RE.findall(cleaned)
    total = len(tokens)

    max_nesting = 0
    saw_loop = False
    # Stack frames: "brace_loop" | "do_brace" | "brace_other" | "single_loop" | "single_do"
    frames: list[str] = []
    expecting_do_while = False

    def _active_loop_depth() -> int:
        return sum(
            1 for f in frames if f in ("brace_loop", "do_brace", "single_loop", "single_do")
        )

    def _unwind_single_loops(next_tok: str | None) -> None:
        nonlocal expecting_do_while
        if next_tok == "else":
            return
        while frames:
            top = frames[-1]
            if top == "single_loop":
                frames.pop()
            elif top == "single_do":
                frames.pop()
                expecting_do_while = True
                break
            else:
                break

    i = 0
    while i < total:
        tok = tokens[i]

        if tok in ("for", "while"):
            if tok == "while" and expecting_do_while:
                expecting_do_while = False
                i += 1
                if i < total and tokens[i] == "(":
                    i = _skip_balanced_parens(tokens, i)
                if i < total and tokens[i] == ";":
                    i += 1
                _unwind_single_loops(tokens[i] if i < total else None)
                continue

            if i + 1 < total and tokens[i + 1] == "(":
                saw_loop = True
                current_depth = _active_loop_depth() + 1
                max_nesting = max(max_nesting, current_depth)
                i = _skip_balanced_parens(tokens, i + 1)
                if i < total and tokens[i] == ";":
                    i += 1
                    _unwind_single_loops(tokens[i] if i < total else None)
                elif i < total and tokens[i] == "{":
                    frames.append("brace_loop")
                    i += 1
                else:
                    frames.append("single_loop")
                continue

        elif tok == "do":
            if i + 1 < total and tokens[i + 1] != "(":
                saw_loop = True
                current_depth = _active_loop_depth() + 1
                max_nesting = max(max_nesting, current_depth)
                i += 1
                if i < total and tokens[i] == "{":
                    frames.append("do_brace")
                    i += 1
                else:
                    frames.append("single_do")
                continue

        elif tok in ("if", "switch"):
            if i + 1 < total and tokens[i + 1] == "(":
                i = _skip_balanced_parens(tokens, i + 1)
                continue

        elif tok == "{":
            frames.append("brace_other")
            i += 1
            continue

        elif tok == "}":
            # Pop until matching braced frame
            popped = None
            while frames:
                top = frames.pop()
                if top in ("brace_loop", "do_brace", "brace_other"):
                    popped = top
                    break
            i += 1
            if popped == "do_brace":
                expecting_do_while = True
            else:
                _unwind_single_loops(tokens[i] if i < total else None)
            continue

        elif tok == ";":
            i += 1
            _unwind_single_loops(tokens[i] if i < total else None)
            continue

        i += 1

    if not saw_loop:
        return ComplexityLevel.CONSTANT
    if max_nesting <= 1:
        return ComplexityLevel.LINEAR
    if max_nesting == 2:
        return ComplexityLevel.QUADRATIC
    if max_nesting == 3:
        return ComplexityLevel.CUBIC
    return ComplexityLevel.EXPONENTIAL


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

    if re.search(r"\b(fenwick|segtree|segment_tree)\b", cleaned, re.IGNORECASE) or re.search(
        r"\bBIT\b", cleaned
    ):
        patterns.append("tree_data_structure")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    if re.search(r"\b(?:std::)?priority_queue\s*<|\bmake_heap\b|\bpush_heap\b", cleaned):
        patterns.append("heap")
        complexity = _higher_complexity(complexity, ComplexityLevel.N_LOG_N)

    if re.search(r"\bnext_permutation\b|\bprev_permutation\b", cleaned):
        patterns.append("permutation_enumeration")
        complexity = _higher_complexity(complexity, ComplexityLevel.FACTORIAL)

    if re.search(
        r"\bbfs\b|\bdfs\b|(?<![a-zA-Z0-9_])(?:std::)?(?:queue|stack)\s*<",
        cleaned,
    ):
        patterns.append("graph_traversal")
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

    if re.search(r"\b1(?:[uUlL]{0,3})\s*<<\s*[a-zA-Z_]\w*|\bbitmask\b", cleaned):
        patterns.append("bitmask")
        complexity = _higher_complexity(complexity, ComplexityLevel.EXPONENTIAL)

    if re.search(r"\bbitset\s*<", cleaned):
        patterns.append("bitset")

    return complexity, patterns


def _has_nested_for_loops(cleaned: str) -> bool:
    """判断是否存在真正的两层及以上 `for` 嵌套循环（排除 amortized `for+while(pop)` 或 `while+adj_for`）。"""
    tokens = _TOKEN_RE.findall(cleaned)
    total = len(tokens)
    frames: list[str] = []
    expecting_do_while = False

    def _active_for_depth() -> int:
        return sum(1 for f in frames if f in ("for_brace", "for_single"))

    def _unwind(next_tok: str | None) -> None:
        nonlocal expecting_do_while
        if next_tok == "else":
            return
        while frames:
            top = frames[-1]
            if top in ("for_single", "other_single"):
                frames.pop()
            elif top == "do_single":
                frames.pop()
                expecting_do_while = True
                break
            else:
                break

    i = 0
    while i < total:
        tok = tokens[i]
        if tok in ("for", "while"):
            if tok == "while" and expecting_do_while:
                expecting_do_while = False
                i += 1
                if i < total and tokens[i] == "(":
                    i = _skip_balanced_parens(tokens, i)
                if i < total and tokens[i] == ";":
                    i += 1
                _unwind(tokens[i] if i < total else None)
                continue
            if i + 1 < total and tokens[i + 1] == "(":
                is_for = tok == "for"
                if is_for and _active_for_depth() >= 1:
                    return True
                i = _skip_balanced_parens(tokens, i + 1)
                if i < total and tokens[i] == ";":
                    i += 1
                    _unwind(tokens[i] if i < total else None)
                elif i < total and tokens[i] == "{":
                    frames.append("for_brace" if is_for else "other_brace")
                    i += 1
                else:
                    frames.append("for_single" if is_for else "other_single")
                continue
        elif tok == "do" and i + 1 < total and tokens[i + 1] != "(":
            i += 1
            if i < total and tokens[i] == "{":
                frames.append("do_brace")
                i += 1
            else:
                frames.append("do_single")
            continue
        elif tok in ("if", "switch") and i + 1 < total and tokens[i + 1] == "(":
            i = _skip_balanced_parens(tokens, i + 1)
            continue
        elif tok == "{":
            frames.append("brace_other")
            i += 1
            continue
        elif tok == "}":
            popped = None
            while frames:
                top = frames.pop()
                if top in ("for_brace", "other_brace", "do_brace", "brace_other"):
                    popped = top
                    break
            i += 1
            if popped == "do_brace":
                expecting_do_while = True
            else:
                _unwind(tokens[i] if i < total else None)
            continue
        elif tok == ";":
            i += 1
            _unwind(tokens[i] if i < total else None)
            continue
        i += 1
    return False


def estimate_static_complexity(code: str) -> tuple[str, list[str]]:
    """综合循环结构与算法模式识别静态复杂度，识别 Dijkstra/单调队列等均摊模式。"""
    cleaned = _strip_cpp_comments_and_strings(code)
    loop_complexity = analyze_loop_complexity(code)
    pattern_complexity, patterns = detect_algorithm_patterns(code)

    effective_loop = loop_complexity
    if loop_complexity == ComplexityLevel.QUADRATIC and not _has_nested_for_loops(cleaned):
        has_container_pop = bool(
            re.search(r"\.(?:pop|pop_front|pop_back)\s*\(", cleaned)
        )
        if "heap" in patterns and has_container_pop:
            effective_loop = ComplexityLevel.N_LOG_N
        elif (has_container_pop or "graph_traversal" in patterns) and "dynamic_programming" not in patterns:
            effective_loop = ComplexityLevel.LINEAR

    final = (
        _higher_complexity(effective_loop, pattern_complexity)
        if pattern_complexity is not None
        else effective_loop
    )
    return final, patterns


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


def estimate_memory_usage(code: str) -> tuple[str, int]:
    cleaned = _strip_cpp_comments_and_strings(code)
    array_patterns = [
        r"(\w+)\s*\[(\d+)\]",
        r"vector\s*<\s*[^>]+\s*>\s+(\w+)\s*\((\d+)\)",
        r"array\s*<\s*[^,>]+,\s*(\d+)\s*>",
    ]

    total_elements = 0
    for pattern in array_patterns:
        matches = re.findall(pattern, cleaned)
        for match in matches:
            try:
                if isinstance(match, tuple):
                    size = int(match[-1])
                else:
                    size = int(match)
                total_elements += size
            except (ValueError, IndexError):
                pass

    memory_bytes = total_elements * 4
    memory_mb = max(1, memory_bytes // (1024 * 1024))

    # 识别动态尺寸二维容器（如 vector<vector<int>> dp(n, vector<int>(m)) 或符号二维数组 dp[N][M]）
    has_2d_dynamic = bool(
        re.search(
            r"vector\s*<\s*vector\s*<[^>]+>\s*>\s+\w+\s*\([^,)]+,\s*vector\s*<",
            cleaned,
        )
        or re.search(
            r"\[\s*[a-zA-Z_]\w*(?:\s*[+\-]\s*\d+)?\s*\]\s*\[\s*[a-zA-Z_]\w*(?:\s*[+\-]\s*\d+)?\s*\]",
            cleaned,
        )
    )
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
            r"|(?:int|long\s+long|double|char|bool|unsigned)\s+\w+\s*\[[^0-9\]\n]+\]",
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
            if idx == 0:
                # 首次冷启动预热：消除二进制冷加载/杀软扫描对最小规模采样的歪斜。
                await DynamicExecutionMonitor.run_monitored_process(
                    [sol_path],
                    in_file,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    cwd=problem_dir,
                    baseline_overhead_ms=baseline_overhead,
                )
            res = await DynamicExecutionMonitor.run_monitored_process(
                [sol_path],
                in_file,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                cwd=problem_dir,
                baseline_overhead_ms=baseline_overhead,
            )
            chosen_cpu = res.get("cpu_time_ms")
            chosen_mem = res.get("memory_mb")
            chosen_status = res.get("status")
            if (
                chosen_status == "ok"
                and chosen_cpu is not None
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
                    )
                    if rep.get("status") == "ok" and rep.get("cpu_time_ms") is not None:
                        cpu_runs.append(float(rep["cpu_time_ms"]))
                        mem_runs.append(float(rep.get("memory_mb") or 0.0))
                chosen_cpu = round(statistics.median(cpu_runs), 2)
                chosen_mem = round(max(mem_runs), 2)
            samples.append(
                {
                    "n": pt,
                    "cpu_time_ms": chosen_cpu,
                    "memory_mb": chosen_mem,
                    "status": chosen_status,
                }
            )
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

        final_complexity, patterns = estimate_static_complexity(code)

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
        risk_notes = build_risk_notes(final_complexity, effective_constraints, warnings)
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

        return ToolResult.ok(
            claimed_complexity=claimed_complexity or detected_claimed,
            estimated_complexity=final_complexity,
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
