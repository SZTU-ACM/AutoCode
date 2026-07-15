"""进程存活检测与整树回收助手。

配合 POSIX 下 ``start_new_session=True`` 启动的子进程，以进程组为单位回收
整个子进程树；Windows 使用 ``taskkill /T`` 回收进程树。回收前用 ``psutil``
校验 PID 存活，已退出的 PID 会被跳过（幂等，不报错）。

该模块是残留进程回收的唯一真值来源，供 :mod:`autocode_mcp.utils.compiler`
的强制终止路径与 ``problem_cleanup_processes`` 工具共同复用。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Iterable
from typing import Any, cast

import psutil

_logger = logging.getLogger(__name__)

# POSIX 上优先 SIGKILL；缺失时（理论上不会）回退 SIGTERM。
POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def is_pid_alive(pid: int) -> bool:
    """PID 是否仍存活（已退出 / 僵尸进程视为不存活）。"""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return proc.is_running()
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        return False


def filter_alive_pids(pids: Iterable[int]) -> list[int]:
    """仅保留仍存活的 PID（用于 resume 过滤已退出的残留 PID）。"""
    return [pid for pid in pids if isinstance(pid, int) and pid > 0 and is_pid_alive(pid)]


async def terminate_pid_tree(pid: int) -> tuple[bool, str]:
    """回收 PID 及其子进程树。

    Args:
        pid: 目标进程 PID

    Returns:
        ``(ok, error)``：``ok`` 为 True 表示已终止或本就不存在（幂等）；
        为 False 时 ``error`` 给出失败原因。
    """
    if not isinstance(pid, int) or pid <= 0:
        return False, "invalid pid"
    if os.name == "nt":
        proc = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0:
            return True, ""
        return False, stderr.decode("utf-8", errors="replace")
    # POSIX：优先按进程组整树回收（要求子进程以 start_new_session=True 启动）。
    try:
        cast(Any, os).killpg(cast(Any, os).getpgid(pid), POSIX_KILL_SIGNAL)
        return True, ""
    except ProcessLookupError:
        return True, ""
    except OSError as exc:
        _logger.debug("killpg failed for pid=%s: %s; fallback to single kill", pid, exc)
    try:
        os.kill(pid, POSIX_KILL_SIGNAL)
        return True, ""
    except ProcessLookupError:
        return True, ""
    except OSError as exc:
        return False, str(exc)
