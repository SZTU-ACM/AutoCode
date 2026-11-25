"""Shared sandbox contracts, protocols, and containment validators."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

SANDBOX_SYSTEM_RO_PATHS: tuple[str, ...] = (
    "/usr",
    "/bin",
    "/lib",
    "/lib64",
    "/etc/alternatives",
    "/etc/ld.so.cache",
)

_WINDOWS_ROOT_RE = re.compile(r"^[A-Za-z]:[/\\]?$")


class SandboxUnavailableError(RuntimeError):
    """Raised when an explicitly requested sandbox backend cannot run on this host."""

    def __init__(self, backend: str, reason: str) -> None:
        self.backend = backend
        self.reason = reason
        super().__init__(f"Sandbox backend {backend!r} is unavailable: {reason}")


@dataclass(frozen=True)
class SandboxSpec:
    """Declarative sandbox policy specification."""

    backend: str = "none"
    extra_args: tuple[str, ...] = ()
    allow_network: bool = False
    ro_paths: tuple[str, ...] = ()
    rw_paths: tuple[str, ...] = ()
    container_image: str | None = None


@dataclass(frozen=True)
class SandboxExecutionPlan:
    """Wrapped subprocess invocation and metadata produced by a SandboxExecutor."""

    argv: list[str]
    cwd: str | None = None
    env_overrides: dict[str, str] = field(default_factory=dict)
    backend: str = "none"
    degraded: bool = False
    reason: str = ""
    ro_paths: tuple[str, ...] = ()
    rw_paths: tuple[str, ...] = ()
    allow_network: bool = False


@dataclass(frozen=True)
class MountedPath:
    """Normalized sandbox bind-mount entry ordered for safe nested layering."""

    path: str
    read_write: bool


@dataclass(frozen=True)
class RemoteExecutionRequest:
    """Execution payload for async remote sandbox microservices."""

    cmd: tuple[str, ...]
    stdin: str = ""
    cwd: str | None = None
    ro_paths: tuple[str, ...] = ()
    rw_paths: tuple[str, ...] = ()
    allow_network: bool = False
    memory_limit_mb: float = 256.0
    time_limit_ms: float = 2000.0
    env: dict[str, str] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteExecutionResult:
    """Structured outcome returned by an async RemoteSandboxExecutor."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    wall_time_ms: float = 0.0
    cpu_time_ms: float | None = None
    memory_mb: float = 0.0
    timed_out: bool = False
    memory_limit_exceeded: bool = False
    backend: str = "remote"
    detail: str = ""


@runtime_checkable
class SandboxExecutor(Protocol):
    """Protocol implemented by local command-wrapping sandbox backends."""

    @property
    def name(self) -> str: ...

    def is_available(self) -> bool: ...

    def wrap_command(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None = None,
        ro_paths: Sequence[str] = (),
        rw_paths: Sequence[str] = (),
        allow_network: bool = False,
        memory_limit_mb: float | None = None,
        time_limit_ms: float | None = None,
    ) -> SandboxExecutionPlan: ...


@runtime_checkable
class RemoteSandboxExecutor(Protocol):
    """Async protocol stub for future remote sandboxes (go-judge, microsandbox, E2B)."""

    @property
    def name(self) -> str: ...

    async def is_available(self) -> bool: ...

    async def execute(self, request: RemoteExecutionRequest) -> RemoteExecutionResult: ...

    async def close(self) -> None: ...


def _split_segments(raw_path: str) -> list[str]:
    return [seg for seg in re.split(r"[/\\]+", raw_path) if seg and seg != "."]


def _is_root_path(path: str) -> bool:
    return path in {"/", "\\"} or bool(_WINDOWS_ROOT_RE.fullmatch(path))


def _norm_key(path: str) -> str:
    return os.path.normcase(path.rstrip("/\\") or path)


def _is_same_or_under(child: str, parent: str) -> bool:
    c_key = _norm_key(child)
    p_key = _norm_key(parent)
    if c_key == p_key:
        return True
    if _is_root_path(p_key):
        return c_key.startswith(p_key)
    return c_key.startswith(p_key + "/") or c_key.startswith(p_key + "\\")


