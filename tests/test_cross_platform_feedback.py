"""Comprehensive regression tests for cross-platform & Linux/macOS/Windows workflow feedback."""

from __future__ import annotations

import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
import yaml

from autocode_mcp.runtime_store import TEST_MANIFEST, WORKFLOW, get_section, set_section
from autocode_mcp.server import _cap_mcp_payload
from autocode_mcp.tools.audit import (
    ProblemAuditTool,
    _extract_statement_numeric_bounds,
    _extract_validator_bounds,
)
from autocode_mcp.tools.complexity import SolutionAnalyzeTool, run_empirical_verification
from autocode_mcp.tools.problem import ProblemGenerateTestsTool, ProblemPackPolygonTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.solution_audit import SolutionAuditBruteTool
from autocode_mcp.tools.stress_test import StressTestRunTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.tools.validation import ProblemValidateTool
from autocode_mcp.utils.compiler import compile_cpp, probe_sanitizer_support
from autocode_mcp.utils.exporters import export_hydro, export_qoj, verify_exported_packages
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow import (
    apply_result,
    current_platform,
    default_manifest,
    inspect_workspace_state,
    load_workflow_state,
    reconcile_workspace_state,
    save_manifest,
    save_workflow_state,
)
from autocode_mcp.workflow.models import CasePlanItem, EmpiricalComplexityConfig


def _make_managed_dir(tmp_path: Path, name: str = "prob", **manifest_kwargs: Any) -> Path:
    pdir = tmp_path / name
    for sub in ("files", "solutions", "statements", "tests"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    manifest = default_manifest("Sample Problem", **manifest_kwargs)
    save_manifest(str(pdir), manifest)
    (pdir / "statements" / "README.md").write_text(
        "# Sample\n\n## 输入格式\n\n$1 \\le n \\le 2 \\times 10^5$。\n\n"
        "## 样例\n\n### 样例输入 #1\n```text\n3\n```\n\n### 样例输出 #1\n```text\n6\n```\n",
        encoding="utf-8",
    )
    (pdir / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n" + "Detailed explanation of the optimal O(n) approach and proof. " * 4,
        encoding="utf-8",
    )
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<n*2<<'\\n';}\n",
        encoding="utf-8",
    )
    return pdir


# ============== P0-1: TL guard & post-export read-back ==============


@pytest.mark.asyncio
async def test_pack_polygon_rejects_large_or_divergent_time_limit_and_verifies_exports(tmp_path):
    pdir = _make_managed_dir(tmp_path, "tl_guard")
    manifest = default_manifest("TL Guard")
    manifest.time_limit_ms = 2000
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    save_manifest(str(pdir), manifest)

    for i in range(1, 5):
        (pdir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (pdir / "tests" / f"{i:02d}.ans").write_text(f"{i * 2}\n", encoding="utf-8")

    pack = ProblemPackPolygonTool()

    # >60s deprecated time_limit rejected
    res_big = await pack.execute(problem_dir=str(pdir), time_limit=120)
    assert not res_big.success
    assert "time_limit_ms" in (res_big.error or "")

    # >10x deviation vs manifest.time_limit_ms (25s vs 2s = 12.5x) rejected unless override
    res_div = await pack.execute(problem_dir=str(pdir), time_limit=25)
    assert not res_div.success
    assert "deviates" in (res_div.error or "") or "10x" in (res_div.error or "")

    # Explicit override allowed when <= 60s
    res_override = await pack.execute(
        problem_dir=str(pdir),
        time_limit=25,
        allow_time_limit_override=True,
        export_formats=["polygon", "hydro", "qoj"],
        export_data_mode="zip",
    )
    assert res_override.success, res_override.error
    assert res_override.data["time_limit_ms"] == 25000
    assert res_override.data["export_verification"]["passed"] is True

    # Normal pack with time_limit_ms=2000 passes read-back verification across all 3 formats
    res_ok = await pack.execute(
        problem_dir=str(pdir),
        time_limit_ms=2000,
        export_formats=["polygon", "hydro", "qoj"],
        export_data_mode="copy",
    )
    assert res_ok.success, res_ok.error
    ev = res_ok.data["export_verification"]
    assert ev["passed"] is True
    assert ev["checks"]["polygon"]["time_limit_ms"] == 2000
    assert ev["checks"]["hydro"]["time_limit_ms"] == 2000
    assert ev["checks"]["qoj"]["time_limit_ms"] == 2000

    # P2-4: Hydro problem.yaml must NOT inject internal 'autocode' tag
    pyaml = yaml.safe_load((pdir / "exports" / "hydro" / "problem.yaml").read_text(encoding="utf-8"))
    assert "autocode" not in (pyaml.get("tag") or [])

    # Tamper problem.xml TL -> verify_exported_packages and problem_audit both catch it
    xml_path = pdir / "problem.xml"
    tree = ET.parse(xml_path)
    tl_node = tree.getroot().find(".//judging/testset/time-limit")
    assert tl_node is not None
    tl_node.text = "2000000"
    tree.write(xml_path, encoding="utf-8")

    tampered = verify_exported_packages(
        str(pdir),
        expected_time_limit_ms=2000,
        expected_memory_limit_mb=256,
        expected_test_count=4,
    )
    assert tampered["passed"] is False
    assert any("2000000" in err for err in tampered["errors"])

    set_section(
        str(pdir),
        WORKFLOW,
        {
            "tests_verified": True,
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
            },
        },
    )
    audit_res = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    assert audit_res.success
    assert audit_res.data["decision"] == "no_go"
    assert any(b["gate"] == "exported_packages" for b in audit_res.data["blocking_issues"])


# ============== P1-1: Relative runtime paths & pure recompile preservation ==============


