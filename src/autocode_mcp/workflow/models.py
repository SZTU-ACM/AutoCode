from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    statement_path: str = "statements/README.md"
    tutorial_path: str = "statements/tutorial.md"
    constraints: dict[str, int] = Field(default_factory=dict)
    solutions: list[SolutionEntry] = Field(default_factory=list)
    case_plan: list[CasePlanItem] = Field(default_factory=list)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
