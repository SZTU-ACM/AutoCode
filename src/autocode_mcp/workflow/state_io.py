"""Workflow state loading, saving, and default initialization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..runtime_store import WORKFLOW, get_section, runtime_file, set_section
from .hashes import DEFAULT_QUALITY_GATES, _sync_solution_hashes
from .manifest import load_manifest, manifest_path


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
        "statement_source_hash": None,
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

