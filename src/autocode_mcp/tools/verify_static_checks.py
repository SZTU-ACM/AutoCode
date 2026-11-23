"""Static test-suite quality checks mixin for ``ProblemVerifyTestsTool``."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from ..utils.answer_ext import normalize_answer_ext
from ..workflow import AutoCodeManifest
from .verify_fingerprint import _LIMIT_GROUPS, _LIMIT_STRATEGY_TYPES, _VerifyHelpersMixin


class _VerifyStaticChecksMixin(_VerifyHelpersMixin):
    """File completeness, empty/duplicate checks, limit ratio, and boundary coverage."""

    def _check_file_count(
        self,
        tests_dir: str,
        answer_ext: str,
        *,
        is_interactive: bool = False,
        allow_prefixed_names: bool = False,
    ) -> dict:
        """检查文件完整性：每个 .in 有对应的 answer_ext（交互题允许省略 .ans），文件名连续。"""
        import re as _re

        tests_path = Path(tests_dir)
        in_files = sorted(p.name for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in")
        ans_files = sorted(p.name for p in tests_path.iterdir() if p.is_file() and p.name.endswith(answer_ext))
        ans_file_set = set(ans_files)
        in_file_set = set(in_files)

        missing_ans = []
        for in_file in in_files:
            ans_file = Path(in_file).with_suffix(answer_ext).name
            if ans_file not in ans_file_set:
                missing_ans.append(in_file)

        orphan_ans = []
        for ans_file in ans_files:
            if not ans_file.endswith(answer_ext):
                continue
            base = ans_file[: -len(answer_ext)]
            in_file = f"{base}.in"
            if in_file not in in_file_set:
                orphan_ans.append(ans_file)

        non_numeric: list[str] = []
        numeric_indices: list[int] = []
        prefixes_seen: set[str] = set()
        for f in in_files:
            stem = Path(f).stem
            if stem.isdigit():
                numeric_indices.append(int(stem))
                prefixes_seen.add("")
            elif allow_prefixed_names:
                m_pref = _re.fullmatch(r"([A-Za-z0-9_-]*?[-_]|[A-Za-z_][A-Za-z0-9_-]*?)(\d+)", stem)
                if m_pref:
                    prefixes_seen.add(m_pref.group(1))
                    numeric_indices.append(int(m_pref.group(2)))
                else:
                    non_numeric.append(f)
            else:
                non_numeric.append(f)

        numeric_indices.sort()
        numeric_index_set = set(numeric_indices)
        # In standalone prefixed layouts (e.g. test1..testN, sample1..sample2), only check contiguity when a single prefix is used.
        check_contiguous = (not allow_prefixed_names) or len(prefixes_seen) <= 1
        expected_indices = (
            list(range(1, max(numeric_indices) + 1)) if (numeric_indices and check_contiguous) else []
        )
        missing_indices = [
            idx for idx in expected_indices if idx not in numeric_index_set
        ]
        duplicate_indices = (
            sorted(idx for idx in numeric_index_set if numeric_indices.count(idx) > 1)
            if check_contiguous
            else []
        )

        passed = (
            bool(in_files)
            and (is_interactive or not missing_ans)
            and not orphan_ans
            and not non_numeric
            and not missing_indices
            and not duplicate_indices
        )
        return {
            "passed": passed,
            "total": len(in_files),
            "missing_ans": missing_ans,
            "orphan_ans": orphan_ans,
            "missing_indices": missing_indices,
            "duplicate_indices": duplicate_indices,
            "non_numeric": non_numeric,
            "interactive_answers_optional": is_interactive,
        }

    def _check_no_empty(
        self,
        tests_dir: str,
        *,
        is_interactive: bool = False,
        answer_ext: str = ".ans",
    ) -> dict:
        """检查没有空文件（交互题允许空占位答案文件，但输入 .in 仍不可为空）。"""
        norm_ext = normalize_answer_ext(answer_ext) or ".ans"
        interactive_optional_exts = tuple(sorted({".ans", ".out", norm_ext}))
        empty_files = []
        for f in os.listdir(tests_dir):
            filepath = os.path.join(tests_dir, f)
            if os.path.isfile(filepath) and os.path.getsize(filepath) == 0:
                if is_interactive and f.endswith(interactive_optional_exts):
                    continue
                empty_files.append(f)

        return {
            "passed": len(empty_files) == 0,
            "total": len(os.listdir(tests_dir)),
            "empty_files": empty_files,
        }

    def _check_limit_ratio(self, tests_dir: str, manifest: dict | None = None) -> dict:
        """检查最终测试中 type=3/4 是否不少于一半。

        manifest 可来自一次性加载并透传，缺省时回退为读取 tests manifest。
        """
        if manifest is None:
            manifest = self._read_tests_manifest(tests_dir)
        if not manifest:
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "tests manifest missing or unreadable",
            }

        tests = manifest.get("tests", [])
        if not isinstance(tests, list):
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "invalid manifest format: tests must be a list",
            }

        in_files = sorted(
            (
                f
                for f in os.listdir(tests_dir)
                if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
            ),
            key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)],
        )
        in_file_set = set(in_files)
        type_by_in_file: dict[str, str] = {}
        group_by_in_file: dict[str, str] = {}
        for item in tests:
            if not isinstance(item, dict):
                continue
            in_file = item.get("in_file")
            type_param = item.get("type_param")
            if isinstance(in_file, str) and isinstance(type_param, str):
                type_by_in_file[in_file] = type_param
                group_by_in_file[in_file] = str(item.get("group", "")).strip().lower()

        missing_in_manifest = sorted(f for f in in_files if f not in type_by_in_file)
        if missing_in_manifest:
            return {
                "passed": False,
                "total": len(in_files),
                "limit_case_count": 0,
                "limit_case_minimum_required": (len(in_files) + 1) // 2 if in_files else 0,
                "limit_case_ratio": 0.0,
                "missing_in_manifest": missing_in_manifest,
                "error": "manifest does not cover all .in files",
            }

        total = len(in_files)
        if total == 0:
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "no .in files found",
            }

        limit_case_count = sum(
            1
            for in_file in in_file_set
            if type_by_in_file[in_file] in _LIMIT_STRATEGY_TYPES
            or group_by_in_file.get(in_file, "") in _LIMIT_GROUPS
        )
        minimum_required = (total + 1) // 2
        ratio = limit_case_count / total

        return {
            "passed": limit_case_count >= minimum_required,
            "total": total,
            "limit_case_count": limit_case_count,
            "limit_case_minimum_required": minimum_required,
            "limit_case_ratio": ratio,
            "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
        }

    def _check_limit_semantics(self, tests_dir: str, manifest: dict | None = None) -> dict:
        """检查 type=3/4 是否存在以及语义门禁。

        manifest 可来自一次性加载并透传，缺省时回退为读取 tests manifest。
        """
        if manifest is None:
            manifest = self._read_tests_manifest(tests_dir)
        if not manifest:
            return {"passed": False, "error": "tests manifest missing or unreadable"}
        tests = manifest.get("tests", [])
        type3 = [t for t in tests if isinstance(t, dict) and t.get("type_param") == "3"]
        type4 = [t for t in tests if isinstance(t, dict) and t.get("type_param") == "4"]
        if not type4:
            return {"passed": False, "error": "type=4 cases missing; update generator first"}
        if not type3:
            return {"passed": False, "error": "type=3 cases missing; update generator first"}
        sig3 = {str(t.get("signature", "")) for t in type3}
        sig4 = {str(t.get("signature", "")) for t in type4}
        overlap_ratio = len(sig3 & sig4) / max(1, min(len(sig3), len(sig4)))
        passed = overlap_ratio < 0.8
        return {
            "passed": passed,
            "type3_count": len(type3),
            "type4_count": len(type4),
            "overlap_ratio": overlap_ratio,
            "hint": "需要确保 type=4 不是仅放大参数，而是有独立卡法" if not passed else "",
        }

    def _check_duplicate_inputs(self, tests_dir: str, *, max_near_ratio: float = 0.2) -> dict:
        tests_path = Path(tests_dir)
        in_files = sorted(
            (p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in"),
            key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)],
        )
        signatures: dict[str, list[str]] = {}
        normalized_signatures: dict[str, list[str]] = {}
        for path in in_files:
            data = path.read_text(encoding="utf-8", errors="replace")
            exact_key = hashlib.sha256(data.encode("utf-8", errors="replace")).hexdigest()
            normalized_key = " ".join(data.split())
            signatures.setdefault(exact_key, []).append(path.name)
            normalized_signatures.setdefault(normalized_key, []).append(path.name)

        exact_duplicates = [names for names in signatures.values() if len(names) > 1]
        near_duplicates = [
            names
            for names in normalized_signatures.values()
            if len(names) > 1 and names not in exact_duplicates
        ]
        duplicate_file_count = sum(len(names) for names in exact_duplicates)
        near_duplicate_file_count = sum(len(names) for names in near_duplicates)
        total = len(in_files)
        ratio = (duplicate_file_count + near_duplicate_file_count) / total if total else 0.0
        near_ratio = near_duplicate_file_count / total if total else 0.0
        # Zero tolerance for byte-identical exact duplicates; ratio threshold applies only to near duplicates.
        passed = len(exact_duplicates) == 0 and near_ratio <= max_near_ratio
        return {
            "passed": passed,
            "total": total,
            "duplicate_file_count": duplicate_file_count,
            "exact_duplicate_count": len(exact_duplicates),
            "exact_duplicate_tolerance": 0,
            "near_duplicate_file_count": near_duplicate_file_count,
            "duplicate_ratio": ratio,
            "near_duplicate_ratio": near_ratio,
            "exact_duplicates": exact_duplicates,
            "near_duplicates": near_duplicates[:20],
            "threshold": max_near_ratio,
        }

    def _check_scale_distribution(self, tests_dir: str) -> dict:
        tests_path = Path(tests_dir)
        in_files = sorted(
            (p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in"),
            key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)],
        )
        buckets = {"empty": 0, "tiny": 0, "small": 0, "medium": 0, "large": 0}
        byte_buckets = {"0-100B": 0, "101B-1KB": 0, "1KB-32KB": 0, "32KB+": 0}
        parsed_first_ints: list[int] = []
        for path in in_files:
            data = path.read_text(encoding="utf-8", errors="replace")
            tokens = data.split()
            if not tokens:
                buckets["empty"] += 1
            else:
                try:
                    first = int(tokens[0])
                    parsed_first_ints.append(first)
                    if first <= 1:
                        buckets["tiny"] += 1
                    elif first <= 10:
                        buckets["small"] += 1
                    elif first <= 1000:
                        buckets["medium"] += 1
                    else:
                        buckets["large"] += 1
                except ValueError:
                    buckets["medium"] += 1
            size = path.stat().st_size
            if size <= 100:
                byte_buckets["0-100B"] += 1
            elif size <= 1024:
                byte_buckets["101B-1KB"] += 1
            elif size <= 32 * 1024:
                byte_buckets["1KB-32KB"] += 1
            else:
                byte_buckets["32KB+"] += 1
        non_empty_scale_buckets = sum(1 for key, value in buckets.items() if key != "empty" and value > 0)
        return {
            "passed": bool(in_files) and buckets["empty"] == 0,
            "total": len(in_files),
            "first_integer_buckets": {k: v for k, v in buckets.items() if v > 0},
            "byte_size_buckets": {k: v for k, v in byte_buckets.items() if v > 0},
            "min_first_integer": min(parsed_first_ints) if parsed_first_ints else None,
            "max_first_integer": max(parsed_first_ints) if parsed_first_ints else None,
            "non_empty_scale_bucket_count": non_empty_scale_buckets,
        }

    def _check_purpose_coverage(
        self, tests_dir: str, manifest: AutoCodeManifest | None = None
    ) -> dict:
        tmanifest = self._read_tests_manifest(tests_dir)
        if not tmanifest:
            return {"passed": False, "error": "tests manifest missing or unreadable"}
        if tmanifest.get("mode") == "register_existing":
            return {
                "passed": True,
                "skipped": True,
                "total": len(tmanifest.get("tests", [])) if isinstance(tmanifest.get("tests"), list) else 0,
                "message": "purpose_coverage skipped for register_existing tests",
            }
        tests = tmanifest.get("tests", [])
        if not isinstance(tests, list) or not tests:
            return {"passed": False, "error": "tests manifest has no tests"}
        type_counts: dict[str, int] = {}
        group_counts: dict[str, int] = {}
        purpose_count = 0
        for item in tests:
            if not isinstance(item, dict):
                continue
            type_param = str(item.get("type_param", "unknown"))
            group = str(item.get("group", "default"))
            if item.get("purpose"):
                purpose_count += 1
            type_counts[type_param] = type_counts.get(type_param, 0) + 1
            group_counts[group] = group_counts.get(group, 0) + 1
        if manifest is not None and manifest.case_plan:
            required_types = {str(cp.type) for cp in manifest.case_plan}
        else:
            required_types = {"1", "2", "3", "4"}
        missing_types = sorted(required_types - set(type_counts))
        return {
            "passed": not missing_types,
            "total": len(tests),
            "type_counts": type_counts,
            "group_counts": group_counts,
            "purpose_count": purpose_count,
            "missing_types": missing_types,
        }

    def _check_boundary_coverage(
        self, tests_dir: str, manifest: AutoCodeManifest | None
    ) -> dict[str, Any]:
        """Verify that formal test inputs cover declared min/max primary scale boundaries."""
        if manifest is None or not manifest.constraints:
            return {
                "passed": True,
                "skipped": True,
                "message": "no numeric constraints declared in manifest",
            }
        expected_min: int | None = None
        expected_max: int | None = None
        c = manifest.constraints
        # Prefer explicit n / N / primary variable bounds
        for primary in ("n", "N"):
            entry = c.get(primary)
            if isinstance(entry, dict):
                if isinstance(entry.get("min"), int) and not isinstance(entry.get("min"), bool):
                    expected_min = int(entry["min"])
                if isinstance(entry.get("max"), int) and not isinstance(entry.get("max"), bool):
                    expected_max = int(entry["max"])
        if expected_min is None:
            for key in ("n_min", "N_min", "min_n"):
                val = c.get(key)
                if isinstance(val, int) and not isinstance(val, bool):
                    expected_min = int(val)
                    break
        if expected_max is None:
            for key in ("n_max", "N_max", "max_n", "sum_n_max"):
                val = c.get(key)
                if isinstance(val, int) and not isinstance(val, bool):
                    expected_max = int(val)
                    break

        if expected_min is None and expected_max is None:
            return {
                "passed": True,
                "skipped": True,
                "message": "no n_min/n_max primary bounds declared in manifest.constraints",
            }

        tests_path = Path(tests_dir)
        in_files = sorted(
            p for p in tests_path.iterdir() if p.is_file() and not p.is_symlink() and p.suffix == ".in"
        )
        observed_vals: list[int] = []
        for p in in_files:
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            first_nonempty = next((ln.strip() for ln in lines if ln.strip()), "")
            if not first_nonempty:
                continue
            tok = first_nonempty.split()[0]
            try:
                observed_vals.append(int(tok))
            except ValueError:
                continue
        if not observed_vals:
            return {
                "passed": False,
                "expected_min": expected_min,
                "expected_max": expected_max,
                "observed_min": None,
                "observed_max": None,
                "missing_boundaries": ["no_integer_inputs_found"],
            }

        obs_min = min(observed_vals)
        obs_max = max(observed_vals)
        missing_boundaries: list[str] = []
        min_covered = expected_min is None or (expected_min in observed_vals)
        max_covered = expected_max is None or (
            expected_max in observed_vals
            or (expected_max > 0 and obs_max >= int(expected_max * 0.95))
        )
        if not min_covered:
            missing_boundaries.append(f"min({expected_min}) not hit (observed min={obs_min})")
        if not max_covered:
            missing_boundaries.append(f"max({expected_max}) not hit (observed max={obs_max})")
        return {
            "passed": not missing_boundaries,
            "total": len(in_files),
            "expected_min": expected_min,
            "expected_max": expected_max,
            "observed_min": obs_min,
            "observed_max": obs_max,
            "min_covered": min_covered,
            "max_covered": max_covered,
            "missing_boundaries": missing_boundaries,
        }
