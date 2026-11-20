---
name: problem-validate
description: Validate statement samples and sample files for competitive programming problems. Ensures the expected outputs in problem statements match the actual solution output.
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
2. When `manifest.json` has `special_judge: true`, `stress_comparison: "checker"`, and a compiled `files/checker`, sample checks use the checker (same argv convention as final verification); otherwise outputs are compared with the usual float/string rules.
3. For interactive and communication problems, do not treat transcript samples as ordinary stdin/stdout:
   - Interactive: validate protocol text with `statement-audit`, run live `InteractiveRunner` against sample files and `statement_samples` (expected_output is optional), and verify `interactor_build` `interaction_scenarios`.
   - Communication: validate `## 通信协议` and multi-stage transcripts with `statement-audit`, run live `CommunicationRunner` (`run_twice` / `grader_linked` / `multi_process`) against sample files / `statement_samples`, and confirm communication budgets (`max_bits` / `max_bytes` / `max_rounds` / `max_queries`) are satisfied.
4. Treat any mismatch or protocol/budget violation as a release blocker.
5. Classify failures by source: statement text, sample files, solution behavior, checker, or interactor/communicator/grader protocol.
6. Re-run validation after fixes before allowing progression.

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
