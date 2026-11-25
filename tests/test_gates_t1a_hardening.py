"""Tests for T1A (#14 & #20): workflow gate hardening and anti-vacuous validator checks."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from autocode_mcp.runtime_store import AUDIT, set_section
from autocode_mcp.server import call_tool, register_all_tools
from autocode_mcp.tools.complexity import SolutionAnalyzeTool
from autocode_mcp.tools.file_ops import FileSaveTool
from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.interactor import InteractorBuildTool
from autocode_mcp.tools.problem import ProblemCreateTool
from autocode_mcp.tools.problem_pack import ProblemPackPolygonTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from autocode_mcp.tools.validator import ValidatorBuildTool
from autocode_mcp.workflow.enforcement import apply_result
from autocode_mcp.workflow.gates import _sync_dynamic_probe_hashes, preflight
from autocode_mcp.workflow.guard import check_gates, effective_require_wrong_solution_kill
from autocode_mcp.workflow.manifest import load_manifest, save_manifest
from autocode_mcp.workflow.models import AutoCodeManifest, SolutionEntry
from autocode_mcp.workflow.reconcile import inspect_workspace_state, reconcile_workspace_state
from autocode_mcp.workflow.state_io import load_workflow_state, save_workflow_state

CPP_REQUIRED = pytest.mark.skipif(not shutil.which("g++"), reason="g++ not available")

STATEMENT_MD = """# Sum 1..N

## 时间限制与空间限制

- 时间限制：1000ms
- 空间限制：256MB

## 题目描述

给定整数 $N$，求 $\\sum_{i=1}^{N} i$。
"""

TUTORIAL_MD = """# 题解

使用等差数列求和或线性累加，复杂度 $O(N)$。
"""

STD_LINEAR_CPP = """
// Time Complexity: O(N)
#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    long long ans = 0;
    for (long long i = 1; i <= n; ++i) ans += i;
    std::cout << ans << "\\n";
    return 0;
}
"""

BRUTE_QUADRATIC_CPP = """
// Time Complexity: O(N^2)
#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    long long ans = 0;
    for (long long i = 1; i <= n; ++i) {
        for (long long j = 1; j <= i; ++j) {
            ans += 1;
        }
    }
    std::cout << ans << "\\n";
    return 0;
}
"""

GEN_CPP = """
#include <cstdlib>
#include <cstring>
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    long long n = 200;
    for (int i = 1; i < argc; ++i) {
        if (std::strncmp(argv[i], "--n=", 4) == 0) {
            n = std::atoll(argv[i] + 4);
            break;
        }
        if (std::strcmp(argv[i], "-n") == 0 && i + 1 < argc) {
            n = std::atoll(argv[i + 1]);
            break;
        }
    }
    if (n == 200 && argc > 1) {
        long long candidate = std::atoll(argv[1]);
        if (candidate > 0) n = candidate;
    }
    if (n <= 0) n = 10;
    std::cout << n << "\\n";
    return 0;
}
"""

VAL_STRICT_CPP = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(1, 500, "n");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""

VACUOUS_VAL_CPP = """
int main() {
    return 0;
}
"""

RUN_TWICE_COMM_CPP = """
#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    const char* s = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = s ? std::atoi(s) : 2;
    int v = inf.readInt(0, 15, "v");
    if (stage == 1) {
        std::string msg = ouf.readToken();
        if (!ouf.seekEof()) quitf(_dirt, "extra");
        std::cout << msg << "\\n";
        quitf(_ok, "relayed bits_used=%d bytes_used=%d", (int)msg.size(), ((int)msg.size() + 7) / 8);
    }
    int decoded = ouf.readInt(0, 15, "decoded");
    if (!ouf.seekEof()) quitf(_dirt, "extra");
    if (decoded == v) quitf(_ok, "OK rounds=2");
    quitf(_wa, "mismatch");
}
"""

RUN_TWICE_SOL_CPP = """
#include <iostream>
#include <string>
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int v;
        std::cin >> v;
        std::cout << v << "\\n";
    } else {
        int v;
        std::cin >> v;
        std::cout << v << "\\n";
    }
    return 0;
}
"""

GRADER_HEADER_H = """
#pragma once
int encode_val(int x);
"""

GRADER_CPP = """
#include <iostream>
#include "problem.h"
int main() {
    int x = 0;
    if (!(std::cin >> x)) return 1;
    std::cout << encode_val(x) << "\\n";
    return 0;
}
"""

GRADER_SOL_CPP = """
#include "problem.h"
int encode_val(int x) {
    return x;
}
"""

CHECKER_CPP = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    long long o = ouf.readLong();
    long long a = ans.readLong();
    if (o != a) quitf(_wa, "expected %lld, got %lld", a, o);
    quitf(_ok, "ok");
}
"""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _run_tracked(problem_dir: str, tool, **kwargs):
    res = await tool.execute(problem_dir=problem_dir, **kwargs)
    apply_result(problem_dir, tool.name, {"problem_dir": problem_dir, **kwargs}, res.success, res.data)
    return res


