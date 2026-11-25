"""Read-only workspace state inspection and reconciliation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..runtime_store import WORKFLOW, get_section, runtime_file
from ..utils.communication_runner import safe_resolve_under_root
from ..utils.platform import get_exe_extension
from .gates import _sync_dynamic_probe_hashes, preflight
from .guard import check_gates, effective_require_wrong_solution_kill
from .hashes import (
    _canonical_comm_rel,
    _file_sha256,
    _invalidate_verify_only,
    current_platform,
)
from .manifest import (
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_requires_brute,
    manifest_uses_testlib_checker,
)
from .state_io import has_workflow_context, load_workflow_state, save_workflow_state


def inspect_workspace_state(problem_dir: str) -> dict[str, Any]:
    """Inspect workspace binaries, per-target platform parity, containment, and unified MCP gate status."""
    root = Path(problem_dir).resolve()
    exe_ext = get_exe_extension()
    cur_plat = current_platform()
    wf_raw = get_section(problem_dir, WORKFLOW)
    wf_present = runtime_file(problem_dir).is_file() and isinstance(wf_raw, dict)
    state = load_workflow_state(problem_dir)
    build_plat = state.get("build_platform") if isinstance(state.get("build_platform"), str) else None
    raw_tp = state.get("target_platforms")
    target_platforms: dict[str, Any] = raw_tp if isinstance(raw_tp, dict) else {}

    manifest = None
    manifest_err: str | None = None
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError) as exc:
        manifest_err = str(exc)

    targets: list[tuple[str, str, str]] = [
        ("sol", "solutions/sol.cpp", f"solutions/sol{exe_ext}"),
    ]
    if manifest is None or manifest_requires_brute(manifest):
        targets.append(("brute", "solutions/brute.cpp", f"solutions/brute{exe_ext}"))
    elif (root / "solutions" / "brute.cpp").is_file():
        targets.append(("brute", "solutions/brute.cpp", f"solutions/brute{exe_ext}"))

    if (root / "files" / "val.cpp").is_file():
        targets.append(("validator", "files/val.cpp", f"files/val{exe_ext}"))
    if (root / "files" / "gen.cpp").is_file():
        targets.append(("generator", "files/gen.cpp", f"files/gen{exe_ext}"))
    if (root / "files" / "checker.cpp").is_file() or (manifest and manifest_uses_testlib_checker(manifest)):
        targets.append(("checker", "files/checker.cpp", f"files/checker{exe_ext}"))
    if manifest and (manifest_is_interactive(manifest) or manifest_is_communication(manifest)):
        if manifest.protocol is None or manifest.protocol.mode != "grader_linked":
            comm_rel = _canonical_comm_rel(problem_dir)
            comm_stem = Path(comm_rel).with_suffix("").as_posix()
            targets.append(("interactor", comm_rel, f"{comm_stem}{exe_ext}"))
    elif (root / "files" / "interactor.cpp").is_file():
        targets.append(("interactor", "files/interactor.cpp", f"files/interactor{exe_ext}"))

    binary_status: dict[str, dict[str, Any]] = {}
    stale_reasons: list[str] = []
    if manifest_err:
        stale_reasons.append(f"manifest.json unreadable: {manifest_err}")

    any_platform_mismatch = bool(build_plat and build_plat != cur_plat)
    if any_platform_mismatch:
        stale_reasons.append(f"build_platform mismatch: {build_plat} != {cur_plat}")

    for name, src_rel, bin_rel in targets:
        raw_src = root / src_rel
        raw_bin = root / bin_rel
        safe_src = safe_resolve_under_root(str(root), src_rel)
        safe_bin = safe_resolve_under_root(str(root), bin_rel)
        src_escaped = (raw_src.exists() or raw_src.is_symlink()) and safe_src is None
        bin_escaped = (raw_bin.exists() or raw_bin.is_symlink()) and safe_bin is None
        src_exists = safe_src is not None and Path(safe_src).is_file()
        bin_exists = safe_bin is not None and Path(safe_bin).is_file()
        target_plat = (
            target_platforms.get(src_rel)
            if isinstance(target_platforms.get(src_rel), str)
            else build_plat
        )
        target_mismatch = bool(target_plat and target_plat != cur_plat)
        if target_mismatch and bin_exists:
            any_platform_mismatch = True

        stale = False
        reason: str | None = None
        if src_escaped or bin_escaped:
            stale = True
            reason = f"symlink escapes problem_dir: {bin_rel if bin_escaped else src_rel}"
        elif src_exists and not bin_exists:
            stale = True
            reason = f"missing binary: {bin_rel}"
        elif src_exists and bin_exists:
            try:
                assert safe_src is not None and safe_bin is not None
                if Path(safe_src).stat().st_mtime > Path(safe_bin).stat().st_mtime + 1e-6:
                    stale = True
                    reason = f"source newer than binary: {src_rel}"
            except OSError:
                pass
        if target_mismatch and bin_exists and not stale:
            stale = True
            reason = f"binary {bin_rel} built on {target_plat}, current platform is {cur_plat}"
        if reason:
            stale_reasons.append(reason)
        binary_status[name] = {
            "source_rel": src_rel,
            "binary_rel": bin_rel,
            "source_exists": src_exists,
            "binary_exists": bin_exists,
            "target_platform": target_plat,
            "stale": stale,
        }

    gate_summary: dict[str, Any] = {
        "quality_gate_issues": [],
        "pack_preflight_issues": [],
        "pack_ready": False,
    }
    if manifest is not None:
        q_issues = check_gates(manifest, state, state.get("verify_signals", {}))
        p_issues = preflight("problem_pack_polygon", problem_dir, {"problem_dir": problem_dir})
        gate_summary = {
            "quality_gate_issues": [{"gate": i.gate, "reason": i.reason} for i in q_issues],
            "pack_preflight_issues": [
                {"gate": v.gate, "reason": v.reason, "next_action": v.next_action}
                for v in p_issues
            ],
            "effective_require_wrong_solution_kill": effective_require_wrong_solution_kill(manifest),
            "pack_ready": (
                (not q_issues)
                and (not p_issues)
                and (not any_platform_mismatch)
                and (not stale_reasons)
            ),
        }

    gen_info = binary_status.get("generator", {})
    sol_info = binary_status.get("sol", {})
    brute_info = binary_status.get("brute", {})
    gen_ready = bool(state.get("generator_built")) and bool(
        gen_info.get("binary_exists") and not gen_info.get("stale")
    )
    sol_ready = bool(state.get("sol_built")) and bool(
        sol_info.get("binary_exists") and not sol_info.get("stale")
    )
    brute_req = manifest is None or manifest_requires_brute(manifest)
    brute_ready = bool(state.get("brute_built")) and bool(
        brute_info.get("binary_exists") and not brute_info.get("stale")
    )
    cur_sol_hash = _file_sha256(problem_dir, "solutions/sol.cpp")
    cur_brute_hash = _file_sha256(problem_dir, "solutions/brute.cpp")
    std_emp_hash = state.get("std_empirical_hash")
    brute_probe_hash = state.get("brute_probed_hash")
    std_emp_hash_ok = bool(
        isinstance(std_emp_hash, str) and std_emp_hash and cur_sol_hash == std_emp_hash
    )
    brute_probe_hash_ok = bool(
        isinstance(brute_probe_hash, str) and brute_probe_hash and cur_brute_hash == brute_probe_hash
    )

    if bool(state.get("std_empirical_verified")):
        if gen_ready and sol_ready and not any_platform_mismatch and std_emp_hash_ok:
            std_emp_status = "verified"
        else:
            std_emp_status = "stale"
    elif not gen_ready or not sol_ready:
        std_emp_status = "pending_binaries"
    else:
        std_emp_status = "pending_run"

    if not brute_req:
        brute_probe_status = "not_applicable"
    elif bool(state.get("brute_probed")):
        if gen_ready and brute_ready and not any_platform_mismatch and brute_probe_hash_ok:
            brute_probe_status = "verified"
        else:
            brute_probe_status = "stale"
    elif not gen_ready or not brute_ready:
        brute_probe_status = "pending_binaries"
    else:
        brute_probe_status = "pending_run"

    dynamic_probes = {
        "std_empirical": {
            "status": std_emp_status,
            "verified": std_emp_status == "verified",
            "hash": std_emp_hash,
        },
        "brute_probe": {
            "status": brute_probe_status,
            "probed": brute_probe_status == "verified",
            "hash": brute_probe_hash,
        },
    }

    return {
        "workflow_state_present": wf_present,
        "build_platform": build_plat,
        "current_platform": cur_plat,
        "platform_mismatch": any_platform_mismatch,
        "binary_status": binary_status,
        "stale_reasons": stale_reasons,
        "gate_summary": gate_summary,
        "dynamic_probes": dynamic_probes,
    }


def reconcile_workspace_state(problem_dir: str) -> dict[str, Any]:
    """Reconcile runtime.json against actual binaries/hashes on disk without fabricating passes."""
    inspection = inspect_workspace_state(problem_dir)
    actions: list[str] = []
    if not has_workflow_context(problem_dir):
        return {
            "reconciled": False,
            "actions": ["no managed workflow context found"],
            "inspection": inspection,
        }

    raw_before = get_section(problem_dir, WORKFLOW) or {}
    state = load_workflow_state(problem_dir)
    if isinstance(raw_before, dict):
        if raw_before.get("sol_built") and not state.get("sol_built"):
            actions.append("invalidated sol_built/downstream due to sol.cpp source hash drift")
        if raw_before.get("brute_built") and not state.get("brute_built"):
            actions.append("invalidated brute_built/downstream due to brute.cpp source hash drift")

    binary_status = inspection["binary_status"]
    flag_map = {
        "sol": "sol_built",
        "brute": "brute_built",
        "generator": "generator_built",
        "validator": "validator_ready",
        "checker": "checker_ready",
        "interactor": "interactor_ready",
    }
    invalidated_verify = False
    for name, flag in flag_map.items():
        info = binary_status.get(name)
        if not info:
            continue
        if info.get("stale") and state.get(flag):
            state[flag] = False
            if name in ("validator", "checker"):
                state[f"{name}_accuracy"] = None
            actions.append(f"cleared {flag} (stale/missing {info['binary_rel']})")
            invalidated_verify = True

    if inspection["platform_mismatch"] or invalidated_verify:
        if inspection["platform_mismatch"]:
            actions.append(
                f"cleared tests_verified/packaged due to platform mismatch "
                f"({inspection['build_platform']} -> {inspection['current_platform']})"
            )
        _invalidate_verify_only(problem_dir, state)

    was_std_emp = bool(state.get("std_empirical_verified"))
    was_brute_probed = bool(state.get("brute_probed"))
    _sync_dynamic_probe_hashes(problem_dir, state)
    if state.get("std_empirical_verified") and (
        inspection["platform_mismatch"] or not state.get("sol_built") or not state.get("generator_built")
    ):
        state["std_empirical_verified"] = False
        state["std_empirical_hash"] = None
    if state.get("brute_probed") and (
        inspection["platform_mismatch"] or not state.get("brute_built") or not state.get("generator_built")
    ):
        state["brute_probed"] = False
        state["brute_probed_hash"] = None
    if was_std_emp and not state.get("std_empirical_verified"):
        actions.append("cleared std_empirical_verified (stale source/generator/platform)")
    if was_brute_probed and not state.get("brute_probed"):
        actions.append("cleared brute_probed (stale source/generator/platform)")

    save_workflow_state(problem_dir, state)
    return {
        "reconciled": True,
        "actions": actions,
        "inspection": inspect_workspace_state(problem_dir),
    }

