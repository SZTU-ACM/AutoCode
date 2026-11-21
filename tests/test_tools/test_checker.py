"""
Checker 工具组测试。
"""
import os
import tempfile

import pytest

from autocode_mcp.tools.checker import CheckerBuildTool

# 简单的 Checker 代码（基于 testlib.h）
CHECKER_CODE = '''
#include "testlib.h"
#include <cmath>

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);

    int n = inf.readInt();
    long long jury = ans.readLong();
    long long contestant = ouf.readLong();

    if (jury == contestant) {
        quitf(_ok, "Correct");
    } else {
        quitf(_wa, "Expected %lld, got %lld", jury, contestant);
    }
}
'''


@pytest.mark.asyncio
async def test_checker_build():
    """测试 Checker 构建。"""
    tool = CheckerBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=CHECKER_CODE,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_checker_build_uses_existing_default_source():
    """checker_build 缺省 code/source_path 时读取 files/checker.cpp。"""
    tool = CheckerBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(files_dir)
        with open(os.path.join(files_dir, "checker.cpp"), "w", encoding="utf-8") as f:
            f.write(CHECKER_CODE)

        result = await tool.execute(problem_dir=tmpdir)

        assert result.success
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_checker_build_with_scenarios():
    """测试 Checker 构建并运行测试场景。"""
    tool = CheckerBuildTool()

    test_scenarios = [
        {
            "input": "5\n",
            "contestant_output": "15\n",
            "reference_output": "15\n",
            "expected_verdict": "AC",
        },
        {
            "input": "5\n",
            "contestant_output": "10\n",
            "reference_output": "15\n",
            "expected_verdict": "WA",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=CHECKER_CODE,
            test_scenarios=test_scenarios,
        )

        assert result.success
        assert "accuracy" in result.data
        assert result.data["total"] == 2


@pytest.mark.asyncio
async def test_checker_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = CheckerBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_checker_build_persists_scenarios_for_verify_tests():
    """checker_build 传入的 test_scenarios 应持久化到 .autocode/checker_scenarios.json 并被自检读取。"""
    from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool

    tool = CheckerBuildTool()
    verifier = ProblemVerifyTestsTool()
    scenarios = [
        {"input": "1\n", "contestant_output": "5\n", "reference_output": "5\n", "expected_verdict": "AC", "category": "ac"},
        {"input": "1\n", "contestant_output": "4\n", "reference_output": "5\n", "expected_verdict": "WA", "category": "wrong_answer"},
        {"input": "1\n", "contestant_output": "abc\n", "reference_output": "5\n", "expected_verdict": "PE", "category": "format_error"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        res = await tool.execute(problem_dir=tmpdir, code=CHECKER_CODE, test_scenarios=scenarios)
        assert res.success, res.error
        assert os.path.isfile(os.path.join(tmpdir, ".autocode", "checker_scenarios.json"))
        self_test = await verifier._check_checker_self_test(tmpdir, 10)
        assert self_test["passed"] is True
        assert self_test["total"] == 3
