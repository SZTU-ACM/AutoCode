"""
Problem 工具组测试。
"""

import asyncio
import json
import os
import tempfile

import pytest

from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.problem import (
    CandidateTest,
    ProblemCleanupProcessesTool,
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
)
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.utils.compiler import RunResult
from autocode_mcp.utils.platform import get_exe_extension


def write_verified_workflow_state(problem_dir: str) -> None:
    workflow_dir = os.path.join(problem_dir, ".autocode-workflow")
    os.makedirs(workflow_dir, exist_ok=True)
    with open(os.path.join(workflow_dir, "state.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "tests_verified": True,
                "verify_signals": {
                    "limit_semantics": {"executed": True, "passed": True},
                    "wrong_solution_kill": {"executed": True, "passed": True},
                    "validator_check": {"executed": True, "passed": True},
                },
            },
            f,
        )


@pytest.mark.asyncio
async def test_problem_create():
    """测试题目目录创建。"""
    tool = ProblemCreateTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "test_problem")
        result = await tool.execute(
            problem_dir=problem_dir,
            problem_name="Test Problem",
        )

        assert result.success
        assert os.path.exists(problem_dir)
        assert os.path.exists(os.path.join(problem_dir, "files"))
        assert os.path.exists(os.path.join(problem_dir, "solutions"))
        assert os.path.exists(os.path.join(problem_dir, "statements"))
        assert os.path.exists(os.path.join(problem_dir, "tests"))


@pytest.mark.asyncio
async def test_problem_create_readme():
    """测试题目 README 创建。"""
    tool = ProblemCreateTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "readme_test")
        await tool.execute(
            problem_dir=problem_dir,
            problem_name="README Test",
        )

        readme_path = os.path.join(problem_dir, "statements", "README.md")
        assert os.path.exists(readme_path)

        with open(readme_path, encoding="utf-8") as f:
            content = f.read()
            assert "README Test" in content
            assert "## 时间限制与空间限制" in content
            assert "## 题目背景（可选）" in content
            assert "## 题目描述" in content
            assert "## 输入格式" in content
            assert "## 输出格式" in content
            assert "### 样例输入 #1" in content
            assert "### 样例输出 #1" in content
            assert "## 说明" in content


@pytest.mark.asyncio
async def test_problem_pack_polygon():
    """测试 Polygon 打包。"""
    create_tool = ProblemCreateTool()
    pack_tool = ProblemPackPolygonTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_test")

        # 创建目录
        await create_tool.execute(
            problem_dir=problem_dir,
            problem_name="Pack Test",
        )

        # 创建一些测试文件
        with open(os.path.join(problem_dir, "sol.cpp"), "w") as f:
            f.write("// sol.cpp")
        with open(os.path.join(problem_dir, "brute.cpp"), "w") as f:
            f.write("// brute.cpp")
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        write_verified_workflow_state(problem_dir)

        # 打包
        result = await pack_tool.execute(problem_dir=problem_dir)

        assert result.success
        assert os.path.exists(os.path.join(problem_dir, "problem.xml"))


@pytest.mark.asyncio
async def test_problem_pack_polygon_creates_xml():
    """测试 Polygon 打包生成 problem.xml。"""
    tool = ProblemPackPolygonTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "xml_test")
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# Test\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        write_verified_workflow_state(problem_dir)

        result = await tool.execute(
            problem_dir=problem_dir,
            time_limit=2,  # 秒
            memory_limit=512,  # MB
        )

        assert result.success

        xml_path = os.path.join(problem_dir, "problem.xml")
        assert os.path.exists(xml_path)

        with open(xml_path) as f:
            content = f.read()
            assert "2000" in content  # time_limit_ms (2秒 * 1000)
            assert "536870912" in content  # memory_limit_bytes (512MB * 1024 * 1024)


@pytest.mark.asyncio
async def test_problem_generate_tests_custom_configs():
    """测试使用自定义 test_configs 生成测试数据。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    create_tool = ProblemCreateTool()
    gen_tool = GeneratorBuildTool()
    sol_tool = SolutionBuildTool()
    generate_tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "custom_config_test")
        await create_tool.execute(problem_dir=problem_dir, problem_name="Custom Config")

        # 简单的 generator 和 solution
        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    int n_min = atoi(argv[3]);
    int n_max = atoi(argv[4]);
    rnd.setSeed(seed);
    int n = rnd.next(n_min, n_max);
    std::cout << n << std::endl;
    return 0;
}
"""
        simple_sol = """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n << std::endl;
    return 0;
}
"""
        await gen_tool.execute(problem_dir=problem_dir, code=simple_gen)
        await sol_tool.execute(problem_dir=problem_dir, solution_type="sol", code=simple_sol)

        # 使用自定义配置
        custom_configs = [
            {"type": "1", "n_min": 1, "n_max": 5, "t_min": 1, "t_max": 1, "seed_offset": 0},
            {"type": "2", "n_min": 10, "n_max": 20, "t_min": 1, "t_max": 1, "seed_offset": 0},
        ]

        result = await generate_tool.execute(
            problem_dir=problem_dir,
            test_count=2,
            test_configs=custom_configs,
        )

        assert result.success
        assert len(result.data["generated_tests"]) == 2


