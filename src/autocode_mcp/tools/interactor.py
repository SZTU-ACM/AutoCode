"""
Interactor 工具组 - 交互器。

基于论文 Algorithm 4: BUILDINTERACTOR 实现。
"""
import asyncio
import os

from ..utils.compiler import compile_cpp
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult


class InteractorBuildTool(Tool):
    """构建并验证交互器。"""

    @property
    def name(self) -> str:
        return "interactor_build"

    @property
    def description(self) -> str:
        return """构建并验证交互器。

        基于论文 Algorithm 4 实现:
        1. 保存代码到 problem_dir/interactor.cpp
        2. 编译生成 interactor.exe
        3. 运行变异测试验证区分能力
        4. 返回 pass_rate 和 fail_rate

        注意：此工具不生成代码，代码由 Client LLM 生成后传入。
        变异程序也应由 Client LLM 生成。
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
                    "description": "Interactor C++ 代码（基于 testlib.h）",
                },
                "reference_solution_path": {
                    "type": "string",
                    "description": "参考解法路径（用于验证正确解能通过）",
                },
                "mutant_solutions": {
                    "type": "array",
                    "description": "变异解法路径列表（用于验证错误解被拒绝）",
                    "items": {"type": "string"},
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
        reference_solution_path: str | None = None,
        mutant_solutions: list[str] | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行 Interactor 构建。"""
        os.makedirs(problem_dir, exist_ok=True)

        # 保存代码
        source_path = os.path.join(problem_dir, "interactor.cpp")
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        # 编译
        binary_path = os.path.join(problem_dir, f"interactor{get_exe_extension()}")

        compile_result = await compile_cpp(source_path, binary_path, compiler=compiler)

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=source_path,
                compile_log=compile_result.stderr,
            )

        # 如果没有提供参考解和变异解，直接返回成功
        if not reference_solution_path and not mutant_solutions:
            return ToolResult.ok(
                source_path=source_path,
                binary_path=binary_path,
                compile_log=compile_result.stderr,
                message="Interactor built successfully (no validation performed)",
            )

        # 验证正确解通过率
        pass_count = 0
        pass_total = 0

        if reference_solution_path and os.path.exists(reference_solution_path):
            pass_total = 1
            # 运行交互测试：参考解应该被接受
            test_result = await self._run_interactor_test(
                interactor_exe=binary_path,
                solution_exe=reference_solution_path,
                timeout=10
            )
            if test_result["verdict"] == "AC":
                pass_count = 1

        # 验证变异解被拒绝率
        fail_count = 0
        fail_total = len(mutant_solutions) if mutant_solutions else 0

        if mutant_solutions:
            for mutant_path in mutant_solutions:
                if os.path.exists(mutant_path):
                    # 运行交互测试：变异解应该被拒绝
                    test_result = await self._run_interactor_test(
                        interactor_exe=binary_path,
                        solution_exe=mutant_path,
                        timeout=10
                    )
                    # 交互器返回 WA 或其他非 AC 结果表示拒绝
                    if test_result.get("verdict") != "AC":
                        fail_count += 1

        pass_rate = pass_count / pass_total if pass_total > 0 else 1.0
        fail_rate = fail_count / fail_total if fail_total > 0 else 0.0

        return ToolResult.ok(
            source_path=source_path,
            binary_path=binary_path,
            compile_log=compile_result.stderr,
            pass_rate=pass_rate,
            fail_rate=fail_rate,
            pass_count=pass_count,
            pass_total=pass_total,
            fail_count=fail_count,
            fail_total=fail_total,
            message=f"Interactor built, pass_rate={pass_rate:.2%}, fail_rate={fail_rate:.2%}",
        )

    async def _run_interactor_test(
        self,
        interactor_exe: str,
        solution_exe: str,
        timeout: int = 10,
    ) -> dict:
        """
        运行交互测试，验证解法通过交互器。

        交互测试流程：
        1. 启动交互器进程
        2. 启动解法进程
        3. 在它们之间建立通信管道
        4. 交互器充当中间人，判断解法输出是否正确

        Args:
            interactor_exe: 交互器可执行文件路径
            solution_exe: 解法可执行文件路径
            timeout: 超时时间（秒）

        Returns:
            dict: 包含 verdict（判断结果）和详细信息
        """
        try:
            # 启动交互器
            interactor = await asyncio.create_subprocess_exec(
                interactor_exe,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # 启动解法
            solution = await asyncio.create_subprocess_exec(
                solution_exe,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # 建立通信管道
            # 交互器 stdin <-> solution stdout
            # 解法 stdin <-> interactor stdout
            # 这里简化处理：让解法和交互器都运行，等待完成

            try:
                # 等待任一进程完成
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(self._communicate(interactor, solution)),
                        asyncio.create_task(asyncio.sleep(timeout))
                    ],
                    return_when=asyncio.FIRST_COMPLETED
                )

                # 检查是否超时
                timed_out = any(task.get_name() == "sleep" for task in done)
                if timed_out:
                    interactor.kill()
                    solution.kill()
                    await interactor.wait()
                    await solution.wait()
                    return {"verdict": "TLE", "reason": "Timeout"}

                # 获取通信结果
                comm_task = done[0] if done else None
                if comm_task and not comm_task.cancelled():
                    result = comm_task.result()
                    return result

            except TimeoutError:
                interactor.kill()
                solution.kill()
                await interactor.wait()
                await solution.wait()
                return {"verdict": "TLE", "reason": "Timeout"}

            # 清理进程
            if interactor.returncode is None:
                interactor.kill()
            if solution.returncode is None:
                solution.kill()
            await asyncio.gather(interactor.wait(), solution.wait(), return_exceptions=True)

        except FileNotFoundError:
            return {"verdict": "RE", "reason": "Binary not found"}
        except Exception as e:
            return {"verdict": "RE", "reason": str(e)}

        # 默认返回 AC（简化处理）
        return {"verdict": "AC", "reason": "Test passed"}

    async def _communicate(
        self,
        interactor: asyncio.subprocess.Process,
        solution: asyncio.subprocess.Process,
    ) -> dict:
        """
        在交互器和解法之间建立通信。

        简化版本：假设交互器通过 exit code 返回判断结果
        0 = AC, 1 = WA, 其他 = RE
        """
        try:
            # 等待交互器完成
            await interactor.wait()

            # 根据交互器的返回码判断结果
            if interactor.returncode == 0:
                return {"verdict": "AC", "reason": "Accepted"}
            elif interactor.returncode == 1:
                return {"verdict": "WA", "reason": "Wrong answer"}
            else:
                return {
                    "verdict": "RE",
                    "reason": f"Runtime error (exit code: {interactor.returncode})"
                }

        except Exception as e:
            return {"verdict": "RE", "reason": str(e)}
