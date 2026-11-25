---
name: autocode-workflow
description: Use when creating competitive programming problems with AutoCode MCP tools. Enforces the plugin workflow for problem statements, std/brute solutions, validators, generators, stress tests, final data verification, and Polygon packaging.
---

# AutoCode Problem Creation Workflow

AutoCode is a host-independent MCP workflow distributed through Claude Code, Codex, DeepSeek Harness (DSH), and Antigravity (`agy`) plugins. It supports `standard`, `spj`, `interactive`, and `communication` (`run_twice` / `grader_linked` / `multi_process`) problems. It exists because AI-generated problems often fail in subtle ways:

- statement is ambiguous or samples do not match the intended solution;
- standard solution has hidden bugs;
- claimed complexity is wrong;
- brute solution is not a reliable oracle;
- generator misses edge cases and TLE patterns;
- final tests do not kill wrong solutions;
- package is built before statement, tests, and manifest are consistent.

The workflow turns AI output into a gated pipeline. Do not skip gates.

## Status Output Contract

Every workflow checkpoint should use:

- `decision`: `go` / `no_go`
- `blocking_issues`: unmet gates or risks
- `next_actions`: exact MCP calls needed to proceed

If a gate fails, stop progression and fix first.

## Core Sequence

Non-interactive problems:

```text
problem_create
  -> solution_build(sol)
  -> solution_build(brute)
  -> solution_analyze / solution_audit_std / solution_audit_brute
  -> validator_build(accuracy >= 0.9)
  -> generator_build
  -> stress_test_run(completed_rounds == total_rounds) (if `special_judge` + `stress_comparison: "checker"`, run `checker_build` first; stress calls `checker(in, sol, brute)`; optional `stress_checker_bidirectional` adds `checker(in, brute, sol)` — see checker prompt)
  -> checker_build(if non-exact output; non-SPJ default is after stress)
  -> problem_validate
  -> problem_generate_tests
  -> problem_verify_tests(passed)
  -> problem_pack_polygon
```

Interactive & Communication problems:

```text
problem_create(problem_type="interactive" | "communication", protocol_mode=..., oracle_mode=...)
  -> solution_build(sol)              # grader_linked auto-links files/grader.cpp + files/problem.h
  -> [solution_build(brute)]          # required only when oracle_mode == "brute"
  -> solution_analyze / solution_audit_std [/ solution_audit_brute]
  -> interactor_build                 # interactive / run_twice / multi_process (optional validator_build allowed; no checker_build)
  -> generator_build
  -> stress_test_run                  # live InteractiveRunner / CommunicationRunner
  -> problem_validate
  -> problem_generate_tests
  -> problem_verify_tests(passed)     # collects protocol_metrics & enforces wrong_solution_kill
  -> problem_audit(mode="full")       # reports communication_metrics & budget utilization
  -> problem_pack_polygon(export_formats=["polygon", "hydro", "qoj"])
```

The authoritative implementation is `src/autocode_mcp/workflow/enforcement.py`, called by the MCP server for every managed workflow tool. `scripts/workflow_guard.py` is only the Claude Code hook adapter; Codex, DSH, and Antigravity use the same server-side gates without hooks.

Interactive and communication tasks are not just "no validator". They require an explicit protocol contract in the statement and a testlib interactor / communicator / grader that can reject protocol and budget violations. Treat missing protocol semantics as a blocker, the same severity as a missing validator for a non-interactive task.

## Mandatory Gates

