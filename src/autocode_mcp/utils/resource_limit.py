from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil

from ..workflow.manifest import load_manifest


@dataclass
class ResourceLimit:
    timeout_sec: int | float = 30
    memory_mb: int = 256


@dataclass
class ProblemConfig:
    time_limit: int | float | None = None
    memory_limit: int | None = None


def get_available_memory_mb() -> int:
    return int(psutil.virtual_memory().available / 1024 / 1024)


def load_problem_config(problem_dir: Path) -> ProblemConfig:
    """Load time/memory limits from `.autocode/manifest.json` if present."""
    manifest = load_manifest(str(problem_dir))
    if manifest is None:
        return ProblemConfig()

    time_limit: int | float | None = None
    if manifest.time_limit_ms > 0:
        if manifest.time_limit_ms % 1000 == 0:
            time_limit = manifest.time_limit_ms // 1000
        else:
            time_limit = manifest.time_limit_ms / 1000.0

    memory_limit: int | None = None
    if manifest.memory_limit_mb > 0:
        memory_limit = manifest.memory_limit_mb

    return ProblemConfig(
        time_limit=time_limit,
        memory_limit=memory_limit,
    )


def get_resource_limit(
    problem_dir: str,
    solution_type: Literal["sol", "brute", "wrong", "reference"],
    timeout: int | float | None = None,
    memory_mb: int | None = None,
) -> ResourceLimit:
    if solution_type == "brute":
        default_timeout: int | float = 60
        default_memory = get_available_memory_mb()
    else:
        default_timeout = 2
        default_memory = 256
        config = load_problem_config(Path(problem_dir))
        if config.time_limit is not None:
            default_timeout = config.time_limit
        if config.memory_limit is not None:
            default_memory = config.memory_limit

    final_timeout = timeout if timeout is not None else default_timeout
    final_memory = memory_mb if memory_mb is not None else default_memory

    return ResourceLimit(timeout_sec=final_timeout, memory_mb=final_memory)