def test_runtime_uses_relative_paths_and_pure_recompile_preserves_verified_stages(tmp_path):
    pdir = _make_managed_dir(tmp_path, "rel_state")
    (pdir / "solutions" / "brute.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")

    # Initial builds & full downstream progress
    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "sol"}, True, {})
    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "brute"}, True, {})
    apply_result(str(pdir), "generator_build", {"problem_dir": str(pdir)}, True, {})
    st = load_workflow_state(str(pdir))
    assert st["problem_dir"] == "."
    assert st["build_platform"] == current_platform()
    raw_runtime = (pdir / ".autocode" / "runtime.json").read_text(encoding="utf-8")
    assert str(pdir) not in raw_runtime

    st.update(
        {
            "solution_analyzed": True,
            "std_audited": True,
            "brute_audited": True,
            "stress_passed": True,
            "stress_completed_rounds": 100,
            "stress_total_rounds": 100,
            "statement_validated": True,
            "sample_files_validated": True,
            "validation_passed": True,
            "tests_generated": True,
            "generated_test_count": 20,
            "tests_verified": True,
            "packaged": True,
            "build_platform": "darwin/arm64" if current_platform() != "darwin/arm64" else "linux/x86_64",
        }
    )
    save_workflow_state(str(pdir), st)

    # Pure cross-platform recompile (source hashes unchanged!) preserves analysis/audit/stress/tests_generated
    st_after = apply_result(
        str(pdir),
        "problem_build_all",
        {"problem_dir": str(pdir)},
        True,
        {
            "compiled": {
                "solutions/sol.cpp": "solutions/sol",
                "solutions/brute.cpp": "solutions/brute",
                "files/gen.cpp": "files/gen",
            },
            "failures": {},
        },
    )
    assert st_after["solution_analyzed"] is True
    assert st_after["std_audited"] is True
    assert st_after["brute_audited"] is True
    assert st_after["stress_passed"] is True
    assert st_after["validation_passed"] is True
    assert st_after["tests_generated"] is True
    assert st_after["generated_test_count"] == 20
    assert st_after["tests_verified"] is False
    assert st_after["packaged"] is False
    assert st_after["build_platform"] == current_platform()

    # Actual source change still invalidates downstream
    (pdir / "solutions" / "sol.cpp").write_text("int main(){return 1;}\n", encoding="utf-8")
    st_changed = apply_result(
        str(pdir),
        "solution_build",
        {"problem_dir": str(pdir), "solution_type": "sol"},
        True,
        {},
    )
    assert st_changed["solution_analyzed"] is False
    assert st_changed["stress_passed"] is False
    assert st_changed["tests_generated"] is False


# ============== P1-2: Custom generator types, append/register_existing & hack preservation ==============


@pytest.mark.asyncio
async def test_custom_generator_types_and_register_existing_and_hack_preservation(tmp_path):
    assert CasePlanItem(name="hack-1", type="5", seed=1).type == "5"
    assert CasePlanItem(name="hack-2", type="hack", seed=2).type == "hack"
    with pytest.raises(ValueError):
        CasePlanItem(name="bad", type="../escape", seed=1)

    pdir = _make_managed_dir(tmp_path, "hack_tests")
    for i in range(1, 5):
        (pdir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (pdir / "tests" / f"{i:02d}.ans").write_text(f"{i * 2}\n", encoding="utf-8")
    (pdir / "tests" / "hack").mkdir()
    (pdir / "tests" / "hack" / "edge.in").write_text("999\n", encoding="utf-8")
    (pdir / "tests" / "hack" / "edge.ans").write_text("1998\n", encoding="utf-8")

    gen_tool = ProblemGenerateTestsTool()
    reg_res = await gen_tool.execute(problem_dir=str(pdir), mode="register_existing")
    assert reg_res.success, reg_res.error
    assert reg_res.data["mode"] == "register_existing"
    assert len(reg_res.data["generated_tests"]) == 4
    assert (pdir / "tests" / "01.in").read_text(encoding="utf-8") == "1\n"
    tm = get_section(str(pdir), TEST_MANIFEST)
    assert tm is not None and len(tm["tests"]) == 4

    # Interactive exports omit legacy "OK\n" placeholder .ans files
    ipdir = _make_managed_dir(tmp_path, "inter_ok", problem_type="interactive", oracle_mode="interactor_only")
    (ipdir / "files" / "interactor.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (ipdir / "tests" / "01.in").write_text("10\n", encoding="utf-8")
    (ipdir / "tests" / "01.ans").write_text("OK\n", encoding="utf-8")
    imanifest = default_manifest("Inter OK", problem_type="interactive", oracle_mode="interactor_only")
    h_res = export_hydro(
        str(ipdir),
        imanifest,
        time_limit_ms=1000,
        memory_limit_mb=256,
        in_files=["01.in"],
        answer_ext=".ans",
        data_mode="copy",
    )
    assert h_res["omitted_empty_answers"] == ["01.ans"]
    assert not (Path(h_res["output_dir"]) / "testdata" / "01.ans").exists()

    q_res = export_qoj(
        str(ipdir),
        imanifest,
        time_limit_ms=1000,
        memory_limit_mb=256,
        in_files=["01.in"],
        answer_ext=".ans",
        data_mode="copy",
    )
    assert q_res["omitted_empty_answers"] == ["01.ans"]
    assert not (Path(q_res["output_dir"]) / "data" / "1.ans").exists()


# ============== P1-3 & P1-4: Fingerprinted verify merge, auto-compile wrong_*.cpp, boundary_coverage ==============


@pytest.mark.asyncio
async def test_verify_fingerprinted_merge_auto_compile_wrong_and_boundary_coverage(tmp_path):
    pdir = _make_managed_dir(tmp_path, "verify_p13")
    manifest = default_manifest("Verify P13")
    manifest.constraints = {"n": {"min": 1, "max": 100}}
    save_manifest(str(pdir), manifest)

    exe_ext = get_exe_extension()
    sol_comp = await compile_cpp(
        str(pdir / "solutions" / "sol.cpp"),
        str(pdir / "solutions" / f"sol{exe_ext}"),
    )
    assert sol_comp.success, sol_comp.stderr

    # Undeclared, uncompiled wrong solution on disk
    (pdir / "solutions" / "wrong_offbyone.cpp").write_text(
        "#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<(n==100?n*2+1:n*2)<<'\\n';}\n",
        encoding="utf-8",
    )

    test_vals = [1, 10, 50, 100]
    for idx, val in enumerate(test_vals, 1):
        (pdir / "tests" / f"{idx:02d}.in").write_text(f"{val}\n", encoding="utf-8")
        (pdir / "tests" / f"{idx:02d}.ans").write_text(f"{val * 2}\n", encoding="utf-8")
    set_section(
        str(pdir),
        TEST_MANIFEST,
        {
            "version": 1,
            "answer_ext": ".ans",
            "tests": [
                {"index": i, "in_file": f"{i:02d}.in", "ans_file": f"{i:02d}.ans", "type_param": t, "signature": f"s{i}"}
                for i, t in enumerate(["1", "2", "3", "4"], 1)
            ],
        },
    )

    verifier = ProblemVerifyTestsTool()

    # 1. Default run executes full manifest suite including auto-discovered & auto-compiled wrong_offbyone
    res_default = await verifier.execute(problem_dir=str(pdir))
    assert res_default.success, res_default.data
    assert "duplicate_inputs" in res_default.data["results"]
    assert "wrong_solution_kill" in res_default.data["results"]
    wk = res_default.data["results"]["wrong_solution_kill"]
    assert wk["passed"] is True
    assert "wrong_offbyone" in wk.get("undeclared_wrong_solutions", [])
    assert wk["details"][0]["auto_compiled"] is True
    assert wk["details"][0]["killed"] is True

    # P3-1: missing validator message does NOT hardcode val.exe on POSIX
    val_msg = res_default.data["results"]["validator"]["message"]
    if sys.platform != "win32":
        assert "val.exe" not in val_msg
    assert f"files/val{exe_ext}" in val_msg

    # P1-6: non-applicable self-tests marked not_applicable with passed=True
    qs = res_default.data["quality_signals"]
    assert qs["checker_self_test"]["status"] == "not_applicable"
    assert qs["checker_self_test"]["passed"] is True
    assert qs["interactor_self_test"]["status"] == "not_applicable"
    assert qs["interactor_self_test"]["passed"] is True

    # 2. Subset re-run on unchanged dataset preserves prior fingerprinted signals (no whack-a-mole!)
    res_sub = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["boundary_coverage"],
        enable_limit_ratio=False,
    )
    assert res_sub.success, res_sub.data
    assert res_sub.data["results"]["boundary_coverage"]["passed"] is True
    wf = get_section(str(pdir), WORKFLOW)
    assert wf is not None
    assert wf["verify_signals"]["wrong_solution_kill"]["executed"] is True
    assert wf["verify_signals"]["wrong_solution_kill"]["passed"] is True
    assert wf["verify_signals"]["boundary_coverage"]["passed"] is True

    # 3. Modifying a test file changes dataset fingerprint and invalidates un-run signals;
    #    subset-only run without core checks must NOT mark tests_verified=True (#4).
    (pdir / "tests" / "04.in").write_text("99\n", encoding="utf-8")
    (pdir / "tests" / "04.ans").write_text("198\n", encoding="utf-8")
    res_after_edit = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["file_count"],
        enable_limit_ratio=False,
    )
    assert res_after_edit.success
    wf2 = get_section(str(pdir), WORKFLOW)
    assert wf2 is not None
    assert wf2["verify_signals"]["wrong_solution_kill"]["executed"] is False
    assert wf2["tests_verified"] is False