def _decode_call_tool_payload(res) -> dict:
    assert res.content, "Expected CallToolResult content"
    return json.loads(res.content[0].text)


async def _setup_standard_problem(tmp_path: Path, name: str = "sum-n") -> str:
    problem_dir = str(tmp_path / name)
    create_res = await _run_tracked(
        problem_dir,
        ProblemCreateTool(),
        problem_name=name,
        problem_type="standard",
    )
    assert create_res.success, create_res.error

    manifest = load_manifest(problem_dir)
    assert manifest is not None
    manifest.constraints = {"n_max": 500}
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", path="solutions/sol.cpp", expected="pass"),
        SolutionEntry(name="brute", role="brute", path="solutions/brute.cpp", expected="pass"),
    ]
    save_manifest(problem_dir, manifest)

    save_tool = FileSaveTool()
    assert (await _run_tracked(problem_dir, save_tool, path="statements/README.md", content=STATEMENT_MD)).success
    assert (await _run_tracked(problem_dir, save_tool, path="statements/tutorial.md", content=TUTORIAL_MD)).success
    assert (await _run_tracked(problem_dir, SolutionBuildTool(), solution_type="sol", code=STD_LINEAR_CPP)).success
    assert (await _run_tracked(problem_dir, SolutionBuildTool(), solution_type="brute", code=BRUTE_QUADRATIC_CPP)).success
    return problem_dir


