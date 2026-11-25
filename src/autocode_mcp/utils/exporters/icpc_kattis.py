"""ICPC / Kattis (2025-09 package format) problem exporter."""

from __future__ import annotations

import math
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
    _zip_directory,
)

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
_KATTIS_FORMAT_VERSION = "2025-09"
_DEFAULT_TIME_MULTIPLIER = 5.0


def _is_wrong_solution_filename(fname: str) -> bool:
    if not fname.endswith((".cpp", ".py", ".java", ".rs")):
        return False
    return fname == "bf.cpp" or fname.startswith(_WRONG_SOLUTION_PREFIXES)


def _resolve_kattis_time_multiplier(manifest: AutoCodeManifest) -> float:
    multipliers = manifest.time_limit_multipliers or {}
    for key in ("icpc", "kattis", "default"):
        val = multipliers.get(key)
        if (
            isinstance(val, int | float)
            and not isinstance(val, bool)
            and math.isfinite(float(val))
            and float(val) > 0.0
        ):
            return float(val)
    return _DEFAULT_TIME_MULTIPLIER


def _identify_sample_filenames(
    in_files: list[str],
    manifest: AutoCodeManifest,
    tests_manifest: dict[str, Any] | None,
) -> set[str]:
    """Return input filenames that belong under ``data/sample/``."""
    sample_names: set[str] = set()
    sample_plan_names = {
        item.name.strip().lower()
        for item in manifest.case_plan
        if item.group.strip().lower() == "sample" and item.name.strip()
    }
    meta_by_filename: dict[str, dict[str, Any]] = {}
    meta_by_index: dict[int, dict[str, Any]] = {}
    if isinstance(tests_manifest, dict) and isinstance(tests_manifest.get("tests"), list):
        for pos, item in enumerate(tests_manifest["tests"], 1):
            if not isinstance(item, dict):
                continue
            in_name = item.get("in_file")
            if isinstance(in_name, str) and in_name:
                meta_by_filename[in_name] = item
            idx = item.get("index")
            resolved_idx = (
                idx if (isinstance(idx, int) and not isinstance(idx, bool) and idx >= 1) else pos
            )
            meta_by_index[resolved_idx] = item

    for idx, fname in enumerate(in_files, 1):
        stem = os.path.splitext(fname)[0].lower()
        item = meta_by_filename.get(fname) or meta_by_index.get(idx, {})
        group_val = str(item.get("group") or "").strip().lower()
        if (
            fname.lower().startswith("sample")
            or bool(item.get("is_sample"))
            or group_val == "sample"
            or stem in sample_plan_names
        ):
            sample_names.add(fname)
    return sample_names


def _unique_dest_name(used: set[str], desired_name: str) -> str:
    if desired_name not in used:
        used.add(desired_name)
        return desired_name
    stem, ext = os.path.splitext(desired_name)
    suffix = 2
    while f"{stem}_{suffix}{ext}" in used:
        suffix += 1
    unique = f"{stem}_{suffix}{ext}"
    used.add(unique)
    return unique


