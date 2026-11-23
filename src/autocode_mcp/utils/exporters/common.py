"""Multi-OJ exporters shared helpers (subtask mapping, staging, zip, and read-back validation)."""

from __future__ import annotations

import heapq
import math
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

from ...workflow.models import SubtaskSpec
from ..answer_ext import normalize_answer_ext
from ..communication_runner import is_safe_stem, safe_resolve_under_root

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




_MAX_VERIFY_MEMBER_BYTES = 2 * 1024 * 1024
_MAX_VERIFY_XML_BYTES = 5 * 1024 * 1024
_MAX_QOJ_VERIFY_TESTS = 10_000


def _read_export_text(
    container: str,
    rel_posix: str,
    *,
    max_bytes: int = _MAX_VERIFY_MEMBER_BYTES,
) -> str | None:
    """Read a UTF-8 text member from either an extracted export dir or a .zip archive safely."""
    if os.path.isfile(container) and container.endswith(".zip"):
        try:
            with zipfile.ZipFile(container, "r") as zf:
                try:
                    info = zf.getinfo(rel_posix)
                except KeyError:
                    return None
                if stat.S_ISLNK(info.external_attr >> 16):
                    return None
                if info.file_size > max_bytes:
                    return None
                with zf.open(info, "r") as fp:
                    data = fp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    return None
                return data.decode("utf-8", errors="replace")
        except (OSError, zipfile.BadZipFile):
            return None
    resolved = safe_resolve_under_root(container, rel_posix)
    if resolved is None or not os.path.isfile(resolved):
        return None
    try:
        with open(resolved, "rb") as fp:
            data = fp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return data.decode("utf-8", errors="replace")
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


def _inspect_zip_members(container: str) -> tuple[set[str] | None, list[str]]:
    """Return ``(member_names, unsafe_members)`` for a zip container (or ``(None, [])`` for a dir)."""
    if not (os.path.isfile(container) and container.endswith(".zip")):
        return None, []
    try:
        with zipfile.ZipFile(container, "r") as zf:
            names: set[str] = set()
            unsafe: list[str] = []
            for info in zf.infolist():
                raw_name = info.filename
                if stat.S_ISLNK(info.external_attr >> 16):
                    unsafe.append(f"symlink:{raw_name}")
                    continue
                if "\\" in raw_name or not _is_safe_export_rel_path(raw_name.rstrip("/")):
                    if not (raw_name.endswith("/") and _is_safe_export_rel_path(raw_name.rstrip("/"))):
                        unsafe.append(raw_name)
                        continue
                names.add(raw_name)
            return names, unsafe
    except (OSError, zipfile.BadZipFile):
        return None, ["invalid_or_corrupt_zip_archive"]


def _load_zip_member_set(container: str) -> set[str] | None:
    names, _ = _inspect_zip_members(container)
    return names


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
    except (OSError, RuntimeError, ValueError):
        return None
    return str(real_cand) if real_cand.exists() else None


def _parse_hydro_time_ms(raw: Any) -> int | None:
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        val = float(raw)
        if math.isfinite(val) and val > 0:
            return int(round(val))
        return None
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
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        val = float(raw)
        if math.isfinite(val) and val > 0:
            return int(round(val))
        return None
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


_SAFE_POLYGON_TEST_PAT_RE = re.compile(
    r"^[A-Za-z0-9_./-]{0,100}%(?:0?[1-9])?d[A-Za-z0-9_./-]{0,32}$"
)
_SAFE_QOJ_SUF_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
_INTERNAL_EXPORT_TOP_DIRS = frozenset({".autocode", "exports"})


def _is_safe_export_rel_path(rel_path: str, *, disallow_internal: bool = False) -> bool:
    if not isinstance(rel_path, str):
        return False
    cleaned = rel_path.replace("\\", "/").strip()
    if not cleaned or cleaned.startswith("/") or ":" in cleaned or "\x00" in cleaned:
        return False
    parts = [p for p in cleaned.split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return False
    if disallow_internal and parts[0].lower() in _INTERNAL_EXPORT_TOP_DIRS:
        return False
    return True


def _extract_polygon_xml_refs(root: Any) -> tuple[list[str], list[str]]:
    """Extract (required_refs, optional_solution_refs) relative paths from parsed problem.xml."""
    required: list[str] = []
    optional_sols: list[str] = []
    seen_req: set[str] = set()
    seen_opt: set[str] = set()

    def _add_req(val: str | None) -> None:
        if isinstance(val, str) and val.strip():
            norm = val.strip().replace("\\", "/")
            if norm not in seen_req:
                seen_req.add(norm)
                required.append(norm)

    def _add_opt(val: str | None) -> None:
        if isinstance(val, str) and val.strip():
            norm = val.strip().replace("\\", "/")
            if norm not in seen_opt and norm not in seen_req:
                seen_opt.add(norm)
                optional_sols.append(norm)

    for el in root.findall(".//statements/statement"):
        _add_req(el.attrib.get("path"))
    for el in root.findall(".//judging/testset/checker"):
        _add_req(el.attrib.get("path"))
    for el in root.findall(".//judging/testset/interactor"):
        _add_req(el.attrib.get("path"))
    for el in root.findall(".//judging/testset/communication"):
        _add_req(el.attrib.get("grader"))
        _add_req(el.attrib.get("header"))
    for el in root.findall(".//files/resources/file"):
        _add_req(el.attrib.get("path"))
    for el in root.findall(".//files/executables/executable/source"):
        _add_req(el.attrib.get("path"))
    for sol_el in root.findall(".//assets/solutions/solution"):
        tag = str(sol_el.attrib.get("tag", "")).strip().lower()
        for src_el in sol_el.findall("source"):
            if tag == "main":
                _add_req(src_el.attrib.get("path"))
            else:
                _add_opt(src_el.attrib.get("path"))

    return required, optional_sols

