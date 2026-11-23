## Purpose

Define the multi-host thin-shell plugin architecture and non-PyPI distribution surface for AutoCode. Four AI hosts (`Claude Code`, `Codex`, `DeepSeek Harness`, `Antigravity`) share one authoritative `autocode-mcp` server (22 Tools only) and shared `skills/`, while standalone CLI tools (`autocode-audit`, `autocode-verify`) install from GitHub Releases or `uv tool install git+...`.

## Requirements

### Requirement: Multi-host thin shells share one authoritative MCP server
AutoCode SHALL support `Claude Code`, `Codex`, `DeepSeek Harness (DSH)`, and `Antigravity (agy)` through thin host-specific plugin manifests built deterministically by `scripts/build_plugin_bundle.py`, while delegating all workflow gating (`src/autocode_mcp/workflow/enforcement.py`) and tool execution to `autocode-mcp`.

#### Scenario: Reproducible multi-host bundles
- **WHEN** `scripts/build_plugin_bundle.py --target <marketplace|agy|dsh>` runs and is re-checked with `--check`
- **THEN** each bundle matches the source repository's provenance fingerprint and exposes shared `skills/` and `autocode-mcp` configuration

### Requirement: MCP server exposes Tools only
The MCP server SHALL expose only Tools (22 registered tools); `prompts` and `resources` capabilities SHALL remain absent (`list_prompts`/`get_prompt`/`list_resources`/`read_resource` are not registered).

#### Scenario: Tools-only capability surface
- **WHEN** any supported host client initializes `autocode-mcp`
- **THEN** only the 22 registered Tools are advertised, and no legacy MCP prompt or resource endpoints exist

### Requirement: Non-PyPI distribution via GitHub Releases and git installs
AutoCode SHALL NOT depend on PyPI publication (`uvx autocode-mcp` or `pip install autocode-mcp` from PyPI). Host bundles (`marketplace`, `agy`, `dsh`) and standalone CLI wheels (`autocode-audit`, `autocode-verify`) SHALL be distributed via GitHub Releases, git-backed `uv tool install`, or repository plugin marketplaces.

#### Scenario: Standalone CLI installation without PyPI
- **WHEN** a user or CI workflow installs AutoCode CLI tools for standalone auditing
- **THEN** installation uses `uv tool install git+https://github.com/SZTU-ACM/AutoCode.git@<tag>` or a GitHub Release wheel, not a PyPI registry lookup
