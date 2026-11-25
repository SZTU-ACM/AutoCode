"""Comprehensive tests for pluggable sandbox backends, registry, runners, and CLI."""

from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from autocode_mcp.cli import audit as audit_cli
from autocode_mcp.tools.base import ToolResult
from autocode_mcp.utils.communication_runner import CommunicationRunner
from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.judge_runner import CheckerRunner, InteractiveRunner, StandardRunner
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.utils.sandbox import (
    AppleContainerSandboxExecutor,
    BwrapSandboxExecutor,
    IsolateSandboxExecutor,
    NoopSandboxExecutor,
    NsjailSandboxExecutor,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteSandboxExecutor,
    SandboxExecutionPlan,
    SandboxExecutor,
    SandboxUnavailableError,
    get_active_sandbox_executor,
    resolve_sandbox_executor,
    sandbox_context,
)
from autocode_mcp.workflow.models import AutoCodeManifest, ProtocolConfig, SandboxConfig


def test_bwrap_exact_argv_network_and_mount_order() -> None:
    executor = BwrapSandboxExecutor(include_system_paths=False, mount_proc=True)
    assert executor.name == "bwrap"
    assert isinstance(executor, SandboxExecutor)

    plan = executor.wrap_command(
        ["/tmp/work/sol", "--fast"],
        cwd="/tmp/work",
        ro_paths=["/opt/inputs", "/tmp/work/readonly"],
        rw_paths=["/tmp/work"],
        allow_network=False,
        memory_limit_mb=256.0,
        time_limit_ms=2000.0,
    )
    argv = plan.argv
    assert argv[0] == "bwrap"
    assert "--die-with-parent" in argv
    assert "--unshare-user-try" in argv
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    assert argv[argv.index("--proc") + 1] == "/proc"
    assert argv[argv.index("--dev") + 1] == "/dev"
    assert argv[argv.index("--tmpfs") + 1] == "/tmp"

    # /tmp tmpfs must appear BEFORE workspace bind mounts under /tmp/...
    tmpfs_idx = argv.index("--tmpfs")
    bind_idx = argv.index("--bind")
    assert tmpfs_idx < bind_idx

    # Parent RW (/tmp/work) must be mounted BEFORE nested child RO (/tmp/work/readonly).
    rw_pos = argv.index("/tmp/work")
    ro_nested_pos = argv.index("/tmp/work/readonly")
    assert rw_pos < ro_nested_pos
    assert argv[rw_pos - 1] == "--bind"
    assert argv[ro_nested_pos - 1] == "--ro-bind"
    assert argv[argv.index("/opt/inputs") - 1] == "--ro-bind"
    assert argv[argv.index("--chdir") + 1] == "/tmp/work"
    sep = argv.index("--")
    assert argv[sep + 1 :] == ["/tmp/work/sol", "--fast"]

    # allow_network=True omits --unshare-net.
    net_plan = executor.wrap_command(["/bin/true"], cwd="/tmp/work", allow_network=True)
    assert "--unshare-net" not in net_plan.argv

    # Container compatibility mode (mount_proc=False) omits --proc while preserving isolation flags.
    no_proc_exec = BwrapSandboxExecutor(include_system_paths=False, mount_proc=False)
    no_proc_argv = no_proc_exec.wrap_command(["/bin/true"], cwd="/tmp/work").argv
    assert "--proc" not in no_proc_argv
    assert "--unshare-user-try" in no_proc_argv
    assert "--unshare-net" in no_proc_argv
    assert "--unshare-pid" in no_proc_argv
    assert no_proc_argv[no_proc_argv.index("--dev") + 1] == "/dev"


