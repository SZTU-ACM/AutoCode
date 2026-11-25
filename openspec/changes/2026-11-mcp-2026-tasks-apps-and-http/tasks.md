## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/mcp-2026-tasks-and-apps/spec.md` under `openspec/changes/2026-11-mcp-2026-tasks-apps-and-http/` aligned with `openspec/config.yaml`.

## 2. ToolAnnotations, Output Schemas & Structured Content

- [x] 2.1 Add `output_schema_from_model` (zero `# type: ignore` / `cast(`) and optional `output_schema`, `annotations`, `execution`, and `ui_resource_uri` metadata properties/helpers to `Tool` in `src/autocode_mcp/tools/base.py`.
- [x] 2.2 Define `ToolDataBase`, `ToolOutputEnvelope`, per-tool Pydantic output models (`*Data` + `*Output`), `TOOL_ANNOTATIONS`, `TOOL_EXECUTION_MODES`, and `TOOL_UI_URIS` across all 22 tools in `src/autocode_mcp/tools/schemas.py` without altering any existing input fields or defaults.
- [x] 2.3 Update `list_tools()` and `call_tool()` in `src/autocode_mcp/server.py` to publish `annotations`, `outputSchema`, `execution`, and `_meta.ui.resourceUri`, while returning `CallToolResult` with both valid JSON `TextContent` and `structuredContent`.

## 3. Bounded MCP Tasks Lifecycle Manager

- [x] 3.1 Implement `McpTaskManager` in `src/autocode_mcp/server.py` for long-running tools (`stress_test_run`, `problem_generate_tests`, `problem_audit`, `problem_verify_tests`) with bounded eviction, status/progress tracking, safe `asyncio.Task` cancellation, and `get_task` / `list_tasks` / `get_task_result` / `cancel_task` APIs.
- [x] 3.2 Wire MCP request handlers (`tasks/get`, `tasks/list`, `tasks/result`, `tasks/cancel`) and task-augmented `tools/call` dispatch (`CreateTaskResult`) in `src/autocode_mcp/server.py` while keeping direct/non-task `call_tool()` synchronous.

## 4. Zero-CDN Self-Contained MCP Apps (`ui://`)

- [x] 4.1 Create `src/autocode_mcp/ui/__init__.py` with resource descriptors and `importlib.resources` loader helpers for `ui://autocode/complexity-plot`, `ui://autocode/audit-dashboard`, and `ui://autocode/protocol-replay`.
- [x] 4.2 Author zero-CDN self-contained HTML apps `complexity_plot.html`, `audit_dashboard.html`, and `protocol_replay.html` with `postMessage` MCP Apps bridge and standalone interactive demo fallbacks.
- [x] 4.3 Expose `list_resources()` and `read_resource()` in `src/autocode_mcp/server.py` with `mimeType="text/html;profile=mcp-app"`.

## 5. Optional Stateless Streamable HTTP Transport

- [x] 5.1 Add `parse_server_args()` (`--transport stdio|http --host 127.0.0.1 --port 8765`, defaulting to `stdio`) and `create_streamable_http_app()` using `StreamableHTTPSessionManager(stateless=True)` and strict `Host`/`Origin` validation in `src/autocode_mcp/server.py`.

## 6. Conformance, Security Tests & Full Verification

- [x] 6.1 Create `tests/test_mcp_protocol_conformance.py` verifying 22-tool `ToolAnnotations` & `outputSchema` / `structuredContent` conformance, all 3 zero-CDN `ui://autocode/*` resources, `McpTaskManager` lifecycle & MCP handlers, CLI & Streamable HTTP ASGI smoke/`Host`/`Origin` checks, and OWASP `MCPSTG` boundaries.
- [x] 6.2 Run `uv run pytest tests/test_mcp_protocol_conformance.py tests/test_docs_sync.py tests/test_plugin_manifest.py tests/test_e2e_mcp.py tests/test_server_enforcement.py -q`, followed by full `uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, and `uv run pytest tests/ -q`.
