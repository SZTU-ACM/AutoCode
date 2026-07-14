"""Hook payload parsing.

Pure functions for reading and recovering the Claude Code hook payload
(``tool_name`` / ``tool_input`` / ``tool_response``). Kept free of state and gate
logic so it can be unit-tested in isolation.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any


def load_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        parsed: dict[str, Any] = json.loads(raw)
        return parsed
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
