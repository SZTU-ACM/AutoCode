"""
problem_build_all 工具 - 并发构建题目的所有标准二进制。

基于 ``compile_all`` 的有上限并发编译，一次性编译 gen/val/sol/brute
（以及存在的 checker/interactor），任一失败不影响其它源文件。
"""

from __future__ import annotations

import os

from .. import TEMPLATES_DIR
from ..utils.compiler import BuildSpec, compile_all
from .base import Tool, ToolResult, input_schema_from_model
from .mixins import BuildToolMixin
from .schemas import ProblemBuildAllInput

# (相对源路径, include 目录相对名) —— include 目录用于解析 testlib.h
_STANDARD_SOURCES = (
    ("files/gen.cpp", "files"),
    ("files/val.cpp", "files"),
    ("solutions/sol.cpp", "solutions"),
    ("solutions/brute.cpp", "solutions"),
)
_OPTIONAL_SOURCES = (
    ("files/checker.cpp", "files"),
    ("files/interactor.cpp", "files"),
)


class ProblemBuildAllTool(Tool, BuildToolMixin):
    """并发构建题目的所有标准二进制。"""

    @property
    def name(self) -> str:
        return "problem_build_all"

    @property
    def description(self) -> str:
        return """并发构建题目的所有标准二进制。

        一次性并发编译 gen.cpp / val.cpp / sol.cpp / brute.cpp（以及存在的 checker.cpp / interactor.cpp），
        基于 compile_all 的有上限并发编译。任一编译失败不会阻断其它源文件的编译。

        前置条件：
        1. 已运行 problem_create 创建题目目录
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemBuildAllInput)

    async def execute(
        self,
        problem_dir: str,
        compiler: str = "g++",
        max_concurrent: int = 4,
        include_extra_dirs: list[str] | None = None,
    ) -> ToolResult:
        """并发构建所有标准源文件。"""
        if not os.path.isdir(problem_dir):
            return ToolResult.fail(f"problem_dir not found: {problem_dir}")

        extra = [d if os.path.isabs(d) else os.path.join(problem_dir, d) for d in (include_extra_dirs or [])]
        if os.path.isdir(TEMPLATES_DIR):
            extra.append(TEMPLATES_DIR)

        specs: list[BuildSpec] = []
        discovered: list[str] = []
        for rel, inc in list(_STANDARD_SOURCES) + list(_OPTIONAL_SOURCES):
            src_path = os.path.join(problem_dir, rel)
            if os.path.isfile(src_path):
                include_dirs = [os.path.join(problem_dir, inc)] + extra
                specs.append(BuildSpec(source=rel, include_dirs=include_dirs, compiler=compiler))
                discovered.append(rel)

        if not specs:
            return ToolResult.fail(
                "No compilable sources found (gen/val/sol/brute/checker/interactor)",
                discovered=[],
            )

        results = await compile_all(problem_dir, specs, max_concurrent=max(1, max_concurrent))

        compiled: dict[str, str | None] = {}
        failures: dict[str, str] = {}
        for rel, result in results.items():
            if result.success:
                compiled[rel] = result.binary_path
            else:
                failures[rel] = result.error or result.stderr or "compile failed"

        if failures:
            return ToolResult.fail(
                f"{len(failures)} source(s) failed to compile",
                compiled=compiled,
                failures=failures,
                discovered=discovered,
            )
        return ToolResult.ok(
            compiled=compiled,
            failures=failures,
            discovered=discovered,
            message="All sources compiled successfully",
        )
