## Purpose

Define the Claude Code plugin as the single supported distribution surface for AutoCode. The MCP server runs locally from the repository (`uv run`), and all non-Claude-Code exposure (MCP `prompts`, `resources`, and bare-MCP-client docs) is removed.

## Requirements

### Requirement: Claude Code plugin is the only distribution surface
AutoCode SHALL be distributed and run exclusively as a Claude Code plugin; no separate published PyPI package SHALL serve as the runtime source for Claude-Code consumers.

#### Scenario: No PyPI runtime dependency
- **WHEN** the Claude Code plugin starts its MCP server
- **THEN** the server is launched from the local repository checkout via `uv run autocode-mcp` (not `uvx autocode-mcp` from PyPI)

### Requirement: MCP server exposes Tools only
The MCP server SHALL expose only Tools; the `prompts` and `resources` capabilities SHALL be removed, and `server.py` SHALL not register `list_prompts`/`get_prompt` or `list_resources`/`read_resource` handlers, nor import the deleted `prompts`/`resources` modules.

#### Scenario: No prompts/resources exposure
- **WHEN** a Claude Code client connects to the MCP server
- **THEN** only Tool calls are available; no prompt templates or MCP resources are advertised

### Requirement: No non-Claude-Code client documentation
README and plugin docs SHALL target the Claude Code plugin and local development only; usage sections for bare MCP clients (Cursor, OpenCode) SHALL be removed.

#### Scenario: CC-only docs
- **WHEN** a user reads the README
- **THEN** only Claude Code plugin and local-dev instructions are present, with no Cursor/OpenCode MCP-client setup