async def _advance_to_generator_built(problem_dir: str) -> None:
    assert (await _run_tracked(problem_dir, SolutionAnalyzeTool(), solution_type="sol")).success
    assert (await _run_tracked(problem_dir, SolutionAuditStdTool())).success
    assert (await _run_tracked(problem_dir, SolutionAuditBruteTool())).success
    val_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VAL_STRICT_CPP,
        test_cases=[
            {"input": "10\n", "expected_valid": True},
            {"input": "999\n", "expected_valid": False},
        ],
    )
    assert val_res.success, val_res.error
    gen_res = await _run_tracked(problem_dir, GeneratorBuildTool(), code=GEN_CPP)
    assert gen_res.success, gen_res.error


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req1_pre_generator_probe_pending_binaries_and_state(tmp_path: Path) -> None:
    """Pre-generator std/brute audits report pending_binaries without marking dynamic probes verified."""
    problem_dir = await _setup_standard_problem(tmp_path, "req1-pregen")

    assert (await _run_tracked(problem_dir, SolutionAnalyzeTool(), solution_type="sol")).success
    std_res = await _run_tracked(problem_dir, SolutionAuditStdTool())
    assert std_res.success, std_res.error
    assert std_res.data["dynamic_probe"]["ran"] is False
    assert std_res.data["dynamic_probe"]["status"] == "pending_binaries"

    brute_res = await _run_tracked(problem_dir, SolutionAuditBruteTool())
    assert brute_res.success, brute_res.error
    assert brute_res.data["brute_probe"]["status"] == "pending_binaries"
    assert brute_res.data["dynamic_probe"]["ran"] is False
    assert brute_res.data["dynamic_probe"]["status"] == "pending_binaries"

    st = load_workflow_state(problem_dir)
    assert st["std_audited"] is True
    assert st["brute_audited"] is True
    assert st["generator_built"] is False
    assert st["std_empirical_verified"] is False
    assert st["brute_probed"] is False

    pre_inspect = inspect_workspace_state(problem_dir)
    assert pre_inspect["dynamic_probes"]["std_empirical"]["status"] == "pending_binaries"
    assert pre_inspect["dynamic_probes"]["brute_probe"]["status"] == "pending_binaries"

    val_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VAL_STRICT_CPP,
        test_cases=[{"input": "5\n", "expected_valid": True}, {"input": "0\n", "expected_valid": False}],
    )
    assert val_res.success
    assert (await _run_tracked(problem_dir, GeneratorBuildTool(), code=GEN_CPP)).success

    post_gen_inspect = inspect_workspace_state(problem_dir)
    assert post_gen_inspect["dynamic_probes"]["std_empirical"]["status"] == "pending_run"
    assert post_gen_inspect["dynamic_probes"]["brute_probe"]["status"] == "pending_run"


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req1_post_generator_non_destructive_verification_and_mcp_server(tmp_path: Path) -> None:
    """Post-generator analyze/audit_std/audit_brute with unchanged SHA256 verify dynamically and preserve gates."""
    register_all_tools()
    problem_dir = await _setup_standard_problem(tmp_path, "req1-postgen")
    await _advance_to_generator_built(problem_dir)

    # Simulate downstream artifacts already verified prior to re-running dynamic probes.
    st = load_workflow_state(problem_dir)
    st["checker_ready"] = True
    st["interactor_ready"] = True
    st["stress_passed"] = True
    save_workflow_state(problem_dir, st)

    sol_hash = _sha256_file(Path(problem_dir) / "solutions" / "sol.cpp")
    brute_hash = _sha256_file(Path(problem_dir) / "solutions" / "brute.cpp")

    analyze_res = _decode_call_tool_payload(
        await call_tool("solution_analyze", {"problem_dir": problem_dir, "solution_type": "sol"})
    )
    assert analyze_res["success"] is True, analyze_res
    assert analyze_res["data"]["empirical_verification"]["passed"] is True

    st_after_analyze = load_workflow_state(problem_dir)
    assert st_after_analyze["std_empirical_verified"] is True
    assert st_after_analyze["std_empirical_hash"] == sol_hash
    for preserved_key in (
        "std_audited",
        "brute_audited",
        "validator_ready",
        "generator_built",
        "checker_ready",
        "interactor_ready",
        "stress_passed",
    ):
        assert st_after_analyze[preserved_key] is True, f"{preserved_key} was unexpectedly reset"

    audit_std_res = _decode_call_tool_payload(
        await call_tool("solution_audit_std", {"problem_dir": problem_dir})
    )
    assert audit_std_res["success"] is True, audit_std_res
    assert audit_std_res["data"]["dynamic_probe"]["ran"] is True
    assert audit_std_res["data"]["dynamic_probe"]["passed"] is True

    audit_brute_res = _decode_call_tool_payload(
        await call_tool("solution_audit_brute", {"problem_dir": problem_dir})
    )
    assert audit_brute_res["success"] is True, audit_brute_res
    assert audit_brute_res["data"]["brute_probe"]["status"] == "ok"
    assert audit_brute_res["data"]["dynamic_probe"]["ran"] is True
    assert audit_brute_res["data"]["dynamic_probe"]["status"] == "ok"

    st_final = load_workflow_state(problem_dir)
    assert st_final["std_empirical_verified"] is True
    assert st_final["std_empirical_hash"] == sol_hash
    assert st_final["brute_probed"] is True
    assert st_final["brute_probed_hash"] == brute_hash
    for preserved_key in (
        "std_audited",
        "brute_audited",
        "validator_ready",
        "generator_built",
        "checker_ready",
        "interactor_ready",
        "stress_passed",
    ):
        assert st_final[preserved_key] is True, f"{preserved_key} was unexpectedly reset"

    summary = inspect_workspace_state(problem_dir)
    assert summary["dynamic_probes"]["std_empirical"]["status"] == "verified"
    assert summary["dynamic_probes"]["brute_probe"]["status"] == "verified"


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req1_post_generator_brute_timeout_circuit_breaker_counts_as_probed(
    tmp_path: Path,
) -> None:
    """timeout_circuit_breaker confirms brute works at min scale and times out at scale, setting brute_probed."""
    problem_dir = await _setup_standard_problem(tmp_path, "req1-cb")
    await _advance_to_generator_built(problem_dir)

    st = load_workflow_state(problem_dir)
    st["stress_passed"] = True
    save_workflow_state(problem_dir, st)

    brute_hash = _sha256_file(Path(problem_dir) / "solutions" / "brute.cpp")
    apply_result(
        problem_dir,
        "solution_audit_brute",
        {"problem_dir": problem_dir},
        True,
        {
            "brute_probe": {"status": "timeout_circuit_breaker", "max_completed_n": 100},
            "dynamic_probe": {"ran": True, "status": "timeout_circuit_breaker", "passed": True},
        },
    )

    st_after = load_workflow_state(problem_dir)
    assert st_after["brute_audited"] is True
    assert st_after["brute_probed"] is True
    assert st_after["brute_probed_hash"] == brute_hash
    assert st_after["stress_passed"] is True


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req1_post_generator_probe_failure_fails_closed(tmp_path: Path) -> None:
    """Failing post-generator dynamic probes fail closed instead of leaving stale passes."""
    problem_dir = await _setup_standard_problem(tmp_path, "req1-fail-closed")
    await _advance_to_generator_built(problem_dir)

    # First verify both probes cleanly, then mark stress_passed=True.
    assert (await _run_tracked(problem_dir, SolutionAuditStdTool())).success
    assert (await _run_tracked(problem_dir, SolutionAuditBruteTool())).success
    st = load_workflow_state(problem_dir)
    st["stress_passed"] = True
    save_workflow_state(problem_dir, st)
    assert st["std_empirical_verified"] is True
    assert st["brute_probed"] is True

    # 1. Empirical failure on unchanged sol.cpp fails closed.
    apply_result(
        problem_dir,
        "solution_audit_std",
        {"problem_dir": problem_dir},
        True,
        {
            "empirical_verification": {"passed": False, "verdict": "ratio_mismatch"},
            "dynamic_probe": {"ran": True, "status": "ratio_mismatch", "passed": False},
        },
    )
    st_std_fail = load_workflow_state(problem_dir)
    assert st_std_fail["std_audited"] is False
    assert st_std_fail["std_empirical_verified"] is False
    assert not st_std_fail["std_empirical_hash"]
    assert st_std_fail["stress_passed"] is False

    # Re-verify std and stress_passed, then trigger real min-scale brute crash failure.
    assert (await _run_tracked(problem_dir, SolutionAuditStdTool())).success
    st = load_workflow_state(problem_dir)
    st["stress_passed"] = True
    save_workflow_state(problem_dir, st)

    crashing_brute = """
#include <cstdlib>
int main() { std::abort(); }
"""
    # Overwrite brute binary via SolutionBuildTool, mark brute_audited=True, then run SolutionAuditBruteTool post-gen.
    assert (await _run_tracked(problem_dir, SolutionBuildTool(), solution_type="brute", code=crashing_brute)).success
    st = load_workflow_state(problem_dir)
    st["brute_audited"] = True
    st["stress_passed"] = True
    save_workflow_state(problem_dir, st)

    brute_res = await _run_tracked(problem_dir, SolutionAuditBruteTool())
    assert brute_res.success is True
    assert brute_res.data["dynamic_probe"]["ran"] is True
    assert brute_res.data["dynamic_probe"]["passed"] is False
    st_brute_fail = load_workflow_state(problem_dir)
    assert st_brute_fail["brute_audited"] is False
    assert st_brute_fail["brute_probed"] is False
    assert not st_brute_fail["brute_probed_hash"]
    assert st_brute_fail["stress_passed"] is False


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req1_hash_drift_invalidation_and_reconcile(tmp_path: Path) -> None:
    """Source edits and generator rebuilds invalidate dynamic probe flags; raw disk edits show stale and reconcile."""
    problem_dir = await _setup_standard_problem(tmp_path, "req1-drift")
    await _advance_to_generator_built(problem_dir)

    assert (await _run_tracked(problem_dir, SolutionAuditStdTool())).success
    assert (await _run_tracked(problem_dir, SolutionAuditBruteTool())).success

    # Out-of-band edit to sol.cpp is detected as stale by inspect_workspace_state and cleaned by reconcile.
    sol_path = Path(problem_dir) / "solutions" / "sol.cpp"
    sol_path.write_text(STD_LINEAR_CPP + "\n// edited out of band\n", encoding="utf-8")

    inspected = inspect_workspace_state(problem_dir)
    assert inspected["dynamic_probes"]["std_empirical"]["status"] == "stale"
    assert inspected["dynamic_probes"]["brute_probe"]["status"] == "verified"

    rec = reconcile_workspace_state(problem_dir)
    assert rec["reconciled"] is True
    assert any("std_empirical" in a for a in rec["actions"])
    st_after_rec = load_workflow_state(problem_dir)
    assert st_after_rec["std_empirical_verified"] is False
    assert not st_after_rec["std_empirical_hash"]
    assert st_after_rec["brute_probed"] is True

    # Out-of-band edit to brute.cpp also syncs cleanly via _sync_dynamic_probe_hashes.
    brute_path = Path(problem_dir) / "solutions" / "brute.cpp"
    brute_path.write_text(BRUTE_QUADRATIC_CPP + "\n// edited out of band\n", encoding="utf-8")
    st_sync = load_workflow_state(problem_dir)
    assert _sync_dynamic_probe_hashes(problem_dir, st_sync) is True
    assert st_sync["brute_probed"] is False
    assert not st_sync["brute_probed_hash"]

    # file_save on files/gen.cpp clears both dynamic probe states.
    st_sync["std_empirical_verified"] = True
    st_sync["std_empirical_hash"] = _sha256_file(sol_path)
    st_sync["brute_probed"] = True
    st_sync["brute_probed_hash"] = _sha256_file(brute_path)
    save_workflow_state(problem_dir, st_sync)

    assert (await _run_tracked(problem_dir, FileSaveTool(), path="files/gen.cpp", content=GEN_CPP + "\n// v2\n")).success
    st_gen = load_workflow_state(problem_dir)
    assert st_gen["std_empirical_verified"] is False
    assert not st_gen["std_empirical_hash"]
    assert st_gen["brute_probed"] is False
    assert not st_gen["brute_probed_hash"]


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req2_vacuous_validator_rejected_and_mixed_accepted(tmp_path: Path) -> None:
    """Positive-only or negative-only validators fail to set validator_ready even at accuracy 1.0."""
    problem_dir = await _setup_standard_problem(tmp_path, "req2-validator")
    assert (await _run_tracked(problem_dir, SolutionAnalyzeTool(), solution_type="sol")).success
    assert (await _run_tracked(problem_dir, SolutionAuditStdTool())).success
    assert (await _run_tracked(problem_dir, SolutionAuditBruteTool())).success

    # 1. Vacuous return 0; validator with positive-only tests -> accuracy 1.0 but blocked!
    vac_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VACUOUS_VAL_CPP,
        test_cases=[
            {"input": "10\n", "expected_valid": True},
            {"input": "42\n", "expected_valid": True},
        ],
    )
    assert vac_res.success is True
    assert vac_res.data["accuracy"] == 1.0
    assert vac_res.data["positive_count"] == 2
    assert vac_res.data["negative_count"] == 0
    assert vac_res.data["has_positive_cases"] is True
    assert vac_res.data["has_negative_cases"] is False
    assert vac_res.data["warnings"], "Expected warning for missing negative test cases"
    assert load_workflow_state(problem_dir)["validator_ready"] is False
    assert preflight("generator_build", problem_dir, {"problem_dir": problem_dir}), "Expected generator_build blocked"

    # 2. Negative-only test cases -> blocked!
    neg_only_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VAL_STRICT_CPP,
        test_cases=[
            {"input": "999\n", "expected_valid": False},
            {"input": "-1\n", "expected_valid": False},
        ],
    )
    assert neg_only_res.success is True
    assert neg_only_res.data["positive_count"] == 0
    assert neg_only_res.data["negative_count"] == 2
    assert neg_only_res.data["has_positive_cases"] is False
    assert neg_only_res.data["has_negative_cases"] is True
    assert neg_only_res.data["warnings"]
    assert load_workflow_state(problem_dir)["validator_ready"] is False

    # 3. Empty test_cases -> blocked!
    empty_res = await _run_tracked(problem_dir, ValidatorBuildTool(), code=VAL_STRICT_CPP, test_cases=[])
    assert empty_res.success is True
    assert empty_res.data["accuracy"] is None
    assert empty_res.data["has_positive_cases"] is False
    assert empty_res.data["has_negative_cases"] is False
    assert empty_res.data["warnings"]
    assert load_workflow_state(problem_dir)["validator_ready"] is False

    # 4. Both positive and negative cases with accuracy >= 0.9 -> validator_ready=True and generator_build unblocked!
    good_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VAL_STRICT_CPP,
        test_cases=[
            {"input": "10\n", "expected_valid": True},
            {"input": "999\n", "expected_valid": False},
        ],
    )
    assert good_res.success is True
    assert good_res.data["accuracy"] == 1.0
    assert good_res.data["positive_count"] == 1
    assert good_res.data["negative_count"] == 1
    assert good_res.data["has_positive_cases"] is True
    assert good_res.data["has_negative_cases"] is True
    assert good_res.data["warnings"] == []
    assert load_workflow_state(problem_dir)["validator_ready"] is True
    assert preflight("generator_build", problem_dir, {"problem_dir": problem_dir}) == []

    # 5. Legacy should_pass alias in test_cases works for mixed valid/invalid classification too.
    alias_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VAL_STRICT_CPP,
        test_cases=[
            {"input": "10\n", "should_pass": True},
            {"input": "999\n", "should_pass": False},
        ],
    )
    assert alias_res.success is True
    assert alias_res.data["positive_count"] == 1
    assert alias_res.data["negative_count"] == 1
    assert alias_res.data["has_positive_cases"] is True
    assert alias_res.data["has_negative_cases"] is True
    assert load_workflow_state(problem_dir)["validator_ready"] is True


