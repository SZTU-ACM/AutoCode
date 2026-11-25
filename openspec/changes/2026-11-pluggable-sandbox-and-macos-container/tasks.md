## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/pluggable-sandbox/spec.md` under `openspec/changes/2026-11-pluggable-sandbox-and-macos-container/` compliant with `openspec/config.yaml`.

## 2. Sandbox Abstraction & Backends (`src/autocode_mcp/utils/sandbox/`)

- [x] 2.1 Implement `base.py` with `SandboxSpec`, `SandboxExecutionPlan`, `SandboxUnavailableError`, `SandboxExecutor` protocol, async `RemoteSandboxExecutor` protocol stub, and shared path/mount/extra-arg validation helpers.
- [x] 2.2 Implement `bwrap.py` (`BwrapSandboxExecutor`), `nsjail.py` (`NsjailSandboxExecutor`), `isolate.py` (`IsolateSandboxExecutor`), and `apple_container.py` (`AppleContainerSandboxExecutor`).
- [x] 2.3 Implement `registry.py` (`NoopSandboxExecutor`, `resolve_sandbox_executor`, `sandbox_context`, `get_active_sandbox_executor`) and export public API via `__init__.py`.

## 3. Runner, CLI, and CI Integration

- [x] 3.1 Integrate `get_active_sandbox_executor` and `wrap_command` into `StandardRunner`, `CheckerRunner`, and `InteractiveRunner` in `src/autocode_mcp/utils/judge_runner.py` with zero-overhead `none` parity.
- [x] 3.2 Integrate `get_active_sandbox_executor` and `wrap_command` into `CommunicationRunner` (`_run_communicator_stage`, `run_multi_process`, `run_from_manifest`) in `src/autocode_mcp/utils/communication_runner.py`.
- [x] 3.3 Add `--sandbox auto|none|bwrap|nsjail|isolate|apple_container` to `src/autocode_mcp/cli/audit.py`, entering `sandbox_context(...)` around `ProblemAuditTool.execute(...)` and returning exit code `1` on `SandboxUnavailableError`.
- [x] 3.4 Update `examples/ci/autocode-problem-audit.yml` with `--sandbox auto` usage and explanatory comment while keeping `tests/test_ci_workflows.py` and `tests/test_docs_sync.py` green.

## 4. Verification & Full Regression Suite

- [x] 4.1 Create `tests/test_sandbox_backends.py` covering backend command construction, network isolation, RO/RW mount containment, traversal/null-byte rejection, `auto` degradation vs `SandboxUnavailableError`, async `ContextVar` isolation, real runner command wrapping, and `cli/audit.py --sandbox`.
- [x] 4.2 Run targeted checks (`uv run pytest tests/test_sandbox_backends.py tests/test_docs_sync.py tests/test_ci_workflows.py tests/test_cli_audit_format.py -q`) and full gates (`uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, `uv run pytest tests/ -q`), verifying `git diff --name-only` stays strictly inside the whitelist.
