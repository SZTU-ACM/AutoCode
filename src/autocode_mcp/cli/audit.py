from __future__ import annotations

import argparse
import asyncio
import json

from ..tools.audit import ProblemAuditTool


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a full AutoCode problem audit.")
    parser.add_argument("problem_dir", help="Problem directory")
    parser.add_argument("--mode", choices=["quick", "full"], default="full")
    parser.add_argument("--report", dest="report_path", help="Write JSON audit report to this path")
    parser.add_argument(
        "--no-difficulty",
        action="store_true",
        help="Skip difficulty signal generation",
    )
    args = parser.parse_args()

    async def run() -> dict:
        tool = ProblemAuditTool()
        result = await tool.execute(
            problem_dir=args.problem_dir,
            mode=args.mode,
            include_difficulty=not args.no_difficulty,
            report_path=args.report_path,
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