| Gate | Requirement |
|------|-------------|
| Problem setup | `problem_create` must create directory structure and `manifest.json` |
| Standard solution | `solution_build(solution_type="sol")` succeeds (or `participant_solutions` for `multi_process`) |
| Brute solution | `solution_build(solution_type="brute")` succeeds after sol (required when `oracle_mode == "brute"`) |
| Complexity audit | `solution_analyze`, `solution_audit_std` (and `solution_audit_brute` when `oracle_mode == "brute"`) reviewed; re-running after `generator_build` with unchanged source SHA-256 executes dynamic empirical/brute probes non-destructively |
| Validator | Standard / SPJ: `validator_build` passes both positive (`expected_valid=True`) and negative (`expected_valid=False`) cases with `accuracy >= 0.9` (optional for interactive / communication) |
| Interactor / Communicator | Interactive & communication (`run_twice` / `multi_process`): `interactor_build` is ready; `grader_linked`: `files/grader.cpp` + `files/problem.h` linked |
| Generator | `generator_build` succeeds after validator/interactor gate |
| Stress | `stress_test_run` completes all rounds |
| Statement validation | `problem_validate` passes samples / protocol checks and sample files |
| Final tests | `problem_generate_tests` creates final tests |
| Test verification | `problem_verify_tests` passes before packaging |
| Full audit | `problem_audit(mode="full")` passes when `audit_gates.require_full_audit=true` (`standalone=True` for external read-only audits) |
| Packaging | `problem_pack_polygon` only after verified tests (supports `export_formats`: `polygon`, `hydro`, `qoj`) |

## Audit Agents

Use these agents when the risk is material:

- `autocode-idea-auditor`: before implementation, especially if the idea has unclear constraints, multiple valid outputs, or interaction.
- `autocode-solution-auditor`: after std/brute exist, before relying on stress results or final generation.
- `autocode-package-auditor`: before `problem_pack_polygon`, especially when wrong solutions, checker, interactor, or custom answer extension are involved.

## Tool Guidance

### `problem_create`

Creates:

- `manifest.json` (accepts optional `time_limit_ms` / `memory_limit_mb`; refuses to overwrite an existing `.autocode/manifest.json` unless `overwrite=true`)
- `solutions/`
- `files/`
- `statements/README.md`
- `statements/tutorial.md`
- `tests/`

Statement format requirement for `statements/README.md`:

- title;
- time/memory limits;
- optional background;
- problem description;
- input format (must include all variable ranges and aggregate constraints);
- output format;
- samples (numbered in ascending order when multiple);
- explanation (sample explanations must be placed here, not mixed into sample blocks; only representative samples need explanation).

Do not infer that a problem is ready from file presence alone. Prefer structured tool results and workflow state.

### `solution_build`

Build `sol` before `brute`.

On macOS, default `g++` is Apple Clang and does not support `<bits/stdc++.h>`. Prefer standard C++20 headers (`<iostream>`, `<vector>`, `<algorithm>`, etc.) or set `"compiler": "g++-14"` in `.autocode/manifest.json` (`problem_create` and `solution_build` detect Apple Clang and surface warnings automatically).

Modifying/rebuilding `sol.cpp` uses SHA-256 hash-based gate invalidation: it invalidates `solution_analyze` / `solution_audit_std` without unnecessarily resetting `solution_audit_brute` when `brute.cpp` is unchanged.

For non-trivial problems, `brute` must be independent enough to serve as an oracle. If it is the same algorithm as `sol`, mark this as a risk and run `solution_audit_brute`.

Before writing brute, perform a quick counterexample check on paper:

- verify brute directly simulates problem constraints;
- reject simplifications that change mandatory selection semantics.

### `solution_analyze` and audit tools

Use:

- `solution_analyze` to estimate time/space complexity, risk notes, and recommended stress profiles.
- `solution_audit_std` to check std complexity and constraint mismatch (both tools share `static_estimated_complexity` and emit explicit `complexity_conflict` warnings when claimed/static/empirical estimates disagree).
- `solution_audit_brute` to check whether brute can support stress testing (runs a small-scale stepped probe with `RLIMIT_CORE=0` when binaries exist, hard-failing on crashes/OOM and auto-clamping `recommended_stress_params.n_max` on timeout).
- Re-running `solution_analyze`, `solution_audit_std`, or `solution_audit_brute` **after `generator_build`** with unchanged canonical `sol.cpp` / `brute.cpp` SHA-256 runs dynamic empirical complexity fitting (`std_empirical_verified`) and `_probe_brute_small_scale` (`brute_probed`) **without** resetting `std_audited`, `brute_audited`, `validator_ready`, `generator_built`, `checker_ready`, `interactor_ready`, or `stress_passed` (failing closed if a dynamic probe fails). Check `dynamic_probe` (`ran` vs `pending_binaries`) in audit responses.

Do not accept a claimed complexity without evidence.

