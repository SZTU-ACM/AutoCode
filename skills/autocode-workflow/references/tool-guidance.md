# AutoCode Tool Guidance Reference

Detailed execution guidance for each AutoCode MCP workflow tool.

## `problem_create`

Creates the managed workspace structure:

- `.autocode/manifest.json` (accepts optional `time_limit_ms` / `memory_limit_mb`; refuses to overwrite an existing `.autocode/manifest.json` unless `overwrite=true`)
- `solutions/`
- `files/`
- `statements/README.md`
- `statements/tutorial.md`
- `tests/`

Required section sequence for `statements/README.md`:

1. Title
2. Time/memory limits
3. Optional background
4. Problem description
5. Input format (including all variable ranges and aggregate constraints)
6. Output format
7. Samples (numbered in ascending order when multiple)
8. Explanation (place sample explanations here, never mixed into sample blocks; only representative samples need explanation)

Do not infer problem readiness from file presence alone; rely on structured tool results and workflow state.

## `solution_build`

Build `sol` before `brute`.

- On macOS, default `g++` is Apple Clang and does not support `<bits/stdc++.h>`. Prefer standard C++20 headers (`<iostream>`, `<vector>`, `<algorithm>`, etc.) or set `"compiler": "g++-14"` in `.autocode/manifest.json` (`problem_create` and `solution_build` surface Apple Clang warnings automatically).
- Rebuilding `sol.cpp` uses SHA-256 hash-based gate invalidation: it invalidates `solution_analyze` / `solution_audit_std` without unnecessarily resetting `solution_audit_brute` when `brute.cpp` is unchanged.
- For non-trivial problems, `brute` must be independent enough to act as a reliable oracle; if it mirrors `sol`, flag the risk in `solution_audit_brute`.
- Before implementing `brute`, run a quick paper counterexample check verifying direct constraint simulation without simplifying mandatory selection rules.

## `solution_analyze` and Audit Tools

- `solution_analyze`: estimates time/space complexity, risk notes, and recommended stress profiles.
- `solution_audit_std`: checks claimed vs static/empirical complexity and constraint mismatches (`complexity_conflict` warnings when estimates disagree).
- `solution_audit_brute`: verifies brute suitability as a stress oracle (runs a small-scale stepped probe with `RLIMIT_CORE=0` when binaries exist, hard-failing on crashes/OOM and auto-clamping `recommended_stress_params.n_max` on timeout).
- Re-running `solution_analyze`, `solution_audit_std`, or `solution_audit_brute` **after `generator_build`** with unchanged canonical `sol.cpp` / `brute.cpp` SHA-256 executes dynamic empirical complexity fitting (`std_empirical_verified`) and `_probe_brute_small_scale` (`brute_probed`) **without** resetting `std_audited`, `brute_audited`, `validator_ready`, `generator_built`, `checker_ready`, `interactor_ready`, or `stress_passed` (failing closed on probe failure). Inspect `dynamic_probe` (`ran` vs `pending_binaries`).

## `validator_build`

Non-interactive problems require a validator backed by positive and negative evidence:

- `accuracy >= 0.9`
- `has_positive_cases == true` (`expected_valid=True`): normal, boundary, and maximum cases
- `has_negative_cases == true` (`expected_valid=False`): near-valid but illegal formats/ranges (prevents vacuous `return 0;` validators)

## `generator_build`

Implement semantically distinct generator strategies:

- `type=1`: tiny / exhaustive / sanity
- `type=2`: random coverage
- `type=3`: boundary and extreme constraints
- `type=4`: targeted worst-case or TLE-inducing patterns (must NOT be just `type=3` with larger parameters)

## `stress_test_run`

