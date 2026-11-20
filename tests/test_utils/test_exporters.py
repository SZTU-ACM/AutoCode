"""Tests for Polygon subtask XML, Hydro/QOJ exporters, and ProblemPackPolygonTool export_formats."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from autocode_mcp.runtime_store import AUDIT, TEST_MANIFEST, WORKFLOW, set_section
from autocode_mcp.tools.problem import ProblemPackPolygonTool, _build_problem_xml
from autocode_mcp.utils.exporters import export_hydro, export_qoj, map_tests_to_subtasks
from autocode_mcp.workflow import default_manifest, save_manifest
from autocode_mcp.workflow.models import AutoCodeManifest, ProtocolConfig, SubtaskSpec


def _parse_qoj_conf(path: Path) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        parsed[parts[0]] = parts[1] if len(parts) > 1 else ""
    return parsed


def _setup_problem(
    tmp_path: Path,
    *,
    problem_type: str = "standard",
    protocol: ProtocolConfig | None = None,
    subtasks: list[SubtaskSpec] | None = None,
    test_count: int = 4,
) -> tuple[Path, AutoCodeManifest, list[str], dict]:
    pdir = tmp_path / f"prob_{problem_type}"
    (pdir / "files").mkdir(parents=True)
    (pdir / "solutions").mkdir()
    (pdir / "statements").mkdir()
    (pdir / "tests").mkdir()

    (pdir / "files" / "testlib.h").write_text("// testlib\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){}\n", encoding="utf-8")
    (pdir / "files" / "val.cpp").write_text("int main(){}\n", encoding="utf-8")
    (pdir / "solutions" / "sol.cpp").write_text("int main(){}\n", encoding="utf-8")
    (pdir / "statements" / "README.md").write_text("# Statement\n\n## 样例\n", encoding="utf-8")
    (pdir / "statements" / "tutorial.md").write_text("# Tutorial\n\n" + "detail " * 25, encoding="utf-8")

    in_files: list[str] = []
    manifest_tests: list[dict] = []
    type_cycle = ["1", "2", "3", "4"]
    group_cycle = ["sanity", "coverage", "limit", "limit"]
    for i in range(1, test_count + 1):
        in_name = f"{i:02d}.in"
        ans_name = f"{i:02d}.ans"
        (pdir / "tests" / in_name).write_text(f"{i}\n", encoding="utf-8")
        (pdir / "tests" / ans_name).write_text(f"{i * 2}\n", encoding="utf-8")
        in_files.append(in_name)
        manifest_tests.append(
            {
                "index": i,
                "in_file": in_name,
                "ans_file": ans_name,
                "type_param": type_cycle[(i - 1) % len(type_cycle)],
                "group": group_cycle[(i - 1) % len(group_cycle)],
            }
        )

    manifest = default_manifest(
        f"Test {problem_type}",
        interactive=(problem_type == "interactive"),
        problem_type=problem_type,  # type: ignore[arg-type]
        protocol_mode=protocol.mode if protocol else None,
    )
    if protocol is not None:
        manifest.protocol = protocol
    if subtasks is not None:
        manifest.subtasks = subtasks
    if problem_type == "spj":
        (pdir / "files" / "checker.cpp").write_text("int main(){}\n", encoding="utf-8")
    elif problem_type == "interactive":
        (pdir / "files" / "interactor.cpp").write_text("int main(){}\n", encoding="utf-8")
    elif problem_type == "communication":
        comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
        if comm_mode == "grader_linked":
            (pdir / "files" / "grader.cpp").write_text("int main(){}\n", encoding="utf-8")
            (pdir / "files" / "problem.h").write_text("int solve(int);\n", encoding="utf-8")
        else:
            (pdir / "files" / "communicator.cpp").write_text("int main(){}\n", encoding="utf-8")

    save_manifest(str(pdir), manifest)
    tests_manifest = {"version": 1, "answer_ext": ".ans", "tests": manifest_tests}
    set_section(str(pdir), TEST_MANIFEST, tests_manifest)
    return pdir, manifest, in_files, tests_manifest


def test_map_tests_to_subtasks_explicit_groups_and_fallback():
    in_files = [f"{i:02d}.in" for i in range(1, 7)]
    tests_manifest = {
        "tests": [
            {"index": 1, "type_param": "1", "group": "small"},
            {"index": 2, "type_param": "2", "group": "small"},
            {"index": 3, "type_param": "3", "group": "large"},
            {"index": 4, "type_param": "4", "group": "large"},
            {"index": 5, "type_param": "3", "group": "extra"},
            {"index": 6, "type_param": "4", "group": "extra"},
        ]
    }
    subtasks = [
        SubtaskSpec(id=1, score=20, test_indices=[1, 2, 99]),
        SubtaskSpec(id=2, score=30, groups=["large"]),
        SubtaskSpec(id=3, score=50),
    ]
    st_map = map_tests_to_subtasks(in_files, subtasks, tests_manifest)
    assert st_map == {1: [1, 2], 2: [3, 4], 3: [5, 6]}

    # Type alias fallback ("tiny"/"tle") works even without explicit group label
    alias_subtasks = [
        SubtaskSpec(id=1, score=50, groups=["tiny"]),
        SubtaskSpec(id=2, score=50, groups=["tle"]),
    ]
    alias_map = map_tests_to_subtasks(in_files, alias_subtasks, tests_manifest)
    assert alias_map[1] == [1]
    assert alias_map[2] == [4, 6]

    assert map_tests_to_subtasks([], subtasks) == {}
    assert map_tests_to_subtasks(in_files, []) == {}


def test_export_hydro_and_qoj_standard_spj_interactive(tmp_path):
    # 1. SPJ with subtasks & multiple dependencies
    subtasks = [
        SubtaskSpec(id=1, name="tiny", score=20, test_indices=[3]),
        SubtaskSpec(id=2, name="mid", score=30, test_indices=[1, 4]),
        SubtaskSpec(id=3, name="full", score=50, test_indices=[2], dependencies=[1, 2]),
    ]
    pdir, manifest, in_files, tests_manifest = _setup_problem(
        tmp_path, problem_type="spj", subtasks=subtasks, test_count=4
    )

    h = export_hydro(
        str(pdir),
        manifest,
        time_limit_ms=1500,
        memory_limit_mb=512,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tests_manifest,
    )
    assert h["test_count"] == 4
    assert h["subtask_count"] == 3
    h_dir = Path(h["output_dir"])
    prob_yaml = yaml.safe_load((h_dir / "problem.yaml").read_text(encoding="utf-8"))
    assert prob_yaml["title"] == "Test spj"
    assert "special-judge" in prob_yaml["tag"]
    assert "subtasks" in prob_yaml["tag"]
    cfg = yaml.safe_load((h_dir / "testdata" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["type"] == "default"
    assert cfg["checker_type"] == "testlib"
    assert cfg["checker"] == "checker.cpp"
    assert cfg["time"] == "1500ms"
    assert cfg["memory"] == "512m"
    assert (h_dir / "testdata" / "checker.cpp").is_file()
    assert (h_dir / "problem_zh.md").is_file()
    assert cfg["subtasks"][2]["if"] == [1, 2]
    assert [c["input"] for c in cfg["subtasks"][1]["cases"]] == ["01.in", "04.in"]

    q = export_qoj(
        str(pdir),
        manifest,
        time_limit_ms=1500,
        memory_limit_mb=512,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tests_manifest,
    )
    q_dir = Path(q["output_dir"])
    conf = _parse_qoj_conf(q_dir / "problem.conf")
    assert conf["n_tests"] == "4"
    assert conf["time_limit"] == "2"
    assert conf["time_limit_ms"] == "1500"
    assert conf["memory_limit"] == "512"
    assert conf["checker"] == "chk.cpp"
    assert (q_dir / "chk.cpp").is_file()
    assert conf["n_subtasks"] == "3"
    # Reordered by subtask: [3], [1, 4], [2] -> end indices 1, 3, 4
    assert conf["subtask_end_1"] == "1"
    assert conf["subtask_end_2"] == "3"
    assert conf["subtask_end_3"] == "4"
    assert conf["subtask_dependence_3"] == "many"
    assert conf["subtask_dependence_3_1"] == "1"
    assert conf["subtask_dependence_3_2"] == "2"
    # Verify data/1.in actually received original test 03.in ("3\n")
    assert (q_dir / "data" / "1.in").read_text(encoding="utf-8") == "3\n"
    assert (q_dir / "data" / "4.in").read_text(encoding="utf-8") == "2\n"

    # 2. Interactive
    ipdir, imanifest, iin, itm = _setup_problem(tmp_path, problem_type="interactive", test_count=2)
    ih = export_hydro(
        str(ipdir), imanifest, time_limit_ms=2000, memory_limit_mb=256, in_files=iin, answer_ext=".ans"
    )
    icfg = yaml.safe_load((Path(ih["output_dir"]) / "testdata" / "config.yaml").read_text(encoding="utf-8"))
    assert icfg["type"] == "interactive"
    assert icfg["interactor"] == "interactor.cpp"
    iq = export_qoj(
        str(ipdir), imanifest, time_limit_ms=2000, memory_limit_mb=256, in_files=iin, answer_ext=".ans"
    )
    iconf = _parse_qoj_conf(Path(iq["output_dir"]) / "problem.conf")
    assert iconf["interaction_mode"] == "on"
    assert iconf["interactor"] == "interactor.cpp"


@pytest.mark.parametrize("comm_mode", ["run_twice", "grader_linked", "multi_process"])
def test_export_hydro_and_qoj_communication_modes(tmp_path, comm_mode):
    if comm_mode == "run_twice":
        proto = ProtocolConfig(
            mode="run_twice",
            passes=2,
            pass_headers=["first", "second"],
            max_bits=12,
            max_bytes=2,
            max_rounds=2,
            communicator_file="files/communicator.cpp",
        )
    elif comm_mode == "grader_linked":
        proto = ProtocolConfig(
            mode="grader_linked",
            grader_file="files/grader.cpp",
            header_file="files/problem.h",
            max_queries=50,
        )
    else:
        proto = ProtocolConfig(
            mode="multi_process",
            communicator_file="files/communicator.cpp",
            participant_solutions=["alice", "bob"],
            max_bytes=128,
        )

    subtasks = [
        SubtaskSpec(id=1, name="st1", score=40, test_indices=[1, 2]),
        SubtaskSpec(id=2, name="st2", score=60, test_indices=[3, 4], dependencies=[1]),
    ]
    pdir, manifest, in_files, tests_manifest = _setup_problem(
        tmp_path / comm_mode,
        problem_type="communication",
        protocol=proto,
        subtasks=subtasks,
        test_count=4,
    )

    h = export_hydro(
        str(pdir),
        manifest,
        time_limit_ms=2000,
        memory_limit_mb=256,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tests_manifest,
    )
    h_cfg = yaml.safe_load((Path(h["output_dir"]) / "testdata" / "config.yaml").read_text(encoding="utf-8"))
    assert h_cfg["type"] == "communication"
    assert h_cfg["protocol"]["mode"] == comm_mode
    if comm_mode == "grader_linked":
        assert h_cfg["grader"] == "grader.cpp"
        assert h_cfg["header"] == "problem.h"
        assert (Path(h["output_dir"]) / "testdata" / "grader.cpp").is_file()
        assert (Path(h["output_dir"]) / "testdata" / "problem.h").is_file()
    else:
        assert h_cfg["interactor"] == "communicator.cpp"
        assert (Path(h["output_dir"]) / "testdata" / "communicator.cpp").is_file()

    q = export_qoj(
        str(pdir),
        manifest,
        time_limit_ms=2000,
        memory_limit_mb=256,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tests_manifest,
    )
    q_conf = _parse_qoj_conf(Path(q["output_dir"]) / "problem.conf")
    assert q_conf["communication_mode"] == comm_mode
    assert q_conf["subtask_dependence_2"] == "1"
    if comm_mode == "run_twice":
        assert q_conf["two_stage"] == "on"
        assert q_conf["n_passes"] == "2"
        assert q_conf["max_bits"] == "12"
        assert q_conf["communicator"] == "communicator.cpp"
    elif comm_mode == "grader_linked":
        assert q_conf["with_implementer"] == "on"
        assert q_conf["implementer"] == "grader.cpp"
        assert q_conf["header"] == "problem.h"
        assert q_conf["max_queries"] == "50"
    else:
        assert q_conf["n_participants"] == "2"
        assert q_conf["communicator"] == "communicator.cpp"
        assert q_conf["max_bytes"] == "128"


def test_exporters_reject_escaping_and_symlink_output_dirs(tmp_path):
    pdir, manifest, in_files, _ = _setup_problem(tmp_path, problem_type="standard", test_count=2)

    with pytest.raises(ValueError, match="escapes problem_dir"):
        export_hydro(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files,
            answer_ext=".ans",
            output_rel_dir="../escape",
        )

    with pytest.raises(ValueError, match="cannot be problem_dir root"):
        export_qoj(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files,
            answer_ext=".ans",
            output_rel_dir=".",
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), str(pdir / "sym_export"))
    with pytest.raises(ValueError, match="escapes problem_dir|symlink"):
        export_hydro(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files,
            answer_ext=".ans",
            output_rel_dir="sym_export",
        )


@pytest.mark.asyncio
async def test_pack_polygon_with_subtasks_and_multi_oj_export(tmp_path):
    subtasks = [
        SubtaskSpec(id=1, name="st1", score=30, groups=["sanity", "coverage"]),
        SubtaskSpec(id=2, name="st2", score=70, groups=["limit"], dependencies=[1]),
    ]
    pdir, manifest, in_files, tests_manifest = _setup_problem(
        tmp_path, problem_type="standard", subtasks=subtasks, test_count=4
    )
    # Mark workflow + audit gates passed so pack succeeds end-to-end
    set_section(
        str(pdir),
        WORKFLOW,
        {
            "tests_verified": True,
            "verify_signals": {
                "limit_semantics": {"executed": True, "passed": True},
                "validator_check": {"executed": True, "passed": True},
                "wrong_solution_kill": {"executed": True, "passed": True},
                "answer_consistency": {"executed": True, "passed": True},
            },
        },
    )
    set_section(
        str(pdir),
        AUDIT,
        {
            "full_audit_passed": True,
            "full_audit": {
                "mode": "full",
                "decision": "go",
                "blocking_issue_count": 0,
                "quality_signals": {
                    "duplicate_inputs": {"executed": True, "passed": True},
                    "scale_distribution": {"executed": True, "passed": True},
                    "purpose_coverage": {"executed": True, "passed": True},
                    "validator_self_test": {"executed": True, "passed": True},
                },
            },
        },
    )

    tool = ProblemPackPolygonTool()
    res = await tool.execute(
        problem_dir=str(pdir),
        export_formats=["polygon", "hydro", "qoj"],
    )
    assert res.success, res.error
    assert res.data["export_formats"] == ["polygon", "hydro", "qoj"]
    assert set(res.data["exports"]) == {"polygon", "hydro", "qoj"}

    # Verify Polygon XML has <groups>, <dependencies>, and <tests>
    root = ET.fromstring((pdir / "problem.xml").read_text(encoding="utf-8"))
    testset = root.find("./judging/testset")
    assert testset is not None
    groups = testset.findall("./groups/group")
    assert len(groups) == 2
    assert groups[0].attrib["name"] == "subtask_1"
    assert groups[0].attrib["points"] == "30"
    assert groups[0].attrib["points-policy"] == "complete-group"
    assert groups[1].attrib["name"] == "subtask_2"
    assert groups[1].attrib["points"] == "70"
    assert groups[1].attrib["points-policy"] == "complete-group"
    deps = groups[1].findall("./dependencies/dependency")
    assert [d.attrib["group"] for d in deps] == ["subtask_1"]

    test_nodes = testset.findall("./tests/test")
    assert len(test_nodes) == 4
    assert [t.attrib["group"] for t in test_nodes] == [
        "subtask_1",
        "subtask_1",
        "subtask_2",
        "subtask_2",
    ]

    # Verify Hydro & QOJ output trees exist and are valid
    assert (pdir / "exports" / "hydro" / "problem.yaml").is_file()
    assert (pdir / "exports" / "hydro" / "testdata" / "config.yaml").is_file()
    assert (pdir / "exports" / "qoj" / "problem.conf").is_file()
    assert (pdir / "exports" / "qoj" / "data" / "4.ans").is_file()

    # Invalid / empty export_formats rejected cleanly
    bad_empty = await tool.execute(problem_dir=str(pdir), export_formats=[])
    assert not bad_empty.success
    bad_fmt = await tool.execute(problem_dir=str(pdir), export_formats=["invalid"])  # type: ignore[list-item]
    assert not bad_fmt.success


def test_build_problem_xml_omits_groups_when_no_subtasks(tmp_path):
    pdir, manifest, in_files, _ = _setup_problem(tmp_path, problem_type="standard", test_count=2)
    xml_str = _build_problem_xml(
        str(pdir),
        time_limit_ms=1000,
        memory_limit_bytes=256 * 1024 * 1024,
        test_count=2,
        answer_ext=".ans",
        is_interactive_problem=False,
        has_checker=False,
        has_interactor=False,
        manifest_model=manifest,
        in_files=in_files,
    )
    root = ET.fromstring(xml_str)
    assert root.find("./judging/testset/groups") is None
    assert root.find("./judging/testset/tests") is None


def test_exporters_block_internal_symlink_and_test_file_escapes(tmp_path):
    pdir, manifest, in_files, _ = _setup_problem(tmp_path, problem_type="standard", test_count=2)

    # 1. Internal symlink inside problem_dir MUST be rejected without deleting files/
    os.symlink(str(pdir / "files"), str(pdir / "internal_sym"))
    with pytest.raises(ValueError, match="symlink"):
        export_hydro(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files,
            answer_ext=".ans",
            output_rel_dir="internal_sym",
        )
    assert (pdir / "files" / "gen.cpp").is_file()

    # 2. Regular file at export destination rejected
    (pdir / "not_a_dir").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        export_qoj(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files,
            answer_ext=".ans",
            output_rel_dir="not_a_dir",
        )

    # 3. Traversal in_files & invalid answer_ext rejected
    with pytest.raises(ValueError, match="Unsafe test input"):
        export_hydro(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=["../../escape.in"],
            answer_ext=".ans",
        )
    with pytest.raises(ValueError, match="Invalid answer_ext"):
        export_qoj(
            str(pdir),
            manifest,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files,
            answer_ext="/../bad",
        )

    # 4. Symlinked test file pointing outside problem_dir rejected
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP_SECRET\n", encoding="utf-8")
    (pdir / "tests" / "01.in").unlink()
    os.symlink(str(secret), str(pdir / "tests" / "01.in"))
    for fn in (export_hydro, export_qoj):
        with pytest.raises(ValueError, match="escapes problem_dir"):
            fn(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=in_files,
                answer_ext=".ans",
            )


def test_map_tests_to_subtasks_matches_case_plan_aliases_and_filename_order():
    from autocode_mcp.workflow.manifest import load_manifest

    comm_manifest = load_manifest(
        os.path.join(os.path.dirname(__file__), "..", "..", "examples", "communication-sample")
    )
    assert comm_manifest is not None
    # Simulate real problem_generate_tests manifest without manual "group" keys and unpadded order
    in_files = ["1.in", "2.in", "10.in", "11.in"]
    tests_manifest = {
        "tests": [
            {"index": 1, "in_file": "1.in", "type_param": "1"},
            {"index": 2, "in_file": "2.in", "type_param": "2"},
            {"index": 10, "in_file": "10.in", "type_param": "3"},
            {"index": 11, "in_file": "11.in", "type_param": "4"},
        ]
    }
    st_map = map_tests_to_subtasks(in_files, comm_manifest.subtasks, tests_manifest)
    assert st_map == {1: [1, 2], 2: [3, 4]}


def test_export_qoj_toposort_overlapping_empty_and_no_blank_prefix(tmp_path):
    # Unordered subtasks with non-contiguous IDs: id=20 depends on id=10
    subtasks = [
        SubtaskSpec(id=20, name="full", score=60, test_indices=[3, 4], dependencies=[10]),
        SubtaskSpec(id=10, name="base", score=40, test_indices=[1, 2]),
    ]
    pdir, manifest, in_files, tm = _setup_problem(
        tmp_path / "topo", problem_type="standard", subtasks=subtasks, test_count=4
    )
    q = export_qoj(
        str(pdir),
        manifest,
        time_limit_ms=1000,
        memory_limit_mb=256,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tm,
    )
    conf_text = (Path(q["output_dir"]) / "problem.conf").read_text(encoding="utf-8")
    # Every line in problem.conf must have a non-empty key and value (no bare "input_pre ")
    for line in conf_text.splitlines():
        parts = line.split()
        assert len(parts) == 2, f"Malformed problem.conf line: {line!r}"
    assert "input_pre" not in conf_text
    assert "output_pre" not in conf_text

    conf = _parse_qoj_conf(Path(q["output_dir"]) / "problem.conf")
    assert conf["n_tests"] == "4"
    assert conf["subtask_end_1"] == "2"
    assert conf["subtask_score_1"] == "40"
    assert conf["subtask_end_2"] == "4"
    assert conf["subtask_score_2"] == "60"
    assert conf["subtask_dependence_2"] == "1"
    assert (Path(q["output_dir"]) / "data" / "1.in").read_text(encoding="utf-8") == "1\n"

    # Independent overlapping subtasks duplicate shared tests so subtask_end_k is strictly increasing
    overlap_subtasks = [
        SubtaskSpec(id=1, score=50, test_indices=[1, 2]),
        SubtaskSpec(id=2, score=50, test_indices=[2, 3]),
    ]
    pdir2, manifest2, in_files2, _ = _setup_problem(
        tmp_path / "overlap", problem_type="standard", subtasks=overlap_subtasks, test_count=3
    )
    q2 = export_qoj(
        str(pdir2),
        manifest2,
        time_limit_ms=1000,
        memory_limit_mb=256,
        in_files=in_files2,
        answer_ext=".ans",
    )
    conf2 = _parse_qoj_conf(Path(q2["output_dir"]) / "problem.conf")
    assert conf2["n_tests"] == "4"
    assert conf2["subtask_end_1"] == "2"
    assert conf2["subtask_end_2"] == "4"

    # Empty subtask fails cleanly rather than emitting broken subtask_end_k == prev_end
    empty_subtasks = [
        SubtaskSpec(id=1, score=50, test_indices=[1, 2]),
        SubtaskSpec(id=2, score=50, groups=["nonexistent_group"]),
    ]
    manifest2.subtasks = empty_subtasks
    with pytest.raises(ValueError, match="empty"):
        export_qoj(
            str(pdir2),
            manifest2,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files2,
            answer_ext=".ans",
        )

    # Dependency cycle rejected
    manifest2.subtasks = [
        SubtaskSpec(id=1, score=50, test_indices=[1], dependencies=[2]),
        SubtaskSpec(id=2, score=50, test_indices=[2], dependencies=[1]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        export_qoj(
            str(pdir2),
            manifest2,
            time_limit_ms=1000,
            memory_limit_mb=256,
            in_files=in_files2,
            answer_ext=".ans",
        )


@pytest.mark.asyncio
async def test_pack_hydro_only_with_out_ext_and_root_interactor(tmp_path):
    pdir, manifest, _, _ = _setup_problem(tmp_path, problem_type="interactive", test_count=2)
    # Move interactor to root and use .out answer files
    (pdir / "interactor.cpp").write_text(
        (pdir / "files" / "interactor.cpp").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (pdir / "files" / "interactor.cpp").unlink()
    for i in (1, 2):
        (pdir / "tests" / f"{i:02d}.ans").rename(pdir / "tests" / f"{i:02d}.out")
    set_section(
        str(pdir),
        TEST_MANIFEST,
        {
            "version": 1,
            "answer_ext": ".out",
            "tests": [
                {"index": 1, "in_file": "01.in", "ans_file": "01.out", "type_param": "3"},
                {"index": 2, "in_file": "02.in", "ans_file": "02.out", "type_param": "4"},
            ],
        },
    )
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    save_manifest(str(pdir), manifest)

    res = await ProblemPackPolygonTool().execute(problem_dir=str(pdir), export_formats=["hydro"])
    assert res.success, res.error
    assert not (pdir / "problem.xml").exists()
    assert res.data["export_formats"] == ["hydro"]
    assert "files_copied" in res.data["results"]
    h_dir = Path(res.data["exports"]["hydro"]["output_dir"])
    assert (h_dir / "testdata" / "interactor.cpp").is_file()
    assert (h_dir / "testdata" / "01.out").is_file()
    assert "testdata/interactor.cpp" in res.data["exports"]["hydro"]["files"]