# ============== P1-5: Truncation & 128KB global MCP payload cap ==============


@pytest.mark.asyncio
async def test_validation_truncation_and_global_mcp_payload_cap(tmp_path):
    pdir = _make_managed_dir(tmp_path, "huge_output")
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\n#include <string>\n"
        "int main(){int n; std::cin>>n; std::cout<<std::string(n, 'A')<<'\\n';}\n",
        encoding="utf-8",
    )
    exe_ext = get_exe_extension()
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "sol.cpp"),
            str(pdir / "solutions" / f"sol{exe_ext}"),
        )
    ).success

    big_ans = "A" * 10000
    (pdir / "tests" / "01.in").write_text("10000\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text(big_ans + "\n", encoding="utf-8")

    val_res = await ProblemValidateTool().execute(
        problem_dir=str(pdir),
        validate_types=["sample_files"],
    )
    assert val_res.success, val_res.error
    sf = val_res.data["sample_files"]
    assert sf["truncated"] is True
    assert sf["details_log_path"].startswith(".autocode/logs/")
    assert (pdir / sf["details_log_path"]).is_file()
    assert len(sf["details"][0]["actual"]) < 2500

    # Global MCP 128KB ceiling spills oversized payloads to .autocode/logs/
    giant_payload = {"success": True, "error": None, "data": {"blob": "X" * 300_000}}
    capped_dict, capped_text = _cap_mcp_payload("problem_validate", str(pdir), giant_payload)
    assert len(capped_text.encode("utf-8")) <= 128 * 1024
    assert capped_dict["data"]["truncated"] is True
    assert (pdir / capped_dict["data"]["full_output_path"]).is_file()


# ============== P1-6: Interactive scenario fallback & scoped audit gates ==============


@pytest.mark.asyncio
async def test_interactive_validate_scenarios_fallback_and_missing_binary_guard(tmp_path):
    from autocode_mcp.tools.validation import _load_interactor_scenarios_fallback

    pdir = _make_managed_dir(tmp_path, "inter_val", problem_type="interactive", oracle_mode="interactor_only")
    (pdir / "statements" / "README.md").write_text(
        "# Interactive\n\n## 交互协议\n\n每次查询后必须 flush，最多查询 20 次，输出最终答案后退出。"
        "交互器返回结果。\n\nJudge: 10\nContestant: ! 5\n",
        encoding="utf-8",
    )
    (pdir / "files" / "interactor.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / ".autocode" / "interactor_scenarios.json").write_text(
        json.dumps(
            {
                "scenarios": [
                    {"name": "s_ac", "input": "10\n", "expected_verdict": "AC"},
                    {"name": "s_wa", "input": "99\n", "expected_verdict": "WA"},
                    {"name": "s_dup", "input": "10\n", "expected_verdict": "AC"},
                ]
            }
        ),
        encoding="utf-8",
    )

    # Negative expected_verdict="WA" and duplicate inputs are filtered out (#10)
    fallback_cases = _load_interactor_scenarios_fallback(str(pdir))
    assert len(fallback_cases) == 1
    assert fallback_cases[0][0] == "s_ac"

    # Sources + live scenarios exist but binaries missing -> fails live validation and emits warning (#10)
    res_missing = await ProblemValidateTool().execute(problem_dir=str(pdir))
    assert not res_missing.success
    assert res_missing.data["sample_files"]["validated"] is False
    assert "warning" in res_missing.data["live_interaction"]

    # Explicit statement_samples with missing binaries -> fails loudly instead of silently passing
    res_explicit = await ProblemValidateTool().execute(
        problem_dir=str(pdir),
        statement_samples=[{"input": "10\n", "expected_output": ""}],
    )
    assert not res_explicit.success


# ============== P2-1: Brute empirical skip, n_max clamp, manifest claimed_complexity & cache ==============


@pytest.mark.asyncio
async def test_empirical_brute_skip_clamp_and_manifest_claimed_complexity_cache(tmp_path, monkeypatch):
    import autocode_mcp.tools.audit as audit_mod

    pdir = _make_managed_dir(tmp_path, "emp_p21")
    manifest = default_manifest("Emp P21")
    manifest.constraints = {"n_max": 15}
    manifest.empirical_complexity = EmpiricalComplexityConfig(claimed_complexity="O(n log n)")
    save_manifest(str(pdir), manifest)

    # Brute empirical verification returns by_design_slow immediately
    brute_emp = await run_empirical_verification(str(pdir), "brute", "O(2^n)", {"n_max": 15})
    assert brute_emp["status"] == "by_design_slow"
    assert brute_emp["passed"] is True
    assert brute_emp["skipped"] is True

    # SolutionAuditBruteTool clamps recommended_stress_params.n_max to problem n_max (15)
    brute_res = await SolutionAuditBruteTool().execute(
        code="int main(){int n; for(int i=0;i<n;i++) for(int j=0;j<n;j++){}}",
        problem_dir=str(pdir),
        constraints={"n_max": 15},
    )
    assert brute_res.success
    assert brute_res.data["recommended_stress_params"]["n_max"] <= 15

    # SolutionAnalyzeTool picks up manifest.empirical_complexity.claimed_complexity
    an_res = await SolutionAnalyzeTool().execute(problem_dir=str(pdir), solution_type="sol")
    assert an_res.success
    assert an_res.data["claimed_complexity"] == "O(n log n)"

    # ProblemAuditTool caches empirical verification across repeated runs when binaries exist (#20)
    exe_ext = get_exe_extension()
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / f"gen{exe_ext}").write_bytes(b"fake-gen")
    (pdir / "solutions" / f"sol{exe_ext}").write_bytes(b"fake-sol")
    for i in range(1, 5):
        (pdir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (pdir / "tests" / f"{i:02d}.ans").write_text(f"{i * 2}\n", encoding="utf-8")
    set_section(
        str(pdir),
        WORKFLOW,
        {
            "tests_verified": True,
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
            },
        },
    )
    calls = {"count": 0, "claimed": None}

    async def fake_emp(pd, stype, claimed, constraints):
        calls["count"] += 1
        calls["claimed"] = claimed
        return {"status": "verified", "passed": True, "fitted_complexity": "O(n log n)", "r_squared": 0.99}

    monkeypatch.setattr(audit_mod, "run_empirical_verification", fake_emp)

    a1 = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    a2 = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    assert a1.success and a2.success
    assert calls["claimed"] == "O(n log n)"
    assert calls["count"] == 1
    assert a2.data["quality_signals"]["empirical_complexity"]["evidence"].get("cached") is True

    # Deleting compiled sol binary bypasses empirical cache (#20)
    (pdir / "solutions" / f"sol{exe_ext}").unlink()
    a3 = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    assert a3.success
    assert calls["count"] == 2


