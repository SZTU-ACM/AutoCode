## Context

Track T0 froze `SolutionEntry.language: Literal["cpp", "python"]` and `AutoCodeManifest.time_limit_multipliers` in `src/autocode_mcp/workflow/models.py`, keeping `validate_executable_solution_language()` restricted to `"cpp"` until Python runtime support landed. Meanwhile, `src/autocode_mcp/tools/build_all.py` skipped non-`"cpp"` entries and `src/autocode_mcp/tools/solution.py` assumed `.cpp` sources and native executables.

This change introduces two cohesive layers while leaving `server.py`, `tools/schemas.py`, `tools/audit*.py`, `utils/sandbox/*`, and `utils/exporters/*` untouched:
- **MCP-side (`src/autocode_mcp/`)**: `utils/lang_runner.py` provides multi-language compile/syntax preparation, OJ multiplier resolution, and sandbox-aware subprocess execution; `workflow/models.py` enables `"python"` in `validate_executable_solution_language`; `tools/build_all.py` and `tools/solution.py` wire Python preparation and scaled execution.
- **Script/Skill-side (`scripts/evolve_generator.py`, `skills/evolutionary-testdata/SKILL.md`)**: Provides an offline, deterministic multi-island evolutionary test-generator optimizer with novelty filtering and composite fitness.

## Architecture & Design Decisions

```text
ProblemBuildAllTool / SolutionBuildTool / SolutionRunTool
  │
  ├─ workflow/models.py: validate_executable_solution_language() -> "cpp" | "python"
  │
  └─ utils/lang_runner.py
       ├─ infer_language_from_path(path) -> "cpp" | "python" | "rust" | "java" | None
       ├─ resolve_time_limit_multiplier(lang, manifest)
       │    ├─ defaults: cpp=1.0, rust=1.0, java=2.0, pypy=2.5, python=5.0
       │    └─ overrides: manifest.time_limit_multipliers (rejects bool/NaN/<=0)
       ├─ compile_or_prepare(src, ...)
       │    ├─ "cpp"          -> utils/compiler.py: compile_cpp(...)
       │    ├─ "python"/"pypy"-> isolated TemporaryDirectory py_compile (-B + PYTHONPYCACHEPREFIX)
       │    ├─ "rust"         -> rustc -O --edition 2021
       │    └─ "java"         -> javac -d <class_dir>
       └─ run_source(target, stdin, ...)
            ├─ scales timeout via resolve_time_limit_multiplier when requested
            ├─ wraps argv via sandbox.get_active_sandbox_executor(manifest)
            └─ executes under _run_process(...) with memory/timeout enforcement

scripts/evolve_generator.py (zero LLM SDK deps)
  ├─ IslandSpec[] (multi-island subpopulations + ring migration)
  ├─ Behavioral NoveltyFilter (behavior signature distance & exact-duplicate gate)
  ├─ Composite Fitness: killed_wrong_weight * killed_wrongs
  │                     + branch_coverage_bonus + runtime_pressure + sample_diversity
  └─ Deterministic PRNG loop + CLI (--problem-dir / --demo / --json)
```

### 1. Zero-Write Python Compilation & Execution (`lang_runner.py`)

`python -m py_compile <src>` ignores `-B` when computing `cfile` and writes `<src_dir>/__pycache__/*.pyc`. To uphold AutoCode's zero-pollution guarantee inside `problem_dir`, `lang_runner.py` invokes `[interpreter, "-B", "-c", "import py_compile, sys; py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True)", abs_src, tmp_pyc]` inside a `tempfile.TemporaryDirectory`, with both `PYTHONDONTWRITEBYTECODE=1` and `PYTHONPYCACHEPREFIX=<tmpdir>`. During execution, `[interpreter, "-B", abs_src]` runs with the same environment so importing sibling `.py` helpers inside `solutions/` never writes bytecode into `problem_dir`.

