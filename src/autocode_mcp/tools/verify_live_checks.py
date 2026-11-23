"""Live execution verification checks mixin for ``ProblemVerifyTestsTool``."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

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
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
)
from .verify_fingerprint import (
    _VERIFY_CONCURRENCY,
    _timing_cache_fingerprint,
    _tv_facade_attr,
    _VerifyHelpersMixin,
)


class _VerifyLiveChecksMixin(_VerifyHelpersMixin):
    """Answer consistency, validator run, and validator/checker/interactor self-tests."""

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
        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
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

            result = await _tv_facade_attr('run_binary', run_binary)(
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
                    verdict, chk_run = await _tv_facade_attr('run_testlib_checker', run_testlib_checker)(
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
        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
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

            result = await _tv_facade_attr('run_binary', run_binary)(val_exe, input_data, timeout=timeout)

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

    async def _check_validator_self_test(self, problem_dir: str, tests_dir: str, timeout: int) -> dict:
        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
        val_exe = os.path.join(problem_dir, "files", f"val{exe_ext}")
        if not os.path.exists(val_exe):
            return {
                "passed": False,
                "skipped": True,
                "message": f"files/val{exe_ext} not found",
            }
        valid_result = await self._check_validator(problem_dir, tests_dir, timeout)
        invalid_fixtures = self._collect_invalid_fixtures(problem_dir, tests_dir)
        rejected: list[str] = []
        accepted: list[str] = []
        fixture_errors: list[dict[str, str]] = []
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
            result = await _tv_facade_attr('run_binary', run_binary)(val_exe, input_data, timeout=timeout)
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
        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
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
                verdict, run = await _tv_facade_attr('run_testlib_checker', run_testlib_checker)(
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
        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
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
                    run = await _tv_facade_attr('run_binary_with_args', run_binary_with_args)(
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
