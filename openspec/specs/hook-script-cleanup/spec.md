## Purpose

Decompose the Claude Code plugin's step-gating hook script (`scripts/workflow_guard.py`) into side-effect-free pure modules with a thin orchestration entry, keeping a clear naming boundary from the MCP-side gate module.

## Requirements

### Requirement: Hook script decomposed into pure modules
The `scripts/workflow_guard.py` Claude Code plugin gate script SHALL be decomposed into `hook_payload.py` (payload parsing), `hook_state.py` (state read/write), and `hook_gates.py` (gate predicates) as side-effect-free pure functions, with `workflow_guard.py` retained only as a thin orchestration entry (`main` / `pre_tool` / `post_tool` / `session_start`).

#### Scenario: Decomposition preserves behavior
- **WHEN** the hook runs under the same stdin JSON contract
- **THEN** it produces identical allow/deny decisions and exit codes as before the refactor

### Requirement: Clear naming boundary with MCP guard
The hook-side decomposed modules SHALL use the `hook_*` prefix to avoid confusion with `src/autocode_mcp/workflow/guard.py` (MCP-side gate), which serves a different purpose.

#### Scenario: No naming collision
- **WHEN** both modules exist in the repository
- **THEN** their names and responsibilities remain distinct and documented
