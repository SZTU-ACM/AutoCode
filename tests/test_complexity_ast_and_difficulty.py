"""Regression tests for Track T1D: inter-procedural AST complexity, CP idioms,
evidence-only difficulty signals, and deterministic instruction counting."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.complexity import (
    ComplexityLevel,
    _analyze_functions_tree_sitter,
    analyze_cpp_interprocedural,
    analyze_loop_complexity,
    detect_algorithm_patterns,
    estimate_static_complexity,
    has_effective_nested_loops,
)
from autocode_mcp.utils.execution_monitor import DynamicExecutionMonitor
from autocode_mcp.utils.ratio_analyzer import EmpiricalRatioAnalyzer
from autocode_mcp.workflow import default_manifest, save_manifest
from autocode_mcp.workflow.models import SolutionEntry

# ---------------------------------------------------------------------------
# 1. Inter-procedural C++ Static Complexity (pure-Python + CST path)
# ---------------------------------------------------------------------------


def test_interprocedural_helper_loop_inside_outer_for_escalates_to_quadratic() -> None:
    """Loops extracted into helper functions called inside outer loops must count toward nesting."""
    code = """
#include <iostream>
using namespace std;
long long ans = 0;

void inner_loop(int n) {
    for (int j = 0; j < n; j++) {
        ans += j;
    }
}

void sol(int n) {
    for (int i = 0; i < n; i++) {
        inner_loop(n);
    }
}

int main() {
    int n;
    cin >> n;
    sol(n);
    cout << ans << '\\n';
    return 0;
}
"""
    comp, _ = estimate_static_complexity(code)
    assert comp == ComplexityLevel.QUADRATIC
    assert has_effective_nested_loops(code) is True
    assert analyze_loop_complexity(code) == ComplexityLevel.QUADRATIC


def test_interprocedural_three_hop_chain_and_recursive_helper_chain() -> None:
    """Multi-hop helper chains and non-DFS recursive helper chains accumulate nesting depth."""
    three_hop = """
void f3(int n) { for (int k = 0; k < n; k++) x += k; }
void f2(int n) { for (int j = 0; j < n; j++) f3(n); }
void f1(int n) { for (int i = 0; i < n; i++) f2(n); }
"""
    comp3, _ = estimate_static_complexity(three_hop)
    assert comp3 == ComplexityLevel.CUBIC

    rec_chain = """
int ans = 0;
void work(int n) {
    for (int j = 0; j < n; j++) ans++;
}
void rec_step(int k, int n) {
    if (k <= 0) return;
    work(n);
    rec_step(k - 1, n);
}
int main() {
    int n = 100;
    rec_step(n, n);
    return 0;
}
"""
    comp_rec, _ = estimate_static_complexity(rec_chain)
    assert comp_rec == ComplexityLevel.QUADRATIC
    assert has_effective_nested_loops(rec_chain) is True


def test_interprocedural_multitest_wrapper_and_sequential_helpers_stay_linear() -> None:
    """Standard `while (T--) solve();` and sequential O(n) helpers must NOT inflate to O(n^2)."""
    code = """
#include <iostream>
using namespace std;
void pass1(int n) { for (int i = 0; i < n; i++) {} }
void pass2(int n) { for (int i = 0; i < n; i++) {} }
void solve() {
    int n; cin >> n;
    pass1(n);
    pass2(n);
}
int main() {
    int T; cin >> T;
    while (T--) solve();
    return 0;
}
"""
    comp, _ = estimate_static_complexity(code)
    assert comp == ComplexityLevel.LINEAR
    assert has_effective_nested_loops(code) is False


def test_interprocedural_named_lambda_inside_loop_escalates_to_quadratic() -> None:
    """Named C++ lambdas called inside a loop contribute their inner loop depth."""
    code = """
