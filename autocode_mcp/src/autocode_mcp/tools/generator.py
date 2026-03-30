"""
Generator 工具组 - 数据生成器。

基于论文 Algorithm 2: BUILDGENERATORSUITE 实现。
"""
import os
import sys
import hashlib
from typing import Optional
from .base import Tool, ToolResult
from ..utils.compiler import compile_cpp, run_binary, run_binary_with_args


class GeneratorBuildTool(Tool):
    """构建数据生成器。"""

    @property
    def name(self) -> str:
        return "generator_build"

    @property
    def description(self) -> str:
        return """构建数据生成器。

        保存并编译 gen.cpp。

        注意：此工具不生成代码，代码由 Client LLM 生成后传入。
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
                    "description": "Generator C++ 代码（基于 testlib.h）",
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
        compiler: str = "g++",
    ) -> ToolResult:
        """执行 Generator 构建。"""
        os.makedirs(problem_dir, exist_ok=True)

        source_path = os.path.join(problem_dir, "gen.cpp")
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        exe_ext = ".exe" if sys.platform == "win32" else ""
        binary_path = os.path.join(problem_dir, f"gen{exe_ext}")

        compile_result = await compile_cpp(source_path, binary_path, compiler=compiler)

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=source_path,
                compile_log=compile_result.stderr,
            )

        return ToolResult.ok(
            source_path=source_path,
            binary_path=binary_path,
            compile_log=compile_result.stderr,
            message="Generator built successfully",
        )


class GeneratorRunTool(Tool):
    """运行多策略数据生成器。"""

    @property
    def name(self) -> str:
        return "generator_run"

    @property
    def description(self) -> str:
        return """运行多策略数据生成器。

        基于论文 Algorithm 2 实现三种策略:
        - tiny: 小数据穷举 (G1)
        - random: 随机数据 (G2)
        - extreme: 极端数据 (溢出、精度、hash碰撞)
        - tle: TLE 诱导数据 (G3)

        自动通过 Validator 过滤无效输入。
        支持去重、平衡、采样。
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
                "strategies": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["tiny", "random", "extreme", "tle"],
                    },
                    "description": "要运行的策略列表",
                },
                "test_count": {
                    "type": "integer",
                    "description": "目标测试数量",
                    "default": 20,
                },
                "validator_path": {
                    "type": "string",
                    "description": "已编译的 Validator 路径（可选，用于过滤）",
                },
                "seed_start": {
                    "type": "integer",
                    "description": "随机种子起始值",
                    "default": 1,
                },
            },
            "required": ["problem_dir", "strategies"],
        }

    async def execute(
        self,
        problem_dir: str,
        strategies: list[str],
        test_count: int = 20,
        validator_path: Optional[str] = None,
        seed_start: int = 1,
        n_min: int = 1,
        n_max: int = 100000,
        t_min: int = 1,
        t_max: int = 1,
    ) -> ToolResult:
        """执行数据生成。"""
        exe_ext = ".exe" if sys.platform == "win32" else ""

        # 检查 generator
        gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")
        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")

        # 检查 validator
        val_exe = validator_path
        if val_exe and not os.path.exists(val_exe):
            val_exe = None

        generated_inputs = []
        signatures = set()  # 用于去重

        # 策略映射到 type 参数
        strategy_type_map = {
            "tiny": "1",
            "random": "2",
            "extreme": "3",
            "tle": "4",
        }

        seed = seed_start
        attempts = 0
        max_attempts = test_count * 10  # 最多尝试 10 倍

        while len(generated_inputs) < test_count and attempts < max_attempts:
            attempts += 1

            # 选择策略
            strategy = strategies[attempts % len(strategies)]
            type_param = strategy_type_map.get(strategy, "2")

            # 运行 generator
            # gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>
            cmd_args = [str(seed), type_param, str(n_min), str(n_max), str(t_min), str(t_max)]

            gen_result = await run_binary_with_args(
                gen_exe,
                cmd_args,
                timeout=10,
            )

            # 只要有输出就接受（某些 generator 可能返回非零退出码但仍产生有效输出）
            input_data = gen_result.stdout
            if not input_data or not input_data.strip():
                continue

            # 计算 signature 用于去重
            sig = hashlib.md5(input_data.encode()).hexdigest()
            if sig in signatures:
                continue
            signatures.add(sig)

            # 使用 validator 过滤
            if val_exe:
                val_result = await run_binary(val_exe, input_data, timeout=5)
                if val_result.return_code != 0:
                    continue

            generated_inputs.append({
                "input": input_data,
                "strategy": strategy,
                "seed": seed,
            })
            seed += 1

        return ToolResult.ok(
            generated_count=len(generated_inputs),
            test_count=test_count,
            inputs=generated_inputs[:test_count],
            strategies_used=strategies,
            message=f"Generated {len(generated_inputs)} test inputs",
        )
