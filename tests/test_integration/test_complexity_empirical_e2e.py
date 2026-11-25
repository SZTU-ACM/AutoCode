from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from autocode_mcp.tools.complexity import SolutionAnalyzeTool
from autocode_mcp.utils.compiler import get_exe_extension
from autocode_mcp.utils.execution_monitor import DynamicExecutionMonitor

GEN_CPP = """
#include <iostream>
#include <cstdlib>

int main(int argc, char* argv[]) {
    int n = (argc >= 5) ? std::atoi(argv[4]) : 1000;
    std::cout << n << "\\n";
    for (int i = 0; i < n; ++i) {
        std::cout << (i % 1000 + 1) << (i + 1 == n ? "\\n" : " ");
    }
    return 0;
}
"""

LINEAR_SOL_CPP = """
#include <iostream>
#include <vector>

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(NULL);
    int n;
    if (!(std::cin >> n)) return 0;
    long long sum = 0;
    for (int i = 0; i < n; ++i) {
        int x;
        std::cin >> x;
        sum += x;
    }
    std::cout << sum << "\\n";
    return 0;
}
"""

QUADRATIC_SOL_CPP = """
#include <iostream>
#include <vector>

int main() {
    std::ios_base::sync_with_stdio(false);
    std::cin.tie(NULL);
    int n;
    if (!(std::cin >> n)) return 0;
    std::vector<int> a(n);
    for (int i = 0; i < n; ++i) {
        std::cin >> a[i];
    }
    volatile long long cnt = 1;
    for (int i = 0; i < n; ++i) {
        for (int j = 0; j < n; ++j) {
            cnt = (cnt * 1315423911LL + (a[i] ^ a[j]) + j) & 0x7fffffffLL;
        }
    }
    std::cout << cnt << "\\n";
    return 0;
}
"""

INTERACTOR_CPP = """
#include <iostream>

int main() {
    int target = 42;
    std::cout << "READY\\n" << std::flush;
    int guess;
    while (std::cin >> guess) {
        if (guess < target) {
            std::cout << "LOW\\n" << std::flush;
        } else if (guess > target) {
            std::cout << "HIGH\\n" << std::flush;
        } else {
            std::cout << "CORRECT\\n" << std::flush;
            break;
        }
    }
    return 0;
}
"""

INTERACTIVE_SOL_CPP = """
#include <iostream>
#include <string>

int main() {
    std::string s;
    if (!(std::cin >> s)) return 0;
    int l = 1, r = 100;
    while (l <= r) {
        int mid = (l + r) / 2;
        std::cout << mid << "\\n" << std::flush;
        std::string resp;
        if (!(std::cin >> resp)) break;
        if (resp == "CORRECT") break;
        if (resp == "LOW") l = mid + 1;
        else r = mid - 1;
    }
    return 0;
}
"""


def _compile_cpp(source_code: str, output_path: str) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    src_file = os.path.splitext(output_path)[0] + ".cpp"
    with open(src_file, "w", encoding="utf-8") as f:
        f.write(source_code)
    cmd = ["g++", "-O2", "-std=c++17", src_file, "-o", output_path]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


@pytest.mark.asyncio
async def test_linear_solution_verified_e2e():
    tool = SolutionAnalyzeTool()
    exe_ext = get_exe_extension()

    with tempfile.TemporaryDirectory() as tmpdir:
        gen_bin = os.path.join(tmpdir, "files", f"gen{exe_ext}")
        sol_bin = os.path.join(tmpdir, "solutions", f"sol{exe_ext}")
        _compile_cpp(GEN_CPP, gen_bin)
        _compile_cpp(LINEAR_SOL_CPP, sol_bin)

        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            claimed_complexity="O(n)",
            constraints={"n_max": 100000, "time_limit_ms": 2000.0},
        )

        assert result.success
        empirical = result.data.get("empirical_verification", {})
        assert empirical.get("passed") is True
        assert empirical.get("verdict") in (
            "verified",
            "verified_with_cache_jump",
            "verified_within_noise_floor",
        )
        assert len(empirical.get("samples", [])) == 5
        assert empirical.get("fitted_alpha") is not None
        assert -0.25 <= empirical.get("fitted_alpha") <= 1.40


@pytest.mark.asyncio
async def test_small_constant_quadratic_solution_blocked_e2e():
    tool = SolutionAnalyzeTool()
    exe_ext = get_exe_extension()

    with tempfile.TemporaryDirectory() as tmpdir:
        gen_bin = os.path.join(tmpdir, "files", f"gen{exe_ext}")
        sol_bin = os.path.join(tmpdir, "solutions", f"sol{exe_ext}")
        _compile_cpp(GEN_CPP, gen_bin)
        _compile_cpp(QUADRATIC_SOL_CPP, sol_bin)

        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            claimed_complexity="O(n)",
            constraints={"n_max": 10000, "time_limit_ms": 2000.0},
        )

        assert result.success
        empirical = result.data.get("empirical_verification", {})
        assert empirical.get("passed") is False, f"unexpected empirical pass: {empirical}"
        assert empirical.get("verdict") == "ratio_mismatch"
        assert empirical.get("fitted_complexity") in (
            "O(n^2)",
            "O(n sqrt n)",
            "O(n log n)",
            "O(n^3)",
            "O(nlogn)",
            "O(nsqrtn)",
        )
        assert "failure_reason" in empirical


