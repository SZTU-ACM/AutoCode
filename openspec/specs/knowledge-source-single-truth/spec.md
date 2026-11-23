## Purpose

Eliminate duplicated knowledge and hand-written schemas: the repository is the single source of truth for shared behavioral assets (`skills/`, `agents/`), host plugin manifests, the MCP server (`autocode-mcp`), and package version metadata; tool `input_schema` is derived from Pydantic models, and documentation sync is verified automatically by tests.

## Requirements

### Requirement: Repository is the single source of truth across all hosts
The AutoCode repository SHALL be the single source of truth for all host thin shells (`Claude Code`, `Codex`, `DSH`, `Antigravity`), shared `skills/`, and the `autocode-mcp` server implementation. Host bundles SHALL be generated reproducibly from the repository rather than maintained as separate forks.

#### Scenario: Reproducible host asset derivation
- **WHEN** plugin bundles are built for `marketplace`, `agy`, or `dsh`
- **THEN** all shared skills, agents, and `src/autocode_mcp/` code originate from the single repository source tree

### Requirement: Single version source across all four hosts
The repository's `pyproject.toml` `[project].version` SHALL be the single authoritative version; `src/autocode_mcp/__init__.py` (`__version__`) and all four host manifests (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.dsh-plugin/package.json`, `.antigravity-plugin/plugin.json`) SHALL be synchronized from it via `scripts/sync_plugin_version.py` and verified in CI with `--check`.

#### Scenario: No version drift across hosts
- **WHEN** the version in `pyproject.toml` is bumped and `scripts/sync_plugin_version.py` runs
- **THEN** `__version__` and all four host manifests match `pyproject.toml`, and `sync_plugin_version.py --check` exits 0

### Requirement: Pydantic-derived tool input schema and docs sync verification
Tool `input_schema` SHALL be derived from Pydantic input models via `input_schema_from_model`, and automated tests (`tests/test_docs_sync.py`) SHALL verify that documented MCP tool names, problem/protocol/oracle enums, and CLI flags remain synchronized with `server.py`, `models.py`, and `cli/`.

#### Scenario: Derived schema and documentation parity
- **WHEN** `tests/test_input_schema.py` and `tests/test_docs_sync.py` execute
- **THEN** registered tool schemas match their Pydantic models and core docs (`README.md`, `CLAUDE.md`, `rules/AGENTS.md`) report zero missing or fabricated identifiers
