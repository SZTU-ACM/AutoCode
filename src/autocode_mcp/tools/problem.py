"""
Problem 工具组 - 题目管理。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass

from ..utils.compiler import run_binary, run_binary_with_args
from ..utils.platform import get_exe_extension
from .base import Tool, ToolResult


@dataclass
class CandidateTest:
    """候选测试数据。"""

    input_data: str
    output_data: str
    type_param: str  # 1=tiny, 2=random, 3=extreme, 4=tle
    signature: str


class ProblemCreateTool(Tool):
    """创建题目目录结构。"""

    @property
    def name(self) -> str:
        return "problem_create"

    @property
    def description(self) -> str:
        return """创建新题目的目录结构。

        创建标准的竞赛编程题目目录：
        - files/: testlib.h, gen.cpp, val.cpp
        - solutions/: sol.cpp, brute.cpp
        - statements/: README.md
        - tests/: 测试数据

        同时复制 testlib.h 模板文件。
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
                "problem_name": {
                    "type": "string",
                    "description": "题目名称",
                },
            },
            "required": ["problem_dir", "problem_name"],
        }

    async def execute(
        self,
        problem_dir: str,
        problem_name: str,
    ) -> ToolResult:
        """执行题目目录创建。"""
        # 创建目录结构
        directories = [
            problem_dir,
            os.path.join(problem_dir, "files"),
            os.path.join(problem_dir, "solutions"),
            os.path.join(problem_dir, "statements"),
            os.path.join(problem_dir, "tests"),
        ]

        created_dirs = []
        for dir_path in directories:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                created_dirs.append(dir_path)

        # 复制 testlib.h
        from .. import TEMPLATES_DIR
        template_testlib = os.path.join(TEMPLATES_DIR, "testlib.h")

        if os.path.exists(template_testlib):
            dest_testlib = os.path.join(problem_dir, "files", "testlib.h")
            shutil.copy2(template_testlib, dest_testlib)
        else:
            return ToolResult.fail(
                f"testlib.h template not found at {template_testlib}. "
                "Please download from https://github.com/MikeMirzayanov/testlib "
                "and place it in the templates/ directory."
            )

        # 创建基础 README.md
        readme_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(f"# {problem_name}\n\n题目描述待补充...\n")

        return ToolResult.ok(
            problem_dir=problem_dir,
            problem_name=problem_name,
            created_directories=created_dirs,
            message=f"Created problem directory: {problem_dir}",
        )


