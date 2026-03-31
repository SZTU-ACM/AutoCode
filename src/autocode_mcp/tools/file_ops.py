"""
文件操作工具。
"""

import os

from .base import Tool, ToolResult


class FileReadTool(Tool):
    """读取文件内容。"""

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return """读取文件内容。

        用于读取题目目录中的文件，如代码、配置、题面等。

        注意：此工具不调用任何 LLM，只负责文件读取。
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径（绝对路径或相对于 problem_dir 的相对路径）",
                },
                "problem_dir": {
                    "type": "string",
                    "description": "题目目录路径（用于解析相对路径）",
                },
            },
            "required": ["path"],
        }

    async def execute(self, path: str, problem_dir: str | None = None) -> ToolResult:
        """执行文件读取。"""
        # 解析路径
        if not os.path.isabs(path) and problem_dir:
            full_path = os.path.join(problem_dir, path)
        else:
            full_path = path

        # 规范化路径并防止路径遍历攻击
        full_path = os.path.normpath(os.path.abspath(full_path))

        # 如果指定了 problem_dir，确保文件在该目录内
        if problem_dir:
            problem_dir = os.path.normpath(os.path.abspath(problem_dir))
            if not full_path.startswith(problem_dir + os.sep) and full_path != problem_dir:
                return ToolResult.fail("Access denied: path outside problem directory")

        if not os.path.exists(full_path):
            return ToolResult.fail(f"File not found: {path}")

        if not os.path.isfile(full_path):
            return ToolResult.fail(f"Not a file: {path}")

        try:
            with open(full_path, encoding="utf-8") as f:
                content = f.read()

            return ToolResult.ok(
                path=full_path,
                content=content,
                size=len(content),
            )
        except Exception as e:
            return ToolResult.fail(f"Failed to read file: {str(e)}")


class FileSaveTool(Tool):
    """保存文件内容。"""

    @property
    def name(self) -> str:
        return "file_save"

    @property
    def description(self) -> str:
        return """保存文件内容。

        用于保存 Client LLM 生成的代码、配置等到题目目录。

        注意：此工具不调用任何 LLM，只负责文件保存。
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径（绝对路径或相对于 problem_dir 的相对路径）",
                },
                "content": {
                    "type": "string",
                    "description": "文件内容",
                },
                "problem_dir": {
                    "type": "string",
                    "description": "题目目录路径（用于解析相对路径）",
                },
            },
            "required": ["path", "content"],
        }

    async def execute(
        self,
        path: str,
        content: str,
        problem_dir: str | None = None,
    ) -> ToolResult:
        """执行文件保存。"""
        # 解析路径
        if not os.path.isabs(path) and problem_dir:
            full_path = os.path.join(problem_dir, path)
        else:
            full_path = path

        # 规范化路径并防止路径遍历攻击
        dir_path = os.path.dirname(full_path)
        if dir_path:
            dir_path = os.path.normpath(os.path.abspath(dir_path))

        # 如果指定了 problem_dir，确保文件在该目录内
        if problem_dir:
            problem_dir = os.path.normpath(os.path.abspath(problem_dir))
            full_path = os.path.normpath(os.path.abspath(full_path))
            if not full_path.startswith(problem_dir + os.sep) and full_path != problem_dir:
                return ToolResult.fail("Access denied: path outside problem directory")

        # 确保目录存在
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        try:
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult.ok(
                path=full_path,
                size=len(content),
                message=f"Saved {len(content)} bytes to {path}",
            )
        except Exception as e:
            return ToolResult.fail(f"Failed to save file: {str(e)}")
