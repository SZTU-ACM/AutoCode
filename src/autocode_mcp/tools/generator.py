"""
Generator 工具组 - 数据生成器。

基于论文 Algorithm 2: BUILDGENERATORSUITE 实现。
"""

from __future__ import annotations

import hashlib
import os
import re

from ..utils.compiler import run_binary, run_binary_with_args
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult, input_schema_from_model
from .mixins import BuildToolMixin, resolve_source
from .schemas import GeneratorBuildInput, GeneratorRunInput


class GeneratorBuildTool(Tool, BuildToolMixin):
    """构建数据生成器。"""

    @property
    def name(self) -> str:
        return "generator_build"

    @property
    def description(self) -> str:
        return """构建数据生成器。

        保存并编译 gen.cpp。

        前置条件：
        1. 已运行 problem_create 创建题目目录

        建议下一步：
        - 运行 validator_build 构建校验器
        - 运行 stress_test_run 进行对拍测试
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(GeneratorBuildInput)

    async def execute(
        self,
        problem_dir: str,
        code: str | None = None,
        source_path: str | None = None,
        compiler: str = "g++",
        enable_semantic_check: bool = True,
        strict_semantic_check: bool = False,
    ) -> ToolResult:
        """执行 Generator 构建。"""
        resolved, err = resolve_source(
            problem_dir,
            code,
            source_path,
            default_source_path=os.path.join("files", "gen.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None

        os.makedirs(problem_dir, exist_ok=True)
        files_dir = os.path.join(problem_dir, "files")
        os.makedirs(files_dir, exist_ok=True)

        canonical_path = os.path.join(files_dir, "gen.cpp")
        try:
            with open(canonical_path, "w", encoding="utf-8") as f:
                f.write(resolved.code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        exe_ext = get_exe_extension()
        binary_path = os.path.join(files_dir, f"gen{exe_ext}")

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

        semantic_check = self._check_type34_semantics(resolved.code) if enable_semantic_check else {"enabled": False}
        if (
            enable_semantic_check
            and strict_semantic_check
            and not semantic_check.get("passed", True)
        ):
            return ToolResult.fail(
                "Generator semantic check failed: type=3/type=4 lack substantial difference",
                semantic_check=semantic_check,
            )

        return ToolResult.ok(
            source_path=compile_source,
            canonical_path=canonical_path,
            binary_path=binary_path,
            binary_size=binary_size,
            compile_log=compile_result.stderr,
            semantic_check=semantic_check,
            message="Generator built successfully",
        )

    def _check_type34_semantics(self, code: str) -> dict:
        type3_blocks = self._extract_type_branch_snippets(code, 3)
        type4_blocks = self._extract_type_branch_snippets(code, 4)
        has_type3 = bool(type3_blocks)
        has_type4 = bool(type4_blocks)
        if not has_type3 or not has_type4:
            return {
                "enabled": True,
                "passed": True,
                "advisory": True,
                "reason": "semantic check could not reliably detect both type=3/type=4 branches",
                "hint": "请人工确认 type=3/type=4 分支存在且有实质差异",
            }

        norm3 = " ".join(type3_blocks).replace(" ", "")
        norm4 = " ".join(type4_blocks).replace(" ", "")
        output_lines = [line.strip() for line in code.splitlines() if "cout" in line or "printf" in line]
        duplicate_outputs = len(set(output_lines)) <= 1 and len(output_lines) > 0
        type3_signals = self._extract_branch_signals(" ".join(type3_blocks))
        type4_signals = self._extract_branch_signals(" ".join(type4_blocks))
        shared_signals = type3_signals.intersection(type4_signals)
        signal_overlap = (
            len(shared_signals) / max(len(type3_signals.union(type4_signals)), 1)
            if (type3_signals or type4_signals)
            else 1.0
        )
        similar_core = (
            norm3 == norm4
            or (norm3 and norm4 and abs(len(norm3) - len(norm4)) < 10)
            or duplicate_outputs
        )
        overlap_advisory = signal_overlap > 0.9 and not similar_core
        return {
            "enabled": True,
            "passed": not similar_core,
            "reason": "" if not similar_core else "type=3/type=4 branch snippets are too similar",
            "hint": "为 type=4 增加针对性卡法，而不仅是 n_max/t_max 取最大值",
            "signal_overlap": round(signal_overlap, 3),
            "signal_overlap_advisory": overlap_advisory,
            "type3_signals": sorted(type3_signals)[:20],
            "type4_signals": sorted(type4_signals)[:20],
        }

    def _extract_type_branch_snippets(self, code: str, type_value: int) -> list[str]:
        patterns = [
            rf"type\s*==\s*{type_value}\b",
            rf"\b{type_value}\s*==\s*type\b",
            rf"case\s+{type_value}\s*:",
        ]
        snippets: list[str] = []
        for pattern in patterns:
            for match in re.finditer(pattern, code):
                snippets.append(code[match.start(): match.start() + 240])
        return snippets

    def _extract_branch_signals(self, snippet: str) -> set[str]:
        """提取分支内结构信号，用于判断 type=3/type=4 是否只有参数放大差异。"""
        tokens = set(re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", snippet))
        stop_words = {
            "if", "else", "case", "switch", "for", "while", "return", "int", "long", "double",
            "seed", "type", "n_min", "n_max", "t_min", "t_max",
        }
        return {t for t in tokens if t not in stop_words and len(t) > 2}


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

        前置条件：
        1. 已运行 generator_build 构建生成器

        建议下一步：
        - 运行 stress_test_run 验证解法正确性
        - 运行 problem_generate_tests 生成最终测试数据
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(GeneratorRunInput)

    async def execute(
        self,
        problem_dir: str,
        strategies: list[str],
        test_count: int = 20,
        validator_path: str | None = None,
        seed_start: int = 1,
        n_min: int = 1,
        n_max: int = 100000,
        t_min: int = 1,
        t_max: int = 1,
        extra_args: list[str] | None = None,
    ) -> ToolResult:
        """执行数据生成。"""
        exe_ext = get_exe_extension()
        extra_args = extra_args or []

        # 检查 generator - 优先查找 files/ 子目录
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        if not os.path.exists(gen_exe):
            gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")

        # 检查 validator
        val_exe = validator_path
        if val_exe and not os.path.exists(val_exe):
            val_exe = None

        generated_inputs = []
        signatures = set()  # 用于去重
        generator_failures: list[dict[str, object]] = []

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
            strategy = strategies[(attempts - 1) % len(strategies)]
            type_param = strategy_type_map.get(strategy, "2")

            # 运行 generator
            # gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]
            cmd_args = [str(seed), type_param, str(n_min), str(n_max), str(t_min), str(t_max)] + extra_args

            gen_result = await run_binary_with_args(
                gen_exe,
                cmd_args,
                timeout=10,
            )

            if not gen_result.success:
                generator_failures.append(
                    {
                        "seed": seed,
                        "strategy": strategy,
                        "return_code": gen_result.return_code,
                        "stderr": (gen_result.stderr or "")[:200],
                    }
                )
                seed += 1
                continue

            input_data = gen_result.stdout
            if not input_data or not input_data.strip():
                generator_failures.append(
                    {
                        "seed": seed,
                        "strategy": strategy,
                        "return_code": gen_result.return_code,
                        "stderr": (gen_result.stderr or "")[:200],
                        "reason": "empty_stdout",
                    }
                )
                seed += 1
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

            generated_inputs.append(
                {
                    "input": input_data,
                    "strategy": strategy,
                    "seed": seed,
                }
            )
            seed += 1

        return ToolResult.ok(
            generated_count=len(generated_inputs),
            test_count=test_count,
            inputs=generated_inputs[:test_count],
            strategies_used=strategies,
            generator_failures=generator_failures[-20:],
            message=f"Generated {len(generated_inputs)} test inputs",
        )
