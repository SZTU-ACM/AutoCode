"""Multi-OJ exporters (Hydro and QOJ/UOJ) plus Polygon subtask mapping helpers.

All exporters validate paths against ``problem_dir`` using ``safe_resolve_under_root``
and explicit symlink-component checks so neither source assets nor export destinations
can escape the problem directory or clobber internal problem subdirectories.
"""

from __future__ import annotations

import heapq
import math
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

import yaml

from ..workflow import (
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from ..workflow.models import AutoCodeManifest, SubtaskSpec
from .answer_ext import normalize_answer_ext
from .communication_runner import (
    is_safe_stem,
    resolve_communicator_source_existing,
    resolve_grader_source_existing,
    safe_resolve_under_root,
)

ExportDataMode = Literal["copy", "hardlink", "zip"]
_ALLOWED_DATA_MODES = frozenset({"copy", "hardlink", "zip"})
_RESERVED_DIRS = frozenset({"tests", "files", "solutions", "statements", ".autocode", "scripts"})

_WINDOWS_DRIVE_RE = re.compile(r"^[a-zA-Z]:")

# Canonical aliases linking generator type_param values to both strategy names
# and default_manifest() case_plan group names.
_TYPE_TO_GROUP_ALIASES: dict[str, frozenset[str]] = {
    "1": frozenset({"tiny", "sanity"}),
    "2": frozenset({"random", "coverage"}),
    "3": frozenset({"extreme", "limit"}),
    "4": frozenset({"tle", "limit"}),
}


def map_tests_to_subtasks(
    in_files: list[str],
    subtasks: list[SubtaskSpec],
    tests_manifest: dict[str, Any] | None = None,
) -> dict[int, list[int]]:
    """Map each SubtaskSpec.id to sorted 1-based test indices (within ``1..len(in_files)``).

    Resolution order per subtask:
    1. Explicit ``subtask.test_indices`` (clamped to valid 1-based indices).
    2. Matching ``subtask.groups`` against test metadata looked up by ``in_file``
       filename first (falling back to 1-based ``index``), checking both ``group``
       and ``type_param`` aliases (``tiny``/``sanity``, ``random``/``coverage``,
       ``extreme``/``tle``/``limit``).
    3. Fallback proportional partitioning across subtasks that specified neither.
    """
    total = len(in_files)
    if not subtasks or total == 0:
        return {}

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

    mapping: dict[int, list[int]] = {}
    unassigned_subtasks: list[SubtaskSpec] = []
    assigned_indices: set[int] = set()

    for st in subtasks:
        if st.test_indices:
            valid = sorted({i for i in st.test_indices if 1 <= i <= total})
            mapping[st.id] = valid
            assigned_indices.update(valid)
        elif st.groups:
            group_set = {g.strip().lower() for g in st.groups if g.strip()}
            matched: list[int] = []
            for idx, fname in enumerate(in_files, 1):
                item = meta_by_filename.get(fname) or meta_by_index.get(idx, {})
                g_name = str(item.get("group") or "").strip().lower()
                t_aliases = _TYPE_TO_GROUP_ALIASES.get(str(item.get("type_param") or ""), frozenset())
                if (g_name and g_name in group_set) or bool(t_aliases & group_set):
                    matched.append(idx)
            mapping[st.id] = matched
            assigned_indices.update(matched)
        else:
            unassigned_subtasks.append(st)

    if unassigned_subtasks:
        remaining = [i for i in range(1, total + 1) if i not in assigned_indices]
        if not remaining and not assigned_indices:
            remaining = list(range(1, total + 1))
        if not remaining:
            for st in unassigned_subtasks:
                mapping[st.id] = []
        else:
            chunk = max(1, math.ceil(len(remaining) / len(unassigned_subtasks)))
            for pos, st in enumerate(unassigned_subtasks):
                mapping[st.id] = remaining[pos * chunk : (pos + 1) * chunk]

    return mapping


def _toposort_subtasks(subtasks: list[SubtaskSpec]) -> list[SubtaskSpec]:
    """Return subtasks topologically ordered so every dependency precedes its dependent.

    Preserves original declaration order among ties. Raises ``ValueError`` on cycles.
    """
    if len(subtasks) <= 1:
        return list(subtasks)

    by_id: dict[int, tuple[int, SubtaskSpec]] = {
        st.id: (orig_pos, st) for orig_pos, st in enumerate(subtasks)
    }
    indegree: dict[int, int] = {st.id: 0 for st in subtasks}
    dependents: dict[int, list[int]] = {st.id: [] for st in subtasks}

    for st in subtasks:
        seen_deps: set[int] = set()
        for dep_id in st.dependencies:
            if dep_id == st.id:
                raise ValueError(f"Subtask {st.id} cannot depend on itself")
            if dep_id in by_id and dep_id not in seen_deps:
                seen_deps.add(dep_id)
                dependents[dep_id].append(st.id)
                indegree[st.id] += 1

    ready: list[tuple[int, int]] = [
        (orig_pos, st_id)
        for st_id, (orig_pos, _) in by_id.items()
        if indegree[st_id] == 0
    ]
    heapq.heapify(ready)

    ordered: list[SubtaskSpec] = []
    while ready:
        _, st_id = heapq.heappop(ready)
        ordered.append(by_id[st_id][1])
        for child_id in dependents[st_id]:
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                heapq.heappush(ready, (by_id[child_id][0], child_id))

    if len(ordered) != len(subtasks):
        raise ValueError("Subtask dependencies contain a cycle")
    return ordered


def _validate_export_rel_parts(rel_export_dir: str) -> list[str]:
    """Parse and validate a relative export directory path inside ``problem_dir``."""
    if not isinstance(rel_export_dir, str) or not rel_export_dir.strip() or "\x00" in rel_export_dir:
        raise ValueError(f"Invalid export directory: {rel_export_dir!r}")
    normalized = rel_export_dir.replace("\\", "/").strip().rstrip("/")
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized) or Path(normalized).is_absolute():
        raise ValueError(f"Export directory escapes problem_dir: {rel_export_dir}")
    parts = [p for p in Path(normalized).parts if p not in ("", ".")]
    if not parts:
        raise ValueError("Export directory cannot be problem_dir root")
    if any(p == ".." for p in parts):
        raise ValueError(f"Export directory escapes problem_dir: {rel_export_dir}")
    if parts[0].lower() in _RESERVED_DIRS:
        raise ValueError(
            f"Export directory targets reserved problem subdirectory {parts[0]!r}: {rel_export_dir}"
        )
    return parts


