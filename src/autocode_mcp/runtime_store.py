"""Single source of truth for problem-dir runtime byproducts.

All non-problem runtime artifacts are consolidated into one hidden file
``<problem_dir>/.autocode/runtime.json`` with top-level section keys:

- ``workflow``: former ``.autocode-workflow/state.json`` (verify signals, audit
  gates, complexity context, etc.)
- ``test_manifest``: former ``tests/.autocode_tests_manifest.json``
- ``generate_checkpoint``: former ``tests/.autocode_generate_state.json``
- ``audit``: former ``full_audit`` / ``full_audit_passed`` nested in the
  workflow state, surfaced as its own section.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

RUNTIME_DIR_NAME = ".autocode"
RUNTIME_FILE_NAME = "runtime.json"

WORKFLOW = "workflow"
TEST_MANIFEST = "test_manifest"
GENERATE_CHECKPOINT = "generate_checkpoint"
AUDIT = "audit"


def runtime_file(problem_dir: str | os.PathLike[str]) -> Path:
    """Path to the consolidated ``runtime.json`` for ``problem_dir``."""
    return Path(problem_dir) / RUNTIME_DIR_NAME / RUNTIME_FILE_NAME


def load_runtime(problem_dir: str | os.PathLike[str]) -> dict[str, Any]:
    """Load the full runtime document; returns ``{}`` if absent or corrupt."""
    path = runtime_file(problem_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_runtime(problem_dir: str | os.PathLike[str], data: dict[str, Any]) -> None:
    """Write the full runtime document, creating ``.autocode/`` as needed.

    Best-effort: a blocked/unwritable ``.autocode`` (e.g. a regular file where the
    directory should be) must not break the caller, mirroring the previous
    ``.autocode-workflow/state.json`` write semantics.
    """
    path = runtime_file(problem_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def get_section(problem_dir: str | os.PathLike[str], key: str) -> Any:
    """Return one top-level section, or ``None`` when missing."""
    return load_runtime(problem_dir).get(key)


def set_section(problem_dir: str | os.PathLike[str], key: str, value: Any) -> None:
    """Overwrite one top-level section."""
    data = load_runtime(problem_dir)
    data[key] = value
    save_runtime(problem_dir, data)


def update_section(problem_dir: str | os.PathLike[str], key: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Read a section (defaulting to ``{}``), merge ``patch``, persist, return it."""
    data = load_runtime(problem_dir)
    section = data.get(key)
    if not isinstance(section, dict):
        section = {}
    section.update(patch)
    data[key] = section
    save_runtime(problem_dir, data)
    return section
