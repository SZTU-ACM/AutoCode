"""Tests for Polygon discovery/staging, standalone ASan/empirical/mutation checks, and --strict."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from autocode_mcp.cli.audit import main as cli_audit_main
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.verify_fingerprint import _discover_wrong_solution_names
from autocode_mcp.utils.compiler import probe_sanitizer_support
from autocode_mcp.utils.problem_layout import (
    discover_test_pairs,
    discover_wrong_solution_files,
    is_wrong_solution_filename,
    parse_polygon_problem_xml,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Capture a non-mutating snapshot of relative paths and (size, mtime_ns)."""
    snap: dict[str, tuple[int, int]] = {}
    for cur, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        cur_p = Path(cur)
        for d in dirnames:
            dp = cur_p / d
            st = dp.lstat()
            snap[f"D:{dp.relative_to(root).as_posix()}"] = (0, st.st_mtime_ns)
        for f in filenames:
            fp = cur_p / f
            st = fp.lstat()
            snap[f"F:{fp.relative_to(root).as_posix()}"] = (st.st_size, st.st_mtime_ns)
    return snap


def _build_polygon_problem(root: Path, *, tl_ms: int = 1500, ml_mb: int = 512) -> Path:
    """Create a complete external Polygon problem directory with extensionless tests/01 + 01.a."""
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "solutions").mkdir(parents=True, exist_ok=True)
    (root / "statements" / "english").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "statements" / "english" / "problem.tex").write_text(
        "Read an integer $n$ ($1 \\le n \\le 10^5$) and print $2n$.\n",
        encoding="utf-8",
    )
    (root / "files" / "val.cpp").write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    long long n;\n"
        "    if (!(std::cin >> n) || n < 1 || n > 100000) return 1;\n"
        "    long long extra;\n"
        "    if (std::cin >> extra) return 1;\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "files" / "check.cpp").write_text(
        "#include <fstream>\n"
        "#include <iostream>\n"
        "int main(int argc, char* argv[]) {\n"
        "    if (argc < 4) return 3;\n"
        "    std::ifstream ouf(argv[2]), ans(argv[3]);\n"
        "    long long a = 0, b = 0;\n"
        "    if (!(ouf >> a) || !(ans >> b)) return 2;\n"
        "    return a == b ? 0 : 1;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "solutions" / "main_sol.cpp").write_text(
        "// Time Complexity: O(1)\n"
        "#include <iostream>\n"
        "int main() {\n"
        "    long long n = 0;\n"
        "    if (!(std::cin >> n)) return 0;\n"
        "    std::cout << (2 * n) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "solutions" / "alt_ac.cpp").write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    long long n = 0;\n"
        "    if (!(std::cin >> n)) return 0;\n"
        "    std::cout << (n + n) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "solutions" / "bad_poly.cpp").write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    long long n = 0;\n"
        "    if (!(std::cin >> n)) return 0;\n"
        "    std::cout << (n + 1) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (root / "solutions" / "tle_poly.cpp").write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    long long n = 0;\n"
        "    if (!(std::cin >> n)) return 0;\n"
        "    volatile long long s = 0;\n"
        "    while (n > 10) { s += 1; if (s < 0) break; }\n"
        "    std::cout << (2 * n) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    test_cases = [("01", 1), ("02", 42), ("03", 100000)]
    for stem, val in test_cases:
        (root / "tests" / stem).write_text(f"{val}\n", encoding="utf-8")
        (root / "tests" / f"{stem}.a").write_text(f"{2 * val}\n", encoding="utf-8")

    ml_bytes = ml_mb * 1024 * 1024
    problem_xml = f"""<?xml version="1.0" encoding="utf-8"?>
<problem revision="1" short-name="poly-double" url="https://polygon.example/p/1">
  <judging cpu-name="Intel Core i7" cpu-speed="3600">
    <testset name="tests">
      <time-limit>{tl_ms}</time-limit>
      <memory-limit>{ml_bytes}</memory-limit>
      <test-count>3</test-count>
      <input-path-pattern>tests/%02d</input-path-pattern>
      <answer-path-pattern>tests/%02d.a</answer-path-pattern>
    </testset>
  </judging>
  <assets>
    <checker name="custom-check" type="testlib">
      <source path="files/check.cpp" type="cpp.g++17"/>
    </checker>
    <validators>
      <validator>
        <source path="files/val.cpp" type="cpp.g++17"/>
      </validator>
    </validators>
    <solutions>
      <solution tag="main">
        <source path="solutions/main_sol.cpp" type="cpp.g++17"/>
      </solution>
      <solution tag="accepted">
        <source path="solutions/alt_ac.cpp" type="cpp.g++17"/>
      </solution>
      <solution tag="wrong-answer">
        <source path="solutions/bad_poly.cpp" type="cpp.g++17"/>
      </solution>
      <solution tag="time-limit-exceeded">
        <source path="solutions/tle_poly.cpp" type="cpp.g++17"/>
      </solution>
    </solutions>
  </assets>
</problem>
"""
    (root / "problem.xml").write_text(problem_xml, encoding="utf-8")
    return root


