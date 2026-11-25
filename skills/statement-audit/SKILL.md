---
name: statement-audit
description: Audit statement, tutorial, formal constraints, ambiguity, and samples for consistency and publication readiness before packaging.
---

# Statement Audit Skill

Use `problem_validate` and `problem_audit` to check statement, constraint consistency, ambiguity, and sample files, ensuring the problem documentation is contest-ready.

## Trigger Conditions

- Before freezing test data or running `problem_pack_polygon` / final `problem_audit`.
- Whenever the statement, constraints, `files/constraints.h`, `files/val.cpp`, or samples are created or edited.

## Checkpoints

- Statement section order: title -> time/memory limits -> optional background -> problem description -> input format (with all variable constraints) -> output format -> samples (numbered ascending) -> explanation (never mixed into sample blocks; only representative samples need explanation).
- Input/output protocol is complete, unambiguous, and matches `sol` execution on all samples (including at least one boundary case).
- `tutorial.md` includes approach, correctness, and complexity (no placeholder text), with terminology consistent across statement, editorial, and code.
- Special judging rules (`checker` / `interactor` / `communicator`) are explicitly documented.

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

## Protocol Requirements Summary

- **Interactive (`## 交互协议`)**: document hidden inputs, turn order, every query/final command, numeric query limits, `std::cout` stdout flush rule, termination rules, error verdicts, and judge/contestant transcripts (`registerInteraction`).
- **Communication (`## 通信协议`)**: document mode (`run_twice` / `grader_linked` / `multi_process`), isolation/`problem.h` signatures, numeric budgets (`max_bits` / `max_bytes` / `max_rounds` / `max_queries`), noise model, error verdicts, multi-stage transcripts, and `subtasks`.
- Full line-by-line interactive & communication audit checklists: [references/protocol-checklists.md](references/protocol-checklists.md).

## Output Format

- `decision`: `go` / `no_go`
- `doc_issues`: documentation and Orchid 4D ambiguity issue list
- `sample_issues`: sample issue list
- `second_interpretation`: documented second plausible interpretation and whether it diverges from intended semantics
- `rewrite_targets`: section titles that require rewriting

## Decision Rules

- `go`: statement protocol is complete, no Orchid 4D ambiguity or diverging second interpretation exists, SMT/subtask constraints and `files/constraints.h` have zero conflicts/drift, and all validated samples match expected behavior.
- `no_go`: any ambiguity, diverging second interpretation, SMT/subtask conflict, `constraints.h` drift, sample mismatch, or missing checker/interactor/communication rule remains.

## Forbidden Behavior

- Do not approve statements with ambiguous I/O protocol or unresolved Orchid 4D ambiguity.
- Do not collapse underspecified text onto a single guessed reading without testing for a plausible second interpretation.
- Do not approve when sample evidence is missing or inconsistent with `sol`, or when `statement_consistency` reports SMT conflicts or `constraints.h` drift.
- Do not approve interactive or communication statements without complete protocol semantics.
