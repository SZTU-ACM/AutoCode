from __future__ import annotations

import asyncio
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from ..utils.communication_runner import safe_resolve_under_root
from ..utils.execution_monitor import DynamicExecutionMonitor
from ..utils.platform import get_exe_extension
from ..utils.ratio_analyzer import normalize_complexity_expression
from ..utils.scale_sampler import MultiScaleSampler
from ..workflow import (
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
)
from .base import Tool, ToolResult, input_schema_from_model
from .complexity import (
    _COMPLEXITY_ORDER,
    ComplexityLevel,
    detect_complexity_conflicts,
    estimate_memory_usage,
    estimate_static_complexity,
    extract_claimed_complexity,
    extract_n_max_from_constraints,
    has_effective_nested_loops,
    run_empirical_verification,
)
from .mixins import resolve_source
from .schemas import SolutionAuditBruteInput, SolutionAuditStdInput


async def _probe_brute_small_scale(
    problem_dir: str,
    recommended_n_max: int,
    *,
    manifest: Any = None,
) -> dict[str, Any]:
    """Run a stepped small-scale feasibility probe on compiled brute binary.

    - Uses ``RLIMIT_CORE=0`` via ``DynamicExecutionMonitor.run_monitored_process``.
    - Stops safely on timeout (circuit breaker) and recommends clamping ``n_max`` down.
    - Hard-fails (``passed=False, crashed=True``) on non-zero exit / signal crash / MLE.
    """
    if manifest is not None and (manifest_is_interactive(manifest) or manifest_is_communication(manifest)):
        return {"status": "skipped", "passed": True, "reason": "protocol problem brute probe skipped"}

    exe_ext = get_exe_extension()
    gen_rel = f"files/gen{exe_ext}"
    brute_rel = f"solutions/brute{exe_ext}"
    raw_gen = Path(problem_dir) / gen_rel
    raw_brute = Path(problem_dir) / brute_rel
    if raw_gen.is_symlink() or raw_brute.is_symlink():
        return {"status": "skipped", "passed": True, "reason": "refusing symlinked probe binaries"}
    gen_path = safe_resolve_under_root(problem_dir, gen_rel)
    brute_path = safe_resolve_under_root(problem_dir, brute_rel)
    if not gen_path or not os.path.isfile(gen_path) or not brute_path or not os.path.isfile(brute_path):
        return {"status": "pending_binaries", "passed": True, "reason": "gen or brute binary not built yet"}

    cap_n = max(1, min(recommended_n_max, 12))
    n_min = 1
    if manifest is not None and isinstance(getattr(manifest, "constraints", None), dict):
        c_dict = manifest.constraints
        raw_n_min: Any = None
        if isinstance(c_dict.get("n"), dict):
            raw_n_min = c_dict["n"].get("min")
        if raw_n_min is None:
            raw_n_min = c_dict.get("n_min")
        if isinstance(raw_n_min, int) and not isinstance(raw_n_min, bool) and raw_n_min >= 1:
            n_min = min(raw_n_min, cap_n)

    if cap_n <= n_min:
        steps = [cap_n]
    else:
        span = cap_n - n_min
        if span <= 4:
            steps = list(range(n_min, cap_n + 1))
        else:
            steps = sorted({n_min + round(span * i / 4) for i in range(5)})

    emp_cfg = manifest.empirical_complexity if manifest is not None else None
    dominant_var = emp_cfg.dominant_var if emp_cfg is not None else "n"
    args_template = emp_cfg.generator_args_template if emp_cfg is not None else None
    extra_vars = dict(emp_cfg.extra_vars) if emp_cfg is not None else {}
    mem_mb = (
        min(512.0, float(manifest.memory_limit_mb))
        if (manifest is not None and getattr(manifest, "memory_limit_mb", None))
        else 256.0
    )

    samples: list[dict[str, Any]] = []
    last_ok_n: int | None = None
    clamped_n_max: int | None = None

    with tempfile.TemporaryDirectory(
        prefix="autocode_brute_probe_", ignore_cleanup_errors=True
    ) as tmp_dir:
        for idx, pt in enumerate(steps):
            in_file = os.path.join(tmp_dir, f"probe_{pt}.in")
            try:
                gen_cmd = MultiScaleSampler.format_generator_command(
                    gen_path,
                    pt,
                    100 + idx,
                    template=args_template,
                    extra_vars=extra_vars or None,
                    dominant_var=dominant_var,
                )
                await asyncio.to_thread(
                    MultiScaleSampler.generate_scale_input_file,
                    gen_cmd,
                    in_file,
                    3.0,
                )
            except Exception as exc:
                if pt == 1 and not samples and idx + 1 < len(steps):
                    continue
                return {
                    "status": "generator_error",
                    "passed": True,
                    "samples": samples,
                    "message": f"Brute probe generator failed at n={pt}: {exc}",
                }

            res = await DynamicExecutionMonitor.run_monitored_process(
                [brute_path],
                in_file,
                time_limit_ms=1000.0,
                memory_limit_mb=mem_mb,
                cwd=tmp_dir,
            )
            if (
                idx == 0
                and sys.platform in ("darwin", "win32")
                and str(res.get("status", "")) == "timeout"
            ):
                # Absorb one-time macOS Gatekeeper / Windows Defender CDHash scan on freshly compiled binary
                res = await DynamicExecutionMonitor.run_monitored_process(
                    [brute_path],
                    in_file,
                    time_limit_ms=1000.0,
                    memory_limit_mb=mem_mb,
                    cwd=tmp_dir,
                )
            status = str(res.get("status", "runtime_error"))
            rc = res.get("returncode")
            obs_mem = float(res.get("memory_mb") or 0.0)
            if obs_mem > mem_mb:
                status = "mle"
            sample = {
                "n": pt,
                "status": status,
                "returncode": rc,
                "wall_time_ms": res.get("wall_time_ms"),
                "memory_mb": res.get("memory_mb"),
            }
            samples.append(sample)

            if status in ("runtime_error", "mle") or (status != "timeout" and rc is not None and rc != 0):
                return {
                    "status": "crashed",
                    "passed": False,
                    "crashed": True,
                    "samples": samples,
                    "failed_n": pt,
                    "failure_reason": (
                        f"Brute process failed at n={pt} (status={status}, returncode={rc}, "
                        f"memory_mb={res.get('memory_mb')})"
                    ),
                }
            if status == "timeout":
                if last_ok_n is None:
                    return {
                        "status": "timeout_at_min_scale",
                        "passed": False,
                        "crashed": False,
                        "samples": samples,
                        "failed_n": pt,
                        "failure_reason": (
                            f"Brute timed out even at minimum probed scale n={pt} "
                            "(possible infinite loop or unusable brute solution)."
                        ),
                    }
                clamped_n_max = max(n_min, last_ok_n)
                return {
                    "status": "timeout_circuit_breaker",
                    "passed": True,
                    "crashed": False,
                    "samples": samples,
                    "recommended_n_max_clamp": clamped_n_max,
                    "message": f"Brute timed out at n={pt}; stopped scaling up and clamped recommended n_max to {clamped_n_max}.",
                }

            last_ok_n = pt
            # Skip early-stop on idx == 0 to avoid macOS Gatekeeper / Windows Defender cold-start wall latency
            if idx >= 1 and float(res.get("wall_time_ms") or 0.0) >= 400.0:
                clamped_n_max = pt
                break

    return {
        "status": "ok",
        "passed": True,
        "crashed": False,
        "samples": samples,
        **({"recommended_n_max_clamp": clamped_n_max} if clamped_n_max is not None else {}),
    }


