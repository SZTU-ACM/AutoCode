"""D 方向 8.1：examples 端到端 + 并发产物一致性基准。

- 三个 example 配置（checker/exact/interactive）应能被 `load_manifest` 真实加载，
  且语义字段符合各自类型（examples/ 此前零测试覆盖）。
- 用真实 checker-sample 配置驱动 `problem_generate_tests`（确定性 mock 二进制），
  断言串行（limit=1）与并发（limit=4）产出一致的测试集——呼应 1.5 的并发一致性基准，
  但建立在真实 example 配置之上，验证 examples 可被端到端驱动且并发路径稳定。
"""

import os
import tempfile
import unittest.mock as mock

import pytest

from autocode_mcp.runtime_store import get_section
from autocode_mcp.tools.problem import ProblemGenerateTestsTool
from autocode_mcp.utils.compiler import RunResult
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow.manifest import load_manifest

EXAMPLES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "examples"))


def _copy_example_manifest(name: str, problem_dir: str) -> None:
    src = os.path.join(EXAMPLES_DIR, name, "autocode.json")
    with open(src, encoding="utf-8") as fin:
        content = fin.read()
    with open(os.path.join(problem_dir, "autocode.json"), "w", encoding="utf-8") as fout:
        fout.write(content)


@pytest.mark.asyncio
async def test_examples_manifests_loadable_and_self_consistent():
    """三个 example manifest 应能被真实加载且语义字段符合各自类型。"""
    checker = load_manifest(os.path.join(EXAMPLES_DIR, "checker-sample"))
    exact = load_manifest(os.path.join(EXAMPLES_DIR, "exact-sample"))
    interactive = load_manifest(os.path.join(EXAMPLES_DIR, "interactive-sample"))

    assert checker is not None
    assert checker.special_judge is True
    assert checker.stress_comparison == "checker"
    assert len(checker.case_plan) == 4

    assert exact is not None
    assert exact.special_judge is False
    assert exact.stress_comparison == "exact"

    assert interactive is not None
    assert interactive.interactive is True


async def _run_example_generate(concurrency_limit: int) -> list[tuple]:
    """用真实 checker-sample 配置 + 确定性 mock 二进制跑 generate_tests，返回测试产物序列。"""
    tool = ProblemGenerateTestsTool()

    async def fake_run_binary_with_args(binary_path, args, timeout=5, process_start_hook=None, **kwargs):
        seed = int(args[0])
        return RunResult(success=True, stdout=f"{seed}\n")

    async def fake_run_binary(binary_path, stdin="", timeout=5, memory_mb=256):
        value = int(stdin.strip())
        return RunResult(success=True, stdout=f"{value * value}\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, f"ex_{concurrency_limit}")
        files_dir = os.path.join(problem_dir, "files")
        solutions_dir = os.path.join(problem_dir, "solutions")
        tests_dir = os.path.join(problem_dir, "tests")
        os.makedirs(files_dir)
        os.makedirs(solutions_dir)

        _copy_example_manifest("checker-sample", problem_dir)
        # 真实 example 配置应能被加载——端到端驱动的前提。
        assert load_manifest(problem_dir).special_judge is True

        exe_ext = get_exe_extension()
        open(os.path.join(files_dir, f"gen{exe_ext}"), "w").close()
        open(os.path.join(solutions_dir, f"sol{exe_ext}"), "w").close()

        with (
            mock.patch("autocode_mcp.tools.problem.run_binary_with_args", fake_run_binary_with_args),
            mock.patch("autocode_mcp.tools.problem.run_binary", fake_run_binary),
        ):
            result = await tool.execute(
                problem_dir=problem_dir,
                test_count=8,
                enable_dedup=True,
                enable_balance=False,
                oversample_ratio=1.0,
                concurrency_limit=concurrency_limit,
            )

        assert result.success, result.error
        manifest = get_section(problem_dir, "test_manifest") or {}
        summary = []
        for t in manifest["tests"]:
            in_path = os.path.join(tests_dir, t["in_file"])
            ans_path = os.path.join(tests_dir, t["ans_file"])
            with open(in_path, encoding="utf-8") as fin:
                in_data = fin.read()
            with open(ans_path, encoding="utf-8") as fans:
                ans_data = fans.read()
            summary.append((t["type_param"], t["signature"], in_data, ans_data))
        return summary


@pytest.mark.asyncio
async def test_example_checker_sample_serial_matches_concurrent():
    """真实 checker-sample 配置驱动下，串行（limit=1）与并发（limit=4）应产出一致测试集。"""
    serial = await _run_example_generate(1)
    concurrent = await _run_example_generate(4)

    assert serial == concurrent
    assert len(serial) == 8
