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
        with open(result.data["path"], "r") as f:
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
async def test_file_read():
    """测试文件读取。"""
    tool = FileReadTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 先创建文件
        test_file = os.path.join(tmpdir, "read_test.txt")
        with open(test_file, "w") as f:
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
