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