def _resolve_safe_export_dir(problem_dir: str, rel_export_dir: str) -> str:
    """Validate and resolve an export subdirectory strictly inside ``problem_dir`` without deleting it yet."""
    parts = _validate_export_rel_parts(rel_export_dir)
    real_root = os.path.realpath(problem_dir)
    cur = real_root
    for part in parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise ValueError(f"Export directory path contains a symlink: {rel_export_dir}")

    target = safe_resolve_under_root(problem_dir, "/".join(parts))
    if target is None:
        raise ValueError(f"Export directory escapes problem_dir: {rel_export_dir}")
    if os.path.normcase(target) == os.path.normcase(real_root):
        raise ValueError("Export directory cannot be problem_dir root")
    if os.path.exists(target) and not os.path.isdir(target):
        raise ValueError(f"Export target exists and is not a directory: {rel_export_dir}")
    return target


def _prepare_clean_export_dir(problem_dir: str, rel_export_dir: str) -> str:
    """Resolve and cleanly recreate an export subdirectory strictly inside ``problem_dir``.

    Rejects traversal, root deletion, reserved problem subdirectories, non-directory targets,
    and any symlink component along the path before calling ``shutil.rmtree``.
    """
    target = _resolve_safe_export_dir(problem_dir, rel_export_dir)
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    return target


def _commit_staged_export_dir(stage_dir: str, target_dir: str) -> None:
    """Atomically swap a validated staging directory into ``target_dir``."""
    if os.path.islink(target_dir):
        raise ValueError(f"Export target is a symlink: {target_dir}")
    if os.path.exists(target_dir):
        if not os.path.isdir(target_dir):
            raise ValueError(f"Export target exists and is not a directory: {target_dir}")
        shutil.rmtree(target_dir)
    os.rename(stage_dir, target_dir)