@pytest.mark.asyncio
async def test_problem_generate_tests_constraints_validation():
    """测试 constraints 参数验证。"""
    tool = ProblemGenerateTestsTool()

    # 测试 t_max 必须为正整数
    result = await tool.execute(
        problem_dir="/dummy",
        constraints={"t_max": 0},
    )
    assert not result.success
    assert "t_max must be positive" in result.error

    result = await tool.execute(
        problem_dir="/dummy",
        constraints={"t_max": -1},
    )
    assert not result.success
    assert "t_max must be positive" in result.error

    # 测试 sum_n_max 必须为正整数
    result = await tool.execute(
        problem_dir="/dummy",
        constraints={"sum_n_max": 0},
    )
    assert not result.success
    assert "sum_n_max must be positive" in result.error

    result = await tool.execute(
        problem_dir="/dummy",
        constraints={"sum_n_max": -5},
    )
    assert not result.success
    assert "sum_n_max must be positive" in result.error

    # 测试 n_max 不能大于 sum_n_max
    result = await tool.execute(
        problem_dir="/dummy",
        constraints={"n_max": 1000, "sum_n_max": 500},
    )
    assert not result.success
    assert "n_max cannot be greater than sum_n_max" in result.error

    # 测试 t_max 不能大于 sum_n_max
    result = await tool.execute(
        problem_dir="/dummy",
        constraints={"t_max": 100, "sum_n_max": 50},
    )
    assert not result.success
    assert "t_max cannot be greater than sum_n_max" in result.error

    # 测试有效的 constraints 应该通过验证（会因缺少 generator 而失败，但不是验证失败）
    result = await tool.execute(
        problem_dir="/nonexistent",
        constraints={"n_max": 1000, "t_max": 10, "sum_n_max": 10000},
    )
    assert not result.success
    assert "Generator not found" in result.error  # 验证通过，但找不到 generator


def test_problem_generate_tests_default_configs_are_valid_for_small_constraints():
    """smart mode 在小 n_max 下不应生成 n_min > n_max 或 0 边界。"""
    tool = ProblemGenerateTestsTool()

    for n_max in (1, 10, 50, 100):
        configs = tool._get_default_configs({"n_max": n_max, "t_max": 1})
        assert configs
        for _, _, n_min, cfg_n_max, t_min, t_max, _ in configs:
            assert 1 <= int(n_min) <= int(cfg_n_max) <= n_max
            assert 1 <= int(t_min) <= int(t_max)


@pytest.mark.asyncio
async def test_problem_generate_tests_test_configs_validation():
    """测试 test_configs 参数验证。"""
    tool = ProblemGenerateTestsTool()

    # 测试缺少 type 字段
    result = await tool.execute(
        problem_dir="/dummy",
        test_configs=[{"n_min": 1, "n_max": 10, "t_min": 1, "t_max": 5}],
    )
    assert not result.success
    assert "test_configs[0]: 'type' is required" in result.error

    # 测试 type 字段值无效
    result = await tool.execute(
        problem_dir="/dummy",
        test_configs=[{"type": "5", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 5}],
    )
    assert not result.success
    assert "test_configs[0]: 'type' must be one of '1', '2', '3', '4'" in result.error

    # 测试缺少必需字段
    result = await tool.execute(
        problem_dir="/dummy",
        test_configs=[{"type": "1", "n_min": 1, "n_max": 10, "t_min": 1}],  # 缺少 t_max
    )
    assert not result.success
    assert "test_configs[0]: 't_max' is required" in result.error

    # 测试字段值为负数
    result = await tool.execute(
        problem_dir="/dummy",
        test_configs=[{"type": "1", "n_min": -1, "n_max": 10, "t_min": 1, "t_max": 5}],
    )
    assert not result.success
    assert "test_configs[0]: 'n_min' must be a non-negative integer" in result.error

    # 测试 n_min > n_max
    result = await tool.execute(
        problem_dir="/dummy",
        test_configs=[{"type": "1", "n_min": 100, "n_max": 10, "t_min": 1, "t_max": 5}],
    )
    assert not result.success
    assert "test_configs[0]: n_min cannot be greater than n_max" in result.error

    # 测试 t_min > t_max
    result = await tool.execute(
        problem_dir="/dummy",
        test_configs=[{"type": "1", "n_min": 1, "n_max": 10, "t_min": 10, "t_max": 5}],
    )
    assert not result.success
    assert "test_configs[0]: t_min cannot be greater than t_max" in result.error

    # 测试有效的 test_configs 应该通过验证（会因缺少 generator 而失败）
    result = await tool.execute(
        problem_dir="/nonexistent",
        test_configs=[{"type": "1", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 5}],
    )
    assert not result.success
    assert "Generator not found" in result.error  # 验证通过，但找不到 generator


