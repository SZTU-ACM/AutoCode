"""Isolated zero-write standalone live execution checks for ``ProblemAuditTool``."""

from __future__ import annotations

import os
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
from ..utils.compiler import compile_cpp, resolve_effective_compiler
from ..utils.mutation import run_mutation_testing
from ..utils.platform import get_exe_extension
from ..workflow import (
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from .audit_signals import _AuditSignalsMixin
from .test_verify import ProblemVerifyTestsTool


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
    ) -> None:
        """Run read-only live checks in standalone mode without mutating ``problem_dir``."""
        if any(b.get("gate") == "path_containment" for b in blocking):
            return
        tests_dir = self._resolve_tests_dir(problem_path, standalone=True, layout=layout)
        if tests_dir is None:
            return

        # Reject symlinked test files escaping problem_dir before reading or running binaries on them.
        escaped_tests = self._find_escaped_entries(problem_path, tests_dir)
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
        answer_ext = verifier._resolve_answer_ext(str(tests_dir), None, tests_manifest or None)
        if not (isinstance(tests_manifest, dict) and tests_manifest.get("answer_ext")):
            has_ans = any(tests_dir.glob("*.ans"))
            has_out = any(tests_dir.glob("*.out"))
            if has_out and not has_ans:
                answer_ext = ".out"

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
        base_includes.append(real_root)
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

            # 3. Wrong solutions: prefer fresh temp compilation; fail closed when require_wrong_solution_kill=True
            compiled_wrongs: list[str] = []
            wrong_overrides: dict[str, str] = {}
            failed_wrong_compiles: list[str] = []
            for s in manifest.solutions:
                if s.role != "wrong" or not is_safe_stem(s.name):
                    continue
                w_src = self._safe_file(problem_path, s.path) if s.path else None
                if w_src is not None:
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
            if failed_wrong_compiles:
                warnings.append(
                    {
                        "gate": "wrong_solution_kill",
                        "reason": f"failed to compile standalone wrong solution(s): {failed_wrong_compiles[:5]}",
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
                quality_signals["wrong_solution_kill"] = self._wrap_verify_signal(wrong_res)
                if not wrong_res.get("passed"):
                    blocking.append(
                        {
                            "gate": "wrong_solution_kill",
                            "reason": str(wrong_res.get("message") or "wrong solution survived all tests"),
                        }
                    )

            # 4. Optional single-point mutation testing inside the same isolated temp context (reuses live judges)
            if run_mutation and sol_src is not None and not any(b.get("gate") == "path_containment" for b in blocking):
                mut_res = await run_mutation_testing(
                    str(problem_path),
                    str(tests_dir),
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
                    warnings.append(
                        {
                            "gate": "mutation_testing",
                            "reason": (
                                f"{mut_res['survived_mutants']}/{mut_res['compiled_mutants']} single-point "
                                f"mutants survived all tests ({surv_ids}); consider strengthening hack/edge test cases."
                            ),
                        }
                    )
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
