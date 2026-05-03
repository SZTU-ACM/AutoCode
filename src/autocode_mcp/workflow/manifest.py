from __future__ import annotations

import json
from pathlib import Path

from .models import AutoCodeManifest, CasePlanItem, SolutionEntry

MANIFEST_NAME = "autocode.json"


def manifest_path(problem_dir: str) -> Path:
    return Path(problem_dir) / MANIFEST_NAME


def default_manifest(problem_name: str, interactive: bool = False) -> AutoCodeManifest:
    return AutoCodeManifest(
        problem_name=problem_name,
        interactive=interactive,
        solutions=[
            SolutionEntry(name="sol", role="main", language="cpp", path="solutions/sol.cpp"),
            SolutionEntry(name="brute", role="brute", language="cpp", path="solutions/brute.cpp"),
        ],
        case_plan=[
            CasePlanItem(
                name="tiny-1",
                type="1",
                seed=1,
                group="sanity",
                purpose="basic correctness",
            ),
            CasePlanItem(
                name="random-1",
                type="2",
                seed=2,
                group="coverage",
                purpose="random coverage",
            ),
            CasePlanItem(
                name="extreme-1",
                type="3",
                seed=3,
                group="limit",
                purpose="edge constraints",
            ),
            CasePlanItem(
                name="tle-1",
                type="4",
                seed=4,
                group="limit",
                purpose="performance pressure",
            ),
        ],
    )


def manifest_uses_testlib_checker(manifest: AutoCodeManifest | None) -> bool:
    """是否按 testlib checker 路径做对拍、终测与（可选）样例校验。"""
    if manifest is None:
        return False
    return manifest.special_judge and manifest.stress_comparison == "checker"


def load_manifest(problem_dir: str) -> AutoCodeManifest | None:
    path = manifest_path(problem_dir)
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read autocode.json: {exc}") from exc
    return AutoCodeManifest.model_validate_json(content)


def save_manifest(problem_dir: str, manifest: AutoCodeManifest) -> Path:
    path = manifest_path(problem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
