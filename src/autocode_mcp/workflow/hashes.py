"""Source/platform SHA-256 tracking, path normalization, and invalidation primitives."""

from __future__ import annotations

import hashlib
import os
import platform as _platform
import sys
from pathlib import Path
from typing import Any

from ..runtime_store import AUDIT, get_section, set_section
from ..utils.communication_runner import safe_resolve_under_root
from .manifest import CANONICAL_PROBLEM_FILES, load_manifest

DEFAULT_QUALITY_GATES: dict[str, Any] = {
    "require_stress_passed": True,
    "require_validation_passed": True,
    "require_tests_verified": True,
    "require_limit_semantics": True,
    "require_wrong_solution_kill": False,
    "require_validator_check": True,
    "min_limit_case_ratio": 0.5,
}


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

    _sync_statement_hash(problem_dir, state)


def resolve_statement_rel(problem_dir: str) -> str:
    """Return the canonical relative path of the problem statement Markdown file."""
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest = None
    if manifest is not None and isinstance(manifest.statement_path, str) and manifest.statement_path.strip():
        norm_stmt = manifest.statement_path.strip().replace("\\", "/")
        safe_manifest_stmt = safe_resolve_under_root(problem_dir, norm_stmt)
        if safe_manifest_stmt is not None and Path(safe_manifest_stmt).is_file():
            return norm_stmt
    for candidate in ("statements/README.md", "statement.md"):
        safe = safe_resolve_under_root(problem_dir, candidate)
        if safe is not None and Path(safe).is_file():
            return candidate
    stmt_dir = safe_resolve_under_root(problem_dir, "statements")
    if stmt_dir is not None and Path(stmt_dir).is_dir():
        for p in sorted(Path(stmt_dir).glob("*.md")):
            safe = safe_resolve_under_root(problem_dir, f"statements/{p.name}")
            if safe is not None and Path(safe).is_file():
                return f"statements/{p.name}"
    return "statements/README.md"


def current_statement_sha256(problem_dir: str) -> str | None:
    """Return the current SHA-256 of the problem statement file, or None if missing."""
    return _file_sha256(problem_dir, resolve_statement_rel(problem_dir))


def is_statement_hash_drifted(
    problem_dir: str,
    audit_state: dict[str, Any] | None = None,
    workflow_state: dict[str, Any] | None = None,
) -> bool:
    """Return True only when a previously recorded statement_source_hash differs from disk."""
    cur_stmt = current_statement_sha256(problem_dir)
    if isinstance(workflow_state, dict):
        wf_hash = workflow_state.get("statement_source_hash")
        if isinstance(wf_hash, str) and wf_hash and cur_stmt != wf_hash:
            return True
    if isinstance(audit_state, dict):
        full_audit = audit_state.get("full_audit")
        if isinstance(full_audit, dict):
            audit_hash = full_audit.get("statement_source_hash")
            if isinstance(audit_hash, str) and audit_hash and cur_stmt != audit_hash:
                return True
    return False


def is_statement_drifted_for_audit(
    problem_dir: str,
    audit_state: dict[str, Any] | None = None,
    workflow_state: dict[str, Any] | None = None,
) -> bool:
    """Return True if the statement is marked dirty or its source hash drifted since validation/audit."""
    if isinstance(workflow_state, dict) and bool(workflow_state.get("statement_dirty", False)):
        return True
    return is_statement_hash_drifted(problem_dir, audit_state, workflow_state)


def _invalidate_statement_only_in_memory(state: dict[str, Any]) -> None:
    """Invalidate statement/audit/packaging gates while preserving stress_passed & tests_verified."""
    state.update(
        {
            "statement_validated": False,
            "validation_passed": False,
            "statement_dirty": True,
            "statement_source_hash": None,
            "packaged": False,
        }
    )


def _sync_statement_hash(problem_dir: str, state: dict[str, Any]) -> bool:
    """Detect direct disk edits to the statement file and invalidate statement/audit gates only."""
    stmt_rel = resolve_statement_rel(problem_dir)
    cur_stmt = _file_sha256(problem_dir, stmt_rel)
    prev_hash = state.get("statement_source_hash")
    if not (isinstance(prev_hash, str) and prev_hash):
        hashes = state.get("source_hashes")
        if isinstance(hashes, dict):
            prev_cand = hashes.get(stmt_rel)
            if isinstance(prev_cand, str) and prev_cand:
                prev_hash = prev_cand
    audit_hash: str | None = None
    audit_state = get_section(problem_dir, AUDIT)
    if isinstance(audit_state, dict):
        full_audit = audit_state.get("full_audit")
        if isinstance(full_audit, dict):
            raw_ah = full_audit.get("statement_source_hash")
            if isinstance(raw_ah, str) and raw_ah:
                audit_hash = raw_ah

    drifted = False
    if isinstance(prev_hash, str) and prev_hash and cur_stmt != prev_hash:
        drifted = True
    if isinstance(audit_hash, str) and audit_hash and cur_stmt != audit_hash:
        drifted = True
    if drifted:
        _invalidate_statement_only_in_memory(state)
    return drifted

