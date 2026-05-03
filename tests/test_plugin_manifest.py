"""Claude Code plugin validation tests."""

from __future__ import annotations

import json
from pathlib import Path


def test_claude_plugin_manifest_links_mcp_config():
    """Claude Code plugin manifest should keep the canonical plugin name."""
    manifest = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "autocode"
    assert manifest["version"] == "1.0.3"


def test_claude_plugin_manifest_has_interface_metadata():
    """Claude Code plugin manifest should expose core metadata."""
    manifest = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))

    assert manifest["description"]
    assert manifest["homepage"] == "https://github.com/SZTU-ACM/AutoCode"
    assert "autocode" in manifest["keywords"]


def test_plugin_settings_activate_default_agent():
    """The plugin should activate its workflow agent by default."""
    settings = json.loads(Path("settings.json").read_text(encoding="utf-8"))
    assert settings["agent"] == "autocode-workflow"


def test_plugin_hooks_exist_for_autocode_mcp_tools():
    """The plugin should install enforcement hooks for AutoCode MCP tools."""
    hooks = json.loads(Path("hooks/hooks.json").read_text(encoding="utf-8"))

    pre = hooks["hooks"]["PreToolUse"][0]
    post = hooks["hooks"]["PostToolUse"][0]
    assert pre["matcher"] == "mcp__autocode__.*"
    assert post["matcher"] == "mcp__autocode__.*"


def test_plugin_agent_exists():
    """The default workflow agent should be present."""
    content = Path("agents/autocode-workflow.md").read_text(encoding="utf-8")
    assert "name: autocode-workflow" in content
    assert "skills:" in content
