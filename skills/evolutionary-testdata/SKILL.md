---
name: evolutionary-testdata
description: "Optimize test generators using ShinkaEvolve-inspired multi-island evolution, behavioral novelty filtering, and composite wrong-kill/coverage/runtime fitness via scripts/evolve_generator.py."
---

# Evolutionary Test Data Optimization Skill

## Purpose

Use this skill to systematically evolve competitive programming test generators and generator parameter suites (`case_plan`, `files/gen.cpp`, and `tests/hack/`) until weak wrong solutions (`role="wrong"`) are killed, structural branch coverage improves, and redundant generator behaviors are filtered out.

## Trigger Conditions

Activate this skill when any of the following conditions occur:

- `problem_verify_tests` reports surviving `role="wrong"` solutions (`wrong_solution_kill` failed) or `expected_verdict` mismatch.
- `problem_verify_tests(verify_types=["coverage"])` reveals uncovered decision branches in `solutions/sol.cpp` (`branch_coverage < min_branch_coverage`).
- `limit_semantics` or `duplicate_inputs` warns that `type=3` and `type=4` test cases overlap or produce low structural diversity.
- Manual ad-hoc parameter tuning fails to expose tricky corner cases (`star`, `broom`, `anti_sort`, `anti_hash`, `overflow_32`, `multi_reset`).

## Step-by-Step Execution Guidance

1. **Ensure baseline artifacts are ready**:
   - Confirm `files/gen`, `files/val`, `solutions/sol` (C++ or Python `.py`), and target `solutions/wrong_*` entries exist and pass `problem_build_all` / `solution_build`.
   - Never bypass `files/val` constraints when evaluating evolved candidates.

2. **Configure multi-island evolutionary search (`scripts/evolve_generator.py`)**:
   - Configure distinct `IslandSpec` subpopulations targeting complementary failure modes:
     - `corner_extremes`: `n_min=1`, minimal/maximal boundaries, equal elements, zero-edge inputs.
     - `tle_stress_adversarial`: `type=4`, full `n_max` / `t_max`, worst-case graph/sequence topologies (`star`, `broom`, `chain`, `anti_sort`, `dense_dag`).
     - `anti_greedy_structure`: tricky small/medium counterexamples (`bipartite`, `cycle`, `negative_trap`, `tie_break`).
     - `hash_and_overflow`: `anti_hash`, `overflow_32`, `multi_reset`, `prime_bucket`.
   - Enforce `NoveltyFilter` (`--novelty-threshold`) so behaviorally duplicate candidates are rejected before entering the archive.
   - Evaluate candidates with the composite fitness objective:
     `killed_wrong_weight * killed_wrongs + branch_coverage_bonus + runtime_pressure + sample_diversity`
     (penalizing any candidate that fails `validator` or breaks `sol`).

3. **Run local deterministic evolution**:
   - Execute `uv run python scripts/evolve_generator.py --problem-dir <dir> --generations 5 --islands 3 --seed 42 --json` (or `--demo` for standalone harness verification).
   - Inspect `best_candidate`, `island_bests`, `archive`, `accepted_novel`, and `rejected_duplicates` in the JSON output.

4. **Materialize and verify winning test cases**:
   - Generate concrete `.in` cases from the top archive `GeneratorCandidate` configurations using `files/gen`.
   - Validate every generated `.in` strictly against `files/val` (`exit_code == 0`) and compute `.ans` via `solutions/sol` (or verify via `files/checker` / `files/interactor`).
   - Persist verified high-kill cases into `tests/hack/` (or update `case_plan` in `.autocode/manifest.json`) and re-run `problem_verify_tests` to confirm `wrong_solution_kill`, `limit_ratio`, `limit_semantics`, `duplicate_inputs`, and `coverage` all pass.

## Required Output Format

Return a structured summary containing:

- `decision`: `go` | `no_go`
- `blocking_issues`: list of unresolved failures (`validator` failure, surviving wrong solution, coverage deficit, or `sol` failure)
- `evolution_summary`:
  - `generations`, `islands`, `total_evaluated`, `accepted_novel`, `rejected_duplicates`
  - `best_candidate`: `candidate_id`, `type_param`, `n_min`, `n_max`, `extra_args`, `total_fitness`
  - `killed_wrong_names`: list of killed `role="wrong"` solutions
- `next_actions`: concrete follow-up steps (e.g., persist `tests/hack/` cases, re-run `problem_verify_tests`, or run `problem_pack_polygon`)

## Decision Rules

- **`go`**: All required `files/val` checks pass, evolved candidates achieve `validator_passed=true` and `sol_passed=true`, target `role="wrong"` solutions are killed (`wrong_solution_kill` passes), and `problem_verify_tests` confirms `limit_ratio`, `limit_semantics`, and `duplicate_inputs` pass.
- **`no_go`**: Any generated candidate violates `files/val`, `solutions/sol` fails or disagrees with the oracle, target wrong solutions survive after evolution, or `problem_verify_tests` fails any quality gate.

## Forbidden Behavior

- Do not bypass `files/val` or keep invalid test inputs even if they kill a wrong solution.
- Do not introduce external LLM SDK dependencies into `scripts/evolve_generator.py` or `src/autocode_mcp/`.
- Do not write temporary scratch files, `.pyc` caches, or unrequested artifacts inside `problem_dir`.
- Do not approve test data based solely on synthetic `--demo` scores without running `problem_verify_tests` on real problem artifacts.
