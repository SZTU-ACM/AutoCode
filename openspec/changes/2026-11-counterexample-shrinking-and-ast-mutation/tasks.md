## 1. OpenSpec & Architecture Baseline

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/shrinker-mutation-and-compactor/spec.md` under `openspec/changes/2026-11-counterexample-shrinking-and-ast-mutation/`.

## 2. Validator-Preserving Shrinker & UB Differential Probe (`shrinker.py`, `stress_test.py`)

- [x] 2.1 Implement `shrink_counterexample` and `ShrinkResult` in `src/autocode_mcp/utils/shrinker.py` with line-level `ddmin`, count-coupled structural `ddmin`, token-chunk `ddmin`, integer binary search toward `0`/`1`/min, dual `validator_exe` (`rc == 0`) + async `predicate` gating, memoization, and bounded budget/timeout.
- [x] 2.2 Integrate `shrink_counterexample` into `StressTestRunTool` in `src/autocode_mcp/tools/stress_test.py`, exposing `shrunk_input`, `original_size`, `shrunk_size`, and `shrink_steps` without breaking existing keys or MCP schemas.
- [x] 2.3 Implement `run_ub_differential_probe` in `src/autocode_mcp/tools/stress_test.py` comparing `-O0` vs `-O2`, GCC vs Clang (when both exist), `-fsanitize=signed-integer-overflow -fsanitize-undefined-trap-on-error`, and optional Linux MSan across sampled inputs.

## 3. Semantic AST Mutation, Equivalent Filter & Gate Blocking (`mutation.py`, `audit.py`, `audit_standalone.py`)

- [x] 3.1 Upgrade `generate_cpp_mutants` in `src/autocode_mcp/utils/mutation.py` with optional `tree-sitter-cpp` CST backend + pure-Python fallback adding `long long -> int` narrowing, `% MOD` removal, loop bound `+/-1`, init extreme swap (`INT_MAX`/`1e9`/`0`), `priority_queue` comparator flip (`greater<>` <-> default), and multi-test `.clear()` deletion.
- [x] 3.2 Add deterministic equivalent-mutant filtering (`if (a <= b) mx = b`, identical branch arms, comments/dead patterns) reporting `equivalent_filtered` and `ast_backend`, plus optional `mull-runner` hook gracefully reporting `backend="source"` when absent.
- [x] 3.3 Update `src/autocode_mcp/tools/audit.py` and `src/autocode_mcp/tools/audit_standalone.py` to upgrade `survived_mutants > 0` to `blocking_issues` (`decision="no_go"`) when `manifest.audit_gates.fail_on_mutation_survival` is `True` while preserving warning-only default when `False`.

## 4. Additional-Greedy Suite Compactor (`test_compactor.py`)

- [x] 4.1 Implement `compact_test_suite` in `src/autocode_mcp/utils/test_compactor.py` using Additional-Greedy set cover with redundancy pruning, deterministic tie-breaking, and optional `pinned_tests`/`test_costs` preserving 100% of covered targets.

## 5. Verification & Regression Suite

- [x] 5.1 Write comprehensive real C++ and unit tests in `tests/test_shrinker_mutation_compactor.py` (passing without `tree-sitter-cpp`, `mull-runner`, or MSan).
- [x] 5.2 Run `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q`; verify `git diff --name-only` matches the whitelist.
