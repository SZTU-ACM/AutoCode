## Purpose

Provide an offline Harbor/Inspect-AI, Promptfoo, and standalone TPR/TNR evaluation suite (`evals/`), adversarial verifier-sabotage regression tests against the real workflow and audit engine (`tests/test_gate_redteam.py`), a trace-driven skill optimizer (`scripts/optimize_skills_gepa.py`), and progressive-disclosure skill slimming (`skills/*/SKILL.md` + `skills/*/references/*.md`).

## Requirements

### Requirement: Harbor, Promptfoo, and standalone TPR/TNR evaluation suite
The system SHALL provide `evals/README.md`, `evals/harbor_tasks/{standard,spj,interactive,communication}/task.yaml`, `evals/promptfoo.yaml`, and `evals/standalone_benchmark.py` capable of running `ProblemAuditTool(standalone=True)` offline against labeled `go` and `no_go` problem packages and computing exact true positive rate (TPR), true negative rate (TNR), confusion matrix, failure breakdown, and zero-write verification.

#### Scenario: Standalone benchmark achieves full TPR and TNR with zero fixture writes
- **WHEN** `evals/standalone_benchmark.py` runs across its built-in labeled `go` (`standard`, `spj`, `interactive`, `communication`) and `no_go` (surviving wrong solution, duplicate inputs, broken solution mismatch, strict missing validator) fixtures
- **THEN** `ProblemAuditTool(standalone=True)` achieves `TPR == 1.0` and `TNR == 1.0`, reports exact confusion-matrix counts and gate breakdown, and leaves every fixture directory byte-for-byte untouched

#### Scenario: Harbor tasks and Promptfoo matrix cover all problem types and host surfaces
- **WHEN** `evals/harbor_tasks/{standard,spj,interactive,communication}/task.yaml` and `evals/promptfoo.yaml` are parsed
- **THEN** all four problem types define deterministic `autocode-audit --strict`, wrong-solution kill, and zero-write acceptance checks, and `evals/promptfoo.yaml` covers `claude-code`, `codex`, `dsh`, and `antigravity` hosts

### Requirement: Adversarial gate-sabotage regression coverage
The system SHALL verify via `tests/test_gate_redteam.py` against real `src/autocode_mcp` tools and state helpers that:
(a) weakening `files/val.cpp` after `validator_build` passes is detected as stale by `inspect_workspace_state` and invalidated by `reconcile_workspace_state` / `ProblemBuildAllTool`, while vacuous `return 0;` validators fail to set `validator_ready=True`;
(b) forging `.autocode/runtime.json` flags (`validator_ready`, `stress_passed`, `tests_verified`) without backing binaries, hashes, or verification signals is flagged by `inspect_workspace_state`, cleared by `reconcile_workspace_state`, blocked by `ProblemAuditTool(standalone=False)`, and ignored by `ProblemAuditTool(standalone=True)`;
(c) symlink escapes targeting `.autocode`, `tests/`, or `solutions/` outside `problem_dir` are blocked via `path_containment` / `safe_resolve_under_root`; and
(d) hidden duplicate/empty tests and undeclared `wrong_*.cpp` solutions on disk are detected by `ProblemAuditTool`, `ProblemVerifyTestsTool`, and `ProblemPackPolygonTool`.

#### Scenario: Weakened validator or forged runtime state fails closed
- **WHEN** an agent weakens `files/val.cpp` after `validator_build` succeeds or writes forged readiness flags into `.autocode/runtime.json` without matching artifacts or hashes
- **THEN** `inspect_workspace_state` surfaces `stale_reasons` and `pack_ready=False`, `reconcile_workspace_state` clears the forged or stale flags, and `ProblemAuditTool` returns `decision="no_go"` (or fails closed)

#### Scenario: Symlink escapes and hidden defects are intercepted
- **WHEN** `.autocode`, `tests/`, or `solutions/` contain symlinks escaping `problem_dir`, or a package hides byte-identical duplicate tests, empty tests, or undeclared `wrong_*.cpp` files on disk
- **THEN** `ProblemAuditTool` blocks external symlink reads via `path_containment` and catches duplicate/empty tests and surviving undeclared wrong solutions as `decision="no_go"`

### Requirement: Trace-driven GEPA skill optimizer with offline Python fallback
The system SHALL provide `scripts/optimize_skills_gepa.py` to ingest JSON/JSONL workflow and audit failure traces, cluster recurring failure modes, optionally integrate with `gepa` when installed (`try: import gepa`), and deterministically generate Pareto-ranked skill patch proposals and unified diffs in pure Python when `gepa` is unavailable.

#### Scenario: Failure trace clustering produces deterministic skill patch proposals without gepa
- **WHEN** `scripts/optimize_skills_gepa.py` processes failure traces covering missing negative validator cases, missing `std::cout.flush()` in interactive problems, and skipped post-generator dynamic probes without `gepa` installed
- **THEN** it clusters every failure mode deterministically and emits targeted unified-diff patch proposals referencing the affected `skills/*/SKILL.md` or `skills/*/references/*.md` files without mutating disk by default

### Requirement: Progressive-disclosure skill slimming with bundle parity
The system SHALL slim verbose guidance and expanded checklists from heavy `skills/*/SKILL.md` entrypoints into linked `skills/<skill>/references/*.md` files while preserving YAML frontmatter, core checklists (`Orchid`, `Anti-Semantic-Collapse`, `OriginalityIndex`, `enable_sanitizers`, `std::cout`), and full plugin bundle inclusion across `marketplace`, `agy`, and `dsh` targets.

#### Scenario: Slimmed skills preserve core checklists and package cleanly in multi-host bundles
- **WHEN** `scripts/build_plugin_bundle.py` builds and verifies `marketplace`, `agy`, and `dsh` bundles after progressive-disclosure slimming
- **THEN** all `skills/<skill>/SKILL.md` links resolve to existing `references/*.md` files, all preserved checklist tokens and frontmatter keys remain intact, and `tests/test_docs_sync.py`, `tests/test_plugin_bundle.py`, and `tests/test_plugin_manifest.py` pass
