## 1. OpenSpec & Packaging Baseline

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/complexity-ast-and-difficulty/spec.md` under `openspec/changes/2026-11-complexity-ast-and-difficulty-refactor/`.
- [x] 1.2 Add optional `ast = ["tree-sitter>=0.23", "tree-sitter-cpp>=0.23"]` dependency group under `[project.optional-dependencies]` in `pyproject.toml`.

## 2. Inter-Procedural Static Complexity & Chinese CP Idioms (`src/autocode_mcp/tools/complexity.py`)

- [x] 2.1 Implement optional `tree-sitter-cpp` CST analyzer (via `importlib`, zero `# type: ignore`) plus enhanced pure-Python brace-scoped function & call-graph analyzer with Tarjan SCC propagation so helper calls in loops and non-DFS recursive helper chains accumulate nesting depth while multitest `while (T--) solve()` drivers and logarithmic loops remain accurate.
- [x] 2.2 Add Chinese CP idiom recognizers in `detect_algorithm_patterns` / `estimate_static_complexity`: Fenwick tree (`lowbit`, `x & -x`), Segment Tree (`ls`/`rs`/`pushup`/`pushdown`/midpoint split), binary lifting (`f[i][j]`/`fa[i][j]` with `1 << j`), bitmask DP, two pointers, monotonic stack/queue, DSU, and Dijkstra/BFS/DFS.
- [x] 2.3 Fix `lower_bound`/`upper_bound`/`binary_search` false bump in `estimate_static_complexity` so top-level single calls stay `O(log n)` (or `O(n)` with an independent linear loop) and escalate to `O(n log n)` only when invoked inside an effective loop (directly or inter-procedurally).

## 3. Evidence-Only Difficulty Signals (`src/autocode_mcp/tools/audit_signals.py`)

- [x] 3.1 Remove the `O(1)=800 ... O(n!)=2600` table, `_difficulty_band`, and `wrong_count >= 2 -> +100` / `limit_ratio >= 0.5 -> +100` increments from `_difficulty_signals`.
- [x] 3.2 Return evidence-first payload (`rating: None`, `band: "uncalibrated"`, `calibration_required: True`, `rating_method: "evidence_only_v2"`, completeness-based `confidence` in `[0.0, 1.0]`, `structural_evidence`, `cognitive_dimensions`, and backward-compatible `constraint_scale` / `estimated_complexity` / `algorithm_tags` / `reasons`), invariant to wrong-solution additions or brute exponent increases.

## 4. Deterministic Instruction Counting & Fitting (`execution_monitor.py`, `ratio_analyzer.py`, `complexity.py`)

- [x] 4.1 Implement `AUTOCODE_METRIC=auto|cpu_time|perf|cachegrind` handling, safe `perf stat -x , -e instructions` and `valgrind --tool=cachegrind` (`I refs`) command builders and parsers, `instruction_count` / `metric_backend` fields, and clean `cpu_time` fallback in `DynamicExecutionMonitor`.
- [x] 4.2 Update `EmpiricalRatioAnalyzer` in `src/autocode_mcp/utils/ratio_analyzer.py` and `run_empirical_verification` in `src/autocode_mcp/tools/complexity.py` to prefer valid `instruction_count` across scales for noise-free ratio fitting and fall back seamlessly to `cpu_time_ms` / `wall_time_ms`.

## 5. Skills, Tests & Full Verification

- [x] 5.1 Update `skills/problem-difficulty-rating/SKILL.md` and `skills/solution-complexity-audit/SKILL.md` to reflect evidence-only difficulty calibration, inter-procedural AST/idiom analysis, and deterministic instruction counting.
- [x] 5.2 Create `tests/test_complexity_ast_and_difficulty.py` (passing without `tree-sitter-cpp`, `perf`, or `valgrind`) and update `tests/test_audit.py:112` for the uncalibrated `difficulty_signals` contract.
- [x] 5.3 Run `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q`, and verify `git diff --name-only` matches the whitelist.