int main() {
    int n = 100;
    long long ans = 0;
    auto work = [&](int m) {
        for (int j = 0; j < m; j++) ans += j;
    };
    for (int i = 0; i < n; i++) {
        work(n);
    }
    return 0;
}
"""
    comp, _ = estimate_static_complexity(code)
    assert comp == ComplexityLevel.QUADRATIC


def test_optional_tree_sitter_cst_path_when_mocked_and_fallback_without_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise CST traversal directly via minimal node stubs and verify python_fallback when absent."""
    import autocode_mcp.tools.complexity as comp_mod

    code = (
        "void inner_loop(int n) { for (int j = 0; j < n; j++) {} }\n"
        "void sol(int n) { for (int i = 0; i < n; i++) inner_loop(n); }\n"
    )
    code_bytes = code.encode("utf-8")

    # Without tree_sitter installed (or forced None), python_fallback succeeds
    monkeypatch.setattr(comp_mod, "_load_tree_sitter_cpp", lambda: None)
    fb_summary = analyze_cpp_interprocedural(code)
    assert fb_summary.backend == "python_fallback"
    assert fb_summary.max_depth == 2

    # Stub minimal tree-sitter CST matching Language/Parser/Node contract
    @dataclass
    class FakeNode:
        type: str
        start_byte: int
        end_byte: int
        children: list[FakeNode] = field(default_factory=list)
        fields: dict[str, FakeNode] = field(default_factory=dict)

        def child_by_field_name(self, name: str) -> FakeNode | None:
            return self.fields.get(name)

    def span(sub: str, offset: int = 0) -> tuple[int, int]:
        idx = code.index(sub, offset)
        return idx, idx + len(sub)

    inner_id_s, inner_id_e = span("inner_loop")
    inner_decl = FakeNode(
        "function_declarator",
        *span("inner_loop(int n)"),
        fields={"declarator": FakeNode("identifier", inner_id_s, inner_id_e)},
    )
    inner_for_body = FakeNode("compound_statement", *span("{}"))
    inner_for = FakeNode(
        "for_statement",
        *span("for (int j = 0; j < n; j++) {}"),
        children=[inner_for_body],
        fields={"body": inner_for_body},
    )
    inner_body = FakeNode("compound_statement", *span("{ for (int j = 0; j < n; j++) {} }"), children=[inner_for])
    inner_fn = FakeNode(
        "function_definition",
        *span("void inner_loop(int n) { for (int j = 0; j < n; j++) {} }"),
        fields={"declarator": inner_decl, "body": inner_body},
    )

    sol_line_off = code.index("void sol")
    sol_id_s, sol_id_e = span("sol", sol_line_off)
    sol_decl = FakeNode(
        "function_declarator",
        *span("sol(int n)", sol_line_off),
        fields={"declarator": FakeNode("identifier", sol_id_s, sol_id_e)},
    )
    call_id_s, call_id_e = span("inner_loop(n)", sol_line_off)
    call_expr = FakeNode(
        "call_expression",
        call_id_s,
        call_id_e,
        fields={"function": FakeNode("identifier", call_id_s, call_id_s + len("inner_loop"))},
    )
    sol_for = FakeNode(
        "for_statement",
        *span("for (int i = 0; i < n; i++) inner_loop(n);", sol_line_off),
        children=[call_expr],
        fields={"body": call_expr},
    )
    sol_body = FakeNode(
        "compound_statement",
        *span("{ for (int i = 0; i < n; i++) inner_loop(n); }", sol_line_off),
        children=[sol_for],
    )
    sol_fn = FakeNode(
        "function_definition",
        *span("void sol(int n) { for (int i = 0; i < n; i++) inner_loop(n); }", sol_line_off),
        fields={"declarator": sol_decl, "body": sol_body},
    )
    root = FakeNode("translation_unit", 0, len(code_bytes), children=[inner_fn, sol_fn])

    class FakeTree:
        root_node = root

    class FakeParser:
        def __init__(self, _lang: object) -> None:
            pass

        def parse(self, _raw: bytes) -> FakeTree:
            return FakeTree()

    monkeypatch.setattr(
        comp_mod,
        "_load_tree_sitter_cpp",
        lambda: (lambda x: x, FakeParser, lambda: "cpp"),
    )
    cst_fns = _analyze_functions_tree_sitter(code, ignore_multitest=True, ignore_small_const=True)
    assert cst_fns is not None
    assert set(cst_fns.keys()) == {"inner_loop", "sol"}
    cst_summary = analyze_cpp_interprocedural(code)
    assert cst_summary.backend == "tree_sitter_cpp"
    assert cst_summary.max_depth == 2
    est_comp, _ = estimate_static_complexity(code)
    assert est_comp == ComplexityLevel.QUADRATIC


# ---------------------------------------------------------------------------
# 2. Chinese CP Idioms & Contextual lower_bound
# ---------------------------------------------------------------------------