# ============== P2-3: LaTeX numeric bound parsing & val.cpp cross-check ==============


@pytest.mark.asyncio
async def test_statement_consistency_latex_bounds_and_validator_crosscheck(tmp_path):
    bounds = _extract_statement_numeric_bounds(
        r"给定 $1 \le n \le 2 \times 10^5$, $a_i \le 10^9 + 7$, $m \le 2^{20}$, $k \le 1,000,000$。"
    )
    assert 200_000 in bounds
    assert 1_000_000_007 in bounds
    assert 1_048_576 in bounds
    assert 1_000_000 in bounds

    pdir = _make_managed_dir(tmp_path, "stmt_p23")
    manifest = default_manifest("Stmt P23")
    manifest.constraints = {"n_max": 200_000}
    save_manifest(str(pdir), manifest)

    val_cpp = pdir / "files" / "val.cpp"
    val_cpp.write_text(
        "const int MAXN = 200000;\nint main(){ int n = inf.readInt(1, MAXN, \"n\"); }\n",
        encoding="utf-8",
    )
    vbounds = _extract_validator_bounds(val_cpp)
    assert vbounds and vbounds[0]["max"] == 200_000

    tool = ProblemAuditTool()
    sc_ok = tool._statement_consistency(pdir, manifest, {"tests": [1]})
    assert sc_ok["passed"] is True
    assert sc_ok["constraint_terms_missing_from_statement"] == []
    assert sc_ok["validator_bound_mismatches"] == []
    assert sc_ok["needs_human_review"] is False

    # Mismatched validator bound triggers validator_bound_mismatches & needs_human_review
    val_cpp.write_text(
        "int main(){ int n = inf.readInt(1, 500000, \"n\"); }\n",
        encoding="utf-8",
    )
    sc_bad = tool._statement_consistency(pdir, manifest, {"tests": [1]})
    assert sc_bad["validator_bound_mismatches"]
    assert sc_bad["needs_human_review"] is True


# ============== P3-3: Sanitizer probe + stress_test_run(enable_sanitizers=True) ==============


@pytest.mark.asyncio
async def test_sanitizer_probe_and_stress_catches_oob_without_overwriting_sol(tmp_path):
    probe = probe_sanitizer_support("g++", use_cache=False)
    if not probe["available"]:
        pytest.skip(f"Sanitizers unavailable on this toolchain: {probe.get('reason')}")
    assert probe["negative_control_passed"] is True
    assert probe["asan_verified"] or probe["ubsan_verified"]

    pdir = _make_managed_dir(tmp_path, "san_stress")
    # Build normal -O2 sol & brute + gen
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\n"
        "int main(){ int n; std::cin >> n; int* a = new int[2]; a[0] = n; a[1] = n; "
        "volatile int oob = a[5]; std::cout << n + (oob & 0) << '\\n'; delete[] a; }\n",
        encoding="utf-8",
    )
    (pdir / "solutions" / "brute.cpp").write_text(
        "#include <iostream>\nint main(){ int n; std::cin >> n; std::cout << n << '\\n'; }\n",
        encoding="utf-8",
    )
    (pdir / "files" / "gen.cpp").write_text(
        "#include <iostream>\nint main(){ std::cout << 7 << '\\n'; }\n",
        encoding="utf-8",
    )
    exe_ext = get_exe_extension()
    sol_bin = pdir / "solutions" / f"sol{exe_ext}"
    assert (await SolutionBuildTool().execute(problem_dir=str(pdir), solution_type="sol")).success
    assert (await SolutionBuildTool().execute(problem_dir=str(pdir), solution_type="brute")).success
    assert (
        await compile_cpp(
            str(pdir / "files" / "gen.cpp"),
            str(pdir / "files" / f"gen{exe_ext}"),
        )
    ).success
    orig_sol_bytes = sol_bin.read_bytes()

    san_stress = await StressTestRunTool().execute(
        problem_dir=str(pdir),
        trials=2,
        n_max=10,
        enable_sanitizers=True,
    )
    if probe["asan_verified"]:
        assert not san_stress.success
        assert san_stress.data.get("sanitizer_error") is True
    assert san_stress.data.get("sanitizers_enabled") is True
    # Production solutions/sol binary was NOT overwritten
    assert sol_bin.read_bytes() == orig_sol_bytes
    assert (pdir / ".autocode" / "san_bin").is_dir()