### `validator_build`

Non-interactive problems need a validator with evidence. A validator build without effective accuracy or negative counterexamples is not enough (`validator_ready` requires both positive and negative cases).

Target:

- `accuracy >= 0.9`
- `has_positive_cases == true` (`expected_valid=True`): normal, boundary, and maximum cases;
- `has_negative_cases == true` (`expected_valid=False`): near-valid but illegal formats/ranges (prevents permissive `return 0;` validators from passing).

### `interactor_build`

Interactive problems and communication problems (`run_twice` / `multi_process`) use `interactor_build` (and must not run `checker_build`; optional `validator_build` is permitted to validate `.in` files).

Require an explicit interaction or communication protocol in the statement before final packaging:

- Interactive (`## 交互协议`): who outputs first; hidden input/range/randomness/adaptiveness; every query and final-answer command; judge response format and meaning; numeric query/round limits; flush requirement after every contestant output; immediate termination behavior after final answer; verdicts for malformed tokens, out-of-range arguments, too many queries, premature EOF, blocked protocol, and extra output.
- Communication (`## 通信协议`): protocol mode (`run_twice` / `grader_linked` / `multi_process`); process isolation rules or header function signatures; numeric communication budgets (`max_bits` / `max_bytes` / `max_rounds` / `max_queries`); channel/noise model; over-budget and invalid-format verdicts; multi-stage transcript samples.

Interactive interactor implementation requirements:

- use `registerInteraction(argc, argv)`;
- read testcase data from `inf` and optional jury data from `ans`;
- write to the contestant via `std::cout` and flush with `std::flush` / `std::endl` (use `tout` only for optional diagnostic logs written to `argv[2]`);
- read contestant output via `ouf`;
- end every branch with `quitf(_ok/_wa/_pe/_fail, ...)`;
- include scripted `interaction_scenarios` in `interactor_build` that cover AC, wrong final answer, malformed command, out-of-range query, query-limit boundary, exceeding query limit, and premature EOF;
- `reference_solution_path` should point to a compiled executable (e.g. `solutions/sol`), though passing a `.cpp` path automatically maps to or compiles its binary, and omitting `reference_solution_path` while providing `test_inputs` auto-resolves a built `solutions/sol`.

Communication implementation requirements:

- `run_twice`: `files/communicator.cpp` uses `registerTestlibCmd(argc, argv)` (strict 4-arg testlib convention) and reads `AUTOCODE_COMM_STAGE` / `AUTOCODE_COMM_ROLE` env vars; each pass runs in an isolated process and temporary working directory; emit `bits_used=... bytes_used=... rounds=...` in `quitf` messages so `CommunicationRunner` and `problem_audit` enforce budgets.
- `grader_linked`: provide `files/grader.cpp` and `files/problem.h`; `solution_build` links them automatically and invalidates compile cache when local headers change.
- `multi_process`: communicator routes lines across K participant processes using `TO <id> <payload>` / `CLOSE <id>` and reads `FROM <id> <payload>` / `EOF <id>`.

### `generator_build`

Generator should implement semantically distinct strategies:

- `type=1`: tiny / exhaustive / sanity;
- `type=2`: random coverage;
- `type=3`: boundary and extreme constraints;
- `type=4`: targeted worst-case or TLE-inducing patterns.

`type=4` must not be only "same as type=3 but with max parameters".

### `stress_test_run`

Use multiple profiles when possible:

- `tiny_exhaustive`
- `random_small`
- `edge_small`

Use `stress_test_run` advisory fields (`complexity_context`, `n_max_advisory`) to choose `n_max` based on audit evidence instead of fixed thresholds.

Pass `enable_sanitizers=true` to run an isolated ASan/UBSan stress pass (compiled into `.autocode/san_bin/` without overwriting production `-O2` binaries; Linux preloaded containers automatically fall back to `-static-libasan -static-libubsan` and verify a negative-control probe before running).

Proceed only when `completed_rounds == total_rounds`.

### `problem_validate`

Validation failure is a release blocker. Do not generate final tests or package until statement samples and sample files pass. Oversized sample previews are automatically truncated in the MCP response and spilled to `.autocode/logs/`.

