"""IOI CMS (Contest Management System) task exporter."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from ...workflow import (
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from ...workflow.models import AutoCodeManifest, SubtaskSpec
from ..communication_runner import (
    is_safe_stem,
    resolve_communicator_source_existing,
    resolve_grader_source_existing,
)
from .common import (
    _ALLOWED_DATA_MODES,
    ExportDataMode,
    _cleanup_stale_sibling,
    _collect_safe_test_pairs,
    _commit_staged_export_dir,
    _copy_if_safe_file,
    _is_effectively_empty_file,
    _is_interactive_dummy_answer,
    _prepare_safe_zip_path,
    _resolve_safe_export_dir,
    _stage_data_file,
    _toposort_subtasks,
    _zip_directory,
    map_tests_to_subtasks,
)


def _slugify_task_name(problem_name: str, problem_dir: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", problem_name.strip()).strip("_").lower()
    if slug:
        return slug[:64]
    fallback = re.sub(
        r"[^A-Za-z0-9_-]+", "_", os.path.basename(os.path.normpath(problem_dir))
    ).strip("_").lower()
    return fallback[:64] or "task"


def _validate_cms_export(
    task_yaml: dict[str, Any],
    out_dir: str,
    *,
    expected_time_limit_ms: int,
    expected_memory_limit_mb: int,
    is_interactive: bool,
    is_communication: bool,
    spj_checker: bool,
) -> dict[str, Any]:
    """Validate IOI CMS ``task.yaml`` fields and staged input/output/check invariants."""
    errors: list[str] = []
    checked_fields = ["name", "time_limit", "memory_limit", "n_input", "score_type"]

    if not isinstance(task_yaml.get("name"), str) or not str(task_yaml["name"]).strip():
        errors.append("CMS task.yaml requires non-empty 'name'")
    expected_tl_s = round(expected_time_limit_ms / 1000.0, 3)
    tl = task_yaml.get("time_limit")
    if not isinstance(tl, int | float) or isinstance(tl, bool) or abs(float(tl) - expected_tl_s) > 1e-6:
        errors.append(f"CMS task.yaml time_limit={tl!r} != expected {expected_tl_s}")
    if task_yaml.get("memory_limit") != expected_memory_limit_mb:
        errors.append(
            f"CMS task.yaml memory_limit={task_yaml.get('memory_limit')!r} != expected {expected_memory_limit_mb}"
        )

    n_input = task_yaml.get("n_input")
    if not isinstance(n_input, int) or isinstance(n_input, bool) or n_input < 0:
        errors.append(f"CMS task.yaml invalid n_input={n_input!r}")
        n_input_int = 0
    else:
        n_input_int = n_input

    for idx in range(n_input_int):
        if not os.path.isfile(os.path.join(out_dir, "input", f"input{idx}.txt")):
            errors.append(f"CMS export missing input/input{idx}.txt")
            break
        if not (is_interactive or is_communication) and not os.path.isfile(
            os.path.join(out_dir, "output", f"output{idx}.txt")
        ):
            errors.append(f"CMS export missing output/output{idx}.txt")
            break

    if isinstance(task_yaml.get("subtasks"), list):
        checked_fields.append("subtasks")
        params = task_yaml.get("score_type_parameters")
        if not isinstance(params, list) or len(params) != len(task_yaml["subtasks"]):
            errors.append("CMS task.yaml score_type_parameters length must match subtasks")
        else:
            param_cases = sum(
                int(p[1])
                for p in params
                if isinstance(p, list | tuple) and len(p) == 2 and isinstance(p[1], int)
            )
            if param_cases != n_input_int:
                errors.append(
                    f"CMS score_type_parameters case count ({param_cases}) != n_input ({n_input_int})"
                )

    check_dir = os.path.join(out_dir, "check")
    has_check = os.path.isdir(check_dir) and any(
        os.path.isfile(os.path.join(check_dir, entry)) for entry in os.listdir(check_dir)
    )
    if (is_interactive or is_communication or spj_checker) and not has_check:
        errors.append("CMS custom/protocol task requires judge source in check/")

    if is_interactive or is_communication:
        output_dir = os.path.join(out_dir, "output")
        if os.path.isdir(output_dir):
            empty_outputs = [
                fname
                for fname in sorted(os.listdir(output_dir))
                if os.path.isfile(os.path.join(output_dir, fname))
                and _is_effectively_empty_file(os.path.join(output_dir, fname))
            ]
            if empty_outputs:
                errors.append(
                    f"CMS protocol export contains empty output files: {empty_outputs[:10]}"
                )

    if errors:
        raise ValueError("; ".join(errors))
    return {"passed": True, "checked_fields": checked_fields}


def export_cms(
    problem_dir: str,
    manifest: AutoCodeManifest,
    *,
    time_limit_ms: int,
    memory_limit_mb: int,
    in_files: list[str],
    answer_ext: str,
    tests_manifest: dict[str, Any] | None = None,
    output_rel_dir: str = "exports/cms",
    data_mode: ExportDataMode = "hardlink",
) -> dict[str, Any]:
    """Export problem package to IOI CMS task layout under ``exports/cms/`` (or ``exports/cms.zip``)."""
    if data_mode not in _ALLOWED_DATA_MODES:
        raise ValueError(f"Unsupported data_mode: {data_mode!r}; expected one of: copy, hardlink, zip")
    if isinstance(time_limit_ms, bool) or time_limit_ms <= 0:
        raise ValueError(f"time_limit_ms must be a positive integer, got {time_limit_ms!r}")
    if isinstance(memory_limit_mb, bool) or memory_limit_mb <= 0:
        raise ValueError(f"memory_limit_mb must be a positive integer, got {memory_limit_mb!r}")

    is_interactive = manifest_is_interactive(manifest)
    is_communication = manifest_is_communication(manifest)
    answers_optional = is_interactive or is_communication
    spj_checker = manifest_uses_testlib_checker(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else None
    )

    safe_pairs = _collect_safe_test_pairs(
        problem_dir, in_files, answer_ext, allow_missing_answer=answers_optional
    )
    valid_in_files = [pair[0] for pair in safe_pairs]

    ordered_subtasks: list[SubtaskSpec] = []
    ordered_indices: list[int] = list(range(1, len(safe_pairs) + 1))
    subtask_slices: list[tuple[SubtaskSpec, int, int]] = []

    if manifest.subtasks:
        ordered_subtasks = _toposort_subtasks(manifest.subtasks)
        st_map = map_tests_to_subtasks(valid_in_files, ordered_subtasks, tests_manifest)
        emitted_by_subtask: dict[int, set[int]] = {}
        reordered: list[int] = []
        valid_ids = {st.id for st in ordered_subtasks}
        direct_deps = {
            st.id: [d for d in st.dependencies if d in valid_ids] for st in ordered_subtasks
        }

        def _transitive_emitted(st_id: int) -> set[int]:
            covered: set[int] = set()
            stack = list(direct_deps.get(st_id, []))
            visited: set[int] = set()
            while stack:
                dep_id = stack.pop()
                if dep_id in visited:
                    continue
                visited.add(dep_id)
                covered.update(emitted_by_subtask.get(dep_id, set()))
                stack.extend(direct_deps.get(dep_id, []))
            return covered

        for st in ordered_subtasks:
            raw_indices = [
                orig_idx for orig_idx in st_map.get(st.id, []) if 1 <= orig_idx <= len(safe_pairs)
            ]
            if not raw_indices:
                raise ValueError(
                    f"CMS export requires every subtask to map to at least one test "
                    f"(subtask {st.id} is empty)"
                )
            dep_covered = _transitive_emitted(st.id)
            own_indices = [idx for idx in raw_indices if idx not in dep_covered]
            if not own_indices:
                own_indices = list(raw_indices)
            start_pos = len(reordered)
            reordered.extend(own_indices)
            emitted_by_subtask[st.id] = set(own_indices) | dep_covered
            subtask_slices.append((st, start_pos, len(own_indices)))

        ordered_indices = reordered

    zip_target: str | None = None
    target_dir: str | None = None
    temp_ctx: tempfile.TemporaryDirectory[str] | None = None
    stage_dir: str | None = None
    if data_mode == "zip":
        zip_target = _prepare_safe_zip_path(problem_dir, output_rel_dir)
        temp_ctx = tempfile.TemporaryDirectory()
        out_dir = temp_ctx.name
    else:
        target_dir = _resolve_safe_export_dir(problem_dir, output_rel_dir)
        parent_dir = os.path.dirname(target_dir)
        os.makedirs(parent_dir, exist_ok=True)
        stage_dir = tempfile.mkdtemp(prefix=".tmp_export_", dir=parent_dir)
        out_dir = stage_dir

    try:
        input_dir = os.path.join(out_dir, "input")
        output_dir = os.path.join(out_dir, "output")
        sol_dir = os.path.join(out_dir, "sol")
        check_dir = os.path.join(out_dir, "check")
        gen_dir = os.path.join(out_dir, "gen")
        stmt_dir = os.path.join(out_dir, "statement")
        for directory in (input_dir, output_dir, sol_dir, check_dir, gen_dir):
            os.makedirs(directory, exist_ok=True)

        effective_stage_arg: ExportDataMode = "copy" if data_mode == "zip" else data_mode
        stage_mode_used: set[str] = set()
        omitted_empty_answers: list[str] = []
        exported_files: list[str] = ["task.yaml"]

        for cms_idx, orig_idx in enumerate(ordered_indices):
            _, orig_ans_name, src_in, src_ans = safe_pairs[orig_idx - 1]
            in_name = f"input{cms_idx}.txt"
            stage_mode_used.add(
                _stage_data_file(src_in, os.path.join(input_dir, in_name), effective_stage_arg)
            )
            exported_files.append(f"input/{in_name}")

            if answers_optional and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
                if orig_ans_name is not None:
                    omitted_empty_answers.append(orig_ans_name)
            elif src_ans is not None:
                out_name = f"output{cms_idx}.txt"
                stage_mode_used.add(
                    _stage_data_file(src_ans, os.path.join(output_dir, out_name), effective_stage_arg)
                )
                exported_files.append(f"output/{out_name}")

        # Statement
        if _copy_if_safe_file(
            problem_dir, manifest.statement_path, os.path.join(stmt_dir, "statement.md")
        ) or _copy_if_safe_file(
            problem_dir, "statements/README.md", os.path.join(stmt_dir, "statement.md")
        ):
            exported_files.append("statement/statement.md")

        # Solutions -> sol/
        copied_sol_names: set[str] = set()

        def _copy_to_sol(rel_src: str) -> None:
            norm_rel = rel_src.replace("\\", "/").lstrip("./")
            base_name = os.path.basename(norm_rel)
            if not base_name or base_name in copied_sol_names:
                return
            if _copy_if_safe_file(problem_dir, norm_rel, os.path.join(sol_dir, base_name)):
                copied_sol_names.add(base_name)
                exported_files.append(f"sol/{base_name}")

        for sol_entry in manifest.solutions:
            _copy_to_sol(sol_entry.path)
        if manifest.protocol and manifest.protocol.participant_solutions:
            for part_name in manifest.protocol.participant_solutions:
                if is_safe_stem(part_name):
                    _copy_to_sol(f"solutions/{part_name}.cpp")
        _copy_to_sol("solutions/sol.cpp")
        if "sol.cpp" not in copied_sol_names:
            _copy_to_sol("sol.cpp")
        _copy_to_sol("solutions/brute.cpp")

        # Generator / Validator -> gen/
        has_gen_asset = False
        if _copy_if_safe_file(problem_dir, "files/gen.cpp", os.path.join(gen_dir, "gen.cpp")) or _copy_if_safe_file(
            problem_dir, "gen.cpp", os.path.join(gen_dir, "gen.cpp")
        ):
            exported_files.append("gen/gen.cpp")
            has_gen_asset = True
        if _copy_if_safe_file(problem_dir, "files/val.cpp", os.path.join(gen_dir, "val.cpp")) or _copy_if_safe_file(
            problem_dir, "val.cpp", os.path.join(gen_dir, "val.cpp")
        ):
            exported_files.append("gen/val.cpp")
            has_gen_asset = True
        if has_gen_asset and _copy_if_safe_file(
            problem_dir, "files/testlib.h", os.path.join(gen_dir, "testlib.h")
        ):
            exported_files.append("gen/testlib.h")

        # Checker / Interactor / Communicator -> check/
        has_check_asset = False
        if is_interactive:
            if _copy_if_safe_file(
                problem_dir, "files/interactor.cpp", os.path.join(check_dir, "interactor.cpp")
            ) or _copy_if_safe_file(
                problem_dir, "interactor.cpp", os.path.join(check_dir, "interactor.cpp")
            ):
                exported_files.append("check/interactor.cpp")
                has_check_asset = True
        elif is_communication:
            if comm_mode == "grader_linked":
                grader_rel = resolve_grader_source_existing(problem_dir, manifest)
                if grader_rel:
                    grader_name = os.path.basename(grader_rel)
                    if _copy_if_safe_file(problem_dir, grader_rel, os.path.join(check_dir, grader_name)):
                        exported_files.append(f"check/{grader_name}")
                        has_check_asset = True
                hdr_rel = (
                    manifest.protocol.header_file
                    if (manifest.protocol and manifest.protocol.header_file)
                    else "files/problem.h"
                )
                if hdr_rel:
                    hdr_name = os.path.basename(hdr_rel)
                    if _copy_if_safe_file(problem_dir, hdr_rel, os.path.join(check_dir, hdr_name)):
                        exported_files.append(f"check/{hdr_name}")
                        _copy_to_sol(hdr_rel)
            else:
                comm_rel = resolve_communicator_source_existing(problem_dir, manifest)
                if comm_rel:
                    comm_name = os.path.basename(comm_rel)
                    if _copy_if_safe_file(problem_dir, comm_rel, os.path.join(check_dir, comm_name)):
                        exported_files.append(f"check/{comm_name}")
                        has_check_asset = True
        elif spj_checker:
            if _copy_if_safe_file(
                problem_dir, "files/checker.cpp", os.path.join(check_dir, "checker.cpp")
            ):
                exported_files.append("check/checker.cpp")
                has_check_asset = True

        if has_check_asset and _copy_if_safe_file(
            problem_dir, "files/testlib.h", os.path.join(check_dir, "testlib.h")
        ):
            exported_files.append("check/testlib.h")

        task_type = "Batch"
        if is_interactive:
            task_type = "Communication"
        elif is_communication:
            task_type = "TwoSteps" if comm_mode == "run_twice" else "Communication"

        task_yaml: dict[str, Any] = {
            "name": _slugify_task_name(manifest.problem_name, problem_dir),
            "title": manifest.problem_name,
            "time_limit": round(time_limit_ms / 1000.0, 3),
            "memory_limit": memory_limit_mb,
            "n_input": len(ordered_indices),
            "task_type": task_type,
            "score_mode": "max_subtask" if subtask_slices else "max",
            "token_mode": "disabled",
            "infile": "",
            "outfile": "",
        }
        if subtask_slices:
            task_yaml["score_type"] = "GroupMin"
            task_yaml["score_type_parameters"] = [
                [st.score, count] for st, _, count in subtask_slices
            ]
            task_yaml["total_score"] = sum(st.score for st, _, _ in subtask_slices)
            cms_subtasks: list[dict[str, Any]] = []
            for st, start_pos, count in subtask_slices:
                st_entry: dict[str, Any] = {
                    "id": st.id,
                    "name": st.name or f"subtask_{st.id}",
                    "score": st.score,
                    "weight": st.score,
                    "test_count": count,
                    "inputs": [f"input{i}.txt" for i in range(start_pos, start_pos + count)],
                }
                if st.dependencies:
                    st_entry["dependencies"] = list(st.dependencies)
                cms_subtasks.append(st_entry)
            task_yaml["subtasks"] = cms_subtasks
        else:
            task_yaml["score_type"] = "Sum"
            task_yaml["total_score"] = 100

        config_validation = _validate_cms_export(
            task_yaml,
            out_dir,
            expected_time_limit_ms=time_limit_ms,
            expected_memory_limit_mb=memory_limit_mb,
            is_interactive=is_interactive,
            is_communication=is_communication,
            spj_checker=spj_checker,
        )

        Path(out_dir, "task.yaml").write_text(
            yaml.safe_dump(task_yaml, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        if data_mode == "zip" and zip_target is not None:
            _zip_directory(out_dir, zip_target)
            _cleanup_stale_sibling(zip_target, is_zip=True)
            effective_data_mode = "zip"
            final_output_dir = zip_target
        else:
            assert stage_dir is not None and target_dir is not None
            _commit_staged_export_dir(stage_dir, target_dir)
            stage_dir = None
            _cleanup_stale_sibling(target_dir, is_zip=False)
            final_output_dir = target_dir
            if stage_mode_used == {"hardlink"}:
                effective_data_mode = "hardlink"
            elif "hardlink" in stage_mode_used:
                effective_data_mode = "mixed"
            else:
                effective_data_mode = "copy"

        result_dict: dict[str, Any] = {
            "format": "cms",
            "output_dir": final_output_dir,
            "data_mode": data_mode,
            "effective_data_mode": effective_data_mode,
            "test_count": len(ordered_indices),
            "subtask_count": len(subtask_slices),
            "total_score": task_yaml["total_score"],
            "omitted_empty_answers": omitted_empty_answers,
            "config_validation": config_validation,
            "files": exported_files,
        }
        if zip_target is not None:
            result_dict["archive_path"] = zip_target
        return result_dict
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
        if stage_dir is not None and os.path.exists(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)
