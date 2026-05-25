"""
Mixin 模块 - 提供代码复用的工具基类。

用于减少工具类之间的重复代码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from ..utils.compiler import CompileResult, RunResult, compile_cpp, run_binary
from ..utils.resource_limit import get_resource_limit
from .base import ToolResult


@dataclass
class ResolvedSource:
    """解析后的源代码信息。"""

    code: str
    original_source_path: str | None  # 用户提供的源文件绝对路径
    include_dir: str | None  # 源文件目录（用于 -I 编译选项）
    from_source_path: bool  # True 表示通过 source_path 读取


def resolve_source(
    problem_dir: str,
    code: str | None,
    source_path: str | None,
    default_source_path: str | None = None,
) -> tuple[ResolvedSource | None, ToolResult | None]:
    """解析源代码来源：source_path 优先于 code，缺省时可读取标准文件。

    Returns:
        成功时返回 (ResolvedSource, None)，失败时返回 (None, ToolResult.fail(...))
    """
    original_source_path = None
    include_dir = None
    from_source_path = False

    if source_path or (code is None and default_source_path):
        source_path = source_path or default_source_path
        assert source_path is not None
        from_source_path = True
        if not os.path.isabs(source_path):
            source_path = os.path.join(problem_dir, source_path)
        if not os.path.exists(source_path):
            return None, ToolResult.fail(f"Source file not found: {source_path}")
        try:
            with open(source_path, encoding="utf-8") as f:
                code = f.read()
        except UnicodeDecodeError:
            try:
                with open(source_path, encoding="latin-1") as f:
                    code = f.read()
            except Exception as e:
                return None, ToolResult.fail(f"Failed to read source file: {e}")
        original_source_path = os.path.abspath(source_path)
        include_dir = os.path.dirname(original_source_path)
    elif code is None:
        return None, ToolResult.fail("Either 'code' or 'source_path' must be provided")

    return ResolvedSource(
        code=code,
        original_source_path=original_source_path,
        include_dir=include_dir,
        from_source_path=from_source_path,
    ), None


class BuildToolMixin:
    """构建工具 Mixin - 封装 compile_cpp。"""

    async def build(
        self,
        source_path: str,
        binary_path: str,
        compiler: str = "g++",
        std: str = "c++20",
        opt_level: str = "O2",
        timeout: int = 30,
        include_dirs: list[str] | None = None,
    ) -> CompileResult:
        return await compile_cpp(
            source_path,
            binary_path,
            timeout=timeout,
            compiler=compiler,
            std=std,
            opt_level=opt_level,
            include_dirs=include_dirs,
        )


class RunToolMixin:
    """运行工具 Mixin - 封装 run_binary 并自动应用资源限制。"""

    async def run(
        self,
        binary_path: str,
        input_data: str,
        problem_dir: str,
        solution_type: Literal["sol", "brute"],
        timeout: int | None = None,
        memory_mb: int | None = None,
    ) -> RunResult:
        limit = get_resource_limit(problem_dir, solution_type, timeout=timeout, memory_mb=memory_mb)
        return await run_binary(
            binary_path,
            input_data,
            timeout=limit.timeout_sec,
            memory_mb=limit.memory_mb,
        )