# ============== P3-4: Unified CLI autocode-verify --status / --strict-gates / --reconcile ==============


def test_autocode_verify_unified_status_strict_gates_and_reconcile(monkeypatch, capsys, tmp_path):
    from autocode_mcp.cli.verify import main as verify_main

    pdir = _make_managed_dir(tmp_path, "cli_unified")
    save_workflow_state(
        str(pdir),
        {
            "created": True,
            "sol_built": True,
            "tests_generated": True,
            "generated_test_count": 2,
            "tests_verified": True,
            "build_platform": "fakeos/fakearch",
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
            },
        },
    )

    # Default mode remains backwards-compatible (exit 0 when paths/manifest ok)
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(pdir)])
    assert verify_main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["platform_mismatch"] is True
    assert out["workflow_state_present"] is True
    assert out["gate_summary"]["pack_ready"] is False

    # --strict-gates fails when platform mismatch or MCP gates fail
    monkeypatch.setattr("sys.argv", ["autocode-verify", str(pdir), "--status", "--strict-gates"])
    assert verify_main() == 1
    out_strict = json.loads(capsys.readouterr().out)
    assert out_strict["success"] is False

    # --reconcile clears stale built/verified flags without fabricating any passes
    # and preserves source hashes so a subsequent problem_build_all pure recompile keeps verified stages (#5)
    (pdir / "solutions" / "brute.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "sol"}, True, {})
    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "brute"}, True, {})
    apply_result(str(pdir), "generator_build", {"problem_dir": str(pdir)}, True, {})
    st_pre = load_workflow_state(str(pdir))
    st_pre.update(
        {
            "solution_analyzed": True,
            "stress_passed": True,
            "tests_generated": True,
            "generated_test_count": 2,
            "tests_verified": True,
            "build_platform": "fakeos/fakearch",
        }
    )
    save_workflow_state(str(pdir), st_pre)

    monkeypatch.setattr("sys.argv", ["autocode-verify", str(pdir), "--reconcile"])
    assert verify_main() == 0
    out_rec = json.loads(capsys.readouterr().out)
    assert out_rec["reconcile"]["reconciled"] is True
    assert out_rec["reconcile"]["actions"]
    st = load_workflow_state(str(pdir))
    assert st["sol_built"] is False
    assert st["tests_verified"] is False
    assert inspect_workspace_state(str(pdir))["gate_summary"]["pack_ready"] is False

    # Pure recompile via problem_build_all after --reconcile preserves analysis/stress/tests_generated (#5)
    st_rebuilt = apply_result(
        str(pdir),
        "problem_build_all",
        {"problem_dir": str(pdir)},
        True,
        {
            "compiled": {
                "solutions/sol.cpp": "solutions/sol",
                "solutions/brute.cpp": "solutions/brute",
                "files/gen.cpp": "files/gen",
            },
            "failures": {},
        },
    )
    assert st_rebuilt["sol_built"] is True
    assert st_rebuilt["solution_analyzed"] is True
    assert st_rebuilt["stress_passed"] is True
    assert st_rebuilt["tests_generated"] is True
    assert st_rebuilt["tests_verified"] is False

    # Helper API sanity check
    rec2 = reconcile_workspace_state(str(pdir))
    assert rec2["reconciled"] is True
    assert time.time() > 0


