"""Tests for exact expected_verdict enforcement, isolated C++ coverage, jngen.h, and red-team assets."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from autocode_mcp import TEMPLATES_DIR
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.utils import coverage as cov_mod
from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.coverage import (
    _parse_gcov_text,
    discover_coverage_backends,
    format_line_ranges,
    run_solution_coverage,
)
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow import AutoCodeManifest, save_manifest
from autocode_mcp.workflow.models import SolutionEntry


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Capture relative file paths and (size, mtime_ns) across ``root``."""
    snap: dict[str, tuple[int, int]] = {}
    for item in sorted(root.rglob("*")):
        if item.is_file():
            st = item.stat()
            snap[item.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns)
    return snap


def _setup_problem_base(
    tmp_path: Path,
    name: str,
    *,
    sol_code: str,
    tests: list[tuple[str, str]],
    time_limit_ms: int = 1000,
    min_branch_coverage: float | None = None,
    solutions: list[SolutionEntry] | None = None,
) -> Path:
    pdir = tmp_path / name
    (pdir / "solutions").mkdir(parents=True)
    (pdir / "files").mkdir(parents=True)
    (pdir / "tests").mkdir(parents=True)
    (pdir / "statements").mkdir(parents=True)
    (pdir / ".autocode").mkdir(parents=True)

    (pdir / "solutions" / "sol.cpp").write_text(sol_code, encoding="utf-8")
    for stem, (inp, ans) in enumerate(tests, 1):
        (pdir / "tests" / f"{stem:02d}.in").write_text(inp, encoding="utf-8")
        (pdir / "tests" / f"{stem:02d}.ans").write_text(ans, encoding="utf-8")

    manifest = AutoCodeManifest(
        problem_name=name,
        time_limit_ms=time_limit_ms,
        memory_limit_mb=256,
        solutions=solutions or [SolutionEntry(name="sol", role="main", path="solutions/sol.cpp")],
    )
    if min_branch_coverage is not None:
        manifest.audit_gates.min_branch_coverage = min_branch_coverage
    manifest.quality_gates.require_limit_semantics = False
    save_manifest(str(pdir), manifest)
    return pdir


# ===========================================================================
# 1. Exact expected_verdict Enforcement
# ===========================================================================


@pytest.mark.asyncio
async def test_exact_verdict_tle_killed_by_re_fails_check(tmp_path: Path) -> None:
    """Wrong solution declaring expected_verdict='TLE' that crashes with RE must fail."""
    sol_code = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 0;\n"
        "    std::cout << (n * 2) << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    # Intentionally exits non-zero (RE) when n > 10 instead of timing out (TLE).
    wrong_re_code = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 0;\n"
        "    if (n > 10) return 1;\n"
        "    std::cout << (n * 2) << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    pdir = _setup_problem_base(
        tmp_path,
        "tle_vs_re",
        sol_code=sol_code,
        tests=[("5\n", "10\n"), ("1000\n", "2000\n")],
        time_limit_ms=1000,
        solutions=[
            SolutionEntry(name="sol", role="main", path="solutions/sol.cpp"),
            SolutionEntry(
                name="wrong_tle",
                role="wrong",
                path="solutions/wrong_tle.cpp",
                expected="fail",
                expected_verdict="TLE",
            ),
        ],
    )
    (pdir / "solutions" / "wrong_tle.cpp").write_text(wrong_re_code, encoding="utf-8")

    exe_ext = get_exe_extension()
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "sol.cpp"),
            str(pdir / "solutions" / f"sol{exe_ext}"),
        )
    ).success
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "wrong_tle.cpp"),
            str(pdir / "solutions" / f"wrong_tle{exe_ext}"),
        )
    ).success

    verifier = ProblemVerifyTestsTool()
    res = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["wrong_solution_kill"],
        enable_limit_ratio=False,
    )
    assert not res.success
    wk = res.data["results"]["wrong_solution_kill"]
    assert wk["passed"] is False
    detail = wk["details"][0]
    assert detail["name"] == "wrong_tle"
    assert detail["killed"] is True
    assert detail["killing_verdict"] == "RE"
    assert detail["expected_verdict"] == "TLE"
    assert detail["expected_verdict_matched"] is False
    assert "TLE" in detail["reason"] and "RE" in detail["reason"]
    assert wk["verdict_mismatches"][0]["actual_verdict"] == "RE"


