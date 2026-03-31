"""
编译和执行工具函数。

提供安全的 C++ 编译和执行能力，包含：
- 超时控制
- 资源限制
- 临时目录隔离
"""

import asyncio
import os
import shutil
import sys
import uuid
from dataclasses import dataclass

from .. import TEMPLATES_DIR
from .platform import get_exe_extension


@dataclass
class CompileResult:
    """编译结果。"""

    success: bool
    binary_path: str | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""


@dataclass
class RunResult:
    """执行结果。"""

    success: bool
    return_code: int = -1
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    timed_out: bool = False
    time_ms: int = 0


def get_work_dir(problem_dir: str, tool_name: str) -> str:
    """
    每次调用生成独立工作目录。

    Args:
        problem_dir: 题目目录
        tool_name: 工具名称

    Returns:
        临时工作目录路径
    """
    run_id = uuid.uuid4().hex[:8]
    work_dir = os.path.join(problem_dir, ".tmp", f"{tool_name}_{run_id}")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def cleanup_work_dir(work_dir: str) -> None:
    """清理工作目录。"""
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)


async def compile_cpp(
    source_path: str,
    binary_path: str,
    timeout: int = 30,
    compiler: str = "g++",
    std: str = "c++2c",
    opt_level: str = "O2",
    include_dirs: list[str] | None = None,
) -> CompileResult:
    """
    编译 C++ 文件，带超时控制。

    Args:
        source_path: 源文件路径
        binary_path: 输出二进制文件路径
        timeout: 超时时间（秒）
        compiler: 编译器名称
        std: C++ 标准
        opt_level: 优化级别
        include_dirs: 额外的 include 目录列表

    Returns:
        CompileResult: 编译结果
    """
    if not os.path.exists(source_path):
        return CompileResult(
            success=False,
            error=f"Source file not found: {source_path}",
        )

    # 确保输出目录存在
    output_dir = os.path.dirname(binary_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # 获取 templates 目录路径（用于 testlib.h）
    templates_dir = TEMPLATES_DIR

    # 构建 include 参数
    include_flags = []
    if include_dirs:
        for inc_dir in include_dirs:
            include_flags.extend(["-I", inc_dir])
    # 添加 templates 目录
    if os.path.exists(templates_dir):
        include_flags.extend(["-I", templates_dir])

    cmd = [
        compiler,
        f"-std={std}",
        f"-{opt_level}",
        *include_flags,
        source_path,
        "-o",
        binary_path,
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return CompileResult(
                success=False,
                error=f"Compilation timeout after {timeout}s",
            )

        if process.returncode != 0:
            return CompileResult(
                success=False,
                error=f"Compilation failed with code {process.returncode}",
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )

        return CompileResult(
            success=True,
            binary_path=binary_path,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    except FileNotFoundError:
        return CompileResult(
            success=False,
            error=f"Compiler not found: {compiler}",
        )
    except Exception as e:
        return CompileResult(
            success=False,
            error=f"Compilation error: {str(e)}",
        )


async def _run_process(
    cmd: list[str],
    stdin: str = "",
    timeout: int = 5,
    memory_mb: int = 256,
) -> RunResult:
    """运行进程的公共逻辑。"""
    import time

    start_time = time.time()

    try:
        if sys.platform == "win32":
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            memory_bytes = memory_mb * 1024 * 1024
            process = await asyncio.create_subprocess_exec(
                "prlimit",
                f"--as={memory_bytes}",
                f"--data={memory_bytes}",
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=stdin.encode("utf-8") if stdin else None), timeout=timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return RunResult(
                success=False,
                timed_out=True,
                error=f"Execution timeout after {timeout}s",
                time_ms=int((time.time() - start_time) * 1000),
            )

        elapsed_ms = int((time.time() - start_time) * 1000)

        return RunResult(
            success=process.returncode == 0,
            return_code=process.returncode,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            time_ms=elapsed_ms,
        )

    except FileNotFoundError:
        return RunResult(
            success=False,
            error=f"Binary not found or prlimit unavailable: {cmd[0]}",
        )
    except Exception as e:
        return RunResult(
            success=False,
            error=f"Execution error: {str(e)}",
        )


async def run_binary(
    binary_path: str,
    stdin: str = "",
    timeout: int = 5,
    memory_mb: int = 256,
) -> RunResult:
    """
    运行二进制文件，带超时和内存限制。

    Args:
        binary_path: 二进制文件路径
        stdin: 标准输入
        timeout: 超时时间（秒）
        memory_mb: 内存限制（MB），仅 Linux 有效

    Returns:
        RunResult: 执行结果
    """
    if not os.path.exists(binary_path):
        return RunResult(
            success=False,
            error=f"Binary not found: {binary_path}",
        )

    return await _run_process([binary_path], stdin, timeout, memory_mb)


async def run_binary_with_args(
    binary_path: str,
    args: list[str],
    stdin: str = "",
    timeout: int = 5,
    memory_mb: int = 256,
) -> RunResult:
    """
    运行二进制文件并传递命令行参数。

    Args:
        binary_path: 二进制文件路径
        args: 命令行参数列表
        stdin: 标准输入
        timeout: 超时时间（秒）
        memory_mb: 内存限制（MB），仅 Linux 有效

    Returns:
        RunResult: 执行结果
    """
    if not os.path.exists(binary_path):
        return RunResult(
            success=False,
            error=f"Binary not found: {binary_path}",
        )

    return await _run_process([binary_path, *args], stdin, timeout, memory_mb)


async def compile_all(
    problem_dir: str,
    sources: list[str],
    compiler: str = "g++",
) -> dict[str, CompileResult]:
    """
    批量编译多个源文件。

    Args:
        problem_dir: 题目目录
        sources: 源文件名列表（如 ["gen.cpp", "val.cpp", "sol.cpp", "brute.cpp"]）
        compiler: 编译器名称

    Returns:
        dict: 源文件名 -> 编译结果
    """
    results = {}
    exe_ext = get_exe_extension()

    for source in sources:
        source_path = os.path.join(problem_dir, source)
        if not os.path.exists(source_path):
            results[source] = CompileResult(
                success=False,
                error=f"Source file not found: {source}",
            )
            continue

        base_name = os.path.splitext(source)[0]
        binary_path = os.path.join(problem_dir, base_name + exe_ext)

        result = await compile_cpp(source_path, binary_path, compiler=compiler)
        results[source] = result

    return results
