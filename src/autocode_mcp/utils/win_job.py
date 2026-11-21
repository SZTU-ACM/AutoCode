"""
Windows Job Objects 模块。

提供 Windows 平台的进程资源限制功能，包括内存限制和 CPU 时间限制。
仅在 Windows 平台上可用，其他平台会抛出 RuntimeError。
"""

from __future__ import annotations

import sys
from types import TracebackType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    import pywintypes
    import win32api
    import win32con
    import win32job
    import win32process


class WinJobObject:
    """Windows Job Object 封装类。

    用于限制进程的内存使用和 CPU 时间。

    Attributes:
        memory_mb: 内存限制（MB）
        timeout_sec: CPU 时间限制（秒）
        job_handle: Job Object 句柄
    """

    def __init__(self, memory_mb: int | float, timeout_sec: int | float):
        """初始化 Job Object。

        Args:
            memory_mb: 内存限制（MB）
            timeout_sec: CPU 时间限制（秒）

        Raises:
            RuntimeError: 在非 Windows 平台上调用
        """
        if not _IS_WINDOWS:
            raise RuntimeError("WinJobObject is only available on Windows")

        self.memory_mb: int | float = memory_mb
        self.timeout_sec: int | float = timeout_sec
        self.job_handle: int | None = None
        self._create_job_object()

    def _create_job_object(self) -> None:
        """创建 Job Object。"""
        # 使用空名称创建匿名 Job Object（pywin32 CreateJobObject 不接受 None，需传入 ""）
        job = win32job.CreateJobObject(None, "")  # type: ignore[func-returns-value]
        self.job_handle = job if job is not None else 0

        limit_flags = (
            win32job.JOB_OBJECT_LIMIT_PROCESS_TIME
            | win32job.JOB_OBJECT_LIMIT_JOB_MEMORY
            | win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )

        extended_limit = {
            "BasicLimitInformation": {
                "PerProcessUserTimeLimit": self._set_time_limit(),
                "PerJobUserTimeLimit": 0,
                "LimitFlags": limit_flags,
                "MinimumWorkingSetSize": 0,
                "MaximumWorkingSetSize": 0,
                "ActiveProcessLimit": 0,
                "Affinity": 0,
                "PriorityClass": win32con.NORMAL_PRIORITY_CLASS,
                "SchedulingClass": 0,
            },
            "IoInfo": {
                "ReadOperationCount": 0,
                "WriteOperationCount": 0,
                "OtherOperationCount": 0,
                "ReadTransferCount": 0,
                "WriteTransferCount": 0,
                "OtherTransferCount": 0,
            },
            "ProcessMemoryLimit": 0,
            "JobMemoryLimit": self._set_memory_limit(),
            "PeakProcessMemoryUsed": 0,
            "PeakJobMemoryUsed": 0,
        }

        win32job.SetInformationJobObject(
            self.job_handle,
            win32job.JobObjectExtendedLimitInformation,
            extended_limit,
        )

    def _set_memory_limit(self) -> int:
        """设置内存限制。

        Returns:
            int: 内存限制（字节）
        """
        return max(1, int(round(float(self.memory_mb) * 1024 * 1024)))

    def _set_time_limit(self) -> int:
        """设置 CPU 时间限制。

        Returns:
            int: 时间限制（100ns 单位，SetInformationJobObject 要求为 int）
        """
        return max(1, int(round(float(self.timeout_sec) * 10_000_000)))

    def assign_process(self, pid: int) -> None:
        """将进程分配到 Job Object。

        Args:
            pid: 进程 ID

        Raises:
            RuntimeError: 分配失败时抛出
        """
        if self.job_handle is None:
            raise RuntimeError("Job object not created")

        query_flag = getattr(
            win32con,
            "PROCESS_QUERY_LIMITED_INFORMATION",
            win32con.PROCESS_QUERY_INFORMATION,
        )
        process_handle = None
        can_query_exit = False
        try:
            try:
                process_handle = win32api.OpenProcess(
                    win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE | query_flag,
                    False,
                    pid,
                )
                can_query_exit = True
            except (pywintypes.error, OSError):
                process_handle = win32api.OpenProcess(
                    win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                    False,
                    pid,
                )

            if can_query_exit:
                try:
                    if win32process.GetExitCodeProcess(process_handle) != win32con.STILL_ACTIVE:
                        return
                except (pywintypes.error, OSError):
                    pass

            win32job.AssignProcessToJobObject(self.job_handle, process_handle)
        except pywintypes.error as e:
            if can_query_exit and process_handle is not None:
                try:
                    if win32process.GetExitCodeProcess(process_handle) != win32con.STILL_ACTIVE:
                        return
                except (pywintypes.error, OSError):
                    pass
            raise RuntimeError(f"Failed to assign process {pid} to job object: {e}") from e
        except OSError as e:
            if can_query_exit and process_handle is not None:
                try:
                    if win32process.GetExitCodeProcess(process_handle) != win32con.STILL_ACTIVE:
                        return
                except (pywintypes.error, OSError):
                    pass
            raise RuntimeError(f"Failed to assign process {pid} to job object: {e}") from e
        finally:
            if process_handle is not None:
                try:
                    win32api.CloseHandle(process_handle)
                except (pywintypes.error, OSError):
                    pass

    def terminate(self) -> None:
        """终止 Job Object 中的所有进程。"""
        if self.job_handle is not None and self.job_handle != 0:
            try:
                win32job.TerminateJobObject(self.job_handle, 1)
            except (pywintypes.error, OSError):
                pass
            try:
                win32api.CloseHandle(self.job_handle)
            except (pywintypes.error, OSError):
                pass
            finally:
                self.job_handle = None

    def close(self) -> None:
        """关闭 Job Object 句柄。

        注意：当前 Job 使用了 KILL_ON_JOB_CLOSE，关闭最后一个句柄时
        仍可能终止 Job 中尚未退出的进程。
        """
        if self.job_handle is not None and self.job_handle != 0:
            try:
                win32api.CloseHandle(self.job_handle)
            except (pywintypes.error, OSError):
                pass
            finally:
                self.job_handle = None

    def __enter__(self) -> WinJobObject:
        """上下文管理器入口。"""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """上下文管理器退出，自动清理资源。"""
        self.terminate()

    def __del__(self) -> None:
        """析构函数，确保资源释放。"""
        self.terminate()
