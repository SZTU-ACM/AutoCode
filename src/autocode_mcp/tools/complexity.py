from __future__ import annotations

import asyncio
import math
import os
import re
from typing import Any

from ..utils.communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_participant_binaries,
)
from ..utils.execution_monitor import DynamicExecutionMonitor
from ..utils.judge_runner import InteractiveRunner
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


def extract_n_max_from_constraints(constraints: dict[str, Any] | None) -> int | None:
    """优先按显式规模字段（n_max/sum_n_max/n）提取主输入规模，避免被值域上限（如 a_i<=1e9）污染。"""
    if not isinstance(constraints, dict):
        return None
    priority_values: list[int] = []
    for key in ("n_max", "N_max", "sum_n_max", "sum_N_max"):
        parsed = _positive_int(constraints.get(key))
        if parsed is not None:
            priority_values.append(parsed)
    for key in ("n", "N"):
        raw = constraints.get(key)
        parsed = _positive_int(raw)
        if parsed is not None:
            priority_values.append(parsed)
        elif isinstance(raw, dict):
            for subkey in ("max", "maximum", "max_value", "n_max", "sum_max"):
                nested = _positive_int(raw.get(subkey))
                if nested is not None:
                    priority_values.append(nested)
    if priority_values:
        return max(priority_values)
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

    if re.search(r"\bbfs\b|\bdfs\b|queue<|stack<", cleaned):
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


def extract_claimed_complexity(code: str) -> str | None:
    match = re.search(r"\bO\(([^()\n]*(?:\([^()\n]*\)[^()\n]*)*)\)", code)
    if not match:
        return None
    return match.group(0)


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
    array_patterns = [
        r"(\w+)\s*\[(\d+)\]",
        r"vector<\w+>\s+(\w+)\s*\((\d+)\)",
        r"array<\w+,\s*(\d+)>",
    ]

    total_elements = 0
    for pattern in array_patterns:
        matches = re.findall(pattern, code)
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

    if total_elements == 0:
        return "O(1)", 64
    elif total_elements < 10000:
        return "O(n)", memory_mb
    elif total_elements < 1000000:
        return "O(n)", memory_mb
    return "O(n) - large", memory_mb


