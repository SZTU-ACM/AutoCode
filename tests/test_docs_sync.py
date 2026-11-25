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

    for flag in ("--standalone", "--layout", "--mutation-check", "--mode", "--report", "--format", "--strict", "--sandbox"):
        assert flag in audit_cli
        assert flag in claude_md, f"Missing audit CLI flag {flag!r} in CLAUDE.md"

    for flag in ("--status", "--strict-gates", "--reconcile"):
        assert flag in verify_cli
        assert flag in claude_md, f"Missing verify CLI flag {flag!r} in CLAUDE.md"

    for public_flag in ("--standalone", "--layout", "--mutation-check", "--format", "--strict", "--sandbox"):
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


def test_readme_interactor_uses_stdout_not_tout():
    """README.md must instruct interactors to prompt contestants via std::cout, never tout."""
    readme_md = _read("README.md")

    assert "不要用 `std::cout`" not in readme_md
    assert "用 `tout` 向选手输出" not in readme_md
    assert "tout.flush()" not in readme_md

    assert "registerInteraction(argc, argv)" in readme_md
    assert "std::cout" in readme_md
    assert "std::cout.flush()" in readme_md
    assert "`inf`" in readme_md
    assert "`ouf`" in readme_md
    assert "`tout`" in readme_md
    assert "`argv[2]`" in readme_md


def test_pr_ci_security_boundary_warnings_present():
    """PR CI example, README.md, and TROUBLESHOOTING.md must warn on untrusted PR CI boundaries."""
    import yaml

    ci_text = _read("examples/ci/autocode-problem-audit.yml")
    ci_yaml = yaml.safe_load(ci_text)
    assert isinstance(ci_yaml, dict)
    on_block = ci_yaml.get("on") or ci_yaml.get(True)
    assert isinstance(on_block, dict)
    assert "pull_request" in on_block
    assert "pull_request_target" not in on_block
    assert ci_yaml["jobs"]["audit"]["runs-on"] == "ubuntu-latest"
    assert ci_yaml["permissions"] == {"contents": "read"}

    readme_md = _read("README.md")
    troubleshooting_md = _read("TROUBLESHOOTING.md")

    for doc_name, content in (
        ("examples/ci/autocode-problem-audit.yml", ci_text),
        ("README.md", readme_md),
        ("TROUBLESHOOTING.md", troubleshooting_md),
    ):
        assert "self-hosted" in content, f"Missing self-hosted runner warning in {doc_name}"
        assert "pull_request_target" in content, (
            f"Missing pull_request_target warning in {doc_name}"
        )
        assert "secrets" in content.lower(), f"Missing secrets warning in {doc_name}"
        assert "ubuntu-latest" in content, f"Missing ephemeral ubuntu-latest guidance in {doc_name}"
