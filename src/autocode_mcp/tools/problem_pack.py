"""Polygon / Hydro / QOJ problem packaging tool (``problem_pack_polygon``)."""

from __future__ import annotations

import os
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from ..runtime_store import AUDIT, TEST_MANIFEST, WORKFLOW, get_section
from ..utils.answer_ext import normalize_answer_ext
from ..utils.communication_runner import (
    is_safe_stem,
    resolve_communicator_source_existing,
    resolve_communicator_source_rel,
    resolve_grader_source_existing,
    safe_resolve_under_root,
)
from ..utils.exporters import (
    ExportDataMode,
    export_cms,
    export_hydro,
    export_icpc_kattis,
    export_polygon,
    export_qoj,
    map_tests_to_subtasks,
    verify_exported_packages,
)
from ..workflow import (
    AutoCodeManifest,
    check_gates,
    default_manifest,
    load_manifest,
    load_workflow_state,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_protocol_judge,
    manifest_uses_testlib_checker,
)
from ..workflow.hashes import is_statement_drifted_for_audit, is_statement_hash_drifted
from .base import Tool, ToolResult, input_schema_from_model
from .problem_helpers import _LIMIT_STRATEGY_TYPES
from .schemas import ProblemPackPolygonInput

_WRONG_SOLUTION_PREFIXES = (
    "wrong_",
    "wrong-",
    "wa_",
    "wa-",
    "tle_",
    "tle-",
    "mle_",
    "mle-",
    "re_",
    "re-",
)


def _is_wrong_solution_filename(fname: str) -> bool:
    if not fname.endswith(".cpp"):
        return False
    return fname == "bf.cpp" or fname.startswith(_WRONG_SOLUTION_PREFIXES)


def _pack_polygon_files(problem_dir: str) -> dict[str, list[str]]:
    """创建 Polygon 标准目录并拷贝源文件，返回操作汇总。"""
    results: dict[str, list[str]] = {
        "files_copied": [],
        "files_removed": [],
        "directories_created": [],
    }
    for dir_name in ["files", "solutions", "statements", "scripts"]:
        dir_path = os.path.join(problem_dir, dir_name)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)
            results["directories_created"].append(dir_name)

    files_dir = os.path.join(problem_dir, "files")
    for src in [
        "testlib.h",
        "gen.cpp",
        "val.cpp",
        "interactor.cpp",
        "communicator.cpp",
        "grader.cpp",
        "problem.h",
    ]:
        src_path = os.path.join(problem_dir, src)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(files_dir, src))
            results["files_copied"].append(f"{src} -> files/{src}")

    solutions_dir = os.path.join(problem_dir, "solutions")
    for src in ["sol.cpp", "brute.cpp"]:
        src_path = os.path.join(problem_dir, src)
        if os.path.exists(src_path):
            shutil.copy2(src_path, os.path.join(solutions_dir, src))
            results["files_copied"].append(f"{src} -> solutions/{src}")

    readme_src = os.path.join(problem_dir, "README.md")
    if os.path.exists(readme_src):
        shutil.copy2(readme_src, os.path.join(problem_dir, "statements", "README.md"))
        results["files_copied"].append("README.md -> statements/README.md")

    return results


def _add_executable(parent: ET.Element, source_path: str) -> ET.Element:
    exe = ET.SubElement(parent, "executable")
    ET.SubElement(exe, "source", {"path": source_path})
    return exe


