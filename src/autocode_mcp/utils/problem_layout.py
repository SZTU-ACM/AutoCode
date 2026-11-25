"""Unified problem layout, Polygon ``problem.xml`` parsing, test-pair, and wrong-solution discovery."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ..workflow.models import ExpectedVerdict
from .answer_ext import normalize_answer_ext
from .communication_runner import is_safe_stem, safe_resolve_under_root

_MAX_POLYGON_XML_BYTES = 5 * 1024 * 1024
_MAX_POLYGON_TESTS = 10_000
_SAFE_POLYGON_TEST_PAT_RE = re.compile(
    r"^[A-Za-z0-9_./-]{0,100}%(?:0?[1-9])?d[A-Za-z0-9_./-]{0,32}$"
)
_WRONG_FILENAME_PREFIXES = (
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
_NON_TEST_FILENAMES = frozenset(
    {
        "checker_scenarios.json",
        "interactor_scenarios.json",
        "communicator_scenarios.json",
        "config.yaml",
        "problem.yaml",
        "problem.conf",
        "problem.xml",
        "readme.md",
        "readme.txt",
    }
)
_NON_TEST_EXTENSIONS = frozenset(
    {".json", ".yaml", ".yml", ".md", ".xml", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".py"}
)


def _natural_sort_key(path: Path) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


@dataclass(frozen=True)
class PolygonSolutionRef:
    """Single solution entry declared inside Polygon ``problem.xml``."""

    path: str
    tag: str
    role: Literal["main", "reference", "wrong"]
    expected: Literal["pass", "fail"]
    expected_verdict: ExpectedVerdict | None = None
    language: Literal["cpp", "python"] = "cpp"


@dataclass
class PolygonProblemMeta:
    """Parsed and containment-validated metadata from Polygon ``problem.xml``."""

    time_limit_ms: int | None = None
    memory_limit_mb: int | None = None
    test_count: int | None = None
    input_path_pattern: str | None = None
    answer_path_pattern: str | None = None
    solutions: list[PolygonSolutionRef] = field(default_factory=list)
    checker_path: str | None = None
    validator_paths: list[str] = field(default_factory=list)
    interactor_path: str | None = None
    communication_mode: str | None = None
    grader_path: str | None = None
    header_path: str | None = None
    statement_paths: list[str] = field(default_factory=list)
    unsafe_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DiscoveredTestPair:
    """Containment-checked test input/answer pair discovered under a problem package."""

    index: int
    input_path: Path
    answer_path: Path | None
    input_name: str
    answer_name: str | None
    staged_stem: str
    answer_ext: str


def _is_safe_polygon_pattern(pattern: str | None) -> bool:
    if not isinstance(pattern, str):
        return False
    cleaned = pattern.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or ":" in cleaned or "\x00" in cleaned:
        return False
    if ".." in cleaned.split("/"):
        return False
    return bool(_SAFE_POLYGON_TEST_PAT_RE.fullmatch(cleaned))


def _map_polygon_solution_tag(
    raw_tag: str,
) -> tuple[Literal["main", "reference", "wrong"], Literal["pass", "fail"], ExpectedVerdict | None] | None:
    tag = raw_tag.strip().lower()
    if tag == "main":
        return ("main", "pass", None)
    if tag in ("accepted", "correct"):
        return ("reference", "pass", None)
    if tag == "wrong-answer":
        return ("wrong", "fail", "WA")
    if tag.startswith("time-limit-exceeded"):
        return ("wrong", "fail", "TLE")
    if tag.startswith("memory-limit-exceeded"):
        return ("wrong", "fail", "MLE")
    if tag in ("runtime-error", "crash"):
        return ("wrong", "fail", "RE")
    if tag in ("rejected", "incorrect", "failed", "presentation-error"):
        return ("wrong", "fail", None)
    return None


def parse_polygon_problem_xml(problem_path: Path) -> PolygonProblemMeta | None:
    """Safely parse Polygon ``problem.xml`` with XXE guards and root containment checks.

    Returns ``None`` when ``problem.xml`` does not exist. Raises ``ValueError`` when
    ``problem.xml`` escapes ``problem_path``, exceeds the size cap, contains disallowed
    ``<!DOCTYPE``/``<!ENTITY`` declarations, or is malformed XML.
    """
    raw_xml_path = problem_path / "problem.xml"
    if not raw_xml_path.exists() and not raw_xml_path.is_symlink():
        return None
    resolved_xml = safe_resolve_under_root(str(problem_path), "problem.xml")
    if resolved_xml is None:
        raise ValueError("problem.xml escapes problem_dir via symlink or invalid path")
    xml_file = Path(resolved_xml)
    if not xml_file.is_file():
        return None
    if xml_file.stat().st_size > _MAX_POLYGON_XML_BYTES:
        raise ValueError("problem.xml exceeds maximum allowed 5MB size")

    raw_text = xml_file.read_text(encoding="utf-8", errors="replace")
    upper_text = raw_text.upper()
    if "<!DOCTYPE" in upper_text or "<!ENTITY" in upper_text:
        raise ValueError("problem.xml contains disallowed DOCTYPE/ENTITY declarations")

    try:
        root = ET.fromstring(raw_text)
    except ET.ParseError as exc:
        raise ValueError(f"invalid problem.xml: {exc}") from exc

    meta = PolygonProblemMeta()
    real_root = os.path.realpath(str(problem_path))

    def _contain_rel(raw_rel: str | None) -> str | None:
        if not isinstance(raw_rel, str) or not raw_rel.strip():
            return None
        norm = raw_rel.strip().replace("\\", "/")
        resolved = safe_resolve_under_root(str(problem_path), norm)
        if resolved is None:
            meta.unsafe_paths.append(norm)
            return None
        return os.path.relpath(resolved, real_root).replace("\\", "/")

    testsets = root.findall(".//judging/testset")
    testset = next((ts for ts in testsets if ts.attrib.get("name") == "tests"), None)
    if testset is None and testsets:
        testset = testsets[0]

    if testset is not None:
        tl_node = testset.find("time-limit")
        if tl_node is not None and tl_node.text and tl_node.text.strip().isdigit():
            tl_val = int(tl_node.text.strip())
            if tl_val > 0:
                meta.time_limit_ms = tl_val

        ml_node = testset.find("memory-limit")
        if ml_node is not None and ml_node.text and ml_node.text.strip().isdigit():
            raw_bytes = int(ml_node.text.strip())
            if raw_bytes > 0:
                if raw_bytes % 1_000_000 == 0 and raw_bytes % (1024 * 1024) != 0:
                    meta.memory_limit_mb = raw_bytes // 1_000_000
                else:
                    meta.memory_limit_mb = raw_bytes // (1024 * 1024)

        tc_node = testset.find("test-count")
        if tc_node is not None and tc_node.text and tc_node.text.strip().isdigit():
            meta.test_count = int(tc_node.text.strip())

        in_pat_node = testset.find("input-path-pattern")
        if in_pat_node is not None and in_pat_node.text:
            cand_in = in_pat_node.text.strip().replace("\\", "/")
            if _is_safe_polygon_pattern(cand_in) and _contain_rel(cand_in % 1) is not None:
                meta.input_path_pattern = cand_in
            elif cand_in:
                meta.unsafe_paths.append(cand_in)

        ans_pat_node = testset.find("answer-path-pattern")
        if ans_pat_node is not None and ans_pat_node.text:
            cand_ans = ans_pat_node.text.strip().replace("\\", "/")
            if _is_safe_polygon_pattern(cand_ans) and _contain_rel(cand_ans % 1) is not None:
                meta.answer_path_pattern = cand_ans
            elif cand_ans:
                meta.unsafe_paths.append(cand_ans)

        chk_ts = testset.find("checker")
        if chk_ts is not None and meta.checker_path is None:
            meta.checker_path = _contain_rel(chk_ts.attrib.get("path"))

        int_ts = testset.find("interactor")
        if int_ts is not None and meta.interactor_path is None:
            meta.interactor_path = _contain_rel(int_ts.attrib.get("path"))

        comm_ts = testset.find("communication")
        if comm_ts is not None:
            mode_val = (comm_ts.attrib.get("mode") or "").strip()
            if mode_val:
                meta.communication_mode = mode_val
            meta.grader_path = _contain_rel(comm_ts.attrib.get("grader"))
            meta.header_path = _contain_rel(comm_ts.attrib.get("header"))

    # <assets> entries take precedence when present (standard Codeforces Polygon structure)
    assets_chk = root.find(".//assets/checker")
    if assets_chk is not None:
        src_el = assets_chk.find("source")
        chk_rel = _contain_rel(
            src_el.attrib.get("path") if src_el is not None else assets_chk.attrib.get("path")
        )
        if chk_rel is not None:
            meta.checker_path = chk_rel

    for val_el in root.findall(".//assets/validators/validator"):
        src_el = val_el.find("source")
        val_rel = _contain_rel(
            src_el.attrib.get("path") if src_el is not None else val_el.attrib.get("path")
        )
        if val_rel is not None and val_rel not in meta.validator_paths:
            meta.validator_paths.append(val_rel)

    assets_int = root.find(".//assets/interactor")
    if assets_int is not None:
        src_el = assets_int.find("source")
        int_rel = _contain_rel(
            src_el.attrib.get("path") if src_el is not None else assets_int.attrib.get("path")
        )
        if int_rel is not None:
            meta.interactor_path = int_rel

    seen_sols: set[str] = set()
    for sol_el in root.findall(".//assets/solutions/solution"):
        raw_tag = str(sol_el.attrib.get("tag", ""))
        mapped = _map_polygon_solution_tag(raw_tag)
        if mapped is None:
            continue
        role, expected, expected_verdict = mapped
        src_els = sol_el.findall("source")
        raw_sources: list[tuple[str | None, str]] = (
            [(s.attrib.get("path"), str(s.attrib.get("type", ""))) for s in src_els]
            if src_els
            else [(sol_el.attrib.get("path"), str(sol_el.attrib.get("type", "")))]
        )
        for raw_p, src_type in raw_sources:
            rel = _contain_rel(raw_p)
            if rel is None or rel in seen_sols:
                continue
            seen_sols.add(rel)
            lang: Literal["cpp", "python"] = (
                "python"
                if (rel.lower().endswith(".py") or src_type.strip().lower().startswith("python"))
                else "cpp"
            )
            meta.solutions.append(
                PolygonSolutionRef(
                    path=rel,
                    tag=raw_tag.strip().lower(),
                    role=role,
                    expected=expected,
                    expected_verdict=expected_verdict,
                    language=lang,
                )
            )

    for stmt_el in root.findall(".//statements/statement"):
        stmt_rel = _contain_rel(stmt_el.attrib.get("path"))
        if stmt_rel is not None and stmt_rel not in meta.statement_paths:
            meta.statement_paths.append(stmt_rel)

    return meta


def is_wrong_solution_filename(path_or_name: str | Path) -> bool:
    """Return True for canonical wrong-solution filenames or files under a ``wrong/`` directory."""
    p = Path(path_or_name)
    name = p.name
    if not name.endswith(".cpp") or not is_safe_stem(p.stem):
        return False
    if any(part.lower() == "wrong" for part in p.parts[:-1]):
        return True
    lower = name.lower()
    return lower == "bf.cpp" or lower.startswith(_WRONG_FILENAME_PREFIXES)


def discover_wrong_solution_files(
    problem_path: Path,
    *,
    search_dirs: tuple[str, ...] | None = None,
) -> list[Path]:
    """Discover containment-checked wrong-solution ``.cpp`` paths under ``problem_path``."""
    dirs = search_dirs or (
        "solutions",
        "solution",
        "solutions/wrong",
        "solution/wrong",
        "wrong",
    )
    found: list[Path] = []
    seen_real: set[str] = set()
    for rel_dir in dirs:
        resolved_dir = safe_resolve_under_root(str(problem_path), rel_dir)
        if resolved_dir is None:
            continue
        raw_dir = problem_path / rel_dir
        if raw_dir.is_symlink():
            continue
        dir_path = Path(resolved_dir)
        if not dir_path.is_dir():
            continue
        in_wrong_subdir = Path(rel_dir).name == "wrong"
        try:
            cpp_files = sorted(dir_path.glob("*.cpp"))
        except OSError:
            continue
        for wf in cpp_files:
            if wf.is_symlink() or not wf.is_file() or not is_safe_stem(wf.stem):
                continue
            resolved_wf = safe_resolve_under_root(str(problem_path), f"{rel_dir}/{wf.name}")
            if resolved_wf is None:
                continue
            if not in_wrong_subdir and not is_wrong_solution_filename(wf):
                continue
            norm_key = os.path.normcase(os.path.realpath(resolved_wf))
            if norm_key not in seen_real:
                seen_real.add(norm_key)
                found.append(Path(resolved_wf))
    return found


def _safe_contained_file(problem_path: Path, candidate: Path) -> Path | None:
    try:
        real_root = problem_path.resolve()
        rel = candidate.resolve().relative_to(real_root)
    except (OSError, RuntimeError, ValueError):
        return None
    resolved = safe_resolve_under_root(str(problem_path), rel.as_posix())
    if resolved is None:
        return None
    p = Path(resolved)
    return p if p.is_file() else None


def _derive_staged_stem(input_path: Path, answer_path: Path | None, index: int) -> str:
    in_stem = input_path.stem if input_path.suffix else input_path.name
    if answer_path is not None:
        ans_stem = answer_path.stem
        if ans_stem != in_stem:
            return f"{index:02d}"
    cleaned = in_stem.replace(".", "_")
    if is_safe_stem(cleaned) and (cleaned.isdigit() or re.fullmatch(r"[A-Za-z0-9_-]*\d+", cleaned)):
        return cleaned
    return f"{index:02d}"


def _derive_answer_ext(
    input_path: Path,
    answer_path: Path | None,
    fallback_ext: str,
) -> str:
    if answer_path is None:
        return fallback_ext
    if answer_path.stem != (input_path.stem if input_path.suffix else input_path.name):
        return ".ans"
    norm = normalize_answer_ext(answer_path.suffix)
    return norm if norm is not None else fallback_ext


def _pattern_to_filename_regex(pattern: str) -> re.Pattern[str] | None:
    name_pat = Path(pattern).name
    m = re.search(r"%(?:0?[1-9])?d", name_pat)
    if not m:
        return None
    prefix = re.escape(name_pat[: m.start()])
    suffix = re.escape(name_pat[m.end() :])
    return re.compile(rf"^{prefix}(\d+){suffix}$")


def discover_test_pairs(
    problem_path: Path,
    *,
    tests_dir: Path | None = None,
    input_pattern: str | None = None,
    answer_pattern: str | None = None,
    test_count: int | None = None,
    answer_ext: str | None = None,
    allow_missing_answer: bool = False,
) -> list[DiscoveredTestPair]:
    """Discover containment-checked test input/answer pairs across standard and Polygon layouts."""
    poly_meta: PolygonProblemMeta | None = None
    if input_pattern is None:
        try:
            poly_meta = parse_polygon_problem_xml(problem_path)
        except (ValueError, OSError):
            poly_meta = None

    eff_in_pat = input_pattern
    eff_ans_pat = answer_pattern
    eff_count = test_count

    if poly_meta is not None:
        if tests_dir is None and poly_meta.input_path_pattern:
            pat_dir_rel = Path(poly_meta.input_path_pattern).parent.as_posix()
            resolved_dir = (
                safe_resolve_under_root(str(problem_path), pat_dir_rel)
                if pat_dir_rel not in ("", ".")
                else str(problem_path.resolve())
            )
            if resolved_dir is not None and Path(resolved_dir).is_dir():
                tests_dir = Path(resolved_dir)
        if poly_meta.input_path_pattern and tests_dir is not None:
            pat_parent = (problem_path / poly_meta.input_path_pattern).parent
            try:
                if pat_parent.resolve() == tests_dir.resolve():
                    eff_in_pat = poly_meta.input_path_pattern
                    if eff_ans_pat is None:
                        eff_ans_pat = poly_meta.answer_path_pattern
                    if eff_count is None:
                        eff_count = poly_meta.test_count
            except OSError:
                pass

    if tests_dir is None:
        default_dir = safe_resolve_under_root(str(problem_path), "tests")
        if default_dir is None or not Path(default_dir).is_dir():
            return []
        tests_dir = Path(default_dir)

    try:
        real_root = problem_path.resolve()
        real_tests = tests_dir.resolve()
        real_tests.relative_to(real_root)
    except (OSError, RuntimeError, ValueError):
        return []
    if not real_tests.is_dir():
        return []

    norm_explicit_ext = normalize_answer_ext(answer_ext)
    pairs: list[DiscoveredTestPair] = []

    if eff_in_pat is not None and _is_safe_polygon_pattern(eff_in_pat):
        ans_pat_ok = eff_ans_pat is not None and _is_safe_polygon_pattern(eff_ans_pat)
        indices: list[int] = []
        if eff_count is not None and 0 < eff_count <= _MAX_POLYGON_TESTS:
            indices = list(range(1, eff_count + 1))
        else:
            name_re = _pattern_to_filename_regex(eff_in_pat)
            if name_re is not None:
                try:
                    entries = sorted(real_tests.iterdir(), key=_natural_sort_key)
                except OSError:
                    entries = []
                for entry in entries:
                    m = name_re.fullmatch(entry.name)
                    if m and entry.is_file():
                        indices.append(int(m.group(1)))

        for idx in indices:
            in_rel = eff_in_pat % idx
            in_resolved = safe_resolve_under_root(str(problem_path), in_rel)
            if in_resolved is None:
                continue
            in_p = Path(in_resolved)
            if not in_p.is_file():
                continue

            ans_p: Path | None = None
            if ans_pat_ok and eff_ans_pat is not None:
                ans_rel = eff_ans_pat % idx
                ans_resolved = safe_resolve_under_root(str(problem_path), ans_rel)
                if ans_resolved is not None and Path(ans_resolved).is_file():
                    ans_p = Path(ans_resolved)
            if ans_p is None:
                probe_exts = [norm_explicit_ext] if norm_explicit_ext else [".a", ".ans", ".out"]
                for ext in probe_exts:
                    if not ext:
                        continue
                    cand = in_p.with_suffix(ext) if in_p.suffix else in_p.parent / f"{in_p.name}{ext}"
                    safe_cand = _safe_contained_file(problem_path, cand)
                    if safe_cand is not None:
                        ans_p = safe_cand
                        break

            if ans_p is None and not allow_missing_answer:
                continue
            eff_ext = _derive_answer_ext(in_p, ans_p, norm_explicit_ext or ".ans")
            pairs.append(
                DiscoveredTestPair(
                    index=idx,
                    input_path=in_p,
                    answer_path=ans_p,
                    input_name=in_p.name,
                    answer_name=ans_p.name if ans_p is not None else None,
                    staged_stem=_derive_staged_stem(in_p, ans_p, idx),
                    answer_ext=eff_ext,
                )
            )
        if pairs:
            return pairs

    try:
        entries = sorted(
            (p for p in real_tests.iterdir() if p.is_file()),
            key=_natural_sort_key,
        )
    except OSError:
        return []

    in_suffix_files = [p for p in entries if p.suffix == ".in"]
    if in_suffix_files:
        if norm_explicit_ext is not None:
            ext_order = [norm_explicit_ext]
        else:
            has_ans = any(p.suffix == ".ans" for p in entries)
            has_out = any(p.suffix == ".out" for p in entries)
            has_a = any(p.suffix == ".a" for p in entries)
            ext_order = []
            if has_ans:
                ext_order.append(".ans")
            if has_out:
                ext_order.append(".out")
            if has_a:
                ext_order.append(".a")
            for default_ext in (".ans", ".out", ".a"):
                if default_ext not in ext_order:
                    ext_order.append(default_ext)

        for idx, in_raw in enumerate(in_suffix_files, 1):
            safe_in_file = _safe_contained_file(problem_path, in_raw)
            if safe_in_file is None:
                continue
            ans_p = None
            for ext in ext_order:
                safe_ans = _safe_contained_file(problem_path, safe_in_file.with_suffix(ext))
                if safe_ans is not None:
                    ans_p = safe_ans
                    break
            if ans_p is None and not allow_missing_answer:
                continue
            eff_ext = _derive_answer_ext(safe_in_file, ans_p, ext_order[0])
            pairs.append(
                DiscoveredTestPair(
                    index=idx,
                    input_path=safe_in_file,
                    answer_path=ans_p,
                    input_name=safe_in_file.name,
                    answer_name=ans_p.name if ans_p is not None else None,
                    staged_stem=_derive_staged_stem(safe_in_file, ans_p, idx),
                    answer_ext=eff_ext,
                )
            )
        return pairs

    # Fallback: extensionless Polygon files (e.g. tests/01 + tests/01.a) or custom paired files
    ans_probe_exts = [norm_explicit_ext] if norm_explicit_ext else [".a", ".ans", ".out"]
    candidate_inputs: list[tuple[Path, Path | None]] = []
    for raw_p in entries:
        lower_name = raw_p.name.lower()
        if lower_name.startswith(".") or lower_name in _NON_TEST_FILENAMES:
            continue
        if raw_p.suffix.lower() in (".a", ".ans", ".out") or raw_p.suffix.lower() in _NON_TEST_EXTENSIONS:
            continue
        safe_in = _safe_contained_file(problem_path, raw_p)
        if safe_in is None:
            continue
        matched_ans: Path | None = None
        for ext in ans_probe_exts:
            if not ext:
                continue
            cand = (
                safe_in.with_suffix(ext)
                if safe_in.suffix
                else safe_in.parent / f"{safe_in.name}{ext}"
            )
            safe_ans = _safe_contained_file(problem_path, cand)
            if safe_ans is not None:
                matched_ans = safe_ans
                break
        if matched_ans is not None or safe_in.name.isdigit():
            candidate_inputs.append((safe_in, matched_ans))

    for idx, (in_p, ans_p) in enumerate(candidate_inputs, 1):
        if ans_p is None and not allow_missing_answer:
            continue
        eff_ext = _derive_answer_ext(in_p, ans_p, ans_probe_exts[0] or ".a")
        pairs.append(
            DiscoveredTestPair(
                index=idx,
                input_path=in_p,
                answer_path=ans_p,
                input_name=in_p.name,
                answer_name=ans_p.name if ans_p is not None else None,
                staged_stem=_derive_staged_stem(in_p, ans_p, idx),
                answer_ext=eff_ext,
            )
        )
    return pairs


def stage_polygon_tests_to_dir(
    problem_path: Path,
    pairs: list[DiscoveredTestPair],
    target_tests_dir: Path,
    *,
    source_tests_dir: Path | None = None,
) -> str:
    """Copy containment-checked test pairs into ``target_tests_dir`` as ``<stem>.in`` + ``<stem><ext>``.

    Also stages orphan answer files matching the resolved extension from ``source_tests_dir`` so
    ``file_count`` checks accurately flag unpaired answer files without writing into ``problem_path``.
    """
    target_tests_dir.mkdir(parents=True, exist_ok=True)
    staged_answer_ext = next((p.answer_ext for p in pairs if p.answer_path is not None), None)
    if staged_answer_ext is None:
        staged_answer_ext = pairs[0].answer_ext if pairs else ".ans"
    staged_answer_ext = normalize_answer_ext(staged_answer_ext) or ".ans"

    used_stems: set[str] = set()
    staged_source_answers: set[Path] = set()
    for pair in pairs:
        safe_in = _safe_contained_file(problem_path, pair.input_path)
        if safe_in is None:
            raise ValueError(f"Test input escapes problem_dir: {pair.input_path}")
        stem = pair.staged_stem
        if stem in used_stems or not is_safe_stem(stem):
            stem = f"{pair.index:02d}"
        while stem in used_stems:
            stem = f"{stem}_{pair.index}"
        used_stems.add(stem)

        (target_tests_dir / f"{stem}.in").write_bytes(safe_in.read_bytes())
        if pair.answer_path is not None:
            safe_ans = _safe_contained_file(problem_path, pair.answer_path)
            if safe_ans is None:
                raise ValueError(f"Test answer escapes problem_dir: {pair.answer_path}")
            staged_source_answers.add(safe_ans.resolve())
            (target_tests_dir / f"{stem}{staged_answer_ext}").write_bytes(safe_ans.read_bytes())

    if source_tests_dir is not None and source_tests_dir.is_dir():
        try:
            src_entries = sorted(source_tests_dir.iterdir(), key=_natural_sort_key)
        except OSError:
            src_entries = []
        for entry in src_entries:
            if not entry.is_file() or not entry.name.endswith(staged_answer_ext):
                continue
            safe_orphan = _safe_contained_file(problem_path, entry)
            if safe_orphan is None or safe_orphan.resolve() in staged_source_answers:
                continue
            dst = target_tests_dir / entry.name
            if not dst.exists():
                dst.write_bytes(safe_orphan.read_bytes())

    return staged_answer_ext


__all__ = [
    "DiscoveredTestPair",
    "PolygonProblemMeta",
    "PolygonSolutionRef",
    "discover_test_pairs",
    "discover_wrong_solution_files",
    "is_wrong_solution_filename",
    "parse_polygon_problem_xml",
    "stage_polygon_tests_to_dir",
]
