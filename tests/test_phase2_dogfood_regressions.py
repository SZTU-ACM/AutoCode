from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.complexity import ComplexityLevel, SolutionAnalyzeTool
from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.problem import ProblemCreateTool
from autocode_mcp.tools.solution import SolutionBuildTool
from autocode_mcp.tools.solution_audit import SolutionAuditStdTool
from autocode_mcp.tools.stress_test import StressTestRunTool, _has_sanitizer_diagnostic
from autocode_mcp.tools.validator import ValidatorBuildTool
from autocode_mcp.utils.compiler import (
    _ALLOWED_EXTRA_FLAGS,
    _SANITIZER_PROBE_CPP,
    probe_sanitizer_support,
)
from autocode_mcp.workflow import load_manifest, save_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = REPO_ROOT / "examples"


def test_sanitizer_probe_uses_runtime_size_and_isolates_ubsan_during_asan(monkeypatch):
    import autocode_mcp.utils.compiler as comp_mod

    assert "volatile int n" in _SANITIZER_PROBE_CPP
    assert "-D_GLIBCXX_ASSERTIONS" in _ALLOWED_EXTRA_FLAGS
    assert _has_sanitizer_diagnostic("Assertion '__n < this->size()' failed")
    assert _has_sanitizer_diagnostic("/usr/include/c++/12/bits/stl_vector.h:1123: __glibcxx_assert")

    asan_envs: list[str] = []

    class FakeProc:
        def __init__(self, rc: int, stderr: str = "") -> None:
            self.returncode = rc
            self.stdout = ""
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        if "-std=c++17" in cmd:
            return FakeProc(0)
        mode = cmd[1] if len(cmd) > 1 else "ok"
        if mode == "ok":
            return FakeProc(0)
        if mode == "asan":
            env = kwargs.get("env") or {}
            asan_envs.append(str(env.get("UBSAN_OPTIONS", "")))
            return FakeProc(1, "ERROR: AddressSanitizer: heap-buffer-overflow")
        if mode == "ubsan":
            return FakeProc(1, "runtime error: signed integer overflow")
        return FakeProc(0)

    monkeypatch.setattr(comp_mod.sys, "platform", "linux")
    monkeypatch.setattr(comp_mod, "_detect_linux_preload", lambda: False)
    monkeypatch.setattr(comp_mod.subprocess, "run", fake_run)

    res = probe_sanitizer_support("g++", use_cache=False)
    assert res["available"] is True
    assert res["asan_verified"] is True
    assert asan_envs and asan_envs[0].startswith("halt_on_error=0")


@pytest.mark.asyncio
async def test_stress_test_sanitizer_catches_vector_oob(tmp_path):
    probe = probe_sanitizer_support("g++", use_cache=False)
    if not probe.get("available"):
        pytest.skip("Sanitizers unavailable on this toolchain")

    prob_dir = tmp_path / "san_vec_oob"
    await ProblemCreateTool().execute(problem_dir=str(prob_dir), problem_name="Vec OOB")
    await SolutionBuildTool().execute(
        problem_dir=str(prob_dir),
        solution_type="sol",
        code=(
            "#include <iostream>\n"
            "#include <vector>\n"
            "int main() {\n"
            "    int n;\n"
            "    std::cin >> n;\n"
            "    std::vector<int> a(n, 1);\n"
            "    a[n] = 99;\n"
            "    std::cout << a[0] << '\\n';\n"
            "    return 0;\n"
            "}\n"
        ),
    )
    await SolutionBuildTool().execute(
        problem_dir=str(prob_dir),
        solution_type="brute",
        code="#include <iostream>\nint main(){int n;std::cin>>n;std::cout<<1<<'\\n';return 0;}\n",
    )
    await ValidatorBuildTool().execute(
        problem_dir=str(prob_dir),
        code=(
            '#include "testlib.h"\n'
            "int main(int c, char** v) {\n"
            "    registerValidation(c, v);\n"
            '    inf.readInt(1, 100, "n");\n'
            "    inf.readEoln();\n"
            "    inf.readEof();\n"
            "    return 0;\n"
            "}\n"
        ),
        test_cases=[{"input": "5\n", "valid": True}, {"input": "0\n", "valid": False}],
    )
    await GeneratorBuildTool().execute(
        problem_dir=str(prob_dir),
        code=(
            "#include <iostream>\n"
            '#include "testlib.h"\n'
            "int main(int c, char** v) {\n"
            "    registerGen(c, v, 1);\n"
            '    std::cout << rnd.next(2, 6) << "\\n";\n'
            "    return 0;\n"
            "}\n"
        ),
    )

    stress_res = await StressTestRunTool().execute(
        problem_dir=str(prob_dir), trials=5, n_max=5, enable_sanitizers=True
    )
    assert not stress_res.success
    if sys.platform == "darwin" or probe.get("asan_verified"):
        assert probe.get("asan_verified") is True


