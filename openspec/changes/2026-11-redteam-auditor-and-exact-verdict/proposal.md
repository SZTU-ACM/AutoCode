## Why

AutoCode's current data-strength verification suffers from three gaps identified in Issue #10:

1. **Lack of adversarial red-team verification**: Pure single-point regex mutation (`utils/mutation.py`, 8 syntactic operators with a budget of 6–12) either fails trivially on sample 1 or triggers equivalent-mutant noise, while missing true competitive-programming Near-AC wrong solutions (fake greeds, missing DP states, 32-bit overflow, uncleaned multi-test state, `unordered_map` hash collisions, single-mod string hash collisions, graph self-loop/multi-edge/isolated-node omissions) and failing to catch "std + jury answers both wrong" or statement ambiguities via independent black-box cross-checking.
2. **Coarse wrong-solution verdict tracking (`verify_wrong_kill.py`)**: `SolutionEntry.expected_verdict` (`"AC" | "WA" | "TLE" | "MLE" | "RE" | "PE"`) was added to `workflow/models.py` during the Wave 1 schema freeze, but `_check_wrong_solution_kill` still ignores `expected_verdict`. As a result, a suboptimal solution declared as `expected_verdict="TLE"` that actually crashes with `"RE"` due to an undersized array silently passes `wrong_solution_kill` (`killed=True`, `passed=True`).
3. **No isolated solution coverage signal (`utils/coverage.py`, `tools/test_verify.py`)**: Problem authors have no deterministic way to inspect which lines, branches, or MC/DC conditions of `solutions/sol.cpp` remain unexecuted by `tests/` without polluting `problem_dir` with `.gcda`/`.gcno`/`.profraw`/`.profdata` artifacts, and `AuditGates.min_branch_coverage` is not yet enforced.
4. **Missing deterministic generator & anti-hash toolkit (`templates/jngen.h`)**: Authors and red-team agents lack a clean, `testlib.h`-compatible C++17 header providing deterministic tree/graph/DAG/permutation/string helpers and anti-hash collision generators.

## What Changes

- **Red-Team Auditor Agent & Skill (`agents/autocode-redteam-auditor.md`, `skills/redteam-hacking/SKILL.md`)**:
  - Define **Black-box mode** (statement-only independent alternative/brute cross-check to detect statement ambiguities and "std + answers both wrong"), **White-box mode** (targeted Near-AC wrong solutions annotated with `expected_verdict`), and **Hack feedback loop** (`val` + `sol`-verified hack inputs saved to `tests/hack/` alongside persistent `solutions/wrong_*.cpp` for zero-AI CI).
- **Exact Verdict Enforcement (`src/autocode_mcp/tools/verify_wrong_kill.py`)**:
  - Read `SolutionEntry.expected_verdict` from `manifest.solutions`, record `expected_verdict` and `expected_verdict_matched: bool` in each `details` entry, and fail (`passed=False`) with clear diagnostics whenever a non-`None` `expected_verdict` mismatches the observed outcome (including unintended `RE` when `TLE` was expected), while preserving 100% existing behavior when `expected_verdict is None`.
- **Isolated Coverage Engine (`src/autocode_mcp/utils/coverage.py`, `src/autocode_mcp/tools/test_verify.py`)**:
  - Add an isolated coverage runner compiling `sol.cpp` inside a `TemporaryDirectory` using Clang (`-fprofile-instr-generate -fcoverage-mapping` + `llvm-profdata` + `llvm-cov export` / `show`, plus optional `-fcoverage-mcdc`) or GCC (`--coverage` + `gcov`) fallback, ensuring zero coverage artifacts ever touch `problem_dir`.
  - Wire optional `verify_types` item `"coverage"` (and automatic invocation when `audit_gates.min_branch_coverage` is configured) into `ProblemVerifyTestsTool.execute`, reporting `line_coverage`, `branch_coverage`, `mcdc_coverage` (when supported), `uncovered_lines`/`uncovered_line_ranges`, and `uncovered_branches`, and degrading cleanly to `status="unavailable"` when host coverage tools are absent.
- **Generator & Anti-Hash C++17 Header (`src/autocode_mcp/templates/jngen.h`)**:
  - Ship a self-contained C++17 header compatible with `#include "testlib.h"` offering deterministic tree, graph, DAG, permutation, string, `splitmix64` probe, GCC `unordered_map` prime-bucket collision, and Thue-Morse / single-mod polynomial hash collision helpers.
- **Regression Tests (`tests/test_exact_verdict_and_coverage.py`)**:
  - Verify exact verdict enforcement (`TLE` vs `RE` mismatch caught, matching verdicts pass, `None` compatibility preserved), real isolated C++ coverage runs (multi-branch uncovered lines + zero writes to `problem_dir` + `min_branch_coverage` gate + unavailable fallback), and `g++ -std=c++17` compilation & execution of `#include "jngen.h"` with `testlib.h`.

## Capabilities

### New Capabilities

- `redteam-verdict-and-coverage`: Adversarial red-team agent/skill protocol, exact `expected_verdict` enforcement in `wrong_solution_kill`, isolated Clang/GCC coverage analysis wired into `problem_verify_tests`, and C++17 `jngen.h` generator/anti-hash header template.

### Modified Capabilities

- None (existing 22 MCP tool input schemas and `workflow/models.py` remain unchanged).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves the 12-stage `CLAUDE.md` pipeline, all 22 MCP tool input schemas (`tools/schemas.py`, `server.py`), and zero-write guarantees against target problem directories during isolated audits and coverage runs.
- **Affected Files**:
  - `agents/autocode-redteam-auditor.md`
  - `skills/redteam-hacking/SKILL.md`
  - `src/autocode_mcp/utils/coverage.py`
  - `src/autocode_mcp/templates/jngen.h`
  - `src/autocode_mcp/tools/verify_wrong_kill.py`
  - `src/autocode_mcp/tools/test_verify.py`
  - `tests/test_exact_verdict_and_coverage.py`
- **Risks**:
  - *Toolchain differences across macOS/Linux/Windows*: Apple Clang on macOS does not place `llvm-cov`/`llvm-profdata` on `$PATH` (`xcrun --find` is needed) and lacks `<bits/stdc++.h>`, while Linux hosts may provide only GNU `g++`/`gcov` or versioned binaries (`g++-14`/`gcov-14`). Mitigated by probing `xcrun` and versioned binaries, falling back cleanly from Clang to GCC, and returning `status="unavailable"` when neither toolchain is present.
  - *Coverage runtime memory/time overhead*: Instrumented `-O0` binaries use extra memory/CPU for profile counters. Mitigated by running inside a dedicated `TemporaryDirectory` with safe headroom and compiling copied source files so `.gcda`/`.gcno`/`.profraw` never touch `problem_dir`.

## Non-goals

- Do NOT modify `src/autocode_mcp/workflow/models.py`, `tools/schemas.py`, `server.py`, `utils/mutation.py`, `tools/audit*.py`, `tools/stress_test.py`, `utils/judge_runner.py`, `utils/communication_runner.py`, `utils/sandbox/*`, `cli/*`, or `pyproject.toml`.
- Do NOT break any of the 22 MCP tool signatures or `tests/test_plugin_manifest.py` / `tests/test_plugin_bundle.py` / `tests/test_docs_sync.py`.
- Do NOT run `coverage` by default when `verify_types=None` and `audit_gates.min_branch_coverage is None`, keeping standard `problem_verify_tests` fast and non-breaking.
