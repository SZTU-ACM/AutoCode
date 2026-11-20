"""Unified deterministic judging and execution engine for AutoCode.

Supports:
- Standard stdio solutions (``StandardRunner``)
- Special Judge / testlib checker solutions (``CheckerRunner``)
- Interactive problems via testlib ``registerInteraction(argc, argv)``
  (``InteractiveRunner``) with solution-only CPU/memory tracking, dual-process
  protocol deadlock detection, crash-to-RE promotion, and bounded chronological
  transcript recording.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import psutil

from .checker_judge import run_testlib_checker, verdict_from_testlib_rc
from .execution_monitor import DynamicExecutionMonitor
from .process import terminate_pid_tree

__all__ = [
    "CheckerRunner",
    "InteractiveRunner",
    "JudgeResult",
    "StandardRunner",
    "parse_judge_metrics",
    "verdict_from_testlib_rc",
]

_MAX_STDERR_BYTES = 1024 * 1024  # 1 MiB cap per stderr stream
_MAX_TRANSCRIPT_BYTES_DEFAULT = 256 * 1024  # 256 KiB cap for recorded transcripts

_METRIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("queries", re.compile(r"\bqueries\s*[=:]\s*(\d+)", re.IGNORECASE)),
    ("bits_used", re.compile(r"\b(?:bits_used|bits)\s*[=:]\s*(\d+)", re.IGNORECASE)),
    ("bytes_used", re.compile(r"\b(?:bytes_used|bytes)\s*[=:]\s*(\d+)", re.IGNORECASE)),
    ("rounds", re.compile(r"\brounds\s*[=:]\s*(\d+)", re.IGNORECASE)),
)
_SCORE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:score|points)\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE),
    re.compile(r"\bpoints\s+(-?\d+(?:\.\d+)?)\b", re.IGNORECASE),
    re.compile(r"partially\s+correct\s*\((-?\d+(?:\.\d+)?)\)", re.IGNORECASE),
)


@dataclass
class JudgeResult:
    """Unified evaluation outcome returned by all runners."""

    verdict: str  # AC | WA | PE | PC | TLE | MLE | RE | FAIL | DEADLOCK
    cpu_time_ms: float | None = None
    wall_time_ms: float = 0.0
    memory_mb: float = 0.0
    stdout: str = ""
    stderr: str = ""
    judge_stdout: str = ""
    judge_stderr: str = ""
    solution_returncode: int | None = None
    judge_returncode: int | None = None
    score: float | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    transcript: str = ""
    detail: str = ""

    @property
    def accepted(self) -> bool:
        return self.verdict == "AC"


def parse_judge_metrics(
    judge_stderr: str, judge_output: str = ""
) -> tuple[float | None, dict[str, Any]]:
    """Extract optional score and structured protocol metrics (queries/bits/bytes/rounds)."""
    combined = f"{judge_stderr}\n{judge_output}"
    metrics: dict[str, Any] = {}
    for key, pattern in _METRIC_PATTERNS:
        match = pattern.search(combined)
        if match:
            try:
                metrics[key] = int(match.group(1))
            except ValueError:
                pass

    score: float | None = None
    for score_pat in _SCORE_PATTERNS:
        match = score_pat.search(combined)
        if match:
            try:
                score = float(match.group(1))
                break
            except ValueError:
                pass
    return score, metrics


def _is_harmless_sigpipe(rc: int | None) -> bool:
    if rc is None:
        return False
    sigpipe = getattr(signal, "SIGPIPE", None)
    if sigpipe is not None and rc == -int(sigpipe):
        return True
    return rc == 141


async def _read_bounded(reader: asyncio.StreamReader | None, limit: int) -> bytes:
    if reader is None:
        return b""
    chunks: list[bytes] = []
    collected = 0
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            if collected < limit:
                take = min(len(chunk), limit - collected)
                chunks.append(chunk[:take])
                collected += take
    except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError, OSError):
        pass
    return b"".join(chunks)


def _format_transcript(events: list[tuple[str, bytes]], max_bytes: int) -> str:
    """Format chronological pipe events into a readable bounded transcript."""
    lines: list[str] = []
    used = 0
    truncated = False
    for role, payload in events:
        text = payload.decode("utf-8", errors="replace")
        for line in text.splitlines():
            entry = f"[{role}] {line}"
            entry_bytes = len(entry.encode("utf-8")) + 1
            if used + entry_bytes > max_bytes:
                truncated = True
                break
            lines.append(entry)
            used += entry_bytes
        if truncated:
            break
    if truncated:
        lines.append("[transcript truncated]")
    return "\n".join(lines)


class StandardRunner:
    """Run a standard stdio solution and optionally compare exact output."""

    @classmethod
    async def run(
        cls,
        solution_exe: str,
        input_data: str,
        *,
        expected_output: str | None = None,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 256.0,
        cwd: str | None = None,
        baseline_overhead_ms: float = 0.0,
    ) -> JudgeResult:
        abs_sol = os.path.abspath(solution_exe)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        if not os.path.isfile(abs_sol):
            return JudgeResult(verdict="FAIL", detail=f"Solution binary not found: {solution_exe}")

        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            tmp_dir = os.path.abspath(raw_tmp_dir)
            in_path = os.path.join(tmp_dir, "input.in")
            with open(in_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(input_data)

            res = await DynamicExecutionMonitor.run_monitored_process(
                [abs_sol],
                in_path,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                cwd=abs_cwd,
                baseline_overhead_ms=baseline_overhead_ms,
            )

        status = str(res.get("status", "runtime_error"))
        stdout = str(res.get("stdout", ""))
        stderr = str(res.get("stderr", ""))
        cpu_ms = res.get("cpu_time_ms")
        wall_ms = float(res.get("wall_time_ms") or 0.0)
        mem_mb = float(res.get("memory_mb") or 0.0)
        rc = res.get("returncode")

        if status == "timeout":
            return JudgeResult(
                verdict="TLE",
                cpu_time_ms=cpu_ms,
                wall_time_ms=wall_ms,
                memory_mb=mem_mb,
                stdout=stdout,
                stderr=stderr,
                solution_returncode=rc,
                detail=f"Time limit exceeded ({time_limit_ms}ms)",
            )
        if status == "mle":
            return JudgeResult(
                verdict="MLE",
                cpu_time_ms=cpu_ms,
                wall_time_ms=wall_ms,
                memory_mb=mem_mb,
                stdout=stdout,
                stderr=stderr,
                solution_returncode=rc,
                detail=f"Memory limit exceeded ({memory_limit_mb}MB)",
            )
        if status != "ok":
            return JudgeResult(
                verdict="RE",
                cpu_time_ms=cpu_ms,
                wall_time_ms=wall_ms,
                memory_mb=mem_mb,
                stdout=stdout,
                stderr=stderr,
                solution_returncode=rc,
                detail=f"Runtime error (exit code {rc})",
            )

        if expected_output is not None:
            actual_norm = stdout.replace("\r\n", "\n").strip()
            expected_norm = expected_output.replace("\r\n", "\n").strip()
            if actual_norm != expected_norm:
                return JudgeResult(
                    verdict="WA",
                    cpu_time_ms=cpu_ms,
                    wall_time_ms=wall_ms,
                    memory_mb=mem_mb,
                    stdout=stdout,
                    stderr=stderr,
                    solution_returncode=rc,
                    detail="Output mismatch",
                )

        return JudgeResult(
            verdict="AC",
            cpu_time_ms=cpu_ms,
            wall_time_ms=wall_ms,
            memory_mb=mem_mb,
            stdout=stdout,
            stderr=stderr,
            solution_returncode=rc,
        )


class CheckerRunner:
    """Run a solution and grade its output with a testlib checker."""

    @classmethod
    async def run(
        cls,
        checker_exe: str,
        solution_exe: str,
        input_data: str,
        answer_data: str,
        *,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 256.0,
        checker_timeout_sec: int = 30,
        cwd: str | None = None,
    ) -> JudgeResult:
        abs_chk = os.path.abspath(checker_exe)
        abs_sol = os.path.abspath(solution_exe)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        if not os.path.isfile(abs_chk):
            return JudgeResult(verdict="FAIL", detail=f"Checker binary not found: {checker_exe}")

        sol_res = await StandardRunner.run(
            abs_sol,
            input_data,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            cwd=abs_cwd,
        )
        if not sol_res.accepted:
            return sol_res

        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            tmp_dir = os.path.abspath(raw_tmp_dir)
            in_path = os.path.join(tmp_dir, "input.in")
            out_path = os.path.join(tmp_dir, "output.out")
            ans_path = os.path.join(tmp_dir, "answer.ans")
            with open(in_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(input_data)
            with open(out_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(sol_res.stdout)
            with open(ans_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(answer_data)

            verdict, chk_run = await run_testlib_checker(
                abs_chk,
                in_path,
                out_path,
                ans_path,
                timeout=checker_timeout_sec,
            )

        score, metrics = parse_judge_metrics(chk_run.stderr or "", chk_run.stdout or "")
        return JudgeResult(
            verdict=verdict,
            cpu_time_ms=sol_res.cpu_time_ms,
            wall_time_ms=sol_res.wall_time_ms,
            memory_mb=sol_res.memory_mb,
            stdout=sol_res.stdout,
            stderr=sol_res.stderr,
            judge_stdout=chk_run.stdout or "",
            judge_stderr=chk_run.stderr or "",
            solution_returncode=sol_res.solution_returncode,
            judge_returncode=chk_run.return_code,
            score=score,
            metrics=metrics,
            detail=(chk_run.stderr or chk_run.error or "").strip()[:500],
        )


class InteractiveRunner:
    """Bidirectional testlib ``registerInteraction(argc, argv)`` execution engine.

    Follows the canonical Polygon / testlib.h interaction contract:
    - ``argv`` passed to interactor: ``[interactor_exe, input_path, output_path, answer_path]``
    - ``interactor.stdout`` (``std::cout``) -> ``solution.stdin``
    - ``solution.stdout`` -> ``interactor.stdin`` (``ouf``)
    - ``output_path`` (``tout``) is captured as ``judge_stdout``
    - Measures solution-only CPU time & peak RSS memory, detects protocol deadlocks
      (both processes alive and idle with zero pipe traffic and negligible CPU progress
      in both processes), promotes contestant crashes to ``RE``, and records a bounded
      chronological transcript.
    """

    @classmethod
    async def run(
        cls,
        interactor_exe: str,
        solution_exe: str,
        input_data: str,
        *,
        answer_data: str = "",
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 256.0,
        deadlock_timeout_ms: float | None = None,
        cwd: str | None = None,
        max_transcript_bytes: int = _MAX_TRANSCRIPT_BYTES_DEFAULT,
    ) -> JudgeResult:
        abs_int = os.path.abspath(interactor_exe)
        abs_sol = os.path.abspath(solution_exe)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        if not os.path.isfile(abs_int):
            return JudgeResult(
                verdict="FAIL", detail=f"Interactor binary not found: {interactor_exe}"
            )
        if not os.path.isfile(abs_sol):
            return JudgeResult(
                verdict="FAIL", detail=f"Solution binary not found: {solution_exe}"
            )

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

            popen_kwargs: dict[str, Any] = {}
            if sys.platform != "win32":
                popen_kwargs["start_new_session"] = True

            wall_start = time.perf_counter()
            interactor_proc = await asyncio.create_subprocess_exec(
                abs_int,
                in_path,
                out_path,
                ans_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=abs_cwd,
                **popen_kwargs,
            )
            try:
                solution_proc = await asyncio.create_subprocess_exec(
                    abs_sol,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=abs_cwd,
                    **popen_kwargs,
                )
            except BaseException:
                await terminate_pid_tree(interactor_proc.pid)
                try:
                    await asyncio.wait_for(interactor_proc.wait(), timeout=1.0)
                except Exception:
                    pass
                raise

            sol_pid = solution_proc.pid
            int_pid = interactor_proc.pid

            try:
                sol_ps: psutil.Process | None = psutil.Process(sol_pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                sol_ps = None
            try:
                int_ps: psutil.Process | None = psutil.Process(int_pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                int_ps = None

            peak_memory_mb = 0.0
            accumulated_cpu_ms = 0.0
            prev_sol_cpu_ms = 0.0
            prev_int_cpu_ms = 0.0
            last_activity_ts = time.perf_counter()
            judge_bytes_sent = 0
            contestant_bytes_sent = 0
            transcript_events: list[tuple[str, bytes]] = []
            transcript_budget = max_transcript_bytes
            abort_reason: str | None = None
            abort_event = asyncio.Event()

            async def _pump(
                reader: asyncio.StreamReader | None,
                writer: asyncio.StreamWriter | None,
                role: str,
            ) -> None:
                nonlocal last_activity_ts, judge_bytes_sent, contestant_bytes_sent, transcript_budget
                if reader is None:
                    return
                writer_open = writer is not None
                try:
                    while True:
                        chunk = await reader.read(4096)
                        if not chunk:
                            break
                        last_activity_ts = time.perf_counter()
                        if role == "judge":
                            judge_bytes_sent += len(chunk)
                        else:
                            contestant_bytes_sent += len(chunk)
                        if transcript_budget > 0:
                            take = min(len(chunk), transcript_budget)
                            transcript_events.append((role, chunk[:take]))
                            transcript_budget -= take
                        if writer_open and writer is not None:
                            try:
                                writer.write(chunk)
                                await writer.drain()
                            except (BrokenPipeError, ConnectionResetError, OSError):
                                writer_open = False
                                try:
                                    writer.close()
                                except Exception:
                                    pass
                except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError, OSError):
                    pass
                finally:
                    if writer_open and writer is not None:
                        try:
                            writer.close()
                        except Exception:
                            pass

            async def _monitor() -> None:
                nonlocal peak_memory_mb, accumulated_cpu_ms, prev_sol_cpu_ms, prev_int_cpu_ms
                nonlocal last_activity_ts, abort_reason
                while solution_proc.returncode is None or interactor_proc.returncode is None:
                    now = time.perf_counter()
                    sol_sampled = False
                    int_sampled = False
                    if sol_ps is not None and solution_proc.returncode is None:
                        try:
                            with sol_ps.oneshot():
                                mem = sol_ps.memory_info().rss / (1024.0 * 1024.0)
                                if mem > peak_memory_mb:
                                    peak_memory_mb = mem
                                sol_times = sol_ps.cpu_times()
                                cpu = (sol_times.user + sol_times.system) * 1000.0
                                if cpu > accumulated_cpu_ms:
                                    accumulated_cpu_ms = cpu
                                if cpu - prev_sol_cpu_ms >= 0.5:
                                    prev_sol_cpu_ms = cpu
                                    last_activity_ts = now
                                sol_sampled = True
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass

                    if int_ps is not None and interactor_proc.returncode is None:
                        try:
                            int_times = int_ps.cpu_times()
                            int_cpu = (int_times.user + int_times.system) * 1000.0
                            if int_cpu - prev_int_cpu_ms >= 0.5:
                                prev_int_cpu_ms = int_cpu
                                last_activity_ts = now
                            int_sampled = True
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass

                    if peak_memory_mb > effective_mem_mb:
                        abort_reason = "MLE"
                        abort_event.set()
                        return
                    if accumulated_cpu_ms > effective_tl_ms:
                        abort_reason = "TLE"
                        abort_event.set()
                        return
                    if (
                        solution_proc.returncode is None
                        and interactor_proc.returncode is None
                        and sol_sampled
                        and int_sampled
                        and (now - last_activity_ts) >= deadlock_sec
                    ):
                        abort_reason = "DEADLOCK"
                        abort_event.set()
                        return
                    if solution_proc.returncode is None and (now - wall_start) >= wall_cap_sec:
                        abort_reason = "TLE"
                        abort_event.set()
                        return
                    await asyncio.sleep(0.005)

            pump_j2s = asyncio.create_task(
                _pump(interactor_proc.stdout, solution_proc.stdin, "judge")
            )
            pump_s2j = asyncio.create_task(
                _pump(solution_proc.stdout, interactor_proc.stdin, "contestant")
            )
            err_int_task = asyncio.create_task(
                _read_bounded(interactor_proc.stderr, _MAX_STDERR_BYTES)
            )
            err_sol_task = asyncio.create_task(
                _read_bounded(solution_proc.stderr, _MAX_STDERR_BYTES)
            )
            monitor_task = asyncio.create_task(_monitor())

            wait_int = asyncio.create_task(interactor_proc.wait())
            wait_sol = asyncio.create_task(solution_proc.wait())
            wait_abort = asyncio.create_task(abort_event.wait())

            sol_exited_first = False
            sol_killed_by_cleanup = False
            abnormal_exit = False

            try:
                done, _ = await asyncio.wait(
                    {wait_int, wait_sol, wait_abort},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if wait_abort in done:
                    await terminate_pid_tree(sol_pid)
                    await terminate_pid_tree(int_pid)
                elif wait_int in done:
                    # Interactor finished judging; stop monitor to prevent wall-cap races
                    if not monitor_task.done():
                        monitor_task.cancel()
                    try:
                        await asyncio.wait_for(asyncio.shield(pump_j2s), timeout=0.1)
                    except (asyncio.TimeoutError, TimeoutError):
                        pass
                    # Allow solution a short grace window to exit on pipe EOF.
                    if solution_proc.returncode is None:
                        try:
                            await asyncio.wait_for(wait_sol, timeout=0.5)
                        except (asyncio.TimeoutError, TimeoutError):
                            sol_killed_by_cleanup = True
                            await terminate_pid_tree(sol_pid)
                elif wait_sol in done:
                    sol_exited_first = True
                    # Solution exited first; let solution->interactor pipe flush EOF and interactor finish.
                    remaining = max(3.0, wall_cap_sec - (time.perf_counter() - wall_start))
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(pump_s2j, wait_int),
                            timeout=remaining,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        abort_reason = abort_reason or "INTERACTOR_HANG"
                        await terminate_pid_tree(int_pid)
            except BaseException:
                abnormal_exit = True
                await terminate_pid_tree(sol_pid)
                await terminate_pid_tree(int_pid)
                raise
            finally:
                for ctrl_task in (monitor_task, wait_abort):
                    if not ctrl_task.done():
                        ctrl_task.cancel()
                if interactor_proc.returncode is None:
                    await terminate_pid_tree(int_pid)
                if solution_proc.returncode is None:
                    await terminate_pid_tree(sol_pid)
                for proc in (interactor_proc, solution_proc):
                    if proc.returncode is None:
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except (asyncio.TimeoutError, TimeoutError):
                            pass
                for bg_task in (pump_j2s, pump_s2j, wait_int, wait_sol):
                    if not bg_task.done():
                        bg_task.cancel()
                cleanup_tasks: list[asyncio.Task[Any]] = [
                    monitor_task,
                    wait_abort,
                    pump_j2s,
                    pump_s2j,
                    wait_int,
                    wait_sol,
                ]
                if abnormal_exit:
                    for err_task in (err_int_task, err_sol_task):
                        if not err_task.done():
                            err_task.cancel()
                    cleanup_tasks.extend([err_int_task, err_sol_task])
                await asyncio.gather(*cleanup_tasks, return_exceptions=True)
                if abnormal_exit:
                    for proc in (interactor_proc, solution_proc):
                        transport = getattr(proc, "_transport", None)
                        if transport is not None:
                            try:
                                transport.close()
                            except Exception:
                                pass

            wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 2)
            try:
                int_stderr_bytes = await asyncio.wait_for(err_int_task, timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError):
                err_int_task.cancel()
                await asyncio.gather(err_int_task, return_exceptions=True)
                int_stderr_bytes = b""
            try:
                sol_stderr_bytes = await asyncio.wait_for(err_sol_task, timeout=1.0)
            except (asyncio.TimeoutError, TimeoutError):
                err_sol_task.cancel()
                await asyncio.gather(err_sol_task, return_exceptions=True)
                sol_stderr_bytes = b""

            judge_stderr = int_stderr_bytes.decode("utf-8", errors="replace")
            sol_stderr = sol_stderr_bytes.decode("utf-8", errors="replace")
            judge_output = ""
            if os.path.isfile(out_path):
                try:
                    with open(out_path, encoding="utf-8", errors="replace") as f:
                        judge_output = f.read(_MAX_STDERR_BYTES)
                except OSError:
                    pass

            transcript = _format_transcript(transcript_events, max_transcript_bytes)
            score, metrics = parse_judge_metrics(judge_stderr, judge_output)
            metrics.setdefault("judge_bytes_sent", judge_bytes_sent)
            metrics.setdefault("contestant_bytes_sent", contestant_bytes_sent)

            sol_rc = solution_proc.returncode
            int_rc = interactor_proc.returncode
            final_cpu_ms = (
                round(accumulated_cpu_ms, 2)
                if accumulated_cpu_ms > 0.0
                else round(min(wall_ms, effective_tl_ms), 2)
            )

            if abort_reason == "MLE":
                verdict = "MLE"
                detail = f"Solution memory limit exceeded ({peak_memory_mb:.1f}MB > {effective_mem_mb:.1f}MB)"
            elif abort_reason == "DEADLOCK":
                verdict = "DEADLOCK"
                if judge_bytes_sent == 0 and judge_output.strip():
                    detail = (
                        "Protocol deadlock: interactor wrote to tout (argv[2]) but sent 0 bytes to stdout; "
                        "in testlib.h registerInteraction, write contestant prompts to std::cout and flush."
                    )
                else:
                    detail = (
                        f"Protocol deadlock: no pipe I/O or CPU progress for {deadlock_sec:.1f}s "
                        "(possible missing flush or mismatched interaction turn order)"
                    )
            elif abort_reason == "TLE":
                verdict = "TLE"
                detail = f"Time limit exceeded (cpu={final_cpu_ms}ms, wall={wall_ms}ms)"
            elif abort_reason == "INTERACTOR_HANG":
                verdict = "FAIL"
                detail = "Interactor did not terminate after solution exited"
            else:
                raw_verdict = verdict_from_testlib_rc(int_rc)
                verdict = raw_verdict
                detail = judge_stderr.strip()[:500]
                if (
                    sol_rc is not None
                    and sol_rc != 0
                    and raw_verdict != "FAIL"
                    and not _is_harmless_sigpipe(sol_rc)
                    and not (sol_killed_by_cleanup and raw_verdict in ("WA", "PE", "PC"))
                    and (raw_verdict in ("AC", "PC", "PE") or sol_rc < 0 or sol_exited_first)
                ):
                    verdict = "RE"
                    extra_msg = f": {judge_stderr.strip()[:300]}" if judge_stderr.strip() else ""
                    detail = f"Solution exited with non-zero code {sol_rc} (judge={raw_verdict}){extra_msg}"

            return JudgeResult(
                verdict=verdict,
                cpu_time_ms=final_cpu_ms if verdict in ("AC", "PC", "WA", "PE") else None,
                wall_time_ms=wall_ms,
                memory_mb=round(peak_memory_mb, 2),
                stdout="",
                stderr=sol_stderr,
                judge_stdout=judge_output,
                judge_stderr=judge_stderr,
                solution_returncode=sol_rc,
                judge_returncode=int_rc,
                score=score,
                metrics=metrics,
                transcript=transcript,
                detail=detail,
            )
