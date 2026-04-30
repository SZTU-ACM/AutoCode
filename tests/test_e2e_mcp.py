"""MCP 端到端兼容性测试。

通过 stdio 启动 MCP Server 进程，进行完整的协议握手和工具调用验证。
包含开发模式（python -m）和打包模式（console script）两种测试。
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

import pytest


class MCPClient:
    """简单的 MCP 客户端，用于端到端测试。"""

    def __init__(self, process: asyncio.subprocess.Process):
        self.process = process
        self.request_id = 0

    async def send_request(self, method: str, params: dict | None = None) -> dict:
        """发送 JSON-RPC 请求并等待响应。"""
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params or {},
        }

        message = json.dumps(request) + "\n"
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()

        response_line = await self.process.stdout.readline()
        if not response_line:
            raise RuntimeError("MCP server closed connection")

        response = json.loads(response_line.decode())

        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        return response.get("result", {})

    async def initialize(self) -> dict:
        """执行 MCP 初始化握手。"""
        result = await self.send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        )

        notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        message = json.dumps(notification) + "\n"
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()

        return result

    async def list_tools(self) -> list[dict]:
        """获取工具列表。"""
        result = await self.send_request("tools/list")
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """调用工具。"""
        return await self.send_request("tools/call", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        """关闭连接。"""
        if self.process.stdin:
            self.process.stdin.close()
            await self.process.stdin.wait_closed()

        if self.process.returncode is None:
            self.process.kill()

        await self.process.communicate()


# ============== 开发模式测试（python -m） ==============


@pytest.fixture
async def mcp_client():
    """启动 MCP Server 并返回客户端实例（开发模式）。"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "autocode_mcp.server",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    client = MCPClient(process)

    try:
        yield client
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_handshake(mcp_client: MCPClient):
    """测试 MCP 协议握手。"""
    result = await mcp_client.initialize()

    assert "protocolVersion" in result
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "autocode-mcp"


@pytest.mark.asyncio
async def test_mcp_list_tools(mcp_client: MCPClient):
    """测试获取工具列表。"""
    await mcp_client.initialize()

    tools = await mcp_client.list_tools()

    assert len(tools) == 20

    tool_names = {t["name"] for t in tools}
    expected_tools = {
        "file_read",
        "file_save",
        "solution_build",
        "solution_run",
        "solution_analyze",
        "solution_audit_std",
        "solution_audit_brute",
        "validator_build",
        "validator_select",
        "generator_build",
        "generator_run",
        "checker_build",
        "interactor_build",
        "stress_test_run",
        "problem_create",
        "problem_generate_tests",
        "problem_cleanup_processes",
        "problem_verify_tests",
        "problem_validate",
        "problem_pack_polygon",
    }
    assert tool_names == expected_tools


@pytest.mark.asyncio
async def test_mcp_call_file_read(mcp_client: MCPClient):
    """测试通过 MCP 调用 file_read 工具。"""
    await mcp_client.initialize()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("hello world")
        temp_path = f.name

    try:
        result = await mcp_client.call_tool("file_read", {"path": temp_path})

        assert "content" in result
        assert not result.get("isError", True)

        content = result["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "text"

        text = content[0]["text"]
        parsed = json.loads(text)
        assert parsed["success"] is True
        assert "data" in parsed
        assert parsed["data"]["content"] == "hello world"

        assert "structuredContent" in result
        assert result["structuredContent"]["success"] is True
    finally:
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_mcp_call_unknown_tool(mcp_client: MCPClient):
    """测试调用不存在的工具返回错误。"""
    await mcp_client.initialize()

    result = await mcp_client.call_tool("nonexistent_tool", {})

    assert result.get("isError") is True
    assert "Unknown tool" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_mcp_text_content_is_valid_json(mcp_client: MCPClient):
    """测试 TextContent 的文本是有效 JSON（不是 Python repr）。"""
    await mcp_client.initialize()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("test")
        temp_path = f.name

    try:
        result = await mcp_client.call_tool("file_read", {"path": temp_path})

        text = result["content"][0]["text"]

        parsed = json.loads(text)

        assert "'" not in text
        assert parsed["success"] is True
    finally:
        os.unlink(temp_path)


@pytest.mark.asyncio
async def test_mcp_chinese_text_encoding(mcp_client: MCPClient):
    """测试中文文本编码正确处理。"""
    await mcp_client.initialize()

    chinese_content = "你好世界"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(chinese_content)
        temp_path = f.name

    try:
        result = await mcp_client.call_tool("file_read", {"path": temp_path})

        text = result["content"][0]["text"]
        parsed = json.loads(text)

        assert parsed["data"]["content"] == chinese_content
        assert chinese_content in text
    finally:
        os.unlink(temp_path)


# ============== 打包模式测试（console script） ==============


@pytest.fixture
async def packaged_mcp_client():
    """启动打包后的 autocode-mcp console script。"""
    process = await asyncio.create_subprocess_exec(
        "autocode-mcp",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    client = MCPClient(process)

    try:
        yield client
    finally:
        await client.close()


@pytest.mark.packaging
@pytest.mark.asyncio
async def test_packaged_console_script_handshake(packaged_mcp_client: MCPClient):
    """测试打包后的 console script 能完成 MCP 握手。"""
    result = await packaged_mcp_client.initialize()

    assert "protocolVersion" in result
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "autocode-mcp"


@pytest.mark.packaging
@pytest.mark.asyncio
async def test_packaged_console_script_list_tools(packaged_mcp_client: MCPClient):
    """测试打包后的 console script 能列出工具。"""
    await packaged_mcp_client.initialize()

    tools = await packaged_mcp_client.list_tools()

    assert len(tools) == 20
    tool_names = {t["name"] for t in tools}
    assert "solution_audit_std" in tool_names
    assert "problem_cleanup_processes" in tool_names


@pytest.mark.packaging
@pytest.mark.asyncio
async def test_packaged_console_script_call_tool(packaged_mcp_client: MCPClient):
    """测试打包后的 console script 能调用工具。"""
    await packaged_mcp_client.initialize()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("packaged test")
        temp_path = f.name

    try:
        result = await packaged_mcp_client.send_request(
            "tools/call", {"name": "file_read", "arguments": {"path": temp_path}}
        )

        assert "content" in result
        assert not result.get("isError", True)

        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["success"] is True
        assert parsed["data"]["content"] == "packaged test"
    finally:
        os.unlink(temp_path)


@pytest.mark.packaging
def test_console_script_exists():
    """验证 autocode-mcp console script 在环境中存在。"""
    assert shutil.which("autocode-mcp") is not None
