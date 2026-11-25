## Context

Issue #21 highlighted that running untrusted PR problem code (solutions, checkers, interactors, communicators) without process/filesystem/network sandboxing relies purely on CI runner ephemerality. `workflow/models.py` already froze `SandboxConfig(backend, extra_args)` in the base schema, leaving runtime command wrapping and backend selection to this change.

Naming boundaries remain explicit:
- Sandbox runtime abstractions, backend builders, runner integrations, and CLI wiring live under `src/autocode_mcp/utils/sandbox/`, `src/autocode_mcp/utils/judge_runner.py`, `src/autocode_mcp/utils/communication_runner.py`, and `src/autocode_mcp/cli/audit.py`.
- Hook helpers under `scripts/` and MCP tool definitions under `src/autocode_mcp/tools/` are untouched.

## Architecture & Design Decisions

```text
autocode-audit --sandbox <mode>   (cli/audit.py)
  │
  ├─ resolve_sandbox_executor(mode)
  │    ├─ unavailable explicit backend ──> exit 1 (SandboxUnavailableError)
  │    └─ "auto" with no host backend  ──> NoopSandboxExecutor(degraded=True, reason=...)
  │
  └─ with sandbox_context(executor):   (ContextVar[SandboxExecutor | None])
       └─ ProblemAuditTool.execute(...)  (untouched tools/audit.py)
            └─ StandardRunner / CheckerRunner / InteractiveRunner / CommunicationRunner
                 └─ get_active_sandbox_executor(manifest)
                      ├─ backend == "none" ──> direct execution (0 overhead)
                      └─ backend != "none" ──> executor.wrap_command(...)
                           ├─ bwrap.py           (Linux unprivileged namespaces)
                           ├─ nsjail.py          (Linux nsjail --mode o)
                           ├─ isolate.py         (Linux IOI isolate --cg)
                           └─ apple_container.py (macOS arm64 Linux microVM)
```

### 1. Shared Containment & Validation Layer (`base.py`)