def test_bwrap_preserves_usrmerge_system_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_usr = tmp_path / "usr"
    (fake_usr / "bin").mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    try:
        fake_bin.symlink_to("usr/bin")
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege not enabled")
        raise

    monkeypatch.setattr(
        "autocode_mcp.utils.sandbox.bwrap.SANDBOX_SYSTEM_RO_PATHS",
        (str(fake_usr), str(fake_bin)),
    )
    plan = BwrapSandboxExecutor(include_system_paths=True).wrap_command(
        ["/app/sol"],
        cwd=str(tmp_path),
        rw_paths=[str(tmp_path)],
    )
    assert "--ro-bind" in plan.argv
    assert str(fake_usr) in plan.argv
    sym_idx = plan.argv.index("--symlink")
    assert plan.argv[sym_idx + 1] == "usr/bin"
    assert plan.argv[sym_idx + 2] == str(fake_bin)


def test_nsjail_isolate_and_apple_container_exact_argv() -> None:
    nsjail = NsjailSandboxExecutor(include_system_paths=False)
    assert nsjail.name == "nsjail"
    ns_plan = nsjail.wrap_command(
        ["/work/sol"],
        cwd="/work",
        ro_paths=["/data/in"],
        rw_paths=["/work"],
        allow_network=False,
        memory_limit_mb=128.0,
        time_limit_ms=1500.0,
    )
    assert ns_plan.argv[:5] == ["nsjail", "--mode", "o", "--quiet", "--iface_no_lo"]
    assert ns_plan.argv[ns_plan.argv.index("--time_limit") + 1] == "2"
    assert ns_plan.argv[ns_plan.argv.index("--rlimit_cpu") + 1] == "2"
    assert ns_plan.argv[ns_plan.argv.index("--rlimit_as") + 1] == "128"
    assert ns_plan.argv[ns_plan.argv.index("--tmpfsmount") + 1] == "/tmp"
    assert ns_plan.argv[ns_plan.argv.index("--bindmount_ro") + 1] == "/data/in:/data/in"
    assert ns_plan.argv[ns_plan.argv.index("--bindmount") + 1] == "/work:/work"
    assert ns_plan.argv[ns_plan.argv.index("--cwd") + 1] == "/work"
    assert ns_plan.argv[-2:] == ["--", "/work/sol"]

    ns_net = nsjail.wrap_command(["/work/sol"], cwd="/work", allow_network=True)
    assert "--disable_clone_newnet" in ns_net.argv
    assert "--iface_no_lo" not in ns_net.argv

    isolate = IsolateSandboxExecutor(box_id=7)
    assert isolate.name == "isolate"
    iso_plan = isolate.wrap_command(
        ["/work/sol"],
        cwd="/work",
        ro_paths=["/data/in"],
        rw_paths=["/work"],
        allow_network=False,
        memory_limit_mb=256.0,
        time_limit_ms=2000.0,
    )
    assert iso_plan.argv[:3] == ["isolate", "--cg", "--box-id=7"]
    assert "--share-net" not in iso_plan.argv
    assert "--cg-mem=262144" in iso_plan.argv
    assert "--mem=262144" in iso_plan.argv
    assert "--time=2" in iso_plan.argv
    assert "--wall-time=6" in iso_plan.argv
    assert "--dir=/data/in" in iso_plan.argv
    assert "--dir=/work:rw" in iso_plan.argv
    assert "--chdir=/work" in iso_plan.argv
    assert iso_plan.argv[-3:] == ["--run", "--", "/work/sol"]

    iso_net = isolate.wrap_command(["/work/sol"], cwd="/work", allow_network=True)
    assert "--share-net" in iso_net.argv

    apple = AppleContainerSandboxExecutor(image="ubuntu:24.04")
    assert apple.name == "apple_container"
    app_plan = apple.wrap_command(
        ["/work/sol", "arg1"],
        cwd="/work",
        ro_paths=["/data/in"],
        rw_paths=["/work"],
        allow_network=False,
        memory_limit_mb=512.0,
        time_limit_ms=2000.0,
    )
    assert app_plan.argv == [
        "container",
        "run",
        "--rm",
        "--network",
        "none",
        "--memory",
        "512M",
        "--volume",
        "/work:/work",
        "--volume",
        "/data/in:/data/in:ro",
        "--workdir",
        "/work",
        "ubuntu:24.04",
        "/work/sol",
        "arg1",
    ]