@pytest.mark.asyncio
async def test_interactive_pipeline_e2e():
    exe_ext = get_exe_extension()

    with tempfile.TemporaryDirectory() as tmpdir:
        int_bin = os.path.join(tmpdir, "files", f"interactor{exe_ext}")
        sol_bin = os.path.join(tmpdir, "solutions", f"sol{exe_ext}")
        _compile_cpp(INTERACTOR_CPP, int_bin)
        _compile_cpp(INTERACTIVE_SOL_CPP, sol_bin)

        dummy_in = os.path.join(tmpdir, "dummy.in")
        with open(dummy_in, "w", encoding="utf-8") as f:
            f.write("\n")

        res = await DynamicExecutionMonitor.run_interactive_pipeline(
            [int_bin],
            [sol_bin],
            dummy_in,
            time_limit_ms=2000.0,
            cwd=tmpdir,
        )

        assert res.get("status") == "ok"
        assert res.get("solution_returncode") == 0
        assert res.get("interactor_returncode") == 0
        assert res.get("cpu_time_ms") is not None
        assert res.get("cpu_time_ms") > 0.0


@pytest.mark.asyncio
async def test_generator_missing_fallback_e2e():
    tool = SolutionAnalyzeTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        sol_dir = os.path.join(tmpdir, "solutions")
        os.makedirs(sol_dir, exist_ok=True)
        with open(os.path.join(sol_dir, "sol.cpp"), "w", encoding="utf-8") as f:
            f.write(LINEAR_SOL_CPP)

        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            claimed_complexity="O(n)",
            constraints={"n_max": 10000, "time_limit_ms": 2000.0},
        )

        assert result.success
        empirical = result.data.get("empirical_verification", {})
        assert empirical.get("status") == "pending_generator"


@pytest.mark.asyncio
async def test_solution_without_claimed_complexity_skips_verification():
    tool = SolutionAnalyzeTool()
    exe_ext = get_exe_extension()

    with tempfile.TemporaryDirectory() as tmpdir:
        gen_bin = os.path.join(tmpdir, "files", f"gen{exe_ext}")
        sol_bin = os.path.join(tmpdir, "solutions", f"sol{exe_ext}")
        _compile_cpp(GEN_CPP, gen_bin)
        _compile_cpp(QUADRATIC_SOL_CPP, sol_bin)

        result = await tool.execute(
            problem_dir=tmpdir,
            solution_type="sol",
            claimed_complexity=None,
            constraints={"n_max": 10000, "time_limit_ms": 2000.0},
        )

        assert result.success
        empirical = result.data.get("empirical_verification", {})
        assert empirical.get("status") == "skipped"
        assert empirical.get("passed") is True


