"""
文件操作工具。
"""

from __future__ import annotations

import os

from .base import Tool, ToolResult, input_schema_from_model
from .schemas import FileReadInput, FileSaveInput

CANONICAL_PROBLEM_FILES = {
    "sol.cpp": "solutions/sol.cpp",
    "brute.cpp": "solutions/brute.cpp",
    "val.cpp": "files/val.cpp",
    "gen.cpp": "files/gen.cpp",
    "checker.cpp": "files/checker.cpp",
    "interactor.cpp": "files/interactor.cpp",
    "README.md": "statements/README.md",
    "tutorial.md": "statements/tutorial.md",
}


def canonical_problem_path(path: str, problem_dir: str | None = None) -> str:
    normalized = path.replace("\\", "/")
    if "/" in normalized:
        return path
    if problem_dir and os.path.exists(os.path.join(problem_dir, path)):
        return path
    return CANONICAL_PROBLEM_FILES.get(normalized, path)


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
        return input_schema_from_model(FileReadInput)

    async def execute(
        self,
        path: str,
        problem_dir: str | None = None,
        offset_bytes: int | None = None,
        limit_bytes: int | None = None,
        start_line: int | None = None,
        line_count: int | None = None,
    ) -> ToolResult:
        """执行文件读取。"""
        # 解析路径
        if not os.path.isabs(path) and problem_dir:
            path = canonical_problem_path(path, problem_dir)
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
            with open(full_path, "rb") as f:
                raw_bytes = f.read()

            total_bytes = len(raw_bytes)

            if start_line is not None or line_count is not None:
                text = raw_bytes.decode("utf-8", errors="replace")
                lines = text.splitlines(keepends=True)
                total_lines = len(lines)
                s_idx = max(0, (start_line or 1) - 1)
                e_idx = s_idx + line_count if line_count is not None else total_lines
                selected = lines[s_idx:e_idx]
                content = "".join(selected)
                is_truncated = (s_idx > 0) or (e_idx < total_lines)
                return ToolResult.ok(
                    path=full_path,
                    content=content,
                    size=len(content),
                    total_bytes=total_bytes,
                    total_lines=total_lines,
                    is_truncated=is_truncated,
                )

            if offset_bytes is not None or limit_bytes is not None:
                off = max(0, offset_bytes or 0)
                lim = limit_bytes if limit_bytes is not None else 64 * 1024
                end = min(total_bytes, off + lim)
                while end < total_bytes and (raw_bytes[end] & 0xC0) == 0x80:
                    end += 1
                slice_bytes = raw_bytes[off:end]
                content = slice_bytes.decode("utf-8", errors="replace")
                next_offset = end if end < total_bytes else None
                return ToolResult.ok(
                    path=full_path,
                    content=content,
                    size=len(content),
                    total_bytes=total_bytes,
                    next_offset=next_offset,
                    is_truncated=(off > 0) or (end < total_bytes),
                )

            content = raw_bytes.decode("utf-8")
            return ToolResult.ok(
                path=full_path,
                content=content,
                size=len(content),
                total_bytes=total_bytes,
                is_truncated=False,
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
        return input_schema_from_model(FileSaveInput)

    async def execute(
        self,
        path: str,
        content: str,
        problem_dir: str | None = None,
    ) -> ToolResult:
        """执行文件保存。"""
        # 解析路径
        if not os.path.isabs(path) and not problem_dir:
            return ToolResult.fail("problem_dir is required for relative file_save paths")

        if not os.path.isabs(path) and problem_dir:
            path = canonical_problem_path(path, problem_dir)
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
            with open(full_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)

            return ToolResult.ok(
                path=full_path,
                size=len(content),
                message=f"Saved {len(content)} bytes to {path}",
            )
        except Exception as e:
            return ToolResult.fail(f"Failed to save file: {str(e)}")