@pytest.mark.asyncio
async def test_exact_verdict_matching_tle_wa_pass_and_none_compat(tmp_path: Path) -> None:
    """Matching expected_verdict ('TLE', 'WA', 'AC'+expected='pass') and None compat pass."""
    sol_code = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 0;\n"
        "    std::cout << (n + 1) << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    wrong_tle_code = (
        "#include <chrono>\n"
        "#include <iostream>\n"
        "#include <thread>\n"
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 0;\n"
        "    if (n >= 100) std::this_thread::sleep_for(std::chrono::milliseconds(1600));\n"
        "    std::cout << (n + 1) << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    wrong_wa_code = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 0;\n"
        "    std::cout << (n == 100 ? n : n + 1) << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    wrong_legacy_re_code = (
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 0;\n"
        "    if (n >= 100) return 1;\n"
        "    std::cout << (n + 1) << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    alt_ac_code = sol_code

    pdir = _setup_problem_base(
        tmp_path,
        "exact_match",
        sol_code=sol_code,
        tests=[("5\n", "6\n"), ("100\n", "101\n")],
        time_limit_ms=1000,
        solutions=[
            SolutionEntry(name="sol", role="main", path="solutions/sol.cpp"),
            SolutionEntry(
                name="wrong_tle",
                role="wrong",
                path="solutions/wrong_tle.cpp",
                expected="fail",
                expected_verdict="TLE",
            ),
            SolutionEntry(
                name="wrong_wa",
                role="wrong",
                path="solutions/wrong_wa.cpp",
                expected="fail",
                expected_verdict="WA",
            ),
            SolutionEntry(
                name="wrong_legacy",
                role="wrong",
                path="solutions/wrong_legacy.cpp",
                expected="fail",
                expected_verdict=None,
            ),
            SolutionEntry(
                name="wrong_pass_ok",
                role="wrong",
                path="solutions/wrong_pass_ok.cpp",
                expected="pass",
                expected_verdict="AC",
            ),
        ],
    )
    (pdir / "solutions" / "wrong_tle.cpp").write_text(wrong_tle_code, encoding="utf-8")
    (pdir / "solutions" / "wrong_wa.cpp").write_text(wrong_wa_code, encoding="utf-8")
    (pdir / "solutions" / "wrong_legacy.cpp").write_text(wrong_legacy_re_code, encoding="utf-8")
    (pdir / "solutions" / "wrong_pass_ok.cpp").write_text(alt_ac_code, encoding="utf-8")

    exe_ext = get_exe_extension()
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "sol.cpp"),
            str(pdir / "solutions" / f"sol{exe_ext}"),
        )
    ).success

    verifier = ProblemVerifyTestsTool()
    res = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["wrong_solution_kill"],
        enable_limit_ratio=False,
    )
    assert res.success, res.data
    wk = res.data["results"]["wrong_solution_kill"]
    assert wk["passed"] is True
    assert "verdict_mismatches" not in wk
    by_name = {item["name"]: item for item in wk["details"]}
    assert by_name["wrong_tle"]["killing_verdict"] == "TLE"
    assert by_name["wrong_tle"]["expected_verdict"] == "TLE"
    assert by_name["wrong_tle"]["expected_verdict_matched"] is True

    assert by_name["wrong_wa"]["killing_verdict"] == "WA"
    assert by_name["wrong_wa"]["expected_verdict"] == "WA"
    assert by_name["wrong_wa"]["expected_verdict_matched"] is True

    assert by_name["wrong_legacy"]["killing_verdict"] == "RE"
    assert by_name["wrong_legacy"]["expected_verdict"] is None
    assert by_name["wrong_legacy"]["expected_verdict_matched"] is True

    assert by_name["wrong_pass_ok"]["killed"] is True
    assert by_name["wrong_pass_ok"]["expected_verdict"] == "AC"
    assert by_name["wrong_pass_ok"]["expected_verdict_matched"] is True


