"""
Stress Test 工具组测试。
"""

import os
import tempfile

import pytest

from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.stress_test import StressTestRunTool
from autocode_mcp.utils.platform import get_exe_extension

# 简单的 C++ 代码用于测试
SIMPLE_CPP = """
#include <iostream>
using namespace std;

int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
"""

BRUTE_CPP = """
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
"""

GENERATOR_CODE = """
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
"""


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


@pytest.mark.asyncio
async def test_stress_test_passes():
    """测试对拍通过的情况。"""
    tool = StressTestRunTool()
    build_tool = SolutionBuildTool()
    gen_tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    rnd.setSeed(seed);
    int a = rnd.next(1, 10);
    int b = rnd.next(1, 10);
    std::cout << a << " " << b << std::endl;
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
        await gen_tool.execute(problem_dir=tmpdir, code=simple_gen)
        await build_tool.execute(problem_dir=tmpdir, solution_type="sol", code=simple_sol)
        await build_tool.execute(problem_dir=tmpdir, solution_type="brute", code=simple_sol)

        result = await tool.execute(problem_dir=tmpdir, trials=5)

        assert result.success
        assert result.data["completed_rounds"] == 5


@pytest.mark.asyncio
async def test_stress_test_profiles_execute_all_trials():
    """stress_profiles 的每个 profile 都应按 trials 真正执行。"""
    tool = StressTestRunTool()
    build_tool = SolutionBuildTool()
    gen_tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    rnd.setSeed(seed);
    int a = rnd.next(1, 10);
    int b = rnd.next(1, 10);
    std::cout << a << " " << b << std::endl;
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
        await gen_tool.execute(problem_dir=tmpdir, code=simple_gen)
        await build_tool.execute(problem_dir=tmpdir, solution_type="sol", code=simple_sol)
        await build_tool.execute(problem_dir=tmpdir, solution_type="brute", code=simple_sol)

        result = await tool.execute(
            problem_dir=tmpdir,
            stress_profiles=[
                {"name": "p1", "trials": 2, "types": ["1"]},
                {"name": "p2", "trials": 3, "types": ["2"]},
            ],
        )

        assert result.success
        assert result.data["total_rounds"] == 5
        assert result.data["completed_rounds"] == 5
        assert len(result.data["stress_profiles"]) == 2
        assert result.data["stress_profiles"][0]["completed_rounds"] == 2
        assert result.data["stress_profiles"][1]["completed_rounds"] == 3


@pytest.mark.asyncio
async def test_generate_input_different_seeds():
    """验证不同 seed 生成不同的输入数据。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    tool = StressTestRunTool()
    gen_tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    rnd.setSeed(seed);
    int a = rnd.next(1, 1000);
    int b = rnd.next(1, 1000);
    std::cout << a << " " << b << std::endl;
    return 0;
}
"""
        build_result = await gen_tool.execute(problem_dir=tmpdir, code=simple_gen)
        assert build_result.success

        exe_ext = get_exe_extension()
        gen_exe = os.path.join(tmpdir, "files", f"gen{exe_ext}")  # 新目录结构
        input_path = os.path.join(tmpdir, "input.txt")

        # 用不同 seed 生成输入
        inputs = []
        for seed in [1, 2, 3, 4, 5]:
            result = await tool._generate_input(
                gen_exe, input_path, round_num=seed, seed=seed, timeout=5, n_max=100
            )
            assert result["success"], f"Generator failed with seed {seed}"
            with open(input_path, encoding="utf-8") as f:
                inputs.append(f.read())

        # 验证所有输入都不同
        assert len(set(inputs)) == len(inputs), "Different seeds should produce different inputs"
