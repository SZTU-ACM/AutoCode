"""Claude Code plugin validation tests."""

from __future__ import annotations

import json
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10 (project floor) has no tomllib
    import tomli as tomllib  # type: ignore[no-redef]


def load_pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def project_version() -> str:
    return str(load_pyproject()["project"]["version"])


def test_claude_plugin_manifest_links_mcp_config():
    """Claude Code plugin manifest should keep the canonical plugin name."""
    manifest = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "autocode"
    assert manifest["version"] == project_version()


def test_claude_plugin_manifest_has_interface_metadata():
    """Claude Code plugin manifest should expose core metadata."""
    manifest = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))

    assert manifest["description"]
    assert manifest["homepage"] == "https://github.com/SZTU-ACM/AutoCode"
    assert "autocode" in manifest["keywords"]


def test_codex_plugin_manifest_declares_shared_assets():
    """Codex uses its own manifest while sharing the MCP and skill assets."""
    manifest = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "autocode"
    assert manifest["version"] == project_version()
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "hooks" not in manifest


def test_host_manifests_and_package_share_one_version_source():
    """Claude, Codex, and the importable package must not drift in version."""
    claude = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    codex = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    from autocode_mcp import __version__

    assert claude["version"] == codex["version"] == __version__ == project_version()


def test_mcp_dependency_stays_on_supported_major():
    """The server uses the MCP 1.x decorator API; do not resolve MCP 2.x."""
    project = load_pyproject()
    dependency = next(item for item in project["project"]["dependencies"] if item.startswith("mcp"))
    assert dependency == "mcp>=1.0.0,<2.0.0"


def test_mcp_config_runs_from_plugin_root():
    """The bundled uv project must be the MCP process working directory."""
    config = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["autocode"]
    assert server["command"] == "uv"
    assert server["args"] == ["run", "autocode-mcp"]
    assert server["cwd"] == "."


def test_plugin_settings_activate_default_agent():
    """The plugin should activate its workflow agent by default."""
    settings = json.loads(Path("settings.json").read_text(encoding="utf-8"))
    assert settings["agent"] == "autocode-workflow"


def test_plugin_hooks_exist_for_autocode_mcp_tools():
    """The plugin should install enforcement hooks for AutoCode MCP tools."""
    hooks = json.loads(Path("hooks/hooks.json").read_text(encoding="utf-8"))

    pre = hooks["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "mcp__autocode__.*"
    assert "PostToolUse" not in hooks["hooks"]


def test_plugin_agent_exists():
    """The default workflow agent should be present."""
    content = Path("agents/autocode-workflow.md").read_text(encoding="utf-8")
    assert "name: autocode-workflow" in content
    assert "skills:" in content
