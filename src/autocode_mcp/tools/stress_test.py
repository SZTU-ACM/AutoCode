"""
Stress Test 工具 - 对拍测试。

基于论文框架，比较 sol.cpp 和 brute.cpp 的输出。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from pydantic import ValidationError

from ..runtime_store import WORKFLOW, get_section
from ..utils.checker_judge import checker_exe_path, run_testlib_checker
from ..utils.communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_participant_binaries,
)
from ..utils.compiler import run_batch, run_binary, run_binary_with_args
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..workflow import (
    AutoCodeManifest,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_requires_brute,
    manifest_uses_testlib_checker,
)
from .base import Tool, ToolResult, input_schema_from_model
from .schemas import StressTestRunInput


@dataclass
class _StressRoundResult:
    """单轮对拍的结果。"""

    status: str  # ok | gen_failed | validator_failed | sol_failed | brute_failed | mismatch
    global_round: int
    input_data: str | None = None
    sol_output: str | None = None
    brute_output: str | None = None
    gen_detail: dict | None = None
    validator_detail: dict | None = None
    stats: dict | None = None



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
        若 manifest.json 中 special_judge 为 true 且 stress_comparison 为 checker，则调用
        checker(input, sol输出, brute输出)（须已编译 files/checker）；brute 作为 answer。
        若另设 stress_checker_bidirectional 为 true，会再调用 checker(input, brute, sol)，二者均须 AC。
        否则比较 sol/brute 输出字符串是否一致。

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
        return input_schema_from_model(StressTestRunInput)

    async def _run_round(
        self,
        *,
        gen_exe: str,
        val_exe: str,
        sol_exe: str,
        brute_exe: str,
        global_round: int,
        temp_dir: str,
        timeout: int,
        n_max: int,
        generator_args: dict | None,
        types: list[str] | None,
        use_checker_stress: bool,
        checker_exe: str,
        is_interactive: bool,
        is_communication: bool,
        interactor_exe: str,
        run_brute: bool,
        problem_dir: str,
        manifest_model: AutoCodeManifest | None,
        sol_name: str,
        brute_name: str,
    ) -> _StressRoundResult:
        """运行单轮对拍（gen→validator→sol/brute→compare 或交互题双向判题），作为并发单元。"""
        input_path = os.path.join(temp_dir, f"input_{global_round}.txt")
        gen_result = await self._generate_input(
            gen_exe, input_path, global_round, seed=global_round, timeout=timeout, n_max=n_max,
            generator_args=generator_args, types=types,
        )
        if not gen_result["success"]:
            return _StressRoundResult(
                status="gen_failed", global_round=global_round, gen_detail=gen_result
            )
        input_data = str(gen_result["input_data"])

        # 验证输入（如果有 validator）
        if os.path.exists(val_exe):
            val_result = await run_binary(val_exe, input_data, timeout=timeout)
            if val_result.return_code != 0:
                return _StressRoundResult(
                    status="validator_failed",
                    global_round=global_round,
                    input_data=input_data,
                    validator_detail={
                        "validator_return_code": val_result.return_code,
                        "validator_stderr": (val_result.stderr or "")[:2000],
                        "validator_stdout": (val_result.stdout or "")[:2000],
                    },
                )

        if is_interactive or (is_communication and manifest_model is not None):
            tl_ms = float(
                getattr(manifest_model, "time_limit_ms", None) or min(timeout * 1000, 5000)
            )
            mem_mb = float(getattr(manifest_model, "memory_limit_mb", None) or 256)
            eff_tl_ms = min(tl_ms, float(timeout) * 1000.0)

            if is_communication and manifest_model is not None:
                sol_judge = await CommunicationRunner.run_from_manifest(
                    problem_dir=problem_dir,
                    manifest=manifest_model,
                    solution_exe=sol_exe,
                    input_data=input_data,
                    time_limit_ms=eff_tl_ms,
                    memory_limit_mb=mem_mb,
                )
            else:
                sol_judge = await InteractiveRunner.run(
                    interactor_exe=interactor_exe,
                    solution_exe=sol_exe,
                    input_data=input_data,
                    time_limit_ms=eff_tl_ms,
                    memory_limit_mb=mem_mb,
                    cwd=temp_dir,
                )
            if not sol_judge.accepted:
                return _StressRoundResult(
                    status="sol_failed",
                    global_round=global_round,
                    input_data=input_data,
                    sol_output=sol_judge.transcript[:2000],
                    gen_detail={
                        "verdict": sol_judge.verdict,
                        "detail": sol_judge.detail,
                        "judge_stderr": sol_judge.judge_stderr[:500],
                        "stderr": sol_judge.stderr[:500],
                        "transcript": sol_judge.transcript[:2000],
                    },
                )

            brute_time_ms = 0
            brute_transcript = ""
            if run_brute and os.path.isfile(brute_exe):
                if is_communication and manifest_model is not None:
                    brute_judge = await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest_model,
                        solution_exe=brute_exe,
                        input_data=input_data,
                        time_limit_ms=float(timeout) * 1000.0,
                        memory_limit_mb=mem_mb,
                    )
                else:
                    brute_judge = await InteractiveRunner.run(
                        interactor_exe=interactor_exe,
                        solution_exe=brute_exe,
                        input_data=input_data,
                        time_limit_ms=float(timeout) * 1000.0,
                        memory_limit_mb=mem_mb,
                        cwd=temp_dir,
                    )
                if not brute_judge.accepted:
                    return _StressRoundResult(
                        status="brute_failed",
                        global_round=global_round,
                        input_data=input_data,
                        sol_output=sol_judge.transcript[:2000],
                        brute_output=brute_judge.transcript[:2000],
                        gen_detail={
                            "verdict": brute_judge.verdict,
                            "detail": brute_judge.detail,
                            "judge_stderr": brute_judge.judge_stderr[:500],
                            "stderr": brute_judge.stderr[:500],
                            "transcript": brute_judge.transcript[:2000],
                            "timed_out": brute_judge.verdict == "TLE",
                            "deadlock": brute_judge.verdict == "DEADLOCK",
                        },
                    )
                brute_time_ms = int(brute_judge.wall_time_ms)
                brute_transcript = brute_judge.transcript[:2000]

            stats = {
                "round": global_round,
                "sol_time_ms": int(sol_judge.wall_time_ms),
                "brute_time_ms": brute_time_ms,
                "input_size": len(input_data),
                "n_value": self._extract_n_value(input_data),
                "sol_metrics": sol_judge.metrics,
            }
            return _StressRoundResult(
                status="ok",
                global_round=global_round,
                input_data=input_data,
                sol_output=sol_judge.transcript[:2000],
                brute_output=brute_transcript,
                stats=stats,
            )

        # 运行 sol 和 brute/reference oracle，比较输出
        sol_result = await run_binary(sol_exe, input_data, timeout=timeout)
        if sol_result.timed_out or not sol_result.success:
            return _StressRoundResult(
                status="sol_failed",
                global_round=global_round,
                input_data=input_data,
                sol_output=sol_result.stdout,
                gen_detail={
                    "stderr": sol_result.stderr or sol_result.error or "",
                    **({"error": sol_result.error} if sol_result.error else {}),
                    **({"timed_out": True} if sol_result.timed_out else {}),
                },
            )
        sol_output = sol_result.stdout

        brute_result = await run_binary(brute_exe, input_data, timeout=timeout)
        if brute_result.timed_out:
            return _StressRoundResult(
                status="brute_failed",
                global_round=global_round,
                input_data=input_data,
                sol_output=sol_output,
                gen_detail={
                    "timed_out": True,
                    **({"error": brute_result.error} if brute_result.error else {}),
                },
            )
        if not brute_result.success:
            return _StressRoundResult(
                status="brute_failed",
                global_round=global_round,
                input_data=input_data,
                sol_output=sol_output,
                gen_detail={
                    "stderr": brute_result.stderr or brute_result.error or "",
                    **({"error": brute_result.error} if brute_result.error else {}),
                },
            )
        brute_output = brute_result.stdout

        stats = {
            "round": global_round,
            "sol_time_ms": sol_result.time_ms,
            "brute_time_ms": brute_result.time_ms,
            "input_size": len(input_data),
            "n_value": self._extract_n_value(input_data),
        }

        # 比较输出（普通题字符串一致；SPJ+checker 用 testlib checker，answer=brute）
        if use_checker_stress:
            sol_path = os.path.join(temp_dir, f"sol_out_{global_round}.txt")
            brute_path = os.path.join(temp_dir, f"brute_out_{global_round}.txt")
            with open(sol_path, "w", encoding="utf-8", newline="\n") as sf:
                sf.write(sol_output)
            with open(brute_path, "w", encoding="utf-8", newline="\n") as bf:
                bf.write(brute_output)
            v1, _ = await run_testlib_checker(
                checker_exe, input_path, sol_path, brute_path, timeout=timeout
            )
            v2_ok = True
            if manifest_model is not None and getattr(manifest_model, "stress_checker_bidirectional", False):
                v2, _ = await run_testlib_checker(
                    checker_exe, input_path, brute_path, sol_path, timeout=timeout
                )
                v2_ok = v2 == "AC"
            if v1 != "AC" or not v2_ok:
                return _StressRoundResult(
                    status="mismatch",
                    global_round=global_round,
                    input_data=input_data,
                    sol_output=sol_output,
                    brute_output=brute_output,
                )
        elif sol_output.replace("\r\n", "\n").strip() != brute_output.replace("\r\n", "\n").strip():
            return _StressRoundResult(
                status="mismatch",
                global_round=global_round,
                input_data=input_data,
                sol_output=sol_output,
                brute_output=brute_output,
            )

        return _StressRoundResult(
            status="ok",
            global_round=global_round,
            input_data=input_data,
            sol_output=sol_output,
            brute_output=brute_output,
            stats=stats,
        )

    def _build_round_failure(
        self,
        res: _StressRoundResult,
        round_stats: list[dict],
        n_max_advisory: str,
        sol_name: str,
        brute_name: str,
    ) -> ToolResult:
        """gen/sol/brute 失败时构造与串行实现一致的结构化失败结果。"""
        gr = res.global_round
        statistics = self._compute_summary(round_stats)
        if res.status == "gen_failed":
            gd = res.gen_detail or {}
            error_detail = gd.get("error", "Unknown error")
            if "timed out" in error_detail:
                hint = "Generator may contain an infinite loop or be too slow. Try increasing the timeout parameter."
            else:
                hint = "Generator crashed unexpectedly. Check stderr for details."
            return ToolResult.fail(
                f"Generator failed at round {gr}: {error_detail}. {hint}",
                round=gr,
                seed=gd.get("seed", gr),
                stderr=gd.get("stderr", ""),
                stdout=gd.get("stdout", ""),
                cmd_args=gd.get("cmd_args", []),
                last_input=res.input_data,
                statistics=statistics,
                n_max_warning=n_max_advisory,
                n_max_advisory=n_max_advisory,
            )
        if res.status == "sol_failed":
            gd = res.gen_detail or {}
            verdict = gd.get("verdict")
            msg = f"{sol_name} failed at round {gr}" + (f" ({verdict})" if verdict else "")
            extra: dict[str, object] = {}
            for k in ("verdict", "detail", "judge_stderr", "transcript", "error"):
                if k in gd:
                    extra[k] = gd[k]
            return ToolResult.fail(
                msg,
                round=gr,
                input_data=res.input_data,
                stderr=gd.get("stderr") or gd.get("error") or "",
                statistics=statistics,
                **extra,
            )
        if (res.gen_detail or {}).get("timed_out"):
            gd = res.gen_detail or {}
            extra_bt: dict[str, object] = {}
            for k in ("verdict", "detail", "judge_stderr", "transcript", "error"):
                if k in gd:
                    extra_bt[k] = gd[k]
            return ToolResult.fail(
                f"{brute_name} timed out at round {gr} (N may be too large)",
                round=gr,
                input_data=res.input_data,
                suggestion="Try reducing n_max parameter",
                n_max_warning=n_max_advisory,
                n_max_advisory=n_max_advisory,
                statistics=statistics,
                **extra_bt,
            )
        gd = res.gen_detail or {}
        extra_bf: dict[str, object] = {}
        for k in ("verdict", "detail", "judge_stderr", "transcript", "error"):
            if k in gd:
                extra_bf[k] = gd[k]
        if gd.get("deadlock"):
            return ToolResult.fail(
                f"{brute_name} deadlocked at round {gr} (possible missing flush or protocol mismatch)",
                round=gr,
                input_data=res.input_data,
                stderr=gd.get("stderr") or gd.get("error") or "",
                statistics=statistics,
                **extra_bf,
            )
        return ToolResult.fail(
            f"{brute_name} failed at round {gr}",
            round=gr,
            input_data=res.input_data,
            stderr=gd.get("stderr") or gd.get("error") or "",
            statistics=statistics,
            **extra_bf,
        )

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
        concurrency_limit: int = 4,
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

        try:
            manifest_model = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")

        is_interactive = manifest_is_interactive(manifest_model)
        is_communication = manifest_is_communication(manifest_model)
        is_protocol = is_interactive or is_communication
        run_brute = manifest_requires_brute(manifest_model)
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
        if not is_protocol:
            if not run_brute and not os.path.exists(brute_exe) and manifest_model is not None:
                # oracle_mode="reference_solutions": 寻找已编译的 role="reference" 对照解
                for sol_entry in manifest_model.solutions:
                    if sol_entry.role != "reference":
                        continue
                    cand = os.path.join(problem_dir, "solutions", f"{sol_entry.name}{exe_ext}")
                    if not os.path.exists(cand):
                        cand = os.path.join(problem_dir, f"{sol_entry.name}{exe_ext}")
                    if os.path.exists(cand):
                        brute_exe = cand
                        effective_brute_name = sol_entry.name
                        break
            if not os.path.exists(brute_exe):
                if run_brute:
                    return ToolResult.fail(f"{effective_brute_name} not found. Run solution_build first.")
                return ToolResult.fail(
                    f"No oracle binary found for stress testing (missing {effective_brute_name} or compiled reference solution). Run solution_build first."
                )
            run_brute = True
        elif run_brute and not os.path.exists(brute_exe):
            return ToolResult.fail(f"{effective_brute_name} not found. Run solution_build first.")

        interactor_exe = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
        if is_interactive:
            if not os.path.isfile(interactor_exe):
                fallback_int = os.path.join(problem_dir, f"interactor{exe_ext}")
                if os.path.isfile(fallback_int):
                    interactor_exe = fallback_int
            if not os.path.isfile(interactor_exe):
                return ToolResult.fail(
                    f"Interactive problem requires files/interactor{exe_ext}. Run interactor_build first."
                )
        elif is_communication and manifest_model is not None:
            comm_mode = manifest_model.protocol.mode if manifest_model.protocol else "run_twice"
            if comm_mode != "grader_linked":
                comm_bin = resolve_communicator_binary(problem_dir, manifest_model, exe_ext)
                if not comm_bin:
                    return ToolResult.fail(
                        f"Communication problem ({comm_mode}) requires compiled communicator binary. Run interactor_build first."
                    )
                interactor_exe = comm_bin

        use_checker_stress = (not is_protocol) and manifest_uses_testlib_checker(manifest_model)
        checker_exe = checker_exe_path(problem_dir, exe_ext) if use_checker_stress else ""
        if use_checker_stress and not os.path.isfile(checker_exe):
            return ToolResult.fail(
                "special_judge with stress_comparison=checker requires files/checker"
                f"{exe_ext}. Run checker_build first."
            )

        # 可选的 validator
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")
        if not os.path.exists(val_exe):
            val_exe = os.path.join(problem_dir, f"val{exe_ext}")

        profiles = stress_profiles or [
            {"name": "default", "trials": trials, "types": types, "generator_args": generator_args}
        ]
        total_rounds = sum(
            max(1, int(trials if p.get("trials") is None else p["trials"]))
            for p in profiles
        )
        profile_n_values: list[int] = []
        for p in profiles:
            p_args = p.get("generator_args")
            if not isinstance(p_args, dict):
                p_args = generator_args
            if isinstance(p_args, dict) and p_args.get("n_max") is not None:
                try:
                    profile_n_values.append(int(p_args["n_max"]))
                except (TypeError, ValueError):
                    pass
        effective_n_max = max(profile_n_values) if profile_n_values else n_max
        complexity_context = self._load_complexity_context(problem_dir)

        concurrency_limit = max(1, int(concurrency_limit))

        failed_round = None
        last_input = None
        sol_output = None
        brute_output = None
        validator_failed = False
        validator_failure_detail: dict[str, object] = {}
        n_max_advisory = self._build_n_max_advisory(effective_n_max, complexity_context)

        # 统计信息收集
        round_stats: list[dict] = []
        profile_reports: list[dict] = []

        with tempfile.TemporaryDirectory(dir=problem_dir) as temp_dir:
            global_round = 0
            for profile in profiles:
                profile_name = str(profile.get("name") or f"profile-{len(profile_reports) + 1}")
                raw_trials = profile.get("trials")
                profile_trials = max(1, int(trials if raw_trials is None else raw_trials))
                raw_types = profile.get("types")
                profile_types = types if raw_types is None else raw_types
                raw_gen_args = profile.get("generator_args")
                profile_generator_args = generator_args if raw_gen_args is None else raw_gen_args
                start_count = len(round_stats)
                profile_failed_round = None

                while not failed_round and (len(round_stats) - start_count) < profile_trials:
                    # 凑一批轮次，有限并发处理，保持轮序与串行一致的失败传播。
                    batch: list[int] = []
                    while (
                        len(batch) < concurrency_limit
                        and (len(round_stats) - start_count + len(batch)) < profile_trials
                    ):
                        global_round += 1
                        batch.append(global_round)

                    async def _worker(
                        round_no: int,
                        _gen_args: dict | None = profile_generator_args,
                        _types: list[str] | None = profile_types,
                    ) -> _StressRoundResult:
                        return await self._run_round(
                            gen_exe=gen_exe,
                            val_exe=val_exe,
                            sol_exe=sol_exe,
                            brute_exe=brute_exe,
                            global_round=round_no,
                            temp_dir=temp_dir,
                            timeout=timeout,
                            n_max=n_max,
                            generator_args=_gen_args,
                            types=_types,
                            use_checker_stress=use_checker_stress,
                            checker_exe=checker_exe,
                            is_interactive=is_interactive,
                            is_communication=is_communication,
                            interactor_exe=interactor_exe,
                            run_brute=run_brute,
                            problem_dir=problem_dir,
                            manifest_model=manifest_model,
                            sol_name=effective_sol_name,
                            brute_name=effective_brute_name,
                        )

                    results = await run_batch(batch, _worker, limit=concurrency_limit)

                    for res in results:  # 按轮序遍历，保证与串行一致的失败传播
                        if res.status == "ok":
                            round_stats.append(res.stats)
                            continue
                        if res.status in ("gen_failed", "sol_failed", "brute_failed"):
                            # 这些失败在串行实现中直接返回，此处同样立即返回。
                            return self._build_round_failure(
                                res,
                                round_stats,
                                n_max_advisory,
                                effective_sol_name,
                                effective_brute_name,
                            )
                        # validator_failed / mismatch：记录并停止后续批次
                        failed_round = res.global_round
                        profile_failed_round = res.global_round
                        last_input = res.input_data
                        sol_output = res.sol_output
                        brute_output = res.brute_output
                        if res.status == "validator_failed":
                            validator_failed = True
                            validator_failure_detail = res.validator_detail or {}
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
            validator_failure_detail,
            n_max_advisory,
            complexity_context,
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

            if gen_result.timed_out or not gen_result.success:
                return {
                    "success": False,
                    "error": "Generator exited with non-zero status",
                    "stderr": gen_result.stderr,
                    "stdout": gen_result.stdout,
                    "cmd_args": cmd_args,
                    "seed": seed,
                    "return_code": gen_result.return_code,
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

            with open(input_path, "w", encoding="utf-8", newline="") as f:
                f.write(gen_result.stdout)

            return {
                "success": True,
                "cmd_args": cmd_args,
                "seed": seed,
                "input_data": gen_result.stdout,
            }

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
        validator_failure_detail: dict[str, object] | None = None,
        n_max_advisory: str | None = None,
        complexity_context: dict[str, object] | None = None,
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
                validator_failure_detail=validator_failure_detail or {},
                n_max_warning=n_max_advisory,
                n_max_advisory=n_max_advisory,
                complexity_context=complexity_context or {},
            )

        return ToolResult.ok(
            completed_rounds=total_rounds,
            total_rounds=total_rounds,
            effective_n_max=effective_n_max,
            statistics=statistics,
            stress_profiles=stress_profiles or [],
            n_max_warning=n_max_advisory,
            n_max_advisory=n_max_advisory,
            complexity_context=complexity_context or {},
            message=f"All {total_rounds} rounds passed",
        )

    def _build_n_max_advisory(self, n_max: int, complexity_context: dict[str, object] | None) -> str:
        context = complexity_context or {}
        brute_complexity = context.get("brute_complexity")
        recommended = context.get("recommended_stress_params")
        return (
            "Review n_max against brute complexity evidence instead of fixed thresholds. "
            f"Current n_max={n_max}. "
            f"brute_complexity={brute_complexity if brute_complexity else 'unknown'}. "
            f"recommended_stress_params={recommended if recommended else 'unavailable'}. "
            "Let the LLM decide final stress parameters based on this context."
        )

    def _load_complexity_context(self, problem_dir: str) -> dict[str, object]:
        state = get_section(problem_dir, WORKFLOW)
        if not isinstance(state, dict):
            return {}
        context: dict[str, object] = {}
        for key in ("brute_complexity", "std_complexity", "recommended_stress_params"):
            if key in state:
                context[key] = state[key]
        return context
