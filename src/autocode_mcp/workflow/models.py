from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ProblemType = Literal["standard", "spj", "interactive", "communication"]
OracleMode = Literal["brute", "interactor_only", "reference_solutions"]
ProtocolMode = Literal["interactive_1v1", "run_twice", "multi_process", "grader_linked"]
ExpectedVerdict = Literal["AC", "WA", "TLE", "MLE", "RE", "PE"]
SandboxBackend = Literal["none", "bwrap", "nsjail", "isolate", "apple_container", "docker"]

_COMMUNICATION_MODES = frozenset({"run_twice", "multi_process", "grader_linked"})
_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:")


def _reject_bool_value(value: Any, field_name: str) -> Any:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer, not bool")
    return value


def _reject_bool_list(value: Any, field_name: str) -> Any:
    if isinstance(value, list | tuple):
        for item in value:
            if isinstance(item, bool):
                raise ValueError(f"{field_name} elements must be integers, not bool")
    return value


def _validate_safe_rel_path(value: Any, field_name: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty relative path")
    if "\x00" in value:
        raise ValueError(f"{field_name} contains null byte")
    normalized = value.replace("\\", "/").strip()
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized) or Path(normalized).is_absolute():
        raise ValueError(f"{field_name} must be a relative path inside problem_dir")
    parts = Path(normalized).parts
    if any(part == ".." for part in parts):
        raise ValueError(f"{field_name} must not contain '..' traversal segments")
    return normalized


def _validate_safe_stem(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"{field_name} entries must be non-empty strings")
    cleaned = value.strip()
    if cleaned in {".", ".."} or any(sep in cleaned for sep in ("/", "\\", ":")):
        raise ValueError(
            f"{field_name} entry must be a safe file stem without path separators: {value!r}"
        )
    return cleaned


_GENERATOR_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


class CasePlanItem(BaseModel):
    name: str
    type: str = Field(
        description="生成器类型参数（标准 1=tiny/2=random/3=extreme/4=tle，也支持自定义如 5/6/hack）",
    )
    seed: int = Field(default=1, ge=0)
    group: str = "default"
    purpose: str | None = None
    check_with: list[str] = Field(default_factory=list)

    @field_validator("type", mode="before")
    @classmethod
    def validate_case_type(cls, value: Any) -> str:
        if isinstance(value, int) and not isinstance(value, bool):
            value = str(value)
        if not isinstance(value, str) or not _GENERATOR_TYPE_RE.fullmatch(value.strip()):
            raise ValueError(
                f"case_plan.type must match ^[A-Za-z0-9][A-Za-z0-9_-]{{0,31}}$, got {value!r}"
            )
        return value.strip()


