"""
编译和执行工具函数。

提供安全的 C++ 编译和执行能力，包含：
- 超时控制
- 资源限制
- 临时目录隔离
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .. import TEMPLATES_DIR
from .cache import CompileCache
from .platform import get_exe_extension

if TYPE_CHECKING:
    from .win_job import WinJobObject

# 模块级日志器
_logger = logging.getLogger(__name__)

_cache = CompileCache()


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
    std: str = "c++20",
    opt_level: str = "O2",
    include_dirs: list[str] | None = None,
    use_cache: bool = True,
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
        use_cache: 是否使用编译缓存

    Returns:
        CompileResult: 编译结果
    """
    if not os.path.exists(source_path):
        return CompileResult(
            success=False,
            error=f"Source file not found: {source_path}",
        )

    if use_cache:
        cached_binary = _cache.get(source_path, compiler, std, opt_level)
        if cached_binary:
            output_dir = os.path.dirname(binary_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            shutil.copy2(cached_binary, binary_path)
            return CompileResult(
                success=True,
                binary_path=binary_path,
                stderr="(cached)",
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

    # Windows 上使用静态链接避免 DLL 版本冲突（特别是 testlib.h 程序）
    if sys.platform == "win32":
        cmd.append("-static")

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
                error=f"Compilation timeout after {timeout}s. Consider increasing timeout or simplifying source code.",
            )

        if process.returncode != 0:
            return CompileResult(
                success=False,
                error=f"Compilation failed with code {process.returncode}",
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
            )

        if use_cache:
            _cache.set(source_path, binary_path, compiler, std, opt_level)

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


def _set_macos_resource_limit(memory_mb: int) -> None:
    """macOS 上设置进程资源限制（通过 preexec_fn 调用）。

    Args:
        memory_mb: 内存限制（MB）

    Note:
        使用 preexec_fn 与 asyncio 存在潜在死锁风险（在极端情况下），
        但实际触发概率极低。这是 macOS 上设置资源限制的标准方式。
    """
    import resource

    memory_bytes = memory_mb * 1024 * 1024
    # 设置虚拟内存限制 (address space)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))  # type: ignore[attr-defined, unused-ignore]
    except (ValueError, OSError) as e:
        _logger.debug("Failed to set RLIMIT_AS on macOS: %s", e)

    # 设置数据段大小限制
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (memory_bytes, memory_bytes))  # type: ignore[attr-defined, unused-ignore]
    except (ValueError, OSError) as e:
        _logger.debug("Failed to set RLIMIT_DATA on macOS: %s", e)


async def _force_terminate_process(
    process: asyncio.subprocess.Process,
    job: WinJobObject | None = None,
) -> None:
    """强制终止进程，确保不会卡住。

    Args:
        process: 要终止的进程
        job: Windows Job Object（可选）
    """
    # 先尝试 Job Object 终止（Windows 上更可靠）
    if job:
        try:
            job.terminate()
        except OSError as e:
            _logger.debug("Job object terminate failed: %s", e)

    # 再尝试正常终止
    try:
        process.kill()
    except ProcessLookupError:
        # 进程已经不存在
        return
    except OSError as e:
        _logger.debug("Failed to kill process: %s", e)

    # 等待进程退出，设置超时防止卡住
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except TimeoutError:
        # 如果 2 秒后进程仍未退出，再次尝试强制终止
        try:
            process.kill()
        except ProcessLookupError:
            return
        except OSError as e:
            _logger.debug("Second kill attempt failed: %s", e)


