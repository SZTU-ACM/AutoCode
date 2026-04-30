"""
Validator 工具组 - 数据校验器。

基于论文 Algorithm 1: BUILDVALIDATOR 实现。
"""

from __future__ import annotations

import os

from ..utils.compiler import run_binary
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult
from .mixins import BuildToolMixin, resolve_source


class ValidatorBuildTool(Tool, BuildToolMixin):
    """构建并验证数据校验器。"""

    @property
    def name(self) -> str:
        return "validator_build"

    @property
    def description(self) -> str:
        return """构建并验证数据校验器。

        基于论文 Algorithm 1 实现:
        1. 保存代码到 problem_dir/files/val.cpp
        2. 编译生成 files/val.exe
        3. 运行测试用例验证健壮性
        4. 返回得分和详细结果

        前置条件：
        1. 已运行 problem_create 创建题目目录

        建议下一步：
        - 运行 generator_build 构建数据生成器
        - 运行 problem_generate_tests 生成测试数据时使用 validator 过滤
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
                    "description": "C++ 源代码（与 source_path 二选一）",
                },
                "source_path": {
                    "type": "string",
                    "description": "源文件路径，相对于 problem_dir 或绝对路径。与 code 二选一，优先级高于 code",
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
            "required": ["problem_dir"],
            "anyOf": [
                {"required": ["code"]},
                {"required": ["source_path"]},
            ],
        }

    async def execute(
        self,
        problem_dir: str,
        code: str | None = None,
        source_path: str | None = None,
        test_cases: list[dict] | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行 Validator 构建。"""
        resolved, err = resolve_source(problem_dir, code, source_path)
        if err is not None:
            return err
        assert resolved is not None

        # 确保目录存在
        os.makedirs(problem_dir, exist_ok=True)
        files_dir = os.path.join(problem_dir, "files")
        os.makedirs(files_dir, exist_ok=True)

        canonical_path = os.path.join(files_dir, "val.cpp")
        try:
            with open(canonical_path, "w", encoding="utf-8") as f:
                f.write(resolved.code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        binary_path = os.path.join(files_dir, f"val{get_exe_extension()}")

        compile_source = resolved.original_source_path or canonical_path
        include_dirs = [resolved.include_dir] if resolved.include_dir else None
        compile_result = await self.build(compile_source, binary_path, compiler=compiler, include_dirs=include_dirs)

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=compile_source,
                canonical_path=canonical_path,
                compile_log=compile_result.stderr,
            )

        binary_size = os.path.getsize(binary_path) if os.path.exists(binary_path) else 0

        # 如果没有测试用例，直接返回成功
        if not test_cases:
            return ToolResult.ok(
                source_path=compile_source,
                canonical_path=canonical_path,
                binary_path=binary_path,
                binary_size=binary_size,
                compile_log=compile_result.stderr,
                message=(
                    "Validator built successfully (no test cases provided). "
                    "Remember to end with inf.readEof(); if trailing whitespace is allowed, use inf.seekEof(); inf.readEof();"
                ),
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

            test_results.append(
                {
                    "index": i + 1,
                    "input": input_data[:100] + "..." if len(input_data) > 100 else input_data,
                    "expected_valid": expected_valid,
                    "actual_valid": actual_valid,
                    "correct": is_correct,
                }
            )

        score = correct_count
        total = len(test_cases)

        return ToolResult.ok(
            source_path=compile_source,
            canonical_path=canonical_path,
            binary_path=binary_path,
            binary_size=binary_size,
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
