## Context

AutoCode runs in heterogeneous environments (Linux CI, macOS Apple Silicon, Windows) where optional native tools (`tree-sitter-cpp`, Linux `perf`, `valgrind`) may or may not be installed or permitted. Both static complexity estimation and empirical complexity fitting must therefore follow a strict **graceful enhancement + deterministic zero-dependency fallback** architecture while dismantling pseudo-quantitative rating formulas.

Naming boundaries remain strict:
- MCP tools and domain analyzers live in `src/autocode_mcp/tools/{complexity,audit_signals}.py`.
- Execution monitoring and empirical regression utilities live in `src/autocode_mcp/utils/{execution_monitor,ratio_analyzer}.py`.
- Client guidance lives in `skills/{problem-difficulty-rating,solution-complexity-audit}/SKILL.md`.

## Architecture & Design Decisions

### 1. Evidence-Only Difficulty Signals (`src/autocode_mcp/tools/audit_signals.py`)

```text
_difficulty_signals(problem_path, manifest, quality_signals)
  ├── rating = None, band = "uncalibrated", calibration_required = True
  ├── rating_method = "evidence_only_v2"
  ├── confidence ∈ [0.0, 1.0]  <-- purely evidence completeness (sol, constraints, signals, statement)
  ├── structural_evidence      <-- static vs empirical complexity, n_max, value_bound, TL headroom, wrong_kill
  ├── cognitive_dimensions     <-- LiveCodeBench Pro inspired:
  │     ├── knowledge_signals  (algorithms, CP data structures, mod/value constraints)
  │     ├── logic_signals      (call-graph depth, loop depth, branch/dim structure, protocol type)
  │     └── observation_signals (low-complexity high-constraint pressure, trap kill evidence, LLM calibration note)
  └── Invariance guarantee: adding wrong solutions or raising brute exponent NEVER inflates rating/confidence
```

- **Tradeoff**: Keeping legacy read keys (`estimated_complexity`, `algorithm_tags`, `constraint_scale`, `implementation_evidence`, `data_strength`, `reasons`, `confidence`) alongside `structural_evidence` and `cognitive_dimensions` preserves `ProblemAuditTool` (`audit.py:334`) and existing scale tests (`test_audit.py:499-551`) without touching forbidden files. Setting `rating=None` and `band="uncalibrated"` removes the false authority of `O(n)=1100` and `wrong_count +100` while surfacing objective evidence for LLM/human calibration.

### 2. Dual-Backend Inter-Procedural Static Analyzer (`src/autocode_mcp/tools/complexity.py`)

```text
C++ Source
  ├── Backend A (optional): tree_sitter + tree_sitter_cpp CST (via importlib when installed)
  └── Backend B (always):   _strip_cpp_comments_and_strings + brace-scoped Function/Call-Graph scanner
          │
          ▼  Normalized per-function summary: own_depth, log_loops, calls[(callee, loop_depth)], bsearch_depths
  _solve_call_graph (Tarjan SCC condensation + DAG propagation)
          ├── Multitest drivers (`while (T--)`) ignored at root depth 0
          ├── Small constant bounds (<= 32) ignored when `ignore_small_const=True`
          ├── Logarithmic loops (`x += x & -x`, `lowbit`, `l <= r` mid halving, `>>= 1`) stay O(log n)
          ├── Tree/Graph DFS & D&C/SegTree SCCs avoid false quadratic explosion
          ├── Helper calls in loops & non-DFS recursive helper chains accumulate nesting depth
          └── `lower_bound` / `upper_bound` escalates to O(n log n) ONLY when effective call depth >= 1
```

- **Tradeoffs**:
  - Loading `tree_sitter` and `tree_sitter_cpp` via `importlib.import_module` satisfies `mypy --strict` without `# type: ignore` even when `[ast]` is uninstalled, while allowing exact CST parsing when installed.
  - Keeping `detect_algorithm_patterns()` backward-compatible for direct unit tests while applying contextual `lower_bound` loop-depth filtering and inter-procedural analysis inside `estimate_static_complexity()` prevents breaking legacy pattern-recognition expectations while fixing real-world static complexity estimates.
  - Using Tarjan's SCC condensation guarantees linear-time termination on arbitrary recursive or mutually recursive call graphs.

### 3. Deterministic Instruction Counting & Fitting (`execution_monitor.py`, `ratio_analyzer.py`)

```text
DynamicExecutionMonitor.run_monitored_process(..., metric=None)
  ├── resolve_metric_backend(metric or AUTOCODE_METRIC) -> auto | cpu_time | perf | cachegrind
  ├── auto: Linux + `perf` in PATH -> try `perf` -> fallback `cpu_time` on error/permission/no-count
  ├── perf: `perf stat -x , -e instructions -o <tmp_perf> -- <cmd>` (sum PMU lines; reject `<not counted>`)
  ├── cachegrind: `valgrind --tool=cachegrind --cachegrind-out-file=/dev/null --log-file=<tmp_cg> <cmd>` -> parse `I refs`
  └── Always return `instruction_count: int | None` and `metric_backend: str`; never write to problem_dir

EmpiricalRatioAnalyzer.verify_complexity / fit_log_linear / fit_exponential
  ├── Active metric priority: `instruction_count` (when all fittable ok samples carry >0 ints)
  │     └── No OS timer noise-floor or cache-jump exemptions; relative CRT plateau subtraction supported
  └── Fallback metric: `cpu_time_ms` -> `wall_time_ms` (preserving existing OS timer noise-floor & cache-jump safeguards)
```

- **Tradeoffs**:
  - Writing `perf -o` and `valgrind --log-file` strictly into a system `TemporaryDirectory` (and `--cachegrind-out-file=os.devnull`) guarantees zero side-effect writes into `problem_dir` (honoring standalone zero-write guarantees) and prevents wrapper diagnostics from corrupting program `stderr`.
  - In `auto` mode, only `perf` (near-native overhead) is probed automatically on Linux; `cachegrind` (10x–100x slowdown) requires explicit `AUTOCODE_METRIC=cachegrind` so multi-scale `auto` runs do not falsely hit `time_limit_ms`.
  - If a wrapper fails before or during measurement (`instruction_count is None` and `status == "runtime_error"`), `run_monitored_process` re-executes the unwrapped command cleanly under `cpu_time` so `perf_event_paranoid` restrictions never misreport a healthy solution as `runtime_error`.

## Risks / Mitigations

- **Legacy rating test expectation**: `tests/test_audit.py:112` explicitly asserted `rating >= 800`. Updated minimally to assert the new uncalibrated evidence-only contract (`rating is None`, `band == "uncalibrated"`, `calibration_required is True`, `rating_method == "evidence_only_v2"`).
- **Valgrind `--` option parsing**: Some `valgrind` versions reject `--` before the executable; resolved by resolving the target executable to an absolute path via `_abs_cmd` (never starting with `-`) instead of passing `--` to `valgrind`.
- **Headroom calculation under `instruction_count`**: `max_scale_headroom_ratio` in `EmpiricalRatioAnalyzer.verify_complexity` continues using `cpu_time_ms` (or `wall_time_ms`) divided by `time_limit_ms` so instruction counts never distort time-limit headroom ratios.