def validate_sandbox_path(
    path: str,
    *,
    label: str = "path",
    forbid_root: bool = False,
) -> str:
    """Validate and normalize a sandbox filesystem path without collapsing '..'."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"Sandbox {label} must be a non-empty string")
    if "\x00" in path:
        raise ValueError(f"Sandbox {label} contains null byte: {path!r}")
    if "\n" in path or "\r" in path:
        raise ValueError(f"Sandbox {label} contains control newline characters: {path!r}")
    cleaned = path.strip()
    if any(seg == ".." for seg in re.split(r"[/\\]+", cleaned)):
        raise ValueError(f"Sandbox {label} must not contain '..' path traversal: {path!r}")
    if len(cleaned) > 1 and not _is_root_path(cleaned):
        cleaned = cleaned.rstrip("/\\") or cleaned
    if forbid_root and _is_root_path(cleaned):
        raise ValueError(f"Sandbox {label} cannot mount filesystem root read-write: {path!r}")
    return cleaned


def validate_command(cmd: Sequence[str]) -> list[str]:
    """Validate command argv entries against empty input, null bytes, and traversal."""
    if isinstance(cmd, str | bytes) or not cmd:
        raise ValueError("Sandbox command must be a non-empty sequence of strings")
    out: list[str] = []
    for idx, arg in enumerate(cmd):
        if not isinstance(arg, str):
            raise ValueError(f"Sandbox cmd[{idx}] must be a string, got {type(arg).__name__}")
        if "\x00" in arg:
            raise ValueError(f"Sandbox cmd[{idx}] contains null byte")
        if idx == 0:
            out.append(validate_sandbox_path(arg, label="cmd[0]"))
        else:
            out.append(arg)
    return out


def validate_extra_args(
    extra_args: Sequence[str],
    *,
    allow_network: bool = False,
) -> tuple[str, ...]:
    """Validate extra sandbox CLI args against control chars, traversal, and net escape."""
    cleaned: list[str] = []
    args_list = list(extra_args)
    for idx, arg in enumerate(args_list):
        if not isinstance(arg, str) or not arg.strip():
            raise ValueError("Sandbox extra_args entries must be non-empty strings")
        if any(ch in arg for ch in ("\x00", "\n", "\r")):
            raise ValueError(f"Sandbox extra_args[{idx}] contains invalid control characters")
        if any(seg == ".." for seg in re.split(r"[/\\]+", arg)):
            raise ValueError(f"Sandbox extra_args[{idx}] must not contain '..' traversal: {arg!r}")
        if not allow_network:
            lowered = arg.strip().lower()
            if lowered in {"--share-net", "--disable_clone_newnet", "--disable-clone-newnet"}:
                raise ValueError(
                    f"Sandbox extra_args[{idx}] cannot re-enable network when allow_network=False: {arg!r}"
                )
            if lowered.startswith("--network=") and lowered != "--network=none":
                raise ValueError(
                    f"Sandbox extra_args[{idx}] cannot override network when allow_network=False: {arg!r}"
                )
            if lowered == "--network":
                next_val = args_list[idx + 1].strip().lower() if idx + 1 < len(args_list) else ""
                if next_val != "none":
                    raise ValueError(
                        "Sandbox extra_args cannot set --network to anything other than 'none' "
                        "when allow_network=False"
                    )
        cleaned.append(arg)
    return tuple(cleaned)


def validate_limits(
    memory_limit_mb: float | None,
    time_limit_ms: float | None,
) -> None:
    """Validate optional resource limit parameters."""
    if memory_limit_mb is not None:
        if (
            isinstance(memory_limit_mb, bool)
            or not isinstance(memory_limit_mb, int | float)
            or not math.isfinite(float(memory_limit_mb))
            or float(memory_limit_mb) <= 0.0
        ):
            raise ValueError(f"memory_limit_mb must be a positive finite number, got {memory_limit_mb!r}")
    if time_limit_ms is not None:
        if (
            isinstance(time_limit_ms, bool)
            or not isinstance(time_limit_ms, int | float)
            or not math.isfinite(float(time_limit_ms))
            or float(time_limit_ms) <= 0.0
        ):
            raise ValueError(f"time_limit_ms must be a positive finite number, got {time_limit_ms!r}")


def prepare_sandbox_mounts(
    *,
    cwd: str | None,
    ro_paths: Sequence[str],
    rw_paths: Sequence[str],
    skip_uncovered_cwd_paths: frozenset[str] = frozenset(),
) -> tuple[str | None, list[MountedPath]]:
    """Validate, deduplicate, and depth-sort RO/RW mount entries."""
    clean_cwd = validate_sandbox_path(cwd, label="cwd") if cwd is not None else None

    ro_clean: list[str] = []
    ro_keys: dict[str, str] = {}
    for raw in ro_paths:
        item = validate_sandbox_path(raw, label="ro_paths")
        key = _norm_key(item)
        if key not in ro_keys:
            ro_keys[key] = item
            ro_clean.append(item)

    rw_clean: list[str] = []
    rw_keys: dict[str, str] = {}
    for raw in rw_paths:
        item = validate_sandbox_path(raw, label="rw_paths", forbid_root=True)
        key = _norm_key(item)
        if key in ro_keys:
            raise ValueError(f"Conflicting read-only and read-write sandbox mount for path: {item!r}")
        if key not in rw_keys:
            rw_keys[key] = item
            rw_clean.append(item)

    entries: list[MountedPath] = [
        *(MountedPath(path=p, read_write=False) for p in ro_clean),
        *(MountedPath(path=p, read_write=True) for p in rw_clean),
    ]

    if clean_cwd is not None and _norm_key(clean_cwd) not in skip_uncovered_cwd_paths:
        covered = any(_is_same_or_under(clean_cwd, m.path) for m in entries)
        if not covered:
            # Unlisted working directories are mounted read-only by default (least privilege).
            entries.append(MountedPath(path=clean_cwd, read_write=False))

    indexed = list(enumerate(entries))
    indexed.sort(key=lambda pair: (len(_split_segments(pair[1].path)), pair[0]))
    return clean_cwd, [entry for _, entry in indexed]


def build_runner_ro_paths(
    binary_path: str,
    *extra_dirs: str | None,
    exclude_dirs: Sequence[str] = (),
) -> tuple[str, ...]:
    """Collect deduplicated read-only directory mounts for runner subprocesses."""
    excluded = {_norm_key(d) for d in exclude_dirs if d}
    candidates: list[str] = [os.path.dirname(binary_path)]
    for item in extra_dirs:
        if item:
            candidates.append(item)
    out: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        if not cand:
            continue
        key = _norm_key(cand)
        if key in excluded or key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return tuple(out)


def merge_sandbox_env(
    base_env: Mapping[str, str] | None,
    overrides: Mapping[str, str],
) -> dict[str, str] | None:
    """Merge sandbox environment overrides without mutating caller dicts."""
    if not overrides:
        return dict(base_env) if base_env is not None else None
    merged = dict(base_env) if base_env is not None else dict(os.environ)
    merged.update(overrides)
    return merged


def resolve_wrapped_invocation(
    executor: SandboxExecutor,
    cmd: Sequence[str],
    *,
    cwd: str | None = None,
    ro_paths: Sequence[str] = (),
    rw_paths: Sequence[str] = (),
    allow_network: bool = False,
    memory_limit_mb: float | None = None,
    time_limit_ms: float | None = None,
    base_env: Mapping[str, str] | None = None,
) -> tuple[list[str], str | None, dict[str, str] | None]:
    """Wrap command via active executor when non-'none', keeping zero-overhead default parity."""
    if executor.name == "none":
        return list(cmd), cwd, dict(base_env) if base_env is not None else None
    plan = executor.wrap_command(
        cmd,
        cwd=cwd,
        ro_paths=ro_paths,
        rw_paths=rw_paths,
        allow_network=allow_network,
        memory_limit_mb=memory_limit_mb,
        time_limit_ms=time_limit_ms,
    )
    return plan.argv, plan.cwd, merge_sandbox_env(base_env, plan.env_overrides)
