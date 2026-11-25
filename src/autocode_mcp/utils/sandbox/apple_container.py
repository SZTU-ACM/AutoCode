"""macOS Apple Silicon apple/container Linux microVM sandbox backend."""

from __future__ import annotations

import math
import os
import platform
import shutil
import sys
from collections.abc import Sequence

from .base import (
    SandboxExecutionPlan,
    prepare_sandbox_mounts,
    validate_command,
    validate_extra_args,
    validate_limits,
    validate_sandbox_path,
)

DEFAULT_APPLE_CONTAINER_IMAGE = "ubuntu:24.04"


def _validate_image_name(image: str) -> str:
    if not isinstance(image, str) or not image.strip():
        raise ValueError("apple_container image must be a non-empty string")
    cleaned = image.strip()
    if any(ch.isspace() or ch in ("\x00", "\n", "\r") for ch in cleaned):
        raise ValueError(f"apple_container image contains invalid whitespace or control chars: {image!r}")
    if cleaned.startswith("-"):
        raise ValueError(f"apple_container image must not start with '-': {image!r}")
    return cleaned


class AppleContainerSandboxExecutor:
    """Build ephemeral Linux microVM invocations via Apple's ``container`` CLI."""

    def __init__(
        self,
        *,
        binary: str = "container",
        image: str | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        self._binary = validate_sandbox_path(binary, label="container binary")
        raw_image = image if image is not None else os.environ.get(
            "AUTOCODE_APPLE_CONTAINER_IMAGE", DEFAULT_APPLE_CONTAINER_IMAGE
        )
        self._image = _validate_image_name(raw_image)
        self._extra_args = validate_extra_args(extra_args, allow_network=True)

    @property
    def name(self) -> str:
        return "apple_container"

    @property
    def image(self) -> str:
        return self._image

    def is_available(self) -> bool:
        return (
            sys.platform == "darwin"
            and platform.machine().lower() in {"arm64", "aarch64"}
            and shutil.which(self._binary) is not None
        )

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
        validated_extra = validate_extra_args(self._extra_args, allow_network=allow_network)
        clean_cwd, mounts = prepare_sandbox_mounts(
            cwd=cwd,
            ro_paths=ro_paths,
            rw_paths=rw_paths,
        )

        argv: list[str] = [self._binary, "run", "--rm"]
        if not allow_network:
            argv.extend(["--network", "none"])
        if memory_limit_mb is not None:
            mem_mb = max(16, math.ceil(float(memory_limit_mb)))
            argv.extend(["--memory", f"{mem_mb}M"])

        for mount in mounts:
            spec = f"{mount.path}:{mount.path}" if mount.read_write else f"{mount.path}:{mount.path}:ro"
            argv.extend(["--volume", spec])

        if clean_cwd is not None:
            argv.extend(["--workdir", clean_cwd])

        argv.extend(validated_extra)
        argv.append(self._image)
        argv.extend(clean_cmd)

        return SandboxExecutionPlan(
            argv=argv,
            cwd=clean_cwd,
            backend=self.name,
            ro_paths=tuple(m.path for m in mounts if not m.read_write),
            rw_paths=tuple(m.path for m in mounts if m.read_write),
            allow_network=allow_network,
        )
