"""
Solution 工具组 - 解法构建和运行。
"""
import os
import sys

from ..utils.compiler import compile_cpp, run_binary
from .base import Tool, ToolResult


class SolutionBuildTool(Tool):
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

        此工具不生成代码，代码由 Client LLM 生成后传入。
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
                "code": {
                    "type": "string",
                    "description": "解法的 C++ 代码",
                },
                "compiler": {
                    "type": "string",
                    "description": "编译器名称",
                    "default": "g++",
                },
            },
            "required": ["problem_dir", "solution_type", "code"],
        }

    async def execute(
        self,
        problem_dir: str,
        solution_type: str,
        code: str,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行解法构建。"""
        # 确保目录存在
        os.makedirs(problem_dir, exist_ok=True)

        # 确定文件名
        source_name = f"{solution_type}.cpp"
        source_path = os.path.join(problem_dir, source_name)

        # 保存代码
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        # 编译
        exe_ext = ".exe" if sys.platform == "win32" else ""
        binary_name = f"{solution_type}{exe_ext}"
        binary_path = os.path.join(problem_dir, binary_name)

        result = await compile_cpp(source_path, binary_path, compiler=compiler)

        if not result.success:
            return ToolResult.fail(
                f"Compilation failed: {result.error}",
                source_path=source_path,
                compile_log=result.stderr,
            )

        return ToolResult.ok(
            source_path=source_path,
            binary_path=binary_path,
            compile_log=result.stderr,
            message=f"Successfully built {solution_type}",
        )


class SolutionRunTool(Tool):
    """运行解法。"""

    @property
    def name(self) -> str:
        return "solution_run"

    @property
    def description(self) -> str:
        return """运行已编译的解法。

        在指定输入上运行解法，返回输出和执行时间。
        用于验证解法正确性或生成答案文件。
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
        solution_type: str,
        input_data: str,
        timeout: int = 30,
    ) -> ToolResult:
        """执行解法运行。"""
        # 确定二进制文件路径
        exe_ext = ".exe" if sys.platform == "win32" else ""
        binary_path = os.path.join(problem_dir, f"{solution_type}{exe_ext}")

        if not os.path.exists(binary_path):
            return ToolResult.fail(
                f"Binary not found: {solution_type}. "
                f"Please run solution_build first."
            )

        # 运行
        result = await run_binary(binary_path, input_data, timeout=timeout)

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
