"""D 方向 8.1：examples 端到端 + 并发产物一致性基准。

- 三个 example 配置（checker/exact/interactive）应能被 `load_manifest` 真实加载，
  且语义字段符合各自类型（examples/ 此前零测试覆盖）。
- 用真实 checker-sample 配置驱动 `problem_generate_tests`（确定性 mock 二进制），
  断言串行（limit=1）与并发（limit=4）产出一致的测试集——呼应 1.5 的并发一致性基准，
  但建立在真实 example 配置之上，验证 examples 可被端到端驱动且并发路径稳定。
"""

import json
import os
import shutil
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest

from autocode_mcp.cli.verify import main as verify_main
from autocode_mcp.runtime_store import get_section
from autocode_mcp.tools.build_all import ProblemBuildAllTool
from autocode_mcp.tools.problem import ProblemGenerateTestsTool
from autocode_mcp.utils.checker_judge import run_testlib_checker
from autocode_mcp.utils.communication_runner import CommunicationRunner
from autocode_mcp.utils.compiler import RunResult, run_binary, run_binary_with_args
from autocode_mcp.utils.judge_runner import InteractiveRunner
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow.manifest import load_manifest

EXAMPLES_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "examples"))


def _copy_example_manifest(name: str, problem_dir: str) -> None:
    src = os.path.join(EXAMPLES_DIR, name, ".autocode", "manifest.json")
    with open(src, encoding="utf-8") as fin:
        content = fin.read()
    dest_dir = os.path.join(problem_dir, ".autocode")
    os.makedirs(dest_dir, exist_ok=True)
    with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as fout:
        fout.write(content)


@pytest.mark.asyncio
async def test_examples_manifests_loadable_and_self_consistent():
    """四个 example manifest 应能被真实加载且语义字段符合各自类型。"""
    checker = load_manifest(os.path.join(EXAMPLES_DIR, "checker-sample"))
    exact = load_manifest(os.path.join(EXAMPLES_DIR, "exact-sample"))
    interactive = load_manifest(os.path.join(EXAMPLES_DIR, "interactive-sample"))
    communication = load_manifest(os.path.join(EXAMPLES_DIR, "communication-sample"))

    assert checker is not None
    assert checker.special_judge is True
    assert checker.stress_comparison == "checker"
    assert len(checker.case_plan) == 4

    assert exact is not None
    assert exact.special_judge is False
    assert exact.stress_comparison == "exact"

    assert interactive is not None
    assert interactive.interactive is True

    assert communication is not None
    assert communication.problem_type == "communication"
    assert communication.protocol is not None
    assert communication.protocol.mode == "run_twice"
    assert communication.protocol.max_bits == 12
    assert communication.oracle_mode == "interactor_only"
    assert len(communication.subtasks) == 2
    assert sum(st.score for st in communication.subtasks) == 100
    assert communication.subtasks[1].dependencies == [1]