@pytest.mark.asyncio
async def test_communication_standalone_audit_source_only_run_twice_and_grader_linked(tmp_path):
    # 1. Run-Twice standalone without prebuilt binaries or .ans files
    p_rt = tmp_path / "comm_rt_src_only"
    shutil.copytree(EXAMPLES_DIR / "communication-sample", p_rt)
    tests_dir = p_rt / "tests"
    tests_dir.mkdir(exist_ok=True)
    for idx, payload in enumerate(("0 3\n", "1 5\n", "1 12\n"), start=1):
        (tests_dir / f"{idx:02d}.in").write_text(payload, encoding="utf-8")

    res_rt = await ProblemAuditTool().execute(
        problem_dir=str(p_rt),
        mode="full",
        standalone=True,
        enable_mutation_testing=False,
    )
    assert res_rt.success, res_rt.error
    assert res_rt.data["decision"] == "go", res_rt.data["blocking_issues"]
    assert res_rt.data["quality_signals"]["file_count"]["passed"] is True
    assert res_rt.data["quality_signals"]["answer_consistency"]["passed"] is True

    # 2. Grader-Linked standalone with wrong solution and no prebuilt binaries or .ans files
    p_gl = tmp_path / "comm_gl_src_only"
    await ProblemCreateTool().execute(
        problem_dir=str(p_gl),
        problem_name="GL Standalone",
        problem_type="communication",
        protocol_mode="grader_linked",
        oracle_mode="interactor_only",
    )
    (p_gl / "files" / "problem.h").write_text(
        "#ifndef P_H\n#define P_H\n#include <vector>\n"
        "std::vector<int> encode(int n);\n"
        "int decode(const std::vector<int>& m);\n#endif\n",
        encoding="utf-8",
    )
    (p_gl / "files" / "grader.cpp").write_text(
        '#include <iostream>\n#include <vector>\n#include "problem.h"\n'
        "int main() {\n"
        "    int n; if (!(std::cin >> n)) return 3;\n"
        "    auto m = encode(n); int a = decode(m);\n"
        "    int bits = static_cast<int>(m.size());\n"
        '    if (a == n) { std::cerr << "ok bits_used=" << bits << " rounds=2\\n"; return 0; }\n'
        '    std::cerr << "wrong answer\\n"; return 1;\n'
        "}\n",
        encoding="utf-8",
    )
    (p_gl / "solutions" / "sol.cpp").write_text(
        '#include <cstddef>\n#include "problem.h"\n'
        "std::vector<int> encode(int n) {\n"
        "    std::vector<int> o; for (int i = 0; i < 8; ++i) o.push_back((n >> i) & 1); return o;\n"
        "}\n"
        "int decode(const std::vector<int>& m) {\n"
        "    int n = 0; for (std::size_t i = 0; i < m.size(); ++i) if (m[i]) n |= (1 << i); return n;\n"
        "}\n",
        encoding="utf-8",
    )
    (p_gl / "solutions" / "wrong_zero.cpp").write_text(
        '#include "problem.h"\n'
        "std::vector<int> encode(int) { return {0}; }\n"
        "int decode(const std::vector<int>&) { return 0; }\n",
        encoding="utf-8",
    )
    (p_gl / "statements" / "README.md").write_text(
        "# GL\n\n## 样例\n\n```text\n7\n```\n", encoding="utf-8"
    )
    (p_gl / "statements" / "tutorial.md").write_text(
        "# Tutorial\n\n## 思路\n\nEncode 8 bits from LSB to MSB and reconstruct the integer in decode.\n",
        encoding="utf-8",
    )
    manifest = load_manifest(str(p_gl))
    assert manifest is not None
    from autocode_mcp.workflow.models import SolutionEntry

    manifest.solutions.append(
        SolutionEntry(name="wrong_zero", path="solutions/wrong_zero.cpp", role="wrong", expected="fail")
    )
    save_manifest(str(p_gl), manifest)
    (p_gl / "tests").mkdir(exist_ok=True)
    for idx, val in enumerate((1, 7, 42, 255), start=1):
        (p_gl / "tests" / f"{idx:02d}.in").write_text(f"{val}\n", encoding="utf-8")

    res_gl = await ProblemAuditTool().execute(
        problem_dir=str(p_gl),
        mode="full",
        standalone=True,
        enable_mutation_testing=False,
    )
    assert res_gl.success, res_gl.error
    assert res_gl.data["decision"] == "go", res_gl.data["blocking_issues"]
    assert res_gl.data["quality_signals"]["answer_consistency"]["passed"] is True
    assert res_gl.data["quality_signals"]["wrong_solution_kill"]["passed"] is True


