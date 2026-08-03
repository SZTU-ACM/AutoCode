"""Claude Code hook adapter for the host-independent workflow engine.

Hooks provide early feedback and session context.  The MCP server calls the
same enforcement functions, so a client without Claude hooks cannot bypass the
workflow gates.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from hook_payload import (  # noqa: E402
    deny,
    get_problem_dir,
    load_payload,
    parse_tool_result,
    tool_short_name,
)
from hook_state import infer_state, load_state, save_state  # noqa: E402

from autocode_mcp.workflow.enforcement import apply_result, preflight  # noqa: E402

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
    problem_dir = get_problem_dir(payload)
    if not problem_dir or short_name == "problem_create":
        return 0
    # The server remains strict.  The hook tolerates legacy/minimal fixtures so
    # it can still provide an early state-based hint before the server response.
    violations = preflight(
        short_name,
        problem_dir,
        payload.get("tool_input", {}),
        strict_manifest=False,
    )
    if violations:
        deny("; ".join(f"{item.gate}: {item.reason}" for item in violations))
    return 0


def post_tool(payload: dict[str, Any]) -> int:
    """Keep the legacy hook entry point working for older Claude installs.

    New installations omit PostToolUse because the MCP server persists this
    transition itself.  The adapter remains useful when an older hook config
    invokes it directly and deliberately delegates all state changes to the
    shared implementation.
    """
    short_name = tool_short_name(payload.get("tool_name", ""))
    problem_dir = get_problem_dir(payload)
    if not problem_dir:
        return 0
    success, data = parse_tool_result(payload)
    apply_result(problem_dir, short_name, payload.get("tool_input", {}), success, data)
    return 0


def session_start() -> int:
    additional_context = (
        "AutoCode workflow active for Claude Code. The MCP server enforces the same gates for every host: "
        "problem_create -> solution_build(sol) -> solution_build(brute) -> solution_analyze -> "
        "solution_audit_std/solution_audit_brute -> validator_build or interactor_build -> generator_build -> "
        "stress_test_run(completed_rounds == total_rounds) -> problem_validate -> problem_generate_tests -> "
        "problem_verify_tests -> problem_pack_polygon. Complete the blocking prerequisite when a workflow gate denies a call."
    )
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": additional_context}}, ensure_ascii=False))
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
