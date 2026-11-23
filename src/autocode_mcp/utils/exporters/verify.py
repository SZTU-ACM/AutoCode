"""Exported Polygon / Hydro / QOJ package read-back verification."""

from __future__ import annotations

import os
from typing import Any

import yaml

from ..communication_runner import safe_resolve_under_root
from .common import (
    _MAX_QOJ_VERIFY_TESTS,
    _MAX_VERIFY_XML_BYTES,
    _SAFE_POLYGON_TEST_PAT_RE,
    _SAFE_QOJ_SUF_RE,
    _export_has_member,
    _extract_polygon_xml_refs,
    _inspect_zip_members,
    _is_safe_export_rel_path,
    _parse_hydro_mem_mb,
    _parse_hydro_time_ms,
    _read_export_text,
    _resolve_contained_container,
)


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
        if (
            os.path.isfile(os.path.join(problem_dir, "problem.xml"))
            or os.path.isdir(os.path.join(problem_dir, "exports", "polygon"))
            or os.path.isfile(os.path.join(problem_dir, "exports", "polygon.zip"))
        ):
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

    def _verify_polygon_container(container_path: str, label: str, *, strict_workspace_sync: bool) -> tuple[list[str], list[str], dict[str, Any]]:
        c_errors: list[str] = []
        c_warnings: list[str] = []
        c_meta: dict[str, Any] = {"present": False, "container": container_path}
        zip_names, zip_unsafe = _inspect_zip_members(container_path)
        if zip_unsafe:
            c_errors.append(f"{label}: unsafe zip archive members detected: {zip_unsafe[:10]}")
            return c_errors, c_warnings, c_meta
        xml_raw = _read_export_text(container_path, "problem.xml", max_bytes=_MAX_VERIFY_XML_BYTES)
        if xml_raw is None:
            c_errors.append(f"{label}: problem.xml missing, unreadable, or exceeds 5MB limit")
            return c_errors, c_warnings, c_meta
        c_meta["present"] = True
        if "<!DOCTYPE" in xml_raw.upper() or "<!ENTITY" in xml_raw.upper():
            c_errors.append(f"{label}: problem.xml contains disallowed DOCTYPE/ENTITY declarations")
            return c_errors, c_warnings, c_meta
        try:
            root = ET.fromstring(xml_raw)
        except ET.ParseError as exc:
            c_errors.append(f"{label}: failed to parse problem.xml: {exc}")
            return c_errors, c_warnings, c_meta

        tl_node = root.find(".//judging/testset/time-limit")
        ml_node = root.find(".//judging/testset/memory-limit")
        tc_node = root.find(".//judging/testset/test-count")
        in_pat_node = root.find(".//judging/testset/input-path-pattern")
        ans_pat_node = root.find(".//judging/testset/answer-path-pattern")
        is_xml_interactive = root.find(".//judging/testset/interactor") is not None and root.find(".//judging/testset/communication") is None
        try:
            tl_val = int(tl_node.text.strip()) if (tl_node is not None and tl_node.text) else None
            ml_bytes = int(ml_node.text.strip()) if (ml_node is not None and ml_node.text) else None
            tc_val = int(tc_node.text.strip()) if (tc_node is not None and tc_node.text) else None
        except ValueError as exc:
            c_errors.append(f"{label}: invalid numeric limit in problem.xml: {exc}")
            return c_errors, c_warnings, c_meta

        c_meta.update(
            {
                "time_limit_ms": tl_val,
                "memory_limit_mb": (ml_bytes // (1024 * 1024)) if ml_bytes is not None else None,
                "test_count": tc_val,
            }
        )
        if tl_val != expected_time_limit_ms:
            c_errors.append(f"{label}: problem.xml time-limit={tl_val}ms != expected {expected_time_limit_ms}ms")
        expected_ml_mib = expected_memory_limit_mb * 1024 * 1024
        expected_ml_si = expected_memory_limit_mb * 1_000_000
        if ml_bytes not in (expected_ml_mib, expected_ml_si):
            c_errors.append(
                f"{label}: problem.xml memory-limit={ml_bytes} != expected {expected_ml_mib}"
            )
        if expected_test_count is not None and tc_val != expected_test_count:
            c_errors.append(f"{label}: problem.xml test-count={tc_val} != expected {expected_test_count}")

        req_refs, opt_refs = _extract_polygon_xml_refs(root)
        dangling_refs: list[str] = []
        for ref in req_refs:
            if not _is_safe_export_rel_path(ref):
                c_errors.append(f"{label}: unsafe path reference in problem.xml: {ref}")
            elif not _export_has_member(container_path, ref, zip_names=zip_names):
                dangling_refs.append(ref)
        for ref in opt_refs:
            if not _is_safe_export_rel_path(ref):
                c_errors.append(f"{label}: unsafe solution path reference in problem.xml: {ref}")
                continue
            if not _export_has_member(container_path, ref, zip_names=zip_names):
                ws_src = safe_resolve_under_root(problem_dir, ref)
                if strict_workspace_sync and ws_src is not None and os.path.isfile(ws_src):
                    dangling_refs.append(ref)
                else:
                    c_warnings.append(f"{label}: optional solution path not found on disk: {ref}")

        if dangling_refs:
            c_errors.append(
                f"{label}: dangling problem.xml path references ({len(dangling_refs)}): {dangling_refs[:10]}"
            )

        # Verify test files referenced by input-path-pattern / answer-path-pattern
        if isinstance(tc_val, int) and 0 < tc_val <= _MAX_QOJ_VERIFY_TESTS:
            in_pat = (in_pat_node.text or "").strip() if in_pat_node is not None else "tests/%02d.in"
            ans_pat = (ans_pat_node.text or "").strip() if ans_pat_node is not None else "tests/%02d.ans"
            in_pat_ok = bool(_SAFE_POLYGON_TEST_PAT_RE.fullmatch(in_pat))
            ans_pat_ok = is_xml_interactive or bool(_SAFE_POLYGON_TEST_PAT_RE.fullmatch(ans_pat))
            if not in_pat_ok:
                c_errors.append(f"{label}: unsafe or invalid input-path-pattern: {in_pat!r}")
            if not ans_pat_ok:
                c_errors.append(f"{label}: unsafe or invalid answer-path-pattern: {ans_pat!r}")
            missing_tests: list[str] = []
            if in_pat_ok and ans_pat_ok:
                for idx in range(1, tc_val + 1):
                    in_rel = in_pat % idx
                    if not _is_safe_export_rel_path(in_rel) or not _export_has_member(
                        container_path, in_rel, zip_names=zip_names
                    ):
                        missing_tests.append(in_rel)
                    if not is_xml_interactive:
                        ans_rel = ans_pat % idx
                        if not _is_safe_export_rel_path(ans_rel) or not _export_has_member(
                            container_path, ans_rel, zip_names=zip_names
                        ):
                            missing_tests.append(ans_rel)
                    if len(missing_tests) >= 10:
                        break
            if missing_tests:
                c_errors.append(f"{label}: missing Polygon test files: {missing_tests[:10]}")
            c_meta["dangling_refs"] = dangling_refs
            c_meta["missing_tests"] = missing_tests
        elif isinstance(tc_val, int) and tc_val > _MAX_QOJ_VERIFY_TESTS:
            c_errors.append(
                f"{label}: problem.xml test-count={tc_val} exceeds safety cap {_MAX_QOJ_VERIFY_TESTS}"
            )
        elif tc_val is None or tc_val <= 0:
            c_errors.append(f"{label}: problem.xml test-count must be positive, got {tc_val}")

        return c_errors, c_warnings, c_meta

    if "polygon" in formats:
        p_errors: list[str] = []
        p_warnings: list[str] = []
        p_meta: dict[str, Any] = {"present": False}

        root_xml = safe_resolve_under_root(problem_dir, "problem.xml")
        has_root_xml = root_xml is not None and os.path.isfile(root_xml)

        p_raw_container = None
        if isinstance(exports_meta, dict) and isinstance(exports_meta.get("polygon"), dict):
            p_raw_container = (
                exports_meta["polygon"].get("archive_path")
                or exports_meta["polygon"].get("export_dir")
            )
            if p_raw_container and os.path.abspath(str(p_raw_container)) == os.path.abspath(problem_dir):
                p_raw_container = None
        if not p_raw_container:
            cand_zip = os.path.join(problem_dir, "exports", "polygon.zip")
            cand_dir = os.path.join(problem_dir, "exports", "polygon")
            if os.path.lexists(cand_zip):
                p_raw_container = cand_zip
            elif os.path.lexists(cand_dir):
                p_raw_container = cand_dir
        p_export_container = (
            _resolve_contained_container(problem_dir, p_raw_container)
            if p_raw_container
            else None
        )

        if p_raw_container is not None and p_export_container is None:
            p_errors.append("polygon export artifact missing or escapes problem_dir")
        if not has_root_xml and p_export_container is None:
            if not p_errors:
                p_errors.append("problem.xml missing or escapes problem_dir")
        else:
            if has_root_xml:
                re_errs, re_warns, re_meta = _verify_polygon_container(
                    os.path.abspath(problem_dir), "polygon(root)", strict_workspace_sync=False
                )
                p_errors.extend(re_errs)
                p_warnings.extend(re_warns)
                p_meta.update(re_meta)
            if p_export_container is not None:
                ex_errs, ex_warns, ex_meta = _verify_polygon_container(
                    p_export_container, "polygon(export)", strict_workspace_sync=True
                )
                p_errors.extend(ex_errs)
                p_warnings.extend(ex_warns)
                if not has_root_xml:
                    p_meta.update(ex_meta)
                else:
                    p_meta["export_package"] = ex_meta

        checks["polygon"] = {"passed": not p_errors, "errors": p_errors, **p_meta}
        errors.extend(p_errors)
        warnings.extend(p_warnings)

    if "hydro" in formats:
        h_raw_container = None
        if isinstance(exports_meta, dict) and isinstance(exports_meta.get("hydro"), dict):
            h_raw_container = exports_meta["hydro"].get("archive_path") or exports_meta["hydro"].get("output_dir")
        if not h_raw_container:
            cand_zip = os.path.join(problem_dir, "exports", "hydro.zip")
            cand_dir = os.path.join(problem_dir, "exports", "hydro")
            h_raw_container = cand_zip if os.path.lexists(cand_zip) else (cand_dir if os.path.lexists(cand_dir) else None)
        h_container = _resolve_contained_container(problem_dir, h_raw_container)
        h_errors: list[str] = []
        h_meta: dict[str, Any] = {"present": h_container is not None}
        if h_container is None:
            h_errors.append("hydro export artifact missing or escapes problem_dir")
        else:
            h_zip_names, h_zip_unsafe = _inspect_zip_members(h_container)
            if h_zip_unsafe:
                h_errors.append(f"hydro export contains unsafe zip members: {h_zip_unsafe[:10]}")
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
                                    if "/" in fname or "\\" in fname or not _is_safe_export_rel_path(f"testdata/{fname}"):
                                        h_errors.append(f"hydro config.yaml contains unsafe case filename: {fname!r}")
                                        continue
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
            q_raw_container = cand_zip if os.path.lexists(cand_zip) else (cand_dir if os.path.lexists(cand_dir) else None)
        q_container = _resolve_contained_container(problem_dir, q_raw_container)
        q_errors: list[str] = []
        q_meta: dict[str, Any] = {"present": q_container is not None}
        if q_container is None:
            q_errors.append("qoj export artifact missing or escapes problem_dir")
        else:
            q_zip_names, q_zip_unsafe = _inspect_zip_members(q_container)
            if q_zip_unsafe:
                q_errors.append(f"qoj export contains unsafe zip members: {q_zip_unsafe[:10]}")
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
                output_suf_safe = bool(_SAFE_QOJ_SUF_RE.fullmatch(output_suf))
                if not output_suf_safe:
                    q_errors.append(f"qoj problem.conf contains unsafe output_suf: {output_suf!r}")
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
                            if not is_qoj_interactive and output_suf_safe and not _export_has_member(
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
