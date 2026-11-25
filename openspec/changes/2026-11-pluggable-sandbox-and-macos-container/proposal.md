## Why

AutoCode compiles and runs untrusted problem packages and contestant/interactor/checker binaries during standalone PR CI audits (`autocode-audit --standalone`) and local verification (`StandardRunner`, `CheckerRunner`, `InteractiveRunner`, `CommunicationRunner`). Previously, GitHub Issue #21 was addressed only with documentation guardrails (`examples/ci/autocode-problem-audit.yml`, `README.md`, `TROUBLESHOOTING.md`) rather than enforceable OS-level runtime isolation. Executing untrusted binaries directly on the host process namespace leaves the filesystem outside temporary working directories and outbound networking uncontained when `--standalone` runs locally or in CI.

Additionally, macOS Apple Silicon hosts lack Linux unprivileged user namespaces and cgroups, motivating an opt-in ephemeral Linux microVM backend (`apple/container`) alongside Linux `bubblewrap` (`bwrap`), `nsjail`, and IOI `isolate`, plus a clean async remote sandbox protocol stub for future microservice sandboxes (`go-judge`, `microsandbox`, `E2B`).

## What Changes

- **Pluggable Sandbox Package (`src/autocode_mcp/utils/sandbox/`)**:
  - `base.py`: Define `SandboxSpec`, `SandboxExecutionPlan`, `SandboxUnavailableError`, `SandboxExecutor` protocol (`name`, `is_available()`, `wrap_command(...)`), async `RemoteSandboxExecutor` protocol stub (`RemoteExecutionRequest`, `RemoteExecutionResult`), and shared path-traversal / null-byte / mount-containment validators.
  - `bwrap.py`: Implement `BwrapSandboxExecutor` for Linux unprivileged `bubblewrap` (`--unshare-net`, `--unshare-pid`, `--die-with-parent`, `--proc /proc`, `--dev /dev`, `--tmpfs /tmp`, read-only system/binary mounts with merged-`/usr` symlink awareness, read-write workspace mounts).
  - `nsjail.py`: Implement `NsjailSandboxExecutor` for Google `nsjail` (`--mode o`, `--iface_no_lo` / network isolation, `--bindmount_ro` / `--bindmount`, `--rlimit_as`, `--time_limit`, `--rlimit_cpu`).
  - `isolate.py`: Implement `IsolateSandboxExecutor` for IOI `isolate` (cgroups v2 `--cg`, `--cg-mem`, `--mem`, `--time`, `--wall-time`, `--dir`, `--run`).
  - `apple_container.py`: Implement `AppleContainerSandboxExecutor` for macOS Apple Silicon `container` CLI (`apple/container`) launching ephemeral `--rm` Linux microVMs with `--network none`, `--memory`, and read-only/read-write `--volume` mounts.
  - `registry.py`: Implement `NoopSandboxExecutor` (`none`), `resolve_sandbox_executor(requested, manifest=None)` supporting `auto|none|bwrap|nsjail|isolate|apple_container` (probing host availability under `auto` with graceful `degraded=True` fallback to `none`, and raising `SandboxUnavailableError` when an explicit backend is unavailable), and `ContextVar`-backed `sandbox_context(...)` / `get_active_sandbox_executor(manifest=None)`.
- **Runner Integration**:
  - Update `src/autocode_mcp/utils/judge_runner.py` (`StandardRunner`, `CheckerRunner`, `InteractiveRunner`) and `src/autocode_mcp/utils/communication_runner.py` (`CommunicationRunner`) to consult `get_active_sandbox_executor` and wrap subprocess commands with minimal least-privilege read-only vs read-write mounts while preserving zero-overhead `none` default parity.