def _seed_pack_ready_signals(
    problem_dir: str,
    *,
    include_audit: bool = True,
    tests_verified: bool = True,
    wrong_killed: bool = True,
) -> None:
    if include_audit:
        set_section(problem_dir, AUDIT, {"score": 95, "verdict": "PASS"})
    st = load_workflow_state(problem_dir)
    for key in (
        "statement_written",
        "std_analyzed",
        "std_audited",
        "brute_audited",
        "validator_ready",
        "generator_built",
        "checker_ready",
        "interactor_ready",
        "stress_passed",
    ):
        st[key] = True
    st["tests_verified"] = tests_verified
    st["wrong_solutions_killed"] = wrong_killed
    st["verify_signals"] = {
        "limit_semantics": {"executed": True, "passed": True},
        "validator_check": {"executed": True, "passed": True},
        "wrong_solution_kill": {"executed": wrong_killed, "passed": wrong_killed},
    }
    save_workflow_state(problem_dir, st)


@CPP_REQUIRED
@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_mode", ["run_twice", "grader_linked"])
async def test_req3_communication_pack_without_ans(tmp_path: Path, protocol_mode: str) -> None:
    """Communication problems pack & pass export verification cleanly with .in files only (no .ans)."""
    problem_dir = str(tmp_path / f"comm-{protocol_mode}")
    assert (
        await _run_tracked(
            problem_dir,
            ProblemCreateTool(),
            problem_name=f"comm-{protocol_mode}",
            problem_type="communication",
            protocol_mode=protocol_mode,
            oracle_mode="interactor_only",
        )
    ).success

    manifest = load_manifest(problem_dir)
    assert manifest is not None
    manifest.time_limit_ms = 1000
    manifest.memory_limit_mb = 256
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", path="solutions/sol.cpp", expected="pass")
    ]
    save_manifest(problem_dir, manifest)

    save_tool = FileSaveTool()
    assert (await _run_tracked(problem_dir, save_tool, path="statements/README.md", content=STATEMENT_MD)).success
    assert (await _run_tracked(problem_dir, save_tool, path="statements/tutorial.md", content=TUTORIAL_MD)).success

    if protocol_mode == "run_twice":
        assert (await _run_tracked(problem_dir, save_tool, path="solutions/sol.cpp", content=RUN_TWICE_SOL_CPP)).success
        assert (
            await _run_tracked(
                problem_dir,
                InteractorBuildTool(),
                code=RUN_TWICE_COMM_CPP,
            )
        ).success
    else:
        assert (await _run_tracked(problem_dir, save_tool, path="files/problem.h", content=GRADER_HEADER_H)).success
        assert (await _run_tracked(problem_dir, save_tool, path="files/grader.cpp", content=GRADER_CPP)).success
        assert (await _run_tracked(problem_dir, save_tool, path="solutions/sol.cpp", content=GRADER_SOL_CPP)).success

    assert (await _run_tracked(problem_dir, save_tool, path="files/val.cpp", content=VAL_STRICT_CPP)).success
    assert (await _run_tracked(problem_dir, save_tool, path="files/gen.cpp", content=GEN_CPP)).success

    tests_dir = Path(problem_dir) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "1.in").write_text("5\n", encoding="utf-8")
    (tests_dir / "2.in").write_text("12\n", encoding="utf-8")
    # Explicitly assert no .ans files exist!
    assert not list(tests_dir.glob("*.ans"))

    _seed_pack_ready_signals(problem_dir)

    pack_res = await ProblemPackPolygonTool().execute(
        problem_dir=problem_dir,
        export_formats=["polygon", "hydro", "qoj"],
    )
    assert pack_res.success is True, pack_res.error
    assert pack_res.data["export_verification"]["passed"] is True, pack_res.data["export_verification"]
    assert set(pack_res.data["exports"].keys()) >= {"polygon", "hydro", "qoj"}


