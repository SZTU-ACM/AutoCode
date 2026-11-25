---
name: idea-feasibility
description: Use before coding to decide whether a problem idea is judgeable, implementable, original, AI-resistant, and formally verifiable.
---

# Idea Feasibility Skill

Used for pre-implementation idea review. The goal is to detect high-risk issues early—such as non-judgeable tasks, underspecified semantics, contradictory constraints, near-duplicate ideas, or trivial LLM solvability—so you avoid rework in the coding phase.

## Trigger Conditions

- The user provides a new problem idea and the input/output protocol is not yet stabilized.
- Constraints, subtask boundaries, or judging rules are unclear (especially for multi-answer, interactive, or communication problems).
- Before the team starts implementing `sol`, `brute`, `generator`, or `validator`.

## Checklist

1. **Judgeability**: Is the answer unique? If not, can a deterministic `checker` or `interactor` validate arbitrary legal outputs without ambiguity?
2. **Constraint Completeness & SMT Readiness**: Are `n_max`, value ranges, number of groups, total scale (e.g., `sum_n`), and subtask bounds clearly defined and mutually satisfiable? When `.autocode/manifest.json` exists, verify satisfiability and subtask containment with `audit_manifest_constraints` (`autocode_mcp.utils.smt_constraints`) and consider single-source `files/constraints.h` generation (`generate_constraints_header`).
3. **Verifiability**: Can tests cover tight boundary witnesses (`min`, `max`, extreme values), edge cases, and wrong-solution counterexamples? Is test generation reproducible (`seed` + `type`)?
4. **Implementation Feasibility**: Are there infeasible complexity assumptions, precision traps, or timeout-prone requirements?
5. **Interactive / Communication Feasibility (if applicable)**: Is the interaction or multi-pass communication protocol complete, bounded (`max_queries`, `max_bits`, `max_bytes`, `max_rounds`), and locally simulatable?
6. **Orchid 4D Ambiguity & Anti-Semantic-Collapse Check (`arXiv:2604.21505`, `arXiv:2607.01953`)**:
   - Inspect the idea across all four Orchid ambiguity dimensions: **lexical** (ambiguous domain terms like path vs simple path, subsequence vs subarray), **syntactic** (quantifier/modifier attachment scope), **semantic** (tie-breaking, corner-case outputs, adaptive vs oblivious adversary), and **vague boundaries** (missing bounds or inclusive/exclusive endpoints).
   - **Mandatory Anti-Semantic-Collapse Protocol**: Treat underspecification separately from incoherence. Explicitly **enumerate a second plausible interpretation** of the problem rules or input/output contract. If a plausible second interpretation produces different valid answers on the same legal input, stop and surface it for clarification rather than silently collapsing to one reading.
7. **Local Originality Check**: When a user-supplied local problem corpus (JSONL or Markdown directory) is available, index and query it with `OriginalityIndex` (`autocode_mcp.utils.originality`, combining BM25 + word/char n-gram cosine similarity, plus an optional dense `Embedder` such as `CPRetriever-Prob`). Inspect top-k matches and overlapping domain terms; flag high/medium similarity or simplified-to-full overlap before implementation.
8. **AI-Resistibility Bare-Solve Probe**: Perform one zero-hint bare-solve pass using a strong reasoning model given only the candidate idea/draft statement (no editorial, tags, or solution hints). Assess whether the task collapses to standard template recall or gets solved trivially on the first attempt; if so, propose strengthening structural constraints, anti-greedy twists, or interactive/communication mechanics.

## Forbidden Actions

- Do not jump directly into code generation before feasibility passes.
- Do not use "to be added later" in place of critical constraints or checker rules.
- Do not silently collapse an underspecified problem idea onto a single assumed interpretation without enumerating a plausible second interpretation.

## Required Output

- `decision`: `go` / `no_go`
- `blocking_issues`: list of blocking issues that must be fixed
- `required_clarifications`: key questions to confirm with the user (max 3, prioritized)
- `second_interpretation`: explicit statement of the second plausible reading (or confirmation that constraints rule out alternatives)
- `ai_resistibility_note`: bare-solve risk summary and suggested twist if trivially solved
- `originality_note`: top local corpus matches/scores (or `not_run` when no local corpus is provided)
- `next_actions`: minimal action checklist before implementation

## Go / No-Go Rules

Return `decision=no_go` if any of the following is true:

- legality of output cannot be judged deterministically (or `checker` rules are missing);
- core constraints or subtask bounds are incomplete, contradictory, or exceed global limits;
- a plausible second interpretation changes expected output semantics and remains unresolved;
- reproducible generation/verification cannot be defined;
- interactive or communication protocol rules are incomplete;
- local originality check detects an unaddressed near-duplicate against the reference corpus.

## Additional Resources

- Orchid 4D probe examples, Anti-Semantic-Collapse walkthrough, and `OriginalityIndex` / `constraints.h` usage: [references/orchid-and-originality.md](references/orchid-and-originality.md)