class SandboxConfig(BaseModel):
    """Optional sandbox isolation configuration for solution execution."""

    backend: SandboxBackend = "none"
    extra_args: list[str] = Field(default_factory=list)

    @field_validator("extra_args", mode="before")
    @classmethod
    def validate_extra_args(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("sandbox.extra_args must be a list of strings")
        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("sandbox.extra_args entries must be non-empty strings")
            if any(ch in item for ch in ("\x00", "\n", "\r")):
                raise ValueError("sandbox.extra_args contains invalid control characters")
            cleaned.append(item)
        return cleaned


class SolutionEntry(BaseModel):
    name: str
    role: Literal["main", "brute", "reference", "wrong"]
    language: Literal["cpp", "python"] = "cpp"
    path: str
    expected: Literal["pass", "fail"] | None = None
    expected_verdict: ExpectedVerdict | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_solution_name(cls, value: Any) -> str:
        return _validate_safe_stem(value, "solutions.name")

    @field_validator("path", mode="before")
    @classmethod
    def validate_solution_path(cls, value: Any) -> str:
        res = _validate_safe_rel_path(value, "solutions.path")
        if res is None:
            raise ValueError("solutions.path must be a non-empty relative path")
        return str(res)


def validate_executable_solution_language(entry: SolutionEntry | str) -> Literal["cpp", "python"]:
    """Validate that a solution entry targets a supported executable runtime language."""
    lang = entry.language if isinstance(entry, SolutionEntry) else str(entry).strip().lower()
    if lang == "cpp":
        return "cpp"
    if lang == "python":
        return "python"
    raise ValueError(
        f"Unsupported executable solution language: {lang!r} (supported: 'cpp', 'python')"
    )


class ProtocolConfig(BaseModel):
    """Protocol specification for interactive and communication problems."""

    mode: ProtocolMode = "interactive_1v1"
    adaptive: bool = False
    max_queries: int | None = Field(default=None, ge=1)
    max_bits: int | None = Field(default=None, ge=1)
    max_bytes: int | None = Field(default=None, ge=1)
    max_rounds: int | None = Field(default=None, ge=1)
    passes: int = Field(default=2, ge=1, le=16)
    pass_headers: list[str] = Field(default_factory=lambda: ["first", "second"])
    allow_validator: bool = True
    communicator_file: str | None = None
    header_file: str | None = None
    grader_file: str | None = None
    participant_solutions: list[str] = Field(default_factory=list)

    @field_validator("passes", "max_queries", "max_bits", "max_bytes", "max_rounds", mode="before")
    @classmethod
    def reject_bool_numeric_fields(cls, value: Any, info: Any) -> Any:
        return _reject_bool_value(value, str(info.field_name))

    @field_validator("communicator_file", "header_file", "grader_file", mode="before")
    @classmethod
    def validate_protocol_paths(cls, value: Any, info: Any) -> Any:
        return _validate_safe_rel_path(value, str(info.field_name))

    @field_validator("participant_solutions", mode="before")
    @classmethod
    def validate_participant_solutions(cls, value: Any) -> Any:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("participant_solutions must be a list of solution stems")
        return [_validate_safe_stem(item, "participant_solutions") for item in value]

    @model_validator(mode="before")
    @classmethod
    def normalize_pass_headers(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        mode = data.get("mode", "interactive_1v1")
        passes = data.get("passes", 2)
        if mode == "run_twice" and "pass_headers" not in data and isinstance(passes, int) and not isinstance(passes, bool):
            if passes == 2:
                return data
            if 1 <= passes <= 16:
                return {**data, "pass_headers": [f"pass_{idx}" for idx in range(1, passes + 1)]}
        return data

    @model_validator(mode="after")
    def validate_pass_headers(self) -> ProtocolConfig:
        if self.mode == "run_twice":
            if len(self.pass_headers) < self.passes:
                raise ValueError(
                    f"protocol.pass_headers length ({len(self.pass_headers)}) must be >= passes ({self.passes})"
                )
            if any(not h.strip() for h in self.pass_headers[: self.passes]):
                raise ValueError("protocol.pass_headers entries must be non-empty")
        return self


class SubtaskSpec(BaseModel):
    """Subtask / test group scoring definition for Polygon, Hydro, and QOJ exports."""

    id: int = Field(ge=1)
    score: int = Field(ge=0, le=1000)
    name: str | None = None
    groups: list[str] = Field(default_factory=list)
    test_indices: list[int] = Field(default_factory=list)
    dependencies: list[int] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "score", mode="before")
    @classmethod
    def reject_bool_scalars(cls, value: Any, info: Any) -> Any:
        return _reject_bool_value(value, str(info.field_name))

    @field_validator("test_indices", "dependencies", mode="before")
    @classmethod
    def reject_bool_lists(cls, value: Any, info: Any) -> Any:
        return _reject_bool_list(value, str(info.field_name))

    @model_validator(mode="after")
    def validate_indices(self) -> SubtaskSpec:
        if any(idx < 1 for idx in self.test_indices):
            raise ValueError("subtask.test_indices must be 1-based positive integers")
        return self


class EmpiricalComplexityConfig(BaseModel):
    """Optional empirical complexity sampling and verification settings."""

    claimed_complexity: str | None = Field(
        default=None,
        max_length=128,
        description="可选显式声明时间复杂度（如 O(n log n) / O(2^n) / O(T)），优先级高于代码注释正则",
    )
    dominant_var: str = Field(
        default="n",
        description="主导复杂度的规模变量名（如 n / T / k / m）",
    )
    scale_points: list[int] | None = Field(
        default=None,
        description="显式采样点序列（如 [12, 14, 16, 18, 20] 或 [1000, 5000, 20000, 100000]）",
    )
    generator_args_template: str | None = Field(
        default=None,
        description="自定义 gen 传参模板（如 '{seed} 2 1000000000 1000000000 {T} {T}'）",
    )
    extra_vars: dict[str, Any] = Field(
        default_factory=dict,
        description="传给 MultiScaleSampler.format_generator_command 的额外变量（如 type/t_min/t_max）",
    )
    scale_mode: Literal["auto", "linear", "exponential", "log"] = Field(
        default="auto",
        description="自动采样点分布策略：auto / linear / exponential / log",
    )
    metric: Literal["auto", "cpu_time_ms", "queries"] = Field(
        default="auto",
        description="实证拟合指标：auto（交互题自动优先 queries）/ cpu_time_ms / queries",
    )
    max_value: int | None = Field(
        default=None,
        ge=1,
        description="主导变量的最大值（缺省时自动从 constraints 中提取）",
    )

    @field_validator("dominant_var", mode="before")
    @classmethod
    def validate_dominant_var(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empirical_complexity.dominant_var must be a non-empty string")
        cleaned = value.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", cleaned):
            raise ValueError(
                f"empirical_complexity.dominant_var must be a valid identifier: {value!r}"
            )
        return cleaned

    @field_validator("max_value", mode="before")
    @classmethod
    def reject_bool_max_value(cls, value: Any) -> Any:
        return _reject_bool_value(value, "empirical_complexity.max_value")

    @field_validator("scale_points", mode="before")
    @classmethod
    def validate_scale_points(cls, value: Any) -> Any:
        if value is None:
            return None
        _reject_bool_list(value, "empirical_complexity.scale_points")
        if not isinstance(value, list):
            raise ValueError("empirical_complexity.scale_points must be a list of positive integers")
        out: list[int] = []
        for item in value:
            if not isinstance(item, int) or item < 1:
                raise ValueError("empirical_complexity.scale_points elements must be positive integers")
            out.append(item)
        unique = sorted(set(out))
        if len(unique) < 2:
            raise ValueError(
                "empirical_complexity.scale_points must contain at least 2 distinct positive integers"
            )
        return unique

    @field_validator("generator_args_template", mode="before")
    @classmethod
    def validate_template(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("empirical_complexity.generator_args_template must be a non-empty string")
        if any(ch in value for ch in ("\x00", "\n", "\r")):
            raise ValueError(
                "empirical_complexity.generator_args_template contains invalid control characters"
            )
        cleaned = value.strip()
        # Allow only simple {identifier} placeholders (disallow format specs/attribute/index traversal)
        stripped = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", cleaned)
        if "{" in stripped or "}" in stripped:
            raise ValueError(
                "empirical_complexity.generator_args_template placeholders must be simple {name} identifiers "
                "without format specifiers, attribute access, or unbalanced braces"
            )
        return cleaned


class QualityGates(BaseModel):
    require_stress_passed: bool = True
    require_validation_passed: bool = True
    require_tests_verified: bool = True
    require_limit_semantics: bool = True
    require_wrong_solution_kill: bool = False
    require_validator_check: bool = True
    min_limit_case_ratio: float = Field(default=0.5, ge=0, le=1)
    wrong_tle_kill_factor: float = Field(
        default=1.0,
        gt=0.0,
        le=5.0,
        allow_inf_nan=False,
        description="wrong_solution_kill 逻辑 TLE 判定阈值（相对 time_limit_ms 倍数，默认 1.0）",
    )
    wrong_slowdown_kill_ratio: float | None = Field(
        default=None,
        gt=1.0,
        allow_inf_nan=False,
        description="可选：当错解相对标程耗时倍率达到该值（且绝对耗时 >= 50ms）时按相对耗时判定击杀",
    )

    @field_validator("wrong_tle_kill_factor", "wrong_slowdown_kill_ratio", mode="before")
    @classmethod
    def reject_bool_tle_fields(cls, value: Any, info: Any) -> Any:
        return _reject_bool_value(value, str(info.field_name))


class AuditGates(BaseModel):
    require_full_audit: bool = False
    require_statement_consistency: bool = True
    require_validator_self_test: bool = False
    require_checker_self_test: bool = True
    require_interactor_self_test: bool = True
    max_duplicate_input_ratio: float = Field(default=0.2, ge=0, le=1)
    min_difficulty_confidence: float = Field(default=0.5, ge=0, le=1)
    enable_mutation_testing: bool = False
    mutation_budget: int = Field(default=6, ge=1, le=12)
    fail_on_mutation_survival: bool = False
    min_branch_coverage: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )

    @field_validator("mutation_budget", mode="before")
    @classmethod
    def reject_bool_mutation_budget(cls, value: Any) -> Any:
        return _reject_bool_value(value, "audit_gates.mutation_budget")

    @field_validator("min_branch_coverage", mode="before")
    @classmethod
    def reject_bool_min_branch_coverage(cls, value: Any) -> Any:
        return _reject_bool_value(value, "audit_gates.min_branch_coverage")


class DifficultyProfile(BaseModel):
    rating: int | None = Field(default=None, ge=800, le=3500)
    band: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    algorithm_tags: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AutoCodeManifest(BaseModel):
    schema_version: str = "1.0"
    problem_name: str
    problem_type: ProblemType = "standard"
    oracle_mode: OracleMode = "brute"
    protocol: ProtocolConfig | None = None
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
    multi_test: bool = False
    scale_token_index: int | None = Field(default=None, ge=0)
    sandbox: SandboxConfig | None = None
    time_limit_multipliers: dict[str, float] | None = None
    compiler: str | None = Field(
        default=None,
        description="可选自定义 C++ 编译器路径或命令（如 g++-14 / /opt/homebrew/bin/g++-14）；缺省为 g++",
    )
    statement_path: str = "statements/README.md"
    tutorial_path: str = "statements/tutorial.md"
    constraints: dict[str, Any] = Field(default_factory=dict)
    empirical_complexity: EmpiricalComplexityConfig | None = None
    solutions: list[SolutionEntry] = Field(default_factory=list)
    case_plan: list[CasePlanItem] = Field(default_factory=list)
    subtasks: list[SubtaskSpec] = Field(default_factory=list)
    quality_gates: QualityGates = Field(default_factory=QualityGates)
    audit_gates: AuditGates = Field(default_factory=AuditGates)
    difficulty: DifficultyProfile = Field(default_factory=DifficultyProfile)

    @field_validator("scale_token_index", mode="before")
    @classmethod
    def reject_bool_scale_token_index(cls, value: Any) -> Any:
        return _reject_bool_value(value, "scale_token_index")

    @field_validator("time_limit_multipliers", mode="before")
    @classmethod
    def validate_time_limit_multipliers(cls, value: Any) -> dict[str, float] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("time_limit_multipliers must be a mapping of language/key to positive float")
        out: dict[str, float] = {}
        for raw_key, raw_val in value.items():
            if not isinstance(raw_key, str) or not raw_key.strip():
                raise ValueError("time_limit_multipliers keys must be non-empty strings")
            if any(ch in raw_key for ch in ("\x00", "\n", "\r")):
                raise ValueError("time_limit_multipliers key contains invalid control characters")
            if isinstance(raw_val, bool) or not isinstance(raw_val, int | float):
                raise ValueError(
                    f"time_limit_multipliers[{raw_key!r}] must be a positive number, got {raw_val!r}"
                )
            mult = float(raw_val)
            if not math.isfinite(mult) or mult <= 0.0:
                raise ValueError(
                    f"time_limit_multipliers[{raw_key!r}] must be a finite positive number, got {raw_val!r}"
                )
            out[raw_key.strip()] = mult
        return out

    @field_validator("compiler", mode="before")
    @classmethod
    def validate_compiler(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("compiler must be a string")
        if not value.strip():
            return None
        from ..utils.compiler import validate_compiler_spec

        return validate_compiler_spec(value)

    @field_validator("statement_path", "tutorial_path", mode="before")
    @classmethod
    def validate_doc_paths(cls, value: Any, info: Any) -> str:
        res = _validate_safe_rel_path(value, str(info.field_name))
        if res is None:
            raise ValueError(f"{info.field_name} must be a non-empty relative path")
        return str(res)

    @model_validator(mode="before")
    @classmethod
    def normalize_problem_type_and_checker_flags(cls, data: Any) -> Any:
        """统一归一化 problem_type / interactive / special_judge / protocol 与 checker 双向对拍标志。"""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        explicit_type = out.get("problem_type")

        interactive_explicit = "interactive" in out
        spj_explicit = "special_judge" in out
        raw_interactive = out.get("interactive", False)
        raw_spj = out.get("special_judge", False)
        if interactive_explicit and not isinstance(raw_interactive, bool):
            raise ValueError("interactive must be a boolean")
        if spj_explicit and not isinstance(raw_spj, bool):
            raise ValueError("special_judge must be a boolean")

        interactive_flag = raw_interactive if interactive_explicit else False
        spj_flag = raw_spj if spj_explicit else False

        if explicit_type is None:
            if interactive_flag and spj_flag:
                raise ValueError("conflicting interactive=True and special_judge=True")
            if interactive_flag:
                out["problem_type"] = "interactive"
            elif spj_flag:
                out["problem_type"] = "spj"
            else:
                out["problem_type"] = "standard"
        else:
            if explicit_type == "interactive":
                if interactive_explicit and not interactive_flag:
                    raise ValueError("conflicting problem_type='interactive' and interactive=False")
                if spj_explicit and spj_flag:
                    raise ValueError("conflicting problem_type='interactive' and special_judge=True")
                out["interactive"] = True
                out["special_judge"] = False
            elif explicit_type == "communication":
                if interactive_explicit and interactive_flag:
                    raise ValueError("conflicting problem_type='communication' and interactive=True")
                if spj_explicit and spj_flag:
                    raise ValueError("conflicting problem_type='communication' and special_judge=True")
                out["interactive"] = False
                out["special_judge"] = False
            elif explicit_type == "spj":
                if interactive_explicit and interactive_flag:
                    raise ValueError("conflicting problem_type='spj' and interactive=True")
                if spj_explicit and not spj_flag:
                    raise ValueError("conflicting problem_type='spj' and special_judge=False")
                out["interactive"] = False
                out["special_judge"] = True
            elif explicit_type == "standard":
                if interactive_explicit and interactive_flag:
                    raise ValueError("conflicting problem_type='standard' and interactive=True")
                if spj_explicit and spj_flag:
                    raise ValueError("conflicting problem_type='standard' and special_judge=True")
                out["interactive"] = False
                out["special_judge"] = False

        ptype = out.get("problem_type")
        if ptype == "interactive" and out.get("protocol") is None:
            out["protocol"] = {"mode": "interactive_1v1"}
        elif ptype == "communication" and out.get("protocol") is None:
            out["protocol"] = {"mode": "run_twice"}

        if out.get("stress_checker_bidirectional") and not (
            bool(out.get("special_judge")) and out.get("stress_comparison") == "checker"
        ):
            out["stress_checker_bidirectional"] = False

        return out

    @model_validator(mode="after")
    def validate_protocol_and_subtasks(self) -> AutoCodeManifest:
        if self.problem_type in {"standard", "spj"}:
            if self.protocol is not None:
                raise ValueError(
                    f"protocol is only allowed for interactive or communication problems, not {self.problem_type!r}"
                )
            if self.oracle_mode == "interactor_only":
                raise ValueError(
                    f"oracle_mode='interactor_only' requires interactive or communication problem, not {self.problem_type!r}"
                )
        elif self.problem_type == "interactive":
            if self.protocol is not None and self.protocol.mode != "interactive_1v1":
                raise ValueError(
                    f"interactive problem requires protocol.mode='interactive_1v1', got {self.protocol.mode!r}"
                )
        elif self.problem_type == "communication":
            if self.protocol is None or self.protocol.mode not in _COMMUNICATION_MODES:
                actual = self.protocol.mode if self.protocol else None
                raise ValueError(
                    f"communication problem requires protocol.mode in {sorted(_COMMUNICATION_MODES)}, got {actual!r}"
                )

        dep_graph: dict[int, list[int]] = {}
        for sub in self.subtasks:
            if sub.id in dep_graph:
                raise ValueError(f"duplicate subtask id: {sub.id}")
            dep_graph[sub.id] = list(sub.dependencies)
        for sub_id, deps in dep_graph.items():
            for dep in deps:
                if dep == sub_id:
                    raise ValueError(f"subtask {sub_id} cannot depend on itself")
                if dep not in dep_graph:
                    raise ValueError(f"subtask {sub_id} depends on unknown subtask id {dep}")

        # 3-color DFS cycle detection (0=unvisited, 1=visiting, 2=visited)
        color: dict[int, int] = dict.fromkeys(dep_graph, 0)

        def _dfs(node: int) -> None:
            color[node] = 1
            for nxt in dep_graph[node]:
                if color[nxt] == 1:
                    raise ValueError(f"cyclic subtask dependency involving subtask {nxt}")
                if color[nxt] == 0:
                    _dfs(nxt)
            color[node] = 2

        for sub_id in dep_graph:
            if color[sub_id] == 0:
                _dfs(sub_id)

        return self
