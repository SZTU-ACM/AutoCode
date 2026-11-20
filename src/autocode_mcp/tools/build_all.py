"""
problem_build_all 工具 - 并发构建题目的所有标准二进制。

基于 ``compile_all`` 的有上限并发编译，一次性编译 gen/val/sol/brute
（以及存在的 checker/interactor），任一失败不影响其它源文件。
"""

from __future__ import annotations

import os

from pydantic import ValidationError

from .. import TEMPLATES_DIR
from ..utils.communication_runner import is_safe_stem, safe_resolve_under_root
from ..utils.compiler import BuildSpec, compile_all
from ..utils.platform import get_exe_extension
from ..workflow import load_manifest, manifest_is_communication
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
    ("files/communicator.cpp", "files"),
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

        try:
            manifest = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")

        extra = [d if os.path.isabs(d) else os.path.join(problem_dir, d) for d in (include_extra_dirs or [])]
        if os.path.isdir(TEMPLATES_DIR):
            extra.append(TEMPLATES_DIR)

        is_grader_linked = (
            manifest is not None
            and manifest_is_communication(manifest)
            and manifest.protocol is not None
            and manifest.protocol.mode == "grader_linked"
        )
        grader_rel: str | None = None
        grader_inc_dirs: list[str] = []
        if is_grader_linked and manifest is not None and manifest.protocol is not None:
            grader_rel = manifest.protocol.grader_file or "files/grader.cpp"
            resolved_grader = safe_resolve_under_root(problem_dir, grader_rel)
            if resolved_grader is None:
                return ToolResult.fail(
                    f"grader_file path escapes problem_dir: {grader_rel}"
                )
            if not os.path.isfile(resolved_grader):
                return ToolResult.fail(
                    f"grader_linked communication problem requires {grader_rel}"
                )
            files_dir = os.path.join(problem_dir, "files")
            if os.path.isdir(files_dir):
                grader_inc_dirs.append(files_dir)
            if manifest.protocol.header_file:
                resolved_hdr = safe_resolve_under_root(problem_dir, manifest.protocol.header_file)
                if resolved_hdr is None:
                    return ToolResult.fail(
                        f"header_file path escapes problem_dir: {manifest.protocol.header_file}"
                    )
                hdr_dir = os.path.dirname(resolved_hdr)
                if hdr_dir and os.path.isdir(hdr_dir) and hdr_dir not in grader_inc_dirs:
                    grader_inc_dirs.append(hdr_dir)

        def _norm_rel(raw: str) -> str:
            return os.path.normpath(raw.replace("\\", "/")).replace("\\", "/")

        # (rel_path, include_rel_dir, is_solution)
        candidate_sources: list[tuple[str, str, bool]] = [
            (_norm_rel(rel), inc, _norm_rel(rel).startswith("solutions/"))
            for rel, inc in list(_STANDARD_SOURCES) + list(_OPTIONAL_SOURCES)
        ]
        seen_rels = {rel for rel, _, _ in candidate_sources}

        if manifest is not None and manifest.protocol is not None:
            if manifest.protocol.communicator_file:
                comm_rel = _norm_rel(manifest.protocol.communicator_file)
                if safe_resolve_under_root(problem_dir, comm_rel) is None:
                    return ToolResult.fail(
                        f"communicator_file path escapes problem_dir: {manifest.protocol.communicator_file}"
                    )
                if comm_rel not in seen_rels:
                    candidate_sources.append((comm_rel, os.path.dirname(comm_rel) or "files", False))
                    seen_rels.add(comm_rel)
            for part_name in manifest.protocol.participant_solutions:
                if not is_safe_stem(part_name):
                    return ToolResult.fail(
                        f"invalid participant_solutions stem: {part_name!r}"
                    )
                part_rel = _norm_rel(f"solutions/{part_name}.cpp")
                if part_rel not in seen_rels:
                    candidate_sources.append((part_rel, "solutions", True))
                    seen_rels.add(part_rel)

        if manifest is not None:
            for sol_entry in manifest.solutions:
                if sol_entry.language != "cpp":
                    continue
                sol_rel = _norm_rel(sol_entry.path)
                if safe_resolve_under_root(problem_dir, sol_rel) is None:
                    return ToolResult.fail(
                        f"manifest solution path escapes problem_dir: {sol_entry.path}"
                    )
                if sol_rel not in seen_rels and sol_rel.endswith(".cpp"):
                    candidate_sources.append((sol_rel, os.path.dirname(sol_rel) or "solutions", True))
                    seen_rels.add(sol_rel)

        specs: list[BuildSpec] = []
        discovered: list[str] = []
        for rel, inc, is_solution in candidate_sources:
            src_path = safe_resolve_under_root(problem_dir, rel)
            if src_path is None:
                return ToolResult.fail(f"source path escapes problem_dir: {rel}")
            if os.path.isfile(src_path):
                include_dirs = [os.path.join(problem_dir, inc)] + extra
                extra_sources: list[str] | None = None
                if is_grader_linked and grader_rel and is_solution:
                    for gi in grader_inc_dirs:
                        if gi not in include_dirs:
                            include_dirs.append(gi)
                    extra_sources = [grader_rel]
                specs.append(
                    BuildSpec(
                        source=rel,
                        include_dirs=include_dirs,
                        extra_sources=extra_sources,
                        compiler=compiler,
                    )
                )
                discovered.append(rel)

        if not specs:
            return ToolResult.fail(
                "No compilable sources found (gen/val/sol/brute/checker/interactor/communicator)",
                discovered=[],
            )

        exe_ext = get_exe_extension()
        for spec in specs:
            bin_rel = f"{os.path.splitext(spec.source)[0]}{exe_ext}"
            old_bin = safe_resolve_under_root(problem_dir, bin_rel)
            if old_bin is None:
                return ToolResult.fail(f"binary output path escapes problem_dir: {bin_rel}")
            if os.path.isfile(old_bin):
                try:
                    os.remove(old_bin)
                except OSError:
                    pass

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