def test_chinese_cp_idioms_fenwick_segtree_binary_lifting_and_others() -> None:
    """Chinese CP idioms without English names must be detected with accurate complexities."""
    fenwick_lowbit = """
int n, tr[200005];
int lowbit(int x) { return x & -x; }
void add(int x, int v) {
    for (; x <= n; x += lowbit(x)) tr[x] += v;
}
int qry(int x) {
    int res = 0;
    for (; x; x -= x & -x) res += tr[x];
    return res;
}
int main() {
    for (int i = 1; i <= n; i++) add(i, 1);
    return qry(n);
}
"""
    fw_comp, fw_pats = estimate_static_complexity(fenwick_lowbit)
    assert fw_comp == ComplexityLevel.N_LOG_N
    assert "fenwick_tree" in fw_pats
    assert "tree_data_structure" in fw_pats

    segtree_cn = """
#define ls (p << 1)
#define rs (p << 1 | 1)
int tr[800005], tag[800005];
void pushup(int p) { tr[p] = tr[ls] + tr[rs]; }
void pushdown(int p, int l, int r) {
    int mid = (l + r) >> 1;
    if (tag[p]) {
        tag[ls] += tag[p]; tag[rs] += tag[p];
        tag[p] = 0;
    }
}
void update(int p, int l, int r, int ql, int qr, int v) {
    if (ql <= l && r <= qr) { tr[p] += v; tag[p] += v; return; }
    pushdown(p, l, r);
    int mid = (l + r) >> 1;
    if (ql <= mid) update(ls, l, mid, ql, qr, v);
    if (qr > mid) update(rs, mid + 1, r, ql, qr, v);
    pushup(p);
}
"""
    st_comp, st_pats = estimate_static_complexity(segtree_cn)
    assert st_comp == ComplexityLevel.N_LOG_N
    assert "segment_tree" in st_pats
    assert "tree_data_structure" in st_pats

    bin_lift = """
int fa[100005][20];
int main() {
    int n = 100000;
    for (int j = 1; j < 20; j++)
        for (int i = 1; i <= n; i++)
            fa[i][j] = fa[fa[i][j - 1]][j - 1];
    return 0;
}
"""
    bl_comp, bl_pats = estimate_static_complexity(bin_lift)
    assert bl_comp == ComplexityLevel.N_LOG_N
    assert "binary_lifting" in bl_pats
    assert "bitmask" not in bl_pats

    bitmask_dp = """
long long f[1 << 18][18];
int main() {
    int n = 18;
    for (int mask = 0; mask < (1 << n); mask++)
        for (int i = 0; i < n; i++)
            f[mask][i] += i;
    return 0;
}
"""
    bm_comp, bm_pats = estimate_static_complexity(bitmask_dp)
    assert bm_comp == ComplexityLevel.EXPONENTIAL
    assert "bitmask" in bm_pats
    assert "bitmask_dp" in bm_pats

    two_ptr = """
int main() {
    int n = 100000;
    int ans = 0;
    for (int l = 0, r = 0; r < n; r++) {
        while (l <= r && a[r] - a[l] > 10) l++;
        ans += r - l + 1;
    }
    return ans;
}
"""
    tp_comp, tp_pats = estimate_static_complexity(two_ptr)
    assert tp_comp == ComplexityLevel.LINEAR
    assert "two_pointers" in tp_pats

    mono_q = """
int q[100005], a[100005];
int main() {
    int n = 100000, hh = 0, tt = -1;
    for (int i = 0; i < n; i++) {
        while (hh <= tt && a[q[tt]] <= a[i]) tt--;
        q[++tt] = i;
    }
    return 0;
}
"""
    mq_comp, mq_pats = estimate_static_complexity(mono_q)
    assert mq_comp == ComplexityLevel.LINEAR
    assert "monotonic_queue" in mq_pats

    dsu_code = """
int fa[100005], sz[100005];
int find(int x) { return fa[x] == x ? x : fa[x] = find(fa[x]); }
int main() {
    for (int i = 1; i <= 100000; i++) fa[i] = i;
    return find(1);
}
"""
    dsu_comp, dsu_pats = estimate_static_complexity(dsu_code)
    assert dsu_comp == ComplexityLevel.LINEAR
    assert "dsu" in dsu_pats

    dijkstra_fw_star = """
#include <queue>
using namespace std;
int head[100005], nxt[200005], to[200005], dist[100005];
int main() {
    priority_queue<pair<int, int>> pq;
    pq.push({0, 1});
    while (!pq.empty()) {
        auto [d, u] = pq.top(); pq.pop();
        for (int i = head[u]; i; i = nxt[i]) pq.push({d - 1, to[i]});
    }
    return 0;
}
"""
    dj_comp, dj_pats = estimate_static_complexity(dijkstra_fw_star)
    assert dj_comp == ComplexityLevel.N_LOG_N
    assert "dijkstra" in dj_pats
    assert "heap" in dj_pats


