## Context

Track T0 establishes the clean runtime baseline, truthful verdict/resource contracts, documentation alignment, and additive manifest schemas required before Wave 1-2 feature tracks build sandbox execution, multi-test complexity fitting, expected-verdict checks, and Python solution support.

Naming boundaries remain strictly separated:
- MCP server runtime, utilities, and manifest contracts live under `src/autocode_mcp/...`.
- Client hook helpers live under `scripts/...` and are untouched by this change.

## Architecture & Design Decisions

### 1. Zero-Write Lazy `CompileCache` (`src/autocode_mcp/utils/cache.py`, `utils/compiler.py`)

```text
CompileCache(cache_dir=None)
  ├── __init__: store explicit path OR None (NO mkdir(), NO disk I/O)
  │
  ├── cache_dir property (when explicit is None):
  │     AUTOCODE_CACHE_DIR
  │       └─> XDG_CACHE_HOME/autocode-mcp/compile
  │             └─> OS cache dir (LOCALAPPDATA / ~/Library/Caches / ~/.cache)/autocode-mcp/compile
  │                   └─> tempfile.gettempdir()/autocode_mcp_compile_cache
  │
  ├── get(): check bucket_dir.is_dir() first; if absent -> return None (0 writes!)
  └── set(): lazily mkdir cache_dir/bucket_dir; on OSError (if default) -> latch temp fallback -> else _disabled=True
```

- **Tradeoff**: Resolving the default directory dynamically via a `cache_dir` property when `_explicit_cache_dir is None` (rather than eagerly caching env vars at module import time) ensures `AUTOCODE_CACHE_DIR` / `XDG_CACHE_HOME` overrides in tests or embedded callers work cleanly without mutating `_cache` or calling `mkdir()` at import time.
- **Root-home guard**: If `Path.home()` resolves to a filesystem root (e.g. `/` in minimal containers), skip home-relative paths so we never attempt `/.cache` and fall directly back to `tempfile.gettempdir()/autocode_mcp_compile_cache`.

### 2. Manifest Resource Limits & Accurate MLE Attribution (`resource_limit.py`, `compiler.py`, tool callers)

- **`resource_limit.py`**:
  - `load_problem_config(problem_dir)` delegates to `load_manifest(str(problem_dir))` instead of reading obsolete `problem.yaml`.
  - When `manifest is None` (missing `.autocode/manifest.json`), returns empty `ProblemConfig()` so `get_resource_limit` gracefully applies defaults (`2s / 256MB` for non-brute, `60s / available MB` for `brute`). Note: corrupt/non-UTF8 manifests still fail fast via `load_manifest` (`ValueError`) rather than silently bypassing validation.
  - `time_limit_ms` converts cleanly to seconds (`int` when divisible by 1000, `float` otherwise), and `memory_limit_mb` populates `ProblemConfig.memory_limit`. Non-`"brute"` roles (`"sol"`, `"reference"`, `"wrong"`) inherit manifest limits unless overridden by explicit parameters.
- **Tool `memory_mb` passthrough**:
  - `stress_test.py` (`_run_round` for both `sol_result` and `brute_result`), `problem_generate.py` (`_generate_single_candidate` and preserved hack test runner), and `validation.py` (`_validate_statement_samples` and `_validate_sample_files`) pass `memory_mb=manifest.memory_limit_mb` (defaulting to `256` when manifest is absent).
- **`compiler.py` MLE vs external `SIGKILL` attribution**:
  - `_sample_rss` takes an immediate initial RSS sample before sleeping (5ms polling interval) and, when `not disable_as_limit` and `rss > float(memory_mb)`, latches `rss_limit_exceeded = True` and terminates the child tree so macOS (where `RLIMIT_AS == RLIMIT_RSS` does not enforce memory) and Linux both enforce physical RSS caps.
  - `_classify_memory_limit_exceeded(...)` determines `memory_limit_exceeded` using explicit evidence:
    1. `rss_limit_exceeded` is True, or (`not disable_as_limit` and observed `peak_rss_mb > float(memory_mb)`).
    2. Windows `STATUS_NO_MEMORY` exit codes (`3221225495`, `-1073741801`).
    3. Explicit OOM diagnostics in `stderr` (`bad_alloc`, `out of memory`, `cannot allocate memory`).
    4. POSIX `SIGKILL` (`-9`, `137`, `-signal.SIGKILL`) ONLY when `not disable_as_limit` and `peak_rss_mb is not None` and `peak_rss_mb >= float(memory_mb) * 0.85`.
  - **Tradeoff**: Requiring `peak_rss_mb >= 0.85 * memory_mb` (or explicit OOM stderr) for `SIGKILL` prevents external `kill -9`, container kills, or macOS Gatekeeper kills on low-RSS processes from being falsely blamed on MLE, while the `0.85` threshold accommodates small sampling intervals right before a kernel OOM kill.

