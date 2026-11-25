## Context

AutoCode validates problem specifications across three stages: pre-implementation idea screening (`skills/idea-feasibility/SKILL.md`, `agents/autocode-idea-auditor.md`), statement auditing (`skills/statement-audit/SKILL.md`), and deterministic constraint consistency checking (`src/autocode_mcp/tools/audit_constraints.py`). Prior tooling checked isolated scalar numbers in statements and `val.cpp`, leaving subtask domain containment, multi-variable predicate satisfiability, single-source C++ constraint header sync, and local problem-corpus duplicate detection unautomated.

Module boundaries remain strictly separated:
- Server-side SMT solving, `constraints.h` generation/drift detection, originality indexing, and audit integration live in `src/autocode_mcp/utils/smt_constraints.py`, `src/autocode_mcp/utils/originality.py`, and `src/autocode_mcp/tools/audit_constraints.py`.
- Host skill/agent guidance lives in `skills/idea-feasibility/SKILL.md`, `skills/statement-audit/SKILL.md`, and `agents/autocode-idea-auditor.md`.
- Hook scripts under `scripts/` and CLI formatters under `src/autocode_mcp/cli/` remain untouched.

## Architecture & Design Decisions

### 1. Dual-Backend SMT Constraint Verifier & Witness Synthesizer (`smt_constraints.py`)

```text
manifest.constraints + manifest.subtasks + val.cpp bounds
  │
  ▼  parse intervals, scalar bounds, and relational/modular AST predicates
Canonical Constraint IR (RelPred over Var/Const/BinOp)
  │
  ├──▶ Optional Z3 Solver (`importlib.import_module("z3")`)
  │      ├── Global & validator satisfiability (with unsat_core tracking)
  │      ├── Subtask i: global ∧ subtask_i satisfiability
  │      ├── Subtask i containment: (subtask_explicit ∧ global_opposite) ∧ ¬global_bound
  │      └── Witness synthesis via Optimize() / Solver() binary search
  │
  └──▶ Pure-Python Fallback Solver (zero external dependencies)
         ├── Fixed-point interval + linear inequality + CRT modular propagation
         ├── Subtask satisfiability & containment counterexample extraction
         └── Bounded backtracking witness synthesis + direct AST verification
```

- **Tradeoff on dynamic `z3` loading vs static `import z3`**:
  - Loading `z3` via `importlib.import_module("z3")` and converting solver values strictly to Python `int`/`bool`/`str` ensures `mypy --strict` passes under BOTH `--extra dev` (without `z3-solver`) and `--all-extras` (with `z3-solver`) without `# type: ignore` or `cast(`.
- **Tradeoff on partial subtask overrides**:
  - Real problems (e.g., `examples/communication-sample`) specify only the overridden variable in `subtask.constraints` (e.g., `{"mode": 0}` while inheriting `X in [0, 15]`). Checking containment only on bounds/variables explicitly constrained by the subtask avoids false positives on inherited variables while still catching `subtask.n_max > global.n_max` or `subtask.n_min < global.n_min`.
- **Single-source `constraints.h` (`rbx` pattern)**:
  - `generate_constraints_header(manifest)` emits deterministic `#ifndef AUTOCODE_CONSTRAINTS_H` guarded `constexpr std::int64_t` definitions (`<VAR>_MIN`, `<VAR>_MAX`, optional subtask constants) without `<bits/stdc++.h>`.
  - `check_constraints_header_drift(header_source, manifest)` strips comments, evaluates C++ constant expressions safely without `eval()`, recognizes canonical `<VAR>_MIN`/`<VAR>_MAX` and common aliases (`MAX_<VAR>`, `MAX<VAR>`), and reports value mismatches and missing constants.

### 2. Zero-Dependency Local Originality Detector (`originality.py`)

```text
User-supplied corpus (JSONL / Markdown dir / ProblemDocument list)
  │  safe path containment (realpath under root, followlinks=False, bounded read size)
  ▼
Normalization + Tokenization (ASCII Porter-lite stemming + CP stopword filter + CJK bigrams)
  │
  ├──▶ Okapi BM25 (non-negative log1p IDF, k1=1.5, b=0.75, query-self normalized)
  ├──▶ Word 1-2 n-gram TF-IDF cosine similarity
  ├──▶ Char 3-4 n-gram TF-IDF cosine similarity
  └──▶ Optional dense Embedder protocol (e.g., CPRetriever-Prob)
         │
         ▼
OriginalityReport (top-k OriginalityMatch with score, component scores, overlapping_terms, risk_level)
```

- **Tradeoff on zero-dependency lexical fusion + optional `Embedder` protocol**:
  - Pure-Python BM25 + word/char n-gram cosine similarity handles paraphrased English statements and CJK statements with zero runtime package bloat, while `Embedder` lets users attach `CPRetriever` dense vectors without adding PyTorch/Transformers to core dependencies.

### 3. `_statement_consistency` Integration (`audit_constraints.py`)

- `_statement_consistency` runs `audit_manifest_constraints(manifest, validator_bounds=val_bounds)` and, when `files/constraints.h` exists safely under `problem_path`, runs `check_constraints_header_drift(header_path, manifest)`.
- Contradictory SMT/subtask constraints or `files/constraints.h` drift force `passed = False` and `needs_human_review = True`, appending human-readable diagnostics to `message` and `review_reason` and exposing structured `smt_constraints` and `constraints_header_drift` dictionaries alongside all preserved Issue #16 keys.

## Risks / Mitigations

- **Z3 solver timeout or nonlinear expressions**: Both Z3 `Solver` and `Optimize` configure a bounded `timeout` (`2000 ms`), falling back cleanly to Solver bisection and the pure-Python verifier if an expression yields `unknown`.
- **Untrusted corpus directories with escaping symlinks or giant files**: `OriginalityIndex.from_directory` uses `os.walk(..., followlinks=False)`, checks `os.path.realpath` containment inside `realpath(corpus_dir)`, decodes with `errors="replace"`, and enforces a per-file size cap (`2 MiB`).
- **Standalone zero-write invariant**: `audit_constraints.py` performs strictly read-only inspection (`_safe_file`) and never generates or writes `files/constraints.h` during audits.