def test_lower_bound_contextual_loop_vs_non_loop() -> None:
    """Top-level single lower_bound must NOT jump to O(n log n); inside a loop (or helper in loop) it must."""
    top_only = """
int main() {
    int pos = lower_bound(arr, arr + n, target) - arr;
    return pos;
}
"""
    comp_top, pats_top = estimate_static_complexity(top_only)
    assert "binary_search" in pats_top
    assert comp_top == ComplexityLevel.LOG_N

    linear_plus_one_bsearch = """
int main() {
    int n; cin >> n;
    vector<int> a(n);
    for (int i = 0; i < n; i++) cin >> a[i];
    int pos = upper_bound(a.begin(), a.end(), 42) - a.begin();
    return pos;
}
"""
    comp_lin, pats_lin = estimate_static_complexity(linear_plus_one_bsearch)
    assert "binary_search" in pats_lin
    assert comp_lin == ComplexityLevel.LINEAR

    in_loop = """
int main() {
    for (int i = 0; i < q; i++) {
        ans += lower_bound(a.begin(), a.end(), qry[i]) - a.begin();
    }
    return 0;
}
"""
    comp_loop, _ = estimate_static_complexity(in_loop)
    assert comp_loop == ComplexityLevel.N_LOG_N

    in_helper_called_in_loop = """
int find_pos(const vector<int>& a, int x) {
    return lower_bound(a.begin(), a.end(), x) - a.begin();
}
int main() {
    int ans = 0;
    for (int i = 0; i < q; i++) ans += find_pos(a, qry[i]);
    return ans;
}
"""
    comp_helper, _ = estimate_static_complexity(in_helper_called_in_loop)
    assert comp_helper == ComplexityLevel.N_LOG_N

    # Also verify detect_algorithm_patterns preserves legacy contract
    pat_comp, _ = detect_algorithm_patterns(top_only)
    assert pat_comp == ComplexityLevel.N_LOG_N


# ---------------------------------------------------------------------------
# 3. Evidence-only _difficulty_signals & Deterministic Invariance
# ---------------------------------------------------------------------------


