---
name: autocode-workflow
description: Use when creating competitive programming problems with AutoCode MCP tools. Enforces the plugin workflow for problem statements, std/brute solutions, validators, generators, stress tests, final data verification, and Polygon packaging.
disable-model-invocation: false
---

# AutoCode Problem Creation Workflow

AutoCode is a Claude Code plugin for competitive programming problem setting. It exists because AI-generated problems often fail in subtle ways:

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
  -> stress_test_run(completed_rounds == total_rounds)
  -> checker_build(if non-exact output)
  -> problem_validate
  -> problem_generate_tests
  -> problem_verify_tests(passed)
  -> problem_pack_polygon
```

Interactive problems:

```text
problem_create
  -> solution_build(sol)
  -> solution_build(brute)
  -> solution_analyze / solution_audit_std / solution_audit_brute
  -> interactor_build
  -> generator_build
  -> stress_test_run
  -> problem_validate
  -> problem_generate_tests
  -> problem_verify_tests(passed)
  -> problem_pack_polygon
```

The authoritative implementation is `scripts/workflow_guard.py`.

## Mandatory Gates

| Gate | Requirement |
|------|-------------|
| Problem setup | `problem_create` must create directory structure and `autocode.json` |
| Standard solution | `solution_build(solution_type="sol")` succeeds |
| Brute solution | `solution_build(solution_type="brute")` succeeds after sol |
| Complexity audit | `solution_analyze`, `solution_audit_std`, and `solution_audit_brute` reviewed |
| Validator | Non-interactive only: `validator_build` returns valid `accuracy >= 0.9` |
| Interactor | Interactive only: `interactor_build` is ready |
| Generator | `generator_build` succeeds after validator/interactor gate |
| Stress | `stress_test_run` completes all rounds |
| Statement validation | `problem_validate` passes samples and sample files |
| Final tests | `problem_generate_tests` creates final tests |
| Test verification | `problem_verify_tests` passes before packaging |
| Packaging | `problem_pack_polygon` only after verified tests |

## Audit Agents

Use these agents when the risk is material:

- `autocode-idea-auditor`: before implementation, especially if the idea has unclear constraints, multiple valid outputs, or interaction.
- `autocode-solution-auditor`: after std/brute exist, before relying on stress results or final generation.
- `autocode-package-auditor`: before `problem_pack_polygon`, especially when wrong solutions, checker, interactor, or custom answer extension are involved.

## Tool Guidance

### `problem_create`

Creates:

- `autocode.json`
- `solutions/`
- `files/`
- `statements/README.md`
- `statements/tutorial.md`
- `tests/`

Do not infer that a problem is ready from file presence alone. Prefer structured tool results and workflow state.

### `solution_build`

Build `sol` before `brute`.

For non-trivial problems, `brute` must be independent enough to serve as an oracle. If it is the same algorithm as `sol`, mark this as a risk and run `solution_audit_brute`.

### `solution_analyze` and audit tools

Use:

- `solution_analyze` to estimate time/space complexity, risk notes, and recommended stress profiles.
- `solution_audit_std` to check std complexity and constraint mismatch.
- `solution_audit_brute` to check whether brute can support stress testing.

Do not accept a claimed complexity without evidence.

### `validator_build`

Non-interactive problems need a validator with evidence. A validator build without effective accuracy is not enough.

Target:

- `accuracy >= 0.9`
- valid inputs include normal, boundary, and maximum cases;
- invalid inputs include near-valid but illegal formats/ranges.

### `interactor_build`

Interactive problems use `interactor_build` instead of `validator_build` and `checker_build`.

Require an explicit interaction protocol in the statement before final packaging.

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

Proceed only when `completed_rounds == total_rounds`.

### `problem_validate`

Validation failure is a release blocker. Do not generate final tests or package until statement samples and sample files pass.

### `problem_generate_tests`

Final tests should include at least half limit-oriented cases (`type=3` + `type=4`) when candidates are available.

For long-running generation:

- warn the user that new chat messages can interrupt MCP calls;
- if interrupted, use `resume=true`;
- use `problem_cleanup_processes` only when cleanup is needed.

### `problem_verify_tests`

Must pass before packaging. Default checks include:

- `file_count`
- `answer_consistency`
- `validator`
- `no_empty`
- `limit_ratio`
- `limit_semantics`

Use `wrong_solution_kill` when wrong solutions are available.

### `problem_pack_polygon`

Only package after `problem_verify_tests(passed=true)`.

## Manifest

Each problem should maintain `autocode.json` as a readable contract. It should describe:

- problem name;
- interactive or non-interactive mode;
- statement/tutorial paths;
- time and memory limits;
- solution roles;
- case plan.

Use `autocode-verify <problem_dir>` for quick structural checks.

## Forbidden Actions

1. Do not build brute before sol.
2. Do not build generator before validator/interactor gate.
3. Do not run stress before sol, brute, and generator are ready.
4. Do not validate/package based on file presence alone.
5. Do not generate final tests before statement validation passes.
6. Do not package before `problem_verify_tests` passes.
7. Do not ignore hook denial; fix the missing prerequisite instead.

## Failure Recovery

When a step fails:

1. Report `decision=no_go`.
2. List `blocking_issues`.
3. Identify whether the fault is in statement, std, brute, validator, generator, checker/interactor, or tests.
4. Fix the failed artifact.
5. Re-run the failed gate and any downstream gate whose assumptions changed.

Never patch around a failed gate by skipping it.
