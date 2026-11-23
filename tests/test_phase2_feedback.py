"""Regression tests for Phase 2 feedback items (NEW-1 through NEW-8)."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from autocode_mcp.runtime_store import WORKFLOW, set_section
from autocode_mcp.tools.audit import ProblemAuditTool, _extract_validator_bounds
from autocode_mcp.tools.complexity import (
    ComplexityLevel,
    SolutionAnalyzeTool,
    estimate_memory_usage,
    estimate_static_complexity,
)
from autocode_mcp.tools.problem import ProblemCreateTool, ProblemPackPolygonTool
from autocode_mcp.tools.solution_audit import SolutionAuditBruteTool, SolutionAuditStdTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.utils.compiler import compile_cpp, run_binary
from autocode_mcp.utils.exporters import verify_exported_packages
from autocode_mcp.utils.mutation import generate_cpp_mutants, run_mutation_testing
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow import default_manifest, load_manifest, save_manifest


def _make_problem(tmp_path: Path, name: str = "prob", **manifest_kwargs: Any) -> Path:
    pdir = tmp_path / name
    for sub in ("files", "solutions", "statements", "tests"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    manifest = default_manifest("Phase2 Sample", **manifest_kwargs)
    save_manifest(str(pdir), manifest)
    (pdir / "statements" / "README.md").write_text(
        "# Sample\n\n## 输入格式\n\n$1 \\le n \\le 10^5$。\n\n"
        "## 样例\n\n### 样例输入 #1\n```text\n3\n```\n\n### 样例输出 #1\n```text\n6\n```\n",
        encoding="utf-8",
    )
    (pdir / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n" + "Detailed algorithmic proof and complexity explanation. " * 4,
        encoding="utf-8",
    )
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<n*2<<'\\n';}\n",
        encoding="utf-8",
    )
    return pdir


# ============== NEW-1: RLIMIT_CORE=0, O(n^2*2^n) classification & brute crash hard-fail ==============


@pytest.mark.asyncio
async def test_new1_rlimit_core_zero_and_brute_exponential_and_crash_hard_fail(tmp_path):
    exe_ext = get_exe_extension()

    # 1. Verify RLIMIT_CORE == (0, 0) in POSIX child processes launched via run_binary
    if sys.platform != "win32":
        rlim_src = tmp_path / "check_core.cpp"
        rlim_bin = tmp_path / "check_core"
        rlim_src.write_text(
            "#include <iostream>\n#include <sys/resource.h>\n"
            "int main(){ struct rlimit rl; getrlimit(RLIMIT_CORE, &rl); "
            "std::cout << rl.rlim_cur << ' ' << rl.rlim_max << '\\n'; return 0; }\n",
            encoding="utf-8",
        )
        assert (await compile_cpp(str(rlim_src), str(rlim_bin))).success
        run_res = await run_binary(str(rlim_bin), "", timeout=5)
        assert run_res.success
        assert run_res.stdout.strip() == "0 0"

    # 2. O(n^2 * 2^n) bitmask brute with parenthesized shift (1LL << (n - 1)) is classified as O(2^n), NOT O(n^3)
    exp_brute_code = """
#include <iostream>
using namespace std;
long long dp[1 << 20];
int main() {
    int n; cin >> n;
    for (long long mask = 0; mask < (1LL << (n - 1)); mask++) {
        for (int i = 0; i < n; i++) {
            for (int j = 0; j < n; j++) {
                dp[mask] += i + j;
            }
        }
    }
    return 0;
}
"""
    est, pats = estimate_static_complexity(exp_brute_code)
    assert est == ComplexityLevel.EXPONENTIAL
    assert "bitmask" in pats

    brute_audit_exp = await SolutionAuditBruteTool().execute(code=exp_brute_code, std_complexity="O(n log n)")
    assert brute_audit_exp.success
    assert brute_audit_exp.data["brute_complexity"] == ComplexityLevel.EXPONENTIAL
    assert brute_audit_exp.data["recommended_stress_params"]["n_max"] <= 10

    # Backtracking recursion is also classified as exponential
    backtrack_code = """
