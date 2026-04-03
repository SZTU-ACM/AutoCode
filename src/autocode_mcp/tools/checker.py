"""
Checker 工具组 - 输出检查器。

基于论文 Algorithm 3: BUILDCHECKER 实现。
"""

import os

from ..utils.compiler import run_binary_with_args
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult
from .mixins import BuildToolMixin


class CheckerBuildTool(Tool, BuildToolMixin):
    """构建并验证输出检查器。"""

    @property
    def name(self) -> str:
        return "checker_build"

    @property
    def description(self) -> str:
        return """构建并验证输出检查器。

        基于论文 Algorithm 3 实现:
        1. 保存代码到 problem_dir/checker.cpp
        2. 编译生成 checker.exe
        3. 运行测试场景验证准确性
        4. 返回准确率和详细结果

        注意：此工具不生成代码，代码由 Client LLM 生成后传入。
        测试场景也应由 Client LLM 生成（40 个场景）。
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
                "code": {
                    "type": "string",
                    "description": "Checker C++ 代码（基于 testlib.h）",
                },
                "test_scenarios": {
                    "type": "array",
                    "description": "测试场景列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "input": {"type": "string"},
                            "contestant_output": {"type": "string"},
                            "reference_output": {"type": "string"},
                            "expected_verdict": {
                                "type": "string",
                                "enum": ["AC", "WA", "PE"],
                            },
                        },
                        "required": ["input", "contestant_output", "reference_output"],
                    },
                },
                "compiler": {
                    "type": "string",
                    "description": "编译器名称",
                    "default": "g++",
                },
            },
            "required": ["problem_dir", "code"],
        }

    async def execute(
        self,
        problem_dir: str,
        code: str,
        test_scenarios: list[dict] | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行 Checker 构建。"""
        os.makedirs(problem_dir, exist_ok=True)

        # 保存代码
        source_path = os.path.join(problem_dir, "checker.cpp")
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        # 编译
        binary_path = os.path.join(problem_dir, f"checker{get_exe_extension()}")

        compile_result = await self.build(source_path, binary_path, compiler=compiler)

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=source_path,
                compile_log=compile_result.stderr,
            )

        # 如果没有测试场景，直接返回成功
        if not test_scenarios:
            return ToolResult.ok(
                source_path=source_path,
                binary_path=binary_path,
                compile_log=compile_result.stderr,
                message="Checker built successfully (no test scenarios provided)",
            )

        # 运行测试场景
        # Checker 调用方式: checker.exe <input> <output> <answer>
        # 需要临时文件
        import tempfile

        test_results = []
        correct_count = 0

        with tempfile.TemporaryDirectory(dir=problem_dir) as temp_dir:
            for i, scenario in enumerate(test_scenarios):
                input_data = scenario.get("input", "")
                contestant_output = scenario.get("contestant_output", "")
                reference_output = scenario.get("reference_output", "")
                expected_verdict = scenario.get("expected_verdict", "AC")

                # 写入临时文件
                input_file = os.path.join(temp_dir, f"input_{i}.txt")
                output_file = os.path.join(temp_dir, f"output_{i}.txt")
                answer_file = os.path.join(temp_dir, f"answer_{i}.txt")

                with open(input_file, "w", encoding="utf-8") as f:
                    f.write(input_data)
                with open(output_file, "w", encoding="utf-8") as f:
                    f.write(contestant_output)
                with open(answer_file, "w", encoding="utf-8") as f:
                    f.write(reference_output)

                # 运行 checker
                # checker.exe input.txt output.txt answer.txt
                run_result = await run_binary_with_args(
                    binary_path,
                    [input_file, output_file, answer_file],
                    timeout=5,
                )

                # Checker 返回 0 表示 AC，非 0 表示 WA/PE
                actual_verdict = "AC" if run_result.return_code == 0 else "WA"

                is_correct = actual_verdict == expected_verdict
                if is_correct:
                    correct_count += 1

                test_results.append(
                    {
                        "index": i + 1,
                        "expected_verdict": expected_verdict,
                        "actual_verdict": actual_verdict,
                        "correct": is_correct,
                    }
                )

        total = len(test_scenarios)
        accuracy = correct_count / total if total > 0 else 0

        return ToolResult.ok(
            source_path=source_path,
            binary_path=binary_path,
            compile_log=compile_result.stderr,
            test_results=test_results,
            correct_count=correct_count,
            total=total,
            accuracy=accuracy,
            message=f"Checker built, accuracy: {accuracy:.2%}",
        )