def _cleanup_stale_sibling(target_path: str, *, is_zip: bool) -> None:
    """Remove stale sibling export artifact left from a previous export_data_mode."""
    if is_zip:
        if target_path.endswith(".zip"):
            sibling_dir = target_path[:-4]
            if os.path.isdir(sibling_dir) and not os.path.islink(sibling_dir):
                shutil.rmtree(sibling_dir, ignore_errors=True)
    else:
        sibling_zip = f"{target_path}.zip"
        if os.path.isfile(sibling_zip) and not os.path.islink(sibling_zip):
            try:
                os.unlink(sibling_zip)
            except OSError:
                pass


def _prepare_safe_zip_path(problem_dir: str, rel_export_dir: str) -> str:
    """Validate and resolve ``<problem_dir>/<rel_export_dir>.zip`` strictly under ``problem_dir``."""
    parts = _validate_export_rel_parts(rel_export_dir)
    zip_parts = [*parts[:-1], f"{parts[-1]}.zip"]
    real_root = os.path.realpath(problem_dir)
    cur = real_root
    for part in zip_parts:
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise ValueError(f"Export archive path contains a symlink: {rel_export_dir}")

    target = safe_resolve_under_root(problem_dir, "/".join(zip_parts))
    if target is None:
        raise ValueError(f"Export archive escapes problem_dir: {rel_export_dir}")
    if os.path.normcase(target) == os.path.normcase(real_root):
        raise ValueError("Export archive cannot be problem_dir root")
    if os.path.exists(target) and os.path.isdir(target):
        raise ValueError(f"Export archive target exists and is a directory: {target}")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    return target


def _zip_directory(src_dir: str, zip_path: str) -> None:
    """Atomically create a deterministic deflated zip archive of ``src_dir`` at ``zip_path``."""
    src_root = Path(src_dir)
    all_entries = sorted(src_root.rglob("*"))
    all_files: list[Path] = []
    for entry in all_entries:
        if entry.is_symlink():
            raise ValueError(f"Refusing to archive symlink inside export staging dir: {entry}")
        if entry.is_file():
            all_files.append(entry)

    parent_dir = os.path.dirname(zip_path) or "."
    os.makedirs(parent_dir, exist_ok=True)
    tmp_zip: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=parent_dir,
            prefix=".tmp_export_",
            suffix=".zip",
            delete=False,
        ) as tmp_f:
            tmp_zip = tmp_f.name
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for file_path in all_files:
                arcname = file_path.relative_to(src_root).as_posix()
                zinfo = zipfile.ZipInfo(filename=arcname, date_time=(2020, 1, 1, 0, 0, 0))
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(zinfo, file_path.read_bytes())
        os.replace(tmp_zip, zip_path)
        tmp_zip = None
    finally:
        if tmp_zip is not None and os.path.lexists(tmp_zip):
            try:
                os.unlink(tmp_zip)
            except OSError:
                pass


def _is_effectively_empty_file(path: str) -> bool:
    """Return True if file is 0 bytes or whitespace-only."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return True
    if size == 0:
        return True
    if size > 64:
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return not f.read().strip()
    except OSError:
        return False


def _is_interactive_dummy_answer(path: str) -> bool:
    """Return True if an interactive answer file is empty or only a legacy 'OK' placeholder."""
    if _is_effectively_empty_file(path):
        return True
    try:
        if os.path.getsize(path) > 16:
            return False
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip().upper() == "OK"
    except OSError:
        return False


def _stage_data_file(src: str, dst: str, data_mode: ExportDataMode) -> str:
    """Stage a test data file via hardlink (with copy fallback) or copy."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if data_mode == "hardlink":
        if os.path.lexists(dst):
            os.unlink(dst)
        try:
            os.link(src, dst)
            return "hardlink"
        except OSError:
            shutil.copy2(src, dst)
            return "copy"
    shutil.copy2(src, dst)
    return "copy"


def _copy_if_safe_file(problem_dir: str, rel_src: str, dest_path: str) -> bool:
    src = safe_resolve_under_root(problem_dir, rel_src)
    if src is None or not os.path.isfile(src):
        return False
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    shutil.copy2(src, dest_path)
    return True


