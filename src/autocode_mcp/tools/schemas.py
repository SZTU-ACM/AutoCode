"""
Pydantic input models for AutoCode MCP tools.

Each model is the single source of truth for a tool's ``input_schema``; the
schema is derived via ``input_schema_from_model`` (in ``base.py``) instead of a
hand-written JSON Schema dict that can drift from the ``execute`` signature.

Models are added per tool and migrated in waves (see change
autocode-perf-robustness-optimization, tasks 7.2/7.3).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProblemAuditInput(BaseModel):
    problem_dir: str
    mode: Literal["quick", "full"] = "full"
    include_difficulty: bool = True
    report_path: str | None = None


class FileReadInput(BaseModel):
    path: str
    problem_dir: str | None = None


class FileSaveInput(BaseModel):
    path: str
    content: str
    problem_dir: str | None = None


class StatementSample(BaseModel):
    input: str
    expected_output: str


class ProblemValidateInput(BaseModel):
    problem_dir: str
    validate_types: list[Literal["statement_samples", "sample_files", "all"]] = ["all"]
    statement_samples: list[StatementSample] | None = None
    tolerance: float = 1e-9
    timeout: int = 30