### 3. Additive Schema Freeze & Adoption Contract for `T1B` / `T2E` (`workflow/models.py`)

New models/fields are strictly optional and additive so all existing `manifest.json` files remain valid:
- `SandboxConfig(BaseModel)`:
  - `backend: Literal["none", "bwrap", "nsjail", "isolate", "apple_container", "docker"] = "none"`
  - `extra_args: list[str] = Field(default_factory=list)` (rejects control characters `\x00`, `\n`, `\r`).
- `SolutionEntry`:
  - `expected_verdict: Literal["AC", "WA", "TLE", "MLE", "RE", "PE"] | None = None`
  - Keeps `language: Literal["cpp", "python"] = "cpp"` compatible.
  - Adds `validate_executable_solution_language(entry: SolutionEntry | str) -> Literal["cpp"]`, which returns `"cpp"` for C++ entries and raises a descriptive `ValueError` for `"python"` (or any unknown language) until Wave 2 `T2E` implements Python execution.
- `AutoCodeManifest`:
  - `multi_test: bool = False`
  - `scale_token_index: int | None = Field(default=None, ge=0)` (boolean rejected)
  - `sandbox: SandboxConfig | None = None`
  - `time_limit_multipliers: dict[str, float] | None = None` (validates non-empty string keys and finite positive float multipliers, rejecting booleans/NaN/Inf).
- `AuditGates`:
  - `fail_on_mutation_survival: bool = False`
  - `min_branch_coverage: float | None = Field(default=None, ge=0.0, le=1.0, allow_inf_nan=False)` (boolean rejected).

#### Downstream Adoption Contract (`T1B` & `T2E`, Out of Scope in T0)
- **`T1B` (Standalone / Build Integration & Sandbox Execution)**:
  - Read `manifest.sandbox` (`backend`, `extra_args`) in runner/sandbox dispatchers; default `"none"` preserves local execution.
  - Read `SolutionEntry.expected_verdict`, `AuditGates.fail_on_mutation_survival`, `AuditGates.min_branch_coverage`, `AutoCodeManifest.multi_test`, and `AutoCodeManifest.scale_token_index` inside standalone/verify/audit pipelines without altering `AutoCodeManifest` schema definitions.
- **`T2E` (Python Solution Runtime & Multipliers)**:
  - Call `validate_executable_solution_language(entry)` at execution boundaries (`audit_standalone.py`, `build_all.py`, `verify_wrong_kill.py`, `solution.py`) before building/running solutions; once `T2E` adds Python runner dispatch and applies `manifest.time_limit_multipliers.get("python", 1.0)`, `T2E` updates `validate_executable_solution_language` (or replaces its guard in the Python-enabled execution path) cleanly without another schema migration.

### 4. Interactor Docs & PR CI Security Boundaries (`README.md`, `TROUBLESHOOTING.md`, `examples/ci/autocode-problem-audit.yml`)

- Align `README.md:196` with `interactor_template.cpp`, `InteractiveRunner`, `CLAUDE.md`, and `skills/autocode-workflow/SKILL.md`: `inf`/`ans` for reading test input/reference answer, `std::cout` + `std::flush`/`std::endl` for contestant prompts, `ouf` for reading contestant output, and `tout` (`argv[2]`) for optional diagnostic logs only.
- Add explicit security boundary warnings in `examples/ci/autocode-problem-audit.yml`, `README.md`, and `TROUBLESHOOTING.md`: untrusted PR audits MUST run only on ephemeral GitHub-hosted runners (`ubuntu-latest`), NEVER on privileged self-hosted runners, NEVER with injected sensitive secrets, and NEVER under `pull_request_target`.

## Risks / Trade-offs

- **Legacy test expectations**: `tests/test_cache.py` (`test_cache_directory_creation`, `test_compile_cache_falls_back_on_readonly_cwd`) and `tests/test_resource_limit.py` explicitly tested the obsolete `.cache/compile` eager `mkdir` and `problem.yaml` behavior; those tests must be updated cleanly to assert the new lazy default cache and `.autocode/manifest.json` contract without weakening coverage.
- **Zero-write regression risk**: Even read-only `autocode-audit . --standalone` invocations could trigger `CompileCache.get` if `use_cache=True` were enabled anywhere; keeping `get()` write-free on missing buckets plus moving the default cache directory out of `cwd` provides defense-in-depth.
