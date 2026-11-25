## Why

AutoCode's deterministic verification kernel (`ProblemAuditTool`, `workflow/reconcile.py`, `workflow/enforcement.py`, `workflow/gates.py`, and `ProblemVerifyTestsTool`) enforces strict problem-setting invariants, yet the repository lacks four critical evaluation, regression, and skill-maintenance capabilities:

1. **No reproducible agent & audit evaluation harness (`evals/`)**: There is no Harbor / Inspect-AI task suite covering `standard`, `spj`, `interactive`, and `communication` problems, no Promptfoo host-comparison config across AutoCode's four host surfaces (`Claude Code`, `Codex`, `DeepSeek Harness`, `Antigravity`), and no offline labeled TPR/TNR benchmark measuring whether `ProblemAuditTool(standalone=True)` reliably passes valid packages (`go`) while rejecting defective/hackable packages (`no_go`) with zero target-directory writes.
2. **No adversarial verifier-sabotage test suite (`tests/test_gate_redteam.py`)**: Red-team sabotage vectors (inspired by RedCodeAgent)—such as weakening `files/val.cpp` after `validator_build` passes, submitting vacuous `return 0;` validators, forging `.autocode/runtime.json` readiness flags without real artifacts/hashes, escaping `problem_dir` via symlinks in `.autocode`/`tests/`/`solutions/`, and hiding duplicate/empty tests or undeclared `wrong_*.cpp` files on disk—lack a dedicated end-to-end regression suite exercising real `src/autocode_mcp` tools.
3. **No trace-driven skill refinement pipeline (`scripts/optimize_skills_gepa.py`)**: Recurring agent failure modes observed in audit logs and workflow traces (missing negative validator cases, missing `std::cout.flush()` in interactive problems, skipped post-generator dynamic probes, duplicate inputs) are not clustered or converted into actionable skill patch proposals.
4. **Heavy `SKILL.md` context bloat (`skills/*/SKILL.md`)**: Core skill entrypoints (especially `skills/autocode-workflow/SKILL.md` at 290 lines and `skills/statement-audit/SKILL.md` / `skills/agent-skill-governance/SKILL.md`) inline verbose checklists and tool parameter tables instead of following the `agentskills.io` progressive-disclosure pattern (`SKILL.md` + linked `references/*.md`).

## What Changes

- **Evaluation Suite (`evals/`)**:
  - Add `evals/README.md` documenting Harbor/Inspect tasks, Promptfoo host matrix runs, and standalone deterministic benchmarking.
  - Add `evals/harbor_tasks/{standard,spj,interactive,communication}/task.yaml` defining Harbor / Inspect-AI compatible benchmark tasks across all four AutoCode problem types with deterministic acceptance commands (`autocode-audit --standalone --strict`, compiled wrong kills, zero-write verification).
  - Add `evals/standalone_benchmark.py` running `ProblemAuditTool(standalone=True)` against labeled `go` and `no_go` problem fixtures (including `CodeContests+`/`Codehacks`-style good, SPJ, interactive, surviving-wrong, duplicate-test, empty/mismatch, and strict-completeness cases) and computing exact TPR, TNR, confusion matrix, failure breakdown, and zero-write invariants.
  - Add `evals/promptfoo.yaml` comparing Claude Code, Codex, DeepSeek Harness (`dsh`), and Antigravity (`agy`) prompts and rubrics.
- **Red-Team Gate-Sabotage Regressions (`tests/test_gate_redteam.py`)**:
  - Test real `src/autocode_mcp` workflows and `ProblemAuditTool` against validator weakening (`inspect_workspace_state`, `reconcile_workspace_state`, `ProblemBuildAllTool`, vacuous `validator_build`), `.autocode/runtime.json` flag/hash forgery, symlink escapes in `.autocode`/`tests/`/`solutions/`, and hidden duplicate/empty tests or undeclared `wrong_*.cpp` files, alongside unit tests for `evals/`, `scripts/optimize_skills_gepa.py`, and skill slimming / bundle parity.
