"""
Mixin 模块 - 提供代码复用的工具基类。

用于减少工具类之间的重复代码。
"""

from __future__ import annotations

from ..utils.compiler import CompileResult, compile_cpp, run_binary
from ..utils.resource_limit import get_resource_limit


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
    ) -> CompileResult:
        return await compile_cpp(
            source_path,
            binary_path,
            timeout=timeout,
            compiler=compiler,
            std=std,
            opt_level=opt_level,
        )


class RunToolMixin:
    """运行工具 Mixin - 封装 run_binary 并自动应用资源限制。"""

    async def run(
        self,
        binary_path: str,
        input_data: str,
        problem_dir: str,
        solution_type: str,
        timeout: int | None = None,
        memory_mb: int | None = None,
    ):
        limit = get_resource_limit(problem_dir, solution_type, timeout=timeout, memory_mb=memory_mb)
        return await run_binary(
            binary_path,
            input_data,
            timeout=limit.timeout_sec,
            memory_mb=limit.memory_mb,
        )
