"""Automated drift guards verifying docs and OpenSpec stay synchronized with code truths."""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from autocode_mcp.server import TOOLS, register_all_tools
from autocode_mcp.workflow.models import OracleMode, ProblemType, ProtocolMode

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def test_claude_md_lists_exact_22_mcp_tools():
    """CLAUDE.md tool table must list the exact set of 22 tools registered in server.py."""
    register_all_tools()
    registered = set(TOOLS.keys())
    assert len(registered) == 22

    claude_md = _read("CLAUDE.md")
    section = claude_md.split("## 工具列表", 1)[1].split("## 题目目录结构", 1)[0]
    documented = set(re.findall(r"`([a-z][a-z0-9_]+)`", section))
    assert documented == registered


def test_docs_cover_canonical_models_and_cli_flags():
    """Core docs must accurately document model enums and CLI flags without drift."""
    claude_md = _read("CLAUDE.md")
    agents_md = _read("rules/AGENTS.md")
    readme_md = _read("README.md")

    for enum_type in (ProblemType, OracleMode, ProtocolMode):
        for literal in get_args(enum_type):
            assert literal in claude_md, f"Missing model literal {literal!r} in CLAUDE.md"
            assert literal in agents_md, f"Missing model literal {literal!r} in rules/AGENTS.md"

    audit_cli = _read("src/autocode_mcp/cli/audit.py")
    verify_cli = _read("src/autocode_mcp/cli/verify.py")

    for flag in ("--standalone", "--layout", "--mutation-check", "--mode", "--report"):
        assert flag in audit_cli
        assert flag in claude_md, f"Missing audit CLI flag {flag!r} in CLAUDE.md"

    for flag in ("--status", "--strict-gates", "--reconcile"):
        assert flag in verify_cli
        assert flag in claude_md, f"Missing verify CLI flag {flag!r} in CLAUDE.md"

    for public_flag in ("--standalone", "--layout", "--mutation-check"):
        assert public_flag in readme_md, f"Missing {public_flag!r} in README.md"
        assert public_flag in agents_md, f"Missing {public_flag!r} in rules/AGENTS.md"


def test_no_pypi_install_advertised_and_openspec_consistent():
    """Docs and OpenSpec must reflect multi-host + standalone CLI without PyPI distribution."""
    readme_md = _read("README.md")
    claude_md = _read("CLAUDE.md")

    assert "uvx autocode" not in readme_md
    assert "pip install autocode-mcp" not in readme_md
    assert "twine check" not in claude_md

    assert not (REPO_ROOT / "openspec" / "specs" / "cc-first-distribution").exists()
    assert not (REPO_ROOT / "openspec" / "changes" / "manifest-into-autocode-store").exists()
    assert (REPO_ROOT / "openspec" / "specs" / "multi-host-distribution" / "spec.md").is_file()
    assert (REPO_ROOT / "openspec" / "specs" / "standalone-problem-audit" / "spec.md").is_file()

    for spec_path in (REPO_ROOT / "openspec" / "specs").rglob("spec.md"):
        content = spec_path.read_text(encoding="utf-8")
        assert "Claude Code plugin is the only distribution surface" not in content


def test_rules_agents_md_synced_with_latest_gates():
    """Antigravity rules/AGENTS.md must stay plain markdown and track key workflow flags."""
    agents_md = _read("rules/AGENTS.md")
    assert not agents_md.startswith("---")
    for token in (
        "enable_sanitizers",
        "--standalone",
        "--layout",
        "--mutation-check",
        "export_data_mode",
        "time_limit_ms",
        "autocode-verify",
    ):
        assert token in agents_md, f"Missing {token!r} in rules/AGENTS.md"
