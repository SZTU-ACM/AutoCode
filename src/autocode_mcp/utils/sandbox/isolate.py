"""IOI isolate (cgroups v2) sandbox backend."""

from __future__ import annotations

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


class IsolateSandboxExecutor:
    """Build IOI ``isolate --cg`` sandboxed command invocations."""

    def __init__(
        self,
        *,
        binary: str = "isolate",
        box_id: int = 0,
        extra_args: Sequence[str] = (),
    ) -> None:
        self._binary = validate_sandbox_path(binary, label="isolate binary")
        if isinstance(box_id, bool) or not isinstance(box_id, int) or not (0 <= box_id <= 999):
            raise ValueError(f"isolate box_id must be an integer in [0, 999], got {box_id!r}")
        self._box_id = box_id
        self._extra_args = validate_extra_args(extra_args, allow_network=True)

    @property
    def name(self) -> str:
        return "isolate"

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
        )

        argv: list[str] = [self._binary, "--cg", f"--box-id={self._box_id}"]
        if allow_network:
            argv.append("--share-net")

        if memory_limit_mb is not None:
            mem_kb = max(1024, int(round(float(memory_limit_mb) * 1024.0)))
            argv.extend([f"--cg-mem={mem_kb}", f"--mem={mem_kb}"])
        if time_limit_ms is not None:
            cpu_sec = max(0.1, round(float(time_limit_ms) / 1000.0, 3))
            wall_sec = max(1.0, round(max(cpu_sec * 3.0, cpu_sec + 2.0), 3))
            argv.extend([f"--time={cpu_sec:g}", f"--wall-time={wall_sec:g}"])

        for mount in mounts:
            suffix = ":rw" if mount.read_write else ""
            argv.append(f"--dir={mount.path}{suffix}")

        if clean_cwd is not None:
            argv.append(f"--chdir={clean_cwd}")

        argv.extend(validated_extra)
        argv.extend(["--run", "--"])
        argv.extend(clean_cmd)

        return SandboxExecutionPlan(
            argv=argv,
            cwd=clean_cwd,
            backend=self.name,
            ro_paths=tuple(m.path for m in mounts if not m.read_write),
            rw_paths=tuple(m.path for m in mounts if m.read_write),
            allow_network=allow_network,
        )
