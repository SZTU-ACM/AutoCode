import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from typing import Any

import psutil

from .process import disable_posix_core_dumps, terminate_pid_tree


class DynamicExecutionMonitor:
    VALID_METRICS = frozenset({"auto", "cpu_time", "perf", "cachegrind"})
    _PERF_SIGNAL_LINE_RE = re.compile(
        r"(?m)^(?P<prog>.+?):\s*(?:Aborted|Segmentation fault|Floating point exception|"
        r"Illegal instruction|Bus error|Killed|Trace/breakpoint trap|Terminated|"
        r"Bad system call)(?:\s*\(core dumped\))?\s*$"
    )

    @classmethod
    def _perf_stderr_indicates_signal(cls, stderr_text: str, effective_cmd: list[str]) -> bool:
        """Return True if ``perf`` reported a child fatal signal while returning exit code 0."""
        if not stderr_text or not effective_cmd:
            return False
        target = effective_cmd[0]
        target_base = os.path.basename(target)
        for match in cls._PERF_SIGNAL_LINE_RE.finditer(stderr_text):
            prog = match.group("prog").strip()
            if prog in (target, target_base):
                return True
        return False

    @classmethod
    def resolve_metric_backend(cls, metric: str | None = None) -> str:
        """Resolve execution metric mode from explicit arg or AUTOCODE_METRIC env var."""
        raw = metric if metric is not None else os.environ.get("AUTOCODE_METRIC", "auto")
        normalized = str(raw).strip().lower() if raw is not None else "auto"
        return normalized if normalized in cls.VALID_METRICS else "auto"

    @staticmethod
    def build_perf_command(
        cmd: list[str],
        *,
        perf_bin: str = "perf",
        output_file: str | None = None,
    ) -> list[str]:
        """Build a safe `perf stat -x , -e instructions` command wrapper."""
        built = [perf_bin, "stat", "-x", ",", "-e", "instructions"]
        if output_file:
            built.extend(["-o", output_file])
        built.append("--")
        built.extend(cmd)
        return built

    @staticmethod
    def build_cachegrind_command(
        cmd: list[str],
        *,
        valgrind_bin: str = "valgrind",
        cachegrind_out: str | None = None,
        log_file: str | None = None,
    ) -> list[str]:
        """Build a safe `valgrind --tool=cachegrind` command wrapper with zero CWD artifacts."""
        cg_out = cachegrind_out if cachegrind_out else os.devnull
        built = [
            valgrind_bin,
            "--tool=cachegrind",
            f"--cachegrind-out-file={cg_out}",
        ]
        if log_file:
            built.append(f"--log-file={log_file}")
        built.extend(cmd)
        return built

    @staticmethod
    def parse_perf_stat_instructions(text: str) -> int | None:
        """Parse `perf stat -x , -e instructions` CSV output, summing hybrid PMU rows."""
        if not text:
            return None
        total = 0
        found = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 3:
                continue
            if "instructions" not in parts[2].lower():
                continue
            raw_val = parts[0]
            if not raw_val or raw_val.startswith("<"):
                continue
            cleaned_num = raw_val.replace("_", "").replace(" ", "")
            if not re.fullmatch(r"\d+", cleaned_num):
                continue
            val = int(cleaned_num)
            if val > 0:
                total += val
                found = True
        return total if found and total > 0 else None

    @staticmethod
    def parse_cachegrind_irefs(text: str) -> int | None:
        """Parse deterministic instruction read count (`I refs` / `Ir`) from Cachegrind output."""
        if not text:
            return None
        match = re.search(r"I\s+refs\s*:\s*([0-9][0-9,\s_]*)", text)
        if match:
            digits = re.sub(r"[,_\s]", "", match.group(1))
            if digits.isdigit() and int(digits) > 0:
                return int(digits)
        if re.search(r"^events:\s*.*\bIr\b", text, re.MULTILINE):
            summary_m = re.search(r"^summary:\s*([0-9]+)\b", text, re.MULTILINE)
            if summary_m and int(summary_m.group(1)) > 0:
                return int(summary_m.group(1))
        return None
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

    @staticmethod
    def _abs_cmd(cmd: list[str], abs_cwd: str | None = None) -> list[str]:
        if not cmd:
            return []
        resolved = list(cmd)
        if os.path.exists(resolved[0]):
            resolved[0] = os.path.abspath(resolved[0])
        elif abs_cwd and os.path.exists(os.path.join(abs_cwd, resolved[0])):
            resolved[0] = os.path.abspath(os.path.join(abs_cwd, resolved[0]))
        return resolved

    @classmethod
    def _sample_tree_stats(
        cls,
        root_proc: psutil.Process,
        known_children: dict[int, psutil.Process],
        cpu_by_pid: dict[int, float],
        *,
        refresh_children: bool,
    ) -> tuple[float, float]:
        """采集根进程与已知后代树的总 RSS（MB）与累计最大 CPU（ms）。"""
        if refresh_children:
            try:
                for child in root_proc.children(recursive=True):
                    known_children.setdefault(child.pid, child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        total_mem = 0.0
        with root_proc.oneshot():
            total_mem += cls.get_memory_mb(root_proc)
            root_cpu = cls.get_cpu_times_ms(root_proc)
            if root_cpu > cpu_by_pid.get(root_proc.pid, 0.0):
                cpu_by_pid[root_proc.pid] = root_cpu

        dead_pids: list[int] = []
        for cpid, cproc in list(known_children.items()):
            try:
                if not cproc.is_running():
                    dead_pids.append(cpid)
                    continue
                with cproc.oneshot():
                    total_mem += cls.get_memory_mb(cproc)
                    c_cpu = cls.get_cpu_times_ms(cproc)
                    if c_cpu > cpu_by_pid.get(cpid, 0.0):
                        cpu_by_pid[cpid] = c_cpu
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                dead_pids.append(cpid)
        for cpid in dead_pids:
            known_children.pop(cpid, None)

        return total_mem, sum(cpu_by_pid.values())

    @classmethod
    async def measure_baseline_overhead(cls, cwd: str | None = None) -> float:
        if sys.platform == "win32":
            # Windows 上 external coreutils `true.exe` 可能有 ~90ms 启动开销，
            # 不能代表原生 C++ 可执行文件进程创建的 ~1.5ms 底噪。
            return 1.5
        try:
            true_bin = next((p for p in ("/usr/bin/true", "/bin/true") if os.path.exists(p)), None)
            if true_bin is None:
                return 1.5
            cmd = [true_bin]
            abs_cwd = os.path.abspath(cwd) if cwd is not None else None
            # 先执行 1 次预热以预加载动态库/文件系统缓存
            warmup = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=abs_cwd,
            )
            await warmup.wait()
            samples: list[float] = []
            for _ in range(3):
                t0 = time.perf_counter()
                subproc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=abs_cwd,
                )
                await subproc.wait()
                samples.append(max(0.0, (time.perf_counter() - t0) * 1000.0))
            return min(15.0, min(samples)) if samples else 1.5
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
        metric: str | None = None,
    ) -> dict[str, Any]:
        abs_cwd = os.path.abspath(cwd) if cwd is not None else None
        effective_cmd = cls._abs_cmd(cmd, abs_cwd)
        requested = cls.resolve_metric_backend(metric)

        wrapper_kind = "cpu_time"
        wrapper_bin: str | None = None
        if requested in ("auto", "perf") and sys.platform.startswith("linux"):
            perf_bin = shutil.which("perf")
            if perf_bin:
                wrapper_kind = "perf"
                wrapper_bin = perf_bin
        elif (
            requested == "cachegrind"
            and sys.platform != "win32"
            and not (effective_cmd and effective_cmd[0].startswith("-"))
        ):
            vg_bin = shutil.which("valgrind")
            if vg_bin:
                wrapper_kind = "cachegrind"
                wrapper_bin = vg_bin

        if wrapper_kind == "cpu_time" or wrapper_bin is None:
            res = await cls._run_monitored_once(
                effective_cmd,
                input_file_path,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                abs_cwd=abs_cwd,
                baseline_overhead_ms=baseline_overhead_ms,
            )
            res["instruction_count"] = None
            res["metric_backend"] = "cpu_time"
            return res

        with tempfile.TemporaryDirectory(prefix="autocode_metric_") as tmp_dir:
            if wrapper_kind == "perf":
                stat_file = os.path.join(tmp_dir, "perf_stat.csv")
                wrapped_cmd = cls.build_perf_command(
                    effective_cmd, perf_bin=wrapper_bin, output_file=stat_file
                )
            else:
                stat_file = os.path.join(tmp_dir, "cachegrind.log")
                wrapped_cmd = cls.build_cachegrind_command(
                    effective_cmd,
                    valgrind_bin=wrapper_bin,
                    cachegrind_out=os.devnull,
                    log_file=stat_file,
                )

            wrapper_time_limit_ms = (
                max(time_limit_ms * 10.0, 15000.0)
                if wrapper_kind == "cachegrind"
                else time_limit_ms
            )
            res = await cls._run_monitored_once(
                wrapped_cmd,
                input_file_path,
                time_limit_ms=wrapper_time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                abs_cwd=abs_cwd,
                baseline_overhead_ms=baseline_overhead_ms,
            )

            if (
                wrapper_kind == "perf"
                and res.get("status") == "ok"
                and cls._perf_stderr_indicates_signal(
                    str(res.get("stderr") or ""), effective_cmd
                )
            ):
                bare_res = await cls._run_monitored_once(
                    effective_cmd,
                    input_file_path,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    abs_cwd=abs_cwd,
                    baseline_overhead_ms=baseline_overhead_ms,
                )
                if bare_res.get("status") != "ok":
                    bare_res["instruction_count"] = None
                    bare_res["metric_backend"] = "cpu_time"
                    return bare_res

            metric_text = ""
            if os.path.isfile(stat_file):
                try:
                    with open(stat_file, encoding="utf-8", errors="replace") as mf:
                        metric_text = mf.read()
                except OSError:
                    metric_text = ""
            if not metric_text.strip():
                metric_text = str(res.get("stderr") or "")

            inst_count = (
                cls.parse_perf_stat_instructions(metric_text)
                if wrapper_kind == "perf"
                else cls.parse_cachegrind_irefs(metric_text)
            )

            if inst_count is not None:
                res["instruction_count"] = inst_count
                res["metric_backend"] = wrapper_kind
                return res

            if res.get("status") == "runtime_error":
                # Wrapper likely failed permissions or startup; re-run bare command under cpu_time
                fallback_res = await cls._run_monitored_once(
                    effective_cmd,
                    input_file_path,
                    time_limit_ms=time_limit_ms,
                    memory_limit_mb=memory_limit_mb,
                    abs_cwd=abs_cwd,
                    baseline_overhead_ms=baseline_overhead_ms,
                )
                fallback_res["instruction_count"] = None
                fallback_res["metric_backend"] = "cpu_time"
                return fallback_res

            res["instruction_count"] = None
            res["metric_backend"] = "cpu_time"
            return res

    @classmethod
    async def _run_monitored_once(
        cls,
        effective_cmd: list[str],
        input_file_path: str,
        *,
        time_limit_ms: float = 2000.0,
        memory_limit_mb: float = 512.0,
        abs_cwd: str | None = None,
        baseline_overhead_ms: float = 0.0,
    ) -> dict[str, Any]:
        timeout_sec = max(0.1, time_limit_ms / 1000.0)
        max_output_bytes = 10 * 1024 * 1024  # 10MB 输出上限，防止管道填满挂起

        popen_kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
            popen_kwargs["preexec_fn"] = disable_posix_core_dumps

        wall_start = time.perf_counter()
        peak_memory_mb = 0.0
        accumulated_cpu_ms = 0.0

        with open(input_file_path, "rb") as in_f:
            subproc = await asyncio.create_subprocess_exec(
                *effective_cmd,
                stdin=in_f,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=abs_cwd,
                **popen_kwargs,
            )

        pid = subproc.pid
        status = "ok"
        stdout_bytes = b""
        stderr_bytes = b""

        try:
            ps_proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            ps_proc = None

        mle_event = asyncio.Event()

        async def _sample_resources() -> None:
            nonlocal peak_memory_mb, accumulated_cpu_ms
            known_children: dict[int, psutil.Process] = {}
            cpu_by_pid: dict[int, float] = {}
            last_child_scan = wall_start
            while subproc.returncode is None:
                if ps_proc:
                    now = time.perf_counter()
                    do_refresh = (now - wall_start) >= 0.025 and (
                        last_child_scan == wall_start or (now - last_child_scan) >= 0.04
                    )
                    if do_refresh:
                        last_child_scan = now
                    try:
                        mem, cpu = cls._sample_tree_stats(
                            ps_proc,
                            known_children,
                            cpu_by_pid,
                            refresh_children=do_refresh,
                        )
                        if mem > peak_memory_mb:
                            peak_memory_mb = mem
                        if cpu > accumulated_cpu_ms:
                            accumulated_cpu_ms = cpu
                        if peak_memory_mb > memory_limit_mb:
                            mle_event.set()
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        break
                await asyncio.sleep(0.002)

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
        wait_proc_task = asyncio.create_task(subproc.wait())
        mle_wait_task = asyncio.create_task(mle_event.wait())

        async def _cleanup_running() -> None:
            await terminate_pid_tree(pid)
            try:
                await asyncio.wait_for(subproc.wait(), timeout=1.0)
            except Exception:
                pass
            for t in (wait_proc_task, mle_wait_task, read_stdout_task, read_stderr_task):
                if not t.done():
                    t.cancel()
            await asyncio.gather(
                wait_proc_task, mle_wait_task, read_stdout_task, read_stderr_task, return_exceptions=True
            )

        try:
            done, _ = await asyncio.wait(
                {wait_proc_task, mle_wait_task},
                timeout=timeout_sec,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if mle_event.is_set() or (mle_wait_task in done and peak_memory_mb > memory_limit_mb):
                status = "mle"
                await _cleanup_running()
            elif wait_proc_task in done:
                mle_wait_task.cancel()
                try:
                    await mle_wait_task
                except asyncio.CancelledError:
                    pass
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        asyncio.gather(read_stdout_task, read_stderr_task),
                        timeout=max(0.5, min(2.0, timeout_sec)),
                    )
                except asyncio.TimeoutError:
                    for t in (read_stdout_task, read_stderr_task):
                        if not t.done():
                            t.cancel()
                    await asyncio.gather(read_stdout_task, read_stderr_task, return_exceptions=True)
            else:
                status = "mle" if peak_memory_mb > memory_limit_mb else "timeout"
                await _cleanup_running()
        except BaseException:
            await _cleanup_running()
            raise
        finally:
            sampler_task.cancel()
            try:
                await sampler_task
            except asyncio.CancelledError:
                pass

        wall_elapsed_ms = (time.perf_counter() - wall_start) * 1000.0

        if peak_memory_mb > memory_limit_mb:
            status = "mle"
            if subproc.returncode is None:
                await terminate_pid_tree(pid)
                try:
                    await asyncio.wait_for(subproc.wait(), timeout=1.0)
                except Exception:
                    pass
        elif status == "ok" and subproc.returncode != 0:
            status = "runtime_error"

        calc_wall_ms = max(0.1, wall_elapsed_ms - baseline_overhead_ms)
        if status == "ok":
            # Windows 上 psutil.cpu_times() 为 15.625ms 调度滴答粒度，
            # Linux 上 /proc/<pid>/stat 为 10ms (USER_HZ=100) jiffy 粒度；
            # 低耗时（不足 4~5 个 tick）改用高精度 monotonic wall time 扣减启动底噪，
            # 避免短运行样本随机跳变到 10ms / 20ms 破坏 log-log 复杂度斜率。
            use_qpc_wall = (
                (sys.platform == "win32" and accumulated_cpu_ms < 64.0)
                or (sys.platform.startswith("linux") and accumulated_cpu_ms < 50.0)
                or accumulated_cpu_ms <= 0.5
            )
            final_cpu_ms = round(calc_wall_ms, 2) if use_qpc_wall else round(accumulated_cpu_ms, 2)
        else:
            final_cpu_ms = None

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

        popen_kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
            popen_kwargs["preexec_fn"] = disable_posix_core_dumps

        wall_start = time.perf_counter()
        with tempfile.TemporaryDirectory(dir=abs_cwd) as raw_tmp_dir:
            tmp_dir = os.path.abspath(raw_tmp_dir)
            # testlib registerInteraction 约定 argv = [exe, input_file, output_file, answer_file]；
            # 若调用方未把 input_file_path 显式附加到命令里，则自动补全参数（无参交互器会安全忽略额外 argv）。
            effective_int_cmd = cls._abs_cmd(interactor_cmd, abs_cwd)
            effective_sol_cmd = cls._abs_cmd(solution_cmd, abs_cwd)
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
                known_children: dict[int, psutil.Process] = {}
                cpu_by_pid: dict[int, float] = {}
                last_child_scan = wall_start
                while solution_proc.returncode is None:
                    if sol_ps:
                        now = time.perf_counter()
                        do_refresh = (now - wall_start) >= 0.025 and (
                            last_child_scan == wall_start or (now - last_child_scan) >= 0.04
                        )
                        if do_refresh:
                            last_child_scan = now
                        try:
                            mem, cpu = cls._sample_tree_stats(
                                sol_ps,
                                known_children,
                                cpu_by_pid,
                                refresh_children=do_refresh,
                            )
                            if mem > peak_memory_mb:
                                peak_memory_mb = mem
                            if cpu > accumulated_cpu_ms:
                                accumulated_cpu_ms = cpu
                            if peak_memory_mb > memory_limit_mb:
                                mle_event.set()
                                break
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            break
                    await asyncio.sleep(0.002)

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

            wall_ms = (time.perf_counter() - wall_start) * 1000.0
            final_cpu_ms: float | None = None
            if status == "ok":
                use_qpc_wall = (
                    (sys.platform == "win32" and accumulated_cpu_ms < 64.0)
                    or (sys.platform.startswith("linux") and accumulated_cpu_ms < 50.0)
                    or accumulated_cpu_ms <= 0.5
                )
                final_cpu_ms = (
                    round(max(0.1, wall_ms), 2) if use_qpc_wall else round(accumulated_cpu_ms, 2)
                )

            return {
                "status": status,
                "solution_returncode": solution_proc.returncode,
                "interactor_returncode": interactor_proc.returncode,
                "cpu_time_ms": final_cpu_ms,
                "wall_time_ms": round(wall_ms, 2),
                "memory_mb": round(peak_memory_mb, 2),
                "instruction_count": None,
                "metric_backend": "cpu_time",
            }
