## Purpose

Provide objective, evidence-only problem difficulty signals, inter-procedural C++ static complexity and competitive-programming idiom recognition, and optional deterministic instruction-count complexity verification with zero-dependency fallbacks.

## Requirements

### Requirement: Evidence-only difficulty signals invariant to brute exponent and wrong-solution count
The system SHALL compute `_difficulty_signals` strictly from objective problem and main-solution evidence without mapping time complexity or wrong-solution counts to numeric Elo/CF ratings, returning `rating: None`, `band: "uncalibrated"`, `calibration_required: True`, `rating_method: "evidence_only_v2"`, completeness-based `confidence` in `[0.0, 1.0]`, `structural_evidence`, and LiveCodeBench-Pro-inspired `cognitive_dimensions` (`knowledge_signals`, `logic_signals`, `observation_signals`).

#### Scenario: O(n) and O(2^n) solutions produce uncalibrated evidence invariant to wrong solutions
- **WHEN** `ProblemAuditTool._difficulty_signals` evaluates an `O(n)` solution or `O(2^n)` brute, or when extra `wrong` solutions are added to `manifest.solutions`
- **THEN** `rating` SHALL be `None`, `band` SHALL be `"uncalibrated"`, `calibration_required` SHALL be `True`, `rating_method` SHALL be `"evidence_only_v2"`, and `confidence` SHALL NOT increase from added wrong solutions or higher brute complexity

### Requirement: Inter-procedural static complexity and Chinese competitive-programming idioms
The system SHALL statically analyze C++ solutions using an optional `tree-sitter-cpp` CST backend when installed and an enhanced pure-Python brace-scoped function and call-graph fallback otherwise, propagating loop nesting depth across helper calls and non-DFS/non-bisection recursive helper chains, recognizing standard Chinese CP idioms (Fenwick `lowbit`/`x & -x`, Segment Tree `ls`/`rs`/`pushup`/`pushdown`/midpoint, binary lifting `f[i][j]`/`fa[i][j]` with `1 << j`, bitmask DP, two pointers, monotonic stack/queue, DSU, Dijkstra/BFS/DFS), and escalating `lower_bound`/`upper_bound` to `O(n log n)` only when invoked inside an effective loop.

#### Scenario: Helper loop inside outer loop escalates while top-level lower_bound does not
- **WHEN** `estimate_static_complexity` analyzes `sol()` calling `inner_loop()` inside a `for` loop, vs a standalone top-level `lower_bound` without enclosing algorithmic loops, vs a `lowbit`/`pushup` Fenwick/Segment Tree solution
- **THEN** the helper-in-loop solution SHALL be classified as `O(n^2)`, the top-level `lower_bound` SHALL NOT be forced to `O(n log n)`, and Fenwick/Segment Tree/binary-lifting tags SHALL be emitted in `patterns` even without English `fenwick`/`segtree` identifiers

### Requirement: Optional deterministic instruction counting with CPU/wall-time fallback
The system SHALL support `AUTOCODE_METRIC=auto|cpu_time|perf|cachegrind` in `DynamicExecutionMonitor` with safe `perf stat -x , -e instructions` and `valgrind --tool=cachegrind` (`I refs`) builders/parsers surfacing `instruction_count` and `metric_backend` while falling back cleanly to CPU/wall time whenever tools are unavailable or fail permissions, and `EmpiricalRatioAnalyzer` SHALL prefer valid `instruction_count` across scales before falling back to `cpu_time_ms` or `wall_time_ms`.

#### Scenario: Instruction count drives noise-free fitting when present and falls back cleanly when absent
- **WHEN** `DynamicExecutionMonitor` fails permissions or lacks `perf`/`valgrind`, or when `EmpiricalRatioAnalyzer.verify_complexity` receives samples containing valid `instruction_count` alongside noisy flat `cpu_time_ms` (or only `wall_time_ms`)
- **THEN** `DynamicExecutionMonitor` SHALL fall back to `metric_backend="cpu_time"` with `instruction_count=None` without erroring, and `EmpiricalRatioAnalyzer` SHALL fit asymptotic scaling using `instruction_count` (`fit_metric="instruction_count"`) when consistently present across scales or seamlessly fall back to `cpu_time_ms` / `wall_time_ms`