def test_difficulty_signals_evidence_only_and_invariant_to_wrong_and_brute(tmp_path: Path) -> None:
    """_difficulty_signals must never map complexity/wrong_count to ratings and must remain invariant."""
    pdir = tmp_path / "prob_inv"
    for sub in ("solutions", "statements", "tests"):
        (pdir / sub).mkdir(parents=True)
    (pdir / "statements" / "README.md").write_text("# Prob\n\n## 样例\n", encoding="utf-8")
    (pdir / "statements" / "tutorial.md").write_text("# Ed\n\nProof details.\n", encoding="utf-8")
    (pdir / "solutions" / "sol.cpp").write_text(
        "#include <iostream>\n"
        "int main(){\n"
        "  int n; std::cin >> n;\n"
        "  long long s = 0;\n"
        "  for (int i = 0; i < n; i++) s += i;\n"
        "  std::cout << s << '\\n';\n"
        "}\n",
        encoding="utf-8",
    )
    (pdir / "solutions" / "brute.cpp").write_text(
        "int main(){ int n; for (int i = 0; i < n; i++) {} return 0; }\n",
        encoding="utf-8",
    )

    manifest = default_manifest("LinearConstructive")
    manifest.constraints = {"n": {"min": 1, "max": 200000}, "a_i": {"min": 1, "max": 10**12}}
    save_manifest(str(pdir), manifest)

    base_quality = {
        "answer_consistency": {"executed": True, "passed": True},
        "limit_semantics": {"executed": True, "passed": True},
        "empirical_complexity": {
            "executed": True,
            "passed": True,
            "evidence": {
                "status": "ok",
                "verdict": "verified",
                "fitted_complexity": "O(n)",
                "r_squared": 0.998,
                "fit_metric": "instruction_count",
                "max_scale_headroom_ratio": 0.045,
            },
        },
    }
    tool = ProblemAuditTool()
    sig_base = tool._difficulty_signals(pdir, manifest, base_quality)

    assert sig_base["rating"] is None
    assert sig_base["band"] == "uncalibrated"
    assert sig_base["calibration_required"] is True
    assert sig_base["rating_method"] == "evidence_only_v2"
    assert 0.0 <= float(sig_base["confidence"]) <= 1.0  # type: ignore[arg-type]
    assert sig_base["estimated_complexity"] == "O(n)"

    se = sig_base["structural_evidence"]
    assert isinstance(se, dict)
    assert se["static_complexity"] == "O(n)"
    assert se["n_max"] == 200000
    assert se["largest_value_bound"] == 10**12
    assert se["tl_headroom_ratio"] == pytest.approx(0.045, abs=1e-4)
    assert se["wrong_solution_count"] == 0
    assert se["empirical_complexity"]["fit_metric"] == "instruction_count"

    cd = sig_base["cognitive_dimensions"]
    assert isinstance(cd, dict)
    assert cd["knowledge_signals"]["requires_64bit_values"] is True
    assert cd["logic_signals"]["loop_nesting_depth"] == 1
    assert cd["observation_signals"]["requires_llm_statement_calibration"] is True
    assert cd["observation_signals"]["low_complexity_high_scale"] is True

    # Mutate: add 5 wrong solutions AND escalate brute.cpp to O(2^n) bitmask backtracking
    for idx in range(5):
        manifest.solutions.append(
            SolutionEntry(name=f"wrong_{idx}", role="wrong", path=f"solutions/wrong_{idx}.cpp")
        )
    (pdir / "solutions" / "brute.cpp").write_text(
        "int main(){ for(int m=0;m<(1<<n);m++) for(int i=0;i<n;i++) for(int j=0;j<n;j++){} }\n",
        encoding="utf-8",
    )
    sig_after = tool._difficulty_signals(pdir, manifest, base_quality)

    assert sig_after["rating"] is None
    assert sig_after["band"] == "uncalibrated"
    assert sig_after["calibration_required"] is True
    assert sig_after["confidence"] == sig_base["confidence"]
    assert sig_after["estimated_complexity"] == sig_base["estimated_complexity"]
    assert sig_after["cognitive_dimensions"] == sig_base["cognitive_dimensions"]
    assert sig_after["structural_evidence"]["wrong_solution_count"] == 5  # type: ignore[index]

    # Empty problem evidence gives confidence == 0.0, rating is None
    empty_dir = tmp_path / "empty_prob"
    empty_dir.mkdir()
    empty_manifest = default_manifest("Empty")
    sig_empty = tool._difficulty_signals(empty_dir, empty_manifest, {})
    assert sig_empty["rating"] is None
    assert sig_empty["band"] == "uncalibrated"
    assert sig_empty["confidence"] == 0.0


# ---------------------------------------------------------------------------
# 4. Deterministic Instruction Counting & Ratio Fitting
# ---------------------------------------------------------------------------