#include <vector>
using namespace std;
int n; vector<int> used, path;
void dfs(int d) {
    if (d == n) return;
    for (int i = 0; i < n; i++) {
        if (!used[i]) {
            used[i] = 1;
            path.push_back(i);
            dfs(d + 1);
            path.pop_back();
            used[i] = 0;
        }
    }
}
int main() { dfs(0); return 0; }
"""
    bt_est, bt_pats = estimate_static_complexity(backtrack_code)
    assert bt_est == ComplexityLevel.EXPONENTIAL
    assert "backtracking" in bt_pats

    # 3. Crashing brute (SIGABRT / non-zero exit) hard-fails solution_audit_brute with passed=False
    pdir = _make_problem(tmp_path, "crashing_brute")
    (pdir / "files" / "gen.cpp").write_text(
        "#include <iostream>\n#include <cstdlib>\n"
        "int main(int argc, char** argv){ int n = argc > 3 ? std::atoi(argv[3]) : 2; std::cout << n << '\\n'; }\n",
        encoding="utf-8",
    )
    (pdir / "solutions" / "brute.cpp").write_text(
        "#include <cstdlib>\n#include <iostream>\n"
        "int main(){ int n; std::cin >> n; if (n >= 2) std::abort(); std::cout << n << '\\n'; }\n",
        encoding="utf-8",
    )
    assert (await compile_cpp(str(pdir / "files" / "gen.cpp"), str(pdir / "files" / f"gen{exe_ext}"))).success
    assert (await compile_cpp(str(pdir / "solutions" / "brute.cpp"), str(pdir / "solutions" / f"brute{exe_ext}"))).success

    crash_res = await SolutionAuditBruteTool().execute(problem_dir=str(pdir), constraints={"n_max": 10})
    assert crash_res.success
    assert crash_res.data["passed"] is False
    assert crash_res.data["brute_probe"]["crashed"] is True
    assert any(f["type"] == "brute_runtime_crash" for f in crash_res.data["findings"])

    # 4. Slow brute hits stepped timeout circuit-breaker cleanly and clamps recommended_n_max down
    (pdir / "solutions" / "brute.cpp").write_text(
        "#include <chrono>\n#include <iostream>\n#include <thread>\n"
        "int main(){ int n; std::cin >> n; "
        "if (n >= 6) std::this_thread::sleep_for(std::chrono::milliseconds(1500)); "
        "std::cout << n << '\\n'; }\n",
        encoding="utf-8",
    )
    assert (await compile_cpp(str(pdir / "solutions" / "brute.cpp"), str(pdir / "solutions" / f"brute{exe_ext}"), use_cache=False)).success
    slow_res = await SolutionAuditBruteTool().execute(problem_dir=str(pdir), constraints={"n_max": 12})
    assert slow_res.success
    assert slow_res.data["passed"] is True
    assert slow_res.data["brute_probe"]["status"] == "timeout_circuit_breaker"
    assert slow_res.data["recommended_stress_params"]["n_max"] <= 4


# ============== NEW-2: Empty wrong_solution_kill fails closed when required ==============


@pytest.mark.asyncio
async def test_new2_wrong_solution_kill_fails_closed_when_empty_and_required(tmp_path):
    pdir = _make_problem(tmp_path, "empty_wrong_kill")
    (pdir / "tests" / "01.in").write_text("3\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("6\n", encoding="utf-8")

    manifest_req = load_manifest(str(pdir))
    assert manifest_req is not None
    manifest_req.quality_gates.require_wrong_solution_kill = True
    save_manifest(str(pdir), manifest_req)

    verifier = ProblemVerifyTestsTool()
    res_req = await verifier._check_wrong_solution_kill(
        problem_dir=str(pdir),
        tests_dir=str(pdir / "tests"),
        wrong_solution_names=[],
        answer_ext=".ans",
        timeout=10,
        manifest=manifest_req,
    )
    assert res_req["passed"] is False
    assert res_req.get("missing_wrong_solutions") is True

    # Full execute() also fails when require_wrong_solution_kill=True and no wrong solutions exist
    full_res = await verifier.execute(
        problem_dir=str(pdir),
        verify_types=["wrong_solution_kill"],
        enable_limit_ratio=False,
    )
    assert not full_res.success
    assert full_res.data["results"]["wrong_solution_kill"]["passed"] is False

    # Disabling require_wrong_solution_kill allows clean skip
    manifest_req.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(pdir), manifest_req)
    res_opt = await verifier._check_wrong_solution_kill(
        problem_dir=str(pdir),
        tests_dir=str(pdir / "tests"),
        wrong_solution_names=[],
        answer_ext=".ans",
        timeout=10,
        manifest=manifest_req,
    )
    assert res_opt["passed"] is True
    assert res_opt.get("skipped") is True


# ============== NEW-3: problem_create protects existing manifest & honors TL/ML ==============


@pytest.mark.asyncio
async def test_new3_problem_create_refuses_silent_overwrite_and_honors_limits(tmp_path):
    pdir = tmp_path / "new3_prob"
    tool = ProblemCreateTool()

    res1 = await tool.execute(
        problem_dir=str(pdir),
        problem_name="Custom TL",
        time_limit_ms=1000,
        memory_limit_mb=512,
    )
    assert res1.success, res1.error
    assert res1.data["time_limit_ms"] == 1000
    assert res1.data["memory_limit_mb"] == 512
    m1 = load_manifest(str(pdir))
    assert m1 is not None and m1.time_limit_ms == 1000 and m1.memory_limit_mb == 512

    # Second call without overwrite=True MUST fail and leave 1000ms untouched
    res_dup = await tool.execute(
        problem_dir=str(pdir),
        problem_name="Accidental Overwrite",
    )
    assert not res_dup.success
    assert "overwrite=true" in (res_dup.error or "").lower()
    m_after_fail = load_manifest(str(pdir))
    assert m_after_fail is not None and m_after_fail.time_limit_ms == 1000

    # Explicit overwrite=True succeeds, updates limits, and emits warning
    res_ow = await tool.execute(
        problem_dir=str(pdir),
        problem_name="Intentional Reset",
        time_limit_ms=1500,
        overwrite=True,
    )
    assert res_ow.success, res_ow.error
    assert res_ow.data.get("overwritten_existing_manifest") is True
    assert "warning" in res_ow.data
    m_after_ow = load_manifest(str(pdir))
    assert m_after_ow is not None and m_after_ow.time_limit_ms == 1500


# ============== NEW-4: Standalone layout adapters + live temp compilation ==============


def test_new4_standalone_audit_standard_cn_layout_and_live_temp_compilation(tmp_path, monkeypatch, capsys):
    import asyncio

    from autocode_mcp.cli.audit import main as audit_cli_main

    cn_dir = tmp_path / "cn_pkg"
    (cn_dir / "data" / "test").mkdir(parents=True)
    (cn_dir / "solution").mkdir()
    (cn_dir / "statement").mkdir()
    (cn_dir / "validator").mkdir()

    (cn_dir / "statement" / "zh-cn.md").write_text(
        "# 中文题库标准布局\n\n$1 \\le n \\le 1000$。\n\n## 样例\n```text\n3\n```\n",
        encoding="utf-8",
    )
    (cn_dir / "solution" / "std.cpp").write_text(
        "#include <iostream>\nint main(){ int n; if (std::cin >> n) std::cout << n * 3 << '\\n'; }\n",
        encoding="utf-8",
    )
    (cn_dir / "validator" / "val.cpp").write_text(
        '#include "testlib.h"\nint main(int argc, char* argv[]){ registerValidation(argc, argv); '
        'inf.readInt(1, 1000, "n"); inf.readEoln(); inf.readEof(); }\n',
        encoding="utf-8",
    )
    for i, val in enumerate([1, 5, 42], 1):
        (cn_dir / "data" / "test" / f"test{i}.in").write_text(f"{val}\n", encoding="utf-8")
        (cn_dir / "data" / "test" / f"test{i}.out").write_text(f"{val * 3}\n", encoding="utf-8")

    # Standalone audit detects standard_cn, compiles std & val in temp dir, verifies live, and leaves zero mutations
    res_ok = asyncio.run(ProblemAuditTool().execute(problem_dir=str(cn_dir), mode="full", standalone=True))
    assert res_ok.success, res_ok.error
    assert res_ok.data["detected_layout"] == "standard_cn"
    assert res_ok.data["decision"] == "go", res_ok.data["blocking_issues"]
    assert res_ok.data["quality_signals"]["file_count"]["passed"] is True
    assert res_ok.data["quality_signals"]["validator_check"]["passed"] is True
    assert res_ok.data["quality_signals"]["answer_consistency"]["passed"] is True
    assert res_ok.data["quality_signals"]["purpose_coverage"]["status"] == "not_applicable"
    assert not (cn_dir / ".autocode").exists()
    assert not any(p.suffix in ("", ".exe") and p.name.startswith("std") and p.name != "std.cpp" for p in (cn_dir / "solution").iterdir())

    # Corrupt test2.out -> standalone CLI --layout standard_cn catches wrong answer (exit 2)
    (cn_dir / "data" / "test" / "test2.out").write_text("999999\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["autocode-audit", str(cn_dir), "--standalone", "--layout", "standard_cn"])
    rc = audit_cli_main()
    assert rc == 2
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["data"]["decision"] == "no_go"
    assert any(b["gate"] == "answer_consistency" for b in cli_out["data"]["blocking_issues"])
    assert not (cn_dir / ".autocode").exists()


# ============== NEW-5: Self-contained exports/polygon/ + dangling path= verification ==============


@pytest.mark.asyncio
async def test_new5_polygon_export_self_contained_and_dangling_path_verification(tmp_path):
    pdir = _make_problem(tmp_path, "poly_self_contained")
    manifest = default_manifest("Poly Self Contained")
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    manifest.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(pdir), manifest)

    (pdir / "files" / "testlib.h").write_text("// testlib\n", encoding="utf-8")
    (pdir / "files" / "gen.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "files" / "val.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (pdir / "solutions" / "brute.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    for i in range(1, 4):
        (pdir / "tests" / f"{i:02d}.in").write_text(f"{i}\n", encoding="utf-8")
        (pdir / "tests" / f"{i:02d}.ans").write_text(f"{i * 2}\n", encoding="utf-8")

    pack = ProblemPackPolygonTool()
    res_dir = await pack.execute(problem_dir=str(pdir), export_formats=["polygon"], export_data_mode="copy")
    assert res_dir.success, res_dir.error
    poly_dir = pdir / "exports" / "polygon"
    assert (poly_dir / "problem.xml").is_file()
    assert (poly_dir / "statements" / "README.md").is_file()
    assert (poly_dir / "files" / "gen.cpp").is_file()
    assert (poly_dir / "files" / "val.cpp").is_file()
    assert (poly_dir / "solutions" / "sol.cpp").is_file()
    assert (poly_dir / "tests" / "03.ans").is_file()
    assert res_dir.data["export_verification"]["passed"] is True

    # Zip mode produces self-contained exports/polygon.zip
    res_zip = await pack.execute(problem_dir=str(pdir), export_formats=["polygon"], export_data_mode="zip")
    assert res_zip.success, res_zip.error
    poly_zip = pdir / "exports" / "polygon.zip"
    assert poly_zip.is_file()
    with zipfile.ZipFile(poly_zip, "r") as zf:
        names = set(zf.namelist())
        assert "problem.xml" in names
        assert "statements/README.md" in names
        assert "solutions/sol.cpp" in names
        assert "tests/01.in" in names

    # Dangling stub exports/polygon/problem.xml without referenced assets MUST fail verification & audit
    poly_zip.unlink()
    poly_dir.mkdir(parents=True, exist_ok=True)
    (poly_dir / "problem.xml").write_text((pdir / "problem.xml").read_text(encoding="utf-8"), encoding="utf-8")
    ver_stub = verify_exported_packages(
        str(pdir),
        formats=["polygon"],
        expected_time_limit_ms=2000,
        expected_memory_limit_mb=256,
        expected_test_count=3,
    )
    assert ver_stub["passed"] is False
    assert any("dangling" in e.lower() or "missing" in e.lower() for e in ver_stub["errors"])


# ============== NEW-6: Zero tolerance for exact duplicate inputs ==============


@pytest.mark.asyncio
async def test_new6_duplicate_inputs_zero_tolerance_for_exact_duplicates(tmp_path):
    pdir = _make_problem(tmp_path, "dup_zero_tol")
    manifest = default_manifest("Dup Zero Tol")
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    manifest.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(pdir), manifest)

    # 20 tests where only 2 (10% < 20% threshold) are byte-identical -> MUST FAIL
    for i in range(1, 21):
        val = 9 if i == 20 else i
        (pdir / "tests" / f"{i:02d}.in").write_text(f"{val}\n", encoding="utf-8")
        (pdir / "tests" / f"{i:02d}.ans").write_text(f"{val * 2}\n", encoding="utf-8")

    verifier = ProblemVerifyTestsTool()
    dup_res = verifier._check_duplicate_inputs(str(pdir / "tests"))
    assert dup_res["passed"] is False
    assert dup_res["exact_duplicate_count"] == 1
    assert dup_res["exact_duplicate_tolerance"] == 0
    assert dup_res["duplicate_ratio"] == pytest.approx(0.1)

    set_section(str(pdir), WORKFLOW, {"tests_verified": True})
    audit_res = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    assert audit_res.success
    assert audit_res.data["decision"] == "no_go"
    assert any(b["gate"] == "duplicate_inputs" for b in audit_res.data["blocking_issues"])

    # Whitespace-only near-duplicate (10% <= 20%) with 0 exact duplicates passes
    (pdir / "tests" / "20.in").write_text("9   \n", encoding="utf-8")
    near_res = verifier._check_duplicate_inputs(str(pdir / "tests"))
    assert near_res["passed"] is True
    assert near_res["exact_duplicate_count"] == 0
    assert near_res["near_duplicate_file_count"] == 2


# ============== NEW-7: Static analyzer accuracy (Dijkstra, regret greedy, space, conflict) ==============


@pytest.mark.asyncio
async def test_new7_static_analyzer_dijkstra_regret_greedy_space_and_conflict_consistency():
    # 1. Multi-test heap-optimized Dijkstra with adj[u][i] indexing -> O(n log n) time, O(n) space
    multitest_dijkstra = """
