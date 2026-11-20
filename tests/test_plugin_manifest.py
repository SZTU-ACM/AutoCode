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
    """Claude, Codex, DSH, AGY, and the importable package must not drift in version."""
    claude = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    codex = json.loads(Path(".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    dsh = json.loads(Path(".dsh-plugin/package.json").read_text(encoding="utf-8"))
    agy = json.loads(Path(".antigravity-plugin/plugin.json").read_text(encoding="utf-8"))
    from autocode_mcp import __version__

    assert (
        claude["version"]
        == codex["version"]
        == dsh["version"]
        == agy["version"]
        == __version__
        == project_version()
    )


def test_antigravity_plugin_manifest_uses_only_supported_fields():
    """AGY plugin.json must stay strictly within supported top-level schema fields."""
    manifest = json.loads(Path(".antigravity-plugin/plugin.json").read_text(encoding="utf-8"))
    allowed_fields = {
        "name",
        "displayName",
        "description",
        "version",
        "author",
        "homepage",
        "repository",
        "logo",
        "suggestedPrompts",
        "disabled",
    }
    assert set(manifest).issubset(allowed_fields)
    assert manifest["name"] == "autocode"
    assert manifest["version"] == project_version()
    assert manifest["description"]
    assert len(manifest["suggestedPrompts"]) == 3
    assert all(isinstance(p, str) and p.strip() for p in manifest["suggestedPrompts"])
    logo_rel = manifest.get("logo")
    assert isinstance(logo_rel, str) and not logo_rel.startswith(("http://", "https://", "/"))
    assert not Path(logo_rel).is_absolute()
    assert ".." not in Path(logo_rel).parts
    assert Path(logo_rel).is_file()


def test_antigravity_mcp_config_and_rules_exist():
    """AGY native plugin config should expose autocode MCP server and plain-markdown rules."""
    config = json.loads(Path("mcp_config.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["autocode"]
    assert server["command"] == "uv"
    assert isinstance(server["args"], list) and server["args"]
    assert "${PLUGIN_ROOT}" in server["args"]
    assert "PLUGIN_ROOT" in " ".join(server["args"])

    rules = Path("rules/AGENTS.md").read_text(encoding="utf-8")
    assert not rules.startswith("---"), "AGY plugin rules/AGENTS.md must be plain markdown without frontmatter"
    assert "problem_create" in rules


def test_sync_plugin_version_check_and_py310_fallback():
    """sync_plugin_version.py --check succeeds on repo and scopes fallback to [project]."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sync_plugin_version", Path("scripts/sync_plugin_version.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.main(["--check"]) == 0
    assert mod.check_versions(expected="0.0.0-mismatch")

    sample_toml = (
        '[tool.example]\nversion = "9.9.9"\n\n'
        '[project]\nname = "demo"\nversion = "3.1.0"\n\n'
        '[tool.other]\nversion = "8.8.8"\n'
    )
    assert mod._extract_project_version_fallback(sample_toml) == "3.1.0"


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
