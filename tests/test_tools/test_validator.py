"""
Validator 工具组测试。
"""
import os
import tempfile

import pytest

from autocode_mcp.tools.validator import ValidatorBuildTool, ValidatorSelectTool

# 简单的 Validator 代码（基于 testlib.h）
VALIDATOR_CODE = '''
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerValidation(argc, argv);

    int n = inf.readInt(1, 100000, "n");
    inf.readEoln();

    for (int i = 0; i < n; i++) {
        inf.readInt(1, 1000000000, "a_i");
        if (i < n - 1) inf.readSpace();
    }
    inf.readEoln();

    inf.readEof();
    return 0;
}
'''


@pytest.mark.asyncio
async def test_validator_build():
    """测试 Validator 构建。"""
    tool = ValidatorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=VALIDATOR_CODE,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_validator_build_with_tests():
    """测试 Validator 构建并运行测试用例。"""
    tool = ValidatorBuildTool()

    test_cases = [
        {"input": "5\n1 2 3 4 5\n", "expected_valid": True},
        {"input": "0\n", "expected_valid": False},  # n < 1
        {"input": "100001\n", "expected_valid": False},  # n > 100000
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=VALIDATOR_CODE,
            test_cases=test_cases,
        )

        assert result.success
        assert "score" in result.data
        assert "total" in result.data
        assert result.data["total"] == 3


@pytest.mark.asyncio
async def test_validator_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = ValidatorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_validator_select():
    """测试 Validator 选择。"""
    tool = ValidatorSelectTool()

    candidates = [
        {"id": "v1", "score": 30, "binary_path": "/path/v1"},
        {"id": "v2", "score": 38, "binary_path": "/path/v2"},
        {"id": "v3", "score": 35, "binary_path": "/path/v3"},
    ]

    result = await tool.execute(candidates=candidates)

    assert result.success
    assert result.data["best_candidate"]["id"] == "v2"
    assert result.data["best_candidate"]["score"] == 38


@pytest.mark.asyncio
async def test_validator_select_empty():
    """测试空候选列表。"""
    tool = ValidatorSelectTool()

    result = await tool.execute(candidates=[])

    assert not result.success
