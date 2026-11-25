"""Regression tests for Issues #15 and #16 (static checks and statement consistency)."""

from __future__ import annotations

import hashlib
import os
import random
from pathlib import Path
from typing import Any

import pytest

from autocode_mcp.cli.report_format import format_audit_markdown, format_audit_summary
from autocode_mcp.runtime_store import set_section
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.tools.verify_static_checks import _extract_effective_scale
from autocode_mcp.workflow import default_manifest, save_manifest


def _write_test_manifest_entries(
    tests_dir: Path,
    type3_texts: list[str],
    type4_texts: list[str],
    *,
    std_timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    idx = 1
    for t_param, group, texts in (
        ("3", "limit", type3_texts),
        ("4", "limit", type4_texts),
    ):
        for body in texts:
            in_name = f"{idx:02d}.in"
            ans_name = f"{idx:02d}.ans"
            (tests_dir / in_name).write_text(body, encoding="utf-8")
            (tests_dir / ans_name).write_text("0\n", encoding="utf-8")
            sig = hashlib.md5(body.encode("utf-8")).hexdigest()
            entry: dict[str, Any] = {
                "index": idx,
                "in_file": in_name,
                "ans_file": ans_name,
                "type_param": t_param,
                "group": group,
                "signature": sig,
            }
            if std_timings and in_name in std_timings:
                entry["std_time_ms"] = std_timings[in_name]
            entries.append(entry)
            idx += 1
    manifest: dict[str, Any] = {"version": 1, "tests": entries}
    set_section(str(tests_dir.parent), "test_manifest", manifest)
    return manifest


def test_limit_semantics_rejects_homogeneous_random_clones_with_zero_md5_overlap(
    tmp_path: Path,
) -> None:
    """Two batches from the same random distribution must fail even when MD5 overlap is 0.0."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    def _gen_random_batch(seed_base: int, n: int = 200) -> list[str]:
        out: list[str] = []
        for offset in range(4):
            rng = random.Random(seed_base + offset)
            arr = " ".join(str(rng.randint(1, 10**9)) for _ in range(n))
            out.append(f"{n}\n{arr}\n")
        return out

    type3_texts = _gen_random_batch(1000)
    type4_texts = _gen_random_batch(2000)
    manifest = _write_test_manifest_entries(tests_dir, type3_texts, type4_texts)

    result = ProblemVerifyTestsTool()._check_limit_semantics(str(tests_dir), manifest)
    assert result["passed"] is False
    assert result["type3_count"] == 4
    assert result["type4_count"] == 4
    assert result["overlap_ratio"] == 0.0
    assert result["structural_divergence"] < result["divergence_threshold"]
    assert result["hint"]

    expected_dims = {
        "byte_size",
        "line_count",
        "token_count",
        "unique_token_ratio",
        "distinct_token_count",
        "numeric_ratio",
        "max_abs_int",
        "avg_tokens_per_line",
        "max_line_tokens",
        "adjacent_equal_ratio",
        "monotonic_step_ratio",
    }
    assert expected_dims.issubset(result["type3_profile"].keys())
    assert expected_dims.issubset(result["type4_profile"].keys())


def test_limit_semantics_rejects_slight_parameter_bump_random_clones(tmp_path: Path) -> None:
    """Slightly scaling n under the same random distribution must still fail limit_semantics."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()

    def _gen_batch(seed_base: int, n: int) -> list[str]:
        out: list[str] = []
        for offset in range(3):
            rng = random.Random(seed_base + offset)
            arr = " ".join(str(rng.randint(1, 10**9)) for _ in range(n))
            out.append(f"{n}\n{arr}\n")
        return out

    manifest = _write_test_manifest_entries(
        tests_dir,
        _gen_batch(300, 100),
        _gen_batch(400, 108),
    )
    result = ProblemVerifyTestsTool()._check_limit_semantics(str(tests_dir), manifest)
    assert result["passed"] is False
    assert result["overlap_ratio"] == 0.0
    assert result["structural_divergence"] < result["divergence_threshold"]


def test_limit_semantics_passes_for_distinct_worst_case_structures(tmp_path: Path) -> None:
    """Constant stress vs random distinct values AND chain tree vs random tree must pass."""
    # 1. Constant/uniform max stress vs distinct random permutation
    tdir1 = tmp_path / "p1" / "tests"
    tdir1.mkdir(parents=True)
    n = 200
    const_cases = [f"{n}\n" + " ".join(["1000000000"] * n) + "\n" for _ in range(2)]
    const_cases[1] = f"{n}\n" + " ".join(["999999999"] * n) + "\n"
    perm_cases: list[str] = []
    for seed in (11, 12):
        rng = random.Random(seed)
        vals = list(range(1, n + 1))
        rng.shuffle(vals)
        perm_cases.append(f"{n}\n" + " ".join(str(v) for v in vals) + "\n")
    m1 = _write_test_manifest_entries(tdir1, const_cases, perm_cases)

    res1 = ProblemVerifyTestsTool()._check_limit_semantics(str(tdir1), m1)
    assert res1["passed"] is True
    assert res1["structural_divergence"] >= 0.15
    assert res1["hint"] == ""
    assert res1["type3_profile"]["adjacent_equal_ratio"] > 0.9
    assert res1["type4_profile"]["unique_token_ratio"] > 0.9

    # 2. Same n, byte size, line count, token count: chain tree vs random tree
    tdir2 = tmp_path / "p2" / "tests"
    tdir2.mkdir(parents=True)
    chain_lines = [f"{i} {i + 1}" for i in range(1, n)]
    chain_cases = [f"{n}\n" + "\n".join(chain_lines) + "\n"]
    chain_cases.append(f"{n}\n" + "\n".join(reversed(chain_lines)) + "\n")

    rand_tree_cases: list[str] = []
    for seed in (21, 22):
        rng = random.Random(seed)
        perm = list(range(1, n + 1))
        rng.shuffle(perm)
        edges = []
        for idx in range(1, n):
            u = perm[rng.randrange(idx)]
            v = perm[idx]
            if rng.randint(0, 1):
                u, v = v, u
            edges.append(f"{u} {v}")
        rng.shuffle(edges)
        rand_tree_cases.append(f"{n}\n" + "\n".join(edges) + "\n")
    m2 = _write_test_manifest_entries(tdir2, chain_cases, rand_tree_cases)

    res2 = ProblemVerifyTestsTool()._check_limit_semantics(str(tdir2), m2)
    assert res2["passed"] is True
    assert res2["structural_divergence"] >= 0.15


def test_limit_semantics_std_timings_and_safe_path_containment(tmp_path: Path) -> None:
    """Incorporates optional std timings and rejects escaping symlink/traversal paths."""
    tests_dir = tmp_path / "p_time" / "tests"
    tests_dir.mkdir(parents=True)
    m = _write_test_manifest_entries(
        tests_dir,
        ["10\n1 2 3 4 5 6 7 8 9 10\n"],
        ["10\n10 9 8 7 6 5 4 3 2 1\n"],
        std_timings={"01.in": 2.0, "02.in": 480.0},
    )
    res = ProblemVerifyTestsTool()._check_limit_semantics(str(tests_dir), m)
    assert res["passed"] is True
    assert res["type3_profile"]["std_time_ms"] == pytest.approx(2.0)
    assert res["type4_profile"]["std_time_ms"] == pytest.approx(480.0)

    # Path traversal outside tests_dir must fail safely
    outside = tmp_path / "secret.in"
    outside.write_text("999\n", encoding="utf-8")
    bad_manifest = {
        "version": 1,
        "tests": [
            {"in_file": "01.in", "type_param": "3", "signature": "sig_a"},
            {"in_file": "../secret.in", "type_param": "4", "signature": "sig_b"},
        ],
    }
    res_bad = ProblemVerifyTestsTool()._check_limit_semantics(str(tests_dir), bad_manifest)
    assert res_bad["passed"] is False
    assert "../secret.in" in res_bad.get("unsafe_files", [])

    # Escaping symlink must also be rejected
    sym_path = tests_dir / "03.in"
    try:
        os.symlink(outside, sym_path)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    sym_manifest = {
        "version": 1,
        "tests": [
            {"in_file": "01.in", "type_param": "3", "signature": "sig_a"},
            {"in_file": "03.in", "type_param": "4", "signature": "sig_c"},
        ],
    }
    res_sym = ProblemVerifyTestsTool()._check_limit_semantics(str(tests_dir), sym_manifest)
    assert res_sym["passed"] is False
    assert "03.in" in res_sym.get("unsafe_files", [])


def test_extract_effective_scale_multitest_and_single_test_disambiguation() -> None:
    """Verify scale_token_index, multi_test, unambiguous heuristic, and single-test protection."""
    m_idx = default_manifest("Idx")
    m_idx.scale_token_index = 2
    assert _extract_effective_scale("1 9 200000\n", m_idx) == 200000

    m_multi = default_manifest("Multi")
    m_multi.multi_test = True
    assert _extract_effective_scale("1\n200000\n", m_multi) == 200000
    assert _extract_effective_scale("1\n200000\n1 2 3 4\n", m_multi) == 200000

    # Without manifest, unambiguous multi-test structure still resolves n instead of T
    big_body = " ".join(["1"] * 50)
    assert _extract_effective_scale(f"1\n200000\n{big_body}\n", None) == 200000
    assert _extract_effective_scale("3\n5\n1 2 3 4 5\n4\n1 2 3 4\n6\n1 2 3 4 5 6\n", None) == 5

    # Single-test n=1 with large value a[0]=10^9 and constraints n_max=100000 must stay n=1
    m_single = default_manifest("Single")
    m_single.multi_test = False
    m_single.constraints = {"n": {"min": 1, "max": 100000}}
    assert _extract_effective_scale("1\n1000000000\n", m_single) == 1

    # Single-test tree (n=5 followed by edges 1 2, 2 3, ...) must stay n=5
    assert _extract_effective_scale("5\n1 2\n2 3\n3 4\n4 5\n", None) == 5
    # Single-test array (n=5 followed by 5 elements) must stay n=5
    assert _extract_effective_scale("5\n10 20 30 40 50\n", None) == 5


def test_scale_distribution_multitest_avoids_tiny_and_uniform_collapse_fails(
    tmp_path: Path,
) -> None:
    """T=1, n=200000 is never bucketed as tiny; >=3 uniform n=1 cases fail unless constrained."""
    # 1. Single T=1, n=200000 test must land in large, never tiny (with AND without manifest)
    p_single = tmp_path / "mt_single"
    t_single = p_single / "tests"
    t_single.mkdir(parents=True)
    payload = "1\n200000\n" + " ".join(["7"] * 2000) + "\n"
    (t_single / "01.in").write_text(payload, encoding="utf-8")

    verifier = ProblemVerifyTestsTool()
    res_no_manifest = verifier._check_scale_distribution(str(t_single))
    assert "tiny" not in res_no_manifest["effective_scale_buckets"]
    assert "tiny" not in res_no_manifest["first_integer_buckets"]
    assert res_no_manifest["effective_scale_buckets"].get("large") == 1
    assert res_no_manifest["min_effective_scale"] == 200000

    # With manifest.multi_test=True and diverse cases, suite passes
    m_mt = default_manifest("MT")
    m_mt.multi_test = True
    m_mt.constraints = {"n": {"min": 1, "max": 200000}}
    save_manifest(str(p_single), m_mt)
    (t_single / "02.in").write_text("1\n1\n5\n", encoding="utf-8")
    (t_single / "03.in").write_text("1\n50\n" + " ".join(["3"] * 50) + "\n", encoding="utf-8")
    res_mt = verifier._check_scale_distribution(str(t_single))
    assert res_mt["passed"] is True
    assert res_mt["effective_scale_buckets"] == {"tiny": 1, "medium": 1, "large": 1}

    # 2. Uniform n=1 suite (>= 3 cases) collapses to 1 bucket and MUST fail
    p_uniform = tmp_path / "uniform_tiny"
    t_uniform = p_uniform / "tests"
    t_uniform.mkdir(parents=True)
    m_big = default_manifest("UniformFail")
    m_big.constraints = {"n": {"min": 1, "max": 100000}}
    save_manifest(str(p_uniform), m_big)
    for i in range(1, 6):
        (t_uniform / f"{i:02d}.in").write_text("1\n", encoding="utf-8")
    res_uniform = verifier._check_scale_distribution(str(t_uniform))
    assert res_uniform["passed"] is False
    assert res_uniform["non_empty_scale_bucket_count"] == 1
    assert res_uniform["narrow_constraint_exempt"] is False

    # 3. Narrow constraint (n_max <= 10) exempts single-bucket collapse
    p_narrow = tmp_path / "narrow_ok"
    t_narrow = p_narrow / "tests"
    t_narrow.mkdir(parents=True)
    m_narrow = default_manifest("Narrow")
    m_narrow.constraints = {"n": {"min": 1, "max": 10}}
    save_manifest(str(p_narrow), m_narrow)
    for i, val in enumerate((2, 4, 6, 8), 1):
        (t_narrow / f"{i:02d}.in").write_text(f"{val}\n", encoding="utf-8")
    res_narrow = verifier._check_scale_distribution(str(t_narrow))
    assert res_narrow["passed"] is True
    assert res_narrow["narrow_constraint_exempt"] is True

    # 4. Mode-prefixed communication problem uses second token X instead of binary mode flag
    p_mode = tmp_path / "mode_comm"
    t_mode = p_mode / "tests"
    t_mode.mkdir(parents=True)
    m_mode = default_manifest("ModeComm", problem_type="communication", oracle_mode="interactor_only")
    m_mode.constraints = {"X": {"min": 0, "max": 15}, "mode": {"min": 0, "max": 1}}
    save_manifest(str(p_mode), m_mode)
    for idx, payload_txt in enumerate(("0 3\n", "1 5\n", "1 12\n"), 1):
        (t_mode / f"{idx:02d}.in").write_text(payload_txt, encoding="utf-8")
    res_mode_ok = verifier._check_scale_distribution(str(t_mode))
    assert res_mode_ok["passed"] is True
    assert res_mode_ok["effective_scale_buckets"] == {"small": 2, "medium": 1}

    for idx, payload_txt in enumerate(("0 1\n", "1 1\n", "1 1\n"), 1):
        (t_mode / f"{idx:02d}.in").write_text(payload_txt, encoding="utf-8")
    res_mode_fail = verifier._check_scale_distribution(str(t_mode))
    assert res_mode_fail["passed"] is False
    assert res_mode_fail["effective_scale_buckets"] == {"tiny": 3}


def test_boundary_coverage_uses_effective_scale_for_multitest(tmp_path: Path) -> None:
    """_check_boundary_coverage must inspect case scale rather than T on multi-test suites."""
    pdir = tmp_path / "bc_mt"
    tdir = pdir / "tests"
    tdir.mkdir(parents=True)
    manifest = default_manifest("BCMT")
    manifest.multi_test = True
    manifest.constraints = {"n": {"min": 1, "max": 200000}}
    save_manifest(str(pdir), manifest)

    (tdir / "01.in").write_text("1\n1\n42\n", encoding="utf-8")
    (tdir / "02.in").write_text("1\n200000\n" + " ".join(["1"] * 50) + "\n", encoding="utf-8")

    res = ProblemVerifyTestsTool()._check_boundary_coverage(str(tdir), manifest)
    assert res["passed"] is True
    assert res["observed_min"] == 1
    assert res["observed_max"] == 200000

    # Without T=1, n=200000 case, boundary_coverage fails cleanly even if T=200
    (tdir / "02.in").write_text("200\n500\n1 2\n", encoding="utf-8")
    res_miss = ProblemVerifyTestsTool()._check_boundary_coverage(str(tdir), manifest)
    assert res_miss["passed"] is False
    assert res_miss["observed_max"] == 500


@pytest.mark.asyncio
async def test_statement_consistency_blocks_val_mismatch_and_renders_markdown_and_summary(
    tmp_path: Path,
) -> None:
    """val.cpp max=1000 vs statement 10^5 must set passed=False and render table + summary."""
    pdir = tmp_path / "stmt_block"
    for sub in ("files", "solutions", "statements", "tests"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)

    (pdir / "statements" / "README.md").write_text(
        "# Test Problem\n\n"
        "## 输入格式\n\n给定整数 $n$（$1 \\le n \\le 10^5$）。\n\n"
        "## 样例\n\n```text\n3\n```\n",
        encoding="utf-8",
    )
    (pdir / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n" + "Detailed O(n) algorithmic analysis and proof of correctness. " * 3,
        encoding="utf-8",
    )
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<n<<'\\n';}\n",
        encoding="utf-8",
    )
    (pdir / "files" / "val.cpp").write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "  registerValidation(argc, argv);\n"
        '  inf.readInt(1, 1000, "n");\n'
        "  inf.readEof();\n"
        "}\n",
        encoding="utf-8",
    )
    for i, val in enumerate((1, 5, 100, 1000), 1):
        (pdir / "tests" / f"{i:02d}.in").write_text(f"{val}\n", encoding="utf-8")
        (pdir / "tests" / f"{i:02d}.ans").write_text(f"{val}\n", encoding="utf-8")

    manifest = default_manifest("StmtBlock")
    manifest.constraints = {"n": {"min": 1, "max": 100000}, "q_max": 50000}
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_validator_check = False
    manifest.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(pdir), manifest)

    set_section(str(pdir), "test_manifest", {"tests": [{"in_file": "01.in", "type_param": "1"}]})

    sc = ProblemAuditTool()._statement_consistency(pdir, manifest, {"tests": [1]})
    assert sc["passed"] is False
    assert sc["needs_human_review"] is True
    assert sc["validator_bound_mismatches"]
    assert sc["validator_bound_mismatches"][0]["var"] == "n"
    assert sc["validator_bound_mismatches"][0]["max"] == 1000
    assert "n" in sc["message"]
    assert "1000" in sc["message"]
    assert "100000" in sc["message"]
    assert "q_max" in sc["message"]
    assert "n" in sc["review_reason"]
    assert "q_max" in sc["review_reason"]

    audit_res = await ProblemAuditTool().execute(problem_dir=str(pdir), mode="full")
    assert audit_res.success
    assert audit_res.data["decision"] == "no_go"
    assert any(
        b.get("gate") == "statement_consistency" and "1000" in str(b.get("reason", ""))
        for b in audit_res.data["blocking_issues"]
    )

    payload = audit_res.to_dict()
    md = format_audit_markdown(payload)
    assert "## Statement Consistency & Communication Metrics" in md
    assert "| Variable | Validator Range | Reason |" in md
    assert "| `n` | `[1, 1000]` |" in md
    assert "100000" in md
    assert "**Missing Constraint Terms from Statement:**" in md
    assert "q_max (expected bound 50000)" in md

    summary = format_audit_summary(payload)
    assert "STMT_CONSISTENCY: passed=False mismatches=1 vars=[n] missing_terms=1" in summary


