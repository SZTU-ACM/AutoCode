from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from ..utils.platform import get_exe_extension
from ..workflow import (
    inspect_workspace_state,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
    reconcile_workspace_state,
)


def _resolve_safe_in_problem(problem_dir: Path, rel: str) -> tuple[Path | None, str | None]:
    """Resolve a manifest-declared path strictly inside ``problem_dir`` (blocking traversal and symlink escapes)."""
    normalized = rel.replace("\\", "/").strip()
    if not normalized or "\x00" in normalized:
        return None, f"invalid path: {rel!r}"
    raw_path = Path(normalized)
    if raw_path.is_absolute() or normalized.startswith("/") or any(part == ".." for part in raw_path.parts):
        return None, f"path escapes problem_dir: {rel}"
    root = problem_dir.resolve()
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, f"path resolves outside problem_dir (symlink escape): {rel}"
    return candidate, None


def _check_paths(problem_dir: Path, manifest: dict) -> list[str]:
    missing: list[str] = []
    for key in ("statement_path", "tutorial_path"):
        rel = manifest.get(key)
        if isinstance(rel, str) and rel:
            resolved, err = _resolve_safe_in_problem(problem_dir, rel)
            if err is not None:
                missing.append(f"{rel} ({err})")
            elif resolved is None or not resolved.exists():
                missing.append(rel)
    return missing


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass
    parser = argparse.ArgumentParser(description="Verify AutoCode workspace contract quickly.")
    parser.add_argument("problem_dir", help="Problem directory")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Include full unified MCP workflow gate status and binary freshness summary.",
    )
    parser.add_argument(
        "--strict-gates",
        action="store_true",
        help="Exit non-zero if MCP workflow quality/pack gates or platform parity checks fail.",
    )
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Reconcile .autocode/runtime.json against disk binaries/platform without fabricating passes.",
    )
    args = parser.parse_args()

    problem_dir = Path(args.problem_dir).resolve()
    try:
        manifest_model = load_manifest(str(problem_dir))
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "problem_dir": str(problem_dir),
                    "error": f"invalid manifest.json: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 1
    if manifest_model is None:
        print(json.dumps({"success": False, "error": "manifest.json not found"}, ensure_ascii=False))
        return 1
    manifest = manifest_model.model_dump(mode="json")
    missing = _check_paths(problem_dir, manifest)
    spj = bool(manifest.get("special_judge", False))
    spj_warnings: list[str] = []
    if manifest_uses_testlib_checker(manifest_model):
        cpp = problem_dir / "files" / "checker.cpp"
        exe = problem_dir / "files" / f"checker{get_exe_extension()}"
        if not cpp.is_file():
            spj_warnings.append("checker workflow (special_judge + stress_comparison=checker) requires files/checker.cpp")
        elif not exe.is_file():
            spj_warnings.append(
                "checker workflow requires compiled checker (run checker_build); missing files/checker"
                f"{get_exe_extension()}"
            )

    protocol_warnings: list[str] = []
    protocol_mode = manifest_model.protocol.mode if manifest_model.protocol else None
    if manifest_is_communication(manifest_model) and manifest_model.protocol is not None:
        if protocol_mode == "grader_linked":
            grader_rel = manifest_model.protocol.grader_file or "files/grader.cpp"
            grader_path, grader_err = _resolve_safe_in_problem(problem_dir, grader_rel)
            if grader_err is not None:
                protocol_warnings.append(f"invalid grader_file: {grader_err}")
            elif grader_path is None or not grader_path.is_file():
                protocol_warnings.append(f"grader_linked communication problem missing {grader_rel}")

            if manifest_model.protocol.header_file:
                hdr_rel = manifest_model.protocol.header_file
                hdr_path, hdr_err = _resolve_safe_in_problem(problem_dir, hdr_rel)
                if hdr_err is not None:
                    protocol_warnings.append(f"invalid header_file: {hdr_err}")
                elif hdr_path is None or not hdr_path.is_file():
                    protocol_warnings.append(f"grader_linked communication problem missing {hdr_rel}")
        else:
            comm_rel = manifest_model.protocol.communicator_file or "files/communicator.cpp"
            comm_path, comm_err = _resolve_safe_in_problem(problem_dir, comm_rel)
            fallback_interactor = problem_dir / "files" / "interactor.cpp"
            if comm_err is not None:
                protocol_warnings.append(f"invalid communicator_file: {comm_err}")
            elif (comm_path is None or not comm_path.is_file()) and not fallback_interactor.is_file():
                protocol_warnings.append(
                    f"communication problem ({protocol_mode}) missing {comm_rel} (or files/interactor.cpp)"
                )

    reconcile_report = None
    if args.reconcile:
        reconcile_report = reconcile_workspace_state(str(problem_dir))
        workspace_inspection = reconcile_report["inspection"]
    else:
        workspace_inspection = inspect_workspace_state(str(problem_dir))

    base_ok = len(missing) == 0 and len(spj_warnings) == 0 and len(protocol_warnings) == 0
    gate_summary = workspace_inspection["gate_summary"]
    gates_ok = bool(gate_summary.get("pack_ready", False))
    overall_ok = base_ok and (gates_ok if args.strict_gates else True)

    result = {
        "success": overall_ok,
        "problem_dir": str(problem_dir),
        "problem_type": manifest_model.problem_type,
        "oracle_mode": manifest_model.oracle_mode,
        "protocol_mode": protocol_mode,
        "interactive": manifest_is_interactive(manifest_model),
        "special_judge": spj,
        "stress_comparison": manifest.get("stress_comparison", "exact"),
        "case_plan_count": len(manifest.get("case_plan", [])),
        "subtask_count": len(manifest_model.subtasks),
        "missing_paths": missing,
        "spj_warnings": spj_warnings,
        "protocol_warnings": protocol_warnings,
        "workflow_state_present": workspace_inspection["workflow_state_present"],
        "build_platform": workspace_inspection["build_platform"],
        "current_platform": workspace_inspection["current_platform"],
        "platform_mismatch": workspace_inspection["platform_mismatch"],
        "binary_status": workspace_inspection["binary_status"],
        "stale_reasons": workspace_inspection["stale_reasons"],
        "gate_summary": gate_summary,
    }
    if args.status:
        from ..workflow import load_workflow_state

        wf_state = load_workflow_state(str(problem_dir))
        result["workflow_flags"] = {
            k: wf_state.get(k)
            for k in (
                "created",
                "sol_built",
                "brute_built",
                "solution_analyzed",
                "std_audited",
                "brute_audited",
                "validator_ready",
                "generator_built",
                "checker_ready",
                "interactor_ready",
                "stress_passed",
                "validation_passed",
                "tests_generated",
                "generated_test_count",
                "tests_verified",
                "packaged",
            )
        }
    if reconcile_report is not None:
        result["reconcile"] = {
            "reconciled": reconcile_report["reconciled"],
            "actions": reconcile_report["actions"],
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
