"""
Solution 工具组 - 解法构建和运行。
"""

import os
import shutil
from typing import Literal

from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult
from .mixins import BuildToolMixin, RunToolMixin, resolve_source


def _is_safe_solution_stem(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return not any(sep and sep in value for sep in (os.sep, os.altsep, "/", "\\", ":"))


class SolutionBuildTool(Tool, BuildToolMixin):
    """构建并编译解法。"""

    @property
    def name(self) -> str:
        return "solution_build"

    @property
    def description(self) -> str:
        return """构建并编译解法代码。

        基于论文框架，支持：
        - sol.cpp: 标准解法（最优时间复杂度）
        - brute.cpp: 暴力解法（用于验证）

        前置条件：
        1. 已运行 problem_create 创建题目目录

        建议下一步：
        - 构建 brute.cpp 后运行 stress_test_run 验证解法正确性
        - 构建 sol.cpp 后运行 generator_build 构建数据生成器
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "problem_dir": {
                    "type": "string",
                    "description": "题目目录路径",
                },
                "solution_type": {
                    "type": "string",
                    "enum": ["sol", "brute"],
                    "description": "解法类型：sol（标准解法）或 brute（暴力解法）",
                },
                "name": {
                    "type": "string",
                    "description": "自定义文件名（不含扩展名），默认使用 solution_type。例如 'brute_force' 替代 'brute'",
                },
                "code": {
                    "type": "string",
                    "description": "C++ 源代码（可选；缺省时读取 solutions/{solution_type}.cpp）",
                },
                "source_path": {
                    "type": "string",
                    "description": "源文件路径，相对于 problem_dir 或绝对路径。优先级高于 code",
                },
                "compiler": {
                    "type": "string",
                    "description": "编译器名称",
                    "default": "g++",
                },
            },
            "required": ["problem_dir", "solution_type"],
        }

    async def execute(
        self,
        problem_dir: str,
        solution_type: Literal["sol", "brute"],
        name: str | None = None,
        code: str | None = None,
        source_path: str | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行解法构建。"""
        if solution_type not in {"sol", "brute"}:
            return ToolResult.fail("solution_type must be 'sol' or 'brute'")
        if name is not None and not _is_safe_solution_stem(name):
            return ToolResult.fail("name must be a file name stem without path separators")

        resolved, err = resolve_source(
            problem_dir,
            code,
            source_path,
            default_source_path=os.path.join("solutions", f"{solution_type}.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None

        # 确保目录存在
        os.makedirs(problem_dir, exist_ok=True)
        solutions_dir = os.path.join(problem_dir, "solutions")
        os.makedirs(solutions_dir, exist_ok=True)

        # 确定文件名
        effective_name = name or solution_type
        exe_ext = get_exe_extension()
        canonical_path = os.path.join(solutions_dir, f"{effective_name}.cpp")
        binary_path = os.path.join(solutions_dir, f"{effective_name}{exe_ext}")
        standard_source_path = os.path.join(solutions_dir, f"{solution_type}.cpp")
        standard_binary_path = os.path.join(solutions_dir, f"{solution_type}{exe_ext}")

        # 保存自定义命名文件，并保留 sol.cpp/brute.cpp 供打包和默认流程使用。
        try:
            with open(canonical_path, "w", encoding="utf-8") as f:
                f.write(resolved.code)
            if os.path.normcase(canonical_path) != os.path.normcase(standard_source_path):
                shutil.copy2(canonical_path, standard_source_path)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        # 编译：source_path 时从原始文件编译，否则从标准位置编译
        compile_source = resolved.original_source_path or canonical_path
        include_dirs = [resolved.include_dir] if resolved.include_dir else None
        result = await self.build(compile_source, binary_path, compiler=compiler, include_dirs=include_dirs)

        if not result.success:
            return ToolResult.fail(
                f"Compilation failed: {result.error}",
                source_path=compile_source,
                canonical_path=canonical_path,
                compile_log=result.stderr,
            )

        binary_size = os.path.getsize(binary_path) if os.path.exists(binary_path) else 0
        if os.path.normcase(binary_path) != os.path.normcase(standard_binary_path):
            try:
                shutil.copy2(binary_path, standard_binary_path)
            except Exception as e:
                return ToolResult.fail(f"Failed to save standard binary: {str(e)}")

        return ToolResult.ok(
            source_path=compile_source,
            canonical_path=canonical_path,
            standard_source_path=standard_source_path,
            binary_path=binary_path,
            standard_binary_path=standard_binary_path,
            binary_size=binary_size,
            compile_log=result.stderr,
            effective_name=effective_name,
            message=f"Successfully built {effective_name}",
        )


class SolutionRunTool(Tool, RunToolMixin):
    """运行解法。"""

    @property
    def name(self) -> str:
        return "solution_run"

    @property
    def description(self) -> str:
        return """运行已编译的解法。

        在指定输入上运行解法，返回输出和执行时间。
        用于验证解法正确性或生成答案文件。

        前置条件：
        1. 已运行 solution_build 构建解法

        建议下一步：
        - 如果验证通过，继续构建 generator
        - 如果验证失败，修复解法代码
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "problem_dir": {
                    "type": "string",
                    "description": "题目目录路径",
                },
                "solution_type": {
                    "type": "string",
                    "enum": ["sol", "brute"],
                    "description": "解法类型：sol 或 brute",
                },
                "name": {
                    "type": "string",
                    "description": "自定义文件名（不含扩展名），默认使用 solution_type",
                },
                "input_data": {
                    "type": "string",
                    "description": "输入数据",
                },
                "timeout": {
                    "type": "integer",
                    "description": "执行超时（秒）",
                    "default": 30,
                },
            },
            "required": ["problem_dir", "solution_type", "input_data"],
        }

    async def execute(
        self,
        problem_dir: str,
        solution_type: Literal["sol", "brute"],
        input_data: str,
        name: str | None = None,
        timeout: int = 30,
    ) -> ToolResult:
        """执行解法运行。"""
        effective_name = name or solution_type
        exe_ext = get_exe_extension()
        binary_path = os.path.join(problem_dir, "solutions", f"{effective_name}{exe_ext}")

        # 如果子目录没有，检查根目录（向后兼容）
        if not os.path.exists(binary_path):
            binary_path = os.path.join(problem_dir, f"{effective_name}{exe_ext}")

        if not os.path.exists(binary_path):
            return ToolResult.fail(
                f"Binary not found: {effective_name}. Please run solution_build first."
            )

        # 运行
        result = await self.run(
            binary_path, input_data, problem_dir, solution_type, timeout=timeout
        )

        if result.timed_out:
            return ToolResult.fail(
                f"Execution timed out after {timeout}s",
                stdout=result.stdout,
                stderr=result.stderr,
                time_ms=result.time_ms,
            )

        if not result.success:
            return ToolResult.fail(
                f"Execution failed with return code {result.return_code}",
                stdout=result.stdout,
                stderr=result.stderr,
                time_ms=result.time_ms,
            )

        return ToolResult.ok(
            stdout=result.stdout,
            stderr=result.stderr,
            time_ms=result.time_ms,
            return_code=result.return_code,
        )