@pytest.mark.asyncio
async def test_exact_verdict_survivor_also_fails_expected_verdict(tmp_path: Path) -> None:
    """A wrong solution expecting WA that gets AC on all tests fails expected_verdict_matched."""
    sol_code = (
        "#include <iostream>\n"
        "int main() { int x; std::cin >> x; std::cout << x << '\\n'; return 0; }\n"
    )
    pdir = _setup_problem_base(
        tmp_path,
        "survivor_verdict",
        sol_code=sol_code,
        tests=[("1\n", "1\n")],
        solutions=[
            SolutionEntry(name="sol", role="main", path="solutions/sol.cpp"),
            SolutionEntry(
                name="wrong_survives",
                role="wrong",
                path="solutions/wrong_survives.cpp",
                expected="fail",
                expected_verdict="WA",
            ),
        ],
    )
    (pdir / "solutions" / "wrong_survives.cpp").write_text(sol_code, encoding="utf-8")
    exe_ext = get_exe_extension()
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "sol.cpp"),
            str(pdir / "solutions" / f"sol{exe_ext}"),
        )
    ).success

    verifier = ProblemVerifyTestsTool()
    res = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["wrong_solution_kill"],
        enable_limit_ratio=False,
    )
    assert not res.success
    wk = res.data["results"]["wrong_solution_kill"]
    assert wk["passed"] is False
    detail = wk["details"][0]
    assert detail["killed"] is False
    assert detail["expected_verdict"] == "WA"
    assert detail["expected_verdict_matched"] is False


# ===========================================================================
# 2. Isolated Coverage Engine & min_branch_coverage Gate
# ===========================================================================


_MULTI_BRANCH_SOL = """#include <iostream>

int classify(int x) {
    if (x < 0) {
        int y = -x;
        return y + 10;
    }
    if (x == 0) {
        return 42;
    }
    if (x > 100 && x % 2 == 0) {
        return x / 2;
    }
    return x * 3;
}

int main() {
    int n;
    if (std::cin >> n) {
        std::cout << classify(n) << '\\n';
    }
    return 0;
}
"""


@pytest.mark.asyncio
async def test_isolated_coverage_reports_uncovered_lines_and_writes_zero_files(
    tmp_path: Path,
) -> None:
    """Real C++ coverage detects uncovered branches/lines and writes nothing inside problem_dir."""
    backends = discover_coverage_backends()
    if not backends:
        pytest.skip("No C++ coverage toolchain (clang++/llvm-cov or g++/gcov) on host")

    pdir = _setup_problem_base(
        tmp_path,
        "cov_partial",
        sol_code=_MULTI_BRANCH_SOL,
        tests=[("5\n", "15\n")],
    )
    before_snap = _snapshot_tree(pdir)

    cov = await run_solution_coverage(
        problem_dir=str(pdir),
        tests_dir=str(pdir / "tests"),
    )
    after_snap = _snapshot_tree(pdir)

    assert before_snap == after_snap, (
        f"Coverage run must never write into problem_dir! "
        f"added={set(after_snap) - set(before_snap)}"
    )
    assert cov["status"] == "ok", cov
    assert cov["passed"] is True
    assert cov["isolated_dir_used"] is True
    assert cov["tests_executed"] == 1
    assert 0.0 < cov["line_coverage"] < 1.0
    assert 0.0 < cov["branch_coverage"] < 1.0
    assert cov["lines_covered"] < cov["lines_total"]
    assert cov["branches_covered"] < cov["branches_total"]
    # Uncovered branches in classify(): lines 5-6 (x < 0), 9 (x == 0), 12 (x > 100 && even)
    for expected_uncovered in (5, 9, 12):
        assert expected_uncovered in cov["uncovered_lines"]
    assert len(cov["uncovered_line_ranges"]) >= 3
    assert len(cov["uncovered_branches"]) >= 1
    assert not list(pdir.rglob("*.gcda"))
    assert not list(pdir.rglob("*.gcno"))
    assert not list(pdir.rglob("*.profraw"))
    assert not list(pdir.rglob("*.profdata"))