#include <iostream>
#include <queue>
#include <vector>
using namespace std;
int main() {
    int T; cin >> T;
    while (T--) {
        int n, m; cin >> n >> m;
        vector<vector<pair<int, int>>> adj(n);
        vector<long long> dist(n, 1e18);
        priority_queue<pair<long long, int>> pq;
        pq.push({0, 0});
        while (!pq.empty()) {
            auto [d, u] = pq.top(); pq.pop();
            for (int i = 0; i < (int)adj[u].size(); i++) {
                int v = adj[u][i].first, w = adj[u][i].second;
                if (dist[u] + w < dist[v]) {
                    dist[v] = dist[u] + w;
                    pq.push({-dist[v], v});
                }
            }
        }
    }
    return 0;
}
"""
    d_comp, d_pats = estimate_static_complexity(multitest_dijkstra)
    assert d_comp == ComplexityLevel.N_LOG_N
    assert "heap" in d_pats
    assert "graph_traversal" not in d_pats
    d_space, _ = estimate_memory_usage(multitest_dijkstra)
    assert d_space == "O(n)"

    # 2. Regret greedy heap -> O(n log n) time, NOT graph_traversal, NOT O(n^2)
    regret_greedy = """
#include <iostream>
#include <queue>
#include <vector>
using namespace std;
int main() {
    int T; cin >> T;
    while (T--) {
        int n; long long cap; cin >> n >> cap;
        vector<long long> a(n);
        priority_queue<long long> pq;
        long long sum = 0;
        for (int i = 0; i < n; i++) {
            cin >> a[i];
            sum += a[i];
            pq.push(a[i]);
            while (sum > cap && !pq.empty()) {
                sum -= pq.top();
                pq.pop();
            }
        }
    }
    return 0;
}
"""
    rg_comp, rg_pats = estimate_static_complexity(regret_greedy)
    assert rg_comp == ComplexityLevel.N_LOG_N
    assert "heap" in rg_pats
    assert "graph_traversal" not in rg_pats

    # 3. 1D linear DP + small-const 2nd dim vector(n, vector(2, 0)) -> O(n) time & O(n) space
    dp1d_code = """
