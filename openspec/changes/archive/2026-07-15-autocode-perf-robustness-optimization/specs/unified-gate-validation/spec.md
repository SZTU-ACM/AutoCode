## ADDED Requirements

### Requirement: Centralized gate evaluation
The system SHALL provide a single module `workflow/guard.py` exposing `check_gates(manifest, workflow_state, verify_signals)` that both `problem_pack_polygon` and `problem_audit` use for QualityGate / AuditGate decisions.

#### Scenario: Single source of truth
- **WHEN** either tool evaluates gates
- **THEN** it delegates to `check_gates` and does not re-implement gate logic

### Requirement: Manifest load failure blocks, not permits
The system SHALL fail gate evaluation when `autocode.json` is missing or unparseable, rather than silently falling back to permissive defaults.

#### Scenario: Corrupt manifest
- **WHEN** the manifest is corrupt or unreadable during pack or audit
- **THEN** the operation returns a blocking error and does not proceed to packaging/audit

### Requirement: Identical gate semantics
The centralized evaluation SHALL produce results identical to the prior per-tool implementations for valid manifests.

#### Scenario: Behavior parity
- **WHEN** a valid manifest with `require_*` gates is evaluated
- **THEN** the decision matches the previous implementation's decision

### Requirement: Hook gate reuses the manifest truth
The hook-side gate (`scripts/hook_gates.py`) SHALL evaluate `manifest_uses_testlib_checker` by importing the single implementation from `autocode_mcp.workflow.manifest`, not a duplicated dictionary-based copy.

#### Scenario: Single source of truth
- **WHEN** the hook evaluates whether the manifest uses a testlib checker
- **THEN** it delegates to `manifest_uses_testlib_checker` and no duplicated logic remains
