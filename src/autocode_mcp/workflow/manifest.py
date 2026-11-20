from __future__ import annotations

import json
from pathlib import Path

from ..runtime_store import RUNTIME_DIR_NAME
from .models import (
    AutoCodeManifest,
    CasePlanItem,
    OracleMode,
    ProblemType,
    ProtocolConfig,
    ProtocolMode,
    QualityGates,
    SolutionEntry,
)

MANIFEST_NAME = "manifest.json"

CANONICAL_PROBLEM_FILES: dict[str, str] = {
    "sol.cpp": "solutions/sol.cpp",
    "brute.cpp": "solutions/brute.cpp",
    "val.cpp": "files/val.cpp",
    "gen.cpp": "files/gen.cpp",
    "checker.cpp": "files/checker.cpp",
    "interactor.cpp": "files/interactor.cpp",
    "communicator.cpp": "files/communicator.cpp",
    "grader.cpp": "files/grader.cpp",
    "problem.h": "files/problem.h",
    "README.md": "statements/README.md",
    "tutorial.md": "statements/tutorial.md",
}


def manifest_path(problem_dir: str) -> Path:
    return Path(problem_dir) / RUNTIME_DIR_NAME / MANIFEST_NAME


def default_manifest(
    problem_name: str,
    interactive: bool = False,
    *,
    problem_type: ProblemType | None = None,
    protocol_mode: ProtocolMode | None = None,
    oracle_mode: OracleMode = "brute",
) -> AutoCodeManifest:
    if problem_type is not None and interactive and problem_type != "interactive":
        raise ValueError(
            f"conflicting problem_type={problem_type!r} and interactive=True"
        )
    resolved_type: ProblemType
    if problem_type is not None:
        resolved_type = problem_type
    elif interactive:
        resolved_type = "interactive"
    else:
        resolved_type = "standard"

    if protocol_mode is not None and resolved_type not in {"interactive", "communication"}:
        raise ValueError(
            f"protocol_mode={protocol_mode!r} is only valid for interactive or communication problems, not {resolved_type!r}"
        )

    protocol: ProtocolConfig | None = None
    if resolved_type == "interactive":
        protocol = ProtocolConfig(mode=protocol_mode or "interactive_1v1")
    elif resolved_type == "communication":
        mode = protocol_mode or "run_twice"
        protocol = ProtocolConfig(
            mode=mode,
            communicator_file="files/communicator.cpp" if mode != "grader_linked" else None,
            grader_file="files/grader.cpp" if mode == "grader_linked" else None,
            header_file="files/problem.h" if mode == "grader_linked" else None,
        )

    solutions = [
        SolutionEntry(name="sol", role="main", language="cpp", path="solutions/sol.cpp"),
    ]
    if oracle_mode == "brute":
        solutions.append(
            SolutionEntry(name="brute", role="brute", language="cpp", path="solutions/brute.cpp")
        )

    quality_gates = QualityGates(
        require_validator_check=resolved_type not in ("interactive", "communication"),
    )

    return AutoCodeManifest(
        problem_name=problem_name,
        problem_type=resolved_type,
        oracle_mode=oracle_mode,
        protocol=protocol,
        interactive=(resolved_type == "interactive"),
        special_judge=(resolved_type == "spj"),
        stress_comparison="checker" if resolved_type == "spj" else "exact",
        quality_gates=quality_gates,
        solutions=solutions,
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


def manifest_problem_type(manifest: AutoCodeManifest | None) -> ProblemType:
    if manifest is None:
        return "standard"
    return manifest.problem_type


def manifest_is_interactive(manifest: AutoCodeManifest | None) -> bool:
    if manifest is None:
        return False
    return manifest.problem_type == "interactive" or bool(manifest.interactive)


def manifest_is_communication(manifest: AutoCodeManifest | None) -> bool:
    if manifest is None:
        return False
    return manifest.problem_type == "communication"


def manifest_uses_protocol_judge(manifest: AutoCodeManifest | None) -> bool:
    return manifest_is_interactive(manifest) or manifest_is_communication(manifest)


def manifest_requires_brute(manifest: AutoCodeManifest | None) -> bool:
    if manifest is None:
        return True
    return manifest.oracle_mode == "brute"


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
        raise ValueError(f"cannot read manifest.json: {exc}") from exc
    return AutoCodeManifest.model_validate_json(content)


def save_manifest(problem_dir: str, manifest: AutoCodeManifest) -> Path:
    path = manifest_path(problem_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
