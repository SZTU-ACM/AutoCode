## Context

AutoCode's deterministic verification (`problem_verify_tests` via `verify_static_checks.py`) and audit reporting (`problem_audit` via `audit_constraints.py` and `cli/report_format.py`) enforce test-suite diversity, scale coverage, and statement-to-validator bound alignment. Prior implementations treated MD5 non-overlap as proof of structural difference, assumed the first integer token always represented $n$, accepted uniform single-bucket test suites, and allowed `validator_bound_mismatches` to pass silently without rendering mismatch details in Markdown/summary CLI outputs.

Naming boundaries remain strictly separated:
- Server verification, constraint parsing, and CLI formatting live in `src/autocode_mcp/tools/verify_static_checks.py`, `src/autocode_mcp/tools/audit_constraints.py`, and `src/autocode_mcp/cli/report_format.py`.
- Client hook scripts under `scripts/...` are untouched.

## Architecture & Design Decisions

### 1. Structural Profile Divergence in `_check_limit_semantics` (`verify_static_checks.py`)

```text
tests_manifest["tests"]
  ├── filter type_param == "3" and "4"
  ├── check MD5 signature overlap_ratio < 0.8
  ├── safe_resolve_under_root(tests_dir, in_file) -> reject escapes / symlinks
  │
  ├── per-case 11D profile (+ optional std_time_ms):
  │     byte_size, line_count, token_count, unique_token_ratio,
  │     distinct_token_count, numeric_ratio, max_abs_int,
  │     avg_tokens_per_line, max_line_tokens,
  │     adjacent_equal_ratio, monotonic_step_ratio
  │
  ├── group mean profiles: type3_profile, type4_profile
  └── normalized dimension diffs -> structural_divergence = 0.7 * max + 0.3 * top3_mean
        └── fail if structural_divergence < 0.15
```

- **Normalization per dimension (prevents near-zero ratio amplification)**:
  - Magnitude/count features (`byte_size`, `line_count`, `token_count`, `distinct_token_count`, `max_abs_int`, `avg_tokens_per_line`, `max_line_tokens`): `abs(a - b) / max(abs(a), abs(b))` (`0.0` when both are `0.0`).
  - `[0, 1]` bounded ratios (`unique_token_ratio`, `numeric_ratio`, `adjacent_equal_ratio`): absolute difference `abs(a - b)` so one random duplicate out of 1,000 tokens (`0.001` vs `0.0`) does not falsely yield `1.0`.
  - Signed `monotonic_step_ratio` in `[-1, 1]` (`(inc - dec) / (k - 1)`): `abs(a - b) / 2.0`.
  - Optional `std_time_ms` (from manifest entries `std_time_ms`/`time_ms` or top-level `std_timings`/`per_test_timings`): `abs(a - b) / max(a, b, 5.0)` with a `5.0 ms` floor to ignore OS timer jitter on fast runs.
- **Top-dimension aggregation vs unweighted 11D mean**:
  - **Tradeoff**: Targeted worst-case generators (e.g., sorted vs random permutation, chain tree vs random tree) intentionally share `byte_size`, `line_count`, `token_count`, and `max_abs_int`, differing sharply in 1–2 structural dimensions (`monotonic_step_ratio`, `adjacent_equal_ratio`). An unweighted average over 11 dimensions dilutes a `0.50` single-dimension structural contrast to `0.045–0.09`, whereas `0.7 * max(diffs) + 0.3 * top3_mean` preserves sensitivity to targeted worst-case structures (`>= 0.24`) while keeping homogeneous random clones (`~0.01–0.09`) cleanly below the `0.15` threshold.
- **Integer parsing safety**: Integer tokens longer than 300 digits are clamped/parsed safely without triggering Python's `int` string-conversion limit or slow arbitrary-precision loops.

### 2. Multi-Test Scale Extraction & Tightened Scale Distribution (`verify_static_checks.py`)

