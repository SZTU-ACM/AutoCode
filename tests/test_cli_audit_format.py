"""Tests for ``autocode-audit --format json|markdown|summary`` and safe Markdown escaping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocode_mcp.cli.report_format import format_audit_markdown, format_audit_summary


def _sample_report(*, decision: str = "no_go") -> dict[str, Any]:
    return {
        "success": True,
        "error": None,
        "data": {
            "decision": decision,
            "mode": "full",
            "standalone": True,
            "detected_layout": "standard_cn",
            "problem_dir": "/tmp/sample-problem",
            "generated_at": "2026-11-23T10:00:00+00:00",
            "blocking_issues": (
                [{"gate": "wrong_solution_kill", "reason": "wrong_greedy survived all tests"}]
                if decision == "no_go"
                else []
            ),
            "risk_report": {
                "package_format": "Standalone/standard_cn",
                "warnings": [{"gate": "statement_consistency", "reason": "n upper bound differs"}],
                "statement_consistency": {"passed": False, "message": "statement says 1e5, val reads 1e4"},
            },
            "structural_signals": {
                "manifest": {"executed": True, "passed": True, "evidence": {"message": "inferred"}},
                "tests_present": {"executed": True, "passed": True, "evidence": {"total": 12}},
            },
            "quality_signals": {
                "validator_check": {"executed": True, "passed": True, "evidence": {"message": "all valid"}},
                "empirical_complexity": {
                    "executed": True,
                    "passed": True,
                    "evidence": {
                        "claimed_complexity": "O(n log n)",
                        "fitted_complexity": "O(n log n)",
                        "verdict": "verified",
                        "fitted_alpha": 1.04,
                        "r_squared": 0.996,
                        "samples": [{"n": 100}, {"n": 1000}],
                    },
                },
                "wrong_solution_kill": {
                    "executed": True,
                    "passed": decision == "go",
                    "evidence": {
                        "details": [
                            {
                                "name": "wrong_overflow",
                                "expected": "fail",
                                "killed": True,
                                "killing_verdict": "WA",
                                "killing_test": "03.in",
                                "max_time_ms": 12.4,
                                "max_ratio_to_std": 1.1,
                            }
                        ]
                    },
                },
                "mutation_testing": {
                    "executed": True,
                    "passed": False,
                    "evidence": {
                        "compiled_mutants": 4,
                        "killed_mutants": 3,
                        "survived_mutants": 1,
                        "judge_error_mutants": 0,
                        "kill_rate": 0.75,
                        "survivors": [
                            {
                                "id": "mut-1",
                                "line": 42,
                                "mutation_type": "comparison_boundary",
                                "original": "a <= b",
                                "mutated": "a < b",
                            }
                        ],
                    },
                },
                "checker_self_test": {
                    "applicable": False,
                    "status": "not_applicable",
                    "executed": False,
                    "passed": True,
                    "evidence": {"reason": "not applicable"},
                },
            },
            "communication_metrics": {
                "protocol_mode": "run_twice",
                "budget_exceeded": False,
                "observed": {"max_bits_used": 64, "max_rounds": 2},
            },
            "next_actions": [
                {"tool_name": "problem_generate_tests", "action": "add edge test killing mutant mut-1"}
            ],
        },
    }


def test_format_audit_markdown_sections_and_badges() -> None:
    go_md = format_audit_markdown(_sample_report(decision="go"))
    assert "# AutoCode Problem Audit Report" in go_md
    assert "✅ GO" in go_md
    assert "## Signal Overview" in go_md
    assert "## Empirical Complexity" in go_md
    assert "## Wrong Solution Kill Matrix" in go_md
    assert "## Mutation Testing" in go_md
    assert "75.0%" in go_md
    assert "mut-1" in go_md
    assert "| `quality` | `checker_self_test` | N/A |" in go_md

    nogo_md = format_audit_markdown(_sample_report(decision="no_go"))
    assert "❌ NO-GO" in nogo_md
    assert "## Blocking Issues" in nogo_md
    assert "wrong_greedy survived all tests" in nogo_md

    err_md = format_audit_markdown({"success": False, "error": "manifest.json not found"})
    assert "❌ ERROR" in err_md
    assert "manifest.json not found" in err_md


def test_format_audit_markdown_escapes_injection_and_ansi() -> None:
    payload = _sample_report(decision="no_go")
    adversarial = (
        "\x1b[31mFAIL\x1b[0m\x07 | evilcol\n# Injected Heading\n"
        "<script>alert(1)</script><img src=x onerror=alert(2)> "
        "[phish](https://evil.example) ```code```"
    )
    payload["data"]["blocking_issues"] = [{"gate": "bad|gate\nline", "reason": adversarial}]
    payload["data"]["quality_signals"]["mutation_testing"]["evidence"]["survivors"] = [
        {
            "id": "m|1",
            "line": 7,
            "mutation_type": "op",
            "original": "x | y `z`",
            "mutated": "x `` y\n| col",
        }
    ]

    md = format_audit_markdown(payload)
    assert "\x1b" not in md
    assert "\x07" not in md
    assert "<script>" not in md
    assert "<img " not in md
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in md
    assert "\n# Injected Heading" not in md
    assert "\\[phish\\]" in md
    assert "\\| evilcol" in md
    # Mutant table code cell escapes pipe and uses longer backtick fence when needed
    assert "\\|" in md
    assert "```x `` y \\| col```" in md


def test_format_audit_summary_output() -> None:
    summary = format_audit_summary(_sample_report(decision="no_go"))
    assert summary.startswith("[NO_GO]")
    assert "BLOCK [wrong_solution_kill]" in summary
    assert "WARN  [statement_consistency]" in summary
    assert "MUTATION: killed=3/4 survived=1 kill_rate=0.75" in summary

    err_summary = format_audit_summary({"success": False, "error": "\x1b[31mbad\x1b[0m\nline"})
    assert err_summary == "ERROR: bad line"


@pytest.mark.parametrize(
    ("decision", "expected_rc"),
    [("go", 0), ("no_go", 2)],
)
def test_cli_audit_format_flags_and_backwards_compatibility(
    monkeypatch,
    tmp_path: Path,
    capsys,
    decision: str,
    expected_rc: int,
) -> None:
    from autocode_mcp.cli import audit as audit_cli
    from autocode_mcp.tools.base import ToolResult

    fake_data = _sample_report(decision=decision)["data"]

    async def fake_execute(self, **kwargs):  # noqa: ANN001, ANN003
        report_path = kwargs.get("report_path")
        if report_path:
            Path(report_path).write_text(json.dumps(fake_data), encoding="utf-8")
        return ToolResult.ok(**fake_data)

    monkeypatch.setattr(audit_cli.ProblemAuditTool, "execute", fake_execute)

    # 1. Default format remains JSON
    monkeypatch.setattr("sys.argv", ["autocode-audit", str(tmp_path), "--standalone"])
    rc_default = audit_cli.main()
    assert rc_default == expected_rc
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is True
    assert parsed["data"]["decision"] == decision

    # 2. --format markdown + --report writes JSON to file and Markdown to stdout
    out_json = tmp_path / "out.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "autocode-audit",
            str(tmp_path),
            "--standalone",
            "--format",
            "markdown",
            "--report",
            str(out_json),
        ],
    )
    rc_md = audit_cli.main()
    assert rc_md == expected_rc
    stdout_md = capsys.readouterr().out
    assert "# AutoCode Problem Audit Report" in stdout_md
    assert out_json.is_file()
    assert json.loads(out_json.read_text(encoding="utf-8"))["decision"] == decision

    # 3. --format summary prints concise text summary
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--standalone", "--format", "summary"],
    )
    rc_sum = audit_cli.main()
    assert rc_sum == expected_rc
    assert f"[{decision.upper()}]" in capsys.readouterr().out


def test_cli_audit_markdown_error_exit_code(monkeypatch, tmp_path: Path, capsys) -> None:
    from autocode_mcp.cli import audit as audit_cli
    from autocode_mcp.tools.base import ToolResult

    async def fake_fail(self, **kwargs):  # noqa: ANN001, ANN003
        return ToolResult.fail("boom <tag>")

    monkeypatch.setattr(audit_cli.ProblemAuditTool, "execute", fake_fail)
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--format", "markdown"],
    )
    rc = audit_cli.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert "❌ ERROR" in out
    assert "&lt;tag&gt;" in out
