"""testlib checker 调用约定：argv 为 input output answer（文件路径）。"""

from __future__ import annotations

import os

from .compiler import RunResult, run_binary_with_args


def verdict_from_run(run: RunResult) -> str:
    """将进程结果映射为 AC/WA/PE/FAIL/TLE。"""
    if run.timed_out:
        return "TLE"
    if run.error and not run.success:
        return "FAIL"
    if not run.success and run.return_code < 0:
        return "FAIL"
    verdict_map = {0: "AC", 1: "WA", 2: "PE"}
    rc = run.return_code
    if rc in verdict_map:
        return verdict_map[rc]
    if rc >= 3:
        return "FAIL"
    return "WA"


async def run_testlib_checker(
    checker_exe: str,
    input_path: str,
    output_path: str,
    answer_path: str,
    *,
    timeout: int,
) -> tuple[str, RunResult]:
    """
    运行 Polygon/testlib 风格 checker。

    Args:
        checker_exe: checker 可执行文件路径
        input_path: 输入文件
        output_path: 选手输出文件
        answer_path: 参考输出（标答）文件
    """
    if not os.path.isfile(checker_exe):
        return "FAIL", RunResult(success=False, error=f"Checker not found: {checker_exe}")
    run = await run_binary_with_args(
        checker_exe,
        [input_path, output_path, answer_path],
        timeout=timeout,
    )
    return verdict_from_run(run), run


def checker_exe_path(problem_dir: str, exe_ext: str) -> str:
    """标准布局：files/checker{exe}。"""
    return os.path.join(problem_dir, "files", f"checker{exe_ext}")
