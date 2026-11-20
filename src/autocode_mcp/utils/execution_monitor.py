import asyncio
import os
import sys
import time
from typing import Any

import psutil

from .process import terminate_pid_tree


class DynamicExecutionMonitor:
    @classmethod
    def get_cpu_times_ms(cls, proc: psutil.Process) -> float:
        try:
            times = proc.cpu_times()
            return (times.user + times.system) * 1000.0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    @classmethod
    def get_memory_mb(cls, proc: psutil.Process) -> float:
        try:
            mem = proc.memory_info()
            return mem.rss / (1024.0 * 1024.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return 0.0

    @classmethod
    async def measure_baseline_overhead(cls, cwd: str | None = None) -> float:
        try:
            cmd = ["/bin/true"] if os.path.exists("/bin/true") else ["true"]
            t0 = time.perf_counter()
            subproc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=cwd,
            )
            await subproc.wait()
            return max(0.0, (time.perf_counter() - t0) * 1000.0)
        except Exception:
            return 1.5

    @classmethod
    async def run_monitored_process(
        cls,
        cmd: list[str],
        input_file_path: str,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 512.0,
        cwd: str | None = None,
        baseline_overhead_ms: float = 0.0,
    ) -> dict[str, Any]:
        timeout_sec = max(0.1, time_limit_ms / 1000.0)
        max_output_bytes = 10 * 1024 * 1024  # 10MB 输出上限，防止管道填满挂起

        wall_start = time.perf_counter()
        peak_memory_mb = 0.0
        accumulated_cpu_ms = 0.0

        with open(input_file_path, "rb") as in_f:
            subproc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=in_f,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                start_new_session=True,
            )

        pid = subproc.pid
        status = "ok"
        stdout_bytes = b""
        stderr_bytes = b""

        try:
            ps_proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            ps_proc = None

        async def _sample_resources() -> None:
            nonlocal peak_memory_mb, accumulated_cpu_ms
            while subproc.returncode is None:
                if ps_proc:
                    try:
                        with ps_proc.oneshot():
                            mem = cls.get_memory_mb(ps_proc)
                            if mem > peak_memory_mb:
                                peak_memory_mb = mem
                            cpu = cls.get_cpu_times_ms(ps_proc)
                            if cpu > accumulated_cpu_ms:
                                accumulated_cpu_ms = cpu
                            if peak_memory_mb > memory_limit_mb:
                                break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break
                await asyncio.sleep(0.005)

        sampler_task = asyncio.create_task(_sample_resources())

        async def _read_stream_limited(reader: asyncio.StreamReader | None, limit: int) -> bytes:
            if not reader:
                return b""
            chunks: list[bytes] = []
            collected = 0
            while not reader.at_eof():
                try:
                    chunk = await reader.read(65536)
                except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
                    break
                if not chunk:
                    break
                if collected < limit:
                    remaining = limit - collected
                    chunks.append(chunk[:remaining])
                    collected += min(len(chunk), remaining)
            return b"".join(chunks)

        read_stdout_task = asyncio.create_task(_read_stream_limited(subproc.stdout, max_output_bytes))
        read_stderr_task = asyncio.create_task(_read_stream_limited(subproc.stderr, max_output_bytes))

        try:
            await asyncio.wait_for(
                asyncio.gather(subproc.wait(), read_stdout_task, read_stderr_task),
                timeout=timeout_sec,
            )
            stdout_bytes = read_stdout_task.result()
            stderr_bytes = read_stderr_task.result()
        except asyncio.TimeoutError:
            status = "timeout"
            await terminate_pid_tree(pid)
            try:
                await asyncio.wait_for(subproc.wait(), timeout=1.0)
            except Exception:
                pass
            if not read_stdout_task.done():
                read_stdout_task.cancel()
            if not read_stderr_task.done():
                read_stderr_task.cancel()
        finally:
            sampler_task.cancel()
            try:
                await sampler_task
            except asyncio.CancelledError:
                pass

        wall_elapsed_ms = (time.perf_counter() - wall_start) * 1000.0

        if status != "timeout":
            if peak_memory_mb > memory_limit_mb:
                status = "mle"
                await terminate_pid_tree(pid)
                try:
                    await asyncio.wait_for(subproc.wait(), timeout=1.0)
                except Exception:
                    pass
            elif subproc.returncode != 0:
                status = "runtime_error"

        calc_wall_ms = max(0.1, wall_elapsed_ms - baseline_overhead_ms)
        final_cpu_ms = (
            accumulated_cpu_ms
            if (accumulated_cpu_ms and accumulated_cpu_ms > 0.5)
            else round(calc_wall_ms, 2)
        ) if status == "ok" else None

        return {
            "status": status,
            "returncode": subproc.returncode,
            "cpu_time_ms": final_cpu_ms,
            "wall_time_ms": round(wall_elapsed_ms, 2),
            "memory_mb": round(peak_memory_mb, 2),
            "stdout": stdout_bytes.decode("utf-8", errors="replace"),
            "stderr": stderr_bytes.decode("utf-8", errors="replace"),
        }

    @classmethod
    async def run_interactive_pipeline(
        cls,
        interactor_cmd: list[str],
        solution_cmd: list[str],
        input_file_path: str,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 512.0,
        cwd: str | None = None,
    ) -> dict[str, Any]:
        import tempfile

        timeout_sec = max(0.1, time_limit_ms / 1000.0)
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        abs_in = os.path.abspath(input_file_path) if input_file_path else ""

        def _abs_cmd(cmd: list[str]) -> list[str]:
            if not cmd:
                return []
            resolved = list(cmd)
            if os.path.exists(resolved[0]):
                resolved[0] = os.path.abspath(resolved[0])
            elif abs_cwd and os.path.exists(os.path.join(abs_cwd, resolved[0])):
                resolved[0] = os.path.abspath(os.path.join(abs_cwd, resolved[0]))
            return resolved

        popen_kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True

        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            tmp_dir = os.path.abspath(raw_tmp_dir)
            # testlib registerInteraction 约定 argv = [exe, input_file, output_file, answer_file]；
            # 若调用方未把 input_file_path 显式附加到命令里，则自动补全参数（无参交互器会安全忽略额外 argv）。
            effective_int_cmd = _abs_cmd(interactor_cmd)
            effective_sol_cmd = _abs_cmd(solution_cmd)
            if abs_in and abs_in not in effective_int_cmd and input_file_path not in effective_int_cmd:
                out_file = os.path.join(tmp_dir, "output.txt")
                ans_file = os.path.join(tmp_dir, "answer.ans")
                with open(ans_file, "w", encoding="utf-8"):
                    pass
                effective_int_cmd.extend([abs_in, out_file, ans_file])

            interactor_proc = await asyncio.create_subprocess_exec(
                *effective_int_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=abs_cwd,
                **popen_kwargs,
            )
            try:
                solution_proc = await asyncio.create_subprocess_exec(
                    *effective_sol_cmd,
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
                sol_ps = psutil.Process(sol_pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                sol_ps = None

            peak_memory_mb = 0.0
            accumulated_cpu_ms = 0.0
            mle_event = asyncio.Event()

            async def _sample_sol() -> None:
                nonlocal peak_memory_mb, accumulated_cpu_ms
                while solution_proc.returncode is None:
                    if sol_ps:
                        try:
                            with sol_ps.oneshot():
                                mem = cls.get_memory_mb(sol_ps)
                                if mem > peak_memory_mb:
                                    peak_memory_mb = mem
                                cpu = cls.get_cpu_times_ms(sol_ps)
                                if cpu > accumulated_cpu_ms:
                                    accumulated_cpu_ms = cpu
                                if peak_memory_mb > memory_limit_mb:
                                    mle_event.set()
                                    break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            break
                    await asyncio.sleep(0.005)

            sampler_task = asyncio.create_task(_sample_sol())

            async def _pump_stream(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter
            ) -> None:
                writer_open = True
                try:
                    while True:
                        chunk = await reader.read(4096)
                        if not chunk:
                            break
                        if writer_open:
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
                    if writer_open:
                        try:
                            writer.close()
                        except Exception:
                            pass

            async def _drain_stream(reader: asyncio.StreamReader | None) -> None:
                if not reader:
                    return
                try:
                    while True:
                        chunk = await reader.read(4096)
                        if not chunk:
                            break
                except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError, OSError):
                    pass

            pump_tasks: list[asyncio.Task[Any]] = []
            if interactor_proc.stdout and solution_proc.stdin:
                pump_tasks.append(
                    asyncio.create_task(_pump_stream(interactor_proc.stdout, solution_proc.stdin))
                )
            if solution_proc.stdout and interactor_proc.stdin:
                pump_tasks.append(
                    asyncio.create_task(_pump_stream(solution_proc.stdout, interactor_proc.stdin))
                )
            if interactor_proc.stderr:
                pump_tasks.append(asyncio.create_task(_drain_stream(interactor_proc.stderr)))
            if solution_proc.stderr:
                pump_tasks.append(asyncio.create_task(_drain_stream(solution_proc.stderr)))

            status = "ok"

            async def _wait_both() -> None:
                await asyncio.gather(solution_proc.wait(), interactor_proc.wait())

            wait_both = asyncio.create_task(_wait_both())
            wait_mle = asyncio.create_task(mle_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {wait_both, wait_mle},
                    timeout=timeout_sec,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    status = "timeout"
                    await terminate_pid_tree(sol_pid)
                    await terminate_pid_tree(int_pid)
                elif wait_mle in done:
                    status = "mle"
                    await terminate_pid_tree(sol_pid)
                    await terminate_pid_tree(int_pid)
            except BaseException:
                await terminate_pid_tree(sol_pid)
                await terminate_pid_tree(int_pid)
                raise
            finally:
                for ctrl_task in (sampler_task, wait_both, wait_mle):
                    if not ctrl_task.done():
                        ctrl_task.cancel()
                if solution_proc.returncode is None:
                    await terminate_pid_tree(sol_pid)
                if interactor_proc.returncode is None:
                    await terminate_pid_tree(int_pid)
                for proc in (solution_proc, interactor_proc):
                    if proc.returncode is None:
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=1.0)
                        except Exception:
                            pass
                for t in pump_tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(
                    sampler_task, wait_both, wait_mle, *pump_tasks, return_exceptions=True
                )

            if status == "ok":
                if peak_memory_mb > memory_limit_mb:
                    status = "mle"
                elif solution_proc.returncode != 0:
                    status = "runtime_error"
                elif interactor_proc.returncode != 0:
                    status = "interactor_error"

            return {
                "status": status,
                "solution_returncode": solution_proc.returncode,
                "interactor_returncode": interactor_proc.returncode,
                "cpu_time_ms": max(accumulated_cpu_ms, 0.1) if status == "ok" else None,
                "memory_mb": round(peak_memory_mb, 2),
            }
