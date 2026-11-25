## Purpose

Define non-trivial structural divergence verification for limit/TLE test groups, multi-test-aware scale and boundary extraction, substantive statement-to-validator consistency gating, and injection-safe Markdown and summary reporting of bound mismatches.

## Requirements

### Requirement: Multi-dimensional structural divergence check for limit semantics
The system SHALL evaluate `_check_limit_semantics` by safely resolving `type=3` and `type=4` test inputs under `tests_dir` via `safe_resolve_under_root`, computing multi-dimensional per-case structural feature profiles (`byte_size`, `line_count`, `token_count`, `unique_token_ratio`, `distinct_token_count`, `numeric_ratio`, `max_abs_int`, `avg_tokens_per_line`, `max_line_tokens`, `adjacent_equal_ratio`, `monotonic_step_ratio`) plus optional per-case `std_time_ms` when present in the test manifest, and failing (`passed=False`) if either type is missing, paths are unsafe or unreadable, MD5 signature `overlap_ratio >= 0.8`, or normalized `structural_divergence` across dimensions is below `0.15`. Return payloads SHALL preserve `passed`, `type3_count`, `type4_count`, `overlap_ratio`, and `hint`, and include `structural_divergence`, `type3_profile`, and `type4_profile`.

#### Scenario: Homogeneous random clones with distinct seeds fail limit semantics
- **WHEN** `type=3` and `type=4` inputs are generated from the same random distribution with distinct seeds and unique MD5 hashes
- **THEN** `_check_limit_semantics` computes a low normalized `structural_divergence` (`< 0.15`) across `type3_profile` and `type4_profile` and returns `passed=False`

#### Scenario: Structurally distinct worst-case suites pass limit semantics
- **WHEN** `type=3` and `type=4` inputs exhibit genuine structural or scale-profile differences (such as constant/uniform stress vs structured chain, sorted vs random, or distinct token/magnitude profiles) with `overlap_ratio < 0.8`
- **THEN** `_check_limit_semantics` computes `structural_divergence >= 0.15` and returns `passed=True` with populated `type3_profile` and `type4_profile`

### Requirement: Multi-test effective scale extraction and non-collapsing scale distribution
The system SHALL provide `_extract_effective_scale(data, manifest)` that honors `manifest.scale_token_index` first, otherwise selects the case scale token on line 2 when `manifest.multi_test` is True or the input exhibits unambiguous multi-test structure (single positive integer `T` on line 1 and integer scale starting line 2 without contradicting single-test bounds), and falls back to the first integer token otherwise. Both `_check_scale_distribution` and `_check_boundary_coverage` SHALL use `_extract_effective_scale` (with `_check_scale_distribution` optionally loading `AutoCodeManifest` from `Path(tests_dir).parent` gracefully if absent, combining effective scale with byte/token scale hints so large multi-test inputs like `T=1, n=200000` are never bucketed as `tiny`, and failing suites with `>= 3` inputs that collapse into a single non-empty scale bucket unless manifest constraints imply a narrow upper range such as `n_max <= 10`).

#### Scenario: Multi-test extreme input T=1, n=200000 is not bucketed as tiny
- **WHEN** an input file starts with `T=1` on line 1 followed by `n=200000` on line 2 (with `multi_test=True`, `scale_token_index`, or unambiguous multi-test structure and/or large byte/token counts)
- **THEN** `_extract_effective_scale` extracts `200000`, `_check_scale_distribution` buckets it as `large` rather than `tiny`, and `_check_boundary_coverage` observes `200000` against `expected_max`

#### Scenario: Uniform single-bucket suite with three or more inputs fails unless constrained narrowly
- **WHEN** a test suite contains at least 3 non-empty inputs that all fall into a single scale bucket (such as all `n=1`) and manifest upper bounds do not imply a narrow range (`n_max <= 10`)
- **THEN** `_check_scale_distribution` returns `passed=False`

### Requirement: Substantive statement-validator consistency blocking and report rendering
The system SHALL set `passed=False` in `_statement_consistency` whenever `validator_bound_mismatches` is non-empty, populating structured `message` and `review_reason` fields that identify mismatched variable names, validator ranges vs expected bounds, and any missing constraint terms. The CLI formatters SHALL render `## Statement Consistency & Communication Metrics` in `format_audit_markdown` whenever `passed is False` OR `validator_bound_mismatches` OR `constraint_terms_missing_from_statement` is non-empty, presenting a safe Markdown table (`| Variable | Validator Range | Reason |`) for mismatches and a list for missing terms, and SHALL surface mismatch counts and variable names in `format_audit_summary`.

#### Scenario: Validator upper bound mismatch fails statement consistency and renders in Markdown and summary
- **WHEN** `files/val.cpp` constrains `n` to `[1, 1000]` while the statement or manifest declares `10^5` (`100000`)
- **THEN** `_statement_consistency` returns `passed=False` with `validator_bound_mismatches`, `message`, and `review_reason` naming `n`, `[1, 1000]`, and `100000`, `format_audit_markdown` renders the `| Variable | Validator Range | Reason |` table safely, and `format_audit_summary` reports the mismatch count and variable `n`

#### Scenario: Missing constraint terms render even when passed is True
- **WHEN** `statement_consistency` has `passed=True` and non-empty `constraint_terms_missing_from_statement`
- **THEN** `format_audit_markdown` still renders `## Statement Consistency & Communication Metrics` with the escaped missing constraint terms list
