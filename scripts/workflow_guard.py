from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIR_NAME = ".autocode-workflow"
STATE_FILE_NAME = "state.json"
MANIFEST_FILE_NAME = "autocode.json"
DEFAULT_QUALITY_GATES = {
    "require_stress_passed": True,
    "require_validation_passed": True,
    "require_tests_verified": True,
    "require_limit_semantics": True,
    "require_wrong_solution_kill": False,
    "require_validator_check": True,
    "min_limit_case_ratio": 0.5,
}


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def load_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        payload = recover_hook_payload(raw) or {}
        payload["_malformed_payload"] = True
        return payload


def recover_hook_payload(raw: str) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    tool_name = extract_json_string_field(raw, "tool_name")
    if tool_name:
        payload["tool_name"] = tool_name

    tool_input = extract_json_field(raw, "tool_input")
    if isinstance(tool_input, dict):
        payload["tool_input"] = tool_input

    tool_response = extract_json_field(raw, "tool_response")
    if tool_response is None:
        tool_response = recover_tool_response(raw)
    if tool_response is not None:
        payload["tool_response"] = tool_response

    if "tool_name" in payload or "tool_input" in payload:
        return payload
    return None


def extract_json_string_field(raw: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if not match:
        return None
    try:
        parsed = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        return match.group(1)
    return parsed if isinstance(parsed, str) else None


def extract_json_field(raw: str, key: str) -> Any:
    key_index = raw.find(f'"{key}"')
    if key_index < 0:
        return None
    colon_index = raw.find(":", key_index)
    if colon_index < 0:
        return None
    value_index = colon_index + 1
    while value_index < len(raw) and raw[value_index].isspace():
        value_index += 1
    if value_index >= len(raw):
        return None

    if raw[value_index] in "{[":
        candidate = balanced_json_value(raw, value_index)
        if candidate is None:
            return None
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw[value_index:])
    except json.JSONDecodeError:
        return None
    return parsed


def balanced_json_value(raw: str, start: int) -> str | None:
    opening = raw[start]
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    quote = ""
    escaped = False
    for index in range(start + 1, len(raw)):
        current = raw[index]
        if escaped:
            escaped = False
            continue
        if current == "\\" and quote:
            escaped = True
            continue
        if quote:
            if current == quote:
                quote = ""
            continue
        if current == '"':
            quote = current
            continue
        if current in "{[":
            stack.append("}" if current == "{" else "]")
            continue
        if current in "}]":
            if current != stack[-1]:
                return None
            stack.pop()
            if not stack:
                return raw[start : index + 1]
    return None


def recover_tool_response(raw: str) -> dict[str, Any] | None:
    for snippet in recovery_snippets(raw):
        recovered = recover_tool_response_snippet(snippet)
        if recovered is not None:
            return recovered
    return None


def recovery_snippets(raw: str) -> list[str]:
    snippets: list[str] = []
    stripped = raw.strip()
    if looks_like_direct_tool_result(stripped):
        snippets.append(stripped)
    for key in ("structuredContent", "structured_content", "tool_response"):
        key_index = raw.find(f'"{key}"')
        if key_index < 0:
            continue
        colon_index = raw.find(":", key_index)
        if colon_index < 0:
            continue
        value_index = colon_index + 1
        while value_index < len(raw) and raw[value_index].isspace():
            value_index += 1
        if value_index < len(raw):
            snippets.append(raw[value_index:])
    return snippets


def looks_like_direct_tool_result(raw: str) -> bool:
    return bool(re.match(r'^\s*\{\s*"success"\s*:', raw))


def recover_tool_response_snippet(raw: str) -> dict[str, Any] | None:
    success = extract_top_level_success_field(raw)
    data = extract_json_field(raw, "data")
    if not isinstance(data, dict):
        data = {}
        for key in (
            "accuracy",
            "completed_rounds",
            "total_rounds",
            "pass_rate",
            "fail_rate",
            "scenario_accuracy",
        ):
            value = extract_number_field(raw, key)
            if value is not None:
                data[key] = value
        passed = extract_bool_field(raw, "passed")
        if passed is not None:
            data["passed"] = passed
    if success is None and not data:
        return None
    return {"structuredContent": {"success": bool(success), "data": data}}


def extract_top_level_success_field(raw: str) -> bool | None:
    match = re.match(r'\s*\{\s*"success"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def extract_bool_field(raw: str, key: str) -> bool | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(true|false)', raw, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).lower() == "true"


def extract_number_field(raw: str, key: str) -> int | float | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(-?\d+(?:\.\d+)?)', raw)
    if not match:
        return None
    text = match.group(1)
    return float(text) if "." in text else int(text)


def tool_short_name(tool_name: str) -> str:
    if tool_name.startswith("mcp__autocode__"):
        return tool_name[len("mcp__autocode__"):]
    return tool_name


