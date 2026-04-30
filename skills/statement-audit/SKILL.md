---
name: statement-audit
description: Audit statement, tutorial, and samples for consistency and publication readiness before packaging.
disable-model-invocation: false
---

# Statement Audit Skill

Use `problem_validate` to check statement and sample files, ensuring the problem documentation is contest-ready.

## Checkpoints

- Input/output protocol is complete and unambiguous.
- Sample input/output matches actual execution of `sol`.
- `tutorial.md` includes approach, correctness, and complexity (not placeholder text).
- Special judging rules (checker/interactor) are explicitly documented in the statement.

## Additional Requirements

- Terminology and variable naming stay consistent across statement, editorial, and code.
- Samples include at least one boundary case.
- For interactive problems, the statement must define interaction order, flush/termination conditions, and consequences of invalid output.

## Output Format

- `decision`: `go` / `no_go`
- `doc_issues`: documentation issue list
- `sample_issues`: sample issue list
- `rewrite_targets`: section titles that require rewriting

## Decision Rules

- `go`: statement protocol is complete and all validated samples match expected behavior.
- `no_go`: any ambiguity, mismatch, or missing checker/interactor rule remains.

## Forbidden Behavior

- Do not approve statements with ambiguous I/O protocol.
- Do not approve when sample evidence is missing or inconsistent with `sol`.
- Do not approve interactive statements without complete protocol semantics.
