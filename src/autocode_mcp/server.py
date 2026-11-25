"""
MCP Server 入口。

提供 AutoCode 原子工具，基于 AutoCode 论文框架。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import McpError
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    CallToolResult,
    CancelTaskRequest,
    CancelTaskResult,
    CreateTaskResult,
    ErrorData,
    GetTaskPayloadRequest,
    GetTaskPayloadResult,
    GetTaskRequest,
    GetTaskResult,
    ListTasksRequest,
    ListTasksResult,
    Resource,
    Task,
    TextContent,
    Tool,
)
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

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
from .tools.schemas import (
    TASK_SUPPORTED_TOOL_NAMES,
    get_tool_annotations,
    get_tool_execution,
    get_tool_meta,
    get_tool_output_schema,
)
from .tools.solution import SolutionBuildTool, SolutionRunTool
from .tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from .tools.stress_test import StressTestRunTool
from .tools.test_verify import ProblemVerifyTestsTool
from .tools.validation import ProblemValidateTool
from .tools.validator import ValidatorBuildTool, ValidatorSelectTool
from .ui import list_ui_resources, read_ui_resource
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
_TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})
_RELATED_TASK_META_KEY = "io.modelcontextprotocol/related-task"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def _compact_value(value: Any, *, max_str: int = 1024, max_list: int = 25, depth: int = 0) -> Any:
    if isinstance(value, str):
        if len(value) > max_str:
            return f"{value[:max_str]}... [truncated {len(value) - max_str} chars]"
        return value
    if depth >= 6:
        return "[truncated]"
    if isinstance(value, list):
        compacted = [
            _compact_value(item, max_str=max_str, max_list=max_list, depth=depth + 1)
            for item in value[:max_list]
        ]
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


@dataclass
class _ManagedTaskRecord:
    """Internal mutable record backing an active or terminal MCP task."""

    task: Task
    tool_name: str
    arguments: dict[str, Any]
    done_event: asyncio.Event
    asyncio_task: asyncio.Task[None] | None = None
    result: CallToolResult | None = None
    expires_at: datetime | None = None
    progress: float | None = None


def _extract_error_summary(result: CallToolResult) -> str:
    """Extract a concise human-readable error message from a failed CallToolResult."""
    if isinstance(result.structuredContent, dict):
        err = result.structuredContent.get("error")
        if isinstance(err, str) and err.strip():
            return err[:240]
    for block in result.content:
        if isinstance(block, TextContent) and block.text.strip():
            return block.text[:240]
    return "Tool execution failed"


class McpTaskManager:
    """Bounded in-memory lifecycle manager for asynchronous MCP Tasks."""

    def __init__(
        self,
        *,
        max_tasks: int = 128,
        default_ttl_ms: int = 3_600_000,
        poll_interval_ms: int = 500,
        supported_tools: frozenset[str] = TASK_SUPPORTED_TOOL_NAMES,
    ) -> None:
        if max_tasks < 1:
            raise ValueError("max_tasks must be >= 1")
        if default_ttl_ms < 1:
            raise ValueError("default_ttl_ms must be >= 1")
        if poll_interval_ms < 1:
            raise ValueError("poll_interval_ms must be >= 1")
        self._max_tasks = max_tasks
        self._default_ttl_ms = default_ttl_ms
        self._poll_interval_ms = poll_interval_ms
        self._supported_tools = supported_tools
        self._tasks: dict[str, _ManagedTaskRecord] = {}

    @property
    def max_tasks(self) -> int:
        return self._max_tasks

    @property
    def supported_tools(self) -> frozenset[str]:
        return self._supported_tools

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _compute_expiry(now: datetime, ttl_ms: int | None) -> datetime | None:
        if ttl_ms is None:
            return None
        return now + timedelta(milliseconds=ttl_ms)

    def _purge_expired(self) -> None:
        now = self._utcnow()
        expired_ids = [
            tid
            for tid, rec in self._tasks.items()
            if rec.expires_at is not None and now >= rec.expires_at and rec.task.status in _TERMINAL_TASK_STATUSES
        ]
        for tid in expired_ids:
            self._tasks.pop(tid, None)

    def _evict_if_needed(self) -> None:
        self._purge_expired()
        if len(self._tasks) < self._max_tasks:
            return
        terminal_ids = [
            tid for tid, rec in self._tasks.items() if rec.task.status in _TERMINAL_TASK_STATUSES
        ]
        for tid in terminal_ids:
            self._tasks.pop(tid, None)
            if len(self._tasks) < self._max_tasks:
                return
        if len(self._tasks) >= self._max_tasks:
            raise McpError(
                ErrorData(
                    code=INTERNAL_ERROR,
                    message=f"Max active MCP task capacity ({self._max_tasks}) reached",
                )
            )

    def _require_record(self, task_id: str) -> _ManagedTaskRecord:
        self._purge_expired()
        rec = self._tasks.get(task_id)
        if rec is None:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Task not found: {task_id}",
                )
            )
        return rec

    async def create_task(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        ttl_ms: int | None = None,
        task_id: str | None = None,
        runner: Callable[[], Awaitable[CallToolResult]] | None = None,
    ) -> CreateTaskResult:
        """Create and launch a bounded background MCP Task for an allowed long-running tool."""
        if tool_name not in self._supported_tools:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Tool '{tool_name}' does not support asynchronous MCP Tasks",
                )
            )
        if ttl_ms is not None and ttl_ms < 0:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message="Task ttl must be a non-negative integer",
                )
            )

        self._evict_if_needed()
        tid = task_id or uuid4().hex
        if tid in self._tasks:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Duplicate task ID: {tid}",
                )
            )

        now = self._utcnow()
        effective_ttl = self._default_ttl_ms if ttl_ms is None else ttl_ms
        task_model = Task(
            taskId=tid,
            status="working",
            statusMessage=f"Running {tool_name}",
            createdAt=now,
            lastUpdatedAt=now,
            ttl=effective_ttl,
            pollInterval=self._poll_interval_ms,
        )
        args_copy = dict(arguments or {})
        rec = _ManagedTaskRecord(
            task=task_model,
            tool_name=tool_name,
            arguments=args_copy,
            done_event=asyncio.Event(),
            expires_at=self._compute_expiry(now, effective_ttl),
        )
        self._tasks[tid] = rec

        async def _execute_background() -> None:
            try:
                call_res = (
                    await runner()
                    if runner is not None
                    else await call_tool(tool_name, args_copy)
                )
                done_now = self._utcnow()
                if rec.task.status in _TERMINAL_TASK_STATUSES:
                    return
                rec.result = call_res
                rec.task.lastUpdatedAt = done_now
                rec.expires_at = self._compute_expiry(done_now, rec.task.ttl)
                if call_res.isError:
                    rec.task.status = "failed"
                    rec.task.statusMessage = _extract_error_summary(call_res)
                else:
                    rec.task.status = "completed"
                    rec.task.statusMessage = f"Completed {tool_name}"
            except asyncio.CancelledError:
                cancel_now = self._utcnow()
                if rec.task.status not in _TERMINAL_TASK_STATUSES:
                    rec.task.status = "cancelled"
                    rec.task.statusMessage = "Task cancelled"
                    rec.task.lastUpdatedAt = cancel_now
                    rec.expires_at = self._compute_expiry(cancel_now, rec.task.ttl)
                if rec.result is None:
                    cancel_payload = ToolResult.fail(
                        "Task cancelled",
                        interrupted=True,
                    ).to_dict()
                    rec.result = CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=json.dumps(cancel_payload, ensure_ascii=False),
                            )
                        ],
                        structuredContent=cancel_payload,
                        isError=True,
                    )
                raise
            except Exception as exc:
                err_now = self._utcnow()
                err_payload = ToolResult.fail(
                    f"{type(exc).__name__}: {exc}",
                    exc_type=type(exc).__name__,
                ).to_dict()
                if rec.task.status not in _TERMINAL_TASK_STATUSES:
                    rec.task.status = "failed"
                    rec.task.statusMessage = f"{type(exc).__name__}: {exc}"[:240]
                    rec.task.lastUpdatedAt = err_now
                    rec.expires_at = self._compute_expiry(err_now, rec.task.ttl)
                rec.result = CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(err_payload, ensure_ascii=False),
                        )
                    ],
                    structuredContent=err_payload,
                    isError=True,
                )
            finally:
                rec.done_event.set()

        rec.asyncio_task = asyncio.create_task(
            _execute_background(),
            name=f"autocode-mcp-task-{tool_name}-{tid[:8]}",
        )
        return CreateTaskResult(task=Task(**rec.task.model_dump()))

    async def update_progress(
        self,
        task_id: str,
        status_message: str,
        *,
        progress: float | None = None,
    ) -> Task:
        """Update status message (and optional 0..1 progress) for an in-flight task."""
        rec = self._require_record(task_id)
        if rec.task.status not in _TERMINAL_TASK_STATUSES:
            rec.task.statusMessage = status_message
            rec.task.lastUpdatedAt = self._utcnow()
            if progress is not None:
                rec.progress = progress
        return Task(**rec.task.model_dump())

    async def get_task(self, task_id: str) -> GetTaskResult:
        """Retrieve current MCP Task status metadata by taskId."""
        rec = self._require_record(task_id)
        return GetTaskResult(**rec.task.model_dump())

    async def list_tasks(self, cursor: str | None = None, limit: int = 50) -> ListTasksResult:
        """List tracked tasks with optional cursor pagination."""
        self._purge_expired()
        if limit < 1:
            raise McpError(ErrorData(code=INVALID_PARAMS, message="limit must be >= 1"))
        offset = 0
        if cursor is not None:
            try:
                offset = int(cursor)
            except ValueError as exc:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Invalid cursor: {cursor}")) from exc
            if offset < 0:
                raise McpError(ErrorData(code=INVALID_PARAMS, message=f"Invalid cursor: {cursor}"))

        records = list(self._tasks.values())
        page = records[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(records) else None
        return ListTasksResult(
            tasks=[Task(**rec.task.model_dump()) for rec in page],
            nextCursor=next_cursor,
        )

    async def cancel_task(self, task_id: str) -> CancelTaskResult:
        """Cancel a running MCP Task and terminate its underlying asyncio task safely."""
        rec = self._require_record(task_id)
        if rec.task.status in _TERMINAL_TASK_STATUSES:
            raise McpError(
                ErrorData(
                    code=INVALID_PARAMS,
                    message=f"Cannot cancel task in terminal state '{rec.task.status}'",
                )
            )

        now = self._utcnow()
        rec.task.status = "cancelled"
        rec.task.statusMessage = "Task cancelled"
        rec.task.lastUpdatedAt = now
        rec.expires_at = self._compute_expiry(now, rec.task.ttl)
        if rec.result is None:
            cancel_payload = ToolResult.fail("Task cancelled", interrupted=True).to_dict()
            rec.result = CallToolResult(
                content=[
                    TextContent(type="text", text=json.dumps(cancel_payload, ensure_ascii=False))
                ],
                structuredContent=cancel_payload,
                isError=True,
            )
        if rec.asyncio_task is not None and not rec.asyncio_task.done():
            rec.asyncio_task.cancel()
            await asyncio.wait({rec.asyncio_task}, timeout=2.0)
        rec.done_event.set()
        return CancelTaskResult(**rec.task.model_dump())

    async def get_task_result(
        self,
        task_id: str,
        *,
        wait: bool = True,
        timeout: float | None = None,
    ) -> GetTaskPayloadResult:
        """Wait for completion (if requested) and return the final MCP task payload."""
        rec = self._require_record(task_id)
        if rec.task.status not in _TERMINAL_TASK_STATUSES:
            if not wait:
                raise McpError(
                    ErrorData(
                        code=INVALID_PARAMS,
                        message=f"Task '{task_id}' is still {rec.task.status}",
                    )
                )
            if timeout is not None:
                await asyncio.wait_for(rec.done_event.wait(), timeout=timeout)
            else:
                await rec.done_event.wait()

        rec = self._require_record(task_id)
        related_meta: dict[str, Any] = {_RELATED_TASK_META_KEY: {"taskId": task_id}}
        if rec.result is not None:
            result_data = rec.result.model_dump(by_alias=True, exclude_none=True)
            existing_meta = result_data.get("_meta")
            merged_meta = dict(existing_meta) if isinstance(existing_meta, dict) else {}
            merged_meta.update(related_meta)
            result_data["_meta"] = merged_meta
            return GetTaskPayloadResult.model_validate(result_data)
        return GetTaskPayloadResult.model_validate({"_meta": related_meta})


TASK_MANAGER = McpTaskManager()


def _get_request_task_ttl() -> tuple[bool, int | None]:
    """Inspect active MCP request context to determine if task augmentation was requested."""
    try:
        ctx = app.request_context
    except LookupError:
        return False, None
    exp = getattr(ctx, "experimental", None)
    task_meta = getattr(exp, "task_metadata", None) if exp is not None else None
    if task_meta is None:
        return False, None
    ttl_val = getattr(task_meta, "ttl", None)
    ttl_ms = int(ttl_val) if isinstance(ttl_val, int) and not isinstance(ttl_val, bool) else None
    return True, ttl_ms


@app.list_tools()
async def list_tools() -> list[Tool]:
    """返回所有可用工具。"""
    tools: list[Tool] = []
    for tool in TOOLS.values():
        meta = get_tool_meta(tool.name)
        extra_kwargs: dict[str, Any] = {"_meta": meta} if meta is not None else {}
        tools.append(
            Tool(
                name=tool.name,
                description=tool.description,
                inputSchema=tool.input_schema,
                outputSchema=get_tool_output_schema(tool.name),
                annotations=get_tool_annotations(tool.name),
                execution=get_tool_execution(tool.name),
                **extra_kwargs,
            )
        )
    return tools


@app.list_resources()
async def list_resources() -> list[Resource]:
    """返回所有内置 MCP Apps UI 资源。"""
    return list_ui_resources()


@app.read_resource()
async def read_resource(uri: AnyUrl) -> list[ReadResourceContents]:
    """读取指定的 ``ui://autocode/*`` MCP App HTML 资源。"""
    try:
        return read_ui_resource(uri)
    except ValueError as exc:
        raise McpError(ErrorData(code=INVALID_PARAMS, message=str(exc))) from exc


