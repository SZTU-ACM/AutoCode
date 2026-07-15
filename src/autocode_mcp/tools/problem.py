"""
Problem 工具组 - 题目管理。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import time
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..runtime_store import (
    AUDIT,
    GENERATE_CHECKPOINT,
    TEST_MANIFEST,
    WORKFLOW,
    get_section,
    set_section,
    update_section,
)
from ..utils.answer_ext import normalize_answer_ext
from ..utils.compiler import RunResult, run_batch, run_binary, run_binary_with_args
from ..utils.platform import get_exe_extension
from ..utils.process import filter_alive_pids, is_pid_alive, terminate_pid_tree
from ..workflow import (
    check_gates,
    default_manifest,
    load_manifest,
    manifest_uses_testlib_checker,
    save_manifest,
)
from .base import Tool, ToolResult, input_schema_from_model
from .schemas import (
    ProblemCleanupProcessesInput,
    ProblemCreateInput,
    ProblemGenerateTestsInput,
    ProblemPackPolygonInput,
)


@dataclass
class CandidateTest:
    """候选测试数据。"""

    input_data: str
    output_data: str
    type_param: str  # 1=tiny, 2=random, 3=extreme, 4=tle
    signature: str


@dataclass
class _CandidateOutcome:
    """单个候选并发处理的结果。"""

    candidate: CandidateTest | None = None
    error: tuple[int, str] | None = None
    fallback_used: bool = False


# 最终测试集中「极限类」占比下限：至少一半来自 generator type 3/4（extreme + TLE 压力）
_LIMIT_STRATEGY_TYPES = frozenset({"3", "4"})


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
        return input_schema_from_model(ProblemCreateInput)

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

        # 题目级 .gitignore：忽略运行期副产物 .autocode/
        gitignore_path = os.path.join(problem_dir, ".gitignore")
        if not os.path.exists(gitignore_path):
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write(".autocode/\n")

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

        if interactive:
            template_interactor = os.path.join(TEMPLATES_DIR, "interactor_template.cpp")
            dest_interactor = os.path.join(problem_dir, "files", "interactor.cpp")
            if os.path.exists(template_interactor) and not os.path.exists(dest_interactor):
                shutil.copy2(template_interactor, dest_interactor)

        # 创建基础 README.md
        readme_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                if interactive:
                    f.write(
                        f"# {problem_name}\n\n"
                        "## 时间限制与空间限制\n\n"
                        "- 时间限制：\n"
                        "- 空间限制：\n\n"
                        "## 题目背景（可选）\n\n"
                        "如不需要背景，可删除本节。\n\n"
                        "## 题目描述\n\n"
                        "请完整描述选手需要完成的目标、隐藏状态含义、判定标准与关键术语。\n\n"
                        "## 输入格式\n\n"
                        "本题为交互题，选手程序不会获得传统静态输入。请在本节明确交互器内部输入或隐藏参数的范围、随机性来源与总规模约束。\n\n"
                        "## 输出格式\n\n"
                        "请完整定义选手可输出的每一种命令、参数范围、最终答案格式，以及每次输出后的 flush 要求。\n\n"
                        "## 交互协议\n\n"
                        "- 明确交互开始时 judge 首先输出什么，或是否等待选手先查询。\n"
                        "- 明确每种查询格式、judge 响应格式、响应取值含义和查询次数上限。\n"
                        "- 明确最终答案格式、输出最终答案后是否必须立即结束程序。\n"
                        "- 明确非法格式、越界参数、查询超限、提前 EOF、未及时 flush、读到错误响应时的判定。\n"
                        "- 若 judge 可能自适应回答，必须说明哪些不变量始终成立。\n\n"
                        "## 样例\n\n"
                        "交互题样例应写成一次 transcript，而不是传统输入输出。请标明哪些行来自 judge，哪些行来自选手。\n\n"
                        "```text\n"
                        "（请填写样例交互过程）\n"
                        "```\n\n"
                        "## 说明\n\n"
                        "所有样例解释统一写在本节；只需解释有代表性的样例即可。\n"
                    )
                else:
                    f.write(
                        f"# {problem_name}\n\n"
                        "## 时间限制与空间限制\n\n"
                        "- 时间限制：\n"
                        "- 空间限制：\n\n"
                        "## 题目背景（可选）\n\n"
                        "如不需要背景，可删除本节。\n\n"
                        "## 题目描述\n\n"
                        "请完整描述任务目标、判定标准与关键术语。\n\n"
                        "## 输入格式\n\n"
                        "请完整给出输入结构，并在本节明确所有变量范围与总规模约束。\n\n"
                        "## 输出格式\n\n"
                        "请明确输出内容与格式细节。\n\n"
                        "## 样例\n\n"
                        "### 样例输入 #1\n\n"
                        "```\n"
                        "（请填写样例输入）\n"
                        "```\n\n"
                        "### 样例输出 #1\n\n"
                        "```\n"
                        "（请填写样例输出）\n"
                        "```\n\n"
                        "## 说明\n\n"
                        "所有样例解释统一写在本节；只需解释有代表性的样例即可。\n"
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
        return input_schema_from_model(ProblemGenerateTestsInput)

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
        concurrency_limit: int = 4,
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
        concurrency_limit = max(1, int(concurrency_limit))

        # 检查必要文件
        gen_exe = os.path.join(problem_dir, "files", f"gen{exe_ext}")
        sol_exe = os.path.join(problem_dir, "solutions", f"{effective_sol_name}{exe_ext}")
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")

        # 解析输出目录
        tests_dir, tests_dir_error = self._resolve_tests_dir(problem_dir, output_dir)
        if tests_dir_error:
            return tests_dir_error
        self._mark_tests_unverified(problem_dir)

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
            "state_path": os.path.join(problem_dir, ".autocode", "runtime.json"),
        }
        active_pids: set[int] = set()
        generator_tle_extra_args_fallbacks = 0

        if resume:
            restored = self._load_state(problem_dir)
            if restored:
                seed = int(restored.get("next_seed", 1))
                candidates = self._restore_candidates(restored.get("candidates", []))
                signatures = {c.signature for c in candidates}
                errors = [(int(e.get("seed", 0)), str(e.get("error", ""))) for e in restored.get("errors", []) if isinstance(e, dict)]
                raw_active_pids = restored.get("active_pids", [])
                if isinstance(raw_active_pids, list):
                    # resume 时过滤掉已退出的残留 PID，只保留仍存活者供后续 cleanup。
                    active_pids = set(
                        filter_alive_pids(int(pid) for pid in raw_active_pids if isinstance(pid, int))
                    )
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
                    problem_dir,
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
                    generator_tle_extra_args_fallbacks=generator_tle_extra_args_fallbacks,
                )

            # 凑一批待处理候选（最多 concurrency_limit 个），有限并发处理，保持种子顺序。
            batch: list[tuple[int, tuple[str, str, str, str, str, str, list[str]]]] = []
            while (
                len(batch) < concurrency_limit
                and seed < candidate_count * 10
                and len(candidates) + len(batch) < candidate_count
            ):
                cfg_idx = (seed - 1) % len(test_configs_list)
                test_cfg = test_configs_list[cfg_idx]
                batch.append((seed, test_cfg))
                seed += 1

            def _make_worker(
                current_val_exe: str, current_validator_available: bool
            ) -> Callable[[tuple[int, Any]], Awaitable[Any]]:
                async def _worker(item: tuple[int, Any]) -> Any:
                    s, cfg = item
                    return await self._process_candidate(
                        s,
                        cfg,
                        gen_exe,
                        sol_exe,
                        current_val_exe,
                        timeout,
                        current_validator_available,
                        active_pids,
                    )

                return _worker

            try:
                outcomes = await run_batch(
                    batch,
                    _make_worker(val_exe, validator_available),
                    limit=concurrency_limit,
                )
            except asyncio.CancelledError:
                self._save_state(
                    problem_dir,
                    phase="cancelled",
                    next_seed=seed,
                    candidates=candidates,
                    errors=errors,
                    answer_ext=normalized_answer_ext,
                    active_pids=active_pids,
                    message="Cancelled by upstream request",
                )
                raise

            # 按种子顺序合并结果，确保与串行实现产物一致。
            for (_seed_val, _cfg), outcome in zip(batch, outcomes, strict=True):
                if outcome.candidate is not None:
                    if enable_dedup and outcome.candidate.signature in signatures:
                        continue
                    if enable_dedup:
                        signatures.add(outcome.candidate.signature)
                    candidates.append(outcome.candidate)
                elif outcome.error is not None:
                    errors.append(outcome.error)
                if outcome.fallback_used:
                    generator_tle_extra_args_fallbacks += 1

            progress_snapshot["phase"] = "candidate_generation"
            progress_snapshot["candidates_generated"] = len(candidates)
            if len(candidates) % checkpoint_every == 0:
                self._save_state(
                    problem_dir,
                    phase="candidate_generation",
                    next_seed=seed + 1,
                    candidates=candidates,
                    errors=errors,
                    answer_ext=normalized_answer_ext,
                    active_pids=active_pids,
                )

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

            with open(test_file, "w", encoding="utf-8", newline="") as f:
                f.write(candidate.input_data)
            with open(ans_file, "w", encoding="utf-8", newline="") as f:
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

        set_section(
            problem_dir,
            TEST_MANIFEST,
            {
                "version": 1,
                "answer_ext": normalized_answer_ext,
                "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
                "tests": test_manifest,
            },
        )
        # 生成检查点已写入 runtime store，清空以免残留。
        set_section(problem_dir, GENERATE_CHECKPOINT, {})
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
                generator_tle_extra_args_fallbacks=generator_tle_extra_args_fallbacks,
                message=f"Generated {len(generated_tests)} test cases (from {len(candidates)} candidates)",
            )
        else:
            self._save_state(
                problem_dir,
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
                generator_tle_extra_args_fallbacks=generator_tle_extra_args_fallbacks,
            )

    def _mark_tests_unverified(self, problem_dir: str) -> None:
        update_section(
            problem_dir,
            WORKFLOW,
            {
                "tests_verified": False,
                "verify_signals": {},
                "limit_case_ratio": None,
            },
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})

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
            if not (filename.endswith(".in") or filename.endswith(answer_ext)):
                continue
            path = os.path.join(tests_dir, filename)
            if os.path.isfile(path):
                os.remove(path)
        return None

    def _normalize_answer_ext(self, answer_ext: str) -> tuple[str | None, ToolResult | None]:
        ext = normalize_answer_ext(answer_ext or ".ans")
        if not ext:
            return None, ToolResult.fail("invalid answer_ext")
        return ext, None

    @staticmethod
    def _generator_run_failed(result: RunResult) -> bool:
        return result.timed_out or not result.success or not (result.stdout or "").strip()

    async def _process_candidate(
        self,
        seed: int,
        test_cfg: tuple[str, str, str, str, str, str, list[str]],
        gen_exe: str,
        sol_exe: str,
        val_exe: str | None,
        timeout: int,
        validator_available: bool,
        active_pids: set[int],
    ) -> _CandidateOutcome:
        """处理单个候选的 gen→(validator)→sol 流水（并发单元）。

        保持与原串行实现一致的失败/超时语义：生成失败记 error，validator 拒绝则跳过，
        sol 失败记 error。type=4 带 extra_args 时先做去参数重试兜底。
        """
        seed_offset, type_param, n_min, n_max, t_min, t_max, extra_args = test_cfg
        base_cmd = [
            str(seed + int(seed_offset)),
            type_param,
            str(n_min),
            str(n_max),
            str(t_min),
            str(t_max),
        ]
        cmd_args = base_cmd + extra_args

        gen_result = await self._run_with_retry(
            gen_exe, cmd_args, timeout=timeout, active_pids=active_pids
        )
        fallback_used = False
        if self._generator_run_failed(gen_result) and type_param == "4" and extra_args:
            fb_result = await self._run_with_retry(
                gen_exe, base_cmd, timeout=timeout, active_pids=active_pids
            )
            if not self._generator_run_failed(fb_result):
                fallback_used = True
                gen_result = fb_result

        if self._generator_run_failed(gen_result):
            return _CandidateOutcome(
                error=(
                    seed,
                    "Generator failed: "
                    f"return_code={gen_result.return_code}, "
                    f"stderr={gen_result.stderr}",
                ),
                fallback_used=fallback_used,
            )

        input_data = gen_result.stdout

        if validator_available and val_exe:
            val_result = await run_binary(val_exe, input_data, timeout=timeout)
            if val_result.return_code != 0:
                return _CandidateOutcome(fallback_used=fallback_used)

        sol_result = await run_binary(sol_exe, input_data, timeout=timeout)
        if not sol_result.success:
            return _CandidateOutcome(
                error=(seed, f"sol failed: {sol_result.stderr}"),
                fallback_used=fallback_used,
            )

        sig = hashlib.md5(input_data.encode()).hexdigest()
        return _CandidateOutcome(
            candidate=CandidateTest(
                input_data=input_data,
                output_data=sol_result.stdout,
                type_param=type_param,
                signature=sig,
            ),
            fallback_used=fallback_used,
        )

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
        problem_dir: str,
        *,
        phase: str,
        next_seed: int,
        candidates: list[CandidateTest],
        errors: list[tuple[int, str]],
        answer_ext: str,
        active_pids: set[int] | None = None,
        message: str | None = None,
    ) -> None:
        set_section(
            problem_dir,
            GENERATE_CHECKPOINT,
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
        )

    def _load_state(self, problem_dir: str) -> dict | None:
        state = get_section(problem_dir, GENERATE_CHECKPOINT)
        return state if isinstance(state, dict) else None

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
                    ("13", "4", str(n_limit), str(n_limit), "1", "1", ["mode=tle_dense"]),
                    ("14", "4", str(int(n_limit * 0.9)), str(n_limit), "1", "1", ["mode=tle_chain"]),
                ]
            )

        return self._sanitize_default_configs(configs, n_limit=n_limit, t_limit=t_limit)

    def _sanitize_default_configs(
        self,
        configs: list[tuple[str, str, str, str, str, str, list[str]]],
        *,
        n_limit: int,
        t_limit: int,
    ) -> list[tuple[str, str, str, str, str, str, list[str]]]:
        """Clamp generated defaults so smart mode never emits invalid generator ranges."""
        safe_configs: list[tuple[str, str, str, str, str, str, list[str]]] = []
        for seed_offset, type_param, n_min, n_max, t_min, t_max, extra_args in configs:
            raw_n_min = int(n_min)
            raw_n_max = int(n_max)
            raw_t_min = int(t_min)
            raw_t_max = int(t_max)

            safe_n_max = min(max(raw_n_max, 1), max(n_limit, 1))
            safe_n_min = min(max(raw_n_min, 1), safe_n_max)
            safe_t_max = min(max(raw_t_max, 1), max(t_limit, 1))
            safe_t_min = min(max(raw_t_min, 1), safe_t_max)

            safe_configs.append(
                (
                    seed_offset,
                    type_param,
                    str(safe_n_min),
                    str(safe_n_max),
                    str(safe_t_min),
                    str(safe_t_max),
                    extra_args,
                )
            )
        return safe_configs


class ProblemCleanupProcessesTool(Tool):
    """清理 problem_generate_tests 残留状态和进程。"""

    @property
    def name(self) -> str:
        return "problem_cleanup_processes"

    @property
    def description(self) -> str:
        return "巡检并回收生成器残留进程（默认执行），仅按当前问题记录的 PID 精准清理。"

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemCleanupProcessesInput)

    async def execute(self, problem_dir: str, kill_all_generators: bool = True) -> ToolResult:
        # 默认即巡检并回收残留 PID（不再直接返回 success）；回收前用 psutil 校验存活，
        # 已退出的 PID 跳过不报错，POSIX 下按进程组整树回收。
        state = self._load_cleanup_state(problem_dir) or {}
        removed_files: list[str] = []
        pids = state.get("active_pids", []) if isinstance(state, dict) else []
        if not isinstance(pids, list):
            pids = []
        valid_pids = [pid for pid in pids if isinstance(pid, int) and pid > 0]
        try:
            killed: list[int] = []
            skipped: list[int] = []
            failed: list[dict[str, str | int]] = []
            for pid in valid_pids:
                if not is_pid_alive(pid):
                    # 已退出的残留 PID：跳过且不报错。
                    skipped.append(pid)
                    continue
                ok, err = await terminate_pid_tree(pid)
                if ok:
                    killed.append(pid)
                else:
                    failed.append({"pid": pid, "stdout": "", "stderr": err})
            # 仅保留仍失败的 PID 供重试；已杀与已退出的均从状态移除。
            failed_pid_set = {int(item["pid"]) for item in failed}
            remaining_pids = [pid for pid in valid_pids if pid in failed_pid_set]
            self._write_cleanup_state(problem_dir, remaining_pids)
            return ToolResult.ok(
                removed_files=removed_files,
                killed_pids=killed,
                skipped_pids=skipped,
                failed_pids=failed,
                warning="No tracked generator PID found; nothing to reclaim" if not valid_pids else "",
                message="Cleanup finished",
            )
        except Exception as exc:
            return ToolResult.fail(f"cleanup failed: {exc}", removed_files=removed_files)

    def _load_cleanup_state(self, problem_dir: str) -> dict | None:
        state = get_section(problem_dir, GENERATE_CHECKPOINT)
        return state if isinstance(state, dict) else None

    def _write_cleanup_state(self, problem_dir: str, remaining_pids: list[int]) -> None:
        update_section(problem_dir, GENERATE_CHECKPOINT, {"active_pids": remaining_pids})


def _pack_polygon_files(problem_dir: str) -> dict[str, list[str]]:
    """创建 Polygon 标准目录并拷贝源文件，返回操作汇总。"""
    results: dict[str, list[str]] = {
        "files_copied": [],
        "files_removed": [],
        "directories_created": [],
    }
    for dir_name in ["files", "solutions", "statements", "scripts"]:
        dir_path = os.path.join(problem_dir, dir_name)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            results["directories_created"].append(dir_name)

    files_dir = os.path.join(problem_dir, "files")
    for src in ["testlib.h", "gen.cpp", "val.cpp", "interactor.cpp"]:
        src_path = os.path.join(problem_dir, src)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(files_dir, src))
            results["files_copied"].append(f"{src} -> files/{src}")

    solutions_dir = os.path.join(problem_dir, "solutions")
    for src in ["sol.cpp", "brute.cpp"]:
        src_path = os.path.join(problem_dir, src)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(solutions_dir, src))
            results["files_copied"].append(f"{src} -> solutions/{src}")

    readme_src = os.path.join(problem_dir, "README.md")
    if os.path.exists(readme_src):
        shutil.copy2(readme_src, os.path.join(problem_dir, "statements", "README.md"))
        results["files_copied"].append("README.md -> statements/README.md")

    return results


def _add_executable(parent: ET.Element, source_path: str) -> ET.Element:
    exe = ET.SubElement(parent, "executable")
    ET.SubElement(exe, "source", {"path": source_path})
    return exe


def _build_problem_xml(
    problem_dir: str,
    *,
    time_limit_ms: int,
    memory_limit_bytes: int,
    test_count: int,
    answer_ext: str,
    is_interactive_problem: bool,
    has_checker: bool,
    has_interactor: bool,
) -> str:
    """基于 xml.etree.ElementTree 生成 problem.xml 内容。"""
    problem_name = os.path.basename(os.path.normpath(problem_dir))
    include_validator = (not is_interactive_problem) or os.path.isfile(
        os.path.join(problem_dir, "files", "val.cpp")
    )

    problem = ET.Element("problem", {"revision": "1", "short-name": problem_name})
    names = ET.SubElement(problem, "names")
    ET.SubElement(names, "name", {"language": "chinese", "value": problem_name})

    statements = ET.SubElement(problem, "statements")
    ET.SubElement(
        statements,
        "statement",
        {
            "charset": "UTF-8",
            "language": "chinese",
            "mathjax": "true",
            "path": "statements/README.md",
            "type": "application/x-tex",
        },
    )

    judging = ET.SubElement(problem, "judging")
    testset = ET.SubElement(judging, "testset", {"name": "tests"})
    ET.SubElement(testset, "time-limit").text = str(time_limit_ms)
    ET.SubElement(testset, "memory-limit").text = str(memory_limit_bytes)
    ET.SubElement(testset, "test-count").text = str(test_count)
    ET.SubElement(testset, "input-path-pattern").text = "tests/%02d.in"
    ET.SubElement(testset, "answer-path-pattern").text = f"tests/%02d{answer_ext}"
    if has_checker:
        ET.SubElement(testset, "checker", {"type": "testlib", "path": "files/checker.cpp"})
    if has_interactor:
        ET.SubElement(testset, "interactor", {"type": "testlib", "path": "files/interactor.cpp"})

    files = ET.SubElement(problem, "files")
    resources = ET.SubElement(files, "resources")
    ET.SubElement(resources, "file", {"path": "files/testlib.h"})

    executables = ET.SubElement(files, "executables")
    _add_executable(executables, "files/gen.cpp")
    if include_validator:
        _add_executable(executables, "files/val.cpp")
    if has_checker:
        _add_executable(executables, "files/checker.cpp")
    if has_interactor:
        _add_executable(executables, "files/interactor.cpp")

    assets = ET.SubElement(problem, "assets")
    solutions = ET.SubElement(assets, "solutions")
    main = ET.SubElement(solutions, "solution", {"tag": "main"})
    ET.SubElement(main, "source", {"path": "solutions/sol.cpp"})
    rejected = ET.SubElement(solutions, "solution", {"tag": "rejected"})
    ET.SubElement(rejected, "source", {"path": "solutions/brute.cpp"})

    ET.indent(problem)
    return '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n' + ET.tostring(
        problem, encoding="unicode"
    )


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
        return input_schema_from_model(ProblemPackPolygonInput)

    async def execute(
        self,
        problem_dir: str,
        time_limit: int = 1,
        memory_limit: int = 256,
    ) -> ToolResult:
        """执行 Polygon 打包。"""
        if not os.path.exists(problem_dir):
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        tests_dir = os.path.join(problem_dir, "tests")
        if not os.path.isdir(tests_dir):
            return ToolResult.fail("tests directory not found, run problem_generate_tests first")
        in_files = sorted(f for f in os.listdir(tests_dir) if f.endswith(".in"))
        if not in_files:
            return ToolResult.fail("no test input files found, run problem_generate_tests first")

        answer_ext = ".ans"
        _test_manifest = get_section(problem_dir, TEST_MANIFEST)
        if isinstance(_test_manifest, dict):
            answer_ext = normalize_answer_ext(str(_test_manifest.get("answer_ext", ".ans"))) or ".ans"

        missing_answers = [
            in_file for in_file in in_files if not os.path.exists(
                os.path.join(tests_dir, f"{os.path.splitext(in_file)[0]}{answer_ext}")
            )
        ]
        if missing_answers:
            return ToolResult.fail(
                "missing answer files for some tests",
                missing_answer_inputs=missing_answers,
            )

        statement_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(statement_path):
            return ToolResult.fail("statement file missing: statements/README.md")

        sol_source = os.path.join(problem_dir, "solutions", "sol.cpp")
        root_sol_source = os.path.join(problem_dir, "sol.cpp")
        if not os.path.exists(sol_source) and not os.path.exists(root_sol_source):
            return ToolResult.fail("main solution source missing: solutions/sol.cpp")

        # 门禁配置统一从 Pydantic manifest 读取；存在但损坏即阻断，缺失则回退默认门禁（保持向后兼容）。
        try:
            manifest_model = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(
                f"invalid or unreadable autocode.json: {exc}",
                stage="problem_pack_polygon",
                gate="manifest_readable",
                required=True,
                actual=False,
            )
        if manifest_model is None:
            manifest_model = default_manifest(os.path.basename(os.path.normpath(problem_dir)))

        quality_gates_cfg = manifest_model.quality_gates
        audit_gates_cfg = manifest_model.audit_gates
        require_tests_verified = quality_gates_cfg.require_tests_verified
        min_limit_case_ratio = min(1.0, max(0.0, quality_gates_cfg.min_limit_case_ratio))
        require_full_audit = audit_gates_cfg.require_full_audit
        is_interactive_problem = manifest_model.interactive

        interactor_cpp = os.path.join(problem_dir, "files", "interactor.cpp")
        root_interactor_cpp = os.path.join(problem_dir, "interactor.cpp")
        if is_interactive_problem and not os.path.exists(interactor_cpp) and not os.path.exists(root_interactor_cpp):
            return ToolResult.fail("interactive problem requires files/interactor.cpp")

        workflow_state = get_section(problem_dir, WORKFLOW)
        if not isinstance(workflow_state, dict):
            if require_tests_verified:
                return ToolResult.fail(
                    "workflow state missing, run problem_verify_tests first",
                    stage="problem_pack_polygon",
                    gate="require_tests_verified",
                    required=True,
                    actual=False,
                )
            workflow_state = {}
        if require_tests_verified and not bool(workflow_state.get("tests_verified", False)):
            return ToolResult.fail(
                "tests are not verified, run problem_verify_tests first",
                stage="problem_pack_polygon",
                gate="require_tests_verified",
                required=True,
                actual=False,
                tests_verified=False,
            )

        verify_signals = workflow_state.get("verify_signals", {}) if isinstance(workflow_state, dict) else {}
        if not isinstance(verify_signals, dict):
            verify_signals = {}
        if require_full_audit:
            audit_state = get_section(problem_dir, AUDIT)
            full_audit = audit_state.get("full_audit", {}) if isinstance(audit_state, dict) else {}
            if not isinstance(full_audit, dict):
                full_audit = {}
            full_audit_passed = bool(audit_state.get("full_audit_passed", False)) if isinstance(audit_state, dict) else False
            try:
                full_audit_blocking_count = int(full_audit.get("blocking_issue_count", 1))
            except (TypeError, ValueError):
                full_audit_blocking_count = 1
            if (
                full_audit.get("mode") != "full"
                or full_audit.get("decision") != "go"
                or not full_audit_passed
                or full_audit_blocking_count != 0
            ):
                return ToolResult.fail(
                    "full audit is required, run problem_audit with mode=full first",
                    stage="problem_pack_polygon",
                    gate="require_full_audit",
                    required=True,
                    actual=full_audit,
                )
            audit_signals = full_audit.get("quality_signals", {})
            if not isinstance(audit_signals, dict):
                audit_signals = {}
            spj_checker = manifest_uses_testlib_checker(manifest_model)
            audit_signal_rules = [
                ("duplicate_inputs", True),
                ("scale_distribution", True),
                ("purpose_coverage", True),
                (
                    "validator_self_test",
                    audit_gates_cfg.require_validator_self_test and not is_interactive_problem,
                ),
                ("checker_self_test", audit_gates_cfg.require_checker_self_test and spj_checker),
                (
                    "interactor_self_test",
                    audit_gates_cfg.require_interactor_self_test and is_interactive_problem,
                ),
            ]
            for signal_name, required in audit_signal_rules:
                if not required:
                    continue
                signal_data = audit_signals.get(signal_name, {})
                if not isinstance(signal_data, dict):
                    signal_data = {}
                if not bool(signal_data.get("executed")) or not bool(signal_data.get("passed")):
                    return ToolResult.fail(
                        f"full audit signal `{signal_name}` not satisfied, run problem_audit first",
                        stage="problem_pack_polygon",
                        gate=f"full_audit.{signal_name}",
                        required=True,
                        actual=signal_data,
                    )

        # 验证信号类质量门禁委托 workflow.guard.check_gates 判定，避免与 problem_audit 双份实现。
        for issue in check_gates(manifest_model, workflow_state, verify_signals):
            if issue.gate == "tests_verified":
                # tests_verified 已在上面按文件存在/可读状态给出更精确报错，此处兜底跳过。
                continue
            return ToolResult.fail(
                f"verification signal `{issue.gate}` not satisfied, run problem_verify_tests first",
                stage="problem_pack_polygon",
                gate=issue.gate,
                required=True,
                actual=verify_signals.get(issue.gate, {}),
            )

        limit_ratio_from_manifest = None
        tests_manifest = get_section(problem_dir, TEST_MANIFEST)
        if isinstance(tests_manifest, dict):
            tests = tests_manifest.get("tests", [])
            if isinstance(tests, list) and tests:
                total = len(tests)
                limit_count = sum(
                    1
                    for item in tests
                    if isinstance(item, dict) and str(item.get("type_param")) in _LIMIT_STRATEGY_TYPES
                )
                limit_ratio_from_manifest = limit_count / total
        if limit_ratio_from_manifest is not None and limit_ratio_from_manifest < min_limit_case_ratio:
            return ToolResult.fail(
                "limit case ratio is below quality_gates.min_limit_case_ratio",
                stage="problem_pack_polygon",
                gate="min_limit_case_ratio",
                required=min_limit_case_ratio,
                actual=limit_ratio_from_manifest,
                limit_case_ratio=limit_ratio_from_manifest,
                min_limit_case_ratio=min_limit_case_ratio,
            )

        # 转换单位：秒 -> 毫秒，MB -> 字节
        time_limit_ms = time_limit * 1000
        memory_limit_bytes = memory_limit * 1024 * 1024

        # 1~4. 创建标准目录并拷贝源文件。
        results = _pack_polygon_files(problem_dir)

        # 5. 创建 problem.xml（基于 xml.etree.ElementTree 生成，避免手工拼接）
        problem_xml = os.path.join(problem_dir, "problem.xml")
        if not os.path.exists(problem_xml):
            actual_test_count = len([f for f in os.listdir(tests_dir) if f.endswith(".in")])
            files_dir = os.path.join(problem_dir, "files")
            has_checker = os.path.isfile(os.path.join(files_dir, "checker.cpp"))
            has_interactor = is_interactive_problem and os.path.isfile(
                os.path.join(files_dir, "interactor.cpp")
            )
            xml_content = _build_problem_xml(
                problem_dir,
                time_limit_ms=time_limit_ms,
                memory_limit_bytes=memory_limit_bytes,
                test_count=actual_test_count,
                answer_ext=answer_ext,
                is_interactive_problem=is_interactive_problem,
                has_checker=has_checker,
                has_interactor=has_interactor,
            )
            with open(problem_xml, "w", encoding="utf-8") as f:
                f.write(xml_content)
            results["files_copied"].append("problem.xml (created)")

        return ToolResult.ok(
            results=results,
            message="Packed to Polygon format",
        )
