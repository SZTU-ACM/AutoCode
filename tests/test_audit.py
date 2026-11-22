import json
from pathlib import Path

import pytest

import autocode_mcp.tools.test_verify as tv
from autocode_mcp.runtime_store import get_section, set_section
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.workflow import default_manifest, save_manifest
from autocode_mcp.workflow.models import AutoCodeManifest


def _write_basic_problem(tmp_path: Path) -> Path:
    problem_dir = tmp_path / "p"
    (problem_dir / "statements").mkdir(parents=True)
    (problem_dir / "solutions").mkdir()
    (problem_dir / "tests").mkdir()
    (problem_dir / "statements" / "README.md").write_text(
        "# P\n\n## 样例\n\n### 样例输入 #1\n```text\n1\n```\n\n### 样例输出 #1\n```text\n1\n```\n",
        encoding="utf-8",
    )
    (problem_dir / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n" + "This tutorial contains enough evidence about approach and complexity. " * 3,
        encoding="utf-8",
    )
    (problem_dir / "solutions" / "sol.cpp").write_text(
        "#include <bits/stdc++.h>\nusing namespace std;\nint main(){int n;cin>>n;cout<<n;}\n",
        encoding="utf-8",
    )
    for i, _type_param in enumerate(["1", "2", "3", "4"], 1):
        (problem_dir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (problem_dir / "tests" / f"{i:02d}.ans").write_text(f"{i}\n", encoding="utf-8")
    set_section(
        str(problem_dir),
        "test_manifest",
        {
            "tests": [
                {
                    "in_file": f"{i:02d}.in",
                    "ans_file": f"{i:02d}.ans",
                    "type_param": type_param,
                    "group": "g",
                    "purpose": f"type {type_param}",
                }
                for i, type_param in enumerate(["1", "2", "3", "4"], 1)
            ]
        },
    )
    save_manifest(str(problem_dir), default_manifest("P"))
    set_section(
        str(problem_dir),
        "workflow",
        {
            "tests_verified": True,
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
                "wrong_solution_kill": {"executed": True, "passed": True},
                "answer_consistency": {"executed": True, "passed": True},
            },
        },
    )
    return problem_dir


def test_manifest_accepts_audit_and_structured_constraints():
    manifest = AutoCodeManifest(
        problem_name="x",
        constraints={
            "n": {"min": 1, "max": 200000, "aliases": ["N"]},
            "sum_n_max": 200000,
        },
        audit_gates={"require_full_audit": True},
        difficulty={"rating": 1600, "band": "中等", "confidence": 0.7},
    )
    assert manifest.audit_gates.require_full_audit is True
    assert manifest.constraints["n"]["max"] == 200000
    assert manifest.difficulty.rating == 1600


def test_problem_verify_static_quality_checks(tmp_path):
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "01.in").write_text("1\n", encoding="utf-8")
    (tests_dir / "02.in").write_text("1\n", encoding="utf-8")
    set_section(str(tmp_path), "test_manifest", {"tests": [{"in_file": "01.in", "type_param": "1"}]})
    tool = ProblemVerifyTestsTool()
    duplicate = tool._check_duplicate_inputs(str(tests_dir))
    scale = tool._check_scale_distribution(str(tests_dir))
    coverage = tool._check_purpose_coverage(str(tests_dir))
    assert not duplicate["passed"]
    assert scale["passed"]
    assert "2" in coverage["missing_types"]


@pytest.mark.asyncio
async def test_problem_audit_full_go_and_writes_report(tmp_path):
    problem_dir = _write_basic_problem(tmp_path)
    tool = ProblemAuditTool()
    result = await tool.execute(
        problem_dir=str(problem_dir),
        mode="full",
        report_path="audit_report.json",
    )
    assert result.success
    assert result.data["decision"] == "go"
    assert (problem_dir / "audit_report.json").is_file()
    audit = get_section(problem_dir, "audit")
    assert audit["full_audit"]["decision"] == "go"
    assert audit["full_audit_passed"] is True
    assert result.data["difficulty_signals"]["rating"] >= 800


@pytest.mark.asyncio
async def test_problem_audit_respects_statement_consistency_override(tmp_path):
    problem_dir = _write_basic_problem(tmp_path)
    (problem_dir / "statements" / "tutorial.md").write_text("short", encoding="utf-8")
    manifest = default_manifest("P")
    manifest.audit_gates.require_statement_consistency = False
    save_manifest(str(problem_dir), manifest)

    result = await ProblemAuditTool().execute(problem_dir=str(problem_dir), mode="full")

    assert result.success
    assert result.data["decision"] == "go"
    assert any(w["gate"] == "statement_consistency" for w in result.data["risk_report"]["warnings"])