@app.experimental.get_task()
async def handle_get_task(req: GetTaskRequest) -> GetTaskResult:
    """MCP ``tasks/get`` request handler."""
    return await TASK_MANAGER.get_task(req.params.taskId)


@app.experimental.list_tasks()
async def handle_list_tasks(req: ListTasksRequest) -> ListTasksResult:
    """MCP ``tasks/list`` request handler."""
    cursor = req.params.cursor if req.params is not None else None
    return await TASK_MANAGER.list_tasks(cursor=cursor)


@app.experimental.get_task_result()
async def handle_get_task_result(req: GetTaskPayloadRequest) -> GetTaskPayloadResult:
    """MCP ``tasks/result`` request handler."""
    return await TASK_MANAGER.get_task_result(req.params.taskId)


@app.experimental.cancel_task()
async def handle_cancel_task(req: CancelTaskRequest) -> CancelTaskResult:
    """MCP ``tasks/cancel`` request handler."""
    return await TASK_MANAGER.cancel_task(req.params.taskId)


async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    """执行同步工具调用并返回带有 ``structuredContent`` 与 JSON ``TextContent`` 的结果。"""
    if name not in TOOLS:
        unknown_payload = ToolResult.fail(f"Unknown tool: {name}").to_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            structuredContent=unknown_payload,
            isError=True,
        )

    if not isinstance(arguments, dict):
        bad_args_payload = ToolResult.fail("Tool arguments must be a JSON object").to_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(bad_args_payload, ensure_ascii=False))],
            structuredContent=bad_args_payload,
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
            isError=not bool(capped_dict.get("success", True)),
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


