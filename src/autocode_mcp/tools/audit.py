"""ProblemAuditTool：统一审核题目质量并输出 structured audit_report 与 go/no_go 决策。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from ..runtime_store import (
    AUDIT,
    TEST_MANIFEST,
    WORKFLOW,
    get_section,
    set_section,
    update_section,
)
from ..utils.communication_runner import resolve_participant_binaries, safe_resolve_under_root
from ..utils.exporters import verify_exported_packages
from ..utils.mutation import run_mutation_testing
from ..utils.platform import get_exe_extension
from ..utils.problem_layout import (
    DiscoveredTestPair,
    discover_test_pairs,
    stage_polygon_tests_to_dir,
)
from ..workflow import (
    check_gates,
    current_platform,
    inspect_workspace_state,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_protocol_judge,
    manifest_uses_testlib_checker,
)
from ..workflow.models import AutoCodeManifest
from .audit_constraints import (
    _MAX_EXPR_ABS as _MAX_EXPR_ABS,
)
from .audit_constraints import (
    _MAX_INT_LITERAL_DIGITS as _MAX_INT_LITERAL_DIGITS,
)
from .audit_constraints import (
    _AuditConstraintsMixin,
)
from .audit_constraints import (
    _collect_constraint_entry_ints as _collect_constraint_entry_ints,
)
from .audit_constraints import (
    _extract_statement_numeric_bounds as _extract_statement_numeric_bounds,
)
from .audit_constraints import (
    _extract_validator_bounds as _extract_validator_bounds,
)
from .audit_constraints import (
    _parse_cpp_int_expr as _parse_cpp_int_expr,
)
from .audit_constraints import (
    _rel_posix as _rel_posix,
)
from .audit_constraints import (
    _safe_int_token as _safe_int_token,
)
from .audit_constraints import (
    _split_call_args as _split_call_args,
)
from .audit_constraints import (
    _split_top_level_op as _split_top_level_op,
)
from .audit_constraints import (
    _strip_outer_parens as _strip_outer_parens,
)
from .audit_standalone import _AuditStandaloneMixin
from .base import Tool, ToolResult, input_schema_from_model
from .complexity import (
    extract_claimed_complexity,
    extract_n_max_from_constraints,
    run_empirical_verification,
)
from .schemas import ProblemAuditInput
from .test_verify import ProblemVerifyTestsTool


class ProblemAuditTool(Tool, _AuditStandaloneMixin, _AuditConstraintsMixin):
    """对题目工作区执行结构、质量、难度与风险的全面审核。"""

    @property
    def name(self) -> str:
        return "problem_audit"

    @property
    def description(self) -> str:
        return """生成完整验题报告。

        聚合 manifest.json、workflow state、测试数据 manifest、题面/题解/解法文件、
        problem_verify_tests 质量信号和难度评级 signals。工具只收集确定性证据，
        不调用 LLM。
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemAuditInput)

    async def execute(
        self,
        problem_dir: str,
        mode: Literal["quick", "full"] = "full",
        include_difficulty: bool = True,
        report_path: str | None = None,
        standalone: bool = False,
        *,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
        enable_mutation_testing: bool | None = None,
        strict: bool = False,
    ) -> ToolResult:
        problem_path = Path(problem_dir)
        if not problem_path.is_dir():
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        allowed_layouts = {"auto", "autocode", "standard_cn", "polygon", "hydro"}
        if layout not in allowed_layouts:
            return ToolResult.fail(
                f"invalid layout: {layout!r}; expected one of: {', '.join(sorted(allowed_layouts))}"
            )

        blocking: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        next_actions: list[dict[str, object]] = []

        # Guard .autocode metadata against symlinks escaping problem_dir before reading
        autocode_escaped: list[str] = []
        for meta_rel in (".autocode", ".autocode/manifest.json", ".autocode/runtime.json"):
            raw_meta = problem_path / meta_rel
            if raw_meta.is_symlink() and safe_resolve_under_root(str(problem_path), meta_rel) is None:
                autocode_escaped.append(meta_rel)
        if autocode_escaped:
            if not standalone:
                return ToolResult.fail(
                    f"refusing symlinked metadata escaping problem_dir: {autocode_escaped}"
                )
            blocking.append(
                {
                    "gate": "path_containment",
                    "reason": f".autocode metadata escapes problem_dir via symlink: {autocode_escaped}",
                }
            )

        detected_layout = self._detect_layout(problem_path, layout)
        manifest_inferred = False
        manifest: AutoCodeManifest | None = None
        if not autocode_escaped:
            try:
                manifest = load_manifest(str(problem_path))
            except (ValidationError, OSError, ValueError) as exc:
                return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")
        if manifest is None:
            if standalone:
                manifest = self._infer_standalone_manifest(problem_path, layout=detected_layout)
                manifest_inferred = True
            else:
                return ToolResult.fail("manifest.json not found")

        workflow_state = {} if (standalone or autocode_escaped) else (get_section(problem_path, WORKFLOW) or {})
        tests_manifest = {} if autocode_escaped else (get_section(problem_path, TEST_MANIFEST) or {})

        raw_tests_dir = (
            self._resolve_tests_dir(problem_path, standalone=standalone, layout=detected_layout)
            if not autocode_escaped
            else None
        )
        escaped_tests = (
            self._find_escaped_entries(problem_path, raw_tests_dir)
            if (standalone and raw_tests_dir is not None)
            else []
        )
        discovered_pairs: list[DiscoveredTestPair] = []
        if standalone and raw_tests_dir is not None and not escaped_tests:
            discovered_pairs = discover_test_pairs(
                problem_path,
                tests_dir=raw_tests_dir,
                allow_missing_answer=True,
            )

        structural_signals = self._structural_signals(
            problem_path, manifest, standalone=standalone, layout=detected_layout
        )
        if (
            standalone
            and raw_tests_dir is not None
            and not escaped_tests
            and not structural_signals["tests_present"]["passed"]
            and discovered_pairs
        ):
            structural_signals["tests_present"] = self._signal(
                True,
                {
                    "total": len(discovered_pairs),
                    "tests_dir": str(raw_tests_dir),
                    "format": "polygon_or_custom",
                },
            )
        if manifest_inferred:
            structural_signals["manifest"] = self._signal(
                True,
                {
                    "message": f"inferred in standalone mode (layout={detected_layout}, no .autocode/manifest.json)",
                    "layout": detected_layout,
                },
            )
        optional_inferred_gates = {"tutorial_path", "statement_path", "main_solution_source"}
        for name, signal in structural_signals.items():
            if not signal["passed"]:
                reason = str(signal["evidence"].get("message", ""))
                if manifest_inferred and name in optional_inferred_gates:
                    warnings.append(
                        {"gate": name, "reason": reason or f"{name} missing in standalone package"}
                    )
                else:
                    blocking.append({"gate": name, "reason": reason})

        verify_signals = workflow_state.get("verify_signals", {})
        if not isinstance(verify_signals, dict):
            verify_signals = {}
        quality_signals: dict[str, dict] = dict(verify_signals)

        has_tests_manifest = bool(isinstance(tests_manifest, dict) and tests_manifest.get("tests"))
        max_duplicate_ratio = manifest.audit_gates.max_duplicate_input_ratio

        with contextlib.ExitStack() as stage_stack:
            staged_tests_dir: Path | None = None
            staged_answer_ext: str | None = None
            if (
                standalone
                and raw_tests_dir is not None
                and not escaped_tests
                and discovered_pairs
                and not any(p.is_file() and p.suffix == ".in" for p in raw_tests_dir.iterdir())
            ):
                stage_root = stage_stack.enter_context(
                    tempfile.TemporaryDirectory(
                        prefix="autocode_poly_stage_",
                        ignore_cleanup_errors=True,
                    )
                )
                staged_tests_dir = Path(stage_root) / "tests"
                staged_answer_ext = stage_polygon_tests_to_dir(
                    problem_path,
                    discovered_pairs,
                    staged_tests_dir,
                    source_tests_dir=raw_tests_dir,
                )

            static_quality = self._static_quality_signals(
                problem_path,
                standalone=standalone,
                has_tests_manifest=has_tests_manifest,
                layout=detected_layout,
                max_near_duplicate_ratio=max_duplicate_ratio,
            )
            if staged_tests_dir is not None:
                stage_verifier = ProblemVerifyTestsTool()
                dup_res = stage_verifier._check_duplicate_inputs(
                    str(staged_tests_dir), max_near_ratio=max_duplicate_ratio
                )
                scale_res = stage_verifier._check_scale_distribution(str(staged_tests_dir))
                static_quality["duplicate_inputs"] = {
                    "executed": True,
                    "passed": bool(dup_res.get("passed")),
                    "evidence": dup_res,
                }
                static_quality["scale_distribution"] = {
                    "executed": True,
                    "passed": bool(scale_res.get("passed")),
                    "evidence": scale_res,
                }
            quality_signals.update(static_quality)
            duplicate_signal = static_quality.get("duplicate_inputs", {})
            duplicate_evidence = duplicate_signal.get("evidence", {}) if isinstance(duplicate_signal, dict) else {}
            if mode == "full":
                exact_dups = duplicate_evidence.get("exact_duplicates") or []
                near_ratio = float(
                    duplicate_evidence.get(
                        "near_duplicate_ratio",
                        duplicate_evidence.get("duplicate_ratio", 0.0),
                    )
                )
                if duplicate_signal.get("executed") and (
                    bool(exact_dups)
                    or near_ratio > max_duplicate_ratio
                    or not duplicate_signal.get("passed")
                ):
                    dup_reason = (
                        f"exact duplicate test inputs detected (zero tolerance): {exact_dups[:5]}"
                        if exact_dups
                        else "duplicate input ratio exceeds audit_gates.max_duplicate_input_ratio"
                    )
                    blocking.append({"gate": "duplicate_inputs", "reason": dup_reason})
                for signal_name in ("scale_distribution", "purpose_coverage"):
                    signal = static_quality.get(signal_name, {})
                    if signal.get("executed") and not signal.get("passed"):
                        blocking.append(
                            {
                                "gate": signal_name,
                                "reason": f"quality signal `{signal_name}` not satisfied",
                            }
                        )

            run_mut = (
                enable_mutation_testing
                if enable_mutation_testing is not None
                else bool(manifest.audit_gates.enable_mutation_testing)
            )
            if standalone:
                await self._run_standalone_live_checks(
                    problem_path=problem_path,
                    manifest=manifest,
                    tests_manifest=tests_manifest if isinstance(tests_manifest, dict) else {},
                    mode=mode,
                    layout=detected_layout,
                    quality_signals=quality_signals,
                    blocking=blocking,
                    warnings=warnings,
                    run_mutation=bool(mode == "full" and run_mut),
                    staged_tests_dir=staged_tests_dir,
                    staged_answer_ext=staged_answer_ext,
                )
            elif mode == "full":
                self._require_full_verify_gates(
                    manifest=manifest,
                    workflow_state=workflow_state,
                    quality_signals=quality_signals,
                    blocking=blocking,
                    next_actions=next_actions,
                )
                await self._require_special_artifact_gates(
                    problem_path=problem_path,
                    manifest=manifest,
                    quality_signals=quality_signals,
                    blocking=blocking,
                    next_actions=next_actions,
                )
                empirical_signal = await self._empirical_complexity_signal(problem_path, manifest)
                quality_signals["empirical_complexity"] = empirical_signal
                if empirical_signal.get("executed") and not empirical_signal.get("passed"):
                    evidence = empirical_signal.get("evidence")
                    failure_reason = (
                        str(
                            evidence.get("failure_reason")
                            or evidence.get("message")
                            or "empirical complexity verification failed"
                        )
                        if isinstance(evidence, dict)
                        else "empirical complexity verification failed"
                    )
                    blocking.append(
                        {
                            "gate": "empirical_complexity",
                            "reason": failure_reason,
                        }
                    )
                    next_actions.append(
                        {
                            "tool_name": "solution_analyze",
                            "tool": "solution_analyze",
                            "action": "verify_empirical_complexity",
                            "recommended_arguments": {
                                "problem_dir": str(problem_path),
                                "solution_type": "sol",
                            },
                            "arguments": {
                                "problem_dir": str(problem_path),
                                "solution_type": "sol",
                            },
                            "priority": "high",
                        }
                    )

        statement_consistency = self._statement_consistency(problem_path, manifest, tests_manifest)
        if statement_consistency["needs_human_review"]:
            warnings.append(
                {
                    "gate": "statement_consistency",
                    "reason": str(
                        statement_consistency.get("review_reason")
                        or "natural-language statement/constraint equivalence needs human review"
                    ),
                }
            )
        if manifest.audit_gates.require_statement_consistency and not statement_consistency["passed"]:
            blocking.append(
                {"gate": "statement_consistency", "reason": str(statement_consistency["message"])}
            )
            next_actions.append(
                {
                    "tool_name": "problem_validate",
                    "tool": "problem_validate",
                    "action": "check_statement_consistency",
                    "recommended_arguments": {"problem_dir": str(problem_path)},
                    "arguments": {"problem_dir": str(problem_path)},
                    "priority": "high",
                }
            )
        elif not statement_consistency["passed"]:
            warnings.append(
                {
                    "gate": "statement_consistency",
                    "reason": str(statement_consistency["message"]),
                }
            )

        difficulty_signals = (
            self._difficulty_signals(problem_path, manifest, quality_signals) if include_difficulty else {}
        )
        if include_difficulty and mode == "full":
            raw_conf = difficulty_signals.get("confidence", 0.0)
            confidence = (
                float(raw_conf)
                if isinstance(raw_conf, int | float) and not isinstance(raw_conf, bool)
                else 0.0
            )
            if confidence < manifest.audit_gates.min_difficulty_confidence:
                warnings.append(
                    {
                        "gate": "difficulty_confidence",
                        "reason": "difficulty confidence is below audit_gates.min_difficulty_confidence",
                    }
                )

        communication_metrics = self._communication_metrics(manifest, quality_signals)
        if mode == "full" and communication_metrics.get("budget_exceeded"):
            blocking.append(
                {
                    "gate": "communication_budget",
                    "reason": str(
                        communication_metrics.get(
                            "budget_violation_detail", "communication budget exceeded"
                        )
                    ),
                }
            )

        warnings.extend(self._host_performance_bias_warnings(manifest, quality_signals))

        if not standalone:
            ws_state = inspect_workspace_state(str(problem_path))
            if ws_state.get("platform_mismatch") or ws_state.get("stale_reasons"):
                drift_reason = (
                    "; ".join(ws_state.get("stale_reasons", []))
                    or "workspace build state drift detected"
                )
                if ws_state.get("platform_mismatch") and mode == "full":
                    blocking.append({"gate": "state_drift", "reason": drift_reason})
                else:
                    warnings.append({"gate": "state_drift", "reason": drift_reason})

        # Verify exported packages (Polygon / Hydro / QOJ) if present on disk
        has_exports = (
            (problem_path / "problem.xml").is_file()
            or (problem_path / "exports" / "polygon").exists()
            or (problem_path / "exports" / "polygon.zip").is_file()
            or (problem_path / "exports" / "hydro").exists()
            or (problem_path / "exports" / "qoj").exists()
            or (problem_path / "exports" / "hydro.zip").is_file()
            or (problem_path / "exports" / "qoj.zip").is_file()
        )
        if has_exports:
            tests_dir_for_exp = self._resolve_tests_dir(
                problem_path, standalone=standalone, layout=detected_layout
            )
            if tests_dir_for_exp is not None:
                in_count = len(list(tests_dir_for_exp.glob("*.in")))
                if in_count > 0:
                    exp_test_count = in_count
                elif not self._find_escaped_entries(problem_path, tests_dir_for_exp):
                    exp_test_count = len(
                        discover_test_pairs(
                            problem_path,
                            tests_dir=tests_dir_for_exp,
                            allow_missing_answer=True,
                        )
                    )
                else:
                    exp_test_count = 0
            else:
                exp_test_count = None
            raw_packaged_tl = workflow_state.get("packaged_time_limit_ms")
            packaged_tl = (
                int(raw_packaged_tl)
                if isinstance(raw_packaged_tl, int)
                and not isinstance(raw_packaged_tl, bool)
                and raw_packaged_tl > 0
                else None
            )
            expected_export_tl = packaged_tl if packaged_tl is not None else manifest.time_limit_ms
            if packaged_tl is not None and packaged_tl != manifest.time_limit_ms:
                warnings.append(
                    {
                        "gate": "exported_packages",
                        "reason": (
                            f"exported package uses overridden time_limit_ms={packaged_tl} "
                            f"(manifest.time_limit_ms={manifest.time_limit_ms})"
                        ),
                    }
                )
            pkg_check = verify_exported_packages(
                str(problem_path),
                expected_time_limit_ms=expected_export_tl,
                expected_memory_limit_mb=manifest.memory_limit_mb,
                expected_test_count=exp_test_count or None,
            )
            quality_signals["exported_packages"] = {
                "executed": True,
                "passed": bool(pkg_check.get("passed")),
                "evidence": pkg_check,
            }
            for w_msg in pkg_check.get("warnings", []):
                warnings.append({"gate": "exported_packages", "reason": str(w_msg)})
            if not pkg_check.get("passed"):
                err_reason = "; ".join(str(e) for e in pkg_check.get("errors", [])) or "exported package verification failed"
                if mode == "full":
                    blocking.append({"gate": "exported_packages", "reason": err_reason})
                else:
                    warnings.append({"gate": "exported_packages", "reason": err_reason})

        # Optional single-point mutation testing (NEW-8) for managed mode (standalone runs inside live temp dir)
        if (
            mode == "full"
            and run_mut
            and not standalone
            and not any(b.get("gate") == "path_containment" for b in blocking)
        ):
            tests_dir_mut = self._resolve_tests_dir(
                problem_path, standalone=False, layout=detected_layout
            )
            sol_src_mut = self._find_main_solution_source(
                problem_path, manifest, standalone=False, layout=detected_layout
            )
            if (
                tests_dir_mut is not None
                and sol_src_mut is not None
                and not self._find_escaped_entries(problem_path, tests_dir_mut)
            ):
                mut_verifier = ProblemVerifyTestsTool()
                mut_ans_ext = mut_verifier._resolve_answer_ext(
                    str(tests_dir_mut), None, tests_manifest if isinstance(tests_manifest, dict) else None
                )
                if not (isinstance(tests_manifest, dict) and tests_manifest.get("answer_ext")):
                    if any(tests_dir_mut.glob("*.out")) and not any(tests_dir_mut.glob("*.ans")):
                        mut_ans_ext = ".out"
                mut_res = await run_mutation_testing(
                    str(problem_path),
                    str(tests_dir_mut),
                    manifest=manifest,
                    sol_source=sol_src_mut,
                    answer_ext=mut_ans_ext,
                    budget=manifest.audit_gates.mutation_budget,
                    verify_with_checker=manifest_uses_testlib_checker(manifest),
                )
                quality_signals["mutation_testing"] = {
                    "executed": bool(mut_res.get("executed")),
                    "passed": bool(mut_res.get("passed", True)),
                    "evidence": mut_res,
                }
                if mut_res.get("executed") and mut_res.get("survived_mutants", 0) > 0:
                    surv_ids = [str(s.get("id")) for s in mut_res.get("survivors", [])[:5]]
                    warnings.append(
                        {
                            "gate": "mutation_testing",
                            "reason": (
                                f"{mut_res['survived_mutants']}/{mut_res['compiled_mutants']} single-point "
                                f"mutants survived all tests ({surv_ids}); consider strengthening hack/edge test cases."
                            ),
                        }
                    )
                if mut_res.get("executed") and mut_res.get("judge_error_mutants", 0) > 0:
                    warnings.append(
                        {
                            "gate": "mutation_testing",
                            "reason": (
                                f"{mut_res['judge_error_mutants']} mutant run(s) hit judge FAIL; "
                                "check checker/interactor/communicator stability."
                            ),
                        }
                    )

        completeness_warnings: list[dict[str, str]] = []
        if standalone:
            exe_ext = get_exe_extension()
            uses_protocol = manifest_uses_protocol_judge(manifest)
            validator_needed = (not uses_protocol) or bool(manifest.quality_gates.require_validator_check)
            has_validator = (
                self._find_validator_source(problem_path, standalone=True) is not None
                or self._safe_file(problem_path, f"files/val{exe_ext}") is not None
            )
            if validator_needed and not has_validator:
                completeness_warnings.append(
                    {
                        "gate": "validator_check",
                        "reason": (
                            "standalone package is missing a test validator (val.cpp); "
                            "input constraints are unverified"
                        ),
                    }
                )
            has_wrong_entries = any(s.role == "wrong" for s in manifest.solutions)
            if not has_wrong_entries:
                completeness_warnings.append(
                    {
                        "gate": "wrong_solution_kill",
                        "reason": (
                            "standalone package has no wrong solutions (wrong_*.cpp / wrong-*.cpp / "
                            "problem.xml rejected); test strength against buggy solutions is unverified"
                        ),
                    }
                )
            if completeness_warnings:
                if strict:
                    existing_gates = {b.get("gate") for b in blocking}
                    for cw in completeness_warnings:
                        if cw["gate"] not in existing_gates:
                            blocking.append(dict(cw))
                else:
                    warnings.extend(dict(cw) for cw in completeness_warnings)

        decision = "go" if not blocking else "no_go"
        report: dict[str, Any] = {
            "decision": decision,
            "mode": mode,
            "standalone": standalone,
            "detected_layout": detected_layout,
            "manifest_inferred": manifest_inferred,
            "problem_dir": str(problem_path.resolve()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "blocking_issues": blocking,
            "risk_report": {
                "warnings": warnings,
                "statement_consistency": statement_consistency,
                "package_format": f"Standalone/{detected_layout}" if manifest_inferred else "AutoCode/Polygon",
            },
            "structural_signals": structural_signals,
            "quality_signals": quality_signals,
            "difficulty_signals": difficulty_signals,
            "communication_metrics": communication_metrics,
            "next_actions": next_actions,
        }
        if standalone:
            report["completeness_warnings"] = completeness_warnings
            report["strict"] = strict

        resolved_report_path = self._write_report(problem_path, report, report_path)
        if resolved_report_path:
            report["report_path"] = str(resolved_report_path)
        if mode == "full" and not standalone:
            self._write_audit_state(problem_path, report)

        return ToolResult.ok(**report)

    def _require_full_verify_gates(
        self,
        *,
        manifest: AutoCodeManifest,
        workflow_state: dict,
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        next_actions: list[dict[str, object]],
    ) -> None:
        # 验证信号类质量门禁统一委托 workflow.guard.check_gates 判定，
        # 与 problem_pack_polygon 共享同一真值来源；此处仅补充 next_actions 提示。
        for issue in check_gates(manifest, workflow_state, quality_signals):
            blocking.append({"gate": issue.gate, "reason": issue.reason})
            if issue.gate == "tests_verified":
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": "run_tests_verification",
                        "recommended_arguments": {},
                        "arguments": {},
                        "priority": "high",
                    }
                )
            else:
                verify_type = "validator" if issue.gate == "validator_check" else issue.gate
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": f"verify_{verify_type}",
                        "recommended_arguments": {"verify_types": [verify_type]},
                        "arguments": {"verify_types": [verify_type]},
                        "priority": "high",
                    }
                )

    async def _require_special_artifact_gates(
        self,
        *,
        problem_path: Path,
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        next_actions: list[dict[str, object]],
    ) -> None:
        verifier = ProblemVerifyTestsTool()
        is_interactive = manifest_is_interactive(manifest)
        is_communication = manifest_is_communication(manifest)
        uses_protocol = manifest_uses_protocol_judge(manifest)
        comm_needs_communicator = is_communication and (
            manifest.protocol is None or manifest.protocol.mode != "grader_linked"
        )
        if uses_protocol:
            quality_signals["validator_self_test"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "validator self-test is not applicable for protocol problems"},
            }
        elif manifest.audit_gates.require_validator_self_test:
            signal = quality_signals.get("validator_self_test", {})
            if not self._signal_satisfied(signal):
                tests_dir = problem_path / "tests"
                if tests_dir.is_dir():
                    raw = await verifier._check_validator_self_test(
                        str(problem_path), str(tests_dir), 60
                    )
                else:
                    raw = {"passed": False, "skipped": True, "message": "tests/ directory not found"}
                data = self._wrap_verify_signal(raw)
                quality_signals["validator_self_test"] = data
                if not self._signal_satisfied(data):
                    blocking.append(
                        {"gate": "validator_self_test", "reason": "validator negative fixtures missing or failed"}
                    )
                    next_actions.append(
                        {
                            "tool_name": "problem_verify_tests",
                            "tool": "problem_verify_tests",
                            "action": "verify_validator_self_test",
                            "recommended_arguments": {"verify_types": ["validator_self_test"]},
                            "arguments": {"verify_types": ["validator_self_test"]},
                            "priority": "high",
                        }
                    )

        if not manifest_uses_testlib_checker(manifest):
            quality_signals["checker_self_test"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "checker self-test is not applicable when stress_comparison != checker"},
            }
        elif manifest.audit_gates.require_checker_self_test:
            raw = await verifier._check_checker_self_test(str(problem_path), 60)
            signal = self._wrap_verify_signal(raw)
            quality_signals["checker_self_test"] = signal
            if not self._signal_satisfied(signal):
                blocking.append({"gate": "checker_self_test", "reason": "checker scenarios missing or failed"})
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": "verify_checker_self_test",
                        "recommended_arguments": {"verify_types": ["checker_self_test"]},
                        "arguments": {"verify_types": ["checker_self_test"]},
                        "priority": "high",
                    }
                )

        if not (is_interactive or comm_needs_communicator):
            quality_signals["interactor_self_test"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "interactor self-test is not applicable for non-interactor problems"},
            }
        elif manifest.audit_gates.require_interactor_self_test:
            raw = await verifier._check_interactor_self_test(
                str(problem_path), 60, manifest=manifest
            )
            signal = self._wrap_verify_signal(raw)
            quality_signals["interactor_self_test"] = signal
            if not self._signal_satisfied(signal):
                blocking.append({"gate": "interactor_self_test", "reason": "interactor scenarios missing or failed"})
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": "verify_interactor_self_test",
                        "recommended_arguments": {"verify_types": ["interactor_self_test"]},
                        "arguments": {"verify_types": ["interactor_self_test"]},
                        "priority": "high",
                    }
                )

    async def _empirical_complexity_signal(
        self, problem_path: Path, manifest: AutoCodeManifest
    ) -> dict[str, Any]:
        sol_source = self._solution_source(problem_path, "sol")
        claimed_complexity: str | None = None
        if (
            manifest.empirical_complexity is not None
            and isinstance(manifest.empirical_complexity.claimed_complexity, str)
            and manifest.empirical_complexity.claimed_complexity.strip()
        ):
            claimed_complexity = manifest.empirical_complexity.claimed_complexity.strip()

        workflow_state = get_section(problem_path, WORKFLOW) or {}
        if not claimed_complexity and isinstance(workflow_state, dict):
            saved_claimed = workflow_state.get("std_claimed_complexity")
            if isinstance(saved_claimed, str) and saved_claimed.strip():
                claimed_complexity = saved_claimed.strip()

        sol_bytes: bytes | None = None
        if sol_source and sol_source.is_file():
            try:
                sol_bytes = sol_source.read_bytes()
            except OSError:
                sol_bytes = None
            if not claimed_complexity and sol_bytes is not None:
                code = sol_bytes.decode("utf-8", errors="replace")
                claimed_complexity = extract_claimed_complexity(code)

        constraints: dict[str, Any] = {"time_limit_ms": float(manifest.time_limit_ms)}
        n_max = extract_n_max_from_constraints(manifest.constraints)
        if n_max is not None:
            constraints["n_max"] = n_max

        # Deterministic cache key: (sol/gen/protocol hashes, compiler, empirical_cfg, claimed_complexity, platform)
        cache_key: str | None = None
        gen_path = self._safe_file(problem_path, "files/gen.cpp")
        if sol_bytes is not None and gen_path is not None:
            try:
                gen_bytes = gen_path.read_bytes()
                proto_rels: list[str] = [
                    "files/grader.cpp",
                    "files/problem.h",
                    "files/interactor.cpp",
                    "files/communicator.cpp",
                ]
                if manifest.protocol is not None:
                    for extra_rel in (
                        manifest.protocol.grader_file,
                        manifest.protocol.header_file,
                        manifest.protocol.communicator_file,
                    ):
                        if extra_rel and extra_rel not in proto_rels:
                            proto_rels.append(extra_rel)
                proto_hashes: dict[str, str] = {}
                for rel in proto_rels:
                    pf = self._safe_file(problem_path, rel)
                    if pf is not None:
                        proto_hashes[rel] = hashlib.sha256(pf.read_bytes()).hexdigest()
                cfg_json = json.dumps(
                    {
                        "emp": manifest.empirical_complexity.model_dump(mode="json")
                        if manifest.empirical_complexity
                        else {},
                        "constraints": constraints,
                        "claimed": claimed_complexity,
                        "compiler": manifest.compiler,
                        "protocol_hashes": proto_hashes,
                        "platform": current_platform(),
                    },
                    sort_keys=True,
                )
                cache_key = hashlib.sha256(
                    hashlib.sha256(sol_bytes).hexdigest().encode()
                    + hashlib.sha256(gen_bytes).hexdigest().encode()
                    + cfg_json.encode()
                ).hexdigest()
            except OSError:
                cache_key = None

        exe_ext = get_exe_extension()
        gen_bin_ready = self._safe_file(problem_path, f"files/gen{exe_ext}") is not None
        sol_bin_ready = self._safe_file(problem_path, f"solutions/sol{exe_ext}") is not None
        if (
            not sol_bin_ready
            and manifest_is_communication(manifest)
            and manifest.protocol is not None
            and manifest.protocol.participant_solutions
        ):
            parts = resolve_participant_binaries(str(problem_path), manifest, None, exe_ext)
            sol_bin_ready = bool(parts) and all(Path(p).is_file() for p in parts)

        if cache_key and gen_bin_ready and sol_bin_ready and isinstance(workflow_state, dict):
            emp_cache = workflow_state.get("empirical_cache")
            if (
                isinstance(emp_cache, dict)
                and emp_cache.get("key") == cache_key
                and isinstance(emp_cache.get("signal"), dict)
                and bool(emp_cache["signal"].get("passed"))
            ):
                cached_sig = dict(emp_cache["signal"])
                ev = cached_sig.get("evidence")
                if isinstance(ev, dict):
                    cached_sig["evidence"] = {**ev, "cached": True}
                return cached_sig

        res = await run_empirical_verification(
            str(problem_path),
            "sol",
            claimed_complexity,
            constraints,
        )

        if res.get("status") in ("pending_generator", "pending_interactor", "skipped"):
            return {
                "executed": False,
                "passed": True,
                "evidence": res,
            }

        passed = bool(res.get("passed"))
        signal = {
            "executed": True,
            "passed": passed,
            "evidence": res,
        }
        if cache_key and passed:
            update_section(
                str(problem_path),
                WORKFLOW,
                {"empirical_cache": {"key": cache_key, "signal": signal}},
            )
        return signal

    def _write_report(self, problem_path: Path, report: dict, report_path: str | None) -> Path | None:
        if not report_path:
            return None
        path = Path(report_path)
        if not path.is_absolute():
            path = problem_path / path
        path.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(path)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_audit_state(self, problem_path: Path, report: dict) -> None:
        full_audit = {
            "decision": report["decision"],
            "mode": report["mode"],
            "generated_at": report["generated_at"],
            "report_path": _rel_posix(problem_path, report.get("report_path")),
            "blocking_issue_count": len(report["blocking_issues"]),
            "quality_signals": report["quality_signals"],
        }
        full_audit_passed = report["decision"] == "go" and report["mode"] == "full"
        set_section(str(problem_path), AUDIT, {"full_audit": full_audit, "full_audit_passed": full_audit_passed})



__all__ = ["ProblemAuditTool"]
