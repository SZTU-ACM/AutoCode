## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/multilang-and-evolutionary/spec.md` under `openspec/changes/2026-11-evolutionary-generator-and-multilang-runner/` compliant with `openspec/config.yaml`.

## 2. Multi-Language Runner & Model Guard

- [x] 2.1 Implement `src/autocode_mcp/utils/lang_runner.py` supporting `cpp`, `python`, `pypy`, `rust`, and `java`, `infer_language_from_path`, `resolve_time_limit_multiplier` (rejecting bool/NaN/non-positive overrides), isolated zero-`__pycache__` `py_compile`, and sandbox-aware execution via `get_active_sandbox_executor` (`uv run pytest tests/test_multilang_and_evolve_smoke.py -q`).
- [x] 2.2 Update `validate_executable_solution_language` in `src/autocode_mcp/workflow/models.py` to accept `"python"` alongside `"cpp"` while still rejecting unsupported languages (`"rust"`, `"ruby"`).

## 3. Build & Run Tool Wiring (22 MCP Schemas Unchanged)

- [x] 3.1 Update `src/autocode_mcp/tools/build_all.py` to validate/prepare declared and standard `.py` solutions concurrently alongside C++ sources without deleting `.py` files or writing `__pycache__`.
- [x] 3.2 Update `src/autocode_mcp/tools/solution.py` (`SolutionBuildTool`, `SolutionRunTool`) to resolve `.py` solutions (`solutions/sol.py`, `solutions/brute.py`, `solutions/wrong_*.py`, explicit `source_path`), check syntax via `py_compile`, and execute Python solutions using `resolve_time_limit_multiplier` without changing MCP schemas or breaking C++ tests.

## 4. Evolutionary Generator Harness & Skill

- [x] 4.1 Implement `scripts/evolve_generator.py` with `IslandSpec`, multi-island ring migration, behavioral novelty filter, composite fitness (`killed_wrong_weight * killed_wrongs + branch_coverage_bonus + runtime_pressure + sample_diversity`), deterministic evolution loop, and CLI (`--help`, `--demo`, `--problem-dir`, `--json`), with zero LLM SDK dependencies.
- [x] 4.2 Author `skills/evolutionary-testdata/SKILL.md` matching sibling `skills/*/SKILL.md` YAML frontmatter and `skills/agent-skill-governance/SKILL.md` rules (`uv run pytest tests/test_plugin_bundle.py tests/test_plugin_manifest.py -q`).

## 5. Verification & Full Regression Gates

- [x] 5.1 Update `tests/test_base_hygiene_regressions.py` (`validate_executable_solution_language("python")` closure + unsupported language guard) and write `tests/test_multilang_and_evolve_smoke.py`.
- [x] 5.2 Run full validation gates (`uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, `uv run pytest tests/ -q`) and verify `git diff --name-only` matches the whitelist.