@pytest.mark.asyncio
async def test_problem_generate_tests_rejects_unsafe_output_dir():
    """测试拒绝危险的测试输出目录。"""
    tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "unsafe_output")
        os.makedirs(os.path.join(problem_dir, "files"))
        os.makedirs(os.path.join(problem_dir, "solutions"))

        result = await tool.execute(problem_dir=problem_dir, output_dir=".")
        assert not result.success
        assert "output_dir cannot be the problem_dir root" in result.error

        result = await tool.execute(problem_dir=problem_dir, output_dir="solutions")
        assert not result.success
        assert "reserved directory" in result.error

        result = await tool.execute(problem_dir=problem_dir, output_dir="solutions/generated")
        assert not result.success
        assert "reserved directory" in result.error

        outside_dir = os.path.join(tmpdir, "outside")
        result = await tool.execute(problem_dir=problem_dir, output_dir=outside_dir)
        assert not result.success
        assert "output_dir must be inside problem_dir" in result.error


@pytest.mark.asyncio
async def test_problem_generate_tests_rejects_symlinked_output_dir():
    """测试拒绝指向题目目录外部的符号链接输出目录。"""
    tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "symlink_output")
        outside_dir = os.path.join(tmpdir, "outside_tests")
        link_dir = os.path.join(problem_dir, "tests_link")
        os.makedirs(os.path.join(problem_dir, "files"))
        os.makedirs(os.path.join(problem_dir, "solutions"))
        os.makedirs(outside_dir)

        try:
            os.symlink(outside_dir, link_dir, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation is not available")

        result = await tool.execute(problem_dir=problem_dir, output_dir="tests_link")

        assert not result.success
        assert "output_dir must be inside problem_dir" in result.error


def test_problem_generate_tests_clear_only_generated_files():
    """测试清理输出目录时只删除旧的 .in/.ans 文件。"""
    tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir)
        keep_path = os.path.join(tests_dir, "notes.txt")
        old_in_path = os.path.join(tests_dir, "01.in")
        old_ans_path = os.path.join(tests_dir, "01.ans")

        with open(keep_path, "w", encoding="utf-8") as f:
            f.write("keep me")
        with open(old_in_path, "w", encoding="utf-8") as f:
            f.write("old input")
        with open(old_ans_path, "w", encoding="utf-8") as f:
            f.write("old answer")

        result = tool._clear_generated_tests(tests_dir)

        assert result is None
        assert os.path.exists(keep_path)
        assert not os.path.exists(old_in_path)
        assert not os.path.exists(old_ans_path)


def test_problem_generate_tests_clear_only_generated_files_with_custom_answer_ext():
    """测试清理输出目录时可按自定义答案后缀删除文件。"""
    tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir)
        keep_path = os.path.join(tests_dir, "notes.txt")
        old_in_path = os.path.join(tests_dir, "01.in")
        old_out_path = os.path.join(tests_dir, "01.out")

        with open(keep_path, "w", encoding="utf-8") as f:
            f.write("keep me")
        with open(old_in_path, "w", encoding="utf-8") as f:
            f.write("old input")
        with open(old_out_path, "w", encoding="utf-8") as f:
            f.write("old answer")

        result = tool._clear_generated_tests(tests_dir, ".out")

        assert result is None
        assert os.path.exists(keep_path)
        assert not os.path.exists(old_in_path)
        assert not os.path.exists(old_out_path)


@pytest.mark.asyncio
async def test_problem_generate_tests_uses_custom_sol_name(monkeypatch):
    """测试生成答案时使用自定义 sol_name。"""
    tool = ProblemGenerateTestsTool()

    async def fake_run_binary_with_args(*args, **kwargs):
        return RunResult(success=True, stdout="7\n")

    async def fake_run_binary(binary_path, stdin="", timeout=5, memory_mb=256):
        assert os.path.basename(binary_path) == f"accepted{get_exe_extension()}"
        assert stdin == "7\n"
        return RunResult(success=True, stdout="7\n")

    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary_with_args", fake_run_binary_with_args)
    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary", fake_run_binary)

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "custom_sol")
        files_dir = os.path.join(problem_dir, "files")
        solutions_dir = os.path.join(problem_dir, "solutions")
        os.makedirs(files_dir)
        os.makedirs(solutions_dir)

        exe_ext = get_exe_extension()
        open(os.path.join(files_dir, f"gen{exe_ext}"), "w").close()
        open(os.path.join(solutions_dir, f"accepted{exe_ext}"), "w").close()

        result = await tool.execute(
            problem_dir=problem_dir,
            test_count=1,
            sol_name="accepted",
            enable_dedup=False,
            oversample_ratio=1.0,
        )

        assert result.success
        assert result.data["sol_name"] == "accepted"
        assert os.path.exists(os.path.join(problem_dir, "tests", "01.in"))
        assert os.path.exists(os.path.join(problem_dir, "tests", "01.ans"))


@pytest.mark.asyncio
async def test_problem_generate_tests_supports_custom_answer_ext(monkeypatch):
    """测试生成答案时支持自定义 answer_ext。"""
    tool = ProblemGenerateTestsTool()

    async def fake_run_binary_with_args(*args, **kwargs):
        return RunResult(success=True, stdout="7\n")

    async def fake_run_binary(binary_path, stdin="", timeout=5, memory_mb=256):
        return RunResult(success=True, stdout="7\n")

    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary_with_args", fake_run_binary_with_args)
    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary", fake_run_binary)

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "custom_ext")
        files_dir = os.path.join(problem_dir, "files")
        solutions_dir = os.path.join(problem_dir, "solutions")
        os.makedirs(files_dir)
        os.makedirs(solutions_dir)

        exe_ext = get_exe_extension()
        open(os.path.join(files_dir, f"gen{exe_ext}"), "w").close()
        open(os.path.join(solutions_dir, f"sol{exe_ext}"), "w").close()

        result = await tool.execute(
            problem_dir=problem_dir,
            test_count=1,
            answer_ext=".out",
            enable_dedup=False,
            oversample_ratio=1.0,
        )

        assert result.success
        assert result.data["answer_ext"] == ".out"
        assert os.path.exists(os.path.join(problem_dir, "tests", "01.in"))
        assert os.path.exists(os.path.join(problem_dir, "tests", "01.out"))


