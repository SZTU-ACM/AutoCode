## Purpose

Eliminate duplicated knowledge and hand-written schemas: the repository is the single source of truth for both the Claude Code plugin's behavioral assets (agents, skills, hooks) and the MCP server implementation (run locally via `uv run`); the package version has a single authoritative source; and tool `input_schema` is derived from Pydantic models, eliminating hand-written JSON Schema drift.

## Requirements

### Requirement: Repository is the single source of truth for the CC plugin
The AutoCode repository SHALL be the single source of truth for both the plugin's behavioral assets (agents, skills, hooks) and the MCP server implementation (run locally via `uv run`). No separate published package or duplicated knowledge surface SHALL serve as an independent source for Claude-Code consumers.

#### Scenario: No duplicated external source
- **WHEN** the plugin's assets and the MCP server are both in use
- **THEN** both are derived from the single repository checkout, and no PyPI-fetched package or separate prompts/resources surface provides overlapping capability

### Requirement: Single version source
The repository's `pyproject.toml` static `version` SHALL be the single authoritative version; the MCP server's `__version__` and the Claude Code plugin's `plugin.json` `version` SHALL be derived from it (via `scripts/sync_plugin_version.py` or `importlib.metadata`), not maintained as independent sources.

#### Scenario: No version drift
- **WHEN** the version is bumped
- **THEN** only `pyproject.toml` is edited, and `plugin.json` and `__version__` converge to it without manual edits

### Requirement: Pydantic-derived tool input schema
Tool `input_schema` SHALL be derived from Pydantic input models via `model_json_schema()` (helper `input_schema_from_model`), eliminating hand-written JSON Schema drift.

#### Scenario: Derived schema correctness
- **WHEN** a tool declares its input via a Pydantic model
- **THEN** the registered `input_schema` matches the model's JSON schema with correct field names and types
