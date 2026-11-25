## Context

AutoCode's stress-testing and test-strength auditing pipelines operate across heterogeneous local and CI toolchains (Linux, macOS Apple Silicon, Windows) where optional native tooling (`tree-sitter-cpp`, `mull-runner`, Clang MSan, dual GCC+Clang compilers) may be absent. Every new capability must run deterministically with zero extra dependencies while upgrading smoothly when optional tools exist.

Naming boundaries remain strict:
- Standalone deterministic algorithms live in `src/autocode_mcp/utils/{shrinker,mutation,test_compactor}.py`.
- Tool-layer orchestration lives in `src/autocode_mcp/tools/{stress_test,audit,audit_standalone}.py`.
- No hook scripts (`scripts/`), CLI modules (`cli/`), or MCP schemas (`tools/schemas.py`, `server.py`) are modified.

## Architecture & Design Decisions

### 1. Validator-Preserving Counterexample Shrinker (`utils/shrinker.py` + `tools/stress_test.py`)

```text
Failing input_data
  │
  ├── Baseline guard: val(input) == 0 and predicate(input) == True
  │
  ▼ Iterative fixpoint (bounded by max_evaluations & timeout_sec, memoized)
  ├── Pass 1: Line-level ddmin (preserves line endings for strict testlib readEoln)
  ├── Pass 2: Count-coupled structural ddmin (updates count token k alongside k-sized groups)
  ├── Pass 3: Per-line & flat token-chunk ddmin (preserves line structure where needed)
  └── Pass 4: Integer binary-search reduction toward 0 -> 1 -> binary search [lo, hi]
        │
        ▼ Accept candidate ONLY if:
          (val_exe is None or run_binary(val_exe, cand).return_code == 0)
          AND (await predicate(cand) is True)
```

- **Tradeoff**: Competitive programming inputs almost always contain a leading count `n` followed by `n` tokens or lines (`40\n a_1 ... a_40\n`). Pure line/token `ddmin` fails `val.cpp` when `n` mismatches the remaining element count. Pairing count-coupled structural `ddmin` with line/token `ddmin` and integer binary search achieves minimal valid counterexamples without encoding problem-specific parsers.
- **Integration**: `StressTestRunTool` preserves existing `input_data` (raw failing preview), `sol_output`, and `brute_output`, and adds `shrunk_input`, `original_size`, `shrunk_size`, and `shrink_steps` to failure payloads without breaking any existing key or MCP schema.

### 2. Semantic AST Mutation, Equivalent Filter & Audit Gate (`utils/mutation.py`, `audit.py`, `audit_standalone.py`)

```text
C++ sol.cpp
  ├── Backend A (optional): tree_sitter + tree_sitter_cpp CST (dynamic importlib)
  └── Backend B (always):   comment/string-aware span + bracket-balanced Python analyzer
          │
          ├── Boundary rules: <=/<, >=/>, ==/!=, +1/+0, -1/-0, </<=, >/>=
          └── CP semantic operators:
                ├── long long -> int narrowing (declarations, using/typedef, casts, vector<long long>)
                ├── % MOD removal (inline `% MOD` & compound `%= MOD` -> no-op preserving control flow)
                ├── loop bound +/-1 (for init/condition bounds)
                ├── init extreme swap (INT_MAX/LLONG_MAX/1e9/1e18/0x3f3f3f3f <-> 0, extreme vars)
                ├── priority_queue comparator flip (greater<T> <-> default max-heap)
                └── multi-test container .clear() deletion -> (void)0;
          │
          ▼ Deterministic Equivalent-Mutant Filter
          ├── Side-effect-free min/max identity: if (a <= b) mx = b / else mx = a / ternary
          ├── Identical if/else branch arms & duplicate return arms
          └── Comments, string literals, #if 0, if (0/false), unreachable statements
          │
          ▼ Execution Backend
          ├── mull-runner hook (when installed & backend in ("auto", "mull"))
          └── Source compilation/execution fallback (always reports backend="source" when absent)
```

- **Tradeoff**: `tree-sitter-cpp` provides exact syntax nodes when installed in `--all-extras`, while the pure-Python span/bracket analyzer guarantees identical operators in default zero-extra-dependency installations.
- **Gate Semantics**: `audit.py` and `audit_standalone.py` inspect `manifest.audit_gates.fail_on_mutation_survival`. When `True` and `survived_mutants > 0`, the gate appends to `blocking_issues` (`decision="no_go"`); when `False` (default), it emits a warning only. `T1B`'s `interactor_bin` / `communicator_exe_override` and Polygon staging remain untouched.

### 3. Multi-Compiler / Multi-Opt UB Differential Probe (`tools/stress_test.py`)

- `run_ub_differential_probe(...)` builds `sol_source` in an isolated `TemporaryDirectory` across:
  1. Primary compiler `-O0` vs `-O2` (via `compile_cpp(..., opt_level=...)`)
  2. Secondary compiler family `-O2` when distinct GCC and Clang compilers coexist on the host
  3. Signed-overflow trap profile (`-O2 -fsanitize=signed-integer-overflow -fsanitize-undefined-trap-on-error`)
  4. Optional Linux Clang MSan (`-O1 -fsanitize=memory`) when `sys.platform.startswith("linux")` and Clang is available
- **Tradeoff**: Using `compile_cpp` for `-O0`/`-O2` and validated isolated subprocess invocation for `-fsanitize=signed-integer-overflow -fsanitize-undefined-trap-on-error` (which is outside `compiler.py`'s frozen `_ALLOWED_EXTRA_FLAGS`) avoids touching forbidden `utils/compiler.py` while strictly preserving `validate_compiler_spec` and process-tree cleanup.

### 4. Additional-Greedy Suite Compactor (`utils/test_compactor.py`)

- `compact_test_suite(coverage_matrix, *, pinned_tests=None, test_costs=None, prune_redundant=True)` maintains the uncovered target universe, greedily picks the test covering the most uncovered targets (with optional cost-aware tie-breaking and deterministic natural ordering), and runs a reverse-order redundancy pass (excluding pinned tests) to guarantee an irredundant cover preserving 100% of covered targets.

## Risks / Mitigations

- **Shrinker runaway / validator hangs**: Mitigated by hard `max_evaluations` and `timeout_sec` caps, per-run `validator_timeout`, and candidate string memoization (`eval_cache`).
- **Compiler contention / stray temp artifacts**: All UB probe builds and mutation runs execute strictly inside `tempfile.TemporaryDirectory` (`use_cache=False`), guaranteeing standalone zero-write compliance.
- **Strict typing (`mypy --strict`) without `# type: ignore` or `cast(`**: Optional `tree_sitter` imports use `importlib.import_module` + `getattr` returning `Any` cleanly, and all new modules use explicit Python 3.10+ annotations.