class ProblemGenerateTestsTool(Tool):
    """生成最终测试数据。"""

    @property
    def name(self) -> str:
        return "problem_generate_tests"

    @property
    def description(self) -> str:
        return """生成最终测试数据集。

        基于论文 Algorithm 2 的后处理步骤：
        - 使用 gen.cpp 生成测试数据
        - 使用 sol.cpp 生成答案
        - 支持去重、平衡、采样

        生成 01.in ~ N.in 及对应的 .ans 文件。

        前置条件：
        1. 已运行 generator_build 构建 gen.cpp
        2. 已运行 solution_build 构建 sol.cpp
        3. 建议先运行 stress_test_run 验证解法正确性

        建议下一步：
        - 运行 problem_pack_polygon 打包为 Polygon 格式
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
                "test_count": {
                    "type": "integer",
                    "description": "测试数据组数",
                    "default": 20,
                },
                "timeout": {
                    "type": "integer",
                    "description": "单次执行超时（秒）",
                    "default": 60,
                },
                "constraints": {
                    "type": "object",
                    "description": "题目约束条件，用于生成极限数据",
                    "properties": {
                        "n_max": {
                            "type": "integer",
                            "description": "N 的最大值约束",
                        },
                        "t_max": {
                            "type": "integer",
                            "description": "T 的最大值约束",
                        },
                        "sum_n_max": {
                            "type": "integer",
                            "description": "sum(N) 的最大值约束",
                        },
                    },
                },
                "test_configs": {
                    "type": "array",
                    "description": "自定义测试配置列表（可选，不提供则根据 constraints 自动生成）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "seed_offset": {
                                "type": "integer",
                                "description": "种子偏移量",
                                "default": 0,
                            },
                            "type": {
                                "type": "string",
                                "enum": ["1", "2", "3", "4"],
                                "description": "生成策略 (1=tiny, 2=random, 3=extreme, 4=tle)",
                            },
                            "n_min": {"type": "integer", "description": "N 最小值"},
                            "n_max": {"type": "integer", "description": "N 最大值"},
                            "t_min": {"type": "integer", "description": "T 最小值"},
                            "t_max": {"type": "integer", "description": "T 最大值"},
                        },
                        "required": ["type", "n_min", "n_max", "t_min", "t_max"],
                    },
                },
                "enable_dedup": {
                    "type": "boolean",
                    "description": "启用去重（基于 MD5 signature）",
                    "default": True,
                },
                "enable_validator_filter": {
                    "type": "boolean",
                    "description": "启用 Validator 过滤（自动检测 val.exe）",
                    "default": True,
                },
                "enable_balance": {
                    "type": "boolean",
                    "description": "启用平衡分布（确保各策略类型均衡）",
                    "default": True,
                },
                "oversample_ratio": {
                    "type": "number",
                    "description": "超额采样比例（生成候选数据 = test_count * ratio）",
                    "default": 1.5,
                },
            },
            "required": ["problem_dir"],
        }

    async def execute(
        self,
        problem_dir: str,
        test_count: int = 20,
        timeout: int = 60,
        constraints: dict | None = None,
        test_configs: list[dict] | None = None,
        enable_dedup: bool = True,
        enable_validator_filter: bool = True,
        enable_balance: bool = True,
        oversample_ratio: float = 1.5,
    ) -> ToolResult:
        """执行测试数据生成。

        实现论文 Algorithm 2 的后处理步骤：
        1. 生成超额候选数据
        2. 去重（基于 MD5 signature）
        3. Validator 过滤（自动检测 val.exe）
        4. 平衡分布（按策略类型）
        5. 采样最终 test_count 个
        """
        # 验证 constraints 参数
        if constraints:
            n_max = constraints.get("n_max")
            if n_max is not None and n_max <= 0:
                return ToolResult.fail("n_max must be positive")
            n_min = constraints.get("n_min")
            if n_min is not None and n_min < 0:
                return ToolResult.fail("n_min must be non-negative")
            if n_max is not None and n_min is not None and n_min > n_max:
                return ToolResult.fail("n_min cannot be greater than n_max")

            t_max = constraints.get("t_max")
            if t_max is not None and t_max <= 0:
                return ToolResult.fail("t_max must be positive")

            sum_n_max = constraints.get("sum_n_max")
            if sum_n_max is not None and sum_n_max <= 0:
                return ToolResult.fail("sum_n_max must be positive")

            if n_max is not None and sum_n_max is not None and n_max > sum_n_max:
                return ToolResult.fail("n_max cannot be greater than sum_n_max")
            if t_max is not None and sum_n_max is not None and t_max > sum_n_max:
                return ToolResult.fail("t_max cannot be greater than sum_n_max")

        # 验证 test_configs 参数
        if test_configs:
            for i, config in enumerate(test_configs):
                if "type" not in config:
                    return ToolResult.fail(f"test_configs[{i}]: 'type' is required")
                if config["type"] not in ("1", "2", "3", "4"):
                    return ToolResult.fail(
                        f"test_configs[{i}]: 'type' must be one of '1', '2', '3', '4'"
                    )

                for field in ["n_min", "n_max", "t_min", "t_max"]:
                    if field not in config:
                        return ToolResult.fail(f"test_configs[{i}]: '{field}' is required")
                    val = config[field]
                    if not isinstance(val, int) or val < 0:
                        return ToolResult.fail(
                            f"test_configs[{i}]: '{field}' must be a non-negative integer"
                        )

                if config["n_min"] > config["n_max"]:
                    return ToolResult.fail(
                        f"test_configs[{i}]: n_min cannot be greater than n_max"
                    )
                if config["t_min"] > config["t_max"]:
                    return ToolResult.fail(
                        f"test_configs[{i}]: t_min cannot be greater than t_max"
                    )

        exe_ext = get_exe_extension()

        # 检查必要文件
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        sol_exe = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")
        tests_dir = os.path.join(problem_dir, "tests")

        # 如果 files 目录下没有，检查根目录
        if not os.path.exists(gen_exe):
            gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")
        if not os.path.exists(sol_exe):
            sol_exe = os.path.join(problem_dir, f"sol{exe_ext}")
        if not os.path.exists(val_exe):
            val_exe = os.path.join(problem_dir, f"val{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")
        if not os.path.exists(sol_exe):
            return ToolResult.fail("sol not found. Run solution_build first.")

        # Validator 是否可用
        validator_available = enable_validator_filter and os.path.exists(val_exe)

        # 创建/清空 tests 目录
        if os.path.exists(tests_dir):
            shutil.rmtree(tests_dir)
        os.makedirs(tests_dir)

        # 获取测试配置
        if test_configs:
            test_configs_list = [
                (
                    str(c.get("seed_offset", 0)),
                    c["type"],
                    str(c["n_min"]),
                    str(c["n_max"]),
                    str(c["t_min"]),
                    str(c["t_max"]),
                )
                for c in test_configs
            ]
        else:
            test_configs_list = self._get_default_configs(constraints)

        # 计算需要生成的候选数量
        candidate_count = int(test_count * oversample_ratio)

        # 生成候选数据
        candidates: list[CandidateTest] = []
        signatures: set[str] = set()  # 用于去重
        errors: list[tuple[int, str]] = []
        seed = 1

        while len(candidates) < candidate_count and seed < candidate_count * 10:
            # 循环使用配置
            cfg_idx = (seed - 1) % len(test_configs_list)
            test_cfg = test_configs_list[cfg_idx]

            seed_offset, type_param, n_min, n_max, t_min, t_max = test_cfg
            cmd_args = [
                str(seed + int(seed_offset)),
                type_param,
                str(n_min),
                str(n_max),
                str(t_min),
                str(t_max),
            ]

            try:
                # 生成输入
                gen_result = await run_binary_with_args(gen_exe, cmd_args, timeout=timeout)
                if gen_result.timed_out or not gen_result.stdout.strip():
                    errors.append((seed, f"Generator failed: {gen_result.stderr}"))
                    seed += 1
                    continue

                input_data = gen_result.stdout

                # 去重检查
                if enable_dedup:
                    sig = hashlib.md5(input_data.encode()).hexdigest()
                    if sig in signatures:
                        seed += 1
                        continue
                    signatures.add(sig)
                else:
                    sig = hashlib.md5(input_data.encode()).hexdigest()

                # Validator 过滤
                if validator_available:
                    val_result = await run_binary(val_exe, input_data, timeout=timeout)
                    if val_result.return_code != 0:
                        # 输入无效，跳过
                        seed += 1
                        continue

                # 生成答案
                sol_result = await run_binary(sol_exe, input_data, timeout=timeout)
                if not sol_result.success:
                    errors.append((seed, f"sol failed: {sol_result.stderr}"))
                    seed += 1
                    continue

                candidates.append(
                    CandidateTest(
                        input_data=input_data,
                        output_data=sol_result.stdout,
                        type_param=type_param,
                        signature=sig,
                    )
                )

            except Exception as e:
                errors.append((seed, str(e)))

            seed += 1

        # 平衡分布和采样
        if enable_balance and len(candidates) > test_count:
            final_tests = self._balance_and_sample(candidates, test_count)
        elif len(candidates) > test_count:
            # 简单确定性采样（按 signature 排序）
            sorted_candidates = sorted(candidates, key=lambda c: c.signature)
            final_tests = sorted_candidates[:test_count]
        else:
            final_tests = candidates

        # 写入文件
        generated_tests = []
        for i, candidate in enumerate(final_tests, 1):
            test_file = os.path.join(tests_dir, f"{i:02d}.in")
            ans_file = os.path.join(tests_dir, f"{i:02d}.ans")

            with open(test_file, "w", encoding="utf-8") as f:
                f.write(candidate.input_data)
            with open(ans_file, "w", encoding="utf-8") as f:
                f.write(candidate.output_data)

            generated_tests.append(i)

        # 统计信息
        type_counts: dict[str, int] = {}
        for c in final_tests:
            type_counts[c.type_param] = type_counts.get(c.type_param, 0) + 1

        type_names = {"1": "tiny", "2": "random", "3": "extreme", "4": "tle"}
        type_distribution = {
            type_names.get(k, k): v for k, v in type_counts.items()
        }

        if len(generated_tests) == test_count:
            return ToolResult.ok(
                tests_dir=tests_dir,
                generated_tests=generated_tests,
                type_distribution=type_distribution,
                dedup_enabled=enable_dedup,
                validator_filter_enabled=validator_available,
                balance_enabled=enable_balance,
                candidates_generated=len(candidates),
                message=f"Generated {len(generated_tests)} test cases (from {len(candidates)} candidates)",
            )
        else:
            return ToolResult.fail(
                f"Partial generation: {len(generated_tests)}/{test_count}",
                generated_tests=generated_tests,
                errors=errors,
            )

    def _balance_and_sample(
        self, candidates: list[CandidateTest], target_count: int
    ) -> list[CandidateTest]:
        """平衡分布并采样。

        确保各策略类型的测试数据数量均衡。
        使用确定性排序保证结果可重现。
        """
        # 按类型分组
        by_type: dict[str, list[CandidateTest]] = {}
        for c in candidates:
            if c.type_param not in by_type:
                by_type[c.type_param] = []
            by_type[c.type_param].append(c)

        # 计算每种类型应该采样多少
        num_types = len(by_type)
        if num_types == 0:
            return []

        base_count = target_count // num_types
        remainder = target_count % num_types

        result: list[CandidateTest] = []
        type_order = sorted(by_type.keys())  # 确保确定性

        for i, type_param in enumerate(type_order):
            type_candidates = by_type[type_param]
            # 前 remainder 个类型多分配一个
            count = base_count + (1 if i < remainder else 0)
            # 使用确定性排序而非随机采样，保证结果可重现
            sorted_candidates = sorted(type_candidates, key=lambda c: c.signature)
            if len(sorted_candidates) <= count:
                result.extend(sorted_candidates)
            else:
                result.extend(sorted_candidates[:count])

        return result[:target_count]

    def _get_default_configs(
        self, constraints: dict | None = None
    ) -> list[tuple[str, str, str, str, str, str]]:
        """获取默认测试配置。

        Args:
            constraints: 题目约束条件，包含 n_max, t_max, sum_n_max 等

        Returns:
            配置列表，每项为 (seed_offset, type, n_min, n_max, t_min, t_max)
        """
        # 从约束中获取极限值
        n_limit = constraints.get("n_max", 100000) if constraints else 100000
        t_limit = constraints.get("t_max", 1) if constraints else 1
        sum_n_limit = constraints.get("sum_n_max", n_limit) if constraints else n_limit

        configs = []

        # 1. 边界情况 (type=1 tiny) - 最小值和极小值
        configs.extend(
            [
                ("0", "1", "1", "1", "1", "1"),  # N=1, T=1
                ("1", "1", "1", "1", str(t_limit), str(t_limit)),  # N=1, T=max
                ("2", "1", "2", "2", "1", "1"),  # N=2
            ]
        )

        # 2. 小数据随机 (type=2 random)
        configs.extend(
            [
                ("3", "2", "1", "10", "1", str(min(3, t_limit))),
                ("4", "2", "10", "100", "1", str(min(3, t_limit))),
            ]
        )

        # 3. 中等数据
        mid_n = n_limit // 2
        configs.extend(
            [
                ("5", "2", "100", str(mid_n // 10), "1", str(min(3, t_limit))),
                ("6", "2", str(mid_n // 10), str(mid_n), "1", str(min(2, t_limit))),
            ]
        )

        # 4. 大数据随机 (type=2)
        if n_limit >= 10000:
            configs.extend(
                [
                    ("7", "2", str(mid_n), str(n_limit), "1", "1"),
                    ("8", "2", str(int(n_limit * 0.8)), str(n_limit), "1", "1"),
                ]
            )

        # 5. 极限数据 (type=3 extreme) - 接近上限
        configs.extend(
            [
                ("9", "3", str(n_limit), str(n_limit), "1", "1"),  # N=max
                ("10", "3", str(n_limit - 1), str(n_limit), "1", "1"),  # N=max-1
                ("11", "3", str(int(n_limit * 0.99)), str(n_limit), "1", "1"),  # 接近极限
            ]
        )

        # 6. T 极限情况
        if t_limit > 1:
            # T=max, N 根据sum约束调整
            n_per_test = min(n_limit, sum_n_limit // t_limit) if sum_n_limit else n_limit
            configs.append(
                ("12", "3", str(max(1, n_per_test // 2)), str(n_per_test), str(t_limit), str(t_limit))
            )

        # 7. TLE 诱导数据 (type=4)
        if n_limit >= 100:
            configs.extend(
                [
                    ("13", "4", str(n_limit), str(n_limit), "1", "1"),
                    ("14", "4", str(int(n_limit * 0.9)), str(n_limit), "1", "1"),
                ]
            )

        return configs


class ProblemPackPolygonTool(Tool):
    """打包为 Polygon 格式。"""

    @property
    def name(self) -> str:
        return "problem_pack_polygon"

    @property
    def description(self) -> str:
        return """将题目打包为 Polygon 格式。

        整理文件到 Polygon 标准目录结构：
        - files/: testlib.h, gen.cpp, val.cpp
        - solutions/: sol.cpp, brute.cpp
        - statements/: README.md
        - tests/: 测试数据
        - problem.xml: 配置文件
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
                "time_limit": {
                    "type": "integer",
                    "description": "时间限制（秒）",
                    "default": 1,
                },
                "memory_limit": {
                    "type": "integer",
                    "description": "内存限制（MB）",
                    "default": 256,
                },
            },
            "required": ["problem_dir"],
        }

    async def execute(
        self,
        problem_dir: str,
        time_limit: int = 1,
        memory_limit: int = 256,
    ) -> ToolResult:
        """执行 Polygon 打包。"""
        if not os.path.exists(problem_dir):
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        # 转换单位：秒 -> 毫秒，MB -> 字节
        time_limit_ms = time_limit * 1000
        memory_limit_bytes = memory_limit * 1024 * 1024

        results = {
            "files_copied": [],
            "files_removed": [],
            "directories_created": [],
        }

        # 1. 创建目录
        directories = ["files", "solutions", "statements", "scripts"]
        for dir_name in directories:
            dir_path = os.path.join(problem_dir, dir_name)
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                results["directories_created"].append(dir_name)

        # 2. 移动文件到 files/
        files_dir = os.path.join(problem_dir, "files")
        for src in ["testlib.h", "gen.cpp", "val.cpp"]:
            src_path = os.path.join(problem_dir, src)
            if os.path.exists(src_path):
                dst_path = os.path.join(files_dir, src)
                shutil.copy2(src_path, dst_path)
                results["files_copied"].append(f"{src} -> files/{src}")

        # 3. 移动文件到 solutions/
        solutions_dir = os.path.join(problem_dir, "solutions")
        for src in ["sol.cpp", "brute.cpp"]:
            src_path = os.path.join(problem_dir, src)
            if os.path.exists(src_path):
                dst_path = os.path.join(solutions_dir, src)
                shutil.copy2(src_path, dst_path)
                results["files_copied"].append(f"{src} -> solutions/{src}")

        # 4. 移动 README.md
        statements_dir = os.path.join(problem_dir, "statements")
        readme_src = os.path.join(problem_dir, "README.md")
        if os.path.exists(readme_src):
            readme_dst = os.path.join(statements_dir, "README.md")
            shutil.copy2(readme_src, readme_dst)
            results["files_copied"].append("README.md -> statements/README.md")

        # 5. 创建 problem.xml
        problem_xml = os.path.join(problem_dir, "problem.xml")
        if not os.path.exists(problem_xml):
            # 动态计算测试数量
            tests_dir = os.path.join(problem_dir, "tests")
            if os.path.exists(tests_dir):
                test_files = [f for f in os.listdir(tests_dir) if f.endswith(".in")]
                actual_test_count = len(test_files)
            else:
                actual_test_count = 0

            problem_name = os.path.basename(problem_dir)
            xml_content = f'''<?xml version="1.0" encoding="utf-8" standalone="no"?>
<problem revision="1" short-name="{problem_name}">
    <names>
        <name language="chinese" value="{problem_name}"/>
    </names>
    <statements>
        <statement charset="UTF-8" language="chinese" mathjax="true" path="statements/README.md" type="application/x-tex"/>
    </statements>
    <judging>
        <testset name="tests">
            <time-limit>{time_limit_ms}</time-limit>
            <memory-limit>{memory_limit_bytes}</memory-limit>
            <test-count>{actual_test_count}</test-count>
            <input-path-pattern>tests/%02d.in</input-path-pattern>
            <answer-path-pattern>tests/%02d.ans</answer-path-pattern>
        </testset>
    </judging>
    <files>
        <resources>
            <file path="files/testlib.h"/>
        </resources>
        <executables>
            <executable>
                <source path="files/gen.cpp"/>
            </executable>
            <executable>
                <source path="files/val.cpp"/>
            </executable>
        </executables>
    </files>
    <assets>
        <solutions>
            <solution tag="main">
                <source path="solutions/sol.cpp"/>
            </solution>
            <solution tag="rejected">
                <source path="solutions/brute.cpp"/>
            </solution>
        </solutions>
    </assets>
</problem>
'''
            with open(problem_xml, "w", encoding="utf-8") as f:
                f.write(xml_content)
            results["files_copied"].append("problem.xml (created)")

        return ToolResult.ok(
            results=results,
            message="Packed to Polygon format",
        )
