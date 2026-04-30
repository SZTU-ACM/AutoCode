---
name: idea-feasibility
description: Use before coding to decide whether a problem idea is judgeable, implementable, and verifiable.
disable-model-invocation: false
---

# Idea Feasibility Skill

Used for pre-implementation idea review. The goal is to detect high-risk issues early—such as non-judgeable tasks, unverifiable requirements, or missing constraints—so you avoid rework in the coding phase.

## Trigger Conditions

- The user only provides a problem idea and the input/output protocol is not yet stable.
- Constraints or judging rules are unclear (especially for multi-answer or interactive problems).
- Before the team starts implementing `sol/brute/generator/validator`.

## Checklist

1. **Judgeability**: Is the answer unique? If not, can a checker define valid outputs precisely?
2. **Constraint Completeness**: Are `n_max`, value ranges, number of groups, and total scale (e.g., `sum_n`) clearly defined?
3. **Verifiability**: Can tests cover boundaries, extremes, and counterexamples? Is generation reproducible (seed + type)?
4. **Implementation Feasibility**: Are there obviously infeasible or timeout-prone requirements?
5. **Interactive Feasibility (if applicable)**: Is the interaction protocol complete and locally simulatable?

## Forbidden Actions

- Do not jump directly into code generation.
- Do not use "to be added later" in place of critical constraints.

## Required Output

- `decision`: `go` / `no_go`
- `blocking_issues`: list of blocking issues that must be fixed
- `required_clarifications`: key questions to confirm with the user (max 3, prioritized)
- `next_actions`: minimal action checklist before implementation

## Go / No-Go Rules

Return `decision=no_go` if any of the following is true:

- legality of output cannot be judged deterministically (or checker rules are missing);
- core constraints are incomplete or contradictory;
- reproducible generation/verification cannot be defined;
- interactive protocol is incomplete for interactive tasks.