@pytest.mark.asyncio
async def test_generate_tests_append_and_replace_preserve_locked_and_hack_dir(tmp_path):
    """mode='append' appends after existing tests; mode='replace' preserves locked entries and tests/hack/*.in."""
    pdir = _make_managed_dir(tmp_path, "gen_modes")
    exe_ext = get_exe_extension()
    (pdir / "files" / "gen.cpp").write_text(
        "#include <iostream>\n"
        "int main(int argc, char** argv){ int s = argc>1 ? std::atoi(argv[1]) : 1; std::cout << s << '\\n'; }\n",
        encoding="utf-8",
    )
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "sol.cpp"),
            str(pdir / "solutions" / f"sol{exe_ext}"),
        )
    ).success
    assert (
        await compile_cpp(
            str(pdir / "files" / "gen.cpp"),
            str(pdir / "files" / f"gen{exe_ext}"),
        )
    ).success

    # Pre-seed 01.in (locked) + tests/hack/custom_hack.in
    (pdir / "tests" / "01.in").write_text("777\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("1554\n", encoding="utf-8")
    (pdir / "tests" / "hack").mkdir()
    (pdir / "tests" / "hack" / "custom_hack.in").write_text("888\n", encoding="utf-8")
    (pdir / "tests" / "hack" / "custom_hack.ans").write_text("1776\n", encoding="utf-8")
    set_section(
        str(pdir),
        TEST_MANIFEST,
        {
            "version": 1,
            "answer_ext": ".ans",
            "tests": [
                {
                    "index": 1,
                    "in_file": "01.in",
                    "ans_file": "01.ans",
                    "type_param": "4",
                    "group": "limit",
                    "purpose": "manual hack",
                    "locked": True,
                }
            ],
        },
    )

    gen = ProblemGenerateTestsTool()
    cfg = [
        {"type": "1", "n_min": 1, "n_max": 5, "t_min": 1, "t_max": 1, "count": 1},
        {"type": "4", "n_min": 5, "n_max": 10, "t_min": 1, "t_max": 1, "count": 1},
    ]

    # 1. append mode preserves 01.in untouched and appends 02.in, 03.in
    app_res = await gen.execute(problem_dir=str(pdir), test_count=2, test_configs=cfg, mode="append")
    assert app_res.success, app_res.error
    assert (pdir / "tests" / "01.in").read_text(encoding="utf-8") == "777\n"
    assert len(app_res.data["generated_tests"]) == 2
    assert app_res.data["total_tests"] == 3

    # 2. replace mode preserves locked 01.in AND tests/hack/custom_hack.in content
    rep_res = await gen.execute(problem_dir=str(pdir), test_count=2, test_configs=cfg, mode="replace")
    assert rep_res.success, rep_res.error
    all_inputs = {
        p.read_text(encoding="utf-8").strip()
        for p in (pdir / "tests").glob("*.in")
    }
    assert "777" in all_inputs
    assert "888" in all_inputs
    assert rep_res.data.get("preserved_locked_count", 0) == 2
    assert rep_res.data["total_tests"] == len(rep_res.data["generated_tests"]) + rep_res.data["preserved_locked_count"]


def test_linux_preload_prefers_static_sanitizer_profile_and_hydro_tag_guard(monkeypatch, tmp_path):
    """On Linux with /etc/ld.so.preload, probe_sanitizer_support tries -static-libasan -static-libubsan first."""
    import autocode_mcp.utils.compiler as comp_mod

    seen_cmds: list[list[str]] = []

    class FakeProc:
        def __init__(self, rc: int, stderr: str = "") -> None:
            self.returncode = rc
            self.stdout = ""
            self.stderr = stderr

    def fake_run(cmd, **_kwargs):
        seen_cmds.append(list(cmd))
        if "-std=c++17" in cmd:
            return FakeProc(0)
        mode = cmd[1] if len(cmd) > 1 else "ok"
        if mode == "ok":
            return FakeProc(0)
        if mode == "asan":
            return FakeProc(1, "ERROR: AddressSanitizer: heap-buffer-overflow")
        if mode == "ubsan":
            return FakeProc(1, "probe.cpp:19: runtime error: signed integer overflow")
        return FakeProc(0)

    monkeypatch.setattr(comp_mod.sys, "platform", "linux")
    monkeypatch.setattr(comp_mod, "_detect_linux_preload", lambda: True)
    monkeypatch.setattr(comp_mod.subprocess, "run", fake_run)

    res = probe_sanitizer_support("g++", use_cache=False)
    assert res["available"] is True
    assert res["profile"] == "static"
    assert "-static-libasan" in res["flags"]
    assert "-static-libubsan" in res["flags"]
    assert res["preload_detected"] is True
    assert res["negative_control_passed"] is True
    assert any("-static-libasan" in c for c in seen_cmds)

    # verify_exported_packages catches leaked 'autocode' tag in hydro/problem.yaml
    hdir = tmp_path / "exports" / "hydro"
    (hdir / "testdata").mkdir(parents=True)
    (hdir / "problem.yaml").write_text("title: X\ntag:\n  - autocode\n", encoding="utf-8")
    (hdir / "testdata" / "config.yaml").write_text("time: 1000ms\nmemory: 256m\nsubtasks: []\n", encoding="utf-8")
    v = verify_exported_packages(
        str(tmp_path),
        formats=["hydro"],
        expected_time_limit_ms=1000,
        expected_memory_limit_mb=256,
    )
    assert v["passed"] is False
    assert any("autocode" in e for e in v["errors"])


# ============== Security & Logic Review Regression Tests ==============


def _try_symlink(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
    """Create a symlink, skipping cleanly on Windows runners without symlink privileges."""
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if sys.platform == "win32":
            pytest.skip(f"Symlink creation requires elevated privileges on Windows: {exc}")
        raise


@pytest.mark.asyncio
async def test_security_stress_traversal_and_san_bin_symlink_guard(tmp_path):
    """C1: StressTestRunTool rejects path-traversal sol/brute names and symlinked .autocode/san_bin."""
    pdir = _make_managed_dir(tmp_path, "sec_stress")
    (pdir / "solutions" / "brute.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")

    bad_sol = await StressTestRunTool().execute(
        problem_dir=str(pdir),
        sol_name="../escape",
        trials=1,
    )
    assert not bad_sol.success
    assert "invalid sol_name" in (bad_sol.error or "").lower()

    bad_brute = await StressTestRunTool().execute(
        problem_dir=str(pdir),
        brute_name="sub/brute",
        trials=1,
    )
    assert not bad_brute.success
    assert "invalid brute_name" in (bad_brute.error or "").lower()

    bad_type = await StressTestRunTool().execute(
        problem_dir=str(pdir),
        types=["../evil"],
        trials=1,
    )
    assert not bad_type.success
    assert "invalid generator type" in (bad_type.error or "").lower()

    # Symlinked .autocode/san_bin escaping problem_dir must be refused before compiling sanitizers
    outside = tmp_path / "outside_san"
    outside.mkdir()
    _try_symlink(pdir / ".autocode" / "san_bin", outside, target_is_directory=True)
    exe_ext = get_exe_extension()
    (pdir / "files" / f"gen{exe_ext}").write_bytes(b"gen")
    (pdir / "solutions" / f"sol{exe_ext}").write_bytes(b"sol")
    (pdir / "solutions" / f"brute{exe_ext}").write_bytes(b"brute")
    san_res = await StressTestRunTool().execute(
        problem_dir=str(pdir),
        trials=1,
        enable_sanitizers=True,
    )
    assert not san_res.success
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_security_locked_and_hack_snapshot_blocks_traversal_and_symlinks(tmp_path):
    """C2: _snapshot_locked_and_hack_tests ignores traversal in TEST_MANIFEST and symlink escapes in tests/hack/."""
    pdir = _make_managed_dir(tmp_path, "sec_snap")
    outside_secret = tmp_path / "secret.txt"
    outside_secret.write_text("TOP_SECRET\n", encoding="utf-8")

    (pdir / "tests" / "hack").mkdir(parents=True, exist_ok=True)
    _try_symlink(pdir / "tests" / "hack" / "escape.in", outside_secret)
    (pdir / "tests" / "hack" / "legit.in").write_text("42\n", encoding="utf-8")
    (pdir / "tests" / "hack" / "legit.ans").write_text("84\n", encoding="utf-8")

    set_section(
        str(pdir),
        TEST_MANIFEST,
        {
            "version": 1,
            "answer_ext": ".ans",
            "tests": [
                {
                    "index": 1,
                    "in_file": "../../secret.txt",
                    "ans_file": "../../secret.txt",
                    "locked": True,
                }
            ],
        },
    )
    tool = ProblemGenerateTestsTool()
    preserved = tool._snapshot_locked_and_hack_tests(str(pdir / "tests"), str(pdir), ".ans")
    contents = [item["input_data"] for item in preserved]
    assert "TOP_SECRET\n" not in contents
    assert "42\n" in contents


@pytest.mark.asyncio
async def test_security_standalone_audit_and_symlink_logs_containment(tmp_path, monkeypatch):
    """H1/H2: standalone problem_audit never writes .autocode/logs/ inside problem_dir; symlinked logs not followed."""
    import autocode_mcp.server as server_mod
    from autocode_mcp.tools.base import ToolResult

    # H1: call_tool("problem_audit", {"standalone": True, ...}) with >128KB payload must NOT create .autocode/
    standalone_dir = tmp_path / "ext_pkg"
    standalone_dir.mkdir()

    class HugeAuditStub:
        async def execute(self, **_kwargs):
            return ToolResult.ok(blob="Z" * 200_000, decision="go")

    monkeypatch.setitem(server_mod.TOOLS, "problem_audit", HugeAuditStub())
    res = await server_mod.call_tool(
        "problem_audit",
        {"problem_dir": str(standalone_dir), "standalone": True},
    )
    assert not res.isError
    assert not (standalone_dir / ".autocode").exists()
    assert res.structuredContent is not None
    assert res.structuredContent["data"]["truncated"] is True
    spilled = res.structuredContent["data"]["full_output_path"]
    assert spilled and Path(spilled).is_file()
    assert not str(Path(spilled).resolve()).startswith(str(standalone_dir.resolve()))

    # H2: Symlinked .autocode/logs escaping problem_dir is never written to
    pdir = _make_managed_dir(tmp_path, "sec_logs")
    outside_logs = tmp_path / "outside_logs"
    outside_logs.mkdir()
    _try_symlink(pdir / ".autocode" / "logs", outside_logs, target_is_directory=True)
    giant = {"success": True, "error": None, "data": {"blob": "Y" * 200_000}}
    capped_dict, _ = _cap_mcp_payload("problem_validate", str(pdir), giant)
    assert list(outside_logs.iterdir()) == []
    spilled2 = capped_dict["data"]["full_output_path"]
    assert spilled2 and not spilled2.startswith(".autocode/")


@pytest.mark.asyncio
async def test_security_wrong_solution_unsafe_name_and_symlink_rejected(tmp_path):
    """H3: wrong_solution_kill rejects unsafe wrong_solution_names and symlinked wrong binaries."""
    pdir = _make_managed_dir(tmp_path, "sec_wrong")
    (pdir / "tests" / "01.in").write_text("5\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("10\n", encoding="utf-8")

    verifier = ProblemVerifyTestsTool()
    res_unsafe = await verifier._check_wrong_solution_kill(
        problem_dir=str(pdir),
        tests_dir=str(pdir / "tests"),
        wrong_solution_names=["../evil"],
        answer_ext=".ans",
        timeout=10,
    )
    assert res_unsafe["passed"] is False
    assert any("invalid wrong solution name" in str(d.get("reason", "")).lower() for d in res_unsafe["details"])

    outside_bin = tmp_path / "outside_bin"
    outside_bin.write_bytes(b"#!/bin/sh\nexit 0\n")
    exe_ext = get_exe_extension()
    _try_symlink(pdir / "solutions" / f"wrong_sym{exe_ext}", outside_bin)
    res_sym = await verifier._check_wrong_solution_kill(
        problem_dir=str(pdir),
        tests_dir=str(pdir / "tests"),
        wrong_solution_names=["wrong_sym"],
        answer_ext=".ans",
        timeout=10,
    )
    assert res_sym["passed"] is False
    assert any("symlink" in str(d.get("reason", "")).lower() for d in res_sym["details"])


@pytest.mark.asyncio
async def test_security_compile_cpp_extra_flags_allowlist(tmp_path):
    """H4: compile_cpp rejects non-allowlisted extra_flags such as -fplugin=..."""
    src = tmp_path / "a.cpp"
    src.write_text("int main(){return 0;}\n", encoding="utf-8")
    out = tmp_path / "a.out"
    res = await compile_cpp(
        str(src),
        str(out),
        extra_flags=["-fplugin=/tmp/evil.so"],
    )
    assert not res.success
    assert "disallowed" in (res.error or "").lower()


def test_security_verify_exported_packages_traversal_malformed_yaml_and_empty_checks(tmp_path):
    """H5/M4/#8: verify_exported_packages blocks Hydro traversal, malformed YAML, empty subtasks, and bad QOJ n_tests."""
    # 1. Hydro traversal in config.yaml
    hdir = tmp_path / "pkg1" / "exports" / "hydro"
    (hdir / "testdata").mkdir(parents=True)
    (hdir / "problem.yaml").write_text("title: Safe\n", encoding="utf-8")
    (hdir / "testdata" / "config.yaml").write_text(
        "time: 1000ms\nmemory: 256m\nsubtasks:\n  - score: 100\n    cases:\n"
        "      - input: ../../etc/passwd\n        output: 01.ans\n",
        encoding="utf-8",
    )
    v1 = verify_exported_packages(
        str(tmp_path / "pkg1"),
        formats=["hydro"],
        expected_time_limit_ms=1000,
        expected_memory_limit_mb=256,
    )
    assert v1["passed"] is False
    assert any("unsafe" in e.lower() or "missing" in e.lower() for e in v1["errors"])

    # 2. Malformed YAML handled gracefully (no uncaught YAMLError)
    hdir2 = tmp_path / "pkg2" / "exports" / "hydro"
    (hdir2 / "testdata").mkdir(parents=True)
    (hdir2 / "problem.yaml").write_text("title: [unterminated\n", encoding="utf-8")
    (hdir2 / "testdata" / "config.yaml").write_text("time: 1000ms\nmemory: 256m\nsubtasks: []\n", encoding="utf-8")
    v2 = verify_exported_packages(
        str(tmp_path / "pkg2"),
        formats=["hydro"],
        expected_time_limit_ms=1000,
        expected_memory_limit_mb=256,
    )
    assert v2["passed"] is False
    assert any("yaml" in e.lower() or "subtask" in e.lower() for e in v2["errors"])

    # 3. QOJ missing/zero n_tests or missing data files fails (#8)
    qdir = tmp_path / "pkg3" / "exports" / "qoj"
    (qdir / "data").mkdir(parents=True)
    (qdir / "problem.conf").write_text("time_limit 1\nmemory_limit 256\nn_tests 3\ninput_pre \ninput_suf in\noutput_pre \noutput_suf ans\n", encoding="utf-8")
    (qdir / "data" / "1.in").write_text("1\n", encoding="utf-8")
    (qdir / "data" / "1.ans").write_text("2\n", encoding="utf-8")
    v3 = verify_exported_packages(
        str(tmp_path / "pkg3"),
        formats=["qoj"],
        expected_time_limit_ms=1000,
        expected_memory_limit_mb=256,
        expected_test_count=3,
    )
    assert v3["passed"] is False
    assert any("missing" in e.lower() for e in v3["errors"])


def test_workflow_build_all_invalidates_changed_validator_and_tracks_target_platforms(tmp_path):
    """#2/#6: problem_build_all clears validator_ready when val.cpp changes; per-target target_platforms catches stale brute/gen."""
    pdir = _make_managed_dir(tmp_path, "wf_targets")
    (pdir / "solutions" / "brute.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "val.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")

    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "sol"}, True, {})
    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "brute"}, True, {})
    apply_result(str(pdir), "validator_build", {"problem_dir": str(pdir)}, True, {"accuracy": 1.0})
    apply_result(str(pdir), "generator_build", {"problem_dir": str(pdir)}, True, {})

    st = load_workflow_state(str(pdir))
    assert st["validator_ready"] is True

    # Modify val.cpp and rebuild via problem_build_all -> validator_ready must be invalidated (#2)
    (pdir / "files" / "val.cpp").write_text("int main(){return 1;}\n", encoding="utf-8")
    st2 = apply_result(
        str(pdir),
        "problem_build_all",
        {"problem_dir": str(pdir)},
        True,
        {"compiled": {"files/val.cpp": "files/val"}, "failures": {}},
    )
    assert st2["validator_ready"] is False

    # Simulate foreign platform build for brute while sol is rebuilt locally -> platform_mismatch remains True (#6)
    exe_ext = get_exe_extension()
    (pdir / "solutions" / f"sol{exe_ext}").write_bytes(b"sol")
    (pdir / "solutions" / f"brute{exe_ext}").write_bytes(b"brute")
    st2["target_platforms"] = {
        "solutions/sol.cpp": current_platform(),
        "solutions/brute.cpp": "foreignos/foreignarch",
    }
    st2["build_platform"] = current_platform()
    save_workflow_state(str(pdir), st2)

    ws = inspect_workspace_state(str(pdir))
    assert ws["platform_mismatch"] is True
    assert ws["binary_status"]["brute"]["stale"] is True


