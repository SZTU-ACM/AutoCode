"""Polygon self-contained package exporter."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...workflow import manifest_is_interactive
from ...workflow.models import AutoCodeManifest
from ..communication_runner import safe_resolve_under_root
from .common import (
    _ALLOWED_DATA_MODES,
    _MAX_VERIFY_XML_BYTES,
    ExportDataMode,
    _cleanup_stale_sibling,
    _collect_safe_test_pairs,
    _commit_staged_export_dir,
    _extract_polygon_xml_refs,
    _is_interactive_dummy_answer,
    _is_safe_export_rel_path,
    _prepare_safe_zip_path,
    _resolve_safe_export_dir,
    _stage_data_file,
    _zip_directory,
)


def export_polygon(
    problem_dir: str,
    manifest: AutoCodeManifest,
    *,
    xml_content: str,
    in_files: list[str],
    answer_ext: str,
    output_rel_dir: str = "exports/polygon",
    data_mode: ExportDataMode = "hardlink",
) -> dict[str, Any]:
    """Build a self-contained Polygon export under ``exports/polygon/`` (or ``exports/polygon.zip``).

    Stages ``problem.xml``, every existing asset referenced by ``path=`` / ``grader=`` / ``header=``,
    and all test input/answer files so no references dangle when the export is moved or audited.
    """
    import xml.etree.ElementTree as ET

    if data_mode not in _ALLOWED_DATA_MODES:
        raise ValueError(f"Unsupported data_mode: {data_mode!r}; expected one of: copy, hardlink, zip")

    is_interactive = manifest_is_interactive(manifest)
    safe_pairs = _collect_safe_test_pairs(
        problem_dir, in_files, answer_ext, allow_missing_answer=is_interactive
    )

    if len(xml_content.encode("utf-8")) > _MAX_VERIFY_XML_BYTES:
        raise ValueError("problem.xml exceeds maximum allowed size")
    if "<!DOCTYPE" in xml_content.upper() or "<!ENTITY" in xml_content.upper():
        raise ValueError("problem.xml contains disallowed DOCTYPE/ENTITY declarations")
    try:
        xml_root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        raise ValueError(f"invalid problem.xml: {exc}") from exc
    req_refs, opt_refs = _extract_polygon_xml_refs(xml_root)
    all_refs = list(dict.fromkeys([*req_refs, *opt_refs]))
    for ref in all_refs:
        if not _is_safe_export_rel_path(ref, disallow_internal=True) or ref.lower() == "problem.xml":
            raise ValueError(f"Unsafe path reference in problem.xml: {ref!r}")

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
        Path(out_dir, "problem.xml").write_text(xml_content, encoding="utf-8")
        stage_mode_used: set[str] = set()
        effective_stage_arg: ExportDataMode = "copy" if data_mode == "zip" else data_mode

        staged_assets: list[str] = ["problem.xml"]
        missing_referenced_files: list[str] = []
        for rel in all_refs:
            src_abs = safe_resolve_under_root(problem_dir, rel)
            if src_abs is None or not os.path.isfile(src_abs):
                missing_referenced_files.append(rel)
                continue
            dst_abs = safe_resolve_under_root(out_dir, rel)
            if dst_abs is None:
                raise ValueError(f"Export destination escapes polygon staging dir: {rel!r}")
            os.makedirs(os.path.dirname(dst_abs), exist_ok=True)
            stage_mode_used.add(_stage_data_file(src_abs, dst_abs, effective_stage_arg))
            staged_assets.append(rel)

        tests_out_dir = os.path.join(out_dir, "tests")
        os.makedirs(tests_out_dir, exist_ok=True)
        omitted_empty_answers: list[str] = []
        for in_name, ans_name, src_in, src_ans in safe_pairs:
            stage_mode_used.add(
                _stage_data_file(src_in, os.path.join(tests_out_dir, in_name), effective_stage_arg)
            )
            if is_interactive and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
                if ans_name is not None:
                    omitted_empty_answers.append(ans_name)
            elif src_ans is not None and ans_name is not None:
                stage_mode_used.add(
                    _stage_data_file(src_ans, os.path.join(tests_out_dir, ans_name), effective_stage_arg)
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
            "format": "polygon",
            "output_dir": final_output_dir,
            "export_dir": final_output_dir,
            "problem_xml": os.path.join(os.path.abspath(problem_dir), "problem.xml"),
            "exported_problem_xml": (
                "problem.xml" if data_mode == "zip" else os.path.join(final_output_dir, "problem.xml")
            ),
            "data_mode": data_mode,
            "effective_data_mode": effective_data_mode,
            "test_count": len(safe_pairs),
            "subtask_count": len(manifest.subtasks),
            "omitted_empty_answers": omitted_empty_answers,
            "staged_assets": staged_assets,
            "missing_referenced_files": missing_referenced_files,
        }
        if zip_target is not None:
            result_dict["archive_path"] = zip_target
        return result_dict
    finally:
        if temp_ctx is not None:
            temp_ctx.cleanup()
        if stage_dir is not None and os.path.exists(stage_dir):
            shutil.rmtree(stage_dir, ignore_errors=True)