@app.call_tool()
async def _dispatch_call_tool(
    name: str, arguments: dict[str, Any]
) -> CallToolResult | CreateTaskResult:
    """Dispatch MCP ``tools/call`` synchronously or as an async task when task-augmented."""
    is_task_request, ttl_ms = _get_request_task_ttl()
    if is_task_request:
        return await TASK_MANAGER.create_task(name, arguments, ttl_ms=ttl_ms)
    return await call_tool(name, arguments)


def _normalize_host_header(raw_host: str) -> str | None:
    """Parse an HTTP Host header strictly into its lowercased hostname (rejecting spoofs)."""
    host_val = raw_host.strip()
    if not host_val or any(ch.isspace() or ch in "@/\\" for ch in host_val):
        return None
    if host_val.startswith("["):
        end_idx = host_val.find("]")
        if end_idx <= 1:
            return None
        hostname = host_val[1:end_idx].lower()
        rest = host_val[end_idx + 1 :]
        if rest:
            if not rest.startswith(":") or not rest[1:].isdigit():
                return None
            port = int(rest[1:])
            if not (1 <= port <= 65535):
                return None
        return hostname
    if ":" in host_val:
        hostname, port_str = host_val.rsplit(":", 1)
        if not hostname or ":" in hostname or not port_str.isdigit():
            return None
        port = int(port_str)
        if not (1 <= port <= 65535):
            return None
        return hostname.lower()
    return host_val.lower()


