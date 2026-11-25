## Purpose

Define formal constraint satisfiability, subtask domain containment, boundary witness synthesis, single-source C++ `constraints.h` drift detection, zero-dependency local problem originality detection, and anti-semantic-collapse audit guidance.

## Requirements

### Requirement: Formal SMT and fallback constraint verification with boundary witness synthesis
The system SHALL verify satisfiability across `manifest.constraints`, `manifest.subtasks` conditions/bounds, and extracted `val.cpp` bounds using optional `z3` when installed and a pure-Python interval/linear/modular fallback solver when absent or explicitly requested. The system SHALL detect contradictory constraints (`min > max`, incompatible relational or parity predicates) and subtasks whose explicit domain bounds exceed global `manifest.constraints` limits, and SHALL synthesize solver-verified tight boundary witnesses (`min`, `max`, and per-variable extreme values).

#### Scenario: Contradictory subtask constraints are detected as unsatisfiable
- **WHEN** a subtask defines incompatible ranges or predicates (such as `min > max` or `n >= 100` alongside `n <= 50` or conflicting parity)
- **THEN** `audit_manifest_constraints` reports `satisfiable=False` for that subtask, sets `passed=False`, and emits a conflict diagnostic under both the Z3 and pure-Python solver backends

#### Scenario: Subtask exceeding global bounds is caught with a counterexample witness
- **WHEN** global constraints bound `n` to `[1, 1000]` and a subtask permits `n` up to `5000` (or below `1`)
- **THEN** `audit_manifest_constraints` reports `contained_in_global=False`, sets `passed=False`, and provides a counterexample witness violating the global domain

#### Scenario: Tight boundary witnesses satisfy every active constraint
- **WHEN** constraints define bounded integer ranges and coupled relational/parity predicates (such as `1 <= n < m <= 100` and `n % 2 == 0`)
- **THEN** `synthesize_boundary_witnesses` produces `min`, `max`, and per-variable extreme assignments that pass `verify_witness_assignment` against the solver

### Requirement: Single-source C++ constraints header generation and drift detection
The system SHALL provide `generate_constraints_header(manifest)` producing a C++ `constexpr` header (`files/constraints.h`) derived from `manifest.constraints`, and SHALL provide `check_constraints_header_drift(header_source, manifest)` to compare constants parsed from an existing `files/constraints.h` against `manifest.constraints` and report mismatched values or missing bounds.

#### Scenario: Generated constraints header exhibits zero drift and edited constants fail drift check
- **WHEN** `generate_constraints_header(manifest)` output is checked against `manifest` and subsequently mutated (or `manifest.constraints` is modified)
- **THEN** the unmodified header reports `has_drift=False`, while the diverged header reports `has_drift=True` naming the mismatched or missing constant

### Requirement: Zero-dependency local originality detection with optional dense embedder
The system SHALL provide `OriginalityIndex` supporting user-supplied JSONL and Markdown problem directories with path-containment validation (`realpath` strictly inside the corpus root), scoring candidate statements with Okapi BM25 plus word and character n-gram cosine similarity, returning top-k matches with scores and overlapping domain terms, and accepting an optional `Embedder` protocol for dense vector scoring without bundling corpora into the wheel.

#### Scenario: Paraphrased duplicate ranks above unrelated problem
- **WHEN** `OriginalityIndex` indexes a corpus containing both a paraphrased problem and an unrelated problem and is queried with a target statement
- **THEN** the paraphrased problem ranks first with a substantially higher similarity score and non-empty `overlapping_terms`, while symlink or traversal escapes outside the corpus root are rejected safely

### Requirement: Statement consistency integration and ambiguity/originality skill guidance
The system SHALL integrate SMT/subtask conflict detection and `files/constraints.h` drift checks into `_statement_consistency` in `src/autocode_mcp/tools/audit_constraints.py` (setting `passed=False` on contradictions or drift while preserving existing keys), and SHALL document the Orchid 4D ambiguity checklist, mandatory second-interpretation anti-semantic-collapse protocol, AI-resistibility bare-solve probe, and local originality check across `skills/idea-feasibility/SKILL.md`, `skills/statement-audit/SKILL.md`, and `agents/autocode-idea-auditor.md`.

#### Scenario: Contradictory subtask or drifted constraints.h blocks statement consistency
- **WHEN** `ProblemAuditTool._statement_consistency` inspects a problem package with contradictory subtask constraints or a drifted `files/constraints.h`
- **THEN** `_statement_consistency` returns `passed=False` and `needs_human_review=True` with structured `smt_constraints` / `constraints_header_drift` diagnostics and descriptive `message` and `review_reason` fields while preserving all existing output keys
