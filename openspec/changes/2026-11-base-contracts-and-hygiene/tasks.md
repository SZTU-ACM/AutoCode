## 1. OpenSpec & Design Alignment

- [x] 1.1 Author `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/base-contracts-and-hygiene/spec.md` under `openspec/changes/2026-11-base-contracts-and-hygiene/` following `openspec/config.yaml`.

## 2. Lazy User/Temp CompileCache (Issue #13)

- [x] 2.1 Refactor `CompileCache` in `src/autocode_mcp/utils/cache.py` to remove `mkdir()` from `__init__` and resolve default paths via `AUTOCODE_CACHE_DIR` -> `XDG_CACHE_HOME/autocode-mcp/compile` -> OS cache dir -> `tempfile.gettempdir()/autocode_mcp_compile_cache`, keeping explicit `cache_dir` overrides and lazy fallback on `OSError`.
- [x] 2.2 Ensure `CompileCache.get()` writes nothing on missing buckets and `CompileCache.set()` lazily creates cache directories only on write; verify `src/autocode_mcp/utils/compiler.py:51` performs zero disk writes at import.
- [x] 2.3 Update `tests/test_cache.py` assertions from eager `.cache/compile` creation to lazy user/temp cache behavior and verify with `uv run pytest tests/test_cache.py -q`.

## 3. Manifest Resource Limits & MLE Attribution (Issue #18)

- [x] 3.1 Update `src/autocode_mcp/utils/resource_limit.py` (`load_problem_config`, `get_resource_limit`) to read `.autocode/manifest.json` (`time_limit_ms`, `memory_limit_mb`) via `load_manifest()` with graceful defaults when missing, and verify `src/autocode_mcp/tools/mixins.py:131`.
- [x] 3.2 Pass `memory_mb=manifest.memory_limit_mb` (fallback `256`) into `run_binary` in `src/autocode_mcp/tools/stress_test.py` (`sol_result` & `brute_result`), `src/autocode_mcp/tools/problem_generate.py`, and `src/autocode_mcp/tools/validation.py` (`_validate_statement_samples` & `_validate_sample_files`).
- [x] 3.3 Update `src/autocode_mcp/utils/compiler.py` (`_run_process` RSS sampler and `_classify_memory_limit_exceeded`) to enforce RSS overflow when `not disable_as_limit` and avoid labeling low-RSS external `SIGKILL` (`-9`/`137`) as `memory_limit_exceeded=True` while preserving `bad_alloc`/`Out of memory`, Windows `STATUS_NO_MEMORY`, and genuine OOM/RSS overflow attribution.
- [x] 3.4 Update `tests/test_resource_limit.py` for `.autocode/manifest.json` and run `uv run pytest tests/test_resource_limit.py tests/test_compiler.py tests/test_tools/test_solution.py -q`.

## 4. Additive Schema Freeze & Python Language Helper (Issue #20)

- [x] 4.1 Add `SandboxConfig`, `SolutionEntry.expected_verdict`, `AutoCodeManifest.{multi_test, scale_token_index, sandbox, time_limit_multipliers}`, and `AuditGates.{fail_on_mutation_survival, min_branch_coverage}` to `src/autocode_mcp/workflow/models.py` with strict validation (rejecting booleans/NaN/control chars) and full backward compatibility.
- [x] 4.2 Add `validate_executable_solution_language(entry)` in `src/autocode_mcp/workflow/models.py` returning `"cpp"` for C++ and raising `ValueError` for unsupported `"python"` execution without modifying `audit_standalone.py`, `build_all.py`, or any of the 22 MCP tool signatures.

## 5. Interactor Docs & PR CI Security Warnings (Issues #17 & #21)

- [x] 5.1 Fix `README.md:196` interactor protocol documentation (`inf`/`ans`, `std::cout` + flush, `ouf`, optional `tout` to `argv[2]`).
- [x] 5.2 Add explicit PR CI security boundary warnings (ephemeral GitHub-hosted runners only, never privileged self-hosted runners, never inject secrets, never `pull_request_target`) in `examples/ci/autocode-problem-audit.yml`, `README.md`, and `TROUBLESHOOTING.md`.
- [x] 5.3 Add doc-sync regression tests in `tests/test_docs_sync.py` and run `uv run pytest tests/test_docs_sync.py -q`.

## 6. Adversarial Regression Suite & Full Verification

- [x] 6.1 Add `tests/test_base_hygiene_regressions.py` covering `autocode-audit . --standalone` zero-write with external `cwd`, lazy `CompileCache` import/init, `resource_limit` manifest passthrough, `memory_mb` passthrough in `stress_test`/`problem_generate`/`validation`, `SIGKILL` vs OOM/RSS attribution, additive schema compatibility on all `examples/*`, and `validate_executable_solution_language`.
- [x] 6.2 Run full quality gates: `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, `uv run pytest tests/ -q`, and verify `git diff --name-only master..HEAD` touches only allowed files.
