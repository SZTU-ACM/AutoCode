## Why

Three static test-suite quality checks and statement/validator consistency reporting currently permit false-positive passes and swallow actionable diagnostics:

1. **`_check_limit_semantics` is a mathematical no-op under default deduplication (Issue #15)**: `src/autocode_mcp/tools/verify_static_checks.py` compares only MD5 `signature` overlap (`sig3 & sig4`) between `type=3` and `type=4` test entries. Because `problem_generate_tests` enables `enable_dedup=True` by default and uses distinct seeds, `sig3 & sig4` is empty (`overlap_ratio == 0.0`), allowing homogeneous random clones from identical distributions to pass `limit_semantics`.
2. **Naive first-token scale extraction & loose `scale_distribution` (Issue #15)**: Both `_check_scale_distribution` and `_check_boundary_coverage` treat the first whitespace token of each `.in` file as $n$. On multi-test inputs (`T=1, n=200000`), $T=1$ causes extreme tests to be bucketed as `tiny` and compared wrongly against `n_min`/`n_max`. Furthermore, `_check_scale_distribution` passes as long as no files are empty, allowing suites of `>= 3` inputs that collapse into a single scale bucket (e.g. all `n=1`) to pass unchecked.
3. **Silent `statement_consistency` passes and hidden mismatch tables (Issue #16)**: `src/autocode_mcp/tools/audit_constraints.py` computes `validator_bound_mismatches` and `constraint_terms_missing_from_statement`, yet sets `passed = has_samples and tutorial_ready`, allowing clear `val.cpp` bound contradictions against statement/manifest constraints to return `passed=True`. Meanwhile, `src/autocode_mcp/cli/report_format.py` only renders the statement consistency section when `passed is False` and omits structured mismatch tables and summary counts/variables.

## What Changes

- **Multi-dimensional structural divergence in `_check_limit_semantics` (`src/autocode_mcp/tools/verify_static_checks.py`)**:
  - Safely resolve `type=3` and `type=4` input files via `safe_resolve_under_root`, compute 11-dimensional per-case structural feature profiles (`byte_size`, `line_count`, `token_count`, `unique_token_ratio`, `distinct_token_count`, `numeric_ratio`, `max_abs_int`, `avg_tokens_per_line`, `max_line_tokens`, `adjacent_equal_ratio`, `monotonic_step_ratio`) plus optional per-case std timings from the test manifest.
  - Fail (`passed=False`) if either type is missing, paths escape/fail, MD5 `overlap_ratio >= 0.8`, or normalized `structural_divergence` falls below threshold (`0.15`), while preserving backwards-compatible keys (`passed`, `type3_count`, `type4_count`, `overlap_ratio`, `hint`) and adding `structural_divergence`, `type3_profile`, and `type4_profile`.
- **Multi-test scale extraction & tightened scale/boundary checks (`src/autocode_mcp/tools/verify_static_checks.py`)**:
  - Optionally load `AutoCodeManifest` from `Path(tests_dir).parent` (or `Path(tests_dir)` in flat layout) safely, gracefully handling absence.
  - Introduce `_extract_effective_scale(data: str, manifest: AutoCodeManifest | None)` honoring `manifest.scale_token_index` first, then `manifest.multi_test` or unambiguous multi-test structure (`T` on line 1, integer scale on line 2), combined with byte/token scale hints in `_check_scale_distribution` so `T=1, n=200000` is never bucketed as `tiny`.
  - Apply `_extract_effective_scale` in `_check_boundary_coverage` and require suites with `>= 3` inputs in `_check_scale_distribution` to span `>= 2` non-empty scale buckets unless manifest primary bounds imply a narrow range (e.g., `n_max <= 10`).
- **Substantive blocking & transparent reporting for `statement_consistency` (`src/autocode_mcp/tools/audit_constraints.py`, `src/autocode_mcp/cli/report_format.py`)**:
  - Set `passed = False` in `_statement_consistency` whenever `validator_bound_mismatches` is non-empty, populating structured `message` and `review_reason` fields naming mismatched variables, validator bounds vs expected bounds, and missing constraint terms.
  - Render `## Statement Consistency & Communication Metrics` in `format_audit_markdown` whenever `passed is False` OR `validator_bound_mismatches` OR `constraint_terms_missing_from_statement` is non-empty, including a `| Variable | Validator Range | Reason |` table and missing-terms list; surface mismatch counts/variables in `format_audit_summary`.
- **Regression test coverage**: Add `tests/test_static_checks_and_consistency.py`.

## Capabilities

### New Capabilities

- `static-checks-and-consistency`: Multi-dimensional structural divergence gating for `limit_semantics`, multi-test-aware scale and boundary verification (`_extract_effective_scale`, tightened `scale_distribution`), substantive `validator_bound_mismatches` blocking in `statement_consistency`, and injection-safe Markdown/summary reporting of mismatch tables and missing terms.

### Modified Capabilities

- None (`openspec/specs/` canonical specs remain intact; `problem_verify_tests` and `problem_audit` workflows in `CLAUDE.md` steps 10–11 gain non-trivial gate enforcement without altering tool contracts).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Reinforces step 10 (`problem_verify_tests` checking `limit_semantics`, `scale_distribution`, `boundary_coverage`) and step 11 (`problem_audit` checking `statement_consistency` and formatting CI/CLI Markdown and summary reports) without changing any of the 22 MCP tool signatures.
- **Affected Files**:
  - `src/autocode_mcp/tools/verify_static_checks.py`
  - `src/autocode_mcp/tools/audit_constraints.py`
  - `src/autocode_mcp/cli/report_format.py`
  - `tests/test_static_checks_and_consistency.py`
  - `tests/test_integration/test_communication_e2e.py` (declare `flip_pos` bound `0 <= p <= 1000` in fixture matching `RUN_TWICE_VAL_CPP`)
- **Dependencies**: None added (uses standard library `math`, `hashlib`, `pathlib`, `re`, and existing `safe_resolve_under_root` / `load_manifest`).
- **Risks**:
  - *Threshold tuning on short/targeted generator outputs*: Small `type=3` vs `type=4` cases that differ primarily in parameter magnitude or ordering (e.g., sorted vs random, chain vs random tree) must clear `structural_divergence >= 0.15`, while identical-distribution random clones fail; mitigated by combining symmetric relative diffs for magnitudes, unscaled absolute diffs for `[0, 1]` ratios, and top-dimension aggregation (`0.7 * max + 0.3 * top3_mean`).
  - *Single-test vs multi-test ambiguity without `multi_test=True`*: Misreading single-test `n=1, a_1=10^9` as `T=1, n=10^9` could break single-test `boundary_coverage`; mitigated by checking line/token structure and manifest upper bounds when `manifest.multi_test` is False.
  - *Large or non-UTF8 `.in` files*: Handled safely via `errors="replace"` decoding, bounded digit-length integer parsing, and `safe_resolve_under_root` containment checks.

## Non-goals

- Do NOT modify any of the 22 MCP tool signatures (`server.py`, `tools/schemas.py`).
- Do NOT edit `tools/audit.py`, `tools/audit_discovery.py`, `tools/audit_standalone.py`, `tools/audit_signals.py`, `tools/complexity.py`, `workflow/models.py`, `workflow/gates.py`, `cli/audit.py`, or `pyproject.toml`.
- Do NOT generate new test files or write into audited problem directories.
