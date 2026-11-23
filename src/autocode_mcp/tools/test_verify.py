"""
Test Verification 工具 - 验证生成的测试数据。

检查文件完整性、答案一致性、约束覆盖等。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .. import TEMPLATES_DIR
from ..runtime_store import TEST_MANIFEST, WORKFLOW, get_section
from ..utils.answer_ext import normalize_answer_ext
from ..utils.checker_judge import (
    checker_exe_path,
    run_testlib_checker,
    verdict_from_run,
    verdict_from_testlib_rc,
)
from ..utils.communication_runner import (
    CommunicationRunner,
    _run_communicator_stage,
    is_safe_stem,
    resolve_communicator_binary,
    resolve_grader_source_existing,
    resolve_participant_binaries,
    safe_resolve_under_root,
)
from ..utils.compiler import (
    compile_cpp,
    resolve_effective_compiler,
    run_batch,
    run_binary,
    run_binary_with_args,
)
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..workflow import (
    current_platform,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_protocol_judge,
    manifest_uses_testlib_checker,
    record_verify_state,
)
from ..workflow.models import AutoCodeManifest
from .base import Tool, ToolResult, input_schema_from_model
from .schemas import ProblemVerifyTestsInput

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
    exe_ext = get_exe_extension()
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


class ProblemVerifyTestsTool(Tool):
    """验证生成的测试数据。"""

    @property
    def name(self) -> str:
        return "problem_verify_tests"

    @property
    def description(self) -> str:
        return """验证生成的测试数据质量。

        自动执行以下检查：
        1. file_count: 每个 .in 有对应的 .ans，文件名连续
        2. answer_consistency: 用 sol 重跑 .in；special_judge 且 stress_comparison=checker 时用 checker，否则比 .ans 文本
        3. validator: 用 val 检查每个 .in 是否满足约束（如有 files/val）
        4. no_empty: 没有空文件
        5. limit_ratio: 最终测试中 extreme/tle（type=3/4）不少于一半（需存在 manifest）

        前置条件：
        1. 已运行 problem_generate_tests 生成测试数据
        2. 已运行 solution_build 构建 sol

        建议下一步：
        - 如果验证通过：运行 problem_pack_polygon 打包
        - 如果验证失败：根据失败信息修复问题
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemVerifyTestsInput)

    async def execute(
        self,
        problem_dir: str,
        tests_dir: str | None = None,
        verify_types: list[str] | None = None,
        sol_name: str | None = None,
        enable_limit_ratio: bool = True,
        answer_ext: str | None = None,
        timeout: int = 60,
        wrong_solution_names: list[str] | None = None,
        tle_kill_factor: float | None = None,
        slowdown_kill_ratio: float | None = None,
    ) -> ToolResult:
        """执行测试数据验证。"""
        policy_err = _validate_tle_policy_params(tle_kill_factor, slowdown_kill_ratio)
        if policy_err is not None:
            return ToolResult.fail(policy_err)

        effective_sol_name = sol_name or "sol"

        # 解析测试目录
        if tests_dir:
            if not os.path.isabs(tests_dir):
                tests_dir = os.path.join(problem_dir, tests_dir)
        else:
            tests_dir = os.path.join(problem_dir, "tests")

        if not os.path.exists(tests_dir):
            self._write_workflow_state(
                problem_dir=problem_dir,
                success=False,
                passed=False,
                quality_signals={},
                results={},
            )
            return ToolResult.fail(f"Tests directory not found: {tests_dir}")

        # tests manifest 一次性加载并透传，避免各检查重复 open。
        tests_manifest = self._read_tests_manifest(tests_dir)
        resolved_answer_ext = self._resolve_answer_ext(tests_dir, answer_ext, tests_manifest)
        try:
            manifest_model = load_manifest(problem_dir)
        except (ValidationError, OSError, ValueError) as exc:
            self._write_workflow_state(
                problem_dir=problem_dir,
                success=False,
                passed=False,
                quality_signals={},
                results={},
            )
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")
        verify_with_checker = manifest_uses_testlib_checker(manifest_model)
        is_interactive = manifest_is_interactive(manifest_model)
        is_communication = manifest_model is not None and manifest_is_communication(manifest_model)
        uses_protocol = manifest_uses_protocol_judge(manifest_model)
        comm_needs_communicator = is_communication and (
            manifest_model is not None
            and (manifest_model.protocol is None or manifest_model.protocol.mode != "grader_linked")
        )

        # 默认执行当前 manifest 门禁所需的全部检查项；verify_types=["all"] 额外全量展开
        explicit_all = bool(verify_types and "all" in verify_types)
        if not verify_types or explicit_all:
            verify_types = ["file_count", "answer_consistency", "validator", "no_empty"]
            if manifest_model is not None or explicit_all:
                for extra in ("duplicate_inputs", "scale_distribution"):
                    if extra not in verify_types:
                        verify_types.append(extra)
                if tests_manifest or explicit_all:
                    if "purpose_coverage" not in verify_types:
                        verify_types.append("purpose_coverage")
                discovered_wrongs, _ = _discover_wrong_solution_names(
                    problem_dir, manifest_model, wrong_solution_names
                )
                need_wrong_kill = bool(
                    discovered_wrongs
                    or (
                        manifest_model is not None
                        and manifest_model.quality_gates.require_wrong_solution_kill
                    )
                )
                if need_wrong_kill and "wrong_solution_kill" not in verify_types:
                    verify_types.append("wrong_solution_kill")
                if not uses_protocol and (
                    explicit_all
                    or (manifest_model is not None and manifest_model.audit_gates.require_validator_self_test)
                ):
                    if "validator_self_test" not in verify_types:
                        verify_types.append("validator_self_test")
                if verify_with_checker and (
                    explicit_all
                    or (manifest_model is not None and manifest_model.audit_gates.require_checker_self_test)
                ):
                    if "checker_self_test" not in verify_types:
                        verify_types.append("checker_self_test")
                if (is_interactive or comm_needs_communicator) and (
                    explicit_all
                    or (manifest_model is not None and manifest_model.audit_gates.require_interactor_self_test)
                ):
                    if "interactor_self_test" not in verify_types:
                        verify_types.append("interactor_self_test")
                if explicit_all and manifest_model is not None and manifest_model.constraints:
                    if "boundary_coverage" not in verify_types:
                        verify_types.append("boundary_coverage")
        else:
            verify_types = list(verify_types)

        if enable_limit_ratio:
            if "limit_ratio" not in verify_types:
                verify_types.append("limit_ratio")
            if "limit_semantics" not in verify_types:
                verify_types.append("limit_semantics")
        else:
            verify_types = [v for v in verify_types if v not in {"limit_ratio", "limit_semantics"}]

        dataset_fingerprint = _compute_dataset_fingerprint(
            problem_dir,
            tests_dir,
            manifest_model,
            answer_ext=resolved_answer_ext,
            tests_manifest=tests_manifest,
        )

        results = {}
        all_passed = True

        # 1. 文件完整性检查（交互题 / 通信题允许省略 .ans）
        answers_optional = is_interactive or is_communication
        if "file_count" in verify_types:
            result = self._check_file_count(
                tests_dir, resolved_answer_ext, is_interactive=answers_optional
            )
            results["file_count"] = result
            if not result["passed"]:
                all_passed = False

        # 2. 空文件检查
        if "no_empty" in verify_types:
            result = self._check_no_empty(
                tests_dir, is_interactive=answers_optional, answer_ext=resolved_answer_ext
            )
            results["no_empty"] = result
            if not result["passed"]:
                all_passed = False

        # 3. 答案一致性检查
        if "answer_consistency" in verify_types:
            result = await self._check_answer_consistency(
                problem_dir,
                tests_dir,
                effective_sol_name,
                resolved_answer_ext,
                timeout,
                verify_with_checker=verify_with_checker,
                manifest=manifest_model,
            )
            results["answer_consistency"] = result
            if not result["passed"]:
                all_passed = False

        # 4. Validator 检查
        if "validator" in verify_types:
            result = await self._check_validator(problem_dir, tests_dir, timeout)
            results["validator"] = result
            if not result["passed"]:
                all_passed = False

        # 5. 极限数据占比检查
        if "limit_ratio" in verify_types:
            result = self._check_limit_ratio(tests_dir, tests_manifest)
            results["limit_ratio"] = result
            if not result["passed"]:
                all_passed = False
        if "limit_semantics" in verify_types:
            result = self._check_limit_semantics(tests_dir, tests_manifest)
            results["limit_semantics"] = result
            if not result["passed"]:
                all_passed = False

        if "wrong_solution_kill" in verify_types:
            wrong_names, undeclared_wrongs = _discover_wrong_solution_names(
                problem_dir, manifest_model, wrong_solution_names
            )
            std_timings: dict[str, float] | None = None
            ans_res = results.get("answer_consistency")
            if isinstance(ans_res, dict) and isinstance(ans_res.get("per_test_timings"), list):
                std_timings = {
                    str(item["file"]): float(item["time_ms"])
                    for item in ans_res["per_test_timings"]
                    if isinstance(item, dict)
                    and isinstance(item.get("file"), str)
                    and isinstance(item.get("time_ms"), int | float)
                }
            else:
                prev_wf = get_section(problem_dir, WORKFLOW) or {}
                prev_signals = prev_wf.get("verify_signals", {}) if isinstance(prev_wf, dict) else {}
                prev_ans = prev_signals.get("answer_consistency", {}) if isinstance(prev_signals, dict) else {}
                prev_ev = prev_ans.get("evidence", {}) if isinstance(prev_ans, dict) else {}
                if isinstance(prev_ev, dict) and isinstance(prev_ev.get("per_test_timings"), list):
                    cur_in_files = sorted(
                        f for f in os.listdir(tests_dir) if f.endswith(".in")
                    )
                    expected_fp = _timing_cache_fingerprint(
                        problem_dir,
                        tests_dir,
                        effective_sol_name,
                        cur_in_files,
                        manifest=manifest_model,
                    )
                    if prev_ev.get("timing_fingerprint") == expected_fp:
                        std_timings = {
                            str(item["file"]): float(item["time_ms"])
                            for item in prev_ev["per_test_timings"]
                            if isinstance(item, dict)
                            and isinstance(item.get("file"), str)
                            and isinstance(item.get("time_ms"), int | float)
                            and not isinstance(item.get("time_ms"), bool)
                        }
            result = await self._check_wrong_solution_kill(
                problem_dir=problem_dir,
                tests_dir=tests_dir,
                wrong_solution_names=wrong_names,
                answer_ext=resolved_answer_ext,
                timeout=timeout,
                verify_with_checker=verify_with_checker,
                manifest=manifest_model,
                std_timings=std_timings,
                tle_kill_factor=tle_kill_factor,
                slowdown_kill_ratio=slowdown_kill_ratio,
                sol_name=effective_sol_name,
                undeclared_wrong_solutions=undeclared_wrongs,
            )
            results["wrong_solution_kill"] = result
            if not result["passed"]:
                all_passed = False

        if "duplicate_inputs" in verify_types:
            max_near = (
                float(manifest_model.audit_gates.max_duplicate_input_ratio)
                if manifest_model is not None
                else 0.2
            )
            result = self._check_duplicate_inputs(tests_dir, max_near_ratio=max_near)
            results["duplicate_inputs"] = result
            if not result["passed"]:
                all_passed = False

        if "scale_distribution" in verify_types:
            result = self._check_scale_distribution(tests_dir)
            results["scale_distribution"] = result
            if not result["passed"]:
                all_passed = False

        if "purpose_coverage" in verify_types:
            result = self._check_purpose_coverage(tests_dir, manifest=manifest_model)
            results["purpose_coverage"] = result
            if not result["passed"] and not result.get("skipped", False):
                all_passed = False

        if "boundary_coverage" in verify_types:
            result = self._check_boundary_coverage(tests_dir, manifest_model)
            results["boundary_coverage"] = result
            if not result["passed"] and not result.get("skipped", False):
                all_passed = False

        if "validator_self_test" in verify_types:
            result = await self._check_validator_self_test(problem_dir, tests_dir, timeout)
            results["validator_self_test"] = result
            if not result["passed"] and not result.get("skipped", False):
                all_passed = False

        if "checker_self_test" in verify_types:
            result = await self._check_checker_self_test(problem_dir, timeout)
            results["checker_self_test"] = result
            if not result["passed"] and not result.get("skipped", False):
                all_passed = False

        if "interactor_self_test" in verify_types:
            result = await self._check_interactor_self_test(
                problem_dir, timeout, manifest=manifest_model
            )
            results["interactor_self_test"] = result
            if not result["passed"] and not result.get("skipped", False):
                all_passed = False

        # 汇总
        total_checks = len(results)
        passed_checks = sum(
            1 for r in results.values() if bool(r.get("passed")) and not r.get("skipped", False)
        )
        skipped_checks = sum(1 for r in results.values() if r.get("skipped", False))
        effective_checks = total_checks - skipped_checks
        quality_signals = self._build_quality_signals(verify_types, results, manifest=manifest_model)
        executed_signals = sorted(
            _SIGNAL_MAP[v] for v in verify_types if v in _SIGNAL_MAP
        )

        if all_passed and effective_checks:
            message = f"All {total_checks} verification checks passed"
            if skipped_checks:
                message = (
                    f"{passed_checks}/{total_checks} verification checks passed, "
                    f"{skipped_checks} skipped"
                )
            self._write_workflow_state(
                problem_dir=problem_dir,
                success=True,
                passed=True,
                quality_signals=quality_signals,
                results=results,
                dataset_fingerprint=dataset_fingerprint,
                executed_signals=executed_signals,
            )
            return ToolResult.ok(
                passed=True,
                results=results,
                quality_signals=quality_signals,
                dataset_fingerprint=dataset_fingerprint,
                executed_signals=executed_signals,
                total_checks=total_checks,
                passed_checks=passed_checks,
                skipped_checks=skipped_checks,
                effective_checks=effective_checks,
                tests_dir=tests_dir,
                sol_name=effective_sol_name,
                limit_ratio_enabled=enable_limit_ratio,
                message=message,
            )
        else:
            self._write_workflow_state(
                problem_dir=problem_dir,
                success=False,
                passed=False,
                quality_signals=quality_signals,
                results=results,
                dataset_fingerprint=dataset_fingerprint,
                executed_signals=executed_signals,
            )
            return ToolResult.fail(
                f"{passed_checks}/{total_checks} checks passed",
                passed=False,
                results=results,
                quality_signals=quality_signals,
                dataset_fingerprint=dataset_fingerprint,
                executed_signals=executed_signals,
                total_checks=total_checks,
                passed_checks=passed_checks,
                skipped_checks=skipped_checks,
                effective_checks=effective_checks,
                tests_dir=tests_dir,
                sol_name=effective_sol_name,
                limit_ratio_enabled=enable_limit_ratio,
            )

    def _build_quality_signals(
        self,
        verify_types: list[str],
        results: dict,
        *,
        manifest: AutoCodeManifest | None = None,
    ) -> dict[str, dict]:
        verify_set = set(verify_types)
        signals: dict[str, dict] = {}
        for verify_name, signal_name in _SIGNAL_MAP.items():
            raw_result = results.get(verify_name)
            result = raw_result if isinstance(raw_result, dict) else {}
            executed = verify_name in verify_set and isinstance(result, dict)
            if executed and bool(result.get("skipped", False)):
                executed = False
            signals[signal_name] = {
                "executed": executed,
                "passed": bool(result.get("passed")) if executed else False,
                "evidence": result if executed else {},
            }

        if manifest is not None:
            uses_protocol = manifest_uses_protocol_judge(manifest)
            is_interactive = manifest_is_interactive(manifest)
            is_comm = manifest_is_communication(manifest)
            comm_needs_comm = is_comm and (
                manifest.protocol is None or manifest.protocol.mode != "grader_linked"
            )
            if uses_protocol and not signals["validator_self_test"]["executed"]:
                signals["validator_self_test"] = {
                    "applicable": False,
                    "status": "not_applicable",
                    "executed": False,
                    "passed": True,
                    "evidence": {"reason": "not applicable for protocol problems"},
                }
            if not manifest_uses_testlib_checker(manifest) and not signals["checker_self_test"]["executed"]:
                signals["checker_self_test"] = {
                    "applicable": False,
                    "status": "not_applicable",
                    "executed": False,
                    "passed": True,
                    "evidence": {"reason": "not applicable when stress_comparison != checker"},
                }
            if not (is_interactive or comm_needs_comm) and not signals["interactor_self_test"]["executed"]:
                signals["interactor_self_test"] = {
                    "applicable": False,
                    "status": "not_applicable",
                    "executed": False,
                    "passed": True,
                    "evidence": {"reason": "not applicable for non-interactor problems"},
                }
        return signals

    def _write_workflow_state(
        self,
        *,
        problem_dir: str,
        success: bool,
        passed: bool,
        quality_signals: dict[str, dict],
        results: dict,
        dataset_fingerprint: str | None = None,
        executed_signals: list[str] | None = None,
    ) -> None:
        try:
            record_verify_state(
                problem_dir,
                success=success,
                passed=passed,
                results=results,
                quality_signals=quality_signals,
                dataset_fingerprint=dataset_fingerprint,
                executed_signals=executed_signals,
            )
        except OSError:
            # Best-effort persistence (matches contract when .autocode is unwritable)
            pass

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

    async def _check_answer_consistency(
        self,
        problem_dir: str,
        tests_dir: str,
        sol_name: str,
        answer_ext: str,
        timeout: int,
        *,
        verify_with_checker: bool = False,
        manifest: AutoCodeManifest | None = None,
        use_system_temp: bool = False,
        sol_exe_override: str | None = None,
        checker_exe_override: str | None = None,
        interactor_exe_override: str | None = None,
    ) -> dict:
        """用 sol 重新运行 .in；verify_with_checker 时用 checker(input, sol_out, jury_ans)，否则比字符串。"""
        exe_ext = get_exe_extension()
        is_interactive = manifest_is_interactive(manifest)
        is_communication = manifest is not None and manifest_is_communication(manifest)
        comm_mode = (
            (manifest.protocol.mode if manifest.protocol else "run_twice")
            if (is_communication and manifest is not None)
            else ""
        )

        if sol_exe_override and os.path.isfile(sol_exe_override):
            sol_exe = sol_exe_override
        else:
            sol_exe = os.path.join(problem_dir, "solutions", f"{sol_name}{exe_ext}")
            if not os.path.exists(sol_exe):
                sol_exe = os.path.join(problem_dir, f"{sol_name}{exe_ext}")

        if not os.path.exists(sol_exe):
            if (
                is_communication
                and manifest is not None
                and manifest.protocol is not None
                and manifest.protocol.participant_solutions
                and sol_name == "sol"
            ):
                parts = resolve_participant_binaries(problem_dir, manifest, None, exe_ext)
                if parts and all(os.path.isfile(p) for p in parts):
                    sol_exe = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
                else:
                    return {
                        "passed": False,
                        "total": 0,
                        "mismatches": [],
                        "error": "Participant solution binaries not found",
                    }
            else:
                return {
                    "passed": False,
                    "total": 0,
                    "mismatches": [],
                    "error": f"{sol_name}{exe_ext} not found, run solution_build first",
                }

        in_files = sorted(
            (
                f
                for f in os.listdir(tests_dir)
                if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
            ),
            key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)],
        )

        mismatches = []
        timed_out = []
        errors = []
        test_timeout = (
            max(0.1, float(manifest.time_limit_ms) / 1000.0 * 1.5)
            if (manifest and getattr(manifest, "time_limit_ms", None))
            else min(5.0, float(timeout))
        )
        mem_mb = (
            float(manifest.memory_limit_mb)
            if (manifest and getattr(manifest, "memory_limit_mb", None))
            else 256.0
        )
        interactor_bin = ""
        if is_interactive:
            if interactor_exe_override and os.path.isfile(interactor_exe_override):
                interactor_bin = interactor_exe_override
            else:
                interactor_bin = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
                if not os.path.isfile(interactor_bin):
                    interactor_bin = os.path.join(problem_dir, f"interactor{exe_ext}")
            if not os.path.isfile(interactor_bin):
                return {
                    "passed": False,
                    "total": len(in_files),
                    "mismatches": [],
                    "timed_out": [],
                    "errors": [],
                    "error": f"interactive problem requires files/interactor{exe_ext}",
                    "mode": "interactive",
                }
        elif is_communication and manifest is not None and comm_mode != "grader_linked":
            if interactor_exe_override and os.path.isfile(interactor_exe_override):
                interactor_bin = interactor_exe_override
            else:
                interactor_bin = resolve_communicator_binary(problem_dir, manifest, exe_ext) or ""
            if not interactor_bin:
                return {
                    "passed": False,
                    "total": len(in_files),
                    "mismatches": [],
                    "timed_out": [],
                    "errors": [],
                    "error": f"communication problem ({comm_mode}) requires compiled communicator binary",
                    "mode": f"communication:{comm_mode}",
                }

        checker_bin = ""
        if not is_interactive and not is_communication and verify_with_checker:
            checker_bin = (
                checker_exe_override
                if (checker_exe_override and os.path.isfile(checker_exe_override))
                else checker_exe_path(problem_dir, exe_ext)
            )
            if not os.path.isfile(checker_bin):
                return {
                    "passed": False,
                    "total": len(in_files),
                    "mismatches": [],
                    "timed_out": [],
                    "errors": [],
                    "error": f"special_judge+stress_comparison=checker requires files/checker{exe_ext}",
                    "mode": "checker",
                }

        async def _check_one(in_file: str) -> tuple[str, Any] | None:
            in_path = os.path.join(tests_dir, in_file)
            ans_file = Path(in_file).with_suffix(answer_ext).name
            ans_path = os.path.join(tests_dir, ans_file)

            if is_interactive or (is_communication and manifest is not None):
                with open(in_path, encoding="utf-8") as f:
                    input_data = f.read()
                expected = ""
                if os.path.exists(ans_path):
                    with open(ans_path, encoding="utf-8") as f:
                        expected = f.read()
                if is_communication and manifest is not None:
                    jres = await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest,
                        solution_exe=sol_exe,
                        input_data=input_data,
                        answer_data=expected,
                        time_limit_ms=test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        use_system_temp=use_system_temp,
                        communicator_exe_override=interactor_bin or None,
                    )
                else:
                    jres = await InteractiveRunner.run(
                        interactor_exe=interactor_bin,
                        solution_exe=sol_exe,
                        input_data=input_data,
                        answer_data=expected,
                        time_limit_ms=test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        cwd=None if use_system_temp else problem_dir,
                    )
                if jres.verdict in ("TLE", "DEADLOCK"):
                    return (
                        "timed_out",
                        {
                            "file": in_file,
                            "verdict": jres.verdict,
                            "detail": jres.detail[:300],
                        },
                    )
                if jres.verdict in ("RE", "MLE", "FAIL"):
                    return (
                        "error",
                        {
                            "file": in_file,
                            "verdict": jres.verdict,
                            "detail": jres.detail[:500],
                            "stderr": jres.stderr[:300],
                        },
                    )
                if not jres.accepted:
                    return (
                        "mismatch",
                        {
                            "file": in_file,
                            "verdict": jres.verdict,
                            "detail": jres.detail[:500],
                            "judge_stderr": jres.judge_stderr[:500],
                            "transcript": jres.transcript[:1000],
                        },
                    )
                elapsed_ms = (
                    float(jres.cpu_time_ms)
                    if jres.cpu_time_ms is not None
                    else float(jres.wall_time_ms)
                )
                return ("ok", {"file": in_file, "time_ms": round(elapsed_ms, 2), "metrics": jres.metrics})

            if not os.path.exists(ans_path):
                return None

            with open(in_path, encoding="utf-8") as f:
                input_data = f.read()

            with open(ans_path, encoding="utf-8") as f:
                expected = f.read()

            result = await run_binary(
                sol_exe,
                input_data,
                timeout=test_timeout,
                memory_mb=mem_mb,
            )

            if result.timed_out:
                return ("timed_out", in_file)
            if not result.success:
                return (
                    "error",
                    {
                        "file": in_file,
                        "return_code": result.return_code,
                        "error": result.error or "",
                        "stderr": result.stderr or "",
                    },
                )
            if verify_with_checker:
                tmp_parent = None if use_system_temp else problem_dir
                with tempfile.TemporaryDirectory(dir=tmp_parent) as tmp:
                    out_path = os.path.join(tmp, "output.txt")
                    with open(out_path, "w", encoding="utf-8", newline="\n") as out_f:
                        out_f.write(result.stdout)
                    verdict, chk_run = await run_testlib_checker(
                        checker_bin,
                        in_path,
                        out_path,
                        ans_path,
                        timeout=timeout,
                    )
                    if verdict != "AC":
                        return (
                            "mismatch",
                            {
                                "file": in_file,
                                "checker_verdict": verdict,
                                "checker_stderr": (chk_run.stderr or "")[:500],
                            },
                        )
                return ("ok", {"file": in_file, "time_ms": round(float(result.time_ms), 2)})
            if result.stdout.replace("\r\n", "\n").strip() != expected.replace("\r\n", "\n").strip():
                return (
                    "mismatch",
                    {
                        "file": in_file,
                        "expected": expected[:200],
                        "actual": result.stdout[:200],
                    },
                )
            return ("ok", {"file": in_file, "time_ms": round(float(result.time_ms), 2)})

        outcomes = await run_batch(in_files, _check_one, limit=_VERIFY_CONCURRENCY)
        protocol_metrics: dict[str, int] = {}
        per_test_timings: list[dict[str, Any]] = []
        for outcome in outcomes:
            if outcome is None:
                continue
            kind, payload = outcome
            if kind == "timed_out":
                timed_out.append(payload)
            elif kind == "error":
                errors.append(payload)
            elif kind == "mismatch":
                mismatches.append(payload)
            elif kind == "ok" and isinstance(payload, dict):
                if isinstance(payload.get("file"), str) and isinstance(
                    payload.get("time_ms"), int | float
                ):
                    per_test_timings.append(
                        {"file": payload["file"], "time_ms": float(payload["time_ms"])}
                    )
                metrics_dict = payload.get("metrics")
                if isinstance(metrics_dict, dict):
                    for mkey in ("queries", "bits_used", "bytes_used", "rounds"):
                        val = metrics_dict.get(mkey)
                        if isinstance(val, int) and not isinstance(val, bool):
                            agg_key = f"max_{mkey}"
                            protocol_metrics[agg_key] = max(protocol_metrics.get(agg_key, 0), val)

        passed = not mismatches and not timed_out and not errors
        timing_vals = [float(item["time_ms"]) for item in per_test_timings]
        timing_fp = _timing_cache_fingerprint(
            problem_dir, tests_dir, sol_name, in_files, manifest=manifest
        )
        out: dict = {
            "passed": passed,
            "total": len(in_files),
            "mismatches": mismatches,
            "timed_out": timed_out,
            "errors": errors,
            "per_test_timings": per_test_timings,
            "timing_fingerprint": timing_fp,
            "max_time_ms": round(max(timing_vals), 2) if timing_vals else None,
            "total_time_ms": round(sum(timing_vals), 2) if timing_vals else None,
        }
        if is_communication:
            out["mode"] = f"communication:{comm_mode}"
            if protocol_metrics:
                out["protocol_metrics"] = protocol_metrics
        elif is_interactive:
            out["mode"] = "interactive"
            if protocol_metrics:
                out["protocol_metrics"] = protocol_metrics
        elif verify_with_checker:
            out["mode"] = "checker"
        return out

    async def _check_validator(
        self,
        problem_dir: str,
        tests_dir: str,
        timeout: int,
        *,
        val_exe_override: str | None = None,
    ) -> dict:
        """用 val 检查每个 .in 是否满足约束。"""
        exe_ext = get_exe_extension()
        val_exe = (
            val_exe_override
            if (val_exe_override and os.path.isfile(val_exe_override))
            else os.path.join(problem_dir, "files", f"val{exe_ext}")
        )

        if not os.path.exists(val_exe):
            return {
                "passed": True,
                "total": 0,
                "skipped": True,
                "message": f"files/val{exe_ext} not found, validator check skipped (run validator_build)",
            }

        in_files = sorted(
            (
                f
                for f in os.listdir(tests_dir)
                if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
            ),
            key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)],
        )

        async def _validate_one(in_file: str) -> dict | None:
            in_path = os.path.join(tests_dir, in_file)

            with open(in_path, encoding="utf-8") as f:
                input_data = f.read()

            result = await run_binary(val_exe, input_data, timeout=timeout)

            if result.return_code != 0 or not result.success:
                err_detail = result.stderr or result.error or ""
                return {
                    "file": in_file,
                    "return_code": result.return_code,
                    "error": result.error or "",
                    "stderr": err_detail[:200],
                }
            return None

        outcomes = await run_batch(in_files, _validate_one, limit=_VERIFY_CONCURRENCY)
        invalid = [o for o in outcomes if o is not None]

        return {
            "passed": len(invalid) == 0,
            "total": len(in_files),
            "invalid": invalid,
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

    async def _check_validator_self_test(self, problem_dir: str, tests_dir: str, timeout: int) -> dict:
        exe_ext = get_exe_extension()
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")
        if not os.path.exists(val_exe):
            return {
                "passed": False,
                "skipped": True,
                "message": f"files/val{exe_ext} not found",
            }
        valid_result = await self._check_validator(problem_dir, tests_dir, timeout)
        invalid_fixtures = self._collect_invalid_fixtures(problem_dir, tests_dir)
        rejected = []
        accepted = []
        fixture_errors = []
        for fixture in invalid_fixtures:
            try:
                if "path" in fixture:
                    fixture_path = Path(str(fixture["path"]))
                    input_data = fixture_path.read_text(encoding="utf-8", errors="replace")
                    name = str(fixture.get("name") or fixture_path.name)
                else:
                    input_data = str(fixture.get("input", ""))
                    name = str(fixture.get("name") or f"inline-{len(rejected) + len(accepted) + 1}")
            except (OSError, UnicodeError) as exc:
                fixture_errors.append({"fixture": str(fixture), "error": str(exc)})
                continue
            result = await run_binary(val_exe, input_data, timeout=timeout)
            if result.return_code != 0:
                rejected.append(name)
            else:
                accepted.append(name)
        passed = bool(valid_result.get("passed")) and bool(invalid_fixtures) and not accepted and not fixture_errors
        return {
            "passed": passed,
            "valid_tests": valid_result,
            "invalid_total": len(invalid_fixtures),
            "invalid_rejected": rejected,
            "invalid_accepted": accepted,
            "invalid_fixture_errors": fixture_errors,
            "skipped": not invalid_fixtures,
            "message": "No invalid fixtures found" if not invalid_fixtures else "",
        }

    async def _check_checker_self_test(self, problem_dir: str, timeout: int) -> dict:
        exe_ext = get_exe_extension()
        checker_bin = checker_exe_path(problem_dir, exe_ext)
        if not os.path.isfile(checker_bin):
            return {
                "passed": False,
                "skipped": True,
                "message": f"files/checker{exe_ext} not found",
            }
        scenarios = self._load_scenarios(problem_dir, "checker_scenarios.json")
        if not scenarios:
            return {"passed": False, "skipped": True, "message": "checker scenarios not found"}
        details = []
        correct = 0
        with tempfile.TemporaryDirectory(dir=problem_dir) as tmp:
            for idx, scenario in enumerate(scenarios, 1):
                input_path = os.path.join(tmp, f"checker_{idx}.in")
                output_path = os.path.join(tmp, f"checker_{idx}.out")
                answer_path = os.path.join(tmp, f"checker_{idx}.ans")
                Path(input_path).write_text(str(scenario.get("input", "")), encoding="utf-8")
                Path(output_path).write_text(
                    str(scenario.get("contestant_output", "")), encoding="utf-8"
                )
                Path(answer_path).write_text(
                    str(scenario.get("reference_output", "")), encoding="utf-8"
                )
                expected = str(scenario.get("expected_verdict", "AC"))
                verdict, run = await run_testlib_checker(
                    checker_bin,
                    input_path,
                    output_path,
                    answer_path,
                    timeout=timeout,
                )
                ok = verdict == expected
                correct += 1 if ok else 0
                details.append(
                    {
                        "index": idx,
                        "expected_verdict": expected,
                        "actual_verdict": verdict,
                        "correct": ok,
                        "stderr": (run.stderr or "")[:500],
                    }
                )
        categories = self._scenario_categories(
            scenarios,
            default_by_verdict={"AC": "ac", "WA": "wrong_answer", "PE": "format_error"},
        )
        required_categories = {"ac", "wrong_answer", "format_error"}
        missing_categories = sorted(required_categories - categories)
        passed = correct == len(scenarios) and not missing_categories
        return {
            "passed": passed,
            "total": len(scenarios),
            "correct_count": correct,
            "scenario_categories": sorted(categories),
            "missing_required_categories": missing_categories,
            "details": details,
        }

    async def _check_interactor_self_test(
        self,
        problem_dir: str,
        timeout: int,
        *,
        manifest: AutoCodeManifest | None = None,
    ) -> dict:
        exe_ext = get_exe_extension()
        is_communication = manifest is not None and manifest_is_communication(manifest)
        is_run_twice = is_communication and (
            manifest is not None
            and (manifest.protocol is None or manifest.protocol.mode == "run_twice")
        )
        total_passes = manifest.protocol.passes if (manifest and manifest.protocol) else 2

        if is_communication:
            interactor_bin = resolve_communicator_binary(problem_dir, manifest, exe_ext) or ""
            if not interactor_bin:
                return {
                    "passed": False,
                    "skipped": True,
                    "message": f"compiled communicator binary not found in {problem_dir}",
                }
            scenarios = self._load_scenarios(
                problem_dir, "communicator_scenarios.json"
            ) or self._load_scenarios(problem_dir, "interactor_scenarios.json")
            if not scenarios:
                return {
                    "passed": False,
                    "skipped": True,
                    "message": "communicator scenarios not found",
                }
        else:
            interactor_bin = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
            if not os.path.isfile(interactor_bin):
                return {
                    "passed": False,
                    "skipped": True,
                    "message": f"files/interactor{exe_ext} not found",
                }
            scenarios = self._load_scenarios(problem_dir, "interactor_scenarios.json")
            if not scenarios:
                return {"passed": False, "skipped": True, "message": "interactor scenarios not found"}

        details = []
        correct = 0
        with tempfile.TemporaryDirectory(dir=os.path.abspath(problem_dir)) as raw_tmp:
            tmp = os.path.abspath(raw_tmp)
            for idx, scenario in enumerate(scenarios, 1):
                input_path = os.path.join(tmp, f"interactor_{idx}.in")
                output_path = os.path.join(tmp, f"interactor_{idx}.out")
                answer_path = os.path.join(tmp, f"interactor_{idx}.ans")
                Path(input_path).write_text(str(scenario.get("input", "")), encoding="utf-8")
                Path(answer_path).write_text(str(scenario.get("answer") or ""), encoding="utf-8")
                expected = str(scenario.get("expected_verdict", "AC"))
                if is_run_twice:
                    Path(output_path).write_text(
                        str(scenario.get("contestant_output", "")), encoding="utf-8"
                    )
                    raw_stage = scenario.get("stage")
                    try:
                        stage = (
                            int(raw_stage)
                            if (raw_stage is not None and not isinstance(raw_stage, bool))
                            else total_passes
                        )
                    except (TypeError, ValueError):
                        stage = total_passes
                    if stage < 1:
                        stage = total_passes
                    stage_env = dict(os.environ)
                    stage_env["AUTOCODE_COMM_STAGE"] = str(stage)
                    stage_env["AUTOCODE_COMM_PASSES"] = str(total_passes)
                    stage_env["AUTOCODE_COMM_ROLE"] = (
                        "channel" if stage < total_passes else "judge"
                    )
                    stage_env["AUTOCODE_COMM_ANS_PATH"] = answer_path
                    rc, _, err_str, timed_out = await _run_communicator_stage(
                        os.path.abspath(interactor_bin),
                        [input_path, output_path, answer_path],
                        env=stage_env,
                        timeout_sec=float(timeout),
                        cwd=tmp,
                    )
                    verdict = "TLE" if timed_out else verdict_from_testlib_rc(rc)
                    stderr_snippet = err_str[:500]
                else:
                    run = await run_binary_with_args(
                        interactor_bin,
                        [input_path, output_path, answer_path],
                        stdin=str(scenario.get("contestant_output", "")),
                        timeout=timeout,
                    )
                    verdict = verdict_from_run(run)
                    stderr_snippet = (run.stderr or "")[:500]
                ok = verdict == expected
                correct += 1 if ok else 0
                details.append(
                    {
                        "index": idx,
                        "expected_verdict": expected,
                        "actual_verdict": verdict,
                        "correct": ok,
                        "stderr": stderr_snippet,
                    }
                )
        if is_communication:
            categories = self._scenario_categories(
                scenarios,
                default_by_verdict={"AC": "ac", "WA": "wrong_answer", "PE": "format_error"},
            )
            required_categories = {"ac", "wrong_answer", "format_error"}
        else:
            categories = self._scenario_categories(
                scenarios,
                default_by_verdict={"AC": "ac", "WA": "wrong_answer", "PE": "malformed_command"},
            )
            required_categories = {
                "ac",
                "wrong_answer",
                "malformed_command",
                "out_of_range",
                "query_limit",
                "premature_eof",
                "extra_output",
            }
        missing_categories = sorted(required_categories - categories)
        passed = correct == len(scenarios) and not missing_categories
        return {
            "passed": passed,
            "total": len(scenarios),
            "correct_count": correct,
            "scenario_categories": sorted(categories),
            "missing_required_categories": missing_categories,
            "details": details,
        }

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

    async def _measure_std_timings(
        self,
        problem_dir: str,
        in_files: list[Path],
        answer_ext: str,
        sol_name: str,
        test_timeout: float,
        mem_mb: float,
        *,
        is_interactive: bool,
        is_communication: bool,
        interactor_bin: str,
        manifest: AutoCodeManifest | None,
        use_system_temp: bool,
    ) -> dict[str, float]:
        """Measure per-test runtime of the standard solution for wrong_ms/std_ms comparison."""
        exe_ext = get_exe_extension()
        sol_exe = os.path.join(problem_dir, "solutions", f"{sol_name}{exe_ext}")
        if not os.path.isfile(sol_exe):
            sol_exe = os.path.join(problem_dir, f"{sol_name}{exe_ext}")
        if not os.path.isfile(sol_exe):
            if (
                is_communication
                and manifest is not None
                and manifest.protocol is not None
                and manifest.protocol.participant_solutions
                and sol_name == "sol"
            ):
                parts = resolve_participant_binaries(problem_dir, manifest, None, exe_ext)
                if parts and all(os.path.isfile(p) for p in parts):
                    sol_exe = os.path.join(problem_dir, "solutions", f"sol{exe_ext}")
                else:
                    return {}
            else:
                return {}

        async def _time_one(in_file: Path) -> tuple[str, float] | None:
            ans_file = in_file.with_suffix(answer_ext)
            input_data = in_file.read_text(encoding="utf-8", errors="replace")
            if is_communication and manifest is not None:
                answer_data = ans_file.read_text(encoding="utf-8", errors="replace") if ans_file.exists() else ""
                jres = await CommunicationRunner.run_from_manifest(
                    problem_dir=problem_dir,
                    manifest=manifest,
                    solution_exe=sol_exe,
                    input_data=input_data,
                    answer_data=answer_data,
                    time_limit_ms=test_timeout * 1000.0,
                    memory_limit_mb=mem_mb,
                    use_system_temp=use_system_temp,
                    communicator_exe_override=interactor_bin or None,
                )
                if not jres.accepted:
                    return None
                ms = float(jres.cpu_time_ms if jres.cpu_time_ms is not None else jres.wall_time_ms)
                return (in_file.name, round(ms, 2))
            if is_interactive and interactor_bin:
                answer_data = ans_file.read_text(encoding="utf-8", errors="replace") if ans_file.exists() else ""
                jres = await InteractiveRunner.run(
                    interactor_exe=interactor_bin,
                    solution_exe=sol_exe,
                    input_data=input_data,
                    answer_data=answer_data,
                    time_limit_ms=test_timeout * 1000.0,
                    memory_limit_mb=mem_mb,
                    cwd=None if use_system_temp else problem_dir,
                )
                if not jres.accepted:
                    return None
                ms = float(jres.cpu_time_ms if jres.cpu_time_ms is not None else jres.wall_time_ms)
                return (in_file.name, round(ms, 2))
            run = await run_binary(sol_exe, input_data, timeout=test_timeout, memory_mb=mem_mb)
            if not run.success or run.timed_out:
                return None
            return (in_file.name, round(float(run.time_ms), 2))

        measured = await run_batch(in_files, _time_one, limit=_VERIFY_CONCURRENCY)
        return {name: ms for item in measured if item is not None for name, ms in [item]}

    async def _check_wrong_solution_kill(
        self,
        problem_dir: str,
        tests_dir: str,
        wrong_solution_names: list[str],
        answer_ext: str,
        timeout: int,
        *,
        verify_with_checker: bool = False,
        manifest: AutoCodeManifest | None = None,
        use_system_temp: bool = False,
        std_timings: dict[str, float] | None = None,
        tle_kill_factor: float | None = None,
        slowdown_kill_ratio: float | None = None,
        sol_name: str = "sol",
        undeclared_wrong_solutions: list[str] | None = None,
        checker_exe_override: str | None = None,
        interactor_exe_override: str | None = None,
        wrong_exe_overrides: dict[str, str] | None = None,
    ) -> dict:
        policy_err = _validate_tle_policy_params(tle_kill_factor, slowdown_kill_ratio)
        if policy_err is not None:
            return {"passed": False, "validated": False, "error": policy_err, "message": policy_err}
        if not wrong_solution_names:
            if manifest is not None and manifest.quality_gates.require_wrong_solution_kill:
                return {
                    "passed": False,
                    "validated": True,
                    "skipped": False,
                    "missing_wrong_solutions": True,
                    "details": [],
                    "message": (
                        "No wrong solutions configured in manifest.json or discovered under "
                        "solutions/wrong_*.cpp while quality_gates.require_wrong_solution_kill is enabled"
                    ),
                }
            return {
                "passed": True,
                "validated": False,
                "skipped": True,
                "details": [],
                "message": "No wrong solutions configured (require_wrong_solution_kill is disabled)",
            }

        exe_ext = get_exe_extension()
        is_interactive = manifest_is_interactive(manifest)
        is_communication = manifest is not None and manifest_is_communication(manifest)
        comm_mode = (
            (manifest.protocol.mode if manifest.protocol else "run_twice")
            if (is_communication and manifest is not None)
            else ""
        )
        interactor_bin = ""
        if is_interactive:
            if interactor_exe_override and os.path.isfile(interactor_exe_override):
                interactor_bin = interactor_exe_override
            else:
                interactor_bin = os.path.join(problem_dir, "files", f"interactor{exe_ext}")
                if not os.path.isfile(interactor_bin):
                    interactor_bin = os.path.join(problem_dir, f"interactor{exe_ext}")
            if not os.path.isfile(interactor_bin):
                return {
                    "passed": False,
                    "validated": True,
                    "details": [],
                    "message": f"interactive problem requires files/interactor{exe_ext}",
                    "mode": "interactive",
                }
        elif is_communication and manifest is not None and comm_mode != "grader_linked":
            if interactor_exe_override and os.path.isfile(interactor_exe_override):
                interactor_bin = interactor_exe_override
            else:
                interactor_bin = resolve_communicator_binary(problem_dir, manifest, exe_ext) or ""
            if not interactor_bin:
                return {
                    "passed": False,
                    "validated": True,
                    "details": [],
                    "message": f"communication problem ({comm_mode}) requires compiled communicator binary",
                    "mode": f"communication:{comm_mode}",
                }

        checker_bin = (
            checker_exe_override
            if (checker_exe_override and os.path.isfile(checker_exe_override))
            else (
                checker_exe_path(problem_dir, exe_ext)
                if (not is_interactive and not is_communication and verify_with_checker)
                else ""
            )
        )
        if not is_interactive and not is_communication and verify_with_checker and not os.path.isfile(checker_bin):
            return {
                "passed": False,
                "validated": True,
                "details": [],
                "message": f"special_judge+stress_comparison=checker requires files/checker{exe_ext}",
            }

        tests_path = Path(tests_dir)
        in_files = sorted(
            (p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in"),
            key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)],
        )
        details = []
        all_killed = True

        has_manifest_tl = bool(manifest and getattr(manifest, "time_limit_ms", None))
        tl_ms = float(manifest.time_limit_ms) if (manifest and has_manifest_tl) else min(5000.0, float(timeout) * 1000.0)
        effective_tle_factor = (
            float(tle_kill_factor)
            if tle_kill_factor is not None
            else (float(manifest.quality_gates.wrong_tle_kill_factor) if manifest is not None else 1.0)
        )
        effective_slowdown_ratio = (
            float(slowdown_kill_ratio)
            if slowdown_kill_ratio is not None
            else (manifest.quality_gates.wrong_slowdown_kill_ratio if manifest is not None else None)
        )
        tle_threshold_ms = max(100.0, tl_ms * effective_tle_factor)
        if has_manifest_tl or tle_kill_factor is not None:
            hard_timeout_sec = max(
                0.1,
                tle_threshold_ms / 1000.0 + 0.5,
                (tl_ms / 1000.0) * 1.5,
            )
        else:
            hard_timeout_sec = min(5.0, float(timeout))
        mem_mb = (
            float(manifest.memory_limit_mb)
            if (manifest and getattr(manifest, "memory_limit_mb", None))
            else 256.0
        )

        std_timings_map: dict[str, float] = dict(std_timings) if std_timings else {}
        missing_std_files = [p for p in in_files if p.name not in std_timings_map]
        if missing_std_files:
            measured_std = await self._measure_std_timings(
                problem_dir,
                missing_std_files,
                answer_ext,
                sol_name,
                hard_timeout_sec,
                mem_mb,
                is_interactive=is_interactive,
                is_communication=is_communication,
                interactor_bin=interactor_bin,
                manifest=manifest,
                use_system_temp=use_system_temp,
            )
            std_timings_map.update(measured_std)

        effective_compiler = resolve_effective_compiler(None, manifest)
        wf_snapshot = get_section(problem_dir, WORKFLOW) or {}
        saved_build_plat = (
            wf_snapshot.get("build_platform")
            if isinstance(wf_snapshot, dict) and isinstance(wf_snapshot.get("build_platform"), str)
            else None
        )
        platform_changed = bool(saved_build_plat and saved_build_plat != current_platform())
        manifest_wrong_paths: dict[str, str] = {}
        if manifest is not None:
            for s in manifest.solutions:
                if s.role == "wrong" and s.path:
                    manifest_wrong_paths[s.name] = s.path

        for wrong_name in wrong_solution_names:
            if not isinstance(wrong_name, str) or not is_safe_stem(wrong_name):
                details.append(
                    {
                        "name": str(wrong_name),
                        "killed": False,
                        "reason": f"invalid wrong solution name (must be a safe file stem): {wrong_name!r}",
                    }
                )
                all_killed = False
                continue

            override_bin = (
                wrong_exe_overrides.get(wrong_name)
                if isinstance(wrong_exe_overrides, dict)
                else None
            )
            if isinstance(override_bin, str) and os.path.isfile(override_bin) and not os.path.islink(override_bin):
                binary_path = Path(override_bin)
            else:
                bin_rel = f"solutions/{wrong_name}{exe_ext}"
                raw_bin = Path(problem_dir) / bin_rel
                if raw_bin.is_symlink():
                    details.append(
                        {
                            "name": wrong_name,
                            "killed": False,
                            "reason": f"refusing symlinked wrong binary: {bin_rel}",
                        }
                    )
                    all_killed = False
                    continue
                resolved_bin = safe_resolve_under_root(problem_dir, bin_rel)
                if resolved_bin is None:
                    details.append(
                        {
                            "name": wrong_name,
                            "killed": False,
                            "reason": f"wrong binary path escapes problem_dir: {bin_rel}",
                        }
                    )
                    all_killed = False
                    continue
                binary_path = Path(resolved_bin)

            src_rel = manifest_wrong_paths.get(wrong_name, f"solutions/{wrong_name}.cpp")
            resolved_src = safe_resolve_under_root(problem_dir, src_rel)
            src_path = (
                Path(resolved_src)
                if (resolved_src is not None and not Path(resolved_src).is_symlink())
                else None
            )

            grader_src: Path | None = None
            grader_deps: list[Path] = []
            if is_communication and manifest is not None and comm_mode == "grader_linked":
                grader_rel = resolve_grader_source_existing(problem_dir, manifest)
                if grader_rel:
                    g_res = safe_resolve_under_root(problem_dir, grader_rel)
                    if g_res and Path(g_res).is_file():
                        grader_src = Path(g_res)
                        grader_deps.append(grader_src)
                hdr_rel = (
                    manifest.protocol.header_file
                    if (manifest.protocol and manifest.protocol.header_file)
                    else "files/problem.h"
                )
                if hdr_rel:
                    h_res = safe_resolve_under_root(problem_dir, hdr_rel)
                    if h_res and Path(h_res).is_file():
                        grader_deps.append(Path(h_res))

            auto_compiled = False
            needs_compile = False
            if not use_system_temp and src_path is not None and src_path.is_file():
                if not binary_path.exists() or platform_changed:
                    needs_compile = True
                else:
                    try:
                        bin_mtime = binary_path.stat().st_mtime
                        if src_path.stat().st_mtime > bin_mtime + 1e-6:
                            needs_compile = True
                        elif any(dep.is_file() and dep.stat().st_mtime > bin_mtime + 1e-6 for dep in grader_deps):
                            needs_compile = True
                    except OSError:
                        pass
            if needs_compile and src_path is not None:
                include_dirs = [TEMPLATES_DIR]
                extra_sources: list[str] | None = None
                if is_communication and manifest is not None and comm_mode == "grader_linked" and grader_src is not None:
                    extra_sources = [str(grader_src)]
                    files_dir = os.path.join(problem_dir, "files")
                    if os.path.isdir(files_dir):
                        include_dirs.append(files_dir)
                comp_res = await compile_cpp(
                    str(src_path),
                    str(binary_path),
                    include_dirs=include_dirs,
                    extra_sources=extra_sources,
                    compiler=effective_compiler,
                )
                if not comp_res.success:
                    details.append(
                        {
                            "name": wrong_name,
                            "killed": False,
                            "auto_compiled": False,
                            "reason": "auto-compile failed",
                            "compile_error": (comp_res.stderr or "")[:500],
                        }
                    )
                    all_killed = False
                    continue
                auto_compiled = True

            if not binary_path.exists():
                details.append({"name": wrong_name, "killed": False, "reason": "binary not found"})
                all_killed = False
                continue

            exp = self._wrong_expected(wrong_name, manifest)
            # expected=pass 解法按标准 TL（不低于 tle_threshold_ms）判定，避免 wrong_tle_kill_factor<1 或相对倍率误杀
            run_tle_threshold_ms = tle_threshold_ms if exp == "fail" else max(tl_ms, tle_threshold_ms)
            # 与 checker / interactor / communicator 一致：每测 "AC" / 非 AC；expected=fail 须至少一测非 AC；expected=pass 须全部 AC

            async def _run_wrong_check(
                in_file: Path,
                _binary_path: Path = binary_path,
                _exp: str = exp,
                _run_tle_ms: float = run_tle_threshold_ms,
            ) -> dict[str, Any]:
                ans_file = in_file.with_suffix(answer_ext)
                tle_reason: str | None = None
                fail_reason_detail: str | None = None
                measured_mem_mb: float | None = None
                if is_communication and manifest is not None:
                    input_data = in_file.read_text(encoding="utf-8")
                    answer_data = ans_file.read_text(encoding="utf-8") if ans_file.exists() else ""
                    jres = await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest,
                        solution_exe=str(_binary_path),
                        input_data=input_data,
                        answer_data=answer_data,
                        time_limit_ms=hard_timeout_sec * 1000.0,
                        memory_limit_mb=mem_mb,
                        use_system_temp=use_system_temp,
                        communicator_exe_override=interactor_bin or None,
                    )
                    verdict = jres.verdict
                    measured_ms = float(
                        jres.cpu_time_ms if jres.cpu_time_ms is not None else jres.wall_time_ms
                    )
                    measured_mem_mb = jres.memory_mb
                    if verdict == "FAIL":
                        fail_reason_detail = (jres.detail or jres.judge_stderr[:200]) or None
                elif is_interactive:
                    input_data = in_file.read_text(encoding="utf-8")
                    answer_data = ans_file.read_text(encoding="utf-8") if ans_file.exists() else ""
                    jres = await InteractiveRunner.run(
                        interactor_exe=interactor_bin,
                        solution_exe=str(_binary_path),
                        input_data=input_data,
                        answer_data=answer_data,
                        time_limit_ms=hard_timeout_sec * 1000.0,
                        memory_limit_mb=mem_mb,
                        cwd=None if use_system_temp else problem_dir,
                    )
                    verdict = jres.verdict
                    measured_ms = float(
                        jres.cpu_time_ms if jres.cpu_time_ms is not None else jres.wall_time_ms
                    )
                    measured_mem_mb = jres.memory_mb
                    if verdict == "FAIL":
                        fail_reason_detail = (jres.detail or jres.judge_stderr[:200]) or None
                else:
                    if not ans_file.exists():
                        return {"file": in_file.name, "verdict": "SKIP", "time_ms": 0.0}
                    input_data = in_file.read_text(encoding="utf-8")
                    run = await run_binary(
                        str(_binary_path),
                        input_data,
                        timeout=hard_timeout_sec,
                        memory_mb=mem_mb,
                    )
                    measured_ms = float(run.time_ms)
                    measured_mem_mb = getattr(run, "memory_mb", None)
                    if run.timed_out:
                        verdict = "TLE"
                        tle_reason = "hard_timeout"
                    elif run.memory_limit_exceeded:
                        verdict = "MLE"
                    elif (run.error and run.return_code == -1) or (
                        run.error and "Exec format error" in run.error
                    ):
                        verdict = "FAIL"
                        fail_reason_detail = run.error
                    elif not run.success:
                        verdict = "RE"
                    elif measured_ms > _run_tle_ms:
                        verdict = "TLE"
                        tle_reason = "exceeded_tle_threshold"
                    elif verify_with_checker:
                        in_path = str(in_file)
                        ans_path = str(ans_file)
                        tmp_parent = None if use_system_temp else problem_dir
                        with tempfile.TemporaryDirectory(dir=tmp_parent) as tmp:
                            out_path = os.path.join(tmp, "output.txt")
                            with open(out_path, "w", encoding="utf-8", newline="\n") as out_f:
                                out_f.write(run.stdout)
                            verdict, chk_run = await run_testlib_checker(
                                checker_bin,
                                in_path,
                                out_path,
                                ans_path,
                                timeout=timeout,
                            )
                        if verdict == "FAIL":
                            fail_reason_detail = (chk_run.error or chk_run.stderr[:200]) or None
                    else:
                        expected = ans_file.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
                        actual = run.stdout.replace("\r\n", "\n").strip()
                        verdict = "AC" if actual == expected else "WA"

                # Logical TLE promotion for interactive/communication runs exceeding _run_tle_ms
                if verdict == "AC" and measured_ms > _run_tle_ms:
                    verdict = "TLE"
                    tle_reason = "exceeded_tle_threshold"

                std_ms = std_timings_map.get(in_file.name)
                ratio_to_std = (
                    round(measured_ms / max(std_ms, 1.0), 2) if std_ms is not None else None
                )
                kill_ratio_to_std = (
                    measured_ms / max(std_ms, 5.0) if std_ms is not None else None
                )
                if (
                    _exp == "fail"
                    and verdict == "AC"
                    and effective_slowdown_ratio is not None
                    and kill_ratio_to_std is not None
                    and measured_ms >= 50.0
                    and kill_ratio_to_std >= effective_slowdown_ratio
                ):
                    verdict = "TLE"
                    tle_reason = "relative_slowdown"

                out_item: dict[str, Any] = {
                    "file": in_file.name,
                    "verdict": verdict,
                    "time_ms": round(measured_ms, 2),
                }
                if isinstance(measured_mem_mb, int | float) and not isinstance(measured_mem_mb, bool):
                    out_item["memory_mb"] = round(float(measured_mem_mb), 2)
                if std_ms is not None:
                    out_item["std_time_ms"] = round(std_ms, 2)
                    out_item["ratio_to_std"] = ratio_to_std
                if tle_reason is not None:
                    out_item["tle_reason"] = tle_reason
                if fail_reason_detail is not None:
                    out_item["fail_detail"] = fail_reason_detail
                return out_item

            per_test: list[str] = []
            per_test_timings: list[dict[str, Any]] = []
            killing_verdict: str | None = None
            killing_test: str | None = None
            killing_tle_reason: str | None = None
            judge_failed = False
            fail_detail: str | None = None
            for start in range(0, len(in_files), _VERIFY_CONCURRENCY):
                chunk = in_files[start : start + _VERIFY_CONCURRENCY]
                chunk_results = await run_batch(
                    chunk, _run_wrong_check, limit=_VERIFY_CONCURRENCY
                )
                for item in chunk_results:
                    v = str(item.get("verdict", "SKIP"))
                    detail = item.get("fail_detail")
                    if v == "SKIP":
                        continue
                    per_test.append(v)
                    per_test_timings.append(item)
                    if v == "FAIL":
                        judge_failed = True
                        if detail and fail_detail is None:
                            fail_detail = str(detail)
                    elif v != "AC" and killing_verdict is None:
                        killing_verdict = v
                        killing_test = str(item.get("file", ""))
                        if item.get("tle_reason"):
                            killing_tle_reason = str(item["tle_reason"])
                if judge_failed:
                    break
                if exp == "fail" and killing_verdict is not None:
                    break

            if judge_failed:
                killed = False
            elif exp == "fail":
                killed = killing_verdict is not None
            elif not per_test:
                killed = False
            else:
                killed = all(v == "AC" for v in per_test)

            wrong_times = [float(item["time_ms"]) for item in per_test_timings]
            wrong_mems = [
                float(item["memory_mb"])
                for item in per_test_timings
                if isinstance(item.get("memory_mb"), int | float)
            ]
            max_memory_mb = round(max(wrong_mems), 2) if wrong_mems else None
            std_matched = [
                float(item["std_time_ms"])
                for item in per_test_timings
                if isinstance(item.get("std_time_ms"), int | float)
            ]
            ratios_meaningful = [
                float(item["ratio_to_std"])
                for item in per_test_timings
                if isinstance(item.get("ratio_to_std"), int | float)
                and float(item["time_ms"]) >= 5.0
            ]
            ratios_all = [
                float(item["ratio_to_std"])
                for item in per_test_timings
                if isinstance(item.get("ratio_to_std"), int | float)
            ]
            max_time_ms = round(max(wrong_times), 2) if wrong_times else 0.0
            total_time_ms = round(sum(wrong_times), 2) if wrong_times else 0.0
            std_max_time_ms = round(max(std_matched), 2) if std_matched else None
            std_total_time_ms = round(sum(std_matched), 2) if std_matched else None
            max_ratio_to_std = (
                round(max(ratios_meaningful), 2)
                if ratios_meaningful
                else (round(max(ratios_all), 2) if ratios_all else None)
            )
            total_ratio_to_std = (
                round(total_time_ms / max(std_total_time_ms, 1.0), 2)
                if std_total_time_ms is not None
                else None
            )
            suspect_slow_tests = [
                item
                for item in per_test_timings
                if float(item["time_ms"]) >= 0.5 * tl_ms
                or (
                    isinstance(item.get("ratio_to_std"), int | float)
                    and float(item["ratio_to_std"]) >= 3.0
                    and float(item["time_ms"]) >= 50.0
                )
            ]

            entry: dict[str, Any] = {
                "name": wrong_name,
                "killed": killed,
                "expected": exp,
                "auto_compiled": auto_compiled,
                "max_time_ms": max_time_ms,
                "total_time_ms": total_time_ms,
                "max_memory_mb": max_memory_mb,
                "std_max_time_ms": std_max_time_ms,
                "max_ratio_to_std": max_ratio_to_std,
                "total_ratio_to_std": total_ratio_to_std,
                "tle_threshold_ms": round(tle_threshold_ms, 1),
                "per_test_timings": per_test_timings,
                "hint": (
                    "expected=fail：须至少一测非 AC（interactor/checker）或与 .ans 不一致（exact）；"
                    "expected=pass：须全部 AC 或与 .ans 一致"
                ),
            }
            if suspect_slow_tests:
                entry["suspect_slow_tests"] = suspect_slow_tests
            if exp == "fail" and not killed and (suspect_slow_tests or max_time_ms >= 0.25 * tl_ms):
                ratio_desc = (
                    f", max_ratio_to_std={max_ratio_to_std}x" if max_ratio_to_std is not None else ""
                )
                entry["host_speed_warning"] = (
                    f"Wrong solution '{wrong_name}' survived locally (max_time_ms={max_time_ms}ms "
                    f"vs tle_threshold_ms={round(tle_threshold_ms, 1)}ms{ratio_desc}). "
                    "A fast local CPU (e.g. Apple Silicon) can mask TLE on typical OJ hardware; "
                    "consider strengthening type=4 cases, tightening time_limit_ms, or configuring "
                    "quality_gates.wrong_tle_kill_factor / wrong_slowdown_kill_ratio."
                )
            if judge_failed:
                entry["judge_failed"] = True
                entry["reason"] = (
                    f"judge returned FAIL: {fail_detail}"
                    if fail_detail
                    else "judge returned FAIL (checker/interactor/execution error)"
                )
            elif killing_verdict is not None:
                entry["killing_verdict"] = killing_verdict
                if killing_test:
                    entry["killing_test"] = killing_test
                if killing_tle_reason:
                    entry["killing_tle_reason"] = killing_tle_reason
            details.append(entry)
            if not killed:
                all_killed = False

        mode_extra = (
            {"mode": f"communication:{comm_mode}"}
            if is_communication
            else (
                {"mode": "interactive"}
                if is_interactive
                else ({"mode": "checker"} if verify_with_checker else {})
            )
        )
        return {
            "passed": all_killed,
            "validated": True,
            "details": details,
            **(
                {"undeclared_wrong_solutions": list(undeclared_wrong_solutions)}
                if undeclared_wrong_solutions
                else {}
            ),
            "tle_policy": {
                "time_limit_ms": tl_ms,
                "wrong_tle_kill_factor": effective_tle_factor,
                "tle_threshold_ms": round(tle_threshold_ms, 1),
                "hard_timeout_ms": round(hard_timeout_sec * 1000.0, 1),
                "wrong_slowdown_kill_ratio": effective_slowdown_ratio,
            },
            "message": "All wrong solutions were killed" if all_killed else "Some wrong solutions survived all tests",
            **mode_extra,
        }
