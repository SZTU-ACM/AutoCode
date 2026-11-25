## Why

AutoCode's `autocode-mcp` server currently exposes all 22 tools with `inputSchema` only, lacks MCP `ToolAnnotations` safety hints and Pydantic-backed `outputSchema` validation contracts, blocks clients synchronously during long-running operations (`stress_test_run`, `problem_generate_tests`, `problem_audit`, `problem_verify_tests`), provides no `ui://` MCP Apps visualization resources for empirical complexity curves, audit matrices, or interactive protocol transcripts, and supports only `stdio` transport.

Modern MCP hosts (2025-11 / 2026 protocol ecosystem) rely on:
1. Explicit `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) so hosts can distinguish read-only inspection (`file_read`, `solution_run`, `problem_audit`) from mutating/destructive operations (`file_save`, `problem_create`, `problem_generate_tests`, `problem_cleanup_processes`).
2. `outputSchema` + `CallToolResult.structuredContent` alongside backwards-compatible JSON `TextContent`.
3. Asynchronous `MCP Tasks` (`Task`, `CreateTaskResult`, `tasks/get`, `tasks/list`, `tasks/result`, `tasks/cancel`) for long-running verification workflows without breaking synchronous `call_tool` behavior.
4. Zero-CDN, self-contained `MCP Apps` (`ui://`) resources for empirical complexity fitting, wrong-solution/mutant audit inspection, and interactive/communication transcript replay.
5. An optional stateless Streamable HTTP transport (`--transport http --host 127.0.0.1 --port 8765`) with localhost-by-default binding and strict `Host`/`Origin` DNS-rebinding protection while keeping `--transport stdio` as the zero-flag default.

## What Changes

- **Annotations, `outputSchema`, and `structuredContent` (`src/autocode_mcp/tools/base.py`, `src/autocode_mcp/tools/schemas.py`, `src/autocode_mcp/server.py`)**:
  - Add `output_schema_from_model(...)` and optional `output_schema`, `annotations`, `execution`, and `ui_resource_uri` metadata lookup/properties on `Tool` without breaking subclasses or existing `input_schema` definitions.
  - Define Pydantic output payload models (`ToolOutputEnvelope`, `ToolDataBase`, and per-tool `*Data` models) plus `ToolAnnotations` and `ToolExecution` metadata for all 22 registered tools in `src/autocode_mcp/tools/schemas.py`.
  - Update `list_tools()` to emit `outputSchema`, `annotations`, `execution`, and `_meta.ui.resourceUri` link hints where applicable, and ensure `call_tool()` consistently emits `CallToolResult(content=[TextContent(...)], structuredContent=capped_payload, isError=...)` with JSON `TextContent` parity.
- **Bounded Asynchronous MCP Task Manager (`src/autocode_mcp/server.py`)**:
  - Implement `McpTaskManager` supporting task-augmented execution for long-running tools (`stress_test_run`, `problem_generate_tests`, `problem_audit`, `problem_verify_tests`), bounded capacity eviction, progress status updates, safe asyncio task cancellation, and MCP handlers for `tasks/get`, `tasks/list`, `tasks/result`, and `tasks/cancel`.
  - Preserve synchronous `call_tool()` execution whenever requests are not task-augmented.
- **Three Self-Contained Zero-CDN `MCP Apps` (`src/autocode_mcp/ui/`)**:
  - Add `src/autocode_mcp/ui/__init__.py` and three zero-CDN HTML applications loaded via `importlib.resources` and served via `list_resources()` / `read_resource()` (`mimeType="text/html;profile=mcp-app"`):
    - `ui://autocode/complexity-plot` (`complexity_plot.html`)
    - `ui://autocode/audit-dashboard` (`audit_dashboard.html`)
    - `ui://autocode/protocol-replay` (`protocol_replay.html`)