def _validate_icpc_kattis_export(
    problem_yaml: dict[str, Any],
    out_dir: str,
    *,
    expected_time_limit_ms: int,
    expected_memory_limit_mb: int,
    is_interactive: bool,
    is_communication: bool,
    spj_checker: bool,
) -> dict[str, Any]:
    """Validate Kattis 2025-09 ``problem.yaml`` and directory invariants."""
    errors: list[str] = []
    checked_fields = [
        "problem_format_version",
        "name",
        "limits.time_limit",
        "limits.time_multiplier",
        "limits.memory",
        "validation",
    ]
    if problem_yaml.get("problem_format_version") != _KATTIS_FORMAT_VERSION:
        errors.append(
            f"ICPC/Kattis problem.yaml must set problem_format_version={_KATTIS_FORMAT_VERSION!r}"
        )
    if not isinstance(problem_yaml.get("name"), str) or not str(problem_yaml["name"]).strip():
        errors.append("ICPC/Kattis problem.yaml requires non-empty 'name'")
    limits = problem_yaml.get("limits")
    if not isinstance(limits, dict):
        errors.append("ICPC/Kattis problem.yaml requires 'limits' mapping")
    else:
        tl = limits.get("time_limit")
        tm = limits.get("time_multiplier")
        mem = limits.get("memory")
        expected_tl_s = round(expected_time_limit_ms / 1000.0, 3)
        if not isinstance(tl, int | float) or isinstance(tl, bool) or abs(float(tl) - expected_tl_s) > 1e-6:
            errors.append(f"ICPC/Kattis limits.time_limit={tl!r} != expected {expected_tl_s}")
        if not isinstance(tm, int | float) or isinstance(tm, bool) or float(tm) <= 0:
            errors.append(f"ICPC/Kattis limits.time_multiplier={tm!r} must be positive")
        if mem != expected_memory_limit_mb:
            errors.append(f"ICPC/Kattis limits.memory={mem!r} != expected {expected_memory_limit_mb}")

    validation = problem_yaml.get("validation")
    if not isinstance(validation, str) or not validation:
        errors.append("ICPC/Kattis problem.yaml requires 'validation' mode")

    out_val_dir = os.path.join(out_dir, "output_validators")
    has_output_validator = os.path.isdir(out_val_dir) and any(
        os.path.isfile(os.path.join(out_val_dir, entry)) for entry in os.listdir(out_val_dir)
    )
    if (is_interactive or is_communication or spj_checker) and not has_output_validator:
        errors.append("ICPC/Kattis custom/interactive export requires files in output_validators/")

    if is_interactive or is_communication:
        for split in ("sample", "secret"):
            split_dir = os.path.join(out_dir, "data", split)
            if not os.path.isdir(split_dir):
                continue
            empty_ans = [
                fname
                for fname in sorted(os.listdir(split_dir))
                if fname.endswith(".ans")
                and os.path.isfile(os.path.join(split_dir, fname))
                and _is_effectively_empty_file(os.path.join(split_dir, fname))
            ]
            if empty_ans:
                errors.append(
                    f"ICPC/Kattis protocol export contains empty .ans files in data/{split}: {empty_ans[:10]}"
                )

    if errors:
        raise ValueError("; ".join(errors))
    return {"passed": True, "checked_fields": checked_fields}