#include <iostream>
#include <vector>
using namespace std;
int main() {
    int n; cin >> n;
    vector<vector<long long>> dp(n + 1, vector<long long>(2, 0));
    for (int i = 1; i <= n; i++) {
        dp[i][0] = dp[i - 1][1] + i;
        dp[i][1] = dp[i - 1][0] + 1;
    }
    cout << dp[n][0] << '\\n';
    return 0;
}
"""
    dp_comp, _ = estimate_static_complexity(dp1d_code)
    dp_space, _ = estimate_memory_usage(dp1d_code)
    assert dp_comp == ComplexityLevel.LINEAR
    assert dp_space == "O(n)"

    # 4. SolutionAnalyzeTool and SolutionAuditStdTool share static_estimated_complexity & flag conflicts
    an = await SolutionAnalyzeTool().execute(code=regret_greedy, claimed_complexity="O(n)")
    st = await SolutionAuditStdTool().execute(code=regret_greedy, claimed_complexity="O(n)")
    assert an.success and st.success
    assert an.data["static_estimated_complexity"] == st.data["static_estimated_complexity"] == ComplexityLevel.N_LOG_N
    assert an.data["complexity_conflict"] is True
    assert st.data["complexity_conflict"] is True
    assert any(f["type"] == "complexity_conflict" for f in st.data["findings"])


# ============== NEW-8: Validator <-> Statement/Manifest bounds & Optional Mutation Testing ==============


@pytest.mark.asyncio
async def test_new8_validator_named_bounds_equivalence_and_optional_mutation_testing(tmp_path):
    pdir = _make_problem(tmp_path, "mut_and_bounds")
    manifest = default_manifest("Mut And Bounds")
    manifest.constraints = {
        "n": {"min": 1, "max": 100000},
        "a": {"min": 0, "max": 1000000000},
        "sum_n": {"min": 1, "max": 200000},
    }
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    manifest.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(pdir), manifest)

    # Validator with readInt, readLongs (4-arg), ensuref sum bound, and intentional lower-bound mismatch on n (0 vs 1)
    val_cpp = pdir / "files" / "val.cpp"
    val_cpp.write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "  registerValidation(argc, argv);\n"
        '  int n = inf.readInt(0, 100000, "n");\n'
        '  inf.readLongs(n, 0, 1000000000LL, "a");\n'
        "  long long sum_n = n;\n"
        '  ensuref(sum_n <= 200000, "sum too large");\n'
        "}\n",
        encoding="utf-8",
    )
    vbounds = _extract_validator_bounds(val_cpp)
    assert any(b["source"] == "read_array" and b["max"] == 1_000_000_000 for b in vbounds)
    assert any(b["source"] == "ensure" and b["var"] == "sum_n" and b["max"] == 200_000 for b in vbounds)

    sc = ProblemAuditTool()._statement_consistency(pdir, manifest, {"tests": [1]})
    assert any("validator min=0 != manifest n.min=1" in str(m.get("reason", "")) for m in sc["validator_bound_mismatches"])

    # Mutation testing catches weak test suite missing the boundary n == 10
    sol_cpp = pdir / "solutions" / "sol.cpp"
    sol_cpp.write_text(
        "#include <iostream>\n"
        "int main(){\n"
        "  int n; std::cin >> n;\n"
        "  if (n <= 10) std::cout << n + 1 << '\\n';\n"
        "  else std::cout << n * 2 << '\\n';\n"
        "}\n",
        encoding="utf-8",
    )
    muts = generate_cpp_mutants(sol_cpp.read_text(encoding="utf-8"), budget=4)
    assert any(m["mutation_type"] == "relational_le_to_lt" for m in muts)

    # Weak tests: only n=3 and n=20 (never hits boundary n=10 -> `<=` to `<` mutant survives!)
    (pdir / "tests" / "01.in").write_text("3\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("4\n", encoding="utf-8")
    (pdir / "tests" / "02.in").write_text("20\n", encoding="utf-8")
    (pdir / "tests" / "02.ans").write_text("40\n", encoding="utf-8")

    weak_mut = await run_mutation_testing(
        str(pdir),
        str(pdir / "tests"),
        manifest=manifest,
        sol_source=sol_cpp,
        budget=4,
    )
    assert weak_mut["executed"] is True
    assert weak_mut["compiled_mutants"] >= 1
    assert weak_mut["survived_mutants"] >= 1
    assert any(s["mutation_type"] == "relational_le_to_lt" for s in weak_mut["survivors"])

    # ProblemAuditTool surfaces survivor warning when enable_mutation_testing=True
    audit_weak = await ProblemAuditTool().execute(
        problem_dir=str(pdir),
        mode="full",
        enable_mutation_testing=True,
    )
    assert audit_weak.success
    assert audit_weak.data["quality_signals"]["mutation_testing"]["executed"] is True
    assert any(w["gate"] == "mutation_testing" for w in audit_weak.data["risk_report"]["warnings"])

    # Adding boundary test n=10 kills the relational_le_to_lt mutant!
    (pdir / "tests" / "03.in").write_text("10\n", encoding="utf-8")
    (pdir / "tests" / "03.ans").write_text("11\n", encoding="utf-8")
    strong_mut = await run_mutation_testing(
        str(pdir),
        str(pdir / "tests"),
        manifest=manifest,
        sol_source=sol_cpp,
        budget=4,
    )
    assert strong_mut["executed"] is True
    assert not any(s["mutation_type"] == "relational_le_to_lt" for s in strong_mut["survivors"])


# ============== Dual-Review Security, State & Edge-Case Hardening Regressions ==============


@pytest.mark.asyncio
async def test_review_security_and_state_hardening(tmp_path):
    from autocode_mcp.runtime_store import get_section
    from autocode_mcp.utils.execution_monitor import DynamicExecutionMonitor
    from autocode_mcp.workflow.enforcement import apply_result

    # 1. [C-1] Standalone path_containment blocks mutation testing (no external symlink read/exec)
    if sys.platform != "win32":
        pkg = tmp_path / "escaped_pkg"
        for sub in ("solutions", "statements", "tests"):
            (pkg / sub).mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside_secret.in"
        outside.write_text("42\n", encoding="utf-8")
        (pkg / "tests" / "01.in").symlink_to(outside)
        (pkg / "tests" / "01.ans").write_text("84\n", encoding="utf-8")
        (pkg / "solutions" / "sol.cpp").write_text(
            "#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<(n<=10?n+1:n*2)<<'\\n';}\n",
            encoding="utf-8",
        )
        esc_audit = await ProblemAuditTool().execute(
            problem_dir=str(pkg),
            mode="full",
            standalone=True,
            enable_mutation_testing=True,
        )
        assert esc_audit.success
        assert esc_audit.data["decision"] == "no_go"
        assert any(b["gate"] == "path_containment" for b in esc_audit.data["blocking_issues"])
        assert "mutation_testing" not in esc_audit.data["quality_signals"]

    # 2. [H-1] Failed problem_create(overwrite=False) must NOT wipe existing runtime.json workflow state
    pdir = _make_problem(tmp_path, "state_preserve", time_limit_ms=1000)
    set_section(
        str(pdir),
        WORKFLOW,
        {
            "tests_generated": True,
            "tests_verified": True,
            "packaged": True,
            "verify_signals": {"file_count": {"executed": True, "passed": True}},
        },
    )
    apply_result(str(pdir), "problem_create", {"problem_dir": str(pdir), "overwrite": False}, False, {})
    wf_after = get_section(str(pdir), WORKFLOW) or {}
    assert wf_after.get("tests_verified") is True
    assert wf_after.get("packaged") is True
    assert wf_after.get("verify_signals", {}).get("file_count", {}).get("passed") is True

    # 3. [H-2] ProblemCreateTool rejects non-bool overwrite and dangling manifest symlink
    creator = ProblemCreateTool()
    bad_bool = await creator.execute(
        problem_dir=str(pdir),
        problem_name="BadBool",
        overwrite="false",  # type: ignore[arg-type]
    )
    assert not bad_bool.success
    if sys.platform != "win32":
        sym_dir = tmp_path / "sym_prob"
        (sym_dir / ".autocode").mkdir(parents=True, exist_ok=True)
        outside_target = tmp_path / "outside_manifest.json"
        (sym_dir / ".autocode" / "manifest.json").symlink_to(outside_target)
        sym_res = await creator.execute(problem_dir=str(sym_dir), problem_name="Sym", overwrite=False)
        assert not sym_res.success
        assert not outside_target.exists()

    # 4. [H-3 / M-2] verify_exported_packages rejects malicious %999999999d pattern, Zip Slip, and escaped exports/polygon
    dos_dir = _make_problem(tmp_path, "dos_polygon")
    (dos_dir / "problem.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<problem short-name="dos"><judging><testset name="tests">'
        '<time-limit>2000</time-limit><memory-limit>268435456</memory-limit>'
        '<test-count>1</test-count>'
        '<input-path-pattern>tests/%999999999d.in</input-path-pattern>'
        '<answer-path-pattern>tests/%02d.ans</answer-path-pattern>'
        '</testset></judging></problem>',
        encoding="utf-8",
    )
    dos_chk = verify_exported_packages(
        str(dos_dir), formats=["polygon"], expected_time_limit_ms=2000, expected_memory_limit_mb=256
    )
    assert dos_chk["passed"] is False
    assert any("input-path-pattern" in e for e in dos_chk["errors"])

    zip_slip_dir = _make_problem(tmp_path, "zip_slip_pkg")
    (zip_slip_dir / "exports").mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_slip_dir / "exports" / "polygon.zip", "w") as zf:
        zf.writestr("../escape.txt", "boom")
        zf.writestr("problem.xml", "<problem/>")
    zs_chk = verify_exported_packages(
        str(zip_slip_dir), formats=["polygon"], expected_time_limit_ms=2000, expected_memory_limit_mb=256
    )
    assert zs_chk["passed"] is False
    assert any("unsafe zip" in e for e in zs_chk["errors"])

    if sys.platform != "win32":
        esc_exp_dir = _make_problem(tmp_path, "esc_poly_export")
        (esc_exp_dir / "tests" / "01.in").write_text("1\n", encoding="utf-8")
        (esc_exp_dir / "tests" / "01.ans").write_text("2\n", encoding="utf-8")
        (esc_exp_dir / "problem.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>'
            '<problem short-name="ok"><judging><testset name="tests">'
            '<time-limit>2000</time-limit><memory-limit>268435456</memory-limit>'
            '<test-count>1</test-count>'
            '<input-path-pattern>tests/%02d.in</input-path-pattern>'
            '<answer-path-pattern>tests/%02d.ans</answer-path-pattern>'
            '</testset></judging></problem>',
            encoding="utf-8",
        )
        (esc_exp_dir / "exports").mkdir(exist_ok=True)
        outside_exp = tmp_path / "outside_poly"
        outside_exp.mkdir()
        (esc_exp_dir / "exports" / "polygon").symlink_to(outside_exp)
        esc_chk = verify_exported_packages(
            str(esc_exp_dir), formats=["polygon"], expected_time_limit_ms=2000, expected_memory_limit_mb=256
        )
        assert esc_chk["passed"] is False
        assert any("escapes problem_dir" in e for e in esc_chk["errors"])

    # 5. [H-5 & M1] DynamicExecutionMonitor kills OOM process as MLE (not timeout), and min-scale timeout hard-fails brute
    exe_ext = get_exe_extension()
    oom_src = tmp_path / "oom_proc.cpp"
    oom_bin = tmp_path / f"oom_proc{exe_ext}"
    oom_in = tmp_path / "oom.in"
    oom_in.write_text("1\n", encoding="utf-8")
    oom_src.write_text(
        "#include <chrono>\n#include <cstddef>\n#include <cstring>\n#include <thread>\n#include <vector>\n"
        "int main(){\n"
        "  const size_t N = 72u * 1024u * 1024u;\n"
        "  std::vector<char> v(N);\n"
        "  std::memset(v.data(), 1, N);\n"
        "  volatile char sink = 0;\n"
        "  for (size_t i = 0; i < N; i += 4096) sink ^= v[i];\n"
        "  (void)sink;\n"
        "  std::this_thread::sleep_for(std::chrono::milliseconds(1500));\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    assert (await compile_cpp(str(oom_src), str(oom_bin))).success
    mon_res = await DynamicExecutionMonitor.run_monitored_process(
        [str(oom_bin)],
        str(oom_in),
        time_limit_ms=5000.0,
        memory_limit_mb=24.0,
        cwd=str(tmp_path),
    )
    assert mon_res["status"] == "mle"

    hang_dir = _make_problem(tmp_path, "hang_brute")
    (hang_dir / "files" / "gen.cpp").write_text(
        "#include <iostream>\nint main(){ std::cout << 1 << '\\n'; }\n",
        encoding="utf-8",
    )
    (hang_dir / "solutions" / "brute.cpp").write_text(
        "#include <chrono>\n#include <thread>\n"
        "int main(){ std::this_thread::sleep_for(std::chrono::milliseconds(1800)); return 0; }\n",
        encoding="utf-8",
    )
    assert (await compile_cpp(str(hang_dir / "files" / "gen.cpp"), str(hang_dir / "files" / f"gen{exe_ext}"))).success
    assert (await compile_cpp(str(hang_dir / "solutions" / "brute.cpp"), str(hang_dir / "solutions" / f"brute{exe_ext}"))).success
    hang_res = await SolutionAuditBruteTool().execute(problem_dir=str(hang_dir), constraints={"n_max": 8})
    assert hang_res.success
    assert hang_res.data["passed"] is False
    assert hang_res.data["brute_probe"]["status"] == "timeout_at_min_scale"


@pytest.mark.asyncio
async def test_review_logic_and_cross_platform_edge_cases(tmp_path):
    from autocode_mcp.tools.complexity import detect_complexity_conflicts

    # 1. [H2] Non-exponential standard algorithms must NOT be misclassified as O(2^n)
    fast_io_and_st = """
