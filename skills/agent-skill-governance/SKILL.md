---
name: agent-skill-governance
description: Define and enforce project-wide quality standards for agent and skill documents. Use when creating, reviewing, or refactoring files under agents/ and skills/ to keep structure, terminology, and output contracts consistent.
---

# Agent and Skill Governance

Mandatory authoring and review standards for `agents/*.md` and `skills/**/SKILL.md` (+ `references/*.md`).

## Trigger Conditions

- Creating, reviewing, slimming, or refactoring any file under `agents/` or `skills/`.

## Scope & Terminology

- Use English consistently (except canonical Chinese statement headings such as `## 交互协议` or `## 通信协议`).
- Standard terms: `decision: go|no_go`, `blocking_issues`, `next_actions`, `validator`, `generator`, `checker`, `interactor`, `limit_ratio`, `limit_semantics`, `wrong_solution_kill`.

## Required Structure

1. **Agent files (`agents/*.md`)**: Role/responsibility statement, workflow or audit scope, mandatory output contract, fail-fast behavior, forbidden behavior.
2. **Skill files (`skills/<skill>/SKILL.md`)**: YAML frontmatter (`name`, `description`), purpose, trigger conditions, step-by-step execution guidance, required output format, decision rules (`go` vs `no_go`), forbidden behavior, and progressive-disclosure links to `references/*.md` when templates or checklists are long.

## Workflow Consistency Rules

- Never contradict `src/autocode_mcp/workflow/enforcement.py` gates or relax hard gates in prose without matching code changes.
- Never infer completion from file presence when structured tool evidence is required.
- Project-specific constraints (such as max clarifications) are allowed when explicit and non-contradictory.

## Output Format

- `decision`: `go` / `no_go`
- `blocking_issues`: governance or consistency violations
- `next_actions`: concrete edits required

## Decision Rules

- `go`: structure, terminology, gate alignment, and all `references/*.md` links pass review.
- `no_go`: missing required sections, broken links, contradictory gates, or missing `no_go` criteria.

## Forbidden Behavior

- Do not duplicate instruction blocks across files or leave broken relative `references/` links.
- Do not strip required safety/checklist terms (`Orchid`, `Anti-Semantic-Collapse`, `enable_sanitizers`, `std::cout`, YAML frontmatter) when slimming skills.

## Additional Resources

- Extended quality bar and review checklist template: [references/review-checklist.md](references/review-checklist.md)