def test_mount_containment_and_validation_guards() -> None:
    # Unlisted cwd defaults strictly to read-only across all backends.
    for backend in (
        BwrapSandboxExecutor(include_system_paths=False),
        NsjailSandboxExecutor(include_system_paths=False),
        IsolateSandboxExecutor(),
        AppleContainerSandboxExecutor(),
        NoopSandboxExecutor(),
    ):
        plan = backend.wrap_command(["/work/sol"], cwd="/work")
        assert plan.ro_paths == ("/work",)
        assert plan.rw_paths == ()

        # Path traversal rejected in cmd[0], cwd, ro_paths, rw_paths.
        for bad_kwargs in (
            {"cmd": ["../evil"], "cwd": "/work"},
            {"cmd": ["/work/sol"], "cwd": "/work/../etc"},
            {"cmd": ["/work/sol"], "ro_paths": ["/work/../../etc"]},
            {"cmd": ["/work/sol"], "rw_paths": ["/work/../escape"]},
            {"cmd": ["/work/sol\x00evil"], "cwd": "/work"},
            {"cmd": ["/work/sol", "arg\x00bad"], "cwd": "/work"},
            {"cmd": ["/work/sol"], "cwd": "/work\x00bad"},
            {"cmd": ["/work/sol"], "ro_paths": ["/ro\nbad"]},
            {"cmd": ["/work/sol"], "rw_paths": ["/"]},
            {"cmd": ["/work/sol"], "rw_paths": ["C:\\"]},
            {"cmd": ["/work/sol"], "ro_paths": ["/dup"], "rw_paths": ["/dup"]},
            {"cmd": [], "cwd": "/work"},
            {"cmd": ["/work/sol"], "memory_limit_mb": 0},
            {"cmd": ["/work/sol"], "memory_limit_mb": math.nan},
            {"cmd": ["/work/sol"], "time_limit_ms": -5},
        ):
            cmd = bad_kwargs.pop("cmd")
            with pytest.raises(ValueError):
                backend.wrap_command(cmd, **bad_kwargs)

    # Extra args security guards.
    with pytest.raises(ValueError):
        BwrapSandboxExecutor(extra_args=["--setenv", "A\x00B", "1"])
    with pytest.raises(ValueError):
        BwrapSandboxExecutor(extra_args=["--ro-bind", "/tmp/../etc", "/etc"])
    with pytest.raises(ValueError):
        BwrapSandboxExecutor(extra_args=["--share-net"]).wrap_command(
            ["/work/sol"], cwd="/work", allow_network=False
        )
    with pytest.raises(ValueError):
        AppleContainerSandboxExecutor(extra_args=["--network", "bridge"]).wrap_command(
            ["/work/sol"], cwd="/work", allow_network=False
        )
    with pytest.raises(ValueError):
        IsolateSandboxExecutor(box_id=-1)
    with pytest.raises(ValueError):
        AppleContainerSandboxExecutor(image="-bad-flag")


@pytest.mark.asyncio
async def test_remote_sandbox_protocol_stub() -> None:
    class FakeRemoteSandbox:
        @property
        def name(self) -> str:
            return "go-judge"

        async def is_available(self) -> bool:
            return True

        async def execute(self, request: RemoteExecutionRequest) -> RemoteExecutionResult:
            return RemoteExecutionResult(
                returncode=0,
                stdout="ok:" + request.stdin,
                backend=self.name,
            )

        async def close(self) -> None:
            return None

    remote = FakeRemoteSandbox()
    assert isinstance(remote, RemoteSandboxExecutor)
    assert await remote.is_available() is True
    res = await remote.execute(RemoteExecutionRequest(cmd=("/sol",), stdin="42"))
    assert res.returncode == 0
    assert res.stdout == "ok:42"
    await remote.close()