@pytest.mark.asyncio
async def test_problem_generate_tests_resume_without_state_falls_back_to_fresh(monkeypatch):
    """resume=true 且无状态文件时应回退 fresh run 并清理旧测试。"""
    tool = ProblemGenerateTestsTool()

    async def fake_run_binary_with_args(*args, **kwargs):
        return RunResult(success=True, stdout="9\n")

    async def fake_run_binary(binary_path, stdin="", timeout=5, memory_mb=256):
        return RunResult(success=True, stdout="9\n")

    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary_with_args", fake_run_binary_with_args)
    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary", fake_run_binary)

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "resume_fallback")
        files_dir = os.path.join(problem_dir, "files")
        solutions_dir = os.path.join(problem_dir, "solutions")
        tests_dir = os.path.join(problem_dir, "tests")
        os.makedirs(files_dir)
        os.makedirs(solutions_dir)
        os.makedirs(tests_dir)

        exe_ext = get_exe_extension()
        open(os.path.join(files_dir, f"gen{exe_ext}"), "w").close()
        open(os.path.join(solutions_dir, f"sol{exe_ext}"), "w").close()

        with open(os.path.join(tests_dir, "02.in"), "w", encoding="utf-8") as f:
            f.write("stale\n")
        with open(os.path.join(tests_dir, "02.ans"), "w", encoding="utf-8") as f:
            f.write("stale\n")

        result = await tool.execute(
            problem_dir=problem_dir,
            test_count=1,
            resume=True,
            enable_dedup=False,
            oversample_ratio=1.0,
        )

        assert result.success
        assert result.data["progress_snapshot"].get("resume_fallback") is True
        assert not os.path.exists(os.path.join(tests_dir, "02.in"))
        assert not os.path.exists(os.path.join(tests_dir, "02.ans"))


def test_problem_verify_tests_file_count_requires_contiguous_numeric_names():
    """测试 file_count 会检查数字文件名连续性。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ["01.in", "01.ans", "03.in", "03.ans"]:
            with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                f.write("x\n")

        result = tool._check_file_count(tmpdir, ".ans")

        assert not result["passed"]
        assert result["missing_indices"] == [2]


def test_problem_verify_tests_file_count_reports_large_gaps():
    """测试跳到大编号时会报告完整缺失区间。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ["01.in", "01.ans", "100.in", "100.ans"]:
            with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                f.write("x\n")

        result = tool._check_file_count(tmpdir, ".ans")

        assert not result["passed"]
        assert result["missing_indices"][0] == 2
        assert result["missing_indices"][-1] == 99
        assert len(result["missing_indices"]) == 98


