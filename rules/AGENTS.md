# AutoCode Problem-Setting Rules

AutoCode is a deterministic competitive-programming verification engine (`autocode-audit`, `autocode-verify`) and quality-gated AI problem-setting workbench. All workflow gates are enforced authoritatively by the `autocode` MCP server (`stdio` default or optional stateless Streamable HTTP `--transport http`, with MCP 2026 `ToolAnnotations`, `outputSchema`, bounded async MCP Tasks, and 3 zero-CDN `ui://autocode/*` MCP Apps).

## Core Principles

- AI proposes candidate artifacts; AutoCode MCP tools verify every stage deterministically (with optional `autocode-redteam-auditor` adversarial review before final sign-off).
- Never skip prerequisite gates or assume readiness from file presence alone — rely on structured MCP tool outputs and `.autocode/runtime.json`.
- At every workflow checkpoint, report `decision: go | no_go`, `blocking_issues`, and exact `next_actions`.
- Stop immediately when a gate fails and fix the root cause before progressing.

## Canonical Workflow

1. `problem_create` -> initialize problem workspace and `.autocode/manifest.json` (supports `problem_type`: `standard` | `spj` | `interactive` | `communication`, `protocol_mode`: `interactive_1v1` | `run_twice` | `multi_process` | `grader_linked`, and `oracle_mode`: `brute` | `interactor_only` | `reference_solutions`).
2. `solution_build(solution_type="sol")` -> build standard solution before brute (supports `language`: `cpp` | `python` | `pypy` | `rust` | `java` with per-language multipliers; in `grader_linked` communication mode, links automatically with `files/grader.cpp` + `files/problem.h`; in `multi_process`, supports multiple `participant_solutions`).
3. `solution_build(solution_type="brute")` -> build independent brute oracle (required when `oracle_mode == "brute"`).
4. `solution_analyze`, `solution_audit_std`, `solution_audit_brute` -> review complexity (combining AST/call-graph, empirical curves, and hardware `perf` / `cachegrind` counters), constraints, and oracle reliability (`solution_audit_brute` required when `oracle_mode == "brute"`).
5. Build judging/validation components:
   - Standard / SPJ: `validator_build(accuracy >= 0.9)` (and `checker_build` before stress when `special_judge=true` + `stress_comparison="checker"`). Optionally synthesize/verify `constraints.h` via Z3 SMT checks (`--extra formal`).
   - Interactive: `interactor_build` (`registerInteraction(argc, argv)` writing to contestant via `std::cout` + `std::flush`, reading via `ouf`, optional log via `tout`) with scripted `interaction_scenarios` covering AC, WA, PE, out-of-range, limit exceeded, premature EOF, and extra output (optional `validator_build` allowed; do not call `checker_build`).
   - Communication (`run_twice` / `multi_process`): `interactor_build` with `files/communicator.cpp` (`run_twice` uses `registerTestlibCmd` + `AUTOCODE_COMM_STAGE` env vars with isolated per-pass processes; `multi_process` multiplexes `TO`/`CLOSE`/`FROM`/`EOF` lines across K participants; optional `validator_build` allowed).
   - Communication (`grader_linked`): provide `files/grader.cpp` and `files/problem.h` (optional `validator_build` allowed).
6. `generator_build` -> build testlib / `jngen.h` generator (`gen <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]`, `type` in `1..4` or custom types like `"5"`/`"hack"`).
7. `stress_test_run` -> complete all rounds across tiny, random, and edge profiles (interactive via `InteractiveRunner`, communication via `CommunicationRunner` enforcing protocol budgets; supports `CounterexampleShrinker` minimal failing input shrinking). Pass `enable_sanitizers=True` to run isolated ASan/UBSan/MSan/TSan probes under `.autocode/san_bin/` with negative-control probing (automatic `-static-libasan -static-libubsan` fallback on Linux containers).
8. `problem_validate` -> verify statement format, samples/transcripts, and `tests/` sample files (including live interactive/communication runs).
9. `problem_generate_tests` -> generate final tests (at least half `type=3/4` extreme/TLE cases by default; preserves `locked: true` and `tests/hack/*.in`; supports evolutionary generation via `scripts/evolve_generator.py`).
10. `problem_verify_tests` -> verify file integrity, validator, answer consistency, communication metrics, `boundary_coverage`, `gcov` branch coverage, `test_compactor` deduplication, limit semantics, and wrong-solution kills (`wrong_ms / std_ms` ratios and exact `expected_verdict` `AC|WA|TLE|MLE|RE|PE`; author 2–3 near-correct single-point mutant wrong solutions alongside standard wrong solutions). Partial re-runs merge `verify_signals` by dataset/source fingerprint.
11. `problem_audit(mode="full")` -> collect final go/no-go report, empirical complexity evidence, `statement_consistency`, `communication_metrics`, originality checks, `host_performance_bias`, and difficulty signals. For external non-AutoCode packages (including Polygon `problem.xml` and extensionless `01`/`01.a`), run read-only `problem_audit(standalone=True)` or CLI `autocode-audit <dir> --standalone --layout auto|autocode|standard_cn|polygon|hydro --sandbox auto|none|bwrap|nsjail|isolate|apple_container --mutation-check --strict --format json|markdown|summary`.
12. `problem_pack_polygon` -> always prefer `time_limit_ms` (avoid passing milliseconds to deprecated `time_limit` seconds); export verified packages via `export_formats` (`polygon` with `<groups>`/`<tests>` subtask XML, `hydro` under `exports/hydro/`, `qoj` under `exports/qoj/`, `icpc`/`kattis`/`icpc_kattis`, and IOI `cms`) and `export_data_mode` (`"hardlink"` | `"copy"` | `"zip"`), with automatic export read-back verification. Utility CLIs also support `python -m autocode_mcp.cli.polygon_sync` (REST API) and `python -m autocode_mcp.cli.render_pdf` (Typst PDF).

## Granular Caching & Cross-Platform State

- Workflow gates cache per-file SHA-256 digests: updating `sol.cpp` invalidates only `sol`-side audits without discarding unchanged `brute` audits; pure re-compiles after moving across platforms preserve analysis/stress/testdata progress and require only binary re-verification.
- Inspect or reconcile workspace state with `autocode-verify <dir> --status --strict-gates` or `autocode-verify <dir> --reconcile`.

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

- AutoCode compiles and runs C++20 code locally (`g++`, GCC 10+ / Clang C++20; avoid `<bits/stdc++.h>` on Apple Clang or configure `manifest.compiler`); only use in trusted local workspaces or isolated `--sandbox` backends (`bwrap`, `nsjail`, `isolate`, `apple_container`).
- Use `uv` for Python execution and package management (`uv sync --extra ast --extra formal` or `uv sync --all-extras` for optional `tree-sitter-cpp` AST mutation and `z3-solver` SMT constraints).
