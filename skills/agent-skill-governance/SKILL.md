---
name: agent-skill-governance
description: Define and enforce project-wide quality standards for agent and skill documents. Use when creating, reviewing, or refactoring files under agents/ and skills/ to keep structure, terminology, and output contracts consistent.
disable-model-invocation: false
---

# Agent and Skill Governance

This skill defines mandatory authoring standards for files under `agents/` and `skills/`.

## Scope

- Agent definitions in `agents/*.md`
- Skill definitions in `skills/**/SKILL.md`

## Language and Terminology

- Use English only.
- Keep terminology consistent across files:
  - `decision: go|no_go`
  - `blocking_issues`
  - `next_actions`
  - `validator`, `generator`, `checker`, `interactor`
  - `limit_ratio`, `limit_semantics`, `wrong_solution_kill`

## Required Structure

### Agent files

Must include:

1. Role and responsibility statement.
2. Workflow or audit scope.
3. Mandatory output contract.
4. Fail-fast behavior.
5. Forbidden behavior.

### Skill files

Must include:

1. Purpose.
2. Trigger conditions.
3. Step-by-step execution guidance.
4. Required output format.
5. Decision rules (`go` vs `no_go`).

## Workflow Consistency Rules

- Do not define contradictory workflow steps between agents and skills.
- Do not relax hard gates in documentation unless workflow guard and tool behavior are updated accordingly.
- Never infer completion from file presence when structured tool evidence is required.

## Quality Bar

Before finalizing edits:

1. Ensure no duplicated instruction blocks.
2. Ensure no mixed-language fragments.
3. Ensure contract fields are spelled identically across files.
4. Ensure each file has explicit no-go criteria.
5. Ensure examples do not conflict with enforced gates.

## Review Checklist Template

Use this checklist when updating any `agents/` or `skills/` file:

- [ ] Role/scope is explicit.
- [ ] Output contract is explicit.
- [ ] Gate behavior is explicit.
- [ ] Decision rules are explicit.
- [ ] Forbidden behavior is explicit.
- [ ] Terminology is consistent with project standards.