def test_problem_verify_tests_limit_ratio_passes_with_manifest():
    """测试极限数据占比校验通过。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 4 组中 2 组为 type=3/4，满足 >=50%
        for i in range(1, 5):
            with open(os.path.join(tmpdir, f"{i:02d}.in"), "w", encoding="utf-8") as f:
                f.write("x\n")
            with open(os.path.join(tmpdir, f"{i:02d}.ans"), "w", encoding="utf-8") as f:
                f.write("y\n")

        manifest = {
            "version": 1,
            "limit_strategy_types": ["3", "4"],
            "tests": [
                {"in_file": "01.in", "ans_file": "01.ans", "type_param": "1", "signature": "a"},
                {"in_file": "02.in", "ans_file": "02.ans", "type_param": "2", "signature": "b"},
                {"in_file": "03.in", "ans_file": "03.ans", "type_param": "3", "signature": "c"},
                {"in_file": "04.in", "ans_file": "04.ans", "type_param": "4", "signature": "d"},
            ],
        }
        with open(
            os.path.join(tmpdir, ".autocode_tests_manifest.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(manifest, f)

        result = tool._check_limit_ratio(tmpdir)
        assert result["passed"] is True
        assert result["limit_case_count"] == 2
        assert result["limit_case_minimum_required"] == 2


def test_problem_verify_tests_limit_ratio_fails_when_insufficient():
    """测试极限数据占比不足时校验失败。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 5 组中只有 2 组 type=3/4，不满足 >=3
        for i in range(1, 6):
            with open(os.path.join(tmpdir, f"{i:02d}.in"), "w", encoding="utf-8") as f:
                f.write("x\n")
            with open(os.path.join(tmpdir, f"{i:02d}.ans"), "w", encoding="utf-8") as f:
                f.write("y\n")

        manifest = {
            "version": 1,
            "limit_strategy_types": ["3", "4"],
            "tests": [
                {"in_file": "01.in", "ans_file": "01.ans", "type_param": "1", "signature": "a"},
                {"in_file": "02.in", "ans_file": "02.ans", "type_param": "2", "signature": "b"},
                {"in_file": "03.in", "ans_file": "03.ans", "type_param": "2", "signature": "c"},
                {"in_file": "04.in", "ans_file": "04.ans", "type_param": "3", "signature": "d"},
                {"in_file": "05.in", "ans_file": "05.ans", "type_param": "4", "signature": "e"},
            ],
        }
        with open(
            os.path.join(tmpdir, ".autocode_tests_manifest.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(manifest, f)

        result = tool._check_limit_ratio(tmpdir)
        assert result["passed"] is False
        assert result["limit_case_count"] == 2
        assert result["limit_case_minimum_required"] == 3


def test_problem_verify_tests_limit_semantics_fails_for_overlapping_signatures():
    """测试 type3/type4 签名高度重叠时触发语义门禁。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "01.in"), "w", encoding="utf-8") as f:
            f.write("x\n")
        manifest = {
            "version": 1,
            "tests": [
                {"in_file": "01.in", "ans_file": "01.ans", "type_param": "3", "signature": "same"},
                {"in_file": "02.in", "ans_file": "02.ans", "type_param": "4", "signature": "same"},
            ],
        }
        with open(os.path.join(tmpdir, ".autocode_tests_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        result = tool._check_limit_semantics(tmpdir)
        assert result["passed"] is False


def test_problem_verify_tests_file_count_supports_multi_part_answer_ext():
    """多段后缀（如 .a.out）不应误报 orphan。"""
    tool = ProblemVerifyTestsTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ["01.in", "01.a.out"]:
            with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                f.write("x\n")
        result = tool._check_file_count(tmpdir, ".a.out")
        assert result["passed"] is True
        assert result["orphan_ans"] == []


@pytest.mark.asyncio
async def test_problem_verify_tests_supports_custom_answer_ext():
    """测试 verify 可读取自定义答案后缀。"""
    tool = ProblemVerifyTestsTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(tmpdir, "01.out"), "w", encoding="utf-8") as f:
            f.write("1\n")
        manifest = {
            "version": 1,
            "answer_ext": ".out",
            "tests": [{"in_file": "01.in", "ans_file": "01.out", "type_param": "3", "signature": "a"}],
        }
        with open(os.path.join(tmpdir, ".autocode_tests_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f)
        result = await tool.execute(
            problem_dir=tmpdir,
            tests_dir=tmpdir,
            verify_types=["file_count", "no_empty"],
            enable_limit_ratio=False,
        )
        assert result.success


@pytest.mark.asyncio
async def test_problem_cleanup_processes_does_not_global_kill_without_tracked_pids():
    """cleanup 不应在无 tracked PID 时按进程名全局误杀。"""
    tool = ProblemCleanupProcessesTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(problem_dir=tmpdir, kill_all_generators=True)
        assert result.success
        if os.name == "nt":
            assert "warning" in result.data
        else:
            assert result.data.get("message") == "Cleanup finished"


@pytest.mark.asyncio
async def test_problem_cleanup_processes_kills_tracked_pids(monkeypatch):
    """cleanup 应按状态文件里的 PID 精准清理。"""
    tool = ProblemCleanupProcessesTool()
    called_cmds: list[list[str]] = []

    class _FakeProc:
        def __init__(self):
            self.returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        called_cmds.append([str(a) for a in args])
        return _FakeProc()

    monkeypatch.setattr("autocode_mcp.tools.problem.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    with tempfile.TemporaryDirectory() as tmpdir:
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        with open(os.path.join(tests_dir, ".autocode_generate_state.json"), "w", encoding="utf-8") as f:
            json.dump({"active_pids": [12345, 23456]}, f)
        result = await tool.execute(problem_dir=tmpdir, kill_all_generators=True)
        assert result.success
        if os.name == "nt":
            assert result.data.get("killed_pids") == [12345, 23456]
            assert len(called_cmds) == 2
        else:
            assert result.data.get("removed_files") == []


@pytest.mark.asyncio
async def test_problem_cleanup_processes_keeps_failed_pid_for_retry(monkeypatch):
    """cleanup 失败 PID 应保留在状态文件中，支持后续重试。"""
    tool = ProblemCleanupProcessesTool()
    calls = {"count": 0}

    class _FakeProc:
        def __init__(self, rc):
            self.returncode = rc

        async def communicate(self):
            return b"", b"failed" if self.returncode != 0 else b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls["count"] += 1
        # 第一个 PID 成功，第二个失败
        return _FakeProc(0 if calls["count"] == 1 else 1)

    monkeypatch.setattr("autocode_mcp.tools.problem.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    with tempfile.TemporaryDirectory() as tmpdir:
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        state_path = os.path.join(tests_dir, ".autocode_generate_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"active_pids": [111, 222]}, f)

        result = await tool.execute(problem_dir=tmpdir, kill_all_generators=True)
        assert result.success
        if os.name == "nt":
            assert result.data.get("killed_pids") == [111]
            assert os.path.exists(state_path)
            with open(state_path, encoding="utf-8") as f:
                state = json.load(f)
            assert state.get("active_pids") == [222]
        else:
            assert result.data.get("removed_files") == []


@pytest.mark.asyncio
async def test_problem_cleanup_processes_preserves_checkpoint_fields(monkeypatch):
    """cleanup 更新 active_pids 时不应覆盖 checkpoint 其他字段。"""
    tool = ProblemCleanupProcessesTool()

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return b"ok", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr("autocode_mcp.tools.problem.asyncio.create_subprocess_exec", fake_create_subprocess_exec)

    with tempfile.TemporaryDirectory() as tmpdir:
        tests_dir = os.path.join(tmpdir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        state_path = os.path.join(tests_dir, ".autocode_generate_state.json")
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "phase": "partial",
                    "next_seed": 9,
                    "answer_ext": ".out",
                    "candidates": [{"signature": "x"}],
                    "errors": [{"seed": 1, "error": "e"}],
                    "active_pids": [555],
                },
                f,
            )

        result = await tool.execute(problem_dir=tmpdir, kill_all_generators=True)
        assert result.success
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        if os.name == "nt":
            assert state.get("active_pids") == []
        else:
            assert state.get("active_pids") == [555]
        assert state.get("phase") == "partial"
        assert state.get("next_seed") == 9
        assert state.get("answer_ext") == ".out"


@pytest.mark.asyncio
async def test_problem_generate_tests_run_with_retry_keeps_pid_on_cancel(monkeypatch):
    """取消时 _run_with_retry 应保留 active_pids 供后续 cleanup 使用。"""
    tool = ProblemGenerateTestsTool()

    async def fake_run_binary_with_args(binary_path, args, timeout=5, process_start_hook=None, **kwargs):
        if process_start_hook:
            process_start_hook(43210)
        raise asyncio.CancelledError()

    monkeypatch.setattr("autocode_mcp.tools.problem.run_binary_with_args", fake_run_binary_with_args)

    active_pids: set[int] = set()
    with pytest.raises(asyncio.CancelledError):
        await tool._run_with_retry("dummy", ["1"], timeout=1, active_pids=active_pids)
    assert 43210 in active_pids


@pytest.mark.asyncio
async def test_problem_verify_tests_default_enables_limit_ratio():
    """默认会启用 limit_ratio（即使 verify_types 未显式包含）。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ["01.in", "01.ans"]:
            with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                f.write("1\n")

        result = await tool.execute(
            problem_dir=tmpdir,
            tests_dir=tmpdir,
            verify_types=["file_count", "no_empty"],  # 不包含 limit_ratio
        )
        assert not result.success
        assert result.data.get("limit_ratio_enabled") is True
        assert "limit_ratio" in result.data.get("results", {})


@pytest.mark.asyncio
async def test_problem_verify_tests_can_disable_limit_ratio():
    """允许显式关闭 limit_ratio，默认其他检查正常执行。"""
    tool = ProblemVerifyTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        for name in ["01.in", "01.ans"]:
            with open(os.path.join(tmpdir, name), "w", encoding="utf-8") as f:
                f.write("1\n")

        result = await tool.execute(
            problem_dir=tmpdir,
            tests_dir=tmpdir,
            verify_types=["file_count", "no_empty"],
            enable_limit_ratio=False,
        )
        assert result.success
        assert result.data.get("limit_ratio_enabled") is False
        assert "limit_ratio" not in result.data.get("results", {})


@pytest.mark.asyncio
async def test_problem_pack_polygon_dynamic_test_count():
    """测试 Polygon 打包使用动态 test-count。"""
    create_tool = ProblemCreateTool()
    pack_tool = ProblemPackPolygonTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "dynamic_test_count_test")
        await create_tool.execute(problem_dir=problem_dir, problem_name="Dynamic Test")

        # 创建 tests 目录和 5 个测试文件
        tests_dir = os.path.join(problem_dir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        for i in range(1, 6):
            with open(os.path.join(tests_dir, f"{i:02d}.in"), "w") as f:
                f.write(f"test {i}\n")
            with open(os.path.join(tests_dir, f"{i:02d}.ans"), "w") as f:
                f.write(f"answer {i}\n")
        with open(os.path.join(problem_dir, "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        write_verified_workflow_state(problem_dir)

        await pack_tool.execute(problem_dir=problem_dir)

        xml_path = os.path.join(problem_dir, "problem.xml")
        with open(xml_path, encoding="utf-8") as f:
            content = f.read()
            assert "<test-count>5</test-count>" in content
            assert "<test-count>20</test-count>" not in content


@pytest.mark.asyncio
async def test_problem_pack_polygon_sanitizes_answer_ext_from_manifest():
    """manifest 中非法 answer_ext 不应污染 problem.xml。"""
    create_tool = ProblemCreateTool()
    pack_tool = ProblemPackPolygonTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_ext_sanitize")
        await create_tool.execute(problem_dir=problem_dir, problem_name="Pack & Test")
        tests_dir = os.path.join(problem_dir, "tests")
        os.makedirs(tests_dir, exist_ok=True)
        with open(os.path.join(tests_dir, "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(tests_dir, "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(tests_dir, ".autocode_tests_manifest.json"), "w", encoding="utf-8") as f:
            json.dump({"answer_ext": ".bad<ext>"}, f)
        with open(os.path.join(problem_dir, "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        write_verified_workflow_state(problem_dir)

        result = await pack_tool.execute(problem_dir=problem_dir)
        assert result.success
        xml_path = os.path.join(problem_dir, "problem.xml")
        with open(xml_path, encoding="utf-8") as f:
            content = f.read()
        assert "<answer-path-pattern>tests/%02d.ans</answer-path-pattern>" in content


@pytest.mark.asyncio
async def test_problem_pack_polygon_fails_without_answers():
    tool = ProblemPackPolygonTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_missing_answer")
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# T\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        result = await tool.execute(problem_dir=problem_dir)
        assert not result.success
        assert "missing answer files" in result.error


@pytest.mark.asyncio
async def test_problem_pack_polygon_fails_when_workflow_state_unverified():
    tool = ProblemPackPolygonTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_unverified")
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, ".autocode-workflow"), exist_ok=True)
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# T\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        with open(
            os.path.join(problem_dir, ".autocode-workflow", "state.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"tests_verified": False}, f)
        result = await tool.execute(problem_dir=problem_dir)
        assert not result.success
        assert "run problem_verify_tests first" in result.error


@pytest.mark.asyncio
async def test_problem_pack_polygon_fails_when_workflow_state_missing():
    tool = ProblemPackPolygonTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_state_missing")
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# T\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        result = await tool.execute(problem_dir=problem_dir)
        assert not result.success
        assert "workflow state missing" in result.error


@pytest.mark.asyncio
async def test_problem_pack_polygon_respects_require_tests_verified_override():
    tool = ProblemPackPolygonTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_override_verify_gate")
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, ".autocode-workflow"), exist_ok=True)
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# T\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        with open(os.path.join(problem_dir, "autocode.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "problem_name": "t",
                    "quality_gates": {
                        "require_tests_verified": False,
                        "require_limit_semantics": False,
                        "require_wrong_solution_kill": False,
                        "require_validator_check": False,
                    },
                },
                f,
            )
        with open(
            os.path.join(problem_dir, ".autocode-workflow", "state.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"tests_verified": False}, f)
        result = await tool.execute(problem_dir=problem_dir)
        assert result.success


@pytest.mark.asyncio
async def test_problem_pack_polygon_fails_when_required_verify_signal_missing():
    tool = ProblemPackPolygonTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_signal_missing")
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, ".autocode-workflow"), exist_ok=True)
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# T\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        with open(os.path.join(problem_dir, ".autocode-workflow", "state.json"), "w", encoding="utf-8") as f:
            json.dump({"tests_verified": True, "verify_signals": {}}, f)
        result = await tool.execute(problem_dir=problem_dir)
        assert not result.success
        assert "verification signal" in result.error


@pytest.mark.asyncio
async def test_problem_pack_polygon_enforces_min_limit_case_ratio():
    tool = ProblemPackPolygonTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "pack_limit_ratio_gate")
        os.makedirs(os.path.join(problem_dir, "tests"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "statements"), exist_ok=True)
        os.makedirs(os.path.join(problem_dir, "solutions"), exist_ok=True)
        with open(os.path.join(problem_dir, "tests", "01.in"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "01.ans"), "w", encoding="utf-8") as f:
            f.write("1\n")
        with open(os.path.join(problem_dir, "tests", "02.in"), "w", encoding="utf-8") as f:
            f.write("2\n")
        with open(os.path.join(problem_dir, "tests", "02.ans"), "w", encoding="utf-8") as f:
            f.write("2\n")
        with open(os.path.join(problem_dir, "tests", ".autocode_tests_manifest.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "tests": [
                        {"in_file": "01.in", "ans_file": "01.ans", "type_param": "1"},
                        {"in_file": "02.in", "ans_file": "02.ans", "type_param": "2"},
                    ]
                },
                f,
            )
        with open(os.path.join(problem_dir, "statements", "README.md"), "w", encoding="utf-8") as f:
            f.write("# T\n")
        with open(os.path.join(problem_dir, "solutions", "sol.cpp"), "w", encoding="utf-8") as f:
            f.write("// sol\n")
        write_verified_workflow_state(problem_dir)
        with open(
            os.path.join(problem_dir, "autocode.json"),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump({"problem_name": "t", "quality_gates": {"min_limit_case_ratio": 0.6}}, f)
        result = await tool.execute(problem_dir=problem_dir)
        assert not result.success
        assert "min_limit_case_ratio" in result.error


@pytest.mark.asyncio
async def test_problem_generate_tests_dedup():
    """测试去重功能。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    create_tool = ProblemCreateTool()
    gen_tool = GeneratorBuildTool()
    sol_tool = SolutionBuildTool()
    generate_tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "dedup_test")
        await create_tool.execute(problem_dir=problem_dir, problem_name="Dedup Test")

        # Generator 固定输出相同内容（用于测试去重）
        fixed_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    std::cout << "1 2" << std::endl;  // 固定输出
    return 0;
}
"""
        simple_sol = """
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
"""
        await gen_tool.execute(problem_dir=problem_dir, code=fixed_gen)
        await sol_tool.execute(problem_dir=problem_dir, solution_type="sol", code=simple_sol)

        # 启用去重，请求 10 个测试但 generator 只能产生 1 种输出
        result = await generate_tool.execute(
            problem_dir=problem_dir,
            test_count=10,
            enable_dedup=True,
            oversample_ratio=2.0,  # 尝试生成 20 个候选
        )

        # 由于去重，实际生成的测试应该少于请求的数量
        # 因为 generator 只能产生 1 种输出，最终只有 1 个测试
        # 这是一个 partial generation 失败
        assert not result.success
        assert "Partial generation" in result.error
        # 验证只生成了 1 个测试（去重生效）
        assert len(result.data.get("generated_tests", [])) == 1


def test_balance_and_sample_at_least_half_extreme_or_tle():
    """最终采样中 type 3/4 不少于一半（候选充足时）。"""
    tool = ProblemGenerateTestsTool()

    def mk(type_param: str, sig: str) -> CandidateTest:
        return CandidateTest(
            input_data=f"{type_param}-{sig}",
            output_data="o",
            type_param=type_param,
            signature=sig,
        )

    candidates = (
        [mk("1", f"a{i}") for i in range(10)]
        + [mk("2", f"b{i}") for i in range(10)]
        + [mk("3", f"c{i}") for i in range(10)]
        + [mk("4", f"d{i}") for i in range(10)]
    )

    out = tool._balance_and_sample(candidates, 10, balance_remainder=True)
    assert len(out) == 10
    assert sum(1 for x in out if x.type_param in ("3", "4")) >= 5

    out11 = tool._balance_and_sample(candidates, 11, balance_remainder=True)
    assert len(out11) == 11
    assert sum(1 for x in out11 if x.type_param in ("3", "4")) >= 6


def test_balance_and_sample_keeps_duplicates_when_dedup_disabled():
    """采样函数不应按 signature 强制去重（由 enable_dedup 控制前置候选）。"""
    tool = ProblemGenerateTestsTool()

    dup1 = CandidateTest("in-a", "out", "3", "same")
    dup2 = CandidateTest("in-b", "out", "3", "same")
    dup3 = CandidateTest("in-c", "out", "2", "same")
    dup4 = CandidateTest("in-d", "out", "1", "same")
    candidates = [dup1, dup2, dup3, dup4]

    out = tool._balance_and_sample(candidates, 4, balance_remainder=False)
    assert len(out) == 4
    assert out.count(dup1) == 1
    assert out.count(dup2) == 1


@pytest.mark.asyncio
async def test_problem_generate_tests_balance():
    """测试平衡分布功能。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    create_tool = ProblemCreateTool()
    gen_tool = GeneratorBuildTool()
    sol_tool = SolutionBuildTool()
    generate_tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "balance_test")
        await create_tool.execute(problem_dir=problem_dir, problem_name="Balance Test")

        # Generator 根据类型输出不同标记
        type_aware_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = atoi(argv[2]);
    std::cout << "type" << type << " " << rnd.next(1, 100) << std::endl;
    return 0;
}
"""
        simple_sol = """
#include <iostream>
int main() {
    std::string s;
    int n;
    std::cin >> s >> n;
    std::cout << s << " " << n << std::endl;
    return 0;
}
"""
        await gen_tool.execute(problem_dir=problem_dir, code=type_aware_gen)
        await sol_tool.execute(problem_dir=problem_dir, solution_type="sol", code=simple_sol)

        # 使用多种类型的配置
        custom_configs = [
            {"type": "1", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 1},  # tiny
            {"type": "2", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 1},  # random
            {"type": "3", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 1},  # extreme
            {"type": "4", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 1},  # tle
        ]

        result = await generate_tool.execute(
            problem_dir=problem_dir,
            test_count=8,
            test_configs=custom_configs,
            enable_balance=True,
            oversample_ratio=2.0,
        )

        assert result.success
        assert result.data.get("balance_enabled") is True
        assert result.data.get("limit_case_quota_met") is True
        assert result.data.get("limit_case_count", 0) >= 4  # 8 条中至少 4 条为 extreme/tle
        # 检查类型分布
        type_dist = result.data.get("type_distribution", {})
        # 应该有 4 种类型，每种 2 个
        assert len(type_dist) == 4


@pytest.mark.asyncio
async def test_problem_generate_tests_oversample():
    """测试超额采样功能。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    create_tool = ProblemCreateTool()
    gen_tool = GeneratorBuildTool()
    sol_tool = SolutionBuildTool()
    generate_tool = ProblemGenerateTestsTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, "oversample_test")
        await create_tool.execute(problem_dir=problem_dir, problem_name="Oversample Test")

        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    rnd.setSeed(seed);
    std::cout << rnd.next(1, 1000) << std::endl;
    return 0;
}
"""
        simple_sol = """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n << std::endl;
    return 0;
}
"""
        await gen_tool.execute(problem_dir=problem_dir, code=simple_gen)
        await sol_tool.execute(problem_dir=problem_dir, solution_type="sol", code=simple_sol)

        result = await generate_tool.execute(
            problem_dir=problem_dir,
            test_count=10,
            oversample_ratio=2.0,  # 生成 20 个候选
        )

        assert result.success
        # 检查候选数量
        candidates = result.data.get("candidates_generated", 0)
        assert candidates >= 10  # 至少生成了 10 个候选
        assert "from" in result.data.get("message", "")  # 消息中应该包含候选数量
