"""Native communication problem execution and judging engine for AutoCode.

Supports three first-class communication problem architectures:
1. ``run_twice`` (Codeforces / IOI / QOJ multi-pass):
   - Physical multi-process isolation with dedicated temporary working directories per pass.
   - Canonical 4-arg testlib ``registerTestlibCmd`` communicator invoked per stage with
     ``AUTOCODE_COMM_STAGE`` / ``AUTOCODE_COMM_PASSES`` / ``AUTOCODE_COMM_ROLE`` env vars.
   - Cumulative CPU accounting across passes, peak RSS memory tracking, channel
     transformation/noise injection, and automatic bit/byte/round budget enforcement.
2. ``grader_linked`` (IOI / QOJ compiled grader + header):
   - Runs contestant code linked with ``files/grader.cpp`` and ``files/problem.h`` in an
     isolated temporary working directory.
   - Distinguishes grader verdicts (``AC``/``WA``/``PE``/``PC``/``FAIL``) from contestant
     runtime crashes (``RE``) and enforces protocol budgets.
3. ``multi_process`` (K-party real-time communication with central manager):
   - Spawns K isolated contestant processes alongside a testlib ``registerInteraction``
     manager using a cross-platform line-multiplexed protocol (``TO <idx> <payload>``,
     ``CLOSE <idx>``, ``FROM <idx> <payload>``, ``EOF <idx>``).
   - Monitors aggregate participant CPU, peak RSS, multi-party deadlocks across all K+1
     processes, crash-to-RE promotion, and chronological transcript recording.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import time
from typing import TYPE_CHECKING, Any

import psutil

from .checker_judge import verdict_from_testlib_rc
from .judge_runner import (
    _MAX_STDERR_BYTES,
    _MAX_TRANSCRIPT_BYTES_DEFAULT,
    JudgeResult,
    StandardRunner,
    _format_transcript,
    _is_harmless_sigpipe,
    _read_bounded,
    parse_judge_metrics,
)
from .platform import get_exe_extension
from .process import disable_posix_core_dumps, terminate_pid_tree

if TYPE_CHECKING:
    from ..workflow.models import AutoCodeManifest

_STREAM_LIMIT = 4 * 1024 * 1024  # 4 MiB line buffer for large messages

_GRADER_VERDICT_RE = re.compile(
    r"(?:^|\b)(?:wrong\s+answer|wrong\s+output\s+format|presentation\s+error|"
    r"unexpected\s+eof|partially\s+correct|points\s*(?:[=:]\s*|\s+)-?\d|"
    r"score\s*[=:]\s*-?\d|fail\b|verdict\s*[:=]\s*(?:AC|WA|PE|PC|FAIL))",
    re.IGNORECASE,
)
_EXPLICIT_WA_PREFIX_RE = re.compile(
    r"^\s*(?:wrong\s+answer|verdict\s*[:=]\s*wa\b)",
    re.IGNORECASE,
)


def is_safe_stem(name: str) -> bool:
    """Return True if ``name`` is a single safe filename stem without separators or traversal."""
    if not isinstance(name, str) or not name.strip() or "\x00" in name:
        return False
    cleaned = name.strip()
    if cleaned in {".", ".."}:
        return False
    return not any(sep and sep in cleaned for sep in (os.sep, os.altsep, "/", "\\", ":"))


def safe_resolve_under_root(problem_dir: str, rel_path: str) -> str | None:
    """Resolve ``rel_path`` under ``problem_dir``, rejecting traversal and symlink escapes.

    Returns the resolved real path if strictly inside (or equal to) ``realpath(problem_dir)``,
    otherwise ``None``.
    """
    if not isinstance(rel_path, str) or not rel_path.strip() or "\x00" in rel_path:
        return None
    normalized = rel_path.strip().replace("\\", "/")
    if os.path.isabs(normalized) or normalized.startswith("/") or ":" in normalized:
        return None
    real_root = os.path.realpath(problem_dir)
    candidate = os.path.join(real_root, normalized)
    real_target = os.path.realpath(candidate)
    try:
        common = os.path.commonpath([real_root, real_target])
    except ValueError:
        return None
    if os.path.normcase(common) != os.path.normcase(real_root):
        return None
    return real_target


def measure_payload_bits_bytes(payload: str) -> tuple[int, int]:
    """Measure message size in (bits, bytes).

    If the payload consists solely of binary ``0``/``1`` characters (plus whitespace),
    count exact bit characters; otherwise fall back to ``bytes * 8``.
    """
    raw_bytes = len(payload.encode("utf-8"))
    stripped = payload.strip()
    if stripped and all(ch in ("0", "1", " ", "\t", "\r", "\n") for ch in stripped):
        bits = sum(1 for ch in stripped if ch in ("0", "1"))
        return bits, (bits + 7) // 8 if bits > 0 else 0
    return raw_bytes * 8, raw_bytes


def enforce_protocol_budgets(
    result: JudgeResult,
    *,
    max_bits: int | None = None,
    max_bytes: int | None = None,
    max_queries: int | None = None,
    max_rounds: int | None = None,
) -> JudgeResult:
    """Enforce manifest protocol budgets on an accepted/partial-credit JudgeResult."""
    if result.verdict not in ("AC", "PC"):
        return result

    checks = (
        ("bits_used", max_bits),
        ("bytes_used", max_bytes),
        ("queries", max_queries),
        ("rounds", max_rounds),
    )
    violations: list[str] = []
    for metric_key, limit in checks:
        if limit is None:
            continue
        actual = result.metrics.get(metric_key)
        if isinstance(actual, int) and not isinstance(actual, bool) and actual > limit:
            violations.append(f"{metric_key}={actual} > {limit}")

    if violations:
        result.verdict = "WA"
        result.detail = "Protocol budget exceeded: " + ", ".join(violations)
    return result


async def _run_communicator_stage(
    communicator_exe: str,
    argv_files: list[str],
    *,
    env: dict[str, str],
    timeout_sec: float,
    cwd: str | None,
) -> tuple[int | None, str, str, bool]:
    """Run one testlib communicator stage with bounded stdout/stderr and clean cancellation."""
    popen_kwargs: dict[str, Any] = {}
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
        popen_kwargs["preexec_fn"] = disable_posix_core_dumps

    proc = await asyncio.create_subprocess_exec(
        communicator_exe,
        *argv_files,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        **popen_kwargs,
    )
    out_task = asyncio.create_task(_read_bounded(proc.stdout, _STREAM_LIMIT))
    err_task = asyncio.create_task(_read_bounded(proc.stderr, _MAX_STDERR_BYTES))
    timed_out = False
    try:
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout_sec)
        except (asyncio.TimeoutError, TimeoutError):
            timed_out = True
            await terminate_pid_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError):
                pass
        try:
            out_bytes = await asyncio.wait_for(out_task, timeout=1.0)
        except (asyncio.TimeoutError, TimeoutError):
            out_task.cancel()
            await asyncio.gather(out_task, return_exceptions=True)
            out_bytes = b""
        try:
            err_bytes = await asyncio.wait_for(err_task, timeout=1.0)
        except (asyncio.TimeoutError, TimeoutError):
            err_task.cancel()
            await asyncio.gather(err_task, return_exceptions=True)
            err_bytes = b""
    except BaseException:
        if proc.returncode is None:
            await terminate_pid_tree(proc.pid)
            try:
                await asyncio.wait_for(proc.wait(), timeout=1.0)
            except Exception:
                pass
        for t in (out_task, err_task):
            if not t.done():
                t.cancel()
        await asyncio.gather(out_task, err_task, return_exceptions=True)
        transport = getattr(proc, "_transport", None)
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass
        raise

    return (
        proc.returncode,
        out_bytes.decode("utf-8", errors="replace"),
        err_bytes.decode("utf-8", errors="replace"),
        timed_out,
    )


def resolve_communicator_binary(
    problem_dir: str,
    manifest: AutoCodeManifest | None,
    exe_ext: str | None = None,
) -> str | None:
    """Resolve compiled communicator (or interactor fallback) binary strictly inside ``problem_dir``."""
    ext = exe_ext if exe_ext is not None else get_exe_extension()
    rel_candidates: list[str] = []
    if manifest is not None and manifest.protocol and manifest.protocol.communicator_file:
        stem = os.path.splitext(manifest.protocol.communicator_file)[0]
        rel_candidates.append(f"{stem}{ext}")
    rel_candidates.extend(
        [
            f"files/communicator{ext}",
            f"communicator{ext}",
            f"files/interactor{ext}",
            f"interactor{ext}",
        ]
    )
    for rel in rel_candidates:
        resolved = safe_resolve_under_root(problem_dir, rel)
        if resolved is not None and os.path.isfile(resolved):
            return resolved
    return None


def resolve_communicator_source_rel(manifest: AutoCodeManifest | None) -> str:
    """Return canonical relative source path for a communication problem's communicator."""
    if manifest is not None and manifest.protocol and manifest.protocol.communicator_file:
        return manifest.protocol.communicator_file.replace("\\", "/")
    return "files/communicator.cpp"


