"""
Test Verification 工具 - 验证生成的测试数据。

检查文件完整性、答案一致性、约束覆盖等。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..runtime_store import TEST_MANIFEST, WORKFLOW, get_section, set_section
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
    resolve_communicator_binary,
    resolve_participant_binaries,
)
from ..utils.compiler import run_batch, run_binary, run_binary_with_args
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..workflow import (
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from ..workflow.models import AutoCodeManifest
from .base import Tool, ToolResult, input_schema_from_model
from .schemas import ProblemVerifyTestsInput

_LIMIT_STRATEGY_TYPES = frozenset({"3", "4"})
_VERIFY_CONCURRENCY = 4


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
        3. validator: 用 val 检查每个 .in 是否满足约束（如有 val.exe）
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
    ) -> ToolResult:
        """执行测试数据验证。"""
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

        # 默认执行所有验证
        if not verify_types:
            verify_types = ["file_count", "answer_consistency", "validator", "no_empty"]

        if enable_limit_ratio:
            if "limit_ratio" not in verify_types:
                verify_types.append("limit_ratio")
            if "limit_semantics" not in verify_types:
                verify_types.append("limit_semantics")
        else:
            verify_types = [v for v in verify_types if v not in {"limit_ratio", "limit_semantics"}]

        results = {}
        all_passed = True

        # 1. 文件完整性检查
        if "file_count" in verify_types:
            result = self._check_file_count(tests_dir, resolved_answer_ext)
            results["file_count"] = result
            if not result["passed"]:
                all_passed = False

        # 2. 空文件检查
        if "no_empty" in verify_types:
            result = self._check_no_empty(tests_dir)
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
            wrong_names = list(wrong_solution_names or [])
            if manifest_model:
                for s in manifest_model.solutions:
                    if s.role == "wrong" and s.name not in wrong_names:
                        wrong_names.append(s.name)
            result = await self._check_wrong_solution_kill(
                problem_dir=problem_dir,
                tests_dir=tests_dir,
                wrong_solution_names=wrong_names,
                answer_ext=resolved_answer_ext,
                timeout=timeout,
                verify_with_checker=verify_with_checker,
                manifest=manifest_model,
            )
            results["wrong_solution_kill"] = result
            if not result["passed"]:
                all_passed = False

        if "duplicate_inputs" in verify_types:
            result = self._check_duplicate_inputs(tests_dir)
            results["duplicate_inputs"] = result
            if not result["passed"]:
                all_passed = False

        if "scale_distribution" in verify_types:
            result = self._check_scale_distribution(tests_dir)
            results["scale_distribution"] = result
            if not result["passed"]:
                all_passed = False

        if "purpose_coverage" in verify_types:
            result = self._check_purpose_coverage(tests_dir)
            results["purpose_coverage"] = result
            if not result["passed"]:
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
        quality_signals = self._build_quality_signals(verify_types, results)

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
            )
            return ToolResult.ok(
                passed=True,
                results=results,
                quality_signals=quality_signals,
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
            )
            return ToolResult.fail(
                f"{passed_checks}/{total_checks} checks passed",
                passed=False,
                results=results,
                quality_signals=quality_signals,
                total_checks=total_checks,
                passed_checks=passed_checks,
                skipped_checks=skipped_checks,
                effective_checks=effective_checks,
                tests_dir=tests_dir,
                sol_name=effective_sol_name,
                limit_ratio_enabled=enable_limit_ratio,
            )

    def _build_quality_signals(self, verify_types: list[str], results: dict) -> dict[str, dict]:
        signal_map = {
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
            "validator_self_test": "validator_self_test",
            "checker_self_test": "checker_self_test",
            "interactor_self_test": "interactor_self_test",
        }
        verify_set = set(verify_types)
        signals: dict[str, dict] = {}
        for verify_name, signal_name in signal_map.items():
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
        return signals

    def _write_workflow_state(
        self,
        *,
        problem_dir: str,
        success: bool,
        passed: bool,
        quality_signals: dict[str, dict],
        results: dict,
    ) -> None:
        state = get_section(problem_dir, WORKFLOW) or {}
        if not isinstance(state, dict):
            state = {}

        limit_ratio = results.get("limit_ratio", {})
        if isinstance(limit_ratio, dict) and "limit_case_ratio" in limit_ratio:
            state["limit_case_ratio"] = limit_ratio.get("limit_case_ratio")
        else:
            state["limit_case_ratio"] = None

        state["verify_signals"] = quality_signals
        state["tests_verified"] = bool(success and passed)
        history = state.get("history", [])
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "tool": "problem_verify_tests",
                "success": state["tests_verified"],
                "at": datetime.now(timezone.utc).isoformat(),
                "key_metrics": {
                    "tests_verified": state["tests_verified"],
                    "limit_case_ratio": state.get("limit_case_ratio"),
                },
            }
        )
        state["history"] = history[-50:]
        set_section(problem_dir, WORKFLOW, state)

    def _check_file_count(self, tests_dir: str, answer_ext: str) -> dict:
        """检查文件完整性：每个 .in 有对应的 answer_ext。"""
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

        non_numeric = [f for f in in_files if not Path(f).stem.isdigit()]
        numeric_indices = sorted(int(Path(f).stem) for f in in_files if Path(f).stem.isdigit())
        numeric_index_set = set(numeric_indices)
        expected_indices = list(range(1, max(numeric_indices) + 1)) if numeric_indices else []
        missing_indices = [
            idx for idx in expected_indices if idx not in numeric_index_set
        ]
        duplicate_indices = sorted(
            idx for idx in numeric_index_set if numeric_indices.count(idx) > 1
        )

        passed = (
            not missing_ans
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
        }

    def _check_no_empty(self, tests_dir: str) -> dict:
        """检查没有空文件。"""
        empty_files = []
        for f in os.listdir(tests_dir):
            filepath = os.path.join(tests_dir, f)
            if os.path.isfile(filepath) and os.path.getsize(filepath) == 0:
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
            f
            for f in os.listdir(tests_dir)
            if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
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
            comm_bin = resolve_communicator_binary(problem_dir, manifest, exe_ext)
            if not comm_bin:
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
            checker_bin = checker_exe_path(problem_dir, exe_ext)
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
                return ("metrics", jres.metrics)

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
                return None
            if result.stdout.replace("\r\n", "\n").strip() != expected.replace("\r\n", "\n").strip():
                return (
                    "mismatch",
                    {
                        "file": in_file,
                        "expected": expected[:200],
                        "actual": result.stdout[:200],
                    },
                )
            return None

        outcomes = await run_batch(in_files, _check_one, limit=_VERIFY_CONCURRENCY)
        protocol_metrics: dict[str, int] = {}
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
            elif kind == "metrics" and isinstance(payload, dict):
                for mkey in ("queries", "bits_used", "bytes_used", "rounds"):
                    val = payload.get(mkey)
                    if isinstance(val, int) and not isinstance(val, bool):
                        agg_key = f"max_{mkey}"
                        protocol_metrics[agg_key] = max(protocol_metrics.get(agg_key, 0), val)

        passed = not mismatches and not timed_out and not errors
        out: dict = {
            "passed": passed,
            "total": len(in_files),
            "mismatches": mismatches,
            "timed_out": timed_out,
            "errors": errors,
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
        self, problem_dir: str, tests_dir: str, timeout: int
    ) -> dict:
        """用 val 检查每个 .in 是否满足约束。"""
        exe_ext = get_exe_extension()
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")

        if not os.path.exists(val_exe):
            return {
                "passed": True,
                "total": 0,
                "skipped": True,
                "message": "val.exe not found, validator check skipped",
            }

        in_files = sorted(
            f
            for f in os.listdir(tests_dir)
            if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
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
            f
            for f in os.listdir(tests_dir)
            if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
        )
        in_file_set = set(in_files)
        type_by_in_file: dict[str, str] = {}
        for item in tests:
            if not isinstance(item, dict):
                continue
            in_file = item.get("in_file")
            type_param = item.get("type_param")
            if isinstance(in_file, str) and isinstance(type_param, str):
                type_by_in_file[in_file] = type_param

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
            1 for in_file in in_file_set if type_by_in_file[in_file] in _LIMIT_STRATEGY_TYPES
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

    def _check_duplicate_inputs(self, tests_dir: str) -> dict:
        tests_path = Path(tests_dir)
        in_files = sorted(p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in")
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
        return {
            "passed": ratio <= 0.2,
            "total": total,
            "duplicate_file_count": duplicate_file_count,
            "near_duplicate_file_count": near_duplicate_file_count,
            "duplicate_ratio": ratio,
            "exact_duplicates": exact_duplicates,
            "near_duplicates": near_duplicates[:20],
            "threshold": 0.2,
        }

    def _check_scale_distribution(self, tests_dir: str) -> dict:
        tests_path = Path(tests_dir)
        in_files = sorted(p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in")
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

    def _check_purpose_coverage(self, tests_dir: str) -> dict:
        manifest = self._read_tests_manifest(tests_dir)
        if not manifest:
            return {"passed": False, "error": "tests manifest missing or unreadable"}
        tests = manifest.get("tests", [])
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
    ) -> dict:
        if not wrong_solution_names:
            return {"passed": True, "validated": False, "message": "No wrong solutions configured"}

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
            comm_bin = resolve_communicator_binary(problem_dir, manifest, exe_ext)
            if not comm_bin:
                return {
                    "passed": False,
                    "validated": True,
                    "details": [],
                    "message": f"communication problem ({comm_mode}) requires compiled communicator binary",
                    "mode": f"communication:{comm_mode}",
                }

        checker_bin = (
            checker_exe_path(problem_dir, exe_ext)
            if (not is_interactive and not is_communication and verify_with_checker)
            else ""
        )
        if not is_interactive and not is_communication and verify_with_checker and not os.path.isfile(checker_bin):
            return {
                "passed": False,
                "validated": True,
                "details": [],
                "message": f"special_judge+stress_comparison=checker requires files/checker{exe_ext}",
            }

        tests_path = Path(tests_dir)
        in_files = sorted(p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in")
        details = []
        all_killed = True
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

        for wrong_name in wrong_solution_names:
            binary_path = Path(problem_dir) / "solutions" / f"{wrong_name}{exe_ext}"
            if not binary_path.exists():
                details.append({"name": wrong_name, "killed": False, "reason": "binary not found"})
                all_killed = False
                continue

            exp = self._wrong_expected(wrong_name, manifest)
            # 与 checker / interactor / communicator 一致：每测 "AC" / 非 AC；expected=fail 须至少一测非 AC；expected=pass 须全部 AC

            async def _run_wrong_check(
                in_file: Path, _binary_path: Path = binary_path
            ) -> tuple[str, str | None]:
                ans_file = in_file.with_suffix(answer_ext)
                if is_communication and manifest is not None:
                    input_data = in_file.read_text(encoding="utf-8")
                    answer_data = ans_file.read_text(encoding="utf-8") if ans_file.exists() else ""
                    jres = await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest,
                        solution_exe=str(_binary_path),
                        input_data=input_data,
                        answer_data=answer_data,
                        time_limit_ms=test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        use_system_temp=use_system_temp,
                    )
                    detail = (jres.detail or jres.judge_stderr[:200]) if jres.verdict == "FAIL" else None
                    return (jres.verdict, detail or None)
                if is_interactive:
                    input_data = in_file.read_text(encoding="utf-8")
                    answer_data = ans_file.read_text(encoding="utf-8") if ans_file.exists() else ""
                    jres = await InteractiveRunner.run(
                        interactor_exe=interactor_bin,
                        solution_exe=str(_binary_path),
                        input_data=input_data,
                        answer_data=answer_data,
                        time_limit_ms=test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        cwd=None if use_system_temp else problem_dir,
                    )
                    detail = (jres.detail or jres.judge_stderr[:200]) if jres.verdict == "FAIL" else None
                    return (jres.verdict, detail or None)

                if not ans_file.exists():
                    return ("SKIP", None)
                input_data = in_file.read_text(encoding="utf-8")
                run = await run_binary(
                    str(_binary_path),
                    input_data,
                    timeout=test_timeout,
                    memory_mb=mem_mb,
                )
                if run.timed_out:
                    return ("TLE", None)
                if run.memory_limit_exceeded:
                    return ("MLE", None)
                if run.error and run.return_code == -1:
                    return ("FAIL", run.error)
                if not run.success:
                    return ("RE", None)
                if verify_with_checker:
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
                    detail = (
                        (chk_run.error or chk_run.stderr[:200]) if verdict == "FAIL" else None
                    )
                    return (verdict, detail or None)
                expected = ans_file.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
                actual = run.stdout.replace("\r\n", "\n").strip()
                return ("AC" if actual == expected else "WA", None)

            per_test: list[str] = []
            killing_verdict: str | None = None
            judge_failed = False
            fail_detail: str | None = None
            for start in range(0, len(in_files), _VERIFY_CONCURRENCY):
                chunk = in_files[start : start + _VERIFY_CONCURRENCY]
                chunk_verdicts = await run_batch(
                    chunk, _run_wrong_check, limit=_VERIFY_CONCURRENCY
                )
                for v, detail in chunk_verdicts:
                    if v == "SKIP":
                        continue
                    per_test.append(v)
                    if v == "FAIL":
                        judge_failed = True
                        if detail and fail_detail is None:
                            fail_detail = detail
                    elif v != "AC" and killing_verdict is None:
                        killing_verdict = v
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

            entry: dict[str, Any] = {
                "name": wrong_name,
                "killed": killed,
                "expected": exp,
                "hint": (
                    "expected=fail：须至少一测非 AC（interactor/checker）或与 .ans 不一致（exact）；"
                    "expected=pass：须全部 AC 或与 .ans 一致"
                ),
            }
            if judge_failed:
                entry["judge_failed"] = True
                entry["reason"] = (
                    f"judge returned FAIL: {fail_detail}"
                    if fail_detail
                    else "judge returned FAIL (checker/interactor/execution error)"
                )
            elif killing_verdict is not None:
                entry["killing_verdict"] = killing_verdict
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
            "message": "All wrong solutions were killed" if all_killed else "Some wrong solutions survived all tests",
            **mode_extra,
        }