def test_execution_monitor_metric_resolution_cmd_builders_and_parsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify AUTOCODE_METRIC parsing, perf/cachegrind builders, and instruction parsers."""
    monkeypatch.delenv("AUTOCODE_METRIC", raising=False)
    assert DynamicExecutionMonitor.resolve_metric_backend() == "auto"
    monkeypatch.setenv("AUTOCODE_METRIC", "CACHEGRIND")
    assert DynamicExecutionMonitor.resolve_metric_backend() == "cachegrind"
    monkeypatch.setenv("AUTOCODE_METRIC", "invalid_backend")
    assert DynamicExecutionMonitor.resolve_metric_backend() == "auto"
    assert DynamicExecutionMonitor.resolve_metric_backend("perf") == "perf"

    perf_cmd = DynamicExecutionMonitor.build_perf_command(
        ["/tmp/sol"], perf_bin="/usr/bin/perf", output_file="/tmp/p.csv"
    )
    assert perf_cmd == ["/usr/bin/perf", "stat", "-x", ",", "-e", "instructions", "-o", "/tmp/p.csv", "--", "/tmp/sol"]

    cg_cmd = DynamicExecutionMonitor.build_cachegrind_command(
        ["/tmp/sol"], valgrind_bin="/usr/bin/valgrind", log_file="/tmp/cg.log"
    )
    assert cg_cmd[0] == "/usr/bin/valgrind"
    assert "--tool=cachegrind" in cg_cmd
    assert f"--cachegrind-out-file={os.devnull}" in cg_cmd
    assert "--log-file=/tmp/cg.log" in cg_cmd
    assert cg_cmd[-1] == "/tmp/sol"

    # Standard perf CSV + hybrid Intel PMU + <not counted>
    assert (
        DynamicExecutionMonitor.parse_perf_stat_instructions(
            "# started on 2026\n1234567,,instructions:u,1500000,100.00,,\n"
        )
        == 1234567
    )
    assert (
        DynamicExecutionMonitor.parse_perf_stat_instructions(
            "1000000,,cpu_core/instructions/,1000,100.00,,\n"
            "<not counted>,,cpu_atom/instructions/,0,100.00,,\n"
        )
        == 1000000
    )
    assert (
        DynamicExecutionMonitor.parse_perf_stat_instructions(
            "1000000,,cpu_core/instructions/,1000,100.00,,\n"
            "250000,,cpu_atom/instructions/,500,100.00,,\n"
        )
        == 1250000
    )
    assert (
        DynamicExecutionMonitor.parse_perf_stat_instructions(
            "<not counted>,,instructions,0,100.00,,\n"
        )
        is None
    )

    # Cachegrind summary & file parser
    assert (
        DynamicExecutionMonitor.parse_cachegrind_irefs(
            "==4211== I   refs:      9,876,543\n==4211== I1  misses:      1,234\n"
        )
        == 9876543
    )
    assert (
        DynamicExecutionMonitor.parse_cachegrind_irefs("events: Ir\nfl: foo.cpp\nsummary: 543210\n")
        == 543210
    )
    assert DynamicExecutionMonitor.parse_cachegrind_irefs("valgrind error") is None


@pytest.mark.asyncio
async def test_execution_monitor_perf_cachegrind_and_clean_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verify real-subprocess perf/cachegrind wrapper paths and clean cpu_time fallback on EACCES."""
    import autocode_mcp.utils.execution_monitor as em_mod

    in_file = tmp_path / "in.txt"
    in_file.write_text("42\n", encoding="utf-8")
    target_script = tmp_path / "target_prog.py"
    target_script.write_text(
        "import sys\nval = sys.stdin.read().strip()\nprint(f'out:{val}')\n",
        encoding="utf-8",
    )

    if sys.platform != "win32":
        shim_dir = tmp_path / "shims"
        shim_dir.mkdir()

        perf_ok_shim = shim_dir / "perf_ok"
        perf_ok_shim.write_text(
            f"#!{sys.executable}\n"
            "import subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "out_file = args[args.index('-o') + 1] if '-o' in args else None\n"
            "cmd = args[args.index('--') + 1:]\n"
            "proc = subprocess.run(cmd, stdin=sys.stdin, capture_output=True, text=True)\n"
            "sys.stdout.write(proc.stdout)\n"
            "sys.stderr.write(proc.stderr)\n"
            "if out_file:\n"
            "    with open(out_file, 'w', encoding='utf-8') as f:\n"
            "        f.write('4200000,,instructions,2000,100.00,,\\n')\n"
            "sys.exit(proc.returncode)\n",
            encoding="utf-8",
        )
        perf_ok_shim.chmod(perf_ok_shim.stat().st_mode | stat.S_IXUSR)

        perf_eacces_shim = shim_dir / "perf_eacces"
        perf_eacces_shim.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "sys.stderr.write('Access to performance monitoring and observability operations is limited.\\n')\n"
            "sys.exit(255)\n",
            encoding="utf-8",
        )
        perf_eacces_shim.chmod(perf_eacces_shim.stat().st_mode | stat.S_IXUSR)

        vg_shim = shim_dir / "valgrind"
        vg_shim.write_text(
            f"#!{sys.executable}\n"
            "import subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "log_file = next((a.split('=', 1)[1] for a in args if a.startswith('--log-file=')), None)\n"
            "cmd_idx = next(i for i, a in enumerate(args) if not a.startswith('-'))\n"
            "proc = subprocess.run(args[cmd_idx:], stdin=sys.stdin, capture_output=True, text=True)\n"
            "sys.stdout.write(proc.stdout)\n"
            "sys.stderr.write(proc.stderr)\n"
            "if log_file:\n"
            "    with open(log_file, 'w', encoding='utf-8') as f:\n"
            "        f.write('==99== I   refs:      8,500,000\\n')\n"
            "sys.exit(proc.returncode)\n",
            encoding="utf-8",
        )
        vg_shim.chmod(vg_shim.stat().st_mode | stat.S_IXUSR)

        monkeypatch.setattr(em_mod.sys, "platform", "linux")

        # 1. Real subprocess perf success
        monkeypatch.setattr(shutil, "which", lambda name: str(perf_ok_shim) if name == "perf" else None)
        res_perf = await DynamicExecutionMonitor.run_monitored_process(
            [sys.executable, str(target_script)], str(in_file), metric="perf"
        )
        assert res_perf["status"] == "ok"
        assert res_perf["metric_backend"] == "perf"
        assert res_perf["instruction_count"] == 4200000
        assert "out:42" in res_perf["stdout"]

        # 2. Real subprocess perf permission error -> clean cpu_time fallback re-runs target
        monkeypatch.setattr(shutil, "which", lambda name: str(perf_eacces_shim) if name == "perf" else None)
        res_fb = await DynamicExecutionMonitor.run_monitored_process(
            [sys.executable, str(target_script)], str(in_file), metric="auto"
        )
        assert res_fb["status"] == "ok"
        assert res_fb["metric_backend"] == "cpu_time"
        assert res_fb["instruction_count"] is None
        assert "out:42" in res_fb["stdout"]

        # 3. Real subprocess cachegrind success with zero CWD artifacts
        monkeypatch.setattr(shutil, "which", lambda name: str(vg_shim) if name == "valgrind" else None)
        before_entries = {p.name for p in tmp_path.iterdir()}
        res_cg = await DynamicExecutionMonitor.run_monitored_process(
            [sys.executable, str(target_script)], str(in_file), cwd=str(tmp_path), metric="cachegrind"
        )
        after_entries = {p.name for p in tmp_path.iterdir()}
        assert res_cg["status"] == "ok"
        assert res_cg["metric_backend"] == "cachegrind"
        assert res_cg["instruction_count"] == 8500000
        assert "out:42" in res_cg["stdout"]
        assert before_entries == after_entries
    else:
        # Windows fallback test where POSIX shebang shims do not apply
        res_win = await DynamicExecutionMonitor.run_monitored_process(
            [sys.executable, str(target_script)], str(in_file), metric="auto"
        )
        assert res_win["status"] == "ok"
        assert res_win["metric_backend"] == "cpu_time"
        assert res_win["instruction_count"] is None


