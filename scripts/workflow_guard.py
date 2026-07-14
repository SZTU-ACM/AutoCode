"""Workflow guard Claude Code plugin hook (thin entry point).

Parsing, state, and gate logic live in ``hook_payload`` / ``hook_state`` /
``hook_gates``; this module only wires stdin/sys.argv to those helpers and keeps
the ``pre`` / ``post`` / ``session`` command contract stable so
``hooks.json`` and existing tests keep working unchanged.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# Make sibling hook_* modules importable when this file is run directly as a
# script (e.g. via `python scripts/workflow_guard.py pre`).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hook_gates import PRE_GATES, _append_history
from hook_payload import (
    deny,
    get_problem_dir,
    load_payload,
    parse_tool_result,
    tool_short_name,
)
from hook_state import infer_state, load_state, save_state

__all__ = [
    "load_payload",
    "infer_state",
    "load_state",
    "save_state",
    "pre_tool",
    "post_tool",
    "session_start",
]


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def pre_tool(payload: dict[str, Any]) -> int:
    if payload.get("_malformed_payload"):
        deny("Hook payload is malformed; cannot verify workflow gates.")
        return 0

    short_name = tool_short_name(payload.get("tool_name", ""))
    if short_name == "problem_create":
        return 0

    problem_dir = get_problem_dir(payload)
    if not problem_dir:
        return 0

    state = load_state(problem_dir)
    tool_input = payload.get("tool_input", {})
    for predicate, reason in PRE_GATES.get(short_name, []):
        if not predicate(state, tool_input):
            deny(reason)
            return 0
    return 0


def post_tool(payload: dict[str, Any]) -> int:
    short_name = tool_short_name(payload.get("tool_name", ""))
    problem_dir = get_problem_dir(payload)
    if not problem_dir:
        return 0

    success, data = parse_tool_result(payload)
    state = load_state(problem_dir)

    if short_name == "problem_generate_tests":
        # 任何重新生成尝试都会让旧验证失效（无论成功还是失败）
        state["tests_verified"] = False
        if not success:
            state["tests_generated"] = False
            state["generated_test_count"] = 0
            _append_history(
                state,
                tool=short_name,
                success=False,
                gate_result="post",
                key_metrics={"generated_test_count": 0},
            )
            save_state(problem_dir, state)
            return 0
        generated_tests = data.get("generated_tests", [])
        state["tests_generated"] = bool(generated_tests)
        state["generated_test_count"] = len(generated_tests)
        _append_history(
            state,
            tool=short_name,
            success=True,
            gate_result="post",
            key_metrics={"generated_test_count": len(generated_tests)},
        )
        save_state(problem_dir, state)
        return 0

    if short_name == "problem_validate":
        state["statement_validated"] = data.get("statement_samples", {}).get("validated", False)
        state["sample_files_validated"] = data.get("sample_files", {}).get("validated", False)
        state["validation_passed"] = success
        _append_history(
            state,
            tool=short_name,
            success=success,
            gate_result="post",
            key_metrics={"validation_passed": success},
        )
        save_state(problem_dir, state)
        return 0

    if short_name == "problem_verify_tests":
        limit_ratio = data.get("results", {}).get("limit_ratio", {})
        if isinstance(limit_ratio, dict):
            state["limit_case_ratio"] = limit_ratio.get("limit_case_ratio")
        else:
            state["limit_case_ratio"] = None
        gates = state.get("quality_gates", {})
        min_limit_ratio = 0.5
        if isinstance(gates, dict):
            try:
                min_limit_ratio = float(gates.get("min_limit_case_ratio", 0.5))
            except (TypeError, ValueError):
                min_limit_ratio = 0.5
        min_limit_ratio = min(1.0, max(0.0, min_limit_ratio))
        ratio_ok = True
        try:
            if state.get("limit_case_ratio") is not None:
                ratio_ok = float(state.get("limit_case_ratio")) >= min_limit_ratio
        except (TypeError, ValueError):
            ratio_ok = False
        quality_signals = data.get("quality_signals", {})
        if isinstance(quality_signals, dict):
            state["verify_signals"] = quality_signals
        else:
            state["verify_signals"] = {}
        state["tests_verified"] = success and bool(data.get("passed", False))
        state["tests_verified"] = state["tests_verified"] and ratio_ok
        _append_history(
            state,
            tool=short_name,
            success=state["tests_verified"],
            gate_result="post",
            key_metrics={
                "tests_verified": state["tests_verified"],
                "limit_case_ratio": state.get("limit_case_ratio"),
            },
        )
        save_state(problem_dir, state)
        return 0

    if not success:
        if short_name == "validator_build":
            state["validator_ready"] = False
            state["validator_accuracy"] = None
        elif short_name == "solution_audit_std":
            state["std_audited"] = False
        elif short_name == "solution_audit_brute":
            state["brute_audited"] = False
        elif short_name == "generator_build":
            state["generator_built"] = False
        elif short_name == "stress_test_run":
            state["stress_completed_rounds"] = 0
            state["stress_total_rounds"] = 0
            state["stress_passed"] = False
        elif short_name == "checker_build":
            state["checker_accuracy"] = None
            state["checker_ready"] = False
        elif short_name == "interactor_build":
            state["interactor_ready"] = False
            state["interaction_scenarios"] = {}
            state["interactor_pass_rate"] = 0
            state["interactor_fail_rate"] = 0
        elif short_name == "problem_pack_polygon":
            state["packaged"] = False
        _append_history(state, tool=short_name, success=False, gate_result="post")
        save_state(problem_dir, state)
        return 0

    if short_name == "problem_create":
        state["created"] = True
        state["interactive"] = bool(payload.get("tool_input", {}).get("interactive", False))
    elif short_name == "solution_build":
        solution_type = payload.get("tool_input", {}).get("solution_type")
        if solution_type == "sol":
            state["sol_built"] = True
            state["solution_analyzed"] = False
            state["std_audited"] = False
            state["brute_audited"] = False
        elif solution_type == "brute":
            state["brute_built"] = True
            state["brute_audited"] = False
    elif short_name == "solution_analyze":
        state["solution_analyzed"] = True
        state["std_audited"] = False
        state["brute_audited"] = False
        std_complexity = (
            data.get("estimated_complexity")
            or data.get("final_complexity")
            or data.get("worst_case_complexity")
        )
        if std_complexity is not None:
            state["std_complexity"] = std_complexity
        if "recommended_stress_params" in data:
            state["recommended_stress_params"] = data.get("recommended_stress_params")
    elif short_name == "solution_audit_std":
        state["std_audited"] = True
    elif short_name == "solution_audit_brute":
        state["brute_audited"] = True
        if "brute_complexity" in data:
            state["brute_complexity"] = data.get("brute_complexity")
        if "recommended_stress_params" in data:
            state["recommended_stress_params"] = data.get("recommended_stress_params")
    elif short_name == "validator_build":
        accuracy = data.get("accuracy")
        state["validator_accuracy"] = accuracy
        state["validator_ready"] = isinstance(accuracy, int | float) and accuracy >= 0.9
    elif short_name == "validator_select":
        pass
    elif short_name == "generator_build":
        state["generator_built"] = True
    elif short_name == "stress_test_run":
        state["stress_completed_rounds"] = data.get("completed_rounds", 0)
        state["stress_total_rounds"] = data.get("total_rounds", 0)
        state["stress_passed"] = data.get("completed_rounds") == data.get("total_rounds")
    elif short_name == "checker_build":
        accuracy = data.get("accuracy")
        state["checker_accuracy"] = accuracy
        state["checker_ready"] = isinstance(accuracy, int | float) and accuracy >= 0.9
    elif short_name == "interactor_build":
        pass_rate = data.get("pass_rate", 0)
        fail_rate = data.get("fail_rate", 0)
        interaction_scenarios = data.get("interaction_scenarios", {})
        if isinstance(interaction_scenarios, dict):
            state["interaction_scenarios"] = interaction_scenarios
        else:
            state["interaction_scenarios"] = {}
        scenario_accuracy = data.get("scenario_accuracy")
        scenario_ready = False
        if isinstance(interaction_scenarios, dict):
            try:
                scenario_ready = (
                    bool(interaction_scenarios.get("validated"))
                    and int(interaction_scenarios.get("total", 0)) > 0
                    and float(interaction_scenarios.get("accuracy", 0)) >= 1.0
                )
            except (TypeError, ValueError):
                scenario_ready = False
        if isinstance(scenario_accuracy, (int, float)) and scenario_accuracy < 1.0:
            scenario_ready = False
        state["interactor_ready"] = scenario_ready or (pass_rate == 1.0 and fail_rate >= 0.8)
        state["interactor_pass_rate"] = pass_rate
        state["interactor_fail_rate"] = fail_rate
    elif short_name == "problem_pack_polygon":
        state["packaged"] = True

    _append_history(state, tool=short_name, success=True, gate_result="post")
    save_state(problem_dir, state)
    return 0


def session_start() -> int:
    additional_context = (
        "AutoCode plugin active. Enforce this workflow with quality gates: "
        "problem_create -> solution_build(sol) -> solution_build(brute) -> "
        "validator_build(accuracy >= 0.9, non-interactive only) -> interactor_build(interactive only) -> "
        "generator_build -> checker_build（SPJ 且 stress_comparison=checker 时可先于 stress）-> "
        "stress_test_run(completed_rounds == total_rounds) -> "
        "checker_build if needed (non-interactive, 非 SPJ checker 路径时通常在 stress 后) -> "
        "problem_validate(validation_passed) -> "
        "problem_generate_tests(generated_test_count > 0, and prefer >=50% type3/type4 in final tests when candidates are sufficient) -> "
        "problem_verify_tests(passed) -> problem_pack_polygon. "
        "Statement format must follow: title -> time/memory limits -> optional background -> problem description -> input format (all variable ranges included) -> output format -> numbered samples -> explanation; sample explanations belong in the explanation section. "
        "In headless or proxy-backed runs, do not end a turn with only an intention or status update while workflow steps remain; call the next AutoCode MCP tool directly, one tool call at a time. "
        "When running long problem_generate_tests tasks, avoid sending new chat messages because that can interrupt MCP calls; if interrupted, resume with checkpoint state (resume=true). "
        "If a hook blocks a step, complete the missing prerequisite instead of retrying blindly."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": additional_context,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    configure_stdio()
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    payload = load_payload() if mode in {"pre", "post"} else {}

    if mode == "pre":
        return pre_tool(payload)
    if mode == "post":
        return post_tool(payload)
    if mode == "session":
        return session_start()

    print(f"Unknown mode: {mode}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
