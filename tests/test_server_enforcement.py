"""MCP-server workflow enforcement tests independent of host hooks."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from autocode_mcp.tools.base import ToolResult
from autocode_mcp.workflow import (
    default_manifest,
    load_workflow_state,
    save_manifest,
    save_workflow_state,
)
from autocode_mcp.workflow.enforcement import _min_limit_ratio_gate_ok


def managed_problem(tmp_path) -> str:
    problem_dir = tmp_path / "problem"
    for name in ("files", "solutions", "statements", "tests"):
        (problem_dir / name).mkdir(parents=True)
    save_manifest(str(problem_dir), default_manifest("Managed problem"))
    return str(problem_dir)


def update_state(problem_dir: str, **updates: Any) -> None:
    state = load_workflow_state(problem_dir)
    state.update(updates)
    save_workflow_state(problem_dir, state)


@pytest.mark.asyncio
async def test_server_blocks_direct_late_call_without_hooks(tmp_path):
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()
    problem_dir = managed_problem(tmp_path)

    result = await call_tool("problem_pack_polygon", {"problem_dir": problem_dir})

    assert result.isError is True
    assert result.structuredContent is not None
    data = result.structuredContent["data"]
    assert data["gate_blocked"] is True
    assert any(item["gate"] == "tests_generated" for item in data["blocking_issues"])
    assert data["next_actions"]


@pytest.mark.asyncio
async def test_server_blocks_runtime_without_manifest(tmp_path):
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    save_workflow_state(
        str(problem_dir),
        {"problem_dir": str(problem_dir), "created": True, "tests_generated": True},
    )

    result = await call_tool("problem_verify_tests", {"problem_dir": str(problem_dir)})

    assert result.isError is True
    assert result.structuredContent["data"]["blocking_issues"][0]["gate"] == "manifest"


@pytest.mark.asyncio
async def test_server_allows_ordered_workflow_after_all_gates(tmp_path, monkeypatch):
    import autocode_mcp.server as server

    server.register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        created=True,
        tests_generated=True,
        generated_test_count=2,
        tests_verified=True,
        limit_case_ratio=0.5,
        verify_signals={
            "limit_semantics": {"executed": True, "passed": True},
            "validator_check": {"executed": True, "passed": True},
        },
    )
    calls: list[dict[str, Any]] = []

    async def fake_execute(**kwargs: Any) -> ToolResult:
        calls.append(kwargs)
        return ToolResult.ok(problem_xml="problem.xml")

    monkeypatch.setitem(server.TOOLS, "problem_pack_polygon", SimpleNamespace(execute=fake_execute))

    result = await server.call_tool("problem_pack_polygon", {"problem_dir": problem_dir})

    assert result.isError is False
    assert result.structuredContent["success"] is True
    assert calls == [{"problem_dir": problem_dir}]
    assert load_workflow_state(problem_dir)["packaged"] is True


@pytest.mark.asyncio
async def test_server_invalidates_downstream_results_after_solution_rebuild(tmp_path, monkeypatch):
    import autocode_mcp.server as server

    server.register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        created=True,
        sol_built=True,
        brute_built=True,
        solution_analyzed=True,
        std_audited=True,
        brute_audited=True,
        validator_ready=True,
        validator_accuracy=1.0,
        generator_built=True,
        stress_passed=True,
        stress_completed_rounds=10,
        stress_total_rounds=10,
        tests_generated=True,
        generated_test_count=2,
        tests_verified=True,
        limit_case_ratio=0.5,
        verify_signals={
            "limit_semantics": {"executed": True, "passed": True},
            "validator_check": {"executed": True, "passed": True},
        },
    )

    async def rebuilt(**_: Any) -> ToolResult:
        return ToolResult.ok(binary_path="solutions/sol")

    monkeypatch.setitem(server.TOOLS, "solution_build", SimpleNamespace(execute=rebuilt))
    result = await server.call_tool(
        "solution_build", {"problem_dir": problem_dir, "solution_type": "sol"}
    )

    assert result.isError is False
    state = load_workflow_state(problem_dir)
    assert state["solution_analyzed"] is False
    assert state["stress_passed"] is False
    assert state["tests_generated"] is False
    assert state["tests_verified"] is False
    assert state["verify_signals"] == {}

    package_result = await server.call_tool("problem_pack_polygon", {"problem_dir": problem_dir})
    assert package_result.isError is True
    assert any(
        item["gate"] == "tests_generated"
        for item in package_result.structuredContent["data"]["blocking_issues"]
    )


@pytest.mark.asyncio
async def test_server_clears_solution_state_after_failed_rebuild(tmp_path, monkeypatch):
    import autocode_mcp.server as server

    server.register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        created=True,
        sol_built=True,
        brute_built=True,
        solution_analyzed=True,
        std_audited=True,
        brute_audited=True,
        tests_generated=True,
        generated_test_count=2,
        tests_verified=True,
    )

    async def failed(**_: Any) -> ToolResult:
        return ToolResult.fail("compile failed")

    monkeypatch.setitem(server.TOOLS, "solution_build", SimpleNamespace(execute=failed))
    result = await server.call_tool(
        "solution_build", {"problem_dir": problem_dir, "solution_type": "sol"}
    )

    assert result.isError is True
    state = load_workflow_state(problem_dir)
    assert state["sol_built"] is False
    assert state["solution_analyzed"] is False
    assert state["tests_generated"] is False
    assert state["tests_verified"] is False


@pytest.mark.asyncio
async def test_server_invalidates_downstream_results_after_source_file_save(tmp_path):
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        created=True,
        sol_built=True,
        brute_built=True,
        solution_analyzed=True,
        std_audited=True,
        brute_audited=True,
        validator_ready=True,
        validator_accuracy=1.0,
        generator_built=True,
        stress_passed=True,
        stress_completed_rounds=10,
        stress_total_rounds=10,
        tests_generated=True,
        generated_test_count=2,
        tests_verified=True,
    )

    result = await call_tool(
        "file_save",
        {
            "problem_dir": problem_dir,
            "path": "solutions/sol.cpp",
            "content": "int main() {}\n",
        },
    )

    assert result.isError is False
    state = load_workflow_state(problem_dir)
    assert state["sol_built"] is False
    assert state["stress_passed"] is False
    assert state["tests_verified"] is False


@pytest.mark.asyncio
async def test_server_persists_verify_result_and_cancellation(tmp_path, monkeypatch):
    import autocode_mcp.server as server

    server.register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(problem_dir, tests_generated=True, generated_test_count=2)

    async def fake_verify(**_: Any) -> ToolResult:
        return ToolResult.ok(
            passed=True,
            results={"limit_ratio": {"limit_case_ratio": 0.75}},
            quality_signals={
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
            },
        )

    monkeypatch.setitem(server.TOOLS, "problem_verify_tests", SimpleNamespace(execute=fake_verify))
    result = await server.call_tool("problem_verify_tests", {"problem_dir": problem_dir})
    assert result.isError is False
    state = load_workflow_state(problem_dir)
    assert state["tests_verified"] is True
    assert state["limit_case_ratio"] == 0.75

    update_state(
        problem_dir,
        sol_built=True,
        brute_built=True,
        solution_analyzed=True,
        std_audited=True,
        brute_audited=True,
        validator_ready=True,
        validator_accuracy=1.0,
        generator_built=True,
        stress_completed_rounds=3,
        stress_total_rounds=4,
        stress_passed=True,
    )

    async def cancelled(**_: Any) -> ToolResult:
        raise asyncio.CancelledError

    monkeypatch.setitem(server.TOOLS, "stress_test_run", SimpleNamespace(execute=cancelled))
    cancelled_result = await server.call_tool("stress_test_run", {"problem_dir": problem_dir})
    assert cancelled_result.isError is True
    assert cancelled_result.structuredContent["data"]["interrupted"] is True
    state = load_workflow_state(problem_dir)
    assert state["stress_passed"] is False
    assert state["stress_completed_rounds"] == 0
    assert state["stress_total_rounds"] == 0


@pytest.mark.asyncio
async def test_server_resets_state_after_exception(tmp_path, monkeypatch):
    import autocode_mcp.server as server

    server.register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        sol_built=True,
        brute_built=True,
        solution_analyzed=True,
        std_audited=True,
        brute_audited=True,
        validator_ready=True,
        validator_accuracy=1.0,
    )

    async def failed(**_: Any) -> ToolResult:
        raise RuntimeError("compiler crashed")

    monkeypatch.setitem(server.TOOLS, "validator_build", SimpleNamespace(execute=failed))
    result = await server.call_tool("validator_build", {"problem_dir": problem_dir})

    assert result.isError is True
    assert result.structuredContent["data"]["exc_type"] == "RuntimeError"
    state = load_workflow_state(problem_dir)
    assert state["validator_ready"] is False
    assert state["validator_accuracy"] is None


@pytest.mark.parametrize("ratio", [True, -0.1, 1.1, float("nan"), float("inf"), "0.5"])
def test_limit_ratio_gate_rejects_invalid_runtime_values(ratio):
    state = {"quality_gates": {"min_limit_case_ratio": 0.5}, "limit_case_ratio": ratio}
    assert _min_limit_ratio_gate_ok(state, {}) is False


def test_apply_result_problem_build_all(tmp_path):
    from autocode_mcp.workflow.enforcement import apply_result

    problem_dir = managed_problem(tmp_path)
    update_state(problem_dir, created=True)
    data = {
        "compiled": {
            "solutions/sol.cpp": "/bin/sol",
            "solutions/brute.cpp": "/bin/brute",
            "files/gen.cpp": "/bin/gen",
        }
    }
    state = apply_result(problem_dir, "problem_build_all", {"problem_dir": problem_dir}, True, data)
    assert state["sol_built"] is True
    assert state["brute_built"] is True
    assert state["generator_built"] is True


@pytest.mark.asyncio
async def test_file_save_granular_invalidation_for_docs_and_tests(tmp_path):
    """修改 tutorial/README 不应清空已通过的对拍，且 tutorial 不应清空终测验证。"""
    from autocode_mcp.server import call_tool, register_all_tools

    register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        created=True,
        sol_built=True,
        brute_built=True,
        stress_passed=True,
        statement_validated=True,
        sample_files_validated=True,
        validation_passed=True,
        tests_generated=True,
        generated_test_count=2,
        tests_verified=True,
        packaged=True,
    )

    res_tut = await call_tool(
        "file_save",
        {"problem_dir": problem_dir, "path": "statements/tutorial.md", "content": "# Updated\n"},
    )
    assert res_tut.isError is False
    state = load_workflow_state(problem_dir)
    assert state["stress_passed"] is True
    assert state["tests_verified"] is True
    assert state["validation_passed"] is True
    assert state["packaged"] is False

    res_readme = await call_tool(
        "file_save",
        {"problem_dir": problem_dir, "path": "statements/README.md", "content": "# Updated\n"},
    )
    assert res_readme.isError is False
    state = load_workflow_state(problem_dir)
    assert state["stress_passed"] is True
    assert state["statement_validated"] is False
    assert state["validation_passed"] is False
    assert state["statement_dirty"] is True

    # 编辑题面后未重新 problem_validate 必须阻断打包
    pack_blocked = await call_tool("problem_pack_polygon", {"problem_dir": problem_dir})
    assert pack_blocked.isError is True
    assert any(
        item["gate"] == "statement_fresh"
        for item in pack_blocked.structuredContent["data"]["blocking_issues"]
    )

    # 重新 problem_validate 通过后应解除 statement_dirty 且保留 stress_passed/tests_verified
    from autocode_mcp.workflow.enforcement import apply_result

    state = apply_result(
        problem_dir,
        "problem_validate",
        {"problem_dir": problem_dir},
        True,
        {"statement_samples": {"validated": True}, "sample_files": {"validated": True}},
    )
    assert state["statement_dirty"] is False
    assert state["stress_passed"] is True
    assert state["tests_verified"] is True

    # 修改 tests/ 文件应失效 tests_verified 但保留 stress_passed
    res_tests = await call_tool(
        "file_save",
        {"problem_dir": problem_dir, "path": "tests/01.in", "content": "1\n"},
    )
    assert res_tests.isError is False
    state = load_workflow_state(problem_dir)
    assert state["stress_passed"] is True
    assert state["tests_verified"] is False

    # 含 .. 的相对路径不能绕过源文件失效
    update_state(problem_dir, sol_built=True, stress_passed=True)
    res_dotdot = await call_tool(
        "file_save",
        {"problem_dir": problem_dir, "path": "files/../solutions/sol.cpp", "content": "int main(){}\n"},
    )
    assert res_dotdot.isError is False
    state = load_workflow_state(problem_dir)
    assert state["sol_built"] is False
    assert state["stress_passed"] is False


def test_apply_result_persists_std_claimed_complexity_and_isolates_brute(tmp_path):
    from autocode_mcp.workflow.enforcement import apply_result

    problem_dir = managed_problem(tmp_path)
    state = apply_result(
        problem_dir,
        "solution_analyze",
        {"problem_dir": problem_dir, "solution_type": "sol", "claimed_complexity": "O(n log n)"},
        True,
        {"estimated_complexity": "O(n log n)", "claimed_complexity": "O(n log n)"},
    )
    assert state["std_claimed_complexity"] == "O(n log n)"

    # brute 分析不能污染 std_claimed_complexity
    state = apply_result(
        problem_dir,
        "solution_analyze",
        {"problem_dir": problem_dir, "solution_type": "brute", "claimed_complexity": "O(n^2)"},
        True,
        {"estimated_complexity": "O(n^2)", "claimed_complexity": "O(n^2)"},
    )
    assert state["std_claimed_complexity"] == "O(n log n)"

    # 重建 sol 必须清空旧的 std_claimed_complexity
    state = apply_result(
        problem_dir,
        "solution_build",
        {"problem_dir": problem_dir, "solution_type": "sol"},
        True,
        {"binary_path": "solutions/sol"},
    )
    assert state["std_claimed_complexity"] is None


@pytest.mark.asyncio
async def test_oracle_mode_interactor_only_and_optional_interactive_validator(tmp_path):
    """oracle_mode=interactor_only 免除 brute 门禁，且交互/通信题允许构建可选 validator。"""
    from autocode_mcp.workflow.enforcement import preflight

    problem_dir = tmp_path / "inter_p"
    for name in ("files", "solutions", "statements", "tests"):
        (problem_dir / name).mkdir(parents=True)
    save_manifest(
        str(problem_dir),
        default_manifest("Interactive No Brute", problem_type="interactive", oracle_mode="interactor_only"),
    )
    update_state(
        str(problem_dir),
        created=True,
        sol_built=True,
        solution_analyzed=True,
        std_audited=True,
    )

    # Interactive problem CAN build optional validator without brute
    assert preflight("validator_build", str(problem_dir), {"problem_dir": str(problem_dir)}) == []
    # Interactive problem CAN build interactor without brute
    assert preflight("interactor_build", str(problem_dir), {"problem_dir": str(problem_dir)}) == []
    # Interactive problem still cannot build checker
    chk_violations = preflight("checker_build", str(problem_dir), {"problem_dir": str(problem_dir)})
    assert any(v.gate == "non_interactive" for v in chk_violations)

    # Before interactor_ready, generator_build is blocked
    gen_blocked = preflight("generator_build", str(problem_dir), {"problem_dir": str(problem_dir)})
    assert any(v.gate == "interactor" for v in gen_blocked)

    update_state(str(problem_dir), interactor_ready=True)
    assert preflight("generator_build", str(problem_dir), {"problem_dir": str(problem_dir)}) == []


@pytest.mark.asyncio
async def test_communication_protocol_modes_enforcement(tmp_path):
    """run_twice 通信题允许并要求 interactor_build；grader_linked 跳过 interactor_build。"""
    from autocode_mcp.workflow.enforcement import apply_result, preflight

    # 1. run_twice
    comm_dir = tmp_path / "comm_rt"
    for name in ("files", "solutions", "statements", "tests"):
        (comm_dir / name).mkdir(parents=True)
    save_manifest(
        str(comm_dir),
        default_manifest(
            "Comm RunTwice",
            problem_type="communication",
            protocol_mode="run_twice",
            oracle_mode="interactor_only",
        ),
    )
    update_state(str(comm_dir), created=True, sol_built=True, solution_analyzed=True, std_audited=True)
    assert preflight("interactor_build", str(comm_dir), {"problem_dir": str(comm_dir)}) == []
    assert any(v.gate == "interactor" for v in preflight("generator_build", str(comm_dir), {"problem_dir": str(comm_dir)}))
    update_state(str(comm_dir), interactor_ready=True)
    assert preflight("generator_build", str(comm_dir), {"problem_dir": str(comm_dir)}) == []

    # Saving files/communicator.cpp invalidates interactor_ready
    state = apply_result(
        str(comm_dir),
        "file_save",
        {"problem_dir": str(comm_dir), "path": "files/communicator.cpp"},
        True,
        {},
    )
    assert state["interactor_ready"] is False

    # 2. grader_linked
    grader_dir = tmp_path / "comm_grader"
    for name in ("files", "solutions", "statements", "tests"):
        (grader_dir / name).mkdir(parents=True)
    save_manifest(
        str(grader_dir),
        default_manifest(
            "Comm Grader",
            problem_type="communication",
            protocol_mode="grader_linked",
            oracle_mode="interactor_only",
        ),
    )
    update_state(str(grader_dir), created=True, sol_built=True, solution_analyzed=True, std_audited=True)
    assert any(v.gate == "interactive" for v in preflight("interactor_build", str(grader_dir), {"problem_dir": str(grader_dir)}))
    assert preflight("generator_build", str(grader_dir), {"problem_dir": str(grader_dir)}) == []

    # Saving files/grader.cpp invalidates sol_built and std_claimed_complexity
    update_state(str(grader_dir), std_claimed_complexity="O(n)")
    state = apply_result(
        str(grader_dir),
        "file_save",
        {"problem_dir": str(grader_dir), "path": "files/grader.cpp"},
        True,
        {},
    )
    assert state["sol_built"] is False
    assert state["std_claimed_complexity"] is None


@pytest.mark.asyncio
async def test_enforcement_fail_closed_oracle_mode_custom_protocol_and_sparse_create(tmp_path):
    """验证 _brute_required fail-closed、file_save 规范别名对齐、自定义协议文件失效、及稀疏 problem_create 保留 protocol_mode。"""
    from autocode_mcp.server import call_tool, register_all_tools
    from autocode_mcp.workflow.enforcement import _brute_required, apply_result

    register_all_tools()

    # _brute_required must fail closed for missing/None/unknown values
    assert _brute_required({}) is True
    assert _brute_required({"oracle_mode": None}) is True
    assert _brute_required({"oracle_mode": ""}) is True
    assert _brute_required({"oracle_mode": "invalid_mode"}) is True
    assert _brute_required({"oracle_mode": "interactor_only"}) is False
    assert _brute_required({"oracle_mode": "reference_solutions"}) is False

    comm_dir = tmp_path / "custom_comm"
    for name in ("files", "solutions", "statements", "tests"):
        (comm_dir / name).mkdir(parents=True)
    manifest = default_manifest(
        "Custom Comm",
        problem_type="communication",
        protocol_mode="run_twice",
        oracle_mode="interactor_only",
    )
    assert manifest.protocol is not None
    manifest.protocol.communicator_file = "files/custom_manager.cpp"
    save_manifest(str(comm_dir), manifest)

    # Sparse apply_result("problem_create", data={}) must preserve manifest-synced protocol_mode
    state = apply_result(
        str(comm_dir),
        "problem_create",
        {"problem_dir": str(comm_dir), "problem_type": "communication"},
        True,
        {},
    )
    assert state["protocol_mode"] == "run_twice"

    # Bare filename alias communicator.cpp must canonicalize into files/communicator.cpp via FileSaveTool
    res_alias = await call_tool(
        "file_save",
        {"problem_dir": str(comm_dir), "path": "communicator.cpp", "content": "// comm\n"},
    )
    assert res_alias.isError is False
    assert (comm_dir / "files" / "communicator.cpp").is_file()

    # Custom manifest communicator_file edit must invalidate interactor_ready
    update_state(str(comm_dir), interactor_ready=True, interaction_scenarios={"validated": True})
    res_custom = await call_tool(
        "file_save",
        {"problem_dir": str(comm_dir), "path": "files/custom_manager.cpp", "content": "// updated\n"},
    )
    assert res_custom.isError is False
    st = load_workflow_state(str(comm_dir))
    assert st["interactor_ready"] is False
    assert st["interaction_scenarios"] == {}

@pytest.mark.asyncio
async def test_solution_build_wrong_reference_preserves_stress_validation_and_generated_tests(
    tmp_path, monkeypatch
):
    """构建 wrong/reference 解不得清空 stress_passed/validation_passed/tests_generated。"""
    import autocode_mcp.server as server

    server.register_all_tools()
    problem_dir = managed_problem(tmp_path)
    update_state(
        problem_dir,
        created=True,
        sol_built=True,
        solution_analyzed=True,
        std_audited=True,
        brute_built=True,
        brute_audited=True,
        validator_ready=True,
        validator_accuracy=1.0,
        generator_built=True,
        stress_passed=True,
        stress_completed_rounds=100,
        stress_total_rounds=100,
        validation_passed=True,
        statement_validated=True,
        sample_files_validated=True,
        tests_generated=True,
        generated_test_count=20,
        tests_verified=True,
        packaged=True,
    )

    async def fake_build(**kwargs: Any) -> ToolResult:
        return ToolResult.ok(effective_name=kwargs.get("name", "wa1"))

    monkeypatch.setitem(server.TOOLS, "solution_build", SimpleNamespace(execute=fake_build))
    res = await server.call_tool(
        "solution_build",
        {
            "problem_dir": problem_dir,
            "solution_type": "wrong",
            "name": "wa1",
            "code": "int main(){return 0;}",
        },
    )
    assert res.isError is False
    after = load_workflow_state(problem_dir)
    assert after["stress_passed"] is True
    assert after["validation_passed"] is True
    assert after["tests_generated"] is True
    assert after["generated_test_count"] == 20
    assert after["tests_verified"] is False
    assert after["packaged"] is False


@pytest.mark.asyncio
async def test_sol_rebuild_preserves_brute_audit_gate_by_file_hash(tmp_path):
    """仅修改/重编 sol.cpp 时，仅失效 sol 侧门禁，不应连带重置未改动 brute.cpp 的 solution_audit_brute。"""
    from autocode_mcp.workflow.enforcement import apply_result, preflight

    problem_dir = managed_problem(tmp_path)
    p = tmp_path / "problem"
    sol_cpp = p / "solutions" / "sol.cpp"
    brute_cpp = p / "solutions" / "brute.cpp"
    sol_cpp.write_text("int main() { return 0; }\n", encoding="utf-8")
    brute_cpp.write_text("int main() { return 0; }\n", encoding="utf-8")

    # 1. 完成初始构建与审计
    update_state(problem_dir, created=True)
    apply_result(problem_dir, "solution_build", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {})
    apply_result(problem_dir, "solution_build", {"problem_dir": problem_dir, "solution_type": "brute"}, True, {})
    apply_result(problem_dir, "solution_analyze", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {"estimated_complexity": "O(n)"})
    apply_result(problem_dir, "solution_audit_std", {"problem_dir": problem_dir}, True, {})
    apply_result(problem_dir, "solution_audit_brute", {"problem_dir": problem_dir}, True, {})

    st = load_workflow_state(problem_dir)
    assert st["std_audited"] is True
    assert st["brute_audited"] is True
    assert st["std_audited_hash"]
    assert st["brute_audited_hash"]

    # 2. 仅修改并重编 sol.cpp（不改动 brute.cpp）
    sol_cpp.write_text("int main() { int x = 1; return x - 1; }\n", encoding="utf-8")
    apply_result(problem_dir, "file_save", {"problem_dir": problem_dir, "path": "solutions/sol.cpp"}, True, {})
    st_saved = load_workflow_state(problem_dir)
    assert st_saved["sol_built"] is False
    assert st_saved["std_audited"] is False
    assert st_saved["brute_built"] is True
    assert st_saved["brute_audited"] is True

    apply_result(problem_dir, "solution_build", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {})
    apply_result(problem_dir, "solution_analyze", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {"estimated_complexity": "O(n)"})
    apply_result(problem_dir, "solution_audit_std", {"problem_dir": problem_dir}, True, {})

    st_after = load_workflow_state(problem_dir)
    assert st_after["std_audited"] is True
    assert st_after["brute_audited"] is True
    # 无需重跑 solution_audit_brute，validator_build 直接放行
    assert preflight("validator_build", problem_dir, {"problem_dir": problem_dir}) == []

    # 3. 外部改动 brute.cpp 后（文件 hash 漂移），自动失效 brute_audited 并阻断 validator_build
    brute_cpp.write_text("int main() { return 42; }\n", encoding="utf-8")
    st_drift = load_workflow_state(problem_dir)
    assert st_drift["brute_audited"] is False
    violations = preflight("validator_build", problem_dir, {"problem_dir": problem_dir})
    assert any(v.gate == "audits" for v in violations)
def test_enforcement_hash_drift_failed_audit_inline_spoof_and_build_all_granularity(tmp_path):
    """回归：外部改动 sol.cpp 失效构建与下游、audit(passed=False)/异源 code 不可放行、build_all 失败清门禁且仅辅助解保留对拍。"""
    from autocode_mcp.workflow.enforcement import apply_result, preflight

    problem_dir = managed_problem(tmp_path)
    p = tmp_path / "problem"
    sol_cpp = p / "solutions" / "sol.cpp"
    brute_cpp = p / "solutions" / "brute.cpp"
    other_cpp = p / "solutions" / "other.cpp"
    sol_cpp.write_text("int main() { return 0; }\n", encoding="utf-8")
    brute_cpp.write_text("int main() { return 0; }\n", encoding="utf-8")
    other_cpp.write_text("int main() { return 1; }\n", encoding="utf-8")

    update_state(problem_dir, created=True)
    apply_result(problem_dir, "solution_build", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {})
    apply_result(problem_dir, "solution_build", {"problem_dir": problem_dir, "solution_type": "brute"}, True, {})
    apply_result(problem_dir, "solution_analyze", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {"estimated_complexity": "O(n)"})

    # 1. solution_audit_std returning passed=False (despite ToolResult.ok) must NOT mark std_audited=True
    st_fail_audit = apply_result(
        problem_dir,
        "solution_audit_std",
        {"problem_dir": problem_dir},
        True,
        {"passed": False},
    )
    assert st_fail_audit["std_audited"] is False
    assert st_fail_audit["std_audited_hash"] is None

    # 2. Inline mismatched code or non-canonical source_path must NOT mark std_audited=True
    st_spoof_code = apply_result(
        problem_dir,
        "solution_audit_std",
        {"problem_dir": problem_dir, "code": "int main() { return 999; }\n"},
        True,
        {"passed": True},
    )
    assert st_spoof_code["std_audited"] is False

    st_spoof_path = apply_result(
        problem_dir,
        "solution_audit_std",
        {"problem_dir": problem_dir, "source_path": "solutions/other.cpp"},
        True,
        {"passed": True},
    )
    assert st_spoof_path["std_audited"] is False

    # Canonical audit passes
    st_ok = apply_result(
        problem_dir,
        "solution_audit_std",
        {"problem_dir": problem_dir},
        True,
        {"passed": True},
    )
    assert st_ok["std_audited"] is True
    assert st_ok["std_audited_hash"] is not None

    # Mark downstream passed (stress + tests_verified)
    update_state(
        problem_dir,
        stress_passed=True,
        tests_generated=True,
        generated_test_count=4,
        tests_verified=True,
        verify_signals={
            "limit_semantics": {"executed": True, "passed": True},
            "validator_check": {"executed": True, "passed": True},
        },
    )
    assert preflight("problem_pack_polygon", problem_dir, {"problem_dir": problem_dir}) == []

    # 3. problem_build_all compiling ONLY an auxiliary wrong solution preserves stress_passed, invalidates tests_verified
    st_aux = apply_result(
        problem_dir,
        "problem_build_all",
        {"problem_dir": problem_dir},
        True,
        {"compiled": {"solutions/wrong_tle.cpp": "/bin/wrong_tle"}, "failures": {}},
    )
    assert st_aux["stress_passed"] is True
    assert st_aux["tests_verified"] is False
    assert st_aux["sol_built"] is True

    # Restore tests_verified then test external sol.cpp edit drift
    update_state(problem_dir, tests_verified=True, verify_signals={"limit_semantics": {"executed": True, "passed": True}, "validator_check": {"executed": True, "passed": True}})
    sol_cpp.write_text("int main() { return 7; }\n", encoding="utf-8")
    st_drifted = load_workflow_state(problem_dir)
    assert st_drifted["sol_built"] is False
    assert st_drifted["std_audited"] is False
    assert st_drifted["tests_verified"] is False
    assert st_drifted["stress_passed"] is False
    pack_blocks = preflight("problem_pack_polygon", problem_dir, {"problem_dir": problem_dir})
    assert any(v.gate == "tests_verified" for v in pack_blocks)

    # 4. problem_build_all failure on solutions/sol.cpp resets sol_built=False
    apply_result(problem_dir, "solution_build", {"problem_dir": problem_dir, "solution_type": "sol"}, True, {})
    assert load_workflow_state(problem_dir)["sol_built"] is True
    st_b_fail = apply_result(
        problem_dir,
        "problem_build_all",
        {"problem_dir": problem_dir},
        False,
        {"compiled": {}, "failures": {"solutions/sol.cpp": "compile error"}},
    )
    assert st_b_fail["sol_built"] is False
    assert st_b_fail["sol_source_hash"] is None