class SolutionAuditStdTool(Tool):
    @property
    def name(self) -> str:
        return "solution_audit_std"

    @property
    def description(self) -> str:
        return "审核标准解与题目约束/复杂度是否匹配，输出结构化风险与证据。"

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(SolutionAuditStdInput)

    async def execute(
        self,
        code: str | None = None,
        source_path: str | None = None,
        problem_dir: str | None = None,
        constraints: dict | None = None,
        claimed_complexity: str | None = None,
    ) -> ToolResult:
        if code is None and source_path is None and not problem_dir:
            return ToolResult.fail("Either 'code', 'source_path', or 'problem_dir' must be provided")

        resolved, err = resolve_source(
            problem_dir or ".",
            code,
            source_path,
            default_source_path=os.path.join("solutions", "sol.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None
        code = resolved.code

        m_loaded = None
        if problem_dir:
            try:
                m_loaded = load_manifest(problem_dir)
            except (OSError, ValueError):
                m_loaded = None
        if not claimed_complexity and m_loaded is not None:
            if (
                m_loaded.empirical_complexity is not None
                and isinstance(m_loaded.empirical_complexity.claimed_complexity, str)
                and m_loaded.empirical_complexity.claimed_complexity.strip()
            ):
                claimed_complexity = m_loaded.empirical_complexity.claimed_complexity.strip()
        if not claimed_complexity:
            extracted = extract_claimed_complexity(code)
            if extracted:
                claimed_complexity = extracted
        if constraints is None and m_loaded is not None and m_loaded.constraints:
            constraints = dict(m_loaded.constraints)

        raw_claimed = claimed_complexity
        if claimed_complexity:
            claimed_complexity = normalize_complexity_expression(claimed_complexity)

        estimated, patterns = estimate_static_complexity(code)
        space_complexity, estimated_memory_mb = estimate_memory_usage(code)
        nested_loops = has_effective_nested_loops(code)
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

        n_max = extract_n_max_from_constraints(constraints)
        raw_tl = constraints.get("time_limit_ms") if isinstance(constraints, dict) else None
        if (raw_tl is None or isinstance(raw_tl, bool)) and m_loaded is not None:
            raw_tl = m_loaded.time_limit_ms
        tl_ms = (
            float(raw_tl)
            if (
                isinstance(raw_tl, int | float)
                and not isinstance(raw_tl, bool)
                and math.isfinite(float(raw_tl))
                and float(raw_tl) > 0
            )
            else 1000.0
        )
        tl_scale = max(1.0, tl_ms / 1000.0)
        is_static_infeasible = False
        if n_max is not None:
            if estimated == ComplexityLevel.QUADRATIC:
                is_static_infeasible = n_max >= int(50_000 * math.sqrt(tl_scale))
            elif estimated == ComplexityLevel.CUBIC:
                is_static_infeasible = n_max >= int(2_000 * (tl_scale ** (1.0 / 3.0)))
            elif estimated == ComplexityLevel.EXPONENTIAL:
                is_static_infeasible = n_max >= 35
            elif estimated == ComplexityLevel.FACTORIAL:
                is_static_infeasible = n_max >= 15

        complexity_feasible = not is_static_infeasible
        is_optimistic_lie = (
            bool(claimed_complexity)
            and claimed_complexity != estimated
            and claimed_complexity in _COMPLEXITY_ORDER
            and estimated in _COMPLEXITY_ORDER
            and _COMPLEXITY_ORDER.index(claimed_complexity) < _COMPLEXITY_ORDER.index(estimated)
        )
        if not complexity_feasible:
            if is_optimistic_lie:
                passed = False
                findings.append(
                    {
                        "severity": "error",
                        "type": "high_tle_risk",
                        "message": (
                            f"static complexity {estimated} is infeasible for n_max={n_max} under "
                            f"{tl_ms:.0f}ms while claimed={claimed_complexity}."
                        ),
                    }
                )
            else:
                findings.append(
                    {
                        "severity": "warning",
                        "type": "high_tle_risk",
                        "message": "n_max 较大，静态分析估算的标准解复杂度过高，存在 TLE 风险，建议参考。",
                    }
                )

        empirical_verification: dict = {"status": "skipped"}
        if problem_dir:
            empirical_verification = await run_empirical_verification(
                problem_dir,
                "sol",
                raw_claimed,
                constraints,
            )
            if empirical_verification.get("status") == "pending_generator":
                pass
            elif empirical_verification.get("status") == "generator_error":
                findings.append(
                    {
                        "severity": "warning",
                        "type": "generator_error",
                        "message": str(empirical_verification.get("message", "生成器运行异常")),
                    }
                )
            elif not empirical_verification.get("passed", True):
                passed = False
                findings.append(
                    {
                        "severity": "error",
                        "type": "empirical_ratio_mismatch",
                        "message": str(empirical_verification.get("failure_reason", "经验复杂度拟合未通过")),
                    }
                )

        has_conflict, conflict_details = detect_complexity_conflicts(
            claimed=claimed_complexity,
            static_estimated=estimated,
            empirical=empirical_verification,
        )
        if has_conflict:
            findings.append(
                {
                    "severity": "warning",
                    "type": "complexity_conflict",
                    "message": "; ".join(conflict_details),
                }
            )

        return ToolResult.ok(
            passed=passed,
            estimated_complexity=estimated,
            static_estimated_complexity=estimated,
            claimed_complexity=claimed_complexity,
            space_complexity=space_complexity,
            estimated_memory_mb=estimated_memory_mb,
            detected_patterns=patterns,
            complexity_conflict=has_conflict,
            complexity_match=not has_conflict,
            complexity_feasible=complexity_feasible,
            conflict_details=conflict_details,
            findings=findings,
            evidence={
                "has_sort": "sorting" in patterns,
                "has_nested_loops": nested_loops,
            },
            empirical_verification=empirical_verification,
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
        return input_schema_from_model(SolutionAuditBruteInput)

    async def execute(
        self,
        code: str | None = None,
        source_path: str | None = None,
        problem_dir: str | None = None,
        std_complexity: str | None = None,
        constraints: dict | None = None,
    ) -> ToolResult:
        if code is None and source_path is None and not problem_dir:
            return ToolResult.fail("Either 'code', 'source_path', or 'problem_dir' must be provided")

        resolved, err = resolve_source(
            problem_dir or ".",
            code,
            source_path,
            default_source_path=os.path.join("solutions", "brute.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None
        code = resolved.code

        if std_complexity:
            std_complexity = normalize_complexity_expression(std_complexity)

        m_loaded = None
        if problem_dir:
            try:
                m_loaded = load_manifest(problem_dir)
            except (OSError, ValueError):
                m_loaded = None

        brute_complexity, brute_patterns = estimate_static_complexity(code)
        brute_has_nested = has_effective_nested_loops(code)
        findings: list[dict] = []
        passed = True

        if std_complexity and brute_complexity == std_complexity:
            findings.append(
                {
                    "severity": "warning",
                    "type": "same_order_as_std",
                    "message": "暴力解复杂度与 std 同阶，可能无法形成有效对拍压力。",
                }
            )
        recommended_n_max = 150
        if brute_complexity == ComplexityLevel.FACTORIAL:
            recommended_n_max = 8
        elif brute_complexity == ComplexityLevel.EXPONENTIAL:
            has_poly_factor = (
                "dynamic_programming" in brute_patterns
                or "backtracking" in brute_patterns
                or brute_has_nested
            )
            recommended_n_max = 10 if has_poly_factor else 14
        elif brute_complexity == ComplexityLevel.CUBIC:
            recommended_n_max = 40
        elif brute_complexity == ComplexityLevel.QUADRATIC:
            recommended_n_max = 80
        problem_n_max = extract_n_max_from_constraints(constraints)
        if problem_n_max is None and m_loaded is not None and m_loaded.constraints:
            problem_n_max = extract_n_max_from_constraints(m_loaded.constraints)
        if problem_n_max is not None:
            recommended_n_max = max(1, min(recommended_n_max, problem_n_max))

        brute_probe: dict[str, Any] = {"status": "skipped", "passed": True}
        if problem_dir:
            brute_probe = await _probe_brute_small_scale(
                problem_dir,
                recommended_n_max,
                manifest=m_loaded,
            )
            if not brute_probe.get("passed", True):
                passed = False
                findings.append(
                    {
                        "severity": "error",
                        "type": "brute_runtime_crash",
                        "message": str(
                            brute_probe.get("failure_reason") or "Brute solution crashed during small-scale probe"
                        ),
                    }
                )
            elif isinstance(brute_probe.get("recommended_n_max_clamp"), int):
                recommended_n_max = max(1, min(recommended_n_max, int(brute_probe["recommended_n_max_clamp"])))

        recommended_trials = 1200 if recommended_n_max >= 100 else 600

        empirical_verification: dict = {"status": "skipped"}
        if problem_dir:
            empirical_verification = await run_empirical_verification(
                problem_dir,
                "brute",
                brute_complexity,
                constraints,
            )
            if empirical_verification.get("passed") is False:
                passed = False
                findings.append(
                    {
                        "severity": "error",
                        "type": "brute_empirical_error",
                        "message": str(
                            empirical_verification.get("failure_reason")
                            or empirical_verification.get("message")
                            or "Brute empirical check failed"
                        ),
                    }
                )

        return ToolResult.ok(
            passed=passed,
            brute_complexity=brute_complexity,
            static_estimated_complexity=brute_complexity,
            detected_patterns=brute_patterns,
            findings=findings,
            constraints=constraints or {},
            recommended_stress_params={
                "trials": recommended_trials,
                "n_max": recommended_n_max,
                "types": ["1", "2", "3"],
                "timeout": 30,
            },
            brute_probe=brute_probe,
            empirical_verification=empirical_verification,
        )
