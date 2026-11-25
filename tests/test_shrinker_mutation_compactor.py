"""Real C++ & unit tests for counterexample shrinking, AST semantic mutation, UB probe, and suite compaction."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from autocode_mcp.runtime_store import TEST_MANIFEST, set_section
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.problem import ProblemCreateTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.stress_test import StressTestRunTool, run_ub_differential_probe
from autocode_mcp.tools.validator import ValidatorBuildTool
from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.mutation import (
    filter_equivalent_mutants,
    generate_cpp_mutants,
    generate_cpp_mutants_report,
    run_mutation_testing,
)
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.utils.shrinker import shrink_counterexample
from autocode_mcp.utils.test_compactor import compact_test_suite, merge_coverage_matrices
from autocode_mcp.workflow import load_manifest, save_manifest


def _snapshot_tree(root: Path) -> set[tuple[str, bytes]]:
    items: set[tuple[str, bytes]] = set()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file():
            items.add((f"F:{rel}", path.read_bytes()))
        elif path.is_dir():
            items.add((f"D:{rel}", b""))
    return items


@pytest.mark.asyncio
async def test_shrink_counterexample_40_plus_elements_with_real_cpp_val_sol_brute(
    tmp_path: Path,
) -> None:
    """Shrink a 45-element failing input down to a minimal valid 2-element counterexample."""
    exe_ext = get_exe_extension()
    val_src = tmp_path / "val.cpp"
    sol_src = tmp_path / "sol.cpp"
    brute_src = tmp_path / "brute.cpp"
    val_bin = str(tmp_path / f"val{exe_ext}")
    sol_bin = str(tmp_path / f"sol{exe_ext}")
    brute_bin = str(tmp_path / f"brute{exe_ext}")

    val_src.write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "    registerValidation(argc, argv);\n"
        '    int n = inf.readInt(2, 100, "n");\n'
        "    inf.readEoln();\n"
        "    for (int i = 0; i < n; ++i) {\n"
        '        inf.readInt(1, 1000000, "a_i");\n'
        "        if (i + 1 < n) inf.readSpace();\n"
        "    }\n"
        "    inf.readEoln();\n"
        "    inf.readEof();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    sol_src.write_text(
        "#include <algorithm>\n"
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; std::cin >> n;\n"
        "    int mn = 1000000000, mx = -1000000000;\n"
        "    for (int i = 0; i < n; ++i) {\n"
        "        int x; std::cin >> x;\n"
        "        mn = std::min(mn, x);\n"
        "        mx = std::max(mx, x);\n"
        "    }\n"
        "    if (mx - mn >= 50) std::cout << -1 << '\\n';\n"
        "    else std::cout << (mx - mn) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    brute_src.write_text(
        "#include <algorithm>\n"
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; std::cin >> n;\n"
        "    int mn = 1000000000, mx = -1000000000;\n"
        "    for (int i = 0; i < n; ++i) {\n"
        "        int x; std::cin >> x;\n"
        "        mn = std::min(mn, x);\n"
        "        mx = std::max(mx, x);\n"
        "    }\n"
        "    std::cout << (mx - mn) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    assert (await compile_cpp(str(val_src), val_bin, use_cache=False)).success
    assert (await compile_cpp(str(sol_src), sol_bin, use_cache=False)).success
    assert (await compile_cpp(str(brute_src), brute_bin, use_cache=False)).success

    from autocode_mcp.utils.compiler import run_binary

    elems = [100 + i * 7 for i in range(45)]
    elems[5] = 17
    elems[37] = 5000
    original_input = f"45\n{' '.join(str(x) for x in elems)}\n"

    async def mismatch_pred(cand: str) -> bool:
        s_res = await run_binary(sol_bin, cand, timeout=3.0)
        b_res = await run_binary(brute_bin, cand, timeout=3.0)
        if not s_res.success or not b_res.success:
            return False
        return s_res.stdout.strip() != b_res.stdout.strip()

    res = await shrink_counterexample(
        original_input,
        mismatch_pred,
        validator_exe=val_bin,
        max_evaluations=160,
        timeout_sec=20.0,
    )

    assert res.validator_checked is True
    assert res.shrunk_size < res.original_size
    assert res.shrink_steps > 0
    assert await mismatch_pred(res.shrunk_input)
    v_final = await run_binary(val_bin, res.shrunk_input, timeout=3.0)
    assert v_final.return_code == 0

    lines = res.shrunk_input.strip().splitlines()
    assert len(lines) == 2
    n_shrunk = int(lines[0])
    arr_shrunk = [int(t) for t in lines[1].split()]
    assert n_shrunk == 2
    assert len(arr_shrunk) == 2
    assert sorted(arr_shrunk) == [1, 51]


@pytest.mark.asyncio
async def test_shrink_counterexample_preserves_validator_bounds_and_budget(tmp_path: Path) -> None:
    """Validator minimum constraints block smaller invalid candidates and max_evaluations is respected."""
    exe_ext = get_exe_extension()
    val_src = tmp_path / "val_min10.cpp"
    val_bin = str(tmp_path / f"val_min10{exe_ext}")
    val_src.write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "    registerValidation(argc, argv);\n"
        '    int n = inf.readInt(10, 100, "n");\n'
        "    inf.readEoln();\n"
        "    for (int i = 0; i < n; ++i) {\n"
        '        inf.readInt(5, 1000, "a_i");\n'
        "        if (i + 1 < n) inf.readSpace();\n"
        "    }\n"
        "    inf.readEoln();\n"
        "    inf.readEof();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    assert (await compile_cpp(str(val_src), val_bin, use_cache=False)).success

    from autocode_mcp.utils.compiler import run_binary

    async def always_fail_after_2(cand: str) -> bool:
        toks = [int(t) for t in cand.split()]
        return bool(toks) and toks[0] >= 2

    big_input = "20\n" + " ".join(["500"] * 20) + "\n"
    res = await shrink_counterexample(
        big_input,
        always_fail_after_2,
        validator_exe=val_bin,
        max_evaluations=200,
        timeout_sec=15.0,
    )
    v_res = await run_binary(val_bin, res.shrunk_input, timeout=3.0)
    assert v_res.return_code == 0
    lines = res.shrunk_input.strip().splitlines()
    assert int(lines[0]) == 10
    assert all(int(x) == 5 for x in lines[1].split())

    calls = 0

    async def tight_pred(cand: str) -> bool:
        nonlocal calls
        calls += 1
        return len(cand.split()) >= 15

    bounded = await shrink_counterexample(
        " ".join(str(1000 + i) for i in range(25)) + "\n",
        tight_pred,
        max_evaluations=4,
        timeout_sec=5.0,
    )
    assert bounded.evaluations <= 4
    assert calls <= 4
    assert bounded.budget_exhausted is True


@pytest.mark.asyncio
async def test_stress_test_run_exposes_shrunk_counterexample(tmp_path: Path) -> None:
    """StressTestRunTool exposes shrunk_input, original_size, shrunk_size, shrink_steps on failure."""
    prob_dir = tmp_path / "stress_shrink"
    await ProblemCreateTool().execute(problem_dir=str(prob_dir), problem_name="Stress Shrink")
    await ValidatorBuildTool().execute(
        problem_dir=str(prob_dir),
        code=(
            '#include "testlib.h"\n'
            "int main(int argc, char* argv[]) {\n"
            "    registerValidation(argc, argv);\n"
            '    int n = inf.readInt(2, 100, "n");\n'
            "    inf.readEoln();\n"
            "    for (int i = 0; i < n; ++i) {\n"
            '        inf.readInt(1, 100000, "a_i");\n'
            "        if (i + 1 < n) inf.readSpace();\n"
            "    }\n"
            "    inf.readEoln();\n"
            "    inf.readEof();\n"
            "    return 0;\n"
            "}\n"
        ),
        test_cases=[
            {"input": "2\n1 2\n", "expected_valid": True},
            {"input": "1\n1\n", "expected_valid": False},
        ],
    )
    await GeneratorBuildTool().execute(
        problem_dir=str(prob_dir),
        code=(
            '#include "testlib.h"\n'
            "#include <iostream>\n"
            "int main(int argc, char* argv[]) {\n"
            "    registerGen(argc, argv, 1);\n"
            '    std::cout << "40\\n";\n'
            "    for (int i = 0; i < 40; ++i) {\n"
            "        int v = (i == 0 ? 10 : (i == 35 ? 25000 : 100 + i));\n"
            "        std::cout << v << (i + 1 < 40 ? ' ' : '\\n');\n"
            "    }\n"
            "    return 0;\n"
            "}\n"
        ),
    )
    await SolutionBuildTool().execute(
        problem_dir=str(prob_dir),
        solution_type="sol",
        code=(
            "#include <algorithm>\n"
            "#include <iostream>\n"
            "int main() {\n"
            "    int n; std::cin >> n;\n"
            "    int mn = 1000000, mx = 0;\n"
            "    for (int i = 0; i < n; ++i) {\n"
            "        int x; std::cin >> x;\n"
            "        mn = std::min(mn, x); mx = std::max(mx, x);\n"
            "    }\n"
            "    if (mx - mn >= 50) std::cout << -999 << '\\n';\n"
            "    else std::cout << (mx - mn) << '\\n';\n"
            "    return 0;\n"
            "}\n"
        ),
    )
    await SolutionBuildTool().execute(
        problem_dir=str(prob_dir),
        solution_type="brute",
        code=(
            "#include <algorithm>\n"
            "#include <iostream>\n"
            "int main() {\n"
            "    int n; std::cin >> n;\n"
            "    int mn = 1000000, mx = 0;\n"
            "    for (int i = 0; i < n; ++i) {\n"
            "        int x; std::cin >> x;\n"
            "        mn = std::min(mn, x); mx = std::max(mx, x);\n"
            "    }\n"
            "    std::cout << (mx - mn) << '\\n';\n"
            "    return 0;\n"
            "}\n"
        ),
    )

    stress_res = await StressTestRunTool().execute(problem_dir=str(prob_dir), trials=3, n_max=40)
    assert not stress_res.success
    assert "shrunk_input" in stress_res.data
    assert "original_size" in stress_res.data
    assert "shrunk_size" in stress_res.data
    assert "shrink_steps" in stress_res.data
    assert stress_res.data["shrunk_size"] < stress_res.data["original_size"]
    assert stress_res.data["shrink_steps"] > 0
    shrunk_lines = stress_res.data["shrunk_input"].strip().splitlines()
    assert int(shrunk_lines[0]) < 10


@pytest.mark.asyncio
async def test_semantic_ast_mutants_and_equivalent_filter_compile_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CP semantic operators generate compilable mutants without tree-sitter-cpp, and filter removes identities."""
    cp_code = (
        "// 中文注释：测试 UTF-8 偏移与语义变异\n"
        "#include <climits>\n"
        "#include <iostream>\n"
        "#include <queue>\n"
        "#include <vector>\n"
        "const int MOD = 1000000007;\n"
        "int main() {\n"
        "    int t = 2;\n"
        "    std::vector<int> adj;\n"
        "    while (t--) {\n"
        "        adj.clear();\n"
        "        int n = 5;\n"
        "        long long total = 0;\n"
        "        int best_min = INT_MAX;\n"
        "        std::priority_queue<int, std::vector<int>, std::greater<int>> min_pq;\n"
        "        std::priority_queue<int> max_pq;\n"
        "        for (int i = 0; i < n; ++i) {\n"
        "            total = (total + i * 100000LL) % MOD;\n"
        "            min_pq.push(i);\n"
        "            max_pq.push(i);\n"
        "        }\n"
        "        if (best_min <= n) std::cout << total << '\\n';\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )

    import autocode_mcp.utils.mutation as mut_mod

    # When tree-sitter-cpp is installed (e.g. --extra ast), CST backend works cleanly even with UTF-8 comments
    if mut_mod._load_tree_sitter_cpp() is not None:
        cst_report = generate_cpp_mutants_report(cp_code, budget=24)
        assert cst_report["ast_backend"] == "tree_sitter_cpp"
        assert "type_narrowing_ll_to_int" in {m["mutation_type"] for m in cst_report["mutants"]}

    # Verify pure-Python fallback works even when tree_sitter_cpp is absent
    monkeypatch.setattr(mut_mod, "_load_tree_sitter_cpp", lambda: None)
    report = generate_cpp_mutants_report(cp_code, budget=24)
    assert report["ast_backend"] == "python_fallback"
    muts = report["mutants"]
    kinds = {m["mutation_type"] for m in muts}
    for expected_kind in (
        "type_narrowing_ll_to_int",
        "mod_removal",
        "loop_bound_off_by_one",
        "init_extreme_swap",
        "priority_queue_comparator_flip",
        "multitest_clear_deletion",
    ):
        assert expected_kind in kinds, f"Missing {expected_kind} in {kinds}"

    exe_ext = get_exe_extension()
    for m in muts[:8]:
        src = tmp_path / f"{m['id']}.cpp"
        exe = tmp_path / f"{m['id']}{exe_ext}"
        src.write_text(m["code"], encoding="utf-8")
        comp = await compile_cpp(str(src), str(exe), use_cache=False)
        assert comp.success, f"Mutant {m['mutation_type']} failed compile: {comp.stderr}"

    # Verify deterministic equivalent-mutant filter
    equiv_code = (
        "#include <iostream>\n"
        "int helper(int a, int b, int c, int d) {\n"
        "    int mx = a;\n"
        "    if (a <= b) mx = b;\n"
        "    int mx2 = 0;\n"
        "    if (c <= d) mx2 = d; else mx2 = c;\n"
        "    int mn = (a <= b ? a : b);\n"
        "    if (a <= b) return b; return a;\n"
        "}\n"
        "int main() {\n"
        "    int n; std::cin >> n;\n"
        "    int res = 0;\n"
        "    if (n <= 5) { res += 1; } else { res += 1; }\n"
        "#if 0\n"
        "    if (n <= 99) res = 7;\n"
        "#endif\n"
        "    if (false) { res = 8; }\n"
        "    // if (n <= 42) res = 3;\n"
        "    if (n <= 10) std::cout << n + 1 << '\\n';\n"
        "    else std::cout << n * 2 << '\\n';\n"
        "    return 0;\n"
        "}\n"
    )
    eq_report = generate_cpp_mutants_report(equiv_code, budget=24)
    assert eq_report["equivalent_filtered"] >= 5
    kept_originals = [m["original"] for m in eq_report["mutants"]]
    assert any("n <= 10" in orig for orig in kept_originals)
    assert not any("a <= b" in orig for orig in kept_originals)
    assert not any("c <= d" in orig for orig in kept_originals)
    assert not any("n <= 5" in orig for orig in kept_originals)
    assert not any("n <= 99" in orig for orig in kept_originals)
    assert not any("n <= 42" in orig for orig in kept_originals)

    # Also test filter_equivalent_mutants directly for comment-only diffs
    kept_direct, filtered_direct = filter_equivalent_mutants(
        "int main() { return 0; }\n",
        [{"line": 1, "mutation_type": "custom", "original": "0", "mutated": "0", "code": "int main() { /* c */ return 0; }\n"}],
    )
    assert kept_direct == []
    assert len(filtered_direct) == 1
    assert filtered_direct[0]["reason"] == "comment_or_whitespace_only"

    # Backward-compatible generate_cpp_mutants populates optional stats_out
    stats: dict[str, object] = {}
    list_muts = generate_cpp_mutants(equiv_code, budget=8, stats_out=stats)
    assert isinstance(list_muts, list)
    assert int(str(stats["equivalent_filtered"])) >= 5


@pytest.mark.asyncio
async def test_fail_on_mutation_survival_blocks_managed_and_standalone_and_mull_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fail_on_mutation_survival=True upgrades survivors to blocking_issues (no_go), and missing mull-runner falls back to source."""
    pdir = tmp_path / "mut_gate"
    await ProblemCreateTool().execute(problem_dir=str(pdir), problem_name="Mut Gate")
    (pdir / "files" / "val.cpp").write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "    registerValidation(argc, argv);\n"
        '    inf.readInt(1, 100, "n");\n'
        "    inf.readEoln();\n"
        "    inf.readEof();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    sol_cpp = pdir / "solutions" / "sol.cpp"
    sol_cpp.write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    int n; std::cin >> n;\n"
        "    if (n <= 10) std::cout << n + 1 << '\\n';\n"
        "    else std::cout << n * 2 << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    tests_dir = pdir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    # Deliberately omit n == 10 so relational_le_to_lt survives while scale buckets stay diverse
    for idx, val_n in enumerate((1, 5, 25, 100), start=1):
        out_n = val_n + 1 if val_n <= 10 else val_n * 2
        (tests_dir / f"{idx:02d}.in").write_text(f"{val_n}\n", encoding="utf-8")
        (tests_dir / f"{idx:02d}.ans").write_text(f"{out_n}\n", encoding="utf-8")
    (pdir / "statements" / "README.md").write_text(
        "# Problem\n\n## Input\n$1 \\le n \\le 100$.\n\n## Sample\n1 -> 2\n",
        encoding="utf-8",
    )
    (pdir / "statements" / "tutorial.md").write_text(
        "# Editorial\n\nCheck `n <= 10` directly in O(1) time and output `n + 1` or `2 * n` accurately.\n",
        encoding="utf-8",
    )
    exe_ext = get_exe_extension()
    assert (await compile_cpp(str(sol_cpp), str(pdir / "solutions" / f"sol{exe_ext}"), use_cache=False)).success
    set_section(
        str(pdir),
        TEST_MANIFEST,
        {
            "version": 1,
            "answer_ext": ".ans",
            "tests": [
                {"index": idx, "in_file": f"{idx:02d}.in", "ans_file": f"{idx:02d}.ans", "type_param": str(idx)}
                for idx in range(1, 5)
            ],
        },
    )

    manifest = load_manifest(str(pdir))
    assert manifest is not None
    manifest.constraints = {"n": {"min": 1, "max": 100}}
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    manifest.quality_gates.require_wrong_solution_kill = False
    manifest.audit_gates.enable_mutation_testing = True
    manifest.audit_gates.fail_on_mutation_survival = False
    save_manifest(str(pdir), manifest)

    # Absent mull-runner reports backend="source" gracefully
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    mut_res = await run_mutation_testing(
        str(pdir),
        str(tests_dir),
        manifest=manifest,
        sol_source=sol_cpp,
        budget=4,
    )
    assert mut_res["executed"] is True
    assert mut_res["backend"] == "source"
    assert mut_res["mull_available"] is False
    assert mut_res["survived_mutants"] >= 1

    # Even when a mull-runner binary is present but lacks pass-plugin harness, fallback to backend="source" cleanly
    if sys.platform != "win32":
        fake_mull = tmp_path / "mull-runner"
        fake_mull.write_text("#!/bin/sh\necho mull-stub 0.1\n", encoding="utf-8")
        fake_mull.chmod(0o755)
        mull_fallback_res = await run_mutation_testing(
            str(pdir),
            str(tests_dir),
            manifest=manifest,
            sol_source=sol_cpp,
            budget=4,
            mull_runner_bin=str(fake_mull),
        )
        assert mull_fallback_res["executed"] is True
        assert mull_fallback_res["mull_available"] is True
        assert mull_fallback_res["backend"] == "source"

    # Default fail_on_mutation_survival=False -> warning only, decision="go" (managed & standalone)
    audit_warn = await ProblemAuditTool().execute(
        problem_dir=str(pdir),
        mode="full",
        standalone=False,
        enable_mutation_testing=True,
    )
    assert audit_warn.success
    assert audit_warn.data["decision"] == "go", audit_warn.data["blocking_issues"]
    assert any(w["gate"] == "mutation_testing" for w in audit_warn.data["risk_report"]["warnings"])
    assert not any(b["gate"] == "mutation_testing" for b in audit_warn.data["blocking_issues"])

    audit_standalone_warn = await ProblemAuditTool().execute(
        problem_dir=str(pdir),
        mode="full",
        standalone=True,
        enable_mutation_testing=True,
    )
    assert audit_standalone_warn.success
    assert audit_standalone_warn.data["decision"] == "go", audit_standalone_warn.data["blocking_issues"]
    assert any(w["gate"] == "mutation_testing" for w in audit_standalone_warn.data["risk_report"]["warnings"])
    assert not any(b["gate"] == "mutation_testing" for b in audit_standalone_warn.data["blocking_issues"])

    # fail_on_mutation_survival=True -> blocking_issues in BOTH managed and standalone (zero writes!)
    manifest.audit_gates.fail_on_mutation_survival = True
    save_manifest(str(pdir), manifest)

    audit_managed_block = await ProblemAuditTool().execute(
        problem_dir=str(pdir),
        mode="full",
        standalone=False,
        enable_mutation_testing=True,
    )
    assert audit_managed_block.success
    assert audit_managed_block.data["decision"] == "no_go"
    assert any(b["gate"] == "mutation_testing" for b in audit_managed_block.data["blocking_issues"])

    before_snap = _snapshot_tree(pdir)
    audit_standalone_block = await ProblemAuditTool().execute(
        problem_dir=str(pdir),
        mode="full",
        standalone=True,
        enable_mutation_testing=True,
    )
    after_snap = _snapshot_tree(pdir)
    assert before_snap == after_snap
    assert audit_standalone_block.success
    assert audit_standalone_block.data["decision"] == "no_go"
    assert any(b["gate"] == "mutation_testing" for b in audit_standalone_block.data["blocking_issues"])


@pytest.mark.asyncio
async def test_ub_differential_probe_detects_signed_overflow_divergence_and_clean_passes(
    tmp_path: Path,
) -> None:
    """UB differential probe catches -O0 vs -O2 signed-overflow UB divergence and trap, and passes clean code."""
    ub_sol = tmp_path / "ub_overflow.cpp"
    ub_sol.write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    int x;\n"
        "    if (!(std::cin >> x)) return 0;\n"
        '    if (x + 1 < x) std::cout << "WRAPPED\\n";\n'
        '    else std::cout << "OK\\n";\n'
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )

    before_snap = _snapshot_tree(tmp_path)
    probe_bad = await run_ub_differential_probe(
        ub_sol,
        ["2147483647\n", "10\n"],
        timeout_sec=5.0,
    )
    after_snap = _snapshot_tree(tmp_path)
    assert before_snap == after_snap
    assert probe_bad["executed"] is True
    assert probe_bad["ub_detected"] is True
    assert probe_bad["passed"] is False
    assert "primary_O0" in probe_bad["profiles_compiled"]
    assert "primary_O2" in probe_bad["profiles_compiled"]
    # Either -O0 vs -O2 output divergence OR ub_trap must fire on signed overflow
    assert probe_bad["mismatches"] or probe_bad["ub_traps"]
    if "ub_trap" in probe_bad["profiles_compiled"]:
        assert any(t["profile"] == "ub_trap" for t in probe_bad["ub_traps"])
    if probe_bad["mismatches"]:
        mismatch = probe_bad["mismatches"][0]
        outs = mismatch["outputs_by_profile"]
        statuses = mismatch.get("status_by_profile", {})
        assert len(set(outs.values())) >= 2 or len(set(statuses.values())) >= 2

    clean_sol = tmp_path / "clean.cpp"
    clean_sol.write_text(
        "#include <iostream>\n"
        "int main() {\n"
        "    long long x;\n"
        "    if (std::cin >> x) std::cout << (x + 1) << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    probe_ok = await run_ub_differential_probe(clean_sol, ["2147483647\n", "10\n"])
    assert probe_ok["executed"] is True
    assert probe_ok["ub_detected"] is False
    assert probe_ok["passed"] is True


def test_compact_test_suite_additional_greedy_optimality() -> None:
    """Additional-Greedy compactor preserves 100% target coverage and eliminates redundancy."""
    # Classic case where greedy picks t1 first, then t2 & t3 cover t1 completely -> pruning yields {t2, t3}
    greedy_trap_matrix = {
        "t1": {"b1", "b2", "b3", "b4"},
        "t2": {"b1", "b2", "b5", "b6"},
        "t3": {"b3", "b4", "b7", "b8"},
        "t4_empty": set(),
    }
    res_trap = compact_test_suite(greedy_trap_matrix)
    assert res_trap["selected_count"] == 2
    assert set(res_trap["selected_tests"]) == {"t2", "t3"}
    assert res_trap["coverage_preserved"] == 1.0
    assert res_trap["covered_targets"] == 8
    assert res_trap["total_targets"] == 8

    # Merged multi-dimensional coverage (branches + wrong kills + mutants)
    merged = merge_coverage_matrices(
        branches={"01.in": ["b1", "b2"], "02.in": ["b2", "b3"], "03.in": ["b1", "b2", "b3"], "04.in": ["b4"]},
        wrong_kills={"01.in": ["wrong_wa"], "03.in": ["wrong_wa", "wrong_tle"], "04.in": []},
        mutants={"02.in": ["mut_1"], "03.in": ["mut_1", "mut_2"], "04.in": ["mut_3"]},
    )
    compacted = compact_test_suite(merged)
    assert compacted["selected_count"] == 2
    assert set(compacted["selected_tests"]) == {"03.in", "04.in"}
    assert compacted["coverage_preserved"] == 1.0
    assert compacted["covered_targets"] == compacted["total_targets"]
    assert set(compacted["dropped_tests"]) == {"01.in", "02.in"}

    # Pinned tests, cost-awareness, binary matrix, and empty edge case
    cost_res = compact_test_suite(
        {
            "expensive": ["x", "y"],
            "cheap_1": ["x"],
            "cheap_2": ["y"],
        },
        test_costs={"expensive": 100.0, "cheap_1": 1.0, "cheap_2": 1.0},
    )
    assert set(cost_res["selected_tests"]) == {"cheap_1", "cheap_2"}

    pinned_res = compact_test_suite(
        {"sample_01": ["x"], "big_02": ["x", "y"]},
        pinned_tests=["sample_01"],
    )
    assert "sample_01" in pinned_res["selected_tests"]
    assert pinned_res["coverage_preserved"] == 1.0

    bin_res = compact_test_suite(
        {"a": [1, 0, 1], "b": [0, 1, 0], "c": [1, 1, 1]},
        binary_matrix=True,
    )
    assert bin_res["selected_tests"] == ["c"]
    assert bin_res["covered_targets"] == 3

    empty_res = compact_test_suite({})
    assert empty_res["selected_tests"] == []
    assert empty_res["coverage_preserved"] == 1.0
    assert empty_res["total_targets"] == 0


def test_type_hacks_absent_in_t2b_sources() -> None:
    """Ensure zero `# type: ignore` or `cast(` hacks were added to T2B src modules."""
    repo_root = Path(__file__).resolve().parent.parent
    for rel in (
        "src/autocode_mcp/utils/shrinker.py",
        "src/autocode_mcp/utils/test_compactor.py",
        "src/autocode_mcp/utils/mutation.py",
        "src/autocode_mcp/tools/stress_test.py",
        "src/autocode_mcp/tools/audit.py",
        "src/autocode_mcp/tools/audit_standalone.py",
    ):
        text = (repo_root / rel).read_text(encoding="utf-8")
        assert "type: ignore" not in text, rel
        assert "cast(" not in text, rel
        assert sys.version_info >= (3, 10)