- Use at least 3 profiles when possible: `tiny_exhaustive`, `random_small`, and `edge_small`.
- Use `complexity_context` and `n_max_advisory` to select `n_max` from audit evidence.
- Pass `enable_sanitizers=true` for an isolated ASan/UBSan stress pass compiled into `.autocode/san_bin/` without overwriting `-O2` binaries (automatically falls back to `-static-libasan -static-libubsan` on preloaded Linux containers and runs a negative-control probe first).
- Proceed only when `completed_rounds == total_rounds`.

## `problem_validate`

Validation failure blocks final test generation and packaging. Oversized sample previews truncate automatically in MCP responses and spill to `.autocode/logs/`.

## `problem_generate_tests`

- Include at least 50% limit-oriented cases (`type=3` + `type=4` or `group="limit"`) when candidates exist.
- Supported modes:
  - `mode="replace"` (default, preserves `locked: true` entries and `tests/hack/*.in`)
  - `mode="append"` (appends after existing indices)
  - `mode="register_existing"` (indexes hand-crafted files in `tests/`)
- Custom generator types (`"5"`, `"6"`, `"hack"`) are supported in `case_plan.type` and `test_configs[*].type`.
- For `type=4` with `extra_args` (e.g. `mode=tle_dense`), the runner falls back to no-extra-args if older generators reject extra flags.
- For long runs, warn users not to interrupt; if interrupted, use `resume=true`; use `problem_cleanup_processes` only when needed.

## `problem_verify_tests`

- Omitting `verify_types` on a managed workspace runs all required manifest checks (`file_count`, `answer_consistency`, `validator`, `no_empty`, `limit_ratio`, `limit_semantics`, `duplicate_inputs`, `scale_distribution`, `purpose_coverage`, plus `wrong_solution_kill` when wrong solutions exist). Pass `verify_types=["all"]` to include `boundary_coverage`, `coverage`, and applicable self-tests. Re-running a subset merges signals by key when dataset/source fingerprints match.
- SPJ (`special_judge=true` + `stress_comparison="checker"` + built `files/checker`) checks `answer_consistency` and `wrong_solution_kill` via the testlib checker.
- `wrong_solution_kill` auto-discovers and auto-compiles `solutions/wrong_*.cpp`. When `manifest.solutions` declares `role == "wrong"` and `quality_gates.require_wrong_solution_kill` is omitted, it defaults to effectively enabled (`true`); explicit `false` opts out. With zero wrong solutions and the gate enabled, it fails closed (`passed=false`).
- `duplicate_inputs` enforces zero tolerance on byte-identical exact duplicates (`exact_duplicates > 0` fails immediately).
- Wrong entries honor `expected` (`fail` default vs `pass` for alternate valid outputs) and `expected_verdict` (`WA`/`TLE`/`MLE`/`RE`/`PE`). Reports per-test timings, `wrong_ms / std_ms`, `max_memory_mb`, `wrong_tle_kill_factor`, `wrong_slowdown_kill_ratio`, and `host_performance_bias` warnings in `problem_audit`.
- **Near-AC Single-Point Mutation Red-Teaming**: construct 2-3 near-AC mutations as `solutions/wrong_*.cpp` (or enable `audit_gates.enable_mutation_testing=true` / `autocode-audit --mutation-check`) and persist verified hacks in `tests/hack/`.

## `problem_pack_polygon`

- Prefer `time_limit_ms` (milliseconds). Large (`> 60`) or >10x divergent values via deprecated `time_limit` (seconds) are rejected unless `allow_time_limit_override=true`.
- Exports read back and verify Polygon `problem.xml`, Hydro `problem.yaml` + `testdata/config.yaml`, and QOJ `problem.conf`.
- `export_formats`: `["polygon", "hydro", "qoj"]`; `export_data_mode`: `"hardlink"` (default), `"copy"`, or `"zip"` (outputs under `exports/<fmt>.zip`).
- Interactive and communication packages omit missing/placeholder `.ans` files and emit `pack_warnings` when undeclared wrong-solution filenames (`wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `tle_*.cpp`, `mle_*.cpp`, `re_*.cpp`, `bf.cpp`) exist under `solutions/`.
