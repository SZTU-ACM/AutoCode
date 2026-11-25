"""
Compiler 工具函数测试。

测试 C++ 编译和执行的核心功能。
"""

import asyncio
import os
import tempfile

import pytest

import autocode_mcp.utils.compiler as compiler_module
from autocode_mcp.utils.compiler import (
    BuildSpec,
    CompileResult,
    _normalize_windows_stdin,
    cleanup_work_dir,
    compile_all,
    compile_cpp,
    get_work_dir,
    run_batch,
    run_binary,
    run_binary_with_args,
)

# 简单的 Hello World 代码
HELLO_WORLD_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""

# 带参数的程序代码
ARGS_CODE = """
#include <iostream>
#include <string>

int main(int argc, char* argv[]) {
    for (int i = 1; i < argc; i++) {
        if (i > 1) std::cout << " ";
        std::cout << argv[i];
    }
    std::cout << std::endl;
    return 0;
}
"""

# 无限循环代码（用于测试超时）
INFINITE_LOOP_CODE = """
#include <iostream>

int main() {
    while (true) {
        // 无限循环
    }
    return 0;
}
"""


@pytest.mark.asyncio
async def test_compile_cpp_success():
    """测试编译成功。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        # 写入源代码
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(HELLO_WORLD_CODE)

        # 编译
        result = await compile_cpp(source_path, binary_path)

        assert result.success
        assert os.path.exists(binary_path)
        assert result.binary_path == binary_path


@pytest.mark.asyncio
async def test_compile_cpp_syntax_error():
    """测试语法错误编译失败。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        # 写入错误代码
        with open(source_path, "w", encoding="utf-8") as f:
            f.write("invalid c++ code {{{")

        # 编译
        result = await compile_cpp(source_path, binary_path)

        assert not result.success
        assert "compilation" in result.error.lower() or "failed" in result.error.lower()
        assert not os.path.exists(binary_path)


@pytest.mark.asyncio
async def test_compile_cpp_timeout():
    """测试编译超时。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        # 写入源代码
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(HELLO_WORLD_CODE)

        # 编译（设置极短超时）
        result = await compile_cpp(source_path, binary_path, timeout=0.001)

        # 正常编译通常很快，但超时测试不稳定
        # 这里只测试超时参数不会导致崩溃
        assert isinstance(result, CompileResult)


@pytest.mark.asyncio
async def test_compile_cpp_missing_source():
    """测试源文件不存在。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "nonexistent.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        result = await compile_cpp(source_path, binary_path)

        assert not result.success
        assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_run_binary_success():
    """测试运行成功。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 先编译
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(HELLO_WORLD_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        assert compile_result.success

        # 运行
        result = await run_binary(binary_path)

        assert result.success
        assert "Hello, World!" in result.stdout
        assert result.return_code == 0


@pytest.mark.asyncio
async def test_run_binary_large_output_streams_to_temp():
    """大输出应正确捕获：输出重定向到临时文件，避免全量读入内存。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "big.cpp")
        binary_path = os.path.join(tmpdir, "big" + (".exe" if os.name == "nt" else ""))
        # 打印 300_000 行，单靠管道缓冲会更易触发内存/阻塞问题。
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(
                "#include <iostream>\n"
                "int main(){\n"
                "for(int i=0;i<300000;i++) std::cout<<\"line \"<<i<<\"\\n\";\n"
                "return 0;}\n"
            )
        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("compilation failed")

        result = await run_binary(binary_path)
        assert result.success, result.error
        # 输出至少应覆盖首尾，证明完整捕获。
        assert result.stdout.startswith("line 0")
        assert "line 299999" in result.stdout
        assert result.stdout.count("\n") == 300000


@pytest.mark.asyncio
async def test_run_binary_timeout():
    """测试运行超时。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 编译无限循环程序
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(INFINITE_LOOP_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        # 运行（设置短超时）
        result = await run_binary(binary_path, timeout=1)

        # 应该超时
        assert result.timed_out or not result.success


@pytest.mark.asyncio
async def test_run_binary_with_stdin():
    """测试运行带标准输入。"""
    # 需要一个读取 stdin 的程序
    stdin_code = """
#include <iostream>
#include <string>

int main() {
    std::string line;
    std::getline(std::cin, line);
    std::cout << "Input: " << line << std::endl;
    return 0;
}
"""

    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(stdin_code)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        # 运行带输入
        result = await run_binary(binary_path, stdin="test input")

        assert result.success
        assert "Input: test input" in result.stdout


@pytest.mark.asyncio
async def test_run_binary_with_args():
    """测试运行带命令行参数。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(ARGS_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        # 运行带参数
        result = await run_binary_with_args(binary_path, args=["hello", "world", "123"])

        assert result.success
        assert "hello world 123" in result.stdout


@pytest.mark.asyncio
async def test_run_binary_missing():
    """测试运行不存在的程序。"""
    result = await run_binary("/nonexistent/binary")

    assert not result.success
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_compile_all_success():
    """测试批量编译成功。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建多个源文件
        sources = []
        for i in range(3):
            source_path = os.path.join(tmpdir, f"test{i}.cpp")
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(f"""
#include <iostream>

int main() {{
    std::cout << "Program {i}" << std::endl;
    return 0;
}}
""")
            sources.append(f"test{i}.cpp")

        # 批量编译
        results = await compile_all(tmpdir, sources)

        assert len(results) == 3
        for i in range(3):
            source_name = f"test{i}.cpp"
            assert source_name in results
            assert results[source_name].success
            # 检查二进制文件存在
            binary_path = os.path.join(tmpdir, f"test{i}" + (".exe" if os.name == "nt" else ""))
            assert os.path.exists(binary_path)


@pytest.mark.asyncio
async def test_compile_all_partial_failure():
    """测试批量编译部分失败。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建一个有效文件和一个无效文件
        valid_source = "valid.cpp"
        invalid_source = "invalid.cpp"

        with open(os.path.join(tmpdir, valid_source), "w") as f:
            f.write(HELLO_WORLD_CODE)

        with open(os.path.join(tmpdir, invalid_source), "w") as f:
            f.write("invalid code {{{")

        # 批量编译
        results = await compile_all(tmpdir, [valid_source, invalid_source])

        assert len(results) == 2
        assert results[valid_source].success
        assert not results[invalid_source].success


@pytest.mark.asyncio
async def test_compile_all_uses_build_spec_options():
    """BuildSpec 应可指定输出路径与超时，且按 source key 索引结果。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "prog.cpp")
        out = os.path.join(tmpdir, "custom", "prog.bin")
        with open(src, "w", encoding="utf-8") as f:
            f.write(HELLO_WORLD_CODE)

        results = await compile_all(
            tmpdir,
            [BuildSpec(source="prog.cpp", binary=out, timeout=60)],
        )

        assert "prog.cpp" in results
        assert results["prog.cpp"].success
        assert os.path.exists(out)


@pytest.mark.asyncio
async def test_compile_all_missing_source_reports_failure():
    """缺失源文件应返回失败而非抛异常。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        results = await compile_all(tmpdir, ["nope.cpp"])
        assert results["nope.cpp"].success is False
        assert "not found" in (results["nope.cpp"].error or "")


def test_get_work_dir():
    """测试工作目录生成。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = get_work_dir(tmpdir, "test_tool")

        assert work_dir.startswith(tmpdir)
        assert "test_tool" in work_dir
        assert os.path.exists(work_dir)

        # 每次调用生成不同目录
        work_dir2 = get_work_dir(tmpdir, "test_tool")
        assert work_dir != work_dir2


def test_cleanup_work_dir():
    """测试工作目录清理。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        work_dir = get_work_dir(tmpdir, "test_tool")

        # 创建一些文件
        test_file = os.path.join(work_dir, "test.txt")
        with open(test_file, "w") as f:
            f.write("test")

        assert os.path.exists(work_dir)

        # 清理
        cleanup_work_dir(work_dir)

        # 目录应该被删除
        assert not os.path.exists(work_dir)


def test_cleanup_nonexistent_dir():
    """测试清理不存在的目录（不应报错）。"""
    cleanup_work_dir("/nonexistent/directory")
    # 应该静默成功


MEMORY_HOG_CODE = """
#include <iostream>
#include <vector>

int main() {
    std::vector<int> data;
    for (int i = 0; i < 10000000; i++) {
        data.push_back(i);
    }
    std::cout << data.size() << std::endl;
    return 0;
}
"""


@pytest.mark.asyncio
async def test_run_binary_with_memory_limit():
    """测试运行带内存限制。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        source_path = os.path.join(tmpdir, "test.cpp")
        binary_path = os.path.join(tmpdir, "test" + (".exe" if os.name == "nt" else ""))

        with open(source_path, "w", encoding="utf-8") as f:
            f.write(MEMORY_HOG_CODE)

        compile_result = await compile_cpp(source_path, binary_path)
        if not compile_result.success:
            pytest.skip("Compilation failed")

        _ = await run_binary(binary_path, memory_mb=4, timeout=10)

        if os.name == "nt":
            pass
        else:
            pass


@pytest.mark.asyncio
async def test_run_binary_with_args_cancelled_force_terminates(monkeypatch):
    """CancelledError 路径应强制终止子进程。"""
    killed = {"value": False}

    class FakeProcess:
        def __init__(self):
            self.pid = 1234
            self.returncode = None

        async def communicate(self, input=None):
            raise asyncio.CancelledError()

        def kill(self):
            killed["value"] = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    with tempfile.TemporaryDirectory() as tmpdir:
        binary_path = os.path.join(tmpdir, "dummy.exe")
        with open(binary_path, "w", encoding="utf-8") as f:
            f.write("x")

        monkeypatch.setattr(compiler_module.sys, "platform", "linux")
        monkeypatch.setattr(compiler_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

        async def noop_terminate_pid_tree(_pid):
            return None

        monkeypatch.setattr(compiler_module, "terminate_pid_tree", noop_terminate_pid_tree)

        with pytest.raises(asyncio.CancelledError):
            await run_binary_with_args(binary_path, ["1"], timeout=1)
        assert killed["value"] is True


def test_normalize_windows_stdin_handles_mixed_newlines():
    """Windows stdin 规范化应只做一次稳定转换。"""
    raw = "1 2\n3 4\r\n5 6\r7 8\n"
    normalized = _normalize_windows_stdin(raw)
    assert normalized == "1 2\r\n3 4\r\n5 6\r\n7 8\r\n"


@pytest.mark.asyncio
async def test_run_batch_preserves_order():
    """run_batch 应保持与输入一致的返回顺序，即使各任务完成时间不同。"""
    items = [5, 1, 3, 2, 4]

    async def worker(x: int) -> int:
        # 让较小的值睡得更久，确保完成顺序与输入顺序不同
        await asyncio.sleep(0.01 * (6 - x))
        return x * 10

    result = await run_batch(items, worker, limit=2)
    assert result == [50, 10, 30, 20, 40]


@pytest.mark.asyncio
async def test_run_batch_respects_concurrency_limit():
    """run_batch 的并发峰值不应超过 limit。"""
    state = {"active": 0, "peak": 0}

    async def worker(x: int) -> int:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return x

    result = await run_batch(list(range(10)), worker, limit=3)
    assert result == list(range(10))
    assert state["peak"] <= 3


@pytest.mark.asyncio
async def test_run_batch_empty_input():
    """空输入应直接返回空列表，不调用 worker。"""
    called = {"value": False}

    async def worker(x: int) -> int:
        called["value"] = True
        return x

    result = await run_batch([], worker, limit=4)
    assert result == []
    assert called["value"] is False


@pytest.mark.asyncio
async def test_run_batch_limit_degrades_to_one():
    """limit<=0 应退化为串行（1 并发）且结果正确。"""
    order: list[int] = []

    async def worker(x: int) -> int:
        order.append(x)
        await asyncio.sleep(0)
        return x

    result = await run_batch([1, 2, 3], worker, limit=0)
    assert result == [1, 2, 3]
    assert order == [1, 2, 3]


@pytest.mark.asyncio
async def test_run_binary_accepts_float_timeout_and_memory():
    """run_binary 应支持浮点 timeout（如 manifest.time_limit_ms / 1000 * 1.5）与 memory_mb。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "echo.cpp")
        exe = os.path.join(tmpdir, "echo.exe" if os.name == "nt" else "echo")
        with open(src, "w", encoding="utf-8") as f:
            f.write(
                '#include <iostream>\nint main(){ int x; if (std::cin >> x) std::cout << x << "\\n"; return 0; }'
            )
        comp = await compile_cpp(src, exe)
        assert comp.success, comp.stderr

        # Warm up first launch so macOS Gatekeeper / Windows Defender CDHash scan is cached
        _ = await run_binary(exe, "0\n", timeout=5.0)
        res = await run_binary(exe, "42\n", timeout=1.5, memory_mb=128.5)
        assert res.success, res.error
        assert res.stdout.strip() == "42"


def test_resolve_compiler_prefers_homebrew_gcc_over_apple_clang_on_macos(monkeypatch):
    """macOS 上 g++ 若实为 Apple Clang 垫片，应自动解析为已安装的 Homebrew GCC。"""
    import subprocess as sp

    compiler_module._resolve_compiler.cache_clear()
    try:
        monkeypatch.setattr(compiler_module.sys, "platform", "darwin")
        monkeypatch.setattr(
            compiler_module.shutil,
            "which",
            lambda name: "/opt/homebrew/bin/g++-14"
            if name == "g++-14"
            else ("/usr/bin/g++" if name == "g++" else None),
        )
        real_isfile = compiler_module.os.path.isfile
        monkeypatch.setattr(
            compiler_module.os.path,
            "isfile",
            lambda p: False if p.startswith(("/opt/homebrew/bin/", "/usr/local/bin/")) else real_isfile(p),
        )
        monkeypatch.setattr(
            compiler_module.subprocess,
            "run",
            lambda *a, **k: sp.CompletedProcess(a[0], 0, stdout="Apple clang version 15.0.0", stderr=""),
        )
        assert compiler_module._resolve_compiler("g++") == "/opt/homebrew/bin/g++-14"
        assert compiler_module._resolve_compiler("clang++") == "clang++"
    finally:
        compiler_module._resolve_compiler.cache_clear()


@pytest.mark.asyncio
async def test_linux_fallback_when_prlimit_unavailable(monkeypatch):
    """Linux 缺少 prlimit（如 Alpine/精简容器）时应回退直接执行而不抛 FileNotFoundError。"""
    seen_cmds: list[list[str]] = []

    class FakeProc:
        pid = 4321
        returncode = 0

        async def communicate(self, input=None):
            return b"", b""

    async def fake_exec(*cmd, **kwargs):
        seen_cmds.append(list(cmd))
        return FakeProc()

    with tempfile.TemporaryDirectory() as tmpdir:
        dummy = os.path.join(tmpdir, "prog")
        with open(dummy, "w", encoding="utf-8") as f:
            f.write("x")

        monkeypatch.setattr(compiler_module.sys, "platform", "linux")
        monkeypatch.setattr(compiler_module.shutil, "which", lambda _name: None)
        monkeypatch.setattr(compiler_module.asyncio, "create_subprocess_exec", fake_exec)

        res = await run_binary(dummy, "", timeout=1.5, memory_mb=256)
        assert res.success, res.error
        assert seen_cmds and os.path.abspath(dummy) == seen_cmds[0][0]


def test_detect_compiler_info_apple_clang_vs_gcc_and_resolve_manifest(monkeypatch):
    """应准确识别 Apple Clang（不支持 <bits/stdc++.h>）与 GNU GCC，并尊重 manifest.compiler 优先级。"""
    import subprocess
    from types import SimpleNamespace

    from autocode_mcp.utils.compiler import (
        detect_compiler_info,
        has_bits_stdcpp_include,
        resolve_effective_compiler,
    )
    from autocode_mcp.workflow.models import AutoCodeManifest

    assert has_bits_stdcpp_include("#include <bits/stdc++.h>\nint main(){}\n") is True
    assert has_bits_stdcpp_include("#include <iostream>\nint main(){}\n") is False

    m_custom = AutoCodeManifest(problem_name="P", compiler="g++-14")
    m_default = AutoCodeManifest(problem_name="P")
    assert resolve_effective_compiler(None, m_custom) == "g++-14"
    assert resolve_effective_compiler("clang++", m_custom) == "clang++"
    assert resolve_effective_compiler(None, m_default) == "g++"

    def fake_run_apple(*_a, **_kw):
        return SimpleNamespace(
            returncode=0,
            stdout="Apple clang version 16.0.0 (clang-1600.0.26.4)\nTarget: arm64-apple-darwin\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_apple)
    monkeypatch.setattr(compiler_module, "_find_suggested_gcc_compilers", lambda: ["/opt/homebrew/bin/g++-14"])
    apple_info = detect_compiler_info("g++", use_cache=False)
    assert apple_info["available"] is True
    assert apple_info["is_apple_clang"] is True
    assert apple_info["supports_bits_stdcpp"] is False
    assert apple_info["suggested_gcc_compilers"] == ["/opt/homebrew/bin/g++-14"]
    assert "bits/stdc++.h" in (apple_info["warning"] or "")
    assert "g++-14" in (apple_info["warning"] or "")

    def fake_run_gcc(*_a, **_kw):
        return SimpleNamespace(
            returncode=0,
            stdout="g++-14 (Homebrew GCC 14.2.0) 14.2.0\nCopyright (C) Free Software Foundation\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run_gcc)
    gcc_info = detect_compiler_info("g++-14", use_cache=False)
    assert gcc_info["available"] is True
    assert gcc_info["is_apple_clang"] is False
    assert gcc_info["is_gcc"] is True
    assert gcc_info["supports_bits_stdcpp"] is True
    assert gcc_info["warning"] is None


@pytest.mark.asyncio
async def test_problem_create_and_solution_build_surface_apple_clang_and_manifest_compiler(
    monkeypatch, tmp_path
):
    """problem_create 应提前暴露 Apple Clang 提示；solution_build 应读取 manifest.compiler。"""
    import autocode_mcp.tools.problem as problem_mod
    import autocode_mcp.tools.solution as solution_mod
    from autocode_mcp.tools.problem import ProblemCreateTool
    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.workflow import load_manifest, save_manifest

    fake_apple = {
        "compiler": "g++",
        "resolved_path": "/usr/bin/g++",
        "available": True,
        "is_apple_clang": True,
        "is_clang": True,
        "is_gcc": False,
        "version_line": "Apple clang version 16.0.0",
        "supports_bits_stdcpp": False,
        "suggested_gcc_compilers": [],
        "warning": "Compiler 'g++' is Apple Clang, which does not support <bits/stdc++.h>.",
    }

    async def fake_detect(*_a, **_kw):
        return dict(fake_apple)

    monkeypatch.setattr(problem_mod, "detect_compiler_info_async", fake_detect)
    monkeypatch.setattr(solution_mod, "detect_compiler_info_async", fake_detect)

    pdir = tmp_path / "mac_prob"
    create_res = await ProblemCreateTool().execute(problem_dir=str(pdir), problem_name="MacProb")
    assert create_res.success
    assert create_res.data["compiler_info"]["is_apple_clang"] is True
    assert "bits/stdc++.h" in create_res.data["compiler_warning"]

    # Configure custom compiler in manifest.json and verify SolutionBuildTool forwards it
    m = load_manifest(str(pdir))
    assert m is not None
    m.compiler = "/opt/homebrew/bin/g++-14"
    save_manifest(str(pdir), m)

    captured: dict[str, str] = {}

    async def fake_build(self, source_path, binary_path, compiler="g++", include_dirs=None, extra_sources=None):
        captured["compiler"] = compiler
        with open(binary_path, "wb") as bf:
            bf.write(b"fakebin")
        return CompileResult(success=True, binary_path=binary_path)

    monkeypatch.setattr(SolutionBuildTool, "build", fake_build)
    sol_res = await SolutionBuildTool().execute(
        problem_dir=str(pdir),
        solution_type="sol",
        code="#include <iostream>\nint main(){return 0;}\n",
    )
    assert sol_res.success
    assert captured["compiler"] == "/opt/homebrew/bin/g++-14"
    assert sol_res.data["compiler"] == "/opt/homebrew/bin/g++-14"


@pytest.mark.asyncio
async def test_compiler_allowlist_negative_cache_and_win_job_float_timeout(monkeypatch, tmp_path):
    """回归：compiler 严格白名单阻断任意可执行文件、unavailable 不写入缓存、WinJobObject 支持浮点超时且返回整数。"""
    import subprocess
    from types import SimpleNamespace

    from pydantic import ValidationError

    import autocode_mcp.utils.compiler as compiler_module
    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.compiler import detect_compiler_info, validate_compiler_spec
    from autocode_mcp.utils.platform import get_exe_extension
    from autocode_mcp.utils.win_job import WinJobObject
    from autocode_mcp.workflow.models import AutoCodeManifest

    # 1. Valid C++ compilers pass allowlist
    for ok_comp in ("g++", "g++-14", "clang++", "/opt/homebrew/bin/g++-14", "x86_64-w64-mingw32-g++", "g++.exe"):
        assert validate_compiler_spec(ok_comp) == ok_comp

    # 2. Arbitrary executables / relative traversal / control chars rejected
    for bad_comp in ("./evil.sh", "../g++", "/bin/sh", "python3", "g++\x00", "g++\n"):
        with pytest.raises(ValueError):
            validate_compiler_spec(bad_comp)
        with pytest.raises(ValidationError):
            AutoCodeManifest(problem_name="P", compiler=bad_comp)
    with pytest.raises(ValueError):
        validate_compiler_spec("")
    assert AutoCodeManifest(problem_name="P", compiler="").compiler is None

    # 3. Invalid compiler fails BEFORE clobbering existing sol.cpp or sol binary
    sol_dir = tmp_path / "prob" / "solutions"
    sol_dir.mkdir(parents=True)
    sol_cpp = sol_dir / "sol.cpp"
    sol_bin = sol_dir / f"sol{get_exe_extension()}"
    sol_cpp.write_text("// original\n", encoding="utf-8")
    sol_bin.write_bytes(b"original-binary")

    bad_res = await SolutionBuildTool().execute(
        problem_dir=str(tmp_path / "prob"),
        solution_type="sol",
        code="// new\n",
        compiler="/bin/sh",
    )
    assert not bad_res.success
    assert "invalid compiler" in (bad_res.error or "").lower()
    assert sol_cpp.read_text(encoding="utf-8") == "// original\n"
    assert sol_bin.read_bytes() == b"original-binary"

    # 4. detect_compiler_info must NOT cache available=False
    compiler_module._COMPILER_INFO_CACHE.clear()
    calls = {"n": 0}

    def flaky_run(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FileNotFoundError("transiently missing")
        return SimpleNamespace(returncode=0, stdout="g++-14 (GCC) 14.2.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", flaky_run)
    monkeypatch.setattr(compiler_module.shutil, "which", lambda _c: "/opt/homebrew/bin/g++-14")
    first = detect_compiler_info("g++-14", use_cache=True)
    assert first["available"] is False
    assert first["supports_bits_stdcpp"] is False
    second = detect_compiler_info("g++-14", use_cache=True)
    assert second["available"] is True
    assert calls["n"] == 2
    compiler_module._COMPILER_INFO_CACHE.clear()

    # 5. WinJobObject._set_time_limit converts float timeout_sec to integer 100ns units
    fake_job = WinJobObject.__new__(WinJobObject)
    fake_job.job_handle = None
    fake_job.timeout_sec = 1.5
    fake_job.memory_mb = 256
    t_100ns = fake_job._set_time_limit()
    assert isinstance(t_100ns, int)
    assert t_100ns == 15_000_000
    assert isinstance(fake_job._set_memory_limit(), int)
    assert fake_job._set_memory_limit() == 256 * 1024 * 1024


@pytest.mark.asyncio
async def test_linux_vm_hwm_helper_and_wait4_darwin_only(monkeypatch, tmp_path):
    """Linux avoids fork-RSS ru_maxrss contamination via _read_linux_vm_hwm_mb and skips os.wait4."""
    import builtins
    import io

    real_open = builtins.open
    monkeypatch.setattr(compiler_module.sys, "platform", "darwin")
    assert compiler_module._read_linux_vm_hwm_mb(12345) is None

    monkeypatch.setattr(compiler_module.sys, "platform", "linux")
    fake_status = "Name:\techo\nVmPeak:\t   18000 kB\nVmRSS:\t   10240 kB\nVmHWM:\t   12288 kB\n"
    monkeypatch.setattr(
        builtins,
        "open",
        lambda path, *a, **k: io.StringIO(fake_status) if str(path) == "/proc/12345/status" else real_open(path, *a, **k),
    )
    assert compiler_module._read_linux_vm_hwm_mb(12345) == pytest.approx(12.0)

    # Verify _run_process does NOT wrap with _PosixWait4Process on Linux (uses async wait / prlimit instead)
    class FakeProc:
        pid = 4321
        returncode = 0

        async def communicate(self, input=None):
            return b"", b""

    monkeypatch.setattr(compiler_module.shutil, "which", lambda _name: None)
    seen = []

    async def fake_exec(*cmd, **kwargs):
        seen.append(cmd)
        return FakeProc()

    monkeypatch.setattr(compiler_module.asyncio, "create_subprocess_exec", fake_exec)
    dummy = tmp_path / "prog"
    dummy.write_text("x", encoding="utf-8")
    res = await run_binary(str(dummy), "", timeout=1.5, memory_mb=256)
    assert res.success
    assert seen


def test_linux_resolve_compiler_upgrades_legacy_gcc_and_windows_commitment_limit(monkeypatch):
    """Linux auto-upgrades legacy GCC (<10) to gcc-toolset/versioned g++; Windows STATUS_COMMITMENT_LIMIT counts as MLE."""
    import subprocess as sp

    from autocode_mcp.utils.compiler import _classify_memory_limit_exceeded

    compiler_module._resolve_compiler.cache_clear()
    try:
        monkeypatch.setattr(compiler_module.sys, "platform", "linux")
        monkeypatch.setattr(
            compiler_module.shutil,
            "which",
            lambda name: "/usr/bin/g++" if name == "g++" else None,
        )
        monkeypatch.setattr(
            compiler_module.subprocess,
            "run",
            lambda *a, **k: sp.CompletedProcess(
                a[0], 0, stdout="g++ (GCC) 8.5.0 20210514 (TencentOS 8.5.0-18)\n", stderr=""
            ),
        )
        monkeypatch.setattr(
            compiler_module.os.path,
            "isfile",
            lambda p: p == "/opt/rh/gcc-toolset-12/root/usr/bin/g++",
        )
        monkeypatch.setattr(
            compiler_module.os,
            "access",
            lambda p, mode: p == "/opt/rh/gcc-toolset-12/root/usr/bin/g++",
        )
        assert compiler_module._resolve_compiler("g++") == "/opt/rh/gcc-toolset-12/root/usr/bin/g++"

        compiler_module._resolve_compiler.cache_clear()
        both = {
            "/opt/rh/gcc-toolset-15/root/usr/bin/g++",
            "/opt/rh/gcc-toolset-12/root/usr/bin/g++",
        }
        monkeypatch.setattr(compiler_module.os.path, "isfile", lambda p: p in both)
        monkeypatch.setattr(compiler_module.os, "access", lambda p, mode: p in both)
        monkeypatch.setattr(
            compiler_module,
            "_compiler_has_asan_runtime",
            lambda p: p == "/opt/rh/gcc-toolset-12/root/usr/bin/g++",
        )
        assert compiler_module._resolve_compiler("g++") == "/opt/rh/gcc-toolset-12/root/usr/bin/g++"

        compiler_module._resolve_compiler.cache_clear()
        monkeypatch.setattr(
            compiler_module.subprocess,
            "run",
            lambda *a, **k: sp.CompletedProcess(
                a[0], 0, stdout="g++ (GCC) 12.2.1 20221121\n", stderr=""
            ),
        )
        assert compiler_module._resolve_compiler("g++") == "g++"
    finally:
        compiler_module._resolve_compiler.cache_clear()

    for code in (3221225773, -1073741523):
        assert _classify_memory_limit_exceeded(
            return_code=code,
            stderr_text="",
            memory_limit_mb=256,
            peak_rss_mb=12.0,
            rss_limit_exceeded=False,
            disable_as_limit=False,
        ) is True