### `problem_generate_tests`

Final tests should include at least half limit-oriented cases (`type=3` + `type=4` or `group="limit"`) when candidates are available.

Use `mode="replace"` (default, automatically preserving `locked: true` entries and `tests/hack/*.in`), `mode="append"` (append new tests after existing indices without overwriting), or `mode="register_existing"` (index hand-crafted tests in `tests/` without regenerating). Custom generator types (such as `"5"`, `"6"`, `"hack"`) are supported in both `case_plan.type` and `test_configs[*].type`.

For `type=4` profiles with `extra_args` (for example `mode=tle_dense`), the runner may fallback to a no-extra-args retry when the generator rejects those args, to preserve compatibility with older generators.

For long-running generation:

- warn the user that new chat messages can interrupt MCP calls;
- if interrupted, use `resume=true`;
- use `problem_cleanup_processes` only when cleanup is needed.

### `problem_verify_tests`

Must pass before packaging. When `verify_types` is omitted on a managed workspace, AutoCode runs all checks required by `manifest.json` (plus `duplicate_inputs`, `scale_distribution`, `purpose_coverage`, and `wrong_solution_kill` when wrong solutions exist); pass `verify_types=["all"]` to include `boundary_coverage` and applicable self-tests. Re-running a subset merges signals by key when the dataset/source fingerprint is unchanged instead of erasing prior passed signals.

With `special_judge: true` and `stress_comparison: "checker"` (and a built `files/checker`), `answer_consistency` and `wrong_solution_kill` use the testlib checker against jury answers; with `stress_comparison: "exact"` they still compare strings to the answer files.

`wrong_solution_kill` auto-discovers and auto-compiles missing or stale `solutions/wrong_*.cpp` files with the same compiler and resource limits as `sol.cpp`. When `manifest.solutions` declares any `role == "wrong"` entry and `quality_gates.require_wrong_solution_kill` is omitted, gate evaluation (`check_gates` / `PRE_GATES`) treats `require_wrong_solution_kill` as effectively enabled (`true`); set `"require_wrong_solution_kill": false` explicitly to opt out. When `require_wrong_solution_kill` is enabled and zero wrong solutions are configured or present, it fails closed (`passed=false`) instead of returning a false pass. `duplicate_inputs` enforces zero tolerance on exact byte-identical duplicates (`exact_duplicates > 0` fails immediately). Wrong entries honor manifest `expected`: default `fail` means at least one test must reject the binary; `pass` means all tests must accept it (checker AC or exact match to `.ans`). The report includes per-test timing breakdowns, `wrong_ms / std_ms` ratios, `max_memory_mb`, configurable TLE policies (`quality_gates.wrong_tle_kill_factor`, default `1.0`, and optional `wrong_slowdown_kill_ratio`), and `host_performance_bias` warnings in `problem_audit` when a fast local CPU may mask TLE on typical OJ hardware.

**Near-AC Single-Point Mutation Red-Teaming (Recommended)**: beyond standard naive/wrong solutions, construct 2–3 near-AC single-point mutations of `sol.cpp` (e.g. off-by-one boundary, missing tie-breaker, 32-bit overflow, dropped edge-case branch) as `solutions/wrong_*.cpp` and verify `wrong_solution_kill` rejects every one; you can also enable automated single-point mutation testing via `audit_gates.enable_mutation_testing=true` or `autocode-audit --mutation-check`, and strengthen tests via `mode="append"` or `tests/hack/` if any mutation passes all tests.

Run `autocode-verify <problem_dir> --status` (or `--strict-gates` / `--reconcile` after cross-platform moves) for unified manifest, binary freshness, and MCP gate status.

### `problem_pack_polygon`

Only package after `problem_verify_tests(passed=true)` (and `problem_audit(mode="full")` when `audit_gates.require_full_audit=true`).

Prefer `time_limit_ms` (milliseconds, matching `manifest.json`). Passing large values (`> 60`) or values deviating by >10x from `manifest.time_limit_ms` via deprecated `time_limit` (seconds) is rejected by default unless `allow_time_limit_override=true`. After exporting, AutoCode automatically reads back Polygon `problem.xml` (verifying every `path=` reference and test file inside self-contained `exports/polygon/` or `exports/polygon.zip`), Hydro `problem.yaml` + `testdata/config.yaml`, and QOJ `problem.conf` to verify TL, ML, and file integrity.

