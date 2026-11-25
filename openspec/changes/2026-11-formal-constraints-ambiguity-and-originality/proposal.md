## Why

Competitive programming problem packages frequently suffer from three pre-coding and auditing failure modes that surface too late in the pipeline:

1. **Unverified multi-constraint & subtask consistency**: Regex-based numeric extraction in `src/autocode_mcp/tools/audit_constraints.py` compares individual scalar bounds across the statement and `val.cpp`, but cannot detect unsatisfiable combinations (e.g., `min > max`, conflicting relational predicates `n < m` and `n >= m`, parity contradictions) or subtasks whose bounds exceed global domain limits.
2. **Duplicated constraint definitions across files**: Statements, `.autocode/manifest.json`, `files/val.cpp`, and `files/gen.cpp` maintain separate copies of numeric bounds, causing subtle constant drift over iterative problem edits.
3. **LLM semantic collapse, statement ambiguity, and duplicate ideas**: Pre-implementation audits (`skills/idea-feasibility/SKILL.md`, `skills/statement-audit/SKILL.md`, `agents/autocode-idea-auditor.md`) lack structured 4D ambiguity taxonomy checks (`Orchid`), explicit anti-semantic-collapse alternative-interpretation probing (`Underspecification != Incoherence`), bare-solve AI-resistibility checks, and a zero-dependency local originality index to flag near-duplicate problems early.

## What Changes

- **Formal SMT Constraint Verification & Single-Source `constraints.h` (`src/autocode_mcp/utils/smt_constraints.py`)**:
  - Implement constraint parsing and satisfiability/containment checking across `manifest.constraints`, `manifest.subtasks`, and extracted `val.cpp` bounds.
  - Use optional `z3-solver>=4.13` dynamically when available, backed by a full pure-Python interval, linear, and modular fallback solver so every capability runs with zero external dependencies.
  - Synthesize solver-verified tight boundary witnesses (`min`, `max`, per-variable extreme values) and verify concrete assignments against the active solver.
  - Implement `generate_constraints_header(manifest) -> str` (single-source C++ `constexpr` header for `files/constraints.h`) and `check_constraints_header_drift(header_source, manifest)`.
- **Local Problem Originality Detector (`src/autocode_mcp/utils/originality.py`)**:
  - Implement zero-dependency `OriginalityIndex` combining Okapi BM25, stemmed word 1-2 n-gram cosine similarity, and character 3-4 n-gram cosine similarity over user-supplied JSONL or Markdown problem corpora (supporting English and CJK text) with safe path containment (`os.path.realpath` + commonpath checks).
  - Define an optional `Embedder` protocol for plugging in dense embeddings (e.g., `CPRetriever-Prob`) without bundling large corpora or heavy ML dependencies into the wheel.
- **Audit Integration (`src/autocode_mcp/tools/audit_constraints.py`)**:
  - Integrate `audit_manifest_constraints` and `check_constraints_header_drift` into `_statement_consistency`, failing `passed=False` when SMT/subtask contradictions or `files/constraints.h` drift are detected while preserving all existing output keys.
- **Skill & Agent Governance Guidance (`skills/idea-feasibility/SKILL.md`, `skills/statement-audit/SKILL.md`, `agents/autocode-idea-auditor.md`)**:
  - Add Orchid 4D ambiguity checklist (lexical, syntactic, semantic, vague boundary), mandatory anti-semantic-collapse "enumerate a second plausible interpretation" protocol, AI-resistibility bare-solve check, and local originality check guidance.
- **Optional Extra & Regression Coverage (`pyproject.toml`, `uv.lock`, `tests/test_smt_constraints_and_originality.py`)**:
  - Add `[project.optional-dependencies] formal = ["z3-solver>=4.13"]` and update `uv.lock`.
  - Add comprehensive regression tests covering both Z3 and pure-Python solver paths, boundary witness synthesis, `constraints.h` generation and drift detection, BM25/n-gram originality detection, path safety, and `audit_constraints` integration.

## Capabilities

### New Capabilities

- `formal-constraints-and-originality`: Formal SMT/fallback constraint satisfiability and subtask containment checking, solver-verified boundary witness synthesis, single-source `files/constraints.h` generation and drift detection, zero-dependency local originality indexing with optional dense `Embedder` protocol, and Orchid 4D ambiguity + anti-semantic-collapse auditing guidance.

### Modified Capabilities

- None (`openspec/specs/` canonical specs remain intact; `CLAUDE.md` step 11 `problem_audit` `statement_consistency` gate gains SMT and `constraints.h` drift checks without modifying any of the 22 MCP tool signatures).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Enhances step 1 (`idea-feasibility`), step 3 (`statement-audit`), and step 11 (`problem_audit` via `_statement_consistency`) while keeping all 22 MCP tool signatures unchanged.
- **Affected Files**:
  - `src/autocode_mcp/utils/smt_constraints.py` (new)
  - `src/autocode_mcp/utils/originality.py` (new)
  - `src/autocode_mcp/tools/audit_constraints.py`
  - `skills/idea-feasibility/SKILL.md`
  - `skills/statement-audit/SKILL.md`
  - `agents/autocode-idea-auditor.md`
  - `pyproject.toml` (`formal = ["z3-solver>=4.13"]` optional extra only) and `uv.lock`
  - `tests/test_smt_constraints_and_originality.py` (new)
- **Dependencies**: Optional extra `formal = ["z3-solver>=4.13"]`; core runtime remains zero-extra-dependency (pure-Python SMT fallback + pure-Python BM25/n-gram similarity).
- **Risks**:
  - *Z3 solver latency or missing type stubs under `mypy --strict`*: Mitigated by loading `z3` dynamically via `importlib`, enforcing a bounded solver timeout, converting extracted values strictly to native Python types without `# type: ignore` or `cast`, and testing both `--extra dev` and `--all-extras`.
  - *False-positive subtask containment failures when subtasks override only a subset of variables*: Mitigated by isolating containment checks to variables/bounds explicitly constrained by each subtask while inheriting unconstrained opposite bounds from the global manifest.
  - *Path traversal or OOM when scanning external problem corpora*: Mitigated by `realpath` containment validation (`followlinks=False`) and bounded per-file read limits.

## Non-goals

- Do NOT bundle any problem corpora or embedding model weights into the wheel or plugin bundles.
- Do NOT modify any of the 22 MCP tool signatures (`server.py`, `tools/schemas.py`).
- Do NOT edit `workflow/models.py`, `tools/audit.py`, `tools/audit_standalone.py`, `tools/verify*.py`, `tools/stress_test.py`, `utils/mutation.py`, `utils/sandbox/*`, `utils/judge_runner.py`, or `cli/*`.
