"""Host-independent workflow enforcement for AutoCode MCP calls.

Claude Code hooks can provide an early, user-friendly denial, but they are not
available to every MCP host.  The MCP server therefore owns the authoritative
preflight checks and workflow state transitions in this module.
"""

from __future__ import annotations

import hashlib
import math
import os
import platform as _platform
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime_store import AUDIT, WORKFLOW, get_section, runtime_file, set_section
from ..utils.communication_runner import safe_resolve_under_root
from ..utils.platform import get_exe_extension
from .guard import check_gates
from .manifest import (
    CANONICAL_PROBLEM_FILES,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_path,
    manifest_requires_brute,
    manifest_uses_testlib_checker,
)

DEFAULT_QUALITY_GATES: dict[str, Any] = {
    "require_stress_passed": True,
    "require_validation_passed": True,
    "require_tests_verified": True,
    "require_limit_semantics": True,
    "require_wrong_solution_kill": False,
    "require_validator_check": True,
    "min_limit_case_ratio": 0.5,
}

Gate = tuple[Callable[[dict[str, Any], dict[str, Any]], bool], str]


@dataclass(frozen=True)
class GateViolation:
    """A deterministic reason a tool call cannot proceed."""

    gate: str
    reason: str
    next_action: str


def current_platform() -> str:
    """Return a normalized OS/architecture tag, e.g. 'linux/x86_64' or 'darwin/arm64'."""
    machine = (_platform.machine() or "unknown").lower()
    return f"{sys.platform}/{machine}"


def _file_sha256(problem_dir: str, rel_path: str) -> str | None:
    resolved = safe_resolve_under_root(problem_dir, rel_path)
    if resolved is None:
        return None
    path = Path(resolved)
    if not path.is_file():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _set_source_hash(state: dict[str, Any], rel_path: str, digest: str | None) -> None:
    hashes = state.get("source_hashes")
    if not isinstance(hashes, dict):
        hashes = {}
        state["source_hashes"] = hashes
    hashes[rel_path] = digest


def _set_target_platform(state: dict[str, Any], rel_path: str, plat: str | None = None) -> None:
    tp = state.get("target_platforms")
    if not isinstance(tp, dict):
        tp = {}
        state["target_platforms"] = tp
    if plat is None:
        tp.pop(rel_path, None)
    else:
        tp[rel_path] = plat


def _update_build_platform(state: dict[str, Any], cur_plat: str) -> None:
    """Update global build_platform while preserving legacy cross-platform mismatch for un-rebuilt targets."""
    old_plat = state.get("build_platform")
    tp = state.get("target_platforms")
    if isinstance(old_plat, str) and old_plat and old_plat != cur_plat and not (isinstance(tp, dict) and tp):
        seeded: dict[str, str] = {}
        hashes = state.get("source_hashes")
        if isinstance(hashes, dict):
            for rel, digest in hashes.items():
                if isinstance(rel, str) and isinstance(digest, str) and digest:
                    seeded[rel] = old_plat
        if state.get("sol_built"):
            seeded.setdefault("solutions/sol.cpp", old_plat)
        if state.get("brute_built"):
            seeded.setdefault("solutions/brute.cpp", old_plat)
        if state.get("generator_built"):
            seeded.setdefault("files/gen.cpp", old_plat)
        if seeded:
            state["target_platforms"] = seeded
    state["build_platform"] = cur_plat


def _grader_deps_unchanged(problem_dir: str, state: dict[str, Any]) -> bool:
    """For grader_linked problems, verify grader/header hashes did not drift before pure recompile."""
    if state.get("protocol_mode") != "grader_linked":
        return True
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        return False
    dep_rels: list[str] = []
    if manifest is not None and manifest.protocol is not None:
        dep_rels.append(manifest.protocol.grader_file or "files/grader.cpp")
        if manifest.protocol.header_file:
            dep_rels.append(manifest.protocol.header_file)
    else:
        dep_rels.extend(["files/grader.cpp", "files/problem.h"])
    raw_hashes = state.get("source_hashes")
    hashes: dict[str, Any] = raw_hashes if isinstance(raw_hashes, dict) else {}
    for dep in dep_rels:
        cur = _file_sha256(problem_dir, dep)
        if cur is None:
            continue
        prev = hashes.get(dep)
        if isinstance(prev, str) and prev and cur != prev:
            return False
    return True


def _record_grader_dep_hashes(problem_dir: str, state: dict[str, Any]) -> None:
    if state.get("protocol_mode") != "grader_linked":
        return
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest = None
    dep_rels = (
        [
            manifest.protocol.grader_file or "files/grader.cpp",
            *(
                [manifest.protocol.header_file]
                if manifest.protocol.header_file
                else []
            ),
        ]
        if (manifest is not None and manifest.protocol is not None)
        else ["files/grader.cpp", "files/problem.h"]
    )
    for dep in dep_rels:
        dig = _file_sha256(problem_dir, dep)
        if dig is not None:
            _set_source_hash(state, dep, dig)


