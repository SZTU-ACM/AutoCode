## Purpose

Define the MCP protocol conformance requirements for `ToolAnnotations`, Pydantic-derived `outputSchema` and `structuredContent`, bounded asynchronous `MCP Tasks`, self-contained zero-CDN `ui://` MCP Apps resources, and optional stateless Streamable HTTP transport without breaking existing `stdio` behavior or the 22-tool input contract.

## Requirements

### Requirement: Complete ToolAnnotations, outputSchema, and structuredContent across all 22 tools
The system SHALL attach explicit `ToolAnnotations` (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) and Pydantic-derived JSON `outputSchema` definitions (with all `$defs` references inlined) to all 22 tools exposed by `list_tools()`, and SHALL return `CallToolResult` containing serialized JSON in `TextContent`, schema-conformant `structuredContent`, and accurate `isError` flags.

#### Scenario: Read-only and destructive annotations plus outputSchema match all 22 tools
- **WHEN** a client queries `list_tools()`
- **THEN** exactly 22 tools are returned, every tool has non-null `ToolAnnotations` (`readOnlyHint=True, destructiveHint=False` on read-only tools such as `file_read`, `solution_run`, `problem_audit`, and `destructiveHint=True` on mutating tools such as `file_save`, `problem_create`, `problem_generate_tests`, `problem_cleanup_processes`) and a valid JSON `outputSchema` with root `type="object"` and no unresolved `$ref` keys

#### Scenario: Tool calls preserve JSON TextContent and schema-valid structuredContent
- **WHEN** `call_tool` executes a tool call (succeeding, failing, gate-blocked, or compacted at the 128 KiB boundary)
- **THEN** `CallToolResult.content[0].text` contains valid JSON, `CallToolResult.structuredContent` validates against that tool's `outputSchema`, and `CallToolResult.isError` equals `not bool(structuredContent.get("success", True))`

### Requirement: Bounded asynchronous MCP Tasks lifecycle management
The system SHALL provide an in-memory bounded `McpTaskManager` supporting task-augmented execution for long-running tools (`stress_test_run`, `problem_generate_tests`, `problem_audit`, `problem_verify_tests`), exposing `create_task`, `update_task_progress`, `get_task`, `list_tasks`, `get_task_result`, and `cancel_task` using `mcp.types.Task` and `CreateTaskResult`, and registering MCP handlers for `tasks/get`, `tasks/list`, `tasks/result`, and `tasks/cancel`, while keeping non-task `call_tool` execution synchronous.

#### Scenario: Task lifecycle tracks working, progress, completion, failure, and safe cancellation
- **WHEN** a task-capable tool is started via `McpTaskManager.create_task`
- **THEN** it immediately returns `CreateTaskResult` with `status="working"`, transitions cleanly to `"completed"` or `"failed"` with retrievable `GetTaskPayloadResult` (including `io.modelcontextprotocol/related-task` metadata), and cancels running `asyncio.Task` instances safely on `cancel_task` while rejecting cancellation of terminal or unknown tasks with `McpError(INVALID_PARAMS)`

#### Scenario: Bounded task registry evicts expired and terminal entries at capacity
- **WHEN** new tasks are created after reaching `max_tasks`
- **THEN** expired tasks and oldest terminal tasks are evicted to keep memory bounded, while synchronous `call_tool` without task metadata continues returning `CallToolResult` directly

### Requirement: Three zero-CDN self-contained MCP Apps UI resources
The system SHALL expose `ui://autocode/complexity-plot`, `ui://autocode/audit-dashboard`, and `ui://autocode/protocol-replay` via `list_resources()` and `read_resource()` with `mimeType="text/html;profile=mcp-app"`, link relevant tools via `_meta.ui.resourceUri`, and embed all CSS, SVG/Canvas rendering, `postMessage` bridge handling, and standalone demo fallbacks inline without external `http(s)://` script or stylesheet URLs.

#### Scenario: UI resources are self-contained and linked from tool metadata
- **WHEN** a client lists and reads all three `ui://autocode/*` resources and inspects `list_tools()`
- **THEN** each resource returns HTML with `mimeType="text/html;profile=mcp-app"` containing zero external `http://` or `https://` `<script src>` / `<link href>` references, and complexity, audit, and protocol tools advertise matching `_meta.ui.resourceUri` metadata

### Requirement: Optional stateless Streamable HTTP transport with localhost and Host/Origin guards
The system SHALL support `--transport stdio|http --host 127.0.0.1 --port 8765` in `main()`, defaulting to `stdio` when invoked with no flags, and SHALL provide an ASGI/Starlette factory `create_streamable_http_app` using `StreamableHTTPSessionManager(stateless=True)` that enforces `Host` and `Origin` validation against DNS rebinding attacks.

#### Scenario: Default CLI stays stdio while HTTP transport serves /mcp and blocks untrusted Host/Origin
- **WHEN** `parse_server_args([])` is evaluated, or an ASGI client queries `create_streamable_http_app()`
- **THEN** zero-flag invocation selects `stdio` seamlessly, valid localhost requests to `/mcp` succeed via `StreamableHTTPSessionManager(stateless=True)`, and requests with spoofed `Host` or untrusted `Origin` headers are rejected with HTTP `421` or `403`
