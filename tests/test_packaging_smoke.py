"""打包产物 smoke test。

验证 uv build 构建的 wheel 安装后，console script 入口能正常工作。
这个测试不在 CI 的常规测试中运行，而是在专门的打包测试 job 中运行。
"""

import asyncio
import json
import os
import shutil
import tempfile

import pytest


class PackagedMCPClient:
    """测试打包后的 autocode-mcp 命令行入口。"""

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
                "clientInfo": {"name": "packaged-test-client", "version": "1.0.0"},
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

    async def close(self) -> None:
        """关闭连接。"""
        if self.process.stdin:
            self.process.stdin.close()
            await self.process.stdin.wait_closed()

        if self.process.returncode is None:
            self.process.kill()

        await self.process.communicate()


@pytest.fixture
async def packaged_mcp_client():
    """启动打包后的 autocode-mcp console script。

    这个 fixture 假设 autocode-mcp 已经通过 pip/uv 安装在当前环境中。
    """
    # 使用已安装的 console script
    process = await asyncio.create_subprocess_exec(
        "autocode-mcp",  # 使用 console script 入口
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    client = PackagedMCPClient(process)

    try:
        yield client
    finally:
        await client.close()


@pytest.mark.packaging
@pytest.mark.asyncio
async def test_packaged_console_script_handshake(packaged_mcp_client: PackagedMCPClient):
    """测试打包后的 console script 能完成 MCP 握手。"""
    result = await packaged_mcp_client.initialize()

    assert "protocolVersion" in result
    assert "serverInfo" in result
    assert result["serverInfo"]["name"] == "autocode-mcp"


@pytest.mark.packaging
@pytest.mark.asyncio
async def test_packaged_console_script_list_tools(packaged_mcp_client: PackagedMCPClient):
    """测试打包后的 console script 能列出工具。"""
    await packaged_mcp_client.initialize()

    tools = await packaged_mcp_client.list_tools()

    assert len(tools) == 15
    tool_names = {t["name"] for t in tools}
    assert "solution_build" in tool_names
    assert "validator_build" in tool_names


@pytest.mark.packaging
@pytest.mark.asyncio
async def test_packaged_console_script_call_tool(packaged_mcp_client: PackagedMCPClient):
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


def test_console_script_exists():
    """验证 autocode-mcp console script 在环境中存在。"""
    assert shutil.which("autocode-mcp") is not None
