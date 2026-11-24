## Why

Several foundational runtime hygiene, resource-enforcement, documentation, and schema gaps undermine AutoCode's zero-write standalone auditing, accurate verdict attribution, CI safety, and forward compatibility:

1. **Standalone zero-write violation (Issue #13)**: `CompileCache.__init__` defaulted to relative `.cache/compile` in the current working directory and eagerly called `mkdir()` on module import (`src/autocode_mcp/utils/compiler.py`). Running `autocode-audit . --standalone` from inside an external problem directory or read-only directory could pollute the target directory or trigger permission errors.
2. **Inconsistent resource limits and blind `SIGKILL` MLE attribution (Issue #18)**: `src/autocode_mcp/utils/resource_limit.py` still read obsolete `problem.yaml` instead of `.autocode/manifest.json` (`time_limit_ms`, `memory_limit_mb`); `stress_test.py`, `problem_generate.py`, and `validation.py` omitted `memory_mb=manifest.memory_limit_mb` when calling `run_binary`; and `compiler.py` blindly labeled every `SIGKILL` (`-9` / `137`) as `memory_limit_exceeded=True` even when observed `peak_rss_mb` remained tiny (misattributing external/Gatekeeper kills as MLE) while failing to catch RSS overflows on macOS where `RLIMIT_AS` does not enforce physical memory limits.
3. **Incorrect interactive `tout` documentation (Issue #17)**: `README.md` instructed interactor authors to send prompts to contestants via `tout` and avoid `std::cout`, contradicting `interactor_template.cpp`, `InteractiveRunner` (`judge_runner.py`), `CLAUDE.md`, and `skills/autocode-workflow/SKILL.md`, and causing protocol deadlocks.
4. **Missing PR CI security boundary warnings (Issue #21)**: `examples/ci/autocode-problem-audit.yml`, `README.md`, and `TROUBLESHOOTING.md` lacked explicit warnings against running untrusted PR C++ audits on privileged self-hosted runners, with `pull_request_target`, or with sensitive secrets.
5. **Unfrozen additive manifest contracts & Python execution guard (Issue #20)**: Downstream capabilities (sandbox isolation, expected-verdict test matrices, multi-test scale fitting, language time-limit multipliers, and stricter mutation/coverage audit gates) need a backward-compatible additive schema freeze in `src/autocode_mcp/workflow/models.py` plus an explicit runtime rejection helper for unimplemented `language="python"` execution.

## What Changes

- **Zero-write lazy `CompileCache`**: Remove eager `mkdir()` from `CompileCache.__init__`; switch the default cache location to a user/temp hierarchy (`AUTOCODE_CACHE_DIR` -> `XDG_CACHE_HOME/autocode-mcp/compile` -> OS cache dir -> `tempfile.gettempdir()/autocode_mcp_compile_cache`), preserving explicit `cache_dir` overrides and falling back gracefully to temp when the user cache dir is unwritable.
- **Manifest-backed resource limits & accurate MLE attribution**:
  - Update `resource_limit.py` (`load_problem_config`, `get_resource_limit`) to read `.autocode/manifest.json` via `load_manifest()` with graceful defaults when missing.
  - Pass `memory_mb=manifest.memory_limit_mb` to `run_binary` in `stress_test.py`, `problem_generate.py`, and `validation.py`.
  - Add physical RSS overflow enforcement (when `not disable_as_limit`) and accurate MLE classification in `compiler.py` so unrelated external `SIGKILL` with low `peak_rss_mb` is not mislabeled as MLE while genuine OOM/RSS overflow, `bad_alloc`/`Out of memory`, and Windows `STATUS_NO_MEMORY` remain detected.
- **Documentation & CI security alignment**:
  - Fix `README.md` interactive interactor documentation (`std::cout` + flush for contestant output, `inf`/`ans` for input/answer, `ouf` for contestant output, `tout` (`argv[2]`) for optional diagnostics).
  - Add explicit PR CI security boundary warnings across `examples/ci/autocode-problem-audit.yml`, `README.md`, and `TROUBLESHOOTING.md`.
- **Additive schema freeze & Python language guard**:
  - Add `SandboxConfig`, `SolutionEntry.expected_verdict`, `AutoCodeManifest.{multi_test, scale_token_index, sandbox, time_limit_multipliers}`, and `AuditGates.{fail_on_mutation_survival, min_branch_coverage}` in `workflow/models.py`.
  - Add `validate_executable_solution_language(entry)` to clearly reject unimplemented `"python"` runtime execution while keeping manifest deserialization compatible.
- **Regression & doc-sync tests**: Add `tests/test_base_hygiene_regressions.py`, expand `tests/test_docs_sync.py`, and update legacy assertions in `tests/test_cache.py` and `tests/test_resource_limit.py` that previously tested `.cache/compile` / `problem.yaml`.

## Capabilities

### New Capabilities

- `base-contracts-and-hygiene`: Lazy user/temp compile caching with standalone zero-write guarantees, manifest-aligned resource limit propagation and accurate RSS/OOM MLE attribution, synchronized interactor/CI security documentation, and backward-compatible additive manifest schema contracts with explicit unsupported-language execution guards.

### Modified Capabilities

- None (canonical `openspec/specs/` entries such as `standalone-problem-audit` remain satisfied and reinforced by the zero-write cache guarantee).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12 workflow stages, SHA-256 gating, `.autocode/runtime.json` relative path hygiene, and the 22 MCP tool signatures (`problem_validate`, `problem_generate_tests`, `stress_test_run`, `solution_run`, etc.).
- **Affected Files**:
  - `src/autocode_mcp/utils/cache.py`
  - `src/autocode_mcp/utils/compiler.py`
  - `src/autocode_mcp/utils/resource_limit.py`
  - `src/autocode_mcp/tools/stress_test.py`
  - `src/autocode_mcp/tools/problem_generate.py`
  - `src/autocode_mcp/tools/validation.py`
  - `src/autocode_mcp/workflow/models.py`
  - `README.md`, `TROUBLESHOOTING.md`, `examples/ci/autocode-problem-audit.yml`
  - `tests/test_docs_sync.py`, `tests/test_base_hygiene_regressions.py`, `tests/test_cache.py`, `tests/test_resource_limit.py`
- **Dependencies**: None added (`psutil`, `pydantic`, `pyyaml` already present).
- **Risks**:
  - *Process RSS polling race*: Short-lived processes killed externally before psutil's first poll have `peak_rss_mb=None`; resolved by sampling immediately upon process launch and refusing to infer MLE from `SIGKILL` alone without RSS or OOM stderr evidence.
  - *macOS vs Linux memory enforcement*: `RLIMIT_AS` aliases `RLIMIT_RSS` on macOS and is not kernel-enforced for `mmap`; mitigated by pairing POSIX rlimits with active RSS monitoring when `disable_as_limit=False`.
  - *Backward compatibility of manifests*: Mitigated by making every new schema field optional with safe defaults and verifying all `examples/*/manifest.json` load via `load_manifest`.

## Non-goals

- Do NOT modify any of the 22 MCP tool external signatures or schemas (`server.py`, `tools/schemas.py`).
- Do NOT edit `workflow/gates.py`, `workflow/enforcement.py`, `tools/audit*.py`, `tools/verify*.py`, `tools/complexity.py`, or `utils/mutation.py`.
- Do NOT implement sandbox backends (`bwrap`, `nsjail`, `isolate`, `apple_container`, `docker`), Python solution execution in `audit_standalone.py` / `build_all.py`, or mutation/coverage enforcement gates in this change.
