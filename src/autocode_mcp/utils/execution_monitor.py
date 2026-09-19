import asyncio
import os
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

        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                subproc.communicate(),
                timeout=timeout_sec,
            )
            stdout_bytes = stdout_data[:max_output_bytes]
            stderr_bytes = stderr_data[:max_output_bytes]
        except asyncio.TimeoutError:
            status = "timeout"
            await terminate_pid_tree(pid)
            try:
                await asyncio.wait_for(subproc.wait(), timeout=1.0)
            except Exception:
                pass
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
        timeout_sec = max(0.1, time_limit_ms / 1000.0)

        with open(input_file_path, "rb") as in_f:
            input_bytes = in_f.read()

        interactor_proc = await asyncio.create_subprocess_exec(
            *interactor_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )

        solution_proc = await asyncio.create_subprocess_exec(
            *solution_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,
        )

        sol_pid = solution_proc.pid
        int_pid = interactor_proc.pid

        try:
            sol_ps = psutil.Process(sol_pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            sol_ps = None

        peak_memory_mb = 0.0
        accumulated_cpu_ms = 0.0

        async def _sample_sol() -> None:
            nonlocal peak_memory_mb, accumulated_cpu_ms
            while solution_proc.returncode is None:
                if sol_ps:
                    try:
                        with sol_ps.oneshot():
                            mem = cls.get_memory_mb(sol_ps)
                            if mem > peak_memory_mb:
                                peak_memory_mb = mem
                            accumulated_cpu_ms = cls.get_cpu_times_ms(sol_ps)
                            if peak_memory_mb > memory_limit_mb:
                                break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break
                await asyncio.sleep(0.02)

        sampler_task = asyncio.create_task(_sample_sol())

        # 启动管道泵送数据
        async def _pump_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
            except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
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
            except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
                pass

        async def _feed_input() -> None:
            if interactor_proc.stdin and input_bytes:
                try:
                    interactor_proc.stdin.write(input_bytes)
                    await interactor_proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError):
                    pass
                finally:
                    try:
                        interactor_proc.stdin.close()
                    except Exception:
                        pass

        pump_tasks = []
        pump_tasks.append(asyncio.create_task(_feed_input()))

        if interactor_proc.stdout and solution_proc.stdin:
            pump_tasks.append(asyncio.create_task(_pump_stream(interactor_proc.stdout, solution_proc.stdin)))
        if solution_proc.stdout and interactor_proc.stdin:
            pump_tasks.append(asyncio.create_task(_pump_stream(solution_proc.stdout, interactor_proc.stdin)))

        # 异步排空 stderr，防止管道填满阻塞
        if interactor_proc.stderr:
            pump_tasks.append(asyncio.create_task(_drain_stream(interactor_proc.stderr)))
        if solution_proc.stderr:
            pump_tasks.append(asyncio.create_task(_drain_stream(solution_proc.stderr)))

        status = "ok"
        try:
            await asyncio.wait_for(
                asyncio.gather(solution_proc.wait(), interactor_proc.wait()),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            status = "timeout"
            await terminate_pid_tree(sol_pid)
            await terminate_pid_tree(int_pid)
            try:
                await asyncio.wait_for(
                    asyncio.gather(solution_proc.wait(), interactor_proc.wait()),
                    timeout=1.0,
                )
            except Exception:
                pass
        finally:
            sampler_task.cancel()
            for t in pump_tasks:
                t.cancel()
            try:
                await sampler_task
            except asyncio.CancelledError:
                pass

        if status != "timeout":
            if peak_memory_mb > memory_limit_mb:
                status = "mle"
                await terminate_pid_tree(sol_pid)
                await terminate_pid_tree(int_pid)
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