@pytest.mark.asyncio
async def test_problem_audit_blocks_missing_purpose_coverage(tmp_path):
    problem_dir = _write_basic_problem(tmp_path)
    set_section(str(problem_dir), "test_manifest", {"tests": [{"in_file": "01.in", "type_param": "1"}]})

    result = await ProblemAuditTool().execute(problem_dir=str(problem_dir), mode="full")

    assert result.success
    assert result.data["decision"] == "no_go"
    assert any(issue["gate"] == "purpose_coverage" for issue in result.data["blocking_issues"])


@pytest.mark.asyncio
async def test_checker_self_test_requires_format_error_category(monkeypatch, tmp_path):
    problem_dir = tmp_path / "p"
    (problem_dir / "files").mkdir(parents=True)
    (problem_dir / "tests").mkdir()
    checker_exe = problem_dir / "files" / f"checker{tv.get_exe_extension()}"
    checker_exe.write_text("fake", encoding="utf-8")
    (problem_dir / "tests" / "checker_scenarios.json").write_text(
        json.dumps(
            [
                {"input": "1", "contestant_output": "1", "reference_output": "1", "expected_verdict": "AC"},
                {"input": "1", "contestant_output": "2", "reference_output": "1", "expected_verdict": "WA"},
            ]
        ),
        encoding="utf-8",
    )

    async def fake_checker(_checker_bin, _in_path, _out_path, _ans_path, *, timeout):
        class FakeRun:
            stderr = ""

        return Path(_out_path).read_text(encoding="utf-8").strip() == "1" and "AC" or "WA", FakeRun()

    monkeypatch.setattr(tv, "run_testlib_checker", fake_checker)

    result = await ProblemVerifyTestsTool()._check_checker_self_test(str(problem_dir), timeout=1)

    assert not result["passed"]
    assert "format_error" in result["missing_required_categories"]


def test_autocode_audit_cli(monkeypatch, capsys, tmp_path):
    from autocode_mcp.cli.audit import main

    problem_dir = _write_basic_problem(tmp_path)
    monkeypatch.setattr("sys.argv", ["autocode-audit", str(problem_dir), "--mode", "full"])

    assert main() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["success"] is True
    assert parsed["data"]["decision"] == "go"


@pytest.mark.asyncio
async def test_problem_audit_statement_consistency_next_action_json_serializable(tmp_path):
    """statement_consistency 阻断时 next_actions 中的 problem_dir 应为 str，可正常 JSON 序列化。"""
    problem_dir = _write_basic_problem(tmp_path)
    (problem_dir / "statements" / "tutorial.md").write_text("short", encoding="utf-8")

    result = await ProblemAuditTool().execute(
        problem_dir=str(problem_dir),
        mode="full",
        report_path="audit_report.json",
    )
    assert result.success
    assert result.data["decision"] == "no_go"
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert json.dumps(str(problem_dir)) in serialized
    assert (problem_dir / "audit_report.json").is_file()


@pytest.mark.asyncio
async def test_problem_audit_uses_workflow_claimed_complexity_fallback(monkeypatch, tmp_path):
    """sol.cpp 缺少 O(...) 注释时，problem_audit 应回退使用 workflow 中保存的 std_claimed_complexity。"""
    import autocode_mcp.tools.audit as audit_mod

    problem_dir = _write_basic_problem(tmp_path)
    set_section(
        str(problem_dir),
        "workflow",
        {
            "tests_verified": True,
            "std_claimed_complexity": "O(n log n)",
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
                "wrong_solution_kill": {"executed": True, "passed": True},
                "answer_consistency": {"executed": True, "passed": True},
            },
        },
    )

    captured: dict[str, object] = {}

    async def fake_empirical(pdir, sol_type, claimed, constraints):
        captured["claimed"] = claimed
        captured["constraints"] = constraints
        return {"status": "verified", "passed": True, "fitted_complexity": "O(n log n)"}

    monkeypatch.setattr(audit_mod, "run_empirical_verification", fake_empirical)

    result = await ProblemAuditTool().execute(problem_dir=str(problem_dir), mode="full")
    assert result.success
    assert captured["claimed"] == "O(n log n)"
    assert result.data["quality_signals"]["empirical_complexity"]["passed"] is True


