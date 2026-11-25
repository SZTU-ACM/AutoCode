"""Red-team verifier-sabotage regressions, eval harness checks, GEPA optimizer tests, and skill parity."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from autocode_mcp.runtime_store import TEST_MANIFEST, WORKFLOW, save_runtime, set_section
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.build_all import ProblemBuildAllTool
from autocode_mcp.tools.complexity import SolutionAnalyzeTool
from autocode_mcp.tools.file_ops import FileSaveTool
from autocode_mcp.tools.problem import ProblemCreateTool
from autocode_mcp.tools.problem_pack import ProblemPackPolygonTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.tools.validator import ValidatorBuildTool
from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow.enforcement import apply_result
from autocode_mcp.workflow.gates import preflight
from autocode_mcp.workflow.manifest import load_manifest, save_manifest
from autocode_mcp.workflow.models import SolutionEntry
from autocode_mcp.workflow.reconcile import inspect_workspace_state, reconcile_workspace_state
from autocode_mcp.workflow.state_io import load_workflow_state

REPO_ROOT = Path(__file__).resolve().parents[1]
CPP_REQUIRED = pytest.mark.skipif(not shutil.which("g++"), reason="g++ not available")

_STATEMENT_MD = """# Sum 1..N

## 时间限制与空间限制

- 时间限制：1000ms
- 空间限制：256MB

## 题目描述

给定整数 $n$（$1 \\le n \\le 500$），输出 $\\sum_{i=1}^n i$。

## 样例

```text
5
```
"""

_TUTORIAL_MD = (
    "# 题解\n\n"
    "使用循环累加或等差数列求和公式 $n(n+1)/2$，时间复杂度 $O(n)$，空间复杂度 $O(1)$，边界 $n=1, 500$ 均不会溢出。\n"
)

_SOL_CPP = """// Time Complexity: O(n)
#include <iostream>
int main() {
    long long n = 0;
    if (!(std::cin >> n)) return 0;
    long long ans = 0;
    for (long long i = 1; i <= n; ++i) ans += i;
    std::cout << ans << '\\n';
    return 0;
}
"""

_BRUTE_CPP = """// Time Complexity: O(n^2)
#include <iostream>
int main() {
    long long n = 0;
    if (!(std::cin >> n)) return 0;
    long long ans = 0;
    for (long long i = 1; i <= n; ++i) {
        for (long long j = 1; j <= i; ++j) ans += 1;
    }
    std::cout << ans << '\\n';
    return 0;
}
"""

_GEN_CPP = """#include <cstdlib>
#include <iostream>
int main(int argc, char* argv[]) {
    long long n = (argc > 1) ? std::atoll(argv[1]) : 10;
    if (n <= 0) n = 10;
    std::cout << n << '\\n';
    return 0;
}
"""

_VAL_STRICT_CPP = """#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(1, 500, "n");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""

_VAL_VACUOUS_CPP = """int main() {
    return 0;
}
"""


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


async def _run_tracked(problem_dir: str, tool: Any, **kwargs: Any):
    res = await tool.execute(problem_dir=problem_dir, **kwargs)
    apply_result(problem_dir, tool.name, {"problem_dir": problem_dir, **kwargs}, res.success, res.data)
    return res


def _try_symlink(target: Path, link_path: Path) -> bool:
    try:
        os.symlink(str(target), str(link_path))
        return True
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            return False
        raise


async def _setup_audited_problem(tmp_path: Path, name: str) -> str:
    pdir = str(tmp_path / name)
    assert (await _run_tracked(pdir, ProblemCreateTool(), problem_name=name, problem_type="standard")).success
    manifest = load_manifest(pdir)
    assert manifest is not None
    manifest.constraints = {"n_max": 500}
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", path="solutions/sol.cpp", expected="pass"),
        SolutionEntry(name="brute", role="brute", path="solutions/brute.cpp", expected="pass"),
    ]
    save_manifest(pdir, manifest)

    saver = FileSaveTool()
    assert (await _run_tracked(pdir, saver, path="statements/README.md", content=_STATEMENT_MD)).success
    assert (await _run_tracked(pdir, saver, path="statements/tutorial.md", content=_TUTORIAL_MD)).success
    assert (await _run_tracked(pdir, SolutionBuildTool(), solution_type="sol", code=_SOL_CPP)).success
    assert (await _run_tracked(pdir, SolutionBuildTool(), solution_type="brute", code=_BRUTE_CPP)).success
    assert (await _run_tracked(pdir, SolutionAnalyzeTool(), solution_type="sol")).success
    assert (await _run_tracked(pdir, SolutionAuditStdTool())).success
    assert (await _run_tracked(pdir, SolutionAuditBruteTool())).success
    return pdir


