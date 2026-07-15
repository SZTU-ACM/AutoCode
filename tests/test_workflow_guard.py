"""Workflow guard hook tests."""

from __future__ import annotations

import importlib.util
import io
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


def write_manifest(problem_dir: Path, *, interactive: bool = False, quality_gates: dict | None = None) -> None:
    manifest = {
        "schema_version": "1.0",
        "problem_name": "Test Problem",
        "interactive": interactive,
    }
    if quality_gates is not None:
        manifest["quality_gates"] = quality_gates
    (problem_dir / ".autocode").mkdir(parents=True, exist_ok=True)
    (problem_dir / ".autocode" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


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


def test_post_tool_parses_prefixed_content_text_result(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__solution_build",
        "tool_input": {"problem_dir": str(problem_dir), "solution_type": "sol"},
        "tool_response": {
            "content": [
                {
                    "type": "text",
                    "text": 'Tool result:\n{"success": true, "data": {"message": "Successfully built sol"}}',
                }
            ]
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["sol_built"] is True
    assert state["history"][-1]["success"] is True


def test_pre_tool_denies_malformed_payload(monkeypatch, capsys):
    module = load_module()
    raw = '{"tool_name":"mcp__autocode__problem_pack_polygon","tool_input":{"problem_dir":"C:\\\\tmp\\\\p"'
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(raw))

    payload = module.load_payload()
    exit_code = module.pre_tool(payload)
    captured = capsys.readouterr().out

    assert exit_code == 0
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "malformed" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_post_tool_uses_last_result_object_not_prefixed_decoy(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    state = module.infer_state(str(problem_dir))
    state["generator_built"] = True
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__generator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "content": [
                {
                    "type": "text",
                    "text": 'log {"success": true}\n{"success": false, "error": "compile failed"}',
                }
            ]
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["generator_built"] is False


def test_post_tool_parses_snake_case_structured_content(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__solution_build",
        "tool_input": {"problem_dir": str(problem_dir), "solution_type": "brute"},
        "tool_response": {
            "structured_content": {
                "success": True,
                "data": {"message": "Successfully built brute"},
            }
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["brute_built"] is True


def test_post_tool_parses_root_content_block_list_result(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__generator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": [
            {
                "type": "text",
                "text": '{"success": true, "data": {"message": "Generator built"}}',
            }
        ],
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["generator_built"] is True


def test_post_tool_recovers_success_from_malformed_tool_response_string(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    payload = {
        "tool_name": "mcp__autocode__generator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": '{"success":true,"data":{"semantic_check":{"hint":"bad\udcba"',
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["generator_built"] is True


def test_load_payload_recovers_malformed_post_tool_json(tmp_path, monkeypatch):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    raw = json.dumps(
        {
            "tool_name": "mcp__autocode__generator_build",
            "tool_input": {"problem_dir": str(problem_dir)},
            "tool_response": {
                "structuredContent": {
                    "success": True,
                    "data": {},
                }
            },
        }
    ).replace('"data": {}', '"data": {unquoted: "value"}')
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(raw))

    payload = module.load_payload()
    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert payload["tool_name"] == "mcp__autocode__generator_build"
    assert payload["tool_input"]["problem_dir"] == str(problem_dir)
    assert state["generator_built"] is True


def test_post_tool_marks_interactor_ready_from_scenarios(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir, interactive=True)

    payload = {
        "tool_name": "mcp__autocode__interactor_build",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {
                    "pass_rate": 0.0,
                    "fail_rate": 0.0,
                    "interaction_scenarios": {
                        "validated": True,
                        "accuracy": 1.0,
                        "total": 2,
                    },
                    "scenario_accuracy": 1.0,
                },
            }
        },
    }

    exit_code = module.post_tool(payload)
    state = module.load_state(str(problem_dir))

    assert exit_code == 0
    assert state["interactor_ready"] is True
    assert state["interaction_scenarios"]["accuracy"] == 1.0


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
    (problem_dir / ".autocode").mkdir(parents=True, exist_ok=True)
    (problem_dir / ".autocode" / "manifest.json").write_text('{"interactive": true}', encoding="utf-8")
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "interactive": True,
        "sol_built": True,
        "brute_built": True,
        "solution_analyzed": True,
        "std_audited": True,
        "brute_audited": True,
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


def test_pre_tool_allows_interactive_generator_with_scripted_interactor(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    (problem_dir / ".autocode").mkdir(parents=True, exist_ok=True)
    (problem_dir / ".autocode" / "manifest.json").write_text('{"interactive": true}', encoding="utf-8")
    state = {
        "problem_dir": str(problem_dir),
        "created": True,
        "interactive": True,
        "sol_built": True,
        "brute_built": True,
        "solution_analyzed": True,
        "std_audited": True,
        "brute_audited": True,
        "interactor_ready": False,
        "interaction_scenarios": {
            "validated": True,
            "accuracy": 1.0,
            "total": 2,
        },
    }
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__generator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
    }

    exit_code = module.pre_tool(payload)
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert captured.strip() == ""


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


def test_post_tool_failed_generate_tests_clears_verified_and_generated(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    state = module.infer_state(str(problem_dir))
    state["tests_verified"] = True
    state["tests_generated"] = True
    state["generated_test_count"] = 7
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__problem_generate_tests",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": False,
                "data": {},
            }
        },
    }

    module.post_tool(payload)
    state = module.load_state(str(problem_dir))
    assert state["tests_verified"] is False
    assert state["tests_generated"] is False
    assert state["generated_test_count"] == 0


def test_post_tool_rolls_back_validator_ready_on_failure(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    state = module.infer_state(str(problem_dir))
    state["validator_ready"] = True
    state["validator_accuracy"] = 1.0
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__validator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {"structuredContent": {"success": False, "data": {}}},
    }
    module.post_tool(payload)
    state = module.load_state(str(problem_dir))
    assert state["validator_ready"] is False
    assert state["validator_accuracy"] is None


def test_infer_state_created_requires_valid_manifest(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)

    state_without_manifest = module.infer_state(str(problem_dir))
    assert state_without_manifest["created"] is False

    write_manifest(problem_dir)
    state_with_manifest = module.infer_state(str(problem_dir))
    assert state_with_manifest["created"] is True


def test_pre_tool_enforces_solution_analyze_before_validator_build(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    state = module.infer_state(str(problem_dir))
    state["created"] = True
    state["sol_built"] = True
    state["brute_built"] = True
    state["solution_analyzed"] = False
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__validator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
    }
    module.pre_tool(payload)
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "solution_analyze" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_pre_tool_enforces_solution_audits_before_validator_build(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    state = module.infer_state(str(problem_dir))
    state["created"] = True
    state["sol_built"] = True
    state["brute_built"] = True
    state["solution_analyzed"] = True
    state["std_audited"] = False
    state["brute_audited"] = False
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__validator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
    }
    module.pre_tool(payload)
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "solution_audit_std" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_pre_tool_enforces_solution_audits_before_generator_build(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    state = module.infer_state(str(problem_dir))
    state["created"] = True
    state["sol_built"] = True
    state["brute_built"] = True
    state["solution_analyzed"] = True
    state["validator_ready"] = True
    state["validator_accuracy"] = 1.0
    state["std_audited"] = True
    state["brute_audited"] = False
    module.save_state(str(problem_dir), state)
    payload = {
        "tool_name": "mcp__autocode__generator_build",
        "tool_input": {"problem_dir": str(problem_dir)},
    }
    module.pre_tool(payload)
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "solution_audit_brute" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_pre_tool_pack_respects_quality_gate_override(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(
        problem_dir,
        quality_gates={
            "require_stress_passed": True,
            "require_validation_passed": True,
            "require_tests_verified": False,
            "require_limit_semantics": False,
            "require_wrong_solution_kill": False,
            "require_validator_check": False,
            "min_limit_case_ratio": 0.5,
        },
    )
    state = module.infer_state(str(problem_dir))
    state["tests_generated"] = True
    state["generated_test_count"] = 1
    state["tests_verified"] = False
    state["verify_signals"] = {}
    module.save_state(str(problem_dir), state)

    payload = {
        "tool_name": "mcp__autocode__problem_pack_polygon",
        "tool_input": {"problem_dir": str(problem_dir)},
    }
    exit_code = module.pre_tool(payload)
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert captured.strip() == ""


def test_post_tool_verify_tests_applies_min_limit_case_ratio(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(
        problem_dir,
        quality_gates={
            "require_stress_passed": True,
            "require_validation_passed": True,
            "require_tests_verified": True,
            "min_limit_case_ratio": 0.8,
        },
    )
    payload = {
        "tool_name": "mcp__autocode__problem_verify_tests",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {
                    "passed": True,
                    "results": {
                        "limit_ratio": {
                            "limit_case_ratio": 0.5,
                        }
                    },
                },
            }
        },
    }
    module.post_tool(payload)
    state = module.load_state(str(problem_dir))
    assert state["tests_verified"] is False
    assert state["limit_case_ratio"] == 0.5


def test_post_tool_verify_tests_persists_quality_signals_and_history(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    payload = {
        "tool_name": "mcp__autocode__problem_verify_tests",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {
                    "passed": True,
                    "quality_signals": {
                        "limit_semantics": {"executed": True, "passed": True},
                        "wrong_solution_kill": {"executed": True, "passed": True},
                        "validator_check": {"executed": True, "passed": True},
                    },
                },
            }
        },
    }
    module.post_tool(payload)
    state = module.load_state(str(problem_dir))
    assert state["verify_signals"]["limit_semantics"]["passed"] is True
    assert isinstance(state.get("history"), list)
    assert state["history"][-1]["tool"] == "problem_verify_tests"


def test_pre_tool_pack_denies_when_required_verify_signal_missing(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(problem_dir)
    state = module.infer_state(str(problem_dir))
    state["tests_generated"] = True
    state["generated_test_count"] = 2
    state["tests_verified"] = True
    state["verify_signals"] = {"limit_semantics": {"executed": True, "passed": False}}
    module.save_state(str(problem_dir), state)
    payload = {
        "tool_name": "mcp__autocode__problem_pack_polygon",
        "tool_input": {"problem_dir": str(problem_dir)},
    }
    module.pre_tool(payload)
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "limit_semantics" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_pre_tool_pack_denies_when_limit_ratio_below_gate(tmp_path, capsys):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "solutions").mkdir(parents=True)
    write_manifest(
        problem_dir,
        quality_gates={
            "require_stress_passed": True,
            "require_validation_passed": True,
            "require_tests_verified": False,
            "min_limit_case_ratio": 0.8,
        },
    )
    state = module.infer_state(str(problem_dir))
    state["tests_generated"] = True
    state["generated_test_count"] = 2
    state["tests_verified"] = False
    state["limit_case_ratio"] = 0.5
    module.save_state(str(problem_dir), state)
    payload = {
        "tool_name": "mcp__autocode__problem_pack_polygon",
        "tool_input": {"problem_dir": str(problem_dir)},
    }
    module.pre_tool(payload)
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "min_limit_case_ratio" in parsed["hookSpecificOutput"]["permissionDecisionReason"]


def test_post_tool_solution_analyze_writes_std_complexity_and_stress_params(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    write_manifest(problem_dir)

    payload = {
        "tool_name": "mcp__autocode__solution_analyze",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {
                    "estimated_complexity": "O(n log n)",
                    "recommended_stress_params": [{"trials": 500, "n_max": 80}],
                },
            }
        },
    }
    module.post_tool(payload)
    state = module.load_state(str(problem_dir))
    assert state["std_complexity"] == "O(n log n)"
    assert state["recommended_stress_params"] == [{"trials": 500, "n_max": 80}]


def test_post_tool_solution_audit_brute_writes_brute_complexity(tmp_path):
    module = load_module()
    problem_dir = tmp_path / "problem"
    (problem_dir / "files").mkdir(parents=True)
    write_manifest(problem_dir)

    payload = {
        "tool_name": "mcp__autocode__solution_audit_brute",
        "tool_input": {"problem_dir": str(problem_dir)},
        "tool_response": {
            "structuredContent": {
                "success": True,
                "data": {
                    "brute_complexity": "O(2^n)",
                    "recommended_stress_params": {"n_max": 8, "trials": 600},
                },
            }
        },
    }
    module.post_tool(payload)
    state = module.load_state(str(problem_dir))
    assert state["brute_complexity"] == "O(2^n)"
    assert state["recommended_stress_params"] == {"n_max": 8, "trials": 600}
