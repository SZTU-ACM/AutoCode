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
from pathlib import Path
from typing import Any

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


def _prepare_clean_export_dir(problem_dir: str, rel_export_dir: str) -> str:
    """Resolve and cleanly recreate an export subdirectory strictly inside ``problem_dir``.

    Rejects traversal, root deletion, non-directory targets, and any symlink component
    along the path (both external and internal symlinks) before calling ``shutil.rmtree``.
    """
    if not isinstance(rel_export_dir, str) or not rel_export_dir.strip() or "\x00" in rel_export_dir:
        raise ValueError(f"Invalid export directory: {rel_export_dir!r}")
    normalized = rel_export_dir.replace("\\", "/").strip()
    if normalized.startswith("/") or _WINDOWS_DRIVE_RE.match(normalized) or Path(normalized).is_absolute():
        raise ValueError(f"Export directory escapes problem_dir: {rel_export_dir}")
    parts = [p for p in Path(normalized).parts if p not in ("", ".")]
    if not parts:
        raise ValueError("Export directory cannot be problem_dir root")
    if any(p == ".." for p in parts):
        raise ValueError(f"Export directory escapes problem_dir: {rel_export_dir}")

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
    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(target, exist_ok=True)
    return target


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
) -> list[tuple[str, str, str, str]]:
    """Validate and resolve ``(in_name, ans_name, src_in_abs, src_ans_abs)`` pairs strictly under ``problem_dir``."""
    norm_ext = normalize_answer_ext(answer_ext)
    if norm_ext is None:
        raise ValueError(f"Invalid answer_ext: {answer_ext!r}")

    pairs: list[tuple[str, str, str, str]] = []
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

        if os.path.isfile(src_in) and os.path.isfile(src_ans):
            pairs.append((in_file, ans_file, src_in, src_ans))
    return pairs


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
) -> dict[str, Any]:
    """Export problem package to Hydro OJ format under ``exports/hydro/``."""
    safe_pairs = _collect_safe_test_pairs(problem_dir, in_files, answer_ext)
    out_dir = _prepare_clean_export_dir(problem_dir, output_rel_dir)
    testdata_dir = os.path.join(out_dir, "testdata")
    os.makedirs(testdata_dir, exist_ok=True)

    is_interactive = manifest_is_interactive(manifest)
    is_communication = manifest_is_communication(manifest)
    spj_checker = manifest_uses_testlib_checker(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else None
    )

    # 1. problem.yaml
    tags: list[str] = ["autocode"]
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

    # 3. Copy validated (input, answer) test data pairs
    case_pairs: list[tuple[str, str]] = []
    for in_file, ans_file, src_in, src_ans in safe_pairs:
        shutil.copy2(src_in, os.path.join(testdata_dir, in_file))
        shutil.copy2(src_ans, os.path.join(testdata_dir, ans_file))
        case_pairs.append((in_file, ans_file))
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

    # 5. Subtasks in config.yaml
    if manifest.subtasks:
        st_map = map_tests_to_subtasks(valid_in_files, manifest.subtasks, tests_manifest)
        hydro_subtasks: list[dict[str, Any]] = []
        for st in manifest.subtasks:
            indices = st_map.get(st.id, [])
            st_cases = [
                {"input": case_pairs[i - 1][0], "output": case_pairs[i - 1][1]}
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
                "cases": [{"input": inp, "output": out} for inp, out in case_pairs],
            }
        ]

    Path(testdata_dir, "config.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    exported_files.append("testdata/config.yaml")

    return {
        "format": "hydro",
        "output_dir": out_dir,
        "test_count": len(case_pairs),
        "subtask_count": len(config["subtasks"]),
        "files": exported_files,
    }


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
) -> dict[str, Any]:
    """Export problem package to QOJ / UOJ format under ``exports/qoj/``."""
    safe_pairs = _collect_safe_test_pairs(problem_dir, in_files, answer_ext)
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

    # Prepare clean output directory only after all validation/toposort succeeded.
    out_dir = _prepare_clean_export_dir(problem_dir, output_rel_dir)
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(data_dir, exist_ok=True)

    is_interactive = manifest_is_interactive(manifest)
    is_communication = manifest_is_communication(manifest)
    spj_checker = manifest_uses_testlib_checker(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else None
    )

    copied_count = 0
    for new_idx, orig_idx in enumerate(ordered_indices, 1):
        _, _, src_in, src_ans = safe_pairs[orig_idx - 1]
        shutil.copy2(src_in, os.path.join(data_dir, f"{new_idx}.in"))
        shutil.copy2(src_ans, os.path.join(data_dir, f"{new_idx}.{ext_no_dot}"))
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

    Path(out_dir, "problem.conf").write_text("\n".join(conf_lines) + "\n", encoding="utf-8")

    return {
        "format": "qoj",
        "output_dir": out_dir,
        "test_count": copied_count,
        "subtask_count": len(ordered_subtasks),
        "files": exported_files,
    }