def resolve_communicator_source_existing(
    problem_dir: str,
    manifest: AutoCodeManifest | None,
) -> str | None:
    """Resolve an existing communicator source path (normalized POSIX relpath) inside ``problem_dir``."""
    candidates: list[str] = []
    if manifest is not None and manifest.protocol and manifest.protocol.communicator_file:
        candidates.append(manifest.protocol.communicator_file.replace("\\", "/"))
    candidates.extend(
        [
            "files/communicator.cpp",
            "communicator.cpp",
            "files/interactor.cpp",
            "interactor.cpp",
        ]
    )
    for rel in candidates:
        resolved = safe_resolve_under_root(problem_dir, rel)
        if resolved is not None and os.path.isfile(resolved):
            return os.path.relpath(resolved, os.path.realpath(problem_dir)).replace("\\", "/")
    return None


def resolve_grader_source_existing(
    problem_dir: str,
    manifest: AutoCodeManifest | None,
) -> str | None:
    """Resolve an existing grader source path (normalized POSIX relpath) inside ``problem_dir``."""
    candidates: list[str] = []
    if manifest is not None and manifest.protocol and manifest.protocol.grader_file:
        candidates.append(manifest.protocol.grader_file.replace("\\", "/"))
    candidates.extend(["files/grader.cpp", "grader.cpp"])
    for rel in candidates:
        resolved = safe_resolve_under_root(problem_dir, rel)
        if resolved is not None and os.path.isfile(resolved):
            return os.path.relpath(resolved, os.path.realpath(problem_dir)).replace("\\", "/")
    return None


def resolve_participant_binaries(
    problem_dir: str,
    manifest: AutoCodeManifest | None,
    primary_solution_exe: str | None = None,
    exe_ext: str | None = None,
) -> list[str]:
    """Resolve participant solution binaries for ``run_twice`` / ``multi_process``."""
    ext = exe_ext if exe_ext is not None else get_exe_extension()
    protocol = manifest.protocol if manifest is not None else None
    part_names = list(protocol.participant_solutions) if protocol else []

    if primary_solution_exe:
        abs_primary = os.path.abspath(primary_solution_exe)
        sol_default = os.path.abspath(os.path.join(problem_dir, "solutions", f"sol{ext}"))
        root_default = os.path.abspath(os.path.join(problem_dir, f"sol{ext}"))
        is_default_sol = os.path.normcase(abs_primary) in (
            os.path.normcase(sol_default),
            os.path.normcase(root_default),
        )
        if not part_names or not is_default_sol:
            return [abs_primary]

    if part_names:
        resolved: list[str] = []
        for name in part_names:
            if not is_safe_stem(name):
                return []
            cand_sol = safe_resolve_under_root(problem_dir, f"solutions/{name}{ext}")
            cand_root = safe_resolve_under_root(problem_dir, f"{name}{ext}")
            if cand_sol is not None and os.path.isfile(cand_sol):
                resolved.append(cand_sol)
            elif cand_root is not None and os.path.isfile(cand_root):
                resolved.append(cand_root)
            else:
                return []
        return resolved

    if primary_solution_exe:
        return [os.path.abspath(primary_solution_exe)]
    return []


