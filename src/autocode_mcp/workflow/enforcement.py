"""Host-independent workflow enforcement for AutoCode MCP calls.

Claude Code hooks can provide an early, user-friendly denial, but they are not
available to every MCP host.  The MCP server therefore owns the authoritative
preflight checks and workflow state transitions in this module.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime_store import AUDIT, WORKFLOW, get_section, runtime_file, set_section
from .manifest import (
    CANONICAL_PROBLEM_FILES,
    load_manifest,
    manifest_path,
    manifest_uses_testlib_checker,
)

DEFAULT_QUALITY_GATES: dict[str, Any] = {
    "require_stress_passed": True,
    "require_validation_passed": True,
    "require_tests_verified": True,
    "require_limit_semantics": True,
    "require_wrong_solution_kill": False,
    "require_validator_check": True,
    "min_limit_case_ratio": 0.5,
}

Gate = tuple[Callable[[dict[str, Any], dict[str, Any]], bool], str]


@dataclass(frozen=True)
class GateViolation:
    """A deterministic reason a tool call cannot proceed."""

    gate: str
    reason: str
    next_action: str


def _state_problem_type(state: dict[str, Any]) -> str:
    ptype = state.get("problem_type")
    if isinstance(ptype, str) and ptype in {"standard", "spj", "interactive", "communication"}:
        return ptype
    return "interactive" if bool(state.get("interactive", False)) else "standard"


def _is_non_interactive(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _state_problem_type(state) not in {"interactive", "communication"}


def _is_interactive(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _state_problem_type(state) == "interactive"


def _is_protocol_problem(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _state_problem_type(state) in {"interactive", "communication"}


def _requires_external_interactor(state: dict[str, Any]) -> bool:
    ptype = _state_problem_type(state)
    if ptype == "interactive":
        return True
    if ptype == "communication":
        return bool(state.get("protocol_mode", "run_twice") != "grader_linked")
    return False


def _interactor_build_allowed(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _requires_external_interactor(state)


_NON_BRUTE_ORACLE_MODES = frozenset({"interactor_only", "reference_solutions"})


def _brute_required(state: dict[str, Any]) -> bool:
    mode = state.get("oracle_mode")
    return not (isinstance(mode, str) and mode in _NON_BRUTE_ORACLE_MODES)


def _brute_built_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _brute_required(state)) or bool(state.get("brute_built"))


def _validator_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if _is_protocol_problem(state, {}):
        return True
    accuracy = state.get("validator_accuracy")
    parsed = _finite_ratio(accuracy)
    return bool(state.get("validator_ready")) and parsed is not None and parsed >= 0.9


def _interactor_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if not _requires_external_interactor(state):
        return True
    if bool(state.get("interactor_ready")):
        return True
    return _interaction_scenarios_ready(state.get("interaction_scenarios", {}))


def _quality_gate_enabled(state: dict[str, Any], key: str, default: bool = True) -> bool:
    gates = state.get("quality_gates", {})
    return bool(gates.get(key, default)) if isinstance(gates, dict) else default


def _stress_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _quality_gate_enabled(state, "require_stress_passed")) or bool(
        state.get("stress_passed")
    )


def _validation_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    if not _quality_gate_enabled(state, "require_validation_passed"):
        return True
    return bool(state.get("validation_passed")) and bool(state.get("statement_validated")) and bool(
        state.get("sample_files_validated")
    )


def _tests_verified_required_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (not _quality_gate_enabled(state, "require_tests_verified")) or bool(
        state.get("tests_verified")
    )


def _audit_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return bool(state.get("std_audited")) and (
        (not _brute_required(state)) or bool(state.get("brute_audited"))
    )


def _finite_ratio(value: Any) -> float | None:
    """Parse a JSON numeric ratio while rejecting bool, NaN, and out-of-range values."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    ratio = float(value)
    if not math.isfinite(ratio) or not 0.0 <= ratio <= 1.0:
        return None
    return ratio


def _interaction_scenarios_ready(interaction: Any) -> bool:
    if not isinstance(interaction, dict) or not bool(interaction.get("validated")):
        return False
    try:
        total = int(interaction.get("total", 0))
    except (TypeError, ValueError):
        return False
    accuracy = _finite_ratio(interaction.get("accuracy", 0))
    return total > 0 and accuracy is not None and accuracy >= 1.0


