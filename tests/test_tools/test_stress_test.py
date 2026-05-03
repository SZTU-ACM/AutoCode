"""
Stress Test 工具组测试。
"""

import json
import os
import tempfile

import pytest

from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.stress_test import StressTestRunTool
from autocode_mcp.tools.validator import ValidatorBuildTool
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


@pytest.mark.asyncio
async def test_stress_test_validator_failure_includes_details():
    """validator 失败时应返回 exit code 与 stderr 详情。"""
    stress_tool = StressTestRunTool()
    build_tool = SolutionBuildTool()
    gen_tool = GeneratorBuildTool()
    val_tool = ValidatorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    std::cout << 1 << " " << 2 << std::endl;
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
        strict_fail_validator = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(10, 20, "a");
    inf.readSpace();
    inf.readInt(10, 20, "b");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""
        await gen_tool.execute(problem_dir=tmpdir, code=simple_gen)
        await build_tool.execute(problem_dir=tmpdir, solution_type="sol", code=simple_sol)
        await build_tool.execute(problem_dir=tmpdir, solution_type="brute", code=simple_sol)
        await val_tool.execute(problem_dir=tmpdir, code=strict_fail_validator)

        result = await stress_tool.execute(problem_dir=tmpdir, trials=1)
        assert not result.success
        assert "validator failed" in result.error.lower()
        detail = result.data.get("validator_failure_detail", {})
        assert isinstance(detail.get("validator_return_code"), int)
        assert detail.get("validator_return_code") != 0
        assert isinstance(detail.get("validator_stderr"), str)


@pytest.mark.asyncio
async def test_stress_test_returns_n_max_warning_for_large_n():
    """应返回基于复杂度证据的 n_max advisory（并兼容 warning 字段）。"""
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
        workflow_dir = os.path.join(tmpdir, ".autocode-workflow")
        os.makedirs(workflow_dir, exist_ok=True)
        with open(os.path.join(workflow_dir, "state.json"), "w", encoding="utf-8") as f:
            json.dump(
                {
                    "brute_complexity": "O(2^n)",
                    "recommended_stress_params": {"n_max": 8, "trials": 500},
                },
                f,
            )

        result = await tool.execute(problem_dir=tmpdir, trials=1, n_max=100)
        assert result.success
        assert "n_max_advisory" in result.data
        assert "Current n_max=100" in result.data["n_max_advisory"]
        assert "brute_complexity=O(2^n)" in result.data["n_max_advisory"]
        assert result.data["n_max_warning"] == result.data["n_max_advisory"]
        assert "complexity_context" in result.data
        assert result.data["complexity_context"]["brute_complexity"] == "O(2^n)"


@pytest.mark.asyncio
async def test_stress_test_strict_validator_line_endings_pass():
    """严格 readEoln/readSpace validator 在 stress 链路应通过。"""
    stress_tool = StressTestRunTool()
    build_tool = SolutionBuildTool()
    gen_tool = GeneratorBuildTool()
    val_tool = ValidatorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        simple_gen = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    rnd.setSeed(seed);
    int a = rnd.next(1, 20);
    int b = rnd.next(1, 20);
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
        strict_validator = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(1, 20, "a");
    inf.readSpace();
    inf.readInt(1, 20, "b");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""
        await gen_tool.execute(problem_dir=tmpdir, code=simple_gen)
        await build_tool.execute(problem_dir=tmpdir, solution_type="sol", code=simple_sol)
        await build_tool.execute(problem_dir=tmpdir, solution_type="brute", code=simple_sol)
        await val_tool.execute(problem_dir=tmpdir, code=strict_validator)

        result = await stress_tool.execute(problem_dir=tmpdir, trials=3, n_max=8)
        assert result.success
        assert result.data["completed_rounds"] == 3


@pytest.mark.asyncio
async def test_stress_rejects_invalid_autocode_manifest():
    """损坏的 autocode.json 应返回结构化失败而非未捕获异常。"""
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
        with open(os.path.join(tmpdir, "autocode.json"), "w", encoding="utf-8") as f:
            f.write(
                '{"schema_version":"1.0","problem_name":"x","interactive":false,'
                '"stress_comparison":"not-a-valid-mode"}'
            )

        result = await tool.execute(problem_dir=tmpdir, trials=2)
        assert not result.success
        err = (result.error or "").lower()
        assert "autocode" in err or "invalid" in err or "readable" in err


@pytest.mark.asyncio
async def test_stress_rejects_unreadable_autocode_manifest():
    """非法 UTF-8 的 autocode.json 应返回结构化失败。"""
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
        with open(os.path.join(tmpdir, "autocode.json"), "wb") as f:
            f.write(b"\xff\xfe\xfd")

        result = await tool.execute(problem_dir=tmpdir, trials=2)
        assert not result.success
        err = (result.error or "").lower()
        assert "autocode" in err or "invalid" in err or "readable" in err


def test_load_complexity_context_missing_state_returns_empty():
    tool = StressTestRunTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        assert tool._load_complexity_context(tmpdir) == {}


def test_load_complexity_context_invalid_json_returns_empty():
    tool = StressTestRunTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        wf = os.path.join(tmpdir, ".autocode-workflow")
        os.makedirs(wf, exist_ok=True)
        with open(os.path.join(wf, "state.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        assert tool._load_complexity_context(tmpdir) == {}


def test_load_complexity_context_non_object_returns_empty():
    tool = StressTestRunTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        wf = os.path.join(tmpdir, ".autocode-workflow")
        os.makedirs(wf, exist_ok=True)
        with open(os.path.join(wf, "state.json"), "w", encoding="utf-8") as f:
            f.write("[1,2]")
        assert tool._load_complexity_context(tmpdir) == {}
