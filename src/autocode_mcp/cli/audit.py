from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from ..tools.audit import ProblemAuditTool
from ..utils.sandbox import (
    SUPPORTED_SANDBOX_CHOICES,
    NoopSandboxExecutor,
    SandboxUnavailableError,
    resolve_sandbox_executor,
    sandbox_context,
)
from .report_format import format_audit_markdown, format_audit_summary


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


def _emit_audit_output(output: dict[str, Any], output_format: str) -> None:
    if output_format == "markdown":
        print(format_audit_markdown(output))
    elif output_format == "summary":
        print(format_audit_summary(output))
    else:
        print(json.dumps(output, ensure_ascii=False))


def main() -> int:
    _configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Run a full AutoCode problem audit.")
    parser.add_argument("problem_dir", help="Problem directory")
    parser.add_argument("--mode", choices=["quick", "full"], default="full")
    parser.add_argument("--report", dest="report_path", help="Write JSON audit report to this path")
    parser.add_argument(
        "--no-difficulty",
        action="store_true",
        help="Skip difficulty signal generation",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Audit an arbitrary problem directory directly without requiring prior workflow state",
    )
    parser.add_argument(
        "--layout",
        choices=["auto", "autocode", "standard_cn", "polygon", "hydro"],
        default="auto",
        help="Directory layout adapter for standalone audits (default: auto)",
    )
    parser.add_argument(
        "--mutation-check",
        action="store_true",
        help="Run optional single-point mutation testing on the main solution during full audit",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Promote standalone completeness omissions (missing validator or wrong solutions) to blocking issues",
    )
    parser.add_argument(
        "--sandbox",
        choices=list(SUPPORTED_SANDBOX_CHOICES),
        default=None,
        help="Execution sandbox backend (auto|none|bwrap|nsjail|isolate|apple_container; default: none unless set in manifest)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "summary"],
        default="json",
        help="Stdout output format (default: json for backwards compatibility; use markdown for Problem CI)",
    )
    args = parser.parse_args()

    try:
        executor = resolve_sandbox_executor(args.sandbox) if args.sandbox is not None else None
    except SandboxUnavailableError as exc:
        _emit_audit_output({"success": False, "error": str(exc)}, args.format)
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if isinstance(executor, NoopSandboxExecutor) and executor.degraded:
        print(f"Warning: {executor.reason}", file=sys.stderr)

    async def run() -> dict[str, Any]:
        with sandbox_context(executor):
            tool = ProblemAuditTool()
            result = await tool.execute(
                problem_dir=args.problem_dir,
                mode=args.mode,
                include_difficulty=not args.no_difficulty,
                report_path=args.report_path,
                standalone=args.standalone,
                layout=args.layout,
                enable_mutation_testing=True if args.mutation_check else None,
                strict=args.strict,
            )
            return result.to_dict()

    output = asyncio.run(run())
    _emit_audit_output(output, args.format)
    if not output.get("success"):
        return 1
    decision = output.get("data", {}).get("decision")
    return 0 if decision == "go" else 2


if __name__ == "__main__":
    raise SystemExit(main())
