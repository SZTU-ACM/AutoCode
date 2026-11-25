"""
工具基类和统一返回值格式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from mcp.types import ToolAnnotations, ToolExecution
from pydantic import BaseModel


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
    def ok(cls, **data: Any) -> ToolResult:
        """创建成功的返回结果。"""
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str, **data: Any) -> ToolResult:
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

    @property
    def output_schema(self) -> dict[str, Any] | None:
        """Optional JSON Schema defining the structured output envelope."""
        from .schemas import get_tool_output_schema

        return get_tool_output_schema(self.name)

    @property
    def annotations(self) -> ToolAnnotations | None:
        """Optional MCP ToolAnnotations safety/behavior hints."""
        from .schemas import get_tool_annotations

        return get_tool_annotations(self.name)

    @property
    def execution(self) -> ToolExecution | None:
        """Optional MCP ToolExecution capability metadata (task support)."""
        from .schemas import get_tool_execution

        return get_tool_execution(self.name)

    @property
    def ui_resource_uri(self) -> str | None:
        """Optional linked MCP App ``ui://`` resource URI."""
        from .schemas import get_tool_ui_uri

        return get_tool_ui_uri(self.name)

    def get_tool_definition(self) -> dict[str, Any]:
        """获取 MCP 工具定义。"""
        definition: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.output_schema is not None:
            definition["outputSchema"] = self.output_schema
        if self.annotations is not None:
            definition["annotations"] = self.annotations.model_dump(exclude_none=True)
        if self.execution is not None:
            definition["execution"] = self.execution.model_dump(exclude_none=True)
        if self.ui_resource_uri is not None:
            definition["_meta"] = {"ui": {"resourceUri": self.ui_resource_uri}}
        return definition

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行工具逻辑。

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 统一格式的返回结果
        """
        pass


def _inline_schema_value(value: Any, defs: dict[str, dict[str, Any]]) -> Any:
    """Recursively inline ``$ref`` pointers and strip Pydantic ``title`` metadata."""
    if isinstance(value, dict):
        return _inline_schema_dict(value, defs)
    if isinstance(value, list):
        return [_inline_schema_value(item, defs) for item in value]
    return value


def _inline_schema_dict(node: dict[str, Any], defs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resolve JSON Schema ``$ref`` nodes in a dict without losing sibling keywords."""
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        resolved: dict[str, Any] = {
            k: v for k, v in defs.get(name, {}).items() if k != "title"
        }
        resolved.update({k: v for k, v in node.items() if k != "$ref"})
        return _inline_schema_dict(resolved, defs)

    out: dict[str, Any] = {}
    for key, val in node.items():
        if key == "title":
            continue
        out[key] = _inline_schema_value(val, defs)
    return out


def schema_from_model(model: type[BaseModel]) -> dict[str, Any]:
    """Derive a self-contained JSON Schema from a Pydantic model with ``$defs`` inlined."""
    schema: dict[str, Any] = model.model_json_schema()
    schema.pop("title", None)
    raw_defs = schema.pop("$defs", {})
    defs: dict[str, dict[str, Any]] = (
        {
            k: v
            for k, v in raw_defs.items()
            if isinstance(k, str) and isinstance(v, dict)
        }
        if isinstance(raw_defs, dict)
        else {}
    )
    return _inline_schema_dict(schema, defs)


def input_schema_from_model(model: type[BaseModel]) -> dict[str, Any]:
    """
    Derive an MCP ``inputSchema`` JSON Schema from a Pydantic input model.

    This is the single source of truth for a tool's ``input_schema``, replacing
    hand-written JSON Schema dicts that can drift from the ``execute`` signature.
    Nested models are inlined (``$defs`` resolved) so clients that do not follow
    ``$ref`` still receive a complete, self-contained schema.
    """
    return schema_from_model(model)


def _compact_output_schema(node: Any) -> Any:
    """Compact nullable ``anyOf`` branches into JSON-Schema ``type: [T, "null"]`` arrays."""
    if isinstance(node, dict):
        branches = node.get("anyOf")
        if isinstance(branches, list) and len(branches) == 2:
            null_branches = [b for b in branches if isinstance(b, dict) and b == {"type": "null"}]
            other_branches = [
                b for b in branches if isinstance(b, dict) and b != {"type": "null"}
            ]
            if (
                len(null_branches) == 1
                and len(other_branches) == 1
                and isinstance(other_branches[0].get("type"), str)
                and "enum" not in other_branches[0]
                and "const" not in other_branches[0]
            ):
                base_branch = other_branches[0]
                merged: dict[str, Any] = {
                    k: _compact_output_schema(v) for k, v in base_branch.items()
                }
                merged["type"] = [base_branch["type"], "null"]
                for key, val in node.items():
                    if key not in {"anyOf", "default"}:
                        merged[key] = _compact_output_schema(val)
                return merged

        out: dict[str, Any] = {}
        for key, val in node.items():
            if key == "default" and val is None:
                continue
            out[key] = _compact_output_schema(val)
        return out
    if isinstance(node, list):
        return [_compact_output_schema(item) for item in node]
    return node


def output_schema_from_model(model: type[BaseModel]) -> dict[str, Any]:
    """Derive a compact, self-contained MCP ``outputSchema`` JSON Schema from a Pydantic model."""
    derived = schema_from_model(model)
    compacted = _compact_output_schema(derived)
    return compacted if isinstance(compacted, dict) else derived
