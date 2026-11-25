## Purpose

Define the pluggable command sandbox abstraction, Linux/macOS backend command builders, graceful availability probing vs explicit failure contracts, async context propagation, runner wrapping, and standalone CI usage without breaking existing MCP tool contracts or zero-write guarantees.

## Requirements

### Requirement: Pluggable local and remote sandbox protocols with strict input containment
The system SHALL provide a `SandboxExecutor` protocol (`name`, `is_available()`, `wrap_command(cmd, *, cwd, ro_paths, rw_paths, allow_network, memory_limit_mb, time_limit_ms)`) returning a `SandboxExecutionPlan`, an `async` `RemoteSandboxExecutor` protocol stub (`RemoteExecutionRequest`, `RemoteExecutionResult`) for future remote sandboxes, and shared validation rejecting empty commands, null bytes (`\x00`), control newlines (`\r`, `\n`), `".."` path-traversal segments, filesystem roots in `rw_paths`, conflicting identical `ro_paths`/`rw_paths`, and network-enabling extra arguments when `allow_network=False`.

#### Scenario: Path traversal, null bytes, and conflicting mounts are rejected
- **WHEN** `wrap_command` is invoked with `".."` segments in `cmd[0]`, `cwd`, `ro_paths`, or `rw_paths`, a null byte in any path or argument, `/` in `rw_paths`, or the same normalized path in both `ro_paths` and `rw_paths`
- **THEN** the sandbox executor SHALL raise `ValueError` before spawning any subprocess

#### Scenario: Remote sandbox protocol stub defines typed async execution contract
- **WHEN** a future remote sandbox client (`go-judge`, `microsandbox`, `E2B`) implements `RemoteSandboxExecutor`
- **THEN** it satisfies `@runtime_checkable` checks for `name`, `async is_available()`, `async execute(request)`, and `async close()`

### Requirement: Linux and Apple Silicon macOS backend command builders
The system SHALL provide command builders for `bwrap` (`--unshare-net`, `--die-with-parent`, `--proc /proc`, `--dev /dev`, `--tmpfs /tmp`, `--ro-bind` system/read-only paths with merged-`/usr` symlink preservation, `--bind` read-write workspace paths), `nsjail` (`--mode o`, `--iface_no_lo` when network is disabled, `--bindmount_ro`/`--bindmount`, `--rlimit_as`, `--time_limit`, `--rlimit_cpu`), `isolate` (`--cg`, `--cg-mem`, `--mem`, `--time`, `--wall-time`, `--dir`, `--run`), and `apple_container` (`container run --rm --network none --memory ... --volume ...:ro/--volume ... --workdir ...` on Apple Silicon macOS).

#### Scenario: Read-only and read-write mounts preserve least-privilege separation
- **WHEN** `wrap_command` is called with distinct `ro_paths`, `rw_paths`, and `allow_network=False` on `bwrap`, `nsjail`, `isolate`, or `apple_container`
- **THEN** the generated `SandboxExecutionPlan.argv` disables network access (`--unshare-net`, `--iface_no_lo`, omitted `--share-net`, or `--network none`), binds `ro_paths` strictly read-only, and binds only `rw_paths` read-write

### Requirement: Host probing, degraded auto fallback, explicit unavailability error, and ContextVar scoping
The system SHALL resolve `auto|none|bwrap|nsjail|isolate|apple_container` via `resolve_sandbox_executor(requested, manifest=None)` and propagate active executors through async-safe `ContextVar` helpers `sandbox_context(...)` and `get_active_sandbox_executor(manifest=None)`. Under `"auto"`, when no host backend is available, resolution SHALL fall back to `NoopSandboxExecutor` (`name="none"`, `degraded=True`, non-empty `reason`); when an unavailable backend is explicitly requested, resolution SHALL raise `SandboxUnavailableError`.

#### Scenario: Auto mode degrades gracefully while explicit unavailable backend fails fast
- **WHEN** no sandbox CLI is installed on the host
- **THEN** `resolve_sandbox_executor("auto")` returns a `"none"` executor with `degraded=True` and a descriptive `reason`, whereas `resolve_sandbox_executor("bwrap")` raises `SandboxUnavailableError`

#### Scenario: ContextVar scopes sandbox selection per async task without cross-task leakage
- **WHEN** an async task enters `with sandbox_context(executor):` concurrently alongside an un-scoped sibling async task
- **THEN** `get_active_sandbox_executor()` returns `executor` inside the scoped task and `NoopSandboxExecutor` (`name="none"`) inside the sibling task

### Requirement: Runner, CLI, and CI audit integration preserving default parity
The system SHALL integrate `get_active_sandbox_executor` and `wrap_command` into `StandardRunner`, `CheckerRunner`, `InteractiveRunner`, and `CommunicationRunner` (`run_twice`, `run_grader_linked`, `run_multi_process`, `run_from_manifest`), preserving zero-overhead direct execution when the active backend is `"none"`, SHALL expose `--sandbox auto|none|bwrap|nsjail|isolate|apple_container` on `autocode-audit` (`cli/audit.py`) returning exit code `1` with a clear message on `SandboxUnavailableError`, and SHALL document/enable `--sandbox auto` in `examples/ci/autocode-problem-audit.yml` while preserving all PR CI security guardrails.

#### Scenario: CLI rejects unavailable explicit sandbox and enables auto sandbox in CI template
- **WHEN** `autocode-audit <dir> --sandbox bwrap` runs on a host lacking `bwrap`
- **THEN** the CLI exits with return code `1` and outputs a clear unavailable-backend error without invoking `ProblemAuditTool.execute`, while `examples/ci/autocode-problem-audit.yml` runs `--standalone --sandbox auto` on ephemeral `ubuntu-latest` runners with `contents: read` permissions
