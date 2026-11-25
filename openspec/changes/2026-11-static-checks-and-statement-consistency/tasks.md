## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/static-checks-and-consistency/spec.md` under `openspec/changes/2026-11-static-checks-and-statement-consistency/` following `openspec/config.yaml`.

## 2. Static Test-Suite Quality Checks (Issue #15)

- [x] 2.1 Rewrite `_check_limit_semantics` in `src/autocode_mcp/tools/verify_static_checks.py` to safely read `type=3` and `type=4` inputs via `safe_resolve_under_root`, compute the 11-dimensional structural profile (plus optional `std_time_ms`), enforce MD5 overlap `< 0.8` and `structural_divergence >= 0.15`, and return backwards-compatible keys plus `structural_divergence`, `type3_profile`, and `type4_profile`.
- [x] 2.2 Implement `_extract_effective_scale(data, manifest)` and optional manifest loading in `src/autocode_mcp/tools/verify_static_checks.py`; integrate byte/token scale hints and the `>= 3` inputs multi-bucket requirement (with narrow-constraint exemption like `n_max <= 10`) into `_check_scale_distribution`, and apply `_extract_effective_scale` in `_check_boundary_coverage`.
- [x] 2.3 Verify with `uv run pytest tests/test_tools/test_problem.py tests/test_audit.py -q`.

## 3. Statement Consistency & Audit Reporting (Issue #16)

- [x] 3.1 Update `_statement_consistency` in `src/autocode_mcp/tools/audit_constraints.py` so non-empty `validator_bound_mismatches` sets `passed = False`, and populate structured `message` and `review_reason` naming mismatched variables, validator vs expected bounds, and missing constraint terms.
- [x] 3.2 Update `format_audit_markdown` and `format_audit_summary` in `src/autocode_mcp/cli/report_format.py` to render `## Statement Consistency & Communication Metrics` whenever `passed is False` OR `validator_bound_mismatches` OR `constraint_terms_missing_from_statement` is non-empty, render the `| Variable | Validator Range | Reason |` table and missing-terms list safely, and surface mismatch counts/variables in summary output.
- [x] 3.3 Align `tests/test_integration/test_communication_e2e.py` `RUN_TWICE_STATEMENT` and `manifest.constraints` with `RUN_TWICE_VAL_CPP` (`flip_pos` in `[0, 1000]`) and verify with `uv run pytest tests/test_cli_audit_format.py tests/test_cross_platform_feedback.py tests/test_phase2_feedback.py tests/test_integration/test_communication_e2e.py -q`.

## 4. Regression Tests & Full Quality Gates

- [x] 4.1 Add comprehensive regression tests in `tests/test_static_checks_and_consistency.py` covering homogeneous random clones vs structured `type=3`/`type=4` suites, `T=1, n=200000` multi-test scale extraction and bucketing, uniform `n=1` suite failure vs `n_max <= 10` exemption, `val.cpp` max=1000 vs statement `10^5` blocking, Markdown table/list rendering, summary output, and injection escaping.
- [x] 4.2 Run all 4 repo checks (`uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, `uv run pytest tests/ -q`) and verify `git diff --name-only` touches only whitelisted files.
