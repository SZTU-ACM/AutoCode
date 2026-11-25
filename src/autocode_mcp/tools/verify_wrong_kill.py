"""Wrong-solution kill verification mixin for ``ProblemVerifyTestsTool``."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .. import TEMPLATES_DIR
from ..runtime_store import WORKFLOW, get_section
from ..utils.checker_judge import checker_exe_path, run_testlib_checker
from ..utils.communication_runner import (
    CommunicationRunner,
    is_safe_stem,
    resolve_communicator_binary,
    resolve_grader_source_existing,
    resolve_participant_binaries,
    safe_resolve_under_root,
)
from ..utils.compiler import compile_cpp, resolve_effective_compiler, run_batch, run_binary
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..workflow import (
    AutoCodeManifest,
    current_platform,
    manifest_is_communication,
    manifest_is_interactive,
)
from .verify_fingerprint import (
    _VERIFY_CONCURRENCY,
    _tv_facade_attr,
    _validate_tle_policy_params,
    _VerifyHelpersMixin,
)


class _VerifyWrongKillMixin(_VerifyHelpersMixin):
    """Standard solution timing baseline and wrong-solution kill / host-bias verification."""

    @staticmethod
    def _wrong_expected_verdict(
        wrong_name: str,
        manifest: AutoCodeManifest | None,
    ) -> str | None:
        if manifest is not None:
            for s in manifest.solutions:
                if s.name == wrong_name and s.role == "wrong":
                    return s.expected_verdict
        return None

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
        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
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
            run = await _tv_facade_attr('run_binary', run_binary)(sol_exe, input_data, timeout=test_timeout, memory_mb=mem_mb)
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

        exe_ext = _tv_facade_attr("get_exe_extension", get_exe_extension)()
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
        all_verdicts_matched = True
        verdict_mismatches: list[dict[str, Any]] = []

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
            exp_verdict = (
                self._wrong_expected_verdict(wrong_name, manifest)
                if isinstance(wrong_name, str)
                else None
            )
            if not isinstance(wrong_name, str) or not is_safe_stem(wrong_name):
                details.append(
                    {
                        "name": str(wrong_name),
                        "killed": False,
                        "expected_verdict": exp_verdict,
                        "expected_verdict_matched": exp_verdict is None,
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
                            "expected_verdict": exp_verdict,
                            "expected_verdict_matched": exp_verdict is None,
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
                            "expected_verdict": exp_verdict,
                            "expected_verdict_matched": exp_verdict is None,
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
                comp_res = await _tv_facade_attr('compile_cpp', compile_cpp)(
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
                            "expected_verdict": exp_verdict,
                            "expected_verdict_matched": exp_verdict is None,
                            "auto_compiled": False,
                            "reason": "auto-compile failed",
                            "compile_error": (comp_res.stderr or "")[:500],
                        }
                    )
                    all_killed = False
                    continue
                auto_compiled = True

            if not binary_path.exists():
                details.append(
                    {
                        "name": wrong_name,
                        "killed": False,
                        "expected_verdict": exp_verdict,
                        "expected_verdict_matched": exp_verdict is None,
                        "reason": "binary not found",
                    }
                )
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
                    run = await _tv_facade_attr('run_binary', run_binary)(
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
                            verdict, chk_run = await _tv_facade_attr('run_testlib_checker', run_testlib_checker)(
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

            if exp_verdict is None:
                expected_verdict_matched = True
            elif judge_failed or not per_test:
                expected_verdict_matched = False
            elif exp_verdict == "AC":
                expected_verdict_matched = all(v == "AC" for v in per_test)
            else:
                expected_verdict_matched = killing_verdict == exp_verdict

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
                "expected_verdict": exp_verdict,
                "expected_verdict_matched": expected_verdict_matched,
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
                    "expected=pass：须全部 AC 或与 .ans 一致；"
                    "若配置 expected_verdict，实际 verdict 必须精确匹配"
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

            if not expected_verdict_matched and not judge_failed:
                actual_v = killing_verdict if killing_verdict is not None else ("AC" if per_test else "NONE")
                loc = f" on {killing_test}" if killing_test else ""
                mismatch_msg = (
                    f"Expected verdict '{exp_verdict}' for wrong solution '{wrong_name}', "
                    f"but observed '{actual_v}'{loc}"
                )
                entry["reason"] = mismatch_msg
                verdict_mismatches.append(
                    {
                        "name": wrong_name,
                        "expected_verdict": exp_verdict,
                        "actual_verdict": actual_v,
                        "test": killing_test,
                        "message": mismatch_msg,
                    }
                )
                all_verdicts_matched = False

            details.append(entry)
            if not killed:
                all_killed = False

        all_passed = all_killed and all_verdicts_matched
        if all_passed:
            summary_msg = "All wrong solutions were killed"
        elif not all_killed and not all_verdicts_matched:
            mismatch_names = ", ".join(m["name"] for m in verdict_mismatches)
            summary_msg = (
                "Some wrong solutions survived all tests and expected_verdict mismatched "
                f"({mismatch_names})"
            )
        elif not all_verdicts_matched:
            mismatch_desc = "; ".join(
                f"{m['name']} (expected {m['expected_verdict']}, got {m['actual_verdict']})"
                for m in verdict_mismatches
            )
            summary_msg = f"Wrong solution expected_verdict mismatch: {mismatch_desc}"
        else:
            summary_msg = "Some wrong solutions survived all tests"

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
            "passed": all_passed,
            "validated": True,
            "details": details,
            **(
                {"undeclared_wrong_solutions": list(undeclared_wrong_solutions)}
                if undeclared_wrong_solutions
                else {}
            ),
            **(
                {"verdict_mismatches": verdict_mismatches}
                if verdict_mismatches
                else {}
            ),
            "tle_policy": {
                "time_limit_ms": tl_ms,
                "wrong_tle_kill_factor": effective_tle_factor,
                "tle_threshold_ms": round(tle_threshold_ms, 1),
                "hard_timeout_ms": round(hard_timeout_sec * 1000.0, 1),
                "wrong_slowdown_kill_ratio": effective_slowdown_ratio,
            },
            "message": summary_msg,
            **mode_extra,
        }