async def run_empirical_verification(
    problem_dir: str,
    solution_type: str,
    claimed_complexity: str | None,
    constraints: dict[str, Any] | None,
) -> dict[str, Any]:
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
    if manifest_model is not None:
        manifest_tl = _positive_float(manifest_model.time_limit_ms)
        if manifest_tl is not None:
            time_limit_ms = manifest_tl
        manifest_n = extract_n_max_from_constraints(manifest_model.constraints)
        if manifest_n is not None:
            actual_n_max = manifest_n
    if constraints:
        explicit_n = _positive_int(constraints.get("n_max"))
        if explicit_n is not None:
            actual_n_max = explicit_n
        explicit_tl = _positive_float(constraints.get("time_limit_ms"))
        if explicit_tl is not None:
            time_limit_ms = explicit_tl

    effective_complexity = claimed_complexity
    scale_points = MultiScaleSampler.compute_scale_points(actual_n_max, effective_complexity)

    empirical_dir = os.path.join(problem_dir, ".autocode", "empirical_tests")
    os.makedirs(empirical_dir, exist_ok=True)

    baseline_overhead = await DynamicExecutionMonitor.measure_baseline_overhead(cwd=problem_dir)

    is_interactive = manifest_is_interactive(manifest_model)
    is_communication = manifest_is_communication(manifest_model)
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
        gen_cmd = MultiScaleSampler.format_generator_command(gen_path, pt, 42 + idx)
        try:
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
            if is_communication and manifest_model is not None:
                jres = await CommunicationRunner.run_from_manifest(
                    problem_dir=problem_dir,
                    manifest=manifest_model,
                    solution_exe=sol_path,
                    input_data=scale_input,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                )
            else:
                jres = await InteractiveRunner.run(
                    interactor_exe=interactor_path,
                    solution_exe=sol_path,
                    input_data=scale_input,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    cwd=problem_dir,
                )
            status_map = {
                "AC": "ok",
                "PC": "ok",
                "TLE": "timeout",
                "DEADLOCK": "timeout",
                "MLE": "mle",
            }
            sample_status = status_map.get(jres.verdict, "runtime_error")
            adjusted_cpu = None
            if sample_status == "ok":
                raw_cpu = jres.cpu_time_ms if jres.cpu_time_ms is not None else jres.wall_time_ms
                adjusted_cpu = round(max(0.1, float(raw_cpu) - baseline_overhead), 2)
            samples.append(
                {
                    "n": pt,
                    "cpu_time_ms": adjusted_cpu,
                    "memory_mb": jres.memory_mb,
                    "status": sample_status,
                    "verdict": jres.verdict,
                }
            )
            if sample_status in ("timeout", "mle"):
                break
        else:
            res = await DynamicExecutionMonitor.run_monitored_process(
                [sol_path],
                in_file,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                cwd=problem_dir,
                baseline_overhead_ms=baseline_overhead,
            )
            samples.append(
                {
                    "n": pt,
                    "cpu_time_ms": res.get("cpu_time_ms"),
                    "memory_mb": res.get("memory_mb"),
                    "status": res.get("status"),
                }
            )
            if res.get("status") in ("timeout", "mle"):
                break

    return EmpiricalRatioAnalyzer.verify_complexity(
        effective_complexity,
        samples,
        time_limit_ms=time_limit_ms,
    )


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

        loop_complexity = analyze_loop_complexity(code)
        pattern_complexity, patterns = detect_algorithm_patterns(code)

        final_complexity = (
            _higher_complexity(loop_complexity, pattern_complexity)
            if pattern_complexity is not None
            else loop_complexity
        )

        if claimed_complexity:
            norm_claimed = normalize_complexity_expression(claimed_complexity)
            final_complexity = norm_claimed

        space_complexity, memory_mb = estimate_memory_usage(code)

        recommended_n_max = COMPLEXITY_TO_N_MAX.get(final_complexity, 10000)
        recommended_time_ms = COMPLEXITY_TO_TIME_LIMIT.get(final_complexity, 1000)
        detected_claimed = extract_claimed_complexity(code)

        warnings: list[str] = []
        if constraints:
            if constraints.get("n_max"):
                if constraints["n_max"] > recommended_n_max:
                    warnings.append(
                        f"Warning: n_max={constraints['n_max']} may cause TLE "
                        f"for {final_complexity} algorithm. Recommended: {recommended_n_max}"
                    )
            if constraints.get("time_limit_ms"):
                if constraints["time_limit_ms"] < recommended_time_ms:
                    warnings.append(
                        f"Warning: time_limit={constraints['time_limit_ms']}ms may be too tight "
                        f"for {final_complexity} algorithm. Recommended: {recommended_time_ms}ms"
                    )

        risk_notes = build_risk_notes(final_complexity, constraints, warnings)
        suggested_test_configs = self._generate_test_configs(recommended_n_max, constraints)
        stress_profiles = self._recommended_stress_profiles(
            final_complexity=final_complexity,
            recommended_n_max=recommended_n_max,
            constraints=constraints,
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
        actual_n_max = constraints.get("n_max", n_max) if constraints else n_max
        return [
            {"type": "1", "n_min": 1, "n_max": 1, "t_min": 1, "t_max": 1},
            {"type": "1", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 1},
            {"type": "2", "n_min": 10, "n_max": actual_n_max // 10, "t_min": 1, "t_max": 1},
            {"type": "2", "n_min": actual_n_max // 10, "n_max": actual_n_max // 2, "t_min": 1, "t_max": 1},
            {"type": "3", "n_min": actual_n_max // 2, "n_max": actual_n_max, "t_min": 1, "t_max": 1},
            {"type": "3", "n_min": actual_n_max, "n_max": actual_n_max, "t_min": 1, "t_max": 1},
        ]

    def _recommended_stress_profiles(
        self,
        final_complexity: str,
        recommended_n_max: int,
        constraints: dict | None,
    ) -> list[dict]:
        n_cap = constraints.get("n_max", recommended_n_max) if constraints else recommended_n_max
        brute_n = min(max(20, n_cap // 50), 2000)
        trials = 300 if final_complexity in {ComplexityLevel.QUADRATIC, ComplexityLevel.CUBIC} else 1000
        return [
            {
                "name": "tiny_exhaustive",
                "trials": min(200, trials),
                "types": ["1"],
                "generator_args": {"type": "1", "n_min": 1, "n_max": 8, "t_min": 1, "t_max": 1},
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
