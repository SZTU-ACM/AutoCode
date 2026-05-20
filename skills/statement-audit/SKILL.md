---
name: statement-audit
description: Audit statement, tutorial, and samples for consistency and publication readiness before packaging.
disable-model-invocation: false
---

# Statement Audit Skill

Use `problem_validate` to check statement and sample files, ensuring the problem documentation is contest-ready.

## Checkpoints

- Statement section order follows this sequence: title -> time/memory limits -> optional background -> problem description -> input format (with all variable constraints) -> output format -> samples (numbered ascending) -> explanation.
- Input/output protocol is complete and unambiguous.
- Sample input/output matches actual execution of `sol`.
- All sample explanations appear only in the explanation section (not embedded under individual samples); only representative samples need explanation.
- `tutorial.md` includes approach, correctness, and complexity (not placeholder text).
- Special judging rules (checker/interactor) are explicitly documented in the statement.

## Additional Requirements

- Terminology and variable naming stay consistent across statement, editorial, and code.
- Samples include at least one boundary case.
- For interactive problems, the statement must define the complete interaction contract: who outputs first, every query/final-answer command, judge response format and meaning, query limit, hidden parameter bounds, flush requirement, termination rule, and verdict consequences for invalid output, out-of-range parameters, too many queries, premature EOF, and missing flush.
- Interactive samples must be written as transcripts with judge/contestant sides clearly marked; do not approve a fake static input/output sample that hides the conversation order.

## Interactive Statement Checklist

For `interactive: true`, require all items below before `go`:

- `Input format` says the contestant does not receive ordinary static input, and lists the interactor's hidden input/range/randomness/adaptiveness.
- `Output format` lists every contestant command, including final answer syntax and whether the program must terminate immediately.
- `Interaction protocol` states the exact order of reads/writes for every round and the meaning of every judge response.
- Query/round limits are numeric, and the statement says what happens at the limit and after exceeding it.
- The flush rule is explicit: after each query/final answer the contestant must flush stdout.
- Illegal behavior verdicts are explicit: malformed command, invalid token, out-of-range argument, too many queries, early termination, continuing after final answer, and blocked protocol.
- Transcript examples identify lines from the judge and contestant and are consistent with the protocol.
- If the judge is adaptive, the maintained invariant is stated so contestants know what they are solving.

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
