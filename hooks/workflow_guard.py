from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


STATE_DIR_NAME = ".autocode-workflow"
STATE_FILE_NAME = "state.json"


def load_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    return json.loads(raw)


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


def infer_state(problem_dir: str) -> dict[str, Any]:
    root = Path(problem_dir)
    return {
        "problem_dir": str(root),
        "created": root.exists() and (root / "files").exists() and (root / "solutions").exists(),
        "sol_built": (root / "solutions" / "sol.cpp").exists() or any(root.glob("solutions/sol.*")),
        "brute_built": (root / "solutions" / "brute.cpp").exists() or any(root.glob("solutions/brute.*")),
        "validator_ready": (root / "files" / "val.cpp").exists() or any(root.glob("files/val.*")),
        "generator_built": (root / "files" / "gen.cpp").exists() or any(root.glob("files/gen.*")),
        "stress_passed": False,
        "checker_ready": (root / "files" / "checker.cpp").exists() or any(root.glob("files/checker.*")),
        "tests_generated": any((root / "tests").glob("*.in")) if (root / "tests").exists() else False,
        "packaged": (root / "problem.xml").exists(),
    }


def load_state(problem_dir: str) -> dict[str, Any]:
    path = state_file(problem_dir)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return infer_state(problem_dir)


def save_state(problem_dir: str, state: dict[str, Any]) -> None:
    path = state_file(problem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_tool_result(payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    tool_response = payload.get("tool_response")
    if isinstance(tool_response, dict):
        structured = tool_response.get("structuredContent")
        if isinstance(structured, dict):
            return bool(structured.get("success")), structured.get("data", {}) or {}
        content = tool_response.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text")
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                    return bool(parsed.get("success")), parsed.get("data", {}) or {}
                except json.JSONDecodeError:
                    return False, {}
    return False, {}


def deny(reason: str) -> None:
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def pre_tool(payload: dict[str, Any]) -> int:
    short_name = tool_short_name(payload.get("tool_name", ""))
    problem_dir = get_problem_dir(payload)

    if short_name == "problem_create":
        return 0

    if not problem_dir:
        return 0

    state = load_state(problem_dir)
    reasons = {
        "solution_build_brute": "必须先构建标准解 sol，再构建 brute。",
        "solution_build": "必须先运行 problem_create 创建题目目录。",
        "validator_build": "必须先完成 problem_create、solution_build(sol)、solution_build(brute)。",
        "generator_build": "必须先完成 validator_build，并让 validator 达到可用状态。",
        "stress_test_run": "必须先完成 validator_build 和 generator_build，然后再进行 stress_test_run。",
        "checker_build": "必须先通过 stress_test_run，再构建 checker。",
        "problem_generate_tests": "必须先通过 stress_test_run，才能生成最终测试数据。",
        "problem_pack_polygon": "必须先生成最终测试数据，再进行打包。",
    }

    tool_input = payload.get("tool_input", {})
    if short_name == "solution_build":
        solution_type = tool_input.get("solution_type")
        if not state["created"]:
            deny(reasons["solution_build"])
            return 0
        if solution_type == "brute" and not state["sol_built"]:
            deny(reasons["solution_build_brute"])
        return 0

    if short_name == "validator_build" and not (state["created"] and state["sol_built"] and state["brute_built"]):
        deny(reasons["validator_build"])
        return 0

    if short_name == "generator_build" and not state["validator_ready"]:
        deny(reasons["generator_build"])
        return 0

    if short_name == "stress_test_run" and not (
        state["sol_built"] and state["brute_built"] and state["validator_ready"] and state["generator_built"]
    ):
        deny(reasons["stress_test_run"])
        return 0

    if short_name == "checker_build" and not state["stress_passed"]:
        deny(reasons["checker_build"])
        return 0

    if short_name == "problem_generate_tests" and not state["stress_passed"]:
        deny(reasons["problem_generate_tests"])
        return 0

    if short_name == "problem_pack_polygon" and not state["tests_generated"]:
        deny(reasons["problem_pack_polygon"])
        return 0

    return 0


def post_tool(payload: dict[str, Any]) -> int:
    short_name = tool_short_name(payload.get("tool_name", ""))
    problem_dir = get_problem_dir(payload)
    if not problem_dir:
        return 0

    success, data = parse_tool_result(payload)
    if not success:
        return 0

    state = load_state(problem_dir)

    if short_name == "problem_create":
        state["created"] = True
    elif short_name == "solution_build":
        solution_type = payload.get("tool_input", {}).get("solution_type")
        if solution_type == "sol":
            state["sol_built"] = True
        elif solution_type == "brute":
            state["brute_built"] = True
    elif short_name == "validator_build":
        state["validator_ready"] = data.get("accuracy", 1.0) >= 0.9
    elif short_name == "generator_build":
        state["generator_built"] = True
    elif short_name == "stress_test_run":
        state["stress_passed"] = data.get("completed_rounds") == data.get("total_rounds")
    elif short_name == "checker_build":
        state["checker_ready"] = data.get("accuracy", 1.0) >= 0.9
    elif short_name == "problem_generate_tests":
        generated_tests = data.get("generated_tests", [])
        state["tests_generated"] = bool(generated_tests)
    elif short_name == "problem_pack_polygon":
        state["packaged"] = True

    save_state(problem_dir, state)
    return 0


def session_start() -> int:
    reminder = (
        "AutoCode plugin active. Enforce this workflow: "
        "problem_create -> solution_build(sol) -> solution_build(brute) -> "
        "validator_build -> generator_build -> stress_test_run -> "
        "checker_build(if needed) -> problem_generate_tests -> problem_pack_polygon."
    )
    print(reminder)
    return 0


def main() -> int:
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
