from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil
import yaml


@dataclass
class ResourceLimit:
    timeout_sec: int = 30
    memory_mb: int = 256


@dataclass
class ProblemConfig:
    time_limit: int | None = None
    memory_limit: int | None = None


def get_available_memory_mb() -> int:
    return int(psutil.virtual_memory().available / 1024 / 1024)


def load_problem_config(problem_dir: Path) -> ProblemConfig:
    config_path = problem_dir / "problem.yaml"
    if not config_path.exists():
        return ProblemConfig()

    with config_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return ProblemConfig(
        time_limit=data.get("time_limit"),
        memory_limit=data.get("memory_limit"),
    )


def get_resource_limit(
    problem_dir: str,
    solution_type: Literal["sol", "brute"],
    timeout: int | None = None,
    memory_mb: int | None = None,
) -> ResourceLimit:
    if solution_type == "brute":
        default_timeout = 60
        default_memory = get_available_memory_mb()
    else:
        default_timeout = 2
        default_memory = 256

    if solution_type == "sol":
        config = load_problem_config(Path(problem_dir))
        if config.time_limit is not None:
            default_timeout = config.time_limit
        if config.memory_limit is not None:
            default_memory = config.memory_limit

    final_timeout = timeout if timeout is not None else default_timeout
    final_memory = memory_mb if memory_mb is not None else default_memory

    return ResourceLimit(timeout_sec=final_timeout, memory_mb=final_memory)