def _limit_ratio_threshold(state: dict[str, Any]) -> float | None:
    gates = state.get("quality_gates", {})
    if not isinstance(gates, dict):
        return 0.5
    return _finite_ratio(gates.get("min_limit_case_ratio", 0.5))


def _min_limit_ratio_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    required = _limit_ratio_threshold(state)
    if required is None:
        return False
    ratio_value = state.get("limit_case_ratio")
    if ratio_value is None:
        return True
    ratio = _finite_ratio(ratio_value)
    return ratio is not None and ratio >= required


def _required_verify_signal_ok(state: dict[str, Any], gate_key: str, signal_name: str) -> bool:
    if not _quality_gate_enabled(state, gate_key):
        return True
    signals = state.get("verify_signals", {})
    signal = signals.get(signal_name, {}) if isinstance(signals, dict) else {}
    return isinstance(signal, dict) and bool(signal.get("executed")) and bool(signal.get("passed"))


def _limit_semantics_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_limit_semantics", "limit_semantics")


def _wrong_solution_kill_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_wrong_solution_kill", "wrong_solution_kill")


def _validator_check_gate_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return _required_verify_signal_ok(state, "require_validator_check", "validator_check")


def _stress_prereq_core_ok(state: dict[str, Any], _: dict[str, Any]) -> bool:
    return (
        bool(state.get("sol_built"))
        and _brute_built_ok(state, {})
        and bool(state.get("solution_analyzed"))
        and _audit_gate_ok(state, {})
        and _validator_gate_ok(state, {})
        and _interactor_gate_ok(state, {})
        and bool(state.get("generator_built"))
    )


def _manifest_checker(problem_dir: str) -> bool:
    try:
        return bool(manifest_uses_testlib_checker(load_manifest(problem_dir)))
    except (OSError, ValueError):
        return False


def _checker_build_prereq_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    if bool(state.get("stress_passed")):
        return True
    problem_dir = tool_input.get("problem_dir")
    return isinstance(problem_dir, str) and bool(problem_dir.strip()) and _manifest_checker(problem_dir) and _stress_prereq_core_ok(state, tool_input)


def _stress_spj_checker_ready_ok(state: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    problem_dir = tool_input.get("problem_dir")
    if not isinstance(problem_dir, str) or not problem_dir.strip() or not _manifest_checker(problem_dir):
        return True
    return bool(state.get("checker_ready"))


def _has_generated_tests(state: dict[str, Any], _: dict[str, Any]) -> bool:
    try:
        return bool(state.get("tests_generated")) and int(state.get("generated_test_count", 0)) > 0
    except (TypeError, ValueError):
        return False


_DOWNSTREAM_INVALIDATING_TOOLS = frozenset(
    {
        "problem_create",
        "solution_build",
        "problem_build_all",
        "solution_analyze",
        "solution_audit_std",
        "solution_audit_brute",
        "validator_build",
        "generator_build",
        "checker_build",
        "interactor_build",
    }
)


def _invalidate_downstream(problem_dir: str, state: dict[str, Any]) -> None:
    """Discard results that depend on an upstream artifact or validation step."""
    state.update(
        {
            "stress_passed": False,
            "stress_completed_rounds": 0,
            "stress_total_rounds": 0,
            "statement_validated": False,
            "sample_files_validated": False,
            "validation_passed": False,
            "tests_generated": False,
            "generated_test_count": 0,
            "tests_verified": False,
            "limit_case_ratio": None,
            "verify_signals": {},
            "packaged": False,
        }
    )
    set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})


def _saved_relative_path(problem_dir: str, raw_path: Any) -> str | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path_text = raw_path.replace("\\", "/")
    if not Path(path_text).is_absolute() and "/" not in path_text:
        path_text = CANONICAL_PROBLEM_FILES.get(path_text, path_text)
    root = Path(problem_dir).resolve()
    path = Path(path_text)
    candidate = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return None