def _write_basic_standalone_problem(
    root: Path,
    *,
    sol_code: str,
    val_code: str | None = None,
    wrong_code: str | None = None,
    gen_code: str | None = None,
    statement_text: str = "Given integer n (1 <= n <= 100000), output n.\n",
    tests: list[tuple[str, str, str]] | None = None,
) -> Path:
    (root / "solutions").mkdir(parents=True, exist_ok=True)
    (root / "files").mkdir(parents=True, exist_ok=True)
    (root / "statements").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "statements" / "README.md").write_text(statement_text, encoding="utf-8")
    (root / "solutions" / "sol.cpp").write_text(sol_code, encoding="utf-8")
    if val_code is not None:
        (root / "files" / "val.cpp").write_text(val_code, encoding="utf-8")
    if wrong_code is not None:
        (root / "solutions" / "wrong-bug.cpp").write_text(wrong_code, encoding="utf-8")
    if gen_code is not None:
        (root / "files" / "gen.cpp").write_text(gen_code, encoding="utf-8")

    case_rows = tests or [
        ("01", "1\n", "1\n"),
        ("02", "25\n", "25\n"),
        ("03", "1000\n", "1000\n"),
    ]
    for stem, inp, ans in case_rows:
        (root / "tests" / f"{stem}.in").write_text(inp, encoding="utf-8")
        (root / "tests" / f"{stem}.ans").write_text(ans, encoding="utf-8")
    return root


def test_polygon_xml_rejects_xxe_and_path_traversal(tmp_path: Path) -> None:
    """XXE declarations must fail fast and traversal paths must be filtered into unsafe_paths."""
    xxe_dir = tmp_path / "xxe"
    xxe_dir.mkdir()
    (xxe_dir / "problem.xml").write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///etc/passwd">]><problem/>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="XXE|DOCTYPE|ENTITY"):
        parse_polygon_problem_xml(xxe_dir)

    ent_dir = tmp_path / "ent"
    ent_dir.mkdir()
    (ent_dir / "problem.xml").write_text(
        '<?xml version="1.0"?>\n<!ENTITY secret "x"><problem/>',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="XXE|DOCTYPE|ENTITY"):
        parse_polygon_problem_xml(ent_dir)

    trav_dir = tmp_path / "trav"
    (trav_dir / "solutions").mkdir(parents=True)
    (trav_dir / "solutions" / "ok.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (trav_dir / "problem.xml").write_text(
        """<?xml version="1.0"?>
<problem>
  <judging><testset name="tests"><time-limit>2000</time-limit><memory-limit>268435456</memory-limit></testset></judging>
  <assets>
    <checker><source path="../outside_checker.cpp" type="cpp"/></checker>
    <validators><validator><source path="../../outside_val.cpp" type="cpp"/></validator></validators>
    <solutions>
      <solution tag="main"><source path="solutions/ok.cpp" type="cpp"/></solution>
      <solution tag="wrong-answer"><source path="../outside_wrong.cpp" type="cpp"/></solution>
    </solutions>
  </assets>
</problem>
""",
        encoding="utf-8",
    )
    meta = parse_polygon_problem_xml(trav_dir)
    assert meta is not None
    assert meta.time_limit_ms == 2000
    assert meta.memory_limit_mb == 256
    assert meta.checker_path is None
    assert meta.validator_paths == []
    assert [s.path for s in meta.solutions] == ["solutions/ok.cpp"]
    assert "../outside_checker.cpp" in meta.unsafe_paths
    assert "../../outside_val.cpp" in meta.unsafe_paths
    assert "../outside_wrong.cpp" in meta.unsafe_paths