class CommunicationRunner:
    """Execution and judging engine for ``run_twice``, ``grader_linked``, and ``multi_process``."""

    @classmethod
    async def run_twice(
        cls,
        communicator_exe: str,
        solution_exes: str | list[str],
        input_data: str,
        *,
        answer_data: str = "",
        passes: int = 2,
        pass_headers: list[str] | None = None,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 256.0,
        max_bits: int | None = None,
        max_bytes: int | None = None,
        max_queries: int | None = None,
        max_rounds: int | None = None,
        cwd: str | None = None,
        max_transcript_bytes: int = _MAX_TRANSCRIPT_BYTES_DEFAULT,
    ) -> JudgeResult:
        """Execute a physically isolated multi-pass Run-Twice communication test."""
        abs_comm = os.path.abspath(communicator_exe)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        if not os.path.isfile(abs_comm):
            return JudgeResult(
                verdict="FAIL", detail=f"Communicator binary not found: {communicator_exe}"
            )

        sol_list = [solution_exes] if isinstance(solution_exes, str) else list(solution_exes)
        abs_sols = [os.path.abspath(s) for s in sol_list]
        if not abs_sols:
            return JudgeResult(verdict="FAIL", detail="No solution binaries provided")
        for s in abs_sols:
            if not os.path.isfile(s):
                return JudgeResult(verdict="FAIL", detail=f"Solution binary not found: {s}")

        total_passes = max(2, int(passes))
        if len(abs_sols) == 1:
            pass_exes = [abs_sols[0]] * total_passes
        elif len(abs_sols) == total_passes:
            pass_exes = abs_sols
        else:
            return JudgeResult(
                verdict="FAIL",
                detail=(
                    f"solution_exes count ({len(abs_sols)}) must be 1 or equal to passes ({total_passes})"
                ),
            )

        if pass_headers and len(pass_headers) >= total_passes:
            headers = [str(h) for h in pass_headers[:total_passes]]
        elif total_passes == 2:
            headers = ["first", "second"]
        else:
            headers = [f"pass_{i}" for i in range(1, total_passes + 1)]

        effective_tl_ms = max(100.0, float(time_limit_ms))
        effective_mem_mb = max(16.0, float(memory_limit_mb))
        comm_timeout_sec = max(10.0, effective_tl_ms / 1000.0 * 2.0)

        wall_start = time.perf_counter()
        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            tmp_dir = os.path.abspath(raw_tmp_dir)
            in_path = os.path.join(tmp_dir, "input.in")
            ans_path = os.path.join(tmp_dir, "answer.ans")
            with open(in_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(input_data)
            with open(ans_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(answer_data)

            total_cpu_ms = 0.0
            peak_mem_mb = 0.0
            per_pass_cpu: list[float] = []
            per_pass_mem: list[float] = []
            measured_bits = 0
            measured_bytes = 0
            stage_reported_bits: int | None = None
            stage_reported_bytes: int | None = None
            cwd_files_written = 0
            transcript_events: list[tuple[str, bytes]] = []
            transcript_budget = max_transcript_bytes

            def _record(role: str, text: str) -> None:
                nonlocal transcript_budget
                if transcript_budget <= 0:
                    return
                raw = text.encode("utf-8", errors="replace")
                take = min(len(raw), transcript_budget)
                transcript_events.append((role, raw[:take]))
                transcript_budget -= take

            current_payload = input_data
            prev_channel_path = ans_path

            for stage in range(1, total_passes + 1):
                header = headers[stage - 1]
                pass_cwd = os.path.join(tmp_dir, f"pass_{stage}_cwd")
                os.makedirs(pass_cwd, exist_ok=True)

                remaining_tl_ms = max(100.0, effective_tl_ms - total_cpu_ms)
                pass_stdin = f"{header}\n{current_payload}" if current_payload else f"{header}\n"

                pass_res = await StandardRunner.run(
                    pass_exes[stage - 1],
                    pass_stdin,
                    time_limit_ms=remaining_tl_ms,
                    memory_limit_mb=effective_mem_mb,
                    cwd=pass_cwd,
                )

                try:
                    cwd_files_written += len(os.listdir(pass_cwd))
                except OSError:
                    pass

                pass_cpu = (
                    float(pass_res.cpu_time_ms)
                    if pass_res.cpu_time_ms is not None
                    else float(pass_res.wall_time_ms)
                )
                total_cpu_ms += pass_cpu
                peak_mem_mb = max(peak_mem_mb, float(pass_res.memory_mb))
                per_pass_cpu.append(round(pass_cpu, 2))
                per_pass_mem.append(round(float(pass_res.memory_mb), 2))
                _record(f"pass_{stage}:{header}", pass_res.stdout)

                if not pass_res.accepted:
                    wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 2)
                    return JudgeResult(
                        verdict=pass_res.verdict,
                        cpu_time_ms=None,
                        wall_time_ms=wall_ms,
                        memory_mb=round(peak_mem_mb, 2),
                        stdout=pass_res.stdout,
                        stderr=pass_res.stderr,
                        solution_returncode=pass_res.solution_returncode,
                        metrics={
                            "passes": total_passes,
                            "completed_passes": stage - 1,
                            "per_pass_cpu_ms": per_pass_cpu,
                            "per_pass_memory_mb": per_pass_mem,
                            "cwd_files_written": cwd_files_written,
                        },
                        transcript=_format_transcript(transcript_events, max_transcript_bytes),
                        detail=f"Pass {stage} ({header}) {pass_res.verdict}: {pass_res.detail}",
                    )

                if total_cpu_ms > effective_tl_ms:
                    wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 2)
                    return JudgeResult(
                        verdict="TLE",
                        cpu_time_ms=None,
                        wall_time_ms=wall_ms,
                        memory_mb=round(peak_mem_mb, 2),
                        stdout=pass_res.stdout,
                        stderr=pass_res.stderr,
                        solution_returncode=pass_res.solution_returncode,
                        metrics={
                            "passes": total_passes,
                            "completed_passes": stage,
                            "per_pass_cpu_ms": per_pass_cpu,
                            "per_pass_memory_mb": per_pass_mem,
                        },
                        transcript=_format_transcript(transcript_events, max_transcript_bytes),
                        detail=(
                            f"Cumulative CPU across passes exceeded limit "
                            f"({total_cpu_ms:.1f}ms > {effective_tl_ms:.1f}ms)"
                        ),
                    )

                pass_out_path = os.path.join(tmp_dir, f"pass_{stage}.out")
                with open(pass_out_path, "w", encoding="utf-8", newline="\n") as pf:
                    pf.write(pass_res.stdout)

                stage_env = dict(os.environ)
                stage_env["AUTOCODE_COMM_STAGE"] = str(stage)
                stage_env["AUTOCODE_COMM_PASSES"] = str(total_passes)
                stage_env["AUTOCODE_COMM_ROLE"] = "channel" if stage < total_passes else "judge"
                stage_env["AUTOCODE_COMM_ANS_PATH"] = ans_path

                comm_rc, comm_stdout, comm_stderr, comm_timed_out = await _run_communicator_stage(
                    abs_comm,
                    [in_path, pass_out_path, prev_channel_path],
                    env=stage_env,
                    timeout_sec=comm_timeout_sec,
                    cwd=tmp_dir,
                )

                stage_verdict = "FAIL" if comm_timed_out else verdict_from_testlib_rc(comm_rc)
                stage_score, stage_metrics = parse_judge_metrics(comm_stderr, "")

                if stage < total_passes:
                    bits_i, bytes_i = measure_payload_bits_bytes(pass_res.stdout)
                    measured_bits += bits_i
                    measured_bytes += bytes_i
                    if "bits_used" in stage_metrics:
                        stage_reported_bits = (stage_reported_bits or 0) + int(
                            stage_metrics["bits_used"]
                        )
                    if "bytes_used" in stage_metrics:
                        stage_reported_bytes = (stage_reported_bytes or 0) + int(
                            stage_metrics["bytes_used"]
                        )

                    if stage_verdict != "AC":
                        wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 2)
                        detail = (
                            f"Communicator timed out at stage {stage}"
                            if comm_timed_out
                            else comm_stderr.strip()[:500]
                        )
                        return JudgeResult(
                            verdict=stage_verdict,
                            cpu_time_ms=round(total_cpu_ms, 2)
                            if stage_verdict in ("WA", "PE", "PC")
                            else None,
                            wall_time_ms=wall_ms,
                            memory_mb=round(peak_mem_mb, 2),
                            stdout=pass_res.stdout,
                            stderr=pass_res.stderr,
                            judge_stdout=comm_stdout,
                            judge_stderr=comm_stderr,
                            solution_returncode=pass_res.solution_returncode,
                            judge_returncode=comm_rc,
                            score=stage_score,
                            metrics={
                                "passes": total_passes,
                                "rounds": stage,
                                "bits_used": (
                                    stage_reported_bits
                                    if stage_reported_bits is not None
                                    else measured_bits
                                ),
                                "bytes_used": (
                                    stage_reported_bytes
                                    if stage_reported_bytes is not None
                                    else measured_bytes
                                ),
                                "per_pass_cpu_ms": per_pass_cpu,
                                "per_pass_memory_mb": per_pass_mem,
                                "cwd_files_written": cwd_files_written,
                            },
                            transcript=_format_transcript(transcript_events, max_transcript_bytes),
                            detail=detail,
                        )

                    channel_path = os.path.join(tmp_dir, f"channel_{stage}.txt")
                    with open(channel_path, "w", encoding="utf-8", newline="\n") as cf:
                        cf.write(comm_stdout)
                    prev_channel_path = channel_path
                    current_payload = comm_stdout
                    _record(f"channel_{stage}->{stage + 1}", comm_stdout)
                else:
                    # Final stage: compute combined communication metrics without double-counting
                    if "bits_used" in stage_metrics:
                        final_bits = max(
                            stage_reported_bits or 0, int(stage_metrics["bits_used"])
                        )
                    elif stage_reported_bits is not None:
                        final_bits = stage_reported_bits
                    else:
                        final_bits = measured_bits

                    if "bytes_used" in stage_metrics:
                        final_bytes = max(
                            stage_reported_bytes or 0, int(stage_metrics["bytes_used"])
                        )
                    elif stage_reported_bytes is not None:
                        final_bytes = stage_reported_bytes
                    else:
                        final_bytes = measured_bytes

                    final_rounds = int(stage_metrics.get("rounds", total_passes))
                    final_metrics: dict[str, Any] = {
                        "passes": total_passes,
                        "rounds": final_rounds,
                        "bits_used": final_bits,
                        "bytes_used": final_bytes,
                        "per_pass_cpu_ms": per_pass_cpu,
                        "per_pass_memory_mb": per_pass_mem,
                        "cwd_files_written": cwd_files_written,
                    }
                    if "queries" in stage_metrics:
                        final_metrics["queries"] = stage_metrics["queries"]

                    wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 2)
                    detail = (
                        f"Communicator timed out at stage {stage}"
                        if comm_timed_out
                        else comm_stderr.strip()[:500]
                    )
                    res = JudgeResult(
                        verdict=stage_verdict,
                        cpu_time_ms=round(total_cpu_ms, 2)
                        if stage_verdict in ("AC", "PC", "WA", "PE")
                        else None,
                        wall_time_ms=wall_ms,
                        memory_mb=round(peak_mem_mb, 2),
                        stdout=pass_res.stdout,
                        stderr=pass_res.stderr,
                        judge_stdout=comm_stdout,
                        judge_stderr=comm_stderr,
                        solution_returncode=pass_res.solution_returncode,
                        judge_returncode=comm_rc,
                        score=stage_score,
                        metrics=final_metrics,
                        transcript=_format_transcript(transcript_events, max_transcript_bytes),
                        detail=detail,
                    )
                    return enforce_protocol_budgets(
                        res,
                        max_bits=max_bits,
                        max_bytes=max_bytes,
                        max_queries=max_queries,
                        max_rounds=max_rounds,
                    )

        return JudgeResult(verdict="FAIL", detail="No passes executed")  # pragma: no cover

    @classmethod
    async def run_grader_linked(
        cls,
        linked_exe: str,
        input_data: str,
        *,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 256.0,
        max_bits: int | None = None,
        max_bytes: int | None = None,
        max_queries: int | None = None,
        max_rounds: int | None = None,
        cwd: str | None = None,
    ) -> JudgeResult:
        """Execute a grader-linked communication binary in an isolated working directory."""
        abs_exe = os.path.abspath(linked_exe)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        if not os.path.isfile(abs_exe):
            return JudgeResult(
                verdict="FAIL", detail=f"Grader-linked binary not found: {linked_exe}"
            )

        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            iso_cwd = os.path.abspath(raw_tmp_dir)
            raw_res = await StandardRunner.run(
                abs_exe,
                input_data,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                cwd=iso_cwd,
            )

        if raw_res.verdict in ("TLE", "MLE", "FAIL"):
            return raw_res

        score, metrics = parse_judge_metrics(raw_res.stderr, raw_res.stdout)
        rc = raw_res.solution_returncode
        combined_msg = f"{raw_res.stderr}\n{raw_res.stdout}".strip()

        if rc == 0:
            if _EXPLICIT_WA_PREFIX_RE.search(raw_res.stderr or ""):
                verdict = "WA"
            else:
                verdict = "AC"
            detail = (raw_res.stderr or raw_res.stdout).strip()[:500]
        elif rc is not None and rc in (1, 2, 3, 4, 7, 8) and _GRADER_VERDICT_RE.search(combined_msg):
            verdict = verdict_from_testlib_rc(rc)
            detail = (raw_res.stderr or raw_res.stdout).strip()[:500]
        else:
            verdict = "RE"
            detail = f"Runtime error (exit code {rc})"

        res = JudgeResult(
            verdict=verdict,
            cpu_time_ms=(
                (raw_res.cpu_time_ms if raw_res.cpu_time_ms is not None else raw_res.wall_time_ms)
                if verdict in ("AC", "PC", "WA", "PE")
                else None
            ),
            wall_time_ms=raw_res.wall_time_ms,
            memory_mb=raw_res.memory_mb,
            stdout=raw_res.stdout,
            stderr=raw_res.stderr,
            judge_stdout=raw_res.stdout,
            judge_stderr=raw_res.stderr,
            solution_returncode=rc,
            judge_returncode=rc if verdict in ("AC", "WA", "PE", "PC", "FAIL") else None,
            score=score,
            metrics=metrics,
            detail=detail,
        )
        return enforce_protocol_budgets(
            res,
            max_bits=max_bits,
            max_bytes=max_bytes,
            max_queries=max_queries,
            max_rounds=max_rounds,
        )

    @classmethod
    async def run_multi_process(
        cls,
        manager_exe: str,
        participant_exes: list[str],
        input_data: str,
        *,
        answer_data: str = "",
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 256.0,
        deadlock_timeout_ms: float | None = None,
        max_bits: int | None = None,
        max_bytes: int | None = None,
        max_queries: int | None = None,
        max_rounds: int | None = None,
        expected_participants: int = 2,
        cwd: str | None = None,
        max_transcript_bytes: int = _MAX_TRANSCRIPT_BYTES_DEFAULT,
    ) -> JudgeResult:
        """Execute K isolated contestant processes orchestrated by a central testlib manager.

        Line-multiplexed wire protocol between Runner and Manager:
        - Manager stdout -> Runner:
          - ``TO <idx> <payload>`` -> forwards ``<payload>\\n`` to participant ``idx`` stdin
          - ``CLOSE <idx>``        -> closes participant ``idx`` stdin
        - Runner -> Manager stdin:
          - ``FROM <idx> <line>``  -> line received from participant ``idx`` stdout
          - ``EOF <idx>``          -> participant ``idx`` closed stdout
        """
        abs_mgr = os.path.abspath(manager_exe)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        if not os.path.isfile(abs_mgr):
            return JudgeResult(
                verdict="FAIL", detail=f"Manager/communicator binary not found: {manager_exe}"
            )
        if not participant_exes:
            return JudgeResult(verdict="FAIL", detail="No participant binaries provided")
        abs_parts = [os.path.abspath(p) for p in participant_exes]
        if len(abs_parts) == 1:
            # Expand a single binary (e.g. sol or wrong solution) to K participants
            abs_parts = [abs_parts[0]] * max(2, int(expected_participants))
        for part_path in abs_parts:
            if not os.path.isfile(part_path):
                return JudgeResult(
                    verdict="FAIL", detail=f"Participant binary not found: {part_path}"
                )

        num_procs = len(abs_parts)
        effective_tl_ms = max(100.0, float(time_limit_ms))
        effective_mem_mb = max(16.0, float(memory_limit_mb))
        deadlock_sec = (
            max(0.2, float(deadlock_timeout_ms) / 1000.0)
            if deadlock_timeout_ms is not None
            else min(max(1.0, effective_tl_ms / 1000.0), 5.0)
        )
        wall_cap_sec = max(effective_tl_ms / 1000.0 * 3.0, effective_tl_ms / 1000.0 + 3.0)

        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            tmp_dir = os.path.abspath(raw_tmp_dir)
            in_path = os.path.join(tmp_dir, "input.in")
            out_path = os.path.join(tmp_dir, "output.txt")
            ans_path = os.path.join(tmp_dir, "answer.ans")
            with open(in_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(input_data)
            with open(ans_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(answer_data)

            popen_kwargs: dict[str, Any] = {"limit": _STREAM_LIMIT}
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True
                popen_kwargs["preexec_fn"] = disable_posix_core_dumps

            mgr_env = dict(os.environ)
            mgr_env["AUTOCODE_COMM_MODE"] = "multi_process"
            mgr_env["AUTOCODE_COMM_PROCS"] = str(num_procs)

            wall_start = time.perf_counter()
            mgr_proc = await asyncio.create_subprocess_exec(
                abs_mgr,
                in_path,
                out_path,
                ans_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=tmp_dir,
                env=mgr_env,
                **popen_kwargs,
            )

            part_procs: list[asyncio.subprocess.Process] = []
            try:
                for idx, part_exe in enumerate(abs_parts):
                    part_cwd = os.path.join(tmp_dir, f"proc_{idx}_cwd")
                    os.makedirs(part_cwd, exist_ok=True)
                    part_env = dict(os.environ)
                    part_env["AUTOCODE_PARTICIPANT_ID"] = str(idx)
                    part_env["AUTOCODE_PARTICIPANT_COUNT"] = str(num_procs)
                    p_proc = await asyncio.create_subprocess_exec(
                        part_exe,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=part_cwd,
                        env=part_env,
                        **popen_kwargs,
                    )
                    part_procs.append(p_proc)
            except BaseException:
                for p_proc in part_procs:
                    await terminate_pid_tree(p_proc.pid)
                await terminate_pid_tree(mgr_proc.pid)
                for p_proc in [*part_procs, mgr_proc]:
                    try:
                        await asyncio.wait_for(p_proc.wait(), timeout=1.0)
                    except Exception:
                        pass
                    transport = getattr(p_proc, "_transport", None)
                    if transport is not None:
                        try:
                            transport.close()
                        except Exception:
                            pass
                raise

            try:
                mgr_ps: psutil.Process | None = psutil.Process(mgr_proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                mgr_ps = None
            part_ps_list: list[psutil.Process | None] = []
            for p_proc in part_procs:
                try:
                    part_ps_list.append(psutil.Process(p_proc.pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    part_ps_list.append(None)

            peak_memory_mb = 0.0
            part_cpu_ms = [0.0] * num_procs
            prev_part_cpu = [0.0] * num_procs
            prev_mgr_cpu = 0.0
            last_activity_ts = time.perf_counter()
            measured_bytes = 0
            measured_bits = 0
            message_rounds = 0
            transcript_events: list[tuple[str, bytes]] = []
            transcript_budget = max_transcript_bytes
            abort_reason: str | None = None
            abort_event = asyncio.Event()
            mgr_stdin_lock = asyncio.Lock()
            mgr_stdin_open = True

            def _record(role: str, raw: bytes) -> None:
                nonlocal transcript_budget
                if transcript_budget <= 0:
                    return
                take = min(len(raw), transcript_budget)
                transcript_events.append((role, raw[:take]))
                transcript_budget -= take

            async def _send_to_manager(line: bytes) -> None:
                nonlocal mgr_stdin_open
                if not mgr_stdin_open or mgr_proc.stdin is None:
                    return
                async with mgr_stdin_lock:
                    if not mgr_stdin_open or mgr_proc.stdin is None:
                        return
                    try:
                        mgr_proc.stdin.write(line)
                        await mgr_proc.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        mgr_stdin_open = False
                        try:
                            mgr_proc.stdin.close()
                        except Exception:
                            pass

            manager_to_sent = 0

            async def _pump_manager() -> None:
                nonlocal last_activity_ts, manager_to_sent, abort_reason
                reader = mgr_proc.stdout
                if reader is None:
                    return
                closed_parts: set[int] = set()
                try:
                    while True:
                        raw_line = await reader.readline()
                        if not raw_line:
                            break
                        last_activity_ts = time.perf_counter()
                        line = raw_line.rstrip(b"\r\n").decode("utf-8", errors="replace")
                        if line.startswith("TO "):
                            rest = line[3:]
                            parts = rest.split(" ", 1)
                            try:
                                target = int(parts[0])
                            except ValueError:
                                continue
                            payload = parts[1] if len(parts) > 1 else ""
                            if 0 <= target < num_procs and target not in closed_parts:
                                manager_to_sent += 1
                                _record(f"manager->p{target}", payload.encode("utf-8"))
                                target_stdin = part_procs[target].stdin
                                if target_stdin is not None:
                                    try:
                                        target_stdin.write((payload + "\n").encode("utf-8"))
                                        await target_stdin.drain()
                                    except (BrokenPipeError, ConnectionResetError, OSError):
                                        closed_parts.add(target)
                                        try:
                                            target_stdin.close()
                                        except Exception:
                                            pass
                        elif line.startswith("CLOSE "):
                            try:
                                target = int(line[6:].strip())
                            except ValueError:
                                continue
                            if 0 <= target < num_procs and target not in closed_parts:
                                closed_parts.add(target)
                                target_stdin = part_procs[target].stdin
                                if target_stdin is not None:
                                    try:
                                        target_stdin.close()
                                    except Exception:
                                        pass
                except ValueError:
                    abort_reason = abort_reason or "MANAGER_LINE_TOO_LONG"
                    abort_event.set()
                except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError, OSError):
                    pass
                finally:
                    for idx in range(num_procs):
                        if idx not in closed_parts and part_procs[idx].stdin is not None:
                            try:
                                part_procs[idx].stdin.close()  # type: ignore[union-attr]
                            except Exception:
                                pass

            async def _pump_participant(idx: int) -> None:
                nonlocal last_activity_ts, measured_bytes, measured_bits, message_rounds
                nonlocal abort_reason
                reader = part_procs[idx].stdout
                if reader is None:
                    await _send_to_manager(f"EOF {idx}\n".encode())
                    return
                send_eof = True
                try:
                    while True:
                        raw_line = await reader.readline()
                        if not raw_line:
                            break
                        last_activity_ts = time.perf_counter()
                        stripped = raw_line.rstrip(b"\r\n")
                        text = stripped.decode("utf-8", errors="replace")
                        b_bits, b_bytes = measure_payload_bits_bytes(text)
                        measured_bits += b_bits
                        measured_bytes += b_bytes
                        message_rounds += 1
                        _record(f"p{idx}->manager", stripped)
                        await _send_to_manager(f"FROM {idx} {text}\n".encode())
                except asyncio.CancelledError:
                    send_eof = False
                    raise
                except ValueError:
                    send_eof = False
                    abort_reason = abort_reason or f"PARTICIPANT_LINE_TOO_LONG:{idx}"
                    abort_event.set()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    if send_eof:
                        await _send_to_manager(f"EOF {idx}\n".encode())

            async def _monitor() -> None:
                nonlocal peak_memory_mb, prev_mgr_cpu, last_activity_ts, abort_reason
                while mgr_proc.returncode is None or any(
                    p.returncode is None for p in part_procs
                ):
                    now = time.perf_counter()
                    all_alive_sampled = True
                    any_part_alive = False

                    for idx, p_proc in enumerate(part_procs):
                        if p_proc.returncode is not None:
                            continue
                        any_part_alive = True
                        ps = part_ps_list[idx]
                        if ps is None:
                            all_alive_sampled = False
                            continue
                        try:
                            with ps.oneshot():
                                mem = ps.memory_info().rss / (1024.0 * 1024.0)
                                if mem > peak_memory_mb:
                                    peak_memory_mb = mem
                                times = ps.cpu_times()
                                cpu = (times.user + times.system) * 1000.0
                                if cpu > part_cpu_ms[idx]:
                                    part_cpu_ms[idx] = cpu
                                if cpu - prev_part_cpu[idx] >= 0.5:
                                    prev_part_cpu[idx] = cpu
                                    last_activity_ts = now
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            all_alive_sampled = False

                    if mgr_proc.returncode is None:
                        if mgr_ps is None:
                            all_alive_sampled = False
                        else:
                            try:
                                m_times = mgr_ps.cpu_times()
                                m_cpu = (m_times.user + m_times.system) * 1000.0
                                if m_cpu - prev_mgr_cpu >= 0.5:
                                    prev_mgr_cpu = m_cpu
                                    last_activity_ts = now
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                all_alive_sampled = False

                    total_part_cpu = sum(part_cpu_ms)
                    if peak_memory_mb > effective_mem_mb:
                        abort_reason = "MLE"
                        abort_event.set()
                        return
                    if total_part_cpu > effective_tl_ms:
                        abort_reason = "TLE"
                        abort_event.set()
                        return
                    if (
                        mgr_proc.returncode is None
                        and any_part_alive
                        and all_alive_sampled
                        and (now - last_activity_ts) >= deadlock_sec
                    ):
                        abort_reason = "DEADLOCK"
                        abort_event.set()
                        return
                    if any_part_alive and (now - wall_start) >= wall_cap_sec:
                        abort_reason = "TLE"
                        abort_event.set()
                        return
                    await asyncio.sleep(0.005)

            pump_mgr_task = asyncio.create_task(_pump_manager())
            pump_part_tasks = [asyncio.create_task(_pump_participant(i)) for i in range(num_procs)]
            err_mgr_task = asyncio.create_task(_read_bounded(mgr_proc.stderr, _MAX_STDERR_BYTES))
            err_part_tasks = [
                asyncio.create_task(_read_bounded(p.stderr, _MAX_STDERR_BYTES))
                for p in part_procs
            ]
            monitor_task = asyncio.create_task(_monitor())

            async def _wait_all_parts() -> None:
                await asyncio.gather(*(p.wait() for p in part_procs))

            wait_mgr = asyncio.create_task(mgr_proc.wait())
            wait_parts = asyncio.create_task(_wait_all_parts())
            wait_abort = asyncio.create_task(abort_event.wait())

            parts_exited_first = False
            killed_by_cleanup: set[int] = set()
            abnormal_exit = False

            try:
                done, _ = await asyncio.wait(
                    {wait_mgr, wait_parts, wait_abort},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if wait_abort in done:
                    for p in part_procs:
                        await terminate_pid_tree(p.pid)
                    await terminate_pid_tree(mgr_proc.pid)
                elif wait_mgr in done:
                    if not monitor_task.done():
                        monitor_task.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(pump_mgr_task), timeout=0.1)
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                    if any(p.returncode is None for p in part_procs):
                        try:
                            await asyncio.wait_for(wait_parts, timeout=0.5)
                        except (asyncio.TimeoutError, TimeoutError):
                            for idx, p in enumerate(part_procs):
                                if p.returncode is None:
                                    killed_by_cleanup.add(idx)
                                    await terminate_pid_tree(p.pid)
                elif wait_parts in done:
                    parts_exited_first = True
                    remaining = max(3.0, wall_cap_sec - (time.perf_counter() - wall_start))
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pump_part_tasks, return_exceptions=True),
                            timeout=min(remaining, 2.0),
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                    async with mgr_stdin_lock:
                        mgr_stdin_open = False
                        if mgr_proc.stdin is not None:
                            try:
                                mgr_proc.stdin.close()
                            except Exception:
                                pass
                    try:
                        await asyncio.wait_for(wait_mgr, timeout=remaining)
                    except (asyncio.TimeoutError, TimeoutError):
                        abort_reason = abort_reason or "MANAGER_HANG"
                        await terminate_pid_tree(mgr_proc.pid)
            except BaseException:
                abnormal_exit = True
                for p in part_procs:
                    await terminate_pid_tree(p.pid)
                await terminate_pid_tree(mgr_proc.pid)
                raise
            finally:
                for ctrl_task in (monitor_task, wait_abort):
                    if not ctrl_task.done():
                        ctrl_task.cancel()
                if mgr_proc.returncode is None:
                    await terminate_pid_tree(mgr_proc.pid)
                for p in part_procs:
                    if p.returncode is None:
                        await terminate_pid_tree(p.pid)
                for proc in [mgr_proc, *part_procs]:
                    if proc.returncode is None:
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                bg_tasks: list[asyncio.Task[Any]] = [
                    pump_mgr_task,
                    *pump_part_tasks,
                    wait_mgr,
                    wait_parts,
                ]
                for bt in bg_tasks:
                    if not bt.done():
                        bt.cancel()
                cleanup_tasks: list[asyncio.Task[Any]] = [monitor_task, wait_abort, *bg_tasks]
                if abnormal_exit:
                    for et in [err_mgr_task, *err_part_tasks]:
                        if not et.done():
                            et.cancel()
                    cleanup_tasks.extend([err_mgr_task, *err_part_tasks])
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                if abnormal_exit:
                    for proc in [mgr_proc, *part_procs]:
                        transport = getattr(proc, "_transport", None)
                        if transport is not None:
                            try:
                                transport.close()
                            except Exception:
                                pass

            wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 2)
            try:
                mgr_err_bytes = await asyncio.wait_for(err_mgr_task, timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError):
                err_mgr_task.cancel()
                await asyncio.gather(err_mgr_task, return_exceptions=True)
                mgr_err_bytes = b""

            part_stderrs: list[str] = []
            for et in err_part_tasks:
                try:
                    eb = await asyncio.wait_for(et, timeout=1.0)
                except (asyncio.TimeoutError, TimeoutError):
                    et.cancel()
                    await asyncio.gather(et, return_exceptions=True)
                    eb = b""
                part_stderrs.append(eb.decode("utf-8", errors="replace"))

            judge_stderr = mgr_err_bytes.decode("utf-8", errors="replace")
            judge_output = ""
            if os.path.isfile(out_path):
                try:
                    with open(out_path, encoding="utf-8", errors="replace") as f:
                        judge_output = f.read(_MAX_STDERR_BYTES)
                except OSError:
                    pass

            score, metrics = parse_judge_metrics(judge_stderr, judge_output)
            metrics.setdefault("bits_used", measured_bits)
            metrics.setdefault("bytes_used", measured_bytes)
            metrics.setdefault("rounds", message_rounds)
            metrics["participants"] = num_procs
            metrics["per_participant_cpu_ms"] = [round(c, 2) for c in part_cpu_ms]

            total_cpu_ms = sum(part_cpu_ms)
            final_cpu_ms = (
                round(total_cpu_ms, 2)
                if total_cpu_ms > 0.0
                else round(min(wall_ms, effective_tl_ms), 2)
            )
            mgr_rc = mgr_proc.returncode
            part_rcs = [p.returncode for p in part_procs]
            bad_part_rcs = [
                (i, rc)
                for i, rc in enumerate(part_rcs)
                if rc is not None and rc != 0 and not _is_harmless_sigpipe(rc)
            ]

            if abort_reason == "MLE":
                verdict = "MLE"
                detail = (
                    f"Participant memory limit exceeded ({peak_memory_mb:.1f}MB > {effective_mem_mb:.1f}MB)"
                )
            elif abort_reason == "DEADLOCK":
                verdict = "DEADLOCK"
                if manager_to_sent == 0:
                    detail = (
                        f"Multi-process protocol deadlock: manager sent 0 TO commands for {deadlock_sec:.1f}s "
                        "(ensure manager writes 'TO <idx> <payload>' to std::cout and flushes)"
                    )
                else:
                    detail = (
                        f"Multi-process protocol deadlock: no traffic or CPU progress for {deadlock_sec:.1f}s"
                    )
            elif abort_reason == "TLE":
                verdict = "TLE"
                detail = f"Time limit exceeded (cpu={final_cpu_ms}ms, wall={wall_ms}ms)"
            elif abort_reason == "MANAGER_HANG":
                verdict = "FAIL"
                detail = "Manager did not terminate after all participants exited"
            elif abort_reason == "MANAGER_LINE_TOO_LONG":
                verdict = "FAIL"
                detail = f"Manager output line exceeded {_STREAM_LIMIT} bytes without newline"
            elif abort_reason and abort_reason.startswith("PARTICIPANT_LINE_TOO_LONG:"):
                verdict = "PE"
                p_idx = abort_reason.split(":", 1)[1]
                detail = (
                    f"Participant p{p_idx} output line exceeded {_STREAM_LIMIT} bytes without newline"
                )
            else:
                raw_verdict = verdict_from_testlib_rc(mgr_rc)
                verdict = raw_verdict
                detail = judge_stderr.strip()[:500]
                genuine_bad_parts = [
                    (i, rc) for i, rc in bad_part_rcs if i not in killed_by_cleanup
                ]
                active_bad_parts = (
                    bad_part_rcs if raw_verdict == "AC" else genuine_bad_parts
                )
                if (
                    active_bad_parts
                    and raw_verdict != "FAIL"
                    and (
                        raw_verdict in ("AC", "PC", "PE")
                        or any(rc < 0 for _, rc in active_bad_parts)
                        or parts_exited_first
                    )
                ):
                    idx0, rc0 = active_bad_parts[0]
                    verdict = "RE"
                    extra_msg = f": {judge_stderr.strip()[:300]}" if judge_stderr.strip() else ""
                    detail = (
                        f"Participant p{idx0} exited with non-zero code {rc0} "
                        f"(judge={raw_verdict}){extra_msg}"
                    )

            res = JudgeResult(
                verdict=verdict,
                cpu_time_ms=final_cpu_ms if verdict in ("AC", "PC", "WA", "PE") else None,
                wall_time_ms=wall_ms,
                memory_mb=round(peak_memory_mb, 2),
                stdout="",
                stderr="\n".join(s for s in part_stderrs if s.strip()),
                judge_stdout=judge_output,
                judge_stderr=judge_stderr,
                solution_returncode=bad_part_rcs[0][1] if bad_part_rcs else 0,
                judge_returncode=mgr_rc,
                score=score,
                metrics=metrics,
                transcript=_format_transcript(transcript_events, max_transcript_bytes),
                detail=detail,
            )
            return enforce_protocol_budgets(
                res,
                max_bits=max_bits,
                max_bytes=max_bytes,
                max_queries=max_queries,
                max_rounds=max_rounds,
            )

    @classmethod
    async def run_from_manifest(
        cls,
        problem_dir: str,
        manifest: AutoCodeManifest,
        solution_exe: str,
        input_data: str,
        *,
        answer_data: str = "",
        time_limit_ms: float | None = None,
        memory_limit_mb: float | None = None,
        deadlock_timeout_ms: float | None = None,
        use_system_temp: bool = False,
        communicator_exe_override: str | None = None,
    ) -> JudgeResult:
        """Dispatch communication execution according to ``manifest.protocol.mode``."""
        protocol = manifest.protocol
        mode = protocol.mode if protocol is not None else "run_twice"
        run_cwd = None if use_system_temp else problem_dir
        tl_ms = (
            float(time_limit_ms)
            if time_limit_ms is not None
            else float(manifest.time_limit_ms if manifest.time_limit_ms > 0 else 2000)
        )
        mem_mb = (
            float(memory_limit_mb)
            if memory_limit_mb is not None
            else float(manifest.memory_limit_mb if manifest.memory_limit_mb > 0 else 256)
        )
        max_bits = protocol.max_bits if protocol else None
        max_bytes = protocol.max_bytes if protocol else None
        max_queries = protocol.max_queries if protocol else None
        max_rounds = protocol.max_rounds if protocol else None

        if mode == "grader_linked":
            return await cls.run_grader_linked(
                linked_exe=solution_exe,
                input_data=input_data,
                time_limit_ms=tl_ms,
                memory_limit_mb=mem_mb,
                max_bits=max_bits,
                max_bytes=max_bytes,
                max_queries=max_queries,
                max_rounds=max_rounds,
                cwd=run_cwd,
            )

        comm_bin = (
            communicator_exe_override
            if (communicator_exe_override and os.path.isfile(communicator_exe_override))
            else resolve_communicator_binary(problem_dir, manifest)
        )
        if not comm_bin:
            return JudgeResult(
                verdict="FAIL",
                detail="Communicator binary not found. Run interactor_build first.",
            )

        part_exes = resolve_participant_binaries(problem_dir, manifest, solution_exe)

        if mode == "multi_process":
            expected_parts = max(
                2,
                len(protocol.participant_solutions) if protocol else 2,
            )
            return await cls.run_multi_process(
                manager_exe=comm_bin,
                participant_exes=part_exes,
                input_data=input_data,
                answer_data=answer_data,
                time_limit_ms=tl_ms,
                memory_limit_mb=mem_mb,
                deadlock_timeout_ms=deadlock_timeout_ms,
                max_bits=max_bits,
                max_bytes=max_bytes,
                max_queries=max_queries,
                max_rounds=max_rounds,
                expected_participants=expected_parts,
                cwd=run_cwd,
            )

        # Default: run_twice
        passes = protocol.passes if protocol else 2
        pass_headers = list(protocol.pass_headers) if protocol else ["first", "second"]
        return await cls.run_twice(
            communicator_exe=comm_bin,
            solution_exes=part_exes,
            input_data=input_data,
            answer_data=answer_data,
            passes=passes,
            pass_headers=pass_headers,
            time_limit_ms=tl_ms,
            memory_limit_mb=mem_mb,
            max_bits=max_bits,
            max_bytes=max_bytes,
            max_queries=max_queries,
            max_rounds=max_rounds,
            cwd=run_cwd,
        )
