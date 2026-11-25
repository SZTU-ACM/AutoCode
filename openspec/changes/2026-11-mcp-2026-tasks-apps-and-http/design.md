## Context

`autocode-mcp` currently runs over `stdio_server()` via `src/autocode_mcp/server.py`, exposing 22 tools registered in `TOOLS: dict[str, Tool]` whose input models live in `src/autocode_mcp/tools/schemas.py` and base contract lives in `src/autocode_mcp/tools/base.py`.

Naming and responsibility boundaries remain explicit:
- MCP protocol definitions, schema helpers, annotations, task lifecycle, resource handlers, and Streamable HTTP factory live strictly under `src/autocode_mcp/tools/base.py`, `src/autocode_mcp/tools/schemas.py`, `src/autocode_mcp/server.py`, and `src/autocode_mcp/ui/`.
- Host enforcement hook scripts under `scripts/` and all individual domain tool implementations (`tools/audit*.py`, `tools/solution.py`, `tools/problem_pack.py`, etc.) remain untouched.

## Architecture & Design Decisions

```text
                     ┌──────────────────────────────────────┐
                     │        CLI: autocode-mcp             │
                     │ --transport stdio (default) | http   │
                     └───────────────┬──────────────────────┘
                                     │
              ┌──────────────────────┴───────────────────────┐
              ▼                                              ▼
     stdio_server()                          create_streamable_http_app()
  (default .mcp.json)                    Starlette + Host/Origin Security Guard
              │                            + StreamableHTTPSessionManager(stateless=True)
              └──────────────────────┬───────────────────────┘
                                     ▼
                          Server("autocode-mcp")
        ┌────────────────────────────┼────────────────────────────┐
        ▼                            ▼                            ▼
  list_tools()                 call_tool()               list_resources() / read_resource()
  - ToolAnnotations            - non-task -> sync          - ui://autocode/complexity-plot
  - outputSchema (Pydantic)      CallToolResult + JSON     - ui://autocode/audit-dashboard
  - _meta.ui.resourceUri       - task-augmented ->         - ui://autocode/protocol-replay
  - execution.taskSupport        McpTaskManager              (mimeType: text/html;profile=mcp-app)
                                   ├── tasks/get
                                   ├── tasks/list
                                   ├── tasks/result
                                   └── tasks/cancel
```

### 1. `ToolAnnotations` + Pydantic `outputSchema` (`tools/base.py`, `tools/schemas.py`)

- **Metadata registry decoupled from tool subclasses**: Because `tools/audit*.py`, `tools/solution.py`, `tools/problem_pack.py`, `tools/build_all.py`, etc. are untouched, `Tool` in `src/autocode_mcp/tools/base.py` exposes optional `annotations`, `output_schema`, `execution`, and `ui_resource_uri` properties backed by explicit metadata registries (`TOOL_ANNOTATIONS`, `TOOL_OUTPUT_MODELS`, `TOOL_UI_URIS`, `TOOL_EXECUTION_MODES` in `schemas.py`), while remaining overridable on custom subclasses.
- **`output_schema_from_model` without `$ref` leakage or `# type: ignore`**: Mirrors `input_schema_from_model` by calling `model.model_json_schema()` and recursively inlining `$defs` with strict dictionary/value typing so `mypy --strict` passes with zero `# type: ignore` or `cast(`.
- **Schema resilience across error, gate-block, and payload-cap paths**:
  - `call_tool()` returns `ToolResult.to_dict()` optionally compacted by `_cap_mcp_payload()` (128 KiB limit). When `_compact_value()` truncates lists > 25 items, it appends a trailing summary string; when a tool fails or is gate-blocked, tool-specific fields in `data` are absent and `gate_blocked` / `blocking_issues` / `exc_type` / `truncated` fields appear instead.
  - Therefore `ToolOutputEnvelope(extra="allow")` requires `success: bool`, allows optional `error: str | None = None`, and embeds tool-specific `*Data(ToolDataBase)` models with optional typed fields and `extra="allow"`. Every response (success, failure, gate-blocked, compacted, or test-mocked) validates cleanly against its tool's `outputSchema`.

### 2. Bounded Asynchronous `McpTaskManager` (`server.py`)

