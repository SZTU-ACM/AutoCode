"""
Mixin 模块测试。
"""

import os
from pathlib import Path

import pytest

from autocode_mcp.tools.mixins import BuildToolMixin, RunToolMixin
from autocode_mcp.utils.compiler import CompileResult, RunResult


class MockBuildTool(BuildToolMixin):
    """用于测试 BuildToolMixin 的 Mock 类。"""

    pass


class MockRunTool(RunToolMixin):
    """用于测试 RunToolMixin 的 Mock 类。"""

    pass


class MockCombinedTool(BuildToolMixin, RunToolMixin):
    """同时拥有 build 和 run 能力的 Mock 类。"""

    pass


class TestBuildToolMixin:
    """BuildToolMixin 测试。"""

    @pytest.mark.asyncio
    async def test_build_compiles_cpp_code(self, tmp_path: Path):
        tool = MockBuildTool()

        source_path = tmp_path / "test.cpp"
        binary_path = tmp_path / "test.exe"

        source_path.write_text(
            '#include <iostream>\nint main() { std::cout << "hello"; return 0; }'
        )

        result = await tool.build(
            str(source_path),
            str(binary_path),
            compiler="g++",
            std="c++20",
            opt_level="O2",
            timeout=30,
        )

        assert isinstance(result, CompileResult)
        assert result.success
        assert result.binary_path == str(binary_path)
        assert os.path.exists(str(binary_path))

    @pytest.mark.asyncio
    async def test_build_returns_error_for_missing_source(self, tmp_path: Path):
        tool = MockBuildTool()

        source_path = tmp_path / "nonexistent.cpp"
        binary_path = tmp_path / "test.exe"

        result = await tool.build(str(source_path), str(binary_path))

        assert isinstance(result, CompileResult)
        assert not result.success
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_build_returns_error_for_invalid_code(self, tmp_path: Path):
        tool = MockBuildTool()

        source_path = tmp_path / "invalid.cpp"
        binary_path = tmp_path / "invalid.exe"

        source_path.write_text("this is not valid c++ code")

        result = await tool.build(str(source_path), str(binary_path))

        assert isinstance(result, CompileResult)
        assert not result.success
        assert result.error is not None


class TestRunToolMixin:
    """RunToolMixin 测试。"""

    @pytest.mark.asyncio
    async def test_run_uses_resource_limit_for_brute(self, tmp_path: Path, monkeypatch):
        tool = MockCombinedTool()

        source_path = tmp_path / "brute.cpp"
        binary_path = tmp_path / "brute.exe"

        source_path.write_text(
            "#include <iostream>\nint main() { int x; std::cin >> x; std::cout << x * 2; return 0; }"
        )

        result = await tool.build(str(source_path), str(binary_path))
        assert result.success

        captured_limit = None

        import autocode_mcp.tools.mixins as mixins_module

        original_run_binary = mixins_module.run_binary

        async def mock_run_binary(binary_path, input_data, timeout, memory_mb):
            nonlocal captured_limit
            captured_limit = (timeout, memory_mb)
            return await original_run_binary(binary_path, input_data, timeout, memory_mb)

        monkeypatch.setattr(mixins_module, "run_binary", mock_run_binary)

        result = await tool.run(
            str(binary_path),
            "5\n",
            str(tmp_path),
            "brute",
        )

        assert captured_limit is not None
        assert captured_limit[0] == 60

    @pytest.mark.asyncio
    async def test_run_uses_resource_limit_for_sol(self, tmp_path: Path, monkeypatch):
        tool = MockCombinedTool()

        source_path = tmp_path / "sol.cpp"
        binary_path = tmp_path / "sol.exe"

        source_path.write_text(
            "#include <iostream>\nint main() { int x; std::cin >> x; std::cout << x * 2; return 0; }"
        )

        result = await tool.build(str(source_path), str(binary_path))
        assert result.success

        captured_limit = None

        import autocode_mcp.tools.mixins as mixins_module

        original_run_binary = mixins_module.run_binary

        async def mock_run_binary(binary_path, input_data, timeout, memory_mb):
            nonlocal captured_limit
            captured_limit = (timeout, memory_mb)
            return await original_run_binary(binary_path, input_data, timeout, memory_mb)

        monkeypatch.setattr(mixins_module, "run_binary", mock_run_binary)

        result = await tool.run(
            str(binary_path),
            "5\n",
            str(tmp_path),
            "sol",
        )

        assert captured_limit is not None
        assert captured_limit[0] == 2

    @pytest.mark.asyncio
    async def test_run_accepts_custom_timeout_and_memory(self, tmp_path: Path, monkeypatch):
        tool = MockCombinedTool()

        source_path = tmp_path / "sol.cpp"
        binary_path = tmp_path / "sol.exe"

        source_path.write_text(
            "#include <iostream>\nint main() { int x; std::cin >> x; std::cout << x * 2; return 0; }"
        )

        result = await tool.build(str(source_path), str(binary_path))
        assert result.success

        captured_limit = None

        import autocode_mcp.tools.mixins as mixins_module

        original_run_binary = mixins_module.run_binary

        async def mock_run_binary(binary_path, input_data, timeout, memory_mb):
            nonlocal captured_limit
            captured_limit = (timeout, memory_mb)
            return await original_run_binary(binary_path, input_data, timeout, memory_mb)

        monkeypatch.setattr(mixins_module, "run_binary", mock_run_binary)

        result = await tool.run(
            str(binary_path),
            "5\n",
            str(tmp_path),
            "sol",
            timeout=10,
            memory_mb=512,
        )

        assert captured_limit is not None
        assert captured_limit[0] == 10
        assert captured_limit[1] == 512

    @pytest.mark.asyncio
    async def test_run_returns_run_result(self, tmp_path: Path):
        tool = MockCombinedTool()

        source_path = tmp_path / "test.cpp"
        binary_path = tmp_path / "test.exe"

        source_path.write_text(
            "#include <iostream>\nint main() { int x; std::cin >> x; std::cout << x * 2; return 0; }"
        )

        result = await tool.build(str(source_path), str(binary_path))
        assert result.success

        result = await tool.run(
            str(binary_path),
            "5\n",
            str(tmp_path),
            "sol",
        )

        assert isinstance(result, RunResult)
        assert result.success
        assert "10" in result.stdout