Pass `export_formats` to export one or more judge formats in a single call (default `["polygon"]`), and optional `export_data_mode` (`"hardlink"` default to avoid 3x workspace disk duplication with automatic copy fallback, `"copy"`, or `"zip"` for compressed uploadable archives under `exports/<fmt>.zip`):

- `polygon`: writes workspace `problem.xml` plus a self-contained `exports/polygon/` directory (or `exports/polygon.zip` in `zip` mode) with all referenced statements, sources, resources, and test files (including `<groups>`/`<dependencies>`/`<tests>` when `subtasks` are defined, and `<communication>` for communication problems).
- `hydro`: exports under `exports/hydro/` (or `exports/hydro.zip` in `zip` mode): `problem.yaml` (without internal tool tags), `problem_zh.md`, `testdata/config.yaml` with subtasks and judge sources; interactive and communication exports omit missing or dummy/placeholder `.ans` files and validate protocol config.
- `qoj`: exports under `exports/qoj/` (or `exports/qoj.zip` in `zip` mode): `problem.conf`, `statement.md`, 1-based contiguous `data/1.in..N.in`, `two_stage`/`with_implementer`/`interaction_mode`, and subtask dependencies; interactive and communication exports omit missing or dummy/placeholder `.ans` files and validate `use_builtin_judger on` & protocol mode.

`problem_pack_polygon` exempts both `interactive` and `communication` problems from mandatory `.ans` files and emits `pack_warnings` if undeclared wrong solutions (`wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, `bf.cpp`) exist under `solutions/`.

## Manifest

Each problem should maintain `manifest.json` as a readable contract. It should describe:

- problem name;
- `problem_type` (`standard` | `spj` | `interactive` | `communication`) and `oracle_mode` (`brute` | `interactor_only` | `reference_solutions`);
- `protocol` (for interactive/communication: `mode`, `passes`, `pass_headers`, `max_bits`, `max_bytes`, `max_rounds`, `max_queries`, `communicator_file`, `grader_file`, `header_file`, `participant_solutions`);
- `subtasks` (id, name, score, constraints, dependencies, `test_indices` / `groups`);
- statement/tutorial paths;
- time and memory limits, and optional `compiler` override (e.g. `"g++-14"` on macOS);
- optional `empirical_complexity` config (`dominant_var`, `scale_points`, `generator_args_template`, `extra_vars`, `scale_mode`, `metric`) for multi-parameter, exponential, or interactive problems;
- `quality_gates` including `wrong_tle_kill_factor` and optional `wrong_slowdown_kill_ratio`;
- solution roles (including `wrong` solutions and optional per-entry `expected`: `fail` | `pass` for `wrong_solution_kill`);
- case plan;
- optional SPJ fields: `special_judge`, `stress_comparison` (`exact` | `checker`), and optional `stress_checker_bidirectional` (only meaningful with `special_judge` + `stress_comparison=checker`).

Use `autocode-verify <problem_dir>` for quick structural checks, and `autocode-audit <problem_dir> --standalone --layout <auto|autocode|standard_cn|polygon|hydro>` (or `problem_audit(standalone=True)`) to audit external non-AutoCode packages read-only with isolated temporary compilation.

## Forbidden Actions

1. Do not build brute before sol.
2. Do not build generator before validator/interactor gate.
3. Do not run stress before sol, brute, and generator are ready.
4. Do not validate/package based on file presence alone.
5. Do not generate final tests before statement validation passes.
6. Do not package before `problem_verify_tests` passes.
7. Do not ignore a structured MCP gate denial (or a Claude hook denial); fix the missing prerequisite instead.

## Failure Recovery

When a step fails:

1. Report `decision=no_go`.
2. List `blocking_issues`.
3. Identify whether the fault is in statement, std, brute, validator, generator, checker/interactor, or tests.
4. Fix the failed artifact.
5. Re-run the failed gate and any downstream gate whose assumptions changed.

Never patch around a failed gate by skipping it.