@pytest.mark.asyncio
async def test_isolated_coverage_gcc_gcov_backend_if_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the GCC/gcov fallback backend end-to-end when available on host."""
    gcc_backends = [b for b in discover_coverage_backends() if b.kind == "gcc_gcov"]
    if not gcc_backends:
        pytest.skip("No gcc_gcov backend available on host")

    pdir = _setup_problem_base(
        tmp_path,
        "cov_gcc_fallback",
        sol_code=_MULTI_BRANCH_SOL,
        tests=[("5\n", "15\n")],
    )
    before_snap = _snapshot_tree(pdir)
    monkeypatch.setattr(cov_mod, "discover_coverage_backends", lambda **_kw: [gcc_backends[0]])

    cov = await run_solution_coverage(
        problem_dir=str(pdir),
        tests_dir=str(pdir / "tests"),
    )
    after_snap = _snapshot_tree(pdir)
    assert before_snap == after_snap
    assert cov["status"] == "ok", cov
    assert cov["backend"] == "gcc_gcov"
    assert 0.0 < cov["line_coverage"] < 1.0
    assert 0.0 < cov["branch_coverage"] < 1.0
    assert 5 in cov["uncovered_lines"]
    assert 9 in cov["uncovered_lines"]
    assert 12 in cov["uncovered_lines"]
    assert len(cov["uncovered_branches"]) >= 1


@pytest.mark.asyncio
async def test_test_verify_coverage_wiring_and_min_branch_coverage_gate(tmp_path: Path) -> None:
    """ProblemVerifyTestsTool wires coverage optionally and enforces audit_gates.min_branch_coverage."""
    if not discover_coverage_backends():
        pytest.skip("No C++ coverage toolchain available on host")

    # 1. Partial tests + min_branch_coverage=0.9 -> default verify_types=None auto-runs coverage & fails
    pdir = _setup_problem_base(
        tmp_path,
        "cov_gate",
        sol_code=_MULTI_BRANCH_SOL,
        tests=[("5\n", "15\n")],
        min_branch_coverage=0.90,
    )
    exe_ext = get_exe_extension()
    assert (
        await compile_cpp(
            str(pdir / "solutions" / "sol.cpp"),
            str(pdir / "solutions" / f"sol{exe_ext}"),
        )
    ).success

    verifier = ProblemVerifyTestsTool()
    res_fail = await verifier.execute(problem_dir=str(pdir), enable_limit_ratio=False)
    assert not res_fail.success
    assert "coverage" in res_fail.data["results"]
    cov_fail = res_fail.data["results"]["coverage"]
    assert cov_fail["status"] == "ok"
    assert cov_fail["passed"] is False
    assert cov_fail["branch_coverage"] < 0.90
    assert res_fail.data["quality_signals"]["coverage"]["executed"] is True
    assert res_fail.data["quality_signals"]["coverage"]["passed"] is False

    # 2. Add tests covering all remaining branches -> coverage passes!
    (pdir / "tests" / "02.in").write_text("-3\n", encoding="utf-8")
    (pdir / "tests" / "02.ans").write_text("13\n", encoding="utf-8")
    (pdir / "tests" / "03.in").write_text("0\n", encoding="utf-8")
    (pdir / "tests" / "03.ans").write_text("42\n", encoding="utf-8")
    (pdir / "tests" / "04.in").write_text("102\n", encoding="utf-8")
    (pdir / "tests" / "04.ans").write_text("51\n", encoding="utf-8")
    (pdir / "tests" / "05.in").write_text("103\n", encoding="utf-8")
    (pdir / "tests" / "05.ans").write_text("309\n", encoding="utf-8")
    (pdir / "tests" / "06.in").write_text("", encoding="utf-8")
    (pdir / "tests" / "06.ans").write_text("", encoding="utf-8")

    res_pass = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["coverage"],
        enable_limit_ratio=False,
    )
    assert res_pass.success, res_pass.data
    cov_pass = res_pass.data["results"]["coverage"]
    assert cov_pass["status"] == "ok"
    assert cov_pass["passed"] is True
    assert cov_pass["branch_coverage"] >= 0.90
    assert cov_pass["uncovered_lines"] == []

    # 3. Without min_branch_coverage and verify_types=None, default run does NOT run coverage
    pdir_fast = _setup_problem_base(
        tmp_path,
        "cov_default_off",
        sol_code=_MULTI_BRANCH_SOL,
        tests=[("5\n", "15\n")],
        min_branch_coverage=None,
    )
    assert (
        await compile_cpp(
            str(pdir_fast / "solutions" / "sol.cpp"),
            str(pdir_fast / "solutions" / f"sol{exe_ext}"),
        )
    ).success
    res_fast = await verifier.execute(problem_dir=str(pdir_fast), enable_limit_ratio=False)
    assert res_fast.success, res_fast.data
    assert "coverage" not in res_fast.data["results"]


@pytest.mark.asyncio
async def test_coverage_unavailable_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing host coverage tools yields status='unavailable' (passes if optional, fails if gated)."""
    monkeypatch.setattr(cov_mod, "discover_coverage_backends", lambda **_kw: [])

    pdir_opt = _setup_problem_base(
        tmp_path,
        "cov_unavail_opt",
        sol_code=_MULTI_BRANCH_SOL,
        tests=[("5\n", "15\n")],
        min_branch_coverage=None,
    )
    verifier = ProblemVerifyTestsTool()
    res_opt = await verifier.execute(
        problem_dir=str(pdir_opt),
        verify_types=["coverage"],
        enable_limit_ratio=False,
    )
    assert res_opt.success, res_opt.data
    assert res_opt.data["results"]["coverage"]["status"] == "unavailable"
    assert res_opt.data["results"]["coverage"]["passed"] is True

    pdir_req = _setup_problem_base(
        tmp_path,
        "cov_unavail_req",
        sol_code=_MULTI_BRANCH_SOL,
        tests=[("5\n", "15\n")],
        min_branch_coverage=0.80,
    )
    res_req = await verifier.execute(
        problem_dir=str(pdir_req),
        verify_types=["coverage"],
        enable_limit_ratio=False,
    )
    assert not res_req.success
    assert res_req.data["results"]["coverage"]["status"] == "unavailable"
    assert res_req.data["results"]["coverage"]["passed"] is False