@pytest.mark.asyncio
async def test_standard_cn_tpj_discovery_and_export_limit_fallback(tmp_path):
    # 1. standard_cn interactive with tpj.cc (registerInteraction), data/test/*.in, data/validator.cpp
    p_int = tmp_path / "cn_interactive"
    (p_int / "solution").mkdir(parents=True)
    (p_int / "statement").mkdir(parents=True)
    (p_int / "data" / "test").mkdir(parents=True)
    (p_int / "tpj.cc").write_text(
        '#include <iostream>\n'
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "    registerInteraction(argc, argv);\n"
        "    int target = inf.readInt();\n"
        "    std::cout << target << std::endl;\n"
        "    int guess = ouf.readInt();\n"
        '    if (guess == target) quitf(_ok, "ok");\n'
        '    quitf(_wa, "bad");\n'
        "}\n",
        encoding="utf-8",
    )
    (p_int / "data" / "validator.cpp").write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "    registerValidation(argc, argv);\n"
        '    inf.readInt(1, 100, "x");\n'
        "    inf.readEoln();\n"
        "    inf.readEof();\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (p_int / "solution" / "std.cpp").write_text(
        "#include <iostream>\nint main(){int x; if(std::cin>>x) std::cout<<x<<std::endl; return 0;}\n",
        encoding="utf-8",
    )
    (p_int / "statement" / "zh-cn.md").write_text(
        "# Guess\n\n`1 <= x <= 100`\n\n## 样例\n\n```text\n5\n```\n", encoding="utf-8"
    )
    (p_int / "solution" / "tutorial.md").write_text(
        "# Tutorial\n\nEcho back the integer sent by the interactor in one round.\n", encoding="utf-8"
    )
    (p_int / "data" / "test" / "test1.in").write_text("5\n", encoding="utf-8")
    (p_int / "data" / "test" / "test2.in").write_text("42\n", encoding="utf-8")

    res_int = await ProblemAuditTool().execute(
        problem_dir=str(p_int),
        mode="full",
        standalone=True,
        enable_mutation_testing=False,
    )
    assert res_int.success, res_int.error
    assert res_int.data["detected_layout"] == "standard_cn"
    assert res_int.data["structural_signals"]["interactor_source"]["passed"] is True
    assert res_int.data["quality_signals"]["file_count"]["passed"] is True
    assert res_int.data["decision"] == "go", res_int.data["blocking_issues"]
    assert res_int.data["quality_signals"]["validator_check"]["passed"] is True

    # 2. standard_cn SPJ with tpj.cc, brute_force.cpp, solution/wrong/*.cpp, and 1000ms export limit fallback
    p_spj = tmp_path / "cn_spj_1000ms"
    (p_spj / "solution" / "wrong").mkdir(parents=True)
    (p_spj / "statement").mkdir(parents=True)
    (p_spj / "data" / "test").mkdir(parents=True)
    (p_spj / "exports" / "qoj").mkdir(parents=True)
    (p_spj / "exports" / "hydro" / "testdata").mkdir(parents=True)

    (p_spj / "tpj.cc").write_text(
        '#include "testlib.h"\n'
        "int main(int argc, char* argv[]) {\n"
        "    registerTestlibCmd(argc, argv);\n"
        "    int j = ans.readInt(); int p = ouf.readInt();\n"
        '    if (j == p) quitf(_ok, "ok");\n'
        '    quitf(_wa, "mismatch");\n'
        "}\n",
        encoding="utf-8",
    )
    (p_spj / "solution" / "std.cpp").write_text(
        "#include <iostream>\nint main(){int a,b; std::cin>>a>>b; std::cout<<(a+b)<<'\\n'; return 0;}\n",
        encoding="utf-8",
    )
    (p_spj / "solution" / "brute_force.cpp").write_text(
        "#include <iostream>\nint main(){int a,b; std::cin>>a>>b; std::cout<<(a+b)<<'\\n'; return 0;}\n",
        encoding="utf-8",
    )
    (p_spj / "solution" / "wrong" / "wa.cpp").write_text(
        "#include <iostream>\nint main(){int a,b; std::cin>>a>>b; std::cout<<(a-b)<<'\\n'; return 0;}\n",
        encoding="utf-8",
    )
    (p_spj / "statement" / "zh-cn.md").write_text(
        "# Sum\n\n## 样例\n\n```text\n1 2\n```\n", encoding="utf-8"
    )
    (p_spj / "solution" / "tutorial.md").write_text(
        "# Tutorial\n\nCompute a + b directly in O(1) time.\n", encoding="utf-8"
    )
    (p_spj / "data" / "test" / "test1.in").write_text("2 5\n", encoding="utf-8")
    (p_spj / "data" / "test" / "test1.out").write_text("7\n", encoding="utf-8")
    (p_spj / "exports" / "qoj" / "data").mkdir(parents=True)
    (p_spj / "exports" / "qoj" / "data" / "1.in").write_text("2 5\n", encoding="utf-8")
    (p_spj / "exports" / "qoj" / "data" / "1.ans").write_text("7\n", encoding="utf-8")
    (p_spj / "exports" / "qoj" / "problem.conf").write_text(
        "n_tests 1\ntime_limit_ms 1000\nmemory_limit 256\n", encoding="utf-8"
    )
    (p_spj / "exports" / "hydro" / "problem.yaml").write_text("title: Sum\n", encoding="utf-8")
    (p_spj / "exports" / "hydro" / "testdata" / "01.in").write_text("2 5\n", encoding="utf-8")
    (p_spj / "exports" / "hydro" / "testdata" / "01.ans").write_text("7\n", encoding="utf-8")
    (p_spj / "exports" / "hydro" / "testdata" / "config.yaml").write_text(
        "time: 1000ms\nmemory: 256m\nsubtasks:\n  - score: 100\n    cases:\n      - input: 01.in\n        output: 01.ans\n",
        encoding="utf-8",
    )

    res_spj = await ProblemAuditTool().execute(
        problem_dir=str(p_spj),
        mode="full",
        standalone=True,
        enable_mutation_testing=False,
    )
    assert res_spj.success, res_spj.error
    assert res_spj.data["structural_signals"]["checker_source"]["passed"] is True
    assert res_spj.data["decision"] == "go", res_spj.data["blocking_issues"]
    assert res_spj.data["quality_signals"]["wrong_solution_kill"]["passed"] is True
    assert res_spj.data["quality_signals"]["exported_packages"]["passed"] is True


