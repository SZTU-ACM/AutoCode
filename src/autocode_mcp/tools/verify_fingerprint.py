"""Dataset fingerprinting, TLE policy validation, and shared test-manifest helpers."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, TypeVar, cast

from ..runtime_store import TEST_MANIFEST, get_section
from ..utils.answer_ext import normalize_answer_ext
from ..utils.communication_runner import (
    is_safe_stem,
    resolve_participant_binaries,
    safe_resolve_under_root,
)
from ..utils.platform import get_exe_extension
from ..workflow import (
    AutoCodeManifest,
    current_platform,
    manifest_is_communication,
)

_T = TypeVar("_T")


def _tv_facade_attr(name: str, default: _T) -> _T:
    """Resolve patchable attribute through ``autocode_mcp.tools.test_verify`` facade."""
    mod = sys.modules.get("autocode_mcp.tools.test_verify")
    return cast(_T, getattr(mod, name, default)) if mod is not None else default


_LIMIT_STRATEGY_TYPES = frozenset({"3", "4"})
_LIMIT_GROUPS = frozenset({"limit", "extreme", "tle"})
_VERIFY_CONCURRENCY = 4
_SIGNAL_MAP: dict[str, str] = {
    "file_count": "file_count",
    "answer_consistency": "answer_consistency",
    "validator": "validator_check",
    "no_empty": "no_empty",
    "limit_ratio": "limit_ratio",
    "limit_semantics": "limit_semantics",
    "wrong_solution_kill": "wrong_solution_kill",
    "duplicate_inputs": "duplicate_inputs",
    "scale_distribution": "scale_distribution",
    "purpose_coverage": "purpose_coverage",
    "boundary_coverage": "boundary_coverage",
    "validator_self_test": "validator_self_test",
    "checker_self_test": "checker_self_test",
    "interactor_self_test": "interactor_self_test",
}


def _validate_tle_policy_params(
    tle_kill_factor: float | None,
    slowdown_kill_ratio: float | None,
) -> str | None:
    """Validate runtime TLE policy parameters (rejects bool, NaN/Inf, out-of-range)."""
    if tle_kill_factor is not None:
        if (
            isinstance(tle_kill_factor, bool)
            or not isinstance(tle_kill_factor, int | float)
            or not math.isfinite(float(tle_kill_factor))
            or not (0.0 < float(tle_kill_factor) <= 5.0)
        ):
            return (
                f"invalid tle_kill_factor: {tle_kill_factor!r}; "
                "must be a finite number in (0, 5.0]"
            )
    if slowdown_kill_ratio is not None:
        if (
            isinstance(slowdown_kill_ratio, bool)
            or not isinstance(slowdown_kill_ratio, int | float)
            or not math.isfinite(float(slowdown_kill_ratio))
            or not (float(slowdown_kill_ratio) > 1.0)
        ):
            return (
                f"invalid slowdown_kill_ratio: {slowdown_kill_ratio!r}; "
                "must be a finite number > 1.0"
            )
    return None


def _timing_cache_fingerprint(
    problem_dir: str,
    tests_dir: str,
    sol_name: str,
    in_files: list[str],
    manifest: AutoCodeManifest | None = None,
) -> str:
    """Compute a compact fingerprint for std per-test timing cache validation."""
    exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
    sol_mtimes: list[tuple[str, int]] = []
    for cand in (
        os.path.join(problem_dir, "solutions", f"{sol_name}{exe_ext}"),
        os.path.join(problem_dir, f"{sol_name}{exe_ext}"),
    ):
        if os.path.isfile(cand):
            try:
                st = os.stat(cand)
                sol_mtimes.append((os.path.realpath(cand), st.st_mtime_ns))
            except OSError:
                pass
            break
    if (
        not sol_mtimes
        and manifest is not None
        and manifest_is_communication(manifest)
        and manifest.protocol is not None
        and manifest.protocol.participant_solutions
        and sol_name == "sol"
    ):
        for part in resolve_participant_binaries(problem_dir, manifest, None, exe_ext) or []:
            try:
                st = os.stat(part)
                sol_mtimes.append((os.path.realpath(part), st.st_mtime_ns))
            except OSError:
                pass

    file_meta: list[tuple[str, int, int]] = []
    for fname in sorted(in_files):
        fpath = os.path.join(tests_dir, fname)
        try:
            st = os.stat(fpath)
            file_meta.append((fname, st.st_size, st.st_mtime_ns))
        except OSError:
            file_meta.append((fname, -1, -1))

    payload = json.dumps(
        {
            "tests_dir": os.path.realpath(tests_dir),
            "sol_name": sol_name,
            "sol_mtimes": sol_mtimes,
            "files": file_meta,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compute_dataset_fingerprint(
    problem_dir: str,
    tests_dir: str,
    manifest: AutoCodeManifest | None = None,
    *,
    answer_ext: str = ".ans",
    tests_manifest: dict[str, Any] | None = None,
) -> str:
    """Compute a content-based SHA-256 fingerprint across tests, manifest policy, sources, and platform."""
    hasher = hashlib.sha256()
    hasher.update(current_platform().encode("utf-8"))
    hasher.update(answer_ext.encode("utf-8"))
    if manifest is not None:
        policy_blob = json.dumps(
            {
                "tl": manifest.time_limit_ms,
                "ml": manifest.memory_limit_mb,
                "qg": manifest.quality_gates.model_dump(mode="json"),
                "cmp": manifest.stress_comparison,
                "spj": manifest.special_judge,
                "ptype": manifest.problem_type,
                "compiler": manifest.compiler,
            },
            sort_keys=True,
        )
        hasher.update(policy_blob.encode("utf-8"))
    if isinstance(tests_manifest, dict):
        hasher.update(json.dumps(tests_manifest, sort_keys=True, ensure_ascii=False).encode("utf-8"))

    allowed_exts = {".in", ".ans", ".out", answer_ext}
    tests_path = Path(tests_dir)
    if tests_path.is_dir():
        for p in sorted(tests_path.iterdir()):
            if p.is_file() and not p.is_symlink() and p.suffix in allowed_exts:
                try:
                    hasher.update(p.name.encode("utf-8"))
                    hasher.update(hashlib.sha256(p.read_bytes()).digest())
                except OSError:
                    continue

    source_rels = [
        "solutions/sol.cpp",
        "files/val.cpp",
        "files/checker.cpp",
        "files/interactor.cpp",
        "files/communicator.cpp",
        "files/grader.cpp",
        "files/problem.h",
    ]
    sols_dir = Path(problem_dir) / "solutions"
    if sols_dir.is_dir() and not sols_dir.is_symlink():
        for wf in sorted(sols_dir.glob("wrong_*.cpp")):
            if wf.is_file() and not wf.is_symlink() and is_safe_stem(wf.stem):
                source_rels.append(f"solutions/{wf.name}")
    if manifest is not None:
        if manifest.protocol is not None:
            for p_rel in (
                manifest.protocol.communicator_file,
                manifest.protocol.grader_file,
                manifest.protocol.header_file,
            ):
                if isinstance(p_rel, str) and p_rel.strip():
                    source_rels.append(p_rel.strip())
            for part_name in manifest.protocol.participant_solutions:
                if is_safe_stem(part_name):
                    source_rels.append(f"solutions/{part_name}.cpp")
        for s in manifest.solutions:
            if s.role == "wrong":
                rel = s.path or f"solutions/{s.name}.cpp"
                if rel not in source_rels:
                    source_rels.append(rel)
    for rel in sorted(set(source_rels)):
        resolved = safe_resolve_under_root(problem_dir, rel)
        if resolved is None:
            continue
        src = Path(resolved)
        if src.is_file() and not src.is_symlink():
            try:
                hasher.update(rel.encode("utf-8"))
                hasher.update(hashlib.sha256(src.read_bytes()).digest())
            except OSError:
                continue
    return hasher.hexdigest()


def _discover_wrong_solution_names(
    problem_dir: str,
    manifest: AutoCodeManifest | None,
    explicit_names: list[str] | None,
) -> tuple[list[str], list[str]]:
    """Return (wrong_names_to_run, undeclared_disk_wrong_names), rejecting unsafe stems."""
    declared: list[str] = []
    declared_stems: set[str] = set()
    if manifest is not None:
        for s in manifest.solutions:
            if s.role == "wrong" and is_safe_stem(s.name):
                if s.name not in declared:
                    declared.append(s.name)
                declared_stems.add(s.name)
                if s.path:
                    declared_stems.add(Path(s.path).stem)
    disk_wrongs: list[str] = []
    sols_dir = Path(problem_dir) / "solutions"
    if sols_dir.is_dir() and not sols_dir.is_symlink():
        for wf in sorted(sols_dir.glob("wrong_*.cpp")):
            if (
                wf.is_file()
                and not wf.is_symlink()
                and is_safe_stem(wf.stem)
                and safe_resolve_under_root(problem_dir, f"solutions/{wf.name}") is not None
                and wf.stem not in disk_wrongs
            ):
                disk_wrongs.append(wf.stem)
    undeclared = [name for name in disk_wrongs if name not in declared_stems]

    combined: list[str] = []
    if explicit_names is not None:
        for name in explicit_names:
            if isinstance(name, str) and name not in combined:
                combined.append(name)
        for name in declared:
            if name not in combined:
                combined.append(name)
    else:
        for name in declared + undeclared:
            if name not in combined:
                combined.append(name)
    return combined, undeclared



class _VerifyHelpersMixin:
    """Shared test manifest, scenario loader, and answer extension helpers."""

    def _read_tests_manifest(self, tests_dir: str) -> dict | None:
        # tests manifest now lives in <problem_dir>/.autocode/runtime.json under
        # the ``test_manifest`` key. ``tests_dir`` is either ``<problem_dir>``
        # (flat layout, e.g. tests written directly in the problem dir) or
        # ``<problem_dir>/tests`` (nested layout). Search both candidate roots.
        for candidate in (tests_dir, os.path.dirname(tests_dir)):
            manifest = get_section(candidate, TEST_MANIFEST)
            if isinstance(manifest, dict) and manifest.get("tests") is not None:
                return manifest
        manifest = get_section(tests_dir, TEST_MANIFEST)
        return manifest if isinstance(manifest, dict) else None

    def _collect_invalid_fixtures(self, problem_dir: str, tests_dir: str) -> list[dict]:
        fixtures: list[dict] = []
        seen_inputs: set[str] = set()
        invalid_dir = Path(tests_dir) / "invalid"
        if invalid_dir.is_dir():
            for path in sorted(p for p in invalid_dir.iterdir() if p.is_file()):
                fixtures.append({"name": path.name, "path": str(path)})
        manifest = self._read_tests_manifest(tests_dir)
        if manifest:
            invalid_tests = manifest.get("invalid_tests", [])
            if isinstance(invalid_tests, list):
                for item in invalid_tests:
                    if not isinstance(item, dict):
                        continue
                    if isinstance(item.get("input"), str):
                        fixtures.append({"name": item.get("name", "inline"), "input": item["input"]})
                        seen_inputs.add(item["input"])
                    elif isinstance(item.get("path"), str):
                        path = Path(item["path"])
                        if not path.is_absolute():
                            path = Path(problem_dir) / path
                        fixtures.append({"name": item.get("name", path.name), "path": str(path)})
        persisted = Path(problem_dir) / ".autocode" / "validator_invalid_fixtures.json"
        if persisted.is_file():
            try:
                data = json.loads(persisted.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = []
            if isinstance(data, list):
                for idx, item in enumerate(data, 1):
                    if isinstance(item, dict) and isinstance(item.get("input"), str):
                        inp = item["input"]
                        if inp in seen_inputs:
                            continue
                        seen_inputs.add(inp)
                        fixtures.append(
                            {"name": str(item.get("name") or f"validator_tc_{idx}"), "input": inp}
                        )
        return fixtures

    def _load_scenarios(self, problem_dir: str, filename: str) -> list[dict]:
        candidates = [
            Path(problem_dir) / "tests" / filename,
            Path(problem_dir) / "files" / filename,
            Path(problem_dir) / ".autocode" / filename,
        ]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            if isinstance(data, dict) and isinstance(data.get("scenarios"), list):
                return [item for item in data["scenarios"] if isinstance(item, dict)]
        return []

    def _scenario_categories(self, scenarios: list[dict], default_by_verdict: dict[str, str]) -> set[str]:
        categories: set[str] = set()
        for scenario in scenarios:
            category = scenario.get("category")
            if isinstance(category, str) and category.strip():
                categories.add(category.strip())
                continue
            verdict = str(scenario.get("expected_verdict", "AC"))
            fallback = default_by_verdict.get(verdict)
            if fallback:
                categories.add(fallback)
        return categories

    def _resolve_answer_ext(
        self, tests_dir: str, answer_ext: str | None, manifest: dict | None = None
    ) -> str:
        normalized = self._normalize_answer_ext(answer_ext)
        if normalized:
            return normalized
        if manifest is None:
            manifest = self._read_tests_manifest(tests_dir)
        if manifest:
            ext = self._normalize_answer_ext(manifest.get("answer_ext"))
            if ext:
                return ext
        return ".ans"

    def _normalize_answer_ext(self, answer_ext: str | None) -> str | None:
        return normalize_answer_ext(answer_ext)

    def _wrong_expected(self, wrong_name: str, manifest: AutoCodeManifest | None) -> str:
        if manifest:
            for s in manifest.solutions:
                if s.name == wrong_name and s.role == "wrong":
                    return s.expected or "fail"
        return "fail"