def _invalidate_for_file_save(problem_dir: str, state: dict[str, Any], tool_input: dict[str, Any]) -> None:
    relative = _saved_relative_path(problem_dir, tool_input.get("path"))
    if relative is None:
        return
    if relative == ".autocode/manifest.json":
        _invalidate_downstream(problem_dir, state)
        state.update(
            {
                "sol_built": False,
                "brute_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "std_claimed_complexity": None,
                "validator_ready": False,
                "validator_accuracy": None,
                "generator_built": False,
                "checker_ready": False,
                "checker_accuracy": None,
                "interactor_ready": False,
                "interaction_scenarios": {},
                "statement_dirty": False,
            }
        )
        return
    if relative == "statements/tutorial.md":
        # 题解仅影响打包与完整审计，不应清空已完成的对拍与终测状态。
        state["packaged"] = False
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
        return
    if relative == "statements/README.md":
        # 修改题面只需重新做题面/样例校验与审计/打包，不清空已通过的对拍结果，
        # 但标记 statement_dirty 以阻断未重新校验就打包。
        state.update(
            {
                "statement_validated": False,
                "validation_passed": False,
                "statement_dirty": True,
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
        return
    if relative == "tests" or relative.startswith("tests/"):
        # 修改 tests/ 文件需重新校验样例/终测，不清空对拍结果。
        state.update(
            {
                "sample_files_validated": False,
                "validation_passed": False,
                "tests_verified": False,
                "limit_case_ratio": None,
                "verify_signals": {},
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
        return
    if relative in {
        "solutions/sol.cpp",
        "solutions/brute.cpp",
        "files/val.cpp",
        "files/gen.cpp",
        "files/checker.cpp",
        "files/interactor.cpp",
    }:
        _invalidate_downstream(problem_dir, state)
    elif relative.startswith("files/"):
        # 公共头文件/辅助代码（如 problem.h / grader.cpp）变更影响整个下游产物。
        _invalidate_downstream(problem_dir, state)
    elif relative.startswith("solutions/"):
        # 其它解法（例如 role=wrong 错解或参考解）变更会使终测与错解杀伤信号过期。
        state.update(
            {
                "tests_verified": False,
                "verify_signals": {},
                "packaged": False,
            }
        )
        set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
    if relative == "solutions/sol.cpp":
        state.update(
            {
                "sol_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "std_claimed_complexity": None,
            }
        )
    elif relative == "solutions/brute.cpp":
        state.update({"brute_built": False, "brute_audited": False})
    elif relative == "files/val.cpp":
        state.update({"validator_ready": False, "validator_accuracy": None})
    elif relative == "files/gen.cpp":
        state["generator_built"] = False
    elif relative == "files/checker.cpp":
        state.update({"checker_ready": False, "checker_accuracy": None})
    custom_comm_files: set[str] = set()
    custom_grader_files: set[str] = set()
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError):
        manifest = None
    if manifest is not None and manifest.protocol is not None:
        if manifest.protocol.communicator_file:
            norm = _saved_relative_path(problem_dir, manifest.protocol.communicator_file)
            if norm:
                custom_comm_files.add(norm)
        for p_file in (manifest.protocol.grader_file, manifest.protocol.header_file):
            if p_file:
                norm = _saved_relative_path(problem_dir, p_file)
                if norm:
                    custom_grader_files.add(norm)

    if relative in ({"files/interactor.cpp", "files/communicator.cpp"} | custom_comm_files):
        _invalidate_downstream(problem_dir, state)
        state.update({"interactor_ready": False, "interaction_scenarios": {}})
    elif relative in ({"files/grader.cpp", "files/problem.h"} | custom_grader_files):
        _invalidate_downstream(problem_dir, state)
        state.update(
            {
                "sol_built": False,
                "brute_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "std_claimed_complexity": None,
            }
        )


def _stress_rounds_complete(data: dict[str, Any]) -> bool:
    try:
        completed = int(data.get("completed_rounds"))
        total = int(data.get("total_rounds"))
    except (TypeError, ValueError):
        return False
    return total > 0 and completed == total


def _violation(gate: str, reason: str, next_action: str) -> Gate:
    def predicate(_: dict[str, Any], __: dict[str, Any]) -> bool:
        return False

    predicate.__name__ = f"always_false_{gate}"
    return predicate, reason


PRE_GATES: dict[str, list[tuple[str, Gate]]] = {
    "problem_build_all": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
    ],
    "solution_build": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
        ("sol_before_brute", (lambda s, i: i.get("solution_type") != "brute" or bool(s.get("sol_built")), "必须先构建标准解 sol，再构建 brute。")),
    ],
    "solution_analyze": [("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol，再进行复杂度分析。"))],
    "solution_audit_std": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。")),
    ],
    "solution_audit_brute": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (lambda s, i: bool(s.get("brute_built")), "必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze。")),
        ("std_audited", (lambda s, i: bool(s.get("std_audited")), "必须先完成 solution_audit_std。")),
    ],
    "validator_select": [("validator_ready", (lambda s, i: bool(s.get("validator_ready")), "必须先完成 validator_build 才能选择校验器版本。"))],
    "validator_build": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须先运行 solution_analyze，再构建 validator。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute，再构建 validator。")),
    ],
    "interactor_build": [
        ("created", (lambda s, i: bool(s.get("created")), "必须先运行 problem_create 创建题目目录。")),
        ("interactive", (_interactor_build_allowed, "只有交互题或非 grader_linked 通信题可运行 interactor_build。")),
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须运行 solution_analyze。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
    ],
    "generator_build": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须运行 solution_analyze。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
        ("validator", (_validator_gate_ok, "必须先完成 validator_build，并且 validator accuracy >= 0.9。")),
        ("interactor", (_interactor_gate_ok, "交互题与通信题（非 grader_linked）必须先完成 interactor_build 并可用。")),
    ],
    "stress_test_run": [
        ("sol_built", (lambda s, i: bool(s.get("sol_built")), "必须先构建标准解 sol。")),
        ("brute_built", (_brute_built_ok, "brute 模式下必须先构建 brute。")),
        ("solution_analyzed", (lambda s, i: bool(s.get("solution_analyzed")), "必须运行 solution_analyze。")),
        ("audits", (_audit_gate_ok, "必须先完成 solution_audit_std（以及 brute 模式下 solution_audit_brute）。")),
        ("validator", (_validator_gate_ok, "必须先完成 validator_build(accuracy >= 0.9)。")),
        ("interactor", (_interactor_gate_ok, "交互题与通信题（非 grader_linked）必须先完成 interactor_build 并可用。")),
        ("generator", (lambda s, i: bool(s.get("generator_built")), "必须先完成 generator_build。")),
        ("checker", (_stress_spj_checker_ready_ok, "SPJ 且 stress_comparison=checker 时需先完成 checker_build。")),
    ],
    "checker_build": [
        ("non_interactive", (_is_non_interactive, "交互题或通信题不应构建 checker，请使用 interactor_build / grader。")),
        ("prerequisites", (_checker_build_prereq_ok, "须先通过 stress_test_run；或在 SPJ checker 路径完成 stress 前置步骤。")),
    ],
    "problem_validate": [("stress", (_stress_required_gate_ok, "必须先通过 stress_test_run，再进行题面与样例验证。"))],
    "problem_generate_tests": [
        ("stress", (_stress_required_gate_ok, "必须先通过 stress_test_run。")),
        ("validation", (_validation_required_gate_ok, "必须先通过 problem_validate（题面与样例均通过）。")),
        ("interactor", (_interactor_gate_ok, "交互题与通信题（非 grader_linked）必须先完成 interactor_build 并可用。")),
    ],
    "problem_verify_tests": [("tests_generated", (_has_generated_tests, "必须先运行 problem_generate_tests 生成最终测试数据。"))],
    "problem_pack_polygon": [
        (
            "statement_fresh",
            (
                lambda s, i: (not _quality_gate_enabled(s, "require_validation_passed"))
                or (not bool(s.get("statement_dirty", False))),
                "题面已修改，必须重新通过 problem_validate 后再打包。",
            ),
        ),
        ("tests_generated", (_has_generated_tests, "必须先生成最终测试数据。")),
        ("tests_verified", (_tests_verified_required_gate_ok, "必须先通过 problem_verify_tests(passed=true)，再进行打包。")),
        ("limit_ratio", (_min_limit_ratio_gate_ok, "最终测试中的极限样例占比未达到 quality_gates.min_limit_case_ratio。")),
        ("limit_semantics", (_limit_semantics_gate_ok, "最终测试未通过 limit_semantics，不能打包。")),
        ("wrong_solution_kill", (_wrong_solution_kill_gate_ok, "最终测试未通过 wrong_solution_kill，不能打包。")),
        ("validator_check", (_validator_check_gate_ok, "最终测试未通过 validator_check，不能打包。")),
    ],
}


