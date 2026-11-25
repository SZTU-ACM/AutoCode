# Agent and Skill Review Checklist

Extended quality bar and review template for files under `agents/` and `skills/`.

## Quality Bar

Before finalizing edits to any agent or skill document:

1. Ensure no duplicated instruction blocks across `SKILL.md` and `references/*.md`.
2. Ensure no mixed-language fragments (keep skill/agent specs in consistent English unless quoting a canonical Chinese section marker such as `## 交互协议` or `## 通信协议`).
3. Ensure output contract field names (`decision`, `blocking_issues`, `next_actions`) are spelled identically across files.
4. Ensure every file states explicit `no_go` criteria and forbidden actions.
5. Ensure examples and progressive-disclosure references stay aligned with enforced server-side gates (`src/autocode_mcp/workflow/enforcement.py`).
6. Ensure `SKILL.md` keeps YAML frontmatter (`name`, `description`) and core checklists intact while offloading lengthy templates/schemas into linked `references/*.md`.

## Review Checklist Template

- [ ] Purpose and scope are explicit.
- [ ] Trigger conditions are explicit (skills).
- [ ] Step-by-step execution guidance is concise and unambiguous.
- [ ] Output contract is explicit (`decision: go|no_go`).
- [ ] Gate behavior is explicit and does not relax server enforcement.
- [ ] Decision rules (`go` vs `no_go`) are explicit.
- [ ] Forbidden behavior is explicit.
- [ ] Terminology matches project standards (`validator`, `generator`, `checker`, `interactor`, `limit_ratio`, `limit_semantics`, `wrong_solution_kill`).
- [ ] All relative links under `skills/<skill>/references/*.md` exist on disk.
