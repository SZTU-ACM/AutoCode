"""
Solution 工具组测试。
"""
import os
import tempfile

import pytest

from autocode_mcp.tools.solution import SolutionBuildTool, SolutionRunTool

# 简单的 C++ 代码用于测试
SIMPLE_CPP = '''
#include <iostream>
using namespace std;

int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
'''

BRUTE_CPP = '''
#include <iostream>
using namespace std;

int main() {
    int n;
    cin >> n;
    int sum = 0;
    for (int i = 1; i <= n; i++) {
        sum += i;
    }
    cout << sum << endl;
    return 0;
}
'''


@pytest.mark.asyncio
async def test_solution_build():
    """测试解法构建。"""
    tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            code=SIMPLE_CPP,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_solution_build_brute():
    """测试暴力解法构建。"""
    tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="brute",
            code=BRUTE_CPP,
        )

        assert result.success
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_solution_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_solution_run():
    """测试解法运行。"""
    build_tool = SolutionBuildTool()
    run_tool = SolutionRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 先构建
        build_result = await build_tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            code=SIMPLE_CPP,
        )
        assert build_result.success

        # 运行
        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            input_data="3 5\n",
        )

        assert run_result.success
        assert run_result.data["stdout"].strip() == "8"


@pytest.mark.asyncio
async def test_solution_run_not_found():
    """测试运行不存在的解法。"""
    tool = SolutionRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            input_data="test",
        )

        assert not result.success
        assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_solution_run_timeout():
    """测试运行超时。"""
    build_tool = SolutionBuildTool()
    run_tool = SolutionRunTool()

    # 无限循环代码
    infinite_loop = '''
#include <iostream>
using namespace std;
int main() {
    while(true) {}
    return 0;
}
'''

    with tempfile.TemporaryDirectory() as tmpdir:
        build_result = await build_tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            code=infinite_loop,
        )
        assert build_result.success

        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            input_data="",
            timeout=1,  # 1秒超时
        )

        assert not run_result.success