NEXT_ACTIONS: dict[str, str] = {
    "created": "调用 problem_create",
    "sol_before_brute": "调用 solution_build(solution_type='sol')",
    "sol_built": "调用 solution_build(solution_type='sol')",
    "brute_built": "调用 solution_build(solution_type='brute')",
    "solution_analyzed": "调用 solution_analyze",
    "std_audited": "调用 solution_audit_std",
    "audits": "调用 solution_audit_std（brute 模式下还需调用 solution_audit_brute）",
    "validator_ready": "调用 validator_build",
    "validator": "调用 validator_build 并确保 accuracy >= 0.9",
    "non_interactive": "交互题或通信题请使用 interactor_build / grader，不要调用 checker_build",
    "interactive": "交互题或非 grader_linked 通信题才可调用 interactor_build",
    "interactor": "调用 interactor_build 并通过交互场景检查",
    "generator": "调用 generator_build",
    "stress": "调用 stress_test_run 并完成全部轮次",
    "checker": "调用 checker_build（SPJ checker 路径）",
    "prerequisites": "完成 stress_test_run 或 SPJ checker 前置步骤",
    "validation": "调用 problem_validate 并通过题面、样例检查",
    "statement_fresh": "题面已修改，请重新调用 problem_validate",
    "tests_generated": "调用 problem_generate_tests",
    "tests_verified": "调用 problem_verify_tests 并通过所有质量信号",
    "limit_ratio": "补充 type=3/type=4 测试数据",
    "limit_semantics": "修正并重新验证极限/TLE 语义差异",
    "wrong_solution_kill": "启用并通过 wrong_solution_kill，或关闭对应 quality gate",
    "validator_check": "修正 validator 并重新运行 problem_verify_tests",
}


