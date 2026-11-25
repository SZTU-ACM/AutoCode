## Why

Two interconnected diagnostic layers in AutoCode (`src/autocode_mcp/tools/audit_signals.py`, `src/autocode_mcp/tools/complexity.py`, and the dynamic scaling pipeline in `execution_monitor.py` / `ratio_analyzer.py`) currently suffer from structural distortion and host-noise fragility (GitHub Issue #19 and Pool C):

1. **Distorted "slower = harder" difficulty rating (`audit_signals.py`, Issue #19)**: `_difficulty_signals` maps `estimated_complexity` directly to a fake CF-style rating (`O(1)=800 ... O(2^n)=2400`, `O(n!)=2600`) and mechanically adds `+100` for `wrong_count >= 2` or `limit_ratio >= 0.5`. This misclassifies hard `O(n)` constructive/math problems as 1100 beginner tasks, scores trivial `O(2^n)` small-constraint brute problems at 2400+, and lets authors artificially inflate difficulty by adding extra wrong solutions.
2. **Brittle single-function regex static complexity (`complexity.py`, Issue #19)**: `_analyze_loop_nesting` only measures syntactic loop depth within individual function bodies without call-graph propagation (extracting an inner loop into a helper called inside an outer loop drops `O(n^2)` to `O(n)`). `detect_algorithm_patterns` also misses common Chinese competitive-programming idioms (`lowbit` / `x & -x` Fenwick trees, `ls`/`rs`/`pushup`/`pushdown` segment trees, binary lifting `f[i][j]`/`fa[i][j]`, DSU, two pointers, monotonic stack/queue) while unconditionally forcing a single top-level `lower_bound`/`upper_bound` to `O(n log n)`.
3. **Host CPU noise in empirical complexity verification (`execution_monitor.py`, `ratio_analyzer.py`, Pool C)**: `DynamicExecutionMonitor` relies exclusively on wall/CPU time, which is susceptible to OS timer granularity (`15.625ms` on Windows), container scheduling jitter, and background load. Optional hardware/VM instruction counting (`perf stat -e instructions` on Linux and `valgrind --tool=cachegrind` `I refs`) provides noise-free asymptotic scaling curves when available, with safe fallback to CPU/wall time.

## What Changes

- **Evidence-only `_difficulty_signals` (`src/autocode_mcp/tools/audit_signals.py`)**:
  - Remove the `O(1)=800 ... O(n!)=2600` complexity-to-rating lookup table, `_difficulty_band`, and `wrong_count >= 2` / `limit_ratio >= 0.5` mechanical bumps.
  - Return an evidence-first structure: `rating: None`, `band: "uncalibrated"`, `calibration_required: True`, `rating_method: "evidence_only_v2"`, `confidence` (purely derived from evidence completeness in `[0.0, 1.0]`), `estimated_complexity`, `algorithm_tags`, `reasons`, `structural_evidence` (static & empirical complexity, `n_max`, `largest_value_bound`, TL headroom ratio, `wrong_solution_count`, and wrong-kill details), and `cognitive_dimensions` (`knowledge_signals`, `logic_signals`, `observation_signals` inspired by LiveCodeBench Pro), preserving backward-compatible `constraint_scale`, `implementation_evidence`, and `data_strength` fields.
- **Inter-procedural CST/Call-Graph Static Analysis & Chinese CP Idioms (`src/autocode_mcp/tools/complexity.py`, `pyproject.toml`)**:
  - Add optional `[project.optional-dependencies] ast = ["tree-sitter>=0.23", "tree-sitter-cpp>=0.23"]` in `pyproject.toml`.
  - Implement an optional `tree-sitter-cpp` CST inter-procedural analyzer (loaded dynamically when installed) plus an always-available pure-Python brace-scoped function & call-graph analyzer using Tarjan SCCs so helper calls inside loops and non-DFS/non-D&C recursive helper chains accurately accumulate nesting depth (`sol()` calling `inner_loop()` in a `for` loop -> `O(n^2)`), while multi-test `while (T--) solve()` drivers and logarithmic Fenwick/binary-search loops do not falsely inflate complexity.
  - Add Chinese CP idiom recognizers: Fenwick tree (`lowbit`, `x & -x`), Segment Tree (`ls`/`rs`/`pushup`/`pushdown`/midpoint split), binary lifting (`f[i][j]`/`fa[i][j]` with `1 << j`), bitmask DP, two pointers, monotonic stack/queue, DSU, Dijkstra/BFS/DFS.
  - Fix `lower_bound`/`upper_bound`/`binary_search` escalation in `estimate_static_complexity`: only escalate to `O(n log n)` when invoked inside an effective loop (directly or via a helper called inside a loop); top-level single `lower_bound` stays `O(log n)` (or `O(n)` if accompanied by an independent linear pass).
- **Deterministic Instruction Counting & Fitting (`src/autocode_mcp/utils/execution_monitor.py`, `src/autocode_mcp/utils/ratio_analyzer.py`, `src/autocode_mcp/tools/complexity.py`)**:
  - Support `AUTOCODE_METRIC=auto|cpu_time|perf|cachegrind` in `DynamicExecutionMonitor`, with safe `perf stat -x , -e instructions` and `valgrind --tool=cachegrind` (`I refs`) command builders & parsers, surfacing `instruction_count: int | None` and `metric_backend: str`, and falling back cleanly to `cpu_time` on macOS/Windows or permission/tool errors.
  - Propagate `instruction_count`, `wall_time_ms`, and `metric_backend` in `run_empirical_verification` samples and update `EmpiricalRatioAnalyzer` to prefer valid `instruction_count` across scales for noise-free log-linear/exponential fitting before falling back seamlessly to `cpu_time_ms` / `wall_time_ms`.
- **Skill Documentation & Regressions (`skills/problem-difficulty-rating/SKILL.md`, `skills/solution-complexity-audit/SKILL.md`, `tests/test_complexity_ast_and_difficulty.py`, `tests/test_audit.py`)**:
  - Update skill guides for evidence-only difficulty calibration and AST/instruction-based complexity auditing.
  - Add comprehensive unit tests passing without `tree-sitter-cpp`, `perf`, or `valgrind` installed, plus update the single legacy test assertion in `tests/test_audit.py:112` that asserted the removed `rating >= 800` formula.

## Capabilities

### New Capabilities

- `complexity-ast-and-difficulty`: Inter-procedural C++ static complexity and Chinese CP idiom analysis (optional `tree-sitter-cpp` CST + pure-Python call-graph fallback), evidence-only difficulty signal extraction (`evidence_only_v2` with LiveCodeBench Pro cognitive dimensions and deterministic invariance), and optional instruction-count empirical scaling (`perf`/`cachegrind` with CPU/wall-time fallback).

### Modified Capabilities

- None (`openspec/specs/` canonical specs remain intact; `solution_analyze`, `solution_audit_std`, `solution_audit_brute`, and `problem_audit` external MCP signatures and `CLAUDE.md` 12-step gating order are unchanged).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12 workflow stages, SHA-256 gating, and all 22 MCP tool signatures (`solution_analyze`, `solution_audit_std`, `solution_audit_brute`, `problem_audit`). Preserves `difficulty_signals.get("confidence", 0.0)` consumed by `ProblemAuditTool` (`audit.py:334`).
- **Affected Files**:
  - `src/autocode_mcp/tools/audit_signals.py` (`_difficulty_signals` and dedicated helpers only)
  - `src/autocode_mcp/tools/complexity.py`
  - `src/autocode_mcp/utils/execution_monitor.py`
  - `src/autocode_mcp/utils/ratio_analyzer.py`
  - `skills/problem-difficulty-rating/SKILL.md`
  - `skills/solution-complexity-audit/SKILL.md`
  - `pyproject.toml` (`[project.optional-dependencies] ast`)
  - `tests/test_complexity_ast_and_difficulty.py` (new) and `tests/test_audit.py:112`
- **Dependencies**: Optional extra `ast = ["tree-sitter>=0.23", "tree-sitter-cpp>=0.23"]`; core zero-extra-dependency runtime preserved.
- **Risks**:
  - *Missing optional tools/packages*: Mitigated via `importlib` dynamic loading for `tree-sitter-cpp` with pure-Python call-graph fallback and automatic fallback to `cpu_time` whenever `perf` / `valgrind` is absent or lacks permissions (including macOS/Windows).
  - *`while (T--) solve()` false quadratic depth*: Mitigated by applying multitest driver loop exclusion and logarithmic Fenwick/binary-search loop classification prior to inter-procedural depth propagation.
  - *Instruction startup CRT plateau*: Mitigated in `EmpiricalRatioAnalyzer` via unit-agnostic relative startup plateau detection when CRT instructions dominate small scales.

## Non-goals

- Do NOT modify any of the 22 MCP tool signatures or schemas (`server.py`, `tools/schemas.py`).
- Do NOT touch `workflow/models.py`, `workflow/gates.py`, `tools/audit.py`, `tools/audit_constraints.py`, `tools/audit_discovery.py`, `tools/audit_standalone.py`, `tools/verify_*.py`, or `cli/*`.
- Do NOT modify `JudgeRunner` or `stress_test_run`, and do NOT train or embed an ML/LightGBM difficulty regressor in this change.
