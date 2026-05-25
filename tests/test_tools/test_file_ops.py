"""
File 工具组测试。
"""

import os
import tempfile

import pytest

from autocode_mcp.tools.file_ops import FileReadTool, FileSaveTool


@pytest.mark.asyncio
async def test_file_save():
    """测试文件保存。"""
    tool = FileSaveTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            path="test.txt",
            content="Hello, World!",
            problem_dir=tmpdir,
        )

        assert result.success
        assert "path" in result.data
        assert os.path.exists(result.data["path"])

        # 验证内容
        with open(result.data["path"]) as f:
            assert f.read() == "Hello, World!"


@pytest.mark.asyncio
async def test_file_save_absolute_path():
    """测试绝对路径保存。"""
    tool = FileSaveTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        abs_path = os.path.join(tmpdir, "absolute.txt")
        result = await tool.execute(
            path=abs_path,
            content="Absolute path test",
        )

        assert result.success
        assert os.path.exists(abs_path)


@pytest.mark.asyncio
async def test_file_save_relative_path_requires_problem_dir():
    """测试相对路径保存必须指定题目目录。"""
    tool = FileSaveTool()

    result = await tool.execute(
        path="test.txt",
        content="Hello, World!",
    )

    assert not result.success
    assert "problem_dir" in result.error


def test_file_save_schema_allows_absolute_path_without_problem_dir():
    """schema 不应让绝对路径保存强制依赖 problem_dir。"""
    tool = FileSaveTool()

    assert "problem_dir" not in tool.input_schema["required"]


@pytest.mark.asyncio
async def test_file_save_canonicalizes_bare_problem_filenames():
    """测试裸文件名保存到 AutoCode 规范目录。"""
    tool = FileSaveTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            path="val.cpp",
            content="int main(){}",
            problem_dir=tmpdir,
        )

        expected_path = os.path.join(tmpdir, "files", "val.cpp")
        assert result.success
        assert result.data["path"] == expected_path
        assert os.path.exists(expected_path)
        assert not os.path.exists(os.path.join(tmpdir, "val.cpp"))


@pytest.mark.asyncio
async def test_file_read_canonicalizes_bare_problem_filenames():
    """测试裸文件名读取 AutoCode 规范目录。"""
    tool = FileReadTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(files_dir, exist_ok=True)
        with open(os.path.join(files_dir, "gen.cpp"), "w", encoding="utf-8") as f:
            f.write("generator")

        result = await tool.execute(
            path="gen.cpp",
            problem_dir=tmpdir,
        )

        assert result.success
        assert result.data["content"] == "generator"


@pytest.mark.asyncio
async def test_file_read_prefers_existing_literal_relative_file():
    """裸文件名已在题目根目录存在时不应被重定向到规范目录。"""
    tool = FileReadTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        statements_dir = os.path.join(tmpdir, "statements")
        os.makedirs(statements_dir)
        with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as f:
            f.write("root")
        with open(os.path.join(statements_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write("statement")

        result = await tool.execute(path="README.md", problem_dir=tmpdir)

        assert result.success
        assert result.data["content"] == "root"


@pytest.mark.asyncio
async def test_file_save_prefers_existing_literal_relative_file():
    """裸文件名已在题目根目录存在时保存仍写原文件。"""
    tool = FileSaveTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(files_dir)
        root_path = os.path.join(tmpdir, "val.cpp")
        canonical_path = os.path.join(files_dir, "val.cpp")
        with open(root_path, "w", encoding="utf-8") as f:
            f.write("old root")
        with open(canonical_path, "w", encoding="utf-8") as f:
            f.write("old canonical")

        result = await tool.execute(path="val.cpp", content="new root", problem_dir=tmpdir)

        assert result.success
        assert result.data["path"] == root_path
        with open(root_path, encoding="utf-8") as f:
            assert f.read() == "new root"
        with open(canonical_path, encoding="utf-8") as f:
            assert f.read() == "old canonical"


@pytest.mark.asyncio
async def test_file_read():
    """测试文件读取。"""
    tool = FileReadTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 先创建文件
        test_file = os.path.join(tmpdir, "read_test.txt")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("Test content")

        result = await tool.execute(
            path="read_test.txt",
            problem_dir=tmpdir,
        )

        assert result.success
        assert result.data["content"] == "Test content"


@pytest.mark.asyncio
async def test_file_read_not_found():
    """测试读取不存在的文件。"""
    tool = FileReadTool()

    result = await tool.execute(
        path="nonexistent.txt",
        problem_dir="/tmp",
    )

    assert not result.success
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_file_save_creates_directories():
    """测试保存文件时自动创建目录。"""
    tool = FileSaveTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            path="subdir/nested/deep.txt",
            content="Nested content",
            problem_dir=tmpdir,
        )

        assert result.success
        assert os.path.exists(result.data["path"])
