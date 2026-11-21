"""
Interactor 工具组测试。
"""

import os
import tempfile

import pytest

from autocode_mcp.tools.interactor import InteractorBuildTool

# 简单的 Interactor 代码（基于 testlib.h registerInteraction 约定）
INTERACTOR_CODE = """
#include <iostream>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // 读取输入
    int n = inf.readInt();

    // 按 testlib registerInteraction 约定：通过 std::cout 发送给选手，并写入 tout 供记录
    std::cout << n << std::endl;
    std::cout.flush();
    tout << n << std::endl;

    // 读取选手输出
    int answer = ouf.readInt();

    // 验证答案
    if (answer == n * 2) {
        quitf(_ok, "Correct");
    } else {
        quitf(_wa, "Expected %d, got %d", n * 2, answer);
    }
}
"""


@pytest.mark.asyncio
async def test_interactor_build():
    """测试 Interactor 构建。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_interactor_build_uses_existing_default_source():
    """interactor_build 缺省 code/source_path 时读取 files/interactor.cpp。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(files_dir)
        with open(os.path.join(files_dir, "interactor.cpp"), "w", encoding="utf-8") as f:
            f.write(INTERACTOR_CODE)

        result = await tool.execute(problem_dir=tmpdir)

        assert result.success
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_interactor_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_interactor_build_no_validation():
    """测试不带验证的 Interactor 构建。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
        )

        assert result.success
        # 没有提供参考解和变异解，应该直接返回成功
        assert "binary_path" in result.data


@pytest.mark.asyncio
async def test_interactor_build_with_scripted_scenarios():
    """测试 testlib registerInteraction 的脚本化场景验证。"""
    tool = InteractorBuildTool()

    scenarios = [
        {
            "input": "5\n",
            "contestant_output": "10\n",
            "answer": "",
            "expected_verdict": "AC",
        },
        {
            "input": "5\n",
            "contestant_output": "15\n",
            "answer": "",
            "expected_verdict": "WA",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            interaction_scenarios=scenarios,
        )

        assert result.success
        report = result.data["interaction_scenarios"]
        assert report["validated"] is True
        assert report["total"] == 2
        assert report["accuracy"] == 1.0
        assert report["details"][0]["interactor_output"] == "5\n"


@pytest.mark.asyncio
async def test_interactor_build_fails_when_scripted_scenario_mismatches():
    """脚本化场景不匹配时不应把 interactor_build 视为成功。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            interaction_scenarios=[
                {
                    "input": "5\n",
                    "contestant_output": "15\n",
                    "expected_verdict": "AC",
                }
            ],
        )

        assert not result.success
        assert result.data["interaction_scenarios"]["accuracy"] == 0.0


SIMPLE_SOLUTION = """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n * 2 << std::endl;
    return 0;
}
"""

WRONG_SOLUTION = """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n * 3 << std::endl;
    return 0;
}
"""


@pytest.mark.asyncio
async def test_interactor_with_reference_solution():
    """测试 Interactor 与参考解法的交互验证。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.platform import get_exe_extension

    tool = InteractorBuildTool()
    sol_tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 构建 interactor
        build_result = await tool.execute(problem_dir=tmpdir, code=INTERACTOR_CODE)
        assert build_result.success

        from autocode_mcp.utils.compiler import compile_cpp

        # 构建正确的参考解法与错误变异解法
        sol_ok = await sol_tool.execute(problem_dir=tmpdir, solution_type="sol", code=SIMPLE_SOLUTION)
        assert sol_ok.success
        correct_sol_exe = os.path.join(tmpdir, "solutions", f"sol{get_exe_extension()}")
        wrong_src = os.path.join(tmpdir, "solutions", "wrong_triple.cpp")
        wrong_sol_exe = os.path.join(tmpdir, "solutions", f"wrong_triple{get_exe_extension()}")
        with open(wrong_src, "w", encoding="utf-8") as wf:
            wf.write(WRONG_SOLUTION)
        wrong_compile = await compile_cpp(wrong_src, wrong_sol_exe)
        assert wrong_compile.success, wrong_compile.stderr

        # 走真实双向管道验证正确解与错误解
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            reference_solution_path=correct_sol_exe,
            mutant_solutions=[wrong_sol_exe],
            test_inputs=["5\n", "12\n"],
        )

        assert result.success
        assert result.data["pass_rate"] == 1.0
        assert result.data["pass_count"] == 2
        assert result.data["pass_total"] == 2
        assert result.data["fail_rate"] == 1.0
        assert result.data["fail_count"] == 1
        assert result.data["fail_total"] == 1
        assert result.data["reference_results"][0]["verdict"] == "AC"
        assert "[judge] 5" in result.data["reference_results"][0]["transcript"]
        assert "[contestant] 10" in result.data["reference_results"][0]["transcript"]
        assert result.data["mutant_results"][0]["killed"] is True
        assert result.data["mutant_results"][0]["verdict"] == "WA"


@pytest.mark.asyncio
async def test_interactor_judge_fail_not_counted_as_mutant_kill():
    """Interactor exiting with FAIL (_fail) must NOT count as killing a mutant."""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.platform import get_exe_extension

    failing_interactor = """
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    quitf(_fail, "internal interactor failure");
}
"""
    tool = InteractorBuildTool()
    sol_tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        sol_ok = await sol_tool.execute(problem_dir=tmpdir, solution_type="sol", code=WRONG_SOLUTION)
        assert sol_ok.success
        mutant_exe = os.path.join(tmpdir, "solutions", f"sol{get_exe_extension()}")

        res = await tool.execute(
            problem_dir=tmpdir,
            code=failing_interactor,
            mutant_solutions=[mutant_exe],
            test_inputs=["5\n"],
        )
        assert res.success
        assert res.data["fail_rate"] == 0.0
        assert res.data["mutant_results"][0]["killed"] is False
        assert res.data["mutant_results"][0]["verdict"] == "FAIL"
        assert res.data["mutant_results"][0]["judge_failed"] is True


@pytest.mark.asyncio
async def test_interactor_build_persists_scenarios_for_verify_tests():
    """interactor_build 传入并验证通过的 interaction_scenarios 应持久化到 .autocode/ 并被自检复用。"""
    from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool

    tool = InteractorBuildTool()
    verifier = ProblemVerifyTestsTool()
    scenarios = [
        {"input": "5\n", "contestant_output": "10\n", "expected_verdict": "AC", "category": "ac"},
        {"input": "5\n", "contestant_output": "9\n", "expected_verdict": "WA", "category": "wrong_answer"},
        {"input": "5\n", "contestant_output": "abc\n", "expected_verdict": "PE", "category": "malformed_command"},
        {"input": "5\n", "contestant_output": "999\n", "expected_verdict": "WA", "category": "out_of_range"},
        {"input": "5\n", "contestant_output": "1\n", "expected_verdict": "WA", "category": "query_limit"},
        {"input": "5\n", "contestant_output": "", "expected_verdict": "PE", "category": "premature_eof"},
        {"input": "5\n", "contestant_output": "bad", "expected_verdict": "PE", "category": "extra_output"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        res = await tool.execute(problem_dir=tmpdir, code=INTERACTOR_CODE, interaction_scenarios=scenarios)
        assert res.success, res.error
        assert os.path.isfile(os.path.join(tmpdir, ".autocode", "interactor_scenarios.json"))
        self_test = await verifier._check_interactor_self_test(tmpdir, 10)
        assert self_test["passed"] is True
        assert self_test["total"] == 7
