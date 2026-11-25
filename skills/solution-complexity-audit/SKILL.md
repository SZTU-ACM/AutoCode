---
name: solution-complexity-audit
description: Audit std/brute assumptions with MCP evidence, inter-procedural AST/idiom analysis, optional instruction counting, and stress readiness.
---

# Solution Complexity Audit

Used in the post-implementation audit stage after std/brute are available. You must provide structured evidence; verbal claims are not sufficient.

## Execution Order

Call tools in this order and record the results:

1. `solution_analyze`: estimate std time/space complexity via inter-procedural CST/call-graph analysis (using optional `tree-sitter-cpp` when installed via `.[ast]`, or the pure-Python brace-scoped call-graph fallback) plus Chinese CP idiom recognizers (Fenwick `lowbit`/`x & -x`, Segment Tree `ls`/`rs`/`pushup`/`pushdown`, binary lifting, bitmask DP, two pointers, monotonic stack/queue, DSU, Dijkstra/BFS/DFS, and loop-contextual `lower_bound`/`upper_bound`).
2. `solution_audit_std`: verify whether `claimed_complexity` conflicts with static complexity, constraints (`n_max`), and multi-scale empirical fitting (`empirical_verification`). When `AUTOCODE_METRIC=perf|cachegrind` (or `auto` on Linux with `perf`) is enabled, prefer `fit_metric="instruction_count"` for noise-free scaling verification, falling back cleanly to `cpu_time_ms` / `wall_time_ms`.
3. `solution_audit_brute`: confirm brute is suitable as a stress oracle and derive `n_max` and `trials` (including small-scale feasibility probe when `gen` + `brute` binaries exist).

## Required Checks

- Whether std has `high_tle_risk` or obvious boundary flaws (prioritize empirical multi-scale evidence and constraint pressure over standalone regex warnings when `gen` is built).
- Whether helper functions called inside loops or recursive helper chains alter effective nesting depth.
- Whether brute truly serves as a conservative correctness oracle rather than the same class of implementation as std.
- Whether complexity conclusions match statement constraints (especially `n_max` and dominant variable scale).
- Whether executable `stress_profiles` recommendations are produced.

## Output Format

- `decision`: `go` / `no_go`
- `findings`: structured issue list (including severity)
- `recommended_stress_params`: recommended stress parameters

## Failure Handling

- If high-risk items exist (especially empirical `ratio_mismatch` or `brute_runtime_crash`), fix the algorithm or constraints first, then rerun the full audit chain.
- Do not skip this audit and proceed directly to final test generation.

## Decision Rules

- `go`: no unresolved `critical` findings, `high_tle_risk` is `false` or `low`, and stress parameters are explicit and executable.
- `no_go`: any unresolved `critical` finding, or brute is not a trustworthy stress oracle.