async def _run_example_generate(concurrency_limit: int) -> list[tuple]:
    """用真实 checker-sample 配置 + 确定性 mock 二进制跑 generate_tests，返回测试产物序列。"""
    tool = ProblemGenerateTestsTool()

    async def fake_run_binary_with_args(binary_path, args, timeout=5, process_start_hook=None, **kwargs):
        seed = int(args[0])
        return RunResult(success=True, stdout=f"{seed}\n")

    async def fake_run_binary(binary_path, stdin="", timeout=5, memory_mb=256):
        value = int(stdin.strip())
        return RunResult(success=True, stdout=f"{value * value}\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        problem_dir = os.path.join(tmpdir, f"ex_{concurrency_limit}")
        files_dir = os.path.join(problem_dir, "files")
        solutions_dir = os.path.join(problem_dir, "solutions")
        tests_dir = os.path.join(problem_dir, "tests")
        os.makedirs(files_dir)
        os.makedirs(solutions_dir)

        _copy_example_manifest("checker-sample", problem_dir)
        # 真实 example 配置应能被加载——端到端驱动的前提。
        assert load_manifest(problem_dir).special_judge is True

        exe_ext = get_exe_extension()
        open(os.path.join(files_dir, f"gen{exe_ext}"), "w").close()
        open(os.path.join(solutions_dir, f"sol{exe_ext}"), "w").close()

        with (
            mock.patch("autocode_mcp.tools.problem.run_binary_with_args", fake_run_binary_with_args),
            mock.patch("autocode_mcp.tools.problem.run_binary", fake_run_binary),
        ):
            result = await tool.execute(
                problem_dir=problem_dir,
                test_count=8,
                enable_dedup=True,
                enable_balance=False,
                oversample_ratio=1.0,
                concurrency_limit=concurrency_limit,
            )

        assert result.success, result.error
        manifest = get_section(problem_dir, "test_manifest") or {}
        summary = []
        for t in manifest["tests"]:
            in_path = os.path.join(tests_dir, t["in_file"])
            ans_path = os.path.join(tests_dir, t["ans_file"])
            with open(in_path, encoding="utf-8") as fin:
                in_data = fin.read()
            with open(ans_path, encoding="utf-8") as fans:
                ans_data = fans.read()
            summary.append((t["type_param"], t["signature"], in_data, ans_data))
        return summary


@pytest.mark.asyncio
async def test_example_checker_sample_serial_matches_concurrent():
    """真实 checker-sample 配置驱动下，串行（limit=1）与并发（limit=4）应产出一致测试集。"""
    serial = await _run_example_generate(1)
    concurrent = await _run_example_generate(4)

    assert serial == concurrent
    assert len(serial) == 8


def _run_verify_cli(monkeypatch, capsys, problem_dir: str) -> tuple[int, dict]:
    monkeypatch.setattr(sys, "argv", ["autocode-verify", problem_dir])
    rc = verify_main()
    out = capsys.readouterr().out.strip().splitlines()[-1]
    return rc, json.loads(out)


def test_all_examples_have_complete_sources_and_valid_manifests(monkeypatch, capsys):
    """四个样例必须拥有完整可移植的 C++ 源码、无 <bits/stdc++.h> 污染且无缺失路径或协议告警。"""
    expected_components = {
        "exact-sample": ["files/gen.cpp", "files/val.cpp"],
        "checker-sample": ["files/gen.cpp", "files/val.cpp", "files/checker.cpp"],
        "interactive-sample": ["files/gen.cpp", "files/interactor.cpp"],
        "communication-sample": ["files/gen.cpp", "files/communicator.cpp"],
    }
    exe_ext = get_exe_extension()

    for name, required_files in expected_components.items():
        ex_dir = Path(EXAMPLES_DIR) / name
        manifest = load_manifest(str(ex_dir))
        assert manifest is not None
        assert len(manifest.solutions) >= 2
        assert any(sol.role == "wrong" for sol in manifest.solutions), f"{name} missing wrong sol"
        for sol in manifest.solutions:
            assert (ex_dir / sol.path).is_file(), f"{name} missing {sol.path}"
        for rel in required_files:
            assert (ex_dir / rel).is_file(), f"{name} missing {rel}"

        for cpp_file in ex_dir.rglob("*.cpp"):
            text = cpp_file.read_text(encoding="utf-8")
            assert "<bits/stdc++.h>" not in text, f"Non-portable header in {cpp_file}"

        rc, payload = _run_verify_cli(monkeypatch, capsys, str(ex_dir))
        assert payload["missing_paths"] == [], f"{name}: {payload['missing_paths']}"
        assert payload["protocol_warnings"] == [], f"{name}: {payload['protocol_warnings']}"
        if name == "checker-sample":
            assert rc == 1
            assert payload["spj_warnings"] == [
                f"checker workflow requires compiled checker (run checker_build); missing files/checker{exe_ext}"
            ]
        else:
            assert rc == 0
            assert payload["success"] is True
            assert payload["spj_warnings"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_all_four_examples_compile_and_run_smoke(tmp_path, monkeypatch, capsys):
    """将四个样例复制到临时目录后，必须全部可真实编译、通过 autocode-verify 且杀伤各自错解。"""
    build_tool = ProblemBuildAllTool()
    exe_ext = get_exe_extension()

    # 1. exact-sample
    exact_dir = tmp_path / "exact-sample"
    shutil.copytree(Path(EXAMPLES_DIR) / "exact-sample", exact_dir)
    build_exact = await build_tool.execute(problem_dir=str(exact_dir))
    assert build_exact.success, build_exact.data
    rc, exact_verify = _run_verify_cli(monkeypatch, capsys, str(exact_dir))
    assert rc == 0 and exact_verify["success"] is True and exact_verify["stale_reasons"] == []

    gen_exact = await run_binary_with_args(str(exact_dir / "files" / f"gen{exe_ext}"), ["7", "3"], timeout=5)
    assert gen_exact.success
    val_res = await run_binary(str(exact_dir / "files" / f"val{exe_ext}"), stdin=gen_exact.stdout, timeout=5)
    assert val_res.success, val_res.stderr
    sol_res = await run_binary(str(exact_dir / "solutions" / f"sol{exe_ext}"), stdin=gen_exact.stdout, timeout=5)
    brute_res = await run_binary(str(exact_dir / "solutions" / f"brute{exe_ext}"), stdin=gen_exact.stdout, timeout=5)
    wrong_res = await run_binary(
        str(exact_dir / "solutions" / f"wrong_int32{exe_ext}"), stdin=gen_exact.stdout, timeout=5
    )
    assert sol_res.success and brute_res.success and wrong_res.success
    assert sol_res.stdout.strip() == brute_res.stdout.strip()
    assert wrong_res.stdout.strip() != sol_res.stdout.strip()

    # 2. checker-sample
    checker_dir = tmp_path / "checker-sample"
    shutil.copytree(Path(EXAMPLES_DIR) / "checker-sample", checker_dir)
    build_checker = await build_tool.execute(problem_dir=str(checker_dir))
    assert build_checker.success, build_checker.data
    rc, checker_verify = _run_verify_cli(monkeypatch, capsys, str(checker_dir))
    assert rc == 0 and checker_verify["success"] is True
    assert checker_verify["spj_warnings"] == [] and checker_verify["stale_reasons"] == []

    gen_chk = await run_binary_with_args(str(checker_dir / "files" / f"gen{exe_ext}"), ["11", "3"], timeout=5)
    assert gen_chk.success
    val_chk = await run_binary(str(checker_dir / "files" / f"val{exe_ext}"), stdin=gen_chk.stdout, timeout=5)
    assert val_chk.success, val_chk.stderr
    chk_sol = await run_binary(str(checker_dir / "solutions" / f"sol{exe_ext}"), stdin=gen_chk.stdout, timeout=5)
    chk_brute = await run_binary(str(checker_dir / "solutions" / f"brute{exe_ext}"), stdin=gen_chk.stdout, timeout=5)
    chk_wrong = await run_binary(
        str(checker_dir / "solutions" / f"wrong-greedy{exe_ext}"), stdin=gen_chk.stdout, timeout=5
    )
    assert chk_sol.stdout.strip() != chk_brute.stdout.strip()
    in_p, ans_p = checker_dir / "case.in", checker_dir / "case.ans"
    sol_p, brt_p, wrg_p = checker_dir / "sol.out", checker_dir / "brt.out", checker_dir / "wrg.out"
    in_p.write_text(gen_chk.stdout, encoding="utf-8", newline="\n")
    ans_p.write_text(chk_sol.stdout, encoding="utf-8", newline="\n")
    sol_p.write_text(chk_sol.stdout, encoding="utf-8", newline="\n")
    brt_p.write_text(chk_brute.stdout, encoding="utf-8", newline="\n")
    wrg_p.write_text(chk_wrong.stdout, encoding="utf-8", newline="\n")
    chk_exe = str(checker_dir / "files" / f"checker{exe_ext}")
    assert (await run_testlib_checker(chk_exe, str(in_p), str(sol_p), str(ans_p), timeout=5))[0] == "AC"
    assert (await run_testlib_checker(chk_exe, str(in_p), str(brt_p), str(ans_p), timeout=5))[0] == "AC"
    assert (await run_testlib_checker(chk_exe, str(in_p), str(wrg_p), str(ans_p), timeout=5))[0] == "WA"

    # 3. interactive-sample
    inter_dir = tmp_path / "interactive-sample"
    shutil.copytree(Path(EXAMPLES_DIR) / "interactive-sample", inter_dir)
    build_inter = await build_tool.execute(problem_dir=str(inter_dir))
    assert build_inter.success, build_inter.data
    rc, inter_verify = _run_verify_cli(monkeypatch, capsys, str(inter_dir))
    assert rc == 0 and inter_verify["success"] is True and inter_verify["stale_reasons"] == []

    gen_int = await run_binary_with_args(str(inter_dir / "files" / f"gen{exe_ext}"), ["19", "4"], timeout=5)
    assert gen_int.success
    inter_exe = str(inter_dir / "files" / f"interactor{exe_ext}")
    assert (
        await InteractiveRunner.run(
            inter_exe, str(inter_dir / "solutions" / f"sol{exe_ext}"), gen_int.stdout
        )
    ).verdict == "AC"
    assert (
        await InteractiveRunner.run(
            inter_exe, str(inter_dir / "solutions" / f"brute{exe_ext}"), gen_int.stdout
        )
    ).verdict == "AC"
    assert (
        await InteractiveRunner.run(
            inter_exe, str(inter_dir / "solutions" / f"wrong_over_limit{exe_ext}"), gen_int.stdout
        )
    ).verdict == "WA"

    # 4. communication-sample
    comm_dir = tmp_path / "communication-sample"
    shutil.copytree(Path(EXAMPLES_DIR) / "communication-sample", comm_dir)
    build_comm = await build_tool.execute(problem_dir=str(comm_dir))
    assert build_comm.success, build_comm.data
    rc, comm_verify = _run_verify_cli(monkeypatch, capsys, str(comm_dir))
    assert rc == 0 and comm_verify["success"] is True and comm_verify["stale_reasons"] == []

    gen_c1 = await run_binary_with_args(str(comm_dir / "files" / f"gen{exe_ext}"), ["5", "1"], timeout=5)
    gen_c2 = await run_binary_with_args(str(comm_dir / "files" / f"gen{exe_ext}"), ["5", "4"], timeout=5)
    assert gen_c1.success and gen_c2.success
    comm_exe = str(comm_dir / "files" / f"communicator{exe_ext}")
    comm_sol = str(comm_dir / "solutions" / f"sol{exe_ext}")
    comm_wrg = str(comm_dir / "solutions" / f"wrong_no_ecc{exe_ext}")
    for payload_in in (gen_c1.stdout, gen_c2.stdout):
        res_ok = await CommunicationRunner.run_twice(
            comm_exe,
            comm_sol,
            payload_in,
            passes=2,
            pass_headers=["first", "second"],
            max_bits=12,
            max_bytes=2,
            max_rounds=2,
        )
        assert res_ok.verdict == "AC", res_ok
    res_sub1 = await CommunicationRunner.run_twice(
        comm_exe,
        comm_wrg,
        gen_c1.stdout,
        passes=2,
        pass_headers=["first", "second"],
        max_bits=12,
        max_bytes=2,
        max_rounds=2,
    )
    res_sub2 = await CommunicationRunner.run_twice(
        comm_exe,
        comm_wrg,
        gen_c2.stdout,
        passes=2,
        pass_headers=["first", "second"],
        max_bits=12,
        max_bytes=2,
        max_rounds=2,
    )
    assert res_sub1.verdict == "AC"
    assert res_sub2.verdict == "WA"

    # 5. Generator small-scale probe parity & communication limit diversity regression checks
    for compiled_dir in (exact_dir, checker_dir, inter_dir, comm_dir):
        gen_exe = str(compiled_dir / "files" / f"gen{exe_ext}")
        for probe_args in (["1", "4", "5", "15", "1", "1"], ["1", "3", "1", "5", "1", "1"]):
            probe_res = await run_binary_with_args(gen_exe, probe_args, timeout=5)
            assert probe_res.success, (
                f"{compiled_dir.name} gen failed probe {probe_args}: {probe_res.stderr}"
            )

    comm_gen = str(comm_dir / "files" / f"gen{exe_ext}")
    t3_outputs: set[str] = set()
    t4_outputs: set[str] = set()
    for seed in range(1, 31):
        r3 = await run_binary_with_args(comm_gen, [str(seed), "3"], timeout=5)
        r4 = await run_binary_with_args(comm_gen, [str(seed), "4"], timeout=5)
        assert r3.success and r4.success
        t3_outputs.add(r3.stdout.strip())
        t4_outputs.add(r4.stdout.strip())
    assert len(t3_outputs | t4_outputs) >= 10
    overlap = len(t3_outputs & t4_outputs) / max(1, min(len(t3_outputs), len(t4_outputs)))
    assert overlap < 0.8
