## Purpose

Define foundational runtime hygiene, resource-limit accuracy, documentation synchronization, PR CI security guardrails, and backward-compatible additive manifest schema contracts for AutoCode.

## Requirements

### Requirement: Zero-write lazy compilation cache outside problem directories
The system SHALL avoid creating any directories or files inside the current working directory during module import or `CompileCache` initialization, and SHALL store default compilation caches in a user-level or temporary location resolved in order: `AUTOCODE_CACHE_DIR`, `XDG_CACHE_HOME/autocode-mcp/compile`, OS user cache directory, and `tempfile.gettempdir()/autocode_mcp_compile_cache`, creating cache directories lazily only when writing cache entries (falling back to the temp directory if the default user cache path is unwritable). Explicit `cache_dir` constructor arguments SHALL remain supported.

#### Scenario: Standalone audit from external problem working directory writes zero cache files
- **WHEN** a process imports `autocode_mcp.utils.compiler` or runs `autocode-audit . --standalone` with `cwd` set to an external problem directory
- **THEN** no `.cache/` directory or new cache artifacts are created inside that problem directory

#### Scenario: Explicit cache override and read-only user cache fallback
- **WHEN** `CompileCache` is initialized with an explicit `cache_dir` or when the default user cache path fails lazily with `OSError`
- **THEN** initialization still creates no directories eagerly, explicit paths are honored upon cache writes, and unwritable default paths fall back cleanly to `tempfile.gettempdir()/autocode_mcp_compile_cache`

### Requirement: Manifest-driven resource limits and accurate MLE attribution
The system SHALL read problem time and memory limits (`time_limit_ms`, `memory_limit_mb`) from `.autocode/manifest.json` via `load_manifest()` in `resource_limit.py` (with graceful defaults when the manifest is missing), SHALL pass `manifest.memory_limit_mb` to `run_binary` during stress testing, test generation, and sample validation, and SHALL distinguish genuine memory exhaustion (RSS limit overflow, explicit `bad_alloc`/`Out of memory`/`cannot allocate memory` diagnostics, Windows `STATUS_NO_MEMORY`, or `SIGKILL` with observed `peak_rss_mb` near or above `memory_mb`) from unrelated external `SIGKILL` (`-9` / `137`) with low RSS.

#### Scenario: Missing manifest falls back to graceful defaults while present manifest propagates memory limit
- **WHEN** `get_resource_limit` or tool pipelines (`stress_test_run`, `problem_generate_tests`, `problem_validate`) execute a solution
- **THEN** `.autocode/manifest.json` `memory_limit_mb` is forwarded to `run_binary` when present, and missing manifests fall back gracefully to `256` MB without consulting `problem.yaml`

#### Scenario: Unrelated external SIGKILL with low RSS is not misclassified as MLE
- **WHEN** a child process exits due to an external `SIGKILL` (`-9` or `137`) while observed `peak_rss_mb` is well below `memory_mb` (or unmeasured) and stderr contains no OOM diagnostic
- **THEN** `RunResult.memory_limit_exceeded` SHALL be `False`, whereas genuine RSS overflow or `std::bad_alloc` SHALL set `RunResult.memory_limit_exceeded = True`

### Requirement: Accurate interactive interactor documentation and PR CI security boundaries
The system SHALL document the canonical testlib `registerInteraction` stream contract in `README.md` (`inf`/`ans` for inputs/answers, `std::cout` with explicit flush for contestant prompts, `ouf` for contestant output, and `tout` (`argv[2]`) solely for optional diagnostic logs) and SHALL publish explicit PR CI security boundary warnings in `examples/ci/autocode-problem-audit.yml`, `README.md`, and `TROUBLESHOOTING.md` mandating ephemeral GitHub-hosted runners and prohibiting privileged self-hosted runners, injected sensitive secrets, and `pull_request_target`.

#### Scenario: Documentation rejects tout-to-contestant instructions and states CI isolation boundaries
- **WHEN** documentation drift checks inspect `README.md`, `TROUBLESHOOTING.md`, and `examples/ci/autocode-problem-audit.yml`
- **THEN** `README.md` instructs using `std::cout` (never `tout`) to communicate with contestants, and all three files warn against self-hosted runners, secret injection, and `pull_request_target` for untrusted PR audits

### Requirement: Backward-compatible additive manifest schema freeze and unsupported-language execution guard
The system SHALL extend `workflow/models.py` purely additively with `SandboxConfig` (`backend`, `extra_args`), `SolutionEntry.expected_verdict`, `AutoCodeManifest.{multi_test, scale_token_index, sandbox, time_limit_multipliers}`, and `AuditGates.{fail_on_mutation_survival, min_branch_coverage}` while keeping all existing manifests valid, and SHALL provide `validate_executable_solution_language(entry)` to accept `"cpp"` and explicitly reject unimplemented `"python"` solution execution until runtime Python execution is implemented.

#### Scenario: Existing manifests load cleanly while unimplemented Python execution is explicitly rejected
- **WHEN** `load_manifest` parses existing problem manifests or manifests containing the new optional fields (`language="python"`, `expected_verdict`, `sandbox`, `multi_test`, `scale_token_index`, `time_limit_multipliers`, `fail_on_mutation_survival`, `min_branch_coverage`)
- **THEN** manifest validation succeeds, and calling `validate_executable_solution_language` on a `language="python"` entry raises `ValueError` while `"cpp"` returns `"cpp"`
