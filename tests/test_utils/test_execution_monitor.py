import os
import sys

import pytest

from autocode_mcp.utils.execution_monitor import DynamicExecutionMonitor


@pytest.mark.asyncio
async def test_run_monitored_process_success(tmp_path: os.PathLike[str]) -> None:
    in_file = os.path.join(str(tmp_path), "input.in")
    with open(in_file, "w", encoding="utf-8") as f:
        f.write("42\n")

    code = "import sys; val = sys.stdin.read().strip(); print(f'read:{val}')"
    cmd = [sys.executable, "-c", code]

    res = await DynamicExecutionMonitor.run_monitored_process(
        cmd,
        in_file,
        time_limit_ms=2000.0,
        memory_limit_mb=512.0,
    )

    assert res["status"] == "ok"
    assert res["returncode"] == 0
    assert "read:42" in res["stdout"]
    assert res["cpu_time_ms"] is not None
    assert res["cpu_time_ms"] >= 0.1
    assert res["memory_mb"] > 0.0


@pytest.mark.asyncio
async def test_run_monitored_process_timeout(tmp_path: os.PathLike[str]) -> None:
    in_file = os.path.join(str(tmp_path), "input.in")
    with open(in_file, "w", encoding="utf-8") as f:
        f.write("1\n")

    code = "import time\nwhile True:\n    time.sleep(0.01)"
    cmd = [sys.executable, "-c", code]

    res = await DynamicExecutionMonitor.run_monitored_process(
        cmd,
        in_file,
        time_limit_ms=200.0,  # 200ms
        memory_limit_mb=512.0,
    )

    assert res["status"] == "timeout"


@pytest.mark.asyncio
async def test_run_monitored_process_runtime_error(tmp_path: os.PathLike[str]) -> None:
    in_file = os.path.join(str(tmp_path), "input.in")
    with open(in_file, "w", encoding="utf-8") as f:
        f.write("1\n")

    code = "import sys; sys.stderr.write('fatal error'); sys.exit(3)"
    cmd = [sys.executable, "-c", code]

    res = await DynamicExecutionMonitor.run_monitored_process(
        cmd,
        in_file,
        time_limit_ms=2000.0,
        memory_limit_mb=512.0,
    )

    assert res["status"] == "runtime_error"
    assert res["returncode"] == 3
    assert "fatal error" in res["stderr"]


@pytest.mark.asyncio
async def test_run_monitored_process_mle(tmp_path: os.PathLike[str]) -> None:
    in_file = os.path.join(str(tmp_path), "input.in")
    with open(in_file, "w", encoding="utf-8") as f:
        f.write("1\n")

    code = "import time\na = [0] * (25 * 1024 * 1024)\ntime.sleep(0.1)"
    cmd = [sys.executable, "-c", code]

    res = await DynamicExecutionMonitor.run_monitored_process(
        cmd,
        in_file,
        time_limit_ms=2000.0,
        memory_limit_mb=20.0,
    )

    assert res["status"] == "mle"
