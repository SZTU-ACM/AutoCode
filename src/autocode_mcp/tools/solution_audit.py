"""
Solution 审计工具：审核标准解与暴力解的可行性与复杂度假设。
"""

from __future__ import annotations

from .base import Tool, ToolResult
from .complexity import ComplexityLevel, analyze_loop_complexity
from .mixins import resolve_source


class SolutionAuditStdTool(Tool):
    @property
    def name(self) -> str:
        return "solution_audit_std"

    @property
    def description(self) -> str:
        return "审核标准解与题目约束/复杂度是否匹配，输出结构化风险与证据。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "source_path": {"type": "string"},
                "problem_dir": {"type": "string"},
                "constraints": {"type": "object"},
                "claimed_complexity": {"type": "string"},
            },
            "anyOf": [
                {"required": ["code"]},
                {"required": ["source_path"]},
            ],
        }

    async def execute(
        self,
        code: str | None = None,
        source_path: str | None = None,
        problem_dir: str = ".",
        constraints: dict | None = None,
        claimed_complexity: str | None = None,
    ) -> ToolResult:
        resolved, err = resolve_source(problem_dir, code, source_path)
        if err is not None:
            return err
        assert resolved is not None
        code = resolved.code
        estimated = analyze_loop_complexity(code)
        findings: list[dict] = []
        passed = True
        if claimed_complexity and claimed_complexity != estimated:
            findings.append(
                {
                    "severity": "warning",
                    "type": "complexity_mismatch",
                    "message": f"claimed={claimed_complexity}, estimated={estimated}",
                }
            )
        if constraints and constraints.get("n_max", 0) >= 10**6 and estimated in {
            ComplexityLevel.QUADRATIC,
            ComplexityLevel.CUBIC,
            ComplexityLevel.EXPONENTIAL,
            ComplexityLevel.FACTORIAL,
        }:
            findings.append(
                {
                    "severity": "error",
                    "type": "high_tle_risk",
                    "message": "n_max 较大但标准解复杂度偏高，存在明显 TLE 风险。",
                }
            )
            passed = False
        return ToolResult.ok(
            passed=passed,
            estimated_complexity=estimated,
            claimed_complexity=claimed_complexity,
            findings=findings,
            evidence={"has_sort": "sort(" in code, "has_nested_loops": code.count("for (") >= 2},
        )


class SolutionAuditBruteTool(Tool):
    @property
    def name(self) -> str:
        return "solution_audit_brute"

    @property
    def description(self) -> str:
        return "审核暴力解是否足够慢但可靠，并给出推荐对拍参数。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "source_path": {"type": "string"},
                "problem_dir": {"type": "string"},
                "std_complexity": {"type": "string"},
                "constraints": {"type": "object"},
            },
            "anyOf": [
                {"required": ["code"]},
                {"required": ["source_path"]},
            ],
        }

    async def execute(
        self,
        code: str | None = None,
        source_path: str | None = None,
        problem_dir: str = ".",
        std_complexity: str | None = None,
        constraints: dict | None = None,
    ) -> ToolResult:
        resolved, err = resolve_source(problem_dir, code, source_path)
        if err is not None:
            return err
        assert resolved is not None
        code = resolved.code
        brute_complexity = analyze_loop_complexity(code)
        findings: list[dict] = []
        if std_complexity and brute_complexity == std_complexity:
            findings.append(
                {
                    "severity": "warning",
                    "type": "same_order_as_std",
                    "message": "暴力解复杂度与 std 同阶，可能无法形成有效对拍压力。",
                }
            )
        recommended_n_max = 150
        if brute_complexity in {ComplexityLevel.CUBIC, ComplexityLevel.EXPONENTIAL, ComplexityLevel.FACTORIAL}:
            recommended_n_max = 40
        elif brute_complexity == ComplexityLevel.QUADRATIC:
            recommended_n_max = 80
        recommended_trials = 1200 if recommended_n_max >= 100 else 600
        return ToolResult.ok(
            passed=True,
            brute_complexity=brute_complexity,
            findings=findings,
            constraints=constraints or {},
            recommended_stress_params={
                "trials": recommended_trials,
                "n_max": recommended_n_max,
                "types": ["1", "2", "3"],
                "timeout": 30,
            },
        )
