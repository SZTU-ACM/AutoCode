## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/redteam-verdict-and-coverage/spec.md` under `openspec/changes/2026-11-redteam-auditor-and-exact-verdict/` following `openspec/config.yaml`.

## 2. Red-Team Auditor Agent & Skill

- [x] 2.1 Create `agents/autocode-redteam-auditor.md` with valid frontmatter (`name`, `description`, `skills`, `model`) matching `agents/*.md` conventions and defining black-box blind cross-check, white-box Near-AC synthesis with `expected_verdict`, and `val`-verified hack feedback loop.
- [x] 2.2 Create `skills/redteam-hacking/SKILL.md` documenting black-box vs white-box methodology, Near-AC wrong-solution taxonomy (fake greedy, missing DP state, `int` overflow, uncleaned multi-test, `unordered_map` collision, single-mod hash collision, graph self-loop/multi-edge/isolated node, suboptimal TLE/MLE), `jngen.h` anti-hash helpers, and CI persistence into `tests/hack/` + `solutions/wrong_*.cpp`.

## 3. Exact Verdict Enforcement (`verify_wrong_kill.py`)

- [x] 3.1 Update `src/autocode_mcp/tools/verify_wrong_kill.py` to read `SolutionEntry.expected_verdict`, record `expected_verdict` and `expected_verdict_matched: bool` in every `details` entry, fail (`passed=False`) with clear diagnostics on verdict mismatch (such as `RE` when `TLE` was expected), and preserve 100% existing behavior when `expected_verdict is None`.

## 4. Isolated Coverage Engine (`coverage.py`, `test_verify.py`)

- [x] 4.1 Implement `src/autocode_mcp/utils/coverage.py` compiling `sol.cpp` in a `TemporaryDirectory` using Clang (`-fprofile-instr-generate -fcoverage-mapping`, optional `-fcoverage-mcdc`, `llvm-profdata`, `llvm-cov`) or GCC (`--coverage`, `gcov`) fallback with zero writes to `problem_dir`, computing `line_coverage`, `branch_coverage`, `mcdc_coverage`, `uncovered_lines`/`uncovered_line_ranges`, `uncovered_branches`, checking `min_branch_coverage`, and returning `status="unavailable"` when tools are absent.
- [x] 4.2 Wire optional `"coverage"` check (and automatic run when `manifest.audit_gates.min_branch_coverage` is configured) into `src/autocode_mcp/tools/test_verify.py` (`execute`, `_build_quality_signals`, `executed_signals`) while keeping `verify_types=None` fast when `min_branch_coverage is None` and leaving `tools/schemas.py` unchanged.

## 5. `jngen.h` Generator & Anti-Hash Header

- [x] 5.1 Implement `src/autocode_mcp/templates/jngen.h` as a self-contained C++17 header compatible with `#include "testlib.h"`, providing deterministic tree/graph/DAG/permutation/string helpers plus `splitmix64` probe, GCC `unordered_map` prime-bucket collision, and Thue-Morse / single-mod anti-hash helpers.

## 6. Verification & Regression Tests

- [x] 6.1 Add `tests/test_exact_verdict_and_coverage.py` covering exact verdict (`TLE` vs `RE` mismatch, matching verdict, `None` compatibility), real C++ coverage runs (uncovered line/branch detection, zero writes to `problem_dir`, `min_branch_coverage` pass/fail, unavailable fallback), and `g++ -std=c++17` compilation & execution of `#include "jngen.h"` with `testlib.h`.
- [x] 6.2 Run `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q` and confirm all checks exit 0.
