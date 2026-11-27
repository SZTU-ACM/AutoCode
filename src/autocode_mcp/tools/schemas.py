"""
Pydantic input models for AutoCode MCP tools.

Each model is the single source of truth for a tool's ``input_schema``; the
schema is derived via ``input_schema_from_model`` (in ``base.py``) instead of a
hand-written JSON Schema dict that can drift from the ``execute`` signature.

All 19 tools are migrated (tasks 7.2 / 7.3 pilot + 7.4 remainder).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations, ToolExecution
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..workflow.models import OracleMode, ProblemType, ProtocolMode
from .base import output_schema_from_model

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
    strict: bool = False


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
            "coverage",
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


# --------------------------------------------------------------------------- #
# Output schemas, ToolAnnotations, Task execution modes & UI hints (all 22)
# --------------------------------------------------------------------------- #


class ToolDataBase(BaseModel):
    """Shared base for tool ``data`` payloads; allows gate/compaction/error metadata."""

    model_config = ConfigDict(extra="allow")

    message: str | None = None


class ToolOutputEnvelope(BaseModel):
    """Canonical structuredContent envelope returned by all AutoCode MCP tools."""

    model_config = ConfigDict(extra="allow")

    success: bool
    error: str | None = None
    data: ToolDataBase | None = None


class FileReadData(ToolDataBase):
    path: str | None = None
    content: str | None = None
    size: int | None = None
    total_bytes: int | None = None
    total_lines: int | None = None
    next_offset: int | None = None
    is_truncated: bool | None = None


class FileReadOutput(ToolOutputEnvelope):
    data: FileReadData | None = None


class FileSaveData(ToolDataBase):
    path: str | None = None
    size: int | None = None


class FileSaveOutput(ToolOutputEnvelope):
    data: FileSaveData | None = None


class SolutionBuildData(ToolDataBase):
    source_path: str | None = None
    canonical_path: str | None = None
    standard_source_path: str | None = None
    binary_path: str | None = None
    standard_binary_path: str | None = None
    binary_size: int | None = None
    compile_log: str | None = None
    compiler: str | None = None
    effective_name: str | None = None
    sanitizers_enabled: bool | None = None
    sanitized_only: bool | None = None
    sanitizer_info: dict[str, Any] | None = None
    compiler_hint: str | None = None
    compiler_warning: str | None = None


class SolutionBuildOutput(ToolOutputEnvelope):
    data: SolutionBuildData | None = None


class SolutionRunData(ToolDataBase):
    mode: str | None = None
    verdict: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    judge_stderr: str | None = None
    time_ms: int | None = None
    cpu_time_ms: float | None = None
    memory_mb: float | None = None
    memory_limit_exceeded: bool | None = None
    return_code: int | None = None
    judge_return_code: int | None = None
    score: float | None = None
    metrics: dict[str, Any] | None = None
    transcript: str | None = None
    detail: str | None = None


class SolutionRunOutput(ToolOutputEnvelope):
    data: SolutionRunData | None = None


class SolutionAnalyzeData(ToolDataBase):
    claimed_complexity: str | None = None
    estimated_complexity: str | None = None
    static_estimated_complexity: str | None = None
    complexity_conflict: bool | None = None
    complexity_match: bool | None = None
    conflict_details: list[str] | None = None
    worst_case_complexity: str | None = None
    average_case_complexity: str | None = None
    time_complexity: str | None = None
    space_complexity: str | None = None
    memory_estimate: dict[str, Any] | None = None
    estimated_memory_mb: float | None = None
    detected_patterns: list[str] | None = None
    recommended_n_max: int | None = None
    recommended_time_limit_ms: int | None = None
    warnings: list[str] | None = None
    risk_notes: list[str] | None = None
    suggested_test_configs: list[dict[str, Any] | str] | None = None
    recommended_stress_params: list[dict[str, Any] | str] | None = None
    empirical_verification: dict[str, Any] | None = None


class SolutionAnalyzeOutput(ToolOutputEnvelope):
    data: SolutionAnalyzeData | None = None


class SolutionAuditStdData(ToolDataBase):
    passed: bool | None = None
    estimated_complexity: str | None = None
    static_estimated_complexity: str | None = None
    claimed_complexity: str | None = None
    space_complexity: str | None = None
    estimated_memory_mb: float | None = None
    detected_patterns: list[str] | None = None
    complexity_conflict: bool | None = None
    complexity_match: bool | None = None
    complexity_feasible: bool | None = None
    conflict_details: list[str] | None = None
    findings: list[dict[str, Any] | str] | None = None
    evidence: dict[str, Any] | None = None
    empirical_verification: dict[str, Any] | None = None
    dynamic_probe: dict[str, Any] | None = None


class SolutionAuditStdOutput(ToolOutputEnvelope):
    data: SolutionAuditStdData | None = None


class SolutionAuditBruteData(ToolDataBase):
    passed: bool | None = None
    brute_complexity: str | None = None
    static_estimated_complexity: str | None = None
    detected_patterns: list[str] | None = None
    findings: list[dict[str, Any] | str] | None = None
    constraints: dict[str, Any] | None = None
    recommended_stress_params: dict[str, Any] | None = None
    brute_probe: dict[str, Any] | None = None
    empirical_verification: dict[str, Any] | None = None
    dynamic_probe: dict[str, Any] | None = None


class SolutionAuditBruteOutput(ToolOutputEnvelope):
    data: SolutionAuditBruteData | None = None


class ValidatorBuildData(ToolDataBase):
    source_path: str | None = None
    canonical_path: str | None = None
    binary_path: str | None = None
    binary_size: int | None = None
    compile_log: str | None = None
    test_results: list[dict[str, Any] | str] | None = None
    score: int | None = None
    total: int | None = None
    accuracy: float | None = None
    positive_count: int | None = None
    negative_count: int | None = None
    has_positive_cases: bool | None = None
    has_negative_cases: bool | None = None
    warnings: list[str] | None = None


class ValidatorBuildOutput(ToolOutputEnvelope):
    data: ValidatorBuildData | None = None


class ValidatorSelectData(ToolDataBase):
    best_candidate: dict[str, Any] | None = None
    all_candidates: list[dict[str, Any] | str] | None = None


class ValidatorSelectOutput(ToolOutputEnvelope):
    data: ValidatorSelectData | None = None


class GeneratorBuildData(ToolDataBase):
    source_path: str | None = None
    canonical_path: str | None = None
    binary_path: str | None = None
    binary_size: int | None = None
    compile_log: str | None = None
    semantic_check: dict[str, Any] | None = None


class GeneratorBuildOutput(ToolOutputEnvelope):
    data: GeneratorBuildData | None = None


class GeneratorRunData(ToolDataBase):
    generated_count: int | None = None
    test_count: int | None = None
    inputs: list[dict[str, Any] | str] | None = None
    strategies_used: list[str] | None = None
    generator_failures: list[Any] | None = None


class GeneratorRunOutput(ToolOutputEnvelope):
    data: GeneratorRunData | None = None


class CheckerBuildData(ToolDataBase):
    source_path: str | None = None
    canonical_path: str | None = None
    binary_path: str | None = None
    binary_size: int | None = None
    compile_log: str | None = None
    test_results: list[dict[str, Any] | str] | None = None
    correct_count: int | None = None
    total: int | None = None
    accuracy: float | None = None


class CheckerBuildOutput(ToolOutputEnvelope):
    data: CheckerBuildData | None = None


class InteractorBuildData(ToolDataBase):
    source_path: str | None = None
    canonical_path: str | None = None
    binary_path: str | None = None
    binary_size: int | None = None
    compile_log: str | None = None
    interaction_scenarios: dict[str, Any] | None = None
    scenario_accuracy: float | None = None
    reference_solution_binary: str | None = None
    pass_rate: float | None = None
    fail_rate: float | None = None
    pass_count: int | None = None
    pass_total: int | None = None
    fail_count: int | None = None
    fail_total: int | None = None
    reference_results: list[dict[str, Any] | str] | None = None
    mutant_results: list[dict[str, Any] | str] | None = None


class InteractorBuildOutput(ToolOutputEnvelope):
    data: InteractorBuildData | None = None


class StressTestRunData(ToolDataBase):
    completed_rounds: int | None = None
    total_rounds: int | None = None
    effective_n_max: int | None = None
    statistics: dict[str, Any] | None = None
    stress_profiles: list[dict[str, Any] | str] | None = None
    n_max_warning: str | None = None
    n_max_advisory: str | None = None
    complexity_context: dict[str, Any] | None = None
    round: int | None = None
    input_data: str | None = None
    sol_output: str | None = None
    brute_output: str | None = None
    validator_failure_detail: dict[str, Any] | None = None
    mismatch_log_path: str | None = None
    sanitizers_enabled: bool | None = None


class StressTestRunOutput(ToolOutputEnvelope):
    data: StressTestRunData | None = None


class ProblemCreateData(ToolDataBase):
    problem_dir: str | None = None
    problem_name: str | None = None
    problem_type: str | None = None
    oracle_mode: str | None = None
    protocol_mode: str | None = None
    interactive: bool | None = None
    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    manifest_path: str | None = None
    created_directories: list[str] | None = None
    compiler_info: dict[str, Any] | None = None
    compiler_warning: str | None = None
    overwritten_existing_manifest: bool | None = None
    warning: str | None = None


class ProblemCreateOutput(ToolOutputEnvelope):
    data: ProblemCreateData | None = None


class ProblemGenerateTestsData(ToolDataBase):
    tests_dir: str | None = None
    generated_tests: list[dict[str, Any] | str] | None = None
    type_distribution: dict[str, Any] | None = None
    dedup_enabled: bool | None = None
    validator_filter_enabled: bool | None = None
    balance_enabled: bool | None = None
    limit_case_count: int | None = None
    limit_case_minimum_required: int | None = None
    limit_case_quota_met: bool | None = None
    candidates_generated: int | None = None
    sol_name: str | None = None
    answer_ext: str | None = None
    progress_snapshot: dict[str, Any] | None = None
    mode: str | None = None
    total_tests: int | None = None
    preserved_locked_count: int | None = None
    generator_tle_extra_args_fallbacks: int | None = None
    errors: list[Any] | None = None


class ProblemGenerateTestsOutput(ToolOutputEnvelope):
    data: ProblemGenerateTestsData | None = None


class ProblemCleanupProcessesData(ToolDataBase):
    removed_files: list[str] | None = None
    killed_pids: list[int | str] | None = None
    skipped_pids: list[Any] | None = None
    failed_pids: list[dict[str, Any] | str] | None = None
    warning: str | None = None


class ProblemCleanupProcessesOutput(ToolOutputEnvelope):
    data: ProblemCleanupProcessesData | None = None


class ProblemVerifyTestsData(ToolDataBase):
    passed: bool | None = None
    results: dict[str, Any] | None = None
    quality_signals: dict[str, Any] | None = None
    dataset_fingerprint: str | None = None
    executed_signals: list[str] | None = None
    total_checks: int | None = None
    passed_checks: int | None = None
    skipped_checks: int | None = None
    effective_checks: int | None = None
    tests_dir: str | None = None
    sol_name: str | None = None
    limit_ratio_enabled: bool | None = None


class ProblemVerifyTestsOutput(ToolOutputEnvelope):
    data: ProblemVerifyTestsData | None = None


class ProblemAuditData(ToolDataBase):
    decision: Literal["go", "no_go"] | None = None
    mode: Literal["quick", "full"] | None = None
    standalone: bool | None = None
    detected_layout: str | None = None
    manifest_inferred: bool | None = None
    problem_dir: str | None = None
    generated_at: str | None = None
    blocking_issues: list[dict[str, Any] | str] | None = None
    risk_report: dict[str, Any] | None = None
    structural_signals: dict[str, Any] | None = None
    quality_signals: dict[str, Any] | None = None
    difficulty_signals: dict[str, Any] | None = None
    communication_metrics: dict[str, Any] | None = None
    completeness_warnings: list[Any] | None = None
    next_actions: list[Any] | None = None
    strict: bool | None = None
    report_path: str | None = None


class ProblemAuditOutput(ToolOutputEnvelope):
    data: ProblemAuditData | None = None


class ProblemValidateData(ToolDataBase):
    statement_samples: dict[str, Any] | None = None
    sample_files: dict[str, Any] | None = None


class ProblemValidateOutput(ToolOutputEnvelope):
    data: ProblemValidateData | None = None


class ProblemPackPolygonData(ToolDataBase):
    results: dict[str, Any] | None = None
    exports: dict[str, Any] | None = None
    export_formats: list[str] | None = None
    export_data_mode: str | None = None
    time_limit_ms: int | None = None
    effective_time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    export_verification: dict[str, Any] | None = None
    pack_warnings: list[str] | None = None


class ProblemPackPolygonOutput(ToolOutputEnvelope):
    data: ProblemPackPolygonData | None = None


class ProblemBuildAllData(ToolDataBase):
    compiled: dict[str, str | None] | None = None
    failures: dict[str, str] | None = None
    discovered: list[str] | None = None


class ProblemBuildAllOutput(ToolOutputEnvelope):
    data: ProblemBuildAllData | None = None


TOOL_OUTPUT_MODELS: dict[str, type[ToolOutputEnvelope]] = {
    "file_read": FileReadOutput,
    "file_save": FileSaveOutput,
    "solution_build": SolutionBuildOutput,
    "solution_run": SolutionRunOutput,
    "solution_analyze": SolutionAnalyzeOutput,
    "solution_audit_std": SolutionAuditStdOutput,
    "solution_audit_brute": SolutionAuditBruteOutput,
    "validator_build": ValidatorBuildOutput,
    "validator_select": ValidatorSelectOutput,
    "generator_build": GeneratorBuildOutput,
    "generator_run": GeneratorRunOutput,
    "checker_build": CheckerBuildOutput,
    "interactor_build": InteractorBuildOutput,
    "stress_test_run": StressTestRunOutput,
    "problem_create": ProblemCreateOutput,
    "problem_generate_tests": ProblemGenerateTestsOutput,
    "problem_cleanup_processes": ProblemCleanupProcessesOutput,
    "problem_verify_tests": ProblemVerifyTestsOutput,
    "problem_audit": ProblemAuditOutput,
    "problem_validate": ProblemValidateOutput,
    "problem_pack_polygon": ProblemPackPolygonOutput,
    "problem_build_all": ProblemBuildAllOutput,
}

TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "file_read": ToolAnnotations(
        title="Read Problem File",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "file_save": ToolAnnotations(
        title="Save Problem File",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "solution_build": ToolAnnotations(
        title="Build Solution",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "solution_run": ToolAnnotations(
        title="Run Solution",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "solution_analyze": ToolAnnotations(
        title="Analyze Solution Complexity",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "solution_audit_std": ToolAnnotations(
        title="Audit Reference Solution",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "solution_audit_brute": ToolAnnotations(
        title="Audit Brute Solution",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "validator_build": ToolAnnotations(
        title="Build Validator",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "validator_select": ToolAnnotations(
        title="Select Best Validator",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "generator_build": ToolAnnotations(
        title="Build Generator",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "generator_run": ToolAnnotations(
        title="Run Generator",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "checker_build": ToolAnnotations(
        title="Build Checker",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "interactor_build": ToolAnnotations(
        title="Build Interactor or Communicator",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "stress_test_run": ToolAnnotations(
        title="Run Stress Tests",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "problem_create": ToolAnnotations(
        title="Create Problem Workspace",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
    "problem_generate_tests": ToolAnnotations(
        title="Generate Test Suite",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    ),
    "problem_cleanup_processes": ToolAnnotations(
        title="Cleanup Background Processes",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "problem_verify_tests": ToolAnnotations(
        title="Verify Test Suite",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "problem_audit": ToolAnnotations(
        title="Audit Problem Package",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "problem_validate": ToolAnnotations(
        title="Validate Problem Samples",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "problem_pack_polygon": ToolAnnotations(
        title="Pack Problem Exports",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "problem_build_all": ToolAnnotations(
        title="Build All Problem Sources",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
}

TASK_SUPPORTED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "stress_test_run",
        "problem_generate_tests",
        "problem_audit",
        "problem_verify_tests",
    }
)

TOOL_EXECUTION_MODES: dict[str, ToolExecution] = {
    name: ToolExecution(
        taskSupport="optional" if name in TASK_SUPPORTED_TOOL_NAMES else "forbidden"
    )
    for name in TOOL_OUTPUT_MODELS
}

TOOL_UI_URIS: dict[str, str] = {
    "solution_analyze": "ui://autocode/complexity-plot",
    "solution_audit_std": "ui://autocode/complexity-plot",
    "solution_audit_brute": "ui://autocode/complexity-plot",
    "problem_audit": "ui://autocode/audit-dashboard",
    "problem_verify_tests": "ui://autocode/audit-dashboard",
    "solution_run": "ui://autocode/protocol-replay",
    "interactor_build": "ui://autocode/protocol-replay",
    "problem_validate": "ui://autocode/protocol-replay",
}

_TOOL_OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    name: output_schema_from_model(model) for name, model in TOOL_OUTPUT_MODELS.items()
}


def get_tool_output_schema(tool_name: str) -> dict[str, Any] | None:
    """Return the pre-computed Pydantic-derived outputSchema for ``tool_name``."""
    return _TOOL_OUTPUT_SCHEMAS.get(tool_name)


def get_tool_annotations(tool_name: str) -> ToolAnnotations | None:
    """Return the ``ToolAnnotations`` metadata for ``tool_name``."""
    return TOOL_ANNOTATIONS.get(tool_name)


def get_tool_execution(tool_name: str) -> ToolExecution | None:
    """Return the ``ToolExecution`` task-support metadata for ``tool_name``."""
    return TOOL_EXECUTION_MODES.get(tool_name)


def get_tool_ui_uri(tool_name: str) -> str | None:
    """Return the linked MCP App ``ui://`` URI for ``tool_name``, if any."""
    return TOOL_UI_URIS.get(tool_name)


def get_tool_meta(tool_name: str) -> dict[str, Any] | None:
    """Return MCP ``_meta`` metadata (e.g. ``{"ui": {"resourceUri": ...}}``) for ``tool_name``."""
    uri = TOOL_UI_URIS.get(tool_name)
    if uri is None:
        return None
    return {"ui": {"resourceUri": uri}}
