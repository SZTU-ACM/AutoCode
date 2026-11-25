## Purpose

Provide adversarial red-team problem auditing (`autocode-redteam-auditor` and `redteam-hacking`), exact expected-verdict enforcement for wrong solutions, isolated host-native C++ code coverage reporting in `problem_verify_tests`, and a self-contained C++17 `jngen.h` generator and anti-hash template header.

## Requirements

### Requirement: Adversarial red-team auditor agent and hacking methodology skill
The system SHALL ship `agents/autocode-redteam-auditor.md` and `skills/redteam-hacking/SKILL.md` with plugin-compatible YAML frontmatter, defining a **Black-box mode** (statement-only independent alternative/brute cross-check to detect statement ambiguities and shared standard-solution/jury-answer errors), a **White-box mode** (targeted Near-AC wrong solutions for fake greeds, missing DP states, `int` overflow, uncleaned multi-test state, `unordered_map` hash collision, single-mod string hash collision, and graph self-loop/multi-edge/isolated-node omissions annotated with `expected_verdict`), and a **Hack feedback loop** validating hack inputs with `files/val` and `solutions/sol` before persisting them to `tests/hack/` alongside `solutions/wrong_*.cpp`.

#### Scenario: Plugin manifest and bundle checks succeed with red-team agent and skill present
- **WHEN** `tests/test_plugin_manifest.py` and `tests/test_plugin_bundle.py` build and validate plugin bundles across `marketplace`, `agy`, and `dsh` targets
- **THEN** `agents/autocode-redteam-auditor.md` and `skills/redteam-hacking/SKILL.md` are included with valid frontmatter and all bundle checks pass

### Requirement: Exact expected verdict enforcement in wrong-solution kill verification
The system SHALL inspect `SolutionEntry.expected_verdict` from `manifest.solutions` inside `_check_wrong_solution_kill`, record `expected_verdict` and `expected_verdict_matched: bool` in every wrong-solution detail entry, fail the check (`passed=False`) with clear diagnostics whenever a non-`None` `expected_verdict` mismatches the observed outcome (including unintended crashes such as `RE` when `TLE` was expected), and preserve existing kill-only semantics when `expected_verdict is None`.

#### Scenario: TLE-expected wrong solution killed by RE fails exact verdict enforcement
- **WHEN** a `role == "wrong"` solution declares `expected_verdict="TLE"` in `.autocode/manifest.json` and exits with `RE` (e.g. due to an undersized array) before reaching a timeout
- **THEN** `details` records `killing_verdict="RE"`, `expected_verdict="TLE"`, and `expected_verdict_matched=False`, and `_check_wrong_solution_kill` returns `passed=False` with mismatch diagnostics

#### Scenario: Matching expected verdict passes while omitted expected_verdict preserves legacy behavior
- **WHEN** a wrong solution declares `expected_verdict="WA"` (or `"TLE"`) and is killed with that exact verdict, or omits `expected_verdict` (`None`) and is killed with any non-`AC` verdict
- **THEN** `expected_verdict_matched` is `True` and `wrong_solution_kill` passes

### Requirement: Isolated solution coverage engine with zero problem-directory writes
The system SHALL provide an isolated coverage engine (`autocode_mcp.utils.coverage`) wired into `ProblemVerifyTestsTool.execute` via optional `verify_types` item `"coverage"` (and automatically executed when `manifest.audit_gates.min_branch_coverage is not None`), compiling `sol.cpp` inside a `TemporaryDirectory` using Clang (`-fprofile-instr-generate -fcoverage-mapping` + `llvm-profdata` + `llvm-cov`) when available or GCC (`--coverage` + `gcov`) fallback without ever writing `.gcda`, `.gcno`, `.profraw`, `.profdata`, or `.gcov` files into `problem_dir`, reporting `line_coverage`, `branch_coverage`, optional `mcdc_coverage`, `uncovered_lines`/`uncovered_line_ranges`, and `uncovered_branches`, enforcing `manifest.audit_gates.min_branch_coverage` when configured, and reporting `status="unavailable"` gracefully when host coverage tools are absent.

#### Scenario: Multi-branch C++ solution reports uncovered lines/branches with zero writes to problem directory
- **WHEN** `ProblemVerifyTestsTool.execute` runs with `verify_types=["coverage"]` on a multi-branch `solutions/sol.cpp` whose `tests/` only exercise a subset of branches
- **THEN** `results["coverage"]` reports accurate `line_coverage` and `branch_coverage` below `1.0`, lists the uncovered line ranges and branches, fails if `min_branch_coverage` exceeds measured `branch_coverage`, and creates zero coverage or temp files inside `problem_dir`

#### Scenario: Missing host coverage toolchain degrades gracefully
- **WHEN** neither Clang (`clang++` + `llvm-profdata` + `llvm-cov`) nor GCC (`g++` + `gcov`) coverage tools are available on the host
- **THEN** coverage returns `status="unavailable"` with clear diagnostics, `passed=True` when `min_branch_coverage is None`, and `passed=False` when `min_branch_coverage` is configured

### Requirement: Self-contained C++17 generator and anti-hash header template
The system SHALL provide `src/autocode_mcp/templates/jngen.h` as a self-contained C++17 header compatible with `#include "testlib.h"`, providing deterministic tree, graph, DAG, permutation, string, `splitmix64` probe, GCC `unordered_map` prime-bucket collision, and Thue-Morse / single-mod polynomial anti-hash helpers that compile cleanly under `g++ -std=c++17`.

#### Scenario: Generator combining testlib.h and jngen.h compiles and emits deterministic structures and anti-hash payloads
- **WHEN** a C++17 program including `"testlib.h"` and `"jngen.h"` is compiled with `-std=c++17` against `templates/` and executed
- **THEN** compilation succeeds cleanly and the binary generates deterministic trees, DAGs, permutations, `unordered_map` prime-bucket collision sequences, and colliding Thue-Morse strings