def _default_state(problem_dir: str) -> dict[str, Any]:
    root = Path(problem_dir)
    manifest = None
    manifest_error: str | None
    try:
        manifest = load_manifest(problem_dir)
        problem_type = manifest.problem_type if manifest else "standard"
        oracle_mode = manifest.oracle_mode if manifest else "brute"
        protocol_mode = manifest.protocol.mode if (manifest and manifest.protocol) else None
        interactive = bool(manifest.interactive) if manifest else False
        gates = manifest.quality_gates.model_dump(mode="json") if manifest else dict(DEFAULT_QUALITY_GATES)
    except (OSError, ValueError) as exc:
        problem_type = "standard"
        oracle_mode = "brute"
        protocol_mode = None
        interactive = False
        gates = dict(DEFAULT_QUALITY_GATES)
        manifest_error = str(exc)
    else:
        manifest_error = None
    state: dict[str, Any] = {
        "problem_dir": str(root),
        "created": root.exists()
        and (root / "files").exists()
        and (root / "solutions").exists()
        and manifest is not None,
        "problem_type": problem_type,
        "oracle_mode": oracle_mode,
        "protocol_mode": protocol_mode,
        "interactive": interactive,
        "sol_built": False,
        "brute_built": False,
        "solution_analyzed": False,
        "std_audited": False,
        "brute_audited": False,
        "validator_ready": False,
        "validator_accuracy": None,
        "generator_built": False,
        "stress_passed": False,
        "stress_completed_rounds": 0,
        "stress_total_rounds": 0,
        "checker_ready": False,
        "checker_accuracy": None,
        "interactor_ready": False,
        "interaction_scenarios": {},
        "statement_validated": False,
        "statement_dirty": False,
        "sample_files_validated": False,
        "validation_passed": False,
        "tests_generated": False,
        "generated_test_count": 0,
        "tests_verified": False,
        "verify_signals": {},
        "packaged": (root / "problem.xml").exists(),
        "quality_gates": gates,
        "history": [],
    }
    if manifest_error:
        state["manifest_error"] = manifest_error
    return state