- **Supported tools**: `TASK_SUPPORTED_TOOLS = frozenset({"stress_test_run", "problem_generate_tests", "problem_audit", "problem_verify_tests"})`. Their `Tool.execution` advertises `ToolExecution(taskSupport="optional")`.
- **Dual direct + protocol support**:
  - Calling `call_tool(name, arguments)` directly in Python (outside an MCP request context) always executes synchronously and returns `CallToolResult`, preserving 100% parity with existing unit/enforcement tests.
  - Protocol calls (`tools/call`) dispatched via `@app.call_tool()` inspect `app.request_context.experimental.task_metadata`: when present, task-capable tools spawn a tracked background `asyncio.Task` via `TASK_MANAGER.create_task(...)` and return `CreateTaskResult(task=...)`; non-task tools raise `McpError(INVALID_PARAMS)`.
  - Handlers for `GetTaskRequest`, `ListTasksRequest`, `GetTaskPayloadRequest`, and `CancelTaskRequest` are registered via `app.experimental.{get_task, list_tasks, get_task_result, cancel_task}()`, enabling `ServerCapabilities.tasks` automatically.
- **Bounded capacity & safe cancellation**:
  - `McpTaskManager(max_tasks=128)` evicts expired tasks first, then oldest terminal (`completed`/`failed`/`cancelled`) tasks when full, and fails cleanly (`INTERNAL_ERROR`) if all slots are actively `working`.
  - `cancel_task(task_id)` rejects missing or already-terminal tasks with `McpError(INVALID_PARAMS)` per MCP spec, marks status `"cancelled"`, cancels the underlying `asyncio.Task`, and awaits termination so `call_tool`'s `CancelledError` cleanup runs deterministically.

### 3. Self-Contained Zero-CDN `MCP Apps` (`src/autocode_mcp/ui/`)

- Packaged inside `src/autocode_mcp/ui/` (`__init__.py` + 3 HTML files) and loaded via `importlib.resources.files("autocode_mcp.ui")` so it works in both editable and built wheel installs.
- Each app contains zero external `http://` / `https://` `<script>`, `<link>`, or network calls; communicates via MCP Apps `postMessage` (`ui/notifications/tool-result`, `ui/notifications/tool-input`, `ui/initialize`) as well as direct `autocode:data` payloads; and ships realistic standalone demo datasets + interactive JSON input fallback.
- `list_resources()` exposes all 3 `ui://autocode/*` resources with `mimeType="text/html;profile=mcp-app"`, and `read_resource()` serves `ReadResourceContents(..., mime_type="text/html;profile=mcp-app")`.

### 4. Stateless Streamable HTTP Transport (`server.py`)

- `create_streamable_http_app(host="127.0.0.1", port=8765, json_response=False, allowed_origins=None)` constructs a `Starlette` ASGI app exposing `/mcp` (via a `Route("/mcp", ...)` wrapping an ASGI callable object to avoid `Mount` 307 trailing-slash redirects on `POST /mcp`) backed by `StreamableHTTPSessionManager(app=app, stateless=True, json_response=json_response, security_settings=...)`.
- **Lifespan & testability**: Runs `session_manager.run()` inside Starlette `lifespan`, with a stateless per-request fallback if an ASGI test transport (`httpx.ASGITransport`) omits ASGI lifespan events.
- **Strict `Host` & `Origin` protection**: In addition to `TransportSecuritySettings`, an explicit ASGI guard parses `Host` (host + optional numeric port) and `Origin` (`urlsplit(origin)` scheme + hostname), rejecting spoofs such as `127.0.0.1:8765.evil.com` (`421 Invalid Host header`) or `Origin: https://evil.example` (`403 Invalid Origin header`).

## Risks / Mitigations

- **Runtime `get_type_hints` evaluation in MCP decorators**: `mcp.server.lowlevel.func_inspection.create_call_wrapper` inspects annotations at runtime when registering `@app.experimental.*` and `@app.read_resource()`. Mitigated by importing `AnyUrl`, `GetTaskRequest`, `ListTasksRequest`, `GetTaskPayloadRequest`, and `CancelTaskRequest` unconditionally at module top-level in `server.py`.
- **`stdio` backward compatibility**: Invoking `autocode-mcp` with no CLI flags in `.mcp.json`, `mcp_config.json`, and `tests/test_e2e_mcp.py` must continue launching `stdio_server()`. Mitigated by defaulting `--transport` to `"stdio"` in `parse_server_args()`.
- **Long-running subprocess cancellation**: Cancelling an MCP task during `problem_generate_tests` or `stress_test_run` must propagate `asyncio.CancelledError` cleanly into the running coroutine without crashing the manager. Mitigated by storing `asyncio.Task` handles in `McpTaskManager` and resolving `done_event` in `finally` blocks.