@pytest.mark.asyncio
async def test_solution_build_enable_sanitizers_isolated_from_production_and_gates(tmp_path):
    """#3: solution_build(enable_sanitizers=True) builds to .autocode/san_bin/ without touching solutions/sol or gates."""
    probe = probe_sanitizer_support("g++", use_cache=False)
    if not probe["available"]:
        pytest.skip(f"Sanitizers unavailable on this toolchain: {probe.get('reason')}")

    pdir = _make_managed_dir(tmp_path, "iso_san")
    exe_ext = get_exe_extension()
    prod_bin = pdir / "solutions" / f"sol{exe_ext}"
    prod_res = await SolutionBuildTool().execute(problem_dir=str(pdir), solution_type="sol")
    assert prod_res.success, prod_res.error
    apply_result(str(pdir), "solution_build", {"problem_dir": str(pdir), "solution_type": "sol"}, True, prod_res.data)
    st = load_workflow_state(str(pdir))
    st["solution_analyzed"] = True
    st["stress_passed"] = True
    save_workflow_state(str(pdir), st)
    orig_bytes = prod_bin.read_bytes()

    san_res = await SolutionBuildTool().execute(
        problem_dir=str(pdir),
        solution_type="sol",
        enable_sanitizers=True,
    )
    assert san_res.success, san_res.error
    assert san_res.data.get("sanitizers_enabled") is True
    assert ".autocode" in san_res.data["binary_path"]
    assert prod_bin.read_bytes() == orig_bytes

    st_after = apply_result(
        str(pdir),
        "solution_build",
        {"problem_dir": str(pdir), "solution_type": "sol", "enable_sanitizers": True},
        True,
        san_res.data,
    )
    assert st_after["solution_analyzed"] is True
    assert st_after["stress_passed"] is True