def _is_valid_host(raw_host: str | None, allowed_hosts: set[str]) -> bool:
    if not raw_host:
        return False
    hostname = _normalize_host_header(raw_host)
    if hostname is None:
        return False
    return hostname in allowed_hosts


def _is_valid_origin(
    raw_origin: str | None,
    allowed_hostnames: set[str],
    allowed_origins: set[str],
) -> bool:
    if raw_origin is None:
        return True
    origin = raw_origin.strip()
    if not origin or origin.lower() == "null":
        return False
    if origin in allowed_origins:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port is not None and not (1 <= port <= 65535):
        return False
    hostname = (parsed.hostname or "").lower()
    return hostname in allowed_hostnames


class _StatelessMcpAsgiEndpoint:
    """ASGI endpoint delegating ``/mcp`` requests to stateless StreamableHTTPSessionManager."""

    def __init__(
        self,
        *,
        session_manager: StreamableHTTPSessionManager,
        json_response: bool,
        security_settings: TransportSecuritySettings,
        allowed_hostnames: set[str],
        allowed_origins: set[str],
    ) -> None:
        self._session_manager = session_manager
        self._json_response = json_response
        self._security_settings = security_settings
        self._allowed_hostnames = allowed_hostnames
        self._allowed_origins = allowed_origins
        self.lifespan_active = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            resp = Response("Unsupported scope", status_code=400)
            await resp(scope, receive, send)
            return

        request = Request(scope, receive)
        if not _is_valid_host(request.headers.get("host"), self._allowed_hostnames):
            resp = Response("Invalid Host header", status_code=421)
            await resp(scope, receive, send)
            return
        if not _is_valid_origin(
            request.headers.get("origin"),
            self._allowed_hostnames,
            self._allowed_origins,
        ):
            resp = Response("Invalid Origin header", status_code=403)
            await resp(scope, receive, send)
            return

        if self.lifespan_active:
            await self._session_manager.handle_request(scope, receive, send)
            return

        # Fallback for ASGI test transports (e.g. httpx.ASGITransport) that omit ASGI lifespan.
        scoped_manager = StreamableHTTPSessionManager(
            app=app,
            json_response=self._json_response,
            stateless=True,
            security_settings=self._security_settings,
        )
        async with scoped_manager.run():
            await scoped_manager.handle_request(scope, receive, send)


