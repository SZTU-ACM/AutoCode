## Context

AutoCode ships a deterministic verification kernel in `src/autocode_mcp/**` and host-shared knowledge in `skills/*/SKILL.md`. This change sits strictly outside `src/autocode_mcp/**` as an evaluation, adversarial regression, trace-optimization, and skill-refactoring layer:

```text
+-----------------------------------------------------------------------------+
| Evaluation & Optimization Layer (evals/, scripts/optimize_skills_gepa.py)   |
|  - evals/harbor_tasks/{standard,spj,interactive,communication}/task.yaml    |
|  - evals/promptfoo.yaml (Claude Code / Codex / DSH / Antigravity comparison)|
|  - evals/standalone_benchmark.py -> ProblemAuditTool(standalone=True)       |
|      * CodeContests+ / Codehacks labeled fixtures -> TPR, TNR, Confusion    |
|      * Snapshot before/after -> zero target writes verified                 |
|  - scripts/optimize_skills_gepa.py                                          |
|      * JSON/JSONL failure trace parser -> Failure mode clustering           |
|      * Optional `import gepa` OR pure-Python Pareto patch proposer          |
+--------------------------------------+--------------------------------------+
                                       | exercises & hardens
                                       v
+-----------------------------------------------------------------------------+
| Deterministic Kernel (src/autocode_mcp/** — strictly READ-ONLY)             |
|  - ProblemAuditTool, ProblemVerifyTestsTool, ProblemBuildAllTool,           |
|    ValidatorBuildTool, inspect_workspace_state, reconcile_workspace_state   |
|  ^ verified by tests/test_gate_redteam.py (RedCodeAgent sabotage vectors)   |
+-----------------------------------------------------------------------------+
                                       ^
+--------------------------------------+--------------------------------------+
| Progressive-Disclosure Skills (skills/<name>/SKILL.md + references/*.md)    |
|  - Slimmed entrypoints keeping YAML frontmatter + core checklists           |
|  - Verbose guidance/templates in references/*.md (copied by bundle builder) |
+-----------------------------------------------------------------------------+
```

## Architecture & Design Decisions

### 1. Standalone TPR/TNR Benchmark (`evals/standalone_benchmark.py`)

- **Fixture design**: Ships a deterministic, self-contained builder generating real C++ competitive-programming packages inside `tempfile.TemporaryDirectory` (zero writes to the repo):
  - `go` suite (`CodeContests+`-style verified packages): `standard` (valid `val.cpp`, multi-scale tests, `wrong_int32.cpp` killed), `spj` (valid `checker.cpp`, `wrong_greedy.cpp` killed), `interactive` (`interactor.cpp` using `registerInteraction` + `std::cout << ... << std::flush`, `wrong_over_limit.cpp` killed), and `communication` (`run_twice` `communicator.cpp`, `wrong_no_ecc.cpp` killed).
  - `no_go` suite (`Codehacks`-style defective/hackable packages): surviving wrong solution (`wrong_offbyone.cpp` not killed), byte-identical duplicate inputs (`duplicate_inputs`), broken `sol.cpp` mismatching `.ans` (`answer_consistency`), and missing validator/wrong-solution completeness under `strict=True` (`validator_check` / `wrong_solution_kill`).
- **Metrics**: Computes `tp`, `tn`, `fp`, `fn`, `tpr = tp / (tp + fn)`, `tnr = tn / (tn + fp)`, `precision`, `accuracy`, `failure_breakdown` grouped by `blocking_issues[*].gate`, and `zero_write_passed` via pre/post filesystem snapshots on each fixture.
- **Tradeoff — external datasets vs built-in fixtures**: Users can pass `--dataset <manifest.json>` pointing to external `CodeContests+`/`Codehacks` folders, while the default built-in suite guarantees 100% deterministic offline CI reproducibility without multi-GB downloads.
- **Tradeoff — sequential execution**: Running benchmark cases sequentially avoids `g++` memory contention and timing jitter on 4-core CI runners while remaining fast (<15s for 8 compact C++ fixtures).

### 2. Harbor Tasks & Promptfoo Host Matrix (`evals/harbor_tasks/*/task.yaml`, `evals/promptfoo.yaml`)

