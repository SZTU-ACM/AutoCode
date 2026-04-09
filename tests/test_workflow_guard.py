"""Workflow guard hook tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path("hooks/workflow_guard.py")


def load_module():
    spec = importlib.util.spec_from_file_location("workflow_guard", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_pre_tool_denies_generator_before_validator(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "sol_built": True,
        "brute_built": True,
        "validator_ready": False,
        "generator_built": False,
        "stress_passed": False,
        "checker_ready": False,
        "tests_generated": False,
        "packaged": False,
    }
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__generator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
    }

    exit_code = module.pre_tool(payload)
    captured = capsys.readouterr().out

    assert exit_code == 0
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_post_tool_marks_stress_passed(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__stress_test_run",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {"completed_rounds": 1000, "total_rounds": 1000},
            }
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["stress_passed"] is True
