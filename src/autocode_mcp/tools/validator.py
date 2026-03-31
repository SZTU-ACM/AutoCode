"""
Validator 工具组 - 数据校验器。

基于论文 Algorithm 1: BUILDVALIDATOR 实现。
"""
import os

from ..utils.compiler import compile_cpp, run_binary
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult


class ValidatorBuildTool(Tool):
    """构建并验证数据校验器。"""

    @property
    def name(self) -> str:
        return "validator_build"

    @property
    def description(self) -> str:
        return """构建并验证数据校验器。

        基于论文 Algorithm 1 实现:
        1. 保存代码到 problem_dir/val.cpp
        2. 编译生成 val.exe
        3. 运行测试用例验证健壮性
        4. 返回得分和详细结果

        注意：此工具不生成代码，代码由 Client LLM 生成后传入。
        测试用例也应由 Client LLM 生成（10 valid + 30 near-valid illegal）。
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
                    "description": "Validator C++ 代码（基于 testlib.h）",
                },
                "test_cases": {
                    "type": "array",
                    "description": "测试用例列表，每个用例包含 input 和 expected_valid",
                    "items": {
                        "type": "object",
                        "properties": {
                            "input": {"type": "string"},
                            "expected_valid": {"type": "boolean"},
                        },
                        "required": ["input", "expected_valid"],
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
        test_cases: list[dict] | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行 Validator 构建。"""
        # 确保目录存在
        os.makedirs(problem_dir, exist_ok=True)

        # 保存代码
        source_path = os.path.join(problem_dir, "val.cpp")
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        # 编译
        binary_path = os.path.join(problem_dir, f"val{get_exe_extension()}")

        compile_result = await compile_cpp(source_path, binary_path, compiler=compiler)

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=source_path,
                compile_log=compile_result.stderr,
            )

        # 如果没有测试用例，直接返回成功
        if not test_cases:
            return ToolResult.ok(
                source_path=source_path,
                binary_path=binary_path,
                compile_log=compile_result.stderr,
                message="Validator built successfully (no test cases provided)",
            )

        # 运行测试用例
        test_results = []
        correct_count = 0

        for i, tc in enumerate(test_cases):
            input_data = tc.get("input", "")
            expected_valid = tc.get("expected_valid", True)

            run_result = await run_binary(binary_path, input_data, timeout=5)

            # Validator 返回 0 表示有效，非 0 表示无效
            actual_valid = run_result.return_code == 0

            is_correct = actual_valid == expected_valid
            if is_correct:
                correct_count += 1

            test_results.append({
                "index": i + 1,
                "input": input_data[:100] + "..." if len(input_data) > 100 else input_data,
                "expected_valid": expected_valid,
                "actual_valid": actual_valid,
                "correct": is_correct,
            })

        score = correct_count
        total = len(test_cases)

        return ToolResult.ok(
            source_path=source_path,
            binary_path=binary_path,
            compile_log=compile_result.stderr,
            test_results=test_results,
            score=score,
            total=total,
            accuracy=score / total if total > 0 else 0,
            message=f"Validator built, score: {score}/{total}",
        )


class ValidatorSelectTool(Tool):
    """从多个候选 Validator 中选择最优。"""

    @property
    def name(self) -> str:
        return "validator_select"

    @property
    def description(self) -> str:
        return """从多个候选 Validator 中选择最优。

        基于论文 Algorithm 1 的评分步骤：
        对每个候选 Validator 运行测试用例，选择得分最高的。

        注意：此工具不生成代码，只负责评分和选择。
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "description": "候选 Validator 的评分结果",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "score": {"type": "integer"},
                            "binary_path": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["candidates"],
        }

    async def execute(self, candidates: list[dict]) -> ToolResult:
        """执行 Validator 选择。"""
        if not candidates:
            return ToolResult.fail("No candidates provided")

        # 按得分排序
        sorted_candidates = sorted(
            candidates,
            key=lambda x: x.get("score", 0),
            reverse=True,
        )

        best = sorted_candidates[0]

        return ToolResult.ok(
            best_candidate=best,
            all_candidates=sorted_candidates,
            message=f"Selected validator with score {best.get('score', 0)}",
        )
