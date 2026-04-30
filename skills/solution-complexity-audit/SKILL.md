---
name: solution-complexity-audit
description: Audit std/brute assumptions with MCP evidence, including worst/average complexity risk and stress readiness.
disable-model-invocation: false
---

# Solution Complexity Audit

Used in the post-implementation audit stage after std/brute are available. You must provide structured evidence; verbal claims are not sufficient.

## Execution Order

Call tools in this order and record the results:

1. `solution_analyze`: estimate std time/space complexity and record worst/average risks.
2. `solution_audit_std`: verify whether `claimed_complexity` conflicts with estimated complexity and constraints.
3. `solution_audit_brute`: confirm brute is suitable as a stress oracle and derive `n_max` and `trials`.

## Required Checks

- Whether std has `high_tle_risk` or obvious boundary flaws.
- Whether brute truly serves as a conservative correctness oracle rather than the same class of implementation as std.
- Whether complexity conclusions match statement constraints (especially `n_max` and total scale).
- Whether executable `stress_profiles` recommendations are produced.

## Pass Criteria (all required)

- std has no high-risk `high_tle_risk`.
- brute provides explicit `recommended_stress_params`.
- Stress parameters are written into the follow-up `stress_test_run` plan.

## Output Format

- `decision`: `go` / `no_go`
- `findings`: structured issue list (including severity)
- `recommended_stress_params`: recommended stress parameters

## Failure Handling

- If high-risk items exist, fix the algorithm or constraints first, then rerun the full audit chain.
- Do not skip this audit and proceed directly to final test generation.

## Decision Rules

- `go`: no unresolved `critical` findings and stress parameters are explicit and executable.
- `no_go`: any unresolved `critical` finding, or brute is not a trustworthy stress oracle.
