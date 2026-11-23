"""
Pydantic input models for AutoCode MCP tools.

Each model is the single source of truth for a tool's ``input_schema``; the
schema is derived via ``input_schema_from_model`` (in ``base.py``) instead of a
hand-written JSON Schema dict that can drift from the ``execute`` signature.

All 19 tools are migrated (tasks 7.2 / 7.3 pilot + 7.4 remainder).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..workflow.models import OracleMode, ProblemType, ProtocolMode

GeneratorTypeStr = Annotated[
    str,
    Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"),
]

# --------------------------------------------------------------------------- #
# Pilot tools (7.2 / 7.3)
# --------------------------------------------------------------------------- #


class ProblemAuditInput(BaseModel):
    problem_dir: str
    mode: Literal["quick", "full"] = "full"
    include_difficulty: bool = True
    report_path: str | None = None
    standalone: bool = False
    layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto"
    enable_mutation_testing: bool | None = None


class FileReadInput(BaseModel):
    path: str
    problem_dir: str | None = None
    offset_bytes: int | None = Field(default=None, ge=0)
    limit_bytes: int | None = Field(default=None, ge=1)
    start_line: int | None = Field(default=None, ge=1)
    line_count: int | None = Field(default=None, ge=1)


class FileSaveInput(BaseModel):
    path: str
    content: str
    problem_dir: str | None = None


class StatementSample(BaseModel):
    input: str
    expected_output: str = ""


class ProblemValidateInput(BaseModel):
    problem_dir: str
    validate_types: list[Literal["statement_samples", "sample_files", "all"]] = ["all"]
    statement_samples: list[StatementSample] | None = None
    samples_dir: str | None = Field(
        default=None,
        description="Optional directory containing sample .in/.ans files (defaults to tests/samples if present, else tests/).",
    )
    tolerance: float = 1e-9
    timeout: int = 30


# --------------------------------------------------------------------------- #
# checker.py
# --------------------------------------------------------------------------- #


class CheckerScenario(BaseModel):
    input: str
    contestant_output: str
    reference_output: str
    expected_verdict: Literal["AC", "WA", "PE", "PC", "FAIL"] = "AC"
    category: str | None = None


class CheckerBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    test_scenarios: list[CheckerScenario] | None = None
    compiler: str | None = Field(
        default=None,
        description="C++ compiler command/path; defaults to manifest.compiler or 'g++'",
    )


# --------------------------------------------------------------------------- #
# build_all.py
# --------------------------------------------------------------------------- #


class ProblemBuildAllInput(BaseModel):
    problem_dir: str
    compiler: str | None = Field(
        default=None,
        description="C++ compiler command/path; defaults to manifest.compiler or 'g++'",
    )
    max_concurrent: int = 4
    include_extra_dirs: list[str] = []


# --------------------------------------------------------------------------- #
# interactor.py
# --------------------------------------------------------------------------- #


class InteractionScenario(BaseModel):
    input: str
    answer: str | None = None
    contestant_output: str
    expected_verdict: Literal["AC", "WA", "PE", "PC", "FAIL", "TLE"] = "AC"
    stage: int | None = Field(default=None, ge=1)
    category: str | None = None


class InteractorBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    reference_solution_path: str | None = Field(
        default=None,
        description=(
            "Compiled reference solution executable path (e.g. 'solutions/sol' or 'solutions/sol.exe'). "
            "Relative paths are resolved under problem_dir; passing a .cpp source path automatically "
            "maps to or compiles its executable binary. When omitted with test_inputs, auto-resolves "
            "solutions/sol if built."
        ),
    )
    mutant_solutions: list[str] | None = Field(
        default=None,
        description=(
            "List of mutant/wrong solution paths (compiled executables or .cpp source files, "
            "absolute or relative to problem_dir)."
        ),
    )
    interaction_scenarios: list[InteractionScenario] | None = None
    test_inputs: list[str] | None = None
    compiler: str | None = Field(
        default=None,
        description="C++ compiler command/path; defaults to manifest.compiler or 'g++'",
    )


# --------------------------------------------------------------------------- #
# generator.py
# --------------------------------------------------------------------------- #


class GeneratorBuildInput(BaseModel):
    problem_dir: str
    code: str | None = None
    source_path: str | None = None
    compiler: str | None = Field(
        default=None,
        description="C++ compiler command/path; defaults to manifest.compiler or 'g++'",
    )
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
    compiler: str | None = Field(
        default=None,
        description="C++ compiler command/path; defaults to manifest.compiler or 'g++'",
    )


class ValidatorCandidate(BaseModel):
    id: str
    score: int
    binary_path: str
    code: str | None = None
    source_path: str | None = None


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
    claimed_complexity: str | None = None


# --------------------------------------------------------------------------- #
# solution.py
# --------------------------------------------------------------------------- #


class SolutionBuildInput(BaseModel):
    problem_dir: str
    solution_type: Literal["sol", "brute", "wrong", "reference"]
    name: str | None = None
    code: str | None = None
    source_path: str | None = None
    compiler: str | None = Field(
        default=None,
        description="C++ compiler command/path; defaults to manifest.compiler or 'g++'",
    )
    enable_sanitizers: bool = Field(
        default=False,
        description="Build with AddressSanitizer/UBSan (auto-probes runtime and static fallback on Linux)",
    )


class SolutionRunInput(BaseModel):
    problem_dir: str
    solution_type: Literal["sol", "brute", "wrong", "reference"]
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
            "all",
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
            "boundary_coverage",
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
    tle_kill_factor: float | None = Field(
        default=None,
        gt=0.0,
        le=5.0,
        description="Override logical TLE threshold multiplier for wrong_solution_kill (defaults to manifest.quality_gates.wrong_tle_kill_factor, default 1.0)",
    )
    slowdown_kill_ratio: float | None = Field(
        default=None,
        gt=1.0,
        description="Optional override: kill wrong solution as TLE when wrong_ms/std_ms >= this ratio (with wrong_ms >= 50ms)",
    )


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
    types: list[GeneratorTypeStr] | None = None
    generator_args: dict | None = None


class StressTestRunInput(BaseModel):
    problem_dir: str
    trials: int = 1000
    n_max: int = 100
    timeout: int = 30
    sol_name: str | None = None
    brute_name: str | None = None
    types: list[GeneratorTypeStr] | None = None
    generator_args: StressGeneratorArgs | None = None
    stress_profiles: list[StressProfile] | None = None
    concurrency_limit: int = 4
    enable_sanitizers: bool = Field(
        default=False,
        description="Compile instrumented binaries into .autocode/san_bin/ with ASan/UBSan and run stress rounds under sanitizers",
    )


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
    problem_type: ProblemType | None = None
    protocol_mode: ProtocolMode | None = None
    oracle_mode: OracleMode = "brute"
    time_limit_ms: int | None = Field(
        default=None,
        ge=1,
        description="Optional initial time limit in milliseconds (e.g. 1000 or 2000); defaults to 2000",
    )
    memory_limit_mb: int | None = Field(
        default=None,
        ge=1,
        description="Optional initial memory limit in MB (e.g. 256 or 512); defaults to 256",
    )
    overwrite: bool = Field(
        default=False,
        description="Set true to explicitly overwrite an existing .autocode/manifest.json",
    )

    @field_validator("time_limit_ms", "memory_limit_mb", mode="before")
    @classmethod
    def _reject_bool_limits(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("limit must be a positive integer, not boolean")
        return v

    @field_validator("overwrite", mode="before")
    @classmethod
    def _require_strict_bool_overwrite(cls, v: Any) -> Any:
        if not isinstance(v, bool):
            raise ValueError("overwrite must be a boolean")
        return v


class GenerateConstraints(BaseModel):
    n_max: int | None = None
    t_max: int | None = None
    sum_n_max: int | None = None


class GenerateTestConfig(BaseModel):
    seed_offset: int = 0
    type: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$",
        description="Generator strategy type (standard '1'..'4' or custom hack types like '5', '6', 'hack')",
    )
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
    mode: Literal["replace", "append", "register_existing"] = Field(
        default="replace",
        description=(
            "'replace' (default, preserves locked/tests/hack cases), "
            "'append' (keeps existing tests/ and appends new cases), or "
            "'register_existing' (indexes existing tests/ without deleting or regenerating)."
        ),
    )
    hard_timeout_seconds: int | None = None
    checkpoint_every: int = 10
    concurrency_limit: int = 4


class ProblemCleanupProcessesInput(BaseModel):
    problem_dir: str
    kill_all_generators: bool = True


class ProblemPackPolygonInput(BaseModel):
    problem_dir: str
    time_limit: int | None = Field(
        default=None,
        ge=1,
        description="Time limit in SECONDS (e.g., 1 or 2). Do NOT pass milliseconds; use time_limit_ms instead.",
    )
    time_limit_ms: int | None = Field(
        default=None,
        ge=1,
        description="Optional time limit in MILLISECONDS (e.g., 1000 or 2000). Overrides manifest.time_limit_ms.",
    )
    allow_time_limit_override: bool = Field(
        default=False,
        description="Set true to explicitly allow time_limit > 60s or >10x deviation from manifest.time_limit_ms.",
    )
    memory_limit: int | None = Field(
        default=None,
        ge=1,
        description="Memory limit in MB (e.g., 256 or 512).",
    )
    export_formats: list[Literal["polygon", "hydro", "qoj"]] = ["polygon"]
    export_data_mode: Literal["copy", "hardlink", "zip"] = Field(
        default="hardlink",
        description=(
            "How Polygon/Hydro/QOJ exporters stage test data files: "
            "'hardlink' (default, avoids 3x disk duplication with automatic copy fallback), "
            "'copy' (independent physical copies), or 'zip' (compressed archive under exports/<fmt>.zip)."
        ),
    )
