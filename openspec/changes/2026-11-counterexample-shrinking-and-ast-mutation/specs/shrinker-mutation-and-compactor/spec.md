## Purpose

Provide validator-preserving counterexample minimization, competitive-programming semantic AST mutation with deterministic equivalent-mutant filtering and optional gate blocking, multi-compiler/optimization undefined-behavior differential probing, and Additional-Greedy test suite compaction.

## Requirements

### Requirement: Validator-preserving counterexample shrinking
The system SHALL provide `shrink_counterexample` in `src/autocode_mcp/utils/shrinker.py` that accepts failing `input_data`, an optional compiled validator executable, and an async failure predicate, iteratively applying line-level `ddmin`, count-coupled structural `ddmin`, token-chunk `ddmin`, and integer binary-search reduction toward `0`/`1`/min within a bounded evaluation and timeout budget, accepting reductions only when both the validator exits with `return_code == 0` and the failure predicate returns `True`. `StressTestRunTool` SHALL shrink stress counterexamples and expose `shrunk_input`, `original_size`, `shrunk_size`, and `shrink_steps` in result data without breaking existing keys or MCP schemas.

#### Scenario: Large valid failing input shrinks to minimal valid counterexample
- **WHEN** `shrink_counterexample` or `stress_test_run` receives a 40+ element failing input alongside a compiled `val` binary and failing `sol` vs `brute`
- **THEN** the shrinker reduces the input to a minimal failing counterexample accepted by `val` (`return_code == 0`) that still triggers the failure predicate, and `stress_test_run` reports `shrunk_input`, `original_size`, `shrunk_size`, and `shrink_steps` with `shrunk_size < original_size`

### Requirement: Semantic AST mutation, equivalent-mutant filtering, mull-runner hook, and blocking gate
The system SHALL generate competitive-programming semantic C++ mutations in `src/autocode_mcp/utils/mutation.py` using optional `tree-sitter-cpp` CST parsing when installed and a pure-Python fallback otherwise, covering `long long -> int` narrowing, `% MOD` removal, loop bound `+/-1`, extreme initializer swaps (`INT_MAX`/`1e9`/`0`), `priority_queue` comparator flips (`greater<>` <-> default), and multi-test container `.clear()` deletion alongside boundary rules; SHALL deterministically filter equivalent mutants (including `<` vs `<=` max/min identities such as `if (a <= b) mx = b` / `else mx = a`, identical branch arms, comments, and dead patterns) and report `equivalent_filtered`; SHALL provide an optional `mull-runner` integration hook that gracefully falls back to `backend="source"` when `mull-runner` is absent; and SHALL upgrade `survived_mutants > 0` to `blocking_issues` (`decision="no_go"`) in `ProblemAuditTool` (`audit.py` and `audit_standalone.py`) when `manifest.audit_gates.fail_on_mutation_survival` is `True` while preserving warning-only behavior when `False`.

#### Scenario: Semantic mutants generated, equivalent mutants filtered, and survival gate blocks when enabled
- **WHEN** `generate_cpp_mutants` / `run_mutation_testing` processes C++ solutions containing semantic CP constructs and equivalent `<`/`<=` patterns without `tree-sitter-cpp` or `mull-runner` installed, and `problem_audit` runs with `fail_on_mutation_survival=True` vs `False`
- **THEN** semantic mutants are generated, equivalent mutants are filtered with `equivalent_filtered` reported, `backend="source"` is reported, and `problem_audit` produces `decision="no_go"` with `gate="mutation_testing"` in `blocking_issues` when `fail_on_mutation_survival=True` (and warning-only when `False`)

### Requirement: Multi-compiler and multi-optimization UB differential probe
The system SHALL provide `run_ub_differential_probe` in `src/autocode_mcp/tools/stress_test.py` that compiles the solution in an isolated temporary directory across `-O0` and `-O2` (plus GCC vs Clang when both compiler families exist), `-fsanitize=signed-integer-overflow -fsanitize-undefined-trap-on-error`, and optional Linux Clang MSan when supported, running sampled inputs and reporting output divergences and UB traps cleanly.

#### Scenario: Signed-overflow UB causes -O0 vs -O2 divergence and trap failure
- **WHEN** `run_ub_differential_probe` tests a C++ solution containing signed integer overflow UB whose control flow diverges between `-O0` and `-O2`
- **THEN** the probe detects the `-O0` vs `-O2` output mismatch and/or the `-fsanitize=signed-integer-overflow -fsanitize-undefined-trap-on-error` runtime trap and reports `ub_detected=True` and `passed=False` without writing inside the problem directory

### Requirement: Additional-Greedy test suite compaction
The system SHALL provide `compact_test_suite` in `src/autocode_mcp/utils/test_compactor.py` using Additional-Greedy set cover (with optional redundancy pruning, pinned tests, and test costs) to select a minimal covering test subset preserving 100% of covered targets across branches, killed wrong solutions, and killed mutants.

#### Scenario: Redundant test cases compacted while preserving 100% target coverage
- **WHEN** `compact_test_suite` receives a coverage matrix with redundant and overlapping test cases
- **THEN** it returns a minimal covering subset with `coverage_preserved == 1.0`, `covered_targets == total_targets`, and strictly fewer selected tests than the redundant original suite