def test_empirical_ratio_analyzer_prefers_instructions_and_falls_back_to_cpu_or_wall() -> None:
    """EmpiricalRatioAnalyzer uses instruction_count when available across scales, else cpu/wall time."""
    # 1. Noisy flat cpu_time_ms (5.0ms across all scales) + clean O(n) instruction_count -> passes!
    linear_inst_samples = [
        {"n": 1000, "cpu_time_ms": 5.0, "instruction_count": 10_000, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 5.0, "instruction_count": 30_000, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 5.0, "instruction_count": 100_000, "status": "ok"},
        {"n": 30000, "cpu_time_ms": 5.0, "instruction_count": 300_000, "status": "ok"},
    ]
    res_lin = EmpiricalRatioAnalyzer.verify_complexity("O(n)", linear_inst_samples, time_limit_ms=2000.0)
    assert res_lin["passed"] is True
    assert res_lin["verdict"] == "verified"
    assert res_lin["fit_metric"] == "instruction_count"
    assert res_lin["fitted_alpha"] == pytest.approx(1.0, abs=0.02)
    assert res_lin["r_squared"] > 0.999
    # Headroom must still come from cpu_time_ms / time_limit_ms (5/2000 = 0.0025), NOT instruction_count!
    assert res_lin["max_scale_headroom_ratio"] == pytest.approx(0.0025, abs=0.001)

    # 2. Sub-2ms O(n^2) instruction growth pretending to be O(n) must NOT hide in timer noise floor
    quad_inst_samples = [
        {"n": 100, "cpu_time_ms": 0.4, "instruction_count": 10_000, "status": "ok"},
        {"n": 200, "cpu_time_ms": 0.5, "instruction_count": 40_000, "status": "ok"},
        {"n": 400, "cpu_time_ms": 0.6, "instruction_count": 160_000, "status": "ok"},
        {"n": 800, "cpu_time_ms": 0.8, "instruction_count": 640_000, "status": "ok"},
    ]
    res_quad = EmpiricalRatioAnalyzer.verify_complexity("O(n)", quad_inst_samples, time_limit_ms=2000.0)
    assert res_quad["passed"] is False
    assert res_quad["verdict"] == "ratio_mismatch"
    assert res_quad["fit_metric"] == "instruction_count"
    assert res_quad["fitted_complexity"] == "O(n^2)"

    # 3. C++ CRT startup instruction plateau (~1,000,000 fixed) + O(n^2) tail still falsified
    crt_plateau_samples = [
        {"n": 50, "cpu_time_ms": 1.0, "instruction_count": 1_002_500, "status": "ok"},
        {"n": 100, "cpu_time_ms": 1.0, "instruction_count": 1_010_000, "status": "ok"},
        {"n": 400, "cpu_time_ms": 1.5, "instruction_count": 1_160_000, "status": "ok"},
        {"n": 1600, "cpu_time_ms": 4.0, "instruction_count": 3_560_000, "status": "ok"},
        {"n": 6400, "cpu_time_ms": 40.0, "instruction_count": 41_960_000, "status": "ok"},
    ]
    res_crt = EmpiricalRatioAnalyzer.verify_complexity("O(n)", crt_plateau_samples, time_limit_ms=2000.0)
    assert res_crt["passed"] is False
    assert res_crt["verdict"] == "ratio_mismatch"
    assert res_crt["fit_metric"] == "instruction_count"

    # 4. Partial instruction_count (missing at last scale) cleanly falls back to cpu_time_ms
    partial_inst = [
        {"n": 100, "cpu_time_ms": 1.0, "instruction_count": 1000, "status": "ok"},
        {"n": 200, "cpu_time_ms": 4.0, "instruction_count": 4000, "status": "ok"},
        {"n": 400, "cpu_time_ms": 16.0, "instruction_count": None, "status": "ok"},
    ]
    res_partial = EmpiricalRatioAnalyzer.verify_complexity("O(n)", partial_inst, time_limit_ms=2000.0)
    assert res_partial["passed"] is False
    assert res_partial["fit_metric"] == "cpu_time_ms"

    # 5. wall_time_ms fallback when both instruction_count and cpu_time_ms are absent
    wall_only = [
        {"n": 1000, "cpu_time_ms": None, "wall_time_ms": 2.0, "status": "ok"},
        {"n": 3000, "cpu_time_ms": None, "wall_time_ms": 6.0, "status": "ok"},
        {"n": 10000, "cpu_time_ms": None, "wall_time_ms": 20.0, "status": "ok"},
    ]
    res_wall = EmpiricalRatioAnalyzer.verify_complexity("O(n)", wall_only, time_limit_ms=2000.0)
    assert res_wall["passed"] is True
    assert res_wall["fit_metric"] == "wall_time_ms"
    assert res_wall["fitted_alpha"] == pytest.approx(1.0, abs=0.05)