```text
_extract_effective_scale(data, manifest)
  ├── 1. manifest.scale_token_index (if set & in range & int) -> tokens[idx]
  ├── 2. line 1 single positive int T & line 2 starts with int n_val:
  │       ├── manifest.multi_test is True -> n_val
  │       └── unambiguous multi-test heuristic (T <= 10^5, len(line2) <= 4,
  │           multi-line / multi-token body, n_val <= manifest n_max if known,
  │           not single-test n-element line or n-vertex edge list) -> n_val
  └── 3. fallback -> first integer token in data
```

- **Tradeoff on single-test vs multi-test disambiguation**: When `manifest.multi_test` is False (or `manifest` is absent), single-test inputs like `1\n1000000000\n` (`n=1, a_1=10^9`) or `5\n1 2\n2 3\n3 4\n4 5\n` (`n=5` tree) must still extract `n=1` and `n=5`. Requiring multi-line body evidence (or no contradicting `manifest` `n_max` when `manifest` is present) prevents false scale inflation on single-test suites while catching `T=1, n=200000` multi-test inputs even without explicit `multi_test=True`.
- **Byte/token scale hints in `_check_scale_distribution`**: Regardless of header integers, inputs exceeding `32 KB` or `1000` tokens are floored at `"large"`, and inputs exceeding `1 KB` or `100` tokens are floored at `"medium"`, ensuring large multi-test suites are never classified as `"tiny"`.
- **Collapse guard**: Suites with `>= 3` inputs fail if `non_empty_scale_bucket_count < 2`, unless extracted primary bounds from `manifest.constraints` indicate a narrow domain (`n_max <= 10`, or `n_min` and `n_max` lie in the same scale bucket). Missing or absent `.autocode/manifest.json` falls back gracefully via `safe_resolve_under_root` + `load_manifest`.

### 3. `statement_consistency` Blocking & Report Transparency (`audit_constraints.py`, `cli/report_format.py`)

- **Substantive blocking**: In `_statement_consistency`, `passed = has_samples and tutorial_ready and not validator_bound_mismatches`. Missing constraint terms (`constraint_terms_missing_from_statement`) alone continue to set `needs_human_review = True` without forcing `passed = False`, preserving compatibility for problems whose statements express bounds implicitly (e.g., bit budget implies $2^8$), while explicit `val.cpp` range conflicts strictly fail `passed`.
- **Structured diagnostics**: Both `message` (populated when `not passed`) and `review_reason` (populated when `needs_human_review`) summarize mismatched variable names, validator `[min, max]` vs expected bounds, and missing constraint terms.
- **Markdown & summary visibility**: `format_audit_markdown` renders `## Statement Consistency & Communication Metrics` whenever `passed is False` OR `validator_bound_mismatches` OR `constraint_terms_missing_from_statement` is non-empty, displaying a `| Variable | Validator Range | Reason |` table and missing-terms bullet list with all untrusted strings passed through `_md_code` / `_escape_md_cell`. `format_audit_summary` emits a single-line `STMT_CONSISTENCY:` line sanitized with `_collapse_inline` showing pass status, mismatch count, variable names, and missing-term count.

## Risks / Mitigations

- **Legacy callers passing 1 argument to `_check_scale_distribution(tests_dir)`**: Signature keeps `manifest: AutoCodeManifest | None = None` optional and loads `Path(tests_dir).parent` automatically, preserving `test_verify.py` and `audit_signals.py` compatibility without touching forbidden files.
- **Corrupt or symlink-escaped files**: All input reads in `_check_limit_semantics`, `_check_scale_distribution`, `_check_boundary_coverage`, and optional manifest discovery resolve via `safe_resolve_under_root` and handle `OSError`/`ValueError` safely.
- **Markdown injection / ANSI escape leakage**: All new table cells and summary tokens pass through `_md_code`, `_escape_md_cell`, and `_collapse_inline`.
