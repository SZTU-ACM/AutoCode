from __future__ import annotations

import argparse
import asyncio
import json
import sys

from ..tools.audit import ProblemAuditTool


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except OSError:
                pass


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
    args = parser.parse_args()

    async def run() -> dict:
        tool = ProblemAuditTool()
        result = await tool.execute(
            problem_dir=args.problem_dir,
            mode=args.mode,
            include_difficulty=not args.no_difficulty,
            report_path=args.report_path,
            standalone=args.standalone,
            layout=args.layout,
            enable_mutation_testing=True if args.mutation_check else None,
        )
        return result.to_dict()

    output = asyncio.run(run())
    print(json.dumps(output, ensure_ascii=False))
    if not output.get("success"):
        return 1
    decision = output.get("data", {}).get("decision")
    return 0 if decision == "go" else 2


if __name__ == "__main__":
    raise SystemExit(main())
