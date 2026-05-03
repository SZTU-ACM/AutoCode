from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from ..utils.platform import get_exe_extension
from ..workflow import load_manifest, manifest_uses_testlib_checker


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
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
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

    result = {
        "success": len(missing) == 0 and len(spj_warnings) == 0,
        "problem_dir": str(problem_dir),
        "interactive": manifest.get("interactive", False),
        "special_judge": spj,
        "stress_comparison": manifest.get("stress_comparison", "exact"),
        "case_plan_count": len(manifest.get("case_plan", [])),
        "missing_paths": missing,
        "spj_warnings": spj_warnings,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
