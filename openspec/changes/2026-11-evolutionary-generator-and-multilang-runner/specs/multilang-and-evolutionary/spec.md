## Purpose

Define the multi-language compile/syntax and sandbox-aware execution runner (`cpp`, `python`, `pypy`, `rust`, `java`) with OJ time-limit multipliers, Python solution build/run support across `problem_build_all`, `solution_build`, and `solution_run`, and the local multi-island evolutionary generator optimization harness and skill.

## Requirements

### Requirement: Multi-language compile, syntax check, OJ multiplier resolution, and sandbox-aware execution
The system SHALL provide `src/autocode_mcp/utils/lang_runner.py` supporting `"cpp"`, `"python"`, `"pypy"`, `"rust"`, and `"java"` with `infer_language_from_path(path)` and `resolve_time_limit_multiplier(language, manifest=None)` using OJ defaults (`cpp: 1.0`, `rust: 1.0`, `java: 2.0`, `pypy: 2.5`, `python: 5.0`) overridden by `manifest.time_limit_multipliers` while rejecting boolean, NaN, infinite, and non-positive multipliers. Python and PyPy syntax checking SHALL execute `py_compile` inside an isolated temporary directory using `-B`, `PYTHONDONTWRITEBYTECODE=1`, and `PYTHONPYCACHEPREFIX` so zero `__pycache__` or `.pyc` files are written inside `problem_dir`, and execution SHALL consult `get_active_sandbox_executor` when non-`"none"` while enforcing timeout and memory limits.

#### Scenario: Python syntax check and execution leave zero __pycache__ inside problem_dir
- **WHEN** a `.py` solution inside `problem_dir` is syntax-checked and executed via `lang_runner`
- **THEN** syntax errors are rejected cleanly and successful runs produce expected output with zero `__pycache__` directories or `.pyc` files created inside `problem_dir`

#### Scenario: Time-limit multiplier resolution applies defaults, manifest overrides, and rejects invalid values
- **WHEN** `resolve_time_limit_multiplier` is called for `"python"` without an override vs with `manifest.time_limit_multipliers={"python": 3.5}` vs with `{"python": True}` or `{"python": 0.0}` or `{"python": float("nan")}`
- **THEN** it returns `5.0` by default, `3.5` for the valid override, and raises `ValueError` for invalid boolean/non-finite/non-positive overrides

### Requirement: Python solution build and run wiring without MCP schema changes
The system SHALL allow `"python"` alongside `"cpp"` in `validate_executable_solution_language()` (`src/autocode_mcp/workflow/models.py`) while rejecting unsupported languages (`"rust"`, `"ruby"`), SHALL validate declared and standard `.py` solutions in `ProblemBuildAllTool` (`src/autocode_mcp/tools/build_all.py`) without deleting `.py` source files, and SHALL enable `SolutionBuildTool` and `SolutionRunTool` (`src/autocode_mcp/tools/solution.py`) to build/check and run `.py` solutions (`AC`, `WA`, and `TLE` scaled by `resolve_time_limit_multiplier`) while keeping all 22 MCP tool input schemas and C++ behavior intact.

#### Scenario: Real Python solutions build and run with AC, WA, and scaled TLE verdicts
- **WHEN** `solutions/sol.py`, `solutions/brute.py`, and `solutions/wrong_tle.py` are built via `SolutionBuildTool` / `ProblemBuildAllTool` and executed via `SolutionRunTool` with `manifest.time_limit_multipliers["python"]`
- **THEN** `AC` and `WA` outputs match expected stdout, runs slower than base C++ `time_limit_ms` but within the scaled Python limit pass (`AC`), runs exceeding the scaled Python limit fail with timeout (`TLE`), and zero `__pycache__` directories appear in `problem_dir`

#### Scenario: Unsupported executable solution languages remain rejected
- **WHEN** `validate_executable_solution_language` is called with `"python"` vs `"rust"` or `"ruby"`
- **THEN** `"python"` returns `"python"` and `"rust"` / `"ruby"` raise `ValueError`

### Requirement: Local ShinkaEvolve-inspired evolutionary test generator harness and skill
The system SHALL provide `scripts/evolve_generator.py` implementing a deterministic multi-island evolutionary loop (`IslandSpec`, ring migration), a behavioral novelty filter rejecting duplicate generator behaviors, a composite fitness function (`killed_wrong_weight * killed_wrongs + branch_coverage_bonus + runtime_pressure + sample_diversity`), and both a Python API and `--help` CLI with zero LLM SDK dependencies, paired with `skills/evolutionary-testdata/SKILL.md` conforming to `skills/agent-skill-governance/SKILL.md` frontmatter and output contracts.

#### Scenario: Multi-island evolutionary loop improves fitness while filtering duplicate behaviors
- **WHEN** `scripts/evolve_generator.py` runs across multiple `IslandSpec` islands with duplicate and novel behavioral candidates under a fixed seed
- **THEN** behaviorally duplicate candidates are filtered out by the novelty gate, higher composite fitness candidates are retained and migrated across islands deterministically, and `scripts/evolve_generator.py --help` exits cleanly with status `0`
