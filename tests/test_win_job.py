"""
Windows Job Objects 测试。

测试 WinJobObject 类的功能。
仅在 Windows 平台上运行。
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_win_job_import():
    """验证 WinJobObject 类存在。"""
    from autocode_mcp.utils.win_job import WinJobObject

    assert WinJobObject is not None


def test_win_job_create():
    """验证 Job Object 创建。"""
    from autocode_mcp.utils.win_job import WinJobObject

    job = WinJobObject(memory_mb=256, timeout_sec=10)

    assert job.memory_mb == 256
    assert job.timeout_sec == 10
    assert job.job_handle is not None

    job.terminate()


def test_win_job_context_manager():
    """验证上下文管理器功能。"""
    from autocode_mcp.utils.win_job import WinJobObject

    with WinJobObject(memory_mb=128, timeout_sec=5) as job:
        assert job.memory_mb == 128
        assert job.timeout_sec == 5
        assert job.job_handle is not None


def test_win_job_memory_limit_calculation():
    """验证内存限制计算。"""
    from autocode_mcp.utils.win_job import WinJobObject

    job = WinJobObject(memory_mb=256, timeout_sec=10)

    memory_bytes = job._set_memory_limit()
    assert memory_bytes == 256 * 1024 * 1024

    job.terminate()


def test_win_job_time_limit_calculation():
    """验证时间限制计算。"""
    from autocode_mcp.utils.win_job import WinJobObject

    job = WinJobObject(memory_mb=256, timeout_sec=10)

    time_100ns = job._set_time_limit()
    assert time_100ns == 10 * 10_000_000

    job.terminate()


def test_win_job_terminate():
    """验证终止功能。"""
    from autocode_mcp.utils.win_job import WinJobObject

    job = WinJobObject(memory_mb=256, timeout_sec=10)
    assert job.job_handle is not None

    job.terminate()
    assert job.job_handle is None


def test_win_job_assign_invalid_process():
    """验证分配无效进程时的错误处理。"""
    from autocode_mcp.utils.win_job import WinJobObject

    job = WinJobObject(memory_mb=256, timeout_sec=10)

    with pytest.raises(RuntimeError):
        job.assign_process(999999)

    job.terminate()
