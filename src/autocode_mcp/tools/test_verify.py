"""Test Verification 工具 - 验证生成的测试数据与质量信号。"""

from __future__ import annotations

import os

from pydantic import ValidationError

from ..runtime_store import WORKFLOW, get_section
from ..utils.checker_judge import run_testlib_checker as run_testlib_checker
from ..utils.compiler import compile_cpp as compile_cpp
from ..utils.compiler import run_binary as run_binary
from ..utils.compiler import run_binary_with_args as run_binary_with_args
from ..utils.coverage import run_solution_coverage
from ..utils.platform import get_exe_extension as get_exe_extension
from ..workflow import (
    AutoCodeManifest,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_protocol_judge,
    manifest_uses_testlib_checker,
    record_verify_state,
)
from .base import Tool, ToolResult, input_schema_from_model
from .schemas import ProblemVerifyTestsInput
from .verify_fingerprint import (
    _LIMIT_GROUPS as _LIMIT_GROUPS,
)
from .verify_fingerprint import (
    _LIMIT_STRATEGY_TYPES as _LIMIT_STRATEGY_TYPES,
)
from .verify_fingerprint import (
    _SIGNAL_MAP as _SIGNAL_MAP,
)
from .verify_fingerprint import (
    _VERIFY_CONCURRENCY as _VERIFY_CONCURRENCY,
)
from .verify_fingerprint import (
    _compute_dataset_fingerprint as _compute_dataset_fingerprint,
)
from .verify_fingerprint import (
    _discover_wrong_solution_names as _discover_wrong_solution_names,
)
from .verify_fingerprint import (
    _timing_cache_fingerprint as _timing_cache_fingerprint,
)
from .verify_fingerprint import (
    _validate_tle_policy_params as _validate_tle_policy_params,
)
from .verify_live_checks import _VerifyLiveChecksMixin
from .verify_static_checks import _VerifyStaticChecksMixin
from .verify_wrong_kill import _VerifyWrongKillMixin


class ProblemVerifyTestsTool(Tool, _VerifyStaticChecksMixin, _VerifyLiveChecksMixin, _VerifyWrongKillMixin):
    """验证最终测试集与题目验证器/判题器/错解信号。"""

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
                if explicit_all or (
                    manifest_model is not None
                    and manifest_model.audit_gates.min_branch_coverage is not None
                ):
                    if "coverage" not in verify_types:
                        verify_types.append("coverage")
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

        if "coverage" in verify_types:
            result = await self._check_coverage(
                problem_dir=problem_dir,
                tests_dir=tests_dir,
                sol_name=effective_sol_name,
                answer_ext=resolved_answer_ext,
                timeout=timeout,
                manifest=manifest_model,
            )
            results["coverage"] = result
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
            {_SIGNAL_MAP[v] for v in verify_types if v in _SIGNAL_MAP}
            | ({"coverage"} if "coverage" in verify_types else set())
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

    async def _check_coverage(
        self,
        problem_dir: str,
        tests_dir: str,
        sol_name: str,
        answer_ext: str,
        timeout: int,
        *,
        manifest: AutoCodeManifest | None = None,
    ) -> dict:
        return await run_solution_coverage(
            problem_dir=problem_dir,
            tests_dir=tests_dir,
            sol_name=sol_name,
            answer_ext=answer_ext,
            timeout=timeout,
            manifest=manifest,
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

        if "coverage" in verify_set:
            cov_res = results.get("coverage")
            cov_dict = cov_res if isinstance(cov_res, dict) else {}
            cov_exec = isinstance(cov_res, dict) and not bool(cov_dict.get("skipped", False))
            signals["coverage"] = {
                "executed": cov_exec,
                "passed": bool(cov_dict.get("passed")) if cov_exec else False,
                "evidence": cov_dict if cov_exec else {},
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



__all__ = ["ProblemVerifyTestsTool"]