- **GEPA Skill Optimizer (`scripts/optimize_skills_gepa.py`)**:
  - Parse workflow and audit failure traces (JSON/JSONL), cluster failure modes deterministically, integrate with optional `gepa` (`try: import gepa`) when installed, and generate Pareto-ranked pure-Python reflective skill patch proposals and unified diffs when `gepa` is absent.
- **Progressive-Disclosure Skill Slimming (`skills/*/SKILL.md` + `skills/*/references/*.md`)**:
  - Slim verbose templates and detailed protocol/tool checklists from existing skills (`autocode-workflow`, `statement-audit`, `agent-skill-governance`, and companion references where helpful) into linked `skills/<name>/references/*.md` files while preserving 100% of YAML frontmatter keys, Orchid 4D / Anti-Semantic-Collapse / `OriginalityIndex` checklists, `enable_sanitizers`, `std::cout`, and all `CLAUDE.md` workflow/gating contracts.

## Capabilities

### New Capabilities

- `agent-evals-and-redteam-security`: Reproducible Harbor/Inspect-AI benchmark tasks, Promptfoo multi-host comparison config, standalone `ProblemAuditTool(standalone=True)` TPR/TNR benchmark runner, RedCodeAgent verifier-sabotage regression suite, trace-driven GEPA skill optimizer (`scripts/optimize_skills_gepa.py`), and progressive-disclosure `skills/<skill>/references/*.md` organization.

### Modified Capabilities

- None (`src/autocode_mcp/**`, the 22 MCP tool signatures, and `CLAUDE.md` pipeline gates are strictly preserved read-only).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12-stage `CLAUDE.md` authoring gates (brute after sol, validator positive+negative cases with `accuracy >= 0.9`, checker ordering vs stress, interactive `std::cout` + `std::flush`, full audit gate before `problem_pack_polygon`, zero-write `standalone=True` audit) and verifies them under adversarial sabotage tests.
- **Affected Files**:
  - `openspec/changes/2026-11-agent-eval-harbor-gepa-and-redteam-security/**`
  - `evals/README.md`
  - `evals/harbor_tasks/{standard,spj,interactive,communication}/task.yaml`
  - `evals/standalone_benchmark.py`
  - `evals/promptfoo.yaml`
  - `scripts/optimize_skills_gepa.py`
  - `tests/test_gate_redteam.py`
  - Existing `skills/{agent-skill-governance,autocode-workflow,idea-feasibility,problem-difficulty-rating,problem-validate,solution-complexity-audit,statement-audit,stress-strategy,testdata-quality}/{SKILL.md,references/*.md}`
- **Dependencies & Risks**:
  - *Optional third-party eval runners (`gepa`, `harbor`, `inspect_ai`, `promptfoo`)*: None are required in `pyproject.toml`. Mitigated by pure-Python deterministic fallbacks in `scripts/optimize_skills_gepa.py` and offline execution of `evals/standalone_benchmark.py` using only `autocode_mcp` and `pyyaml`.
  - *Compiler/CPU contention during benchmark runs*: Compiling multiple C++ fixtures concurrently could trigger compiler memory spikes or transient ASan/timing overhead. Mitigated by running `standalone_benchmark.py` fixtures sequentially (or bounded) in isolated `TemporaryDirectory` workspaces.
  - *Accidental checklist loss during skill slimming*: Moving content out of `SKILL.md` could drop critical agent guardrails (`Orchid`, `Anti-Semantic-Collapse`, `enable_sanitizers`, `std::cout`, YAML frontmatter). Mitigated by preserving core checklists in `SKILL.md`, moving only verbose templates/deep references to `references/*.md`, and enforcing preservation assertions in `tests/test_gate_redteam.py` plus `tests/test_docs_sync.py` / `tests/test_plugin_bundle.py` / `tests/test_plugin_manifest.py`.

## Non-goals

- Do NOT modify `src/autocode_mcp/**`, any of the 22 MCP tool signatures, `agents/**`, `skills/evolutionary-testdata/**`, `skills/redteam-hacking/**`, `pyproject.toml`, `uv.lock`, `examples/ci/*`, `CLAUDE.md`, `README.md`, or `rules/**`.
- Do NOT add runtime LLM SDK dependencies or auto-rewrite repository skills by default without explicit `--apply`.
