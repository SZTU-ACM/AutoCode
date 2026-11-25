"""Hydro OJ package exporter."""

from __future__ import annotations

import os
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
from ...workflow.models import AutoCodeManifest
from ..answer_ext import normalize_answer_ext
from ..communication_runner import (
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
    _zip_directory,
    map_tests_to_subtasks,
)


def _validate_hydro_export_config(
    config: dict[str, Any],
    testdata_dir: str,
    *,
    is_interactive: bool,
    is_communication: bool,
    comm_mode: str | None,
    answer_ext: str = ".ans",
) -> dict[str, Any]:
    """Validate platform-specific Hydro config.yaml fields and interactive cleanliness."""
    checked_fields: list[str] = ["type", "time", "memory", "subtasks"]
    errors: list[str] = []

    if is_interactive:
        if config.get("type") != "interactive":
            errors.append(f"Hydro interactive export must set type='interactive', got {config.get('type')!r}")
        checked_fields.append("interactor")
        interactor_name = config.get("interactor")
        if not isinstance(interactor_name, str) or not interactor_name:
            errors.append("Hydro interactive export requires 'interactor' field in testdata/config.yaml")
        elif not os.path.isfile(os.path.join(testdata_dir, interactor_name)):
            errors.append(f"Hydro interactive export missing interactor file: testdata/{interactor_name}")
        norm_ext = normalize_answer_ext(answer_ext) or ".ans"
        answer_exts = tuple(sorted({".ans", ".out", norm_ext}))
        empty_answers = [
            fname
            for fname in sorted(os.listdir(testdata_dir))
            if fname.endswith(answer_exts)
            and os.path.isfile(os.path.join(testdata_dir, fname))
            and _is_effectively_empty_file(os.path.join(testdata_dir, fname))
        ]
        if empty_answers:
            errors.append(f"Hydro interactive export contains empty answer files: {empty_answers[:10]}")
    elif is_communication:
        checked_fields.append("protocol")
        if config.get("type") != "communication":
            errors.append(f"Hydro communication export must set type='communication', got {config.get('type')!r}")
        if not isinstance(config.get("protocol"), dict) or not config["protocol"].get("mode"):
            errors.append("Hydro communication export requires protocol.mode in testdata/config.yaml")
        if comm_mode == "grader_linked":
            checked_fields.append("grader")
            if config.get("grader") and not os.path.isfile(os.path.join(testdata_dir, str(config["grader"]))):
                errors.append(f"Hydro communication export missing grader file: testdata/{config['grader']}")
        else:
            checked_fields.append("interactor")
            if config.get("interactor") and not os.path.isfile(os.path.join(testdata_dir, str(config["interactor"]))):
                errors.append(f"Hydro communication export missing communicator file: testdata/{config['interactor']}")

    if errors:
        raise ValueError("; ".join(errors))
    return {"passed": True, "checked_fields": checked_fields}



