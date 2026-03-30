"""
Stress Test 工具 - 对拍测试。

基于论文框架，比较 sol.cpp 和 brute.cpp 的输出。
"""
import os
import sys
import tempfile

from ..utils.compiler import run_binary
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

        基于论文框架，这是验证解法正确性的关键步骤。
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
        exe_ext = ".exe" if sys.platform == "win32" else ""

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
                # gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>
                # type=1 表示小数据
                try:
                    with open(input_path, "w") as f:
                        gen_result = await run_binary(
                            gen_exe,
                            "",
                            timeout=timeout,
                        )
                        if gen_result.timed_out or not gen_result.success:
                            return ToolResult.fail(
                                f"Generator failed at round {i}",
                                round=i,
                                stderr=gen_result.stderr,
                            )
                        f.write(gen_result.stdout)
                except Exception as e:
                    return ToolResult.fail(f"Generator error at round {i}: {str(e)}")

                # 2. 验证输入（如果有 validator）
                if os.path.exists(val_exe):
                    with open(input_path) as f:
                        input_data = f.read()
                    val_result = await run_binary(val_exe, input_data, timeout=timeout)
                    if val_result.returncode != 0:
                        validator_failed = True
                        last_input = input_data
                        failed_round = i
                        break

                # 3. 运行 sol
                with open(input_path) as f:
                    input_data = f.read()
                sol_result = await run_binary(sol_exe, input_data, timeout=timeout)
                if sol_result.timed_out or not sol_result.success:
                    return ToolResult.fail(
                        f"sol failed at round {i}",
                        round=i,
                        input_data=input_data,
                        stderr=sol_result.stderr,
                    )
                sol_output = sol_result.stdout

                # 4. 运行 brute
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
                total_rounds=trials,
            )

        return ToolResult.ok(
            completed_rounds=trials,
            total_rounds=trials,
            message=f"All {trials} rounds passed",
        )
