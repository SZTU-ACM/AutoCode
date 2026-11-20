# AutoCode Problem-Setting Rules

AutoCode is a quality-gated workbench for competitive programming problem authoring and verification. All workflow gates are enforced authoritatively by the `autocode` MCP server.

## Core Principles

- AI proposes candidate artifacts; AutoCode MCP tools verify every stage deterministically.
- Never skip prerequisite gates or assume readiness from file presence alone — rely on structured MCP tool outputs and `.autocode/runtime.json`.
- At every workflow checkpoint, report `decision: go | no_go`, `blocking_issues`, and exact `next_actions`.
- Stop immediately when a gate fails and fix the root cause before progressing.

## Canonical Workflow

1. `problem_create` -> initialize problem workspace and `.autocode/manifest.json` (supports `problem_type`: `standard` | `spj` | `interactive` | `communication`, `protocol_mode`: `interactive_1v1` | `run_twice` | `multi_process` | `grader_linked`, and `oracle_mode`: `brute` | `interactor_only` | `reference_solutions`).
2. `solution_build(solution_type="sol")` -> build standard solution before brute (in `grader_linked` communication mode, links automatically with `files/grader.cpp` + `files/problem.h`; in `multi_process`, supports multiple `participant_solutions`).
3. `solution_build(solution_type="brute")` -> build independent brute oracle (required when `oracle_mode == "brute"`).
4. `solution_analyze`, `solution_audit_std`, `solution_audit_brute` -> review complexity, constraints, and oracle reliability (`solution_audit_brute` required when `oracle_mode == "brute"`).
5. Build judging/validation components:
   - Standard / SPJ: `validator_build(accuracy >= 0.9)` (and `checker_build` before stress when `special_judge=true` + `stress_comparison="checker"`).
   - Interactive: `interactor_build` (`registerInteraction(argc, argv)` writing to contestant via `std::cout` + `std::flush`, reading via `ouf`, optional log via `tout`) with scripted protocol scenarios covering AC, WA, PE, out-of-range, limit exceeded, premature EOF, and extra output (optional `validator_build` allowed; do not call `checker_build`).
   - Communication (`run_twice` / `multi_process`): `interactor_build` with `files/communicator.cpp` (`run_twice` uses `registerTestlibCmd` + `AUTOCODE_COMM_STAGE` env vars with isolated per-pass processes; `multi_process` multiplexes `TO`/`CLOSE`/`FROM`/`EOF` lines across K participants; optional `validator_build` allowed).
   - Communication (`grader_linked`): provide `files/grader.cpp` and `files/problem.h` (optional `validator_build` allowed).
6. `generator_build` -> build testlib generator (`gen <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]`, `type` in `1..4`).
7. `stress_test_run` -> complete all rounds across tiny, random, and edge profiles (interactive via `InteractiveRunner`, communication via `CommunicationRunner` enforcing protocol budgets).
8. `problem_validate` -> verify statement format, samples/transcripts, and `tests/` sample files (including live interactive/communication runs).
9. `problem_generate_tests` -> generate final tests (at least half `type=3/4` extreme/TLE cases by default).
10. `problem_verify_tests` -> verify file integrity, validator, answer consistency, communication metrics, limit semantics, and wrong-solution kills.
11. `problem_audit(mode="full")` -> collect final go/no-go report, empirical complexity evidence, `communication_metrics`, and difficulty signals (use `standalone=True` or `autocode-audit --standalone` for read-only auditing of external non-AutoCode problem packages).
12. `problem_pack_polygon` -> export verified packages via `export_formats` (`polygon` with `<groups>`/`<tests>` subtask XML, `hydro` under `exports/hydro/`, and `qoj` under `exports/qoj/`).

## Statement Format Contract (`statements/README.md`)

Always keep sections in this exact order:
1. Problem Title
2. Time & Memory Limits
3. Background (optional)
4. Problem Description
5. Input Format (must include every variable range and aggregate sum constraint)
6. Output Format (plus Interaction / Communication Protocol section for interactive or communication problems)
7. Samples (numbered ascending; transcript format for interactive or communication problems)
8. Explanation / Notes (place all sample explanations here, never inline beside sample blocks)

## Safety & Environment

- AutoCode compiles and runs C++20 code locally (`g++`, GCC 10+ / Clang C++20); only use in trusted local workspaces.
- Use `uv` for Python execution and package management.