@pytest.mark.asyncio
async def test_interactive_log_n_query_curve_empirical_e2e():
    """交互题 O(log n)（n <= 1e18）应使用查询次数随 log2(n) 曲线拟合，而非误判为 O(1)。"""
    from autocode_mcp.utils.compiler import compile_cpp
    from autocode_mcp.workflow import default_manifest, save_manifest

    gen_interactive_cpp = """
#include <iostream>
#include <cstdlib>
int main(int argc, char* argv[]) {
    long long n = (argc >= 5) ? std::atoll(argv[4]) : 1000LL;
    long long target = (n * 7) / 10;
    if (target < 1) target = 1;
    std::cout << n << " " << target << "\\n";
    return 0;
}
"""
    interactor_log_cpp = """
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    long long n = inf.readLong();
    long long target = inf.readLong();
    std::cout << n << std::endl;
    int queries = 0;
    while (true) {
        std::string cmd = ouf.readWord();
        if (cmd == "?") {
            long long x = ouf.readLong(1LL, n);
            ++queries;
            if (queries > 120) quitf(_wa, "too many queries=%d", queries);
            if (x < target) std::cout << "<" << std::endl;
            else if (x > target) std::cout << ">" << std::endl;
            else std::cout << "=" << std::endl;
        } else if (cmd == "!") {
            long long ans = ouf.readLong(1LL, n);
            if (ans == target) quitf(_ok, "OK queries=%d", queries);
            quitf(_wa, "wrong answer");
        } else {
            quitf(_pe, "bad cmd");
        }
    }
}
"""
    sol_bsearch_cpp = """
#include <iostream>
#include <string>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    long long lo = 1, hi = n;
    while (lo <= hi) {
        long long mid = lo + (hi - lo) / 2;
        std::cout << "? " << mid << std::endl;
        std::string resp;
        if (!(std::cin >> resp)) return 0;
        if (resp == "=") {
            std::cout << "! " << mid << std::endl;
            return 0;
        }
        if (resp == "<") lo = mid + 1;
        else hi = mid - 1;
    }
    std::cout << "! " << lo << std::endl;
    return 0;
}
"""
    exe_ext = get_exe_extension()
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = default_manifest(
            "Interactive LogN", problem_type="interactive", oracle_mode="interactor_only"
        )
        manifest.constraints = {"n_max": 10**18}
        assert manifest.protocol is not None
        manifest.protocol.max_queries = 80
        save_manifest(tmpdir, manifest)

        gen_src = os.path.join(tmpdir, "files", "gen.cpp")
        int_src = os.path.join(tmpdir, "files", "interactor.cpp")
        sol_src = os.path.join(tmpdir, "solutions", "sol.cpp")
        os.makedirs(os.path.dirname(gen_src), exist_ok=True)
        os.makedirs(os.path.dirname(sol_src), exist_ok=True)
        with open(gen_src, "w", encoding="utf-8") as f:
            f.write(gen_interactive_cpp)
        with open(int_src, "w", encoding="utf-8") as f:
            f.write(interactor_log_cpp)
        with open(sol_src, "w", encoding="utf-8") as f:
            f.write(sol_bsearch_cpp)

        assert (await compile_cpp(gen_src, os.path.join(tmpdir, "files", f"gen{exe_ext}"))).success
        assert (await compile_cpp(int_src, os.path.join(tmpdir, "files", f"interactor{exe_ext}"))).success
        assert (await compile_cpp(sol_src, os.path.join(tmpdir, "solutions", f"sol{exe_ext}"))).success

        result = await SolutionAnalyzeTool().execute(
            problem_dir=tmpdir,
            solution_type="sol",
            claimed_complexity="O(log n)",
        )
        assert result.success, result.error
        empirical = result.data["empirical_verification"]
        assert empirical["passed"] is True
        assert empirical["fit_metric"] == "queries"
        assert empirical["verdict"] == "verified_query_curve"
        assert empirical["fitted_complexity"] == "O(log n)"
        assert empirical["scale_mode"] == "log"
        assert empirical["query_curve"]["queries_per_log2_n"] > 0.3


@pytest.mark.asyncio
async def test_multi_param_manifest_empirical_complexity_template_e2e():
    """多参数题（主导变量 T 而非 n）通过 manifest.empirical_complexity 显式模板应正确拟合。"""
    from autocode_mcp.workflow import EmpiricalComplexityConfig, default_manifest, save_manifest

    gen_multi_cpp = """
#include <iostream>
#include <cstdlib>
int main(int argc, char* argv[]) {
    long long n = (argc >= 5) ? std::atoll(argv[4]) : 1000000000LL;
    int T = (argc >= 7) ? std::atoi(argv[6]) : 100;
    std::cout << n << " " << T << "\\n";
    return 0;
}
"""
    sol_multi_cpp = """
#include <iostream>
int main() {
    long long n;
    int T;
    if (!(std::cin >> n >> T)) return 0;
    long long acc = 0;
    for (int i = 1; i <= T; ++i) {
        acc ^= (n + i * 1315423911LL);
        for (int j = 0; j < 200; ++j) {
            acc = (acc * 1103515245LL + 12345LL + j) & 0x7fffffffLL;
        }
    }
    std::cout << acc << "\\n";
    return 0;
}
"""
    exe_ext = get_exe_extension()
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest = default_manifest("MultiParam Problem")
        manifest.constraints = {"n_max": 10**9, "T_max": 200000}
        manifest.empirical_complexity = EmpiricalComplexityConfig(
            dominant_var="T",
            scale_points=[10000, 30000, 60000, 120000, 200000],
            generator_args_template="{seed} 2 1000000000 1000000000 {T} {T}",
        )
        save_manifest(tmpdir, manifest)
        _compile_cpp(gen_multi_cpp, os.path.join(tmpdir, "files", f"gen{exe_ext}"))
        _compile_cpp(sol_multi_cpp, os.path.join(tmpdir, "solutions", f"sol{exe_ext}"))

        result = await SolutionAnalyzeTool().execute(
            problem_dir=tmpdir,
            solution_type="sol",
            claimed_complexity="O(T)",
        )
        assert result.success, result.error
        empirical = result.data["empirical_verification"]
        assert empirical["passed"] is True
        assert empirical["dominant_var"] == "T"
        assert empirical["claimed_complexity"] == "O(n)"
        assert [s["n"] for s in empirical["samples"]] == [10000, 30000, 60000, 120000, 200000]