def _build_problem_xml(
    problem_dir: str,
    *,
    time_limit_ms: int,
    memory_limit_bytes: int,
    test_count: int,
    answer_ext: str,
    is_interactive_problem: bool,
    has_checker: bool,
    has_interactor: bool,
    manifest_model: AutoCodeManifest | None = None,
    in_files: list[str] | None = None,
    tests_manifest: dict[str, Any] | None = None,
) -> str:
    """基于 xml.etree.ElementTree 生成 problem.xml 内容。"""
    problem_name = os.path.basename(os.path.normpath(problem_dir))
    is_communication_problem = manifest_is_communication(manifest_model)
    include_validator = (not is_interactive_problem and not is_communication_problem) or os.path.isfile(
        os.path.join(problem_dir, "files", "val.cpp")
    )

    problem = ET.Element("problem", {"revision": "1", "short-name": problem_name})
    names = ET.SubElement(problem, "names")
    ET.SubElement(names, "name", {"language": "chinese", "value": problem_name})

    stmt_rel = (
        manifest_model.statement_path.replace("\\", "/")
        if (manifest_model is not None and manifest_model.statement_path)
        else "statements/README.md"
    )
    statements = ET.SubElement(problem, "statements")
    ET.SubElement(
        statements,
        "statement",
        {
            "charset": "UTF-8",
            "language": "chinese",
            "mathjax": "true",
            "path": stmt_rel,
            "type": "application/x-tex",
        },
    )

    in_pat = "tests/%02d.in"
    ans_pat = f"tests/%02d{answer_ext}"
    if in_files:
        stems = [os.path.splitext(os.path.basename(f))[0] for f in in_files]
        parsed_stems = [re.fullmatch(r"([A-Za-z_-]*)(\d+)", s) for s in stems]
        if all(m is not None for m in parsed_stems):
            prefixes = {m.group(1) for m in parsed_stems if m is not None}
            if len(prefixes) == 1:
                pfx = next(iter(prefixes))
                num_strs = [m.group(2) for m in parsed_stems if m is not None]
                nums = sorted(int(ns) for ns in num_strs)
                if nums == list(range(1, len(nums) + 1)):
                    first_one = next((ns for ns in num_strs if int(ns) == 1), "01")
                    width = len(first_one) if (len(first_one) > 1 and first_one.startswith("0")) else 0
                    fmt = f"%0{width}d" if width >= 2 else "%d"
                    in_pat = f"tests/{pfx}{fmt}.in"
                    ans_pat = f"tests/{pfx}{fmt}{answer_ext}"

    judging = ET.SubElement(problem, "judging")
    testset = ET.SubElement(judging, "testset", {"name": "tests"})
    ET.SubElement(testset, "time-limit").text = str(time_limit_ms)
    ET.SubElement(testset, "memory-limit").text = str(memory_limit_bytes)
    ET.SubElement(testset, "test-count").text = str(test_count)
    ET.SubElement(testset, "input-path-pattern").text = in_pat
    ET.SubElement(testset, "answer-path-pattern").text = ans_pat
    if has_checker:
        ET.SubElement(testset, "checker", {"type": "testlib", "path": "files/checker.cpp"})
    comm_mode = (
        (manifest_model.protocol.mode if manifest_model.protocol else "run_twice")
        if (is_communication_problem and manifest_model is not None)
        else None
    )
    comm_rel: str | None = None
    grader_rel: str | None = None
    header_rel: str | None = None
    if is_communication_problem and manifest_model is not None:
        if comm_mode == "grader_linked":
            grader_rel = resolve_grader_source_existing(problem_dir, manifest_model) or (
                manifest_model.protocol.grader_file
                if (manifest_model.protocol and manifest_model.protocol.grader_file)
                else "files/grader.cpp"
            )
            cand_hdr = (
                manifest_model.protocol.header_file
                if (manifest_model.protocol and manifest_model.protocol.header_file)
                else "files/problem.h"
            )
            hdr_resolved = safe_resolve_under_root(problem_dir, cand_hdr) if cand_hdr else None
            header_rel = (
                os.path.relpath(hdr_resolved, os.path.realpath(problem_dir)).replace("\\", "/")
                if (hdr_resolved is not None and os.path.isfile(hdr_resolved))
                else None
            )
            comm_attrs = {"mode": "grader_linked", "grader": grader_rel}
            if header_rel:
                comm_attrs["header"] = header_rel
            ET.SubElement(testset, "communication", comm_attrs)
        else:
            comm_rel = (
                resolve_communicator_source_existing(problem_dir, manifest_model)
                or resolve_communicator_source_rel(manifest_model).replace("\\", "/")
            )
            ET.SubElement(testset, "interactor", {"type": "testlib", "path": comm_rel})
            comm_attrs = {"mode": comm_mode or "run_twice"}
            if manifest_model.protocol and comm_mode == "run_twice":
                comm_attrs["passes"] = str(manifest_model.protocol.passes)
            ET.SubElement(testset, "communication", comm_attrs)
    elif has_interactor:
        ET.SubElement(testset, "interactor", {"type": "testlib", "path": "files/interactor.cpp"})

    if manifest_model is not None and manifest_model.subtasks:
        resolved_in_files = (
            in_files if in_files is not None else [f"{i:02d}.in" for i in range(1, test_count + 1)]
        )
        st_map = map_tests_to_subtasks(resolved_in_files, manifest_model.subtasks, tests_manifest)
        index_to_group: dict[int, int] = {}
        for st in manifest_model.subtasks:
            for idx in st_map.get(st.id, []):
                if idx not in index_to_group:
                    index_to_group[idx] = st.id
        groups_el = ET.SubElement(testset, "groups")
        valid_subtask_ids = {st.id for st in manifest_model.subtasks}
        for st in manifest_model.subtasks:
            grp_el = ET.SubElement(
                groups_el,
                "group",
                {
                    "name": f"subtask_{st.id}",
                    "feedback-policy": "complete",
                    "points-policy": "complete-group",
                    "points": str(st.score),
                },
            )
            deps = [d for d in st.dependencies if d in valid_subtask_ids]
            if deps:
                deps_el = ET.SubElement(grp_el, "dependencies")
                for dep_id in deps:
                    ET.SubElement(deps_el, "dependency", {"group": f"subtask_{dep_id}"})
        tests_el = ET.SubElement(testset, "tests")
        for idx in range(1, test_count + 1):
            t_attrs: dict[str, str] = {"method": "manual"}
            owner = index_to_group.get(idx)
            if owner is not None:
                t_attrs["group"] = f"subtask_{owner}"
            ET.SubElement(tests_el, "test", t_attrs)

    files = ET.SubElement(problem, "files")
    resources = ET.SubElement(files, "resources")
    if os.path.isfile(os.path.join(problem_dir, "files", "testlib.h")):
        ET.SubElement(resources, "file", {"path": "files/testlib.h"})
    if grader_rel and os.path.isfile(os.path.join(problem_dir, grader_rel)):
        ET.SubElement(resources, "file", {"path": grader_rel, "type": "cpp.g++"})
    if header_rel and os.path.isfile(os.path.join(problem_dir, header_rel)):
        ET.SubElement(resources, "file", {"path": header_rel, "type": "h.g++"})

    executables = ET.SubElement(files, "executables")
    if os.path.isfile(os.path.join(problem_dir, "files", "gen.cpp")):
        _add_executable(executables, "files/gen.cpp")
    if include_validator and os.path.isfile(os.path.join(problem_dir, "files", "val.cpp")):
        _add_executable(executables, "files/val.cpp")
    if has_checker:
        _add_executable(executables, "files/checker.cpp")
    if comm_rel and os.path.isfile(os.path.join(problem_dir, comm_rel)):
        _add_executable(executables, comm_rel)
    elif has_interactor:
        _add_executable(executables, "files/interactor.cpp")

    assets = ET.SubElement(problem, "assets")
    solutions = ET.SubElement(assets, "solutions")
    role_to_tag = {
        "main": "main",
        "brute": "rejected",
        "wrong": "rejected",
        "reference": "accepted",
    }
    part_sols = (
        list(manifest_model.protocol.participant_solutions)
        if (manifest_model is not None and manifest_model.protocol is not None)
        else []
    )
    if manifest_model and (manifest_model.solutions or part_sols):
        emitted_sol_paths: set[str] = set()
        for sol_entry in manifest_model.solutions:
            if (
                part_sols
                and sol_entry.role == "main"
                and sol_entry.path == "solutions/sol.cpp"
                and not os.path.isfile(os.path.join(problem_dir, "solutions", "sol.cpp"))
            ):
                continue
            if (
                sol_entry.role == "brute"
                and not os.path.isfile(os.path.join(problem_dir, sol_entry.path))
            ):
                continue
            if sol_entry.role == "wrong" and sol_entry.expected == "pass":
                tag = "accepted"
            else:
                tag = role_to_tag.get(sol_entry.role, "rejected")
            sol_node = ET.SubElement(solutions, "solution", {"tag": tag})
            ET.SubElement(sol_node, "source", {"path": sol_entry.path})
            emitted_sol_paths.add(sol_entry.path)
        for part_name in part_sols:
            if is_safe_stem(part_name):
                part_path = f"solutions/{part_name}.cpp"
                if part_path not in emitted_sol_paths:
                    pnode = ET.SubElement(solutions, "solution", {"tag": "main"})
                    ET.SubElement(pnode, "source", {"path": part_path})
                    emitted_sol_paths.add(part_path)
    else:
        main = ET.SubElement(solutions, "solution", {"tag": "main"})
        ET.SubElement(main, "source", {"path": "solutions/sol.cpp"})
        if os.path.isfile(os.path.join(problem_dir, "solutions", "brute.cpp")):
            rejected = ET.SubElement(solutions, "solution", {"tag": "rejected"})
            ET.SubElement(rejected, "source", {"path": "solutions/brute.cpp"})

    ET.indent(problem)
    return '<?xml version="1.0" encoding="utf-8" standalone="no"?>\n' + ET.tostring(
        problem, encoding="unicode"
    )