- Each `task.yaml` captures Harbor / Inspect-AI metadata (`schema_version`, `task_id`, `problem_type`, `reference_example`, `sandbox`, `instruction`, `required_artifacts`, `acceptance_checks`, `scoring`) and binds acceptance explicitly to `autocode-audit --standalone --strict`, compiled wrong-solution kill checks, and zero-write assertions.
- `evals/promptfoo.yaml` defines providers for `claude-code`, `codex`, `dsh`, and `antigravity` referencing `rules/AGENTS.md` and `skills/*/SKILL.md` prompts, paired with deterministic rubric assertions across problem creation, interactive flushing (`std::cout.flush()`), and Orchid 4D / Anti-Semantic-Collapse statement auditing.

### 3. RedCodeAgent Gate-Sabotage Regressions (`tests/test_gate_redteam.py`)

- Tests run against real `src/autocode_mcp` tools and workflow state (NO mocking out of `ProblemAuditTool`, `reconcile_workspace_state`, or `ValidatorBuildTool`):
  1. **Validator weakening**: Out-of-band edit to `files/val.cpp` caught by `inspect_workspace_state` (`stale=True`) and cleared by `reconcile_workspace_state`; `ProblemBuildAllTool` rebuild detects SHA-256 drift and clears `validator_ready`; vacuous `return 0;` `validator_build` blocked via `has_negative_cases=False` or `< 0.9` accuracy.
  2. **Runtime state forgery**: Writing forged `validator_ready=True`, `stress_passed=True`, `tests_verified=True` into `.autocode/runtime.json` without matching binaries/signals/hashes is caught by `inspect_workspace_state`, rolled back by `reconcile_workspace_state`, blocked by `ProblemAuditTool(standalone=False)`, and ignored completely by `ProblemAuditTool(standalone=True)`.
  3. **Symlink escapes**: Symlinks escaping `problem_dir` in `.autocode`, `tests/`, and `solutions/` are rejected via `path_containment` / `safe_resolve_under_root` and never read, compiled, or overwritten outside `problem_dir`.
  4. **Hidden duplicates/empty tests & undeclared wrong solutions**: Byte-identical duplicates, empty inputs, and undeclared `solutions/wrong_*.cpp` files on disk are detected cleanly by `ProblemAuditTool`, `ProblemVerifyTestsTool`, and `ProblemPackPolygonTool`.

### 4. Trace-Driven GEPA Skill Optimizer (`scripts/optimize_skills_gepa.py`)

- **Boundary**: Hook/CLI script lives strictly in `scripts/optimize_skills_gepa.py` without touching `src/autocode_mcp/**`.
- **Fail-closed & optional `gepa` integration**: Uses `try: import gepa` dynamically. When `gepa` is absent (default offline environment) or disabled, a deterministic pure-Python clusterer + Pareto scorer + patch generator maps trace failure signatures (`validator_negative_missing`, `interactive_stdout_flush`, `post_generator_probe_skip`, `weak_testdata_wrong_survival`, `statement_ambiguity_or_smt_drift`, `duplicate_or_empty_tests`) to targeted `skills/<name>/SKILL.md` / `references/*.md` diff proposals. Default mode is read-only (`--apply` is opt-in).

### 5. Progressive-Disclosure Skill Slimming (`skills/*/SKILL.md` + `references/*.md`)

- Follows `agentskills.io` progressive disclosure: keep `SKILL.md` lean and scannable (YAML frontmatter `name`+`description`, triggers, core steps, decision rules, forbidden behavior, output contract, and links to `references/*.md`), while moving lengthy per-tool parameter descriptions, manifest schema tables, and expanded interactive/communication checklists into `skills/<name>/references/*.md`.
- `scripts/build_plugin_bundle.py` recursively copies `skills/` via `shutil.copytree` and digests `skills/` recursively via `rglob("*")`, so `references/*.md` files are automatically included and fingerprinted in `marketplace`, `agy`, and `dsh` plugin bundles.

## Risks / Trade-offs

- **Risk: Over-slimming removes critical constraints**: Mitigated by keeping all required tokens (`Orchid`, `Anti-Semantic-Collapse`, `OriginalityIndex`, `enable_sanitizers`, `std::cout`, `registerInteraction`, `decision`, `go`, `no_go`) inside the primary `SKILL.md` files and testing them in `tests/test_gate_redteam.py`.
- **Risk: Platform symlink permission differences (Windows)**: Mitigated in `tests/test_gate_redteam.py` by using the same `OSError` / `winerror == 1314` skip guard used in `tests/test_audit.py`.
- **Risk: `gepa` API evolution**: Mitigated by isolating the optional `gepa` import inside `_try_import_gepa()` and keeping the deterministic Python proposal engine self-contained and tested both with `gepa=None` and with a mock `gepa` module.