@pytest.mark.asyncio
async def test_perf_stderr_signal_detection_and_bare_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When perf stat exits 0 after a child fatal signal, fall back to bare execution to capture real RE."""
    from autocode_mcp.utils import execution_monitor as em_mod

    assert DynamicExecutionMonitor._perf_stderr_indicates_signal(
        "/tmp/work/abort: Aborted (core dumped)\n1234,,instructions:u\n",
        ["/tmp/work/abort"],
    )
    assert DynamicExecutionMonitor._perf_stderr_indicates_signal(
        "abort: Segmentation fault\n",
        ["/tmp/work/abort"],
    )
    assert not DynamicExecutionMonitor._perf_stderr_indicates_signal(
        "other_proc: Aborted\n",
        ["/tmp/work/abort"],
    )

    in_file = tmp_path / "in.txt"
    in_file.write_text("1\n", encoding="utf-8")
    monkeypatch.setattr(em_mod.sys, "platform", "linux")
    monkeypatch.setattr(em_mod.shutil, "which", lambda name: "/usr/bin/perf" if name == "perf" else None)

    calls: list[list[str]] = []

    async def fake_once(
        cls,
        effective_cmd: list[str],
        input_file_path: str,
        **_kwargs,
    ) -> dict:
        calls.append(list(effective_cmd))
        if effective_cmd[0] == "/usr/bin/perf":
            return {
                "status": "ok",
                "returncode": 0,
                "cpu_time_ms": 5.0,
                "wall_time_ms": 5.0,
                "memory_mb": 4.0,
                "stdout": "",
                "stderr": f"{effective_cmd[-1]}: Aborted\n2953007,,instructions:u,100.00,,\n",
            }
        return {
            "status": "runtime_error",
            "returncode": -6,
            "cpu_time_ms": None,
            "wall_time_ms": 2.0,
            "memory_mb": 1.0,
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(
        DynamicExecutionMonitor,
        "_run_monitored_once",
        classmethod(fake_once),
    )

    res = await DynamicExecutionMonitor.run_monitored_process(
        ["/tmp/work/abort"], str(in_file), metric="perf"
    )
    assert len(calls) == 2
    assert calls[0][0] == "/usr/bin/perf"
    assert calls[1] == ["/tmp/work/abort"]
    assert res["status"] == "runtime_error"
    assert res["returncode"] == -6
    assert res["metric_backend"] == "cpu_time"
    assert res["instruction_count"] is None