def test_wrong_solution_filename_and_custom_test_patterns(tmp_path: Path) -> None:
    """Wrong filename patterns and custom input/answer test patterns must be discovered safely."""
    positive_names = [
        "wrong_1.cpp",
        "wrong-greedy.cpp",
        "wa_overflow.cpp",
        "wa-edge.cpp",
        "tle_brute.cpp",
        "tle-n2.cpp",
        "mle_table.cpp",
        "mle-vec.cpp",
        "re_oob.cpp",
        "re-zero.cpp",
        "bf.cpp",
        "wrong/sub/anything.cpp",
        "WRONG/sub/another.cpp",
    ]
    for name in positive_names:
        assert is_wrong_solution_filename(name), f"Expected wrong match for {name}"

    for clean in (
        "sol.cpp",
        "main.cpp",
        "std.cpp",
        "brute.cpp",
        "reference.cpp",
        "checker.cpp",
        "wrong/helper.py",
    ):
        assert not is_wrong_solution_filename(clean), f"Unexpected wrong match for {clean}"

    custom_dir = tmp_path / "custom_pattern"
    (custom_dir / "data").mkdir(parents=True)
    for idx in (1, 2, 10):
        (custom_dir / "data" / f"case_{idx:02d}.txt").write_text(f"{idx}\n", encoding="utf-8")
        (custom_dir / "data" / f"case_{idx:02d}.out").write_text(f"{idx * 2}\n", encoding="utf-8")

    pairs = discover_test_pairs(
        custom_dir,
        tests_dir=custom_dir / "data",
        input_pattern="data/case_%02d.txt",
        answer_pattern="data/case_%02d.out",
        test_count=12,
    )
    assert [p.input_name for p in pairs] == ["case_01.txt", "case_02.txt", "case_10.txt"]
    assert [p.answer_name for p in pairs] == ["case_01.out", "case_02.out", "case_10.out"]


def test_polygon_extensionless_tests_full_standalone_audit_zero_write(tmp_path: Path) -> None:
    """Full standalone audit on Polygon tests/01+01.a discovers XML assets and leaves zero writes."""
    poly = _build_polygon_problem(tmp_path / "poly_pkg", tl_ms=1500, ml_mb=512)
    meta = parse_polygon_problem_xml(poly)
    assert meta is not None
    assert meta.time_limit_ms == 1500
    assert meta.memory_limit_mb == 512
    assert meta.test_count == 3
    assert meta.checker_path == "files/check.cpp"
    assert meta.validator_paths == ["files/val.cpp"]
    assert [(s.path, s.role, s.expected_verdict) for s in meta.solutions] == [
        ("solutions/main_sol.cpp", "main", None),
        ("solutions/alt_ac.cpp", "reference", None),
        ("solutions/bad_poly.cpp", "wrong", "WA"),
        ("solutions/tle_poly.cpp", "wrong", "TLE"),
    ]

    before = _snapshot_tree(poly)
    import asyncio

    tool = ProblemAuditTool()
    out = asyncio.run(
        tool.execute(
            str(poly),
            mode="full",
            standalone=True,
            layout="auto",
        )
    )
    after = _snapshot_tree(poly)

    assert before == after, "Standalone audit must perform zero writes inside Polygon problem_dir"
    assert out.success is True
    data = out.data
    assert data["detected_layout"] == "polygon"
    assert data["decision"] == "go"
    assert data["blocking_issues"] == []
    assert data["completeness_warnings"] == []
    assert data["structural_signals"]["tests_present"]["passed"] is True
    assert data["structural_signals"]["tests_present"]["evidence"]["total"] == 3
    assert data["quality_signals"]["validator_check"]["passed"] is True
    assert data["quality_signals"]["wrong_solution_kill"]["executed"] is True
    assert data["quality_signals"]["wrong_solution_kill"]["passed"] is True
    assert data["quality_signals"]["exported_packages"]["passed"] is True


