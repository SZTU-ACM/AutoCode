"""
Problem 工具组 - 题目管理。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import time
from dataclasses import dataclass
from xml.sax.saxutils import escape

from ..utils.compiler import RunResult, run_binary, run_binary_with_args
from ..utils.platform import get_exe_extension
from ..workflow import default_manifest, save_manifest
from .base import Tool, ToolResult


@dataclass
class CandidateTest:
    """候选测试数据。"""

    input_data: str
    output_data: str
    type_param: str  # 1=tiny, 2=random, 3=extreme, 4=tle
    signature: str


# 最终测试集中「极限类」占比下限：至少一半来自 generator type 3/4（extreme + TLE 压力）
_LIMIT_STRATEGY_TYPES = frozenset({"3", "4"})
_TEST_MANIFEST_FILENAME = ".autocode_tests_manifest.json"
_GENERATE_STATE_FILENAME = ".autocode_generate_state.json"
_POSIX_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _normalize_answer_ext_value(answer_ext: str | None) -> str | None:
    ext = (answer_ext or "").strip()
    if not ext:
        return None
    if not ext.startswith("."):
        ext = f".{ext}"
    if not any(ch != "." for ch in ext[1:]):
        return None
    if any(ch in ext for ch in ('/', '\\', ':', '*', '?', '"', "<", ">", "|", "&")):
        return None
    if ext == ".in":
        return None
    return ext


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
                "interactive": {
                    "type": "boolean",
                    "description": "是否为交互题",
                    "default": False,
                },
            },
            "required": ["problem_dir", "problem_name"],
        }

    async def execute(
        self,
        problem_dir: str,
        problem_name: str,
        interactive: bool = False,
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
                "and place it in src/autocode_mcp/templates/."
            )

        # 创建基础 README.md
        readme_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                f.write(
                    f"# {problem_name}\n\n"
                    "## 题目描述\n\n请补充题目背景与目标。\n\n"
                    "## 输入格式\n\n请补充输入格式。\n\n"
                    "## 输出格式\n\n请补充输出格式。\n\n"
                    "## 样例\n\n请补充样例输入输出。\n"
                )

        tutorial_path = os.path.join(problem_dir, "statements", "tutorial.md")
        if not os.path.exists(tutorial_path):
            with open(tutorial_path, "w", encoding="utf-8") as f:
                f.write(
                    f"# {problem_name} 题解\n\n"
                    "## 思路概述\n\n请补充核心思路。\n\n"
                    "## 正确性说明\n\n请补充关键证明。\n\n"
                    "## 复杂度分析\n\n请补充时间与空间复杂度。\n"
                )

        manifest = default_manifest(problem_name=problem_name, interactive=interactive)
        manifest_file = save_manifest(problem_dir, manifest)

        return ToolResult.ok(
            problem_dir=problem_dir,
            problem_name=problem_name,
            interactive=interactive,
            manifest_path=str(manifest_file),
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
        - 最终测试集中至少一半为极限类（generator type=3 extreme 与 type=4 tle），在候选不足时可能无法完全满足

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
                    "description": "题目约束条件。不提供 test_configs 时，系统将根据 constraints 自动生成覆盖边界、随机、极限、TLE 诱导等策略的测试配置（smart mode）",
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
                            "extra_args": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "附加命令行参数，追加在标准 6 参数之后传递给 generator",
                            },
                        },
                        "required": ["type", "n_min", "n_max", "t_min", "t_max"],
                    },
                },
                "output_dir": {
                    "type": "string",
                    "description": "测试数据输出目录路径，默认为 problem_dir/tests。必须位于 problem_dir 下，且不能是题目根目录或 files/solutions/statements 等保留目录",
                },
                "sol_name": {
                    "type": "string",
                    "description": "标准解法文件名（不含扩展名），默认 'sol'",
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
                    "description": "启用平衡分布：在已满足「至少一半为 extreme/tle」后，将剩余名额在各非极限类型间尽量均衡分配；关闭时剩余名额按确定性的 (type_param, signature) 顺序填充",
                    "default": True,
                },
                "oversample_ratio": {
                    "type": "number",
                    "description": "超额采样比例（生成候选数据 = test_count * ratio）",
                    "default": 1.5,
                },
                "answer_ext": {
                    "type": "string",
                    "description": "答案文件后缀，默认 .ans，可配置为 .out",
                    "default": ".ans",
                },
                "resume": {
                    "type": "boolean",
                    "description": "是否尝试从状态文件恢复中断任务",
                    "default": False,
                },
                "hard_timeout_seconds": {
                    "type": "integer",
                    "description": "工具级硬超时（秒），超时后保存状态并返回失败",
                },
                "checkpoint_every": {
                    "type": "integer",
                    "description": "每生成多少候选写一次 checkpoint，默认 10",
                    "default": 10,
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
        output_dir: str | None = None,
        sol_name: str | None = None,
        enable_dedup: bool = True,
        enable_validator_filter: bool = True,
        enable_balance: bool = True,
        oversample_ratio: float = 1.5,
        answer_ext: str = ".ans",
        resume: bool = False,
        hard_timeout_seconds: int | None = None,
        checkpoint_every: int = 10,
    ) -> ToolResult:
        """执行测试数据生成。

        实现论文 Algorithm 2 的后处理步骤：
        1. 生成超额候选数据
        2. 去重（基于 MD5 signature）
        3. Validator 过滤（自动检测 val.exe）
        4. 采样：至少一半为 type=3/4（极限 + TLE 压力），其余再平衡或按签名排序
        5. 输出最终 test_count 个
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
        effective_sol_name = sol_name or "sol"
        normalized_answer_ext, answer_ext_error = self._normalize_answer_ext(answer_ext)
        if answer_ext_error:
            return answer_ext_error
        assert normalized_answer_ext is not None
        checkpoint_every = max(1, checkpoint_every)

        # 检查必要文件
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        sol_exe = os.path.join(problem_dir, "solutions", f"{effective_sol_name}{exe_ext}")
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")

        # 解析输出目录
        tests_dir, tests_dir_error = self._resolve_tests_dir(problem_dir, output_dir)
        if tests_dir_error:
            return tests_dir_error

        # 如果 files 目录下没有，检查根目录
        if not os.path.exists(gen_exe):
            gen_exe = os.path.join(problem_dir, f"gen{exe_ext}")
        if not os.path.exists(sol_exe):
            sol_exe = os.path.join(problem_dir, f"{effective_sol_name}{exe_ext}")
        if not os.path.exists(val_exe):
            val_exe = os.path.join(problem_dir, f"val{exe_ext}")

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")
        if not os.path.exists(sol_exe):
            return ToolResult.fail(f"{effective_sol_name} not found. Run solution_build first.")

        # Validator 是否可用
        validator_available = enable_validator_filter and os.path.exists(val_exe)

        state_path = os.path.join(tests_dir, _GENERATE_STATE_FILENAME)
        start_ts = time.time()

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
                    [str(a) for a in c.get("extra_args", [])],
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
        progress_snapshot = {
            "phase": "initializing",
            "candidates_generated": 0,
            "target_candidates": candidate_count,
            "generated_tests": 0,
            "test_count": test_count,
            "state_path": state_path,
        }
        active_pids: set[int] = set()

        if resume:
            restored = self._load_state(state_path)
            if restored:
                seed = int(restored.get("next_seed", 1))
                candidates = self._restore_candidates(restored.get("candidates", []))
                signatures = {c.signature for c in candidates}
                errors = [(int(e.get("seed", 0)), str(e.get("error", ""))) for e in restored.get("errors", []) if isinstance(e, dict)]
                raw_active_pids = restored.get("active_pids", [])
                if isinstance(raw_active_pids, list):
                    active_pids = {int(pid) for pid in raw_active_pids if isinstance(pid, int)}
                progress_snapshot["phase"] = str(restored.get("phase", "resumed"))
                progress_snapshot["candidates_generated"] = len(candidates)
            else:
                # resume=true 但状态文件不存在/损坏时，回退到 fresh run，
                # 避免与旧测试文件混合导致 manifest 覆盖不完整。
                clear_error = self._clear_generated_tests(tests_dir, normalized_answer_ext)
                if clear_error:
                    return clear_error
                progress_snapshot["phase"] = "resume_fallback_fresh"
                progress_snapshot["resume_fallback"] = True
        else:
            # 创建/清空 tests 目录。只移除旧的测试数据，避免误删用户源码或其他文件。
            clear_error = self._clear_generated_tests(tests_dir, normalized_answer_ext)
            if clear_error:
                return clear_error

        while len(candidates) < candidate_count and seed < candidate_count * 10:
            elapsed = time.time() - start_ts
            if hard_timeout_seconds and elapsed >= hard_timeout_seconds:
                self._save_state(
                    state_path,
                    phase="timed_out",
                    next_seed=seed,
                    candidates=candidates,
                    errors=errors,
                    answer_ext=normalized_answer_ext,
                    active_pids=active_pids,
                    message="Hard timeout reached",
                )
                return ToolResult.fail(
                    f"Generation timed out after {hard_timeout_seconds}s",
                    generated_tests=[],
                    errors=errors,
                    sol_name=effective_sol_name,
                    progress_snapshot=progress_snapshot,
                    resume_hint="Set resume=true to continue from checkpoint",
                )
            # 循环使用配置
            cfg_idx = (seed - 1) % len(test_configs_list)
            test_cfg = test_configs_list[cfg_idx]

            seed_offset, type_param, n_min, n_max, t_min, t_max, extra_args = test_cfg
            cmd_args = [
                str(seed + int(seed_offset)),
                type_param,
                str(n_min),
                str(n_max),
                str(t_min),
                str(t_max),
            ] + extra_args

            try:
                # 生成输入
                gen_result = await self._run_with_retry(
                    gen_exe,
                    cmd_args,
                    timeout=timeout,
                    active_pids=active_pids,
                )
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

            except asyncio.CancelledError:
                self._save_state(
                    state_path,
                    phase="cancelled",
                    next_seed=seed,
                    candidates=candidates,
                    errors=errors,
                    answer_ext=normalized_answer_ext,
                    active_pids=active_pids,
                    message="Cancelled by upstream request",
                )
                raise
            except Exception as e:
                errors.append((seed, str(e)))

            progress_snapshot["phase"] = "candidate_generation"
            progress_snapshot["candidates_generated"] = len(candidates)
            if len(candidates) % checkpoint_every == 0:
                self._save_state(
                    state_path,
                    phase="candidate_generation",
                    next_seed=seed + 1,
                    candidates=candidates,
                    errors=errors,
                    answer_ext=normalized_answer_ext,
                    active_pids=active_pids,
                )
            seed += 1

        # 极限占比 + 平衡/确定性采样
        progress_snapshot["phase"] = "sampling"
        if len(candidates) > test_count:
            final_tests = self._balance_and_sample(
                candidates, test_count, balance_remainder=enable_balance
            )
        else:
            final_tests = candidates

        # 最终写盘前清理历史生成产物，防止 resume 场景残留旧编号样例。
        clear_before_write_error = self._clear_generated_tests(tests_dir, normalized_answer_ext)
        if clear_before_write_error:
            return clear_before_write_error

        # 写入文件
        generated_tests = []
        test_manifest: list[dict[str, str | int]] = []
        for i, candidate in enumerate(final_tests, 1):
            test_file = os.path.join(tests_dir, f"{i:02d}.in")
            ans_file = os.path.join(tests_dir, f"{i:02d}{normalized_answer_ext}")

            with open(test_file, "w", encoding="utf-8") as f:
                f.write(candidate.input_data)
            with open(ans_file, "w", encoding="utf-8") as f:
                f.write(candidate.output_data)

            generated_tests.append(i)
            test_manifest.append(
                {
                    "index": i,
                    "in_file": f"{i:02d}.in",
                    "ans_file": f"{i:02d}{normalized_answer_ext}",
                    "type_param": candidate.type_param,
                    "signature": candidate.signature,
                }
            )

        manifest_path = os.path.join(tests_dir, _TEST_MANIFEST_FILENAME)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "answer_ext": normalized_answer_ext,
                    "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
                    "tests": test_manifest,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        if os.path.exists(state_path):
            os.remove(state_path)
        # 统计信息
        type_counts: dict[str, int] = {}
        for c in final_tests:
            type_counts[c.type_param] = type_counts.get(c.type_param, 0) + 1

        type_names = {"1": "tiny", "2": "random", "3": "extreme", "4": "tle"}
        type_distribution = {
            type_names.get(k, k): v for k, v in type_counts.items()
        }

        limit_in_final = sum(1 for c in final_tests if c.type_param in _LIMIT_STRATEGY_TYPES)
        limit_minimum = (len(final_tests) + 1) // 2 if final_tests else 0
        limit_quota_met = len(final_tests) == 0 or limit_in_final >= limit_minimum

        if len(generated_tests) == test_count:
            return ToolResult.ok(
                tests_dir=tests_dir,
                generated_tests=generated_tests,
                type_distribution=type_distribution,
                dedup_enabled=enable_dedup,
                validator_filter_enabled=validator_available,
                balance_enabled=enable_balance,
                limit_case_count=limit_in_final,
                limit_case_minimum_required=limit_minimum,
                limit_case_quota_met=limit_quota_met,
                candidates_generated=len(candidates),
                sol_name=effective_sol_name,
                answer_ext=normalized_answer_ext,
                progress_snapshot=progress_snapshot,
                message=f"Generated {len(generated_tests)} test cases (from {len(candidates)} candidates)",
            )
        else:
            self._save_state(
                state_path,
                phase="partial",
                next_seed=seed,
                candidates=candidates,
                errors=errors,
                answer_ext=normalized_answer_ext,
                active_pids=active_pids,
                message="Partial generation result",
            )
            return ToolResult.fail(
                f"Partial generation: {len(generated_tests)}/{test_count}",
                generated_tests=generated_tests,
                errors=errors,
                sol_name=effective_sol_name,
                answer_ext=normalized_answer_ext,
                progress_snapshot=progress_snapshot,
                resume_hint="Set resume=true to continue from checkpoint",
                limit_case_count=limit_in_final,
                limit_case_minimum_required=limit_minimum,
                limit_case_quota_met=limit_quota_met,
            )

    def _resolve_tests_dir(
        self,
        problem_dir: str,
        output_dir: str | None,
    ) -> tuple[str | None, ToolResult | None]:
        """解析并校验测试输出目录，防止清理时误删题目文件或外部目录。"""
        problem_root = os.path.realpath(problem_dir)
        raw_output_dir = output_dir or "tests"
        tests_dir = raw_output_dir
        if not os.path.isabs(tests_dir):
            tests_dir = os.path.join(problem_root, tests_dir)
        tests_dir = os.path.abspath(tests_dir)
        resolved_tests_dir = os.path.realpath(tests_dir)

        try:
            common = os.path.commonpath([problem_root, resolved_tests_dir])
        except ValueError:
            common = ""
        if os.path.normcase(common) != os.path.normcase(problem_root):
            return None, ToolResult.fail("output_dir must be inside problem_dir")

        if os.path.normcase(resolved_tests_dir) == os.path.normcase(problem_root):
            return None, ToolResult.fail("output_dir cannot be the problem_dir root")

        reserved_dirs = {"files", "solutions", "statements"}
        for reserved in reserved_dirs:
            reserved_path = os.path.realpath(os.path.join(problem_root, reserved))
            try:
                reserved_common = os.path.commonpath([reserved_path, resolved_tests_dir])
            except ValueError:
                reserved_common = ""
            if os.path.normcase(reserved_common) == os.path.normcase(reserved_path):
                return None, ToolResult.fail(f"output_dir cannot be reserved directory: {reserved}")

        if os.path.exists(tests_dir) and os.path.islink(tests_dir):
            return None, ToolResult.fail(f"output_dir cannot be a symlink: {tests_dir}")

        if os.path.exists(tests_dir) and not os.path.isdir(tests_dir):
            return None, ToolResult.fail(f"output_dir exists and is not a directory: {tests_dir}")

        return tests_dir, None

    def _clear_generated_tests(self, tests_dir: str, answer_ext: str = ".ans") -> ToolResult | None:
        """创建测试目录并清理旧的 .in/.answer_ext 文件。"""
        os.makedirs(tests_dir, exist_ok=True)
        for filename in os.listdir(tests_dir):
            if not (
                filename.endswith(".in")
                or filename.endswith(answer_ext)
                or filename == _TEST_MANIFEST_FILENAME
                or filename == _GENERATE_STATE_FILENAME
            ):
                continue
            path = os.path.join(tests_dir, filename)
            if os.path.isfile(path):
                os.remove(path)
        return None

    def _normalize_answer_ext(self, answer_ext: str) -> tuple[str | None, ToolResult | None]:
        ext = _normalize_answer_ext_value(answer_ext or ".ans")
        if not ext:
            return None, ToolResult.fail("invalid answer_ext")
        return ext, None

    async def _run_with_retry(
        self,
        binary_path: str,
        args: list[str],
        timeout: int,
        active_pids: set[int],
    ) -> RunResult:
        last_result: RunResult | None = None
        for attempt in range(3):
            started_pid: int | None = None
            cancelled = False

            def _on_start(pid: int) -> None:
                nonlocal started_pid
                started_pid = pid
                active_pids.add(pid)

            try:
                last_result = await run_binary_with_args(
                    binary_path,
                    args,
                    timeout=timeout,
                    process_start_hook=_on_start,
                )
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                # 取消路径保留 PID 到状态文件，供 cleanup 精准回收。
                if started_pid is not None and not cancelled:
                    active_pids.discard(started_pid)
            if last_result.success:
                return last_result
            await asyncio.sleep(0.1 * (2**attempt))
        if last_result is not None:
            return last_result
        return RunResult(success=False, error="Generator execution returned no result")

    def _save_state(
        self,
        state_path: str,
        *,
        phase: str,
        next_seed: int,
        candidates: list[CandidateTest],
        errors: list[tuple[int, str]],
        answer_ext: str,
        active_pids: set[int] | None = None,
        message: str | None = None,
    ) -> None:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "phase": phase,
                    "next_seed": next_seed,
                    "answer_ext": answer_ext,
                    "message": message,
                    "active_pids": sorted(active_pids or []),
                    "candidates": [
                        {
                            "input_data": c.input_data,
                            "output_data": c.output_data,
                            "type_param": c.type_param,
                            "signature": c.signature,
                        }
                        for c in candidates
                    ],
                    "errors": [{"seed": seed, "error": err} for seed, err in errors[-200:]],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def _load_state(self, state_path: str) -> dict | None:
        if not os.path.exists(state_path):
            return None
        try:
            with open(state_path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def _restore_candidates(self, raw_candidates: list[dict]) -> list[CandidateTest]:
        out: list[CandidateTest] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            out.append(
                CandidateTest(
                    input_data=str(item.get("input_data", "")),
                    output_data=str(item.get("output_data", "")),
                    type_param=str(item.get("type_param", "2")),
                    signature=str(item.get("signature", "")),
                )
            )
        return out


    def _balance_and_sample(
        self,
        candidates: list[CandidateTest],
        target_count: int,
        balance_remainder: bool = True,
    ) -> list[CandidateTest]:
        """采样：至少一半为极限类（type 3/4），其余再分配。

        先取不少于 ceil(target_count/2) 条来自 extreme/tle 的候选（若候选不足则全取），
        再在剩余候选中填满 target_count；剩余部分在 balance_remainder 为真时在
        各类型间均衡，否则按 (type_param, signature) 确定性排序依次选取。
        """
        if target_count <= 0 or not candidates:
            return []

        need_limit = (target_count + 1) // 2  # 不少于一半（向上取整到整数条数）
        extreme_pool = sorted(
            [c for c in candidates if c.type_param in _LIMIT_STRATEGY_TYPES],
            key=lambda c: (c.type_param, c.signature),
        )

        result: list[CandidateTest] = []
        selected_ids: set[int] = set()

        for c in extreme_pool:
            if len(result) >= need_limit:
                break
            cid = id(c)
            if cid in selected_ids:
                continue
            result.append(c)
            selected_ids.add(cid)

        remaining = [c for c in candidates if id(c) not in selected_ids]
        need_more = target_count - len(result)
        if need_more <= 0:
            return result[:target_count]

        if balance_remainder:
            by_type: dict[str, list[CandidateTest]] = {}
            for c in remaining:
                by_type.setdefault(c.type_param, []).append(c)
            for t in by_type:
                by_type[t] = sorted(by_type[t], key=lambda c: c.signature)

            type_order = sorted(by_type.keys())
            if not type_order:
                return result[:target_count]

            num_types = len(type_order)
            base_count = need_more // num_types
            rem = need_more % num_types

            for i, type_param in enumerate(type_order):
                count = base_count + (1 if i < rem else 0)
                for c in by_type[type_param][:count]:
                    cid = id(c)
                    if cid in selected_ids:
                        continue
                    result.append(c)
                    selected_ids.add(cid)
                    if len(result) >= target_count:
                        break
                if len(result) >= target_count:
                    break

            if len(result) < target_count:
                for c in sorted(remaining, key=lambda c: (c.type_param, c.signature)):
                    if len(result) >= target_count:
                        break
                    cid = id(c)
                    if cid in selected_ids:
                        continue
                    result.append(c)
                    selected_ids.add(cid)
        else:
            for c in sorted(remaining, key=lambda c: (c.type_param, c.signature)):
                if len(result) >= target_count:
                    break
                cid = id(c)
                if cid in selected_ids:
                    continue
                result.append(c)
                selected_ids.add(cid)

        return result[:target_count]

    def _get_default_configs(
        self, constraints: dict | None = None
    ) -> list[tuple[str, str, str, str, str, str, list[str]]]:
        """获取默认测试配置。

        Args:
            constraints: 题目约束条件，包含 n_max, t_max, sum_n_max 等

        Returns:
            配置列表，每项为 (seed_offset, type, n_min, n_max, t_min, t_max, extra_args)
        """
        # 从约束中获取极限值
        n_limit = constraints.get("n_max", 100000) if constraints else 100000
        t_limit = constraints.get("t_max", 1) if constraints else 1
        sum_n_limit = constraints.get("sum_n_max", n_limit) if constraints else n_limit

        configs = []

        # 1. 边界情况 (type=1 tiny) - 最小值和极小值
        configs.extend(
            [
                ("0", "1", "1", "1", "1", "1", []),  # N=1, T=1
                ("1", "1", "1", "1", str(t_limit), str(t_limit), []),  # N=1, T=max
                ("2", "1", "2", "2", "1", "1", []),  # N=2
            ]
        )

        # 2. 小数据随机 (type=2 random)
        configs.extend(
            [
                ("3", "2", "1", "10", "1", str(min(3, t_limit)), []),
                ("4", "2", "10", "100", "1", str(min(3, t_limit)), []),
            ]
        )

        # 3. 中等数据
        mid_n = n_limit // 2
        configs.extend(
            [
                ("5", "2", "100", str(mid_n // 10), "1", str(min(3, t_limit)), []),
                ("6", "2", str(mid_n // 10), str(mid_n), "1", str(min(2, t_limit)), []),
            ]
        )

        # 4. 大数据随机 (type=2)
        if n_limit >= 10000:
            configs.extend(
                [
                    ("7", "2", str(mid_n), str(n_limit), "1", "1", []),
                    ("8", "2", str(int(n_limit * 0.8)), str(n_limit), "1", "1", []),
                ]
            )

        # 5. 极限数据 (type=3 extreme) - 接近上限
        configs.extend(
            [
                ("9", "3", str(n_limit), str(n_limit), "1", "1", []),  # N=max
                ("10", "3", str(n_limit - 1), str(n_limit), "1", "1", []),  # N=max-1
                ("11", "3", str(int(n_limit * 0.99)), str(n_limit), "1", "1", []),  # 接近极限
            ]
        )

        # 6. T 极限情况
        if t_limit > 1:
            # T=max, N 根据sum约束调整
            n_per_test = min(n_limit, sum_n_limit // t_limit) if sum_n_limit else n_limit
            configs.append(
                ("12", "3", str(max(1, n_per_test // 2)), str(n_per_test), str(t_limit), str(t_limit), [])
            )

        # 7. TLE 诱导数据 (type=4)
        if n_limit >= 100:
            configs.extend(
                [
                    ("13", "4", str(n_limit), str(n_limit), "1", "1", []),
                    ("14", "4", str(int(n_limit * 0.9)), str(n_limit), "1", "1", []),
                ]
            )

        return configs


class ProblemCleanupProcessesTool(Tool):
    """清理 problem_generate_tests 残留状态和进程。"""

    @property
    def name(self) -> str:
        return "problem_cleanup_processes"

    @property
    def description(self) -> str:
        return "清理生成器残留进程与状态文件。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "problem_dir": {"type": "string", "description": "题目目录路径"},
                "kill_all_generators": {
                    "type": "boolean",
                    "description": "是否尝试清理当前问题任务记录的 generator PID（不会全局按进程名误杀）",
                    "default": False,
                },
            },
            "required": ["problem_dir"],
        }

    async def execute(self, problem_dir: str, kill_all_generators: bool = False) -> ToolResult:
        tests_dir = os.path.join(problem_dir, "tests")
        state_path = os.path.join(tests_dir, _GENERATE_STATE_FILENAME)
        state = self._load_cleanup_state(state_path) or {}
        removed_files: list[str] = []
        pids = state.get("active_pids", []) if isinstance(state, dict) else []
        if not isinstance(pids, list):
            pids = []
        if kill_all_generators:
            try:
                killed: list[int] = []
                failed: list[dict[str, str | int]] = []
                for pid in pids:
                    if not isinstance(pid, int) or pid <= 0:
                        continue
                    if os.name == "nt":
                        proc = await asyncio.create_subprocess_exec(
                            "taskkill",
                            "/PID",
                            str(pid),
                            "/F",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        stdout, stderr = await proc.communicate()
                        if proc.returncode == 0:
                            killed.append(pid)
                        else:
                            failed.append(
                                {
                                    "pid": pid,
                                    "stdout": stdout.decode("utf-8", errors="replace"),
                                    "stderr": stderr.decode("utf-8", errors="replace"),
                                }
                            )
                    else:
                        try:
                            os.kill(pid, _POSIX_KILL_SIGNAL)
                            killed.append(pid)
                        except OSError as exc:
                            failed.append(
                                {
                                    "pid": pid,
                                    "stdout": "",
                                    "stderr": str(exc),
                                }
                            )
                # 仅移除已成功清理的 PID；失败 PID 保留，支持后续重试。
                remaining_pids = [pid for pid in pids if isinstance(pid, int) and pid not in killed]
                self._write_cleanup_state(state_path, remaining_pids)
                return ToolResult.ok(
                    removed_files=removed_files,
                    killed_pids=killed,
                    failed_pids=failed,
                    warning="No tracked generator PID found; skipped process termination" if not pids else "",
                    message="Cleanup finished",
                )
            except Exception as exc:
                return ToolResult.fail(f"cleanup failed: {exc}", removed_files=removed_files)
        # 未请求 kill：不删除 checkpoint，仅返回状态。
        return ToolResult.ok(
            removed_files=removed_files,
            killed_pids=[],
            failed_pids=[],
            warning="",
            message="Cleanup finished",
        )

    def _load_cleanup_state(self, state_path: str) -> dict | None:
        if not os.path.exists(state_path):
            return None
        try:
            with open(state_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            return None
        return None

    def _write_cleanup_state(self, state_path: str, remaining_pids: list[int]) -> None:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        state = self._load_cleanup_state(state_path) or {}
        if not isinstance(state, dict):
            state = {}
        state["active_pids"] = remaining_pids
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


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
                manifest_path = os.path.join(tests_dir, _TEST_MANIFEST_FILENAME)
                answer_ext = ".ans"
                if os.path.exists(manifest_path):
                    try:
                        with open(manifest_path, encoding="utf-8") as mf:
                            manifest = json.load(mf)
                        answer_ext = _normalize_answer_ext_value(str(manifest.get("answer_ext", ".ans"))) or ".ans"
                    except (OSError, json.JSONDecodeError):
                        answer_ext = ".ans"
            else:
                actual_test_count = 0
                answer_ext = ".ans"

            problem_name = os.path.basename(problem_dir)
            xml_problem_name = escape(problem_name, {'"': "&quot;"})
            xml_answer_ext = escape(answer_ext, {'"': "&quot;"})
            xml_content = f'''<?xml version="1.0" encoding="utf-8" standalone="no"?>
<problem revision="1" short-name="{xml_problem_name}">
    <names>
        <name language="chinese" value="{xml_problem_name}"/>
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
            <answer-path-pattern>tests/%02d{xml_answer_ext}</answer-path-pattern>
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