def test_host_probing_auto_fallback_vs_explicit_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Linux with nothing installed: auto degrades to none (degraded=True); explicit raises.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    auto_exec = resolve_sandbox_executor("auto")
    assert auto_exec.name == "none"
    assert isinstance(auto_exec, NoopSandboxExecutor)
    assert auto_exec.degraded is True
    assert "bwrap" in auto_exec.reason
    assert auto_exec.wrap_command(["/work/sol"], cwd="/work").degraded is True

    for backend in ("bwrap", "nsjail", "isolate", "apple_container", "docker"):
        with pytest.raises(SandboxUnavailableError) as exc_info:
            resolve_sandbox_executor(backend)
        assert exc_info.value.backend == backend

    # 2. Linux with nsjail & bwrap installed: auto prefers bwrap.
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"bwrap", "nsjail"} else None,
    )
    selected = resolve_sandbox_executor("auto")
    assert selected.name == "bwrap"
    assert isinstance(selected, BwrapSandboxExecutor)

    # 3. macOS Apple Silicon with container CLI available vs Intel macOS.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: "/usr/local/bin/container" if name == "container" else None,
    )
    mac_exec = resolve_sandbox_executor("auto")
    assert mac_exec.name == "apple_container"
    assert isinstance(mac_exec, AppleContainerSandboxExecutor)

    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert AppleContainerSandboxExecutor().is_available() is False
    mac_intel = resolve_sandbox_executor("auto")
    assert mac_intel.name == "none"
    assert isinstance(mac_intel, NoopSandboxExecutor)
    assert mac_intel.degraded is True

    # 4. Windows degrades gracefully under auto and rejects POSIX backends.
    monkeypatch.setattr(sys, "platform", "win32")
    win_auto = resolve_sandbox_executor("auto")
    assert win_auto.name == "none"
    assert isinstance(win_auto, NoopSandboxExecutor)
    assert win_auto.degraded is True
    with pytest.raises(SandboxUnavailableError):
        resolve_sandbox_executor("bwrap")

    # 5. Manifest sandbox resolution & explicit none override.
    manifest = AutoCodeManifest(
        problem_name="sbx",
        sandbox=SandboxConfig(backend="bwrap", extra_args=["--unshare-ipc"]),
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    from_manifest = resolve_sandbox_executor(None, manifest)
    assert from_manifest.name == "bwrap"
    assert resolve_sandbox_executor("none", manifest).name == "none"


@pytest.mark.asyncio
async def test_async_contextvar_isolation_across_concurrent_tasks() -> None:
    class TagExecutor:
        def __init__(self, tag: str) -> None:
            self._tag = tag

        @property
        def name(self) -> str:
            return f"fake-{self._tag}"

        def is_available(self) -> bool:
            return True

        def wrap_command(
            self,
            cmd: Sequence[str],
            *,
            cwd: str | None = None,
            ro_paths: Sequence[str] = (),
            rw_paths: Sequence[str] = (),
            allow_network: bool = False,
            memory_limit_mb: float | None = None,
            time_limit_ms: float | None = None,
        ) -> SandboxExecutionPlan:
            return SandboxExecutionPlan(
                argv=list(cmd),
                cwd=cwd,
                backend=self.name,
                ro_paths=tuple(ro_paths),
                rw_paths=tuple(rw_paths),
                allow_network=allow_network,
            )

    assert get_active_sandbox_executor().name == "none"

    async def task_a() -> tuple[str, str, str]:
        with sandbox_context(TagExecutor("alpha")):
            before = get_active_sandbox_executor().name
            await asyncio.sleep(0.02)
            child = asyncio.create_task(_read_name_async())
            child_name = await child
            after = get_active_sandbox_executor().name
            return before, child_name, after

    async def _read_name_async() -> str:
        await asyncio.sleep(0.005)
        return get_active_sandbox_executor().name

    async def task_b() -> tuple[str, str]:
        before = get_active_sandbox_executor().name
        await asyncio.sleep(0.02)
        after = get_active_sandbox_executor().name
        return before, after

    async def task_c() -> tuple[str, str]:
        with sandbox_context(TagExecutor("gamma")):
            await asyncio.sleep(0.01)
            first = get_active_sandbox_executor().name
            with sandbox_context("none"):
                inner = get_active_sandbox_executor().name
            return first, inner

    res_a, res_b, res_c = await asyncio.gather(task_a(), task_b(), task_c())
    assert res_a == ("fake-alpha", "fake-alpha", "fake-alpha")
    assert res_b == ("none", "none")
    assert res_c == ("fake-gamma", "none")
    assert get_active_sandbox_executor().name == "none"

    with pytest.raises(RuntimeError):
        with sandbox_context(TagExecutor("err")):
            assert get_active_sandbox_executor().name == "fake-err"
            raise RuntimeError("boom")
    assert get_active_sandbox_executor().name == "none"


class _RecordingShimExecutor:
    """Fake SandboxExecutor executing commands through a real Python shim script."""

    def __init__(self, shim_script: Path, log_path: Path) -> None:
        self._shim_script = shim_script
        self._log_path = log_path
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "fake-shim"

    def is_available(self) -> bool:
        return True

    def wrap_command(
        self,
        cmd: Sequence[str],
        *,
        cwd: str | None = None,
        ro_paths: Sequence[str] = (),
        rw_paths: Sequence[str] = (),
        allow_network: bool = False,
        memory_limit_mb: float | None = None,
        time_limit_ms: float | None = None,
    ) -> SandboxExecutionPlan:
        self.calls.append(
            {
                "cmd": list(cmd),
                "cwd": cwd,
                "ro_paths": list(ro_paths),
                "rw_paths": list(rw_paths),
                "allow_network": allow_network,
                "memory_limit_mb": memory_limit_mb,
                "time_limit_ms": time_limit_ms,
            }
        )
        return SandboxExecutionPlan(
            argv=[sys.executable, str(self._shim_script), str(self._log_path), "--", *cmd],
            cwd=cwd,
            env_overrides={"AUTOCODE_SANDBOX_ACTIVE": "1"},
            backend=self.name,
            ro_paths=tuple(ro_paths),
            rw_paths=tuple(rw_paths),
            allow_network=allow_network,
        )


def _create_shim_script(tmp_path: Path) -> tuple[Path, Path]:
    shim = tmp_path / "sandbox_shim.py"
    log_path = tmp_path / "shim_invocations.jsonl"
    shim.write_text(
        """
import json
import os
import subprocess
import sys

log_file = sys.argv[1]
sep = sys.argv.index("--")
inner = sys.argv[sep + 1 :]
with open(log_file, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"inner": inner, "cwd": os.getcwd(), "marker": os.environ.get("AUTOCODE_SANDBOX_ACTIVE")}) + "\\n")
proc = subprocess.run(inner)
raise SystemExit(proc.returncode)
""".lstrip(),
        encoding="utf-8",
    )
    return shim, log_path


async def _compile_cpp_fixture(tmp_path: Path, name: str, code: str) -> str:
    src = tmp_path / f"{name}.cpp"
    exe = tmp_path / f"{name}{get_exe_extension()}"
    src.write_text(code, encoding="utf-8")
    res = await compile_cpp(str(src), str(exe), use_cache=False)
    assert res.success, f"Compile {name} failed: {res.stderr}"
    return str(exe)


@pytest.mark.skipif(not shutil.which("g++"), reason="g++ not available")
@pytest.mark.asyncio
async def test_judge_and_communication_runners_wrap_commands(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bins"
    bin_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    shim, log_path = _create_shim_script(tmp_path)

    sol_exe = await _compile_cpp_fixture(
        bin_dir,
        "sol_add",
        '#include <iostream>\nint main(){int a,b; if(std::cin>>a>>b) std::cout<<(a+b)<<"\\n"; return 0;}\n',
    )
    chk_exe = await _compile_cpp_fixture(
        bin_dir,
        "chk",
        """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    int a = inf.readInt();
    int b = inf.readInt();
    int got = ouf.readInt();
    int exp = ans.readInt();
    if (got == a + b && got == exp) quitf(_ok, "ok");
    quitf(_wa, "bad");
}
""",
    )
    int_exe = await _compile_cpp_fixture(
        bin_dir,
        "interactor",
        """
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int n = inf.readInt();
    std::cout << n << std::endl;
    int got = ouf.readInt();
    tout << got << "\\n";
    if (got == n * 2) quitf(_ok, "ok");
    quitf(_wa, "wrong");
}
""",
    )
    int_sol_exe = await _compile_cpp_fixture(
        bin_dir,
        "int_sol",
        '#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<(n*2)<<std::endl; return 0;}\n',
    )
    comm_exe = await _compile_cpp_fixture(
        bin_dir,
        "comm",
        """
#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    const char* s = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = s ? std::atoi(s) : 2;
    int x = inf.readInt();
    std::string val = ouf.readToken();
    if (stage == 1) {
        std::cout << val << "\\n";
        quitf(_ok, "relay");
    }
    if (std::stoi(val) == x + 1) quitf(_ok, "ok");
    quitf(_wa, "bad");
}
""",
    )
    comm_sol_exe = await _compile_cpp_fixture(
        bin_dir,
        "comm_sol",
        """
#include <iostream>
#include <string>
int main() {
    std::string h;
    int x;
    std::cin >> h >> x;
    if (h == "first") std::cout << x << "\\n";
    else std::cout << (x + 1) << "\\n";
    return 0;
}
""",
    )
    mgr_exe = await _compile_cpp_fixture(
        bin_dir,
        "mgr",
        """
#include <iostream>
#include <string>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int x = inf.readInt();
    std::cout << "TO 0 " << x << std::endl;
    std::cout << "TO 1 " << x << std::endl;
    int seen = 0;
    int vals[2] = {0, 0};
    bool got[2] = {false, false};
    while (seen < 2) {
        std::string tag = ouf.readToken();
        if (tag == "EOF") {
            ouf.readInt();
            continue;
        }
        if (tag != "FROM") quitf(_pe, "unexpected tag %s", tag.c_str());
        int idx = ouf.readInt();
        int v = ouf.readInt();
        if (idx >= 0 && idx < 2 && !got[idx]) {
            got[idx] = true;
            vals[idx] = v;
            seen++;
        }
    }
    std::cout << "CLOSE 0\\nCLOSE 1\\n" << std::flush;
    if (got[0] && got[1] && vals[0] + vals[1] == x * 2) quitf(_ok, "ok");
    quitf(_wa, "bad");
}
""",
    )
    part_exe = await _compile_cpp_fixture(
        bin_dir,
        "part",
        '#include <iostream>\nint main(){int x; if(std::cin>>x) std::cout<<x<<std::endl; return 0;}\n',
    )
    grader_linked_exe = await _compile_cpp_fixture(
        bin_dir,
        "grader_linked_sol",
        '#include <iostream>\nint main(){int x; if(std::cin>>x) std::cerr<<"ok bits_used=4 rounds=1\\n"; return 0;}\n',
    )

    recorder = _RecordingShimExecutor(shim, log_path)
    with sandbox_context(recorder):
        # 1. StandardRunner
        std_res = await StandardRunner.run(
            sol_exe,
            "3 4\n",
            expected_output="7\n",
            cwd=str(work_dir),
        )
        assert std_res.verdict == "AC", std_res.detail

        # 2. CheckerRunner (wraps both solution and checker; checker gets rw_paths=())
        chk_res = await CheckerRunner.run(
            chk_exe,
            sol_exe,
            "3 4\n",
            "7\n",
            cwd=str(work_dir),
        )
        assert chk_res.verdict == "AC", chk_res.detail

        # 3. InteractiveRunner (interactor rw=[tmp_dir], solution rw=() with no tmp_dir)
        int_res = await InteractiveRunner.run(
            int_exe,
            int_sol_exe,
            "21\n",
            cwd=str(work_dir),
        )
        assert int_res.verdict == "AC", int_res.detail
        assert int_res.judge_stdout.strip() == "42"

        # 4. CommunicationRunner.run_twice
        rt_res = await CommunicationRunner.run_twice(
            comm_exe,
            comm_sol_exe,
            "9\n",
            cwd=str(work_dir),
        )
        assert rt_res.verdict == "AC", rt_res.detail

        # 5. CommunicationRunner.run_grader_linked
        gl_res = await CommunicationRunner.run_grader_linked(
            grader_linked_exe,
            "11\n",
            cwd=str(work_dir),
        )
        assert gl_res.verdict == "AC", gl_res.detail
        assert gl_res.metrics.get("bits_used") == 4

        # 6. CommunicationRunner.run_multi_process
        mp_res = await CommunicationRunner.run_multi_process(
            mgr_exe,
            [part_exe, part_exe],
            "15\n",
            cwd=str(work_dir),
        )
        assert mp_res.verdict == "AC", mp_res.detail

    # Inspect wrapped calls recorded on both FakeSandboxExecutor and shim JSONL log.
    called_bins = [os.path.basename(c["cmd"][0]) for c in recorder.calls]
    assert any("sol_add" in b for b in called_bins)
    assert any("chk" in b for b in called_bins)
    assert any("interactor" in b for b in called_bins)
    assert any("int_sol" in b for b in called_bins)
    assert any("comm" in b for b in called_bins)
    assert any("comm_sol" in b for b in called_bins)
    assert any("grader_linked_sol" in b for b in called_bins)
    assert any("mgr" in b for b in called_bins)
    assert sum("part" in b for b in called_bins) == 2
    assert all(c["allow_network"] is False for c in recorder.calls)

    # Checker and communicator stages never receive rw_paths.
    chk_calls = [c for c in recorder.calls if os.path.basename(c["cmd"][0]).startswith("chk")]
    comm_calls = [c for c in recorder.calls if os.path.basename(c["cmd"][0]).startswith("comm") and "sol" not in os.path.basename(c["cmd"][0])]
    int_sol_calls = [c for c in recorder.calls if os.path.basename(c["cmd"][0]).startswith("int_sol")]
    assert chk_calls and chk_calls[0]["rw_paths"] == []
    assert comm_calls and all(c["rw_paths"] == [] for c in comm_calls)
    assert int_sol_calls and int_sol_calls[0]["rw_paths"] == []

    # Verify shim actually executed every wrapped subprocess in real OS execution.
    shim_rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(shim_rows) == len(recorder.calls)
    assert any(row["marker"] == "1" for row in shim_rows)

    # CommunicationRunner.run_from_manifest cleanly reports FAIL when manifest requests unavailable backend.
    prob_dir = tmp_path / "prob"
    (prob_dir / "files").mkdir(parents=True)
    (prob_dir / "solutions").mkdir(parents=True)
    manifest = AutoCodeManifest(
        problem_name="comm-sbx",
        problem_type="communication",
        oracle_mode="interactor_only",
        protocol=ProtocolConfig(mode="run_twice"),
        sandbox=SandboxConfig(backend="docker"),
    )
    fail_res = await CommunicationRunner.run_from_manifest(
        str(prob_dir),
        manifest,
        comm_sol_exe,
        "9\n",
        use_system_temp=True,
        communicator_exe_override=comm_exe,
    )
    assert fail_res.verdict == "FAIL"
    assert "docker" in fail_res.detail.lower()


def test_cli_audit_sandbox_flag_and_context_propagation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen_backends: list[tuple[str, bool]] = []

    async def fake_execute(self: Any, **kwargs: Any) -> ToolResult:
        active = get_active_sandbox_executor()
        degraded = active.degraded if isinstance(active, NoopSandboxExecutor) else False
        seen_backends.append((active.name, degraded))
        return ToolResult.ok(decision="go", mode="quick", standalone=True)

    monkeypatch.setattr(audit_cli.ProblemAuditTool, "execute", fake_execute)

    # 1. Explicit unavailable backend exits 1 without calling ProblemAuditTool.execute.
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--standalone", "--sandbox", "bwrap"],
    )
    rc_unavail = audit_cli.main()
    assert rc_unavail == 1
    assert seen_backends == []
    captured_unavail = capsys.readouterr()
    err_json = json.loads(captured_unavail.out)
    assert err_json["success"] is False
    assert "bwrap" in err_json["error"]
    assert "bwrap" in captured_unavail.err

    # 2. --sandbox auto degrades gracefully to none and keeps stdout valid JSON.
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--standalone", "--sandbox", "auto"],
    )
    rc_auto_degraded = audit_cli.main()
    assert rc_auto_degraded == 0
    assert seen_backends[-1] == ("none", True)
    captured_degraded = capsys.readouterr()
    assert json.loads(captured_degraded.out)["success"] is True
    assert "Warning:" in captured_degraded.err

    # 3. --sandbox auto activates host backend when available.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else None)
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--standalone", "--sandbox", "auto"],
    )
    rc_auto_live = audit_cli.main()
    assert rc_auto_live == 0
    assert seen_backends[-1] == ("bwrap", False)
    captured_live = capsys.readouterr()
    assert json.loads(captured_live.out)["success"] is True
    assert captured_live.err == ""

    # 4. Omitting --sandbox preserves default 'none' without warnings.
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--standalone"],
    )
    rc_default = audit_cli.main()
    assert rc_default == 0
    assert seen_backends[-1] == ("none", False)
    captured_default = capsys.readouterr()
    assert json.loads(captured_default.out)["success"] is True
    assert captured_default.err == ""

    # 5. Unavailable explicit backend under --format markdown also exits 1 cleanly.
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        "sys.argv",
        ["autocode-audit", str(tmp_path), "--sandbox", "apple_container", "--format", "markdown"],
    )
    rc_md_unavail = audit_cli.main()
    assert rc_md_unavail == 1
    captured_md = capsys.readouterr()
    assert "❌ ERROR" in captured_md.out
    assert "apple_container" in captured_md.out
    assert "apple_container" in captured_md.err