def test_statement_consistency_bound_mismatch_and_claimed_complexity_space_vs_time(tmp_path):
    """#11/#21/#22: Catch mismatched numeric bound even if key appears in text; parse complex C++ exprs; distinguish space vs time complexity."""
    from autocode_mcp.tools.complexity import extract_claimed_complexity

    # #22: Space complexity comment must NOT shadow Time complexity comment
    cpp_code = (
        "/*\n"
        " * Space complexity: O(n)\n"
        " * Time complexity: O(n log n)\n"
        " */\n"
        "int main() { return 0; }\n"
    )
    assert extract_claimed_complexity(cpp_code) == "O(n log n)"
    assert extract_claimed_complexity("// Space complexity: O(n)\nint main(){}\n") is None

    # #11 & #21: Statement says 1 <= n <= 10^5, manifest says n: {max: 200000} -> caught even though 'n' is in text
    pdir = _make_managed_dir(tmp_path, "stmt_strict")
    (pdir / "statements" / "README.md").write_text(
        "# Problem\n\n给定 $1 \\le n \\le 10^5$，$0 \\le x < 998244353$。\n\n"
        "## 样例\n```text\n200000\n```\n",
        encoding="utf-8",
    )
    manifest = default_manifest("Stmt Strict")
    manifest.constraints = {"n": {"min": 1, "max": 200_000}}
    save_manifest(str(pdir), manifest)

    # #21: Validator uses parenthesized shift + strict < modulo bound (998244352 = 998244353 - 1)
    (pdir / "files" / "val.cpp").write_text(
        "int main(){\n"
        "  int n = inf.readInt(1, (1 << 10) + (1 << 5), \"n\");\n"
        "  int x = inf.readInt(0, 998244352, \"x\");\n"
        "}\n",
        encoding="utf-8",
    )
    vbounds = _extract_validator_bounds(pdir / "files" / "val.cpp")
    assert any(b["max"] == 1056 for b in vbounds)

    sc = ProblemAuditTool()._statement_consistency(pdir, manifest, {"tests": [1]})
    # Fenced code block containing 200000 was stripped and 'n' in text no longer hides bound mismatch (#11/#21)
    assert any("200000" in term for term in sc["constraint_terms_missing_from_statement"])
    # Strict < modulo 998244352 matches 998244353 in statement, while 1056 is flagged as mismatch
    mismatched_maxes = [m["max"] for m in sc["validator_bound_mismatches"]]
    assert 1056 in mismatched_maxes
    assert 998244352 not in mismatched_maxes


