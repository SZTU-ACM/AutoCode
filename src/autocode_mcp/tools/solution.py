"""
Solution 工具组 - 解法构建和运行。
"""

import os
import shutil
from typing import Literal

from pydantic import ValidationError

from ..utils.communication_runner import (
    CommunicationRunner,
    resolve_participant_binaries,
    safe_resolve_under_root,
)
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..workflow import load_manifest, manifest_is_communication, manifest_is_interactive
from .base import Tool, ToolResult, input_schema_from_model
from .mixins import BuildToolMixin, RunToolMixin, resolve_source
from .schemas import SolutionBuildInput, SolutionRunInput


def _is_safe_solution_stem(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return not any(sep and sep in value for sep in (os.sep, os.altsep, "/", "\\", ":"))


class SolutionBuildTool(Tool, BuildToolMixin):
    """构建并编译解法。"""

    @property
    def name(self) -> str:
        return "solution_build"

    @property
    def description(self) -> str:
        return """构建并编译解法代码。

        基于论文框架，支持：
        - sol.cpp: 标准解法（最优时间复杂度）
        - brute.cpp: 暴力解法（用于验证）

        前置条件：
        1. 已运行 problem_create 创建题目目录

        建议下一步：
        - 构建 brute.cpp 后运行 stress_test_run 验证解法正确性
        - 构建 sol.cpp 后运行 generator_build 构建数据生成器
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(SolutionBuildInput)

    async def execute(
        self,
        problem_dir: str,
        solution_type: Literal["sol", "brute"],
        name: str | None = None,
        code: str | None = None,
        source_path: str | None = None,
        compiler: str = "g++",
    ) -> ToolResult:
        """执行解法构建。"""
        if solution_type not in {"sol", "brute"}:
            return ToolResult.fail("solution_type must be 'sol' or 'brute'")
        if name is not None and not _is_safe_solution_stem(name):
            return ToolResult.fail("name must be a file name stem without path separators")

        resolved, err = resolve_source(
            problem_dir,
            code,
            source_path,
            default_source_path=os.path.join("solutions", f"{solution_type}.cpp"),
        )
        if err is not None:
            return err
        assert resolved is not None

        # 确保目录存在
        os.makedirs(problem_dir, exist_ok=True)
        solutions_dir = os.path.join(problem_dir, "solutions")
        os.makedirs(solutions_dir, exist_ok=True)

        # 确定文件名
        effective_name = name or solution_type
        exe_ext = get_exe_extension()
        canonical_path = os.path.join(solutions_dir, f"{effective_name}.cpp")
        binary_path = os.path.join(solutions_dir, f"{effective_name}{exe_ext}")
        standard_source_path = os.path.join(solutions_dir, f"{solution_type}.cpp")
        standard_binary_path = os.path.join(solutions_dir, f"{solution_type}{exe_ext}")

        for p in (binary_path, standard_binary_path):
            if os.path.isfile(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        # 保存自定义命名文件，并保留 sol.cpp/brute.cpp 供打包和默认流程使用。
        try:
            with open(canonical_path, "w", encoding="utf-8") as f:
                f.write(resolved.code)
            if os.path.normcase(canonical_path) != os.path.normcase(standard_source_path):
                shutil.copy2(canonical_path, standard_source_path)
        except Exception as e:
            return ToolResult.fail(f"Failed to save code: {str(e)}")

        # 编译：source_path 时从原始文件编译，否则从标准位置编译
        compile_source = resolved.original_source_path or canonical_path
        include_dirs_list: list[str] = []
        if resolved.include_dir:
            include_dirs_list.append(resolved.include_dir)
        extra_sources: list[str] | None = None

        try:
            manifest = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")

        if (
            manifest is not None
            and manifest_is_communication(manifest)
            and manifest.protocol is not None
            and manifest.protocol.mode == "grader_linked"
        ):
            grader_rel = manifest.protocol.grader_file or "files/grader.cpp"
            grader_abs = safe_resolve_under_root(problem_dir, grader_rel)
            if grader_abs is None:
                return ToolResult.fail(f"grader_file path escapes problem_dir: {grader_rel}")
            if not os.path.isfile(grader_abs):
                return ToolResult.fail(
                    f"grader_linked communication problem requires {grader_rel}. "
                    "Save the grader source file before running solution_build."
                )
            extra_sources = [grader_abs]
            files_inc = os.path.join(problem_dir, "files")
            if os.path.isdir(files_inc) and files_inc not in include_dirs_list:
                include_dirs_list.append(files_inc)
            if manifest.protocol.header_file:
                hdr_abs = safe_resolve_under_root(problem_dir, manifest.protocol.header_file)
                if hdr_abs is None:
                    return ToolResult.fail(
                        f"header_file path escapes problem_dir: {manifest.protocol.header_file}"
                    )
                hdr_dir = os.path.dirname(hdr_abs)
                if hdr_dir and os.path.isdir(hdr_dir) and hdr_dir not in include_dirs_list:
                    include_dirs_list.append(hdr_dir)

        include_dirs = include_dirs_list or None
        if extra_sources:
            result = await self.build(
                compile_source,
                binary_path,
                compiler=compiler,
                include_dirs=include_dirs,
                extra_sources=extra_sources,
            )
        else:
            result = await self.build(
                compile_source,
                binary_path,
                compiler=compiler,
                include_dirs=include_dirs,
            )

        if not result.success:
            return ToolResult.fail(
                f"Compilation failed: {result.error}",
                source_path=compile_source,
                canonical_path=canonical_path,
                compile_log=result.stderr,
            )

        binary_size = os.path.getsize(binary_path) if os.path.exists(binary_path) else 0
        if os.path.normcase(binary_path) != os.path.normcase(standard_binary_path):
            try:
                shutil.copy2(binary_path, standard_binary_path)
            except Exception as e:
                return ToolResult.fail(f"Failed to save standard binary: {str(e)}")

        return ToolResult.ok(
            source_path=compile_source,
            canonical_path=canonical_path,
            standard_source_path=standard_source_path,
            binary_path=binary_path,
            standard_binary_path=standard_binary_path,
            binary_size=binary_size,
            compile_log=result.stderr,
            effective_name=effective_name,
            message=f"Successfully built {effective_name}",
        )


class SolutionRunTool(Tool, RunToolMixin):
    """运行解法。"""

    @property
    def name(self) -> str:
        return "solution_run"

    @property
    def description(self) -> str:
        return """运行已编译的解法。

        在指定输入上运行解法，返回输出和执行时间。
        用于验证解法正确性或生成答案文件。

        前置条件：
        1. 已运行 solution_build 构建解法

        建议下一步：
        - 如果验证通过，继续构建 generator
        - 如果验证失败，修复解法代码
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(SolutionRunInput)

    async def execute(
        self,
        problem_dir: str,
        solution_type: Literal["sol", "brute"],
        input_data: str,
        name: str | None = None,
        timeout: int = 30,
    ) -> ToolResult:
        """执行解法运行。"""
        effective_name = name or solution_type
        exe_ext = get_exe_extension()

        try:
            manifest = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")

        binary_path = os.path.join(problem_dir, "solutions", f"{effective_name}{exe_ext}")

        # 如果子目录没有，检查根目录（向后兼容）
        if not os.path.exists(binary_path):
            binary_path = os.path.join(problem_dir, f"{effective_name}{exe_ext}")

        if not os.path.exists(binary_path):
            if (
                manifest is not None
                and manifest_is_communication(manifest)
                and manifest.protocol is not None
                and manifest.protocol.participant_solutions
                and effective_name == "sol"
            ):
                parts = resolve_participant_binaries(problem_dir, manifest, None, exe_ext)
                if parts and all(os.path.isfile(p) for p in parts):
                    binary_path = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
                else:
                    return ToolResult.fail(
                        "Participant solution binaries not found: "
                        + ", ".join(manifest.protocol.participant_solutions)
                    )
            else:
                return ToolResult.fail(
                    f"Binary not found: {effective_name}. Please run solution_build first."
                )

        if manifest is not None and manifest_is_communication(manifest):
            if solution_type == "brute":
                run_tl_ms = float(timeout) * 1000.0
            else:
                tl_ms = float(
                    manifest.time_limit_ms
                    if manifest.time_limit_ms > 0
                    else timeout * 1000
                )
                run_tl_ms = min(tl_ms, float(timeout) * 1000.0)
            mem_mb = float(manifest.memory_limit_mb) if manifest.memory_limit_mb > 0 else 256.0
            comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
            judge_res = await CommunicationRunner.run_from_manifest(
                problem_dir=problem_dir,
                manifest=manifest,
                solution_exe=binary_path,
                input_data=input_data,
                time_limit_ms=run_tl_ms,
                memory_limit_mb=mem_mb,
            )
            payload = {
                "mode": f"communication:{comm_mode}",
                "verdict": judge_res.verdict,
                "stdout": judge_res.judge_stdout or judge_res.stdout,
                "stderr": judge_res.stderr,
                "judge_stderr": judge_res.judge_stderr,
                "time_ms": int(judge_res.wall_time_ms),
                "cpu_time_ms": judge_res.cpu_time_ms,
                "memory_mb": judge_res.memory_mb,
                "return_code": judge_res.solution_returncode,
                "judge_return_code": judge_res.judge_returncode,
                "score": judge_res.score,
                "metrics": judge_res.metrics,
                "transcript": judge_res.transcript,
                "detail": judge_res.detail,
            }
            if judge_res.accepted:
                return ToolResult.ok(**payload)
            return ToolResult.fail(
                f"Communication execution verdict: {judge_res.verdict}"
                + (f" ({judge_res.detail})" if judge_res.detail else ""),
                **payload,
            )

        # 交互题自动通过 InteractiveRunner 与 files/interactor 运行双向交互
        if manifest_is_interactive(manifest):
            interactor_exe = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
            if not os.path.isfile(interactor_exe):
                fallback_int = os.path.join(problem_dir, f"interactor{exe_ext}")
                if os.path.isfile(fallback_int):
                    interactor_exe = fallback_int
            if not os.path.isfile(interactor_exe):
                return ToolResult.fail(
                    f"Interactive problem requires files/interactor{exe_ext}. Run interactor_build first."
                )
            if solution_type == "brute":
                run_tl_ms = float(timeout) * 1000.0
            else:
                tl_ms = float(
                    manifest.time_limit_ms
                    if (manifest and manifest.time_limit_ms > 0)
                    else timeout * 1000
                )
                run_tl_ms = min(tl_ms, float(timeout) * 1000.0)
            mem_mb = float(manifest.memory_limit_mb) if (manifest and manifest.memory_limit_mb > 0) else 256.0
            judge_res = await InteractiveRunner.run(
                interactor_exe=interactor_exe,
                solution_exe=binary_path,
                input_data=input_data,
                time_limit_ms=run_tl_ms,
                memory_limit_mb=mem_mb,
                cwd=problem_dir,
            )
            payload = {
                "mode": "interactive",
                "verdict": judge_res.verdict,
                "stdout": judge_res.judge_stdout,
                "stderr": judge_res.stderr,
                "judge_stderr": judge_res.judge_stderr,
                "time_ms": int(judge_res.wall_time_ms),
                "cpu_time_ms": judge_res.cpu_time_ms,
                "memory_mb": judge_res.memory_mb,
                "return_code": judge_res.solution_returncode,
                "judge_return_code": judge_res.judge_returncode,
                "metrics": judge_res.metrics,
                "transcript": judge_res.transcript,
                "detail": judge_res.detail,
            }
            if judge_res.accepted:
                return ToolResult.ok(**payload)
            return ToolResult.fail(
                f"Interactive execution verdict: {judge_res.verdict}"
                + (f" ({judge_res.detail})" if judge_res.detail else ""),
                **payload,
            )

        # 运行
        result = await self.run(
            binary_path, input_data, problem_dir, solution_type, timeout=timeout
        )

        if result.memory_limit_exceeded:
            return ToolResult.fail(
                f"Memory limit exceeded (MLE) after {result.time_ms}ms",
                stdout=result.stdout,
                stderr=result.stderr,
                time_ms=result.time_ms,
                memory_limit_exceeded=True,
            )

        if result.timed_out:
            return ToolResult.fail(
                f"Execution timed out after {timeout}s",
                stdout=result.stdout,
                stderr=result.stderr,
                time_ms=result.time_ms,
            )

        if not result.success:
            return ToolResult.fail(
                f"Execution failed with return code {result.return_code}",
                stdout=result.stdout,
                stderr=result.stderr,
                time_ms=result.time_ms,
            )

        return ToolResult.ok(
            stdout=result.stdout,
            stderr=result.stderr,
            time_ms=result.time_ms,
            return_code=result.return_code,
        )
