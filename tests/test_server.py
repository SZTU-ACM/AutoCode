"""
AutoCode MCP Server 测试。
"""


def test_import():
    """测试模块导入。"""
    from autocode_mcp import __version__
    assert __version__ == "0.1.0"


def test_tool_result():
    """测试 ToolResult 数据类。"""
    from autocode_mcp.tools.base import ToolResult

    # 测试成功结果
    result = ToolResult.ok(message="test")
    assert result.success is True
    assert result.error is None
    assert result.data["message"] == "test"

    # 测试失败结果
    result = ToolResult.fail("error message")
    assert result.success is False
    assert result.error == "error message"

    # 测试 to_dict
    result = ToolResult.ok(value=123)
    d = result.to_dict()
    assert d["success"] is True
    assert d["data"]["value"] == 123


def test_tool_base():
    """测试 Tool 基类。"""
    from autocode_mcp.tools.base import Tool

    class TestTool(Tool):
        @property
        def name(self) -> str:
            return "test_tool"

        @property
        def description(self) -> str:
            return "A test tool"

        @property
        def input_schema(self) -> dict:
            return {
                "type": "object",
                "properties": {
                    "input": {"type": "string"},
                },
            }

        async def execute(self, **kwargs):
            from autocode_mcp.tools.base import ToolResult
            return ToolResult.ok(**kwargs)

    tool = TestTool()
    assert tool.name == "test_tool"
    assert tool.description == "A test tool"

    definition = tool.get_tool_definition()
    assert definition["name"] == "test_tool"
    assert "inputSchema" in definition


def test_all_tools_registered():
    """测试所有工具都能正确注册。"""
    from autocode_mcp.tools import (
        CheckerBuildTool,
        FileReadTool,
        FileSaveTool,
        GeneratorBuildTool,
        GeneratorRunTool,
        InteractorBuildTool,
        ProblemCreateTool,
        ProblemGenerateTestsTool,
        ProblemPackPolygonTool,
        SolutionBuildTool,
        SolutionRunTool,
        StressTestRunTool,
        ValidatorBuildTool,
        ValidatorSelectTool,
    )

    tools = [
        FileReadTool(),
        FileSaveTool(),
        SolutionBuildTool(),
        SolutionRunTool(),
        StressTestRunTool(),
        ProblemCreateTool(),
        ProblemGenerateTestsTool(),
        ProblemPackPolygonTool(),
        ValidatorBuildTool(),
        ValidatorSelectTool(),
        GeneratorBuildTool(),
        GeneratorRunTool(),
        CheckerBuildTool(),
        InteractorBuildTool(),
    ]

    # 验证每个工具都有必要的属性
    for tool in tools:
        assert hasattr(tool, "name")
        assert hasattr(tool, "description")
        assert hasattr(tool, "input_schema")
        assert callable(tool.execute)
