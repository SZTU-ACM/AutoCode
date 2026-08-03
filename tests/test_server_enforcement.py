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