def load_workflow_state(problem_dir: str) -> dict[str, Any]:
    loaded = get_section(problem_dir, WORKFLOW)
    state = dict(loaded) if isinstance(loaded, dict) else _default_state(problem_dir)
    try:
        manifest = load_manifest(problem_dir)
    except (OSError, ValueError) as exc:
        state["manifest_error"] = str(exc)
    else:
        state.pop("manifest_error", None)
        if manifest:
            state["problem_type"] = manifest.problem_type
            state["oracle_mode"] = manifest.oracle_mode
            state["protocol_mode"] = manifest.protocol.mode if manifest.protocol else None
            state["interactive"] = bool(manifest.interactive)
            state["quality_gates"] = manifest.quality_gates.model_dump(mode="json")
    return state


def save_workflow_state(problem_dir: str, state: dict[str, Any]) -> None:
    set_section(problem_dir, WORKFLOW, state)


def has_workflow_context(problem_dir: str) -> bool:
    """Return whether a directory has entered the managed AutoCode workflow.

    Bare tool calls in an arbitrary temporary directory remain useful for
    low-level diagnostics and backwards-compatible unit tests.  Once a
    manifest or workflow state exists, every subsequent call is subject to the
    authoritative gate engine.
    """
    return manifest_path(problem_dir).is_file() or runtime_file(problem_dir).is_file()


def preflight(
    tool_name: str,
    problem_dir: str,
    tool_input: dict[str, Any],
    *,
    strict_manifest: bool = True,
) -> list[GateViolation]:
    if tool_name == "problem_audit" and bool(tool_input.get("standalone")):
        return []
    state = load_workflow_state(problem_dir)
    violations: list[GateViolation] = []
    if strict_manifest and tool_name != "problem_create":
        if state.get("manifest_error"):
            violations.append(
                GateViolation(
                    "manifest",
                    f"manifest.json 无法读取或校验：{state['manifest_error']}",
                    "修复 manifest.json 后重试",
                )
            )
            return violations
        if not manifest_path(problem_dir).is_file():
            violations.append(
                GateViolation(
                    "manifest",
                    "受管工作流缺少 .autocode/manifest.json。",
                    "调用 problem_create 或恢复 manifest.json",
                )
            )
            return violations
    for gate_name, (predicate, reason) in PRE_GATES.get(tool_name, []):
        if not predicate(state, tool_input):
            violations.append(GateViolation(gate_name, reason, NEXT_ACTIONS.get(gate_name, reason)))
    return violations


def prepare_call(tool_name: str, problem_dir: str) -> None:
    """Persist invalidation that must happen before a long-running call."""
    if tool_name != "problem_generate_tests":
        return
    state = load_workflow_state(problem_dir)
    state["tests_verified"] = False
    state["packaged"] = False
    save_workflow_state(problem_dir, state)


def _append_history(state: dict[str, Any], tool: str, success: bool, data: dict[str, Any], gate_result: str) -> None:
    history = state.get("history")
    if not isinstance(history, list):
        history = []
    metrics = {}
    for key in ("completed_rounds", "total_rounds", "accuracy", "generated_tests", "passed"):
        if key in data:
            metrics[key] = data[key]
    history.append({"tool": tool, "success": success, "timestamp": datetime.now(timezone.utc).isoformat(), "gate_result": gate_result, "key_metrics": metrics})
    state["history"] = history[-200:]


