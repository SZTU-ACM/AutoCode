"""Safe JSON log spilling and `.autocode/` containment helpers."""

from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _is_symlink_or_escapes(root: Path, target: Path) -> bool:
    """Return True if ``target`` or any existing ancestor up to ``root`` is a symlink or escapes ``root``."""
    try:
        real_root = root.resolve()
    except OSError:
        return True
    cur = target
    while True:
        if cur.is_symlink():
            return True
        if cur == root or cur.parent == cur:
            break
        cur = cur.parent
    try:
        target.resolve().relative_to(real_root)
    except (OSError, ValueError):
        return True
    return False


def safe_prepare_autocode_subdir(problem_dir: str, subdir_name: str) -> Path | None:
    """Safely create and return ``<problem_dir>/.autocode/<subdir_name>`` without following symlinks."""
    if (
        not problem_dir
        or not os.path.isdir(problem_dir)
        or not isinstance(subdir_name, str)
        or not subdir_name
        or subdir_name in (".", "..")
        or "/" in subdir_name
        or "\\" in subdir_name
        or "\x00" in subdir_name
    ):
        return None
    root = Path(problem_dir)
    autocode_dir = root / ".autocode"
    target_dir = autocode_dir / subdir_name
    if _is_symlink_or_escapes(root, autocode_dir) or _is_symlink_or_escapes(root, target_dir):
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    if _is_symlink_or_escapes(root, target_dir):
        return None
    return target_dir


def safe_spill_json(
    problem_dir: str | None,
    prefix: str,
    payload: Any,
    *,
    allow_workspace: bool = True,
    raw_json: str | None = None,
) -> str | None:
    """Safely persist JSON diagnostics under ``.autocode/logs/`` (or secure temp file fallback).

    - Never follows symlinks escaping ``problem_dir``.
    - Appends a random hex suffix to prevent collisions on coarse Windows timers or concurrent calls.
    - When ``allow_workspace=False`` (e.g. ``problem_audit(standalone=True)``), never touches ``problem_dir``.
    """
    safe_prefix = re.sub(r"[^A-Za-z0-9_-]", "_", prefix).strip("_") or "log"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    rand = secrets.token_hex(4)
    filename = f"{safe_prefix}_{ts}_{rand}.json"
    serialized = raw_json if raw_json is not None else json.dumps(payload, ensure_ascii=False, indent=2)

    if allow_workspace and isinstance(problem_dir, str) and problem_dir.strip():
        logs_dir = safe_prepare_autocode_subdir(problem_dir, "logs")
        if logs_dir is not None:
            out_path = logs_dir / filename
            if not out_path.is_symlink():
                try:
                    out_path.write_text(serialized, encoding="utf-8")
                    return f".autocode/logs/{filename}"
                except OSError:
                    pass

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"autocode_{safe_prefix}_",
            suffix=".json",
            delete=False,
        ) as tmp_f:
            tmp_f.write(serialized)
            return tmp_f.name
    except OSError:
        return None
