"""Test generation tool (``problem_generate_tests``)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import ValidationError

from ..runtime_store import GENERATE_CHECKPOINT, TEST_MANIFEST, set_section
from ..utils.communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_participant_binaries,
)
from ..utils.compiler import RunResult, run_batch, run_binary, run_binary_with_args
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..utils.process import filter_alive_pids
from ..workflow import (
    AutoCodeManifest,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
)
from .base import Tool, ToolResult, input_schema_from_model
from .problem_generate_helpers import (
    CandidateTest,
    _CandidateOutcome,
    _ProblemGenerateHelpersMixin,
)
from .problem_helpers import _GENERATOR_TYPE_RE, _LIMIT_STRATEGY_TYPES, _facade_attr
from .schemas import ProblemGenerateTestsInput


class ProblemGenerateTestsTool(Tool, _ProblemGenerateHelpersMixin):
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
        mode: Literal["replace", "append", "register_existing"] = "replace",
        hard_timeout_seconds: int | None = None,
        checkpoint_every: int = 10,
        concurrency_limit: int = 4,
    ) -> ToolResult:
        """执行测试数据生成。

        实现论文 Algorithm 2 的后处理步骤：
        1. 生成超额候选数据
        2. 去重（基于 MD5 signature）
        3. Validator 过滤（自动检测 files/val）
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

        if mode not in ("replace", "append", "register_existing"):
            return ToolResult.fail(
                f"invalid mode {mode!r}; expected one of: replace, append, register_existing"
            )

        # 验证 test_configs 参数（兼容 1..4 及自定义类型如 5/6/hack），浅拷贝避免就地修改调用方字典
        if test_configs:
            normalized_configs: list[dict] = []
            for i, raw_config in enumerate(test_configs):
                config = dict(raw_config)
                if "type" not in config:
                    return ToolResult.fail(f"test_configs[{i}]: 'type' is required")
                raw_type = config["type"]
                if isinstance(raw_type, int) and not isinstance(raw_type, bool):
                    raw_type = str(raw_type)
                if not isinstance(raw_type, str) or not _GENERATOR_TYPE_RE.fullmatch(raw_type.strip()):
                    return ToolResult.fail(
                        f"test_configs[{i}]: 'type' must match ^[A-Za-z0-9][A-Za-z0-9_-]{{0,31}}$"
                    )
                config["type"] = raw_type.strip()

                for field in ["n_min", "n_max", "t_min", "t_max"]:
                    if field not in config:
                        return ToolResult.fail(f"test_configs[{i}]: '{field}' is required")
                    val = config[field]
                    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
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
                normalized_configs.append(config)
            test_configs = normalized_configs

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

        try:
            manifest_model = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")
        is_interactive = manifest_is_interactive(manifest_model)
        is_communication = manifest_is_communication(manifest_model)

        if mode == "register_existing":
            assert tests_dir is not None
            return self._register_existing_tests(
                problem_dir=problem_dir,
                tests_dir=tests_dir,
                answer_ext=normalized_answer_ext,
                manifest_model=manifest_model,
            )

        if not os.path.exists(gen_exe):
            return ToolResult.fail("Generator not found. Run generator_build first.")
        if not os.path.exists(sol_exe):
            if (
                is_communication
                and manifest_model is not None
                and manifest_model.protocol is not None
                and manifest_model.protocol.participant_solutions
                and effective_sol_name == "sol"
            ):
                parts = resolve_participant_binaries(problem_dir, manifest_model, None, exe_ext)
                if parts and all(os.path.isfile(p) for p in parts):
                    sol_exe = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
                else:
                    return ToolResult.fail(
                        "Participant solution binaries not found: "
                        + ", ".join(manifest_model.protocol.participant_solutions)
                    )
            else:
                return ToolResult.fail(f"{effective_sol_name} not found. Run solution_build first.")

        interactor_exe: str | None = None
        if is_interactive:
            interactor_exe = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
            if not os.path.exists(interactor_exe):
                interactor_exe = os.path.join(problem_dir, f"interactor{exe_ext}")
            if not os.path.exists(interactor_exe):
                return ToolResult.fail("Interactor not found. Run interactor_build first.")
        elif is_communication and manifest_model is not None:
            comm_mode = manifest_model.protocol.mode if manifest_model.protocol else "run_twice"
            if comm_mode != "grader_linked":
                comm_bin = resolve_communicator_binary(problem_dir, manifest_model, exe_ext)
                if not comm_bin:
                    return ToolResult.fail(
                        f"Communicator not found for communication ({comm_mode}). Run interactor_build first."
                    )
                interactor_exe = comm_bin

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

        assert tests_dir is not None
        existing_manifest_entries: list[dict[str, Any]] = []
        existing_max_index = 0
        preserved_locked: list[dict[str, Any]] = []
        if mode == "append":
            os.makedirs(tests_dir, exist_ok=True)
            existing_manifest_entries, existing_max_index, existing_sigs = (
                self._snapshot_existing_tests(tests_dir, problem_dir, normalized_answer_ext)
            )
            if enable_dedup:
                signatures.update(existing_sigs)
        elif mode == "replace":
            preserved_locked = self._snapshot_locked_and_hack_tests(
                tests_dir, problem_dir, normalized_answer_ext
            )

        if resume:
            restored = self._load_state(problem_dir)
            if restored:
                seed = int(restored.get("next_seed", 1))
                candidates = self._restore_candidates(restored.get("candidates", []))
                signatures.update(c.signature for c in candidates)
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
                # resume=true 但状态文件不存在/损坏时，回退到 fresh run（延迟到写盘前再原子清理，避免超时/取消破坏 locked 用例）
                os.makedirs(tests_dir, exist_ok=True)
                progress_snapshot["phase"] = "resume_fallback_fresh"
                progress_snapshot["resume_fallback"] = True
        elif mode == "replace":
            # 确保目录存在；历史测试清理延后到最终写盘前执行，确保超时/取消时不会丢失人工 locked 测试点。
            os.makedirs(tests_dir, exist_ok=True)

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
                        is_interactive=is_interactive,
                        is_communication=is_communication,
                        interactor_exe=interactor_exe,
                        manifest_model=manifest_model,
                        problem_dir=problem_dir,
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

        if mode == "replace":
            # 最终写盘前清理历史生成产物，防止 resume 场景残留旧编号样例。
            clear_before_write_error = self._clear_generated_tests(tests_dir, normalized_answer_ext)
            if clear_before_write_error:
                return clear_before_write_error

        # 写入文件
        type_to_group: dict[str, str] = {
            "1": "sanity",
            "2": "coverage",
            "3": "limit",
            "4": "limit",
        }
        if manifest_model is not None and manifest_model.case_plan:
            for plan_item in manifest_model.case_plan:
                if plan_item.group and plan_item.group.strip():
                    type_to_group[str(plan_item.type)] = plan_item.group.strip()

        generated_tests: list[int] = []
        test_manifest: list[dict[str, Any]] = list(existing_manifest_entries) if mode == "append" else []
        start_index = (existing_max_index + 1) if mode == "append" else 1
        preserved_by_sig: dict[str, dict[str, Any]] = (
            {str(p["signature"]): p for p in preserved_locked if p.get("signature")}
            if mode == "replace"
            else {}
        )
        preserved_locked_count = 0

        for offset, candidate in enumerate(final_tests):
            i = start_index + offset
            test_file = os.path.join(tests_dir, f"{i:02d}.in")
            ans_name = f"{i:02d}{normalized_answer_ext}"
            ans_file = os.path.join(tests_dir, ans_name)

            with open(test_file, "w", encoding="utf-8", newline="") as f:
                f.write(candidate.input_data)
            write_ans = (not is_interactive) or bool(candidate.output_data.strip())
            if write_ans:
                with open(ans_file, "w", encoding="utf-8", newline="") as f:
                    f.write(candidate.output_data)

            generated_tests.append(i)
            entry_m: dict[str, Any] = {
                "index": i,
                "in_file": f"{i:02d}.in",
                "ans_file": ans_name if write_ans else "",
                "type_param": candidate.type_param,
                "group": type_to_group.get(str(candidate.type_param), "default"),
                "signature": candidate.signature,
            }
            matched_locked = preserved_by_sig.get(candidate.signature)
            if matched_locked is not None:
                entry_m["locked"] = True
                if matched_locked.get("purpose"):
                    entry_m["purpose"] = str(matched_locked["purpose"])
                preserved_locked_count += 1
            test_manifest.append(entry_m)

        # replace 模式下保留人工锁定（locked=true）与 tests/hack/ 用例，绝不静默覆盖
        if mode == "replace" and preserved_locked:
            next_idx = start_index + len(final_tests)
            seen_written_sigs = {str(m.get("signature", "")) for m in test_manifest}
            for item in preserved_locked:
                sig = str(item.get("signature", ""))
                if enable_dedup and sig and sig in seen_written_sigs:
                    continue
                idx = next_idx
                next_idx += 1
                ans_text = item.get("output_data")
                if ans_text is None and not is_interactive:
                    if is_communication and manifest_model is not None:
                        tl_ms = float(manifest_model.time_limit_ms or timeout * 1000)
                        mem_mb = float(manifest_model.memory_limit_mb or 256)
                        comm_run = await CommunicationRunner.run_from_manifest(
                            problem_dir=problem_dir,
                            manifest=manifest_model,
                            solution_exe=sol_exe,
                            input_data=str(item["input_data"]),
                            time_limit_ms=min(tl_ms * 1.5, float(timeout) * 1000.0),
                            memory_limit_mb=mem_mb,
                        )
                        if not comm_run.accepted:
                            return ToolResult.fail(
                                f"Communication solution failed on preserved hack test ({item.get('purpose', idx)}): {comm_run.verdict} {comm_run.detail}"
                            )
                        ans_text = comm_run.judge_stdout or comm_run.stdout or ""
                    elif os.path.isfile(sol_exe):
                        hack_mem_mb = (
                            float(manifest_model.memory_limit_mb)
                            if (
                                manifest_model is not None
                                and manifest_model.memory_limit_mb
                                and manifest_model.memory_limit_mb > 0
                            )
                            else 256.0
                        )
                        sol_run = await _facade_attr('run_binary', run_binary)(
                            sol_exe,
                            str(item["input_data"]),
                            timeout=timeout,
                            memory_mb=hack_mem_mb,
                        )
                        if not sol_run.success:
                            return ToolResult.fail(
                                f"Solution failed on preserved hack test ({item.get('purpose', idx)}): "
                                f"{sol_run.error or sol_run.stderr[:300]}"
                            )
                        ans_text = sol_run.stdout
                    else:
                        return ToolResult.fail(
                            f"Cannot compute answer for preserved hack test ({item.get('purpose', idx)}): sol binary missing"
                        )
                with open(os.path.join(tests_dir, f"{idx:02d}.in"), "w", encoding="utf-8", newline="") as f:
                    f.write(str(item["input_data"]))
                ans_name = f"{idx:02d}{normalized_answer_ext}"
                if isinstance(ans_text, str) and ((not is_interactive) or bool(ans_text.strip())):
                    with open(os.path.join(tests_dir, ans_name), "w", encoding="utf-8", newline="") as f:
                        f.write(ans_text)
                else:
                    ans_name = ""
                test_manifest.append(
                    {
                        "index": idx,
                        "in_file": f"{idx:02d}.in",
                        "ans_file": ans_name,
                        "type_param": str(item.get("type_param", "4")),
                        "group": str(item.get("group", "limit")),
                        "purpose": str(item.get("purpose", "manual hack test")),
                        "signature": sig,
                        "locked": True,
                    }
                )
                seen_written_sigs.add(sig)
                preserved_locked_count += 1

        set_section(
            problem_dir,
            TEST_MANIFEST,
            {
                "version": 1,
                "mode": mode,
                "answer_ext": normalized_answer_ext,
                "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
                "tests": test_manifest,
            },
        )
        # 生成检查点已写入 runtime store，清空以免残留。
        set_section(problem_dir, GENERATE_CHECKPOINT, {})
        # 统计信息（覆盖完整 test_manifest，含 append 已有用例与 replace 保留的 locked 用例）
        type_counts: dict[str, int] = {}
        for m_item in test_manifest:
            tp = str(m_item.get("type_param", "unknown"))
            type_counts[tp] = type_counts.get(tp, 0) + 1

        type_names = {"1": "tiny", "2": "random", "3": "extreme", "4": "tle"}
        type_distribution = {
            type_names.get(k, k): v for k, v in type_counts.items()
        }

        limit_in_final = sum(
            1
            for m_item in test_manifest
            if str(m_item.get("type_param")) in _LIMIT_STRATEGY_TYPES
            or str(m_item.get("group", "")).lower() in {"limit", "extreme", "tle"}
        )
        limit_minimum = (len(test_manifest) + 1) // 2 if test_manifest else 0
        limit_quota_met = len(test_manifest) == 0 or limit_in_final >= limit_minimum

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
                mode=mode,
                total_tests=len(test_manifest),
                preserved_locked_count=preserved_locked_count,
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
        *,
        is_interactive: bool = False,
        is_communication: bool = False,
        interactor_exe: str | None = None,
        manifest_model: AutoCodeManifest | None = None,
        problem_dir: str | None = None,
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
                    f"stderr={gen_result.stderr or gen_result.error or ''}",
                ),
                fallback_used=fallback_used,
            )

        input_data = gen_result.stdout

        if validator_available and val_exe:
            val_result = await _facade_attr('run_binary', run_binary)(val_exe, input_data, timeout=timeout)
            if val_result.return_code != 0:
                return _CandidateOutcome(fallback_used=fallback_used)

        mem_mb = (
            float(manifest_model.memory_limit_mb)
            if (
                manifest_model is not None
                and manifest_model.memory_limit_mb
                and manifest_model.memory_limit_mb > 0
            )
            else 256.0
        )

        if is_communication and manifest_model is not None and problem_dir is not None:
            tl_ms = (
                min(float(timeout) * 1000.0, max(float(manifest_model.time_limit_ms) * 1.5, 1000.0))
                if manifest_model.time_limit_ms
                else float(timeout) * 1000.0
            )
            jres = await CommunicationRunner.run_from_manifest(
                problem_dir=problem_dir,
                manifest=manifest_model,
                solution_exe=sol_exe,
                input_data=input_data,
                time_limit_ms=tl_ms,
                memory_limit_mb=mem_mb,
            )
            if not jres.accepted:
                return _CandidateOutcome(
                    error=(
                        seed,
                        f"communication sol failed ({jres.verdict}): "
                        f"{jres.detail or jres.judge_stderr[:300]}",
                    ),
                    fallback_used=fallback_used,
                )
            output_data = jres.judge_stdout if jres.judge_stdout.strip() else "OK\n"
        elif is_interactive and interactor_exe:
            tl_ms = (
                min(float(timeout) * 1000.0, max(float(manifest_model.time_limit_ms) * 1.5, 1000.0))
                if (manifest_model and manifest_model.time_limit_ms)
                else float(timeout) * 1000.0
            )
            jres = await InteractiveRunner.run(
                interactor_exe=interactor_exe,
                solution_exe=sol_exe,
                input_data=input_data,
                time_limit_ms=tl_ms,
                memory_limit_mb=mem_mb,
                cwd=problem_dir,
            )
            if not jres.accepted:
                return _CandidateOutcome(
                    error=(
                        seed,
                        f"interactive sol failed ({jres.verdict}): "
                        f"{jres.detail or jres.judge_stderr[:300]}",
                    ),
                    fallback_used=fallback_used,
                )
            output_data = jres.judge_stdout if jres.judge_stdout.strip() else ""
        else:
            sol_result = await _facade_attr('run_binary', run_binary)(
                sol_exe, input_data, timeout=timeout, memory_mb=mem_mb
            )
            if not sol_result.success:
                return _CandidateOutcome(
                    error=(seed, f"sol failed: {sol_result.stderr or sol_result.error or f'return_code={sol_result.return_code}'}"),
                    fallback_used=fallback_used,
                )
            output_data = sol_result.stdout

        sig = hashlib.md5(input_data.encode()).hexdigest()
        return _CandidateOutcome(
            candidate=CandidateTest(
                input_data=input_data,
                output_data=output_data,
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
        started_pid: int | None = None
        cancelled = False

        def _on_start(pid: int) -> None:
            nonlocal started_pid
            started_pid = pid
            active_pids.add(pid)

        try:
            return await _facade_attr('run_binary_with_args', run_binary_with_args)(
                binary_path,
                args,
                timeout=timeout,
                process_start_hook=_on_start,
            )
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if started_pid is not None and not cancelled:
                active_pids.discard(started_pid)