# ---------------------------------------------------------------------------
# 1. RedCodeAgent Gate-Sabotage Regression Tests (against real src/autocode_mcp)
# ---------------------------------------------------------------------------


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_redteam_validator_weakening_and_vacuous_validator_blocked(tmp_path: Path) -> None:
    """(a) Weakening files/val.cpp after validator_build & vacuous return 0 validators are blocked."""
    pdir = await _setup_audited_problem(tmp_path, "sabotage-val")
    exe_ext = get_exe_extension()

    # Valid build with both positive & negative cases passes
    good_val = await _run_tracked(
        pdir,
        ValidatorBuildTool(),
        code=_VAL_STRICT_CPP,
        test_cases=[
            {"input": "10\n", "expected_valid": True},
            {"input": "999\n", "expected_valid": False},
        ],
    )
    assert good_val.success and good_val.data["accuracy"] == 1.0
    assert load_workflow_state(pdir)["validator_ready"] is True
    assert preflight("generator_build", pdir, {"problem_dir": pdir}) == []

    # Vector 1: Out-of-band weakening of files/val.cpp on disk after validator_build passed
    val_src = Path(pdir) / "files" / "val.cpp"
    val_bin = Path(pdir) / "files" / f"val{exe_ext}"
    val_src.write_text(_VAL_VACUOUS_CPP, encoding="utf-8")
    bin_mtime = val_bin.stat().st_mtime
    os.utime(val_src, (bin_mtime + 5.0, bin_mtime + 5.0))

    inspected = inspect_workspace_state(pdir)
    assert inspected["binary_status"]["validator"]["stale"] is True
    assert any("files/val.cpp" in r for r in inspected["stale_reasons"])
    assert inspected["gate_summary"]["pack_ready"] is False

    reconciled = reconcile_workspace_state(pdir)
    assert reconciled["reconciled"] is True
    assert any("validator_ready" in act for act in reconciled["actions"])
    assert load_workflow_state(pdir)["validator_ready"] is False
    assert preflight("generator_build", pdir, {"problem_dir": pdir})

    # Re-establish valid validator, then test Vector 2: weaken + recompile via problem_build_all
    assert (
        await _run_tracked(
            pdir,
            ValidatorBuildTool(),
            code=_VAL_STRICT_CPP,
            test_cases=[
                {"input": "10\n", "expected_valid": True},
                {"input": "999\n", "expected_valid": False},
            ],
        )
    ).success
    assert load_workflow_state(pdir)["validator_ready"] is True

    val_src.write_text(_VAL_VACUOUS_CPP, encoding="utf-8")
    build_all = await _run_tracked(pdir, ProblemBuildAllTool())
    assert build_all.success
    st_after_rebuild = load_workflow_state(pdir)
    assert st_after_rebuild["validator_ready"] is False
    assert st_after_rebuild["validator_accuracy"] is None
    assert preflight("generator_build", pdir, {"problem_dir": pdir})

    # Vector 3: Direct vacuous validator_build (positive-only OR mixed failing < 0.9) stays blocked
    vac_pos_only = await _run_tracked(
        pdir,
        ValidatorBuildTool(),
        code=_VAL_VACUOUS_CPP,
        test_cases=[{"input": "10\n", "expected_valid": True}],
    )
    assert vac_pos_only.success and vac_pos_only.data["accuracy"] == 1.0
    assert load_workflow_state(pdir)["validator_ready"] is False

    vac_mixed = await _run_tracked(
        pdir,
        ValidatorBuildTool(),
        code=_VAL_VACUOUS_CPP,
        test_cases=[
            {"input": "10\n", "expected_valid": True},
            {"input": "999\n", "expected_valid": False},
        ],
    )
    assert vac_mixed.success and vac_mixed.data["accuracy"] == 0.5
    assert load_workflow_state(pdir)["validator_ready"] is False
    assert preflight("generator_build", pdir, {"problem_dir": pdir})


