"""Unit tests for hook payload parsing (scripts/hook_payload.py)."""

from __future__ import annotations

import io

from hook_payload import (
    extract_json_field,
    get_problem_dir,
    load_payload,
    parse_tool_response_payload,
    tool_short_name,
)


def test_tool_short_name_strips_prefix():
    assert tool_short_name("mcp__autocode__generator_build") == "generator_build"
    assert tool_short_name("generator_build") == "generator_build"


def test_get_problem_dir_reads_tool_input():
    assert get_problem_dir({"tool_input": {"problem_dir": "/p"}}) == "/p"
    # whitespace-only is rejected; non-empty (possibly padded) is accepted as-is
    assert get_problem_dir({"tool_input": {"problem_dir": "   "}}) is None
    assert get_problem_dir({"tool_input": {}}) is None
    assert get_problem_dir({}) is None


def test_load_payload_valid_json(monkeypatch):
    monkeypatch.setattr(
        "hook_payload.sys.stdin",
        io.StringIO('{"tool_name":"x","tool_input":{"problem_dir":"/p"}}'),
    )
    payload = load_payload()
    assert payload == {"tool_name": "x", "tool_input": {"problem_dir": "/p"}}
    assert "_malformed_payload" not in payload


def test_load_payload_malformed_recovers(monkeypatch):
    raw = '{"tool_name":"mcp__autocode__problem_pack_polygon","tool_input":{"problem_dir":"C:\\tmp\\p"'
    monkeypatch.setattr("hook_payload.sys.stdin", io.StringIO(raw))
    payload = load_payload()
    assert payload.get("tool_name") == "mcp__autocode__problem_pack_polygon"
    assert payload["_malformed_payload"] is True


def test_extract_json_field_scalar_and_object():
    raw = 'prefix {"key": "value", "n": 5, "obj": {"a": 1}} suffix'
    assert extract_json_field(raw, "key") == "value"
    assert extract_json_field(raw, "n") == 5
    assert extract_json_field(raw, "obj") == {"a": 1}
    assert extract_json_field(raw, "missing") is None


def test_parse_tool_response_payload_structured_content():
    assert parse_tool_response_payload(
        {"structuredContent": {"success": True, "data": {"x": 1}}}
    ) == {"success": True, "data": {"x": 1}}
    assert parse_tool_response_payload({"success": False}) == {"success": False}
    assert parse_tool_response_payload("not json") is None