def test_checker_sample_discovers_wrong_greedy_and_runs_isolated_empirical(tmp_path: Path) -> None:
    """examples/checker-sample must discover wrong-greedy.cpp and run isolated empirical complexity."""
    prob = tmp_path / "checker-sample"
    shutil.copytree(REPO_ROOT / "examples" / "checker-sample", prob)
    (prob / "tests").mkdir(exist_ok=True)
    for stem, n_val in [("01", 2), ("02", 15), ("03", 5000), ("04", 1000000000)]:
        (prob / "tests" / f"{stem}.in").write_text(f"{n_val}\n", encoding="utf-8")
        (prob / "tests" / f"{stem}.ans").write_text(f"0 {n_val}\n", encoding="utf-8")

    # Also verify unified wrong-solution discovery helper & verify_fingerprint discovery match
    discovered = [p.name for p in discover_wrong_solution_files(prob)]
    assert "wrong-greedy.cpp" in discovered
    assert "sol.cpp" not in discovered
    assert "brute.cpp" not in discovered

    wrong_names, _ = _discover_wrong_solution_names(str(prob), None, None)
    assert "wrong-greedy" in wrong_names

    before = _snapshot_tree(prob)
    import asyncio

    tool = ProblemAuditTool()
    out = asyncio.run(tool.execute(str(prob), mode="full", standalone=True, layout="auto"))
    after = _snapshot_tree(prob)

    assert before == after
    assert out.success is True
    data = out.data
    assert data["decision"] == "go"
    assert data["quality_signals"]["wrong_solution_kill"]["executed"] is True
    assert data["quality_signals"]["wrong_solution_kill"]["passed"] is True
    emp = data["quality_signals"]["empirical_complexity"]
    assert emp["executed"] is True
    assert emp["passed"] is True
    assert emp["evidence"]["isolated_standalone"] is True
    assert not (prob / ".autocode" / "empirical_tests").exists()


def test_communication_sample_standalone_mutation_check_via_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """examples/communication-sample copied to tmp_path runs --standalone --mutation-check with zero writes."""
    prob = tmp_path / "communication-sample"
    shutil.copytree(REPO_ROOT / "examples" / "communication-sample", prob)
    (prob / "tests").mkdir(exist_ok=True)
    for idx, (mode, x) in enumerate([(0, 3), (0, 12), (1, 0), (1, 15)], 1):
        (prob / "tests" / f"{idx:02d}.in").write_text(f"{mode} {x}\n", encoding="utf-8")
        (prob / "tests" / f"{idx:02d}.ans").write_text(f"{x}\n", encoding="utf-8")

    report_path = tmp_path / "comm_report.json"
    before = _snapshot_tree(prob)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autocode-audit",
            str(prob),
            "--standalone",
            "--mutation-check",
            "--format",
            "json",
            "--report",
            str(report_path),
        ],
    )
    exit_code = cli_audit_main()
    after = _snapshot_tree(prob)

    assert before == after
    assert exit_code == 0
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["decision"] == "go"
    assert data["quality_signals"]["wrong_solution_kill"]["passed"] is True
    mut = data["quality_signals"]["mutation_testing"]
    assert mut["executed"] is True
    assert mut["evidence"]["killed_mutants"] > 0
    assert mut["evidence"]["kill_rate"] > 0.5