- **CLI & Problem CI Integration**:
  - Add `--sandbox auto|none|bwrap|nsjail|isolate|apple_container` to `src/autocode_mcp/cli/audit.py`, entering `sandbox_context(...)` around `ProblemAuditTool.execute(...)` without modifying `tools/audit.py`, and exiting with status `1` and a clear diagnostic when an explicitly requested backend is unavailable.
  - Update `examples/ci/autocode-problem-audit.yml` to pass `--sandbox auto` with inline guidance while preserving all PR CI security warnings and least-privilege constraints.
- **Tests**:
  - Add `tests/test_sandbox_backends.py` covering backend argv construction, `--unshare-net` / network isolation, read-only vs read-write mount containment, path-traversal and null-byte guards, `auto` degradation vs explicit `SandboxUnavailableError`, async `ContextVar` isolation across concurrent tasks, runner wrapping with fake/recording `SandboxExecutor`s, and `cli/audit.py --sandbox`.

## Capabilities

### New Capabilities

- `pluggable-sandbox`: Pluggable OS/microVM command sandboxing abstraction (`none`, `bwrap`, `nsjail`, `isolate`, `apple_container`), async remote sandbox protocol stub, `ContextVar` runtime activation, runner command wrapping, and CLI `--sandbox` enforcement.

### Modified Capabilities

- `standalone-problem-audit`: Reinforced with optional `--sandbox` CLI isolation around standalone execution without writing into target problem directories or altering `tools/audit*.py`.

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12 authoring workflow stages, gate checks, zero-write standalone audit guarantees, and all 22 MCP tool signatures (`server.py`, `tools/schemas.py`).
- **Affected Files**:
  - `src/autocode_mcp/utils/sandbox/__init__.py`, `base.py`, `bwrap.py`, `nsjail.py`, `isolate.py`, `apple_container.py`, `registry.py`
  - `src/autocode_mcp/utils/judge_runner.py`
  - `src/autocode_mcp/utils/communication_runner.py`
  - `src/autocode_mcp/cli/audit.py`
  - `examples/ci/autocode-problem-audit.yml`
  - `tests/test_sandbox_backends.py`
- **Dependencies**: None added (`pyproject.toml` and `uv.lock` remain unchanged); sandbox CLIs (`bwrap`, `nsjail`, `isolate`, `container`) are probed dynamically at runtime.
- **Risks**:
  - *Process resource accounting under wrappers*: `DynamicExecutionMonitor` samples process trees recursively (`children(recursive=True)`), and `nsjail` / `isolate` enforce kernel rlimits; short-lived wrapper overhead can skew sub-10ms timing if enabled unconditionally, mitigated by keeping `none` as the zero-overhead default.
  - *Linux merged-`/usr` symlinks & host unavailability*: On Ubuntu 22.04/24.04 (`/bin -> usr/bin`), `--ro-bind /bin /bin` can conflict with empty rootfs construction; mitigated by emitting `--symlink` for symlinked top-level system directories in `bwrap.py` and probing availability gracefully under `--sandbox auto`.
  - *macOS ELF vs Mach-O mismatch under `apple_container`*: Host macOS binaries are Mach-O while `apple/container` runs a Linux microVM; mitigated by documenting that `apple_container` targets Linux-compiled executables and keeping host `none` fallback explicit.

## Non-goals

- Do NOT modify any of the 22 MCP tool external signatures (`server.py`, `tools/schemas.py`).
- Do NOT modify `workflow/models.py`, `workflow/gates.py`, `tools/audit*.py`, `tools/verify*.py`, `tools/stress_test.py`, `utils/mutation.py`, `pyproject.toml`, or `uv.lock`.
- Do NOT require `bwrap`, `nsjail`, `isolate`, or `apple/container` to be pre-installed on developer workstations or CI runners; `--sandbox auto` must degrade cleanly to `none` when no backend is present.
- Do NOT implement a concrete HTTP/gRPC client for remote sandbox services (`go-judge`, `microsandbox`, `E2B`); only the typed async protocol stub (`RemoteSandboxExecutor`) is defined here.
