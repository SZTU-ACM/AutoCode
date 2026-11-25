## 1. OpenSpec & Change Artifacts

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/standalone-polygon-and-strict/spec.md` under `openspec/changes/2026-11-standalone-audit-and-polygon-discovery/` conforming to `openspec/config.yaml`.

## 2. Polygon `problem.xml` Parser & Unified Discovery (Issue #12)

- [x] 2.1 Create `src/autocode_mcp/utils/problem_layout.py` implementing XXE-guarded `parse_polygon_problem_xml`, `discover_test_pairs`, `is_wrong_solution_filename`, and `discover_wrong_solution_files` with `safe_resolve_under_root` containment enforcement.
- [x] 2.2 Wire `problem_layout.py` into `src/autocode_mcp/tools/audit_discovery.py` (`_detect_layout`, `_resolve_tests_dir`, `_find_main_solution_source`, `_find_validator_source`, `_find_checker_source`, `_find_interactor_source`, `_find_generator_source`, `_infer_standalone_limits`, `_infer_standalone_manifest`) and `src/autocode_mcp/tools/verify_fingerprint.py` (`_compute_dataset_fingerprint`, `_discover_wrong_solution_names`).
- [x] 2.3 Verify `examples/checker-sample/solutions/wrong-greedy.cpp` and Polygon `tests/01` + `01.a` fixtures are discovered cleanly.

## 3. Zero-Write Staging, ASan/UBSan, Empirical Complexity, Communication Mutation, and `--strict` (Issues #11 & #12)

- [x] 3.1 Update `src/autocode_mcp/tools/audit.py` and `src/autocode_mcp/tools/audit_standalone.py` to stage non-`.in` Polygon tests into a `TemporaryDirectory` (after containment verification) so `_static_quality_signals` and live checks run with zero writes in `problem_path`, and update `structural_signals["tests_present"]` and `exp_test_count` in `audit.py`.
- [x] 3.2 Add isolated ASan/UBSan compilation/execution on up to 3 representative inputs (smallest/median/largest) inside `tmp_bin_dir` in `src/autocode_mcp/tools/audit_standalone.py`, gracefully recording `unavailable` when runtime support is absent and blocking on ASan/UBSan errors.
- [x] 3.3 Add isolated `tmp_bin_dir` empirical complexity (`MultiScaleSampler` + `EmpiricalRatioAnalyzer`) in `src/autocode_mcp/tools/audit_standalone.py` when `mode == "full"`, non-protocol, and a generator compiles (never writing `problem_path/.autocode/empirical_tests`).
- [x] 3.4 Update `src/autocode_mcp/utils/mutation.py` to honor `interactor_bin` and forward `communicator_exe_override=eff_communicator` for communication problems, plus discover non-`.in` input/answer pairs cleanly without modifying mutation operators.
- [x] 3.5 Add `--strict` CLI flag in `src/autocode_mcp/cli/audit.py` and `strict: bool = False` keyword parameter on `ProblemAuditTool.execute` in `src/autocode_mcp/tools/audit.py` (keeping `ProblemAuditInput` unchanged); emit `completeness_warnings` in standalone reports and promote omissions to `blocking_issues` when `strict=True`; propagate `statement_consistency.get("review_reason")` in `audit.py`.

## 4. Documentation & Comprehensive Verification

- [x] 4.1 Add concise `--strict` documentation to `README.md` and `CLAUDE.md` while keeping `tests/test_docs_sync.py` passing.
- [x] 4.2 Author `tests/test_standalone_polygon_and_strict.py` covering XXE-guarded Polygon `problem.xml` + `tests/01`/`01.a`, `examples/checker-sample` `wrong-greedy.cpp` discovery, `examples/communication-sample` standalone `--mutation-check`, ASan OOB probe + `unavailable` fallback, isolated empirical complexity probe, `--strict` completeness gating, and zero-write filesystem snapshot assertions.
- [x] 4.3 Run `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q`, verifying all checks pass and only whitelisted files were changed.
