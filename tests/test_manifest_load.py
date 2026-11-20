"""manifest.json 读取与 manifest 归一化。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from autocode_mcp.workflow import load_manifest
from autocode_mcp.workflow.models import AutoCodeManifest


def test_load_manifest_bad_utf8_raises_value_error() -> None:
    with tempfile.TemporaryDirectory() as d:
        autocode_dir = Path(d) / ".autocode"
        autocode_dir.mkdir()
        p = autocode_dir / "manifest.json"
        p.write_bytes(b"\xff\xfe\xfd")
        with pytest.raises(ValueError, match="cannot read manifest.json"):
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
    assert m.problem_type == "spj"


def test_manifest_legacy_interactive_derives_problem_type_and_protocol() -> None:
    m = AutoCodeManifest(problem_name="guess", interactive=True)
    assert m.problem_type == "interactive"
    assert m.interactive is True
    assert m.protocol is not None
    assert m.protocol.mode == "interactive_1v1"


def test_manifest_communication_modes_and_pass_headers() -> None:
    m = AutoCodeManifest(problem_name="comm", problem_type="communication")
    assert m.interactive is False
    assert m.protocol is not None
    assert m.protocol.mode == "run_twice"
    assert m.protocol.pass_headers == ["first", "second"]

    m3 = AutoCodeManifest(
        problem_name="comm3",
        problem_type="communication",
        protocol={"mode": "run_twice", "passes": 3, "max_bits": 64},
    )
    assert m3.protocol is not None
    assert m3.protocol.pass_headers == ["pass_1", "pass_2", "pass_3"]
    assert m3.protocol.max_bits == 64

    with pytest.raises(ValueError, match="conflicting"):
        AutoCodeManifest(problem_name="bad", problem_type="communication", interactive=True)
    with pytest.raises(ValueError, match="conflicting"):
        AutoCodeManifest(problem_name="bad2", problem_type="standard", interactive=True)
    with pytest.raises(ValueError, match="protocol.mode"):
        AutoCodeManifest(
            problem_name="bad3",
            problem_type="communication",
            protocol={"mode": "interactive_1v1"},
        )
    with pytest.raises(ValueError, match="pass_headers"):
        AutoCodeManifest(
            problem_name="bad4",
            problem_type="communication",
            protocol={"mode": "run_twice", "passes": 3, "pass_headers": ["first", "second"]},
        )


def test_manifest_subtasks_validation() -> None:
    m = AutoCodeManifest(
        problem_name="sub",
        subtasks=[
            {"id": 1, "score": 30, "groups": ["small"]},
            {"id": 2, "score": 70, "groups": ["full"], "dependencies": [1]},
        ],
    )
    assert len(m.subtasks) == 2

    with pytest.raises(ValueError, match="duplicate subtask id"):
        AutoCodeManifest(
            problem_name="dup",
            subtasks=[{"id": 1, "score": 50}, {"id": 1, "score": 50}],
        )
    with pytest.raises(ValueError, match="depend on itself"):
        AutoCodeManifest(
            problem_name="self",
            subtasks=[{"id": 1, "score": 100, "dependencies": [1]}],
        )
    with pytest.raises(ValueError, match="unknown subtask id"):
        AutoCodeManifest(
            problem_name="unknown",
            subtasks=[{"id": 1, "score": 100, "dependencies": [99]}],
        )
    with pytest.raises(ValueError, match="1-based"):
        AutoCodeManifest(
            problem_name="zero_idx",
            subtasks=[{"id": 1, "score": 100, "test_indices": [0]}],
        )
    with pytest.raises(ValueError, match="cyclic subtask dependency"):
        AutoCodeManifest(
            problem_name="cycle",
            subtasks=[
                {"id": 1, "score": 50, "dependencies": [2]},
                {"id": 2, "score": 50, "dependencies": [1]},
            ],
        )


def test_manifest_rejects_bool_in_numeric_fields_and_conflicting_flags() -> None:
    from autocode_mcp.workflow import default_manifest

    with pytest.raises(ValueError, match="not bool"):
        AutoCodeManifest(
            problem_name="b1",
            problem_type="communication",
            protocol={"mode": "run_twice", "passes": True},
        )
    with pytest.raises(ValueError, match="not bool"):
        AutoCodeManifest(
            problem_name="b2",
            problem_type="communication",
            protocol={"mode": "run_twice", "max_bits": True},
        )
    with pytest.raises(ValueError, match="not bool"):
        AutoCodeManifest(problem_name="b3", subtasks=[{"id": True, "score": 50}])
    with pytest.raises(ValueError, match="not bool"):
        AutoCodeManifest(problem_name="b4", subtasks=[{"id": 1, "score": False}])
    with pytest.raises(ValueError, match="not bool"):
        AutoCodeManifest(problem_name="b5", subtasks=[{"id": 1, "score": 50, "test_indices": [True]}])

    with pytest.raises(ValueError, match="conflicting"):
        AutoCodeManifest(problem_name="c1", interactive=True, special_judge=True)
    with pytest.raises(ValueError, match="conflicting"):
        AutoCodeManifest(problem_name="c2", problem_type="spj", special_judge=False)
    with pytest.raises(ValueError, match="conflicting"):
        AutoCodeManifest(problem_name="c3", problem_type="standard", special_judge=True)
    with pytest.raises(ValueError, match="protocol is only allowed"):
        AutoCodeManifest(problem_name="c4", problem_type="standard", protocol={"mode": "run_twice"})
    with pytest.raises(ValueError, match="interactor_only"):
        AutoCodeManifest(problem_name="c5", problem_type="standard", oracle_mode="interactor_only")
    with pytest.raises(ValueError, match="relative path|traversal"):
        AutoCodeManifest(
            problem_name="c6",
            problem_type="communication",
            protocol={"mode": "grader_linked", "grader_file": "../../etc/passwd"},
        )
    with pytest.raises(ValueError, match="relative path"):
        AutoCodeManifest(
            problem_name="c7",
            problem_type="communication",
            protocol={"mode": "run_twice", "communicator_file": "/etc/passwd"},
        )
    with pytest.raises(ValueError, match="protocol_mode"):
        default_manifest("bad", problem_type="standard", protocol_mode="run_twice")
    with pytest.raises(ValueError, match="conflicting"):
        default_manifest("bad2", interactive=True, problem_type="standard")