def test_gcov_parser_and_line_range_compression() -> None:
    """Verify gcov text parsing (including ===== and never executed) and range formatting."""
    sample_gcov = (
        "        -:    0:Source:sol.cpp\n"
        "        -:    1:#include <iostream>\n"
        "function main called 1 returned 100% blocks executed 60%\n"
        "        1:    2:int main() {\n"
        "        1:    3:    if (x > 0) {\n"
        "branch  0 taken 100%\n"
        "branch  1 taken 0% (fallthrough)\n"
        "    #####:    4:        return 1;\n"
        "    =====:    5:        throw 1;\n"
        "branch  2 never executed\n"
        "        -:    6:    }\n"
        "        1:    7:    return 0;\n"
    )
    parsed = _parse_gcov_text(sample_gcov)
    assert parsed["lines_total"] == 5
    assert parsed["lines_covered"] == 3
    assert parsed["uncovered_lines"] == [4, 5]
    assert parsed["uncovered_line_ranges"] == ["4-5"]
    assert parsed["branches_total"] == 3
    assert parsed["branches_covered"] == 1
    assert len(parsed["uncovered_branches"]) == 2
    assert format_line_ranges([2, 4, 5, 6, 9, 10, 12]) == ["2", "4-6", "9-10", "12"]


# ===========================================================================
# 3. jngen.h Compilation & Execution (C++17 + testlib.h + Anti-Hash)
# ===========================================================================


