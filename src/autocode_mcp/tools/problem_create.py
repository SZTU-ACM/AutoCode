"""Problem creation tool (``problem_create``)."""

from __future__ import annotations

import os
import shutil
from typing import Any

from pydantic import ValidationError

from ..utils.compiler import detect_compiler_info_async
from ..workflow import (
    OracleMode,
    ProblemType,
    ProtocolMode,
    default_manifest,
    manifest_path,
    save_manifest,
)
from .base import Tool, ToolResult, input_schema_from_model
from .problem_helpers import _facade_attr
from .schemas import ProblemCreateInput


class ProblemCreateTool(Tool):
    """创建题目目录结构。"""

    @property
    def name(self) -> str:
        return "problem_create"

    @property
    def description(self) -> str:
        return """创建新题目的目录结构。

        创建标准的竞赛编程题目目录：
        - files/: testlib.h, gen.cpp, val.cpp
        - solutions/: sol.cpp, brute.cpp
        - statements/: README.md
        - tests/: 测试数据

        同时复制 testlib.h 模板文件。
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemCreateInput)

    async def execute(
        self,
        problem_dir: str,
        problem_name: str,
        interactive: bool = False,
        problem_type: ProblemType | None = None,
        protocol_mode: ProtocolMode | None = None,
        oracle_mode: OracleMode = "brute",
        time_limit_ms: int | None = None,
        memory_limit_mb: int | None = None,
        overwrite: bool = False,
    ) -> ToolResult:
        """执行题目目录创建。"""
        if problem_type is not None and interactive and problem_type != "interactive":
            return ToolResult.fail(
                f"conflicting arguments: problem_type={problem_type!r} cannot be combined with interactive=True"
            )
        if not isinstance(overwrite, bool):
            return ToolResult.fail("overwrite must be a boolean")
        if time_limit_ms is not None and (isinstance(time_limit_ms, bool) or not isinstance(time_limit_ms, int) or time_limit_ms <= 0):
            return ToolResult.fail("time_limit_ms must be a positive integer (milliseconds)")
        if memory_limit_mb is not None and (isinstance(memory_limit_mb, bool) or not isinstance(memory_limit_mb, int) or memory_limit_mb <= 0):
            return ToolResult.fail("memory_limit_mb must be a positive integer (MB)")
        existing_manifest = manifest_path(problem_dir)
        autocode_dir_path = os.path.join(problem_dir, ".autocode")
        for check_p in (
            autocode_dir_path,
            str(existing_manifest),
            os.path.join(autocode_dir_path, ".gitignore"),
            os.path.join(problem_dir, "files"),
            os.path.join(problem_dir, "files", "testlib.h"),
        ):
            if os.path.islink(check_p):
                return ToolResult.fail(f"refusing to write through symlink: {check_p}")
        if os.path.isdir(existing_manifest):
            return ToolResult.fail(f"manifest path is a directory: {existing_manifest}")
        existed_before = os.path.lexists(existing_manifest)
        if existed_before and not overwrite:
            return ToolResult.fail(
                f"manifest.json already exists at {existing_manifest}; refusing to overwrite existing problem configuration without overwrite=true",
                existing_manifest_path=str(existing_manifest),
            )
        resolved_type: ProblemType = (
            problem_type if problem_type is not None else ("interactive" if interactive else "standard")
        )
        is_interactive = resolved_type == "interactive"
        is_communication = resolved_type == "communication"
        manifest_overrides: dict[str, Any] = {}
        if time_limit_ms is not None:
            manifest_overrides["time_limit_ms"] = time_limit_ms
        if memory_limit_mb is not None:
            manifest_overrides["memory_limit_mb"] = memory_limit_mb
        try:
            manifest = default_manifest(
                problem_name=problem_name,
                interactive=is_interactive,
                problem_type=resolved_type,
                protocol_mode=protocol_mode,
                oracle_mode=oracle_mode,
                **manifest_overrides,
            )
        except (ValidationError, ValueError) as exc:
            return ToolResult.fail(f"invalid problem configuration: {exc}")
        # 创建目录结构
        directories = [
            problem_dir,
            os.path.join(problem_dir, "files"),
            os.path.join(problem_dir, "solutions"),
            os.path.join(problem_dir, "statements"),
            os.path.join(problem_dir, "tests"),
        ]

        created_dirs = []
        for dir_path in directories:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)
                created_dirs.append(dir_path)

        # 运行期副产物统一收纳到 .autocode/，并用其内部的 .gitignore（`*`）自忽略，
        # `*` 同时忽略 .gitignore 自身，因此整个 .autocode/ 在 git status 中零痕迹，
        # 无需在题目根再生成 .gitignore。
        autocode_dir = os.path.join(problem_dir, ".autocode")
        try:
            os.makedirs(autocode_dir, exist_ok=True)
        except OSError:
            pass
        gitignore_path = os.path.join(autocode_dir, ".gitignore")
        try:
            if not os.path.exists(gitignore_path):
                with open(gitignore_path, "w", encoding="utf-8") as f:
                    f.write("# AutoCode state — self-ignored\n*\n")
        except OSError:
            pass

        # 复制 testlib.h
        from .. import TEMPLATES_DIR
        template_testlib = os.path.join(TEMPLATES_DIR, "testlib.h")

        if os.path.exists(template_testlib):
            dest_testlib = os.path.join(problem_dir, "files", "testlib.h")
            shutil.copy2(template_testlib, dest_testlib)
        else:
            return ToolResult.fail(
                f"testlib.h template not found at {template_testlib}. "
                "Please download from https://github.com/MikeMirzayanov/testlib "
                "and place it in src/autocode_mcp/templates/."
            )

        if is_interactive:
            template_interactor = os.path.join(TEMPLATES_DIR, "interactor_template.cpp")
            dest_interactor = os.path.join(problem_dir, "files", "interactor.cpp")
            if os.path.exists(template_interactor) and not os.path.exists(dest_interactor):
                shutil.copy2(template_interactor, dest_interactor)
        elif is_communication:
            comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
            if comm_mode == "grader_linked":
                for tmpl_name, dest_name in (
                    ("grader_template.cpp", "grader.cpp"),
                    ("problem_h_template.h", "problem.h"),
                ):
                    src_tmpl = os.path.join(TEMPLATES_DIR, tmpl_name)
                    dest_file = os.path.join(problem_dir, "files", dest_name)
                    if os.path.exists(src_tmpl) and not os.path.exists(dest_file):
                        shutil.copy2(src_tmpl, dest_file)
            else:
                tmpl_comm = os.path.join(TEMPLATES_DIR, "communicator_run_twice_template.cpp")
                fallback_tmpl = os.path.join(TEMPLATES_DIR, "interactor_template.cpp")
                src_tmpl = tmpl_comm if os.path.exists(tmpl_comm) else fallback_tmpl
                dest_comm = os.path.join(problem_dir, "files", "communicator.cpp")
                if os.path.exists(src_tmpl) and not os.path.exists(dest_comm):
                    shutil.copy2(src_tmpl, dest_comm)
        else:
            template_validator = os.path.join(TEMPLATES_DIR, "validator_template.cpp")
            dest_validator = os.path.join(problem_dir, "files", "val.cpp")
            if os.path.exists(template_validator) and not os.path.exists(dest_validator):
                shutil.copy2(template_validator, dest_validator)

        template_generator = os.path.join(TEMPLATES_DIR, "generator_template.cpp")
        dest_generator = os.path.join(problem_dir, "files", "gen.cpp")
        if os.path.exists(template_generator) and not os.path.exists(dest_generator):
            shutil.copy2(template_generator, dest_generator)

        # 创建基础 README.md
        readme_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(readme_path):
            with open(readme_path, "w", encoding="utf-8") as f:
                if is_communication:
                    comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
                    comm_isolation_note = (
                        f"- 通信模式：`{comm_mode}`（与 `files/grader.cpp` 与 `files/problem.h` 链接编译，禁止使用全局变量跨阶段偷传状态）。\n"
                        if comm_mode == "grader_linked"
                        else f"- 通信模式：`{comm_mode}`（各阶段运行在相互隔离的独立进程中，禁止通过全局变量或外部文件传递状态）。\n"
                    )
                    f.write(
                        f"# {problem_name}\n\n"
                        "## 时间限制与空间限制\n\n"
                        f"- 时间限制：{manifest.time_limit_ms} ms\n"
                        f"- 空间限制：{manifest.memory_limit_mb} MB\n\n"
                        "## 题目背景（可选）\n\n"
                        "如不需要背景，可删除本节。\n\n"
                        "## 题目描述\n\n"
                        "本题为通信题，请完整描述编码/发送端与解码/接收端各自获知的信息与还原目标。\n\n"
                        "## 输入格式\n\n"
                        "请明确第一阶段与第二阶段的输入格式、隐藏参数范围与总规模约束。\n\n"
                        "## 输出格式\n\n"
                        "请完整定义编码消息格式、字符集/比特数上限及最终还原答案格式。\n\n"
                        "## 通信协议\n\n"
                        f"{comm_isolation_note}"
                        "- 明确第一阶段（Encoder / Alice）与第二阶段（Decoder / Bob）的入口标识或函数签名。\n"
                        "- 明确通信预算上限（最大比特数 / 字节数 / 轮次）及超限判定规则。\n"
                        "- 明确信道变换/噪声/对抗模型（若为无噪信道亦须明示）。\n"
                        "- 明确非法格式、提前 EOF、超预算与通信失败时的判分规则。\n\n"
                        "## 样例\n\n"
                        "通信题样例应写出完整的两阶段 transcript（标明 Pass 1 / Channel / Pass 2）。\n\n"
                        "```text\n"
                        "（请填写两阶段样例通信过程）\n"
                        "```\n\n"
                        "## 说明\n\n"
                        "所有样例解释统一写在本节；只需解释有代表性的样例即可。\n"
                    )
                elif is_interactive:
                    f.write(
                        f"# {problem_name}\n\n"
                        "## 时间限制与空间限制\n\n"
                        f"- 时间限制：{manifest.time_limit_ms} ms\n"
                        f"- 空间限制：{manifest.memory_limit_mb} MB\n\n"
                        "## 题目背景（可选）\n\n"
                        "如不需要背景，可删除本节。\n\n"
                        "## 题目描述\n\n"
                        "请完整描述选手需要完成的目标、隐藏状态含义、判定标准与关键术语。\n\n"
                        "## 输入格式\n\n"
                        "本题为交互题，选手程序不会获得传统静态输入。请在本节明确交互器内部输入或隐藏参数的范围、随机性来源与总规模约束。\n\n"
                        "## 输出格式\n\n"
                        "请完整定义选手可输出的每一种命令、参数范围、最终答案格式，以及每次输出后的 flush 要求。\n\n"
                        "## 交互协议\n\n"
                        "- 明确交互开始时 judge 首先输出什么，或是否等待选手先查询。\n"
                        "- 明确每种查询格式、judge 响应格式、响应取值含义和查询次数上限。\n"
                        "- 明确最终答案格式、输出最终答案后是否必须立即结束程序。\n"
                        "- 明确非法格式、越界参数、查询超限、提前 EOF、未及时 flush、读到错误响应时的判定。\n"
                        "- 若 judge 可能自适应回答，必须说明哪些不变量始终成立。\n\n"
                        "## 样例\n\n"
                        "交互题样例应写成一次 transcript，而不是传统输入输出。请标明哪些行来自 judge，哪些行来自选手。\n\n"
                        "```text\n"
                        "（请填写样例交互过程）\n"
                        "```\n\n"
                        "## 说明\n\n"
                        "所有样例解释统一写在本节；只需解释有代表性的样例即可。\n"
                    )
                else:
                    f.write(
                        f"# {problem_name}\n\n"
                        "## 时间限制与空间限制\n\n"
                        f"- 时间限制：{manifest.time_limit_ms} ms\n"
                        f"- 空间限制：{manifest.memory_limit_mb} MB\n\n"
                        "## 题目背景（可选）\n\n"
                        "如不需要背景，可删除本节。\n\n"
                        "## 题目描述\n\n"
                        "请完整描述任务目标、判定标准与关键术语。\n\n"
                        "## 输入格式\n\n"
                        "请完整给出输入结构，并在本节明确所有变量范围与总规模约束。\n\n"
                        "## 输出格式\n\n"
                        "请明确输出内容与格式细节。\n\n"
                        "## 样例\n\n"
                        "### 样例输入 #1\n\n"
                        "```\n"
                        "（请填写样例输入）\n"
                        "```\n\n"
                        "### 样例输出 #1\n\n"
                        "```\n"
                        "（请填写样例输出）\n"
                        "```\n\n"
                        "## 说明\n\n"
                        "所有样例解释统一写在本节；只需解释有代表性的样例即可。\n"
                    )

        tutorial_path = os.path.join(problem_dir, "statements", "tutorial.md")
        if not os.path.exists(tutorial_path):
            with open(tutorial_path, "w", encoding="utf-8") as f:
                f.write(
                    f"# {problem_name} 题解\n\n"
                    "## 思路概述\n\n请补充核心思路。\n\n"
                    "## 正确性说明\n\n请补充关键证明。\n\n"
                    "## 复杂度分析\n\n请补充时间与空间复杂度。\n"
                )

        manifest_file = save_manifest(problem_dir, manifest)
        compiler_info = await _facade_attr("detect_compiler_info_async", detect_compiler_info_async)(manifest.compiler or "g++")
        extra_meta: dict[str, Any] = {"compiler_info": compiler_info}
        if compiler_info.get("is_apple_clang") and compiler_info.get("warning"):
            extra_meta["compiler_warning"] = str(compiler_info["warning"])
        if existed_before:
            extra_meta["overwritten_existing_manifest"] = True
            extra_meta["warning"] = "Existing .autocode/manifest.json was overwritten because overwrite=true was passed"

        return ToolResult.ok(
            problem_dir=problem_dir,
            problem_name=problem_name,
            problem_type=manifest.problem_type,
            oracle_mode=manifest.oracle_mode,
            protocol_mode=manifest.protocol.mode if manifest.protocol else None,
            interactive=manifest.interactive,
            time_limit_ms=manifest.time_limit_ms,
            memory_limit_mb=manifest.memory_limit_mb,
            manifest_path=str(manifest_file),
            created_directories=created_dirs,
            **extra_meta,
            message=f"Created problem directory: {problem_dir}",
        )