@pytest.mark.asyncio
async def test_complexity_lie_and_tle_audit():
    quad_code = (
        "// Time Complexity: O(n log n)\n#include <iostream>\n"
        "int main(){int n;std::cin>>n;long long s=0;"
        "for(int i=0;i<n;++i)for(int j=0;j<n;++j)s+=i^j;"
        'std::cout<<s<<"\\n";return 0;}\n'
    )
    an_res = await SolutionAnalyzeTool().execute(
        code=quad_code,
        constraints={"n_max": 100000, "time_limit_ms": 1000},
        claimed_complexity="O(n log n)",
    )
    assert an_res.success
    assert an_res.data["static_estimated_complexity"] == ComplexityLevel.QUADRATIC
    assert an_res.data["complexity_conflict"] is True
    assert an_res.data["complexity_match"] is False

    au_infeasible = await SolutionAuditStdTool().execute(
        code=quad_code,
        constraints={"n_max": 100000, "time_limit_ms": 1000},
        claimed_complexity="O(n log n)",
    )
    assert au_infeasible.success
    assert au_infeasible.data["passed"] is False
    assert au_infeasible.data["complexity_feasible"] is False
    assert au_infeasible.data["complexity_match"] is False
    assert any(
        f.get("severity") == "error" and f.get("type") == "high_tle_risk"
        for f in au_infeasible.data["findings"]
    )

    au_feasible = await SolutionAuditStdTool().execute(
        code=quad_code,
        constraints={"n_max": 1000, "time_limit_ms": 1000},
        claimed_complexity="O(n log n)",
    )
    assert au_feasible.success
    assert au_feasible.data["passed"] is True
    assert au_feasible.data["complexity_feasible"] is True
    assert au_feasible.data["complexity_match"] is False
    assert any(f.get("type") == "complexity_mismatch" for f in au_feasible.data["findings"])
