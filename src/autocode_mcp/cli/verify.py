from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from ..workflow import load_manifest


def _check_paths(problem_dir: Path, manifest: dict) -> list[str]:
    missing: list[str] = []
    for key in ("statement_path", "tutorial_path"):
        rel = manifest.get(key)
        if isinstance(rel, str) and rel:
            if not (problem_dir / rel).exists():
                missing.append(rel)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AutoCode workspace contract quickly.")
    parser.add_argument("problem_dir", help="Problem directory")
    args = parser.parse_args()

    problem_dir = Path(args.problem_dir).resolve()
    try:
        manifest_model = load_manifest(str(problem_dir))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(
            json.dumps(
                {
                    "success": False,
                    "problem_dir": str(problem_dir),
                    "error": f"invalid autocode.json: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 1
    if manifest_model is None:
        print(json.dumps({"success": False, "error": "autocode.json not found"}, ensure_ascii=False))
        return 1
    manifest = manifest_model.model_dump(mode="json")
    missing = _check_paths(problem_dir, manifest)
    result = {
        "success": len(missing) == 0,
        "problem_dir": str(problem_dir),
        "interactive": manifest.get("interactive", False),
        "case_plan_count": len(manifest.get("case_plan", [])),
        "missing_paths": missing,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
