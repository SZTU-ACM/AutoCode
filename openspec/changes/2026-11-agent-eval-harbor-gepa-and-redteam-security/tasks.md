## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/agent-evals-and-redteam-security/spec.md` under `openspec/changes/2026-11-agent-eval-harbor-gepa-and-redteam-security/` conforming to `openspec/config.yaml`.

## 2. Evaluation Suite (`evals/`)

- [x] 2.1 Create `evals/README.md` documenting Harbor/Inspect-AI tasks, Promptfoo host matrix runs, and deterministic standalone benchmark commands.
- [x] 2.2 Create `evals/harbor_tasks/{standard,spj,interactive,communication}/task.yaml` covering all 4 problem types with `autocode-audit --standalone --strict`, compiled wrong-kill checks, and zero-write assertions.
- [x] 2.3 Implement `evals/standalone_benchmark.py` running `ProblemAuditTool(standalone=True)` over labeled `go` and `no_go` fixtures (`CodeContests+` / `Codehacks`-style) and reporting TPR, TNR, confusion matrix, failure breakdown, and zero-write checks (`uv run python evals/standalone_benchmark.py --json`).
- [x] 2.4 Create `evals/promptfoo.yaml` comparing `claude-code`, `codex`, `dsh`, and `antigravity` host prompts and rubrics.

## 3. Trace-Driven GEPA Skill Optimizer (`scripts/optimize_skills_gepa.py`)

- [x] 3.1 Implement `scripts/optimize_skills_gepa.py` with JSON/JSONL trace ingestion, deterministic failure-mode clustering, optional `try: import gepa` adapter, Pareto-ranked pure-Python patch/prompt proposal generator, unified diff emission, and CLI flags (`--traces`, `--skills-dir`, `--output`, `--apply`).

## 4. Progressive-Disclosure Skill Slimming (`skills/*/SKILL.md` + `references/*.md`)

- [x] 4.1 Slim heavy existing skills (`autocode-workflow`, `statement-audit`, `agent-skill-governance`, and companion references in `idea-feasibility` / `testdata-quality` / `solution-complexity-audit` as appropriate) into `skills/<skill>/references/*.md` while preserving 100% of YAML frontmatter, Orchid 4D, Anti-Semantic-Collapse, `OriginalityIndex`, `enable_sanitizers`, and `std::cout` checklists (`uv run pytest tests/test_docs_sync.py tests/test_plugin_bundle.py tests/test_plugin_manifest.py -q`).

## 5. Red-Team Gate-Sabotage & Regression Tests (`tests/test_gate_redteam.py`)

- [x] 5.1 Implement RedCodeAgent gate-sabotage regression tests against real `src/autocode_mcp` covering (a) `files/val.cpp` weakening & vacuous `return 0;` validator blocking, (b) `.autocode/runtime.json` flag/hash forgery detection & rollback, (c) `.autocode` / `tests/` / `solutions/` symlink escapes via `path_containment`, and (d) hidden duplicate/empty tests and undeclared `wrong_*.cpp` detection.
- [x] 5.2 Add tests in `tests/test_gate_redteam.py` verifying `evals/standalone_benchmark.py` (`TPR == 1.0`, `TNR == 1.0`, zero writes), `evals/harbor_tasks/*/task.yaml`, `evals/promptfoo.yaml`, `scripts/optimize_skills_gepa.py` (with and without `gepa`), and progressive-disclosure skill slimming + bundle copy across `marketplace`, `agy`, and `dsh`.
- [x] 5.3 Run `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q` and confirm all checks exit 0.
