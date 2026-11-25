## 1. OpenSpec & Optional Dependency Setup

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/formal-constraints-and-originality/spec.md` under `openspec/changes/2026-11-formal-constraints-ambiguity-and-originality/` following `openspec/config.yaml`.
- [x] 1.2 Add `formal = ["z3-solver>=4.13"]` under `[project.optional-dependencies]` in `pyproject.toml` and regenerate `uv.lock` cleanly via `uv lock`.

## 2. SMT Constraint Verification & Single-Source `constraints.h`

- [x] 2.1 Implement `src/autocode_mcp/utils/smt_constraints.py` with constraint AST parsing, optional dynamic `z3` backend, full pure-Python interval/linear/modular fallback solver, subtask satisfiability & global containment verification, and solver-verified tight boundary witness synthesis (`min`, `max`, extreme values).
- [x] 2.2 Implement `generate_constraints_header(manifest) -> str` and `check_constraints_header_drift(header_source, manifest)` in `src/autocode_mcp/utils/smt_constraints.py`.

## 3. Local Originality Detector

- [x] 3.1 Implement `src/autocode_mcp/utils/originality.py` with `OriginalityIndex` (Okapi BM25 + word 1-2 n-gram cosine + char 3-4 n-gram cosine, English/CJK tokenization, top-k matches with overlapping terms), safe directory/JSONL loading with path-containment guards, and the `Embedder` protocol for dense embeddings (`CPRetriever`).

## 4. Audit Integration & Skills/Agent Governance

- [x] 4.1 Integrate `audit_manifest_constraints` and `check_constraints_header_drift` cleanly into `_statement_consistency` in `src/autocode_mcp/tools/audit_constraints.py`, failing `passed=False` on SMT/subtask contradictions or header drift without altering any existing Issue #16 keys or the 22 MCP tool signatures.
- [x] 4.2 Update `skills/idea-feasibility/SKILL.md`, `skills/statement-audit/SKILL.md`, and `agents/autocode-idea-auditor.md` with the Orchid 4D ambiguity checklist, mandatory anti-semantic-collapse second-interpretation protocol, AI-resistibility bare-solve probe, and local originality check guidance.

## 5. Regression Tests & Full Verification

- [x] 5.1 Add comprehensive regression tests in `tests/test_smt_constraints_and_originality.py` covering both Z3 and pure-Python backends, contradictory/out-of-bounds subtasks, verified boundary witnesses, `constraints.h` generation/drift, BM25/n-gram + dense `Embedder` originality checks, corpus path-containment safety, and `audit_constraints` integration.
- [x] 5.2 Verify `uv run --extra dev` and `uv run --all-extras` for `ruff check .`, `mypy src/`, `scripts/sync_plugin_version.py --check`, `test_plugin_manifest.py`, `test_plugin_bundle.py`, `test_static_checks_and_consistency.py`, and full `pytest tests/ -q`.