def test_jngen_header_compiles_with_testlib_and_emits_antihash(tmp_path: Path) -> None:
    """Compile and execute a C++17 generator combining testlib.h and jngen.h."""
    assert (Path(TEMPLATES_DIR) / "jngen.h").is_file()

    gen_src = tmp_path / "gen_jngen.cpp"
    gen_src.write_text(
        r"""
#include "testlib.h"
#include "jngen.h"
#include <cassert>
#include <cstdint>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>
#include <utility>
#include <vector>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    jngen::set_seed(static_cast<std::uint64_t>(rnd.next(1, 1000000)));

    // 1. Permutation, distinct_array extremes, & tree
    auto perm = jngen::permutation(8, 1);
    assert(perm.size() == 8);
    auto d_empty = jngen::distinct_array(0, std::numeric_limits<long long>::min(),
                                          std::numeric_limits<long long>::max());
    assert(d_empty.empty());
    auto d_full = jngen::distinct_array(5, std::numeric_limits<long long>::min(),
                                         std::numeric_limits<long long>::max());
    assert(d_full.size() == 5);
    assert(std::set<long long>(d_full.begin(), d_full.end()).size() == 5);
    auto tree = jngen::random_tree(12, 1);
    assert(tree.n == 12 && tree.edges.size() == 11);
    auto prufer = jngen::prufer_tree(10);
    assert(prufer.edges.size() == 9);

    // 2. Graph, DAG, and edge-case topology
    auto g = jngen::random_graph(10, 18, false, true);
    assert(g.edges.size() == 18);
    auto dag = jngen::random_dag(10, 20, true, true, false);
    assert(dag.edges.size() == 20);
    for (const auto& e : dag.edges) {
        assert(e.first < e.second);
    }
    auto ecg = jngen::edge_case_graph(6, 5, true, true, 1);
    assert(ecg.edges.size() == 5);

    // 3. Anti-hash: splitmix64 probe & low-bit collisions
    auto probes = jngen::splitmix64_probe(99ULL, 5);
    assert(probes.size() == 5);
    auto sm_cols = jngen::splitmix64_lowbit_collisions(17ULL, 4, 8, 5ULL);
    for (auto k : sm_cols) {
        assert((jngen::splitmix64(k ^ 17ULL) & 255ULL) == 5ULL);
    }

    // 4. Anti-hash: GCC unordered_map prime bucket collisions (single & multi-prime)
    const long long prime = 107897LL;
    auto umap_keys = jngen::anti_unordered_map_sequence(25, prime, 1000000000LL, 3LL);
    std::set<long long> uniq(umap_keys.begin(), umap_keys.end());
    assert(uniq.size() == 25);
    for (auto v : umap_keys) {
        assert(v % prime == 3LL && v <= 1000000000LL);
    }

    auto multi_distinct = jngen::anti_unordered_map_multi_prime(20);
    assert(multi_distinct.size() == 20);
    assert(std::set<long long>(multi_distinct.begin(), multi_distinct.end()).size() == 20);
    for (auto v : multi_distinct) {
        assert(v >= 1 && v <= 1000000000LL);
        assert(v % 107897LL == 0LL || v % 126271LL == 0LL);
    }

    // Exceeding distinct multiples under small max_val must terminate cleanly via bounded cycle.
    auto multi_wrapped = jngen::anti_unordered_map_multi_prime(50, 500000LL);
    assert(multi_wrapped.size() == 50);
    assert(std::set<long long>(multi_wrapped.begin(), multi_wrapped.end()).size() == 7);
    for (auto v : multi_wrapped) {
        assert(v >= 1 && v <= 500000LL);
        assert(v % 107897LL == 0LL || v % 126271LL == 0LL);
    }

    // max_val < min(prime_buckets) must throw std::invalid_argument instead of spinning.
    bool threw_small_max = false;
    try {
        (void)jngen::anti_unordered_map_multi_prime(10, 100000LL);
    } catch (const std::invalid_argument&) {
        threw_small_max = true;
    }
    assert(threw_small_max);

    // 5. Anti-hash: Thue-Morse odd-base uint64_t polynomial collision
    auto tm_pair = jngen::thue_morse_collision_pair(11);
    assert(tm_pair.first.size() == 2048 && tm_pair.first != tm_pair.second);
    std::uint64_t h1 = 0ULL, h2 = 0ULL;
    const std::uint64_t base = 13331ULL;
    for (char ch : tm_pair.first) h1 = h1 * base + static_cast<unsigned char>(ch);
    for (char ch : tm_pair.second) h2 = h2 * base + static_cast<unsigned char>(ch);
    assert(h1 == h2);

    // 6. Anti-hash: single-mod polynomial collision
    const std::uint64_t mod = 1000000007ULL;
    auto mod_pair = jngen::anti_single_mod_hash_pair(mod, 131ULL, 16, 1ULL);
    assert(mod_pair.first != mod_pair.second);
    std::uint64_t m1 = 0ULL, m2 = 0ULL;
    for (char ch : mod_pair.first) m1 = (m1 * 131ULL + static_cast<unsigned char>(ch)) % mod;
    for (char ch : mod_pair.second) m2 = (m2 * 131ULL + static_cast<unsigned char>(ch)) % mod;
    assert(m1 == m2);

    std::cout << tree.edges.size() << ' ' << umap_keys[0] << ' ' << tm_pair.first.substr(0, 8) << '\n';
    return 0;
}
""",
        encoding="utf-8",
    )
    exe_ext = get_exe_extension()
    gen_bin = tmp_path / f"gen_jngen{exe_ext}"
    comp = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-Wall",
            "-Wextra",
            "-isystem",
            TEMPLATES_DIR,
            str(gen_src),
            "-o",
            str(gen_bin),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert comp.returncode == 0, comp.stderr

    r1 = subprocess.run([str(gen_bin), "123"], capture_output=True, text=True, timeout=15, check=False)
    r2 = subprocess.run([str(gen_bin), "123"], capture_output=True, text=True, timeout=15, check=False)
    assert r1.returncode == 0, r1.stderr
    assert r1.stdout == r2.stdout
    assert r1.stdout.strip().startswith("11 3 ")

    # Also verify standalone inclusion (without testlib.h) under g++ -std=c++17.
    standalone_src = tmp_path / "gen_standalone.cpp"
    standalone_src.write_text(
        '#include "jngen.h"\n'
        "#include <iostream>\n"
        "int main() {\n"
        '    std::cout << jngen::thue_morse_string(8) << "\\n";\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    standalone_bin = tmp_path / f"gen_standalone{exe_ext}"
    comp_solo = subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-isystem",
            TEMPLATES_DIR,
            str(standalone_src),
            "-o",
            str(standalone_bin),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert comp_solo.returncode == 0, comp_solo.stderr
    solo_run = subprocess.run([str(standalone_bin)], capture_output=True, text=True, timeout=10, check=False)
    assert solo_run.returncode == 0
    assert solo_run.stdout.strip() == "abbabaab"


# ===========================================================================
# 4. Red-Team Agent & Skill Assets
# ===========================================================================


def _parse_frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} missing YAML frontmatter"
    _, fm_raw, body = text.split("---\n", 2)
    fm = yaml.safe_load(fm_raw)
    assert isinstance(fm, dict)
    return fm, body