def test_report_format_renders_consistency_on_passed_true_and_escapes_adversarial() -> None:
    """Section renders when passed=True with missing terms/mismatches and neutralizes injections."""
    payload_review: dict[str, Any] = {
        "success": True,
        "error": None,
        "data": {
            "decision": "go",
            "mode": "full",
            "standalone": False,
            "detected_layout": "autocode",
            "problem_dir": "/tmp/review",
            "blocking_issues": [],
            "risk_report": {
                "warnings": [],
                "statement_consistency": {
                    "passed": True,
                    "needs_human_review": True,
                    "message": "",
                    "review_reason": "missing statement constraint terms: m (expected bound 500000)",
                    "validator_bound_mismatches": [],
                    "constraint_terms_missing_from_statement": ["m (expected bound 500000)"],
                },
            },
        },
    }
    md_review = format_audit_markdown(payload_review)
    assert "## Statement Consistency & Communication Metrics" in md_review
    assert "**REVIEW**" in md_review
    assert "m (expected bound 500000)" in md_review
    sum_review = format_audit_summary(payload_review)
    assert "STMT_CONSISTENCY: passed=True mismatches=0 missing_terms=1" in sum_review

    # Adversarial escaping in validator_bound_mismatches & missing terms
    payload_adv: dict[str, Any] = {
        "success": True,
        "error": None,
        "data": {
            "decision": "no_go",
            "mode": "full",
            "standalone": True,
            "detected_layout": "auto",
            "problem_dir": "/tmp/adv",
            "blocking_issues": [],
            "risk_report": {
                "warnings": [],
                "statement_consistency": {
                    "passed": False,
                    "message": "bad | msg <script>\x1b[31mRED\x1b[0m",
                    "validator_bound_mismatches": [
                        {
                            "var": "n|evil\n# Heading",
                            "min": 1,
                            "max": 1000,
                            "reason": "max=1000 != 10^5 | <img src=x>",
                        }
                    ],
                    "constraint_terms_missing_from_statement": ["k\n# FakeSection <script>"],
                },
            },
        },
    }
    md_adv = format_audit_markdown(payload_adv)
    assert "\x1b" not in md_adv
    assert "<script>" not in md_adv
    assert "<img " not in md_adv
    assert "\n# Heading" not in md_adv
    assert "\n# FakeSection" not in md_adv
    assert r"n\|evil" in md_adv

    sum_adv = format_audit_summary(payload_adv)
    assert "\x1b" not in sum_adv
    assert "\n# Heading" not in sum_adv
    assert "STMT_CONSISTENCY: passed=False mismatches=1 vars=[n|evil # Heading] missing_terms=1" in sum_adv