For `"python"` and `"pypy"`, `CompileResult.binary_path` is set to the validated source path (`abs_src`). Both `build_all.py` and `solution.py` skip stale-binary deletion for interpreted `.py` targets so existing `.py` source files are never unlinked prior to syntax checking.

### 2. Time-Limit Multiplier Precedence & Validation

`resolve_time_limit_multiplier(language, manifest)` validates both `language` and any `manifest.time_limit_multipliers` entries (rejecting `bool`, non-numeric values, `NaN`, `inf`, and `<= 0.0` even if `manifest` is a raw dict or unvalidated mock object). In `SolutionRunTool`:
- C++ targets preserve existing `RunToolMixin.run` / `InteractiveRunner` / `CommunicationRunner` behavior unchanged.
- Python targets resolve `multiplier = resolve_time_limit_multiplier("python", manifest)`. For `brute`, effective timeout is `float(timeout) * multiplier`. For `sol`, `wrong`, and `reference`, when `manifest.time_limit_ms > 0`, the manifest base limit is scaled and bounded by `min((manifest.time_limit_ms / 1000.0) * multiplier, float(timeout) * multiplier)` so both manifest-configured problems (using default `timeout=30`) and explicit `timeout=1` overrides respect the multiplier deterministically.

### 3. Multi-Language Support Scope (`lang_runner.py` vs `models.py`)

`lang_runner.py` implements the 5-language abstraction (`cpp`, `python`, `pypy`, `rust`, `java`) with safe toolchain lookup (`shutil.which`) and clean failure diagnostics when optional toolchains (`pypy3`, `rustc`, `javac`/`java`) are absent. `workflow/models.py:validate_executable_solution_language` strictly permits `"cpp"` and `"python"` (matching `SolutionEntry.language: Literal["cpp", "python"]`) and continues rejecting `"rust"`, `"ruby"`, etc.

### 4. ShinkaEvolve-Inspired Generator Harness (`scripts/evolve_generator.py`)

- **Multi-island architecture**: Each `IslandSpec` maintains an independent population biased toward distinct test-generation niches (e.g., corner extremes, adversarial structure, TLE stress, anti-hash/overflow) with periodic ring migration of novel elites.
- **Behavioral novelty filter**: Evaluates a candidate's behavioral descriptor (killed-wrong signature set, coverage/runtime quantiles, structural sample signature) against previously admitted archive descriptors. Candidates below `novelty_threshold` (or exact behavioral duplicates) are filtered out before entering the archive, preventing population collapse onto clones.
- **Deterministic & zero-LLM**: All randomness flows through an explicit `random.Random(seed)`. No external LLM SDKs are imported or required; an optional `--problem-dir` mode invokes local AutoCode artifacts directly while a built-in deterministic evaluation mode enables fast smoke tests and CI verification.

## Risks / Mitigations

- **Sandbox command wrapping vs `RLIMIT_AS` on interpreters/JVMs**: `prlimit --as` can abort `java`, `pypy3`, or sandbox wrapper processes due to large virtual address reservations despite tiny physical RSS. Mitigated by passing `disable_as_limit=True` when wrapping with a non-`none` sandbox or running `java`/`pypy`, while keeping RSS monitoring active in `lang_runner.py` for `"python"` and checking `memory_mb` bounds consistently.
- **Stale C++ binary vs newly added `.py` solution**: If a directory contains both `solutions/sol` and `solutions/sol.py`, `SolutionRunTool` consults `manifest.solutions` first (matching `name`/`role` and `language`), and otherwise resolves existing binary before `.py` fallback; `SolutionBuildTool` resolves `.py` vs `.cpp` unambiguously from `source_path`, `manifest.solutions`, or on-disk files.
- **Test regression on T0 Python guard**: `tests/test_base_hygiene_regressions.py` previously asserted that `validate_executable_solution_language("python")` raised `ValueError`. Updating that single assertion cleanly while adding `"ruby"` / keeping `"rust"` rejection preserves contract coverage.
