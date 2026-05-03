"""autocode.json 读取与 manifest 归一化。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from autocode_mcp.workflow import load_manifest
from autocode_mcp.workflow.models import AutoCodeManifest


def test_load_manifest_bad_utf8_raises_value_error() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "autocode.json"
        p.write_bytes(b"\xff\xfe\xfd")
        with pytest.raises(ValueError, match="cannot read autocode.json"):
            load_manifest(d)


def test_manifest_normalizes_bidirectional_without_checker_workflow() -> None:
    m = AutoCodeManifest(problem_name="x", stress_checker_bidirectional=True)
    assert m.stress_checker_bidirectional is False


def test_manifest_keeps_bidirectional_with_checker_workflow() -> None:
    m = AutoCodeManifest(
        problem_name="x",
        special_judge=True,
        stress_comparison="checker",
        stress_checker_bidirectional=True,
    )
    assert m.stress_checker_bidirectional is True
