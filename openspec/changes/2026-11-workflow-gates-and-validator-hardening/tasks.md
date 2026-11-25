## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/workflow-gates-and-validator-hardening/spec.md` under `openspec/changes/2026-11-workflow-gates-and-validator-hardening/` aligned with `openspec/config.yaml`.

## 2. Post-Generator Non-Destructive Dynamic Verification (Issue #14)

- [x] 2.1 Add `std_empirical_verified`, `std_empirical_hash`, `brute_probed`, `brute_probed_hash` tracking and `_sync_dynamic_probe_hashes` / file-save invalidation in `src/autocode_mcp/workflow/gates.py` while keeping the pre-generator gate order intact.
- [x] 2.2 Update `src/autocode_mcp/workflow/enforcement.py` (`apply_result`, `_apply_problem_build_all`, and `solution_analyze` / `solution_audit_std` / `solution_audit_brute` handlers) so post-generator runs with unchanged canonical source SHA-256 perform dynamic verification without resetting `std_audited`, `brute_audited`, `validator_ready`, `generator_built`, `checker_ready`, `interactor_ready`, or `stress_passed`, and fail closed when probes fail.
- [x] 2.3 Update `src/autocode_mcp/tools/solution_audit.py` (`SolutionAuditStdTool`, `SolutionAuditBruteTool`) and `src/autocode_mcp/workflow/reconcile.py` (`inspect_workspace_state`, `reconcile_workspace_state`) to expose clear `dynamic_probe` / `dynamic_probes` status (`ran` vs `pending_binaries`), and ensure post-generator `_probe_brute_small_scale` sets `brute_probed=True` on `status in ("ok", "timeout_circuit_breaker")`.

## 3. Anti-Vacuous Validator Guard (Issue #20)

- [x] 3.1 Update `ValidatorBuildTool.execute` in `src/autocode_mcp/tools/validator.py` to track `positive_count`, `negative_count`, `has_positive_cases`, `has_negative_cases`, and emit actionable warnings/messages when positive or negative cases are missing.
- [x] 3.2 Update `enforcement.py` (`validator_build`) and `gates.py` (`_validator_gate_ok`) to require `accuracy >= 0.9`, `has_positive_cases`, and `has_negative_cases` before marking `validator_ready=True`, blocking `return 0;` validators on positive-only test suites.

## 4. Communication `.ans` Pack Exemption, Undeclared Wrong Scan, and Smart Default Gates (Issue #20)

- [x] 4.1 Update `src/autocode_mcp/tools/problem_pack.py` to exempt `manifest_is_communication(manifest_model)` from mandatory `.ans` files and broaden undeclared wrong-solution detection to `wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, and `bf.cpp`.
- [x] 4.2 Update `src/autocode_mcp/utils/exporters/{polygon,hydro,qoj,verify}.py` (with coordinator-approved whitelist amendment #40) so communication problems missing `.ans` export and pass read-back verification cleanly.
- [x] 4.3 Implement `effective_require_wrong_solution_kill` in `src/autocode_mcp/workflow/guard.py` and propagate parity across `gates.py`, `enforcement.py`, and `reconcile.py` without editing `workflow/models.py`.

## 5. Docs, Tests, and Full Verification

- [x] 5.1 Update `skills/autocode-workflow/SKILL.md` and `agents/autocode-workflow.md` to document post-generator dynamic verification, positive+negative validator requirements, and smart `require_wrong_solution_kill` defaults.
- [x] 5.2 Write comprehensive regression tests in `tests/test_gates_t1a_hardening.py` (plus update any directly impacted legacy fixture asserting the old vacuous validator) and verify `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q` all exit 0.
