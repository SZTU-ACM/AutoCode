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
    long long cnt = 0;
    for (int i = 0; i < n; ++i) {
        for (int j = i + 1; j < n; ++j) {
            cnt += (a[i] ^ a[j]) + j;
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
    src_file = output_path + ".cpp"
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
        assert empirical.get("verdict") in ("verified", "verified_with_cache_jump")
        assert len(empirical.get("samples", [])) == 5
        assert empirical.get("fitted_alpha") is not None
        assert 0.50 <= empirical.get("fitted_alpha") <= 1.40


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
        assert empirical.get("passed") is False
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
