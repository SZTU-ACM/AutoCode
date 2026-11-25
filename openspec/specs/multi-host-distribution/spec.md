## Purpose

Define the multi-host thin-shell plugin architecture and non-PyPI distribution surface for AutoCode. Four AI hosts (`Claude Code`, `Codex`, `DeepSeek Harness`, `Antigravity`) share one authoritative `autocode-mcp` server (22 MCP Tools with `ToolAnnotations`, `outputSchema`, `structuredContent`, and bounded async MCP Tasks; 3 zero-CDN `ui://autocode/*` MCP Apps UI resources; `stdio` default plus optional stateless Streamable HTTP `--transport http`) and shared `skills/`, while standalone CLI tools (`autocode-audit`, `autocode-verify`, `polygon_sync`, `render_pdf`) install from GitHub Releases or `uv tool install git+...`.

## Requirements

### Requirement: Multi-host thin shells share one authoritative MCP server
AutoCode SHALL support `Claude Code`, `Codex`, `DeepSeek Harness (DSH)`, and `Antigravity (agy)` through thin host-specific plugin manifests built deterministically by `scripts/build_plugin_bundle.py`, while delegating all workflow gating (`src/autocode_mcp/workflow/enforcement.py`) and tool execution to `autocode-mcp`.

#### Scenario: Reproducible multi-host bundles
- **WHEN** `scripts/build_plugin_bundle.py --target <marketplace|agy|dsh>` runs and is re-checked with `--check`
- **THEN** each bundle matches the source repository's provenance fingerprint and exposes shared `skills/` and `autocode-mcp` configuration

### Requirement: MCP server exposes 22 compliant Tools, UI App resources, and Tasks without legacy prompts
The MCP server SHALL expose all 22 registered Tools with `ToolAnnotations`, inlined Pydantic `outputSchema`/`structuredContent`, and bounded async `MCP Tasks` (`tasks/get|list|result|cancel`) alongside the 3 self-contained zero-CDN `ui://autocode/*` MCP Apps UI resources (`list_resources`/`read_resource`), while `prompts` capabilities SHALL remain absent (`list_prompts`/`get_prompt` are not registered). Optional `--transport http` SHALL enforce Host/Origin DNS rebinding protection while keeping zero-flag invocation on `stdio`.

#### Scenario: MCP 2026 conformance across tools, tasks, UI apps, and transport
- **WHEN** any supported host client initializes `autocode-mcp`
- **THEN** all 22 Tools advertise `ToolAnnotations` and `outputSchema`, the 3 `ui://autocode/*` HTML resources contain zero external CDN URLs, and no legacy prompt endpoints exist

### Requirement: Non-PyPI distribution via GitHub Releases and git installs
AutoCode SHALL NOT depend on PyPI publication (`uvx autocode-mcp` or `pip install autocode-mcp` from PyPI). Host bundles (`marketplace`, `agy`, `dsh`) and standalone CLI wheels (`autocode-audit`, `autocode-verify`) SHALL be distributed via GitHub Releases, git-backed `uv tool install`, or repository plugin marketplaces.

#### Scenario: Standalone CLI installation without PyPI
- **WHEN** a user or CI workflow installs AutoCode CLI tools for standalone auditing
- **THEN** installation uses `uv tool install git+https://github.com/SZTU-ACM/AutoCode.git@<tag>` or a GitHub Release wheel, not a PyPI registry lookup
