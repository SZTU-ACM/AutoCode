"""
Stress Test 工具组测试。
"""
import tempfile

import pytest

from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.stress_test import StressTestRunTool

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

GENERATOR_CODE = '''
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

    for (int i = 0; i < n; i++) {
        if (i > 0) std::cout << " ";
        std::cout << rnd.next(1, 1000000000);
    }
    std::cout << std::endl;

    return 0;
}
'''


@pytest.mark.asyncio
async def test_stress_test_run_not_found():
    """测试运行不存在的文件。"""
    tool = StressTestRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            trials=10,
        )

        assert not result.success
        assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_stress_test_missing_brute():
    """测试缺少 brute 解法。"""
    tool = StressTestRunTool()
    build_tool = SolutionBuildTool()
    gen_tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 只构建 sol 和 gen
        await build_tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            code=SIMPLE_CPP,
        )
        await gen_tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )

        result = await tool.execute(
            problem_dir=tmpdir,
            trials=10,
        )

        assert not result.success
        assert "brute" in result.error.lower()


@pytest.mark.asyncio
async def test_stress_test_missing_generator():
    """测试缺少生成器。"""
    tool = StressTestRunTool()
    build_tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 只构建 sol 和 brute
        await build_tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            code=SIMPLE_CPP,
        )
        await build_tool.execute(
            problem_dir=tmpdir,
            solution_type="brute",
            code=BRUTE_CPP,
        )

        result = await tool.execute(
            problem_dir=tmpdir,
            trials=10,
        )

        assert not result.success
        assert "generator" in result.error.lower()
