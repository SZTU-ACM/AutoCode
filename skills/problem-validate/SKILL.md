---
name: problem-validate
description: Validate statement samples and sample files for competitive programming problems. Ensures the expected outputs in problem statements match the actual solution output.
disable-model-invocation: false
---

# Problem Validation Skill

Use this skill to enforce sample correctness before final test generation.

## Trigger Conditions

Use when:

- `stress_test_run` has passed and the next step is sample verification;
- the user updates statement samples or sample files;
- workflow is blocked by `problem_validate` or sample mismatch.

## Core Instructions

1. Run `problem_validate` against statement samples and sample files.
2. Treat any mismatch as a release blocker.
3. Classify failures by source: statement text, sample files, or solution behavior.
4. Re-run validation after fixes before allowing progression.

## Output Contract

- `decision`: `go` / `no_go`
- `blocking_issues`: validation blockers that must be fixed
- `next_actions`: exact re-validation steps after fixes

## Forbidden Behavior

- Do not proceed to `problem_generate_tests` while validation is failing.
- Do not approve based on file presence without validation evidence.
- Do not rewrite expected outputs unless the corrected output is justified.

## Decision Rules

- `go`: all selected validation types pass without unresolved mismatches.
- `no_go`: any selected validation type fails, or validation evidence is incomplete.

## Additional Resources

For tool examples, detailed output schemas, error recovery playbooks, and comparison rules, see [reference.md](reference.md).
