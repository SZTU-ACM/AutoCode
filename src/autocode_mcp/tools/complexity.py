from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from ..utils.execution_monitor import DynamicExecutionMonitor
from ..utils.platform import get_exe_extension
from ..utils.ratio_analyzer import EmpiricalRatioAnalyzer, normalize_complexity_expression
from ..utils.scale_sampler import MultiScaleSampler
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
    ComplexityLevel.QUADRATIC: 3000,
    ComplexityLevel.CUBIC: 5000,
    ComplexityLevel.EXPONENTIAL: 10000,
    ComplexityLevel.FACTORIAL: 10000,
}


def analyze_loop_complexity(code: str) -> str:
    loop_patterns = [
        r"\bfor\s*\(",
        r"\bwhile\s*\(",
        r"\bfor\s+\w+.*:",
    ]

    max_nesting = 0
    brace_depth = 0
    saw_loop = False

    lines = code.split("\n")
    for line in lines:
        if "//" in line:
            line = line[: line.index("//")]

        has_loop = any(re.search(p, line) for p in loop_patterns)
        if has_loop:
            saw_loop = True
            max_nesting = max(max_nesting, brace_depth)

        for char in line:
            if char == "{":
                brace_depth += 1
            elif char == "}":
                brace_depth = max(0, brace_depth - 1)

    if not saw_loop:
        return ComplexityLevel.CONSTANT
    if max_nesting <= 1:
        return ComplexityLevel.LINEAR
    elif max_nesting == 2:
        return ComplexityLevel.QUADRATIC
    elif max_nesting == 3:
        return ComplexityLevel.CUBIC
    return ComplexityLevel.EXPONENTIAL


def detect_algorithm_patterns(code: str) -> tuple[str | None, list[str]]:
    patterns: list[str] = []
    complexity: str | None = None

    if re.search(r"\bbinary_search\b|\blower_bound\b|\bupper_bound\b", code):
        patterns.append("binary_search")
        complexity = ComplexityLevel.N_LOG_N

    if re.search(r"\bsort\b|\bstable_sort\b|\bpartial_sort\b", code):
        patterns.append("sorting")
        complexity = ComplexityLevel.N_LOG_N

    if re.search(r"\bmerge(_sort|_count)?\b|\bdivide_and_conquer\b", code, re.IGNORECASE):
        patterns.append("divide_and_conquer")
        complexity = ComplexityLevel.N_LOG_N

    if re.search(r"\b(fenwick|bit|segtree|segment_tree)\b", code, re.IGNORECASE):
        patterns.append("tree_data_structure")
        complexity = ComplexityLevel.N_LOG_N

    if re.search(r"\bbfs\b|\bdfs\b|queue<|stack<", code):
        patterns.append("graph_traversal")
        complexity = ComplexityLevel.LINEAR

    if re.search(r"dp\[|memo\[|memoization", code):
        patterns.append("dynamic_programming")
        complexity = ComplexityLevel.QUADRATIC

    if re.search(r"unordered_map|unordered_set|hash_map", code):
        patterns.append("hash_table")

    if re.search(r"\breturn\s+\w+\s*\([^)]*\)", code) and re.search(
        r"\b\w+\s*\([^)]*\)\s*{", code
    ):
        patterns.append("recursion")

    if re.search(r"1\s*<<\s*\w+|bitmask|bitset", code):
        patterns.append("bitmask")
        complexity = ComplexityLevel.EXPONENTIAL

    return complexity, patterns


def extract_claimed_complexity(code: str) -> str | None:
    match = re.search(r"O\([^)]*\)", code)
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

    if not os.path.isfile(gen_path) or not os.path.isfile(sol_path):
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

    actual_n_max = 10000
    time_limit_ms = 2000.0
    if constraints:
        actual_n_max = int(constraints.get("n_max") or 10000)
        time_limit_ms = float(constraints.get("time_limit_ms") or 2000.0)

    effective_complexity = claimed_complexity
    scale_points = MultiScaleSampler.compute_scale_points(actual_n_max, effective_complexity)

    empirical_dir = os.path.join(problem_dir, ".autocode", "empirical_tests")
    os.makedirs(empirical_dir, exist_ok=True)

    baseline_overhead = await DynamicExecutionMonitor.measure_baseline_overhead(cwd=problem_dir)

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

        res = await DynamicExecutionMonitor.run_monitored_process(
            [sol_path],
            in_file,
            time_limit_ms=time_limit_ms,
            cwd=problem_dir,
            baseline_overhead_ms=baseline_overhead,
        )
        samples.append({
            "n": pt,
            "cpu_time_ms": res.get("cpu_time_ms"),
            "memory_mb": res.get("memory_mb"),
            "status": res.get("status"),
        })
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

        if pattern_complexity is not None:
            complexity_order = [
                ComplexityLevel.CONSTANT,
                ComplexityLevel.LOG_N,
                ComplexityLevel.LINEAR,
                ComplexityLevel.N_LOG_N,
                ComplexityLevel.QUADRATIC,
                ComplexityLevel.CUBIC,
                ComplexityLevel.EXPONENTIAL,
                ComplexityLevel.FACTORIAL,
            ]
            loop_idx = complexity_order.index(loop_complexity)
            pattern_idx = complexity_order.index(pattern_complexity)
            final_complexity = pattern_complexity if pattern_idx > loop_idx else loop_complexity
        else:
            final_complexity = loop_complexity

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
