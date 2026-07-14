"""Hook quality-gate predicates and ``PRE_GATES`` table.

Single source of truth for gate evaluation, replacing the per-tool
duplications. Manifest reads use ``load_manifest`` from ``hook_state``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from hook_state import load_manifest

Gate = tuple[Callable[[dict[str, Any], dict[str, Any]], bool], str]

DEFAULT_QUALITY_GATES = {
    "require_stress_passed": True,
    "require_validation_passed": True,
    "require_tests_verified": True,
    "require_limit_semantics": True,
    "require_wrong_solution_kill": False,
    "require_validator_check": True,
    "min_limit_case_ratio": 0.5,
}


def _is_non_interactive(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return not bool(state.get("interactive", False))


def _is_interactive(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return bool(state.get("interactive", False))


def _validator_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if bool(state.get("interactive", False)):
        return True
    if not bool(state.get("validator_ready")):
        return False
    accuracy = state.get("validator_accuracy")
    return isinstance(accuracy, int | float) and accuracy >= 0.9


def _interactor_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if not bool(state.get("interactive", False)):
        return True
    if bool(state.get("interactor_ready")):
        return True
    interaction = state.get("interaction_scenarios", {})
    if not isinstance(interaction, dict):
        return False
    try:
        accuracy = float(interaction.get("accuracy", 0))
    except (TypeError, ValueError):
        return False
    return bool(interaction.get("validated")) and int(interaction.get("total", 0)) > 0 and accuracy >= 1.0


def _quality_gate_enabled(state: dict[str, Any], key: str, default: bool = True) -> bool:
    gates = state.get("quality_gates", {})
    if not isinstance(gates, dict):
        return default
    value = gates.get(key, default)
    return bool(value)


def _stress_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _quality_gate_enabled(state, "require_stress_passed")) or bool(
        state.get("stress_passed")
    )


def _validation_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if not _quality_gate_enabled(state, "require_validation_passed"):
        return True
    return (
        bool(state.get("validation_passed"))
        and bool(state.get("statement_validated"))
        and bool(state.get("sample_files_validated"))
    )


def _tests_verified_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _quality_gate_enabled(state, "require_tests_verified")) or bool(
        state.get("tests_verified")
    )


def _audit_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return bool(state.get("std_audited")) and bool(state.get("brute_audited"))


def _min_limit_ratio_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    gates = state.get("quality_gates", {})
    if not isinstance(gates, dict):
        return True
    try:
        required = float(gates.get("min_limit_case_ratio", 0.5))
    except (TypeError, ValueError):
        required = 0.5
    required = min(1.0, max(0.0, required))
    ratio = state.get("limit_case_ratio")
    if ratio is None:
        return True
    try:
        ratio_val = float(ratio)
    except (TypeError, ValueError):
        return False
    return ratio_val >= required


def _required_verify_signal_ok(state: dict[str, Any], gate_key: str, signal_name: str) -> bool:
    if not _quality_gate_enabled(state, gate_key):
        return True
    verify_signals = state.get("verify_signals", {})
    if not isinstance(verify_signals, dict):
        return False
    signal = verify_signals.get(signal_name, {})
    if not isinstance(signal, dict):
        return False
    return bool(signal.get("executed")) and bool(signal.get("passed"))


def _limit_semantics_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_limit_semantics", "limit_semantics")


def _wrong_solution_kill_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_wrong_solution_kill", "wrong_solution_kill")


def _validator_check_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_validator_check", "validator_check")


def _stress_prereq_core_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    """与 stress_test_run 相同的核心前置（不含 stress_passed / checker）。"""
    return (
        bool(state.get("sol_built"))
        and bool(state.get("brute_built"))
        and bool(state.get("solution_analyzed"))
        and _audit_gate_ok(state, tool_input)
        and _validator_gate_ok(state, tool_input)
        and bool(state.get("generator_built"))
    )


def _manifest_dict_uses_testlib_checker(manifest: dict[str, Any]) -> bool:
    """与 Pydantic manifest 的 manifest_uses_testlib_checker 一致（仅用 JSON 真值）。"""
    return manifest.get("special_judge") is True and manifest.get("stress_comparison") == "checker"


def _checker_build_prereq_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    """stress 通过后，或 SPJ+checker 对拍路径在 stress 前完成 checker 编译。"""
    if bool(state.get("stress_passed")):
        return True
    problem_dir = tool_input.get("problem_dir")
    if not isinstance(problem_dir, str) or not problem_dir.strip():
        return False
    manifest = load_manifest(problem_dir.strip())
    if not _manifest_dict_uses_testlib_checker(manifest):
        return False
    return _stress_prereq_core_ok(state, tool_input)


def _stress_spj_checker_ready_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    """SPJ + stress_comparison=checker 时对拍依赖 checker。"""
    problem_dir = tool_input.get("problem_dir")
    if not isinstance(problem_dir, str) or not problem_dir.strip():
        return True
    manifest = load_manifest(problem_dir.strip())
    if not _manifest_dict_uses_testlib_checker(manifest):
        return True
    return bool(state.get("checker_ready"))


def _append_history(
    state: dict[str, Any],
    *,
    tool: str,
    success: bool,
    key_metrics: dict[str, Any] | None = None,
    gate_result: str = "n/a",
) -> None:
    history = state.get("history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "tool": tool,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "gate_result": gate_result,
            "key_metrics": key_metrics or {},
        }
    )
    state["history"] = history[-200:]


PRE_GATES: dict[str, list[Gate]] = {
    "solution_build": [
        (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。"),
        (
            lambda s, i: i.get("solution_type") != "brute" or bool(s.get("sol_built")),
            "必须先构建标准解 sol，再构建 brute。",
        ),
    ],
    "solution_analyze": [
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol，再进行复杂度分析。"),
    ],
    "solution_audit_std": [
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。"),
        (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。"),
    ],
    "solution_audit_brute": [
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。"),
        (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute。"),
        (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。"),
        (lambda s, i: bool(s.get("std_audited")), "必须先完成 solution_audit_std。"),
    ],
    "validator_select": [
        (lambda s, i: bool(s.get("validator_ready")), "必须先完成 validator_build 才能选择校验器版本。"),
    ],
    "validator_build": [
        (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。"),
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。"),
        (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze，再构建 validator。"),
        (_audit_gate_ok, "必须先完成 solution_audit_std 与 solution_audit_brute。"),
        (_is_non_interactive, "交互题不应构建 validator，应改用 interactor_build。"),
        (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute，再构建 validator。"),
    ],
    "interactor_build": [
        (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。"),
        (_is_interactive, "只有交互题可运行 interactor_build。请在 problem_create 设 interactive=true。"),
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。"),
        (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute。"),
        (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。"),
        (_audit_gate_ok, "必须先完成 solution_audit_std 与 solution_audit_brute。"),
    ],
    "generator_build": [
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。"),
        (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute。"),
        (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。"),
        (_audit_gate_ok, "必须先完成 solution_audit_std 与 solution_audit_brute。"),
        (
            _validator_gate_ok,
            "必须先完成 validator_build，并且 validator accuracy >= 0.9。",
        ),
        (
            _interactor_gate_ok,
            "交互题必须先完成 interactor_build 并可用。",
        ),
    ],
    "stress_test_run": [
        (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。"),
        (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute。"),
        (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。"),
        (_audit_gate_ok, "必须先完成 solution_audit_std 与 solution_audit_brute。"),
        (
            _validator_gate_ok,
            "必须先完成 validator_build(accuracy >= 0.9)，再进行 stress_test_run。",
        ),
        (lambda s, i: bool(s.get("generator_built")), "必须先完成 generator_build，再进行 stress_test_run。"),
        (
            _stress_spj_checker_ready_ok,
            "SPJ 且 stress_comparison=checker 时需先成功完成 checker_build（accuracy >= 0.9）。",
        ),
    ],
    "checker_build": [
        (_is_non_interactive, "交互题不应构建 checker，请使用 interactor_build。"),
        (
            _checker_build_prereq_ok,
            "须先通过 stress_test_run；或在 autocode.json 设 special_judge 并完成与 stress 相同的前置步骤后再编译 checker。",
        ),
    ],
    "problem_validate": [
        (_stress_required_gate_ok, "必须先通过 stress_test_run，再进行题面与样例验证。"),
    ],
    "problem_generate_tests": [
        (_stress_required_gate_ok, "必须先通过 stress_test_run。"),
        (_validation_required_gate_ok, "必须先通过 problem_validate（题面与样例均通过）。"),
        (
            lambda s, i: not bool(s.get("interactive")) or bool(s.get("interactor_ready")),
            "交互题必须先完成 interactor_build 并可用。",
        ),
    ],
    "problem_verify_tests": [
        (
            lambda s, i: bool(s.get("tests_generated")) and int(s.get("generated_test_count", 0)) > 0,
            "必须先运行 problem_generate_tests 生成最终测试数据。",
        ),
    ],
    "problem_pack_polygon": [
        (
            lambda s, i: bool(s.get("tests_generated")) and int(s.get("generated_test_count", 0)) > 0,
            "必须先生成最终测试数据。",
        ),
        (_tests_verified_required_gate_ok, "必须先通过 problem_verify_tests(passed=true)，再进行打包。"),
        (_min_limit_ratio_gate_ok, "最终测试中的极限样例占比未达到 quality_gates.min_limit_case_ratio。"),
        (_limit_semantics_gate_ok, "最终测试未通过 limit_semantics，不能打包。"),
        (_wrong_solution_kill_gate_ok, "最终测试未通过 wrong_solution_kill，不能打包。"),
        (_validator_check_gate_ok, "最终测试未通过 validator_check，不能打包。"),
    ],
}