@pytest.mark.asyncio
async def test_redteam_forged_runtime_json_rejected_by_reconcile_and_audit(tmp_path: Path) -> None:
    """(b) Forging .autocode/runtime.json flags without real binaries/hashes is caught and cleared."""
    pdir = tmp_path / "forged-runtime"
    for sub in ("files", "solutions", "statements", "tests", ".autocode"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    (pdir / "statements" / "README.md").write_text(_STATEMENT_MD, encoding="utf-8")
    (pdir / "statements" / "tutorial.md").write_text(_TUTORIAL_MD, encoding="utf-8")
    (pdir / "solutions" / "sol.cpp").write_text(_SOL_CPP, encoding="utf-8")
    (pdir / "solutions" / "brute.cpp").write_text(_BRUTE_CPP, encoding="utf-8")
    (pdir / "files" / "val.cpp").write_text(_VAL_STRICT_CPP, encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text(_GEN_CPP, encoding="utf-8")
    (pdir / "tests" / "1.in").write_text("5\n", encoding="utf-8")
    (pdir / "tests" / "1.ans").write_text("15\n", encoding="utf-8")

    manifest_payload = {
        "schema_version": "1.0",
        "problem_name": "ForgedState",
        "problem_type": "standard",
        "constraints": {"n_max": 500},
        "solutions": [
            {"name": "sol", "role": "main", "path": "solutions/sol.cpp", "expected": "pass"},
            {"name": "brute", "role": "brute", "path": "solutions/brute.cpp", "expected": "pass"},
        ],
    }
    (pdir / ".autocode" / "manifest.json").write_text(json.dumps(manifest_payload), encoding="utf-8")

    # Forge all readiness flags in runtime.json without any compiled binaries or real hashes
    forged_wf = {
        "created": True,
        "sol_built": True,
        "brute_built": True,
        "solution_analyzed": True,
        "std_audited": True,
        "brute_audited": True,
        "validator_ready": True,
        "validator_accuracy": 1.0,
        "validator_has_positive_cases": True,
        "validator_has_negative_cases": True,
        "generator_built": True,
        "stress_passed": True,
        "stress_completed_rounds": 10,
        "stress_total_rounds": 10,
        "statement_validated": True,
        "sample_files_validated": True,
        "validation_passed": True,
        "tests_generated": True,
        "generated_test_count": 1,
        "tests_verified": True,
        "verify_signals": {},
        "packaged": True,
        "sol_source_hash": "0" * 64,
        "brute_source_hash": "f" * 64,
    }
    set_section(str(pdir), WORKFLOW, forged_wf)

    # 1. inspect_workspace_state detects missing binaries and incomplete gates
    inspected = inspect_workspace_state(str(pdir))
    assert inspected["stale_reasons"], "Expected stale_reasons for missing binaries"
    assert inspected["gate_summary"]["pack_ready"] is False
    assert inspected["gate_summary"]["quality_gate_issues"]

    # 2. reconcile_workspace_state rolls back forged flags
    rec = reconcile_workspace_state(str(pdir))
    assert rec["reconciled"] is True
    assert rec["actions"]
    st_after = load_workflow_state(str(pdir))
    for flag in ("sol_built", "brute_built", "validator_ready", "generator_built", "tests_verified", "packaged", "stress_passed"):
        assert st_after[flag] is False, f"Expected forged flag {flag} cleared"

    # 3. Managed ProblemAuditTool rejects forged workspace as no_go
    audit_managed = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full", standalone=False)
    assert audit_managed.success
    assert audit_managed.data["decision"] == "no_go"
    assert audit_managed.data["blocking_issues"]

    # 4. Standalone ProblemAuditTool ignores forged runtime.json completely
    (pdir / "solutions" / "wrong_untested.cpp").write_text(_SOL_CPP, encoding="utf-8")
    manifest_obj = load_manifest(str(pdir))
    assert manifest_obj is not None
    manifest_obj.solutions.append(
        SolutionEntry(name="wrong_untested", role="wrong", path="solutions/wrong_untested.cpp", expected="fail")
    )
    save_manifest(str(pdir), manifest_obj)
    set_section(str(pdir), WORKFLOW, forged_wf)
    audit_standalone = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full", standalone=True)
    assert audit_standalone.success
    assert audit_standalone.data["decision"] == "no_go"


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_redteam_symlink_escapes_blocked_across_autocode_tests_and_solutions(tmp_path: Path) -> None:
    """(c) Symlinks escaping problem_dir in .autocode, tests/, and solutions/ are blocked."""
    outside_dir = tmp_path / "outside_secrets"
    outside_dir.mkdir()
    secret_file = outside_dir / "secret.txt"
    secret_file.write_text("TOP_SECRET\n", encoding="utf-8")
    secret_cpp = outside_dir / "outside_sol.cpp"
    secret_cpp.write_text(_SOL_CPP, encoding="utf-8")
    secret_bin = outside_dir / f"outside_bin{get_exe_extension()}"
    assert (await compile_cpp(str(secret_cpp), str(secret_bin), use_cache=False)).success

    # 1. Symlinked .autocode/runtime.json escaping problem_dir
    pkg_meta = tmp_path / "esc-autocode"
    (pkg_meta / ".autocode").mkdir(parents=True)
    (pkg_meta / "tests").mkdir()
    (pkg_meta / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (pkg_meta / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    if not _try_symlink(secret_file, pkg_meta / ".autocode" / "runtime.json"):
        pytest.skip("symlinks require elevated privileges on this Windows host")

    standalone_meta = await ProblemAuditTool().execute(problem_dir=str(pkg_meta), mode="full", standalone=True)
    assert standalone_meta.success
    assert standalone_meta.data["decision"] == "no_go"
    assert any(b.get("gate") == "path_containment" for b in standalone_meta.data["blocking_issues"])

    managed_meta = await ProblemAuditTool().execute(problem_dir=str(pkg_meta), mode="full", standalone=False)
    assert managed_meta.success is False
    assert "symlink" in (managed_meta.error or "").lower()

    # save_runtime must also refuse writing through an escaping symlink
    save_runtime(str(pkg_meta), {"workflow": {"forged": True}})
    assert secret_file.read_text(encoding="utf-8") == "TOP_SECRET\n"

    # 2. Symlinked tests/01.in escaping problem_dir
    pkg_tests = tmp_path / "esc-tests"
    (pkg_tests / "tests").mkdir(parents=True)
    (pkg_tests / "solutions").mkdir()
    (pkg_tests / "solutions" / "sol.cpp").write_text(_SOL_CPP, encoding="utf-8")
    assert _try_symlink(secret_file, pkg_tests / "tests" / "01.in")
    (pkg_tests / "tests" / "01.ans").write_text("1\n", encoding="utf-8")

    res_tests = await ProblemAuditTool().execute(problem_dir=str(pkg_tests), mode="full", standalone=True)
    assert res_tests.success
    assert res_tests.data["decision"] == "no_go"
    assert any(b.get("gate") == "path_containment" for b in res_tests.data["blocking_issues"])

    # 3. Symlinked solutions/sol binary & solutions/sol.cpp source escaping problem_dir
    pkg_sol_bin = tmp_path / "esc-sol-bin"
    (pkg_sol_bin / "solutions").mkdir(parents=True)
    (pkg_sol_bin / "tests").mkdir()
    (pkg_sol_bin / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (pkg_sol_bin / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    assert _try_symlink(secret_bin, pkg_sol_bin / "solutions" / f"sol{get_exe_extension()}")

    res_sol_bin = await ProblemAuditTool().execute(problem_dir=str(pkg_sol_bin), mode="full", standalone=True)
    assert res_sol_bin.success
    assert res_sol_bin.data["decision"] == "no_go"
    assert any(b.get("gate") == "path_containment" for b in res_sol_bin.data["blocking_issues"])

    pkg_sol_src = tmp_path / "esc-sol-src"
    (pkg_sol_src / "solutions").mkdir(parents=True)
    (pkg_sol_src / "tests").mkdir()
    (pkg_sol_src / "statements").mkdir()
    (pkg_sol_src / ".autocode").mkdir()
    (pkg_sol_src / "statements" / "README.md").write_text(_STATEMENT_MD, encoding="utf-8")
    (pkg_sol_src / "statements" / "tutorial.md").write_text(_TUTORIAL_MD, encoding="utf-8")
    (pkg_sol_src / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (pkg_sol_src / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    (pkg_sol_src / ".autocode" / "manifest.json").write_text(
        json.dumps({"problem_name": "EscSolSrc", "solutions": [{"name": "sol", "role": "main", "path": "solutions/sol.cpp"}]}),
        encoding="utf-8",
    )
    assert _try_symlink(secret_cpp, pkg_sol_src / "solutions" / "sol.cpp")

    res_sol_src = await ProblemAuditTool().execute(problem_dir=str(pkg_sol_src), mode="full", standalone=True)
    assert res_sol_src.success
    assert res_sol_src.data["decision"] == "no_go"
    assert any(b.get("gate") == "main_solution_source" for b in res_sol_src.data["blocking_issues"])


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_redteam_hidden_duplicates_empty_tests_and_undeclared_wrongs(tmp_path: Path) -> None:
    """(d) Hidden duplicate/empty tests and undeclared wrong_*.cpp files are detected."""
    # 1. Duplicate byte-identical tests
    dup_dir = tmp_path / "hidden-dup"
    for sub in ("solutions", "tests", "statements", "files"):
        (dup_dir / sub).mkdir(parents=True)
    (dup_dir / "statements" / "README.md").write_text(_STATEMENT_MD, encoding="utf-8")
    (dup_dir / "statements" / "tutorial.md").write_text(_TUTORIAL_MD, encoding="utf-8")
    (dup_dir / "solutions" / "sol.cpp").write_text(_SOL_CPP, encoding="utf-8")
    (dup_dir / "files" / "val.cpp").write_text(_VAL_STRICT_CPP, encoding="utf-8")
    (dup_dir / "tests" / "01.in").write_text("5\n", encoding="utf-8")
    (dup_dir / "tests" / "01.ans").write_text("15\n", encoding="utf-8")
    (dup_dir / "tests" / "02.in").write_text("5\n", encoding="utf-8")
    (dup_dir / "tests" / "02.ans").write_text("15\n", encoding="utf-8")

    dup_res = await ProblemAuditTool().execute(problem_dir=str(dup_dir), mode="full", standalone=True)
    assert dup_res.success
    assert dup_res.data["decision"] == "no_go"
    assert any(b.get("gate") == "duplicate_inputs" for b in dup_res.data["blocking_issues"])

    # 2. Empty test file
    empty_dir = tmp_path / "hidden-empty"
    shutil.copytree(dup_dir, empty_dir)
    (empty_dir / "tests" / "02.in").write_text("", encoding="utf-8")
    empty_res = await ProblemAuditTool().execute(problem_dir=str(empty_dir), mode="full", standalone=True)
    assert empty_res.success
    assert empty_res.data["decision"] == "no_go"
    assert any(b.get("gate") == "no_empty" for b in empty_res.data["blocking_issues"])

    # 3. Undeclared wrong_*.cpp on disk: caught in standalone inference, ProblemVerifyTestsTool, and pack_warnings
    wrong_dir = tmp_path / "hidden-wrong"
    for sub in ("solutions", "tests", "statements", "files"):
        (wrong_dir / sub).mkdir(parents=True)
    (wrong_dir / "statements" / "README.md").write_text(_STATEMENT_MD, encoding="utf-8")
    (wrong_dir / "statements" / "tutorial.md").write_text(_TUTORIAL_MD, encoding="utf-8")
    (wrong_dir / "solutions" / "sol.cpp").write_text(_SOL_CPP, encoding="utf-8")
    (wrong_dir / "files" / "val.cpp").write_text(_VAL_STRICT_CPP, encoding="utf-8")
    # Stealth wrong solution that prints exact sum for n <= 500 (survives!)
    (wrong_dir / "solutions" / "wrong_stealth.cpp").write_text(_SOL_CPP, encoding="utf-8")
    (wrong_dir / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (wrong_dir / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    (wrong_dir / "tests" / "02.in").write_text("500\n", encoding="utf-8")
    (wrong_dir / "tests" / "02.ans").write_text("125250\n", encoding="utf-8")

    # Standalone without manifest discovers wrong_stealth.cpp and fails wrong_solution_kill
    wrong_standalone = await ProblemAuditTool().execute(problem_dir=str(wrong_dir), mode="full", standalone=True)
    assert wrong_standalone.success
    assert wrong_standalone.data["decision"] == "no_go"
    assert any(b.get("gate") == "wrong_solution_kill" for b in wrong_standalone.data["blocking_issues"])

    # Managed manifest omitting wrong_stealth.cpp -> ProblemVerifyTestsTool still discovers & flags it
    (wrong_dir / ".autocode").mkdir(exist_ok=True)
    (wrong_dir / ".autocode" / "manifest.json").write_text(
        json.dumps(
            {
                "problem_name": "HiddenWrong",
                "constraints": {"n_max": 500},
                "solutions": [{"name": "sol", "role": "main", "path": "solutions/sol.cpp", "expected": "pass"}],
            }
        ),
        encoding="utf-8",
    )
    set_section(
        str(wrong_dir),
        TEST_MANIFEST,
        {
            "version": 1,
            "answer_ext": ".ans",
            "tests": [
                {"index": 1, "in_file": "01.in", "ans_file": "01.ans", "type_param": "3", "signature": "s1"},
                {"index": 2, "in_file": "02.in", "ans_file": "02.ans", "type_param": "4", "signature": "s2"},
            ],
        },
    )
    exe_ext = get_exe_extension()
    assert (await compile_cpp(str(wrong_dir / "solutions" / "sol.cpp"), str(wrong_dir / "solutions" / f"sol{exe_ext}"))).success
    assert (await compile_cpp(str(wrong_dir / "files" / "val.cpp"), str(wrong_dir / "files" / f"val{exe_ext}"))).success

    verify_res = await ProblemVerifyTestsTool().execute(
        problem_dir=str(wrong_dir),
        verify_types=["wrong_solution_kill"],
        enable_limit_ratio=False,
    )
    assert verify_res.success is False
    wk = verify_res.data["results"]["wrong_solution_kill"]
    assert wk["passed"] is False
    assert "wrong_stealth" in wk.get("undeclared_wrong_solutions", [])

    # And ProblemPackPolygonTool warns when undeclared wrong_*.cpp exists on disk
    (wrong_dir / "solutions" / "wrong_stealth.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (wrong_dir / "tests" / "02.in").write_text("500\n", encoding="utf-8")
    set_section(
        str(wrong_dir),
        WORKFLOW,
        {
            "created": True,
            "sol_built": True,
            "brute_built": True,
            "solution_analyzed": True,
            "std_audited": True,
            "brute_audited": True,
            "validator_ready": True,
            "validator_accuracy": 1.0,
            "validator_has_positive_cases": True,
            "validator_has_negative_cases": True,
            "generator_built": True,
            "stress_passed": True,
            "stress_completed_rounds": 4,
            "stress_total_rounds": 4,
            "statement_validated": True,
            "sample_files_validated": True,
            "validation_passed": True,
            "tests_generated": True,
            "generated_test_count": 2,
            "tests_verified": True,
            "limit_case_ratio": 1.0,
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
                "wrong_solution_kill": {"executed": True, "passed": True},
            },
        },
    )
    pack_res = await ProblemPackPolygonTool().execute(problem_dir=str(wrong_dir))
    assert pack_res.success, pack_res.error
    assert any("wrong_stealth.cpp" in w for w in pack_res.data.get("pack_warnings", []))


# ---------------------------------------------------------------------------
# 2. Evals Suite Tests (Harbor Tasks, Promptfoo Matrix, Standalone Benchmark)
# ---------------------------------------------------------------------------


def test_evals_harbor_tasks_and_promptfoo_configs_valid() -> None:
    """Harbor task specs and Promptfoo matrix must cover all 4 problem types and 4 hosts."""
    evals_dir = REPO_ROOT / "evals"
    readme = (evals_dir / "README.md").read_text(encoding="utf-8")
    assert "standalone_benchmark.py" in readme
    assert "harbor_tasks" in readme
    assert "promptfoo.yaml" in readme

    for ptype in ("standard", "spj", "interactive", "communication"):
        task_path = evals_dir / "harbor_tasks" / ptype / "task.yaml"
        assert task_path.is_file(), f"Missing Harbor task: {task_path}"
        task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
        assert isinstance(task, dict)
        assert task["problem_type"] == ptype
        assert (REPO_ROOT / task["reference_example"]).is_dir()
        checks = task["acceptance_checks"]
        assert "autocode-audit" in checks["autocode_audit_cmd"]
        assert "--strict" in checks["autocode_audit_cmd"]
        assert checks["expected_decision"] == "go"
        assert checks["require_compiled_wrong_kill"] is True
        assert checks["zero_write_on_standalone_audit"] is True
        if ptype == "interactive":
            assert "std::cout" in task["instruction"]
            assert "std::flush" in task["instruction"]
            assert "registerInteraction" in task["instruction"]

    promptfoo = yaml.safe_load((evals_dir / "promptfoo.yaml").read_text(encoding="utf-8"))
    prompt_ids = {p["id"] for p in promptfoo["prompts"]}
    assert prompt_ids == {"claude-code", "codex", "dsh", "antigravity"}
    serialized = json.dumps(promptfoo)
    assert "registerInteraction" in serialized
    assert "std::cout" in serialized
    assert "second_interpretation" in serialized


@CPP_REQUIRED
def test_evals_standalone_benchmark_achieves_perfect_tpr_tnr_and_zero_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Standalone benchmark computes exact TPR/TNR=1.0, failure breakdown, and zero writes."""
    bench_mod = _load_module("standalone_benchmark", REPO_ROOT / "evals" / "standalone_benchmark.py")
    out_json = tmp_path / "bench_report.json"

    rc = bench_mod.main(["--json", "--output", str(out_json), "--min-tpr", "1.0", "--min-tnr", "1.0"])
    assert rc == 0, capsys.readouterr().out
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["total"] == 8
    assert payload["confusion_matrix"] == {"tp": 4, "tn": 4, "fp": 0, "fn": 0}
    assert payload["metrics"]["tpr"] == 1.0
    assert payload["metrics"]["tnr"] == 1.0
    assert payload["zero_write_passed"] is True
    assert payload["mismatched_cases"] == []
    for gate in ("wrong_solution_kill", "duplicate_inputs", "answer_consistency", "validator_check"):
        assert payload["failure_breakdown"].get(gate, 0) >= 1


# ---------------------------------------------------------------------------
# 3. GEPA Trace-Driven Skill Optimizer Tests
# ---------------------------------------------------------------------------


def test_gepa_optimizer_clusters_traces_and_falls_back_without_gepa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """optimize_skills_gepa.py clusters failure traces and generates pure-Python patches when gepa is absent."""
    gepa_mod = _load_module("optimize_skills_gepa", REPO_ROOT / "scripts" / "optimize_skills_gepa.py")
    monkeypatch.setattr(gepa_mod, "_try_import_gepa", lambda: None)

    skills_copy = tmp_path / "skills"
    shutil.copytree(REPO_ROOT / "skills", skills_copy)

    traces_jsonl = tmp_path / "traces.jsonl"
    trace_rows = [
        {
            "trace_id": "t1",
            "tool": "validator_build",
            "gate": "validator",
            "reason": "has_negative_cases=False; permissive 'return 0;' validator rejected",
        },
        {
            "trace_id": "t2",
            "tool": "interactor_build",
            "gate": "interactor_self_test",
            "reason": "Interactor timed out due to missing std::cout.flush() and using tout",
        },
        {
            "trace_id": "t3",
            "tool": "solution_audit_std",
            "gate": "empirical_complexity",
            "reason": "dynamic probe status=pending_run skipped after generator_build",
        },
        {
            "trace_id": "t4",
            "data": {
                "decision": "no_go",
                "blocking_issues": [
                    {"gate": "wrong_solution_kill", "reason": "wrong_greedy survived all tests"},
                    {"gate": "duplicate_inputs", "reason": "exact duplicate inputs detected"},
                ],
            },
        },
    ]
    traces_jsonl.write_text("\n".join(json.dumps(r) for r in trace_rows) + "\n", encoding="utf-8")

    # Dry-run does not modify copied skills
    before_wf = (skills_copy / "autocode-workflow" / "SKILL.md").read_text(encoding="utf-8")
    report = gepa_mod.optimize_skills_from_traces([traces_jsonl], skills_dir=skills_copy, apply_changes=False)
    assert report.backend == "deterministic"
    assert report.gepa_available is False
    assert report.total_events == 5
    cluster_ids = {c.cluster_id for c in report.clusters}
    assert {
        "missing_negative_validator_cases",
        "interactive_missing_stdout_flush",
        "post_generator_probe_skip_or_drift",
        "weak_testdata_or_surviving_wrong",
        "duplicate_or_empty_tests",
    }.issubset(cluster_ids)
    assert report.proposals
    assert all(p.pareto_score > 0 for p in report.proposals)
    assert any(p.unified_diff for p in report.proposals)
    assert report.applied_files == []
    assert (skills_copy / "autocode-workflow" / "SKILL.md").read_text(encoding="utf-8") == before_wf

    # CLI JSON output smoke check
    out_json = tmp_path / "gepa_report.json"
    rc = gepa_mod.main(
        ["--traces", str(traces_jsonl), "--skills-dir", str(skills_copy), "--json", "--output", str(out_json)]
    )
    assert rc == 0
    cli_payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert cli_payload["backend"] == "deterministic"
    assert len(cli_payload["clusters"]) == len(report.clusters)


def test_gepa_optimizer_uses_optional_gepa_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When optional gepa module is importable, optimizer uses it and degrades cleanly on error."""
    gepa_mod = _load_module("optimize_skills_gepa", REPO_ROOT / "scripts" / "optimize_skills_gepa.py")
    skills_copy = tmp_path / "skills"
    shutil.copytree(REPO_ROOT / "skills", skills_copy)
    trace_file = tmp_path / "trace.json"
    trace_file.write_text(
        json.dumps({"gate": "validator", "reason": "has_negative_cases=False"}),
        encoding="utf-8",
    )

    fake_gepa = SimpleNamespace(
        optimize=lambda seed_candidate, trainset, max_metric_calls: {
            "best_candidate": {
                "skills/autocode-workflow/SKILL.md": "Always pass positive and negative fixtures to validator_build."
            }
        }
    )
    monkeypatch.setattr(gepa_mod, "_try_import_gepa", lambda: fake_gepa)
    rep_gepa = gepa_mod.optimize_skills_from_traces([trace_file], skills_dir=skills_copy, apply_changes=True)
    assert rep_gepa.backend == "gepa"
    assert rep_gepa.gepa_available is True
    assert rep_gepa.proposals[0].backend == "gepa"
    assert "Always pass positive and negative fixtures to validator_build." in (
        skills_copy / "autocode-workflow" / "SKILL.md"
    ).read_text(encoding="utf-8")

    # Broken gepa.optimize falls back cleanly to deterministic backend
    broken_gepa = SimpleNamespace(optimize=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(gepa_mod, "_try_import_gepa", lambda: broken_gepa)
    rep_fb = gepa_mod.optimize_skills_from_traces([trace_file], skills_dir=skills_copy)
    assert rep_fb.backend == "deterministic"
    assert rep_fb.proposals


# ---------------------------------------------------------------------------
# 4. Progressive-Disclosure Skill Slimming & Multi-Host Bundle Parity
# ---------------------------------------------------------------------------


def test_skills_progressive_disclosure_links_frontmatter_and_bundle_parity(tmp_path: Path) -> None:
    """Slimmed SKILL.md files retain frontmatter, core checklists, valid links, and bundle inclusion."""
    whitelisted_skills = (
        "agent-skill-governance",
        "autocode-workflow",
        "idea-feasibility",
        "problem-difficulty-rating",
        "problem-validate",
        "solution-complexity-audit",
        "statement-audit",
        "stress-strategy",
        "testdata-quality",
    )
    link_re = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

    for skill_name in whitelisted_skills:
        skill_dir = REPO_ROOT / "skills" / skill_name
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file()
        text = skill_md.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert len(lines) <= 120, f"{skill_name}/SKILL.md exceeded 120 lines ({len(lines)})"
        assert text.startswith("---\n"), f"{skill_name}/SKILL.md missing YAML frontmatter"
        fm_raw = text.split("---\n", 2)[1]
        fm = yaml.safe_load(fm_raw)
        assert isinstance(fm, dict)
        assert fm.get("name") == skill_name
        assert isinstance(fm.get("description"), str) and fm["description"].strip()

        for target in link_re.findall(text):
            if target.startswith(("http://", "https://", "#")):
                continue
            resolved = (skill_dir / target).resolve()
            resolved.relative_to(skill_dir.resolve())
            assert resolved.is_file(), f"Broken link {target!r} in {skill_name}/SKILL.md"

    # Preserve critical domain tokens
    wf_text = (REPO_ROOT / "skills" / "autocode-workflow" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("enable_sanitizers", "std::cout", "registerInteraction", "accuracy >= 0.9", "wrong_solution_kill"):
        assert token in wf_text

    stmt_text = (REPO_ROOT / "skills" / "statement-audit" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("Orchid", "Anti-Semantic-Collapse", "second_interpretation", "constraints.h", "std::cout"):
        assert token in stmt_text

    idea_text = (REPO_ROOT / "skills" / "idea-feasibility" / "SKILL.md").read_text(encoding="utf-8")
    for token in ("Orchid", "Anti-Semantic-Collapse", "OriginalityIndex", "second_interpretation"):
        assert token in idea_text

    # Bundle parity across marketplace, agy, and dsh including references/*.md
    builder = _load_module("build_plugin_bundle", REPO_ROOT / "scripts" / "build_plugin_bundle.py")
    expected_refs = (
        Path("skills/autocode-workflow/references/tool-guidance.md"),
        Path("skills/autocode-workflow/references/protocols-and-manifest.md"),
        Path("skills/statement-audit/references/protocol-checklists.md"),
        Path("skills/agent-skill-governance/references/review-checklist.md"),
        Path("skills/idea-feasibility/references/orchid-and-originality.md"),
    )
    for target in ("marketplace", "agy", "dsh"):
        out_dir = tmp_path / f"bundle-{target}"
        builder.build(out_dir, target=target)
        assert builder.check(out_dir, target=target) == []
        for ref_rel in expected_refs:
            assert (out_dir / ref_rel).is_file(), f"Missing {ref_rel} in {target} bundle"
