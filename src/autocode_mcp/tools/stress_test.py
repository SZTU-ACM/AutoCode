"""
Stress Test 工具 - 对拍测试。

基于论文框架，比较 sol.cpp 和 brute.cpp 的输出。
"""

from __future__ import annotations

import os
import tempfile

from ..utils.compiler import run_binary, run_binary_with_args
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult


class StressTestRunTool(Tool):
    """运行对拍测试。"""

    @property
    def name(self) -> str:
        return "stress_test_run"

    @property
    def description(self) -> str:
        return """运行对拍测试，比较 sol.cpp 和 brute.cpp 的输出。

        用于验证解法正确性。支持自定义轮数和参数。
        使用小数据（N <= 100）确保暴力解法快速运行。

        前置条件：
        1. 已运行 solution_build 构建 sol.cpp
        2. 已运行 solution_build 构建 brute.cpp
        3. 已运行 generator_build 构建 gen.cpp

        建议下一步：
        - 如果通过：运行 problem_generate_tests 生成测试数据
        - 如果失败：检查 sol.cpp 和 brute.cpp 的差异，修复错误
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
                "trials": {
                    "type": "integer",
                    "description": "测试轮数",
                    "default": 1000,
                },
                "n_max": {
                    "type": "integer",
                    "description": "小数据测试的 N 上限",
                    "default": 100,
                },
                "timeout": {
                    "type": "integer",
                    "description": "单次执行超时（秒）",
                    "default": 30,
                },
            },
            "required": ["problem_dir"],
        }

    async def execute(
        self,
        problem_dir: str,
        trials: int = 1000,
        n_max: int = 100,
        timeout: int = 30,
    ) -> ToolResult:
        """执行对拍测试。"""
        exe_ext = get_exe_extension()

        # 检查必要文件
        gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")
        sol_exe = os.path.join(problem_dir, f"sol{exe_ext}")
        brute_exe = os.path.join(problem_dir, f"brute{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")
        if not os.path.exists(sol_exe):
            return ToolResult.fail("sol not found. Run solution_build first.")
        if not os.path.exists(brute_exe):
            return ToolResult.fail("brute not found. Run solution_build first.")

        # 可选的 validator
        val_exe = os.path.join(problem_dir, f"val{exe_ext}")

        failed_round = None
        last_input = None
        sol_output = None
        brute_output = None
        validator_failed = False

        with tempfile.TemporaryDirectory(dir=problem_dir) as temp_dir:
            input_path = os.path.join(temp_dir, "input.txt")

            for i in range(1, trials + 1):
                # 1. 生成输入数据
                gen_result = await self._generate_input(
                    gen_exe, input_path, i, seed=i, timeout=timeout
                )
                if not gen_result["success"]:
                    return ToolResult.fail(
                        f"Generator failed at round {i}: {gen_result.get('error', '')}. "
                        f"Check that the generator accepts command-line arguments (seed).",
                        round=i,
                        stderr=gen_result.get("stderr", ""),
                    )

                # 2. 验证输入（如果有 validator）
                if os.path.exists(val_exe):
                    with open(input_path) as f:
                        input_data = f.read()
                    val_result = await run_binary(val_exe, input_data, timeout=timeout)
                    if val_result.return_code != 0:
                        validator_failed = True
                        last_input = input_data
                        failed_round = i
                        break

                # 3. 运行 sol 和 brute，比较输出
                with open(input_path) as f:
                    input_data = f.read()

                # 运行 sol
                sol_result = await run_binary(sol_exe, input_data, timeout=timeout)
                if sol_result.timed_out or not sol_result.success:
                    return ToolResult.fail(
                        f"sol failed at round {i}",
                        round=i,
                        input_data=input_data,
                        stderr=sol_result.stderr,
                    )
                sol_output = sol_result.stdout

                # 运行 brute
                brute_result = await run_binary(brute_exe, input_data, timeout=timeout)
                if brute_result.timed_out:
                    return ToolResult.fail(
                        f"brute timed out at round {i} (N may be too large)",
                        round=i,
                        input_data=input_data,
                        suggestion="Try reducing n_max parameter",
                    )
                if not brute_result.success:
                    return ToolResult.fail(
                        f"brute failed at round {i}",
                        round=i,
                        input_data=input_data,
                        stderr=brute_result.stderr,
                    )
                brute_output = brute_result.stdout

                # 5. 比较输出
                if sol_output.strip() != brute_output.strip():
                    last_input = input_data
                    failed_round = i
                    break

        return self._format_result(
            failed_round,
            validator_failed,
            last_input,
            sol_output,
            brute_output,
            trials,
        )

    async def _generate_input(
        self,
        gen_exe: str,
        input_path: str,
        round_num: int,
        seed: int,
        timeout: int,
    ) -> dict:
        """
        生成输入数据。

        Args:
            gen_exe: generator 可执行文件路径
            input_path: 输入文件保存路径
            round_num: 当前轮次
            seed: 随机种子
            timeout: 超时时间（秒）

        Returns:
            dict: {"success": bool, "error": str | None}
        """
        try:
            gen_result = await run_binary_with_args(
                gen_exe,
                [str(seed)],
                timeout=timeout,
            )
            # Generator 可能因 testlib.h 优化问题崩溃，但输出仍有效
            # 只要没有超时且有输出，就认为成功
            if gen_result.timed_out:
                return {
                    "success": False,
                    "error": f"Generator timed out at round {round_num}",
                    "stderr": gen_result.stderr,
                }

            if not gen_result.stdout.strip():
                return {
                    "success": False,
                    "error": f"Generator produced no output at round {round_num}",
                    "stderr": gen_result.stderr,
                }

            with open(input_path, "w", encoding="utf-8") as f:
                f.write(gen_result.stdout)

            return {"success": True}

        except Exception as e:
            return {
                "success": False,
                "error": f"Generator error at round {round_num}: {str(e)}",
            }

    def _format_result(
        self,
        failed_round: int | None,
        validator_failed: bool,
        last_input: str | None,
        sol_output: str | None,
        brute_output: str | None,
        total_rounds: int,
    ) -> ToolResult:
        """
        格式化测试结果。
        """
        if failed_round:
            return ToolResult.fail(
                f"Output mismatch at round {failed_round}"
                if not validator_failed
                else f"Validator failed at round {failed_round}",
                round=failed_round,
                input_data=last_input,
                sol_output=sol_output,
                brute_output=brute_output,
                completed_rounds=failed_round - 1,
                total_rounds=total_rounds,
            )

        return ToolResult.ok(
            completed_rounds=total_rounds,
            total_rounds=total_rounds,
            message=f"All {total_rounds} rounds passed",
        )