@pytest.mark.asyncio
async def test_req3_classic_problem_still_requires_ans_on_pack(tmp_path: Path) -> None:
    """Standard problems still fail packaging if any test .in lacks a matching .ans file."""
    problem_dir = str(tmp_path / "classic-missing-ans")
    assert (
        await ProblemCreateTool().execute(
            problem_dir=problem_dir,
            problem_name="classic-missing-ans",
            problem_type="standard",
        )
    ).success

    save_tool = FileSaveTool()
    assert (await save_tool.execute(problem_dir=problem_dir, path="statements/README.md", content=STATEMENT_MD)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="statements/tutorial.md", content=TUTORIAL_MD)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="solutions/sol.cpp", content=STD_LINEAR_CPP)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="files/val.cpp", content=VAL_STRICT_CPP)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="files/gen.cpp", content=GEN_CPP)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="files/checker.cpp", content=CHECKER_CPP)).success

    tests_dir = Path(problem_dir) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "1.in").write_text("5\n", encoding="utf-8")
    _seed_pack_ready_signals(problem_dir)

    pack_res = await ProblemPackPolygonTool().execute(problem_dir=problem_dir)
    assert pack_res.success is False
    assert ".ans" in (pack_res.error or "").lower() or "answer" in (pack_res.error or "").lower()


