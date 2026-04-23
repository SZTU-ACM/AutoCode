"""
MCP Server 入口。

提供 15 个原子工具，基于 AutoCode 论文框架。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    GetPromptResult,
    Prompt,
    PromptMessage,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
)

from . import prompts, resources
from .tools.base import Tool as BaseTool
from .tools.base import ToolResult
from .tools.checker import CheckerBuildTool
from .tools.complexity import SolutionAnalyzeTool
from .tools.file_ops import FileReadTool, FileSaveTool
from .tools.generator import GeneratorBuildTool, GeneratorRunTool
from .tools.interactor import InteractorBuildTool
from .tools.problem import ProblemCreateTool, ProblemGenerateTestsTool, ProblemPackPolygonTool
from .tools.solution import SolutionBuildTool, SolutionRunTool
from .tools.stress_test import StressTestRunTool
from .tools.validation import ProblemValidateTool
from .tools.validator import ValidatorBuildTool, ValidatorSelectTool

# 创建 MCP Server 实例
app = Server("autocode-mcp")

# 所有工具实例
TOOLS: dict[str, BaseTool] = {}


def register_tool(tool: BaseTool) -> None:
    """注册工具。"""
    TOOLS[tool.name] = tool


def register_all_tools() -> None:
    """注册所有工具。"""
    # File 工具组
    register_tool(FileReadTool())
    register_tool(FileSaveTool())

    # Solution 工具组
    register_tool(SolutionBuildTool())
    register_tool(SolutionRunTool())
    register_tool(SolutionAnalyzeTool())

    # Stress Test 工具组
    register_tool(StressTestRunTool())

    # Problem 工具组
    register_tool(ProblemCreateTool())
    register_tool(ProblemGenerateTestsTool())
    register_tool(ProblemPackPolygonTool())
    register_tool(ProblemValidateTool())

    # Validator 工具组
    register_tool(ValidatorBuildTool())
    register_tool(ValidatorSelectTool())

    # Generator 工具组
    register_tool(GeneratorBuildTool())
    register_tool(GeneratorRunTool())

    # Checker 工具组
    register_tool(CheckerBuildTool())

    # Interactor 工具组
    register_tool(InteractorBuildTool())


@app.list_tools()
async def list_tools() -> list[Tool]:
    """返回所有可用工具。"""
    return [
        Tool(
            name=tool.name,
            description=tool.description,
            inputSchema=tool.input_schema,
        )
        for tool in TOOLS.values()
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """执行工具调用。"""
    if name not in TOOLS:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True,
        )

    tool = TOOLS[name]
    try:
        result = await tool.execute(**arguments)
        result_dict = result.to_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result_dict, ensure_ascii=False))],
            structuredContent=result_dict,
            isError=not result.success,
        )
    except Exception as e:
        error_result = ToolResult.fail(str(e))
        error_dict = error_result.to_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(error_dict, ensure_ascii=False))],
            structuredContent=error_dict,
            isError=True,
        )


def main() -> None:
    """启动 MCP Server。"""
    register_all_tools()

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )

    asyncio.run(run())


@app.list_resources()
async def list_resources() -> list[Resource]:
    """返回所有可用资源。"""
    resource_list = []
    # 模板资源
    for template_name in resources.list_templates():
        resource_list.append(
            Resource(
                uri=f"template://{template_name}",
                name=template_name,
                description=f"Template file: {template_name}",
                mimeType="text/plain",
            )
        )
    return resource_list


@app.read_resource()
async def read_resource(uri: str) -> ReadResourceResult:
    """读取资源内容。"""
    if uri.startswith("template://"):
        template_name = uri[11:]
        path = resources.get_template_path(template_name)
        if path:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            return ReadResourceResult(
                contents=[
                    TextResourceContents(
                        uri=uri,
                        text=content,
                        mimeType="text/plain",
                    )
                ]
            )
        return ReadResourceResult(
            contents=[
                TextResourceContents(
                    uri=uri,
                    text=f"Template not found: {template_name}",
                    mimeType="text/plain",
                )
            ]
        )
    return ReadResourceResult(
        contents=[
            TextResourceContents(
                uri=uri,
                text=f"Unknown resource: {uri}",
                mimeType="text/plain",
            )
        ]
    )


@app.list_prompts()
async def list_prompts() -> list[Prompt]:
    """返回所有可用提示词。"""
    return [
        Prompt(
            name=name,
            description=f"Prompt template: {name}",
        )
        for name in prompts.list_prompts()
    ]


@app.get_prompt()
async def get_prompt(name: str, arguments: dict[str, str] | None = None) -> GetPromptResult:
    """获取提示词内容。"""
    content = prompts.get_prompt(name)
    if not content:
        return GetPromptResult(
            description="Error: Prompt not found",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=f"Prompt not found: {name}"),
                )
            ],
        )
    return GetPromptResult(
        description=f"Prompt template: {name}",
        messages=[
            PromptMessage(
                role="user",
                content=TextContent(type="text", text=content),
            )
        ],
    )


if __name__ == "__main__":
    main()
