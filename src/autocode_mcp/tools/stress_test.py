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
                    "description": "小数据测试的 N 上限（stress test 保持小规模以确保 brute 快速运行）。同时作为 generator_args.n_max 的默认值",
                    "default": 100,
                },
                "timeout": {
                    "type": "integer",
                    "description": "单次执行超时（秒）",
                    "default": 30,
                },
                "sol_name": {
                    "type": "string",
                    "description": "标准解法文件名（不含扩展名），默认 'sol'",
                },
                "brute_name": {
                    "type": "string",
                    "description": "暴力解法文件名（不含扩展名），默认 'brute'",
                },
                "types": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["1", "2", "3", "4"],
                    },
                    "description": "生成策略类型列表，轮次之间循环使用。例如 ['1','2','3','4'] 表示依次使用 tiny, random, extreme, tle。未指定时使用 generator_args.type 或默认 '2'",
                },
                "generator_args": {
                    "type": "object",
                    "description": "Generator 命令行参数。调用协议: gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]。seed 由系统自动填充为当前轮次，其余参数在此指定",
                    "properties": {
                        "type": {
                            "type": "string",
                            "default": "2",
                            "description": "生成策略类型: 1=tiny(小数据穷举), 2=random(随机数据), 3=extreme(极端数据:溢出/精度/hash碰撞), 4=tle(TLE诱导数据)",
                        },
                        "n_min": {
                            "type": "integer",
                            "default": 1,
                            "description": "每次测试中 N 的最小值（N 表示问题规模，如数组长度、节点数等）",
                        },
                        "n_max": {
                            "type": "integer",
                            "description": "每次测试中 N 的最大值。未指定时使用顶层 n_max 参数值",
                        },
                        "t_min": {
                            "type": "integer",
                            "default": 1,
                            "description": "测试组数 T 的最小值（T 表示多组测试时的组数）",
                        },
                        "t_max": {
                            "type": "integer",
                            "default": 1,
                            "description": "测试组数 T 的最大值",
                        },
                        "extra_args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "附加命令行参数，追加在标准 6 参数之后传递给 generator",
                            "default": [],
                        },
                    },
                },
                "stress_profiles": {
                    "type": "array",
                    "description": "多轮对拍配置，每个 profile 可覆盖 trials/types/generator_args",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "trials": {"type": "integer"},
                            "types": {"type": "array", "items": {"type": "string", "enum": ["1", "2", "3", "4"]}},
                            "generator_args": {"type": "object"},
                        },
                    },
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
        sol_name: str | None = None,
        brute_name: str | None = None,
        types: list[str] | None = None,
        generator_args: dict | None = None,
        stress_profiles: list[dict] | None = None,
    ) -> ToolResult:
        """执行对拍测试。"""
        exe_ext = get_exe_extension()
        effective_sol_name = sol_name or "sol"
        effective_brute_name = brute_name or "brute"

        # 检查必要文件 - 优先查找子目录，回退到根目录
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        if not os.path.exists(gen_exe):
            gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")

        sol_exe = os.path.join(problem_dir, "solutions", f"{effective_sol_name}{exe_ext}")
        if not os.path.exists(sol_exe):
            sol_exe = os.path.join(problem_dir, f"{effective_sol_name}{exe_ext}")

        brute_exe = os.path.join(problem_dir, "solutions", f"{effective_brute_name}{exe_ext}")
        if not os.path.exists(brute_exe):
            brute_exe = os.path.join(problem_dir, f"{effective_brute_name}{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")
        if not os.path.exists(sol_exe):
            return ToolResult.fail(f"{effective_sol_name} not found. Run solution_build first.")
        if not os.path.exists(brute_exe):
            return ToolResult.fail(f"{effective_brute_name} not found. Run solution_build first.")

        # 可选的 validator
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")
        if not os.path.exists(val_exe):
            val_exe = os.path.join(problem_dir, f"val{exe_ext}")

        profiles = stress_profiles or [
            {"name": "default", "trials": trials, "types": types, "generator_args": generator_args}
        ]
        total_rounds = sum(max(1, int(p.get("trials", trials))) for p in profiles)
        first_profile_args = profiles[0].get("generator_args") or generator_args
        effective_n_max = first_profile_args.get("n_max", n_max) if first_profile_args else n_max

        failed_round = None
        last_input = None
        sol_output = None
        brute_output = None
        validator_failed = False

        # 统计信息收集
        round_stats: list[dict] = []
        profile_reports: list[dict] = []

        with tempfile.TemporaryDirectory(dir=problem_dir) as temp_dir:
            input_path = os.path.join(temp_dir, "input.txt")

            global_round = 0
            for profile in profiles:
                profile_name = str(profile.get("name", f"profile-{len(profile_reports) + 1}"))
                profile_trials = max(1, int(profile.get("trials", trials)))
                profile_types = profile.get("types", types)
                profile_generator_args = profile.get("generator_args", generator_args)
                start_count = len(round_stats)
                profile_failed_round = None

                for _ in range(profile_trials):
                    global_round += 1
                    # 1. 生成输入数据
                    gen_result = await self._generate_input(
                        gen_exe, input_path, global_round, seed=global_round, timeout=timeout, n_max=n_max,
                        generator_args=profile_generator_args, types=profile_types,
                    )
                    if not gen_result["success"]:
                        error_detail = gen_result.get("error", "Unknown error")
                        if "timed out" in error_detail:
                            hint = "Generator may contain an infinite loop or be too slow. Try increasing the timeout parameter."
                        elif "no output" in error_detail:
                            hint = "Check that the generator follows the protocol: gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]"
                        else:
                            hint = "Generator crashed unexpectedly. Check stderr for details."
                        return ToolResult.fail(
                            f"Generator failed at round {global_round}: {error_detail}. {hint}",
                            round=global_round,
                            seed=gen_result.get("seed", global_round),
                            stderr=gen_result.get("stderr", ""),
                            stdout=gen_result.get("stdout", ""),
                            cmd_args=gen_result.get("cmd_args", []),
                            last_input=last_input,
                            statistics=self._compute_summary(round_stats),
                        )

                    # 2. 验证输入（如果有 validator）
                    if os.path.exists(val_exe):
                        with open(input_path, encoding="utf-8") as f:
                            input_data = f.read()
                        val_result = await run_binary(val_exe, input_data, timeout=timeout)
                        if val_result.return_code != 0:
                            validator_failed = True
                            last_input = input_data
                            failed_round = global_round
                            profile_failed_round = global_round
                            break

                    # 3. 运行 sol 和 brute，比较输出
                    with open(input_path, encoding="utf-8") as f:
                        input_data = f.read()
                    last_input = input_data

                    # 运行 sol
                    sol_result = await run_binary(sol_exe, input_data, timeout=timeout)
                    if sol_result.timed_out or not sol_result.success:
                        return ToolResult.fail(
                            f"{effective_sol_name} failed at round {global_round}",
                            round=global_round,
                            input_data=input_data,
                            stderr=sol_result.stderr,
                            statistics=self._compute_summary(round_stats),
                        )
                    sol_output = sol_result.stdout

                    # 运行 brute
                    brute_result = await run_binary(brute_exe, input_data, timeout=timeout)
                    if brute_result.timed_out:
                        return ToolResult.fail(
                            f"{effective_brute_name} timed out at round {global_round} (N may be too large)",
                            round=global_round,
                            input_data=input_data,
                            suggestion="Try reducing n_max parameter",
                            statistics=self._compute_summary(round_stats),
                        )
                    if not brute_result.success:
                        return ToolResult.fail(
                            f"{effective_brute_name} failed at round {global_round}",
                            round=global_round,
                            input_data=input_data,
                            stderr=brute_result.stderr,
                            statistics=self._compute_summary(round_stats),
                        )
                    brute_output = brute_result.stdout

                    # 收集统计信息
                    round_stats.append({
                        "round": global_round,
                        "sol_time_ms": sol_result.time_ms,
                        "brute_time_ms": brute_result.time_ms,
                        "input_size": len(input_data),
                        "n_value": self._extract_n_value(input_data),
                    })

                    # 5. 比较输出
                    if sol_output.strip() != brute_output.strip():
                        last_input = input_data
                        failed_round = global_round
                        profile_failed_round = global_round
                        break

                profile_reports.append(
                    {
                        "name": profile_name,
                        "trials": profile_trials,
                        "completed_rounds": len(round_stats) - start_count,
                        "failed_round": profile_failed_round,
                    }
                )
                if failed_round:
                    break

        return self._format_result(
            failed_round,
            validator_failed,
            last_input,
            sol_output,
            brute_output,
            total_rounds,
            effective_n_max,
            round_stats,
            profile_reports,
        )

    async def _generate_input(
        self,
        gen_exe: str,
        input_path: str,
        round_num: int,
        seed: int,
        timeout: int,
        n_max: int = 100,
        generator_args: dict | None = None,
        types: list[str] | None = None,
    ) -> dict:
        """生成输入数据。"""
        try:
            # 确定 type 参数
            if types:
                type_param = types[(round_num - 1) % len(types)]
            elif generator_args:
                type_param = generator_args.get("type", "2")
            else:
                type_param = "2"

            # 构建命令参数
            # gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]
            if generator_args:
                cmd_args = [
                    str(seed),
                    type_param,
                    str(generator_args.get("n_min", 1)),
                    str(generator_args.get("n_max", n_max)),
                    str(generator_args.get("t_min", 1)),
                    str(generator_args.get("t_max", 1)),
                ] + generator_args.get("extra_args", [])
            else:
                cmd_args = [
                    str(seed),
                    type_param,
                    "1",
                    str(n_max),
                    "1",
                    "1",
                ]

            gen_result = await run_binary_with_args(
                gen_exe,
                cmd_args,
                timeout=timeout,
            )
            if gen_result.timed_out:
                return {
                    "success": False,
                    "error": "Generator timed out",
                    "stderr": gen_result.stderr,
                    "stdout": gen_result.stdout,
                    "cmd_args": cmd_args,
                    "seed": seed,
                }

            if not gen_result.stdout.strip():
                return {
                    "success": False,
                    "error": "Generator produced no output",
                    "stderr": gen_result.stderr,
                    "stdout": gen_result.stdout,
                    "cmd_args": cmd_args,
                    "seed": seed,
                }

            with open(input_path, "w", encoding="utf-8") as f:
                f.write(gen_result.stdout)

            return {"success": True, "cmd_args": cmd_args, "seed": seed}

        except Exception as e:
            return {
                "success": False,
                "error": f"Generator error: {str(e)}",
                "stderr": "",
                "stdout": "",
                "cmd_args": cmd_args if 'cmd_args' in locals() else [],
                "seed": seed,
            }

    def _extract_n_value(self, input_data: str) -> int | None:
        """尝试从输入数据的第一行解析 N 值。"""
        try:
            first_line = input_data.strip().split("\n")[0]
            # 尝试解析为整数（常见竞赛编程格式）
            n = int(first_line.strip())
            return n if n > 0 else None
        except (ValueError, IndexError):
            return None

    def _compute_n_distribution(self, round_stats: list[dict]) -> dict[str, int]:
        """计算 N 值分布。"""
        buckets = {"1": 0, "2-10": 0, "11-50": 0, "51-100": 0, "101+": 0}
        for stat in round_stats:
            n = stat.get("n_value")
            if n is None:
                continue
            if n == 1:
                buckets["1"] += 1
            elif n <= 10:
                buckets["2-10"] += 1
            elif n <= 50:
                buckets["11-50"] += 1
            elif n <= 100:
                buckets["51-100"] += 1
            else:
                buckets["101+"] += 1
        return {k: v for k, v in buckets.items() if v > 0}

    def _compute_summary(self, round_stats: list[dict]) -> dict | None:
        """计算统计摘要。"""
        if not round_stats:
            return None

        sol_times = [s["sol_time_ms"] for s in round_stats]
        brute_times = [s["brute_time_ms"] for s in round_stats]

        summary = {
            "rounds_completed": len(round_stats),
            "sol_time": {
                "min_ms": min(sol_times),
                "max_ms": max(sol_times),
                "avg_ms": sum(sol_times) // len(sol_times),
                "total_ms": sum(sol_times),
            },
            "brute_time": {
                "min_ms": min(brute_times),
                "max_ms": max(brute_times),
                "avg_ms": sum(brute_times) // len(brute_times),
                "total_ms": sum(brute_times),
            },
            "n_distribution": self._compute_n_distribution(round_stats),
            "slowest_round": max(round_stats, key=lambda s: s["sol_time_ms"]),
        }

        # 计算最大时间比
        ratios = []
        for s in round_stats:
            bt = max(s["brute_time_ms"], 1)
            ratios.append(s["sol_time_ms"] / bt)
        summary["max_ratio"] = max(ratios)

        return summary

    def _format_result(
        self,
        failed_round: int | None,
        validator_failed: bool,
        last_input: str | None,
        sol_output: str | None,
        brute_output: str | None,
        total_rounds: int,
        effective_n_max: int = 100,
        round_stats: list[dict] | None = None,
        stress_profiles: list[dict] | None = None,
    ) -> ToolResult:
        """格式化测试结果。"""
        statistics = self._compute_summary(round_stats or [])

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
                statistics=statistics,
                stress_profiles=stress_profiles or [],
            )

        return ToolResult.ok(
            completed_rounds=total_rounds,
            total_rounds=total_rounds,
            effective_n_max=effective_n_max,
            statistics=statistics,
            stress_profiles=stress_profiles or [],
            message=f"All {total_rounds} rounds passed",
        )
