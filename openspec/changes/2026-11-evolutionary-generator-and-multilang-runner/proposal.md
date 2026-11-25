## Why

AutoCode currently supports only C++ (`*.cpp`) compilation and execution in `problem_build_all`, `solution_build`, and `solution_run`. Although `.autocode/manifest.json` already allows `SolutionEntry.language = "python"` and `AutoCodeManifest.time_limit_multipliers` (for Issue #20), `build_all.py` silently skips non-C++ entries, `solution.py` hardcodes `.cpp` file extensions, and `validate_executable_solution_language()` raises `ValueError` on `"python"`. This blocks authors and benchmarks that supply Python reference, brute-force, or near-AC wrong solutions (`sol.py`, `brute.py`, `wrong_tle.py`) and leaves multi-language OJ time-limit scaling unimplemented.

In parallel, AutoCode's generator pipeline (`generator_build`, `stress_test_run`, and red-team hacking) currently optimizes test generators through manual single-shot iteration rather than systematic evolutionary search. Adding a deterministic, zero-LLM-SDK, ShinkaEvolve-inspired multi-island evolutionary generator optimization harness (`scripts/evolve_generator.py`) paired with a skill (`skills/evolutionary-testdata/SKILL.md`) enables automated discovery of high-kill, high-coverage, diverse corner-case test generators.

## What Changes

- **Multi-Language Runtime Utility (`src/autocode_mcp/utils/lang_runner.py`)**:
  - Add unified compile/syntax-check and execution support for `"cpp"`, `"python"` (`sys.executable` / `python3`), `"pypy"` (`pypy3`), `"rust"` (`rustc`), and `"java"` (`javac` / `java`).
  - Add `infer_language_from_path(path)` and `resolve_time_limit_multiplier(language, manifest=None)` using OJ defaults (`cpp: 1.0`, `rust: 1.0`, `java: 2.0`, `pypy: 2.5`, `python: 5.0`) overridden by `manifest.time_limit_multipliers` while strictly rejecting boolean, NaN, infinite, or non-positive multipliers.
  - Perform Python/PyPy syntax validation via `py_compile` inside an isolated temporary directory with `-B`, `PYTHONDONTWRITEBYTECODE=1`, and `PYTHONPYCACHEPREFIX` so zero `__pycache__` or `.pyc` files are ever created inside `problem_dir`.
  - Integrate `get_active_sandbox_executor` when non-`"none"` and enforce timeout and peak-memory limits cleanly.
- **Workflow Model & MCP Tool Wiring (`workflow/models.py`, `tools/build_all.py`, `tools/solution.py`)**:
  - Update `validate_executable_solution_language` in `src/autocode_mcp/workflow/models.py` to accept `"python"` alongside `"cpp"` while still rejecting unsupported languages (`"rust"`, `"ruby"`, etc.).
  - Update `src/autocode_mcp/tools/build_all.py` to stop skipping `language == "python"` solutions, checking/preparing `.py` solutions cleanly alongside C++ targets without touching source files.
  - Update `src/autocode_mcp/tools/solution.py` (`SolutionBuildTool` and `SolutionRunTool`) to infer/resolve `.py` paths (`solutions/sol.py`, `solutions/brute.py`, `solutions/wrong_*.py`, or explicit `source_path`), validate via `py_compile`, and execute Python solutions with `resolve_time_limit_multiplier` applied cleanly while keeping all C++ paths and all 22 MCP tool signatures untouched.
- **Evolutionary Generator Harness & Skill (`scripts/evolve_generator.py`, `skills/evolutionary-testdata/SKILL.md`)**:
  - Add a self-contained ShinkaEvolve-inspired local evolutionary generator optimization script (`scripts/evolve_generator.py`) featuring multi-island populations (`IslandSpec`), ring migration, a behavioral novelty filter preventing duplicate generator behaviors, a composite fitness function (`killed_wrong_weight * killed_wrongs + branch_coverage_bonus + runtime_pressure + sample_diversity`), deterministic seeded evolution, and a CLI + importable Python API with zero LLM SDK dependencies.
  - Add `skills/evolutionary-testdata/SKILL.md` documenting trigger conditions, workflow integration, CLI/API usage, output contract (`go` / `no_go`), and forbidden behavior aligned with `skills/agent-skill-governance/SKILL.md`.
- **Tests (`tests/test_multilang_and_evolve_smoke.py`, `tests/test_base_hygiene_regressions.py`)**:
  - Update the `validate_executable_solution_language("python")` expectation in `tests/test_base_hygiene_regressions.py` while keeping unsupported language rejection coverage.
  - Add smoke and edge-case tests covering `lang_runner`, real `.py` `SolutionBuildTool` / `ProblemBuildAllTool` / `SolutionRunTool` `AC`/`WA`/`TLE` runs scaled by `time_limit_multipliers["python"]`, zero `__pycache__` pollution, sandbox wrapping, and `scripts/evolve_generator.py` multi-island fitness + novelty filtering.

## Capabilities

### New Capabilities

- `multilang-and-evolutionary`: Multi-language compile/syntax and sandbox-aware execution runner (`cpp`, `python`, `pypy`, `rust`, `java`) with OJ time-limit multipliers, Python runtime support in `problem_build_all` / `solution_build` / `solution_run`, and a deterministic multi-island evolutionary test-generator optimization harness + skill.

### Modified Capabilities

- None outside `multilang-and-evolutionary` (existing `base-contracts` Python language guard is updated cleanly to enable `"python"` runtime execution).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12 authoring workflow stages, hard quality gates, zero-write standalone guarantees, and all 22 MCP tool input schemas (`tools/schemas.py`, `server.py` untouched).
- **Affected Files**:
  - `src/autocode_mcp/utils/lang_runner.py` (new)
  - `src/autocode_mcp/workflow/models.py`
  - `src/autocode_mcp/tools/build_all.py`
  - `src/autocode_mcp/tools/solution.py`
  - `scripts/evolve_generator.py` (new)
  - `skills/evolutionary-testdata/SKILL.md` (new)
  - `tests/test_multilang_and_evolve_smoke.py` (new)
  - `tests/test_base_hygiene_regressions.py`
- **Dependencies**: None added (`pyproject.toml` and `uv.lock` remain unchanged; zero LLM SDK dependencies).
- **Risks**:
  - *`__pycache__` pollution in `problem_dir`*: Running `python -m py_compile` directly writes `__pycache__/*.pyc` next to the `.py` source even when `-B` is passed. Mitigated by compiling into an explicit `TemporaryDirectory` `cfile` with `-B`, `PYTHONDONTWRITEBYTECODE=1`, and `PYTHONPYCACHEPREFIX` set to that temp directory, and passing the same flags during execution.
  - *Accidental deletion of `.py` sources in `build_all.py` / `solution.py`*: Existing pre-build cleanup deletes `binary_path` before rebuilding. Mitigated by never including interpreted `.py` source files in stale-binary removal targets.
  - *Timeout confusion between `timeout` arg and `manifest.time_limit_ms`*: Mitigated by applying `resolve_time_limit_multiplier` consistently across `min(manifest.time_limit_ms / 1000, timeout) * multiplier` for non-brute Python runs and `timeout * multiplier` for `brute`.

## Non-goals

- Do NOT modify any of the 22 MCP tool external signatures (`server.py`, `tools/schemas.py`).
- Do NOT modify `tools/problem_pack.py`, `utils/exporters/*`, `utils/mutation.py`, `utils/sandbox/*`, `tools/audit*.py`, `pyproject.toml`, `uv.lock`, `examples/ci/*`, or other `skills/*` files.
- Do NOT introduce any LLM SDK dependency inside `src/autocode_mcp/` or `scripts/evolve_generator.py`.
- Do NOT enable `"rust"`, `"java"`, or `"pypy"` inside `AutoCodeManifest.solutions[].language` / `validate_executable_solution_language()` (manifest schema remains `Literal["cpp", "python"]` while `lang_runner.py` provides the underlying 5-language abstraction).
