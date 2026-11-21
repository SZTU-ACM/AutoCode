"""
编译和执行工具函数。

提供安全的 C++ 编译和执行能力，包含：
- 超时控制
- 资源限制
- 临时目录隔离
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .. import TEMPLATES_DIR
from .cache import CompileCache
from .platform import get_exe_extension
from .process import is_pid_alive, terminate_pid_tree

if TYPE_CHECKING:
    from .win_job import WinJobObject

# 模块级日志器
_logger = logging.getLogger(__name__)

_cache = CompileCache()


def _normalize_windows_stdin(stdin: str) -> str:
    """规范化 Windows 下传入程序的文本换行。

    先将 CRLF/CR 统一成 LF，再一次性转为 CRLF，避免重复转换导致的异常输入形态。
    """
    if not stdin:
        return stdin
    normalized = stdin.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", "\r\n")


@dataclass
class CompileResult:
    """编译结果。"""

    success: bool
    binary_path: str | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""


@dataclass
class BuildSpec:
    """单个源文件的并发编译规格（供 ``compile_all`` 使用）。"""

    source: str
    binary: str | None = None
    include_dirs: list[str] | None = None
    extra_sources: list[str] | None = None
    compiler: str = "g++"
    std: str = "c++20"
    opt_level: str = "O2"
    timeout: int = 30


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
    memory_limit_exceeded: bool = False


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


@functools.lru_cache(maxsize=8)
def _resolve_compiler(compiler: str) -> str:
    """Resolve `g++` on macOS away from Apple Clang toward Homebrew GCC if installed.

    Apple's `/usr/bin/g++` is a Clang shim lacking GNU `<bits/stdc++.h>`. When the
    caller requests the default `"g++"` on darwin, probe `g++-15`..`g++-10` via
    PATH and standard Homebrew prefixes and return the first working GCC binary.
    """
    if sys.platform != "darwin" or compiler != "g++":
        return compiler
    resolved = shutil.which(compiler) or compiler
    try:
        probe = subprocess.run(
            [resolved, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        banner = f"{probe.stdout}\n{probe.stderr}".lower()
        if "clang" not in banner:
            return compiler
    except (OSError, subprocess.SubprocessError):
        return compiler

    for candidate in ("g++-15", "g++-14", "g++-13", "g++-12", "g++-11", "g++-10"):
        found = shutil.which(candidate)
        if found:
            return found
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            full_path = os.path.join(prefix, candidate)
            if os.path.isfile(full_path) and os.access(full_path, os.X_OK):
                return full_path
    return compiler


def _has_local_headers(
    include_dirs: list[str] | None,
    source_path: str | None = None,
) -> bool:
    """Check whether any include directory or source directory contains local C/C++ headers."""
    dirs_to_check: list[str] = list(include_dirs or [])
    if source_path:
        src_dir = os.path.dirname(os.path.abspath(source_path))
        if src_dir:
            dirs_to_check.append(src_dir)
    if not dirs_to_check:
        return False
    templates_real = os.path.realpath(TEMPLATES_DIR) if os.path.exists(TEMPLATES_DIR) else ""
    header_exts = (".h", ".hpp", ".hh", ".hxx")
    seen_real: set[str] = set()
    for inc_dir in dirs_to_check:
        if not inc_dir or not os.path.isdir(inc_dir):
            continue
        real_dir = os.path.normcase(os.path.realpath(inc_dir))
        if real_dir in seen_real:
            continue
        seen_real.add(real_dir)
        if templates_real and real_dir == os.path.normcase(templates_real):
            continue
        try:
            for entry in os.listdir(inc_dir):
                lower = entry.lower()
                if lower.endswith(header_exts) and lower != "testlib.h":
                    return True
        except OSError:
            return True
    return False


async def compile_cpp(
    source_path: str,
    binary_path: str,
    timeout: int = 30,
    compiler: str = "g++",
    std: str = "c++20",
    opt_level: str = "O2",
    include_dirs: list[str] | None = None,
    extra_sources: list[str] | None = None,
    use_cache: bool = True,
) -> CompileResult:
    """
    编译 C++ 文件（支持多文件链接，如 grader_linked 通信题），带超时控制。

    Args:
        source_path: 主源文件路径
        binary_path: 输出二进制文件路径
        timeout: 超时时间（秒）
        compiler: 编译器名称
        std: C++ 标准
        opt_level: 优化级别
        include_dirs: 额外的 include 目录列表
        extra_sources: 一起编译链接的附加源文件列表（如 files/grader.cpp）
        use_cache: 是否使用编译缓存（多文件或存在本地头文件时自动绕过缓存避免陈旧命中）

    Returns:
        CompileResult: 编译结果
    """
    if not os.path.exists(source_path):
        return CompileResult(
            success=False,
            error=f"Source file not found: {source_path}",
        )

    normalized_extra = list(extra_sources or [])
    for extra_src in normalized_extra:
        if not os.path.exists(extra_src):
            return CompileResult(
                success=False,
                error=f"Extra source file not found: {extra_src}",
            )

    effective_compiler = _resolve_compiler(compiler)

    effective_use_cache = (
        use_cache and not normalized_extra and not _has_local_headers(include_dirs, source_path)
    )

    if effective_use_cache:
        cached_binary = _cache.get(source_path, effective_compiler, std, opt_level)
        if cached_binary:
            output_dir = os.path.dirname(binary_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            shutil.copy2(cached_binary, binary_path)
            if sys.platform != "win32":
                try:
                    os.chmod(binary_path, os.stat(binary_path).st_mode | 0o111)
                except OSError:
                    pass
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
        include_flags.extend(["-isystem", templates_dir])

    cmd = [
        effective_compiler,
        f"-std={std}",
        f"-{opt_level}",
        "-Wall",
        "-Wextra",
        *include_flags,
        source_path,
        *normalized_extra,
        "-o",
        binary_path,
    ]

    # Windows 上使用静态链接避免 DLL 版本冲突（特别是 testlib.h 程序）
    if sys.platform == "win32":
        cmd.append("-static")

    popen_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **popen_kwargs,
        )

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError, asyncio.CancelledError) as e:
            if process.pid:
                await terminate_pid_tree(process.pid)
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except Exception:
                pass
            if isinstance(e, asyncio.CancelledError):
                raise
            return CompileResult(
                success=False,
                error=f"Compilation timeout after {timeout}s. Consider increasing timeout or simplifying source code.",
            )

        if process.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")
            if sys.platform == "darwin" and "bits/stdc++.h" in err_text:
                err_text = (
                    f"{err_text}\n"
                    "Hint (macOS): Apple Clang does not include <bits/stdc++.h>. "
                    "Install Homebrew GCC (`brew install gcc`) so `g++-14`/`g++-13` is available."
                )
            return CompileResult(
                success=False,
                error=f"Compilation failed with code {process.returncode}",
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=err_text,
            )

        if effective_use_cache:
            _cache.set(source_path, binary_path, effective_compiler, std, opt_level)

        return CompileResult(
            success=True,
            binary_path=binary_path,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )

    except FileNotFoundError:
        return CompileResult(
            success=False,
            error=f"Compiler not found: {effective_compiler}",
        )
    except Exception as e:
        return CompileResult(
            success=False,
            error=f"Compilation error: {str(e)}",
        )


def _clamp_rlimit_bytes(memory_bytes: int, limit_attr: str) -> int:
    """将请求的内存上限钳制到当前进程的 POSIX hard limit 以内，避免 EPERM/ValueError。"""
    import resource

    limit_const = getattr(resource, limit_attr, None)
    if limit_const is None:
        return memory_bytes
    try:
        _, cur_hard = cast(Any, resource).getrlimit(limit_const)
    except (ValueError, OSError):
        return memory_bytes
    rlim_inf = getattr(resource, "RLIM_INFINITY", -1)
    if cur_hard != rlim_inf and cur_hard >= 0:
        return min(memory_bytes, int(cur_hard))
    return memory_bytes


def _set_posix_resource_limit(memory_mb: int | float) -> None:
    """POSIX 上设置进程资源限制（通过 preexec_fn 调用）。"""
    import resource

    requested_bytes = max(1, int(round(float(memory_mb) * 1024 * 1024)))
    for limit_attr in ("RLIMIT_AS", "RLIMIT_DATA"):
        limit_const = getattr(resource, limit_attr, None)
        if limit_const is None:
            continue
        clamped = _clamp_rlimit_bytes(requested_bytes, limit_attr)
        try:
            cast(Any, resource).setrlimit(limit_const, (clamped, clamped))
        except (ValueError, OSError) as e:
            _logger.debug("Failed to set %s on POSIX: %s", limit_attr, e)


def _set_macos_resource_limit(memory_mb: int | float) -> None:
    """macOS 上设置进程资源限制（兼容入口，委托给 POSIX 实现）。"""
    _set_posix_resource_limit(memory_mb)


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
        except (OSError, RuntimeError) as e:
            _logger.debug("Job object terminate failed: %s", e)

    already_gone = False
    try:
        if process.pid:
            await terminate_pid_tree(process.pid)
    finally:
        # 再尝试直接 kill；即使 terminate_pid_tree 被取消也确保清理子进程句柄。
        try:
            process.kill()
        except ProcessLookupError:
            already_gone = True
        except OSError as e:
            _logger.debug("Failed to kill process: %s", e)

    if already_gone:
        return

    # 等待进程退出，设置超时防止卡住
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except (asyncio.TimeoutError, TimeoutError):
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
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    process_start_hook: Callable[[int], None] | None = None,
) -> RunResult:
    """运行进程的公共逻辑。"""
    import tempfile

    start_time = time.monotonic()
    safe_memory_mb = max(1, int(round(float(memory_mb))))
    job = None
    process = None
    out_f = err_f = None
    stdout_path = stderr_path = None

    try:
        # 大输出重定向到临时文件，避免全量读入内存：运行期间输出由 OS 落盘，
        # 峰值内存仅受单次写入块大小限制，而非整份输出。
        out_f = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".autocode.out")
        err_f = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".autocode.err")
        stdout_path, stderr_path = out_f.name, err_f.name

        if sys.platform == "win32":
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
            )
            if process.pid:
                try:
                    from .win_job import WinJobObject

                    job = WinJobObject(memory_mb=safe_memory_mb, timeout_sec=timeout)
                    job.assign_process(process.pid)
                except (OSError, RuntimeError, TypeError) as e:
                    if job is not None:
                        job.close()
                        job = None
                    err_msg = str(e)
                    is_expected_short_lived = (
                        not is_pid_alive(process.pid)
                        or "Access is denied" in err_msg
                        or "拒绝访问" in err_msg
                        or "(5," in err_msg
                    )
                    log_fn = _logger.debug if is_expected_short_lived else _logger.warning
                    log_fn(
                        "Failed to setup Windows Job Object (pid=%s, cmd=%s): %s",
                        process.pid,
                        cmd[0] if cmd else "",
                        e,
                    )
        elif sys.platform == "darwin":
            # macOS: 使用 preexec_fn 设置资源限制；start_new_session 使子进程成为
            # 独立进程组 leader，便于超时/取消/清理时以 os.killpg 整树回收。
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=out_f,
                stderr=err_f,
                preexec_fn=lambda: _set_posix_resource_limit(safe_memory_mb),
                start_new_session=True,
            )
        else:
            # Linux: 优先使用 prlimit（钳制到 hard limit）；缺失时回退 preexec_fn。
            memory_bytes = safe_memory_mb * 1024 * 1024
            prlimit_path = shutil.which("prlimit")
            if prlimit_path:
                as_bytes = _clamp_rlimit_bytes(memory_bytes, "RLIMIT_AS")
                data_bytes = _clamp_rlimit_bytes(memory_bytes, "RLIMIT_DATA")
                process = await asyncio.create_subprocess_exec(
                    prlimit_path,
                    f"--as={as_bytes}",
                    f"--data={data_bytes}",
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=out_f,
                    stderr=err_f,
                    start_new_session=True,
                )
            else:
                exec_kwargs: dict[str, Any] = {
                    "stdin": asyncio.subprocess.PIPE,
                    "stdout": out_f,
                    "stderr": err_f,
                    "start_new_session": True,
                }
                if os.name != "nt":
                    exec_kwargs["preexec_fn"] = lambda: _set_posix_resource_limit(safe_memory_mb)
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    **exec_kwargs,
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
                processed_stdin = _normalize_windows_stdin(stdin)

            await asyncio.wait_for(
                process.communicate(input=(processed_stdin or "").encode("utf-8")),
                timeout=timeout,
            )
        except asyncio.CancelledError:
            # 调用被取消时也必须强制清理子进程，防止残留。
            await _force_terminate_process(process, job)
            raise
        except (asyncio.TimeoutError, TimeoutError):
            # 超时时强制终止进程
            await _force_terminate_process(process, job)
            for _timeout_fh in (out_f, err_f):
                if _timeout_fh is not None:
                    try:
                        _timeout_fh.close()
                    except OSError:
                        pass
            out_str = ""
            err_str = ""
            if stdout_path and os.path.exists(stdout_path):
                try:
                    with open(stdout_path, "rb") as _out:
                        out_str = _out.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
                except OSError:
                    pass
            if stderr_path and os.path.exists(stderr_path):
                try:
                    with open(stderr_path, "rb") as _err:
                        err_str = _err.read(10 * 1024 * 1024).decode("utf-8", errors="replace")
                except OSError:
                    pass
            return RunResult(
                success=False,
                timed_out=True,
                error=f"Execution timeout after {timeout}s. The program may contain an infinite loop or the input data may be too large.",
                stdout=out_str,
                stderr=err_str,
                time_ms=int((time.monotonic() - start_time) * 1000),
            )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # 正常完成后关闭 Job Handle；若 Job 配置了 KILL_ON_JOB_CLOSE，
        # 关闭时仍可能终止 Job 中尚未退出的子进程。
        if job:
            job.close()

        out_f.close()
        err_f.close()
        with open(stdout_path, "rb") as _out, open(stderr_path, "rb") as _err:
            stdout_data = _out.read(10 * 1024 * 1024)
            stderr_data = _err.read(10 * 1024 * 1024)
        ret = process.returncode if process.returncode is not None else -1
        out_str = stdout_data.decode("utf-8", errors="replace")
        err_str = stderr_data.decode("utf-8", errors="replace")
        mle = False
        if ret in (-9, 137, 3221225495, -1073741801) or (
            hasattr(signal, "SIGKILL") and ret == -signal.SIGKILL
        ):
            mle = True
        elif "bad_alloc" in err_str or "Out of memory" in err_str:
            mle = True
        return RunResult(
            success=ret == 0,
            return_code=ret,
            stdout=out_str,
            stderr=err_str,
            time_ms=elapsed_ms,
            memory_limit_exceeded=mle,
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
    finally:
        for _fh in (out_f, err_f):
            if _fh is not None:
                try:
                    _fh.close()
                except OSError:
                    pass
        for _p in (stdout_path, stderr_path):
            if _p and os.path.exists(_p):
                try:
                    os.remove(_p)
                except OSError:
                    pass


async def run_binary(
    binary_path: str,
    stdin: str = "",
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    process_start_hook: Callable[[int], None] | None = None,
) -> RunResult:
    """
    运行二进制文件，带超时和内存限制。

    Args:
        binary_path: 二进制文件路径
        stdin: 标准输入
        timeout: 超时时间（秒）
        memory_mb: 内存限制（MB），仅 Linux/macOS/Windows 支持

    Returns:
        RunResult: 执行结果
    """
    if not os.path.exists(binary_path):
        return RunResult(
            success=False,
            error=f"Binary not found: {binary_path}",
        )

    abs_bin = os.path.abspath(binary_path)
    return await _run_process([abs_bin], stdin, timeout, memory_mb, process_start_hook)


async def run_binary_with_args(
    binary_path: str,
    args: list[str],
    stdin: str = "",
    timeout: int | float = 5,
    memory_mb: int | float = 256,
    process_start_hook: Callable[[int], None] | None = None,
) -> RunResult:
    """
    运行二进制文件并传递命令行参数。

    Args:
        binary_path: 二进制文件路径
        args: 命令行参数列表
        stdin: 标准输入
        timeout: 超时时间（秒）
        memory_mb: 内存限制（MB），仅 Linux/macOS/Windows 支持

    Returns:
        RunResult: 执行结果
    """
    if not os.path.exists(binary_path):
        return RunResult(
            success=False,
            error=f"Binary not found: {binary_path}",
        )

    abs_bin = os.path.abspath(binary_path)
    return await _run_process(
        [abs_bin, *args],
        stdin,
        timeout,
        memory_mb,
        process_start_hook,
    )


T = TypeVar("T")
R = TypeVar("R")


async def run_batch(
    items: list[T],
    worker: Callable[[T], Awaitable[R]],
    limit: int = 4,
) -> list[R]:
    """使用有上限的并发运行 ``worker``，保持输入顺序。

    用于把原本严格串行的多子进程步骤（生成/校验/对拍等）改为有限并发，
    在不改变单步正确性、超时与失败语义的前提下提升吞吐。

    Args:
        items: 待处理的输入项列表
        worker: 单项的异步处理函数
        limit: 最大并发数（<=0 时退化为 1）

    Returns:
        与 ``items`` 顺序一致的 ``worker`` 返回值列表
    """
    safe_limit = max(1, int(limit))
    if not items:
        return []
    if safe_limit >= len(items):
        return await asyncio.gather(*(worker(it) for it in items))
    semaphore = asyncio.Semaphore(safe_limit)

    async def bounded(item: T) -> R:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(bounded(it) for it in items))


async def compile_all(
    problem_dir: str,
    specs: list[str | BuildSpec],
    max_concurrent: int = 4,
) -> dict[str, CompileResult]:
    """
    并发编译多个源文件。

    源路径相对于 ``problem_dir``（或绝对路径）。默认输出二进制与源文件同目录、
    同名换扩展名；可通过 :class:`BuildSpec` 指定输出路径、include 目录与编译选项。

    Args:
        problem_dir: 题目目录（用于解析相对路径与默认输出目录）
        specs: 源文件名或 :class:`BuildSpec` 列表（如 ["gen.cpp", "val.cpp"]）
        max_concurrent: 最大并发编译数

    Returns:
        dict: 传入的源名/规格 source -> 编译结果
    """
    semaphore = asyncio.Semaphore(max(1, max_concurrent))
    exe_ext = get_exe_extension()

    def _resolve(spec: str | BuildSpec) -> BuildSpec:
        if isinstance(spec, str):
            return BuildSpec(source=spec)
        return spec

    async def compile_one(spec: BuildSpec) -> tuple[str, CompileResult]:
        async with semaphore:
            source_key = spec.source
            source_path = spec.source if os.path.isabs(spec.source) else os.path.join(problem_dir, spec.source)
            if not os.path.exists(source_path):
                return source_key, CompileResult(
                    success=False,
                    error=f"Source file not found: {source_path}",
                )
            if spec.binary:
                binary_path = spec.binary if os.path.isabs(spec.binary) else os.path.join(problem_dir, spec.binary)
            else:
                binary_path = os.path.splitext(source_path)[0] + exe_ext
            resolved_extra = (
                [
                    p if os.path.isabs(p) else os.path.join(problem_dir, p)
                    for p in spec.extra_sources
                ]
                if spec.extra_sources
                else None
            )
            result = await compile_cpp(
                source_path,
                binary_path,
                timeout=spec.timeout,
                compiler=spec.compiler,
                std=spec.std,
                opt_level=spec.opt_level,
                include_dirs=spec.include_dirs,
                extra_sources=resolved_extra,
            )
            return source_key, result

    tasks = [compile_one(_resolve(s)) for s in specs]
    results = await asyncio.gather(*tasks)
    return dict(results)
