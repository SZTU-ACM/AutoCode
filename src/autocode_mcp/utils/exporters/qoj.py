"""QOJ / UOJ package exporter."""

from __future__ import annotations

import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...workflow import (
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from ...workflow.models import AutoCodeManifest, SubtaskSpec
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
    _toposort_subtasks,
    _zip_directory,
    map_tests_to_subtasks,
)


def _validate_qoj_export_config(
    conf_lines: list[str],
    out_dir: str,
    data_dir: str,
    *,
    is_interactive: bool,
    is_communication: bool,
    expected_tests: int,
) -> dict[str, Any]:
    """Validate platform-specific QOJ/UOJ problem.conf fields and interactive cleanliness."""
    parsed: dict[str, str] = {}
    for line in conf_lines:
        parts = line.split(" ", 1)
        if len(parts) == 2:
            parsed[parts[0]] = parts[1]

    checked_fields: list[str] = ["use_builtin_judger", "n_tests", "time_limit", "memory_limit"]
    errors: list[str] = []
    if parsed.get("use_builtin_judger") != "on":
        errors.append("QOJ export requires 'use_builtin_judger on'")
    if parsed.get("n_tests") != str(expected_tests):
        errors.append(f"QOJ export n_tests={parsed.get('n_tests')!r} != {expected_tests}")

    if is_interactive:
        checked_fields.extend(["interaction_mode", "interactor"])
        if parsed.get("interaction_mode") != "on":
            errors.append("QOJ interactive export requires 'interaction_mode on'")
        interactor_name = parsed.get("interactor")
        if not interactor_name:
            errors.append("QOJ interactive export requires 'interactor <file>' in problem.conf")
        elif not os.path.isfile(os.path.join(out_dir, interactor_name)):
            errors.append(f"QOJ interactive export missing interactor file: {interactor_name}")
        if "use_builtin_checker" in parsed:
            errors.append("QOJ interactive export must not set use_builtin_checker")
        empty_answers = [
            fname
            for fname in sorted(os.listdir(data_dir))
            if not fname.endswith(".in")
            and os.path.isfile(os.path.join(data_dir, fname))
            and _is_effectively_empty_file(os.path.join(data_dir, fname))
        ]
        if empty_answers:
            errors.append(f"QOJ interactive export contains empty answer files: {empty_answers[:10]}")
    elif is_communication:
        checked_fields.append("communication_mode")
        if not parsed.get("communication_mode"):
            errors.append("QOJ communication export requires 'communication_mode'")

    if errors:
        raise ValueError("; ".join(errors))
    return {"passed": True, "checked_fields": checked_fields}



