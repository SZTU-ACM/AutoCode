"""Sandbox registry, host probing, fallback policy, and ContextVar activation."""

from __future__ import annotations

import platform
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from .apple_container import AppleContainerSandboxExecutor
from .base import (
    SandboxExecutionPlan,
    SandboxExecutor,
    SandboxUnavailableError,
    prepare_sandbox_mounts,
    validate_command,
    validate_extra_args,
    validate_limits,
)
from .bwrap import BwrapSandboxExecutor
from .isolate import IsolateSandboxExecutor
from .nsjail import NsjailSandboxExecutor

if TYPE_CHECKING:
    from ...workflow.models import AutoCodeManifest, SandboxConfig

SUPPORTED_SANDBOX_CHOICES: tuple[str, ...] = (
    "auto",
    "none",
    "bwrap",
    "nsjail",
    "isolate",
    "apple_container",
)
_KNOWN_UNIMPLEMENTED_BACKENDS: frozenset[str] = frozenset({"docker"})


class NoopSandboxExecutor:
    """Zero-overhead pass-through executor used for ``backend="none"`` and degraded ``auto``."""

    def __init__(self, *, degraded: bool = False, reason: str = "") -> None:
        self._degraded = degraded
        self._reason = reason

    @property
    def name(self) -> str:
        return "none"

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def reason(self) -> str:
        return self._reason

    def is_available(self) -> bool:
        return True

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
    ) -> SandboxExecutionPlan:
        clean_cmd = validate_command(cmd)
        validate_limits(memory_limit_mb, time_limit_ms)
        clean_cwd, mounts = prepare_sandbox_mounts(
            cwd=cwd,
            ro_paths=ro_paths,
            rw_paths=rw_paths,
        )
        return SandboxExecutionPlan(
            argv=clean_cmd,
            cwd=clean_cwd,
            backend=self.name,
            degraded=self._degraded,
            reason=self._reason,
            ro_paths=tuple(m.path for m in mounts if not m.read_write),
            rw_paths=tuple(m.path for m in mounts if m.read_write),
            allow_network=allow_network,
        )


_ACTIVE_SANDBOX: ContextVar[SandboxExecutor | None] = ContextVar(
    "autocode_active_sandbox",
    default=None,
)


def _instantiate_backend(
    backend: str,
    *,
    extra_args: Sequence[str] = (),
) -> SandboxExecutor:
    if backend == "none":
        return NoopSandboxExecutor()
    if backend == "bwrap":
        return BwrapSandboxExecutor(extra_args=extra_args)
    if backend == "nsjail":
        return NsjailSandboxExecutor(extra_args=extra_args)
    if backend == "isolate":
        return IsolateSandboxExecutor(extra_args=extra_args)
    if backend == "apple_container":
        return AppleContainerSandboxExecutor(extra_args=extra_args)
    if backend in _KNOWN_UNIMPLEMENTED_BACKENDS:
        raise SandboxUnavailableError(
            backend,
            f"backend {backend!r} is not implemented in local sandbox registry",
        )
    raise ValueError(
        f"Unknown sandbox backend {backend!r}; expected one of: {', '.join(SUPPORTED_SANDBOX_CHOICES)}"
    )


def _auto_probe_order() -> tuple[str, ...]:
    if sys.platform.startswith("linux"):
        return ("bwrap", "nsjail", "isolate")
    if sys.platform == "darwin":
        return ("apple_container",)
    return ()


def _unavailable_reason(backend: str) -> str:
    if backend == "apple_container":
        return (
            f"requires Apple Silicon macOS (darwin arm64) and 'container' CLI on PATH "
            f"(host={sys.platform}/{platform.machine()})"
        )
    if backend in {"bwrap", "nsjail", "isolate"}:
        return f"requires Linux and '{backend}' CLI on PATH (host={sys.platform})"
    return f"backend {backend!r} is not available on {sys.platform}/{platform.machine()}"


def resolve_sandbox_executor(
    requested: str | None = None,
    manifest: AutoCodeManifest | None = None,
    *,
    extra_args: Sequence[str] | None = None,
) -> SandboxExecutor:
    """Resolve a sandbox backend from explicit choice or ``manifest.sandbox``.

    - ``"none"`` (or ``None`` without ``manifest.sandbox``) returns ``NoopSandboxExecutor``.
    - ``"auto"`` probes host backends and degrades cleanly to ``NoopSandboxExecutor(degraded=True)``
      when none are available.
    - Explicit unavailable backends raise ``SandboxUnavailableError``.
    """
    manifest_sandbox = manifest.sandbox if manifest is not None else None
    effective_name = requested
    if effective_name is None:
        effective_name = manifest_sandbox.backend if manifest_sandbox is not None else "none"

    normalized = str(effective_name).strip().lower()
    effective_extra: tuple[str, ...] = ()
    if extra_args is not None:
        effective_extra = validate_extra_args(extra_args, allow_network=True)
    elif manifest_sandbox is not None and (
        requested is None or normalized == manifest_sandbox.backend
    ):
        effective_extra = validate_extra_args(manifest_sandbox.extra_args, allow_network=True)

    if normalized == "auto":
        candidates = _auto_probe_order()
        for candidate_name in candidates:
            candidate_extra = (
                effective_extra
                if (
                    extra_args is not None
                    or (manifest_sandbox is not None and candidate_name == manifest_sandbox.backend)
                )
                else ()
            )
            candidate = _instantiate_backend(candidate_name, extra_args=candidate_extra)
            if candidate.is_available():
                return candidate
        probed_str = ", ".join(candidates) if candidates else "none supported on this OS"
        return NoopSandboxExecutor(
            degraded=True,
            reason=(
                f"No host sandbox backend available on {sys.platform}/{platform.machine()} "
                f"(probed: {probed_str}); falling back to 'none'"
            ),
        )

    executor = _instantiate_backend(normalized, extra_args=effective_extra)
    if not executor.is_available():
        raise SandboxUnavailableError(normalized, _unavailable_reason(normalized))
    return executor


@contextmanager
def sandbox_context(
    backend: SandboxExecutor | SandboxConfig | str | None,
    *,
    extra_args: Sequence[str] = (),
) -> Iterator[SandboxExecutor | None]:
    """Scope an active SandboxExecutor to the current sync/async execution context."""
    executor: SandboxExecutor | None
    if backend is None:
        executor = None
    elif isinstance(backend, str):
        executor = resolve_sandbox_executor(backend, extra_args=extra_args)
    elif isinstance(backend, SandboxExecutor):
        executor = backend
    else:
        from ...workflow.models import SandboxConfig

        if isinstance(backend, SandboxConfig):
            eff_args = extra_args if extra_args else backend.extra_args
            executor = resolve_sandbox_executor(backend.backend, extra_args=eff_args)
        else:
            raise TypeError(f"Unsupported sandbox backend specification: {type(backend).__name__}")

    token = _ACTIVE_SANDBOX.set(executor)
    try:
        yield executor
    finally:
        _ACTIVE_SANDBOX.reset(token)


def get_active_sandbox_executor(
    manifest: AutoCodeManifest | None = None,
) -> SandboxExecutor:
    """Return the active ContextVar sandbox executor, or resolve from manifest / default 'none'."""
    active = _ACTIVE_SANDBOX.get()
    if active is not None:
        return active
    if manifest is not None and manifest.sandbox is not None:
        return resolve_sandbox_executor(
            manifest.sandbox.backend,
            manifest=manifest,
            extra_args=manifest.sandbox.extra_args,
        )
    return NoopSandboxExecutor()
