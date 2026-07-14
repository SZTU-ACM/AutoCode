"""
Pydantic input models for AutoCode MCP tools.

Each model is the single source of truth for a tool's ``input_schema``; the
schema is derived via ``input_schema_from_model`` (in ``base.py``) instead of a
hand-written JSON Schema dict that can drift from the ``execute`` signature.

All 19 tools are migrated (tasks 7.2 / 7.3 pilot + 7.4 remainder).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# --------------------------------------------------------------------------- #
# Pilot tools (7.2 / 7.3)
# --------------------------------------------------------------------------- #


class ProblemAuditInput(BaseModel):
    problem_dir: str
    mode: Literal["quick", "full"] = "full"
    include_difficulty: bool = True
    report_path: str | None = None


class FileReadInput(BaseModel):
    path: str
    problem_dir: str | None = None


class FileSaveInput(BaseModel):
    path: str
    content: str
    problem_dir: str | None = None


class StatementSample(BaseModel):
    input: str
    expected_output: str


class ProblemValidateInput(BaseModel):
    problem_dir: str
    validate_types: list[Literal["statement_samples", "sample_files", "all"]] = ["all"]
    statement_samples: list[StatementSample] | None = None
    tolerance: float = 1e-9
    timeout: int = 30


# --------------------------------------------------------------------------- #
# checker.py
# --------------------------------------------------------------------------- #


class CheckerScenario(BaseModel):
    input: str
    contestant_output: str
    reference_output: str
    expected_verdict: Literal["AC", "WA", "PE", "FAIL"] = "AC"


class CheckerBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    test_scenarios: list[CheckerScenario] | None = None
    compiler: str = "g++"


# --------------------------------------------------------------------------- #
# build_all.py
# --------------------------------------------------------------------------- #


class ProblemBuildAllInput(BaseModel):
    problem_dir: str
    compiler: str = "g++"
    max_concurrent: int = 4
    include_extra_dirs: list[str] = []


# --------------------------------------------------------------------------- #
# interactor.py
# --------------------------------------------------------------------------- #


class InteractionScenario(BaseModel):
    input: str
    answer: str | None = None
    contestant_output: str
    expected_verdict: Literal["AC", "WA", "PE", "FAIL", "TLE"] = "AC"


class InteractorBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    reference_solution_path: str | None = None
    mutant_solutions: list[str] | None = None
    interaction_scenarios: list[InteractionScenario] | None = None
    compiler: str = "g++"


# --------------------------------------------------------------------------- #
# generator.py
# --------------------------------------------------------------------------- #


class GeneratorBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    compiler: str = "g++"
    enable_semantic_check: bool = True
    strict_semantic_check: bool = False


class GeneratorRunInput(BaseModel):
    problem_dir: str
    strategies: list[Literal["tiny", "random", "extreme", "tle"]]
    test_count: int = 20
    validator_path: str | None = None
    seed_start: int = 1
    n_min: int = 1
    n_max: int = 100000
    t_min: int = 1
    t_max: int = 1
    extra_args: list[str] = []


# --------------------------------------------------------------------------- #
# validator.py
# --------------------------------------------------------------------------- #


class ValidatorTestCase(BaseModel):
    input: str
    expected_valid: bool


class ValidatorBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    test_cases: list[ValidatorTestCase] | None = None
    compiler: str = "g++"


class ValidatorCandidate(BaseModel):
    id: str
    score: int
    binary_path: str


class ValidatorSelectInput(BaseModel):
    candidates: list[ValidatorCandidate]


# --------------------------------------------------------------------------- #
# complexity.py
# --------------------------------------------------------------------------- #


class AnalyzeConstraints(BaseModel):
    n_max: int | None = None
    time_limit_ms: int | None = None


class SolutionAnalyzeInput(BaseModel):
    code: str | None = None
    problem_dir: str | None = None
    solution_type: Literal["sol", "brute"] = "sol"
    source_path: str | None = None
    constraints: AnalyzeConstraints | None = None


# --------------------------------------------------------------------------- #
# solution.py
# --------------------------------------------------------------------------- #


class SolutionBuildInput(BaseModel):
    problem_dir: str
    solution_type: Literal["sol", "brute"]
    name: str | None = None
    code: str | None = None
    source_path: str | None = None
    compiler: str = "g++"


class SolutionRunInput(BaseModel):
    problem_dir: str
    solution_type: Literal["sol", "brute"]
    input_data: str
    name: str | None = None
    timeout: int = 30


# --------------------------------------------------------------------------- #
# test_verify.py
# --------------------------------------------------------------------------- #


class ProblemVerifyTestsInput(BaseModel):
    problem_dir: str
    tests_dir: str | None = None
    verify_types: list[
        Literal[
            "file_count",
            "answer_consistency",
            "validator",
            "no_empty",
            "limit_ratio",
            "limit_semantics",
            "wrong_solution_kill",
            "duplicate_inputs",
            "scale_distribution",
            "purpose_coverage",
            "validator_self_test",
            "checker_self_test",
            "interactor_self_test",
        ]
    ] | None = None
    sol_name: str | None = None
    enable_limit_ratio: bool = True
    answer_ext: str | None = None
    timeout: int = 60
    wrong_solution_names: list[str] | None = None


# --------------------------------------------------------------------------- #
# stress_test.py
# --------------------------------------------------------------------------- #


class StressGeneratorArgs(BaseModel):
    type: str = "2"
    n_min: int = 1
    n_max: int | None = None
    t_min: int = 1
    t_max: int = 1
    extra_args: list[str] = []


class StressProfile(BaseModel):
    name: str | None = None
    trials: int | None = None
    types: list[Literal["1", "2", "3", "4"]] | None = None
    generator_args: dict | None = None


class StressTestRunInput(BaseModel):
    problem_dir: str
    trials: int = 1000
    n_max: int = 100
    timeout: int = 30
    sol_name: str | None = None
    brute_name: str | None = None
    types: list[Literal["1", "2", "3", "4"]] | None = None
    generator_args: StressGeneratorArgs | None = None
    stress_profiles: list[StressProfile] | None = None
    concurrency_limit: int = 4


# --------------------------------------------------------------------------- #
# solution_audit.py
# --------------------------------------------------------------------------- #


class SolutionAuditStdInput(BaseModel):
    code: str | None = None
    source_path: str | None = None
    problem_dir: str | None = None
    constraints: dict | None = None
    claimed_complexity: str | None = None


class SolutionAuditBruteInput(BaseModel):
    code: str | None = None
    source_path: str | None = None
    problem_dir: str | None = None
    std_complexity: str | None = None
    constraints: dict | None = None


# --------------------------------------------------------------------------- #
# problem.py
# --------------------------------------------------------------------------- #


class ProblemCreateInput(BaseModel):
    problem_dir: str
    problem_name: str
    interactive: bool = False


class GenerateConstraints(BaseModel):
    n_max: int | None = None
    t_max: int | None = None
    sum_n_max: int | None = None


class GenerateTestConfig(BaseModel):
    seed_offset: int = 0
    type: Literal["1", "2", "3", "4"]
    n_min: int
    n_max: int
    t_min: int
    t_max: int
    extra_args: list[str] = []


class ProblemGenerateTestsInput(BaseModel):
    problem_dir: str
    test_count: int = 20
    timeout: int = 60
    constraints: GenerateConstraints | None = None
    test_configs: list[GenerateTestConfig] | None = None
    output_dir: str | None = None
    sol_name: str | None = None
    enable_dedup: bool = True
    enable_validator_filter: bool = True
    enable_balance: bool = True
    oversample_ratio: float = 1.5
    answer_ext: str = ".ans"
    resume: bool = False
    hard_timeout_seconds: int | None = None
    checkpoint_every: int = 10
    concurrency_limit: int = 4


class ProblemCleanupProcessesInput(BaseModel):
    problem_dir: str
    kill_all_generators: bool = True


class ProblemPackPolygonInput(BaseModel):
    problem_dir: str
    time_limit: int = 1
    memory_limit: int = 256
