---
name: autocode-workflow
description: Use when creating competitive programming problems with AutoCode MCP tools. Enforces the plugin workflow for problem statements, std/brute solutions, validators, generators, stress tests, final data verification, and Polygon packaging.
---

# AutoCode Problem Creation Workflow

AutoCode is a host-independent MCP workflow distributed through Claude Code, Codex, DeepSeek Harness (DSH), and Antigravity (`agy`) plugins. It supports `standard`, `spj`, `interactive`, and `communication` (`run_twice` / `grader_linked` / `multi_process`) problems with server-side gates enforced in `src/autocode_mcp/workflow/enforcement.py` (`scripts/workflow_guard.py` is only the Claude Code hook adapter).

## Status Output Contract

Every workflow checkpoint must use:

- `decision`: `go` / `no_go`
- `blocking_issues`: unmet gates or risks
- `next_actions`: exact MCP calls needed to proceed

## Core Sequence

Non-interactive (`standard` / `spj`):

```text
problem_create -> solution_build(sol) -> solution_build(brute)
  -> solution_analyze / solution_audit_std / solution_audit_brute
  -> validator_build(pos + neg, accuracy >= 0.9) -> generator_build
  -> [checker_build if spj + stress_comparison=="checker"]
  -> stress_test_run(completed_rounds == total_rounds)
  -> [checker_build if spj + exact stress] -> problem_validate
  -> problem_generate_tests -> problem_verify_tests(passed) -> problem_pack_polygon
```

Interactive & Communication:

```text
problem_create(problem_type="interactive"|"communication", protocol_mode=..., oracle_mode=...)
  -> solution_build(sol) -> [solution_build(brute) if oracle_mode=="brute"]
  -> solution_analyze / solution_audit_std [/ solution_audit_brute]
  -> interactor_build (interactive / run_twice / multi_process; optional validator_build)
  -> generator_build -> stress_test_run -> problem_validate
  -> problem_generate_tests -> problem_verify_tests(passed)
  -> problem_audit(mode="full") -> problem_pack_polygon(export_formats=["polygon","hydro","qoj"])
```

## Mandatory Gates

| Gate | Requirement |
|------|-------------|
| Problem setup | `problem_create` creates workspace and `.autocode/manifest.json` |
| Solutions | `solution_build(sol)` before `solution_build(brute)` (required when `oracle_mode == "brute"`) |
| Complexity audit | `solution_analyze`, `solution_audit_std` (and `solution_audit_brute`); re-running post-`generator_build` with unchanged SHA-256 runs dynamic probes non-destructively |
| Validator | Non-protocol: `validator_build` passes both positive (`expected_valid=True`) and negative (`expected_valid=False`) cases with `accuracy >= 0.9` |
| Interactor / Communicator | Interactive & non-`grader_linked` communication: `interactor_build` ready; `grader_linked`: `files/grader.cpp` + `files/problem.h` linked |
| Generator & Stress | `generator_build` with distinct `type=1..4` strategies (when any parameter span $\ge 10^5$, `type=2` MUST use log-uniform sampling by decade rather than bare `rnd.next(1, n_max)`, plus independent multi-parameter regime sampling); `stress_test_run` completes all rounds (`enable_sanitizers=true` runs isolated ASan/UBSan in `.autocode/san_bin/`) |
| Validate & Generate | `problem_validate` passes samples/protocol; `problem_generate_tests` creates >=50% `type=3/4` limit cases |
| Verify & Audit | `problem_verify_tests` passes `duplicate_inputs` (zero exact duplicates), `limit_semantics`, and `wrong_solution_kill`; `problem_audit(mode="full")` passes (`autocode-audit --standalone` for external packages) |
| Packaging | `problem_pack_polygon` after verified tests (`export_formats`: `polygon`, `hydro`, `qoj`) |

## Audit Agents

- `autocode-idea-auditor`: pre-implementation feasibility, constraints, and ambiguity check.
- `autocode-solution-auditor`: post-implementation std/brute complexity and stress parameters.
- `autocode-package-auditor`: pre-packaging verification of statement, tests, wrong kills, and protocol metrics.

## Critical Guardrails

- **macOS / Portable C++20**: avoid `<bits/stdc++.h>` (unsupported by Apple Clang); use standard headers or configure `"compiler": "g++-14"`.
- **Interactive Interactor**: use `registerInteraction(argc, argv)`, read `inf`/`ouf`, and prompt contestants via `std::cout` + `std::flush` / `std::endl` (`tout` is diagnostic only).
- **Red-Teaming & Wrong Solutions**: declare `role="wrong"` solutions with explicit `expected` (`fail`/`pass`) and `expected_verdict` (`WA`/`TLE`/`MLE`/`RE`/`PE`); persist verified hacks under `tests/hack/`.
- **Workspace Inspection**: use `autocode-verify <problem_dir> --status` (or `--strict-gates` / `--reconcile`) and `autocode-audit <problem_dir> --standalone` for read-only audits.

## Forbidden Actions

1. Do not build `brute` before `sol`, or `generator` before `validator`/`interactor` gates.
2. Do not run `stress_test_run` before `sol`, `brute` (when required), and `generator` are ready.
3. Do not infer readiness from file presence alone or ignore structured MCP/hook gate denials.
4. Do not proceed to `problem_generate_tests` before `problem_validate`, or `problem_pack_polygon` before `problem_verify_tests` passes.

## Failure Recovery

On gate failure: report `decision=no_go` and `blocking_issues`, isolate the fault (statement, std, brute, validator, generator, judge, or tests), fix the artifact, and re-run the failed gate plus invalidated downstream gates.

## Additional Resources

- Detailed tool parameters, stress/sanitizer notes, verify signals, and Polygon/Hydro/QOJ export specs: [references/tool-guidance.md](references/tool-guidance.md)
- Interactive/communication protocol specs, `manifest.json` contract, and CLI usage: [references/protocols-and-manifest.md](references/protocols-and-manifest.md)