def _is_pure_recompile(
    problem_dir: str,
    state: dict[str, Any],
    rel_path: str,
    *,
    legacy_hash_key: str | None = None,
    ready_flag: str | None = None,
) -> tuple[bool, str | None]:
    """Return (True, sha256) when recompiling identical source bytes (e.g. cross-platform rebuild)."""
    cur = _file_sha256(problem_dir, rel_path)
    if cur is None:
        return False, None
    if ready_flag is not None and not bool(state.get(ready_flag)):
        return False, cur
    if rel_path in ("solutions/sol.cpp", "solutions/brute.cpp") and not _grader_deps_unchanged(
        problem_dir, state
    ):
        return False, cur
    hashes = state.get("source_hashes")
    prev = hashes.get(rel_path) if isinstance(hashes, dict) else None
    if not (isinstance(prev, str) and prev) and legacy_hash_key:
        prev = state.get(legacy_hash_key)
    return (isinstance(prev, str) and bool(prev) and cur == prev), cur


def _canonical_comm_rel(problem_dir: str) -> str:
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest = None
    if manifest is not None and manifest.protocol is not None and manifest.protocol.communicator_file:
        norm = _saved_relative_path(problem_dir, manifest.protocol.communicator_file)
        if norm:
            return norm
    if (Path(problem_dir) / "files" / "communicator.cpp").is_file():
        return "files/communicator.cpp"
    return "files/interactor.cpp"


def _matches_canonical_source(
    problem_dir: str,
    tool_input: dict[str, Any],
    canonical_rel: str,
) -> bool:
    """Return True when tool_input audited the canonical source file (not an unrelated path/inline snippet)."""
    source_path = tool_input.get("source_path")
    if isinstance(source_path, str) and source_path.strip():
        resolved = _saved_relative_path(problem_dir, source_path)
        if resolved != canonical_rel:
            return False
    code = tool_input.get("code")
    if isinstance(code, str):
        canonical_path = Path(problem_dir) / canonical_rel
        if canonical_path.is_file():
            try:
                disk_text = canonical_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return False
            if code.replace("\r\n", "\n") != disk_text.replace("\r\n", "\n"):
                return False
    return True


def _sync_solution_hashes(problem_dir: str, state: dict[str, Any]) -> None:
    """Invalidate cached build/audit/downstream gates if backing source file hashes drifted."""
    cur_sol = _file_sha256(problem_dir, "solutions/sol.cpp")
    sol_source_hash = state.get("sol_source_hash")
    sol_drifted = (
        isinstance(sol_source_hash, str) and bool(sol_source_hash) and cur_sol != sol_source_hash
    )
    if sol_drifted:
        state.update(
            {
                "sol_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "sol_source_hash": None,
                "sol_analyzed_hash": None,
                "std_audited_hash": None,
                "std_claimed_complexity": None,
            }
        )
        _set_source_hash(state, "solutions/sol.cpp", None)
    std_hash = state.get("std_audited_hash")
    if isinstance(std_hash, str) and std_hash and cur_sol != std_hash:
        state["std_audited"] = False
        state["std_audited_hash"] = None
    analyzed_hash = state.get("sol_analyzed_hash")
    if isinstance(analyzed_hash, str) and analyzed_hash and cur_sol != analyzed_hash:
        state["solution_analyzed"] = False
        state["sol_analyzed_hash"] = None

    cur_brute = _file_sha256(problem_dir, "solutions/brute.cpp")
    brute_source_hash = state.get("brute_source_hash")
    brute_drifted = (
        isinstance(brute_source_hash, str)
        and bool(brute_source_hash)
        and cur_brute != brute_source_hash
    )
    if brute_drifted:
        state.update(
            {
                "brute_built": False,
                "brute_audited": False,
                "brute_source_hash": None,
                "brute_audited_hash": None,
            }
        )
        _set_source_hash(state, "solutions/brute.cpp", None)
    brute_hash = state.get("brute_audited_hash")
    if isinstance(brute_hash, str) and brute_hash and cur_brute != brute_hash:
        state["brute_audited"] = False
        state["brute_audited_hash"] = None

    if sol_drifted or brute_drifted:
        _invalidate_downstream_in_memory(state)


def _state_problem_type(state: dict[str, Any]) -> str:
    ptype = state.get("problem_type")
    if isinstance(ptype, str) and ptype in {"standard", "spj", "interactive", "communication"}:
        return ptype
    return "interactive" if bool(state.get("interactive", False)) else "standard"


