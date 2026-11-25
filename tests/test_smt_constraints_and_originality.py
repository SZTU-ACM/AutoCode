"""Tests for SMT constraint solver, constraints.h drift, originality index, and audit integration."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from autocode_mcp.runtime_store import set_section
from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.utils.originality import Embedder, OriginalityIndex, ProblemDocument
from autocode_mcp.utils.smt_constraints import (
    SolverBackend,
    audit_manifest_constraints,
    check_constraints_header_drift,
    generate_constraints_header,
    is_z3_available,
    synthesize_boundary_witnesses,
    verify_witness_assignment,
)
from autocode_mcp.workflow import SubtaskSpec, default_manifest, save_manifest


def _active_backends() -> list[SolverBackend]:
    backends: list[SolverBackend] = ["python"]
    if is_z3_available():
        backends.append("z3")
    return backends


@pytest.mark.parametrize("backend", _active_backends())
def test_smt_detects_contradictory_subtasks_unsat(backend: SolverBackend) -> None:
    """Contradictory ranges (min > max, incompatible predicates, parity clash) must fail UNSAT."""
    manifest = default_manifest("UnsatSubtasks")
    manifest.constraints = {"n": {"min": 1, "max": 1000}, "m": {"min": 1, "max": 1000}}
    manifest.subtasks = [
        SubtaskSpec(id=1, name="inverted-bounds", score=30, constraints={"n": {"min": 200, "max": 100}}),
        SubtaskSpec(
            id=2,
            name="incompatible-predicates",
            score=30,
            constraints={"conditions": ["n >= 50", "n <= 20"]},
        ),
        SubtaskSpec(
            id=3,
            name="parity-clash",
            score=40,
            constraints={"n": {"min": 1, "max": 10, "parity": "even"}, "cond": "n % 2 == 1"},
        ),
    ]

    report = audit_manifest_constraints(manifest, solver=backend)
    assert report.solver_used == backend
    assert report.passed is False
    assert report.global_satisfiable is True
    assert report.all_subtasks_satisfiable is False
    assert len(report.subtasks) == 3
    for st in report.subtasks:
        assert st.satisfiable is False
        assert any(c.kind == "unsat" for c in st.conflicts)


@pytest.mark.parametrize("backend", _active_backends())
def test_smt_detects_coupled_relational_contradiction_and_subtask_exceeds_global(
    backend: SolverBackend,
) -> None:
    """Catch subtask bounds exceeding global domain AND coupled multi-var contradictions."""
    manifest = default_manifest("SubtaskContainment")
    manifest.constraints = {
        "n": {"min": 5, "max": 1000},
        "m": {"min": 5, "max": 1000},
        "rel": "n <= m",
    }
    manifest.subtasks = [
        SubtaskSpec(
            id=1,
            name="valid-partial-override",
            score=30,
            constraints={"n_max": 100},
        ),
        SubtaskSpec(
            id=2,
            name="exceeds-global-max",
            score=35,
            constraints={"n": {"min": 5, "max": 5000}},
        ),
        SubtaskSpec(
            id=3,
            name="exceeds-global-min",
            score=20,
            constraints={"n_min": 1, "n_max": 50},
        ),
        SubtaskSpec(
            id=4,
            name="coupled-unsat-with-global",
            score=15,
            constraints={"n": {"min": 200, "max": 300}, "m": {"min": 10, "max": 100}},
        ),
    ]

    report = audit_manifest_constraints(manifest, solver=backend)
    assert report.passed is False
    assert report.all_subtasks_contained is False

    st1, st2, st3, st4 = report.subtasks
    assert st1.satisfiable is True and st1.contained_in_global is True

    assert st2.satisfiable is True and st2.contained_in_global is False
    exceed_max_conflict = next(c for c in st2.conflicts if c.kind == "subtask_exceeds_global")
    assert exceed_max_conflict.witness is not None
    assert exceed_max_conflict.witness["n"] > 1000

    assert st3.satisfiable is True and st3.contained_in_global is False
    exceed_min_conflict = next(c for c in st3.conflicts if c.kind == "subtask_exceeds_global")
    assert exceed_min_conflict.witness is not None
    assert exceed_min_conflict.witness["n"] < 5

    assert st4.satisfiable is False
    assert any(c.kind == "unsat" for c in st4.conflicts)


@pytest.mark.parametrize("backend", _active_backends())
def test_smt_boundary_witness_synthesis_and_verification(backend: SolverBackend) -> None:
    """Synthesized min/max/extreme boundary witnesses must satisfy coupled, parity, and != predicates."""
    manifest = default_manifest("CoupledWitness")
    manifest.constraints = {
        "n": {"min": 1, "max": 100, "parity": "even"},
        "m": {"min": 1, "max": 100, "neq": 100},
        "a_i": r"|a_i| \le 10^9",
        "conditions": ["n < m", r"1 \le k \le 2^{10}"],
    }

    report = audit_manifest_constraints(manifest, solver=backend)
    assert report.passed is True
    assert report.global_satisfiable is True
    assert report.witness_verifications
    assert all(wv.valid for wv in report.witness_verifications)

    witnesses = synthesize_boundary_witnesses(manifest, solver=backend)
    assert "min" in witnesses and "max" in witnesses
    assert "n_min" in witnesses and "n_max" in witnesses
    assert "m_min" in witnesses and "m_max" in witnesses

    # Tight bounds deduced across coupled constraints:
    # n >= 1 & n % 2 == 0 -> n_min = 2
    # m <= 100 & m != 100 -> m_max = 99
    # n < m <= 99 & n % 2 == 0 -> n_max = 98
    # m > n >= 2 -> m_min = 3
    assert witnesses["n_min"]["n"] == 2
    assert witnesses["m_max"]["m"] == 99
    assert witnesses["n_max"]["n"] == 98
    assert witnesses["m_min"]["m"] == 3
    assert witnesses["a_i_min"]["a_i"] == -(10**9)
    assert witnesses["a_i_max"]["a_i"] == 10**9
    assert witnesses["k_max"]["k"] == 1024

    for name, assignment in witnesses.items():
        ver = verify_witness_assignment(assignment, manifest, solver=backend)
        assert ver.valid is True, f"witness {name} failed verification: {ver.unsatisfied_predicates}"

    # Invalid assignment violating n < m and parity must fail verification
    bad_ver = verify_witness_assignment(
        {"n": 3, "m": 3, "a_i": 0, "k": 1},
        manifest,
        solver=backend,
    )
    assert bad_ver.valid is False
    assert bad_ver.unsatisfied_predicates

    # Linear sum & nonlinear product bounds + validator conflict checks
    sum_manifest = default_manifest("SumProd")
    sum_manifest.constraints = {
        "a": {"min": 1, "max": 100},
        "b": {"min": 1, "max": 100},
        "conds": ["a * b <= 10", "a + b <= 11"],
    }
    w_sum = synthesize_boundary_witnesses(sum_manifest, solver=backend)
    assert w_sum["a_max"]["a"] == 10
    assert w_sum["a_max"]["b"] == 1
    assert verify_witness_assignment(w_sum["a_max"], sum_manifest, solver=backend).valid is True

    disjoint_report = audit_manifest_constraints(
        sum_manifest,
        validator_bounds=[{"var": "a", "min": 50, "max": 100, "source": "read_scalar"}],
        solver=backend,
    )
    assert disjoint_report.passed is False
    assert disjoint_report.validator_consistent is False
    assert any(c.kind == "validator_conflict" for c in disjoint_report.conflicts)

    # Empty originality corpus & non-UTF8 edge cases
    empty_idx = OriginalityIndex()
    empty_rep = empty_idx.check("any problem statement")
    assert empty_rep.original is True and empty_rep.max_similarity == 0.0
    assert empty_idx.query("") == []


def test_real_communication_example_passes_smt_audit() -> None:
    """Real examples/communication-sample manifest with partial subtask overrides must pass SMT audit."""
    from autocode_mcp.workflow import load_manifest

    comm_dir = Path(__file__).resolve().parents[1] / "examples" / "communication-sample"
    comm_manifest = load_manifest(str(comm_dir))
    assert comm_manifest is not None
    for backend in _active_backends():
        rep = audit_manifest_constraints(comm_manifest, solver=backend)
        assert rep.passed is True
        assert rep.all_subtasks_satisfiable is True
        assert rep.all_subtasks_contained is True


def test_smt_fallback_to_python_when_z3_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """solver='auto' must fall back seamlessly to pure-Python solver when z3 is absent."""
    import autocode_mcp.utils.smt_constraints as smt_mod

    monkeypatch.setattr(smt_mod, "_load_z3_module", lambda: None)
    assert smt_mod.is_z3_available() is False

    manifest = default_manifest("FallbackOnly")
    manifest.constraints = {"n": {"min": 1, "max": 50}}
    manifest.subtasks = [
        SubtaskSpec(id=1, score=100, constraints={"n_max": 200}),
    ]
    report = audit_manifest_constraints(manifest, solver="auto")
    assert report.solver_used == "python"
    assert report.z3_available is False
    assert report.passed is False
    assert report.all_subtasks_contained is False


def test_constraints_header_generation_and_drift_detection(tmp_path: Path) -> None:
    """generate_constraints_header emits single-source C++ constexpr header and drift check catches edits."""
    manifest = default_manifest("HeaderSingleSource")
    manifest.constraints = {
        "n": {"min": 1, "max": 200_000, "aliases": ["N"]},
        "a_i": {"min": -1_000_000_000, "max": 1_000_000_000},
        "q_max": 50_000,
    }
    manifest.subtasks = [
        SubtaskSpec(id=1, name="small", score=30, constraints={"n_max": 1000}),
    ]

    header = generate_constraints_header(manifest)
    assert "#ifndef AUTOCODE_CONSTRAINTS_H" in header
    assert "<bits/stdc++.h>" not in header
    assert "N_MIN = 1LL;" in header
    assert "N_MAX = 200000LL;" in header
    assert "A_I_MIN = -1000000000LL;" in header
    assert "Q_MAX = 50000LL;" in header
    assert "SUBTASK_1_N_MAX = 1000LL;" in header

    hdr_path = tmp_path / "constraints.h"
    hdr_path.write_text(header, encoding="utf-8")

    clean_drift = check_constraints_header_drift(hdr_path, manifest)
    assert clean_drift.exists is True
    assert clean_drift.has_drift is False
    assert clean_drift.mismatches == ()

    # Value drift in constraints.h
    hdr_path.write_text(header.replace("200000LL", "1000LL"), encoding="utf-8")
    val_drift = check_constraints_header_drift(hdr_path, manifest)
    assert val_drift.has_drift is True
    assert any(m.constant_name == "N_MAX" and m.header_value == 1000 and m.manifest_value == 200000 for m in val_drift.mismatches)

    # Missing newly added manifest constraint
    hdr_path.write_text(header, encoding="utf-8")
    manifest.constraints["m_max"] = 300_000
    missing_drift = check_constraints_header_drift(hdr_path, manifest)
    assert missing_drift.has_drift is True
    assert "M_MAX" in missing_drift.missing_constants


def test_originality_index_catches_paraphrase_and_distinguishes_unrelated(tmp_path: Path) -> None:
    """BM25 + n-gram OriginalityIndex must rank paraphrased duplicates above unrelated tasks (EN & ZH)."""
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    # JSONL corpus with paraphrased tree XOR task & unrelated FFT / grid tasks
    jsonl_path = corpus_dir / "archive.jsonl"
    records = [
        {
            "id": "cf-tree-xor",
            "title": "Tree XOR Path Pairs",
            "statement": (
                "Given a tree with N vertices and N-1 weighted edges, "
                "find the number of simple paths whose XOR sum of edge weights equals K."
            ),
            "tags": ["trees", "xor", "dfs"],
        },
        {
            "id": "unrelated-fft",
            "title": "Polynomial Convolution",
            "statement": (
                "Given two polynomials of degree N, compute their product modulo 998244353 "
                "using Number Theoretic Transform."
            ),
            "tags": ["math", "fft"],
        },
        {
            "id": "unrelated-grid",
            "title": "Shortest Grid Walk",
            "statement": (
                "Find the minimum number of steps to move from the top-left corner to the "
                "bottom-right corner of a binary grid avoiding blocked cells."
            ),
            "tags": ["bfs", "shortest-path"],
        },
    ]
    jsonl_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )

    # Markdown problem in directory (Chinese)
    (corpus_dir / "zh-knapsack.md").write_text(
        "# 背包方案计数\n\n给定 n 个物品，每个物品有体积和价值，求总体积不超过 m 的最大价值。\n",
        encoding="utf-8",
    )

    index = OriginalityIndex.from_directory(corpus_dir)
    assert index.size == 4

    paraphrased_query = (
        "You are given an undirected acyclic connected graph (a tree) with n nodes and "
        "weighted edges. Count pairs of nodes such that the bitwise XOR of edge weights "
        "along the unique path between them equals target k."
    )
    report = index.check(paraphrased_query, threshold=0.25)
    assert report.corpus_size == 4
    assert report.used_dense_embedder is False
    assert report.matches[0].problem_id == "cf-tree-xor"
    assert report.matches[0].score > report.matches[1].score * 2.0
    assert report.original is False
    assert any(term in report.matches[0].overlapping_terms for term in ("xor", "tree", "path", "weight", "edg"))

    completely_unrelated = (
        "Implement a persistent segment tree supporting range affine updates and "
        "online kth order statistics queries on Euler tour intervals."
    )
    unrelated_report = index.check(completely_unrelated, threshold=0.35)
    assert unrelated_report.original is True
    assert unrelated_report.max_similarity < 0.35

    # Chinese paraphrased duplicate check
    zh_query = "有 n 个物品，已知每个物品的体积和价值，计算总体积至多为 m 时的最大价值。"
    zh_matches = index.query(zh_query, top_k=2)
    assert zh_matches[0].problem_id == "zh-knapsack"
    assert zh_matches[0].score > zh_matches[1].score


def test_originality_index_dense_embedder_protocol_and_path_containment(tmp_path: Path) -> None:
    """Embedder protocol integrates cleanly and escaping symlinks/traversal are rejected safely."""

    class _StubCPRetriever:
        def embed_texts(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
            vecs: list[list[float]] = []
            for t in texts:
                low = t.lower()
                vecs.append(
                    [
                        1.0 if ("segment" in low or "range" in low) else 0.0,
                        1.0 if ("flow" in low or "network" in low) else 0.0,
                        0.1,
                    ]
                )
            return vecs

    stub = _StubCPRetriever()
    assert isinstance(stub, Embedder)

    docs = [
        ProblemDocument(problem_id="p-seg", title="Range Queries", statement="Maintain array under range updates."),
        ProblemDocument(problem_id="p-flow", title="Max Flow", statement="Compute maximum s-t flow in a network."),
    ]
    idx = OriginalityIndex(docs, embedder=stub)
    res = idx.check("Answer dynamic range minimum queries with a segment tree.", threshold=0.35)
    assert res.used_dense_embedder is True
    assert res.matches[0].problem_id == "p-seg"
    assert res.matches[0].dense_cosine is not None and res.matches[0].dense_cosine > 0.9

    # Path traversal & escaping symlink guards
    corpus_dir = tmp_path / "safe_corpus"
    corpus_dir.mkdir()
    (corpus_dir / "ok.md").write_text("# Valid\n\nSimple math problem.\n", encoding="utf-8")
    outside_secret = tmp_path / "outside_secret.md"
    outside_secret.write_text("# TopSecret\n\nCONFIDENTIAL_NEVER_INDEX_TOKEN\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes root"):
        idx.add_corpus_file(corpus_dir, "../outside_secret.md")

    escape_link = corpus_dir / "escape.md"
    try:
        os.symlink(outside_secret, escape_link)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")

    safe_idx = OriginalityIndex.from_directory(corpus_dir)
    assert [d.problem_id for d in safe_idx.documents] == ["ok"]
    assert "escape.md" in safe_idx.skipped_unsafe_paths
    assert safe_idx.query("CONFIDENTIAL_NEVER_INDEX_TOKEN")[0].score == 0.0

    with pytest.raises(ValueError, match="Unsafe escaping path"):
        OriginalityIndex.from_directory(corpus_dir, strict_containment=True)


def _setup_minimal_problem(pdir: Path) -> None:
    for sub in ("files", "solutions", "statements", "tests"):
        (pdir / sub).mkdir(parents=True, exist_ok=True)
    (pdir / "statements" / "README.md").write_text(
        "# Demo\n\n## 输入格式\n\n给定 $1 \\le n \\le 1000$。\n\n## 样例\n\n```text\n1\n```\n",
        encoding="utf-8",
    )
    (pdir / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n" + "Complete complexity analysis and correctness proof for the problem. " * 3,
        encoding="utf-8",
    )
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\nint main(){int n; if(std::cin>>n) std::cout<<n<<'\\n';}\n",
        encoding="utf-8",
    )
    (pdir / "tests" / "01.in").write_text("1\n", encoding="utf-8")
    (pdir / "tests" / "01.ans").write_text("1\n", encoding="utf-8")
    set_section(str(pdir), "test_manifest", {"tests": [{"in_file": "01.in", "type_param": "1"}]})


@pytest.mark.asyncio
async def test_audit_constraints_blocks_smt_conflict_and_constraints_header_drift(
    tmp_path: Path,
) -> None:
    """_statement_consistency & ProblemAuditTool must block SMT subtask conflicts and constraints.h drift."""
    # 1. Contradictory subtask fails _statement_consistency and blocks ProblemAuditTool
    p_unsat = tmp_path / "smt_unsat_pkg"
    _setup_minimal_problem(p_unsat)
    m_unsat = default_manifest("SmtUnsat")
    m_unsat.constraints = {"n": {"min": 1, "max": 1000}}
    m_unsat.subtasks = [
        SubtaskSpec(id=1, score=100, constraints={"n_max": 5000}),
    ]
    m_unsat.quality_gates.require_tests_verified = False
    m_unsat.quality_gates.require_limit_semantics = False
    m_unsat.quality_gates.require_validator_check = False
    m_unsat.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(p_unsat), m_unsat)

    tool = ProblemAuditTool()
    sc_unsat = tool._statement_consistency(p_unsat, m_unsat, {"tests": [1]})
    assert sc_unsat["passed"] is False
    assert sc_unsat["needs_human_review"] is True
    assert "SMT constraint conflict" in str(sc_unsat["message"])
    smt_info = sc_unsat["smt_constraints"]
    assert isinstance(smt_info, dict)
    assert smt_info["passed"] is False
    assert smt_info["all_subtasks_contained"] is False

    audit_unsat = await tool.execute(problem_dir=str(p_unsat), mode="full")
    assert audit_unsat.success
    assert audit_unsat.data["decision"] == "no_go"
    assert any(
        b.get("gate") == "statement_consistency" and "subtask 1" in str(b.get("reason", ""))
        for b in audit_unsat.data["blocking_issues"]
    )

    # 2. Single-source constraints.h passes when synced, blocks when drifted
    p_hdr = tmp_path / "hdr_pkg"
    _setup_minimal_problem(p_hdr)
    m_hdr = default_manifest("HdrSync")
    m_hdr.constraints = {"n": {"min": 1, "max": 1000}}
    m_hdr.quality_gates.require_tests_verified = False
    m_hdr.quality_gates.require_limit_semantics = False
    m_hdr.quality_gates.require_validator_check = False
    m_hdr.quality_gates.require_wrong_solution_kill = False
    save_manifest(str(p_hdr), m_hdr)

    (p_hdr / "files" / "constraints.h").write_text(
        generate_constraints_header(m_hdr), encoding="utf-8"
    )
    (p_hdr / "files" / "val.cpp").write_text(
        '#include "testlib.h"\n'
        '#include "constraints.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "  registerValidation(argc, argv);\n"
        '  inf.readInt(N_MIN, N_MAX, "n");\n'
        "  inf.readEof();\n"
        "}\n",
        encoding="utf-8",
    )

    sc_ok = tool._statement_consistency(p_hdr, m_hdr, {"tests": [1]})
    assert sc_ok["passed"] is True
    assert sc_ok["validator_bounds_checked"] == 1
    assert sc_ok["validator_bound_mismatches"] == []
    hdr_drift = sc_ok["constraints_header_drift"]
    assert isinstance(hdr_drift, dict)
    assert hdr_drift["exists"] is True
    assert hdr_drift["has_drift"] is False

    # Introduce drift into files/constraints.h
    bad_hdr = generate_constraints_header(m_hdr).replace("1000LL", "500LL")
    (p_hdr / "files" / "constraints.h").write_text(bad_hdr, encoding="utf-8")

    sc_drift = tool._statement_consistency(p_hdr, m_hdr, {"tests": [1]})
    assert sc_drift["passed"] is False
    assert sc_drift["needs_human_review"] is True
    assert "constraints.h drift" in str(sc_drift["message"])

    audit_drift = await tool.execute(problem_dir=str(p_hdr), mode="full")
    assert audit_drift.success
    assert audit_drift.data["decision"] == "no_go"
    assert any(
        b.get("gate") == "statement_consistency" and "constraints.h" in str(b.get("reason", ""))
        for b in audit_drift.data["blocking_issues"]
    )