@pytest.mark.asyncio
async def test_problem_audit_standalone_clean_bare_package_is_read_only(tmp_path):
    """standalone=True 应对无 .autocode/ 的外部题包完成实时检查，且不写入 .autocode/。"""
    from autocode_mcp.utils.compiler import compile_cpp
    from autocode_mcp.utils.platform import get_exe_extension

    bare_dir = tmp_path / "external_pkg"
    (bare_dir / "statements").mkdir(parents=True)
    (bare_dir / "solutions").mkdir()
    (bare_dir / "tests").mkdir()
    (bare_dir / "statements" / "README.md").write_text(
        "# External\n\n## 样例\n\n```text\n1\n```\n", encoding="utf-8"
    )
    sol_src = bare_dir / "solutions" / "sol.cpp"
    sol_src.write_text(
        "#include <iostream>\nint main(){ int x; if (std::cin >> x) std::cout << x * 2 << '\\n'; }\n",
        encoding="utf-8",
    )
    sol_bin = bare_dir / "solutions" / f"sol{get_exe_extension()}"
    comp = await compile_cpp(str(sol_src), str(sol_bin))
    assert comp.success, comp.stderr

    for i in range(1, 5):
        (bare_dir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (bare_dir / "tests" / f"{i:02d}.ans").write_text(f"{i * 2}\n", encoding="utf-8")

    # Non-standalone mode must still fail without manifest.json
    non_standalone = await ProblemAuditTool().execute(problem_dir=str(bare_dir), mode="full")
    assert not non_standalone.success
    assert "manifest.json not found" in (non_standalone.error or "")

    # Standalone mode succeeds, verifies answer consistency live, and leaves zero .autocode/ writes
    result = await ProblemAuditTool().execute(
        problem_dir=str(bare_dir), mode="full", standalone=True
    )
    assert result.success, result.error
    assert result.data["standalone"] is True
    assert result.data["manifest_inferred"] is True
    assert result.data["decision"] == "go", result.data["blocking_issues"]
    assert result.data["quality_signals"]["file_count"]["passed"] is True
    assert result.data["quality_signals"]["no_empty"]["passed"] is True
    assert result.data["quality_signals"]["answer_consistency"]["passed"] is True
    assert not (bare_dir / ".autocode").exists()


def test_problem_audit_standalone_catches_broken_package_and_cli_exit_codes(
    monkeypatch, capsys, tmp_path
):
    """standalone=True 应拦截空测试文件与答案不一致，CLI --standalone 返回对应退出码。"""
    import asyncio

    from autocode_mcp.cli.audit import main as audit_cli_main
    from autocode_mcp.utils.compiler import compile_cpp
    from autocode_mcp.utils.platform import get_exe_extension

    bare_dir = tmp_path / "broken_pkg"
    (bare_dir / "solutions").mkdir(parents=True)
    (bare_dir / "tests").mkdir()
    sol_src = bare_dir / "solutions" / "sol.cpp"
    sol_src.write_text(
        "#include <iostream>\nint main(){ int x; if (std::cin >> x) std::cout << x + 1 << '\\n'; }\n",
        encoding="utf-8",
    )
    sol_bin = bare_dir / "solutions" / f"sol{get_exe_extension()}"
    comp = asyncio.run(compile_cpp(str(sol_src), str(sol_bin)))
    assert comp.success, comp.stderr

    # Clean case first -> CLI exit code 0
    (bare_dir / "tests" / "01.in").write_text("5\n", encoding="utf-8")
    (bare_dir / "tests" / "01.ans").write_text("6\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["autocode-audit", str(bare_dir), "--standalone"])
    assert audit_cli_main() == 0
    clean_out = json.loads(capsys.readouterr().out)
    assert clean_out["data"]["decision"] == "go"
    assert clean_out["data"]["standalone"] is True

    # Introduce empty file + mismatched answer -> CLI exit code 2 (no_go)
    (bare_dir / "tests" / "02.in").write_text("10\n", encoding="utf-8")
    (bare_dir / "tests" / "02.ans").write_text("999\n", encoding="utf-8")
    (bare_dir / "tests" / "03.in").write_text("", encoding="utf-8")
    (bare_dir / "tests" / "03.ans").write_text("0\n", encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["autocode-audit", str(bare_dir), "--standalone"])
    assert audit_cli_main() == 2
    broken_out = json.loads(capsys.readouterr().out)
    assert broken_out["data"]["decision"] == "no_go"
    gates = {item["gate"] for item in broken_out["data"]["blocking_issues"]}
    assert "no_empty" in gates
    assert "answer_consistency" in gates
    assert not (bare_dir / ".autocode").exists()


@pytest.mark.asyncio
async def test_problem_audit_standalone_supports_hydro_testdata_and_out_ext(tmp_path):
    """standalone=True 应能识别 Hydro 风格的 testdata/ 目录与 .out 答案后缀。"""
    hydro_dir = tmp_path / "hydro_pkg"
    (hydro_dir / "testdata").mkdir(parents=True)
    (hydro_dir / "problem_zh.md").write_text("# Hydro Prob\n\n## 样例\n", encoding="utf-8")
    (hydro_dir / "testdata" / "01.in").write_text("1\n", encoding="utf-8")
    (hydro_dir / "testdata" / "01.out").write_text("2\n", encoding="utf-8")
    (hydro_dir / "testdata" / "02.in").write_text("10\n", encoding="utf-8")
    (hydro_dir / "testdata" / "02.out").write_text("20\n", encoding="utf-8")

    result = await ProblemAuditTool().execute(
        problem_dir=str(hydro_dir), mode="full", standalone=True
    )
    assert result.success, result.error
    assert result.data["decision"] == "go", result.data["blocking_issues"]
    assert result.data["quality_signals"]["file_count"]["passed"] is True
    assert result.data["quality_signals"]["no_empty"]["passed"] is True
    assert not (hydro_dir / ".autocode").exists()


@pytest.mark.asyncio
async def test_mcp_call_tool_standalone_audit_never_writes_runtime_json(tmp_path):
    """通过 MCP server call_tool 调用 problem_audit(standalone=True) 不得写入 .autocode/runtime.json。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()

    pdir = tmp_path / "managed_prob"
    (pdir / "statements").mkdir(parents=True)
    (pdir / "solutions").mkdir()
    (pdir / "tests").mkdir()
    (pdir / "statements" / "README.md").write_text("# P\n\n## 样例\n", encoding="utf-8")
    (pdir / "statements" / "tutorial.md").write_text("# T\n\n" + "detail " * 20, encoding="utf-8")
    (pdir / "solutions" / "sol.cpp").write_text("int main(){}\n", encoding="utf-8")
    (pdir / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    save_manifest(str(pdir), default_manifest("Managed"))

    assert not (pdir / ".autocode" / "runtime.json").exists()
    res = await call_tool(
        "problem_audit", {"problem_dir": str(pdir), "mode": "full", "standalone": True}
    )
    assert not res.isError, res.structuredContent
    assert res.structuredContent["data"]["standalone"] is True
    assert not (pdir / ".autocode" / "runtime.json").exists()

    # Leftover runtime.json without manifest.json must NOT block standalone audit via preflight
    bare_leftover = tmp_path / "leftover_runtime"
    (bare_leftover / "tests").mkdir(parents=True)
    (bare_leftover / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (bare_leftover / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    set_section(str(bare_leftover), "workflow", {"created": True})
    before_bytes = (bare_leftover / ".autocode" / "runtime.json").read_bytes()
    res2 = await call_tool(
        "problem_audit", {"problem_dir": str(bare_leftover), "mode": "quick", "standalone": True}
    )
    assert not res2.isError, res2.structuredContent
    assert res2.structuredContent["data"]["decision"] == "go"
    assert (bare_leftover / ".autocode" / "runtime.json").read_bytes() == before_bytes


@pytest.mark.asyncio
async def test_problem_audit_standalone_blocks_symlink_escapes_and_handles_readonly_dir(tmp_path):
    """standalone=True 应拦截符号链接逃逸，且能在只读题目目录（0o555）中运行实测不报错。"""
    import os
    import sys

    from pydantic import ValidationError

    from autocode_mcp.utils.compiler import compile_cpp
    from autocode_mcp.utils.platform import get_exe_extension

    # 1. Manifest statement_path traversal rejected at Pydantic validation layer
    with pytest.raises(ValidationError):
        AutoCodeManifest(problem_name="evil", statement_path="/etc/passwd")

    # 2. Symlinked test input & binary escaping problem_dir blocked by path_containment
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    esc_dir = tmp_path / "esc_pkg"
    (esc_dir / "tests").mkdir(parents=True)
    symlink_created = False
    try:
        os.symlink(str(outside), str(esc_dir / "tests" / "01.in"))
        symlink_created = True
    except OSError as exc:
        if not (sys.platform == "win32" and getattr(exc, "winerror", None) == 1314):
            raise
    if symlink_created:
        (esc_dir / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
        esc_res = await ProblemAuditTool().execute(
            problem_dir=str(esc_dir), mode="full", standalone=True
        )
        assert esc_res.success
        assert esc_res.data["decision"] == "no_go"
        assert any(i["gate"] == "path_containment" for i in esc_res.data["blocking_issues"])

    # 3. Read-only directory live SPJ checker execution uses system temp dir cleanly
    if sys.platform != "win32" and os.geteuid() != 0:
        ro_dir = tmp_path / "ro_spj"
        (ro_dir / "files").mkdir(parents=True)
        (ro_dir / "solutions").mkdir()
        (ro_dir / "tests").mkdir()
        (ro_dir / "files" / "checker.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
        (ro_dir / "solutions" / "sol.cpp").write_text(
            "#include <iostream>\nint main(){int x; if (std::cin>>x) std::cout<<x<<'\\n';}\n",
            encoding="utf-8",
        )
        ext = get_exe_extension()
        assert (
            await compile_cpp(
                str(ro_dir / "files" / "checker.cpp"),
                str(ro_dir / "files" / f"checker{ext}"),
            )
        ).success
        assert (
            await compile_cpp(
                str(ro_dir / "solutions" / "sol.cpp"),
                str(ro_dir / "solutions" / f"sol{ext}"),
            )
        ).success
        (ro_dir / "tests" / "01.in").write_text("42\n", encoding="utf-8")
        (ro_dir / "tests" / "01.ans").write_text("42\n", encoding="utf-8")

        os.chmod(ro_dir, 0o555)
        try:
            ro_res = await ProblemAuditTool().execute(
                problem_dir=str(ro_dir), mode="full", standalone=True
            )
            assert ro_res.success, ro_res.error
            assert ro_res.data["decision"] == "go", ro_res.data["blocking_issues"]
            assert ro_res.data["quality_signals"]["answer_consistency"]["passed"] is True
        finally:
            os.chmod(ro_dir, 0o755)


@pytest.mark.asyncio
async def test_problem_audit_standalone_qoj_layout_and_corrupt_manifest(tmp_path):
    """standalone=True 支持 QOJ data/ 布局，同时对损坏的 manifest.json 显式报错。"""
    qoj_dir = tmp_path / "qoj_pkg"
    (qoj_dir / "data").mkdir(parents=True)
    (qoj_dir / "statement.md").write_text("# QOJ\n\n## 样例\n", encoding="utf-8")
    (qoj_dir / "data" / "1.in").write_text("1\n", encoding="utf-8")
    (qoj_dir / "data" / "1.ans").write_text("2\n", encoding="utf-8")
    res = await ProblemAuditTool().execute(problem_dir=str(qoj_dir), mode="quick", standalone=True)
    assert res.success
    assert res.data["decision"] == "go"
    assert res.data["quality_signals"]["file_count"]["passed"] is True

    # Corrupt manifest.json still fails cleanly
    corrupt_dir = tmp_path / "corrupt_pkg"
    (corrupt_dir / ".autocode").mkdir(parents=True)
    (corrupt_dir / ".autocode" / "manifest.json").write_text("{not valid json", encoding="utf-8")
    bad = await ProblemAuditTool().execute(
        problem_dir=str(corrupt_dir), mode="full", standalone=True
    )
    assert not bad.success
    assert "invalid or unreadable manifest.json" in (bad.error or "")

    # Non-existent problem_dir fails cleanly
    missing = await ProblemAuditTool().execute(
        problem_dir=str(tmp_path / "does_not_exist"), standalone=True
    )
    assert not missing.success


@pytest.mark.asyncio
async def test_difficulty_signals_ignores_noisy_empirical_o1_and_separates_value_bounds(tmp_path):
    """低信噪比实证 O(1) 不得覆盖静态复杂度；largest_numeric_constraint 应聚焦输入规模。"""
    from autocode_mcp.workflow import default_manifest, save_manifest

    pdir = tmp_path / "diff_sig"
    (pdir / "solutions").mkdir(parents=True)
    (pdir / "statements").mkdir(parents=True)
    manifest = default_manifest("Dijkstra Scale")
    manifest.constraints = {"n": {"min": 1, "max": 200000}, "a_i": {"min": 1, "max": 10**12}}
    save_manifest(str(pdir), manifest)
    (pdir / "solutions" / "sol.cpp").write_text(
        """
#include <queue>
#include <vector>
using namespace std;
int main() {
    int n = 10;
    vector<vector<pair<int, int>>> adj(n);
    priority_queue<pair<int, int>> pq;
    pq.push({0, 0});
    while (!pq.empty()) {
        auto cur = pq.top();
        pq.pop();
        for (auto &e : adj[cur.second]) {
            pq.push(e);
        }
    }
    return 0;
}
""",
        encoding="utf-8",
    )

    tool = ProblemAuditTool()
    noisy_signals = {
        "empirical_complexity": {
            "executed": True,
            "passed": True,
            "evidence": {
                "verdict": "verified_within_noise_floor",
                "fitted_complexity": "O(1)",
                "r_squared": 0.12,
            },
        },
        "answer_consistency": {"executed": True, "passed": True},
        "limit_semantics": {"executed": True, "passed": True},
    }
    res = tool._difficulty_signals(pdir, manifest, noisy_signals)
    assert res["estimated_complexity"] == "O(n log n)"
    assert "heap" in res["algorithm_tags"]
    assert "graph_traversal" not in res["algorithm_tags"]
    assert res["constraint_scale"]["largest_numeric_constraint"] == 200000
    assert res["constraint_scale"]["largest_value_bound"] == 10**12


@pytest.mark.asyncio
async def test_verify_tests_surfaces_infra_error_and_marks_wrong_kill_fail_not_re(monkeypatch, tmp_path):
    """底层运行异常时 _check_answer_consistency 应透出 result.error，wrong_solution_kill 应判定为 FAIL 而非 RE。"""
    import autocode_mcp.tools.test_verify as tv
    from autocode_mcp.utils.compiler import RunResult
    from autocode_mcp.utils.platform import get_exe_extension

    pdir = tmp_path / "infra_err_pkg"
    (pdir / "solutions").mkdir(parents=True)
    (pdir / "tests").mkdir(parents=True)
    exe_ext = get_exe_extension()
    (pdir / "solutions" / f"sol{exe_ext}").write_text("x", encoding="utf-8")
    (pdir / "solutions" / f"wa_bad{exe_ext}").write_text("x", encoding="utf-8")
    (pdir / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("1\n", encoding="utf-8")

    async def fake_run_binary(*_args, **_kwargs):
        return RunResult(
            success=False,
            return_code=-1,
            stdout="",
            stderr="",
            error="Execution error: simulated WinJobObject TypeError",
        )

    monkeypatch.setattr(tv, "run_binary", fake_run_binary)
    verifier = ProblemVerifyTestsTool()

    ans_res = await verifier._check_answer_consistency(
        str(pdir), str(pdir / "tests"), "sol", ".ans", 5, use_system_temp=True
    )
    assert ans_res["passed"] is False
    assert ans_res["errors"]
    assert "simulated WinJobObject TypeError" in ans_res["errors"][0]["error"]

    val_bin = pdir / "files" / f"val{exe_ext}"
    val_bin.parent.mkdir(parents=True, exist_ok=True)
    val_bin.write_text("x", encoding="utf-8")
    val_res = await verifier._check_validator(str(pdir), str(pdir / "tests"), 5)
    assert val_res["passed"] is False
    assert "simulated WinJobObject TypeError" in val_res["invalid"][0]["error"]

    kill_res = await verifier._check_wrong_solution_kill(
        str(pdir), str(pdir / "tests"), ["wa_bad"], ".ans", 5, use_system_temp=True
    )
    assert kill_res["passed"] is False
    detail = kill_res["details"][0]
    assert detail["killed"] is False
    assert detail["judge_failed"] is True
    assert "simulated WinJobObject TypeError" in detail["reason"]
    assert detail.get("killing_verdict") != "RE"


async def test_wrong_solution_kill_timing_ratios_configurable_tle_and_audit_host_bias_warning(
    monkeypatch, tmp_path
):
    """wrong_solution_kill 应输出 wrong/std 耗时倍率与分布、支持配置 TLE 阈值/相对倍率，并在 audit 提示主机算力偏差。"""
    from autocode_mcp.utils.compiler import RunResult
    from autocode_mcp.utils.platform import get_exe_extension
    from autocode_mcp.workflow.models import QualityGates, SolutionEntry

    pdir = _write_basic_problem(tmp_path)
    exe_ext = get_exe_extension()
    (pdir / "solutions" / f"wrong_slow{exe_ext}").write_text("fake", encoding="utf-8")

    # Scenario 1: slow wrong solution on fast host (902ms vs std 50ms -> 18.04x slower) with TL=3000ms
    manifest = default_manifest("BitmaskDP")
    manifest.time_limit_ms = 3000
    manifest.solutions.append(
        SolutionEntry(name="wrong_slow", role="wrong", path="solutions/wrong_slow.cpp", expected="fail")
    )
    manifest.quality_gates = QualityGates(wrong_tle_kill_factor=1.0, wrong_slowdown_kill_ratio=None)
    save_manifest(str(pdir), manifest)

    async def fake_run_902ms(_bin, _stdin, **_kw):
        return RunResult(success=True, return_code=0, stdout="1\n", time_ms=902)

    # Make all answers match "1\n" so only timing distinguishes wrong_slow
    for i in range(1, 5):
        (pdir / "tests" / f"{i:02d}.ans").write_text("1\n", encoding="utf-8")

    monkeypatch.setattr(tv, "run_binary", fake_run_902ms)
    verifier = ProblemVerifyTestsTool()
    std_timings = {f"{i:02d}.in": 50.0 for i in range(1, 5)}

    res_survive = await verifier._check_wrong_solution_kill(
        str(pdir),
        str(pdir / "tests"),
        ["wrong_slow"],
        ".ans",
        30,
        manifest=manifest,
        std_timings=std_timings,
    )
    assert res_survive["passed"] is False
    detail = res_survive["details"][0]
    assert detail["killed"] is False
    assert detail["max_time_ms"] == 902.0
    assert detail["std_max_time_ms"] == 50.0
    assert detail["max_ratio_to_std"] == pytest.approx(18.04, abs=0.05)
    assert len(detail["per_test_timings"]) == 4
    assert detail["suspect_slow_tests"]
    assert "host_speed_warning" in detail

    # Persist into workflow verify_signals and verify problem_audit emits host_performance_bias warning
    set_section(
        str(pdir),
        "workflow",
        {
            "tests_verified": True,
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
                "wrong_solution_kill": {"executed": True, "passed": False, "evidence": res_survive},
                "answer_consistency": {"executed": True, "passed": True},
            },
        },
    )
    audit_res = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    assert audit_res.success
    assert any(
        w["gate"] == "host_performance_bias" for w in audit_res.data["risk_report"]["warnings"]
    )

    # Scenario 2: wrong_slowdown_kill_ratio=10.0 kills the 18x slower wrong solution as TLE
    res_rel_kill = await verifier._check_wrong_solution_kill(
        str(pdir),
        str(pdir / "tests"),
        ["wrong_slow"],
        ".ans",
        30,
        manifest=manifest,
        std_timings=std_timings,
        slowdown_kill_ratio=10.0,
    )
    assert res_rel_kill["passed"] is True
    assert res_rel_kill["details"][0]["killing_verdict"] == "TLE"
    assert res_rel_kill["details"][0]["killing_tle_reason"] == "relative_slowdown"

    # Scenario 3: 3200ms vs TL=3000ms (1.07x TL, below legacy 1.5x=4500ms) is killed under default 1.0x factor,
    # and survives if caller explicitly relaxes tle_kill_factor=1.5
    async def fake_run_3200ms(_bin, _stdin, **_kw):
        return RunResult(success=True, return_code=0, stdout="1\n", time_ms=3200)

    monkeypatch.setattr(tv, "run_binary", fake_run_3200ms)
    res_1x = await verifier._check_wrong_solution_kill(
        str(pdir),
        str(pdir / "tests"),
        ["wrong_slow"],
        ".ans",
        30,
        manifest=manifest,
        std_timings=std_timings,
    )
    assert res_1x["passed"] is True
    assert res_1x["details"][0]["killing_verdict"] == "TLE"
    assert res_1x["details"][0]["killing_tle_reason"] == "exceeded_tle_threshold"

    res_1_5x = await verifier._check_wrong_solution_kill(
        str(pdir),
        str(pdir / "tests"),
        ["wrong_slow"],
        ".ans",
        30,
        manifest=manifest,
        std_timings=std_timings,
        tle_kill_factor=1.5,
    )
    assert res_1_5x["passed"] is False


@pytest.mark.asyncio
async def test_wrong_solution_expected_pass_policy_validation_std_cache_and_interactive_empty_ans(
    monkeypatch, tmp_path
):
    """回归：expected=pass 不被相对倍率或 factor<1 误杀、非法 TLE 参数阻断、缺失 std 耗时补测、交互题无/空 .ans 通过检查。"""
    from pydantic import ValidationError

    from autocode_mcp.utils.compiler import RunResult
    from autocode_mcp.utils.platform import get_exe_extension
    from autocode_mcp.workflow.models import QualityGates, SolutionEntry

    pdir = _write_basic_problem(tmp_path)
    exe_ext = get_exe_extension()
    (pdir / "solutions" / f"sol{exe_ext}").write_text("fake", encoding="utf-8")
    (pdir / "solutions" / f"alt_pass{exe_ext}").write_text("fake", encoding="utf-8")
    for i in range(1, 5):
        (pdir / "tests" / f"{i:02d}.ans").write_text("1\n", encoding="utf-8")

    # 1. QualityGates rejects NaN/Inf/bool
    for bad_q in (
        {"wrong_tle_kill_factor": True},
        {"wrong_tle_kill_factor": float("nan")},
        {"wrong_tle_kill_factor": float("inf")},
        {"wrong_slowdown_kill_ratio": True},
        {"wrong_slowdown_kill_ratio": float("nan")},
        {"wrong_slowdown_kill_ratio": 1.0},
    ):
        with pytest.raises(ValidationError):
            QualityGates(**bad_q)

    # 2. Runtime validation in ProblemVerifyTestsTool rejects invalid parameters
    verifier = ProblemVerifyTestsTool()
    for bad_kw in (
        {"tle_kill_factor": 0.0},
        {"tle_kill_factor": float("nan")},
        {"tle_kill_factor": True},
        {"slowdown_kill_ratio": 0.5},
        {"slowdown_kill_ratio": float("inf")},
        {"slowdown_kill_ratio": True},
    ):
        r = await verifier.execute(problem_dir=str(pdir), **bad_kw)
        assert not r.success

    # 3. expected="pass" solution running at 600ms (with TL=1000ms, factor=0.5 -> 500ms, slowdown_ratio=3.0 vs std=20ms)
    # must NOT be falsely killed!
    manifest = default_manifest("PassProtection")
    manifest.time_limit_ms = 1000
    manifest.solutions.append(
        SolutionEntry(name="alt_pass", role="wrong", path="solutions/alt_pass.cpp", expected="pass")
    )
    manifest.quality_gates = QualityGates(wrong_tle_kill_factor=0.5, wrong_slowdown_kill_ratio=3.0)
    save_manifest(str(pdir), manifest)

    async def fake_run(exe, _stdin, **_kw):
        if exe.endswith(f"sol{exe_ext}"):
            return RunResult(success=True, return_code=0, stdout="1\n", time_ms=20)
        return RunResult(success=True, return_code=0, stdout="1\n", time_ms=600)

    monkeypatch.setattr(tv, "run_binary", fake_run)
    # Pass partial std_timings (only 01.in) -> missing 02..04.in must be measured automatically
    res_pass = await verifier._check_wrong_solution_kill(
        str(pdir),
        str(pdir / "tests"),
        ["alt_pass"],
        ".ans",
        30,
        manifest=manifest,
        std_timings={"01.in": 20.0},
    )
    assert res_pass["passed"] is True
    detail = res_pass["details"][0]
    assert detail["killed"] is True  # expected="pass" satisfied (all AC)
    assert len(detail["per_test_timings"]) == 4
    assert all(item.get("std_time_ms") == 20.0 for item in detail["per_test_timings"])

    # 4. Interactive problem with omitted or empty .ans passes file_count & no_empty, while empty .in still fails
    itests = tmp_path / "inter_tests"
    itests.mkdir()
    (itests / "01.in").write_text("10\n", encoding="utf-8")
    (itests / "02.in").write_text("20\n", encoding="utf-8")
    (itests / "02.ans").write_text("", encoding="utf-8")
    fc = verifier._check_file_count(str(itests), ".ans", is_interactive=True)
    assert fc["passed"] is True
    ne = verifier._check_no_empty(str(itests), is_interactive=True, answer_ext=".ans")
    assert ne["passed"] is True
    (itests / "03.in").write_text("", encoding="utf-8")
    ne_bad = verifier._check_no_empty(str(itests), is_interactive=True, answer_ext=".ans")
    assert ne_bad["passed"] is False
    assert "03.in" in ne_bad["empty_files"]


