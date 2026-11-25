---
name: statement-audit
description: Audit statement, tutorial, formal constraints, ambiguity, and samples for consistency and publication readiness before packaging.
---

# Statement Audit Skill

Use `problem_validate` and `problem_audit` to check statement, constraint consistency, ambiguity, and sample files, ensuring the problem documentation is contest-ready.

## Trigger Conditions

- Before freezing test data or running `pack_polygon` / final `problem_audit`.
- Whenever the statement, constraints, `files/constraints.h`, `files/val.cpp`, or samples are created or edited.

## Checkpoints

- Statement section order follows this sequence: title -> time/memory limits -> optional background -> problem description -> input format (with all variable constraints) -> output format -> samples (numbered ascending) -> explanation.
- Input/output protocol is complete and unambiguous.
- Sample input/output matches actual execution of `sol`.
- All sample explanations appear only in the explanation section (not embedded under individual samples); only representative samples need explanation.
- `tutorial.md` includes approach, correctness, and complexity (not placeholder text).
- Special judging rules (`checker`/`interactor`) are explicitly documented in the statement.

## Orchid 4D Ambiguity Checklist (`arXiv:2604.21505`)

Audit every statement systematically across all four Orchid ambiguity dimensions:

1. **Lexical Ambiguity**: Check domain terms and math symbols for dual meanings (e.g., "subsequence" vs "subarray/substring", "path" vs "simple path", "tree" vs "rooted tree", "increasing" vs "strictly increasing", "degree" vs "in-degree/out-degree", $0$-indexed vs $1$-indexed positions).
2. **Syntactic Ambiguity**: Check quantifier scope, prepositional attachment, and expression grouping (e.g., "for all $x$ there exists $y$" vs "there exists $y$ for all $x$", "distinct pairs $(i, j)$" ordered vs unordered, operator precedence before/after modulo).
3. **Semantic Ambiguity**: Check behavioral edge cases and multi-answer rules (tie-breaking criteria, empty/zero inputs, negative values, disconnected graphs, self-loops or parallel edges, modulo applied before vs after comparison/optimization, adaptive vs oblivious adversary).
4. **Vague Boundaries**: Check numeric scale and boundary precision (no vague phrasing like "sufficiently large", explicit multi-test sum limits like $\sum n$, strict `<` vs inclusive `<=`, exact match between statement prose, `.autocode/manifest.json`, `files/constraints.h`, and `files/val.cpp`).

## Mandatory Anti-Semantic-Collapse Protocol (`arXiv:2607.01953`)

- Underspecification does not equal incoherence: a statement can look internally coherent and match all samples while admitting multiple valid readings.
- Before returning `go`, explicitly **enumerate a second plausible interpretation** of the problem statement (covering graph/sequence assumptions, edge inputs, or output acceptance criteria).
- Test whether both interpretations satisfy the provided samples yet diverge on legal boundary inputs. If they diverge, return `decision: no_go` and list the exact ambiguity in `doc_issues` and `rewrite_targets`.

## Formal Constraints & Single-Source `constraints.h` Verification

- Verify via `audit_manifest_constraints` (`autocode_mcp.utils.smt_constraints`) that global constraints, `subtasks`, and extracted `val.cpp` bounds are mutually satisfiable and every subtask stays strictly within global bounds.
- Confirm boundary samples or `case_plan` extreme cases cover synthesized tight boundary witnesses (`min`, `max`, extreme parity/coupled values).
- If `files/constraints.h` is used as the single source of truth, verify with `check_constraints_header_drift` that header constants match `.autocode/manifest.json` without drift.

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

## Communication Statement Checklist

For `problem_type: "communication"`, require all items below before `go`:

- `## 通信协议` section is present and specifies the exact communication mode (`run_twice` / `grader_linked` / `multi_process`).
- Process isolation rules (no cross-pass globals, temp files, or shared state) or exact `problem.h` C++ function signatures are stated unambiguously.
- Numeric communication budgets (`max_bits`, `max_bytes`, `max_rounds`, `max_queries`) and over-budget verdict/scoring rules are stated explicitly.
- Channel transformation / noise / adversarial corruption model is defined (or explicitly stated as noiseless).
- Invalid output format, premature EOF, extra output, and multi-stage verdicts are defined.
- Sample section provides a complete multi-stage transcript (e.g., Pass 1 -> Channel -> Pass 2) consistent with `communicator.cpp` / `grader.cpp` and `sol.cpp`.
- If subtasks are defined in `manifest.json`, subtask scores, constraints, and dependencies match the statement.

## Output Format

- `decision`: `go` / `no_go`
- `doc_issues`: documentation and Orchid 4D ambiguity issue list
- `sample_issues`: sample issue list
- `second_interpretation`: documented second plausible interpretation and whether it diverges from the intended semantics
- `rewrite_targets`: section titles that require rewriting

## Decision Rules

- `go`: statement protocol is complete, no Orchid 4D ambiguity or diverging second interpretation exists, SMT/subtask constraints and `files/constraints.h` have zero conflicts/drift, and all validated samples match expected behavior.
- `no_go`: any ambiguity, diverging second interpretation, SMT/subtask conflict, `constraints.h` drift, sample mismatch, or missing checker/interactor/communication rule remains.

## Forbidden Behavior

- Do not approve statements with ambiguous I/O protocol or unresolved Orchid 4D ambiguity.
- Do not collapse underspecified text onto a single guessed reading without testing for a plausible second interpretation.
- Do not approve when sample evidence is missing or inconsistent with `sol`, or when `statement_consistency` reports SMT conflicts or `constraints.h` drift.
- Do not approve interactive or communication statements without complete protocol semantics.