def export_hydro(
    problem_dir: str,
    manifest: AutoCodeManifest,
    *,
    time_limit_ms: int,
    memory_limit_mb: int,
    in_files: list[str],
    answer_ext: str,
    tests_manifest: dict[str, Any] | None = None,
    output_rel_dir: str = "exports/hydro",
    data_mode: ExportDataMode = "hardlink",
) -> dict[str, Any]:
    """Export problem package to Hydro OJ format under ``exports/hydro/`` (or ``exports/hydro.zip``)."""
    if data_mode not in _ALLOWED_DATA_MODES:
        raise ValueError(f"Unsupported data_mode: {data_mode!r}; expected one of: copy, hardlink, zip")

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
        testdata_dir = os.path.join(out_dir, "testdata")
        os.makedirs(testdata_dir, exist_ok=True)

        # 1. problem.yaml (never inject internal tool tags like "autocode")
        tags: list[str] = []
        if is_interactive:
            tags.append("interactive")
        elif is_communication:
            tags.append("communication")
            if comm_mode:
                tags.append(comm_mode)
        elif spj_checker:
            tags.append("special-judge")
        if manifest.subtasks:
            tags.append("subtasks")

        problem_yaml: dict[str, Any] = {
            "title": manifest.problem_name,
            "tag": tags,
        }
        Path(out_dir, "problem.yaml").write_text(
            yaml.safe_dump(problem_yaml, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        # 2. problem_zh.md (statement)
        exported_files: list[str] = ["problem.yaml"]
        if _copy_if_safe_file(
            problem_dir, manifest.statement_path, os.path.join(out_dir, "problem_zh.md")
        ) or _copy_if_safe_file(
            problem_dir, "statements/README.md", os.path.join(out_dir, "problem_zh.md")
        ):
            exported_files.append("problem_zh.md")

        # 3. Stage validated test data (omit empty .ans files for interactive problems)
        case_pairs: list[tuple[str, str | None]] = []
        omitted_empty_answers: list[str] = []
        stage_mode_used: set[str] = set()
        effective_stage_arg: ExportDataMode = "copy" if data_mode == "zip" else data_mode

        for in_file, ans_file, src_in, src_ans in safe_pairs:
            stage_mode_used.add(
                _stage_data_file(src_in, os.path.join(testdata_dir, in_file), effective_stage_arg)
            )
            if answers_optional and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
                if ans_file is not None:
                    omitted_empty_answers.append(ans_file)
                case_pairs.append((in_file, None))
            elif src_ans is not None and ans_file is not None:
                stage_mode_used.add(
                    _stage_data_file(
                        src_ans, os.path.join(testdata_dir, ans_file), effective_stage_arg
                    )
                )
                case_pairs.append((in_file, ans_file))
            else:
                case_pairs.append((in_file, None))
        valid_in_files = [pair[0] for pair in case_pairs]

        # 4. Copy judge assets (testlib.h, checker / interactor / communicator / grader / header)
        if _copy_if_safe_file(problem_dir, "files/testlib.h", os.path.join(testdata_dir, "testlib.h")):
            exported_files.append("testdata/testlib.h")

        config: dict[str, Any] = {
            "time": f"{time_limit_ms}ms",
            "memory": f"{memory_limit_mb}m",
        }

        if is_interactive:
            config["type"] = "interactive"
            if _copy_if_safe_file(
                problem_dir, "files/interactor.cpp", os.path.join(testdata_dir, "interactor.cpp")
            ) or _copy_if_safe_file(
                problem_dir, "interactor.cpp", os.path.join(testdata_dir, "interactor.cpp")
            ):
                config["interactor"] = "interactor.cpp"
                exported_files.append("testdata/interactor.cpp")
        elif is_communication:
            config["type"] = "communication"
            protocol_meta: dict[str, Any] = {"mode": comm_mode or "run_twice"}
            if manifest.protocol is not None:
                if comm_mode == "run_twice":
                    protocol_meta["passes"] = manifest.protocol.passes
                    protocol_meta["pass_headers"] = list(manifest.protocol.pass_headers)
                for k in ("max_bits", "max_bytes", "max_rounds", "max_queries"):
                    val = getattr(manifest.protocol, k, None)
                    if val is not None:
                        protocol_meta[k] = val
                if manifest.protocol.participant_solutions:
                    protocol_meta["participant_solutions"] = list(
                        manifest.protocol.participant_solutions
                    )
            config["protocol"] = protocol_meta

            if comm_mode == "grader_linked":
                grader_rel = resolve_grader_source_existing(problem_dir, manifest)
                if grader_rel:
                    grader_name = os.path.basename(grader_rel)
                    if _copy_if_safe_file(
                        problem_dir, grader_rel, os.path.join(testdata_dir, grader_name)
                    ):
                        config["grader"] = grader_name
                        exported_files.append(f"testdata/{grader_name}")
                hdr_rel = (
                    manifest.protocol.header_file
                    if (manifest.protocol and manifest.protocol.header_file)
                    else "files/problem.h"
                )
                if hdr_rel:
                    hdr_name = os.path.basename(hdr_rel)
                    if _copy_if_safe_file(problem_dir, hdr_rel, os.path.join(testdata_dir, hdr_name)):
                        config["header"] = hdr_name
                        exported_files.append(f"testdata/{hdr_name}")
            else:
                comm_rel = resolve_communicator_source_existing(problem_dir, manifest)
                if comm_rel:
                    comm_name = os.path.basename(comm_rel)
                    if _copy_if_safe_file(
                        problem_dir, comm_rel, os.path.join(testdata_dir, comm_name)
                    ):
                        config["interactor"] = comm_name
                        exported_files.append(f"testdata/{comm_name}")
        else:
            config["type"] = "default"
            if spj_checker and _copy_if_safe_file(
                problem_dir, "files/checker.cpp", os.path.join(testdata_dir, "checker.cpp")
            ):
                config["checker_type"] = "testlib"
                config["checker"] = "checker.cpp"
                exported_files.append("testdata/checker.cpp")

        def _hydro_case_item(inp: str, out: str | None) -> dict[str, str]:
            return {"input": inp, "output": out} if out is not None else {"input": inp}

        # 5. Subtasks in config.yaml
        if manifest.subtasks:
            st_map = map_tests_to_subtasks(valid_in_files, manifest.subtasks, tests_manifest)
            hydro_subtasks: list[dict[str, Any]] = []
            for st in manifest.subtasks:
                indices = st_map.get(st.id, [])
                st_cases = [
                    _hydro_case_item(case_pairs[i - 1][0], case_pairs[i - 1][1])
                    for i in indices
                    if 1 <= i <= len(case_pairs)
                ]
                entry: dict[str, Any] = {
                    "id": st.id,
                    "score": st.score,
                    "cases": st_cases,
                }
                if st.name:
                    entry["name"] = st.name
                if st.dependencies:
                    entry["if"] = list(st.dependencies)
                hydro_subtasks.append(entry)
            config["subtasks"] = hydro_subtasks
        else:
            config["subtasks"] = [
                {
                    "id": 1,
                    "score": 100,
                    "cases": [_hydro_case_item(inp, out) for inp, out in case_pairs],
                }
            ]

        config_validation = _validate_hydro_export_config(
            config,
            testdata_dir,
            is_interactive=is_interactive,
            is_communication=is_communication,
            comm_mode=comm_mode,
            answer_ext=answer_ext,
        )

        Path(testdata_dir, "config.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        exported_files.append("testdata/config.yaml")

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
            "format": "hydro",
            "output_dir": final_output_dir,
            "data_mode": data_mode,
            "effective_data_mode": effective_data_mode,
            "test_count": len(case_pairs),
            "subtask_count": len(config["subtasks"]),
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


