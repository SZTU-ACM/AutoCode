import json
from pathlib import Path

import pytest

import autocode_mcp.tools.test_verify as tv
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.workflow import default_manifest, save_manifest
from autocode_mcp.workflow.models import AutoCodeManifest


def _write_basic_problem(tmp_path: Path) -> Path:
    problem_dir = tmp_path / "p"
    (problem_dir / "statements").mkdir(parents=True)
    (problem_dir / "solutions").mkdir()
    (problem_dir / "tests").mkdir()
    (problem_dir / ".autocode-workflow").mkdir()
    (problem_dir / "statements" / "README.md").write_text(
        "# P\n\n## 样例\n\n### 样例输入 #1\n```text\n1\n```\n\n### 样例输出 #1\n```text\n1\n```\n",
        encoding="utf-8",
    )
    (problem_dir / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n" + "This tutorial contains enough evidence about approach and complexity. " * 3,
        encoding="utf-8",
    )
    (problem_dir / "solutions" / "sol.cpp").write_text(
        "#include <bits/stdc++.h>\nusing namespace std;\nint main(){int n;cin>>n;cout<<n;}\n",
        encoding="utf-8",
    )
    for i, _type_param in enumerate(["1", "2", "3", "4"], 1):
        (problem_dir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (problem_dir / "tests" / f"{i:02d}.ans").write_text(f"{i}\n", encoding="utf-8")
    (problem_dir / "tests" / ".autocode_tests_manifest.json").write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "in_file": f"{i:02d}.in",
                        "ans_file": f"{i:02d}.ans",
                        "type_param": type_param,
                        "group": "g",
                        "purpose": f"type {type_param}",
                    }
                    for i, type_param in enumerate(["1", "2", "3", "4"], 1)
                ]
            }
        ),
        encoding="utf-8",
    )
    save_manifest(str(problem_dir), default_manifest("P"))
    (problem_dir / ".autocode-workflow" / "state.json").write_text(
        json.dumps(
            {
                "tests_verified": True,
                "verify_signals": {
                    "limit_semantics": {"executed": True, "passed": True},
                    "validator_check": {"executed": True, "passed": True},
                    "wrong_solution_kill": {"executed": True, "passed": True},
                    "answer_consistency": {"executed": True, "passed": True},
                },
            }
        ),
        encoding="utf-8",
    )
    return problem_dir


def test_manifest_accepts_audit_and_structured_constraints():
    manifest = AutoCodeManifest(
        problem_name="x",
        constraints={
            "n": {"min": 1, "max": 200000, "aliases": ["N"]},
            "sum_n_max": 200000,
        },
        audit_gates={"require_full_audit": True},
        difficulty={"rating": 1600, "band": "中等", "confidence": 0.7},
    )
    assert manifest.audit_gates.require_full_audit is True
    assert manifest.constraints["n"]["max"] == 200000
    assert manifest.difficulty.rating == 1600


def test_problem_verify_static_quality_checks(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "01.in").write_text("1\n", encoding="utf-8")
    (tests_dir / "02.in").write_text("1\n", encoding="utf-8")
    (tests_dir / ".autocode_tests_manifest.json").write_text(
        json.dumps({"tests": [{"in_file": "01.in", "type_param": "1"}]}),
        encoding="utf-8",
    )
    tool = ProblemVerifyTestsTool()
    duplicate = tool._check_duplicate_inputs(str(tests_dir))
    scale = tool._check_scale_distribution(str(tests_dir))
    coverage = tool._check_purpose_coverage(str(tests_dir))
    assert not duplicate["passed"]
    assert scale["passed"]
    assert "2" in coverage["missing_types"]


@pytest.mark.asyncio
async def test_problem_audit_full_go_and_writes_report(tmp_path):
    problem_dir = _write_basic_problem(tmp_path)
    tool = ProblemAuditTool()
    result = await tool.execute(
        problem_dir=str(problem_dir),
        mode="full",
        report_path="audit_report.json",
    )
    assert result.success
    assert result.data["decision"] == "go"
    assert (problem_dir / "audit_report.json").is_file()
    state = json.loads((problem_dir / ".autocode-workflow" / "state.json").read_text())
    assert state["full_audit"]["decision"] == "go"
    assert state["full_audit_passed"] is True
    assert result.data["difficulty_signals"]["rating"] >= 800


@pytest.mark.asyncio
async def test_problem_audit_respects_statement_consistency_override(tmp_path):
    problem_dir = _write_basic_problem(tmp_path)
    (problem_dir / "statements" / "tutorial.md").write_text("short", encoding="utf-8")
    manifest = default_manifest("P")
    manifest.audit_gates.require_statement_consistency = False
    save_manifest(str(problem_dir), manifest)

    result = await ProblemAuditTool().execute(problem_dir=str(problem_dir), mode="full")

    assert result.success
    assert result.data["decision"] == "go"
    assert any(w["gate"] == "statement_consistency" for w in result.data["risk_report"]["warnings"])


@pytest.mark.asyncio
async def test_problem_audit_blocks_missing_purpose_coverage(tmp_path):
    problem_dir = _write_basic_problem(tmp_path)
    (problem_dir / "tests" / ".autocode_tests_manifest.json").write_text(
        json.dumps({"tests": [{"in_file": "01.in", "type_param": "1"}]}),
        encoding="utf-8",
    )

    result = await ProblemAuditTool().execute(problem_dir=str(problem_dir), mode="full")

    assert result.success
    assert result.data["decision"] == "no_go"
    assert any(issue["gate"] == "purpose_coverage" for issue in result.data["blocking_issues"])


@pytest.mark.asyncio
async def test_checker_self_test_requires_format_error_category(monkeypatch, tmp_path):
    problem_dir = tmp_path / "p"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "tests").mkdir()
    checker_exe = problem_dir / "files" / f"checker{tv.get_exe_extension()}"
    checker_exe.write_text("fake", encoding="utf-8")
    (problem_dir / "tests" / "checker_scenarios.json").write_text(
        json.dumps(
            [
                {"input": "1", "contestant_output": "1", "reference_output": "1", "expected_verdict": "AC"},
                {"input": "1", "contestant_output": "2", "reference_output": "1", "expected_verdict": "WA"},
            ]
        ),
        encoding="utf-8",
    )

    async def fake_checker(_checker_bin, _in_path, _out_path, _ans_path, *, timeout):
        class FakeRun:
            stderr = ""

        return Path(_out_path).read_text(encoding="utf-8").strip() == "1" and "AC" or "WA", FakeRun()

    monkeypatch.setattr(tv, "run_testlib_checker", fake_checker)

    result = await ProblemVerifyTestsTool()._check_checker_self_test(str(problem_dir), timeout=1)

    assert not result["passed"]
    assert "format_error" in result["missing_required_categories"]


def test_autocode_audit_cli(monkeypatch, capsys, tmp_path):
    from autocode_mcp.cli.audit import main

    problem_dir = _write_basic_problem(tmp_path)
    monkeypatch.setattr("sys.argv", ["autocode-audit", str(problem_dir), "--mode", "full"])

    assert main() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is True
    assert parsed["data"]["decision"] == "go"