@CPP_REQUIRED
@pytest.mark.asyncio
async def test_req3_undeclared_wrong_solutions_scan_patterns(tmp_path: Path) -> None:
    """problem_pack_polygon warns for all 11 undeclared wrong/wa/tle/mle/re/bf filename patterns."""
    problem_dir = await _setup_standard_problem(tmp_path, "req3-wrong-scan")
    manifest = load_manifest(problem_dir)
    assert manifest is not None
    manifest.solutions.append(
        SolutionEntry(
            name="wrong_declared",
            role="wrong",
            path="solutions/wrong_declared.cpp",
            expected="fail",
        )
    )
    save_manifest(problem_dir, manifest)

    save_tool = FileSaveTool()
    assert (await save_tool.execute(problem_dir=problem_dir, path="files/val.cpp", content=VAL_STRICT_CPP)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="files/gen.cpp", content=GEN_CPP)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="files/checker.cpp", content=CHECKER_CPP)).success
    assert (await save_tool.execute(problem_dir=problem_dir, path="solutions/wrong_declared.cpp", content="int main(){return 0;}\n")).success

    undeclared_names = [
        "wrong_edge.cpp",
        "wrong-heuristic.cpp",
        "wa_case1.cpp",
        "wa-offbyone.cpp",
        "tle_quad.cpp",
        "tle-exp.cpp",
        "mle_big.cpp",
        "mle-table.cpp",
        "re_oob.cpp",
        "re-div0.cpp",
        "bf.cpp",
    ]
    sol_dir = Path(problem_dir) / "solutions"
    for fname in undeclared_names:
        (sol_dir / fname).write_text("int main(){return 0;}\n", encoding="utf-8")
    (sol_dir / "helper.h").write_text("#pragma once\n", encoding="utf-8")
    (sol_dir / "wrong_dir").mkdir(exist_ok=True)
    (sol_dir / "wrong_dir" / "nested.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")

    tests_dir = Path(problem_dir) / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "1.in").write_text("5\n", encoding="utf-8")
    (tests_dir / "1.ans").write_text("15\n", encoding="utf-8")
    _seed_pack_ready_signals(problem_dir)

    pack_res = await ProblemPackPolygonTool().execute(problem_dir=problem_dir)
    assert pack_res.success is True, pack_res.error
    joined_warnings = "\n".join(pack_res.data.get("pack_warnings", []))
    for fname in undeclared_names:
        assert fname in joined_warnings, f"Expected undeclared wrong warning for {fname}"
    assert "wrong_declared.cpp" not in joined_warnings
    assert "helper.h" not in joined_warnings
    assert "wrong_dir" not in joined_warnings