#include <bits/stdc++.h>
using namespace std;
char buf[1 << 20];
int seg[1 << 19];
int st[20][100005];
void build(int p, int l, int r) {
    if (l == r) { seg[p] = l; return; }
    int m = (l + r) >> 1;
    build(p << 1, l, m);
    build(p << 1 | 1, m + 1, r);
}
int popcnt(int x) {
    int c = 0;
    while (x) { x = (x - 1) & x; c++; }
    return c;
}
int main() {
    int n = 100;
    for (int j = 1; j < 20; j++)
        for (int i = 0; i + (1 << (j - 1)) < n; i++)
            st[j][i] = min(st[j - 1][i], st[j - 1][i + (1 << (j - 1))]);
    build(1, 1, n);
    return popcnt(n);
}
"""
    st_est, st_pats = estimate_static_complexity(fast_io_and_st)
    assert st_est != ComplexityLevel.EXPONENTIAL
    assert "bitmask" not in st_pats
    assert "backtracking" not in st_pats

    tree_dfs_code = """
#include <vector>
using namespace std;
vector<int> g[100005], path;
void dfs(int u, int p) {
    path.push_back(u);
    for (int v : g[u]) if (v != p) dfs(v, u);
    path.pop_back();
}
int main() { dfs(1, 0); return 0; }
"""
    t_est, t_pats = estimate_static_complexity(tree_dfs_code)
    assert t_est != ComplexityLevel.EXPONENTIAL
    assert "backtracking" not in t_pats

    # 2. [H3] Non-fit empirical sentinel ("unknown", r_squared=None) must NOT trigger conflict
    has_conf, details = detect_complexity_conflicts(
        claimed="O(n)",
        static_estimated="O(n)",
        empirical={"status": "ok", "verdict": "insufficient_samples", "fitted_complexity": "unknown", "r_squared": None},
    )
    assert has_conf is False
    assert details == []

    # 3. [M4] 2D static array memory is totaled even alongside a small 1D array
    sp_cls, mem_mb = estimate_memory_usage("int dx[4];\nint dp[10000][10000];\nint main(){return 0;}\n")
    assert sp_cls == "O(n^2)"
    assert mem_mb >= 380

    # 4. [H6] Custom audit_gates.max_duplicate_input_ratio > 0.2 is honored when exact duplicates == 0
    dup_dir = _make_problem(tmp_path, "custom_near_dup")
    dm = load_manifest(str(dup_dir))
    assert dm is not None
    dm.audit_gates.max_duplicate_input_ratio = 0.5
    save_manifest(str(dup_dir), dm)
    # 2 near-duplicate inputs (differ by whitespace only) out of 6 -> near_ratio = 2/6 = 0.333 <= 0.5
    (dup_dir / "tests" / "01.in").write_text("1 2\n", encoding="utf-8")
    (dup_dir / "tests" / "02.in").write_text("1   2\n", encoding="utf-8")
    for idx in range(3, 7):
        (dup_dir / "tests" / f"{idx:02d}.in").write_text(f"{idx} {idx + 1}\n", encoding="utf-8")
    for idx in range(1, 7):
        (dup_dir / "tests" / f"{idx:02d}.ans").write_text("1\n", encoding="utf-8")
    ver = ProblemVerifyTestsTool()
    dup_res = ver._check_duplicate_inputs(str(dup_dir / "tests"), max_near_ratio=0.5)
    assert dup_res["passed"] is True
    assert dup_res["exact_duplicate_count"] == 0

    # 5. [H7] Custom statement_path and 1.in..2.in naming pack and self-verify cleanly
    custom_dir = _make_problem(tmp_path, "custom_poly_pack")
    cm = load_manifest(str(custom_dir))
    assert cm is not None
    cm.statement_path = "statements/problem_zh.md"
    cm.quality_gates.require_wrong_solution_kill = False
    cm.quality_gates.require_limit_semantics = False
    cm.quality_gates.require_validator_check = False
    save_manifest(str(custom_dir), cm)
    (custom_dir / "statements" / "problem_zh.md").write_text("# Custom Statement\n", encoding="utf-8")
    (custom_dir / "tests" / "1.in").write_text("3\n", encoding="utf-8")
    (custom_dir / "tests" / "1.ans").write_text("6\n", encoding="utf-8")
    (custom_dir / "tests" / "2.in").write_text("5\n", encoding="utf-8")
    (custom_dir / "tests" / "2.ans").write_text("10\n", encoding="utf-8")
    set_section(
        str(custom_dir),
        WORKFLOW,
        {
            "tests_generated": True,
            "tests_verified": True,
            "verify_signals": {
                "file_count": {"executed": True, "passed": True},
                "no_empty": {"executed": True, "passed": True},
                "answer_consistency": {"executed": True, "passed": True},
            },
        },
    )
    pack_custom = await ProblemPackPolygonTool().execute(
        problem_dir=str(custom_dir), export_formats=["polygon"]
    )
    assert pack_custom.success, pack_custom.error
    xml_txt = (custom_dir / "problem.xml").read_text(encoding="utf-8")
    assert 'path="statements/problem_zh.md"' in xml_txt
    assert "<input-path-pattern>tests/%d.in</input-path-pattern>" in xml_txt
    assert "solutions/brute.cpp" not in xml_txt

    # 6. [M5 & H1] Mutation generator skips mid-line block comments, <=>, 1e-1, spaced templates; SPJ without checker skips safely
    tricky_cpp = (
        "int main() {\n"
        "  double eps = 1e-1; /* start comment\n"
        "  if (a <= b) return 0;\n"
        "  */\n"
        "  auto cmp = (1 <=> 2);\n"
        "  std::vector < int > v;\n"
        "  if (eps <= 1.0) return 0;\n"
        "}\n"
    )
    tricky_muts = generate_cpp_mutants(tricky_cpp, budget=8)
    assert all(m["line"] != 3 for m in tricky_muts), f"mutated inside block comment: {tricky_muts}"
    assert all("<>" not in m["mutated"] for m in tricky_muts)
    assert all("1e- 0" not in m["mutated"] for m in tricky_muts)
    assert all("vector <= int" not in m["mutated"] for m in tricky_muts)

    spj_skip = await run_mutation_testing(
        str(custom_dir),
        str(custom_dir / "tests"),
        manifest=cm,
        sol_source=custom_dir / "solutions" / "sol.cpp",
        verify_with_checker=True,
        checker_bin=None,
    )
    assert spj_skip["executed"] is False
    assert spj_skip["skipped"] is True

    # 7. [M6] Reversed ensuref + unary -MAXA + exact named max mismatch detection (no off-by-one bypass)
    val_cpp = tmp_path / "rev_val.cpp"
    val_cpp.write_text(
        'const int MAXA = 100000;\n'
        'int main() {\n'
        '  int n = inf.readInt(1, 99999, "n");\n'
        '  int x = inf.readInt(-MAXA, MAXA, "x");\n'
        '  ensuref((1 <= sum_n) && (sum_n <= 200000), "sum");\n'
        '}\n',
        encoding="utf-8",
    )
    bounds = _extract_validator_bounds(val_cpp)
    assert any(b["var"] == "x" and b["min"] == -100000 and b["max"] == 100000 for b in bounds)
    assert any(b["var"] == "sum_n" and b["min"] == 1 for b in bounds)
    assert any(b["var"] == "sum_n" and b["max"] == 200000 for b in bounds)

    off1_dir = _make_problem(tmp_path, "off_by_one_val")
    om = load_manifest(str(off1_dir))
    assert om is not None
    om.constraints = {"n": {"min": 1, "max": 1e5}}
    save_manifest(str(off1_dir), om)
    (off1_dir / "files" / "val.cpp").write_text(val_cpp.read_text(encoding="utf-8"), encoding="utf-8")
    sc = ProblemAuditTool()._statement_consistency(off1_dir, om, {})
    assert any(
        m.get("var") == "n" and "99999" in str(m.get("reason", ""))
        for m in sc["validator_bound_mismatches"]
    )