def apply_result(problem_dir: str, tool_name: str, tool_input: dict[str, Any], success: bool, data: dict[str, Any]) -> dict[str, Any]:
    state = load_workflow_state(problem_dir)
    if tool_name == "problem_audit" and bool(tool_input.get("standalone")):
        return state
    if tool_name in _DOWNSTREAM_INVALIDATING_TOOLS:
        _invalidate_downstream(problem_dir, state)
    elif tool_name == "file_save" and success:
        _invalidate_for_file_save(problem_dir, state, tool_input)
    if tool_name == "problem_generate_tests":
        state["tests_verified"] = False
        generated = data.get("generated_tests", [])
        state["tests_generated"] = bool(generated) if success else False
        state["generated_test_count"] = len(generated) if isinstance(generated, list) else 0
    elif tool_name == "problem_validate":
        statement = data.get("statement_samples", {})
        samples = data.get("sample_files", {})
        state["statement_validated"] = bool(statement.get("validated")) if isinstance(statement, dict) else False
        state["sample_files_validated"] = bool(samples.get("validated")) if isinstance(samples, dict) else False
        state["validation_passed"] = bool(success)
        if success and state["statement_validated"]:
            state["statement_dirty"] = False
        elif not success:
            state["packaged"] = False
            set_section(problem_dir, AUDIT, {"full_audit": {}, "full_audit_passed": False})
    elif tool_name == "problem_verify_tests":
        results = data.get("results", {})
        ratio = results.get("limit_ratio", {}) if isinstance(results, dict) else {}
        state["limit_case_ratio"] = ratio.get("limit_case_ratio") if isinstance(ratio, dict) else None
        signals = data.get("quality_signals", {})
        state["verify_signals"] = signals if isinstance(signals, dict) else {}
        ratio_ok = _min_limit_ratio_gate_ok(state, {})
        state["tests_verified"] = bool(success and data.get("passed", False) and ratio_ok)
    elif not success:
        resets: dict[str, tuple[str, ...]] = {
            "solution_analyze": ("solution_analyzed",),
            "validator_build": ("validator_ready", "validator_accuracy"),
            "solution_audit_std": ("std_audited",),
            "solution_audit_brute": ("brute_audited",),
            "generator_build": ("generator_built",),
            "stress_test_run": ("stress_completed_rounds", "stress_total_rounds", "stress_passed"),
            "checker_build": ("checker_ready", "checker_accuracy"),
            "interactor_build": ("interactor_ready", "interaction_scenarios"),
            "problem_pack_polygon": ("packaged",),
        }
        for key in resets.get(tool_name, ()):
            state[key] = 0 if key.endswith("rounds") else ({} if key.endswith("scenarios") else False)
        if tool_name == "solution_build":
            solution_type = tool_input.get("solution_type")
            if solution_type == "sol":
                state.update(
                    {
                        "sol_built": False,
                        "solution_analyzed": False,
                        "std_audited": False,
                        "brute_audited": False,
                        "std_claimed_complexity": None,
                    }
                )
            elif solution_type == "brute":
                state.update({"brute_built": False, "brute_audited": False})
        elif tool_name == "problem_validate":
            state.update(
                {
                    "statement_validated": False,
                    "sample_files_validated": False,
                    "validation_passed": False,
                }
            )
        if tool_name in {"validator_build", "checker_build"}:
            state["validator_accuracy" if tool_name == "validator_build" else "checker_accuracy"] = None
    elif tool_name == "problem_create":
        state["created"] = True
        ptype = data.get("problem_type") or tool_input.get("problem_type")
        if ptype is None:
            ptype = "interactive" if bool(tool_input.get("interactive", False)) else "standard"
        state["problem_type"] = ptype
        state["interactive"] = ptype == "interactive"
        state["oracle_mode"] = data.get("oracle_mode") or tool_input.get("oracle_mode") or state.get("oracle_mode") or "brute"
        if "protocol_mode" in data:
            state["protocol_mode"] = data["protocol_mode"]
        elif state.get("protocol_mode") is None:
            if ptype == "interactive":
                state["protocol_mode"] = tool_input.get("protocol_mode") or "interactive_1v1"
            elif ptype == "communication":
                state["protocol_mode"] = tool_input.get("protocol_mode") or "run_twice"
            else:
                state["protocol_mode"] = None
        state.update(
            {
                "sol_built": False,
                "brute_built": False,
                "solution_analyzed": False,
                "std_audited": False,
                "brute_audited": False,
                "std_claimed_complexity": None,
                "validator_ready": False,
                "validator_accuracy": None,
                "generator_built": False,
                "checker_ready": False,
                "checker_accuracy": None,
                "interactor_ready": False,
                "interaction_scenarios": {},
                "statement_dirty": False,
            }
        )
    elif tool_name == "problem_build_all" and success:
        compiled = data.get("compiled", {})
        if isinstance(compiled, dict):
            if "solutions/sol.cpp" in compiled:
                state.update(
                    {
                        "sol_built": True,
                        "solution_analyzed": False,
                        "std_audited": False,
                        "brute_audited": False,
                        "std_claimed_complexity": None,
                    }
                )
            if "solutions/brute.cpp" in compiled:
                state.update({"brute_built": True, "brute_audited": False})
            if "files/gen.cpp" in compiled:
                state["generator_built"] = True
    elif tool_name == "solution_build":
        solution_type = tool_input.get("solution_type")
        if solution_type == "sol":
            state.update(
                {
                    "sol_built": True,
                    "solution_analyzed": False,
                    "std_audited": False,
                    "brute_audited": False,
                    "std_claimed_complexity": None,
                }
            )
        elif solution_type == "brute":
            state.update({"brute_built": True, "brute_audited": False})
    elif tool_name == "solution_analyze":
        solution_type = tool_input.get("solution_type", "sol")
        if solution_type == "sol":
            state.update({"solution_analyzed": True, "std_audited": False, "brute_audited": False})
            complexity = data.get("estimated_complexity") or data.get("final_complexity") or data.get("worst_case_complexity")
            if complexity is not None:
                state["std_complexity"] = complexity
            claimed = data.get("claimed_complexity") or tool_input.get("claimed_complexity")
            state["std_claimed_complexity"] = (
                claimed.strip() if isinstance(claimed, str) and claimed.strip() else None
            )
            if "recommended_stress_params" in data:
                state["recommended_stress_params"] = data["recommended_stress_params"]
    elif tool_name == "solution_audit_std":
        state["std_audited"] = True
        claimed = data.get("claimed_complexity") or tool_input.get("claimed_complexity")
        if isinstance(claimed, str) and claimed.strip():
            state["std_claimed_complexity"] = claimed.strip()
    elif tool_name == "solution_audit_brute":
        state["brute_audited"] = True
        for key in ("brute_complexity", "recommended_stress_params"):
            if key in data:
                state[key] = data[key]
    elif tool_name == "validator_build":
        accuracy = data.get("accuracy")
        state["validator_accuracy"] = accuracy
        parsed = _finite_ratio(accuracy)
        state["validator_ready"] = parsed is not None and parsed >= 0.9
    elif tool_name == "generator_build":
        state["generator_built"] = True
    elif tool_name == "stress_test_run":
        state["stress_completed_rounds"] = data.get("completed_rounds", 0)
        state["stress_total_rounds"] = data.get("total_rounds", 0)
        state["stress_passed"] = success and _stress_rounds_complete(data)
    elif tool_name == "checker_build":
        accuracy = data.get("accuracy")
        state["checker_accuracy"] = accuracy
        parsed = _finite_ratio(accuracy)
        state["checker_ready"] = parsed is not None and parsed >= 0.9
    elif tool_name == "interactor_build":
        scenarios = data.get("interaction_scenarios", {})
        state["interaction_scenarios"] = scenarios if isinstance(scenarios, dict) else {}
        pass_rate = _finite_ratio(data.get("pass_rate", 0))
        fail_rate = _finite_ratio(data.get("fail_rate", 0))
        scenario_ready = _interaction_scenarios_ready(state["interaction_scenarios"])
        state["interactor_ready"] = scenario_ready or (
            pass_rate == 1.0 and fail_rate is not None and fail_rate >= 0.8
        )
        state["interactor_pass_rate"] = pass_rate
        state["interactor_fail_rate"] = fail_rate
    elif tool_name == "problem_pack_polygon":
        state["packaged"] = True
    _append_history(state, tool_name, success, data, "post")
    save_workflow_state(problem_dir, state)
    return state


def blocked_result(tool_name: str, violations: list[GateViolation]) -> dict[str, Any]:
    return {
        "gate_blocked": True,
        "tool": tool_name,
        "blocking_issues": [{"gate": v.gate, "reason": v.reason} for v in violations],
        "next_actions": [v.next_action for v in violations],
    }