def _write_raw_manifest(problem_dir: Path, quality_gates: dict, with_wrong: bool) -> AutoCodeManifest:
    solutions = [{"name": "sol", "role": "main", "path": "solutions/sol.cpp", "expected": "pass"}]
    if with_wrong:
        solutions.append({"name": "wrong_1", "role": "wrong", "path": "solutions/wrong_1.cpp", "expected": "fail"})
    payload = {
        "problem_name": problem_dir.name,
        "problem_type": "standard",
        "solutions": solutions,
        "quality_gates": quality_gates,
    }
    autocode_dir = problem_dir / ".autocode"
    autocode_dir.mkdir(parents=True, exist_ok=True)
    (autocode_dir / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    manifest = load_manifest(str(problem_dir))
    assert manifest is not None
    return manifest


def _prepare_pack_preflight_workspace(problem_dir: Path, *, with_wrong: bool, quality_gates: dict) -> AutoCodeManifest:
    problem_dir.mkdir(parents=True, exist_ok=True)
    (problem_dir / "statements").mkdir(exist_ok=True)
    (problem_dir / "solutions").mkdir(exist_ok=True)
    (problem_dir / "files").mkdir(exist_ok=True)
    (problem_dir / "tests").mkdir(exist_ok=True)
    (problem_dir / "statements" / "README.md").write_text(STATEMENT_MD, encoding="utf-8")
    (problem_dir / "statements" / "tutorial.md").write_text(TUTORIAL_MD, encoding="utf-8")
    (problem_dir / "solutions" / "sol.cpp").write_text(STD_LINEAR_CPP, encoding="utf-8")
    if with_wrong:
        (problem_dir / "solutions" / "wrong_1.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (problem_dir / "tests" / "1.in").write_text("5\n", encoding="utf-8")
    (problem_dir / "tests" / "1.ans").write_text("15\n", encoding="utf-8")
    manifest = _write_raw_manifest(problem_dir, quality_gates=quality_gates, with_wrong=with_wrong)
    _seed_pack_ready_signals(str(problem_dir), wrong_killed=False)
    return manifest


def test_req4_smart_default_require_wrong_solution_kill(tmp_path: Path) -> None:
    """Wrong solutions implicitly enable require_wrong_solution_kill unless explicitly set to False."""
    # Case 1: role="wrong" present + require_wrong_solution_kill omitted -> effectively True!
    dir_default_on = tmp_path / "smart-default-on"
    m_on = _prepare_pack_preflight_workspace(dir_default_on, with_wrong=True, quality_gates={})
    assert "require_wrong_solution_kill" not in m_on.quality_gates.model_fields_set
    assert effective_require_wrong_solution_kill(m_on) is True

    issues_on = check_gates(m_on, load_workflow_state(str(dir_default_on)), verify_signals={})
    assert any(i.gate == "wrong_solution_kill" for i in issues_on)

    violations_on = preflight("problem_pack_polygon", str(dir_default_on), {"problem_dir": str(dir_default_on)})
    assert any("wrong_solution_kill" in v.reason for v in violations_on)
    assert inspect_workspace_state(str(dir_default_on))["gate_summary"]["effective_require_wrong_solution_kill"] is True

    # Case 2: role="wrong" present + explicit require_wrong_solution_kill=False -> opted out!
    dir_opt_out = tmp_path / "smart-opt-out"
    m_off = _prepare_pack_preflight_workspace(
        dir_opt_out,
        with_wrong=True,
        quality_gates={"require_wrong_solution_kill": False},
    )
    assert "require_wrong_solution_kill" in m_off.quality_gates.model_fields_set
    assert effective_require_wrong_solution_kill(m_off) is False

    issues_off = check_gates(m_off, load_workflow_state(str(dir_opt_out)), verify_signals={})
    assert not any(i.gate == "wrong_solution_kill" for i in issues_off)
    violations_off = preflight("problem_pack_polygon", str(dir_opt_out), {"problem_dir": str(dir_opt_out)})
    assert not any("wrong_solution_kill" in v.reason for v in violations_off)
    assert inspect_workspace_state(str(dir_opt_out))["gate_summary"]["effective_require_wrong_solution_kill"] is False

    # Case 3: No role="wrong" solution + omitted flag -> remains False!
    dir_no_wrong = tmp_path / "smart-no-wrong"
    m_none = _prepare_pack_preflight_workspace(dir_no_wrong, with_wrong=False, quality_gates={})
    assert "require_wrong_solution_kill" not in m_none.quality_gates.model_fields_set
    assert effective_require_wrong_solution_kill(m_none) is False
    issues_none = check_gates(m_none, load_workflow_state(str(dir_no_wrong)), verify_signals={})
    assert not any(i.gate == "wrong_solution_kill" for i in issues_none)
    violations_none = preflight("problem_pack_polygon", str(dir_no_wrong), {"problem_dir": str(dir_no_wrong)})
    assert not any("wrong_solution_kill" in v.reason for v in violations_none)
