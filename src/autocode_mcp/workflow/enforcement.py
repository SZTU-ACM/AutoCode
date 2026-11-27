"""Host-independent workflow enforcement for AutoCode MCP calls.

Claude Code hooks can provide an early, user-friendly denial, but they are not
available to every MCP host.  The MCP server therefore owns the authoritative
preflight checks and workflow state transitions in this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..runtime_store import AUDIT, set_section
from .gates import _DOWNSTREAM_INVALIDATING_TOOLS as _DOWNSTREAM_INVALIDATING_TOOLS
from .gates import _NON_BRUTE_ORACLE_MODES as _NON_BRUTE_ORACLE_MODES
from .gates import NEXT_ACTIONS as NEXT_ACTIONS
from .gates import PRE_GATES as PRE_GATES
from .gates import Gate as Gate
from .gates import GateViolation as GateViolation
from .gates import _apply_effective_quality_gates as _apply_effective_quality_gates
from .gates import _audit_gate_ok as _audit_gate_ok
from .gates import _brute_built_ok as _brute_built_ok
from .gates import _brute_required as _brute_required
from .gates import _checker_build_prereq_ok as _checker_build_prereq_ok
from .gates import _finite_ratio as _finite_ratio
from .gates import _has_generated_tests as _has_generated_tests
from .gates import _interaction_scenarios_ready as _interaction_scenarios_ready
from .gates import _interactor_build_allowed as _interactor_build_allowed
from .gates import _interactor_gate_ok as _interactor_gate_ok
from .gates import _invalidate_for_file_save as _invalidate_for_file_save
from .gates import _is_interactive as _is_interactive
from .gates import _is_non_interactive as _is_non_interactive
from .gates import _is_protocol_problem as _is_protocol_problem
from .gates import _limit_ratio_threshold as _limit_ratio_threshold
from .gates import _limit_semantics_gate_ok as _limit_semantics_gate_ok
from .gates import _manifest_checker as _manifest_checker
from .gates import _min_limit_ratio_gate_ok as _min_limit_ratio_gate_ok
from .gates import _quality_gate_enabled as _quality_gate_enabled
from .gates import _required_verify_signal_ok as _required_verify_signal_ok
from .gates import _requires_external_interactor as _requires_external_interactor
from .gates import _state_problem_type as _state_problem_type
from .gates import _stress_prereq_core_ok as _stress_prereq_core_ok
from .gates import _stress_required_gate_ok as _stress_required_gate_ok
from .gates import _stress_rounds_complete as _stress_rounds_complete
from .gates import _stress_spj_checker_ready_ok as _stress_spj_checker_ready_ok
from .gates import _sync_dynamic_probe_hashes as _sync_dynamic_probe_hashes
from .gates import _tests_verified_required_gate_ok as _tests_verified_required_gate_ok
from .gates import _validation_required_gate_ok as _validation_required_gate_ok
from .gates import _validator_check_gate_ok as _validator_check_gate_ok
from .gates import _validator_gate_ok as _validator_gate_ok
from .gates import _violation as _violation
from .gates import _wrong_solution_kill_gate_ok as _wrong_solution_kill_gate_ok
from .gates import preflight as preflight
from .gates import prepare_call as prepare_call
from .hashes import DEFAULT_QUALITY_GATES as DEFAULT_QUALITY_GATES
from .hashes import _canonical_comm_rel as _canonical_comm_rel
from .hashes import _file_sha256 as _file_sha256
from .hashes import _grader_deps_unchanged as _grader_deps_unchanged
from .hashes import _invalidate_downstream as _invalidate_downstream
from .hashes import _invalidate_downstream_in_memory as _invalidate_downstream_in_memory
from .hashes import _invalidate_verify_only as _invalidate_verify_only
from .hashes import _is_pure_recompile as _is_pure_recompile
from .hashes import _matches_canonical_source as _matches_canonical_source
from .hashes import _record_grader_dep_hashes as _record_grader_dep_hashes
from .hashes import _saved_relative_path as _saved_relative_path
from .hashes import _set_source_hash as _set_source_hash
from .hashes import _set_target_platform as _set_target_platform
from .hashes import _sync_solution_hashes as _sync_solution_hashes
from .hashes import _update_build_platform as _update_build_platform
from .hashes import current_platform as current_platform
from .hashes import current_statement_sha256 as current_statement_sha256
from .hashes import resolve_statement_rel as resolve_statement_rel
from .manifest import load_manifest, manifest_path
from .reconcile import inspect_workspace_state as inspect_workspace_state
from .reconcile import reconcile_workspace_state as reconcile_workspace_state
from .state_io import _default_state as _default_state
from .state_io import has_workflow_context as has_workflow_context
from .state_io import load_workflow_state as load_workflow_state
from .state_io import save_workflow_state as save_workflow_state


def _append_history(state: dict[str, Any], tool: str, success: bool, data: dict[str, Any], gate_result: str) -> None:
    history = state.get("history")
    if not isinstance(history, list):
        history = []
    metrics = {}
    for key in ("completed_rounds", "total_rounds", "accuracy", "generated_tests", "passed"):
        if key in data:
            metrics[key] = data[key]
    entry = {
        "tool": tool,
        "success": success,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "gate_result": gate_result,
        "key_metrics": metrics,
    }
    # When problem_verify_tests writes directly and then MCP server calls apply_result,
    # upgrade the direct entry in-place instead of logging duplicate history entries.
    if (
        gate_result == "post"
        and tool == "problem_verify_tests"
        and history
        and isinstance(history[-1], dict)
        and history[-1].get("tool") == "problem_verify_tests"
        and history[-1].get("gate_result") == "tool_direct"
    ):
        history[-1] = entry
    else:
        history.append(entry)
    state["history"] = history[-200:]


def merge_verify_state(
    state: dict[str, Any],
    *,
    success: bool,
    passed: bool,
    results: dict[str, Any],
    quality_signals: dict[str, Any],
    dataset_fingerprint: str | None = None,
    executed_signals: list[str] | set[str] | None = None,
    require_core_checks: bool = False,
) -> dict[str, Any]:
    """Merge verification signals into workflow state (Single Source of Truth).

    When ``dataset_fingerprint`` is provided:
      - Signals executed or explicitly evaluated in this run are updated and stamped
        with ``dataset_fingerprint``.
      - Signals omitted from a subset run are preserved ONLY when the prior stored
        signal carries the exact same ``dataset_fingerprint``. Legacy or stale
        un-fingerprinted signals are always overwritten.
    When ``dataset_fingerprint`` is None (e.g., error exit or unit-test mock):
      - ``verify_signals`` is replaced directly.
    """
    prev_signals = state.get("verify_signals")
    prev_signals = prev_signals if isinstance(prev_signals, dict) else {}
    prev_fp = state.get("verify_dataset_fingerprint")
    in_run = set(executed_signals) if executed_signals is not None else set()

    if isinstance(dataset_fingerprint, str) and dataset_fingerprint:
        merged: dict[str, Any] = {}
        for sig_name, raw_sig in quality_signals.items():
            sig = dict(raw_sig) if isinstance(raw_sig, dict) else {}
            was_evaluated = (
                sig_name in in_run
                or bool(sig.get("executed"))
                or sig.get("status") == "not_applicable"
            )
            if was_evaluated:
                if bool(sig.get("executed")) or sig.get("status") == "not_applicable":
                    sig["fingerprint"] = dataset_fingerprint
                merged[sig_name] = sig
            else:
                prev_sig = prev_signals.get(sig_name)
                if (
                    isinstance(prev_sig, dict)
                    and prev_sig.get("fingerprint") == dataset_fingerprint
                    and (bool(prev_sig.get("executed")) or prev_sig.get("status") == "not_applicable")
                ):
                    merged[sig_name] = dict(prev_sig)
                else:
                    merged[sig_name] = sig
        for prev_name, prev_sig in prev_signals.items():
            if prev_name not in merged and isinstance(prev_sig, dict):
                if prev_sig.get("fingerprint") == dataset_fingerprint and (
                    bool(prev_sig.get("executed")) or prev_sig.get("status") == "not_applicable"
                ):
                    merged[prev_name] = dict(prev_sig)

        state["verify_signals"] = merged
        state["verify_dataset_fingerprint"] = dataset_fingerprint

        limit_ratio = results.get("limit_ratio", {}) if isinstance(results, dict) else {}
        if isinstance(limit_ratio, dict) and "limit_case_ratio" in limit_ratio:
            state["limit_case_ratio"] = limit_ratio.get("limit_case_ratio")
        elif (
            "limit_ratio" not in in_run
            and prev_fp == dataset_fingerprint
            and state.get("limit_case_ratio") is not None
        ):
            pass
        else:
            state["limit_case_ratio"] = None
    else:
        state["verify_signals"] = dict(quality_signals) if isinstance(quality_signals, dict) else {}
        state["verify_dataset_fingerprint"] = None
        limit_ratio = results.get("limit_ratio", {}) if isinstance(results, dict) else {}
        state["limit_case_ratio"] = (
            limit_ratio.get("limit_case_ratio")
            if isinstance(limit_ratio, dict) and "limit_case_ratio" in limit_ratio
            else None
        )

    any_merged_failed = any(
        isinstance(sig, dict) and bool(sig.get("executed")) and not bool(sig.get("passed"))
        for sig in state["verify_signals"].values()
    )
    ratio_ok = _min_limit_ratio_gate_ok(state, {})
    core_ok = True
    if require_core_checks and isinstance(dataset_fingerprint, str) and dataset_fingerprint:
        sigs = state["verify_signals"]
        for core_key in ("file_count", "no_empty", "answer_consistency"):
            csig = sigs.get(core_key)
            if not (isinstance(csig, dict) and bool(csig.get("executed")) and bool(csig.get("passed"))):
                core_ok = False
                break
        req_ratio = _limit_ratio_threshold(state)
        if core_ok and req_ratio is not None and req_ratio > 0 and _quality_gate_enabled(state, "require_limit_semantics"):
            if state.get("limit_case_ratio") is None:
                core_ok = False
    state["tests_verified"] = bool(success and passed and ratio_ok and core_ok and not any_merged_failed)
    return state


def record_verify_state(
    problem_dir: str,
    *,
    success: bool,
    passed: bool,
    results: dict[str, Any],
    quality_signals: dict[str, Any],
    dataset_fingerprint: str | None = None,
    executed_signals: list[str] | set[str] | None = None,
) -> dict[str, Any]:
    """Persist problem_verify_tests outcome via the unified enforcement state model."""
    state = load_workflow_state(problem_dir)
    managed = manifest_path(problem_dir).is_file() and _quality_gate_enabled(
        state, "require_tests_verified"
    )
    merge_verify_state(
        state,
        success=success,
        passed=passed,
        results=results,
        quality_signals=quality_signals,
        dataset_fingerprint=dataset_fingerprint,
        executed_signals=executed_signals,
        require_core_checks=managed,
    )
    _append_history(
        state,
        "problem_verify_tests",
        success,
        {"passed": passed},
        "tool_direct",
    )
    save_workflow_state(problem_dir, state)
    return state


def _apply_problem_build_all(
    problem_dir: str,
    state: dict[str, Any],
    data: dict[str, Any],
) -> None:
    """Apply granular per-target build/invalidation state for problem_build_all."""
    compiled = data.get("compiled", {}) if isinstance(data.get("compiled"), dict) else {}
    failures = data.get("failures", {}) if isinstance(data.get("failures"), dict) else {}

    custom_comm_files: set[str] = set()
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest = None
    if manifest is not None and manifest.protocol is not None and manifest.protocol.communicator_file:
        norm = _saved_relative_path(problem_dir, manifest.protocol.communicator_file)
        if norm:
            custom_comm_files.add(norm)

    comm_targets = {"files/interactor.cpp", "files/communicator.cpp"} | custom_comm_files
    invalidate_full = False
    invalidate_verify_only = False

    # Failed targets had their old binaries removed prior to compilation; reset their gates.
    for rel in failures:
        if isinstance(rel, str):
            _set_source_hash(state, rel, None)
        if rel == "solutions/sol.cpp":
            state.update(
                {
                    "sol_built": False,
                    "solution_analyzed": False,
                    "std_audited": False,
                    "std_empirical_verified": False,
                    "std_empirical_hash": None,
                    "sol_source_hash": None,
                    "sol_analyzed_hash": None,
                    "std_audited_hash": None,
                    "std_claimed_complexity": None,
                }
            )
            invalidate_full = True
        elif rel == "solutions/brute.cpp":
            state.update(
                {
                    "brute_built": False,
                    "brute_audited": False,
                    "brute_probed": False,
                    "brute_probed_hash": None,
                    "brute_source_hash": None,
                    "brute_audited_hash": None,
                }
            )
            invalidate_full = True
        elif rel == "files/gen.cpp":
            state.update(
                {
                    "generator_built": False,
                    "std_empirical_verified": False,
                    "std_empirical_hash": None,
                    "brute_probed": False,
                    "brute_probed_hash": None,
                }
            )
            invalidate_full = True
        elif rel == "files/val.cpp":
            state.update(
                {
                    "validator_ready": False,
                    "validator_accuracy": None,
                    "validator_has_positive_cases": False,
                    "validator_has_negative_cases": False,
                }
            )
            invalidate_full = True
        elif rel == "files/checker.cpp":
            state.update({"checker_ready": False, "checker_accuracy": None})
            invalidate_full = True
        elif rel in comm_targets:
            state.update(
                {
                    "interactor_ready": False,
                    "interaction_scenarios": {},
                    "std_empirical_verified": False,
                    "std_empirical_hash": None,
                }
            )
            invalidate_full = True
        elif isinstance(rel, str) and rel.startswith("files/"):
            state.update(
                {
                    "std_empirical_verified": False,
                    "std_empirical_hash": None,
                    "brute_probed": False,
                    "brute_probed_hash": None,
                }
            )
            invalidate_full = True
        elif isinstance(rel, str) and rel.startswith("solutions/"):
            invalidate_verify_only = True

    cur_plat = current_platform()
    for rel in compiled:
        if isinstance(rel, str):
            _set_target_platform(state, rel, cur_plat)
        if rel == "solutions/sol.cpp":
            pure, cur_sol = _is_pure_recompile(
                problem_dir,
                state,
                "solutions/sol.cpp",
                legacy_hash_key="sol_source_hash",
            )
            state["sol_built"] = True
            state["sol_source_hash"] = cur_sol
            _set_source_hash(state, "solutions/sol.cpp", cur_sol)
            _record_grader_dep_hashes(problem_dir, state)
            if not pure:
                state.update(
                    {
                        "solution_analyzed": False,
                        "std_audited": False,
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                        "sol_analyzed_hash": None,
                        "std_audited_hash": None,
                        "std_claimed_complexity": None,
                    }
                )
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif rel == "solutions/brute.cpp":
            pure, cur_brute = _is_pure_recompile(
                problem_dir,
                state,
                "solutions/brute.cpp",
                legacy_hash_key="brute_source_hash",
            )
            state["brute_built"] = True
            state["brute_source_hash"] = cur_brute
            _set_source_hash(state, "solutions/brute.cpp", cur_brute)
            _record_grader_dep_hashes(problem_dir, state)
            if not pure:
                if cur_brute is None or cur_brute != state.get("brute_audited_hash"):
                    state.update({"brute_audited": False, "brute_audited_hash": None})
                state.update({"brute_probed": False, "brute_probed_hash": None})
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif rel == "files/gen.cpp":
            pure, cur_gen = _is_pure_recompile(problem_dir, state, "files/gen.cpp")
            state["generator_built"] = True
            _set_source_hash(state, "files/gen.cpp", cur_gen)
            if not pure:
                state.update(
                    {
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                        "brute_probed": False,
                        "brute_probed_hash": None,
                    }
                )
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif rel == "files/val.cpp":
            pure, cur_val = _is_pure_recompile(problem_dir, state, "files/val.cpp")
            _set_source_hash(state, "files/val.cpp", cur_val)
            if not pure:
                state.update(
                    {
                        "validator_ready": False,
                        "validator_accuracy": None,
                        "validator_has_positive_cases": False,
                        "validator_has_negative_cases": False,
                    }
                )
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif rel == "files/checker.cpp":
            pure, cur_chk = _is_pure_recompile(problem_dir, state, "files/checker.cpp")
            _set_source_hash(state, "files/checker.cpp", cur_chk)
            if not pure:
                state.update({"checker_ready": False, "checker_accuracy": None})
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif isinstance(rel, str) and rel in comm_targets:
            pure, cur_comm = _is_pure_recompile(problem_dir, state, rel)
            _set_source_hash(state, rel, cur_comm)
            if not pure:
                state.update(
                    {
                        "interactor_ready": False,
                        "interaction_scenarios": {},
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                    }
                )
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif isinstance(rel, str) and rel.startswith("files/"):
            pure, cur_f = _is_pure_recompile(problem_dir, state, rel)
            _set_source_hash(state, rel, cur_f)
            if not pure:
                state.update(
                    {
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                        "brute_probed": False,
                        "brute_probed_hash": None,
                    }
                )
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif isinstance(rel, str) and rel.startswith("solutions/"):
            invalidate_verify_only = True

    if compiled and not failures:
        _update_build_platform(state, cur_plat)

    if invalidate_full:
        _invalidate_downstream(problem_dir, state)
    elif invalidate_verify_only:
        _invalidate_verify_only(problem_dir, state)


def _post_generator_unchanged_source(
    problem_dir: str,
    state: dict[str, Any],
    tool_name: str,
    tool_input: dict[str, Any],
) -> bool:
    """Return True when a post-generator audit/analyze call targets unchanged canonical source."""
    if not bool(state.get("generator_built")):
        return False
    if tool_name == "solution_analyze":
        solution_type = tool_input.get("solution_type", "sol")
        if solution_type == "brute":
            if not _matches_canonical_source(problem_dir, tool_input, "solutions/brute.cpp"):
                return False
            cur = _file_sha256(problem_dir, "solutions/brute.cpp")
            expected = [
                h
                for h in (state.get("brute_source_hash"), state.get("brute_audited_hash"))
                if isinstance(h, str) and h
            ]
            return isinstance(cur, str) and bool(cur) and bool(expected) and all(cur == h for h in expected)
        if solution_type != "sol":
            return False
        if not _matches_canonical_source(problem_dir, tool_input, "solutions/sol.cpp"):
            return False
        cur = _file_sha256(problem_dir, "solutions/sol.cpp")
        expected = [
            h
            for h in (
                state.get("sol_source_hash"),
                state.get("sol_analyzed_hash"),
                state.get("std_audited_hash"),
            )
            if isinstance(h, str) and h
        ]
        return isinstance(cur, str) and bool(cur) and bool(expected) and all(cur == h for h in expected)
    if tool_name == "solution_audit_std":
        if not _matches_canonical_source(problem_dir, tool_input, "solutions/sol.cpp"):
            return False
        cur = _file_sha256(problem_dir, "solutions/sol.cpp")
        expected = [
            h
            for h in (
                state.get("sol_source_hash"),
                state.get("sol_analyzed_hash"),
                state.get("std_audited_hash"),
            )
            if isinstance(h, str) and h
        ]
        return isinstance(cur, str) and bool(cur) and bool(expected) and all(cur == h for h in expected)
    if tool_name == "solution_audit_brute":
        if not _matches_canonical_source(problem_dir, tool_input, "solutions/brute.cpp"):
            return False
        cur = _file_sha256(problem_dir, "solutions/brute.cpp")
        expected = [
            h
            for h in (state.get("brute_source_hash"), state.get("brute_audited_hash"))
            if isinstance(h, str) and h
        ]
        return isinstance(cur, str) and bool(cur) and bool(expected) and all(cur == h for h in expected)
    return False


def _empirical_probe_outcome(data: dict[str, Any]) -> tuple[bool, bool, bool]:
    """Classify empirical_verification output as (ran_and_passed, failed, skipped)."""
    emp = data.get("empirical_verification")
    if not isinstance(emp, dict):
        return False, False, False
    status = str(emp.get("status") or "")
    if emp.get("passed") is False or status in ("manifest_error", "generator_error"):
        return False, True, False
    if status == "skipped":
        return False, False, True
    if status in ("pending_generator", "pending_interactor", "by_design_slow"):
        return False, False, False
    if emp.get("passed") is True:
        return True, False, False
    return False, False, False


def _brute_probe_outcome(data: dict[str, Any]) -> tuple[bool, bool]:
    """Classify brute_probe output as (probed_ok, failed)."""
    probe = data.get("brute_probe")
    if not isinstance(probe, dict):
        return False, False
    status = str(probe.get("status") or "")
    if probe.get("passed") is False or status in ("crashed", "timeout_at_min_scale", "generator_error"):
        return False, True
    if status in ("ok", "timeout_circuit_breaker") and probe.get("passed", True) is not False:
        return True, False
    return False, False


def apply_result(problem_dir: str, tool_name: str, tool_input: dict[str, Any], success: bool, data: dict[str, Any]) -> dict[str, Any]:
    state = load_workflow_state(problem_dir)
    _sync_dynamic_probe_hashes(problem_dir, state)
    if tool_name == "problem_create" and not success:
        return state
    if tool_name == "problem_audit" and bool(tool_input.get("standalone")):
        return state
    if tool_name == "solution_build" and bool(tool_input.get("enable_sanitizers")):
        # Isolated sanitizer build in .autocode/san_bin/ — never alters production workflow gates.
        _append_history(state, tool_name, success, data, "post")
        save_workflow_state(problem_dir, state)
        return state

    is_auxiliary_solution_build = (
        tool_name == "solution_build"
        and tool_input.get("solution_type") in ("wrong", "reference")
    )
    pure_recompile = False
    if success and tool_name == "solution_build" and not is_auxiliary_solution_build:
        st_type = tool_input.get("solution_type")
        if st_type == "sol":
            pure_recompile, _ = _is_pure_recompile(
                problem_dir,
                state,
                "solutions/sol.cpp",
                legacy_hash_key="sol_source_hash",
            )
        elif st_type == "brute":
            pure_recompile, _ = _is_pure_recompile(
                problem_dir,
                state,
                "solutions/brute.cpp",
                legacy_hash_key="brute_source_hash",
            )
    elif success and tool_name == "validator_build":
        pure_recompile, _ = _is_pure_recompile(
            problem_dir, state, "files/val.cpp", ready_flag="validator_ready"
        )
    elif success and tool_name == "generator_build":
        pure_recompile, _ = _is_pure_recompile(
            problem_dir, state, "files/gen.cpp"
        )
    elif success and tool_name == "checker_build":
        pure_recompile, _ = _is_pure_recompile(
            problem_dir, state, "files/checker.cpp", ready_flag="checker_ready"
        )
    elif success and tool_name == "interactor_build":
        pure_recompile, _ = _is_pure_recompile(
            problem_dir,
            state,
            _canonical_comm_rel(problem_dir),
            ready_flag="interactor_ready",
        )

    post_gen_non_destructive = False
    if success and tool_name in {"solution_analyze", "solution_audit_std", "solution_audit_brute"}:
        if _post_generator_unchanged_source(problem_dir, state, tool_name, tool_input):
            if tool_name == "solution_analyze":
                _, emp_failed, _ = _empirical_probe_outcome(data)
                post_gen_non_destructive = not emp_failed
            elif tool_name == "solution_audit_std":
                audit_ok = data.get("passed", True) is not False
                _, emp_failed, _ = _empirical_probe_outcome(data)
                post_gen_non_destructive = audit_ok and not emp_failed
            elif tool_name == "solution_audit_brute":
                audit_ok = data.get("passed", True) is not False
                _, probe_failed = _brute_probe_outcome(data)
                _, emp_failed, _ = _empirical_probe_outcome(data)
                post_gen_non_destructive = audit_ok and not probe_failed and not emp_failed

    if tool_name in _DOWNSTREAM_INVALIDATING_TOOLS and not is_auxiliary_solution_build:
        if post_gen_non_destructive:
            pass
        elif pure_recompile:
            _invalidate_verify_only(problem_dir, state)
        else:
            _invalidate_downstream(problem_dir, state)
    elif is_auxiliary_solution_build and success:
        state.update(
            {
                "tests_verified": False,
                "limit_case_ratio": None,
                "verify_signals": {},
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
    elif tool_name == "file_save" and success:
        _invalidate_for_file_save(problem_dir, state, tool_input)
    if tool_name == "problem_generate_tests":
        state["tests_verified"] = False
        generated = data.get("generated_tests", [])
        total_tests = data.get("total_tests")
        if isinstance(total_tests, int) and not isinstance(total_tests, bool) and total_tests > 0:
            effective_count = total_tests
        else:
            effective_count = len(generated) if isinstance(generated, list) else 0
        state["tests_generated"] = bool(effective_count > 0) if success else False
        state["generated_test_count"] = effective_count if success else 0
    elif tool_name == "problem_validate":
        statement = data.get("statement_samples", {})
        samples = data.get("sample_files", {})
        state["statement_validated"] = bool(statement.get("validated")) if isinstance(statement, dict) else False
        state["sample_files_validated"] = bool(samples.get("validated")) if isinstance(samples, dict) else False
        state["validation_passed"] = bool(success)
        if success and state["statement_validated"]:
            state["statement_dirty"] = False
            stmt_rel = resolve_statement_rel(problem_dir)
            stmt_dig = current_statement_sha256(problem_dir)
            state["statement_source_hash"] = stmt_dig
            if stmt_dig is not None:
                _set_source_hash(state, stmt_rel, stmt_dig)
        elif not success:
            state["statement_source_hash"] = None
            state["packaged"] = False
            set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
    elif (
        tool_name == "problem_audit"
        and not bool(tool_input.get("standalone"))
        and str(tool_input.get("mode", "full")) == "full"
    ):
        stmt_dig = current_statement_sha256(problem_dir)
        if (
            success
            and data.get("decision") == "go"
            and stmt_dig is not None
            and not bool(state.get("statement_dirty", False))
        ):
            state["statement_source_hash"] = stmt_dig
            _set_source_hash(state, resolve_statement_rel(problem_dir), stmt_dig)
    elif tool_name == "problem_build_all":
        _apply_problem_build_all(problem_dir, state, data)
    elif tool_name == "problem_verify_tests":
        results = data.get("results", {})
        signals = data.get("quality_signals", {})
        raw_fp = data.get("dataset_fingerprint")
        raw_exec = data.get("executed_signals")
        managed = manifest_path(problem_dir).is_file() and _quality_gate_enabled(
            state, "require_tests_verified"
        )
        merge_verify_state(
            state,
            success=success,
            passed=bool(data.get("passed", False)),
            results=results if isinstance(results, dict) else {},
            quality_signals=signals if isinstance(signals, dict) else {},
            dataset_fingerprint=raw_fp if isinstance(raw_fp, str) else None,
            executed_signals=raw_exec if isinstance(raw_exec, list) else None,
            require_core_checks=managed,
        )
    elif not success:
        resets: dict[str, tuple[str, ...]] = {
            "solution_analyze": (
                "solution_analyzed",
                "sol_analyzed_hash",
                "std_empirical_verified",
                "std_empirical_hash",
            ),
            "validator_build": (
                "validator_ready",
                "validator_accuracy",
                "validator_has_positive_cases",
                "validator_has_negative_cases",
            ),
            "solution_audit_std": (
                "std_audited",
                "std_audited_hash",
                "std_empirical_verified",
                "std_empirical_hash",
            ),
            "solution_audit_brute": (
                "brute_audited",
                "brute_audited_hash",
                "brute_probed",
                "brute_probed_hash",
            ),
            "generator_build": (
                "generator_built",
                "std_empirical_verified",
                "std_empirical_hash",
                "brute_probed",
                "brute_probed_hash",
            ),
            "stress_test_run": ("stress_completed_rounds", "stress_total_rounds", "stress_passed"),
            "checker_build": ("checker_ready", "checker_accuracy"),
            "interactor_build": (
                "interactor_ready",
                "interaction_scenarios",
                "std_empirical_verified",
                "std_empirical_hash",
            ),
            "problem_pack_polygon": ("packaged",),
        }
        for key in resets.get(tool_name, ()):
            if key.endswith("_hash"):
                state[key] = None
            elif key.endswith("rounds"):
                state[key] = 0
            elif key.endswith("scenarios"):
                state[key] = {}
            else:
                state[key] = False
        if tool_name == "solution_build":
            solution_type = tool_input.get("solution_type")
            if solution_type == "sol":
                state.update(
                    {
                        "sol_built": False,
                        "solution_analyzed": False,
                        "std_audited": False,
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                        "sol_source_hash": None,
                        "sol_analyzed_hash": None,
                        "std_audited_hash": None,
                        "std_claimed_complexity": None,
                    }
                )
                _set_source_hash(state, "solutions/sol.cpp", None)
            elif solution_type == "brute":
                state.update(
                    {
                        "brute_built": False,
                        "brute_audited": False,
                        "brute_probed": False,
                        "brute_probed_hash": None,
                        "brute_source_hash": None,
                        "brute_audited_hash": None,
                    }
                )
                _set_source_hash(state, "solutions/brute.cpp", None)
        elif tool_name == "problem_validate":
            state.update(
                {
                    "statement_validated": False,
                    "sample_files_validated": False,
                    "validation_passed": False,
                }
            )
        if tool_name in {"validator_build", "checker_build"}:
            state["validator_accuracy" if tool_name == "validator_build" else "checker_accuracy"] = None
    elif tool_name == "problem_create":
        state["created"] = True
        ptype = data.get("problem_type") or tool_input.get("problem_type")
        if ptype is None:
            ptype = "interactive" if bool(tool_input.get("interactive", False)) else "standard"
        state["problem_type"] = ptype
        state["interactive"] = ptype == "interactive"
        state["oracle_mode"] = data.get("oracle_mode") or tool_input.get("oracle_mode") or state.get("oracle_mode") or "brute"
        if "protocol_mode" in data:
            state["protocol_mode"] = data["protocol_mode"]
        elif state.get("protocol_mode") is None:
            if ptype == "interactive":
                state["protocol_mode"] = tool_input.get("protocol_mode") or "interactive_1v1"
            elif ptype == "communication":
                state["protocol_mode"] = tool_input.get("protocol_mode") or "run_twice"
            else:
                state["protocol_mode"] = None
        state.update(
            {
                "sol_built": False,
                "brute_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "std_empirical_verified": False,
                "std_empirical_hash": None,
                "brute_probed": False,
                "brute_probed_hash": None,
                "sol_source_hash": None,
                "sol_analyzed_hash": None,
                "std_audited_hash": None,
                "brute_source_hash": None,
                "brute_audited_hash": None,
                "std_claimed_complexity": None,
                "validator_ready": False,
                "validator_accuracy": None,
                "validator_has_positive_cases": False,
                "validator_has_negative_cases": False,
                "generator_built": False,
                "checker_ready": False,
                "checker_accuracy": None,
                "interactor_ready": False,
                "interaction_scenarios": {},
                "statement_dirty": False,
            }
        )
    elif tool_name == "solution_build":
        solution_type = tool_input.get("solution_type")
        cur_plat = current_platform()
        _update_build_platform(state, cur_plat)
        if solution_type == "sol":
            cur_sol = _file_sha256(problem_dir, "solutions/sol.cpp")
            sol_changed = cur_sol is None or cur_sol != state.get("sol_source_hash")
            state["sol_built"] = True
            state["sol_source_hash"] = cur_sol
            _set_source_hash(state, "solutions/sol.cpp", cur_sol)
            _set_target_platform(state, "solutions/sol.cpp", cur_plat)
            _record_grader_dep_hashes(problem_dir, state)
            if sol_changed:
                state.update(
                    {
                        "solution_analyzed": False,
                        "std_audited": False,
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                        "sol_analyzed_hash": None,
                        "std_audited_hash": None,
                        "std_claimed_complexity": None,
                    }
                )
        elif solution_type == "brute":
            cur_brute = _file_sha256(problem_dir, "solutions/brute.cpp")
            brute_changed = cur_brute is None or cur_brute != state.get("brute_source_hash")
            state["brute_built"] = True
            state["brute_source_hash"] = cur_brute
            _set_source_hash(state, "solutions/brute.cpp", cur_brute)
            _set_target_platform(state, "solutions/brute.cpp", cur_plat)
            _record_grader_dep_hashes(problem_dir, state)
            if brute_changed and (
                cur_brute is None or cur_brute != state.get("brute_audited_hash")
            ):
                state.update({"brute_audited": False, "brute_audited_hash": None})
            if brute_changed and (
                cur_brute is None or cur_brute != state.get("brute_probed_hash")
            ):
                state.update({"brute_probed": False, "brute_probed_hash": None})
    elif tool_name == "solution_analyze":
        solution_type = tool_input.get("solution_type", "sol")
        if solution_type == "sol" and _matches_canonical_source(
            problem_dir, tool_input, "solutions/sol.cpp"
        ):
            cur_sol = _file_sha256(problem_dir, "solutions/sol.cpp")
            emp_ok, emp_failed, emp_skipped = _empirical_probe_outcome(data)
            if post_gen_non_destructive:
                state["solution_analyzed"] = True
                state["sol_analyzed_hash"] = cur_sol
                if emp_ok:
                    state["std_empirical_verified"] = True
                    state["std_empirical_hash"] = cur_sol
                elif not (
                    emp_skipped
                    and bool(state.get("std_empirical_verified"))
                    and state.get("std_empirical_hash") == cur_sol
                ):
                    state["std_empirical_verified"] = False
                    state["std_empirical_hash"] = None
            elif emp_failed:
                state.update(
                    {
                        "solution_analyzed": True,
                        "sol_analyzed_hash": cur_sol,
                        "std_audited": False,
                        "std_audited_hash": None,
                        "std_empirical_verified": False,
                        "std_empirical_hash": None,
                    }
                )
            else:
                state.update(
                    {
                        "solution_analyzed": True,
                        "sol_analyzed_hash": cur_sol,
                        "std_audited": False,
                        "std_audited_hash": None,
                        "std_empirical_verified": bool(emp_ok),
                        "std_empirical_hash": cur_sol if emp_ok else None,
                    }
                )
            complexity = data.get("estimated_complexity") or data.get("final_complexity") or data.get("worst_case_complexity")
            if complexity is not None:
                state["std_complexity"] = complexity
            claimed = data.get("claimed_complexity") or tool_input.get("claimed_complexity")
            if isinstance(claimed, str) and claimed.strip():
                state["std_claimed_complexity"] = claimed.strip()
            elif not post_gen_non_destructive:
                state["std_claimed_complexity"] = None
            if "recommended_stress_params" in data:
                state["recommended_stress_params"] = data["recommended_stress_params"]
    elif tool_name == "solution_audit_std":
        audit_passed = data.get("passed", True) is not False
        emp_ok, emp_failed, emp_skipped = _empirical_probe_outcome(data)
        if not audit_passed or emp_failed:
            state["std_audited"] = False
            state["std_audited_hash"] = None
            state["std_empirical_verified"] = False
            state["std_empirical_hash"] = None
        elif _matches_canonical_source(problem_dir, tool_input, "solutions/sol.cpp"):
            cur_sol = _file_sha256(problem_dir, "solutions/sol.cpp")
            state["std_audited"] = True
            state["std_audited_hash"] = cur_sol
            if emp_ok:
                state["std_empirical_verified"] = True
                state["std_empirical_hash"] = cur_sol
            elif not (
                emp_skipped
                and post_gen_non_destructive
                and bool(state.get("std_empirical_verified"))
                and state.get("std_empirical_hash") == cur_sol
            ):
                state["std_empirical_verified"] = False
                state["std_empirical_hash"] = None
            claimed = data.get("claimed_complexity") or tool_input.get("claimed_complexity")
            if isinstance(claimed, str) and claimed.strip():
                state["std_claimed_complexity"] = claimed.strip()
    elif tool_name == "solution_audit_brute":
        audit_passed = data.get("passed", True) is not False
        probe_ok, probe_failed = _brute_probe_outcome(data)
        _, emp_failed, _ = _empirical_probe_outcome(data)
        if not audit_passed or probe_failed or emp_failed:
            state["brute_audited"] = False
            state["brute_audited_hash"] = None
            state["brute_probed"] = False
            state["brute_probed_hash"] = None
        elif _matches_canonical_source(problem_dir, tool_input, "solutions/brute.cpp"):
            cur_brute = _file_sha256(problem_dir, "solutions/brute.cpp")
            state["brute_audited"] = True
            state["brute_audited_hash"] = cur_brute
            if probe_ok:
                state["brute_probed"] = True
                state["brute_probed_hash"] = cur_brute
            else:
                state["brute_probed"] = False
                state["brute_probed_hash"] = None
            for key in ("brute_complexity", "recommended_stress_params"):
                if key in data:
                    state[key] = data[key]
    elif tool_name == "validator_build":
        accuracy = data.get("accuracy")
        state["validator_accuracy"] = accuracy
        parsed = _finite_ratio(accuracy)
        has_pos = bool(data.get("has_positive_cases"))
        has_neg = bool(data.get("has_negative_cases"))
        state["validator_has_positive_cases"] = has_pos
        state["validator_has_negative_cases"] = has_neg
        state["validator_ready"] = (
            parsed is not None
            and parsed >= 0.9
            and has_pos
            and has_neg
        )
        cur_plat = current_platform()
        _update_build_platform(state, cur_plat)
        _set_source_hash(state, "files/val.cpp", _file_sha256(problem_dir, "files/val.cpp"))
        _set_target_platform(state, "files/val.cpp", cur_plat)
    elif tool_name == "generator_build":
        cur_plat = current_platform()
        state["generator_built"] = True
        if not pure_recompile:
            state.update(
                {
                    "std_empirical_verified": False,
                    "std_empirical_hash": None,
                    "brute_probed": False,
                    "brute_probed_hash": None,
                }
            )
        _update_build_platform(state, cur_plat)
        _set_source_hash(state, "files/gen.cpp", _file_sha256(problem_dir, "files/gen.cpp"))
        _set_target_platform(state, "files/gen.cpp", cur_plat)
    elif tool_name == "stress_test_run":
        state["stress_completed_rounds"] = data.get("completed_rounds", 0)
        state["stress_total_rounds"] = data.get("total_rounds", 0)
        state["stress_passed"] = success and _stress_rounds_complete(data)
    elif tool_name == "checker_build":
        cur_plat = current_platform()
        accuracy = data.get("accuracy")
        state["checker_accuracy"] = accuracy
        parsed = _finite_ratio(accuracy)
        state["checker_ready"] = parsed is not None and parsed >= 0.9
        _update_build_platform(state, cur_plat)
        _set_source_hash(state, "files/checker.cpp", _file_sha256(problem_dir, "files/checker.cpp"))
        _set_target_platform(state, "files/checker.cpp", cur_plat)
    elif tool_name == "interactor_build":
        cur_plat = current_platform()
        scenarios = data.get("interaction_scenarios", {})
        state["interaction_scenarios"] = scenarios if isinstance(scenarios, dict) else {}
        pass_rate = _finite_ratio(data.get("pass_rate", 0))
        fail_rate = _finite_ratio(data.get("fail_rate", 0))
        scenario_ready = _interaction_scenarios_ready(state["interaction_scenarios"])
        state["interactor_ready"] = scenario_ready or (
            pass_rate == 1.0 and fail_rate is not None and fail_rate >= 0.8
        )
        state["interactor_pass_rate"] = pass_rate
        state["interactor_fail_rate"] = fail_rate
        if not pure_recompile:
            state["std_empirical_verified"] = False
            state["std_empirical_hash"] = None
        _update_build_platform(state, cur_plat)
        comm_rel = _canonical_comm_rel(problem_dir)
        _set_source_hash(state, comm_rel, _file_sha256(problem_dir, comm_rel))
        _set_target_platform(state, comm_rel, cur_plat)
    elif tool_name == "problem_pack_polygon":
        state["packaged"] = True
        tl_ms = data.get("time_limit_ms")
        if isinstance(tl_ms, int) and not isinstance(tl_ms, bool):
            state["packaged_time_limit_ms"] = tl_ms
    try:
        manifest_eff = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest_eff = None
    _apply_effective_quality_gates(state, manifest_eff)
    _append_history(state, tool_name, success, data, "post")
    save_workflow_state(problem_dir, state)
    return state


def blocked_result(tool_name: str, violations: list[GateViolation]) -> dict[str, Any]:
    return {
        "gate_blocked": True,
        "tool": tool_name,
        "blocking_issues": [{"gate": v.gate, "reason": v.reason} for v in violations],
        "next_actions": [v.next_action for v in violations],
    }

