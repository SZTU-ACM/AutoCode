"""Isolated zero-write standalone live execution checks for ``ProblemAuditTool``."""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from .. import TEMPLATES_DIR
from ..utils.checker_judge import checker_exe_path
from ..utils.communication_runner import (
    is_safe_stem,
    resolve_communicator_binary,
    resolve_communicator_source_existing,
    resolve_grader_source_existing,
    resolve_participant_binaries,
)
from ..utils.compiler import (
    compile_cpp,
    probe_sanitizer_support_async,
    resolve_effective_compiler,
    run_binary,
)
from ..utils.execution_monitor import DynamicExecutionMonitor
from ..utils.mutation import run_mutation_testing
from ..utils.platform import get_exe_extension
from ..utils.problem_layout import discover_test_pairs, stage_polygon_tests_to_dir
from ..utils.ratio_analyzer import EmpiricalRatioAnalyzer
from ..utils.scale_sampler import MultiScaleSampler
from ..workflow import (
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from .audit_signals import _AuditSignalsMixin
from .complexity import (
    estimate_static_complexity,
    extract_claimed_complexity,
    extract_dominant_var_max,
    extract_n_max_from_constraints,
)
from .stress_test import _has_sanitizer_diagnostic
from .test_verify import ProblemVerifyTestsTool


def _natural_sort_key(path: Path) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


class _AuditStandaloneMixin(_AuditSignalsMixin):
    """Standalone ephemeral compilation and judging checks without touching target repos."""

    async def _run_standalone_live_checks(
        self,
        *,
        problem_path: Path,
        manifest: AutoCodeManifest,
        tests_manifest: dict[str, Any],
        mode: Literal["quick", "full"],
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        warnings: list[dict[str, str]],
        run_mutation: bool = False,
        strict: bool = False,
        staged_tests_dir: Path | None = None,
        staged_answer_ext: str | None = None,
    ) -> None:
        """Run read-only live checks in standalone mode without mutating ``problem_dir``."""
        if any(b.get("gate") == "path_containment" for b in blocking):
            return
        raw_tests_dir = self._resolve_tests_dir(problem_path, standalone=True, layout=layout)
        if raw_tests_dir is None:
            return

        # Reject symlinked test files escaping problem_dir before reading or running binaries on them.
        escaped_tests = self._find_escaped_entries(problem_path, raw_tests_dir)
        if escaped_tests:
            blocking.append(
                {
                    "gate": "path_containment",
                    "reason": f"test files escape problem_dir via symlink: {escaped_tests[:10]}",
                }
            )
            return

        exe_ext = get_exe_extension()
        escaped_bins: list[str] = []
        for rel_bin in (
            f"files/val{exe_ext}",
            f"files/checker{exe_ext}",
            f"files/interactor{exe_ext}",
            f"interactor{exe_ext}",
            f"solutions/sol{exe_ext}",
            f"sol{exe_ext}",
        ):
            raw_bin = problem_path / rel_bin
            if raw_bin.is_symlink() and self._safe_file(problem_path, rel_bin) is None:
                escaped_bins.append(rel_bin)
        if escaped_bins:
            blocking.append(
                {
                    "gate": "path_containment",
                    "reason": f"executable symlinks escape problem_dir: {escaped_bins}",
                }
            )
            return

        verifier = ProblemVerifyTestsTool()
        is_interactive = manifest_is_interactive(manifest)
        is_comm_flag = manifest_is_communication(manifest)
        answers_optional = is_interactive or is_comm_flag

        with contextlib.ExitStack() as stage_stack:
            tests_dir = raw_tests_dir
            if staged_tests_dir is not None and staged_tests_dir.is_dir():
                tests_dir = staged_tests_dir
                answer_ext = staged_answer_ext or ".ans"
            else:
                has_in_files = any(p.is_file() and p.suffix == ".in" for p in raw_tests_dir.iterdir())
                if not has_in_files:
                    discovered_pairs = discover_test_pairs(
                        problem_path,
                        tests_dir=raw_tests_dir,
                        allow_missing_answer=True,
                    )
                    if discovered_pairs:
                        tmp_stage = stage_stack.enter_context(
                            tempfile.TemporaryDirectory(
                                prefix="autocode_poly_stage_",
                                ignore_cleanup_errors=True,
                            )
                        )
                        staged_dir = Path(tmp_stage) / "tests"
                        answer_ext = stage_polygon_tests_to_dir(
                            problem_path,
                            discovered_pairs,
                            staged_dir,
                            source_tests_dir=raw_tests_dir,
                        )
                        tests_dir = staged_dir
                    else:
                        answer_ext = verifier._resolve_answer_ext(
                            str(raw_tests_dir), None, tests_manifest or None
                        )
                else:
                    answer_ext = verifier._resolve_answer_ext(
                        str(raw_tests_dir), None, tests_manifest or None
                    )
                    if not (isinstance(tests_manifest, dict) and tests_manifest.get("answer_ext")):
                        has_ans = any(raw_tests_dir.glob("*.ans"))
                        has_out = any(raw_tests_dir.glob("*.out"))
                        has_a = any(raw_tests_dir.glob("*.a"))
                        if has_out and not has_ans:
                            answer_ext = ".out"
                        elif has_a and not has_ans and not has_out:
                            answer_ext = ".a"

            file_count = verifier._check_file_count(
                str(tests_dir),
                answer_ext,
                is_interactive=answers_optional,
                allow_prefixed_names=True,
            )
            quality_signals["file_count"] = self._wrap_verify_signal(file_count)
            if not file_count.get("passed"):
                blocking.append(
                    {
                        "gate": "file_count",
                        "reason": "test input/answer files are missing, unpaired, or non-contiguous",
                    }
                )

            no_empty = verifier._check_no_empty(
                str(tests_dir), is_interactive=answers_optional, answer_ext=answer_ext
            )
            quality_signals["no_empty"] = self._wrap_verify_signal(no_empty)
            if not no_empty.get("passed"):
                blocking.append(
                    {
                        "gate": "no_empty",
                        "reason": f"empty test files detected: {no_empty.get('empty_files', [])}",
                    }
                )

            if mode != "full":
                return

            real_root = str(problem_path.resolve())
            compiler = resolve_effective_compiler(None, manifest)
            # Problem-local include directories come BEFORE bundled TEMPLATES_DIR so custom headers win
            base_includes: list[str] = []
            for inc_rel in ("files", "validator", "checker", "testdata", "data"):
                inc_sub = self._safe_dir(problem_path, inc_rel)
                if inc_sub is not None:
                    base_includes.append(str(inc_sub))
            if manifest.protocol is not None and manifest.protocol.header_file:
                hdr_file = self._safe_file(problem_path, manifest.protocol.header_file)
                if hdr_file is not None and str(hdr_file.parent) not in base_includes:
                    base_includes.append(str(hdr_file.parent))
            base_includes.append(TEMPLATES_DIR)

            with tempfile.TemporaryDirectory(
                prefix="autocode_standalone_", ignore_cleanup_errors=True
            ) as tmp_bin_dir:
                # 1. Validator: prefer fresh temp compilation from source (avoids stale/cross-OS binaries)
                val_exe_path: str | None = None
                val_src = self._find_validator_source(problem_path, standalone=True)
                if val_src is not None:
                    tmp_val = os.path.join(tmp_bin_dir, f"val{exe_ext}")
                    v_comp = await compile_cpp(
                        str(val_src),
                        tmp_val,
                        compiler=compiler,
                        include_dirs=base_includes,
                        use_cache=False,
                    )
                    if v_comp.success:
                        val_exe_path = tmp_val
                    else:
                        blocking.append(
                            {
                                "gate": "validator_check",
                                "reason": f"failed to compile standalone validator {val_src.name}: {v_comp.error or v_comp.stderr[:200]}",
                            }
                        )
                else:
                    val_bin = self._safe_file(problem_path, f"files/val{exe_ext}")
                    if val_bin is not None:
                        val_exe_path = str(val_bin)

                if val_exe_path is not None:
                    val_res = await verifier._check_validator(
                        str(problem_path), str(tests_dir), 60, val_exe_override=val_exe_path
                    )
                    quality_signals["validator_check"] = self._wrap_verify_signal(val_res)
                    if not val_res.get("skipped") and not val_res.get("passed"):
                        blocking.append(
                            {
                                "gate": "validator_check",
                                "reason": "validator rejected one or more test inputs",
                            }
                        )

                is_comm = manifest_is_communication(manifest)
                spj_checker = manifest_uses_testlib_checker(manifest)

                judge_ready = True
                interactor_override: str | None = None
                checker_override: str | None = None
                if is_interactive:
                    int_src = self._find_interactor_source(problem_path, standalone=True)
                    if int_src is not None:
                        tmp_int = os.path.join(tmp_bin_dir, f"interactor{exe_ext}")
                        icomp = await compile_cpp(
                            str(int_src),
                            tmp_int,
                            compiler=compiler,
                            include_dirs=base_includes,
                            use_cache=False,
                        )
                        if icomp.success:
                            interactor_override = tmp_int
                        else:
                            blocking.append(
                                {
                                    "gate": "interactor_compile",
                                    "reason": f"failed to compile standalone interactor: {icomp.error or icomp.stderr[:200]}",
                                }
                            )
                    else:
                        int_bin = self._safe_file(problem_path, f"files/interactor{exe_ext}") or self._safe_file(
                            problem_path, f"interactor{exe_ext}"
                        )
                        if int_bin is not None:
                            interactor_override = str(int_bin)
                    judge_ready = interactor_override is not None
                elif is_comm:
                    comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
                    if comm_mode != "grader_linked":
                        comm_rel = resolve_communicator_source_existing(str(problem_path), manifest)
                        comm_src = self._safe_file(problem_path, comm_rel) if comm_rel else None
                        if comm_src is not None:
                            tmp_comm = os.path.join(tmp_bin_dir, f"communicator{exe_ext}")
                            ccomp = await compile_cpp(
                                str(comm_src),
                                tmp_comm,
                                compiler=compiler,
                                include_dirs=[str(comm_src.parent), *base_includes],
                                use_cache=False,
                            )
                            if ccomp.success:
                                interactor_override = tmp_comm
                            else:
                                blocking.append(
                                    {
                                        "gate": "communicator_compile",
                                        "reason": f"failed to compile standalone communicator: {ccomp.error or ccomp.stderr[:200]}",
                                    }
                                )
                        else:
                            comm_bin = resolve_communicator_binary(str(problem_path), manifest, exe_ext)
                            if comm_bin:
                                interactor_override = comm_bin
                        judge_ready = interactor_override is not None
                elif spj_checker:
                    chk_src = self._find_checker_source(problem_path, standalone=True)
                    if chk_src is not None:
                        tmp_chk = os.path.join(tmp_bin_dir, f"checker{exe_ext}")
                        ccomp = await compile_cpp(
                            str(chk_src),
                            tmp_chk,
                            compiler=compiler,
                            include_dirs=base_includes,
                            use_cache=False,
                        )
                        if ccomp.success:
                            checker_override = tmp_chk
                        else:
                            blocking.append(
                                {
                                    "gate": "checker_compile",
                                    "reason": f"failed to compile standalone checker: {ccomp.error or ccomp.stderr[:200]}",
                                }
                            )
                    else:
                        chk_bin = checker_exe_path(str(problem_path), exe_ext)
                        try:
                            Path(chk_bin).resolve().relative_to(real_root)
                            if not Path(chk_bin).is_symlink() and Path(chk_bin).is_file():
                                checker_override = chk_bin
                        except (ValueError, RuntimeError, OSError):
                            pass
                    judge_ready = checker_override is not None

                # 2. Main solution: prefer fresh temp compilation from source (avoids stale/cross-OS binaries)
                grader_extra_srcs: list[str] | None = None
                if is_comm and manifest.protocol is not None and manifest.protocol.mode == "grader_linked":
                    g_rel = resolve_grader_source_existing(str(problem_path), manifest)
                    if g_rel:
                        g_file = self._safe_file(problem_path, g_rel)
                        if g_file is not None:
                            grader_extra_srcs = [str(g_file)]

                sol_override: str | None = None
                sol_ready = False
                sol_includes: list[str] = list(base_includes)
                sol_src = self._find_main_solution_source(
                    problem_path, manifest, standalone=True, layout=layout
                )
                if (
                    is_comm
                    and manifest.protocol is not None
                    and manifest.protocol.participant_solutions
                ):
                    parts = resolve_participant_binaries(str(problem_path), manifest, None, exe_ext)
                    sol_ready = bool(parts) and all(Path(p).is_file() for p in parts)
                if not sol_ready and sol_src is not None:
                    if sol_src.suffix.lower() == ".py":
                        warnings.append(
                            {
                                "gate": "answer_consistency",
                                "reason": f"skipping unsupported language='python' main solution {sol_src.name}",
                            }
                        )
                    else:
                        tmp_sol = os.path.join(tmp_bin_dir, f"sol{exe_ext}")
                        sol_includes = [str(sol_src.parent), *base_includes]
                        scomp = await compile_cpp(
                            str(sol_src),
                            tmp_sol,
                            compiler=compiler,
                            include_dirs=sol_includes,
                            extra_sources=grader_extra_srcs,
                            use_cache=False,
                        )
                        if scomp.success:
                            sol_override = tmp_sol
                            sol_ready = True
                        else:
                            blocking.append(
                                {
                                    "gate": "answer_consistency",
                                    "reason": f"failed to compile standalone solution {sol_src.name}: {scomp.error or scomp.stderr[:200]}",
                                }
                            )
                elif not sol_ready:
                    sol_bin = self._safe_file(problem_path, f"solutions/sol{exe_ext}") or self._safe_file(
                        problem_path, f"sol{exe_ext}"
                    )
                    if sol_bin is not None:
                        sol_override = str(sol_bin)
                        sol_ready = True

                std_timings_live: dict[str, float] | None = None
                if sol_ready and judge_ready:
                    ans_res = await verifier._check_answer_consistency(
                        str(problem_path),
                        str(tests_dir),
                        "sol",
                        answer_ext,
                        60,
                        verify_with_checker=spj_checker,
                        manifest=manifest,
                        use_system_temp=True,
                        sol_exe_override=sol_override,
                        checker_exe_override=checker_override,
                        interactor_exe_override=interactor_override,
                    )
                    quality_signals["answer_consistency"] = self._wrap_verify_signal(ans_res)
                    if isinstance(ans_res.get("per_test_timings"), list):
                        std_timings_live = {
                            str(item["file"]): float(item["time_ms"])
                            for item in ans_res["per_test_timings"]
                            if isinstance(item, dict)
                            and isinstance(item.get("file"), str)
                            and isinstance(item.get("time_ms"), int | float)
                        }
                    if not ans_res.get("passed"):
                        blocking.append(
                            {
                                "gate": "answer_consistency",
                                "reason": str(
                                    ans_res.get("error")
                                    or "solution output inconsistent with test answers/judge"
                                ),
                            }
                        )
                elif not any(b.get("gate") in ("answer_consistency", "checker_compile", "interactor_compile", "communicator_compile") for b in blocking):
                    warnings.append(
                        {
                            "gate": "answer_consistency",
                            "reason": "solution or judge source/binary not found; skipped live answer_consistency in standalone mode",
                        }
                    )

                # 2.5 Isolated ASan/UBSan probe on up to 3 representative inputs
                await self._run_standalone_sanitizer_check(
                    tmp_bin_dir=tmp_bin_dir,
                    tests_dir=tests_dir,
                    manifest=manifest,
                    compiler=compiler,
                    exe_ext=exe_ext,
                    sol_src=sol_src,
                    sol_override=sol_override,
                    sol_includes=sol_includes,
                    grader_extra_srcs=grader_extra_srcs,
                    is_protocol=is_interactive or is_comm,
                    quality_signals=quality_signals,
                    blocking=blocking,
                    warnings=warnings,
                )

                # 2.6 Isolated empirical complexity probe strictly in tmp_bin_dir (non-protocol only)
                await self._run_standalone_empirical_check(
                    problem_path=problem_path,
                    tmp_bin_dir=tmp_bin_dir,
                    manifest=manifest,
                    compiler=compiler,
                    exe_ext=exe_ext,
                    base_includes=base_includes,
                    sol_src=sol_src,
                    sol_override=sol_override,
                    is_protocol=is_interactive or is_comm,
                    quality_signals=quality_signals,
                    blocking=blocking,
                    warnings=warnings,
                )

                # 3. Wrong solutions: prefer fresh temp compilation; fail closed when require_wrong_solution_kill=True
                compiled_wrongs: list[str] = []
                wrong_overrides: dict[str, str] = {}
                failed_wrong_compiles: list[str] = []
                for s in manifest.solutions:
                    if s.role != "wrong" or not is_safe_stem(s.name):
                        continue
                    w_src = self._safe_file(problem_path, s.path) if s.path else None
                    if w_src is not None:
                        if s.language != "cpp" or w_src.suffix.lower() == ".py":
                            warnings.append(
                                {
                                    "gate": "wrong_solution_kill",
                                    "reason": f"skipping unsupported language='{s.language}' wrong solution {s.name}",
                                }
                            )
                            continue
                        tmp_w = os.path.join(tmp_bin_dir, f"{s.name}{exe_ext}")
                        wcomp = await compile_cpp(
                            str(w_src),
                            tmp_w,
                            compiler=compiler,
                            include_dirs=[str(w_src.parent), *base_includes],
                            extra_sources=grader_extra_srcs,
                            use_cache=False,
                        )
                        if wcomp.success:
                            compiled_wrongs.append(s.name)
                            wrong_overrides[s.name] = tmp_w
                        else:
                            failed_wrong_compiles.append(
                                f"{s.name}: {(wcomp.error or wcomp.stderr)[:120]}"
                            )
                        continue
                    existing_w = self._safe_file(problem_path, f"solutions/{s.name}{exe_ext}")
                    if existing_w is not None:
                        compiled_wrongs.append(s.name)
                block_on_wrong_compile_fail = bool(
                    strict or manifest.quality_gates.require_wrong_solution_kill
                )
                if failed_wrong_compiles:
                    compile_fail_reason = (
                        f"failed to compile standalone wrong solution(s): {failed_wrong_compiles[:5]}"
                    )
                    if block_on_wrong_compile_fail:
                        blocking.append(
                            {
                                "gate": "wrong_solution_kill",
                                "reason": compile_fail_reason,
                            }
                        )
                    else:
                        warnings.append(
                            {
                                "gate": "wrong_solution_kill",
                                "reason": compile_fail_reason,
                            }
                        )
                if judge_ready and (compiled_wrongs or manifest.quality_gates.require_wrong_solution_kill):
                    wrong_res = await verifier._check_wrong_solution_kill(
                        problem_dir=str(problem_path),
                        tests_dir=str(tests_dir),
                        wrong_solution_names=compiled_wrongs,
                        answer_ext=answer_ext,
                        timeout=60,
                        verify_with_checker=spj_checker,
                        manifest=manifest,
                        use_system_temp=True,
                        std_timings=std_timings_live,
                        checker_exe_override=checker_override,
                        interactor_exe_override=interactor_override,
                        wrong_exe_overrides=wrong_overrides or None,
                    )
                    if failed_wrong_compiles and isinstance(wrong_res, dict):
                        wrong_res["failed_compiles"] = list(failed_wrong_compiles)
                        if block_on_wrong_compile_fail:
                            wrong_res["passed"] = False
                    quality_signals["wrong_solution_kill"] = self._wrap_verify_signal(wrong_res)
                    if not wrong_res.get("passed"):
                        has_survivors = bool(wrong_res.get("survivors"))
                        if not (failed_wrong_compiles and block_on_wrong_compile_fail and not has_survivors):
                            blocking.append(
                                {
                                    "gate": "wrong_solution_kill",
                                    "reason": str(
                                        wrong_res.get("message") or "wrong solution survived all tests"
                                    ),
                                }
                            )
                elif failed_wrong_compiles and block_on_wrong_compile_fail:
                    quality_signals["wrong_solution_kill"] = {
                        "executed": True,
                        "passed": False,
                        "evidence": {
                            "passed": False,
                            "failed_compiles": list(failed_wrong_compiles),
                            "message": f"all standalone wrong solution(s) failed to compile: {failed_wrong_compiles[:5]}",
                        },
                    }

                # 4. Optional single-point mutation testing inside the same isolated temp context (reuses live judges)
                if (
                    run_mutation
                    and sol_src is not None
                    and sol_src.suffix.lower() != ".py"
                    and not any(b.get("gate") == "path_containment" for b in blocking)
                ):
                    mut_res = await run_mutation_testing(
                        str(problem_path),
                        str(raw_tests_dir),
                        manifest=manifest,
                        sol_source=sol_src,
                        answer_ext=answer_ext,
                        budget=manifest.audit_gates.mutation_budget,
                        verify_with_checker=spj_checker,
                        checker_bin=checker_override,
                        interactor_bin=interactor_override,
                    )
                    quality_signals["mutation_testing"] = {
                        "executed": bool(mut_res.get("executed")),
                        "passed": bool(mut_res.get("passed", True)),
                        "evidence": mut_res,
                    }
                    if mut_res.get("executed") and mut_res.get("survived_mutants", 0) > 0:
                        surv_ids = [str(s.get("id")) for s in mut_res.get("survivors", [])[:5]]
                        mut_reason = (
                            f"{mut_res['survived_mutants']}/{mut_res['compiled_mutants']} single-point "
                            f"mutants survived all tests ({surv_ids}); consider strengthening hack/edge test cases."
                        )
                        if manifest.audit_gates.fail_on_mutation_survival:
                            blocking.append({"gate": "mutation_testing", "reason": mut_reason})
                        else:
                            warnings.append({"gate": "mutation_testing", "reason": mut_reason})
                    if mut_res.get("executed") and mut_res.get("judge_error_mutants", 0) > 0:
                        warnings.append(
                            {
                                "gate": "mutation_testing",
                                "reason": (
                                    f"{mut_res['judge_error_mutants']} mutant run(s) hit judge FAIL; "
                                    "check checker/interactor/communicator stability."
                                ),
                            }
                        )

    async def _run_standalone_sanitizer_check(
        self,
        *,
        tmp_bin_dir: str,
        tests_dir: Path,
        manifest: AutoCodeManifest,
        compiler: str,
        exe_ext: str,
        sol_src: Path | None,
        sol_override: str | None,
        sol_includes: list[str],
        grader_extra_srcs: list[str] | None,
        is_protocol: bool,
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        warnings: list[dict[str, str]],
    ) -> None:
        """Compile and run isolated ASan/UBSan checks on up to 3 representative inputs."""
        if is_protocol:
            quality_signals["sanitizer_check"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "standalone sanitizer check is not applicable for protocol problems"},
            }
            return
        if sol_src is None or sol_src.suffix.lower() == ".py" or sol_override is None:
            quality_signals["sanitizer_check"] = {
                "applicable": True,
                "status": "skipped",
                "executed": False,
                "passed": True,
                "evidence": {"status": "skipped", "reason": "main C++ solution source unavailable"},
            }
            return

        in_paths = sorted(
            (p for p in tests_dir.iterdir() if p.is_file() and p.suffix == ".in"),
            key=lambda p: (p.stat().st_size, _natural_sort_key(p)),
        )
        if not in_paths:
            quality_signals["sanitizer_check"] = {
                "applicable": True,
                "status": "skipped",
                "executed": False,
                "passed": True,
                "evidence": {"status": "skipped", "reason": "no test inputs available for sanitizer check"},
            }
            return

        if len(in_paths) <= 3:
            rep_paths = in_paths
        else:
            picked = [in_paths[0], in_paths[len(in_paths) // 2], in_paths[-1]]
            rep_paths = list(dict.fromkeys(picked))

        san_probe = await probe_sanitizer_support_async(compiler)
        if not san_probe.get("available"):
            quality_signals["sanitizer_check"] = {
                "applicable": True,
                "status": "unavailable",
                "executed": False,
                "passed": True,
                "evidence": {
                    "status": "unavailable",
                    "reason": str(san_probe.get("reason") or "sanitizer runtime unavailable"),
                    "sanitizer_info": san_probe,
                },
            }
            return

        tmp_sol_san = os.path.join(tmp_bin_dir, f"sol_san{exe_ext}")
        san_comp = await compile_cpp(
            str(sol_src),
            tmp_sol_san,
            compiler=compiler,
            opt_level="O1",
            include_dirs=sol_includes,
            extra_sources=grader_extra_srcs,
            use_cache=False,
            sanitizers=True,
        )
        if not san_comp.success:
            reason = str(san_comp.error or san_comp.stderr[:200] or "sanitizer compile failed")
            quality_signals["sanitizer_check"] = {
                "applicable": True,
                "status": "unavailable",
                "executed": False,
                "passed": True,
                "evidence": {
                    "status": "unavailable",
                    "reason": reason,
                    "sanitizer_info": san_probe,
                },
            }
            warnings.append({"gate": "sanitizer_check", "reason": f"ASan/UBSan build skipped: {reason}"})
            return

        san_env = dict(san_probe.get("env", {}))
        san_disable_as = bool(san_probe.get("asan_verified"))
        tl_ms = float(manifest.time_limit_ms) if manifest.time_limit_ms > 0 else 2000.0
        mem_mb = float(manifest.memory_limit_mb) if manifest.memory_limit_mb > 0 else 256.0
        san_timeout = min(30.0, max(5.0, (tl_ms / 1000.0) * 3.0))

        failures: list[dict[str, Any]] = []
        timeouts: list[str] = []
        for rep_path in rep_paths:
            inp = rep_path.read_text(encoding="utf-8", errors="replace")
            run = await run_binary(
                tmp_sol_san,
                inp,
                timeout=san_timeout,
                memory_mb=mem_mb,
                env=san_env,
                disable_as_limit=san_disable_as,
                cwd=tmp_bin_dir,
            )
            err_text = (run.stderr or run.error or "").strip()
            has_diag = _has_sanitizer_diagnostic(run.stderr)
            if run.timed_out and not has_diag:
                timeouts.append(rep_path.name)
                continue
            if has_diag or not run.success:
                failures.append(
                    {
                        "file": rep_path.name,
                        "return_code": run.return_code,
                        "sanitizer_diagnostic": has_diag,
                        "detail": (err_text or f"exit_code={run.return_code}")[:400],
                    }
                )

        if timeouts:
            warnings.append(
                {
                    "gate": "sanitizer_check",
                    "reason": f"ASan/UBSan run timed out on {timeouts} (instrumentation overhead)",
                }
            )

        passed = not failures
        quality_signals["sanitizer_check"] = {
            "applicable": True,
            "status": "passed" if passed else "failed",
            "executed": True,
            "passed": passed,
            "evidence": {
                "status": "passed" if passed else "failed",
                "profile": san_probe.get("profile"),
                "flags": list(san_probe.get("flags", [])),
                "asan_verified": bool(san_probe.get("asan_verified")),
                "ubsan_verified": bool(san_probe.get("ubsan_verified")),
                "tested_inputs": [p.name for p in rep_paths],
                "timeouts": timeouts,
                "failures": failures,
            },
        }
        if failures:
            first = failures[0]
            blocking.append(
                {
                    "gate": "sanitizer_check",
                    "reason": f"ASan/UBSan flagged error on {first['file']}: {first['detail'][:200]}",
                }
            )

    async def _run_standalone_empirical_check(
        self,
        *,
        problem_path: Path,
        tmp_bin_dir: str,
        manifest: AutoCodeManifest,
        compiler: str,
        exe_ext: str,
        base_includes: list[str],
        sol_src: Path | None,
        sol_override: str | None,
        is_protocol: bool,
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        warnings: list[dict[str, str]],
    ) -> None:
        """Run isolated empirical complexity verification strictly inside ``tmp_bin_dir``."""
        if is_protocol:
            quality_signals["empirical_complexity"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"status": "not_applicable", "reason": "isolated standalone empirical complexity is for non-protocol problems"},
            }
            return
        if sol_src is None or sol_override is None or sol_src.suffix.lower() == ".py":
            return

        gen_src = self._find_generator_source(problem_path, standalone=True)
        if gen_src is None:
            quality_signals["empirical_complexity"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"status": "no_generator", "reason": "generator source not found"},
            }
            return

        tmp_gen = os.path.join(tmp_bin_dir, f"gen{exe_ext}")
        gcomp = await compile_cpp(
            str(gen_src),
            tmp_gen,
            compiler=compiler,
            include_dirs=[str(gen_src.parent), *base_includes],
            use_cache=False,
        )
        if not gcomp.success:
            err_msg = str(gcomp.error or gcomp.stderr[:200] or "generator compile failed")
            quality_signals["empirical_complexity"] = {
                "executed": False,
                "passed": True,
                "evidence": {
                    "status": "generator_compile_failed",
                    "reason": err_msg,
                    "isolated_standalone": True,
                },
            }
            warnings.append({"gate": "empirical_complexity", "reason": f"standalone generator build skipped: {err_msg}"})
            return

        try:
            sol_code = sol_src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        explicit_claimed: str | None = None
        emp_cfg = manifest.empirical_complexity
        if emp_cfg is not None and isinstance(emp_cfg.claimed_complexity, str) and emp_cfg.claimed_complexity.strip():
            explicit_claimed = emp_cfg.claimed_complexity.strip()
        if not explicit_claimed:
            explicit_claimed = extract_claimed_complexity(sol_code)

        if explicit_claimed:
            claimed_complexity = explicit_claimed
            claimed_source = "explicit"
        else:
            static_est, _ = estimate_static_complexity(sol_code)
            claimed_complexity = static_est if static_est != "unknown" else "O(n)"
            claimed_source = "static_fallback"

        dominant_var = emp_cfg.dominant_var if emp_cfg is not None else "n"
        scale_mode = emp_cfg.scale_mode if emp_cfg is not None else "auto"
        args_template = emp_cfg.generator_args_template if emp_cfg is not None else None
        extra_vars: dict[str, Any] = dict(emp_cfg.extra_vars) if emp_cfg is not None else {}

        actual_n_max = 10000
        if emp_cfg is not None and emp_cfg.max_value is not None:
            actual_n_max = emp_cfg.max_value
        else:
            dom_max = extract_dominant_var_max(manifest.constraints, dominant_var)
            if dom_max is not None:
                actual_n_max = dom_max
            else:
                manifest_n = extract_n_max_from_constraints(manifest.constraints)
                if manifest_n is not None:
                    actual_n_max = manifest_n

        if (
            dominant_var.lower() in ("t", "t_max")
            and args_template is None
            and "n_min" not in extra_vars
            and "n_max" not in extra_vars
        ):
            fixed_n = extract_n_max_from_constraints(manifest.constraints)
            if fixed_n is not None:
                extra_vars["n_min"] = fixed_n
                extra_vars["n_max"] = fixed_n

        time_limit_ms = float(manifest.time_limit_ms) if manifest.time_limit_ms > 0 else 2000.0
        memory_limit_mb = float(manifest.memory_limit_mb) if manifest.memory_limit_mb > 0 else 512.0

        if emp_cfg is not None and emp_cfg.scale_points:
            scale_points = list(emp_cfg.scale_points)
        else:
            scale_points = MultiScaleSampler.compute_scale_points(
                actual_n_max,
                claimed_complexity,
                scale_mode=scale_mode,
                dominant_var=dominant_var,
            )

        empirical_dir = os.path.join(tmp_bin_dir, "empirical_tests")
        os.makedirs(empirical_dir, exist_ok=True)
        baseline_overhead = await DynamicExecutionMonitor.measure_baseline_overhead(cwd=tmp_bin_dir)

        samples: list[dict[str, Any]] = []
        for idx, pt in enumerate(scale_points):
            in_file = os.path.join(empirical_dir, f"scale_{pt}.in")
            try:
                gen_cmd = MultiScaleSampler.format_generator_command(
                    tmp_gen,
                    pt,
                    42 + idx,
                    template=args_template,
                    extra_vars=extra_vars or None,
                    dominant_var=dominant_var,
                )
                await asyncio.to_thread(
                    MultiScaleSampler.generate_scale_input_file,
                    gen_cmd,
                    in_file,
                    5.0,
                )
            except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
                quality_signals["empirical_complexity"] = {
                    "executed": False,
                    "passed": True,
                    "evidence": {
                        "status": "generator_error",
                        "message": f"Failed generating scale point {pt}: {exc}",
                        "isolated_standalone": True,
                    },
                }
                warnings.append(
                    {
                        "gate": "empirical_complexity",
                        "reason": f"standalone empirical generator probe skipped: {exc}",
                    }
                )
                return

            if idx == 0:
                await DynamicExecutionMonitor.run_monitored_process(
                    [sol_override],
                    in_file,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    cwd=tmp_bin_dir,
                    baseline_overhead_ms=baseline_overhead,
                )
            res = await DynamicExecutionMonitor.run_monitored_process(
                [sol_override],
                in_file,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                cwd=tmp_bin_dir,
                baseline_overhead_ms=baseline_overhead,
            )
            chosen_cpu = res.get("cpu_time_ms")
            chosen_mem = res.get("memory_mb")
            chosen_status = res.get("status")
            if (
                chosen_status == "ok"
                and chosen_cpu is not None
                and float(res.get("wall_time_ms") or 0.0) < 50.0
            ):
                cpu_runs: list[float] = [float(chosen_cpu)]
                mem_runs: list[float] = [float(chosen_mem or 0.0)]
                for _ in range(2):
                    rep = await DynamicExecutionMonitor.run_monitored_process(
                        [sol_override],
                        in_file,
                        time_limit_ms=time_limit_ms,
                        memory_limit_mb=memory_limit_mb,
                        cwd=tmp_bin_dir,
                        baseline_overhead_ms=baseline_overhead,
                    )
                    if rep.get("status") == "ok" and rep.get("cpu_time_ms") is not None:
                        cpu_runs.append(float(rep["cpu_time_ms"]))
                        mem_runs.append(float(rep.get("memory_mb") or 0.0))
                chosen_cpu = round(statistics.median(cpu_runs), 2)
                chosen_mem = round(max(mem_runs), 2)
            sample_row: dict[str, Any] = {
                "n": pt,
                "cpu_time_ms": chosen_cpu,
                "memory_mb": chosen_mem,
                "status": chosen_status,
            }
            if "instruction_count" in res:
                sample_row["instruction_count"] = res["instruction_count"]
            if "metric_backend" in res:
                sample_row["metric_backend"] = res["metric_backend"]
            samples.append(sample_row)
            if chosen_status in ("timeout", "mle"):
                break

        verdict_dict = EmpiricalRatioAnalyzer.verify_complexity(
            claimed_complexity,
            samples,
            time_limit_ms=time_limit_ms,
            dominant_var=dominant_var,
        )
        verdict_dict["dominant_var"] = dominant_var
        verdict_dict["scale_mode"] = scale_mode
        verdict_dict["isolated_standalone"] = True
        verdict_dict["claimed_source"] = claimed_source

        passed = bool(verdict_dict.get("passed"))
        quality_signals["empirical_complexity"] = {
            "executed": True,
            "passed": passed,
            "evidence": verdict_dict,
        }
        if not passed:
            failure_reason = str(
                verdict_dict.get("failure_reason")
                or verdict_dict.get("message")
                or "empirical complexity verification failed"
            )
            if claimed_source == "explicit" or verdict_dict.get("verdict") in (
                "timeout_at_scale",
                "mle_at_scale",
            ):
                blocking.append({"gate": "empirical_complexity", "reason": failure_reason})
            else:
                warnings.append({"gate": "empirical_complexity", "reason": failure_reason})