def export_icpc_kattis(
    problem_dir: str,
    manifest: AutoCodeManifest,
    *,
    time_limit_ms: int,
    memory_limit_mb: int,
    in_files: list[str],
    answer_ext: str,
    tests_manifest: dict[str, Any] | None = None,
    output_rel_dir: str = "exports/icpc_kattis",
    data_mode: ExportDataMode = "hardlink",
) -> dict[str, Any]:
    """Export problem package to ICPC / Kattis ``2025-09`` layout."""
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
    sample_names = _identify_sample_filenames(
        [pair[0] for pair in safe_pairs], manifest, tests_manifest
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
        sample_dir = os.path.join(out_dir, "data", "sample")
        secret_dir = os.path.join(out_dir, "data", "secret")
        accepted_dir = os.path.join(out_dir, "submissions", "accepted")
        wrong_dir = os.path.join(out_dir, "submissions", "wrong_answer")
        tle_dir = os.path.join(out_dir, "submissions", "time_limit_exceeded")
        in_val_dir = os.path.join(out_dir, "input_validators")
        out_val_dir = os.path.join(out_dir, "output_validators")
        stmt_dir = os.path.join(out_dir, "problem_statement")
        for directory in (sample_dir, secret_dir, accepted_dir, wrong_dir, in_val_dir, out_val_dir, stmt_dir):
            os.makedirs(directory, exist_ok=True)

        validation_mode = "default"
        if is_interactive:
            validation_mode = "custom interactive"
        elif is_communication:
            validation_mode = "custom multi-pass" if comm_mode == "run_twice" else "custom interactive"
        elif spj_checker:
            validation_mode = "custom"

        problem_yaml: dict[str, Any] = {
            "problem_format_version": _KATTIS_FORMAT_VERSION,
            "name": manifest.problem_name,
            "type": "scoring" if manifest.subtasks else "pass-fail",
            "limits": {
                "time_limit": round(time_limit_ms / 1000.0, 3),
                "time_multiplier": _resolve_kattis_time_multiplier(manifest),
                "memory": memory_limit_mb,
            },
            "validation": validation_mode,
        }
        Path(out_dir, "problem.yaml").write_text(
            yaml.safe_dump(problem_yaml, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        exported_files: list[str] = ["problem.yaml"]

        # Statement
        if (
            _copy_if_safe_file(
                problem_dir, manifest.statement_path, os.path.join(stmt_dir, "problem.en.md")
            )
            or _copy_if_safe_file(
                problem_dir, "statements/README.md", os.path.join(stmt_dir, "problem.en.md")
            )
            or _copy_if_safe_file(
                problem_dir, "README.md", os.path.join(stmt_dir, "problem.en.md")
            )
        ):
            exported_files.append("problem_statement/problem.en.md")

        # Test data split into data/sample and data/secret with canonical .ans extension
        effective_stage_arg: ExportDataMode = "copy" if data_mode == "zip" else data_mode
        stage_mode_used: set[str] = set()
        omitted_empty_answers: list[str] = []
        sample_count = 0
        secret_count = 0

        for in_file, ans_file, src_in, src_ans in safe_pairs:
            is_sample = in_file in sample_names
            split = "sample" if is_sample else "secret"
            split_dir = sample_dir if is_sample else secret_dir
            stem = os.path.splitext(in_file)[0]
            dst_in = os.path.join(split_dir, f"{stem}.in")
            stage_mode_used.add(_stage_data_file(src_in, dst_in, effective_stage_arg))
            exported_files.append(f"data/{split}/{stem}.in")
            if is_sample:
                sample_count += 1
            else:
                secret_count += 1

            if answers_optional and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
                if ans_file is not None:
                    omitted_empty_answers.append(ans_file)
            elif src_ans is not None:
                dst_ans = os.path.join(split_dir, f"{stem}.ans")
                stage_mode_used.add(_stage_data_file(src_ans, dst_ans, effective_stage_arg))
                exported_files.append(f"data/{split}/{stem}.ans")

        # Input & output validators (+ testlib.h when present)
        has_val = _copy_if_safe_file(
            problem_dir, "files/val.cpp", os.path.join(in_val_dir, "val.cpp")
        ) or _copy_if_safe_file(problem_dir, "val.cpp", os.path.join(in_val_dir, "val.cpp"))
        if has_val:
            exported_files.append("input_validators/val.cpp")
            if _copy_if_safe_file(
                problem_dir, "files/testlib.h", os.path.join(in_val_dir, "testlib.h")
            ):
                exported_files.append("input_validators/testlib.h")

        copied_out_validator = False
        if is_interactive:
            if _copy_if_safe_file(
                problem_dir, "files/interactor.cpp", os.path.join(out_val_dir, "interactor.cpp")
            ) or _copy_if_safe_file(
                problem_dir, "interactor.cpp", os.path.join(out_val_dir, "interactor.cpp")
            ):
                exported_files.append("output_validators/interactor.cpp")
                copied_out_validator = True
        elif is_communication:
            if comm_mode == "grader_linked":
                grader_rel = resolve_grader_source_existing(problem_dir, manifest)
                if grader_rel:
                    grader_name = os.path.basename(grader_rel)
                    if _copy_if_safe_file(
                        problem_dir, grader_rel, os.path.join(out_val_dir, grader_name)
                    ):
                        exported_files.append(f"output_validators/{grader_name}")
                        copied_out_validator = True
                hdr_rel = (
                    manifest.protocol.header_file
                    if (manifest.protocol and manifest.protocol.header_file)
                    else "files/problem.h"
                )
                if hdr_rel:
                    hdr_name = os.path.basename(hdr_rel)
                    if _copy_if_safe_file(
                        problem_dir, hdr_rel, os.path.join(out_val_dir, hdr_name)
                    ):
                        exported_files.append(f"output_validators/{hdr_name}")
            else:
                comm_rel = resolve_communicator_source_existing(problem_dir, manifest)
                if comm_rel:
                    comm_name = os.path.basename(comm_rel)
                    if _copy_if_safe_file(
                        problem_dir, comm_rel, os.path.join(out_val_dir, comm_name)
                    ):
                        exported_files.append(f"output_validators/{comm_name}")
                        copied_out_validator = True
        elif spj_checker:
            if _copy_if_safe_file(
                problem_dir, "files/checker.cpp", os.path.join(out_val_dir, "checker.cpp")
            ):
                exported_files.append("output_validators/checker.cpp")
                copied_out_validator = True

        if copied_out_validator and _copy_if_safe_file(
            problem_dir, "files/testlib.h", os.path.join(out_val_dir, "testlib.h")
        ):
            exported_files.append("output_validators/testlib.h")

        # Submissions (accepted / wrong_answer / optional time_limit_exceeded)
        used_acc: set[str] = set()
        used_wa: set[str] = set()
        used_tle: set[str] = set()
        seen_rel_paths: set[str] = set()

        def _stage_solution(rel_path: str, bucket: str) -> None:
            norm_rel = rel_path.replace("\\", "/").lstrip("./")
            if norm_rel in seen_rel_paths:
                return
            base_name = os.path.basename(norm_rel)
            if not base_name:
                return
            if bucket == "accepted":
                dst_name = _unique_dest_name(used_acc, base_name)
                if _copy_if_safe_file(problem_dir, norm_rel, os.path.join(accepted_dir, dst_name)):
                    exported_files.append(f"submissions/accepted/{dst_name}")
                    seen_rel_paths.add(norm_rel)
            elif bucket == "time_limit_exceeded":
                os.makedirs(tle_dir, exist_ok=True)
                dst_name = _unique_dest_name(used_tle, base_name)
                if _copy_if_safe_file(problem_dir, norm_rel, os.path.join(tle_dir, dst_name)):
                    exported_files.append(f"submissions/time_limit_exceeded/{dst_name}")
                    seen_rel_paths.add(norm_rel)
            else:
                dst_name = _unique_dest_name(used_wa, base_name)
                if _copy_if_safe_file(problem_dir, norm_rel, os.path.join(wrong_dir, dst_name)):
                    exported_files.append(f"submissions/wrong_answer/{dst_name}")
                    seen_rel_paths.add(norm_rel)

        for sol_entry in manifest.solutions:
            if sol_entry.role in ("main", "reference") or (
                sol_entry.role == "wrong" and sol_entry.expected == "pass"
            ):
                _stage_solution(sol_entry.path, "accepted")
            elif sol_entry.role == "wrong":
                bucket = (
                    "time_limit_exceeded"
                    if sol_entry.expected_verdict == "TLE"
                    else "wrong_answer"
                )
                _stage_solution(sol_entry.path, bucket)
            elif sol_entry.role == "brute" and sol_entry.expected == "fail":
                bucket = (
                    "time_limit_exceeded"
                    if sol_entry.expected_verdict == "TLE"
                    else "wrong_answer"
                )
                _stage_solution(sol_entry.path, bucket)

        if manifest.protocol and manifest.protocol.participant_solutions:
            for part_name in manifest.protocol.participant_solutions:
                if is_safe_stem(part_name):
                    _stage_solution(f"solutions/{part_name}.cpp", "accepted")

        if not used_acc:
            _stage_solution("solutions/sol.cpp", "accepted")
            if not used_acc:
                _stage_solution("sol.cpp", "accepted")

        sols_dir = os.path.join(problem_dir, "solutions")
        if os.path.isdir(sols_dir) and not os.path.islink(sols_dir):
            for fname in sorted(os.listdir(sols_dir)):
                fpath = os.path.join(sols_dir, fname)
                if os.path.isfile(fpath) and not os.path.islink(fpath) and _is_wrong_solution_filename(fname):
                    _stage_solution(f"solutions/{fname}", "wrong_answer")

        config_validation = _validate_icpc_kattis_export(
            problem_yaml,
            out_dir,
            expected_time_limit_ms=time_limit_ms,
            expected_memory_limit_mb=memory_limit_mb,
            is_interactive=is_interactive,
            is_communication=is_communication,
            spj_checker=spj_checker,
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
            "format": "icpc_kattis",
            "output_dir": final_output_dir,
            "data_mode": data_mode,
            "effective_data_mode": effective_data_mode,
            "test_count": len(safe_pairs),
            "sample_count": sample_count,
            "secret_count": secret_count,
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
