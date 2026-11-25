"""Tests for ICPC/Kattis & IOI CMS exporters, Polygon sync CLI, Typst PDF renderer, and PR CI guard."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from autocode_mcp.cli import polygon_sync, render_pdf
from autocode_mcp.runtime_store import AUDIT, TEST_MANIFEST, WORKFLOW, set_section
from autocode_mcp.tools.problem_pack import ProblemPackPolygonTool
from autocode_mcp.utils.exporters import export_cms, export_icpc_kattis
from autocode_mcp.workflow import default_manifest, save_manifest
from autocode_mcp.workflow.models import ProtocolConfig, SolutionEntry, SubtaskSpec


def _build_sample_problem(
    tmp_path: Path,
    *,
    name: str = "prob",
    problem_type: str = "standard",
    subtasks: list[SubtaskSpec] | None = None,
    answer_ext: str = ".ans",
    use_sample_prefix: bool = False,
) -> tuple[Path, Any, list[str], dict[str, Any]]:
    pdir = tmp_path / name
    for sub in ("files", "solutions", "statements", "tests"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)

    (pdir / "files" / "testlib.h").write_text("// testlib\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "val.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "solutions" / "sol.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "solutions" / "brute.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "solutions" / "wrong_wa.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "solutions" / "tle_slow.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "statements" / "README.md").write_text(
        "# A + B Problem\n\nGiven $1 \\le n \\le 10^9$, compute sum.\n",
        encoding="utf-8",
    )
    (pdir / "statements" / "tutorial.md").write_text(
        "# Editorial\n\nUse $O(1)$ arithmetic.\n",
        encoding="utf-8",
    )

    in_files: list[str] = []
    tm_tests: list[dict[str, Any]] = []
    specs = [
        ("sample_01" if use_sample_prefix else "01", "1", "sample"),
        ("02", "2", "coverage"),
        ("03", "3", "limit"),
        ("04", "4", "limit"),
    ]
    for idx, (stem, t_param, grp) in enumerate(specs, 1):
        in_name = f"{stem}.in"
        ans_name = f"{stem}{answer_ext}"
        (pdir / "tests" / in_name).write_text(f"{idx} {idx + 1}\n", encoding="utf-8")
        if problem_type not in ("interactive", "communication"):
            (pdir / "tests" / ans_name).write_text(f"{2 * idx + 1}\n", encoding="utf-8")
        in_files.append(in_name)
        tm_tests.append(
            {
                "index": idx,
                "in_file": in_name,
                "ans_file": ans_name,
                "type_param": t_param,
                "group": grp,
            }
        )

    manifest = default_manifest(
        "A Plus B",
        interactive=(problem_type == "interactive"),
        problem_type=problem_type,  # type: ignore[arg-type]
        protocol_mode="run_twice" if problem_type == "communication" else None,
        time_limit_ms=1500,
        memory_limit_mb=512,
    )
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", language="cpp", path="solutions/sol.cpp"),
        SolutionEntry(name="brute", role="brute", language="cpp", path="solutions/brute.cpp"),
        SolutionEntry(
            name="wrong_wa",
            role="wrong",
            language="cpp",
            path="solutions/wrong_wa.cpp",
            expected="fail",
            expected_verdict="WA",
        ),
        SolutionEntry(
            name="tle_slow",
            role="wrong",
            language="cpp",
            path="solutions/tle_slow.cpp",
            expected="fail",
            expected_verdict="TLE",
        ),
    ]
    if subtasks is not None:
        manifest.subtasks = subtasks
    if problem_type == "spj":
        (pdir / "files" / "checker.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    elif problem_type == "interactive":
        (pdir / "files" / "interactor.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    elif problem_type == "communication":
        (pdir / "files" / "communicator.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")

    save_manifest(str(pdir), manifest)
    tests_manifest = {"version": 1, "answer_ext": answer_ext, "tests": tm_tests}
    set_section(str(pdir), TEST_MANIFEST, tests_manifest)
    return pdir, manifest, in_files, tests_manifest


# ============== 1. ICPC/Kattis & IOI CMS Exporters ==============


def test_export_icpc_kattis_2025_09_directory_and_zip(tmp_path: Path) -> None:
    pdir, manifest, in_files, tm = _build_sample_problem(
        tmp_path,
        name="kattis_spj",
        problem_type="spj",
        answer_ext=".out",
        use_sample_prefix=True,
    )

    res = export_icpc_kattis(
        str(pdir),
        manifest,
        time_limit_ms=1500,
        memory_limit_mb=512,
        in_files=in_files,
        answer_ext=".out",
        tests_manifest=tm,
        data_mode="copy",
    )
    assert res["format"] == "icpc_kattis"
    assert res["test_count"] == 4
    assert res["sample_count"] == 1
    assert res["secret_count"] == 3
    assert res["config_validation"]["passed"] is True

    out_dir = Path(res["output_dir"])
    pyaml = yaml.safe_load((out_dir / "problem.yaml").read_text(encoding="utf-8"))
    assert pyaml["problem_format_version"] == "2025-09"
    assert pyaml["name"] == "A Plus B"
    assert pyaml["limits"]["time_limit"] == 1.5
    assert pyaml["limits"]["time_multiplier"] == 5.0
    assert pyaml["limits"]["memory"] == 512
    assert pyaml["validation"] == "custom"

    # Kattis requires .ans even when source used .out
    assert (out_dir / "data" / "sample" / "sample_01.in").read_text(encoding="utf-8") == "1 2\n"
    assert (out_dir / "data" / "sample" / "sample_01.ans").read_text(encoding="utf-8") == "3\n"
    assert (out_dir / "data" / "secret" / "02.in").read_text(encoding="utf-8") == "2 3\n"
    assert (out_dir / "data" / "secret" / "02.ans").read_text(encoding="utf-8") == "5\n"
    assert (out_dir / "submissions" / "accepted" / "sol.cpp").is_file()
    assert (out_dir / "submissions" / "wrong_answer" / "wrong_wa.cpp").is_file()
    assert (out_dir / "submissions" / "time_limit_exceeded" / "tle_slow.cpp").is_file()
    assert (out_dir / "input_validators" / "val.cpp").is_file()
    assert (out_dir / "input_validators" / "testlib.h").is_file()
    assert (out_dir / "output_validators" / "checker.cpp").is_file()
    assert (out_dir / "output_validators" / "testlib.h").is_file()
    assert (out_dir / "problem_statement" / "problem.en.md").is_file()

    # Zip mode replaces sibling dir and creates deterministic zip
    zres = export_icpc_kattis(
        str(pdir),
        manifest,
        time_limit_ms=1500,
        memory_limit_mb=512,
        in_files=in_files,
        answer_ext=".out",
        tests_manifest=tm,
        data_mode="zip",
    )
    zip_path = Path(zres["archive_path"])
    assert zip_path.is_file()
    assert not (pdir / "exports" / "icpc_kattis").exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert {
            "problem.yaml",
            "data/sample/sample_01.in",
            "data/sample/sample_01.ans",
            "data/secret/04.in",
            "data/secret/04.ans",
            "submissions/accepted/sol.cpp",
            "submissions/wrong_answer/wrong_wa.cpp",
            "input_validators/val.cpp",
            "output_validators/checker.cpp",
            "problem_statement/problem.en.md",
        }.issubset(names)


def test_export_icpc_kattis_interactive_and_communication(tmp_path: Path) -> None:
    ipdir, imanifest, iin, itm = _build_sample_problem(
        tmp_path, name="kattis_inter", problem_type="interactive", use_sample_prefix=True
    )
    (ipdir / "tests" / "sample_01.ans").write_text("OK\n", encoding="utf-8")
    (ipdir / "tests" / "02.ans").write_text("   \n", encoding="utf-8")

    ires = export_icpc_kattis(
        str(ipdir),
        imanifest,
        time_limit_ms=2000,
        memory_limit_mb=256,
        in_files=iin,
        answer_ext=".ans",
        tests_manifest=itm,
        data_mode="copy",
    )
    assert set(ires["omitted_empty_answers"]) == {"sample_01.ans", "02.ans"}
    i_dir = Path(ires["output_dir"])
    iyaml = yaml.safe_load((i_dir / "problem.yaml").read_text(encoding="utf-8"))
    assert iyaml["validation"] == "custom interactive"
    assert (i_dir / "output_validators" / "interactor.cpp").is_file()
    assert not list((i_dir / "data" / "sample").glob("*.ans"))
    assert not list((i_dir / "data" / "secret").glob("*.ans"))

    # Missing interactor must fail validation without leaving partial output
    (ipdir / "files" / "interactor.cpp").unlink()
    with pytest.raises(ValueError, match="output_validators"):
        export_icpc_kattis(
            str(ipdir),
            imanifest,
            time_limit_ms=2000,
            memory_limit_mb=256,
            in_files=iin,
            answer_ext=".ans",
            output_rel_dir="exports/kattis_fail",
            data_mode="copy",
        )
    assert not (ipdir / "exports" / "kattis_fail").exists()

    cpdir, cmanifest, cin, ctm = _build_sample_problem(
        tmp_path, name="kattis_comm", problem_type="communication"
    )
    cmanifest.protocol = ProtocolConfig(mode="run_twice", passes=2)
    cres = export_icpc_kattis(
        str(cpdir),
        cmanifest,
        time_limit_ms=2000,
        memory_limit_mb=256,
        in_files=cin,
        answer_ext=".ans",
        tests_manifest=ctm,
        data_mode="copy",
    )
    cyaml = yaml.safe_load((Path(cres["output_dir"]) / "problem.yaml").read_text(encoding="utf-8"))
    assert cyaml["validation"] == "custom multi-pass"
    assert (Path(cres["output_dir"]) / "output_validators" / "communicator.cpp").is_file()


def test_export_cms_task_layout_and_subtask_weights(tmp_path: Path) -> None:
    subtasks = [
        SubtaskSpec(id=1, name="small", score=30, test_indices=[1, 2]),
        SubtaskSpec(id=2, name="full", score=70, test_indices=[3, 4], dependencies=[1]),
    ]
    pdir, manifest, in_files, tm = _build_sample_problem(
        tmp_path, name="cms_spj", problem_type="spj", subtasks=subtasks
    )

    res = export_cms(
        str(pdir),
        manifest,
        time_limit_ms=1500,
        memory_limit_mb=512,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tm,
        data_mode="copy",
    )
    assert res["format"] == "cms"
    assert res["test_count"] == 4
    assert res["subtask_count"] == 2
    assert res["total_score"] == 100
    assert res["config_validation"]["passed"] is True

    out_dir = Path(res["output_dir"])
    task_yaml = yaml.safe_load((out_dir / "task.yaml").read_text(encoding="utf-8"))
    assert task_yaml["time_limit"] == 1.5
    assert task_yaml["memory_limit"] == 512
    assert task_yaml["n_input"] == 4
    assert task_yaml["score_type"] == "GroupMin"
    assert task_yaml["score_type_parameters"] == [[30, 2], [70, 2]]
    assert task_yaml["total_score"] == 100
    assert task_yaml["subtasks"][0]["score"] == 30
    assert task_yaml["subtasks"][1]["score"] == 70
    assert task_yaml["subtasks"][1]["dependencies"] == [1]

    for idx in range(4):
        assert (out_dir / "input" / f"input{idx}.txt").is_file()
        assert (out_dir / "output" / f"output{idx}.txt").is_file()
    assert (out_dir / "sol" / "sol.cpp").is_file()
    assert (out_dir / "sol" / "brute.cpp").is_file()
    assert (out_dir / "check" / "checker.cpp").is_file()
    assert (out_dir / "gen" / "gen.cpp").is_file()
    assert (out_dir / "gen" / "val.cpp").is_file()
    assert (out_dir / "statement" / "statement.md").is_file()

    # Empty subtask fails cleanly
    manifest.subtasks = [
        SubtaskSpec(id=1, score=50, test_indices=[1]),
        SubtaskSpec(id=2, score=50, groups=["nonexistent"]),
    ]
    with pytest.raises(ValueError, match="empty"):
        export_cms(
            str(pdir),
            manifest,
            time_limit_ms=1500,
            memory_limit_mb=512,
            in_files=in_files,
            answer_ext=".ans",
            tests_manifest=tm,
            output_rel_dir="exports/cms_empty",
        )
    assert not (pdir / "exports" / "cms_empty").exists()

    # Zip mode
    manifest.subtasks = subtasks
    zres = export_cms(
        str(pdir),
        manifest,
        time_limit_ms=1500,
        memory_limit_mb=512,
        in_files=in_files,
        answer_ext=".ans",
        tests_manifest=tm,
        data_mode="zip",
    )
    zpath = Path(zres["archive_path"])
    assert zpath.is_file()
    assert not (pdir / "exports" / "cms").exists()
    with zipfile.ZipFile(zpath) as zf:
        assert {"task.yaml", "input/input0.txt", "output/output0.txt", "sol/sol.cpp", "check/checker.cpp", "gen/gen.cpp"}.issubset(
            set(zf.namelist())
        )


def test_icpc_and_cms_reject_traversal_and_symlinks(tmp_path: Path) -> None:
    pdir, manifest, in_files, tm = _build_sample_problem(tmp_path, name="safe_bounds")

    for exporter in (export_icpc_kattis, export_cms):
        with pytest.raises(ValueError, match="escapes problem_dir"):
            exporter(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=in_files,
                answer_ext=".ans",
                output_rel_dir="../escape",
            )
        with pytest.raises(ValueError, match="cannot be problem_dir root"):
            exporter(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=in_files,
                answer_ext=".ans",
                output_rel_dir=".",
            )
        with pytest.raises(ValueError, match="reserved"):
            exporter(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=in_files,
                answer_ext=".ans",
                output_rel_dir="tests",
            )
        with pytest.raises(ValueError, match="Unsafe test input"):
            exporter(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=["../../evil.in"],
                answer_ext=".ans",
            )

    outside = tmp_path / "outside"
    outside.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET\n", encoding="utf-8")
    try:
        os.symlink(str(outside), str(pdir / "sym_dir"))
        os.symlink(str(secret), str(pdir / "tests" / "sym.in"))
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege not enabled")
        raise

    for exporter in (export_icpc_kattis, export_cms):
        with pytest.raises(ValueError, match="symlink"):
            exporter(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=in_files,
                answer_ext=".ans",
                output_rel_dir="sym_dir",
            )
        with pytest.raises(ValueError, match="escapes problem_dir"):
            exporter(
                str(pdir),
                manifest,
                time_limit_ms=1000,
                memory_limit_mb=256,
                in_files=["sym.in"],
                answer_ext=".ans",
            )


@pytest.mark.asyncio
async def test_problem_pack_tool_dispatches_icpc_kattis_cms_and_keeps_comm_exemption(
    tmp_path: Path,
) -> None:
    subtasks = [
        SubtaskSpec(id=1, name="s1", score=40, test_indices=[1, 2]),
        SubtaskSpec(id=2, name="s2", score=60, test_indices=[3, 4], dependencies=[1]),
    ]
    pdir, manifest, _, _ = _build_sample_problem(
        tmp_path, name="pack_multi", problem_type="standard", subtasks=subtasks
    )
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    save_manifest(str(pdir), manifest)

    tool = ProblemPackPolygonTool()
    # MCP JSON schema contract stays untouched
    assert tool.input_schema["properties"]["export_formats"]["items"]["enum"] == [
        "polygon",
        "hydro",
        "qoj",
    ]

    res = await tool.execute(
        problem_dir=str(pdir),
        export_formats=["icpc", "kattis", "cms"],
        export_data_mode="copy",
    )
    assert res.success, res.error
    assert res.data["export_formats"] == ["icpc", "kattis", "cms"]
    assert (pdir / "exports" / "icpc" / "problem.yaml").is_file()
    assert (pdir / "exports" / "kattis" / "problem.yaml").is_file()
    assert (pdir / "exports" / "cms" / "task.yaml").is_file()
    ev = res.data["export_verification"]
    assert ev["passed"] is True
    assert ev["checks"]["icpc"]["passed"] is True
    assert ev["checks"]["kattis"]["passed"] is True
    assert ev["checks"]["cms"]["passed"] is True

    # Communication problem without .ans files packs cleanly (#20 exemption preserved)
    cpdir, cmanifest, _, _ = _build_sample_problem(
        tmp_path, name="pack_comm", problem_type="communication"
    )
    cmanifest.quality_gates.require_tests_verified = False
    cmanifest.quality_gates.require_limit_semantics = False
    cmanifest.quality_gates.require_validator_check = False
    save_manifest(str(cpdir), cmanifest)
    set_section(
        str(cpdir),
        AUDIT,
        {"full_audit_passed": True, "full_audit": {"mode": "full", "decision": "go", "blocking_issue_count": 0}},
    )

    cres = await tool.execute(
        problem_dir=str(cpdir),
        export_formats=["polygon", "icpc", "cms"],
        export_data_mode="zip",
    )
    assert cres.success, cres.error
    assert Path(cres.data["exports"]["icpc"]["archive_path"]).is_file()
    assert Path(cres.data["exports"]["cms"]["archive_path"]).is_file()


# ============== 2. Polygon Sync CLI (apiSig, HTTP pull/push, containment, zero credential leak) ==============


def test_polygon_api_sig_deterministic_sha512() -> None:
    params = {"z": "last", "a": "first", "problemId": 42, "apiKey": "KEY123", "time": 1700000000, "apiSig": "stale"}
    sig = polygon_sync.compute_polygon_api_sig(
        "problem.tests",
        params,
        "SECRET_ABC",
        rand="ab12cd",
    )
    expected_canonical = "ab12cd/problem.tests?a=first&apiKey=KEY123&problemId=42&time=1700000000&z=last#SECRET_ABC"
    expected = "ab12cd" + hashlib.sha512(expected_canonical.encode("utf-8")).hexdigest()
    assert sig == expected

    signed = polygon_sync.build_signed_params(
        "problem.info",
        {"problemId": 99},
        api_key="KEY123",
        api_secret="SECRET_ABC",
        timestamp=1700000005,
        rand="xyz789",
    )
    assert signed["apiKey"] == "KEY123"
    assert signed["time"] == "1700000005"
    assert signed["apiSig"].startswith("xyz789")
    assert len(signed["apiSig"]) == 6 + 128


def test_polygon_sync_pull_push_real_http_and_security(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_key = "SUPER_SECRET_POLYGON_KEY_123"
    api_secret = "SUPER_SECRET_POLYGON_SECRET_456"
    recorded_calls: list[tuple[str, dict[str, str]]] = []
    malicious_mode = False

    class _MockPolygonHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            parsed = urllib.parse.parse_qs(body, keep_blank_values=True)
            flat = {k: v[0] for k, v in parsed.items()}
            method = self.path.lstrip("/")
            if method.startswith("api/"):
                method = method[4:]
            recorded_calls.append((method, flat))

            # Verify canonical SHA-512 apiSig on the server side
            sig = flat.get("apiSig", "")
            rand = sig[:6]
            expected = polygon_sync.compute_polygon_api_sig(method, flat, api_secret, rand=rand)
            if sig != expected or flat.get("apiKey") != api_key:
                self._send_json({"status": "FAILED", "comment": "Invalid signature"})
                return

            if method == "problem.info":
                self._send_json({"status": "OK", "result": {"timeLimit": 2000, "memoryLimit": 256, "interactive": False}})
            elif method == "problem.statements":
                self._send_json(
                    {
                        "status": "OK",
                        "result": {
                            "english": {
                                "name": "Demo Polygon",
                                "legend": "Compute $a + b$.",
                                "input": "Two integers.",
                                "output": "Their sum.",
                            }
                        },
                    }
                )
            elif method == "problem.files":
                source_files = [{"name": "val.cpp"}]
                if malicious_mode:
                    source_files.append({"name": "../../outside_evil.cpp"})
                self._send_json(
                    {
                        "status": "OK",
                        "result": {
                            "resourceFiles": [{"name": "testlib.h"}],
                            "sourceFiles": source_files,
                            "auxFiles": [],
                        },
                    }
                )
            elif method == "problem.viewFile":
                self._send_bytes(b"// content of " + flat.get("name", "").encode("utf-8"))
            elif method == "problem.solutions":
                self._send_json({"status": "OK", "result": [{"name": "sol.cpp", "tag": "MA"}]})
            elif method == "problem.viewSolution":
                self._send_bytes(b"int main(){return 0;}\n")
            elif method == "problem.tests":
                self._send_json(
                    {
                        "status": "OK",
                        "result": [
                            {"index": 1, "input": "1 2\n", "answer": "3\n"},
                            {"index": 2},
                        ],
                    }
                )
            elif method == "problem.testInput":
                self._send_bytes(b"10 20\n")
            elif method == "problem.testAnswer":
                self._send_bytes(b"30\n")
            else:
                self._send_json({"status": "OK", "result": {}})

        def _send_json(self, obj: dict[str, Any]) -> None:
            raw = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _send_bytes(self, raw: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockPolygonHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}/api"

    try:
        # Missing credentials must fail cleanly
        monkeypatch.delenv("POLYGON_API_KEY", raising=False)
        monkeypatch.delenv("POLYGON_API_SECRET", raising=False)
        assert polygon_sync.main(["pull", "101", str(tmp_path / "noauth"), "--base-url", base_url]) == 1
        capsys.readouterr()

        monkeypatch.setenv("POLYGON_API_KEY", api_key)
        monkeypatch.setenv("POLYGON_API_SECRET", api_secret)

        # 1. Pull via CLI
        pull_dir = tmp_path / "pulled"
        rc_pull = polygon_sync.main(["pull", "101", str(pull_dir), "--base-url", base_url])
        assert rc_pull == 0
        io_pull = capsys.readouterr()
        assert api_key not in io_pull.out and api_key not in io_pull.err
        assert api_secret not in io_pull.out and api_secret not in io_pull.err
        assert "apiSig" not in io_pull.out and "apiSig" not in io_pull.err

        assert (pull_dir / "statements" / "README.md").is_file()
        assert (pull_dir / "files" / "testlib.h").is_file()
        assert (pull_dir / "files" / "val.cpp").is_file()
        assert (pull_dir / "solutions" / "sol.cpp").is_file()
        assert (pull_dir / "tests" / "01.in").read_text(encoding="utf-8") == "1 2\n"
        assert (pull_dir / "tests" / "01.ans").read_text(encoding="utf-8") == "3\n"
        assert (pull_dir / "tests" / "02.in").read_text(encoding="utf-8") == "10 20\n"
        assert (pull_dir / "tests" / "02.ans").read_text(encoding="utf-8") == "30\n"
        assert (pull_dir / ".autocode" / "manifest.json").is_file()

        # Verify ZERO credential bytes anywhere on disk (including .autocode/)
        for fpath in pull_dir.rglob("*"):
            if fpath.is_file():
                text = fpath.read_text(encoding="utf-8", errors="replace")
                assert api_key not in text, f"API key leaked into {fpath}"
                assert api_secret not in text, f"API secret leaked into {fpath}"
                assert "apiSig" not in text, f"apiSig leaked into {fpath}"

        # 2. Malicious traversal filename during pull must raise without writing outside pull_dir
        malicious_mode = True
        bad_pull_dir = tmp_path / "bad_pull"
        client = polygon_sync.PolygonClient(base_url=base_url)
        with pytest.raises(polygon_sync.PolygonPathError):
            polygon_sync.pull_problem(client, 101, str(bad_pull_dir))
        assert not (tmp_path / "outside_evil.cpp").exists()
        malicious_mode = False

        # Zip-slip archive extraction also blocked
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../escape.txt", "pwn")
        with pytest.raises(polygon_sync.PolygonPathError):
            polygon_sync.extract_zip_safely(buf.getvalue(), str(bad_pull_dir))
        assert not (tmp_path / "escape.txt").exists()

        # 3. Push pre-check blocks unverified runtime.json BEFORE issuing any HTTP request
        recorded_calls.clear()
        set_section(str(pull_dir), WORKFLOW, {"tests_verified": False})
        assert polygon_sync.main(["push", "101", str(pull_dir), "--base-url", base_url]) == 1
        assert recorded_calls == []
        capsys.readouterr()

        # Mark tests verified -> push succeeds and issues updateInfo/saveFile/setValidator/saveSolution/saveTest/saveStatement
        set_section(str(pull_dir), WORKFLOW, {"tests_verified": True})
        rc_push = polygon_sync.main(["push", "101", str(pull_dir), "--base-url", base_url])
        assert rc_push == 0
        io_push = capsys.readouterr()
        assert api_key not in io_push.out and api_secret not in io_push.out
        called_methods = [m for m, _ in recorded_calls]
        assert "problem.updateInfo" in called_methods
        assert "problem.saveFile" in called_methods
        assert "problem.setValidator" in called_methods
        assert "problem.saveSolution" in called_methods
        assert "problem.saveTest" in called_methods
        assert "problem.saveStatement" in called_methods
    finally:
        server.shutdown()
        server.server_close()


# ============== 3. Typst PDF Statement Renderer ==============


def test_markdown_to_typst_conversion_and_escaping() -> None:
    md = (
        "# Problem Title #1\n\n"
        "Price is $100 and $200, user @alice has array `a[i]` with value $1 \\le x_{i} \\le 10^{9}$.\n\n"
        "$$\n\\sum_{i=1}^n \\frac{1}{i} \\ge \\sqrt{n}\n$$\n\n"
        "- Item with *emphasis* and **bold**\n"
        "1. Ordered item\n\n"
        "| Var | Bound |\n"
        "| --- | --- |\n"
        "| $n$ | $10^5$ |\n\n"
        "```cpp\n#include <iostream>\nint x = a[0] * b;\n```\n"
    )
    typ = render_pdf.markdown_to_typst(md, title='Test "Title"')
    assert '= Problem Title \\#1' in typ
    assert '\\$100 and \\$200' in typ
    assert '\\@alice' in typ
    assert '`a[i]`' in typ
    assert '$1 <= x_(i) <= 10^(9)$' in typ
    assert '$ sum_(i=1)^n frac(1, i) >= sqrt(n) $' in typ
    assert '- Item with _emphasis_ and *bold*' in typ
    assert '+ Ordered item' in typ
    assert '#table(' in typ
    # Code block content must NOT be escaped
    assert "#include <iostream>\nint x = a[0] * b;" in typ


def test_render_pdf_cli_missing_and_present_typst(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pdir, _, _, _ = _build_sample_problem(tmp_path, name="typst_prob")
    out_pdf = tmp_path / "out" / "stmt.pdf"

    # 1. typst missing: default fails cleanly with instructions; --emit-typ writes .typ cleanly
    monkeypatch.setattr(render_pdf.shutil, "which", lambda _name: None)
    rc_fail = render_pdf.main([str(pdir), "-o", str(out_pdf)])
    assert rc_fail == 1
    io_fail = capsys.readouterr()
    assert "brew install typst" in io_fail.err

    rc_emit = render_pdf.main([str(pdir), "-o", str(out_pdf), "--include-tutorial", "--emit-typ"])
    assert rc_emit == 0
    io_emit = capsys.readouterr()
    typ_file = out_pdf.with_suffix(".typ")
    assert typ_file.is_file()
    typ_content = typ_file.read_text(encoding="utf-8")
    assert "= A + B Problem" in typ_content
    assert "#pagebreak()" in typ_content
    assert "= Editorial" in typ_content
    assert "Typst source written to" in io_emit.out

    # 2. Mocked typst binary invocation invokes `typst compile` cleanly
    invoked_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        invoked_cmds.append(cmd)
        Path(cmd[3]).write_bytes(b"%PDF-1.4\n%mock\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(render_pdf.shutil, "which", lambda _name: "/usr/local/bin/typst")
    monkeypatch.setattr(render_pdf.subprocess, "run", _fake_run)
    rc_ok = render_pdf.main([str(pdir), "-o", str(out_pdf), "--include-tutorial"])
    assert rc_ok == 0
    capsys.readouterr()
    assert len(invoked_cmds) == 1
    assert invoked_cmds[0][0] == "/usr/local/bin/typst"
    assert invoked_cmds[0][1] == "compile"
    assert invoked_cmds[0][3] == str(out_pdf)
    assert out_pdf.read_bytes().startswith(b"%PDF-1.4")


# ============== 4. Problem CI PR Comment Step ==============


def test_problem_ci_pr_comment_step_guarded_and_safe() -> None:
    ci_path = Path(__file__).resolve().parents[1] / "examples" / "ci" / "autocode-problem-audit.yml"
    raw = ci_path.read_text(encoding="utf-8")
    wf = yaml.safe_load(raw)
    assert wf["permissions"] == {"contents": "read"}
    on_block = wf.get("on") or wf.get(True)
    assert "pull_request" in on_block
    assert "pull_request_target" not in on_block
    assert not re.search(r"\$\{\{\s*github\.event\.", raw)

    steps = wf["jobs"]["audit"]["steps"]
    comment_steps = [s for s in steps if s.get("uses") == "actions/github-script@v7"]
    assert len(comment_steps) == 1
    step = comment_steps[0]
    cond = str(step.get("if", ""))
    assert "failure()" in cond
    assert "vars.AUTOCODE_PR_COMMENT == 'true'" in cond
    assert "github.event_name == 'pull_request'" in cond
    assert "audit_report.md" in step["with"]["script"]


def test_polygon_push_precheck_blocks_broken_packages_before_network(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_prob"
    empty_dir.mkdir()
    with pytest.raises(polygon_sync.PolygonPrecheckError, match="statement"):
        polygon_sync.run_push_precheck(str(empty_dir))

    (empty_dir / "statements").mkdir()
    (empty_dir / "statements" / "README.md").write_text("# S\n", encoding="utf-8")
    with pytest.raises(polygon_sync.PolygonPrecheckError, match="solution"):
        polygon_sync.run_push_precheck(str(empty_dir))

    (empty_dir / "solutions").mkdir()
    (empty_dir / "solutions" / "sol.cpp").write_text("int main(){}\n", encoding="utf-8")
    with pytest.raises(polygon_sync.PolygonPrecheckError, match="tests"):
        polygon_sync.run_push_precheck(str(empty_dir))

    (empty_dir / "tests").mkdir()
    (empty_dir / "tests" / "01.in").write_text("", encoding="utf-8")
    with pytest.raises(polygon_sync.PolygonPrecheckError, match="Empty test"):
        polygon_sync.run_push_precheck(str(empty_dir))

    # Corrupt manifest.json blocked
    (empty_dir / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (empty_dir / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    (empty_dir / ".autocode").mkdir()
    (empty_dir / ".autocode" / "manifest.json").write_text("{corrupt", encoding="utf-8")
    with pytest.raises(polygon_sync.PolygonPrecheckError, match="Invalid manifest"):
        polygon_sync.run_push_precheck(str(empty_dir))


def test_render_pdf_nested_latex_and_non_utf8_tolerance(tmp_path: Path) -> None:
    pdir = tmp_path / "non_utf8"
    (pdir / "statements").mkdir(parents=True)
    (pdir / "statements" / "README.md").write_bytes(
        b"# Title\n\nFormula: $\\frac{\\sqrt{x}}{y}$\n\nByte: \xff\n"
    )
    out_pdf = tmp_path / "out.pdf"
    res = render_pdf.render_statement_pdf(
        str(pdir), output_path=str(out_pdf), emit_typ=True, typst_bin=""
    )
    assert res["typ_path"] == str(out_pdf.with_suffix(".typ"))
    assert "frac(sqrt(x), y)" in res["typ_source"]
