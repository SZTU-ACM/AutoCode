from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CasePlanItem(BaseModel):
    name: str
    type: Literal["1", "2", "3", "4"]
    seed: int = Field(default=1, ge=0)
    group: str = "default"
    purpose: str | None = None
    check_with: list[str] = Field(default_factory=list)


class SolutionEntry(BaseModel):
    name: str
    role: Literal["main", "brute", "reference", "wrong"]
    language: Literal["cpp", "python"] = "cpp"
    path: str
    expected: Literal["pass", "fail"] | None = None


class QualityGates(BaseModel):
    require_stress_passed: bool = True
    require_validation_passed: bool = True
    require_tests_verified: bool = True
    require_limit_semantics: bool = True
    require_wrong_solution_kill: bool = False
    require_validator_check: bool = True
    min_limit_case_ratio: float = Field(default=0.5, ge=0, le=1)


class AutoCodeManifest(BaseModel):
    schema_version: str = "1.0"
    problem_name: str
    interactive: bool = False
    special_judge: bool = Field(
        default=False,
        description="标记为特殊判题题；与 stress_comparison=checker 联用时对拍/终测/错解/样例走 testlib checker",
    )
    stress_comparison: Literal["exact", "checker"] = Field(
        default="exact",
        description="仅当 special_judge 时建议关注：exact=对拍与终测仍主要比字符串；checker=对拍与终测等用 checker（须已编译 files/checker）",
    )
    stress_checker_bidirectional: bool = Field(
        default=False,
        description="仅当 special_judge 且 stress_comparison=checker：为 true 时对拍再调用 checker(in,brute,sol)，要求与 checker(in,sol,brute) 均为 AC",
    )
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    statement_path: str = "statements/README.md"
    tutorial_path: str = "statements/tutorial.md"
    constraints: dict[str, int] = Field(default_factory=dict)
    solutions: list[SolutionEntry] = Field(default_factory=list)
    case_plan: list[CasePlanItem] = Field(default_factory=list)
    quality_gates: QualityGates = Field(default_factory=QualityGates)

    @model_validator(mode="before")
    @classmethod
    def normalize_stress_checker_bidirectional(cls, data: Any) -> Any:
        """无 checker 对拍工作流时忽略误设的 stress_checker_bidirectional。"""
        if not isinstance(data, dict):
            return data
        if data.get("stress_checker_bidirectional") and not (
            bool(data.get("special_judge")) and data.get("stress_comparison") == "checker"
        ):
            return {**data, "stress_checker_bidirectional": False}
        return data
