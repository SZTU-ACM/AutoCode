"""
MCP Server 入口。

提供 AutoCode 原子工具，基于 AutoCode 论文框架。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)

from . import __version__
from .tools.audit import ProblemAuditTool
from .tools.base import Tool as BaseTool
from .tools.base import ToolResult
from .tools.build_all import ProblemBuildAllTool
from .tools.checker import CheckerBuildTool
from .tools.complexity import SolutionAnalyzeTool
from .tools.file_ops import FileReadTool, FileSaveTool
from .tools.generator import GeneratorBuildTool, GeneratorRunTool
from .tools.interactor import InteractorBuildTool
from .tools.problem import (
    ProblemCleanupProcessesTool,
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
)
from .tools.solution import SolutionBuildTool, SolutionRunTool
from .tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from .tools.stress_test import StressTestRunTool
from .tools.test_verify import ProblemVerifyTestsTool
from .tools.validation import ProblemValidateTool
from .tools.validator import ValidatorBuildTool, ValidatorSelectTool
from .utils.logs import safe_spill_json
from .workflow.enforcement import (
    apply_result,
    blocked_result,
    has_workflow_context,
    preflight,
    prepare_call,
)

# 创建 MCP Server 实例
app = Server("autocode-mcp", version=__version__)

# 所有工具实例
TOOLS: dict[str, BaseTool] = {}

_MAX_MCP_RESPONSE_BYTES = 128 * 1024


def _compact_value(value: Any, *, max_str: int = 1024, max_list: int = 25, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) > max_str:
            return f"{value[:max_str]}... [truncated {len(value) - max_str} chars]"
        return value
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, list):
        compacted = [_compact_value(item, max_str=max_str, max_list=max_list, depth=depth + 1) for item in value[:max_list]]
        if len(value) > max_list:
            compacted.append(f"... [{len(value) - max_list} more items truncated]")
        return compacted
    if isinstance(value, dict):
        return {
            k: _compact_value(v, max_str=max_str, max_list=max_list, depth=depth + 1)
            for k, v in value.items()
        }
    return value


def _cap_mcp_payload(
    tool_name: str,
    problem_dir: Any,
    payload: dict[str, Any],
    *,
    allow_workspace_spill: bool = True,
) -> tuple[dict[str, Any], str]:
    """Ensure MCP tool JSON responses stay under 128 KB, spilling oversized payloads safely."""
    raw_text = json.dumps(payload, ensure_ascii=False)
    if tool_name == "file_read":
        return payload, raw_text
    raw_bytes = len(raw_text.encode("utf-8"))
    if raw_bytes <= _MAX_MCP_RESPONSE_BYTES:
        return payload, raw_text

    pdir_str = problem_dir if isinstance(problem_dir, str) else None
    full_path = safe_spill_json(
        pdir_str,
        f"tool_{tool_name}",
        payload,
        allow_workspace=allow_workspace_spill,
        raw_json=raw_text,
    )
    compacted_payload = dict(payload)
    data = compacted_payload.get("data")
    if isinstance(data, dict):
        compacted_data = _compact_value(data)
        compacted_data["truncated"] = True
        compacted_data["original_bytes"] = raw_bytes
        if full_path:
            compacted_data["full_output_path"] = full_path
        compacted_payload["data"] = compacted_data
    else:
        compacted_payload["data"] = {
            "truncated": True,
            "original_bytes": raw_bytes,
            "full_output_path": full_path,
        }
    compacted_text = json.dumps(compacted_payload, ensure_ascii=False)
    if len(compacted_text.encode("utf-8")) <= _MAX_MCP_RESPONSE_BYTES:
        return compacted_payload, compacted_text

    minimal_payload = {
        "success": payload.get("success", False),
        "error": _compact_value(payload.get("error"), max_str=1024),
        "data": {
            "truncated": True,
            "original_bytes": raw_bytes,
            "full_output_path": full_path,
            "summary_keys": sorted(data.keys())[:200] if isinstance(data, dict) else [],
        },
    }
    return minimal_payload, json.dumps(minimal_payload, ensure_ascii=False)


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
    register_tool(SolutionAuditStdTool())
    register_tool(SolutionAuditBruteTool())

    # Stress Test 工具组
    register_tool(StressTestRunTool())

    # Problem 工具组
    register_tool(ProblemCreateTool())
    register_tool(ProblemGenerateTestsTool())
    register_tool(ProblemCleanupProcessesTool())
    register_tool(ProblemVerifyTestsTool())
    register_tool(ProblemAuditTool())
    register_tool(ProblemPackPolygonTool())
    register_tool(ProblemValidateTool())
    register_tool(ProblemBuildAllTool())

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
    raw_problem_dir = arguments.get("problem_dir")
    problem_dir: str | None = (
        raw_problem_dir if isinstance(raw_problem_dir, str) and raw_problem_dir.strip() else None
    )
    is_standalone_audit = name == "problem_audit" and bool(arguments.get("standalone"))
    enforce_workflow = (
        not is_standalone_audit
        and problem_dir is not None
        and (name == "problem_create" or has_workflow_context(problem_dir))
    )
    if enforce_workflow and problem_dir is not None:
        violations = preflight(name, problem_dir, arguments)
        if violations:
            blocked = ToolResult.fail(
                "Workflow gate blocked this tool call",
                **blocked_result(name, violations),
            )
            blocked_dict = blocked.to_dict()
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(blocked_dict, ensure_ascii=False))],
                structuredContent=blocked_dict,
                isError=True,
            )
        prepare_call(name, problem_dir)
    try:
        result = await tool.execute(**arguments)
        if enforce_workflow and problem_dir is not None:
            apply_result(problem_dir, name, arguments, result.success, result.data)
        result_dict = result.to_dict()
        capped_dict, capped_text = _cap_mcp_payload(
            name,
            problem_dir,
            result_dict,
            allow_workspace_spill=not is_standalone_audit,
        )
        return CallToolResult(
            content=[TextContent(type="text", text=capped_text)],
            structuredContent=capped_dict,
            isError=not result.success,
        )
    except asyncio.CancelledError:
        if enforce_workflow and problem_dir is not None:
            apply_result(problem_dir, name, arguments, False, {"interrupted": True})
        cancel_result = ToolResult.fail(
            "Tool call interrupted by cancellation",
            interrupted=True,
            resume_hint="Retry with resume=true if tool supports checkpoints",
        )
        cancel_dict = cancel_result.to_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(cancel_dict, ensure_ascii=False))],
            structuredContent=cancel_dict,
            isError=True,
        )
    except Exception as e:
        if enforce_workflow and problem_dir is not None:
            apply_result(problem_dir, name, arguments, False, {"exception": type(e).__name__})
        import traceback

        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        error_result = ToolResult.fail(
            f"{type(e).__name__}: {e}",
            exc_type=type(e).__name__,
            traceback=tb,
        )
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


if __name__ == "__main__":
    main()