def test_redteam_agent_and_skill_frontmatter_and_modes() -> None:
    """autocode-redteam-auditor.md and skills/redteam-hacking/SKILL.md match plugin conventions."""
    repo_root = Path(__file__).resolve().parents[1]
    agent_path = repo_root / "agents" / "autocode-redteam-auditor.md"
    skill_path = repo_root / "skills" / "redteam-hacking" / "SKILL.md"
    assert agent_path.is_file()
    assert skill_path.is_file()

    agent_fm, agent_body = _parse_frontmatter(agent_path)
    assert agent_fm["name"] == "autocode-redteam-auditor"
    assert isinstance(agent_fm["description"], str) and agent_fm["description"].strip()
    assert "redteam-hacking" in agent_fm["skills"]
    assert agent_fm["model"] == "inherit"
    for keyword in ("Black-Box", "White-Box", "Hack", "expected_verdict", "tests/hack/"):
        assert keyword in agent_body

    skill_fm, skill_body = _parse_frontmatter(skill_path)
    assert skill_fm["name"] == "redteam-hacking"
    assert isinstance(skill_fm["description"], str) and skill_fm["description"].strip()
    for keyword in (
        "Black-Box",
        "White-Box",
        "unordered_map",
        "jngen",
        "expected_verdict",
        "tests/hack/",
        "wrong_*.cpp",
    ):
        assert keyword in skill_body


