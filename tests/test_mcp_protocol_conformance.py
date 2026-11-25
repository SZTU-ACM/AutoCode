"""MCP 2026 protocol conformance, Tasks, Apps UI, Streamable HTTP, and OWASP MCPSTG tests."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import (
    INVALID_PARAMS,
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    CancelTaskRequest,
    CancelTaskRequestParams,
    CancelTaskResult,
    CreateTaskResult,
    GetTaskPayloadRequest,
    GetTaskPayloadRequestParams,
    GetTaskPayloadResult,
    GetTaskRequest,
    GetTaskRequestParams,
    GetTaskResult,
    ListTasksRequest,
    ListTasksResult,
    TaskMetadata,
    TextContent,
    ToolAnnotations,
)
from pydantic import AnyUrl
from starlette.testclient import TestClient

from autocode_mcp.server import (
    _MAX_MCP_RESPONSE_BYTES,
    TASK_MANAGER,
    TOOLS,
    McpTaskManager,
    _cap_mcp_payload,
    app,
    call_tool,
    create_streamable_http_app,
    handle_cancel_task,
    handle_get_task,
    handle_get_task_result,
    handle_list_tasks,
    list_resources,
    list_tools,
    parse_server_args,
    read_resource,
    register_all_tools,
)
from autocode_mcp.tools.schemas import TASK_SUPPORTED_TOOL_NAMES, TOOL_UI_URIS
from autocode_mcp.ui import MCP_APP_MIME_TYPE, UI_RESOURCES, read_ui_resource

READ_ONLY_TOOLS = {
    "file_read",
    "solution_run",
    "solution_analyze",
    "solution_audit_std",
    "solution_audit_brute",
    "validator_select",
    "generator_run",
    "problem_audit",
    "problem_validate",
}

DESTRUCTIVE_TOOLS = {
    "file_save",
    "solution_build",
    "validator_build",
    "generator_build",
    "checker_build",
    "interactor_build",
    "problem_create",
    "problem_generate_tests",
    "problem_cleanup_processes",
    "problem_pack_polygon",
    "problem_build_all",
}


def _make_minimal_problem(base: Path, name: str = "demo-prob") -> Path:
    pdir = base / name
    (pdir / "statements").mkdir(parents=True)
    (pdir / "solutions").mkdir()
    (pdir / "files").mkdir()
    (pdir / "tests").mkdir()
    (pdir / "statements" / "README.md").write_text("# Sum\n\n## 样例\n", encoding="utf-8")
    (pdir / "statements" / "tutorial.md").write_text("# Tutorial\n\nO(n)\n", encoding="utf-8")
    (pdir / "solutions" / "sol.cpp").write_text(
        "// Time Complexity: O(n)\n#include <iostream>\nint main(){int n;std::cin>>n;std::cout<<n;}\n",
        encoding="utf-8",
    )
    (pdir / "tests" / "01.in").write_text("5\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("5\n", encoding="utf-8")
    return pdir


# --------------------------------------------------------------------------- #
# 1. ToolAnnotations + outputSchema + structuredContent across all 22 tools
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_all_22_tools_have_valid_annotations_output_schema_and_execution():
    register_all_tools()
    tools = await list_tools()
    assert len(tools) == 22
    assert {t.name for t in tools} == set(TOOLS.keys())

    for tool in tools:
        ann = tool.annotations
        assert isinstance(ann, ToolAnnotations), f"{tool.name} missing ToolAnnotations"
        assert ann.title and ann.title.strip(), f"{tool.name} missing annotation title"
        assert isinstance(ann.readOnlyHint, bool), f"{tool.name} readOnlyHint not bool"
        assert isinstance(ann.destructiveHint, bool), f"{tool.name} destructiveHint not bool"
        assert isinstance(ann.idempotentHint, bool), f"{tool.name} idempotentHint not bool"
        assert ann.openWorldHint is False, f"{tool.name} must keep openWorldHint=False"

        if tool.name in READ_ONLY_TOOLS:
            assert ann.readOnlyHint is True
            assert ann.destructiveHint is False
        elif tool.name in DESTRUCTIVE_TOOLS:
            assert ann.readOnlyHint is False
            assert ann.destructiveHint is True
        else:
            # stress_test_run / problem_verify_tests perform additive runtime updates only
            assert ann.readOnlyHint is False
            assert ann.destructiveHint is False

        schema = tool.outputSchema
        assert isinstance(schema, dict), f"{tool.name} missing outputSchema"
        assert schema.get("type") == "object"
        assert "success" in schema.get("required", [])
        assert "$defs" not in schema
        assert "$ref" not in json.dumps(schema)
        jsonschema.Draft202012Validator.check_schema(schema)

        assert tool.execution is not None
        expected_mode = "optional" if tool.name in TASK_SUPPORTED_TOOL_NAMES else "forbidden"
        assert tool.execution.taskSupport == expected_mode

        if tool.name in TOOL_UI_URIS:
            assert tool.meta == {"ui": {"resourceUri": TOOL_UI_URIS[tool.name]}}

        # Also check BaseTool.get_tool_definition() parity
        raw_def = TOOLS[tool.name].get_tool_definition()
        assert raw_def["outputSchema"] == schema
        assert raw_def["annotations"]["readOnlyHint"] == ann.readOnlyHint


@pytest.mark.asyncio
async def test_call_tool_structured_content_validates_against_output_schema(tmp_path: Path):
    register_all_tools()
    schemas_by_name = {t.name: t.outputSchema for t in await list_tools()}

    prob_dir = tmp_path / "managed_prob"
    calls: list[tuple[str, dict[str, Any], bool]] = [
        (
            "problem_create",
            {"problem_dir": str(prob_dir), "problem_name": "managed_prob"},
            False,
        ),
        (
            "file_save",
            {
                "problem_dir": str(prob_dir),
                "path": "solutions/sol.cpp",
                "content": "// Time Complexity: O(n)\nint main(){return 0;}\n",
            },
            False,
        ),
        (
            "file_read",
            {"problem_dir": str(prob_dir), "path": "solutions/sol.cpp"},
            False,
        ),
        (
            "file_read",
            {"problem_dir": str(prob_dir), "path": "solutions/does_not_exist.cpp"},
            True,
        ),
        (
            "solution_analyze",
            {"problem_dir": str(prob_dir), "solution_type": "sol"},
            True,  # gate blocked before solution_build
        ),
        (
            "solution_analyze",
            {
                "code": "// Time Complexity: O(n log n)\n#include <algorithm>\nint main(){return 0;}\n",
                "claimed_complexity": "O(n log n)",
            },
            False,
        ),
        (
            "validator_select",
            {
                "candidates": [
                    {"id": "v1", "score": 9, "binary_path": "/tmp/v1", "code": "int main(){}"},
                    {"id": "v2", "score": 10, "binary_path": "/tmp/v2", "code": "int main(){}"},
                ]
            },
            False,
        ),
        (
            "problem_cleanup_processes",
            {"problem_dir": str(prob_dir)},
            False,
        ),
        (
            "problem_pack_polygon",
            {"problem_dir": str(prob_dir)},
            True,  # gate blocked
        ),
        (
            "problem_audit",
            {"problem_dir": str(prob_dir), "mode": "quick", "standalone": True},
            False,
        ),
    ]

    for tool_name, args, expected_is_error in calls:
        res = await call_tool(tool_name, args)
        assert isinstance(res, CallToolResult)
        assert res.isError is expected_is_error
        assert res.structuredContent is not None
        assert res.content and isinstance(res.content[0], TextContent)
        parsed = json.loads(res.content[0].text)
        assert parsed == res.structuredContent
        assert res.isError == (not bool(res.structuredContent.get("success", True)))
        schema = schemas_by_name[tool_name]
        assert schema is not None
        jsonschema.validate(instance=res.structuredContent, schema=schema)

    # Also verify >128KB compacted payload with truncated >25-item list still matches outputSchema
    oversized = {
        "success": True,
        "data": {
            "generated_tests": [{"input": "1\n", "blob": "X" * 6000} for _ in range(30)],
            "total_tests": 30,
        },
    }
    capped_payload, capped_text = _cap_mcp_payload(
        "problem_generate_tests",
        str(prob_dir),
        oversized,
        allow_workspace_spill=False,
    )
    assert len(capped_text.encode("utf-8")) <= _MAX_MCP_RESPONSE_BYTES
    assert capped_payload["data"]["truncated"] is True
    gen_schema = schemas_by_name["problem_generate_tests"]
    assert gen_schema is not None
    jsonschema.validate(instance=capped_payload, schema=gen_schema)


# --------------------------------------------------------------------------- #
# 2. 3 Zero-CDN Self-Contained MCP Apps (ui://autocode/*)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mcp_apps_resources_are_self_contained_and_zero_cdn():
    resources = await list_resources()
    expected_uris = {
        "ui://autocode/complexity-plot",
        "ui://autocode/audit-dashboard",
        "ui://autocode/protocol-replay",
    }
    assert len(resources) == 3
    assert {str(r.uri) for r in resources} == expected_uris
    assert {spec.uri for spec in UI_RESOURCES} == expected_uris

    for res in resources:
        assert res.mimeType == MCP_APP_MIME_TYPE
        assert res.size and res.size > 500
        assert res.meta == {
            "ui": {
                "csp": {"connectDomains": [], "resourceDomains": []},
                "prefersBorder": True,
            }
        }
        contents = await read_resource(res.uri)
        assert len(contents) == 1
        html = contents[0].content
        assert isinstance(html, str)
        assert contents[0].mime_type == MCP_APP_MIME_TYPE
        assert "<!DOCTYPE html>" in html
        assert "postMessage" in html
        assert "ui/notifications/tool-result" in html

        # Zero external HTTP(S) CDN script/link/network references (only SVG XML NS permitted)
        urls = set(re.findall(r"https?://[^\s\"'<>]+", html, re.IGNORECASE))
        assert urls <= {"http://www.w3.org/2000/svg"}, f"External URL leaked in {res.uri}: {urls}"
        assert not re.search(r"<script[^>]+src\s*=", html, re.IGNORECASE)
        assert not re.search(r"<link[^>]+href\s*=", html, re.IGNORECASE)
        assert not re.search(r"@import\s+url|fetch\s*\(", html, re.IGNORECASE)

    # Check app-specific interactive widgets are present
    plot_html = read_ui_resource("ui://autocode/complexity-plot")[0].content
    assert isinstance(plot_html, str)
    assert "<svg" in plot_html and "log10" in plot_html and "cache_jump" in plot_html

    audit_html = read_ui_resource("ui://autocode/audit-dashboard")[0].content
    assert isinstance(audit_html, str)
    assert "surviving_mutants" in audit_html and "wrong_solution_kill" in audit_html

    replay_html = read_ui_resource(AnyUrl("ui://autocode/protocol-replay"))[0].content
    assert isinstance(replay_html, str)
    assert "transcript" in replay_html and "scrubber" in replay_html

    with pytest.raises(McpError) as exc_info:
        await read_resource(AnyUrl("ui://autocode/nonexistent"))
    assert exc_info.value.error.code == INVALID_PARAMS


# --------------------------------------------------------------------------- #
# 3. Asynchronous MCP Tasks Lifecycle Manager & Protocol Handlers
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_task_manager_lifecycle_progress_failure_cancellation_and_bounds(tmp_path: Path):
    register_all_tools()
    manager = McpTaskManager(max_tasks=3, default_ttl_ms=60_000, poll_interval_ms=100)

    # Non-task tool must be rejected
    with pytest.raises(McpError) as exc_info:
        await manager.create_task("file_read", {"path": "/tmp/x"})
    assert exc_info.value.error.code == INVALID_PARAMS

    # 1. Real task execution: problem_audit (standalone quick)
    prob_dir = _make_minimal_problem(tmp_path, "task-audit")
    created = await manager.create_task(
        "problem_audit",
        {"problem_dir": str(prob_dir), "mode": "quick", "standalone": True},
    )
    assert isinstance(created, CreateTaskResult)
    assert created.task.status == "working"
    tid1 = created.task.taskId

    await manager.update_progress(tid1, "Scanning signals 50%", progress=0.5)
    t_info = await manager.get_task(tid1)
    assert t_info.taskId == tid1

    payload1 = await manager.get_task_result(tid1, timeout=10.0)
    assert payload1.model_extra is not None
    assert payload1.model_extra["isError"] is False
    assert payload1.model_extra["structuredContent"]["data"]["decision"] == "go"
    assert payload1.meta == {"io.modelcontextprotocol/related-task": {"taskId": tid1}}
    assert (await manager.get_task(tid1)).status == "completed"

    # 2. Failing task (tool returns isError=True or raises exception)
    async def _raise_runner() -> CallToolResult:
        raise RuntimeError("simulated stress runner crash")

    failed_created = await manager.create_task("stress_test_run", {}, runner=_raise_runner)
    tid2 = failed_created.task.taskId
    payload2 = await manager.get_task_result(tid2, timeout=5.0)
    assert (await manager.get_task(tid2)).status == "failed"
    assert payload2.model_extra is not None
    assert payload2.model_extra["isError"] is True
    assert "RuntimeError" in payload2.model_extra["structuredContent"]["error"]

    # 3. Cancellation of an actively running asyncio Task
    started_evt = asyncio.Event()
    cancelled_observed = False

    async def _blocking_runner() -> CallToolResult:
        nonlocal cancelled_observed
        started_evt.set()
        try:
            await asyncio.sleep(30.0)
        except asyncio.CancelledError:
            cancelled_observed = True
            raise
        return CallToolResult(content=[TextContent(type="text", text="{}")], isError=False)

    cancel_created = await manager.create_task(
        "problem_generate_tests", {}, runner=_blocking_runner
    )
    tid3 = cancel_created.task.taskId
    await started_evt.wait()

    cancelled_res = await manager.cancel_task(tid3)
    assert cancelled_res.status == "cancelled"
    assert cancelled_observed is True

    # Cancelling a terminal task again MUST fail with INVALID_PARAMS
    with pytest.raises(McpError) as cancel_err:
        await manager.cancel_task(tid3)
    assert cancel_err.value.error.code == INVALID_PARAMS

    # 4. Bounded eviction when terminal tasks exist -> oldest terminal evicted cleanly
    listed_before = await manager.list_tasks(limit=2)
    assert len(listed_before.tasks) == 2
    assert listed_before.nextCursor == "2"
    listed_page2 = await manager.list_tasks(cursor=listed_before.nextCursor, limit=2)
    assert len(listed_page2.tasks) == 1
    assert listed_page2.nextCursor is None

    async def _quick_ok() -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text='{"success":true}')],
            structuredContent={"success": True},
            isError=False,
        )

    t4 = await manager.create_task("problem_verify_tests", {}, runner=_quick_ok)
    await manager.get_task_result(t4.task.taskId, timeout=5.0)
    all_after = await manager.list_tasks(limit=10)
    assert len(all_after.tasks) == 3
    assert tid1 not in {t.taskId for t in all_after.tasks}

    # 5. All slots actively working -> reject 4th task cleanly
    gate_evt = asyncio.Event()

    async def _hold_open() -> CallToolResult:
        await gate_evt.wait()
        return CallToolResult(content=[TextContent(type="text", text='{"success":true}')])

    busy_mgr = McpTaskManager(max_tasks=2)
    b1 = await busy_mgr.create_task("stress_test_run", {}, runner=_hold_open)
    b2 = await busy_mgr.create_task("problem_audit", {}, runner=_hold_open)
    with pytest.raises(McpError):
        await busy_mgr.create_task("problem_verify_tests", {}, runner=_hold_open)
    await busy_mgr.cancel_task(b1.task.taskId)
    await busy_mgr.cancel_task(b2.task.taskId)


@pytest.mark.asyncio
async def test_task_handlers_and_connected_mcp_session_end_to_end(tmp_path: Path):
    register_all_tools()
    prob_dir = _make_minimal_problem(tmp_path, "connected-task")

    async with create_connected_server_and_client_session(app) as session:
        init_res = await session.initialize()
        assert init_res.capabilities.resources is not None
        assert init_res.capabilities.tasks is not None
        assert init_res.capabilities.tasks.list is not None
        assert init_res.capabilities.tasks.cancel is not None

        # Standard list_resources / read_resource over real MCP session
        res_list = await session.list_resources()
        assert len(res_list.resources) == 3
        read_res = await session.read_resource(AnyUrl("ui://autocode/complexity-plot"))
        assert read_res.contents[0].mimeType == MCP_APP_MIME_TYPE

        # Task-augmented tools/call over real MCP session
        create_res = await session.send_request(
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="problem_audit",
                    arguments={"problem_dir": str(prob_dir), "mode": "quick", "standalone": True},
                    task=TaskMetadata(ttl=30_000),
                ),
            ),
            CreateTaskResult,
        )
        task_id = create_res.task.taskId

        task_status = await session.send_request(
            GetTaskRequest(params=GetTaskRequestParams(taskId=task_id)),
            GetTaskResult,
        )
        assert task_status.taskId == task_id

        tasks_list = await session.send_request(ListTasksRequest(), ListTasksResult)
        assert any(t.taskId == task_id for t in tasks_list.tasks)

        payload = await session.send_request(
            GetTaskPayloadRequest(params=GetTaskPayloadRequestParams(taskId=task_id)),
            GetTaskPayloadResult,
        )
        assert payload.model_extra is not None
        assert payload.model_extra["structuredContent"]["data"]["decision"] == "go"
        assert (await TASK_MANAGER.get_task(task_id)).status == "completed"

        # Verify handler delegates cleanly and cancelling terminal task raises INVALID_PARAMS
        assert (await handle_get_task(GetTaskRequest(params=GetTaskRequestParams(taskId=task_id)))).taskId == task_id
        assert any(t.taskId == task_id for t in (await handle_list_tasks(ListTasksRequest())).tasks)
        assert (await handle_get_task_result(GetTaskPayloadRequest(params=GetTaskPayloadRequestParams(taskId=task_id)))).meta
        with pytest.raises(McpError):
            await session.send_request(
                CancelTaskRequest(params=CancelTaskRequestParams(taskId=task_id)),
                CancelTaskResult,
            )
        with pytest.raises(McpError):
            await handle_cancel_task(
                CancelTaskRequest(params=CancelTaskRequestParams(taskId=task_id))
            )

        # Synchronous non-task call_tool still returns CallToolResult directly
        sync_res = await session.call_tool(
            "file_read",
            {"problem_dir": str(prob_dir), "path": "tests/01.in"},
        )
        assert not sync_res.isError
        assert sync_res.structuredContent is not None
        assert sync_res.structuredContent["data"]["content"] == "5\n"


# --------------------------------------------------------------------------- #
# 4. CLI --transport parsing & Stateless Streamable HTTP ASGI Transport
# --------------------------------------------------------------------------- #


def test_cli_parse_server_args_defaults_to_stdio():
    default_args = parse_server_args([])
    assert default_args.transport == "stdio"
    assert default_args.host == "127.0.0.1"
    assert default_args.port == 8765
    assert default_args.json_response is False

    http_args = parse_server_args(
        ["--transport", "http", "--host", "127.0.0.1", "--port", "9123", "--json-response"]
    )
    assert http_args.transport == "http"
    assert http_args.host == "127.0.0.1"
    assert http_args.port == 9123
    assert http_args.json_response is True

    with pytest.raises(SystemExit):
        parse_server_args(["--port", "70000"])


def test_streamable_http_asgi_lifespan_smoke_and_host_origin_security(tmp_path: Path):
    sample_file = tmp_path / "hello.txt"
    sample_file.write_text("streamable-ok", encoding="utf-8")

    asgi_app = create_streamable_http_app(host="127.0.0.1", port=8765, json_response=True)
    headers = {
        "Host": "127.0.0.1:8765",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    with TestClient(asgi_app, base_url="http://127.0.0.1:8765") as client:
        health = client.get("/health", headers={"Host": "127.0.0.1:8765"})
        assert health.status_code == 200
        assert health.json()["stateless"] is True

        init_resp = client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "http-smoke", "version": "1.0"},
                },
            },
        )
        assert init_resp.status_code == 200
        assert init_resp.json()["result"]["serverInfo"]["name"] == "autocode-mcp"

        tools_resp = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert tools_resp.status_code == 200
        tools = tools_resp.json()["result"]["tools"]
        assert len(tools) == 22
        assert all("annotations" in t and "outputSchema" in t for t in tools)

        res_resp = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
        )
        assert res_resp.status_code == 200
        assert len(res_resp.json()["result"]["resources"]) == 3

        call_resp = client.post(
            "/mcp",
            headers={**headers, "Origin": "http://localhost:3000"},
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "file_read", "arguments": {"path": str(sample_file)}},
            },
        )
        assert call_resp.status_code == 200
        call_result = call_resp.json()["result"]
        assert call_result["isError"] is False
        assert call_result["structuredContent"]["data"]["content"] == "streamable-ok"

        # Host & Origin DNS-rebinding rejection checks
        assert (
            client.post(
                "/mcp",
                headers={**headers, "Host": "evil.example:8765"},
                json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/mcp",
                headers={**headers, "Host": "127.0.0.1:8765.evil.com"},
                json={"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}},
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/mcp",
                headers={**headers, "Origin": "https://evil.example"},
                json={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/mcp",
                headers={**headers, "Origin": "http://localhost.evil.com:8765"},
                json={"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {}},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/mcp",
                headers={**headers, "Origin": "null"},
                json={"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}},
            ).status_code
            == 403
        )


@pytest.mark.asyncio
async def test_streamable_http_asgi_without_lifespan_via_httpx():
    asgi_app = create_streamable_http_app(host="127.0.0.1", port=8765, json_response=True)
    transport = httpx.ASGITransport(app=asgi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        resp = await client.post(
            "/mcp",
            headers={
                "Host": "127.0.0.1:8765",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        assert resp.status_code == 200
        assert len(resp.json()["result"]["tools"]) == 22


def test_streamable_http_default_sse_mode():
    asgi_app = create_streamable_http_app(host="127.0.0.1", port=8765)
    with TestClient(asgi_app, base_url="http://127.0.0.1:8765") as client:
        resp = client.post(
            "/mcp",
            headers={
                "Host": "127.0.0.1:8765",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        assert "ui://autocode/complexity-plot" in resp.text
        assert "ui://autocode/audit-dashboard" in resp.text
        assert "ui://autocode/protocol-replay" in resp.text


# --------------------------------------------------------------------------- #
# 5. OWASP MCPSTG-* Security & Boundary Checks
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_owasp_mcpstg_boundaries_via_call_tool_and_mcp_session(tmp_path: Path):
    register_all_tools()
    prob_dir = _make_minimal_problem(tmp_path, "owasp-prob")
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("TOP_SECRET_CREDENTIAL\n", encoding="utf-8")

    # 1. Invalid tool name
    unknown_res = await call_tool("nonexistent_admin_tool", {})
    assert unknown_res.isError is True
    assert "Unknown tool" in unknown_res.content[0].text

    # 2. Malformed non-dict or missing args via direct call_tool & MCP JSON-RPC validation
    bad_args: Any = "not-a-dict"
    malformed_res = await call_tool("file_read", bad_args)
    assert malformed_res.isError is True
    assert malformed_res.structuredContent is not None
    assert malformed_res.structuredContent["success"] is False

    # 2b. Task-augmented call on unsupported tool must fail cleanly over MCP session
    async with create_connected_server_and_client_session(app) as session:
        await session.initialize()
        schema_err = await session.call_tool("file_read", {"offset_bytes": -10})
        assert schema_err.isError is True
        assert "Input validation error" in schema_err.content[0].text
        unsupported_task_res = await session.send_request(
            CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="file_read",
                    arguments={"path": str(outside_secret)},
                    task=TaskMetadata(ttl=5000),
                ),
            ),
            CallToolResult,
        )
        assert unsupported_task_res.isError is True
        assert "does not support asynchronous MCP Tasks" in unsupported_task_res.content[0].text

    missing_arg_res = await call_tool("file_read", {})
    assert missing_arg_res.isError is True

    # 2c. Payload size capping (>128 KiB) via real call_tool
    big_candidates = [
        {"id": f"c{i}", "score": i, "binary_path": f"/tmp/v{i}", "code": "A" * 8000}
        for i in range(25)
    ]
    capped_call = await call_tool("validator_select", {"candidates": big_candidates})
    assert not capped_call.isError
    assert len(capped_call.content[0].text.encode("utf-8")) <= _MAX_MCP_RESPONSE_BYTES
    assert capped_call.structuredContent is not None
    assert capped_call.structuredContent["data"]["truncated"] is True
    val_sel_schema = next(t.outputSchema for t in await list_tools() if t.name == "validator_select")
    assert val_sel_schema is not None
    jsonschema.validate(instance=capped_call.structuredContent, schema=val_sel_schema)

    # 3. Path traversal & symlink escape rejection via call_tool
    traversal_read = await call_tool(
        "file_read",
        {"problem_dir": str(prob_dir), "path": "../secret.txt"},
    )
    assert traversal_read.isError is True
    assert traversal_read.structuredContent is not None
    assert "Access denied" in (traversal_read.structuredContent.get("error") or "")

    traversal_save = await call_tool(
        "file_save",
        {"problem_dir": str(prob_dir), "path": "../pwned.txt", "content": "hacked"},
    )
    assert traversal_save.isError is True
    assert not (tmp_path / "pwned.txt").exists()

    link_path = prob_dir / "files" / "escape_link.txt"
    symlink_supported = False
    try:
        os.symlink(str(outside_secret), str(link_path))
        symlink_supported = True
    except OSError:
        pass
    if symlink_supported:
        sym_read = await call_tool(
            "file_read",
            {"problem_dir": str(prob_dir), "path": "files/escape_link.txt"},
        )
        assert sym_read.isError is True
        assert "Access denied" in (sym_read.structuredContent.get("error") or "")
