"""Claude hook compatibility wrappers for the MCP workflow state store."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from autocode_mcp.runtime_store import RUNTIME_DIR_NAME, RUNTIME_FILE_NAME  # noqa: E402
from autocode_mcp.workflow.enforcement import (  # noqa: E402
    DEFAULT_QUALITY_GATES,
    _default_state,
    load_workflow_state,
    save_workflow_state,
)
from autocode_mcp.workflow.manifest import MANIFEST_NAME  # noqa: E402

MANIFEST_FILE_NAME = MANIFEST_NAME


def runtime_file(problem_dir: str) -> Path:
    return Path(problem_dir) / RUNTIME_DIR_NAME / RUNTIME_FILE_NAME


def manifest_file(problem_dir: str) -> Path:
    return Path(problem_dir) / RUNTIME_DIR_NAME / MANIFEST_FILE_NAME


def _load_runtime(problem_dir: str) -> dict[str, Any]:
    path = runtime_file(problem_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_runtime(problem_dir: str, data: dict[str, Any]) -> None:
    path = runtime_file(problem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_manifest(problem_dir: str) -> dict[str, Any]:
    path = manifest_file(problem_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _extract_quality_gates(manifest: dict[str, Any]) -> dict[str, Any]:
    configured = manifest.get("quality_gates") if isinstance(manifest, dict) else {}
    configured = configured if isinstance(configured, dict) else {}
    gates = dict(DEFAULT_QUALITY_GATES)
    for key in DEFAULT_QUALITY_GATES:
        if key in configured:
            gates[key] = configured[key]
    try:
        gates["min_limit_case_ratio"] = min(1.0, max(0.0, float(gates["min_limit_case_ratio"])))
    except (TypeError, ValueError):
        gates["min_limit_case_ratio"] = 0.5
    return gates


def infer_state(problem_dir: str) -> dict[str, Any]:
    return _default_state(problem_dir)


def load_state(problem_dir: str) -> dict[str, Any]:
    return load_workflow_state(problem_dir)


def save_state(problem_dir: str, state: dict[str, Any]) -> None:
    save_workflow_state(problem_dir, state)