def test_standalone_asan_catches_oob_and_falls_back_when_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Standalone sanitizer probe catches real heap-buffer-overflow and degrades gracefully if unavailable."""
    # Subcase A: real ASan OOB probe
    san_probe = probe_sanitizer_support("g++")
    oob_prob = _write_basic_standalone_problem(
        tmp_path / "oob_prob",
        sol_code=(
            "#include <iostream>\n"
            "#include <vector>\n"
            "int main() {\n"
            "    int n = 1;\n"
            "    if (!(std::cin >> n)) return 0;\n"
            "    std::vector<int> a(n, 7);\n"
            "    volatile int leak = a[n];\n"
            "    (void)leak;\n"
            "    std::cout << n << '\\n';\n"
            "    return 0;\n"
            "}\n"
        ),
        val_code=(
            "#include <iostream>\n"
            "int main() { long long n; return (std::cin >> n && n >= 1 && n <= 100000) ? 0 : 1; }\n"
        ),
        wrong_code=(
            "#include <iostream>\n"
            "int main() { long long n; std::cin >> n; std::cout << (n + 1) << '\\n'; return 0; }\n"
        ),
    )

    import asyncio

    tool = ProblemAuditTool()
    if san_probe.get("available") and san_probe.get("asan_verified"):
        before = _snapshot_tree(oob_prob)
        res = asyncio.run(tool.execute(str(oob_prob), mode="full", standalone=True))
        after = _snapshot_tree(oob_prob)
        assert before == after
        assert res.data["decision"] == "no_go"
        san_sig = res.data["quality_signals"]["sanitizer_check"]
        assert san_sig["executed"] is True
        assert san_sig["status"] == "failed"
        assert san_sig["passed"] is False
        assert any(b["gate"] == "sanitizer_check" for b in res.data["blocking_issues"])

    # Subcase B: clean code + unavailable sanitizer runtime -> status == "unavailable", still goes
    clean_prob = _write_basic_standalone_problem(
        tmp_path / "clean_prob",
        sol_code=(
            "// Time Complexity: O(1)\n"
            "#include <iostream>\n"
            "int main() { long long n; if (std::cin >> n) std::cout << n << '\\n'; return 0; }\n"
        ),
        val_code=(
            "#include <iostream>\n"
            "int main() { long long n; return (std::cin >> n && n >= 1 && n <= 100000) ? 0 : 1; }\n"
        ),
        wrong_code=(
            "#include <iostream>\n"
            "int main() { long long n; std::cin >> n; std::cout << (n + 1) << '\\n'; return 0; }\n"
        ),
    )

    async def _fake_unavailable(_compiler: str) -> dict[str, Any]:
        return {"available": False, "reason": "simulated missing sanitizer runtime"}

    monkeypatch.setattr(
        "autocode_mcp.tools.audit_standalone.probe_sanitizer_support_async",
        _fake_unavailable,
    )
    res_unavail = asyncio.run(tool.execute(str(clean_prob), mode="full", standalone=True))
    assert res_unavail.data["decision"] == "go"
    san_unavail = res_unavail.data["quality_signals"]["sanitizer_check"]
    assert san_unavail["status"] == "unavailable"
    assert san_unavail["executed"] is False
    assert san_unavail["passed"] is True


def test_standalone_isolated_empirical_detects_mismatch_with_zero_writes(tmp_path: Path) -> None:
    """Isolated empirical complexity check catches O(n)-claimed O(n^2) solution without writing .autocode."""
    prob = _write_basic_standalone_problem(
        tmp_path / "emp_mismatch",
        sol_code=(
            "// Time Complexity: O(n)\n"
            "#include <iostream>\n"
            "int main() {\n"
            "    long long n = 0;\n"
            "    if (!(std::cin >> n)) return 0;\n"
            "    volatile long long acc = 0;\n"
            "    for (long long i = 0; i < n; ++i) {\n"
            "        for (long long j = 0; j < n; ++j) {\n"
            "            acc = (acc * 1103515245LL + (i ^ j) + 12345LL) & 0x7fffffffLL;\n"
            "        }\n"
            "    }\n"
            "    std::cout << (n + (acc & 0)) << '\\n';\n"
            "    return 0;\n"
            "}\n"
        ),
        val_code=(
            "#include <iostream>\n"
            "int main() { long long n; return (std::cin >> n && n >= 1 && n <= 6000) ? 0 : 1; }\n"
        ),
        wrong_code=(
            "#include <iostream>\n"
            "int main() { long long n; std::cin >> n; std::cout << (n + 1) << '\\n'; return 0; }\n"
        ),
        gen_code=(
            "#include <cstdlib>\n"
            "#include <iostream>\n"
            "int main(int argc, char* argv[]) {\n"
            "    long long n = argc > 3 ? std::atoll(argv[3]) : 10;\n"
            "    std::cout << n << '\\n';\n"
            "    return 0;\n"
            "}\n"
        ),
        statement_text="Given integer n (1 <= n <= 6000), output n.\n",
        tests=[("01", "5\n", "5\n"), ("02", "50\n", "50\n"), ("03", "500\n", "500\n")],
    )

    before = _snapshot_tree(prob)
    import asyncio

    out = asyncio.run(ProblemAuditTool().execute(str(prob), mode="full", standalone=True))
    after = _snapshot_tree(prob)

    assert before == after
    assert not (prob / ".autocode").exists()
    emp = out.data["quality_signals"]["empirical_complexity"]
    assert emp["executed"] is True
    assert emp["evidence"]["isolated_standalone"] is True
    assert emp["passed"] is False, f"unexpected standalone empirical pass: {emp}"
    assert out.data["decision"] == "no_go"
    assert any(b["gate"] == "empirical_complexity" for b in out.data["blocking_issues"])


def test_standalone_completeness_warnings_and_strict_cli_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing validator/wrong-solutions warn by default and become blocking under --strict."""
    incomplete = _write_basic_standalone_problem(
        tmp_path / "incomplete_pkg",
        sol_code=(
            "// Time Complexity: O(1)\n"
            "#include <iostream>\n"
            "int main() { long long n; if (std::cin >> n) std::cout << n << '\\n'; return 0; }\n"
        ),
        val_code=None,
        wrong_code=None,
    )

    rep_lenient = tmp_path / "lenient.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["autocode-audit", str(incomplete), "--standalone", "--report", str(rep_lenient)],
    )
    code_lenient = cli_audit_main()
    assert code_lenient == 0
    lenient_data = json.loads(rep_lenient.read_text(encoding="utf-8"))
    assert lenient_data["decision"] == "go"
    assert lenient_data["strict"] is False
    warning_gates = {w["gate"] for w in lenient_data["completeness_warnings"]}
    assert warning_gates == {"validator_check", "wrong_solution_kill"}

    rep_strict = tmp_path / "strict.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autocode-audit",
            str(incomplete),
            "--standalone",
            "--strict",
            "--report",
            str(rep_strict),
        ],
    )
    code_strict = cli_audit_main()
    assert code_strict == 2
    strict_data = json.loads(rep_strict.read_text(encoding="utf-8"))
    assert strict_data["decision"] == "no_go"
    assert strict_data["strict"] is True
    blocking_gates = {b["gate"] for b in strict_data["blocking_issues"]}
    assert {"validator_check", "wrong_solution_kill"} <= blocking_gates

    # Complete package passes under --strict with empty completeness_warnings
    complete = _write_basic_standalone_problem(
        tmp_path / "complete_pkg",
        sol_code=(
            "// Time Complexity: O(1)\n"
            "#include <iostream>\n"
            "int main() { long long n; if (std::cin >> n) std::cout << n << '\\n'; return 0; }\n"
        ),
        val_code=(
            "#include <iostream>\n"
            "int main() { long long n; return (std::cin >> n && n >= 1 && n <= 100000) ? 0 : 1; }\n"
        ),
        wrong_code=(
            "#include <iostream>\n"
            "int main() { long long n; std::cin >> n; std::cout << (n + 1) << '\\n'; return 0; }\n"
        ),
    )
    rep_ok = tmp_path / "strict_ok.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "autocode-audit",
            str(complete),
            "--standalone",
            "--strict",
            "--report",
            str(rep_ok),
        ],
    )
    code_ok = cli_audit_main()
    assert code_ok == 0
    ok_data = json.loads(rep_ok.read_text(encoding="utf-8"))
    assert ok_data["decision"] == "go"
    assert ok_data["completeness_warnings"] == []


def test_statement_consistency_forwards_review_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """audit.py must forward _statement_consistency()['review_reason'] when present."""
    prob = _write_basic_standalone_problem(
        tmp_path / "reason_pkg",
        sol_code=(
            "// Time Complexity: O(1)\n"
            "#include <iostream>\n"
            "int main() { long long n; if (std::cin >> n) std::cout << n << '\\n'; return 0; }\n"
        ),
    )
    custom_reason = "Custom comparator ordering in statement requires manual semantic check"

    def _fake_stmt_consistency(_self: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "passed": True,
            "needs_human_review": True,
            "review_reason": custom_reason,
            "message": "heuristic warning",
            "findings": [],
        }

    monkeypatch.setattr(ProblemAuditTool, "_statement_consistency", _fake_stmt_consistency)
    import asyncio

    res = asyncio.run(ProblemAuditTool().execute(str(prob), mode="quick", standalone=True))
    stmt_warnings = [
        w["reason"]
        for w in res.data["risk_report"]["warnings"]
        if w.get("gate") == "statement_consistency"
    ]
    assert custom_reason in stmt_warnings
