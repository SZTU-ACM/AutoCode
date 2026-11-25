"""Google nsjail sandbox backend."""

from __future__ import annotations

import math
import os
import shutil
import sys
from collections.abc import Sequence

from .base import (
    SANDBOX_SYSTEM_RO_PATHS,
    SandboxExecutionPlan,
    prepare_sandbox_mounts,
    validate_command,
    validate_extra_args,
    validate_limits,
    validate_sandbox_path,
)


class NsjailSandboxExecutor:
    """Build standalone ``nsjail --mode o`` sandboxed command invocations."""

    def __init__(
        self,
        *,
        binary: str = "nsjail",
        extra_args: Sequence[str] = (),
        include_system_paths: bool = True,
    ) -> None:
        self._binary = validate_sandbox_path(binary, label="nsjail binary")
        self._extra_args = validate_extra_args(extra_args, allow_network=True)
        self._include_system_paths = include_system_paths

    @property
    def name(self) -> str:
        return "nsjail"

    def is_available(self) -> bool:
        return sys.platform.startswith("linux") and shutil.which(self._binary) is not None

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
            skip_uncovered_cwd_paths=frozenset({"/tmp"}),
        )

        argv: list[str] = [self._binary, "--mode", "o", "--quiet"]
        if not allow_network:
            argv.append("--iface_no_lo")
        else:
            argv.append("--disable_clone_newnet")

        if time_limit_ms is not None:
            secs = max(1, math.ceil(float(time_limit_ms) / 1000.0))
            argv.extend(["--time_limit", str(secs), "--rlimit_cpu", str(secs)])
        if memory_limit_mb is not None:
            mem_mb = max(1, math.ceil(float(memory_limit_mb)))
            argv.extend(["--rlimit_as", str(mem_mb)])

        argv.extend(["--tmpfsmount", "/tmp"])

        explicit_mount_keys = {os.path.normcase(m.path) for m in mounts}
        if self._include_system_paths:
            for sys_path in SANDBOX_SYSTEM_RO_PATHS:
                if os.path.normcase(sys_path) in explicit_mount_keys:
                    continue
                if os.path.exists(sys_path):
                    argv.extend(["--bindmount_ro", f"{sys_path}:{sys_path}"])

        for mount in mounts:
            flag = "--bindmount" if mount.read_write else "--bindmount_ro"
            argv.extend([flag, f"{mount.path}:{mount.path}"])

        argv.extend(["--cwd", clean_cwd if clean_cwd is not None else "/tmp"])
        argv.extend(validated_extra)
        argv.append("--")
        argv.extend(clean_cmd)

        return SandboxExecutionPlan(
            argv=argv,
            cwd=clean_cwd,
            backend=self.name,
            ro_paths=tuple(m.path for m in mounts if not m.read_write),
            rw_paths=tuple(m.path for m in mounts if m.read_write),
            allow_network=allow_network,
        )
