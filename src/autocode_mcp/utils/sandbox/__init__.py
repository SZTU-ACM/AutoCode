"""Pluggable command sandbox backends and context registry."""

from __future__ import annotations

from .apple_container import DEFAULT_APPLE_CONTAINER_IMAGE, AppleContainerSandboxExecutor
from .base import (
    SANDBOX_SYSTEM_RO_PATHS,
    MountedPath,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteSandboxExecutor,
    SandboxExecutionPlan,
    SandboxExecutor,
    SandboxSpec,
    SandboxUnavailableError,
    build_runner_ro_paths,
    merge_sandbox_env,
    prepare_sandbox_mounts,
    resolve_wrapped_invocation,
    validate_command,
    validate_extra_args,
    validate_limits,
    validate_sandbox_path,
)
from .bwrap import BwrapSandboxExecutor
from .isolate import IsolateSandboxExecutor
from .nsjail import NsjailSandboxExecutor
from .registry import (
    SUPPORTED_SANDBOX_CHOICES,
    NoopSandboxExecutor,
    get_active_sandbox_executor,
    resolve_sandbox_executor,
    sandbox_context,
)

__all__ = [
    "AppleContainerSandboxExecutor",
    "BwrapSandboxExecutor",
    "DEFAULT_APPLE_CONTAINER_IMAGE",
    "IsolateSandboxExecutor",
    "NoopSandboxExecutor",
    "NsjailSandboxExecutor",
    "MountedPath",
    "RemoteExecutionRequest",
    "RemoteExecutionResult",
    "RemoteSandboxExecutor",
    "SANDBOX_SYSTEM_RO_PATHS",
    "SUPPORTED_SANDBOX_CHOICES",
    "SandboxExecutionPlan",
    "SandboxExecutor",
    "SandboxSpec",
    "SandboxUnavailableError",
    "build_runner_ro_paths",
    "get_active_sandbox_executor",
    "merge_sandbox_env",
    "prepare_sandbox_mounts",
    "resolve_sandbox_executor",
    "resolve_wrapped_invocation",
    "sandbox_context",
    "validate_command",
    "validate_extra_args",
    "validate_limits",
    "validate_sandbox_path",
]
