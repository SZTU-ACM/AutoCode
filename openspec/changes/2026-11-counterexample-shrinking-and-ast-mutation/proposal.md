## Why

Deterministic adversarial verification and stress debugging in AutoCode currently face four concrete gaps (GitHub Issue #10 and testing-efficiency enhancements):

1. **Unshrunk stress counterexamples (`tools/stress_test.py`)**: When `stress_test_run` discovers a failing input (such as a 40+ element array or multi-line test case), it reports the raw generator output verbatim without minimization. Developers and problem authors must manually isolate which elements or bounds trigger the failure while preserving `val.cpp` validity.
2. **Shallow single-line regex mutation (`utils/mutation.py`, Issue #10)**: `generate_cpp_mutants` currently uses only 8 single-line regex rules (`<=`/`<`, `>=`/`>`, `==`/`!=`, `+ 1`/`+ 0`, `- 1`/`- 0`) capped at 12 mutants, missing competitive-programming semantic bug classes (`long long -> int` overflow narrowing, `% MOD` removal, loop bound `+/-1`, extreme initializer swaps, `priority_queue` comparator flips, multi-test `.clear()` deletion), lacking an equivalent-mutant filter (`if (a <= b) mx = b` / identical arms / dead patterns) and external `mull-runner` integration hook.
3. **Warning-only mutation survival gate (`tools/audit.py`, `tools/audit_standalone.py`)**: Even though `AuditGates.fail_on_mutation_survival` exists in `workflow/models.py`, both managed and standalone `problem_audit` paths ignore it and emit only non-blocking warnings when mutants survive all test cases.
4. **Missing UB differential probing and suite compaction (`tools/stress_test.py`, `utils/test_compactor.py`)**: Undefined behavior (such as signed integer overflow optimized differently between `-O0` and `-O2` or between GCC and Clang) can go unnoticed when `sol` and `brute` both misbehave or under a single compiler configuration, and large generated/hack test suites lack a deterministic minimal-set-cover compactor.

## What Changes

- **Validator-Preserving Counterexample Shrinker (`src/autocode_mcp/utils/shrinker.py`, `src/autocode_mcp/tools/stress_test.py`)**:
  - Implement `shrink_counterexample(...)` applying bounded iterative line-level `ddmin`, count-coupled structural `ddmin`, token-chunk `ddmin`, and integer binary-search reduction toward `0`/`1`/min, accepting reductions only when both the optional compiled validator (`return_code == 0`) and async failure predicate hold.
  - Integrate into `StressTestRunTool` when counterexamples occur, exposing `shrunk_input`, `original_size`, `shrunk_size`, and `shrink_steps` in `ToolResult.data` without modifying existing result keys or MCP schemas.
- **Semantic AST Mutation, Equivalent Filtering, `mull-runner` Hook & Gate Enforcement (`src/autocode_mcp/utils/mutation.py`, `src/autocode_mcp/tools/audit.py`, `src/autocode_mcp/tools/audit_standalone.py`)**:
  - Upgrade `generate_cpp_mutants` with optional `tree-sitter-cpp` CST parsing (dynamically loaded via `importlib`) and a pure-Python fallback adding CP semantic operators: `long long -> int` narrowing, `% MOD` removal, loop bound `+/-1`, init extreme swap (`INT_MAX`/`1e9`/`0`), `priority_queue` comparator flip (`greater<>` <-> default), and multi-test container `.clear()` deletion alongside boundary rules.
  - Add deterministic equivalent-mutant filtering (`if (a <= b) mx = b` / `<` vs `<=` identity, identical branch arms, comments/dead patterns) reporting `equivalent_filtered` cleanly.
  - Add optional `mull-runner` hook in `run_mutation_testing`, reporting `backend="source"` gracefully when `mull-runner` is absent.
  - Respect `manifest.audit_gates.fail_on_mutation_survival` in `audit.py` and `audit_standalone.py`, appending to `blocking_issues` (`decision="no_go"`) when `True` and `survived_mutants > 0`, while preserving warning-only default when `False` and keeping standalone zero-write guarantees and interactive/communication/Polygon handling intact.
- **Multi-Compiler / Multi-Opt UB Differential Probe (`src/autocode_mcp/tools/stress_test.py`)**:
  - Add `run_ub_differential_probe(...)` compiling in an isolated temp directory across `-O0` vs `-O2` (and GCC vs Clang when both exist) plus `-fsanitize=signed-integer-overflow -fsanitize-undefined-trap-on-error` (and optional Linux MSan when supported), reporting output divergences and UB traps across sampled inputs.
- **Additional-Greedy Test Suite Compactor (`src/autocode_mcp/utils/test_compactor.py`)**:
  - Implement `compact_test_suite(coverage_matrix, ...)` computing a minimal covering test subset via Additional-Greedy set cover (with redundancy pruning and optional pinned tests/costs) preserving 100% of covered targets (branches, killed wrong solutions, killed mutants).
- **Regression Tests (`tests/test_shrinker_mutation_compactor.py`)**:
  - Add end-to-end real C++ and unit tests passing without `tree-sitter-cpp`, `mull-runner`, or MSan installed.

## Capabilities

### New Capabilities

- `shrinker-mutation-and-compactor`: Validator-preserving counterexample shrinking (`ddmin` + integer binary search), dual-backend CP semantic AST mutation with deterministic equivalent-mutant filtering, `mull-runner` fallback hook, `fail_on_mutation_survival` audit gate enforcement, multi-compiler/optimization UB differential probing, and Additional-Greedy test suite compaction.

### Modified Capabilities

- None (`openspec/specs/` canonical specifications and all 22 MCP tool external signatures remain unchanged).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves the 12-step authoring workflow, `stress_test_run` prerequisite gating, standalone zero-write guarantee (`--standalone` never mutates target problem directories), and all 22 MCP tool signatures (`stress_test_run`, `problem_audit`).
- **Affected Files**:
  - `src/autocode_mcp/utils/shrinker.py` (new)
  - `src/autocode_mcp/utils/test_compactor.py` (new)
  - `src/autocode_mcp/utils/mutation.py`
  - `src/autocode_mcp/tools/stress_test.py`
  - `src/autocode_mcp/tools/audit.py`
  - `src/autocode_mcp/tools/audit_standalone.py`
  - `tests/test_shrinker_mutation_compactor.py` (new)
- **Dependencies**: Zero new required dependencies (`tree-sitter-cpp` remains an optional extra via dynamic `importlib`; `mull-runner` and MSan are optional host tools with graceful fallbacks).
- **Risks**:
  - *Validator rejection during naive element deletion*: Mitigated by count-coupled structural `ddmin` alongside line/token `ddmin` and strict dual-check (`validator_rc == 0` AND `predicate == True`) with bounded evaluation/time budgets.
  - *Compiler resource contention during UB probing*: Mitigated by isolated temp dirs, bounded input sampling (`max_inputs`), sequential/bounded subprocess execution, and process-tree cleanup on timeout.
  - *Equivalent mutant false removals*: Mitigated by restricting equivalence checks to proven side-effect-free syntactic identities (`a <= b` max/min idioms, identical arms, dead branches/comments).

## Non-goals

- Do NOT modify any of the 22 MCP tool signatures or Pydantic input schemas (`server.py`, `tools/schemas.py`).
- Do NOT touch `workflow/models.py`, `tools/verify_wrong_kill.py`, `tools/test_verify.py`, `utils/coverage.py`, `tools/audit_constraints.py`, `tools/audit_discovery.py`, `utils/judge_runner.py`, `utils/communication_runner.py`, `utils/compiler.py`, `utils/sandbox/*`, `cli/*`, `pyproject.toml`, or `uv.lock`.
- Do NOT introduce `# type: ignore` or `cast(` in `src/`.