def _collect_safe_test_pairs(
    problem_dir: str,
    in_files: list[str],
    answer_ext: str,
    *,
    allow_missing_answer: bool = False,
) -> list[tuple[str, str | None, str, str | None]]:
    """Validate and resolve ``(in_name, ans_name, src_in_abs, src_ans_abs)`` pairs strictly under ``problem_dir``."""
    norm_ext = normalize_answer_ext(answer_ext)
    if norm_ext is None:
        raise ValueError(f"Invalid answer_ext: {answer_ext!r}")

    pairs: list[tuple[str, str | None, str, str | None]] = []
    for in_file in in_files:
        if (
            not isinstance(in_file, str)
            or os.path.basename(in_file) != in_file
            or not is_safe_stem(in_file)
            or not in_file.endswith(".in")
        ):
            raise ValueError(f"Unsafe test input filename: {in_file!r}")
        ans_file = f"{os.path.splitext(in_file)[0]}{norm_ext}"
        if os.path.basename(ans_file) != ans_file or not is_safe_stem(ans_file):
            raise ValueError(f"Unsafe test answer filename: {ans_file!r}")

        src_in = safe_resolve_under_root(problem_dir, f"tests/{in_file}")
        if src_in is None:
            raise ValueError(f"Test input escapes problem_dir: {in_file}")
        src_ans = safe_resolve_under_root(problem_dir, f"tests/{ans_file}")
        if src_ans is None:
            raise ValueError(f"Test answer escapes problem_dir: {ans_file}")

        if not os.path.isfile(src_in):
            continue
        if os.path.isfile(src_ans):
            pairs.append((in_file, ans_file, src_in, src_ans))
        elif allow_missing_answer:
            pairs.append((in_file, None, src_in, None))
    return pairs


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
    spj_checker = manifest_uses_testlib_checker(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else None
    )

    safe_pairs = _collect_safe_test_pairs(
        problem_dir, in_files, answer_ext, allow_missing_answer=is_interactive
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
            if is_interactive and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
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
    spj_checker = manifest_uses_testlib_checker(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else None
    )

    safe_pairs = _collect_safe_test_pairs(
        problem_dir, in_files, answer_ext, allow_missing_answer=is_interactive
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
            if is_interactive and (src_ans is None or _is_interactive_dummy_answer(src_ans)):
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


_MAX_VERIFY_MEMBER_BYTES = 2 * 1024 * 1024
_MAX_VERIFY_XML_BYTES = 5 * 1024 * 1024
_MAX_QOJ_VERIFY_TESTS = 10_000


def _read_export_text(container: str, rel_posix: str) -> str | None:
    """Read a UTF-8 text member from either an extracted export dir or a .zip archive safely."""
    if os.path.isfile(container) and container.endswith(".zip"):
        try:
            with zipfile.ZipFile(container, "r") as zf:
                try:
                    info = zf.getinfo(rel_posix)
                except KeyError:
                    return None
                if info.file_size > _MAX_VERIFY_MEMBER_BYTES:
                    return None
                return zf.read(info).decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile):
            return None
    resolved = safe_resolve_under_root(container, rel_posix)
    if resolved is None or not os.path.isfile(resolved):
        return None
    try:
        if os.path.getsize(resolved) > _MAX_VERIFY_MEMBER_BYTES:
            return None
        return Path(resolved).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _export_has_member(
    container: str,
    rel_posix: str,
    *,
    zip_names: set[str] | None = None,
) -> bool:
    """Check whether ``rel_posix`` exists strictly inside an export dir or .zip archive."""
    if os.path.isfile(container) and container.endswith(".zip"):
        if zip_names is not None:
            return rel_posix in zip_names
        try:
            with zipfile.ZipFile(container, "r") as zf:
                return rel_posix in zf.namelist()
        except (OSError, zipfile.BadZipFile):
            return False
    resolved = safe_resolve_under_root(container, rel_posix)
    return resolved is not None and os.path.isfile(resolved)


def _load_zip_member_set(container: str) -> set[str] | None:
    if not (os.path.isfile(container) and container.endswith(".zip")):
        return None
    try:
        with zipfile.ZipFile(container, "r") as zf:
            return set(zf.namelist())
    except (OSError, zipfile.BadZipFile):
        return None


def _resolve_contained_container(problem_dir: str, candidate: str | None) -> str | None:
    if not candidate:
        return None
    try:
        real_root = Path(problem_dir).resolve()
        cand_path = Path(candidate)
        if not cand_path.is_absolute():
            cand_path = real_root / cand_path
        real_cand = cand_path.resolve()
        real_cand.relative_to(real_root)
    except (OSError, ValueError):
        return None
    return str(real_cand) if real_cand.exists() else None


def _parse_hydro_time_ms(raw: Any) -> int | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if len(s) > 32:
        return None
    if s.endswith("ms"):
        try:
            val = float(s[:-2])
            return int(round(val)) if math.isfinite(val) else None
        except (ValueError, OverflowError):
            return None
    if s.endswith("s"):
        try:
            val = float(s[:-1]) * 1000.0
            return int(round(val)) if math.isfinite(val) else None
        except (ValueError, OverflowError):
            return None
    return None


def _parse_hydro_mem_mb(raw: Any) -> int | None:
    if not isinstance(raw, str):
        return None
    s = raw.strip().lower()
    if len(s) > 32:
        return None
    for suf, mult in (("mb", 1.0), ("m", 1.0), ("gb", 1024.0), ("g", 1024.0)):
        if s.endswith(suf):
            try:
                val = float(s[: -len(suf)]) * mult
                return int(round(val)) if math.isfinite(val) else None
            except (ValueError, OverflowError):
                return None
    return None


def verify_exported_packages(
    problem_dir: str,
    *,
    formats: list[str] | None = None,
    expected_time_limit_ms: int,
    expected_memory_limit_mb: int,
    expected_test_count: int | None = None,
    exports_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read back exported Polygon/Hydro/QOJ artifacts and verify TL, ML, test counts, files, and cleanliness."""
    import xml.etree.ElementTree as ET

    if formats is None:
        detected: list[str] = []
        if os.path.isfile(os.path.join(problem_dir, "problem.xml")):
            detected.append("polygon")
        if os.path.isdir(os.path.join(problem_dir, "exports", "hydro")) or os.path.isfile(
            os.path.join(problem_dir, "exports", "hydro.zip")
        ):
            detected.append("hydro")
        if os.path.isdir(os.path.join(problem_dir, "exports", "qoj")) or os.path.isfile(
            os.path.join(problem_dir, "exports", "qoj.zip")
        ):
            detected.append("qoj")
        formats = detected

    checks: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    warnings: list[str] = []

    if "polygon" in formats:
        xml_path = safe_resolve_under_root(problem_dir, "problem.xml")
        p_errors: list[str] = []
        p_meta: dict[str, Any] = {"present": xml_path is not None and os.path.isfile(xml_path)}
        if xml_path is None or not os.path.isfile(xml_path):
            p_errors.append("problem.xml missing or escapes problem_dir")
        else:
            try:
                if os.path.getsize(xml_path) > _MAX_VERIFY_XML_BYTES:
                    p_errors.append("problem.xml exceeds 5MB safety size limit")
                else:
                    xml_raw = Path(xml_path).read_text(encoding="utf-8", errors="replace")
                    if "<!DOCTYPE" in xml_raw.upper() or "<!ENTITY" in xml_raw.upper():
                        p_errors.append("problem.xml contains disallowed DOCTYPE/ENTITY declarations")
                    else:
                        root = ET.fromstring(xml_raw)
                        tl_node = root.find(".//judging/testset/time-limit")
                        ml_node = root.find(".//judging/testset/memory-limit")
                        tc_node = root.find(".//judging/testset/test-count")
                        tl_val = int(tl_node.text.strip()) if (tl_node is not None and tl_node.text) else None
                        ml_bytes = int(ml_node.text.strip()) if (ml_node is not None and ml_node.text) else None
                        tc_val = int(tc_node.text.strip()) if (tc_node is not None and tc_node.text) else None
                        p_meta.update(
                            {
                                "time_limit_ms": tl_val,
                                "memory_limit_mb": (ml_bytes // (1024 * 1024)) if ml_bytes is not None else None,
                                "test_count": tc_val,
                            }
                        )
                        if tl_val != expected_time_limit_ms:
                            p_errors.append(
                                f"problem.xml time-limit={tl_val}ms != expected {expected_time_limit_ms}ms"
                            )
                        if ml_bytes != expected_memory_limit_mb * 1024 * 1024:
                            p_errors.append(
                                f"problem.xml memory-limit={ml_bytes} != expected {expected_memory_limit_mb * 1024 * 1024}"
                            )
                        if expected_test_count is not None and tc_val != expected_test_count:
                            p_errors.append(
                                f"problem.xml test-count={tc_val} != expected {expected_test_count}"
                            )
            except (ET.ParseError, OSError, ValueError) as exc:
                p_errors.append(f"failed to parse problem.xml: {exc}")
        checks["polygon"] = {"passed": not p_errors, "errors": p_errors, **p_meta}
        errors.extend(p_errors)

    if "hydro" in formats:
        h_raw_container = None
        if isinstance(exports_meta, dict) and isinstance(exports_meta.get("hydro"), dict):
            h_raw_container = exports_meta["hydro"].get("archive_path") or exports_meta["hydro"].get("output_dir")
        if not h_raw_container:
            cand_zip = os.path.join(problem_dir, "exports", "hydro.zip")
            cand_dir = os.path.join(problem_dir, "exports", "hydro")
            h_raw_container = cand_zip if os.path.isfile(cand_zip) else (cand_dir if os.path.isdir(cand_dir) else None)
        h_container = _resolve_contained_container(problem_dir, h_raw_container)
        h_errors: list[str] = []
        h_meta: dict[str, Any] = {"present": h_container is not None}
        if h_container is None:
            h_errors.append("hydro export artifact missing or escapes problem_dir")
        else:
            h_zip_names = _load_zip_member_set(h_container)
            pyaml_text = _read_export_text(h_container, "problem.yaml")
            if pyaml_text is None:
                h_errors.append("hydro export missing problem.yaml")
            else:
                try:
                    pyaml = yaml.safe_load(pyaml_text) or {}
                except yaml.YAMLError as exc:
                    h_errors.append(f"hydro problem.yaml invalid YAML: {exc}")
                    pyaml = {}
                raw_tags = pyaml.get("tag") if isinstance(pyaml, dict) else []
                if isinstance(raw_tags, list) and any(
                    isinstance(t, str) and t.strip().lower() in {"autocode", "mcp__autocode"}
                    for t in raw_tags
                ):
                    h_errors.append("hydro problem.yaml leaked internal 'autocode' tag")
            cfg_text = _read_export_text(h_container, "testdata/config.yaml")
            if cfg_text is None:
                h_errors.append("hydro export missing testdata/config.yaml")
            else:
                try:
                    cfg = yaml.safe_load(cfg_text) or {}
                except yaml.YAMLError as exc:
                    h_errors.append(f"hydro testdata/config.yaml invalid YAML: {exc}")
                    cfg = {}
                tl_val = _parse_hydro_time_ms(cfg.get("time")) if isinstance(cfg, dict) else None
                ml_val = _parse_hydro_mem_mb(cfg.get("memory")) if isinstance(cfg, dict) else None
                h_meta.update({"time_limit_ms": tl_val, "memory_limit_mb": ml_val})
                if tl_val != expected_time_limit_ms:
                    h_errors.append(
                        f"hydro config.yaml time={cfg.get('time')!r} != expected {expected_time_limit_ms}ms"
                    )
                if ml_val != expected_memory_limit_mb:
                    h_errors.append(
                        f"hydro config.yaml memory={cfg.get('memory')!r} != expected {expected_memory_limit_mb}m"
                    )
                if isinstance(cfg, dict) and isinstance(cfg.get("subtasks"), list):
                    referenced_inputs: set[str] = set()
                    for st in cfg["subtasks"]:
                        if not isinstance(st, dict):
                            continue
                        for case in st.get("cases", []) or []:
                            if not isinstance(case, dict):
                                continue
                            for field_key in ("input", "output"):
                                fname = case.get(field_key)
                                if isinstance(fname, str) and fname:
                                    if field_key == "input":
                                        referenced_inputs.add(fname)
                                    if not _export_has_member(
                                        h_container,
                                        f"testdata/{fname}",
                                        zip_names=h_zip_names,
                                    ):
                                        h_errors.append(f"hydro export missing testdata/{fname}")
                    h_meta["referenced_input_count"] = len(referenced_inputs)
                    if expected_test_count is not None and expected_test_count > 0:
                        if len(referenced_inputs) == 0:
                            h_errors.append(
                                f"hydro export references 0 inputs (expected {expected_test_count})"
                            )
                        elif len(referenced_inputs) > expected_test_count:
                            h_errors.append(
                                f"hydro export references {len(referenced_inputs)} inputs > expected {expected_test_count}"
                            )
                        elif len(cfg["subtasks"]) == 1 and len(referenced_inputs) < expected_test_count:
                            h_errors.append(
                                f"hydro export references {len(referenced_inputs)} inputs < expected {expected_test_count}"
                            )
        checks["hydro"] = {"passed": not h_errors, "errors": h_errors, **h_meta}
        errors.extend(h_errors)

    if "qoj" in formats:
        q_raw_container = None
        if isinstance(exports_meta, dict) and isinstance(exports_meta.get("qoj"), dict):
            q_raw_container = exports_meta["qoj"].get("archive_path") or exports_meta["qoj"].get("output_dir")
        if not q_raw_container:
            cand_zip = os.path.join(problem_dir, "exports", "qoj.zip")
            cand_dir = os.path.join(problem_dir, "exports", "qoj")
            q_raw_container = cand_zip if os.path.isfile(cand_zip) else (cand_dir if os.path.isdir(cand_dir) else None)
        q_container = _resolve_contained_container(problem_dir, q_raw_container)
        q_errors: list[str] = []
        q_meta: dict[str, Any] = {"present": q_container is not None}
        if q_container is None:
            q_errors.append("qoj export artifact missing or escapes problem_dir")
        else:
            q_zip_names = _load_zip_member_set(q_container)
            conf_text = _read_export_text(q_container, "problem.conf")
            if conf_text is None:
                q_errors.append("qoj export missing problem.conf")
            else:
                parsed: dict[str, str] = {}
                for line in conf_text.splitlines():
                    parts = line.strip().split(" ", 1)
                    if len(parts) == 2:
                        parsed[parts[0]] = parts[1].strip()
                def _safe_qoj_int(key: str) -> int | None:
                    val_s = parsed.get(key, "")
                    return int(val_s) if val_s.isdigit() and len(val_s) <= 18 else None

                tl_ms_val = _safe_qoj_int("time_limit_ms")
                ml_val = _safe_qoj_int("memory_limit")
                n_tests_val = _safe_qoj_int("n_tests")
                is_qoj_interactive = parsed.get("interaction_mode", "").lower() == "on"
                output_suf = parsed.get("output_suf", "ans").lstrip(".") or "ans"
                q_meta.update(
                    {
                        "time_limit_ms": tl_ms_val,
                        "memory_limit_mb": ml_val,
                        "n_tests": n_tests_val,
                    }
                )
                if tl_ms_val != expected_time_limit_ms:
                    q_errors.append(
                        f"qoj problem.conf time_limit_ms={tl_ms_val} != expected {expected_time_limit_ms}"
                    )
                if ml_val != expected_memory_limit_mb:
                    q_errors.append(
                        f"qoj problem.conf memory_limit={ml_val} != expected {expected_memory_limit_mb}"
                    )
                if expected_test_count is not None and expected_test_count > 0:
                    if n_tests_val is None or n_tests_val == 0:
                        q_errors.append(
                            f"qoj problem.conf n_tests={n_tests_val} (expected >= {expected_test_count})"
                        )
                    elif n_tests_val < expected_test_count:
                        q_errors.append(
                            f"qoj problem.conf n_tests={n_tests_val} < expected {expected_test_count}"
                        )
                if n_tests_val is not None:
                    if n_tests_val > _MAX_QOJ_VERIFY_TESTS:
                        q_errors.append(
                            f"qoj problem.conf n_tests={n_tests_val} exceeds safety cap {_MAX_QOJ_VERIFY_TESTS}"
                        )
                    else:
                        for idx in range(1, n_tests_val + 1):
                            if not _export_has_member(
                                q_container, f"data/{idx}.in", zip_names=q_zip_names
                            ):
                                q_errors.append(f"qoj export missing data/{idx}.in")
                                break
                            if not is_qoj_interactive and not _export_has_member(
                                q_container,
                                f"data/{idx}.{output_suf}",
                                zip_names=q_zip_names,
                            ):
                                q_errors.append(f"qoj export missing data/{idx}.{output_suf}")
                                break
        checks["qoj"] = {"passed": not q_errors, "errors": q_errors, **q_meta}
        errors.extend(q_errors)

    return {
        "passed": not errors,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }
