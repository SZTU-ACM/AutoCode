"""Regression tests for Track T0 base contracts, hygiene, resource limits, and schema freeze."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocode_mcp.tools.problem_generate import ProblemGenerateTestsTool
from autocode_mcp.tools.stress_test import StressTestRunTool
from autocode_mcp.tools.validation import ProblemValidateTool
from autocode_mcp.utils.cache import CompileCache, default_compile_cache_dir
from autocode_mcp.utils.compiler import compile_cpp, run_binary
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.utils.resource_limit import get_resource_limit, load_problem_config
from autocode_mcp.workflow.manifest import load_manifest, save_manifest
from autocode_mcp.workflow.models import (
    AuditGates,
    AutoCodeManifest,
    SandboxConfig,
    SolutionEntry,
    validate_executable_solution_language,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _snapshot_tree(root: Path) -> dict[str, tuple[str, int | None]]:
    """Capture relative paths and sizes/types in ``root`` to detect any writes."""
    snap: dict[str, tuple[str, int | None]] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            snap[rel] = ("dir", None)
        else:
            snap[rel] = ("file", p.stat().st_size)
    return snap


# ============== Issue #13: CompileCache zero-write & default cache dir ==============


def test_import_compiler_and_init_cache_are_zero_write(tmp_path: Path):
    """Importing compiler and constructing CompileCache must not create .cache or any cwd files."""
    work_dir = tmp_path / "external_prob"
    work_dir.mkdir()
    isolated_cache = tmp_path / "isolated_cache"

    env = dict(os.environ)
    env["AUTOCODE_CACHE_DIR"] = str(isolated_cache)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import autocode_mcp.utils.compiler as c; "
                "from autocode_mcp.utils.cache import CompileCache; "
                "cc = CompileCache(); "
                "assert not cc.cache_dir.exists()"
            ),
        ],
        cwd=str(work_dir),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert list(work_dir.iterdir()) == []
    assert not isolated_cache.exists()


def test_default_compile_cache_dir_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Default compile cache resolves AUTOCODE_CACHE_DIR -> XDG_CACHE_HOME -> OS cache dir."""
    custom_env = tmp_path / "custom_autocode_cache"
    xdg_dir = tmp_path / "xdg_cache"

    monkeypatch.setenv("AUTOCODE_CACHE_DIR", str(custom_env))
    monkeypatch.setenv("XDG_CACHE_HOME", str(xdg_dir))
    assert default_compile_cache_dir() == custom_env

    monkeypatch.delenv("AUTOCODE_CACHE_DIR", raising=False)
    assert default_compile_cache_dir() == xdg_dir / "autocode-mcp" / "compile"

    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    resolved = default_compile_cache_dir()
    assert resolved.is_absolute()
    assert ".cache/compile" not in resolved.as_posix()

    # Explicit constructor override must still be respected and lazy.
    explicit_dir = tmp_path / "explicit_override"
    cache = CompileCache(str(explicit_dir))
    assert cache.cache_dir == explicit_dir
    assert not explicit_dir.exists()


def test_standalone_audit_cli_cwd_zero_write(tmp_path: Path):
    """Running `autocode-audit . --standalone` with cwd=<problem_dir> writes zero files inside it."""
    sample_src = REPO_ROOT / "examples" / "exact-sample"
    target_dir = tmp_path / "exact_copy"
    shutil.copytree(sample_src, target_dir)
    tests_dir = target_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "01.in").write_text("3\n1000000000 1000000000 1000000000\n", encoding="utf-8")
    (tests_dir / "01.ans").write_text("3000000000\n", encoding="utf-8")
    before = _snapshot_tree(target_dir)

    isolated_cache = tmp_path / "user_compile_cache"
    env = dict(os.environ)
    env["AUTOCODE_CACHE_DIR"] = str(isolated_cache)

    proc = subprocess.run(
        [sys.executable, "-m", "autocode_mcp.cli.audit", ".", "--standalone", "--format", "json"],
        cwd=str(target_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode in (0, 2), f"stdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert "validator_check" in payload["data"]["quality_signals"]

    after = _snapshot_tree(target_dir)
    assert after == before
    assert not (target_dir / ".cache").exists()


# ============== Issue #18: resource_limit, memory_mb passthrough, MLE attribution ==============


def test_resource_limit_reads_manifest_and_ignores_problem_yaml(tmp_path: Path):
    """resource_limit reads .autocode/manifest.json and ignores legacy problem.yaml."""
    (tmp_path / "problem.yaml").write_text("time_limit: 99\nmemory_limit: 999\n", encoding="utf-8")
    # Without .autocode/manifest.json, graceful defaults apply (problem.yaml is ignored).
    missing_cfg = load_problem_config(tmp_path)
    assert missing_cfg.time_limit is None
    assert missing_cfg.memory_limit is None
    missing_limit = get_resource_limit(str(tmp_path), "sol")
    assert missing_limit.timeout_sec == 2
    assert missing_limit.memory_mb == 256

    # With .autocode/manifest.json, time_limit_ms & memory_limit_mb are honored.
    save_manifest(
        str(tmp_path),
        AutoCodeManifest(
            problem_name="Mem Check",
            time_limit_ms=1500,
            memory_limit_mb=512,
        ),
    )
    cfg = load_problem_config(tmp_path)
    assert cfg.time_limit == 1.5
    assert cfg.memory_limit == 512

    sol_limit = get_resource_limit(str(tmp_path), "sol")
    assert sol_limit.timeout_sec == 1.5
    assert sol_limit.memory_mb == 512

    wrong_limit = get_resource_limit(str(tmp_path), "wrong")
    assert wrong_limit.memory_mb == 512


@pytest.mark.asyncio
async def test_tools_pass_manifest_memory_mb_to_run_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """stress_test.py, problem_generate.py, and validation.py pass manifest.memory_limit_mb."""
    import autocode_mcp.tools.problem as problem_facade
    import autocode_mcp.tools.problem_generate as pg_mod
    import autocode_mcp.tools.stress_test as st_mod
    import autocode_mcp.tools.validation as val_mod

    manifest = AutoCodeManifest(
        problem_name="Memory Passthrough",
        time_limit_ms=2000,
        memory_limit_mb=512,
    )
    save_manifest(str(tmp_path), manifest)
    exe_ext = get_exe_extension()
    sol_src = tmp_path / "solutions" / "sol.cpp"
    brute_src = tmp_path / "solutions" / "brute.cpp"
    gen_src = tmp_path / "files" / "gen.cpp"
    sol_src.parent.mkdir(parents=True, exist_ok=True)
    gen_src.parent.mkdir(parents=True, exist_ok=True)

    cpp_echo = (
        "#include <iostream>\n"
        "int main() { int x; if (std::cin >> x) std::cout << x << '\\n'; return 0; }\n"
    )
    gen_cpp = (
        "#include <iostream>\n"
        "int main() { std::cout << 42 << '\\n'; return 0; }\n"
    )
    sol_src.write_text(cpp_echo, encoding="utf-8")
    brute_src.write_text(cpp_echo, encoding="utf-8")
    gen_src.write_text(gen_cpp, encoding="utf-8")

    sol_exe = str(tmp_path / "solutions" / f"sol{exe_ext}")
    brute_exe = str(tmp_path / "solutions" / f"brute{exe_ext}")
    gen_exe = str(tmp_path / "files" / f"gen{exe_ext}")
    assert (await compile_cpp(str(sol_src), sol_exe, use_cache=False)).success
    assert (await compile_cpp(str(brute_src), brute_exe, use_cache=False)).success
    assert (await compile_cpp(str(gen_src), gen_exe, use_cache=False)).success

    seen_calls: list[tuple[str, float]] = []
    orig_run_binary = run_binary

    async def recording_run_binary(
        binary_path: str,
        stdin: str = "",
        timeout: int | float = 5,
        memory_mb: int | float = 256,
        **kwargs: Any,
    ) -> Any:
        seen_calls.append((Path(binary_path).name, float(memory_mb)))
        return await orig_run_binary(
            binary_path,
            stdin=stdin,
            timeout=timeout,
            memory_mb=memory_mb,
            **kwargs,
        )

    monkeypatch.setattr(st_mod, "run_binary", recording_run_binary)
    monkeypatch.setattr(pg_mod, "run_binary", recording_run_binary)
    monkeypatch.setattr(problem_facade, "run_binary", recording_run_binary)
    monkeypatch.setattr(val_mod, "run_binary", recording_run_binary)

    # 1) stress_test._run_round -> sol_exe & brute_exe
    st_tool = StressTestRunTool()
    round_res = await st_tool._run_round(
        gen_exe=gen_exe,
        val_exe=str(tmp_path / "files" / f"missing_val{exe_ext}"),
        sol_exe=sol_exe,
        brute_exe=brute_exe,
        global_round=1,
        temp_dir=str(tmp_path),
        timeout=5,
        n_max=10,
        generator_args=None,
        types=None,
        use_checker_stress=False,
        checker_exe="",
        is_interactive=False,
        is_communication=False,
        interactor_exe="",
        run_brute=True,
        problem_dir=str(tmp_path),
        manifest_model=manifest,
        sol_name="sol",
        brute_name="brute",
    )
    assert round_res.status == "ok"

    # 2) problem_generate._process_candidate -> sol_exe
    pg_tool = ProblemGenerateTestsTool()
    cand = await pg_tool._process_candidate(
        1,
        ("0", "1", "1", "10", "1", "1", []),
        gen_exe,
        sol_exe,
        None,
        5,
        False,
        set(),
        manifest_model=manifest,
        problem_dir=str(tmp_path),
    )
    assert cand.error is None and cand.candidate is not None

    # 3) validation.execute -> _validate_statement_samples & _validate_sample_files
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "01.in").write_text("7\n", encoding="utf-8")
    (tests_dir / "01.ans").write_text("7\n", encoding="utf-8")
    val_tool = ProblemValidateTool()
    val_res = await val_tool.execute(
        problem_dir=str(tmp_path),
        validate_types=["all"],
        statement_samples=[{"input": "9\n", "expected_output": "9\n"}],
        timeout=5,
    )
    assert val_res.success, val_res.error

    sol_calls = [mem for name, mem in seen_calls if name.startswith("sol")]
    brute_calls = [mem for name, mem in seen_calls if name.startswith("brute")]
    assert len(sol_calls) == 4  # stress_test + problem_generate + 2x validation
    assert all(mem == 512.0 for mem in sol_calls), seen_calls
    assert brute_calls == [512.0], seen_calls


@pytest.mark.asyncio
async def test_external_sigkill_with_low_rss_not_misclassified_as_mle(tmp_path: Path):
    """External SIGKILL while peak_rss_mb is far below memory_mb must NOT report MLE."""
    if sys.platform == "win32":
        pytest.skip("POSIX SIGKILL test")

    src = tmp_path / "sleeper.cpp"
    exe = tmp_path / "sleeper"
    src.write_text(
        "#include <chrono>\n#include <thread>\n"
        "int main() { std::this_thread::sleep_for(std::chrono::seconds(5)); return 0; }\n",
        encoding="utf-8",
    )
    comp = await compile_cpp(str(src), str(exe), use_cache=False)
    assert comp.success, comp.stderr

    loop = asyncio.get_running_loop()

    def _kill_soon(pid: int) -> None:
        loop.call_later(0.15, lambda: os.kill(pid, signal.SIGKILL))

    res = await run_binary(
        str(exe),
        "",
        timeout=5,
        memory_mb=256,
        process_start_hook=_kill_soon,
    )
    assert not res.success
    assert res.return_code == -signal.SIGKILL
    assert res.memory_mb is not None and res.memory_mb < 64.0
    assert res.memory_limit_exceeded is False


@pytest.mark.asyncio
async def test_genuine_rss_overflow_still_flags_mle(tmp_path: Path):
    """Genuine RSS overflow (including short-lived warmed -O2 binaries) must reliably flag MLE."""
    src = tmp_path / "hog.cpp"
    exe = tmp_path / ("hog.exe" if sys.platform == "win32" else "hog")
    src.write_text(
        "#include <cstring>\n#include <iostream>\n#include <vector>\n"
        "int main() {\n"
        "    std::vector<char> buf(48 * 1024 * 1024, 1);\n"
        "    volatile char sum = 0;\n"
        "    for (size_t i = 0; i < buf.size(); i += 4096) sum += buf[i];\n"
        "    std::cout << (int)sum << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    comp = await compile_cpp(str(src), str(exe), opt_level="O2", use_cache=False)
    assert comp.success, comp.stderr

    for run_idx in range(10):
        res = await run_binary(str(exe), "", timeout=10, memory_mb=8)
        assert not res.success, f"run {run_idx} unexpectedly succeeded: {res}"
        assert res.memory_limit_exceeded is True, f"run {run_idx} missed MLE: {res}"
        assert res.memory_mb is not None and res.memory_mb > 8.0, f"run {run_idx}: {res}"


def test_mle_classifier_distinguishes_external_sigkill_from_oom_evidence():
    """Verify _classify_memory_limit_exceeded handles OOM markers, Windows codes, and RSS."""
    from autocode_mcp.utils.compiler import _classify_memory_limit_exceeded

    # External SIGKILL (-9 or 137) with low RSS or unknown RSS must not be labeled MLE.
    assert (
        _classify_memory_limit_exceeded(
            return_code=-9,
            stderr_text="",
            memory_limit_mb=256,
            peak_rss_mb=3.5,
            rss_limit_exceeded=False,
            disable_as_limit=False,
        )
        is False
    )
    assert (
        _classify_memory_limit_exceeded(
            return_code=137,
            stderr_text="",
            memory_limit_mb=256,
            peak_rss_mb=None,
            rss_limit_exceeded=False,
            disable_as_limit=False,
        )
        is False
    )

    # Near-limit SIGKILL (>= 85% RSS) is treated as MLE.
    assert (
        _classify_memory_limit_exceeded(
            return_code=-9,
            stderr_text="",
            memory_limit_mb=256,
            peak_rss_mb=240.0,
            rss_limit_exceeded=False,
            disable_as_limit=False,
        )
        is True
    )

    # Explicit OOM diagnostics and Windows STATUS_NO_MEMORY always classify as MLE.
    assert (
        _classify_memory_limit_exceeded(
            return_code=-6,
            stderr_text="terminate called after throwing an instance of 'std::bad_alloc'",
            memory_limit_mb=256,
            peak_rss_mb=2.0,
            rss_limit_exceeded=False,
            disable_as_limit=False,
        )
        is True
    )
    assert (
        _classify_memory_limit_exceeded(
            return_code=3221225495,
            stderr_text="",
            memory_limit_mb=256,
            peak_rss_mb=None,
            rss_limit_exceeded=False,
            disable_as_limit=False,
        )
        is True
    )


# ============== Issue #20 & Schema Freeze ==============


def test_all_example_manifests_load_cleanly_after_schema_freeze():
    """All bundled example manifests remain 100% backward compatible."""
    for sample_dir in sorted((REPO_ROOT / "examples").iterdir()):
        manifest_file = sample_dir / ".autocode" / "manifest.json"
        if manifest_file.is_file():
            loaded = load_manifest(str(sample_dir))
            assert loaded is not None, f"Failed to load {manifest_file}"


def test_additive_schema_fields_and_python_language_guard():
    """Additive Wave 1-2 fields validate cleanly, and Python runtime execution is rejected."""
    manifest = AutoCodeManifest(
        problem_name="Schema Freeze",
        multi_test=True,
        scale_token_index=0,
        sandbox=SandboxConfig(backend="bwrap", extra_args=["--unshare-net"]),
        time_limit_multipliers={"cpp": 1.0, "python": 3.0},
        solutions=[
            SolutionEntry(
                name="sol",
                role="main",
                language="cpp",
                path="solutions/sol.cpp",
                expected_verdict="AC",
            ),
            SolutionEntry(
                name="py_sol",
                role="reference",
                language="python",
                path="solutions/sol.py",
                expected_verdict="AC",
            ),
            SolutionEntry(
                name="wa_sol",
                role="wrong",
                language="cpp",
                path="solutions/wa.cpp",
                expected="fail",
                expected_verdict="WA",
            ),
        ],
        audit_gates=AuditGates(
            fail_on_mutation_survival=True,
            min_branch_coverage=0.85,
        ),
    )

    assert manifest.multi_test is True
    assert manifest.scale_token_index == 0
    assert manifest.sandbox is not None and manifest.sandbox.backend == "bwrap"
    assert manifest.time_limit_multipliers == {"cpp": 1.0, "python": 3.0}
    assert manifest.audit_gates.fail_on_mutation_survival is True
    assert manifest.audit_gates.min_branch_coverage == 0.85

    roundtrip = AutoCodeManifest.model_validate_json(manifest.model_dump_json())
    assert roundtrip == manifest

    assert validate_executable_solution_language(manifest.solutions[0]) == "cpp"
    with pytest.raises(ValueError, match="python"):
        validate_executable_solution_language(manifest.solutions[1])
    with pytest.raises(ValueError, match="Unsupported"):
        validate_executable_solution_language("rust")

    with pytest.raises(ValidationError):
        AutoCodeManifest.model_validate({"problem_name": "Bad Bool", "scale_token_index": True})
    with pytest.raises(ValidationError):
        AutoCodeManifest.model_validate(
            {"problem_name": "Bad Mult", "time_limit_multipliers": {"python": 0.0}}
        )
    with pytest.raises(ValidationError):
        AuditGates.model_validate({"min_branch_coverage": 1.5})
    with pytest.raises(ValidationError):
        AuditGates.model_validate({"min_branch_coverage": True})
    with pytest.raises(ValidationError):
        SandboxConfig.model_validate({"backend": "unknown"})
