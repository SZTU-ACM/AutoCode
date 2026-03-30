"""
Problem 工具组测试。
"""
import os
import tempfile
import pytest
from autocode_mcp.tools.problem import (
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
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

        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "README Test" in content


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
        os.makedirs(problem_dir)

        result = await tool.execute(
            problem_dir=problem_dir,
            time_limit=2000,
            memory_limit=536870912,
        )

        assert result.success

        xml_path = os.path.join(problem_dir, "problem.xml")
        assert os.path.exists(xml_path)

        with open(xml_path, "r") as f:
            content = f.read()
            assert "2000" in content  # time_limit
            assert "536870912" in content  # memory_limit
