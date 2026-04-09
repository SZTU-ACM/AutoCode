"""真实 MCP 端到端兼容性测试。

通过 stdio 启动 MCP Server 进程，进行完整的协议握手和工具调用验证。
"""

import asyncio
import json
import os
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

        # 发送请求
        message = json.dumps(request) + "\n"
        self.process.stdin.write(message.encode())
        await self.process.stdin.drain()

        # 读取响应
        response_line = await self.process.stdout.readline()
        if not response_line:
            raise RuntimeError("MCP server closed connection")

        response = json.loads(response_line.decode())

        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        return response.get("result", {})

    async def initialize(self) -> dict:
        """执行 MCP 初始化握手。"""
        # 发送 initialize 请求
        result = await self.send_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        )

        # 发送 initialized 通知
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
        try:
            self.process.kill()
        except ProcessLookupError:
            pass


@pytest.fixture
async def mcp_client():
    """启动 MCP Server 并返回客户端实例。"""
    # 使用 uv run 启动 autocode-mcp
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

    # 验证有 15 个工具
    assert len(tools) == 15

    # 验证关键工具存在
    tool_names = {t["name"] for t in tools}
    expected_tools = {
        "file_read",
        "file_save",
        "solution_build",
        "solution_run",
        "validator_build",
        "generator_build",
        "checker_build",
        "stress_test_run",
        "problem_create",
        "problem_generate_tests",
    }
    assert expected_tools.issubset(tool_names)


@pytest.mark.asyncio
async def test_mcp_call_file_read(mcp_client: MCPClient):
    """测试通过 MCP 调用 file_read 工具。"""
    await mcp_client.initialize()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("hello world")
        temp_path = f.name

    try:
        result = await mcp_client.call_tool("file_read", {"path": temp_path})

        # 验证返回结构
        assert "content" in result
        assert not result.get("isError", True)

        # 验证 content 是列表且包含 TextContent
        content = result["content"]
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0]["type"] == "text"

        # 验证文本内容是有效 JSON
        text = content[0]["text"]
        parsed = json.loads(text)
        assert parsed["success"] is True
        assert "data" in parsed
        assert parsed["data"]["content"] == "hello world"

        # 验证 structuredContent 存在
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

        # 必须是有效 JSON
        parsed = json.loads(text)

        # 不能是 Python repr 格式（如 {'success': True}）
        # Python repr 使用单引号，JSON 使用双引号
        assert "'" not in text  # JSON 不使用单引号
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

        # 验证中文正确编码（ensure_ascii=False）
        assert parsed["data"]["content"] == chinese_content
        # 原始文本应该包含中文字符，不是 \uXXXX 转义
        assert chinese_content in text
    finally:
        os.unlink(temp_path)