async def _run_process(
    cmd: list[str],
    stdin: str = "",
    timeout: int = 5,
    memory_mb: int = 256,
    process_start_hook: Callable[[int], None] | None = None,
) -> RunResult:
    """运行进程的公共逻辑。"""
    import time

    start_time = time.time()
    job = None
    process = None

    try:
        if sys.platform == "win32":
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if process.pid:
                try:
                    from .win_job import WinJobObject

                    job = WinJobObject(memory_mb=memory_mb, timeout_sec=timeout)
                    job.assign_process(process.pid)
                except (OSError, RuntimeError) as e:
                    # Job Object 创建/分配失败，记录日志并继续（进程仍可运行）
                    _logger.warning(
                        "Failed to setup Windows Job Object (pid=%s, cmd=%s): %s",
                        process.pid,
                        cmd[0] if cmd else "",
                        e,
                    )
                    job = None
        elif sys.platform == "darwin":
            # macOS: 使用 preexec_fn 设置资源限制
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=lambda: _set_macos_resource_limit(memory_mb),
            )
        else:
            # Linux: 使用 prlimit 设置资源限制
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

        if process and process.pid and process_start_hook:
            try:
                process_start_hook(process.pid)
            except Exception as e:
                _logger.debug("process_start_hook failed: %s", e)

        try:
            # Windows 上 testlib strict 模式期望 CRLF 换行符
            # 将 LF 转换为 CRLF 以满足 validator 的 readEoln() 要求
            processed_stdin = stdin
            if sys.platform == "win32" and stdin:
                # 避免重复转换：先还原已有的 CRLF，再将所有 LF 转为 CRLF
                processed_stdin = stdin.replace("\r\n", "\n").replace("\n", "\r\n")

            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=processed_stdin.encode("utf-8") if processed_stdin else None),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            # 调用被取消时也必须强制清理子进程，防止残留。
            await _force_terminate_process(process, job)
            raise
        except TimeoutError:
            # 超时时强制终止进程
            await _force_terminate_process(process, job)
            return RunResult(
                success=False,
                timed_out=True,
                error=f"Execution timeout after {timeout}s. The program may contain an infinite loop or the input data may be too large.",
                time_ms=int((time.time() - start_time) * 1000),
            )

        elapsed_ms = int((time.time() - start_time) * 1000)

        # 正常完成后关闭 Job Handle；若 Job 配置了 KILL_ON_JOB_CLOSE，
        # 关闭时仍可能终止 Job 中尚未退出的子进程。
        if job:
            job.close()

        return RunResult(
            success=process.returncode == 0,
            return_code=process.returncode if process.returncode is not None else -1,
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
        # 最后防线：捕获所有异常确保进程被终止，防止资源泄漏
        if process:
            await _force_terminate_process(process, job)
        return RunResult(
            success=False,
            error=f"Execution error: {str(e)}",
        )


async def run_binary(
    binary_path: str,
    stdin: str = "",
    timeout: int = 5,
    memory_mb: int = 256,
    process_start_hook: Callable[[int], None] | None = None,
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

    return await _run_process([binary_path], stdin, timeout, memory_mb, process_start_hook)


async def run_binary_with_args(
    binary_path: str,
    args: list[str],
    stdin: str = "",
    timeout: int = 5,
    memory_mb: int = 256,
    process_start_hook: Callable[[int], None] | None = None,
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

    return await _run_process(
        [binary_path, *args],
        stdin,
        timeout,
        memory_mb,
        process_start_hook,
    )


async def compile_all(
    problem_dir: str,
    sources: list[str],
    compiler: str = "g++",
    max_concurrent: int = 4,
) -> dict[str, CompileResult]:
    """
    并发编译多个源文件。

    Args:
        problem_dir: 题目目录
        sources: 源文件名列表（如 ["gen.cpp", "val.cpp", "sol.cpp", "brute.cpp"]）
        compiler: 编译器名称
        max_concurrent: 最大并发编译数

    Returns:
        dict: 源文件名 -> 编译结果
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    exe_ext = get_exe_extension()

    async def compile_one(source: str) -> tuple[str, CompileResult]:
        async with semaphore:
            source_path = os.path.join(problem_dir, source)
            if not os.path.exists(source_path):
                return source, CompileResult(
                    success=False,
                    error=f"Source file not found: {source}",
                )
            base_name = os.path.splitext(source)[0]
            binary_path = os.path.join(problem_dir, base_name + exe_ext)
            result = await compile_cpp(source_path, binary_path, compiler=compiler)
            return source, result

    tasks = [compile_one(src) for src in sources]
    results = await asyncio.gather(*tasks)
    return dict(results)