- **Optional Stateless Streamable HTTP Transport (`src/autocode_mcp/server.py`)**:
  - Add `parse_server_args()` and CLI flags `--transport stdio|http --host 127.0.0.1 --port 8765` in `main()` (defaulting to `stdio` when invoked with no flags).
  - Add `create_streamable_http_app(...)` backed by `StreamableHTTPSessionManager(stateless=True)` and strict `Host`/`Origin` DNS-rebinding protection.
- **Conformance & OWASP `MCPSTG-*` Tests (`tests/test_mcp_protocol_conformance.py`)**:
  - Verify all 22 tools' `ToolAnnotations`, `outputSchema`, and `structuredContent` validation, all 3 `ui://autocode/*` resources (zero external `http(s)://` script/link tags), `McpTaskManager` lifecycle (start, progress, complete, fail, cancel, bounded eviction, MCP handlers), CLI `--transport` + Streamable HTTP ASGI smoke & `Host`/`Origin` enforcement, and OWASP `MCPSTG` boundary checks.

## Capabilities

### New Capabilities

- `mcp-2026-tasks-and-apps`: Complete MCP `ToolAnnotations`, Pydantic-derived `outputSchema`, bounded async `MCP Tasks` lifecycle management, three zero-CDN `ui://autocode/*` MCP Apps resources, and optional stateless Streamable HTTP transport with strict `Host`/`Origin` validation.

### Modified Capabilities

- `multi-host-distribution`: Preserves default `stdio` invocation for `.mcp.json` and `mcp_config.json` while adding opt-in `--transport http` support and richer MCP client capability metadata (`annotations`, `outputSchema`, `tasks`, `resources`).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12 authoring workflow stages, server-side `preflight` / `prepare_call` / `apply_result` gates, standalone `problem_audit` zero-write guarantees, and all 22 MCP tool names and input parameters (`tests/test_docs_sync.py`).
- **Affected Files**:
  - `src/autocode_mcp/tools/base.py`
  - `src/autocode_mcp/tools/schemas.py`
  - `src/autocode_mcp/server.py`
  - `src/autocode_mcp/ui/__init__.py`, `complexity_plot.html`, `audit_dashboard.html`, `protocol_replay.html`
  - `tests/test_mcp_protocol_conformance.py`
- **Dependencies**: Zero new dependencies (`mcp>=1.0.0,<2.0.0` with `starlette`, `uvicorn`, `anyio`, `pydantic`, and `jsonschema` is already present in the environment; `pyproject.toml` and `uv.lock` remain untouched).
- **Risks**:
  - *Schema rejection on compacted/error/mocked payloads*: `_cap_mcp_payload()` truncates large lists by appending summary strings, and gate errors/tool failures omit normal `data` keys; mitigated by modeling tool-specific `data` fields as optional under `ToolDataBase(extra="allow")` while strictly enforcing the outer `{"success": bool, "error"?: str|null, "data"?: object}` envelope.
  - *Cross-platform/browser or offline CSP restrictions in MCP Apps*: External CDNs fail in sandboxed iframes and violate zero-network guarantees; mitigated by embedding 100% inline CSS/SVG/JS with zero external URLs.
  - *DNS rebinding on HTTP transport*: Prefix-matching `Host`/`Origin` checks can allow subdomain/port spoofing; mitigated by binding `127.0.0.1` by default and parsing hostname/scheme strictly in ASGI validation plus `TransportSecuritySettings`.

## Non-goals

- Do NOT alter any of the 22 MCP tool names or existing `inputSchema` fields/defaults (`tests/test_docs_sync.py` asserts exact `TOOLS.keys()` = 22).
- Do NOT implement Sampling (`SEP-2577`, deprecated).
- Do NOT modify `pyproject.toml`, `uv.lock`, `workflow/models.py`, `tools/audit*.py`, `tools/problem_pack.py`, `tools/build_all.py`, `tools/solution.py`, `utils/exporters/*`, `skills/*`, or `examples/ci/*`.
- Do NOT remove or break synchronous `call_tool(name, arguments)` return behavior for existing tests or `stdio` clients.
