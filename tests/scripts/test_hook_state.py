"""Unit tests for hook state persistence (scripts/hook_state.py)."""

from __future__ import annotations

import json
from pathlib import Path

from hook_state import _extract_quality_gates, infer_state, load_state, save_state


def _write_manifest(problem_dir: Path, **manifest: object) -> None:
    (problem_dir / ".autocode").mkdir(parents=True, exist_ok=True)
    (problem_dir / ".autocode" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_infer_state_created_requires_valid_manifest(tmp_path):
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    assert infer_state(str(problem_dir))["created"] is False
    _write_manifest(problem_dir, problem_name="P", interactive=False)
    assert infer_state(str(problem_dir))["created"] is True


def test_save_load_state_roundtrip(tmp_path):
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = infer_state(str(problem_dir))
    state["sol_built"] = True
    save_state(str(problem_dir), state)
    loaded = load_state(str(problem_dir))
    assert loaded["sol_built"] is True


def test_extract_quality_gates_applies_overrides():
    gates = _extract_quality_gates(
        {"quality_gates": {"require_tests_verified": False, "min_limit_case_ratio": 0.8}}
    )
    assert gates["require_tests_verified"] is False
    assert gates["min_limit_case_ratio"] == 0.8
    # unset keys fall back to defaults
    assert gates["require_stress_passed"] is True