def test_parse_gcov_text_skips_compiler_throw_branches() -> None:
    """_parse_gcov_text ignores GCC synthetic C++ exception-unwinding (throw) branches."""
    sample = "\n".join(
        [
            "        -:    0:Source:sol.cpp",
            "        6:    1:int main() {",
            "        6:    2:    if (std::cin >> n) {",
            "call    0 returned 100%",
            "branch  1 taken 100% (fallthrough)",
            "branch  2 taken 0% (throw)",
            "branch  3 never executed (throw)",
            "branch  4 taken 83% (fallthrough)",
            "branch  5 taken 17%",
            "        5:    3:        std::cout << n << '\\n';",
            "        -:    4:    }",
            "        6:    5:    return 0;",
            "        -:    6:}",
        ]
    )
    metrics = _parse_gcov_text(sample)
    assert metrics["lines_covered"] == 4
    assert metrics["lines_total"] == 4
    assert metrics["line_coverage"] == 1.0
    assert metrics["branches_covered"] == 3
    assert metrics["branches_total"] == 3
    assert metrics["branch_coverage"] == 1.0
    assert metrics["uncovered_branches"] == []


def test_find_gcov_tool_prefers_compiler_sibling_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Toolset / MinGW compilers resolve their own sibling gcov rather than mismatched PATH gcov."""
    toolset_bin = tmp_path / "gcc-toolset-15" / "root" / "usr" / "bin"
    path_bin = tmp_path / "usr_bin"
    toolset_bin.mkdir(parents=True)
    path_bin.mkdir(parents=True)

    gxx = toolset_bin / ("g++.exe" if cov_mod.sys.platform == "win32" else "g++")
    sib_gcov = toolset_bin / ("gcov.exe" if cov_mod.sys.platform == "win32" else "gcov")
    path_gcov = path_bin / "gcov"
    for p in (gxx, sib_gcov, path_gcov):
        p.write_text("#!/bin/sh\n", encoding="utf-8")
        p.chmod(0o755)

    monkeypatch.setattr(cov_mod.shutil, "which", lambda name: str(path_gcov) if name == "gcov" else None)
    assert cov_mod.find_gcov_tool(str(gxx)) == str(sib_gcov)