def create_streamable_http_app(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    json_response: bool = False,
    allowed_origins: Sequence[str] | None = None,
) -> Starlette:
    """Build a stateless Streamable HTTP Starlette ASGI app with Host/Origin protection."""
    register_all_tools()

    norm_host = host.strip().lower()
    if norm_host in _LOOPBACK_HOSTS:
        allowed_hostnames = {"127.0.0.1", "localhost", "::1"}
        sdk_allowed_hosts = [
            "127.0.0.1",
            "127.0.0.1:*",
            "localhost",
            "localhost:*",
            "[::1]",
            "[::1]:*",
        ]
        sdk_allowed_origins = [
            "http://127.0.0.1",
            "http://127.0.0.1:*",
            "https://127.0.0.1",
            "https://127.0.0.1:*",
            "http://localhost",
            "http://localhost:*",
            "https://localhost",
            "https://localhost:*",
            "http://[::1]",
            "http://[::1]:*",
        ]
    else:
        allowed_hostnames = {norm_host}
        sdk_allowed_hosts = [norm_host, f"{norm_host}:*"]
        sdk_allowed_origins = [
            f"http://{norm_host}",
            f"http://{norm_host}:*",
            f"https://{norm_host}",
            f"https://{norm_host}:*",
        ]

    extra_origins = [o.strip() for o in (allowed_origins or ()) if o.strip()]
    sdk_allowed_origins.extend(extra_origins)
    allowed_origin_set = set(extra_origins)

    security_settings = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=sdk_allowed_hosts,
        allowed_origins=sdk_allowed_origins,
    )
    session_manager = StreamableHTTPSessionManager(
        app=app,
        json_response=json_response,
        stateless=True,
        security_settings=security_settings,
    )
    mcp_endpoint = _StatelessMcpAsgiEndpoint(
        session_manager=session_manager,
        json_response=json_response,
        security_settings=security_settings,
        allowed_hostnames=allowed_hostnames,
        allowed_origins=allowed_origin_set,
    )

    async def health(request: Request) -> Response:
        if not _is_valid_host(request.headers.get("host"), allowed_hostnames):
            return Response("Invalid Host header", status_code=421)
        if not _is_valid_origin(
            request.headers.get("origin"),
            allowed_hostnames,
            allowed_origin_set,
        ):
            return Response("Invalid Origin header", status_code=403)
        return JSONResponse(
            {
                "status": "ok",
                "server": "autocode-mcp",
                "version": __version__,
                "transport": "http",
                "stateless": True,
                "bind": f"{host}:{port}",
            }
        )

    @contextlib.asynccontextmanager
    async def lifespan(_starlette_app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            mcp_endpoint.lifespan_active = True
            try:
                yield
            finally:
                mcp_endpoint.lifespan_active = False

    return Starlette(
        debug=False,
        routes=[
            Route("/mcp", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]),
            Route("/mcp/", endpoint=mcp_endpoint, methods=["GET", "POST", "DELETE"]),
            Route("/health", endpoint=health, methods=["GET"]),
        ],
        lifespan=lifespan,
    )


def parse_server_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse ``autocode-mcp`` CLI transport arguments, defaulting to ``stdio``."""
    parser = argparse.ArgumentParser(
        prog="autocode-mcp",
        description="AutoCode MCP Server (stdio default or optional stateless Streamable HTTP).",
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default="stdio",
        help="MCP transport mode (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host for --transport http (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Bind port for --transport http (default: 8765)",
    )
    parser.add_argument(
        "--json-response",
        action="store_true",
        default=False,
        help="Return application/json instead of SSE streams in HTTP mode",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Additional allowed Origin header value for --transport http",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    return args


def main(argv: Sequence[str] | None = None) -> None:
    """启动 MCP Server。"""
    args = parse_server_args(argv)
    register_all_tools()

    if args.transport == "http":
        import uvicorn

        asgi_app = create_streamable_http_app(
            host=args.host,
            port=args.port,
            json_response=args.json_response,
            allowed_origins=args.allowed_origin,
        )
        uvicorn.run(asgi_app, host=args.host, port=args.port)
        return

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
