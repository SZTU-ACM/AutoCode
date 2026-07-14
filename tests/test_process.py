"""进程存活检测与整树回收助手（utils.process）的单元测试。"""

from __future__ import annotations

import os

import pytest

from autocode_mcp.utils import process as process_module
from autocode_mcp.utils.process import (
    filter_alive_pids,
    is_pid_alive,
    terminate_pid_tree,
)


def test_is_pid_alive_current_process():
    """当前进程 PID 应被判定为存活。"""
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_rejects_invalid_pid():
    """非法 PID（<=0 或非整数）一律判定为不存活。"""
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False
    assert is_pid_alive("123") is False  # type: ignore[arg-type]


def test_is_pid_alive_stale_pid():
    """极不可能存在的 PID 应判定为不存活。"""
    assert is_pid_alive(2_000_000_000) is False


def test_filter_alive_pids_keeps_only_alive():
    """filter_alive_pids 应仅保留存活 PID，丢弃已退出 / 非法 PID。"""
    alive = os.getpid()
    result = filter_alive_pids([alive, 2_000_000_000, 0, -5])
    assert result == [alive]


@pytest.mark.asyncio
async def test_terminate_pid_tree_invalid_pid():
    """非法 PID 返回失败且给出错误说明。"""
    ok, err = await terminate_pid_tree(0)
    assert ok is False
    assert err


@pytest.mark.asyncio
async def test_terminate_pid_tree_stale_pid_is_idempotent(monkeypatch):
    """对已不存在的 PID 回收应幂等成功（不抛错）。"""
    if os.name == "nt":
        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"", b""

        async def fake_exec(*args, **kwargs):
            return _FakeProc()

        monkeypatch.setattr(process_module.asyncio, "create_subprocess_exec", fake_exec)

    ok, err = await terminate_pid_tree(2_000_000_000)
    assert ok is True
    assert err == ""