class ProblemPackPolygonTool(Tool):
    """打包为 Polygon 格式。"""

    @property
    def name(self) -> str:
        return "problem_pack_polygon"

    @property
    def description(self) -> str:
        return """将题目打包为 Polygon 格式，并可选导出 Hydro 与 QOJ/UOJ 格式。

        支持通过 export_formats 指定导出目标（默认 ["polygon"]，可选 "hydro"、"qoj"）：
        - polygon: 整理到标准目录结构并生成 problem.xml（含 subtask groups/tests）
        - hydro: 导出到 exports/hydro/（problem.yaml、problem_zh.md、testdata/config.yaml）
        - qoj: 导出到 exports/qoj/（problem.conf、statement.md、data/1.in..N.in）
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemPackPolygonInput)

    async def execute(
        self,
        problem_dir: str,
        time_limit: int | None = None,
        time_limit_ms: int | None = None,
        allow_time_limit_override: bool = False,
        memory_limit: int | None = None,
        export_formats: list[str] | None = None,
        export_data_mode: ExportDataMode = "hardlink",
    ) -> ToolResult:
        """执行 Polygon / Hydro / QOJ / ICPC-Kattis / CMS 打包导出。"""
        if not os.path.exists(problem_dir):
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        allowed_data_modes = {"copy", "hardlink", "zip"}
        if not isinstance(export_data_mode, str) or export_data_mode not in allowed_data_modes:
            return ToolResult.fail(
                f"unsupported export_data_mode {export_data_mode!r}; expected one of: copy, hardlink, zip"
            )

        allowed_formats = {"polygon", "hydro", "qoj", "icpc", "kattis", "icpc_kattis", "cms"}
        if export_formats is None:
            formats: list[str] = ["polygon"]
        else:
            if not isinstance(export_formats, list) or not export_formats:
                return ToolResult.fail(
                    "export_formats must be a non-empty list of: polygon, hydro, qoj, icpc, kattis, cms"
                )
            formats = []
            for fmt in export_formats:
                if not isinstance(fmt, str) or fmt not in allowed_formats:
                    return ToolResult.fail(
                        f"unsupported export format {fmt!r}; expected one of: polygon, hydro, qoj, icpc, kattis, cms"
                    )
                if fmt not in formats:
                    formats.append(fmt)

        tests_dir = os.path.join(problem_dir, "tests")
        if not os.path.isdir(tests_dir):
            return ToolResult.fail("tests directory not found, run problem_generate_tests first")
        def _test_sort_key(filename: str) -> tuple[int, int, str]:
            stem = os.path.splitext(filename)[0]
            return (0, int(stem), filename) if stem.isdigit() else (1, 0, filename)

        in_files = sorted(
            (f for f in os.listdir(tests_dir) if f.endswith(".in")),
            key=_test_sort_key,
        )
        if not in_files:
            return ToolResult.fail("no test input files found, run problem_generate_tests first")

        answer_ext = ".ans"
        _test_manifest = get_section(problem_dir, TEST_MANIFEST)
        if isinstance(_test_manifest, dict):
            answer_ext = normalize_answer_ext(str(_test_manifest.get("answer_ext", ".ans"))) or ".ans"

        statement_path = os.path.join(problem_dir, "statements", "README.md")
        if not os.path.exists(statement_path):
            return ToolResult.fail("statement file missing: statements/README.md")

        # 门禁配置统一从 Pydantic manifest 读取；存在但损坏即阻断，缺失则回退默认门禁（保持向后兼容）。
        try:
            manifest_model = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(
                f"invalid or unreadable manifest.json: {exc}",
                stage="problem_pack_polygon",
                gate="manifest_readable",
                required=True,
                actual=False,
            )
        if manifest_model is None:
            manifest_model = default_manifest(os.path.basename(os.path.normpath(problem_dir)))

        # 交互题与通信题允许无 .ans 文件（或由导出器忽略空/缺失 .ans）；标准题与 SPJ 强制要求成对答案文件
        if not (manifest_is_interactive(manifest_model) or manifest_is_communication(manifest_model)):
            missing_answers = [
                in_file for in_file in in_files if not os.path.exists(
                    os.path.join(tests_dir, f"{os.path.splitext(in_file)[0]}{answer_ext}")
                )
            ]
            if missing_answers:
                return ToolResult.fail(
                    "missing answer files for some tests",
                    missing_answer_inputs=missing_answers,
                )

        sol_source = os.path.join(problem_dir, "solutions", "sol.cpp")
        root_sol_source = os.path.join(problem_dir, "sol.cpp")
        has_main_sol = os.path.exists(sol_source) or os.path.exists(root_sol_source)
        if (
            not has_main_sol
            and manifest_is_communication(manifest_model)
            and manifest_model.protocol is not None
            and manifest_model.protocol.participant_solutions
        ):
            resolved_parts = [
                safe_resolve_under_root(problem_dir, f"solutions/{name}.cpp")
                for name in manifest_model.protocol.participant_solutions
                if is_safe_stem(name)
            ]
            if (
                len(resolved_parts) == len(manifest_model.protocol.participant_solutions)
                and all(p is not None and os.path.isfile(p) for p in resolved_parts)
            ):
                has_main_sol = True
        if not has_main_sol:
            return ToolResult.fail("main solution source missing: solutions/sol.cpp")

        quality_gates_cfg = manifest_model.quality_gates
        audit_gates_cfg = manifest_model.audit_gates
        require_tests_verified = quality_gates_cfg.require_tests_verified
        min_limit_case_ratio = min(1.0, max(0.0, quality_gates_cfg.min_limit_case_ratio))
        require_full_audit = audit_gates_cfg.require_full_audit
        is_interactive_problem = manifest_is_interactive(manifest_model)
        is_communication_problem = manifest_is_communication(manifest_model)
        is_protocol_problem = manifest_uses_protocol_judge(manifest_model)
        comm_needs_communicator = is_communication_problem and (
            manifest_model.protocol is None or manifest_model.protocol.mode != "grader_linked"
        )

        interactor_cpp = os.path.join(problem_dir, "files", "interactor.cpp")
        root_interactor_cpp = os.path.join(problem_dir, "interactor.cpp")
        if is_interactive_problem and not os.path.exists(interactor_cpp) and not os.path.exists(root_interactor_cpp):
            return ToolResult.fail("interactive problem requires files/interactor.cpp")
        if is_communication_problem:
            if comm_needs_communicator:
                comm_rel = resolve_communicator_source_rel(manifest_model)
                if resolve_communicator_source_existing(problem_dir, manifest_model) is None:
                    return ToolResult.fail(f"communication problem requires {comm_rel}")
            else:
                grader_rel = (
                    manifest_model.protocol.grader_file
                    if (manifest_model.protocol and manifest_model.protocol.grader_file)
                    else "files/grader.cpp"
                )
                if resolve_grader_source_existing(problem_dir, manifest_model) is None:
                    return ToolResult.fail(f"grader_linked communication problem requires {grader_rel}")

        workflow_state = get_section(problem_dir, WORKFLOW)
        if not isinstance(workflow_state, dict):
            if require_tests_verified:
                return ToolResult.fail(
                    "workflow state missing, run problem_verify_tests first",
                    stage="problem_pack_polygon",
                    gate="require_tests_verified",
                    required=True,
                    actual=False,
                )
            workflow_state = {}
        if require_tests_verified and not bool(workflow_state.get("tests_verified", False)):
            return ToolResult.fail(
                "tests are not verified, run problem_verify_tests first",
                stage="problem_pack_polygon",
                gate="require_tests_verified",
                required=True,
                actual=False,
                tests_verified=False,
            )

        audit_state = get_section(problem_dir, AUDIT)
        synced_workflow = (
            load_workflow_state(problem_dir) if isinstance(get_section(problem_dir, WORKFLOW), dict) else workflow_state
        )
        stmt_drifted = is_statement_drifted_for_audit(
            problem_dir,
            audit_state if isinstance(audit_state, dict) else None,
            synced_workflow if isinstance(synced_workflow, dict) else None,
        )
        stmt_hash_drifted = is_statement_hash_drifted(
            problem_dir,
            audit_state if isinstance(audit_state, dict) else None,
            workflow_state if isinstance(workflow_state, dict) else None,
        )
        if quality_gates_cfg.require_validation_passed and stmt_hash_drifted:
            return ToolResult.fail(
                "statement has changed since last validation, run problem_validate first",
                stage="problem_pack_polygon",
                gate="statement_fresh",
                required=True,
                actual=False,
            )

        verify_signals = synced_workflow.get("verify_signals", {}) if isinstance(synced_workflow, dict) else {}
        if not isinstance(verify_signals, dict):
            verify_signals = {}
        if require_full_audit:
            full_audit = audit_state.get("full_audit", {}) if isinstance(audit_state, dict) else {}
            if not isinstance(full_audit, dict):
                full_audit = {}
            full_audit_passed = bool(audit_state.get("full_audit_passed", False)) if isinstance(audit_state, dict) else False
            try:
                full_audit_blocking_count = int(full_audit.get("blocking_issue_count", 1))
            except (TypeError, ValueError):
                full_audit_blocking_count = 1
            if (
                stmt_drifted
                or full_audit.get("mode") != "full"
                or full_audit.get("decision") != "go"
                or not full_audit_passed
                or full_audit_blocking_count != 0
            ):
                return ToolResult.fail(
                    "full audit is required, run problem_audit with mode=full first",
                    stage="problem_pack_polygon",
                    gate="require_full_audit",
                    required=True,
                    actual=full_audit,
                )
            audit_signals = full_audit.get("quality_signals", {})
            if not isinstance(audit_signals, dict):
                audit_signals = {}
            spj_checker = manifest_uses_testlib_checker(manifest_model)
            audit_signal_rules = [
                ("duplicate_inputs", True),
                ("scale_distribution", True),
                ("purpose_coverage", True),
                (
                    "validator_self_test",
                    audit_gates_cfg.require_validator_self_test and not is_protocol_problem,
                ),
                ("checker_self_test", audit_gates_cfg.require_checker_self_test and spj_checker),
                (
                    "interactor_self_test",
                    audit_gates_cfg.require_interactor_self_test
                    and (is_interactive_problem or comm_needs_communicator),
                ),
            ]
            for signal_name, required in audit_signal_rules:
                if not required:
                    continue
                signal_data = audit_signals.get(signal_name, {})
                if not isinstance(signal_data, dict):
                    signal_data = {}
                if not bool(signal_data.get("executed")) or not bool(signal_data.get("passed")):
                    return ToolResult.fail(
                        f"full audit signal `{signal_name}` not satisfied, run problem_audit first",
                        stage="problem_pack_polygon",
                        gate=f"full_audit.{signal_name}",
                        required=True,
                        actual=signal_data,
                    )

        # 验证信号类质量门禁委托 workflow.guard.check_gates 判定，避免与 problem_audit 双份实现。
        for issue in check_gates(manifest_model, workflow_state, verify_signals):
            if issue.gate == "tests_verified":
                # tests_verified 已在上面按文件存在/可读状态给出更精确报错，此处兜底跳过。
                continue
            return ToolResult.fail(
                f"verification signal `{issue.gate}` not satisfied, run problem_verify_tests first",
                stage="problem_pack_polygon",
                gate=issue.gate,
                required=True,
                actual=verify_signals.get(issue.gate, {}),
            )

        limit_ratio_from_manifest = None
        tests_manifest = get_section(problem_dir, TEST_MANIFEST)
        if isinstance(tests_manifest, dict):
            tests = tests_manifest.get("tests", [])
            if isinstance(tests, list) and tests:
                total = len(tests)
                limit_count = sum(
                    1
                    for item in tests
                    if isinstance(item, dict) and str(item.get("type_param")) in _LIMIT_STRATEGY_TYPES
                )
                limit_ratio_from_manifest = limit_count / total
        if limit_ratio_from_manifest is not None and limit_ratio_from_manifest < min_limit_case_ratio:
            return ToolResult.fail(
                "limit case ratio is below quality_gates.min_limit_case_ratio",
                stage="problem_pack_polygon",
                gate="min_limit_case_ratio",
                required=min_limit_case_ratio,
                actual=limit_ratio_from_manifest,
                limit_case_ratio=limit_ratio_from_manifest,
                min_limit_case_ratio=min_limit_case_ratio,
            )

        # 单位转换与合法性校验：显式传参优先；缺省时回读 manifest 中的 time_limit_ms / memory_limit_mb
        if time_limit_ms is not None:
            if isinstance(time_limit_ms, bool) or not isinstance(time_limit_ms, int) or time_limit_ms <= 0:
                return ToolResult.fail("time_limit_ms must be a positive integer (milliseconds)")
        if time_limit is not None:
            if isinstance(time_limit, bool) or not isinstance(time_limit, int) or time_limit <= 0:
                return ToolResult.fail("time_limit must be a positive integer (seconds)")
            if time_limit_ms is not None and time_limit * 1000 != time_limit_ms:
                return ToolResult.fail(
                    f"Conflicting time_limit={time_limit}s and time_limit_ms={time_limit_ms}ms provided; "
                    "pass only one or matching values"
                )
        if time_limit_ms is not None:
            effective_time_limit_ms = time_limit_ms
        elif time_limit is not None:
            if not allow_time_limit_override and time_limit > 60:
                return ToolResult.fail(
                    f"Suspicious time_limit={time_limit} seconds (> 60s). "
                    "time_limit expects SECONDS (e.g. 1 or 2); "
                    f"if you meant {time_limit}ms, pass time_limit_ms={time_limit} instead "
                    "(or set allow_time_limit_override=true)."
                )
            effective_time_limit_ms = time_limit * 1000
        else:
            effective_time_limit_ms = int(manifest_model.time_limit_ms)
            if effective_time_limit_ms <= 0:
                return ToolResult.fail("manifest.time_limit_ms must be positive")

        if not allow_time_limit_override:
            if effective_time_limit_ms > 60_000:
                return ToolResult.fail(
                    f"Suspicious effective time limit {effective_time_limit_ms}ms (> 60000ms). "
                    "Check units (time_limit is seconds, time_limit_ms is milliseconds) "
                    "or set allow_time_limit_override=true."
                )
            manifest_tl_ms = int(manifest_model.time_limit_ms) if manifest_model.time_limit_ms > 0 else 0
            if (time_limit is not None or time_limit_ms is not None) and manifest_tl_ms > 0:
                ratio = effective_time_limit_ms / float(manifest_tl_ms)
                if ratio > 10.0 or ratio < 0.1:
                    return ToolResult.fail(
                        f"Requested time limit ({effective_time_limit_ms}ms) deviates >10x from "
                        f"manifest.time_limit_ms ({manifest_tl_ms}ms). "
                        "Check seconds vs milliseconds (use time_limit_ms) or set allow_time_limit_override=true."
                    )
        if memory_limit is not None:
            if isinstance(memory_limit, bool) or not isinstance(memory_limit, int) or memory_limit <= 0:
                return ToolResult.fail("memory_limit must be a positive integer (MB)")
            memory_limit_mb = memory_limit
            memory_limit_bytes = memory_limit * 1024 * 1024
        else:
            memory_limit_mb = int(manifest_model.memory_limit_mb)
            if memory_limit_mb <= 0:
                return ToolResult.fail("manifest.memory_limit_mb must be positive")
            memory_limit_bytes = memory_limit_mb * 1024 * 1024

        tests_manifest_dict = tests_manifest if isinstance(tests_manifest, dict) else None
        results: dict[str, Any] = {
            "files_copied": [],
            "files_removed": [],
            "directories_created": [],
        }
        exports: dict[str, Any] = {}
        actual_test_count = len(in_files)

        if "polygon" in formats:
            # 1~4. 创建标准目录并拷贝源文件。
            results = _pack_polygon_files(problem_dir)

            # 5. 创建或刷新 problem.xml（基于 xml.etree.ElementTree 生成，避免手工拼接与陈旧配置残留）
            problem_xml = os.path.join(problem_dir, "problem.xml")
            xml_existed = os.path.exists(problem_xml)
            files_dir = os.path.join(problem_dir, "files")
            has_checker = os.path.isfile(os.path.join(files_dir, "checker.cpp"))
            has_interactor = is_interactive_problem and os.path.isfile(
                os.path.join(files_dir, "interactor.cpp")
            )
            xml_content = _build_problem_xml(
                problem_dir,
                time_limit_ms=effective_time_limit_ms,
                memory_limit_bytes=memory_limit_bytes,
                test_count=actual_test_count,
                answer_ext=answer_ext,
                is_interactive_problem=is_interactive_problem,
                has_checker=has_checker,
                has_interactor=has_interactor,
                manifest_model=manifest_model,
                in_files=in_files,
                tests_manifest=tests_manifest_dict,
            )
            with open(problem_xml, "w", encoding="utf-8") as f:
                f.write(xml_content)
            results["files_copied"].append(
                "problem.xml (updated)" if xml_existed else "problem.xml (created)"
            )
            try:
                exports["polygon"] = export_polygon(
                    problem_dir,
                    manifest_model,
                    xml_content=xml_content,
                    in_files=in_files,
                    answer_ext=answer_ext,
                    data_mode=export_data_mode,
                )
            except (ValueError, OSError) as exc:
                return ToolResult.fail(f"Polygon export failed: {exc}")

        if "hydro" in formats:
            try:
                exports["hydro"] = export_hydro(
                    problem_dir,
                    manifest_model,
                    time_limit_ms=effective_time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    in_files=in_files,
                    answer_ext=answer_ext,
                    tests_manifest=tests_manifest_dict,
                    data_mode=export_data_mode,
                )
            except (ValueError, OSError) as exc:
                return ToolResult.fail(f"Hydro export failed: {exc}")

        if "qoj" in formats:
            try:
                exports["qoj"] = export_qoj(
                    problem_dir,
                    manifest_model,
                    time_limit_ms=effective_time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    in_files=in_files,
                    answer_ext=answer_ext,
                    tests_manifest=tests_manifest_dict,
                    data_mode=export_data_mode,
                )
            except (ValueError, OSError) as exc:
                return ToolResult.fail(f"QOJ export failed: {exc}")

        for icpc_fmt in ("icpc", "kattis", "icpc_kattis"):
            if icpc_fmt in formats:
                try:
                    exports[icpc_fmt] = export_icpc_kattis(
                        problem_dir,
                        manifest_model,
                        time_limit_ms=effective_time_limit_ms,
                        memory_limit_mb=memory_limit_mb,
                        in_files=in_files,
                        answer_ext=answer_ext,
                        tests_manifest=tests_manifest_dict,
                        output_rel_dir=f"exports/{icpc_fmt}",
                        data_mode=export_data_mode,
                    )
                except (ValueError, OSError) as exc:
                    return ToolResult.fail(f"ICPC/Kattis export ({icpc_fmt}) failed: {exc}")

        if "cms" in formats:
            try:
                exports["cms"] = export_cms(
                    problem_dir,
                    manifest_model,
                    time_limit_ms=effective_time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    in_files=in_files,
                    answer_ext=answer_ext,
                    tests_manifest=tests_manifest_dict,
                    data_mode=export_data_mode,
                )
            except (ValueError, OSError) as exc:
                return ToolResult.fail(f"CMS export failed: {exc}")

        # Scan for undeclared wrong solutions on disk to prevent manifest/problem.xml desync
        pack_warnings: list[str] = []
        declared_sol_paths = {PurePosixPath(s.path.replace("\\", "/")).as_posix().lstrip("./") for s in manifest_model.solutions}
        sols_dir = os.path.join(problem_dir, "solutions")
        if os.path.isdir(sols_dir):
            undeclared_wrongs = sorted(
                f"solutions/{fname}"
                for fname in os.listdir(sols_dir)
                if os.path.isfile(os.path.join(sols_dir, fname))
                and _is_wrong_solution_filename(fname)
                and f"solutions/{fname}" not in declared_sol_paths
            )
            if undeclared_wrongs:
                pack_warnings.append(
                    f"Undeclared wrong solutions on disk not listed in manifest.json/problem.xml: {undeclared_wrongs}"
                )

        # Post-export read-back verification (TL, ML, test count, file existence, no leaked internal tags)
        export_verification = verify_exported_packages(
            problem_dir,
            formats=formats,
            expected_time_limit_ms=effective_time_limit_ms,
            expected_memory_limit_mb=memory_limit_mb,
            expected_test_count=actual_test_count,
            exports_meta=exports,
        )
        checks_map = export_verification.get("checks")
        if isinstance(checks_map, dict):
            for extra_fmt in ("icpc", "kattis", "icpc_kattis", "cms"):
                meta = exports.get(extra_fmt)
                if isinstance(meta, dict):
                    validation = meta.get("config_validation", {})
                    checks_map[extra_fmt] = {
                        "passed": bool(isinstance(validation, dict) and validation.get("passed")),
                        "present": True,
                        "time_limit_ms": effective_time_limit_ms,
                        "memory_limit_mb": memory_limit_mb,
                        "test_count": meta.get("test_count"),
                        "output_dir": meta.get("output_dir"),
                    }
        if not export_verification["passed"]:
            return ToolResult.fail(
                "Exported package self-verification failed: " + "; ".join(export_verification["errors"]),
                results=results,
                exports=exports,
                export_formats=formats,
                export_data_mode=export_data_mode,
                export_verification=export_verification,
                pack_warnings=pack_warnings,
            )

        msg = (
            "Packed to Polygon format"
            if formats == ["polygon"]
            else f"Exported problem package ({', '.join(formats)})"
        )
        return ToolResult.ok(
            results=results,
            exports=exports,
            export_formats=formats,
            export_data_mode=export_data_mode,
            time_limit_ms=effective_time_limit_ms,
            effective_time_limit_ms=effective_time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            export_verification=export_verification,
            pack_warnings=pack_warnings,
            message=msg,
        )
