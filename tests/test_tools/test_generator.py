"""
Generator 工具组测试。
"""
import os
import tempfile

import pytest

from autocode_mcp.tools.generator import GeneratorBuildTool, GeneratorRunTool

# 简单的 Generator 代码（基于 testlib.h）
GENERATOR_CODE = '''
#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    int n_min = atoi(argv[3]);
    int n_max = atoi(argv[4]);
    int t_min = atoi(argv[5]);
    int t_max = atoi(argv[6]);

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
async def test_generator_build():
    """测试 Generator 构建。"""
    tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_generator_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_generator_run():
    """测试 Generator 运行。"""
    build_tool = GeneratorBuildTool()
    run_tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 先构建
        build_result = await build_tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )
        assert build_result.success

        # 运行
        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            strategies=["random"],
            test_count=5,
        )

        assert run_result.success
        assert run_result.data["generated_count"] == 5
        assert len(run_result.data["inputs"]) == 5


@pytest.mark.asyncio
async def test_generator_run_multiple_strategies():
    """测试多策略 Generator 运行。"""
    build_tool = GeneratorBuildTool()
    run_tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        await build_tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )

        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            strategies=["tiny", "random"],
            test_count=10,
        )

        assert run_result.success
        assert run_result.data["generated_count"] == 10


@pytest.mark.asyncio
async def test_generator_run_not_found():
    """测试运行不存在的 Generator。"""
    tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            strategies=["random"],
            test_count=5,
        )

        assert not result.success
        assert "not found" in result.error.lower()