def export_qoj(
    problem_dir: str,
    manifest: AutoCodeManifest,
    *,
    time_limit_ms: int,
    memory_limit_mb: int,
    in_files: list[str],
    answer_ext: str,
    tests_manifest: dict[str, Any] | None = None,
    output_rel_dir: str = "exports/qoj",
    data_mode: ExportDataMode = "hardlink",
) -> dict[str, Any]:
    """Export problem package to QOJ / UOJ format under ``exports/qoj/`` (or ``exports/qoj.zip``)."""
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
    norm_ext = normalize_answer_ext(answer_ext)
    assert norm_ext is not None
    ext_no_dot = norm_ext.lstrip(".") or "ans"

    valid_in_files = [pair[0] for pair in safe_pairs]

    # UOJ/QOJ uses contiguous 1-based test filenames: 1.in .. N.in, 1.<ext> .. N.<ext>.
    # When subtasks are defined, topologically sort subtasks so dep_pos < pos, omit tests
    # already covered by transitive dependencies, duplicate tests shared by independent
    # subtasks so scoring semantics are preserved, and guarantee strict contiguous bounds
    # 0 < subtask_end_1 < ... < subtask_end_k == n_tests.
    ordered_subtasks: list[SubtaskSpec] = []
    ordered_indices: list[int] = list(range(1, len(safe_pairs) + 1))
    subtask_ends: list[tuple[SubtaskSpec, int]] = []

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
                d = stack.pop()
                if d in visited:
                    continue
                visited.add(d)
                covered.update(emitted_by_subtask.get(d, set()))
                stack.extend(direct_deps.get(d, []))
            return covered

        for st in ordered_subtasks:
            raw_indices = [
                orig_idx for orig_idx in st_map.get(st.id, []) if 1 <= orig_idx <= len(safe_pairs)
            ]
            if not raw_indices:
                raise ValueError(
                    f"QOJ export requires every subtask to map to at least one test "
                    f"(subtask {st.id} is empty)"
                )
            dep_covered = _transitive_emitted(st.id)
            own_indices = [idx for idx in raw_indices if idx not in dep_covered]
            if not own_indices:
                # Subtask's entire index set overlapped its transitive deps; emit its own indices
                # explicitly so subtask_end_k > subtask_end_{k-1} remains strictly valid.
                own_indices = list(raw_indices)
            reordered.extend(own_indices)
            emitted_by_subtask[st.id] = set(own_indices) | dep_covered
            subtask_ends.append((st, len(reordered)))

        ordered_indices = reordered

    # Prepare clean output directory / zip destination only after all validation/toposort succeeded.
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
        data_dir = os.path.join(out_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        copied_count = 0
        omitted_empty_answers: list[str] = []
        stage_mode_used: set[str] = set()
        effective_stage_arg: ExportDataMode = "copy" if data_mode == "zip" else data_mode

        for new_idx, orig_idx in enumerate(ordered_indices, 1):
            _, orig_ans_name, src_in, src_ans = safe_pairs[orig_idx - 1]
            stage_mode_used.add(
                _stage_data_file(src_in, os.path.join(data_dir, f"{new_idx}.in"), effective_stage_arg)
            )
            if answers_optional and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
                if orig_ans_name is not None:
                    omitted_empty_answers.append(orig_ans_name)
            elif src_ans is not None:
                stage_mode_used.add(
                    _stage_data_file(
                        src_ans,
                        os.path.join(data_dir, f"{new_idx}.{ext_no_dot}"),
                        effective_stage_arg,
                    )
                )
            copied_count += 1

        exported_files: list[str] = ["problem.conf"]
        if _copy_if_safe_file(
            problem_dir, manifest.statement_path, os.path.join(out_dir, "statement.md")
        ) or _copy_if_safe_file(
            problem_dir, "statements/README.md", os.path.join(out_dir, "statement.md")
        ):
            exported_files.append("statement.md")

        if _copy_if_safe_file(problem_dir, "files/testlib.h", os.path.join(out_dir, "testlib.h")):
            exported_files.append("testlib.h")

        tl_sec = max(1, math.ceil(time_limit_ms / 1000.0))
        # Omit empty input_pre/output_pre (UOJ/QOJ defaults omitted prefixes to "";
        # writing bare keys with trailing space breaks whitespace-tokenized load_config).
        conf_lines: list[str] = [
            "use_builtin_judger on",
            f"n_tests {copied_count}",
            "n_ex_tests 0",
            "n_sample_tests 0",
            "input_suf in",
            f"output_suf {ext_no_dot}",
            f"time_limit {tl_sec}",
            f"time_limit_ms {time_limit_ms}",
            f"memory_limit {memory_limit_mb}",
            "output_limit 64",
        ]

        if is_interactive:
            conf_lines.append("interaction_mode on")
            if _copy_if_safe_file(
                problem_dir, "files/interactor.cpp", os.path.join(out_dir, "interactor.cpp")
            ) or _copy_if_safe_file(
                problem_dir, "interactor.cpp", os.path.join(out_dir, "interactor.cpp")
            ):
                conf_lines.append("interactor interactor.cpp")
                exported_files.append("interactor.cpp")
        elif is_communication:
            conf_lines.append(f"communication_mode {comm_mode or 'run_twice'}")
            if comm_mode == "grader_linked":
                conf_lines.append("with_implementer on")
                grader_rel = resolve_grader_source_existing(problem_dir, manifest)
                if grader_rel:
                    grader_name = os.path.basename(grader_rel)
                    if _copy_if_safe_file(problem_dir, grader_rel, os.path.join(out_dir, grader_name)):
                        conf_lines.append(f"implementer {grader_name}")
                        exported_files.append(grader_name)
                hdr_rel = (
                    manifest.protocol.header_file
                    if (manifest.protocol and manifest.protocol.header_file)
                    else "files/problem.h"
                )
                if hdr_rel:
                    hdr_name = os.path.basename(hdr_rel)
                    if _copy_if_safe_file(problem_dir, hdr_rel, os.path.join(out_dir, hdr_name)):
                        conf_lines.append(f"header {hdr_name}")
                        exported_files.append(hdr_name)
            elif comm_mode == "multi_process":
                n_parts = max(
                    2,
                    len(manifest.protocol.participant_solutions) if manifest.protocol else 2,
                )
                conf_lines.append(f"n_participants {n_parts}")
                comm_rel = resolve_communicator_source_existing(problem_dir, manifest)
                if comm_rel:
                    comm_name = os.path.basename(comm_rel)
                    if _copy_if_safe_file(problem_dir, comm_rel, os.path.join(out_dir, comm_name)):
                        conf_lines.append(f"communicator {comm_name}")
                        exported_files.append(comm_name)
            else:
                # run_twice (QOJ two-stage communication)
                passes = manifest.protocol.passes if manifest.protocol else 2
                if passes == 2:
                    conf_lines.append("two_stage on")
                conf_lines.append(f"n_passes {passes}")
                comm_rel = resolve_communicator_source_existing(problem_dir, manifest)
                if comm_rel:
                    comm_name = os.path.basename(comm_rel)
                    if _copy_if_safe_file(problem_dir, comm_rel, os.path.join(out_dir, comm_name)):
                        conf_lines.append(f"communicator {comm_name}")
                        exported_files.append(comm_name)

            if manifest.protocol is not None:
                for k in ("max_bits", "max_bytes", "max_rounds", "max_queries"):
                    val = getattr(manifest.protocol, k, None)
                    if val is not None:
                        conf_lines.append(f"{k} {val}")
        elif spj_checker and _copy_if_safe_file(
            problem_dir, "files/checker.cpp", os.path.join(out_dir, "chk.cpp")
        ):
            conf_lines.append("checker chk.cpp")
            exported_files.append("chk.cpp")
        else:
            conf_lines.append("use_builtin_checker lcmp")

        if ordered_subtasks:
            conf_lines.append(f"n_subtasks {len(ordered_subtasks)}")
            # Map SubtaskSpec.id -> 1-based QOJ topological subtask position (dep_pos < pos guaranteed)
            id_to_pos = {st.id: pos for pos, st in enumerate(ordered_subtasks, 1)}
            for pos, (st, end_idx) in enumerate(subtask_ends, 1):
                conf_lines.append(f"subtask_end_{pos} {end_idx}")
                conf_lines.append(f"subtask_score_{pos} {st.score}")
                if st.dependencies:
                    dep_positions = [
                        str(id_to_pos[d]) for d in st.dependencies if d in id_to_pos
                    ]
                    if len(dep_positions) == 1:
                        conf_lines.append(f"subtask_dependence_{pos} {dep_positions[0]}")
                    elif len(dep_positions) > 1:
                        conf_lines.append(f"subtask_dependence_{pos} many")
                        for dep_i, dep_pos in enumerate(dep_positions, 1):
                            conf_lines.append(f"subtask_dependence_{pos}_{dep_i} {dep_pos}")

        config_validation = _validate_qoj_export_config(
            conf_lines,
            out_dir,
            data_dir,
            is_interactive=is_interactive,
            is_communication=is_communication,
            expected_tests=copied_count,
        )

        Path(out_dir, "problem.conf").write_text("\n".join(conf_lines) + "\n", encoding="utf-8")

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
            "format": "qoj",
            "output_dir": final_output_dir,
            "data_mode": data_mode,
            "effective_data_mode": effective_data_mode,
            "test_count": copied_count,
            "subtask_count": len(ordered_subtasks),
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


