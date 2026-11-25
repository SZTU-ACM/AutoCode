"""Linux unprivileged bubblewrap (bwrap) sandbox backend."""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
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


@functools.lru_cache(maxsize=4)
def _probe_bwrap_proc_mount(binary: str) -> bool:
    """Return True if ``bwrap`` can mount a fresh ``--proc /proc`` inside a user/PID namespace."""
    if not sys.platform.startswith("linux"):
        return True
    resolved = shutil.which(binary)
    if not resolved or not os.path.isfile(resolved):
        return True
    true_bin = next((p for p in ("/bin/true", "/usr/bin/true") if os.path.exists(p)), None)
    if true_bin is None:
        return True
    probe_cmd = [resolved, "--unshare-user-try", "--unshare-pid", "--proc", "/proc"]
    for sys_path in ("/usr", "/bin", "/lib", "/lib64"):
        if os.path.islink(sys_path) and os.path.exists(sys_path):
            probe_cmd.extend(["--symlink", os.readlink(sys_path), sys_path])
        elif os.path.exists(sys_path):
            probe_cmd.extend(["--ro-bind", sys_path, sys_path])
    probe_cmd.extend(["--", true_bin])
    try:
        proc = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3.0,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


class BwrapSandboxExecutor:
    """Build unprivileged ``bubblewrap`` sandboxed command invocations."""

    def __init__(
        self,
        *,
        binary: str = "bwrap",
        extra_args: Sequence[str] = (),
        include_system_paths: bool = True,
        mount_proc: bool | None = None,
    ) -> None:
        self._binary = validate_sandbox_path(binary, label="bwrap binary")
        self._extra_args = validate_extra_args(extra_args, allow_network=True)
        self._include_system_paths = include_system_paths
        self._mount_proc = mount_proc

    @property
    def name(self) -> str:
        return "bwrap"

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

        argv: list[str] = [self._binary, "--die-with-parent", "--new-session", "--unshare-user-try"]
        if not allow_network:
            argv.append("--unshare-net")
        argv.append("--unshare-pid")
        use_proc = (
            self._mount_proc if self._mount_proc is not None else _probe_bwrap_proc_mount(self._binary)
        )
        if use_proc:
            argv.extend(["--proc", "/proc"])
        argv.extend(
            [
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
            ]
        )

        explicit_mount_keys = {os.path.normcase(m.path) for m in mounts}
        if self._include_system_paths:
            for sys_path in SANDBOX_SYSTEM_RO_PATHS:
                if os.path.normcase(sys_path) in explicit_mount_keys:
                    continue
                if os.path.islink(sys_path):
                    # Preserve valid merged-/usr symlinks (/bin -> usr/bin, /lib -> usr/lib).
                    if os.path.exists(sys_path):
                        argv.extend(["--symlink", os.readlink(sys_path), sys_path])
                elif os.path.exists(sys_path):
                    argv.extend(["--ro-bind", sys_path, sys_path])

        for mount in mounts:
            flag = "--bind" if mount.read_write else "--ro-bind"
            argv.extend([flag, mount.path, mount.path])

        argv.extend(["--chdir", clean_cwd if clean_cwd is not None else "/tmp"])
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