def _is_non_interactive(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _state_problem_type(state) not in {"interactive", "communication"}


def _is_interactive(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _state_problem_type(state) == "interactive"


def _is_protocol_problem(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _state_problem_type(state) in {"interactive", "communication"}


def _requires_external_interactor(state: dict[str, Any]) -> bool:
    ptype = _state_problem_type(state)
    if ptype == "interactive":
        return True
    if ptype == "communication":
        return bool(state.get("protocol_mode", "run_twice") != "grader_linked")
    return False


def _interactor_build_allowed(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _requires_external_interactor(state)


_NON_BRUTE_ORACLE_MODES = frozenset({"interactor_only", "reference_solutions"})


def _brute_required(state: dict[str, Any]) -> bool:
    mode = state.get("oracle_mode")
    return not (isinstance(mode, str) and mode in _NON_BRUTE_ORACLE_MODES)


def _brute_built_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _brute_required(state)) or bool(state.get("brute_built"))


def _validator_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if _is_protocol_problem(state, {}):
        return True
    accuracy = state.get("validator_accuracy")
    parsed = _finite_ratio(accuracy)
    return bool(state.get("validator_ready")) and parsed is not None and parsed >= 0.9


def _interactor_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if not _requires_external_interactor(state):
        return True
    if bool(state.get("interactor_ready")):
        return True
    return _interaction_scenarios_ready(state.get("interaction_scenarios", {}))


def _quality_gate_enabled(state: dict[str, Any], key: str, default: bool = True) -> bool:
    gates = state.get("quality_gates", {})
    return bool(gates.get(key, default)) if isinstance(gates, dict) else default


def _stress_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _quality_gate_enabled(state, "require_stress_passed")) or bool(
        state.get("stress_passed")
    )


def _validation_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if not _quality_gate_enabled(state, "require_validation_passed"):
        return True
    return bool(state.get("validation_passed")) and bool(state.get("statement_validated")) and bool(
        state.get("sample_files_validated")
    )


def _tests_verified_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _quality_gate_enabled(state, "require_tests_verified")) or bool(
        state.get("tests_verified")
    )


def _audit_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return bool(state.get("std_audited")) and (
        (not _brute_required(state)) or bool(state.get("brute_audited"))
    )


def _finite_ratio(value: Any) -> float | None:
    """Parse a JSON numeric ratio while rejecting bool, NaN, and out-of-range values."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    ratio = float(value)
    if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
        return None
    return ratio


def _interaction_scenarios_ready(interaction: Any) -> bool:
    if not isinstance(interaction, dict) or not bool(interaction.get("validated")):
        return False
    try:
        total = int(interaction.get("total", 0))
    except (TypeError, ValueError):
        return False
    accuracy = _finite_ratio(interaction.get("accuracy", 0))
    return total > 0 and accuracy is not None and accuracy >= 1.0


def _limit_ratio_threshold(state: dict[str, Any]) -> float | None:
    gates = state.get("quality_gates", {})
    if not isinstance(gates, dict):
        return 0.5
    return _finite_ratio(gates.get("min_limit_case_ratio", 0.5))


def _min_limit_ratio_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    required = _limit_ratio_threshold(state)
    if required is None:
        return False
    ratio_value = state.get("limit_case_ratio")
    if ratio_value is None:
        return True
    ratio = _finite_ratio(ratio_value)
    return ratio is not None and ratio >= required


def _required_verify_signal_ok(state: dict[str, Any], gate_key: str, signal_name: str) -> bool:
    if not _quality_gate_enabled(state, gate_key):
        return True
    signals = state.get("verify_signals", {})
    signal = signals.get(signal_name, {}) if isinstance(signals, dict) else {}
    return isinstance(signal, dict) and bool(signal.get("executed")) and bool(signal.get("passed"))


def _limit_semantics_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_limit_semantics", "limit_semantics")


def _wrong_solution_kill_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_wrong_solution_kill", "wrong_solution_kill")


def _validator_check_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_validator_check", "validator_check")


def _stress_prereq_core_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (
        bool(state.get("sol_built"))
        and _brute_built_ok(state, {})
        and bool(state.get("solution_analyzed"))
        and _audit_gate_ok(state, {})
        and _validator_gate_ok(state, {})
        and _interactor_gate_ok(state, {})
        and bool(state.get("generator_built"))
    )


def _manifest_checker(problem_dir: str) -> bool:
    try:
        return bool(manifest_uses_testlib_checker(load_manifest(problem_dir)))
    except (OSError, ValueError):
        return False


def _checker_build_prereq_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    if bool(state.get("stress_passed")):
        return True
    problem_dir = tool_input.get("problem_dir")
    return isinstance(problem_dir, str) and bool(problem_dir.strip()) and _manifest_checker(problem_dir) and _stress_prereq_core_ok(state, tool_input)


def _stress_spj_checker_ready_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    problem_dir = tool_input.get("problem_dir")
    if not isinstance(problem_dir, str) or not problem_dir.strip() or not _manifest_checker(problem_dir):
        return True
    return bool(state.get("checker_ready"))


def _has_generated_tests(state: dict[str, Any], _: dict[str, Any]) -> bool:
    try:
        return bool(state.get("tests_generated")) and int(state.get("generated_test_count", 0)) > 0
    except (TypeError, ValueError):
        return False


_DOWNSTREAM_INVALIDATING_TOOLS = frozenset(
    {
        "problem_create",
        "solution_build",
        "solution_analyze",
        "solution_audit_std",
        "solution_audit_brute",
        "validator_build",
        "generator_build",
        "checker_build",
        "interactor_build",
    }
)


def _invalidate_verify_only(problem_dir: str, state: dict[str, Any]) -> None:
    """Invalidate binary-dependent verification/packaging while preserving source-verified stages."""
    state.update(
        {
            "tests_verified": False,
            "verify_signals": {},
            "verify_dataset_fingerprint": None,
            "packaged": False,
        }
    )
    set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})


def _invalidate_downstream_in_memory(state: dict[str, Any]) -> None:
    """Pure in-memory downstream invalidation (safe for read-only inspections)."""
    state.update(
        {
            "stress_passed": False,
            "stress_completed_rounds": 0,
            "stress_total_rounds": 0,
            "statement_validated": False,
            "sample_files_validated": False,
            "validation_passed": False,
            "tests_generated": False,
            "generated_test_count": 0,
            "tests_verified": False,
            "limit_case_ratio": None,
            "verify_signals": {},
            "verify_dataset_fingerprint": None,
            "packaged": False,
        }
    )


def _invalidate_downstream(problem_dir: str, state: dict[str, Any]) -> None:
    """Discard results that depend on an upstream artifact or validation step."""
    _invalidate_downstream_in_memory(state)
    set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})


def _saved_relative_path(problem_dir: str, raw_path: Any) -> str | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path_text = raw_path.strip().replace("\\", "/")
    if not Path(path_text).is_absolute() and "/" not in path_text:
        path_text = CANONICAL_PROBLEM_FILES.get(path_text, path_text)
    root = Path(problem_dir).resolve()
    path = Path(path_text)
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        rel = candidate.relative_to(root).as_posix()
    except ValueError:
        try:
            Path(os.path.normcase(str(candidate))).relative_to(Path(os.path.normcase(str(root))))
        except ValueError:
            return None
        rel = Path(*candidate.parts[len(root.parts) :]).as_posix()
    if sys.platform in ("win32", "darwin"):
        low = rel.lower()
        canonical_lower = {
            ".autocode/manifest.json",
            "statements/tutorial.md",
            "statements/readme.md",
            "solutions/sol.cpp",
            "solutions/brute.cpp",
            "files/val.cpp",
            "files/gen.cpp",
            "files/checker.cpp",
            "files/interactor.cpp",
            "files/communicator.cpp",
            "files/grader.cpp",
            "files/problem.h",
        }
        if low in canonical_lower:
            return "statements/README.md" if low == "statements/readme.md" else low
    return rel


def _invalidate_for_file_save(problem_dir: str, state: dict[str, Any], tool_input: dict[str, Any]) -> None:
    relative = _saved_relative_path(problem_dir, tool_input.get("path"))
    if relative is None:
        return
    if relative == ".autocode/manifest.json":
        _invalidate_downstream(problem_dir, state)
        state.update(
            {
                "sol_built": False,
                "brute_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "sol_source_hash": None,
                "sol_analyzed_hash": None,
                "std_audited_hash": None,
                "brute_source_hash": None,
                "brute_audited_hash": None,
                "std_claimed_complexity": None,
                "validator_ready": False,
                "validator_accuracy": None,
                "generator_built": False,
                "checker_ready": False,
                "checker_accuracy": None,
                "interactor_ready": False,
                "interaction_scenarios": {},
                "statement_dirty": False,
                "source_hashes": {},
                "verify_dataset_fingerprint": None,
            }
        )
        return
    if relative == "statements/tutorial.md":
        # 题解仅影响打包与完整审计，不应清空已完成的对拍与终测状态。
        state["packaged"] = False
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
        return
    if relative == "statements/README.md":
        # 修改题面只需重新做题面/样例校验与审计/打包，不清空已通过的对拍结果，
        # 但标记 statement_dirty 以阻断未重新校验就打包。
        state.update(
            {
                "statement_validated": False,
                "validation_passed": False,
                "statement_dirty": True,
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
        return
    if relative == "tests" or relative.startswith("tests/"):
        # 修改 tests/ 文件需重新校验样例/终测，不清空对拍结果。
        state.update(
            {
                "sample_files_validated": False,
                "validation_passed": False,
                "tests_verified": False,
                "limit_case_ratio": None,
                "verify_signals": {},
                "verify_dataset_fingerprint": None,
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
        return
    if relative in {
        "solutions/sol.cpp",
        "solutions/brute.cpp",
        "files/val.cpp",
        "files/gen.cpp",
        "files/checker.cpp",
        "files/interactor.cpp",
    }:
        _invalidate_downstream(problem_dir, state)
    elif relative.startswith("files/"):
        # 公共头文件/辅助代码（如 problem.h / grader.cpp）变更影响整个下游产物。
        _invalidate_downstream(problem_dir, state)
    elif relative.startswith("solutions/"):
        # 其它解法（例如 role=wrong 错解或参考解）变更会使终测与错解杀伤信号过期。
        state.update(
            {
                "tests_verified": False,
                "verify_signals": {},
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
    if relative == "solutions/sol.cpp":
        state.update(
            {
                "sol_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "sol_source_hash": None,
                "sol_analyzed_hash": None,
                "std_audited_hash": None,
                "std_claimed_complexity": None,
            }
        )
        _set_source_hash(state, "solutions/sol.cpp", None)
    elif relative == "solutions/brute.cpp":
        state.update(
            {
                "brute_built": False,
                "brute_audited": False,
                "brute_source_hash": None,
                "brute_audited_hash": None,
            }
        )
        _set_source_hash(state, "solutions/brute.cpp", None)
    elif relative == "files/val.cpp":
        state.update({"validator_ready": False, "validator_accuracy": None})
        _set_source_hash(state, "files/val.cpp", None)
    elif relative == "files/gen.cpp":
        state["generator_built"] = False
        _set_source_hash(state, "files/gen.cpp", None)
    elif relative == "files/checker.cpp":
        state.update({"checker_ready": False, "checker_accuracy": None})
        _set_source_hash(state, "files/checker.cpp", None)
    custom_comm_files: set[str] = set()
    custom_grader_files: set[str] = set()
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest = None
    if manifest is not None and manifest.protocol is not None:
        if manifest.protocol.communicator_file:
            norm = _saved_relative_path(problem_dir, manifest.protocol.communicator_file)
            if norm:
                custom_comm_files.add(norm)
        for p_file in (manifest.protocol.grader_file, manifest.protocol.header_file):
            if p_file:
                norm = _saved_relative_path(problem_dir, p_file)
                if norm:
                    custom_grader_files.add(norm)

    if relative in ({"files/interactor.cpp", "files/communicator.cpp"} | custom_comm_files):
        _invalidate_downstream(problem_dir, state)
        state.update({"interactor_ready": False, "interaction_scenarios": {}})
        _set_source_hash(state, relative, None)
    elif relative in ({"files/grader.cpp", "files/problem.h"} | custom_grader_files):
        _invalidate_downstream(problem_dir, state)
        state.update(
            {
                "sol_built": False,
                "brute_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "sol_source_hash": None,
                "sol_analyzed_hash": None,
                "std_audited_hash": None,
                "brute_source_hash": None,
                "brute_audited_hash": None,
                "std_claimed_complexity": None,
            }
        )
        _set_source_hash(state, relative, None)
        _set_source_hash(state, "solutions/sol.cpp", None)
        _set_source_hash(state, "solutions/brute.cpp", None)


def _stress_rounds_complete(data: dict[str, Any]) -> bool:
    try:
        completed = int(data.get("completed_rounds"))
        total = int(data.get("total_rounds"))
    except (TypeError, ValueError):
        return False
    return total > 0 and completed == total


def _violation(gate: str, reason: str, next_action: str) -> Gate:
    def predicate(_: dict[str, Any], __: dict[str, Any]) -> bool:
        return False

    predicate.__name__ = f"always_false_{gate}"
    return predicate, reason


PRE_GATES: dict[str, list[tuple[str, Gate]]] = {
    "problem_build_all": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
    ],
    "solution_build": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
        ("sol_before_brute", (lambda s, i: i.get("solution_type") != "brute" or bool(s.get("sol_built")), "必须先构建标准解 sol，再构建 brute。")),
    ],
    "solution_analyze": [("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol，再进行复杂度分析。"))],
    "solution_audit_std": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。")),
    ],
    "solution_audit_brute": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。")),
        ("std_audited", (lambda s, i: bool(s.get("std_audited")), "必须先完成 solution_audit_std。")),
    ],
    "validator_select": [("validator_ready", (lambda s, i: bool(s.get("validator_ready")), "必须先完成 validator_build 才能选择校验器版本。"))],
    "validator_build": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze，再构建 validator。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute，再构建 validator。")),
    ],
    "interactor_build": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
        ("interactive", (_interactor_build_allowed, "只有交互题或非 grader_linked 通信题可运行 interactor_build。")),
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须运行 solution_analyze。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
    ],
    "generator_build": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须运行 solution_analyze。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
        ("validator", (_validator_gate_ok, "必须先完成 validator_build，并且 validator accuracy >= 0.9。")),
        ("interactor", (_interactor_gate_ok, "交互题与通信题（非 grader_linked）必须先完成 interactor_build 并可用。")),
    ],
    "stress_test_run": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须运行 solution_analyze。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
        ("validator", (_validator_gate_ok, "必须先完成 validator_build(accuracy >= 0.9)。")),
        ("interactor", (_interactor_gate_ok, "交互题与通信题（非 grader_linked）必须先完成 interactor_build 并可用。")),
        ("generator", (lambda s, i: bool(s.get("generator_built")), "必须先完成 generator_build。")),
        ("checker", (_stress_spj_checker_ready_ok, "SPJ 且 stress_comparison=checker 时需先完成 checker_build。")),
    ],
    "checker_build": [
        ("non_interactive", (_is_non_interactive, "交互题或通信题不应构建 checker，请使用 interactor_build / grader。")),
        ("prerequisites", (_checker_build_prereq_ok, "须先通过 stress_test_run；或在 SPJ checker 路径完成 stress 前置步骤。")),
    ],
    "problem_validate": [("stress", (_stress_required_gate_ok, "必须先通过 stress_test_run，再进行题面与样例验证。"))],
    "problem_generate_tests": [
        ("stress", (_stress_required_gate_ok, "必须先通过 stress_test_run。")),
        ("validation", (_validation_required_gate_ok, "必须先通过 problem_validate（题面与样例均通过）。")),
        ("interactor", (_interactor_gate_ok, "交互题与通信题（非 grader_linked）必须先完成 interactor_build 并可用。")),
    ],
    "problem_verify_tests": [("tests_generated", (_has_generated_tests, "必须先运行 problem_generate_tests 生成最终测试数据。"))],
    "problem_pack_polygon": [
        (
            "statement_fresh",
            (
                lambda s, i: (not _quality_gate_enabled(s, "require_validation_passed"))
                or (not bool(s.get("statement_dirty", False))),
                "题面已修改，必须重新通过 problem_validate 后再打包。",
            ),
        ),
        ("tests_generated", (_has_generated_tests, "必须先生成最终测试数据。")),
        ("tests_verified", (_tests_verified_required_gate_ok, "必须先通过 problem_verify_tests(passed=true)，再进行打包。")),
        ("limit_ratio", (_min_limit_ratio_gate_ok, "最终测试中的极限样例占比未达到 quality_gates.min_limit_case_ratio。")),
        ("limit_semantics", (_limit_semantics_gate_ok, "最终测试未通过 limit_semantics，不能打包。")),
        ("wrong_solution_kill", (_wrong_solution_kill_gate_ok, "最终测试未通过 wrong_solution_kill，不能打包。")),
        ("validator_check", (_validator_check_gate_ok, "最终测试未通过 validator_check，不能打包。")),
    ],
}


NEXT_ACTIONS: dict[str, str] = {
    "created": "调用 problem_create",
    "sol_before_brute": "调用 solution_build(solution_type='sol')",
    "sol_built": "调用 solution_build(solution_type='sol')",
    "brute_built": "调用 solution_build(solution_type='brute')",
    "solution_analyzed": "调用 solution_analyze",
    "std_audited": "调用 solution_audit_std",
    "audits": "调用 solution_audit_std（brute 模式下还需调用 solution_audit_brute）",
    "validator_ready": "调用 validator_build",
    "validator": "调用 validator_build 并确保 accuracy >= 0.9",
    "non_interactive": "交互题或通信题请使用 interactor_build / grader，不要调用 checker_build",
    "interactive": "交互题或非 grader_linked 通信题才可调用 interactor_build",
    "interactor": "调用 interactor_build 并通过交互场景检查",
    "generator": "调用 generator_build",
    "stress": "调用 stress_test_run 并完成全部轮次",
    "checker": "调用 checker_build（SPJ checker 路径）",
    "prerequisites": "完成 stress_test_run 或 SPJ checker 前置步骤",
    "validation": "调用 problem_validate 并通过题面、样例检查",
    "statement_fresh": "题面已修改，请重新调用 problem_validate",
    "tests_generated": "调用 problem_generate_tests",
    "tests_verified": "调用 problem_verify_tests 并通过所有质量信号",
    "limit_ratio": "补充 type=3/type=4 测试数据",
    "limit_semantics": "修正并重新验证极限/TLE 语义差异",
    "wrong_solution_kill": "启用并通过 wrong_solution_kill，或关闭对应 quality gate",
    "validator_check": "修正 validator 并重新运行 problem_verify_tests",
}


def _default_state(problem_dir: str) -> dict[str, Any]:
    root = Path(problem_dir)
    manifest = None
    manifest_error: str | None
    try:
        manifest = load_manifest(problem_dir)
        problem_type = manifest.problem_type if manifest else "standard"
        oracle_mode = manifest.oracle_mode if manifest else "brute"
        protocol_mode = manifest.protocol.mode if (manifest and manifest.protocol) else None
        interactive = bool(manifest.interactive) if manifest else False
        gates = manifest.quality_gates.model_dump(mode="json") if manifest else dict(DEFAULT_QUALITY_GATES)
    except (OSError, ValueError) as exc:
        problem_type = "standard"
        oracle_mode = "brute"
        protocol_mode = None
        interactive = False
        gates = dict(DEFAULT_QUALITY_GATES)
        manifest_error = str(exc)
    else:
        manifest_error = None
    state: dict[str, Any] = {
        "problem_dir": ".",
        "build_platform": None,
        "target_platforms": {},
        "source_hashes": {},
        "verify_dataset_fingerprint": None,
        "packaged_time_limit_ms": None,
        "created": root.exists()
        and (root / "files").exists()
        and (root / "solutions").exists()
        and manifest is not None,
        "problem_type": problem_type,
        "oracle_mode": oracle_mode,
        "protocol_mode": protocol_mode,
        "interactive": interactive,
        "sol_built": False,
        "brute_built": False,
        "solution_analyzed": False,
        "std_audited": False,
        "brute_audited": False,
        "sol_source_hash": None,
        "sol_analyzed_hash": None,
        "std_audited_hash": None,
        "brute_source_hash": None,
        "brute_audited_hash": None,
        "validator_ready": False,
        "validator_accuracy": None,
        "generator_built": False,
        "stress_passed": False,
        "stress_completed_rounds": 0,
        "stress_total_rounds": 0,
        "checker_ready": False,
        "checker_accuracy": None,
        "interactor_ready": False,
        "interaction_scenarios": {},
        "statement_validated": False,
        "statement_dirty": False,
        "sample_files_validated": False,
        "validation_passed": False,
        "tests_generated": False,
        "generated_test_count": 0,
        "tests_verified": False,
        "verify_signals": {},
        "packaged": (root / "problem.xml").exists(),
        "quality_gates": gates,
        "history": [],
    }
    if manifest_error:
        state["manifest_error"] = manifest_error
    return state


def load_workflow_state(problem_dir: str) -> dict[str, Any]:
    loaded = get_section(problem_dir, WORKFLOW)
    state = dict(loaded) if isinstance(loaded, dict) else _default_state(problem_dir)
    state["problem_dir"] = "."
    if not isinstance(state.get("source_hashes"), dict):
        state["source_hashes"] = {}
    if not isinstance(state.get("target_platforms"), dict):
        state["target_platforms"] = {}
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError) as exc:
        state["manifest_error"] = str(exc)
    else:
        state.pop("manifest_error", None)
        if manifest:
            state["problem_type"] = manifest.problem_type
            state["oracle_mode"] = manifest.oracle_mode
            state["protocol_mode"] = manifest.protocol.mode if manifest.protocol else None
            state["interactive"] = bool(manifest.interactive)
            state["quality_gates"] = manifest.quality_gates.model_dump(mode="json")
    _sync_solution_hashes(problem_dir, state)
    return state


def save_workflow_state(problem_dir: str, state: dict[str, Any]) -> None:
    normalized = dict(state)
    normalized["problem_dir"] = "."
    set_section(problem_dir, WORKFLOW, normalized)


def has_workflow_context(problem_dir: str) -> bool:
    """Return whether a directory has entered the managed AutoCode workflow.

    Bare tool calls in an arbitrary temporary directory remain useful for
    low-level diagnostics and backwards-compatible unit tests.  Once a
    manifest or workflow state exists, every subsequent call is subject to the
    authoritative gate engine.
    """
    return manifest_path(problem_dir).is_file() or runtime_file(problem_dir).is_file()


def preflight(
    tool_name: str,
    problem_dir: str,
    tool_input: dict[str, Any],
    *,
    strict_manifest: bool = True,
) -> list[GateViolation]:
    if tool_name == "problem_audit" and bool(tool_input.get("standalone")):
        return []
    state = load_workflow_state(problem_dir)
    violations: list[GateViolation] = []
    if strict_manifest and tool_name != "problem_create":
        if state.get("manifest_error"):
            violations.append(
                GateViolation(
                    "manifest",
                    f"manifest.json 无法读取或校验：{state['manifest_error']}",
                    "修复 manifest.json 后重试",
                )
            )
            return violations
        if not manifest_path(problem_dir).is_file():
            violations.append(
                GateViolation(
                    "manifest",
                    "受管工作流缺少 .autocode/manifest.json。",
                    "调用 problem_create 或恢复 manifest.json",
                )
            )
            return violations
    for gate_name, (predicate, reason) in PRE_GATES.get(tool_name, []):
        if not predicate(state, tool_input):
            violations.append(GateViolation(gate_name, reason, NEXT_ACTIONS.get(gate_name, reason)))
    return violations


def prepare_call(tool_name: str, problem_dir: str) -> None:
    """Persist invalidation that must happen before a long-running call."""
    if tool_name != "problem_generate_tests":
        return
    state = load_workflow_state(problem_dir)
    state["tests_verified"] = False
    state["packaged"] = False
    save_workflow_state(problem_dir, state)


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
                    "brute_source_hash": None,
                    "brute_audited_hash": None,
                }
            )
            invalidate_full = True
        elif rel == "files/gen.cpp":
            state["generator_built"] = False
            invalidate_full = True
        elif rel == "files/val.cpp":
            state.update({"validator_ready": False, "validator_accuracy": None})
            invalidate_full = True
        elif rel == "files/checker.cpp":
            state.update({"checker_ready": False, "checker_accuracy": None})
            invalidate_full = True
        elif rel in comm_targets:
            state.update({"interactor_ready": False, "interaction_scenarios": {}})
            invalidate_full = True
        elif isinstance(rel, str) and rel.startswith("files/"):
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
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif rel == "files/gen.cpp":
            pure, cur_gen = _is_pure_recompile(problem_dir, state, "files/gen.cpp")
            state["generator_built"] = True
            _set_source_hash(state, "files/gen.cpp", cur_gen)
            if not pure:
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif rel == "files/val.cpp":
            pure, cur_val = _is_pure_recompile(problem_dir, state, "files/val.cpp")
            _set_source_hash(state, "files/val.cpp", cur_val)
            if not pure:
                state.update({"validator_ready": False, "validator_accuracy": None})
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
                state.update({"interactor_ready": False, "interaction_scenarios": {}})
                invalidate_full = True
            else:
                invalidate_verify_only = True
        elif isinstance(rel, str) and rel.startswith("files/"):
            pure, cur_f = _is_pure_recompile(problem_dir, state, rel)
            _set_source_hash(state, rel, cur_f)
            if not pure:
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


def apply_result(problem_dir: str, tool_name: str, tool_input: dict[str, Any], success: bool, data: dict[str, Any]) -> dict[str, Any]:
    state = load_workflow_state(problem_dir)
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

    if tool_name in _DOWNSTREAM_INVALIDATING_TOOLS and not is_auxiliary_solution_build:
        if pure_recompile:
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
        elif not success:
            state["packaged"] = False
            set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
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
            "solution_analyze": ("solution_analyzed", "sol_analyzed_hash"),
            "validator_build": ("validator_ready", "validator_accuracy"),
            "solution_audit_std": ("std_audited", "std_audited_hash"),
            "solution_audit_brute": ("brute_audited", "brute_audited_hash"),
            "generator_build": ("generator_built",),
            "stress_test_run": ("stress_completed_rounds", "stress_total_rounds", "stress_passed"),
            "checker_build": ("checker_ready", "checker_accuracy"),
            "interactor_build": ("interactor_ready", "interaction_scenarios"),
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
                "sol_source_hash": None,
                "sol_analyzed_hash": None,
                "std_audited_hash": None,
                "brute_source_hash": None,
                "brute_audited_hash": None,
                "std_claimed_complexity": None,
                "validator_ready": False,
                "validator_accuracy": None,
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
    elif tool_name == "solution_analyze":
        solution_type = tool_input.get("solution_type", "sol")
        if solution_type == "sol" and _matches_canonical_source(
            problem_dir, tool_input, "solutions/sol.cpp"
        ):
            cur_sol = _file_sha256(problem_dir, "solutions/sol.cpp")
            state.update(
                {
                    "solution_analyzed": True,
                    "sol_analyzed_hash": cur_sol,
                    "std_audited": False,
                    "std_audited_hash": None,
                }
            )
            complexity = data.get("estimated_complexity") or data.get("final_complexity") or data.get("worst_case_complexity")
            if complexity is not None:
                state["std_complexity"] = complexity
            claimed = data.get("claimed_complexity") or tool_input.get("claimed_complexity")
            state["std_claimed_complexity"] = (
                claimed.strip() if isinstance(claimed, str) and claimed.strip() else None
            )
            if "recommended_stress_params" in data:
                state["recommended_stress_params"] = data["recommended_stress_params"]
    elif tool_name == "solution_audit_std":
        audit_passed = data.get("passed", True) is not False
        if not audit_passed:
            state["std_audited"] = False
            state["std_audited_hash"] = None
        elif _matches_canonical_source(problem_dir, tool_input, "solutions/sol.cpp"):
            state["std_audited"] = True
            state["std_audited_hash"] = _file_sha256(problem_dir, "solutions/sol.cpp")
            claimed = data.get("claimed_complexity") or tool_input.get("claimed_complexity")
            if isinstance(claimed, str) and claimed.strip():
                state["std_claimed_complexity"] = claimed.strip()
    elif tool_name == "solution_audit_brute":
        audit_passed = data.get("passed", True) is not False
        if not audit_passed:
            state["brute_audited"] = False
            state["brute_audited_hash"] = None
        elif _matches_canonical_source(problem_dir, tool_input, "solutions/brute.cpp"):
            state["brute_audited"] = True
            state["brute_audited_hash"] = _file_sha256(problem_dir, "solutions/brute.cpp")
            for key in ("brute_complexity", "recommended_stress_params"):
                if key in data:
                    state[key] = data[key]
    elif tool_name == "validator_build":
        accuracy = data.get("accuracy")
        state["validator_accuracy"] = accuracy
        parsed = _finite_ratio(accuracy)
        state["validator_ready"] = parsed is not None and parsed >= 0.9
        cur_plat = current_platform()
        _update_build_platform(state, cur_plat)
        _set_source_hash(state, "files/val.cpp", _file_sha256(problem_dir, "files/val.cpp"))
        _set_target_platform(state, "files/val.cpp", cur_plat)
    elif tool_name == "generator_build":
        cur_plat = current_platform()
        state["generator_built"] = True
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
        _update_build_platform(state, cur_plat)
        comm_rel = _canonical_comm_rel(problem_dir)
        _set_source_hash(state, comm_rel, _file_sha256(problem_dir, comm_rel))
        _set_target_platform(state, comm_rel, cur_plat)
    elif tool_name == "problem_pack_polygon":
        state["packaged"] = True
        tl_ms = data.get("time_limit_ms")
        if isinstance(tl_ms, int) and not isinstance(tl_ms, bool):
            state["packaged_time_limit_ms"] = tl_ms
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
            "pack_ready": (
                (not q_issues)
                and (not p_issues)
                and (not any_platform_mismatch)
                and (not stale_reasons)
            ),
        }

    return {
        "workflow_state_present": wf_present,
        "build_platform": build_plat,
        "current_platform": cur_plat,
        "platform_mismatch": any_platform_mismatch,
        "binary_status": binary_status,
        "stale_reasons": stale_reasons,
        "gate_summary": gate_summary,
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

    save_workflow_state(problem_dir, state)
    return {
        "reconciled": True,
        "actions": actions,
        "inspection": inspect_workspace_state(problem_dir),
    }
