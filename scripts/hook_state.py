"""Hook workflow state read/write.

State + manifest persistence for the workflow guard. Gate configuration
(``DEFAULT_QUALITY_GATES``) is owned by ``hook_gates`` and imported lazily here
to avoid a circular import.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_DIR_NAME = ".autocode-workflow"
STATE_FILE_NAME = "state.json"
MANIFEST_FILE_NAME = "autocode.json"


def state_file(problem_dir: str) -> Path:
    return Path(problem_dir) / STATE_DIR_NAME / STATE_FILE_NAME


def manifest_file(problem_dir: str) -> Path:
    return Path(problem_dir) / MANIFEST_FILE_NAME


def load_manifest(problem_dir: str) -> dict[str, Any]:
    path = manifest_file(problem_dir)
    if not path.exists():
        return {}
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def _is_manifest_valid_for_created(manifest: dict[str, Any]) -> bool:
    problem_name = manifest.get("problem_name")
    return isinstance(problem_name, str) and bool(problem_name.strip())


def _extract_quality_gates(manifest: dict[str, Any]) -> dict[str, Any]:
    from hook_gates import DEFAULT_QUALITY_GATES

    configured = manifest.get("quality_gates")
    if not isinstance(configured, dict):
        configured = {}
    gates = dict(DEFAULT_QUALITY_GATES)
    for key in DEFAULT_QUALITY_GATES:
        if key in configured:
            gates[key] = configured[key]
    try:
        ratio = float(gates.get("min_limit_case_ratio", 0.5))
    except (TypeError, ValueError):
        ratio = 0.5
    gates["min_limit_case_ratio"] = min(1.0, max(0.0, ratio))
    return gates


def infer_state(problem_dir: str) -> dict[str, Any]:
    root = Path(problem_dir)
    manifest = load_manifest(problem_dir)
    is_interactive = bool(manifest.get("interactive", False))
    return {
        "problem_dir": str(root),
        "created": (
            root.exists()
            and (root / "files").exists()
            and (root / "solutions").exists()
            and _is_manifest_valid_for_created(manifest)
        ),
        "interactive": is_interactive,
        "sol_built": False,
        "brute_built": False,
        "solution_analyzed": False,
        "std_audited": False,
        "brute_audited": False,
        "validator_ready": False,
        "validator_accuracy": None,
        "generator_built": False,
        "stress_passed": False,
        "stress_completed_rounds": 0,
        "stress_total_rounds": 0,
        "checker_ready": False,
        "checker_accuracy": None,
        "interactor_ready": False,
        "statement_validated": False,
        "sample_files_validated": False,
        "validation_passed": False,
        "tests_generated": False,
        "generated_test_count": 0,
        "tests_verified": False,
        "verify_signals": {},
        "packaged": (root / "problem.xml").exists(),
        "quality_gates": _extract_quality_gates(manifest),
        "history": [],
    }


def load_state(problem_dir: str) -> dict[str, Any]:
    path = state_file(problem_dir)
    if path.exists():
        try:
            loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = infer_state(problem_dir)
    else:
        loaded = infer_state(problem_dir)
    manifest = load_manifest(problem_dir)
    loaded["interactive"] = bool(manifest.get("interactive", loaded.get("interactive", False)))
    loaded["quality_gates"] = _extract_quality_gates(manifest)
    return loaded


def save_state(problem_dir: str, state: dict[str, Any]) -> None:
    path = state_file(problem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
