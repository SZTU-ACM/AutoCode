"""
工具基类和统一返回值格式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """
    所有工具的统一返回值格式。

    Attributes:
        success: 操作是否成功
        error: 失败原因（编译错误 stderr 等）
        data: 工具特定的结果数据
    """

    success: bool
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式返回给 MCP Client。"""
        result: dict[str, Any] = {"success": self.success}
        if self.error:
            result["error"] = self.error
        if self.data:
            result["data"] = self.data
        return result

    @classmethod
    def ok(cls, **data) -> ToolResult:
        """创建成功的返回结果。"""
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str, **data) -> ToolResult:
        """创建失败的返回结果。"""
        return cls(success=False, error=error, data=data)


class Tool(ABC):
    """
    MCP 工具的基类。

    每个工具负责单一职责，不调用任何 LLM。
    工具只负责：编译、执行、评分、文件操作。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称。"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述。"""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema 格式的输入定义。"""
        pass

    def get_tool_definition(self) -> dict[str, Any]:
        """获取 MCP 工具定义。"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """
        执行工具逻辑。

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 统一格式的返回结果
        """
        pass
