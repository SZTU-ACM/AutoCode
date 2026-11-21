"""
Interactor 工具组 - 交互器。

基于论文 Algorithm 4: BUILDINTERACTOR 实现，使用统一 InteractiveRunner
按 testlib registerInteraction(argc, argv) 约定执行真实双向交互验证。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

from pydantic import ValidationError

from ..utils.checker_judge import verdict_from_run, verdict_from_testlib_rc
from ..utils.communication_runner import (
    CommunicationRunner,
    _run_communicator_stage,
    resolve_communicator_source_rel,
    safe_resolve_under_root,
)
from ..utils.compiler import compile_cpp, run_binary_with_args
from ..utils.judge_runner import InteractiveRunner, JudgeResult
from ..utils.platform import get_exe_extension
from ..utils.process import terminate_pid_tree
from ..workflow import load_manifest, manifest_is_communication
from ..workflow.models import AutoCodeManifest
from .base import Tool, ToolResult, input_schema_from_model
from .mixins import resolve_source
from .schemas import InteractorBuildInput


class InteractorBuildTool(Tool):
    """构建并验证交互器。"""

    @property
    def name(self) -> str:
        return "interactor_build"

    @property
    def description(self) -> str:
        return """构建并验证交互器。

        基于论文 Algorithm 4 实现:
        1. 保存代码到 problem_dir/files/interactor.cpp
        2. 编译生成 files/interactor.exe
        3. 可用 interaction_scenarios 按 testlib registerInteraction 约定验证协议判定
        4. 可用 reference_solution_path / mutant_solutions + test_inputs 走真实双向管道验证区分能力
        5. 返回 scenario accuracy、pass_rate、fail_rate 与交互 transcript 证据

        注意：此工具不生成代码，代码由 Client LLM 生成后传入。
        交互场景和变异程序也应由 Client LLM 生成。
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(InteractorBuildInput)

    async def execute(
        self,
        problem_dir: str,
        code: str | None = None,
        source_path: str | None = None,
        reference_solution_path: str | None = None,
        mutant_solutions: list[str] | None = None,
        interaction_scenarios: list[dict] | None = None,
        test_inputs: list[str] | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行 Interactor / Communicator 构建。"""
        try:
            manifest = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")

        is_communication = manifest is not None and manifest_is_communication(manifest)
        if (
            is_communication
            and manifest is not None
            and manifest.protocol is not None
            and manifest.protocol.mode == "grader_linked"
        ):
            return ToolResult.fail(
                "grader_linked communication problems link files/grader.cpp with solution_build; "
                "interactor_build is not used."
            )
        default_rel = (
            resolve_communicator_source_rel(manifest)
            if is_communication
            else "files/interactor.cpp"
        )

        resolved, err = resolve_source(
            problem_dir,
            code,
            source_path,
            default_source_path=default_rel,
        )
        if err is not None:
            return err
        assert resolved is not None

        os.makedirs(problem_dir, exist_ok=True)
        canonical_path = safe_resolve_under_root(problem_dir, default_rel)
        if canonical_path is None:
            return ToolResult.fail(f"Target communicator/interactor path escapes problem_dir: {default_rel}")
        os.makedirs(os.path.dirname(canonical_path), exist_ok=True)
        try:
            with open(canonical_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(resolved.code)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        binary_rel = f"{os.path.splitext(default_rel)[0]}{get_exe_extension()}"
        binary_path = safe_resolve_under_root(problem_dir, binary_rel)
        if binary_path is None:
            return ToolResult.fail(f"Target binary path escapes problem_dir: {binary_rel}")

        if os.path.isfile(binary_path):
            try:
                os.remove(binary_path)
            except OSError:
                pass

        compile_source = resolved.original_source_path or canonical_path
        include_dirs = [resolved.include_dir] if resolved.include_dir else None
        compile_result = await compile_cpp(
            compile_source, binary_path, compiler=compiler, include_dirs=include_dirs
        )

        if not compile_result.success:
            return ToolResult.fail(
                f"Compilation failed: {compile_result.error}",
                source_path=compile_source,
                canonical_path=canonical_path,
                compile_log=compile_result.stderr,
            )

        binary_size = os.path.getsize(binary_path) if os.path.exists(binary_path) else 0

        scenario_report = None
        if interaction_scenarios:
            scenario_report = await self._run_interaction_scenarios(
                interactor_exe=binary_path,
                problem_dir=problem_dir,
                scenarios=interaction_scenarios,
                timeout=10,
                manifest=manifest,
            )
            if scenario_report["accuracy"] < 1.0:
                return ToolResult.fail(
                    "Interactor scripted scenarios failed",
                    source_path=compile_source,
                    canonical_path=canonical_path,
                    binary_path=binary_path,
                    binary_size=binary_size,
                    compile_log=compile_result.stderr,
                    interaction_scenarios=scenario_report,
                    scenario_accuracy=scenario_report["accuracy"],
                )
            autocode_dir = os.path.join(problem_dir, ".autocode")
            os.makedirs(autocode_dir, exist_ok=True)
            scen_name = (
                "communicator_scenarios.json" if is_communication else "interactor_scenarios.json"
            )
            with open(os.path.join(autocode_dir, scen_name), "w", encoding="utf-8", newline="\n") as sf:
                json.dump(interaction_scenarios, sf, ensure_ascii=False, indent=2)

        # 如果没有提供任何解法验证，直接返回成功（pass_rate 为 0）
        if not reference_solution_path and not mutant_solutions:
            message = "Communicator built successfully" if is_communication else "Interactor built successfully"
            if scenario_report is None:
                message += " (no validation performed)"
            else:
                message += f", scenario_accuracy={scenario_report['accuracy']:.2%}"
            return ToolResult.ok(
                source_path=compile_source,
                canonical_path=canonical_path,
                binary_path=binary_path,
                binary_size=binary_size,
                compile_log=compile_result.stderr,
                interaction_scenarios=scenario_report,
                scenario_accuracy=scenario_report["accuracy"] if scenario_report else None,
                pass_rate=0.0,
                fail_rate=0.0,
                pass_count=0,
                pass_total=0,
                fail_count=0,
                fail_total=0,
                message=message,
            )

        # 解析真实双向交互/通信使用的测试输入列表：
        # 优先 test_inputs -> 其次 interaction_scenarios 中提取的唯一输入 -> 兜底 [""]
        live_inputs = self._resolve_live_test_inputs(test_inputs, interaction_scenarios)

        async def _run_live(sol_path: str, input_data: str) -> JudgeResult:
            if is_communication and manifest is not None:
                return await CommunicationRunner.run_from_manifest(
                    problem_dir=problem_dir,
                    manifest=manifest,
                    solution_exe=sol_path,
                    input_data=input_data,
                    time_limit_ms=5000.0,
                )
            return await InteractiveRunner.run(
                interactor_exe=binary_path,
                solution_exe=sol_path,
                input_data=input_data,
                time_limit_ms=5000.0,
                cwd=problem_dir,
            )

        pass_count = 0
        pass_total = 0
        reference_details: list[dict] = []

        if reference_solution_path:
            if not os.path.exists(reference_solution_path):
                return ToolResult.fail(
                    f"Reference solution not found: {reference_solution_path}",
                    source_path=compile_source,
                    canonical_path=canonical_path,
                    binary_path=binary_path,
                )
            pass_total = len(live_inputs)
            for idx, input_data in enumerate(live_inputs, 1):
                judge_res = await _run_live(reference_solution_path, input_data)
                if judge_res.accepted:
                    pass_count += 1
                reference_details.append(
                    {
                        "index": idx,
                        "verdict": judge_res.verdict,
                        "cpu_time_ms": judge_res.cpu_time_ms,
                        "metrics": judge_res.metrics,
                        "judge_stderr": judge_res.judge_stderr[:500],
                        "detail": judge_res.detail,
                        "transcript": judge_res.transcript[:2000],
                    }
                )

        fail_count = 0
        fail_total = len(mutant_solutions) if mutant_solutions else 0
        mutant_details: list[dict] = []

        if mutant_solutions:
            for mutant_path in mutant_solutions:
                if not os.path.exists(mutant_path):
                    mutant_details.append(
                        {"path": mutant_path, "killed": False, "verdict": "MISSING"}
                    )
                    continue
                killed = False
                judge_failed = False
                last_verdict = "AC"
                last_detail = ""
                for input_data in live_inputs:
                    judge_res = await _run_live(mutant_path, input_data)
                    last_verdict = judge_res.verdict
                    last_detail = judge_res.detail
                    if judge_res.verdict == "FAIL":
                        judge_failed = True
                        killed = False
                        break
                    if not judge_res.accepted:
                        killed = True
                        break
                if killed:
                    fail_count += 1
                mutant_entry: dict[str, object] = {
                    "path": mutant_path,
                    "killed": killed,
                    "verdict": last_verdict,
                    "detail": last_detail,
                }
                if judge_failed:
                    mutant_entry["judge_failed"] = True
                mutant_details.append(mutant_entry)

        pass_rate = pass_count / pass_total if pass_total > 0 else 0.0
        fail_rate = fail_count / fail_total if fail_total > 0 else 0.0

        return ToolResult.ok(
            source_path=compile_source,
            canonical_path=canonical_path,
            binary_path=binary_path,
            binary_size=binary_size,
            compile_log=compile_result.stderr,
            interaction_scenarios=scenario_report,
            scenario_accuracy=scenario_report["accuracy"] if scenario_report else None,
            pass_rate=pass_rate,
            fail_rate=fail_rate,
            pass_count=pass_count,
            pass_total=pass_total,
            fail_count=fail_count,
            fail_total=fail_total,
            reference_results=reference_details,
            mutant_results=mutant_details,
            message=(
                f"{'Communicator' if is_communication else 'Interactor'} built, "
                f"pass_rate={pass_rate:.2%}, fail_rate={fail_rate:.2%}"
            ),
        )

    @staticmethod
    def _resolve_live_test_inputs(
        test_inputs: list[str] | None,
        interaction_scenarios: list[dict] | None,
    ) -> list[str]:
        if test_inputs:
            return list(test_inputs)
        if interaction_scenarios:
            seen: set[str] = set()
            extracted: list[str] = []
            for scenario in interaction_scenarios:
                raw = scenario.get("input")
                if isinstance(raw, str) and raw not in seen:
                    seen.add(raw)
                    extracted.append(raw)
            if extracted:
                return extracted
        return [""]

    async def _run_interaction_scenarios(
        self,
        interactor_exe: str,
        problem_dir: str,
        scenarios: list[dict],
        timeout: int = 10,
        manifest: AutoCodeManifest | None = None,
    ) -> dict:
        """用脚本化选手输出验证 testlib interactor / communicator 的协议判定能力。"""
        details = []
        correct_count = 0
        is_run_twice = (
            manifest is not None
            and manifest_is_communication(manifest)
            and (manifest.protocol is None or manifest.protocol.mode == "run_twice")
        )
        total_passes = manifest.protocol.passes if (manifest and manifest.protocol) else 2

        with tempfile.TemporaryDirectory(dir=os.path.abspath(problem_dir)) as raw_temp_dir:
            temp_dir = os.path.abspath(raw_temp_dir)
            for i, scenario in enumerate(scenarios):
                input_data = scenario.get("input", "")
                answer_data = scenario.get("answer") or ""
                contestant_output = scenario.get("contestant_output", "")
                expected_verdict = scenario.get("expected_verdict", "AC")

                input_file = os.path.join(temp_dir, f"input_{i}.txt")
                output_file = os.path.join(temp_dir, f"interaction_{i}.txt")
                answer_file = os.path.join(temp_dir, f"answer_{i}.txt")

                with open(input_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write(input_data)
                with open(answer_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write(answer_data)

                if is_run_twice:
                    # run_twice communicator 使用 registerTestlibCmd：argv[2] 为选手本阶段输出文件
                    with open(output_file, "w", encoding="utf-8", newline="\n") as f:
                        f.write(contestant_output)
                    stage = int(scenario.get("stage") or total_passes)
                    stage_env = dict(os.environ)
                    stage_env["AUTOCODE_COMM_STAGE"] = str(stage)
                    stage_env["AUTOCODE_COMM_PASSES"] = str(total_passes)
                    stage_env["AUTOCODE_COMM_ROLE"] = "channel" if stage < total_passes else "judge"
                    stage_env["AUTOCODE_COMM_ANS_PATH"] = answer_file
                    rc, out_str, err_str, timed_out = await _run_communicator_stage(
                        os.path.abspath(interactor_exe),
                        [input_file, output_file, answer_file],
                        env=stage_env,
                        timeout_sec=float(timeout),
                        cwd=temp_dir,
                    )
                    actual_verdict = "TLE" if timed_out else verdict_from_testlib_rc(rc)
                    interactor_stdout = out_str
                    effective_output = out_str
                    stderr_snippet = err_str[:500]
                else:
                    if (
                        manifest is not None
                        and manifest_is_communication(manifest)
                        and manifest.protocol is not None
                        and manifest.protocol.mode == "multi_process"
                    ):
                        # multi_process communicator expects AUTOCODE_COMM_MODE / AUTOCODE_COMM_PROCS
                        mp_env = dict(os.environ)
                        mp_env["AUTOCODE_COMM_MODE"] = "multi_process"
                        mp_env["AUTOCODE_COMM_PROCS"] = str(
                            max(2, len(manifest.protocol.participant_solutions))
                        )
                        proc_mp = await asyncio.create_subprocess_exec(
                            os.path.abspath(interactor_exe),
                            input_file,
                            output_file,
                            answer_file,
                            stdin=asyncio.subprocess.PIPE,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=temp_dir,
                            env=mp_env,
                        )
                        try:
                            out_b, err_b = await asyncio.wait_for(
                                proc_mp.communicate(contestant_output.encode("utf-8")),
                                timeout=timeout,
                            )
                            actual_verdict = verdict_from_testlib_rc(proc_mp.returncode)
                            interactor_stdout = out_b.decode("utf-8", errors="replace")
                            stderr_snippet = err_b.decode("utf-8", errors="replace")[:500]
                        except asyncio.CancelledError:
                            await terminate_pid_tree(proc_mp.pid)
                            try:
                                await asyncio.wait_for(proc_mp.wait(), timeout=1.0)
                            except Exception:
                                pass
                            raise
                        except (asyncio.TimeoutError, TimeoutError):
                            await terminate_pid_tree(proc_mp.pid)
                            try:
                                await asyncio.wait_for(proc_mp.wait(), timeout=1.0)
                            except Exception:
                                pass
                            actual_verdict = "TLE"
                            interactor_stdout = ""
                            stderr_snippet = "timeout"
                        tout_output = ""
                        if os.path.exists(output_file):
                            with open(output_file, encoding="utf-8", errors="replace") as f:
                                tout_output = f.read()
                        effective_output = tout_output if tout_output else interactor_stdout
                    else:
                        run_result = await run_binary_with_args(
                            interactor_exe,
                            [input_file, output_file, answer_file],
                            stdin=contestant_output,
                            timeout=timeout,
                        )
                        actual_verdict = verdict_from_run(run_result)
                        tout_output = ""
                        if os.path.exists(output_file):
                            with open(output_file, encoding="utf-8", errors="replace") as f:
                                tout_output = f.read()
                        # 兼容标准 testlib（std::cout 发给选手、tout 写 argv[2]）与历史仅写 tout 的脚本场景
                        interactor_stdout = run_result.stdout or ""
                        effective_output = tout_output if tout_output else interactor_stdout
                        stderr_snippet = (run_result.stderr or "")[:500]

                is_correct = actual_verdict == expected_verdict
                if is_correct:
                    correct_count += 1

                details.append(
                    {
                        "index": i + 1,
                        "expected_verdict": expected_verdict,
                        "actual_verdict": actual_verdict,
                        "correct": is_correct,
                        "interactor_output": effective_output,
                        "interactor_stdout": interactor_stdout,
                        "stderr": stderr_snippet,
                    }
                )

        total = len(scenarios)
        return {
            "validated": True,
            "correct_count": correct_count,
            "total": total,
            "accuracy": correct_count / total if total else 0.0,
            "details": details,
        }
