"""Helper dataclass & mixin methods for ``ProblemGenerateTestsTool``."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..runtime_store import (
    AUDIT,
    GENERATE_CHECKPOINT,
    TEST_MANIFEST,
    WORKFLOW,
    get_section,
    set_section,
    update_section,
)
from ..utils.answer_ext import normalize_answer_ext
from ..utils.compiler import RunResult
from ..workflow import (
    AutoCodeManifest,
)
from .base import ToolResult
from .problem_helpers import _LIMIT_STRATEGY_TYPES


@dataclass
class CandidateTest:
    """候选测试数据。"""

    input_data: str
    output_data: str
    type_param: str  # 1=tiny, 2=random, 3=extreme, 4=tle
    signature: str


@dataclass
class _CandidateOutcome:
    """单个候选并发处理的结果。"""

    candidate: CandidateTest | None = None
    error: tuple[int, str] | None = None
    fallback_used: bool = False



class _ProblemGenerateHelpersMixin:
    """Snapshot, resume checkpoint, sampling balance, and default config helpers."""

    def _mark_tests_unverified(self, problem_dir: str) -> None:
        update_section(
            problem_dir,
            WORKFLOW,
            {
                "tests_verified": False,
                "verify_signals": {},
                "verify_dataset_fingerprint": None,
                "limit_case_ratio": None,
                "packaged": False,
            },
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})

    def _resolve_tests_dir(
        self,
        problem_dir: str,
        output_dir: str | None,
    ) -> tuple[str | None, ToolResult | None]:
        """解析并校验测试输出目录，防止清理时误删题目文件或外部目录。"""
        problem_root = os.path.realpath(problem_dir)
        raw_output_dir = output_dir or "tests"
        tests_dir = raw_output_dir
        if not os.path.isabs(tests_dir):
            tests_dir = os.path.join(problem_root, tests_dir)
        tests_dir = os.path.abspath(tests_dir)
        resolved_tests_dir = os.path.realpath(tests_dir)

        try:
            common = os.path.commonpath([problem_root, resolved_tests_dir])
        except ValueError:
            common = ""
        if os.path.normcase(common) != os.path.normcase(problem_root):
            return None, ToolResult.fail("output_dir must be inside problem_dir")

        if os.path.normcase(resolved_tests_dir) == os.path.normcase(problem_root):
            return None, ToolResult.fail("output_dir cannot be the problem_dir root")

        reserved_dirs = {"files", "solutions", "statements"}
        for reserved in reserved_dirs:
            reserved_path = os.path.realpath(os.path.join(problem_root, reserved))
            try:
                reserved_common = os.path.commonpath([reserved_path, resolved_tests_dir])
            except ValueError:
                reserved_common = ""
            if os.path.normcase(reserved_common) == os.path.normcase(reserved_path):
                return None, ToolResult.fail(f"output_dir cannot be reserved directory: {reserved}")

        if os.path.exists(tests_dir) and os.path.islink(tests_dir):
            return None, ToolResult.fail(f"output_dir cannot be a symlink: {tests_dir}")

        if os.path.exists(tests_dir) and not os.path.isdir(tests_dir):
            return None, ToolResult.fail(f"output_dir exists and is not a directory: {tests_dir}")

        return tests_dir, None

    def _clear_generated_tests(self, tests_dir: str, answer_ext: str = ".ans") -> ToolResult | None:
        """创建测试目录并清理旧的 .in/.answer_ext 文件。"""
        os.makedirs(tests_dir, exist_ok=True)
        for filename in os.listdir(tests_dir):
            if not (filename.endswith(".in") or filename.endswith(answer_ext)):
                continue
            path = os.path.join(tests_dir, filename)
            if os.path.isfile(path):
                os.remove(path)
        return None

    @staticmethod
    def _test_file_sort_key(filename: str) -> tuple[int, int, str]:
        stem = os.path.splitext(filename)[0]
        return (0, int(stem), filename) if stem.isdigit() else (1, 0, filename)

    @staticmethod
    def _safe_plain_file(base_dir: str, root_dir: str, fname: str) -> str | None:
        """Return resolved file path strictly inside ``base_dir`` and ``root_dir``, rejecting traversal/symlinks."""
        if (
            not isinstance(fname, str)
            or not fname
            or fname in {".", ".."}
            or "/" in fname
            or "\\" in fname
            or (os.sep and os.sep in fname)
            or (os.altsep and os.altsep in fname)
            or ":" in fname
            or "\x00" in fname
        ):
            return None
        candidate = os.path.join(base_dir, fname)
        if os.path.islink(candidate) or not os.path.isfile(candidate):
            return None
        try:
            real_cand = Path(candidate).resolve()
            real_base = Path(base_dir).resolve()
            real_root = Path(root_dir).resolve()
            real_cand.relative_to(real_base)
            real_cand.relative_to(real_root)
        except (OSError, ValueError):
            return None
        return str(real_cand)

    def _snapshot_existing_tests(
        self,
        tests_dir: str,
        problem_dir: str,
        answer_ext: str,
    ) -> tuple[list[dict[str, Any]], int, set[str]]:
        """Snapshot existing tests/ entries and signatures for mode='append' / 'register_existing'."""
        prev_manifest = get_section(problem_dir, TEST_MANIFEST)
        prev_by_file: dict[str, dict[str, Any]] = {}
        if isinstance(prev_manifest, dict) and isinstance(prev_manifest.get("tests"), list):
            for item in prev_manifest["tests"]:
                if isinstance(item, dict) and isinstance(item.get("in_file"), str):
                    prev_by_file[item["in_file"]] = dict(item)

        in_files = sorted(
            (
                f
                for f in os.listdir(tests_dir)
                if f.endswith(".in") and self._safe_plain_file(tests_dir, problem_dir, f) is not None
            ),
            key=self._test_file_sort_key,
        )
        max_numeric = max(
            (int(os.path.splitext(f)[0]) for f in in_files if os.path.splitext(f)[0].isdigit()),
            default=0,
        )
        next_non_numeric = max_numeric
        entries: list[dict[str, Any]] = []
        sigs: set[str] = set()
        max_idx = max_numeric
        for fname in in_files:
            stem = os.path.splitext(fname)[0]
            if stem.isdigit():
                idx = int(stem)
            else:
                next_non_numeric += 1
                idx = next_non_numeric
            max_idx = max(max_idx, idx)
            in_path = self._safe_plain_file(tests_dir, problem_dir, fname)
            if in_path is None:
                continue
            try:
                content = Path(in_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = ""
            sig = hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()
            sigs.add(sig)
            ans_name = f"{stem}{answer_ext}"
            resolved_ans = self._safe_plain_file(tests_dir, problem_dir, ans_name)
            if resolved_ans is None and answer_ext != ".out":
                alt_name = f"{stem}.out"
                if self._safe_plain_file(tests_dir, problem_dir, alt_name) is not None:
                    ans_name = alt_name
                    resolved_ans = alt_name
            prev = prev_by_file.get(fname, {})
            entry = {
                **prev,
                "index": idx,
                "in_file": fname,
                "ans_file": ans_name if resolved_ans is not None else "",
                "type_param": str(prev.get("type_param", "registered")),
                "group": str(prev.get("group", "default")),
                "signature": sig,
            }
            entries.append(entry)
        return entries, max_idx, sigs

    def _snapshot_locked_and_hack_tests(
        self,
        tests_dir: str,
        problem_dir: str,
        answer_ext: str,
    ) -> list[dict[str, Any]]:
        """Snapshot locked entries in TEST_MANIFEST and any tests/hack/*.in files before replace."""
        preserved: list[dict[str, Any]] = []
        seen_sigs: set[str] = set()
        prev_manifest = get_section(problem_dir, TEST_MANIFEST)
        if isinstance(prev_manifest, dict) and isinstance(prev_manifest.get("tests"), list) and os.path.isdir(tests_dir):
            for item in prev_manifest["tests"]:
                if not isinstance(item, dict) or not bool(item.get("locked", False)):
                    continue
                in_name = item.get("in_file")
                if not isinstance(in_name, str) or not in_name:
                    continue
                in_path = self._safe_plain_file(tests_dir, problem_dir, in_name)
                if in_path is None:
                    continue
                try:
                    in_text = Path(in_path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                sig = hashlib.md5(in_text.encode("utf-8", errors="replace")).hexdigest()
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                raw_ans_name = item.get("ans_file")
                ans_name = (
                    raw_ans_name
                    if isinstance(raw_ans_name, str) and raw_ans_name
                    else f"{os.path.splitext(in_name)[0]}{answer_ext}"
                )
                ans_path = self._safe_plain_file(tests_dir, problem_dir, ans_name) if ans_name else None
                ans_text = (
                    Path(ans_path).read_text(encoding="utf-8", errors="replace")
                    if ans_path is not None
                    else None
                )
                preserved.append(
                    {
                        "input_data": in_text,
                        "output_data": ans_text,
                        "type_param": str(item.get("type_param", "4")),
                        "group": str(item.get("group", "limit")),
                        "purpose": str(item.get("purpose", "locked manual test")),
                        "signature": sig,
                    }
                )

        hack_dir = os.path.join(tests_dir, "hack")
        hack_dir_safe = False
        if os.path.isdir(hack_dir) and not os.path.islink(hack_dir):
            try:
                real_hack = Path(hack_dir).resolve()
                real_hack.relative_to(Path(tests_dir).resolve())
                real_hack.relative_to(Path(problem_dir).resolve())
                hack_dir_safe = True
            except (OSError, ValueError):
                hack_dir_safe = False
        if hack_dir_safe:
            for fname in sorted(os.listdir(hack_dir), key=self._test_file_sort_key):
                if not fname.endswith(".in"):
                    continue
                in_path = self._safe_plain_file(hack_dir, problem_dir, fname)
                if in_path is None:
                    continue
                try:
                    in_text = Path(in_path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                sig = hashlib.md5(in_text.encode("utf-8", errors="replace")).hexdigest()
                if sig in seen_sigs:
                    continue
                seen_sigs.add(sig)
                stem = os.path.splitext(fname)[0]
                ans_path = self._safe_plain_file(hack_dir, problem_dir, f"{stem}{answer_ext}")
                ans_text = (
                    Path(ans_path).read_text(encoding="utf-8", errors="replace")
                    if ans_path is not None
                    else None
                )
                preserved.append(
                    {
                        "input_data": in_text,
                        "output_data": ans_text,
                        "type_param": "4",
                        "group": "limit",
                        "purpose": f"manual hack ({fname})",
                        "signature": sig,
                    }
                )
        return preserved

    def _register_existing_tests(
        self,
        *,
        problem_dir: str,
        tests_dir: str,
        answer_ext: str,
        manifest_model: AutoCodeManifest | None,
    ) -> ToolResult:
        """Index existing tests/*.in without deleting or regenerating any files."""
        if not os.path.isdir(tests_dir):
            return ToolResult.fail(f"tests directory not found: {tests_dir}")
        entries, _, _ = self._snapshot_existing_tests(tests_dir, problem_dir, answer_ext)
        if not entries:
            return ToolResult.fail(f"no .in files found in {tests_dir}")
        set_section(
            problem_dir,
            TEST_MANIFEST,
            {
                "version": 1,
                "mode": "register_existing",
                "answer_ext": answer_ext,
                "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
                "tests": entries,
            },
        )
        set_section(problem_dir, GENERATE_CHECKPOINT, {})
        indices = [int(e["index"]) for e in entries]
        limit_in_final = sum(
            1
            for e in entries
            if str(e.get("type_param")) in _LIMIT_STRATEGY_TYPES
            or str(e.get("group", "")).lower() in {"limit", "extreme", "tle"}
        )
        limit_minimum = (len(entries) + 1) // 2
        return ToolResult.ok(
            tests_dir=tests_dir,
            generated_tests=indices,
            mode="register_existing",
            total_tests=len(entries),
            limit_case_count=limit_in_final,
            limit_case_minimum_required=limit_minimum,
            limit_case_quota_met=limit_in_final >= limit_minimum,
            answer_ext=answer_ext,
            message=f"Registered {len(entries)} existing test cases in {tests_dir}",
        )

    def _normalize_answer_ext(self, answer_ext: str) -> tuple[str | None, ToolResult | None]:
        ext = normalize_answer_ext(answer_ext or ".ans")
        if not ext:
            return None, ToolResult.fail("invalid answer_ext")
        return ext, None

    @staticmethod
    def _generator_run_failed(result: RunResult) -> bool:
        return result.timed_out or not result.success or not (result.stdout or "").strip()


    def _save_state(
        self,
        problem_dir: str,
        *,
        phase: str,
        next_seed: int,
        candidates: list[CandidateTest],
        errors: list[tuple[int, str]],
        answer_ext: str,
        active_pids: set[int] | None = None,
        message: str | None = None,
    ) -> None:
        set_section(
            problem_dir,
            GENERATE_CHECKPOINT,
            {
                "version": 1,
                "phase": phase,
                "next_seed": next_seed,
                "answer_ext": answer_ext,
                "message": message,
                "active_pids": sorted(active_pids or []),
                "candidates": [
                    {
                        "input_data": c.input_data,
                        "output_data": c.output_data,
                        "type_param": c.type_param,
                        "signature": c.signature,
                    }
                    for c in candidates
                ],
                "errors": [{"seed": seed, "error": err} for seed, err in errors[-200:]],
            },
        )

    def _load_state(self, problem_dir: str) -> dict | None:
        state = get_section(problem_dir, GENERATE_CHECKPOINT)
        return state if isinstance(state, dict) else None

    def _restore_candidates(self, raw_candidates: list[dict]) -> list[CandidateTest]:
        out: list[CandidateTest] = []
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            out.append(
                CandidateTest(
                    input_data=str(item.get("input_data", "")),
                    output_data=str(item.get("output_data", "")),
                    type_param=str(item.get("type_param", "2")),
                    signature=str(item.get("signature", "")),
                )
            )
        return out


    def _balance_and_sample(
        self,
        candidates: list[CandidateTest],
        target_count: int,
        balance_remainder: bool = True,
    ) -> list[CandidateTest]:
        """采样：至少一半为极限类（type 3/4），其余再分配。

        先取不少于 ceil(target_count/2) 条来自 extreme/tle 的候选（若候选不足则全取），
        再在剩余候选中填满 target_count；剩余部分在 balance_remainder 为真时在
        各类型间均衡，否则按 (type_param, signature) 确定性排序依次选取。
        """
        if target_count <= 0 or not candidates:
            return []

        need_limit = (target_count + 1) // 2  # 不少于一半（向上取整到整数条数）
        extreme_pool = sorted(
            [c for c in candidates if c.type_param in _LIMIT_STRATEGY_TYPES],
            key=lambda c: (c.type_param, c.signature),
        )

        result: list[CandidateTest] = []
        selected_ids: set[int] = set()

        for c in extreme_pool:
            if len(result) >= need_limit:
                break
            cid = id(c)
            if cid in selected_ids:
                continue
            result.append(c)
            selected_ids.add(cid)

        remaining = [c for c in candidates if id(c) not in selected_ids]
        need_more = target_count - len(result)
        if need_more <= 0:
            return result[:target_count]

        if balance_remainder:
            by_type: dict[str, list[CandidateTest]] = {}
            for c in remaining:
                by_type.setdefault(c.type_param, []).append(c)
            for t in by_type:
                by_type[t] = sorted(by_type[t], key=lambda c: c.signature)

            type_order = sorted(by_type.keys())
            if not type_order:
                return result[:target_count]

            num_types = len(type_order)
            base_count = need_more // num_types
            rem = need_more % num_types

            for i, type_param in enumerate(type_order):
                count = base_count + (1 if i < rem else 0)
                for c in by_type[type_param][:count]:
                    cid = id(c)
                    if cid in selected_ids:
                        continue
                    result.append(c)
                    selected_ids.add(cid)
                    if len(result) >= target_count:
                        break
                if len(result) >= target_count:
                    break

            if len(result) < target_count:
                for c in sorted(remaining, key=lambda c: (c.type_param, c.signature)):
                    if len(result) >= target_count:
                        break
                    cid = id(c)
                    if cid in selected_ids:
                        continue
                    result.append(c)
                    selected_ids.add(cid)
        else:
            for c in sorted(remaining, key=lambda c: (c.type_param, c.signature)):
                if len(result) >= target_count:
                    break
                cid = id(c)
                if cid in selected_ids:
                    continue
                result.append(c)
                selected_ids.add(cid)

        return result[:target_count]

    def _get_default_configs(
        self, constraints: dict | None = None
    ) -> list[tuple[str, str, str, str, str, str, list[str]]]:
        """获取默认测试配置。

        Args:
            constraints: 题目约束条件，包含 n_max, t_max, sum_n_max 等

        Returns:
            配置列表，每项为 (seed_offset, type, n_min, n_max, t_min, t_max, extra_args)
        """
        # 从约束中获取极限值
        n_limit = constraints.get("n_max", 100000) if constraints else 100000
        t_limit = constraints.get("t_max", 1) if constraints else 1
        sum_n_limit = constraints.get("sum_n_max", n_limit) if constraints else n_limit

        configs: list[tuple[str, str, str, str, str, str, list[str]]] = []

        # 1. 边界情况 (type=1 tiny) - 最小值和极小值
        configs.extend(
            [
                ("0", "1", "1", "1", "1", "1", []),  # N=1, T=1
                ("1", "1", "1", "1", str(t_limit), str(t_limit), []),  # N=1, T=max
                ("2", "1", "2", "2", "1", "1", []),  # N=2
            ]
        )

        # 2. 小数据随机 (type=2 random)
        configs.extend(
            [
                ("3", "2", "1", "10", "1", str(min(3, t_limit)), []),
                ("4", "2", "10", "100", "1", str(min(3, t_limit)), []),
            ]
        )

        # 3. 中等数据
        mid_n = n_limit // 2
        configs.extend(
            [
                ("5", "2", "100", str(mid_n // 10), "1", str(min(3, t_limit)), []),
                ("6", "2", str(mid_n // 10), str(mid_n), "1", str(min(2, t_limit)), []),
            ]
        )

        # 4. 大数据随机 (type=2)
        if n_limit >= 10000:
            configs.extend(
                [
                    ("7", "2", str(mid_n), str(n_limit), "1", "1", []),
                    ("8", "2", str(int(n_limit * 0.8)), str(n_limit), "1", "1", []),
                ]
            )

        # 5. 极限数据 (type=3 extreme) - 接近上限
        configs.extend(
            [
                ("9", "3", str(n_limit), str(n_limit), "1", "1", []),  # N=max
                ("10", "3", str(n_limit - 1), str(n_limit), "1", "1", []),  # N=max-1
                ("11", "3", str(int(n_limit * 0.99)), str(n_limit), "1", "1", []),  # 接近极限
            ]
        )

        # 6. T 极限情况
        if t_limit > 1:
            # T=max, N 根据sum约束调整
            n_per_test = min(n_limit, sum_n_limit // t_limit) if sum_n_limit else n_limit
            configs.append(
                ("12", "3", str(max(1, n_per_test // 2)), str(n_per_test), str(t_limit), str(t_limit), [])
            )

        # 7. TLE 诱导数据 (type=4)，确保默认配置中极限/TLE 用例占比 >= 50%
        configs.extend(
            [
                ("13", "4", str(n_limit), str(n_limit), "1", "1", ["mode=tle_dense"]),
                ("14", "4", str(max(1, int(n_limit * 0.9))), str(n_limit), "1", "1", ["mode=tle_chain"]),
                ("15", "4", str(max(1, int(n_limit * 0.95))), str(n_limit), "1", "1", ["mode=tle_adversarial"]),
                ("16", "3", str(n_limit), str(n_limit), "1", str(t_limit), []),
                ("17", "4", str(n_limit), str(n_limit), "1", str(t_limit), ["mode=tle_worst"]),
            ]
        )

        return self._sanitize_default_configs(configs, n_limit=n_limit, t_limit=t_limit)

    def _sanitize_default_configs(
        self,
        configs: list[tuple[str, str, str, str, str, str, list[str]]],
        *,
        n_limit: int,
        t_limit: int,
    ) -> list[tuple[str, str, str, str, str, str, list[str]]]:
        """Clamp generated defaults so smart mode never emits invalid generator ranges."""
        safe_configs: list[tuple[str, str, str, str, str, str, list[str]]] = []
        for seed_offset, type_param, n_min, n_max, t_min, t_max, extra_args in configs:
            raw_n_min = int(n_min)
            raw_n_max = int(n_max)
            raw_t_min = int(t_min)
            raw_t_max = int(t_max)

            safe_n_max = min(max(raw_n_max, 1), max(n_limit, 1))
            safe_n_min = min(max(raw_n_min, 1), safe_n_max)
            safe_t_max = min(max(raw_t_max, 1), max(t_limit, 1))
            safe_t_min = min(max(raw_t_min, 1), safe_t_max)

            safe_configs.append(
                (
                    seed_offset,
                    type_param,
                    str(safe_n_min),
                    str(safe_n_max),
                    str(safe_t_min),
                    str(safe_t_max),
                    extra_args,
                )
            )
        return safe_configs