@pytest.mark.asyncio
async def test_linux_live_bwrap_runner_executes_cpp_and_isolates_fs(tmp_path: Path) -> None:
    """When Linux host has bwrap installed, StandardRunner runs C++ inside bwrap with /etc/shadow hidden."""
    if not sys.platform.startswith("linux") or not shutil.which("bwrap"):
        pytest.skip("Linux live bwrap binary not present")

    from autocode_mcp.utils.compiler import compile_cpp
    from autocode_mcp.utils.judge_runner import StandardRunner

    src = tmp_path / "bwrap_check.cpp"
    exe = tmp_path / "bwrap_check"
    src.write_text(
        "#include <fstream>\n"
        "#include <iostream>\n"
        "int main() {\n"
        '    std::ifstream shadow("/etc/shadow");\n'
        '    int x = 0; std::cin >> x;\n'
        '    std::cout << (shadow.good() ? -1 : x * 2) << "\\n";\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    comp = await compile_cpp(str(src), str(exe), use_cache=False)
    assert comp.success, comp.stderr

    with sandbox_context("bwrap") as executor:
        assert executor is not None and executor.name == "bwrap"
        jres = await StandardRunner.run(
            solution_exe=str(exe),
            input_data="21\n",
            expected_output="42\n",
            time_limit_ms=3000.0,
            memory_limit_mb=256.0,
            cwd=str(tmp_path),
        )
    assert jres.verdict == "AC", jres.detail
    assert jres.stdout.strip() == "42"

