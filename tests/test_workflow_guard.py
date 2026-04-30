"""Workflow guard hook tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path("scripts/workflow_guard.py")


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


def test_pre_tool_denies_generator_when_validator_accuracy_absent(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "interactive": False,
        "sol_built": True,
        "brute_built": True,
        "validator_ready": True,
        "validator_accuracy": None,
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


def test_pre_tool_denies_stress_when_validator_accuracy_absent(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "interactive": False,
        "sol_built": True,
        "brute_built": True,
        "validator_ready": True,
        "validator_accuracy": None,
        "generator_built": True,
    }
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__stress_test_run",
        "tool_input": {"problem_dir": str(problem_dir)},
    }

    exit_code = module.pre_tool(payload)
    captured = capsys.readouterr().out

    assert exit_code == 0
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_pre_tool_denies_interactive_generator_before_interactor(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    (problem_dir / "autocode.json").write_text('{"interactive": true}', encoding="utf-8")
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "interactive": True,
        "sol_built": True,
        "brute_built": True,
        "interactor_ready": False,
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
    assert "interactor_build" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_pre_tool_denies_pack_before_tests_verified(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "sol_built": True,
        "brute_built": True,
        "validator_ready": True,
        "validator_accuracy": 1.0,
        "generator_built": True,
        "stress_passed": True,
        "checker_ready": False,
        "validation_passed": True,
        "tests_generated": True,
        "generated_test_count": 3,
        "tests_verified": False,
        "packaged": False,
    }
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__problem_pack_polygon",
        "tool_input": {"problem_dir": str(problem_dir)},
    }

    exit_code = module.pre_tool(payload)
    captured = capsys.readouterr().out

    assert exit_code == 0
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "problem_verify_tests" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_post_tool_marks_tests_verified(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__problem_verify_tests",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {"passed": True},
            }
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["tests_verified"] is True


def test_post_tool_does_not_mark_validator_ready_without_accuracy(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__validator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {},
            }
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["validator_ready"] is False


def test_post_tool_clears_tests_verified_after_regeneration(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = module.infer_state(str(problem_dir))
    state["tests_verified"] = True
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__problem_generate_tests",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {"generated_tests": [1, 2]},
            }
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["tests_generated"] is True
    assert state["tests_verified"] is False