All backends share `validate_sandbox_inputs(...)` and `normalize_mount_layout(...)` in `base.py` so validation logic is never duplicated:
- Reject empty commands, non-string argv entries, null bytes (`\x00`), control newlines (`\r`, `\n`), and any `".."` path traversal segment in `cmd[0]`, `cwd`, `ro_paths`, `rw_paths`, or `extra_args`.
- Reject filesystem roots (`/`, `\`, `C:\`) in `rw_paths`.
- Reject identical normalized paths appearing in both `ro_paths` and `rw_paths` (`ValueError`), while sorting nested mounts shallowest-first so parent/child RO-vs-RW overrides compose deterministically (e.g. RW workdir + RO subpath or vice versa).
- Reject `--share-net` or non-`none` `--network` overrides in `extra_args` when `allow_network=False`.
- Reject non-finite or non-positive `memory_limit_mb` / `time_limit_ms` when explicitly provided.

### 2. Backend Tradeoffs & Platform Specifics

- **`BwrapSandboxExecutor` (`bwrap.py`)**:
  - Mounts `--proc /proc`, `--dev /dev`, and `--tmpfs /tmp` *before* user `ro_paths`/`rw_paths` so temporary workspaces under `/tmp/...` are layered on top of the ephemeral tmpfs rather than masked by it.
  - Emits `--symlink <target> <sys_path>` when top-level system paths (`/bin`, `/lib`, `/lib64`) are symlinks (Ubuntu merged-`/usr` compatibility) and `--ro-bind` for real directories/files (`/usr`, `/etc/alternatives`, `/etc/ld.so.cache`).
  - Tradeoff: `bwrap` enforces namespace, network (`--unshare-net`), and mount isolation while delegating CPU/RSS limits to `DynamicExecutionMonitor` / `InteractiveRunner` rather than wrapping a second `prlimit` binary.
- **`NsjailSandboxExecutor` (`nsjail.py`)**:
  - Uses `--mode o --quiet`, `--iface_no_lo` (when `not allow_network`) or `--disable_clone_newnet` (when `allow_network`), `--rlimit_as` (MiB), `--time_limit` / `--rlimit_cpu` (integer seconds ceiling), `--tmpfsmount /tmp`, `--bindmount_ro`, and `--bindmount`.
- **`IsolateSandboxExecutor` (`isolate.py`)**:
  - Uses cgroups v2 `--cg`, `--box-id=<id>`, `--cg-mem=<KiB>`, `--mem=<KiB>`, `--time=<sec>`, `--wall-time=<sec>`, `--dir=<p>` (RO) vs `--dir=<p>:rw` (RW), `--chdir=<cwd>`, `--run -- <cmd>`.
- **`AppleContainerSandboxExecutor` (`apple_container.py`)**:
  - Available strictly on Apple Silicon macOS (`sys.platform == "darwin"`, `arm64`/`aarch64`, and `container` binary in `PATH`).
  - Builds `container run --rm` with `--network none`, `--memory <N>M`, `--volume <p>:<p>:ro` / `--volume <p>:<p>`, `--workdir <cwd>`, `<image>`, `<cmd>`.
  - Tradeoff: Because `apple/container` executes Linux containers inside a lightweight Virtualization.framework microVM, binaries passed to it must be Linux-ABI executables (not host macOS Mach-O binaries); when unavailable or not requested, `auto` probes fall back cleanly to `none`.
- **Async `RemoteSandboxExecutor` Stub (`base.py`)**:
  - Defines `RemoteExecutionRequest`, `RemoteExecutionResult`, and `@runtime_checkable class RemoteSandboxExecutor(Protocol)` (`name`, `async is_available()`, `async execute(request)`, `async close()`) so future `go-judge`, `microsandbox`, or `E2B` clients plug in cleanly without changing local `SandboxExecutor` callers.

### 3. `ContextVar` Activation vs `tools/audit.py` Untouched (`registry.py`, `cli/audit.py`)

- **Why `ContextVar`**: `ProblemAuditTool.execute()` (`tools/audit.py`) and `tools/verify*.py` are shared across tracks and remain untouched. Using `contextvars.ContextVar` in `sandbox/registry.py` propagates the CLI-selected sandbox cleanly across `await` boundaries and child `asyncio.Task`s while isolating concurrent requests in the same event loop.
- **Precedence**:
  1. Active `_ACTIVE_SANDBOX` (`ContextVar` set by `sandbox_context(executor_or_name)`): overrides manifest config when explicitly set (including `--sandbox none`).
  2. `manifest.sandbox` (when `ContextVar` is unset/`None`, e.g., `CommunicationRunner.run_from_manifest` or callers passing `manifest`): resolves `manifest.sandbox.backend` and `manifest.sandbox.extra_args`.
  3. Default `NoopSandboxExecutor()` (`backend="none"`): zero-overhead direct execution.
- **Least-Privilege Runner Mounts (`judge_runner.py`, `communication_runner.py`)**:
  - `StandardRunner`: solution binary's directory is `ro_paths` (unless identical to `cwd`), isolated `cwd` (if provided) is `rw_paths`.
  - `CheckerRunner`: checker binary's directory and temporary `in/out/ans` directory are strictly `ro_paths`; `rw_paths=()`.
  - `InteractiveRunner`: interactor gets `ro_paths=[interactor_dir, ...]` and `rw_paths=[tmp_dir]` (for `tout`), while contestant solution gets `ro_paths=[solution_dir, ...]` and `rw_paths=()` with NO access to `tmp_dir` (`input.in` / `answer.ans`).
  - `CommunicationRunner`: `run_twice` stages pass isolated `pass_cwd` to `StandardRunner` and run communicator with `ro_paths=[comm_dir, tmp_dir]`, `rw_paths=()`; `run_multi_process` isolates each participant `proc_{idx}_cwd` separately from manager `tmp_dir`.

## Risks / Mitigations

- **Silent fallback vs explicit failure**: Passing `--sandbox auto` must never break environments lacking `bwrap`/`container`, so `auto` falls back to `NoopSandboxExecutor(degraded=True, reason=...)` and logs a warning to `stderr`; conversely, passing an explicit backend such as `--sandbox bwrap` when unavailable MUST fail fast (`SandboxUnavailableError` -> CLI exit `1`) rather than silently running unsandboxed.
- **Existing test parity**: When `executor.name == "none"`, runners execute commands directly without modifying argv, preserving 100% existing timing, `RLIMIT_CORE=0`, and test mock parity.
