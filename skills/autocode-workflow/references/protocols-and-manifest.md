# Protocols, Manifest, and CLI Reference

Detailed reference for interactive/communication protocols, `manifest.json` fields, and AutoCode CLI tools.

## Interactive & Communication Protocols

`interactor_build` applies to `interactive` problems and `communication` problems (`run_twice` / `multi_process`). Do not call `checker_build` for protocol problems; optional `validator_build` is allowed for `.in` inputs.

### Interactive (`## 交互协议`)

Statement must specify:

- who outputs first, hidden input/range/randomness/adaptiveness;
- every query and final-answer command, judge response format and meaning;
- numeric query/round limits, flush requirement after every contestant output, immediate termination after final answer;
- verdicts for malformed tokens, out-of-range arguments, too many queries, premature EOF, blocked protocol, and extra output.

Interactor implementation (`files/interactor.cpp`):

- call `registerInteraction(argc, argv)`;
- read test input via `inf` and optional jury data via `ans`;
- write to the contestant via `std::cout` and flush with `std::flush` / `std::endl` (use `tout` ONLY for optional diagnostic logs to `argv[2]`);
- read contestant output via `ouf`;
- terminate every branch with `quitf(_ok/_wa/_pe/_fail, ...)`;
- supply scripted `interaction_scenarios` covering AC, wrong final answer, malformed command, out-of-range query, query-limit boundary, exceeding query limit, and premature EOF;
- `reference_solution_path` accepts a compiled binary (or `.cpp` auto-mapped/compiled); omitting it while passing `test_inputs` auto-resolves built `solutions/sol`.

### Communication (`## 通信协议`)

Statement must specify protocol mode (`run_twice` / `grader_linked` / `multi_process`), isolation rules or `problem.h` signatures, numeric budgets (`max_bits` / `max_bytes` / `max_rounds` / `max_queries`), channel/noise model, error verdicts, and multi-stage transcripts.

Implementation requirements:

- `run_twice`: `files/communicator.cpp` uses `registerTestlibCmd(argc, argv)` (4-arg testlib convention), reads `AUTOCODE_COMM_STAGE` / `AUTOCODE_COMM_ROLE` env vars, runs each pass in an isolated process and temp cwd, and emits `bits_used=... bytes_used=... rounds=...` in `quitf` messages so `CommunicationRunner` and `problem_audit` enforce budgets.
- `grader_linked`: provide `files/grader.cpp` and `files/problem.h`; `solution_build` links them automatically and invalidates compile cache when local headers change.
- `multi_process`: communicator routes lines across `K` participant processes via `TO <id> <payload>` / `CLOSE <id>` and reads `FROM <id> <payload>` / `EOF <id>`.

## `manifest.json` Contract

Each problem maintains `.autocode/manifest.json` with:

- `problem_name`, `problem_type` (`standard` | `spj` | `interactive` | `communication`), `oracle_mode` (`brute` | `interactor_only` | `reference_solutions`)
- `protocol` (`mode`, `passes`, `pass_headers`, `max_bits`, `max_bytes`, `max_rounds`, `max_queries`, `communicator_file`, `grader_file`, `header_file`, `participant_solutions`)
- `subtasks` (`id`, `name`, `score`, `constraints`, `dependencies`, `test_indices` / `groups`)
- `statement_path`, `tutorial_path`, `time_limit_ms`, `memory_limit_mb`, optional `compiler` (e.g. `"g++-14"` on macOS)
- optional `empirical_complexity` (`dominant_var`, `scale_points`, `generator_args_template`, `extra_vars`, `scale_mode`, `metric`)
- `quality_gates` (`wrong_tle_kill_factor`, optional `wrong_slowdown_kill_ratio`, `require_wrong_solution_kill`, etc.)
- `solutions` (`role`, optional `expected`: `fail` | `pass`, optional `expected_verdict`)
- `case_plan`, and optional SPJ fields (`special_judge`, `stress_comparison`: `exact` | `checker`, `stress_checker_bidirectional`)

## Standalone & Verification CLIs

- Quick workspace status/reconciliation: `autocode-verify <problem_dir> --status` (or `--strict-gates` / `--reconcile`)
- Zero-AI read-only package audit: `autocode-audit <problem_dir> --standalone --layout <auto|autocode|standard_cn|polygon|hydro>` (or `problem_audit(standalone=True)`)