def get_problem_dir(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input", {})
    problem_dir = tool_input.get("problem_dir")
    if isinstance(problem_dir, str) and problem_dir.strip():
        return problem_dir
    return None


def state_file(problem_dir: str) -> Path:
    return Path(problem_dir) / STATE_DIR_NAME / STATE_FILE_NAME


def manifest_file(problem_dir: str) -> Path:
    return Path(problem_dir) / MANIFEST_FILE_NAME


def load_manifest(problem_dir: str) -> dict[str, Any]:
    path = manifest_file(problem_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _is_manifest_valid_for_created(manifest: dict[str, Any]) -> bool:
    problem_name = manifest.get("problem_name")
    return isinstance(problem_name, str) and bool(problem_name.strip())


def _extract_quality_gates(manifest: dict[str, Any]) -> dict[str, Any]:
    configured = manifest.get("quality_gates")
    if not isinstance(configured, dict):
        configured = {}
    gates = dict(DEFAULT_QUALITY_GATES)
    for key in DEFAULT_QUALITY_GATES:
        if key in configured:
            gates[key] = configured[key]
    try:
        ratio = float(gates.get("min_limit_case_ratio", 0.5))
    except (TypeError, ValueError):
        ratio = 0.5
    gates["min_limit_case_ratio"] = min(1.0, max(0.0, ratio))
    return gates


def infer_state(problem_dir: str) -> dict[str, Any]:
    root = Path(problem_dir)
    manifest = load_manifest(problem_dir)
    is_interactive = bool(manifest.get("interactive", False))
    return {
        "problem_dir": str(root),
        "created": (
            root.exists()
            and (root / "files").exists()
            and (root / "solutions").exists()
            and _is_manifest_valid_for_created(manifest)
        ),
        "interactive": is_interactive,
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
        "statement_validated": False,
        "sample_files_validated": False,
        "validation_passed": False,
        "tests_generated": False,
        "generated_test_count": 0,
        "tests_verified": False,
        "verify_signals": {},
        "packaged": (root / "problem.xml").exists(),
        "quality_gates": _extract_quality_gates(manifest),
        "history": [],
    }


def load_state(problem_dir: str) -> dict[str, Any]:
    path = state_file(problem_dir)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            loaded = infer_state(problem_dir)
    else:
        loaded = infer_state(problem_dir)
    manifest = load_manifest(problem_dir)
    loaded["interactive"] = bool(manifest.get("interactive", loaded.get("interactive", False)))
    loaded["quality_gates"] = _extract_quality_gates(manifest)
    return loaded


def save_state(problem_dir: str, state: dict[str, Any]) -> None:
    path = state_file(problem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_tool_result(payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    tool_response = payload.get("tool_response")
    parsed = parse_tool_response_payload(tool_response)
    if isinstance(parsed, dict):
        return bool(parsed.get("success")), parsed.get("data", {}) or {}
    return False, {}


def parse_tool_response_payload(tool_response: Any) -> dict[str, Any] | None:
    if isinstance(tool_response, dict):
        for key in ("structuredContent", "structured_content"):
            structured = tool_response.get(key)
            if isinstance(structured, dict):
                return structured
        if "success" in tool_response:
            return tool_response
        content = tool_response.get("content")
        for text in iter_content_texts(content):
            parsed = parse_json_object_text(text)
            if isinstance(parsed, dict):
                return parsed
    elif isinstance(tool_response, str):
        parsed = parse_json_object_text(tool_response)
        if isinstance(parsed, dict):
            return parsed
        recovered = recover_tool_response(tool_response)
        if isinstance(recovered, dict):
            return parse_tool_response_payload(recovered)
    elif isinstance(tool_response, list):
        for text in iter_content_texts(tool_response):
            parsed = parse_json_object_text(text)
            if isinstance(parsed, dict):
                return parsed
            recovered = recover_tool_response(text)
            if isinstance(recovered, dict):
                return parse_tool_response_payload(recovered)
    return None


def iter_content_texts(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for item in content:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return texts


def parse_json_object_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    parsed_objects = []
    for candidate in [stripped, *extract_json_objects(stripped)]:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed_objects.append(parsed)
    for parsed in reversed(parsed_objects):
        if is_tool_result_object(parsed):
            return parsed
    return None


def is_tool_result_object(value: dict[str, Any]) -> bool:
    if "success" in value:
        return isinstance(value.get("success"), bool)
    if isinstance(value.get("structuredContent"), dict):
        return True
    if isinstance(value.get("structured_content"), dict):
        return True
    return False


def extract_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        quote = ""
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if escaped:
                escaped = False
                continue
            if current == "\\":
                escaped = bool(quote)
                continue
            if quote:
                if current == quote:
                    quote = ""
                continue
            if current in {'"', "'"}:
                quote = current
                continue
            if current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    objects.append(text[start : index + 1])
                    break
    return objects


def deny(reason: str) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


Gate = tuple[Callable[[dict[str, Any], dict[str, Any]], bool], str]


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
