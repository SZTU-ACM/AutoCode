---
name: testdata-quality
description: "Verify final tests with hard quality gates: integrity, consistency, validator, limit semantics, and wrong-solution kill."
disable-model-invocation: false
---

# Test Data Quality Skill

Final test data must pass:

1. `problem_verify_tests` checks: `file_count` / `answer_consistency` / `validator` / `no_empty`. When `autocode.json` has `special_judge: true` **and** `stress_comparison: "checker"`, `answer_consistency` and `wrong_solution_kill` use `files/checker` (testlib); with `stress_comparison: "exact"` they still compare strings to `.ans`.
2. `limit_ratio` and `limit_semantics` (type=3 and type=4 must not be semantically overlapping).
3. `wrong_solution_kill`: for each `role=wrong` solution in `autocode.json`, behavior depends on `expected` (default `fail`). **`expected=fail`**: at least one final test must be non-AC under checker mode, or not match `.ans` under exact mode (wrong solution must be "killed"). **`expected=pass`**: every final test must be AC / match `.ans` (e.g. alternate valid output). The tool reports `killed`, `expected`, and a `hint` per wrong solution; interpret them together.

## Additional Required Checks

- Whether test points contain obvious duplicates (low information gain).
- Whether type=3 and type=4 are semantically distinct rather than simple parameter scaling.
- Whether pass/fail behavior of reference/wrong solutions matches expected roles.

## Output Template

- `decision`: `go` / `no_go`
- `failed_checks`: list of failed checks (with reasons)
- `coverage_report`: scale distribution, limit-case ratio, and wrong-solution kill statistics
- `repair_priority`: fix priority (`P0` / `P1` / `P2`)
- `reverify_plan`: re-validation order after fixes

## Decision Rules

- `go`: all required checks pass, limit semantics are valid, and wrong-solution checks match each entry's `expected` semantics (not only the legacy "always killed by one test" reading).
- `no_go`: any required check fails or coverage quality is insufficient.

## Forbidden Behavior

- Do not approve final tests if any required check fails.
- Do not accept type=3 and type=4 when semantics are effectively identical.
- Do not skip wrong-solution-kill validation when wrong solutions are available.
