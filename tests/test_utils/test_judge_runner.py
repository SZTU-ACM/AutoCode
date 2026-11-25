"""Unified JudgeRunner (StandardRunner, CheckerRunner, InteractiveRunner) tests."""

from __future__ import annotations

import os
import shutil

import pytest

from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.judge_runner import (
    CheckerRunner,
    InteractiveRunner,
    StandardRunner,
    parse_judge_metrics,
    verdict_from_testlib_rc,
)
from autocode_mcp.utils.platform import get_exe_extension

pytestmark = pytest.mark.skipif(not shutil.which("g++"), reason="g++ not available")


def test_verdict_from_testlib_rc_and_metrics():
    assert verdict_from_testlib_rc(0) == "AC"
    assert verdict_from_testlib_rc(1) == "WA"
    assert verdict_from_testlib_rc(2) == "PE"
    assert verdict_from_testlib_rc(3) == "FAIL"
    assert verdict_from_testlib_rc(4) == "PE"
    assert verdict_from_testlib_rc(7) == "PC"
    assert verdict_from_testlib_rc(8) == "PE"
    assert verdict_from_testlib_rc(-9) == "FAIL"
    assert verdict_from_testlib_rc(None) == "FAIL"

    score, metrics = parse_judge_metrics(
        "points 0.75 ok queries=12 bits_used=48 bytes_used=6 rounds=2"
    )
    assert score == pytest.approx(0.75)
    assert metrics == {"queries": 12, "bits_used": 48, "bytes_used": 6, "rounds": 2}


async def _build(tmp_path, name: str, code: str) -> str:
    src = tmp_path / f"{name}.cpp"
    exe = tmp_path / f"{name}{get_exe_extension()}"
    src.write_text(code, encoding="utf-8")
    res = await compile_cpp(str(src), str(exe))
    assert res.success, f"Compile {name} failed: {res.stderr}"
    return str(exe)


@pytest.mark.asyncio
async def test_standard_runner_verdicts(tmp_path):
    add_exe = await _build(
        tmp_path,
        "add",
        """
#include <iostream>
int main() {
    long long a, b;
    if (!(std::cin >> a >> b)) return 0;
    std::cout << (a + b) << "\\n";
    return 0;
}
""",
    )
    re_exe = await _build(
        tmp_path,
        "re",
        """
int main() { return 42; }
""",
    )
    tle_exe = await _build(
        tmp_path,
        "tle",
        """
int main() {
    volatile unsigned long long x = 0;
    while (true) { x++; }
}
""",
    )

    ac = await StandardRunner.run(add_exe, "3 4\n", expected_output="7\n")
    assert ac.verdict == "AC"
    assert ac.accepted

    wa = await StandardRunner.run(add_exe, "3 4\n", expected_output="8\n")
    assert wa.verdict == "WA"
    assert not wa.accepted

    re_res = await StandardRunner.run(re_exe, "")
    assert re_res.verdict == "RE"
    assert re_res.solution_returncode == 42

    tle_res = await StandardRunner.run(tle_exe, "", time_limit_ms=250.0)
    assert tle_res.verdict == "TLE"

    missing = await StandardRunner.run(os.path.join(str(tmp_path), "nope"), "")
    assert missing.verdict == "FAIL"


@pytest.mark.asyncio
async def test_checker_runner_ac_wa_pe_pc(tmp_path):
    checker_exe = await _build(
        tmp_path,
        "chk",
        """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    int n = inf.readInt();
    int out = ouf.readInt();
    if (!ouf.seekEof()) quitf(_dirt, "extra tokens");
    if (out == n) quitf(_ok, "exact queries=1");
    if (out == n - 1) quitp(0.5, "close score=0.5");
    quitf(_wa, "expected %d got %d", n, out);
}
""",
    )
    sol_ok = await _build(
        tmp_path,
        "sol_ok",
        '#include <iostream>\nint main(){int n; std::cin>>n; std::cout<<n<<"\\n";}',
    )
    sol_pc = await _build(
        tmp_path,
        "sol_pc",
        '#include <iostream>\nint main(){int n; std::cin>>n; std::cout<<(n-1)<<"\\n";}',
    )
    sol_wa = await _build(
        tmp_path,
        "sol_wa",
        '#include <iostream>\nint main(){int n; std::cin>>n; std::cout<<(n+5)<<"\\n";}',
    )
    sol_dirt = await _build(
        tmp_path,
        "sol_dirt",
        '#include <iostream>\nint main(){int n; std::cin>>n; std::cout<<n<<" 999\\n";}',
    )

    r_ac = await CheckerRunner.run(checker_exe, sol_ok, "10\n", "10\n", cwd=str(tmp_path))
    assert r_ac.verdict == "AC"
    assert r_ac.metrics.get("queries") == 1

    r_pc = await CheckerRunner.run(checker_exe, sol_pc, "10\n", "10\n", cwd=str(tmp_path))
    assert r_pc.verdict == "PC"
    assert r_pc.score == pytest.approx(0.5)

    r_wa = await CheckerRunner.run(checker_exe, sol_wa, "10\n", "10\n", cwd=str(tmp_path))
    assert r_wa.verdict == "WA"

    r_pe = await CheckerRunner.run(checker_exe, sol_dirt, "10\n", "10\n", cwd=str(tmp_path))
    assert r_pe.verdict == "PE"


GUESS_INTERACTOR = """
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int n = inf.readInt();
    int secret = inf.readInt();
    int max_q = inf.readInt();

    std::cout << n << std::endl;
    std::cout.flush();

    int queries = 0;
    while (true) {
        std::string op = ouf.readToken();
        if (op == "?") {
            int x = ouf.readInt(1, n);
            queries++;
            if (queries > max_q) {
                quitf(_wa, "too many queries: %d > %d", queries, max_q);
            }
            int resp = (x < secret ? -1 : (x > secret ? 1 : 0));
            std::cout << resp << std::endl;
            std::cout.flush();
        } else if (op == "!") {
            int ans = ouf.readInt(1, n);
            if (!ouf.seekEof()) {
                quitf(_dirt, "extra trailing output after final answer");
            }
            tout << ans << "\\n";
            if (ans == secret) {
                quitf(_ok, "guessed %d in queries=%d", secret, queries);
            } else if (std::abs(ans - secret) == 1) {
                quitp(0.5, "off by one queries=%d", queries);
            } else {
                quitf(_wa, "wrong answer %d != %d", ans, secret);
            }
        } else {
            quitf(_pe, "unknown command: %s", op.c_str());
        }
    }
}
"""


@pytest.mark.asyncio
async def test_interactive_runner_ac_wa_pe_pc_deadlock_tle_re(tmp_path):
    interactor_exe = await _build(tmp_path, "interactor", GUESS_INTERACTOR)

    sol_bsearch = await _build(
        tmp_path,
        "sol_bsearch",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    int l = 1, r = n;
    while (l < r) {
        int mid = l + (r - l) / 2;
        std::cout << "? " << mid << std::endl;
        int resp;
        if (!(std::cin >> resp)) return 0;
        if (resp == 0) { l = r = mid; break; }
        if (resp < 0) l = mid + 1;
        else r = mid - 1;
    }
    std::cout << "! " << l << std::endl;
    return 0;
}
""",
    )
    sol_wa = await _build(
        tmp_path,
        "sol_wa",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    std::cout << "! 1" << std::endl;
    return 0;
}
""",
    )
    sol_pc = await _build(
        tmp_path,
        "sol_pc",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    std::cout << "! 72" << std::endl;
    return 0;
}
""",
    )
    sol_dirt = await _build(
        tmp_path,
        "sol_dirt",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    std::cout << "! 73 extra\\n" << std::flush;
    return 0;
}
""",
    )
    sol_eof = await _build(
        tmp_path,
        "sol_eof",
        """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    return 0;
}
""",
    )
    sol_no_flush = await _build(
        tmp_path,
        "sol_no_flush",
        """
#include <cstdio>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    // Deliberately buffered without fflush, then block on reading response -> deadlock!
    printf("? 50\\n");
    int resp;
    if (scanf("%d", &resp) != 1) return 0;
    printf("! 50\\n");
    return 0;
}
""",
    )
    sol_tle = await _build(
        tmp_path,
        "sol_tle",
        """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    volatile unsigned long long x = 0;
    while (true) { x++; }
}
""",
    )
    sol_re = await _build(
        tmp_path,
        "sol_re",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    std::cout << "! 73" << std::endl;
    return 7;
}
""",
    )

    # AC
    res_ac = await InteractiveRunner.run(
        interactor_exe, sol_bsearch, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res_ac.verdict == "AC", res_ac.detail
    assert res_ac.accepted
    assert res_ac.metrics.get("queries", 0) >= 1
    assert res_ac.metrics["queries"] <= 10
    assert res_ac.judge_stdout.strip() == "73"
    assert "[judge] 100" in res_ac.transcript
    assert "[contestant] ! 73" in res_ac.transcript
    assert res_ac.cpu_time_ms is not None

    # WA
    res_wa = await InteractiveRunner.run(
        interactor_exe, sol_wa, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res_wa.verdict == "WA"

    # PC
    res_pc = await InteractiveRunner.run(
        interactor_exe, sol_pc, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res_pc.verdict == "PC"
    assert res_pc.score == pytest.approx(0.5)

    # PE (extra output -> _dirt)
    res_dirt = await InteractiveRunner.run(
        interactor_exe, sol_dirt, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res_dirt.verdict == "PE"

    # PE (premature EOF -> _unexpected_eof)
    res_eof = await InteractiveRunner.run(
        interactor_exe, sol_eof, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res_eof.verdict == "PE"

    # DEADLOCK (missing flush)
    res_dl = await InteractiveRunner.run(
        interactor_exe,
        sol_no_flush,
        "100 73 10\n",
        time_limit_ms=2000.0,
        deadlock_timeout_ms=400.0,
        cwd=str(tmp_path),
    )
    assert res_dl.verdict == "DEADLOCK"
    assert "deadlock" in res_dl.detail.lower()

    # Solution CPU TLE (must NOT be misclassified as DEADLOCK)
    res_tle = await InteractiveRunner.run(
        interactor_exe,
        sol_tle,
        "100 73 10\n",
        time_limit_ms=300.0,
        deadlock_timeout_ms=1000.0,
        cwd=str(tmp_path),
    )
    assert res_tle.verdict == "TLE"

    # Solution RE (non-zero exit code even when interactor reports AC)
    res_re = await InteractiveRunner.run(
        interactor_exe, sol_re, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res_re.verdict == "RE"
    assert res_re.solution_returncode == 7


@pytest.mark.asyncio
async def test_interactive_runner_diagnoses_tout_only_interactor(tmp_path):
    """When an interactor writes prompts to tout instead of std::cout, deadlock hint calls it out."""
    broken_int = await _build(
        tmp_path,
        "broken_tout_int",
        """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int n = inf.readInt();
    tout << n << std::endl;
    tout.flush();
    int ans = ouf.readInt();
    if (ans == n) quitf(_ok, "ok");
    quitf(_wa, "wa");
}
""",
    )
    sol = await _build(
        tmp_path,
        "echo_sol",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    std::cout << n << std::endl;
    return 0;
}
""",
    )
    res = await InteractiveRunner.run(
        broken_int,
        sol,
        "42\n",
        time_limit_ms=2000.0,
        deadlock_timeout_ms=400.0,
        cwd=str(tmp_path),
    )
    assert res.verdict == "DEADLOCK"
    assert "tout" in res.detail
    assert "std::cout" in res.detail


@pytest.mark.asyncio
async def test_interactive_runner_compute_then_deadlock(tmp_path):
    """Solution that burns >15ms CPU then forgets to flush must still trigger DEADLOCK."""
    interactor_exe = await _build(tmp_path, "int_dl2", GUESS_INTERACTOR)
    sol_compute_dl = await _build(
        tmp_path,
        "sol_compute_dl",
        """
#include <cstdio>
#include <chrono>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    auto start = std::chrono::steady_clock::now();
    volatile unsigned long long acc = 0;
    while (std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now() - start).count() < 40) {
        acc += 1;
    }
    // Deliberately buffered without fflush after CPU work -> deadlock!
    printf("? 50\\n");
    int resp;
    if (scanf("%d", &resp) != 1) return 0;
    printf("! 50\\n");
    return 0;
}
""",
    )
    res = await InteractiveRunner.run(
        interactor_exe,
        sol_compute_dl,
        "100 73 10\n",
        time_limit_ms=2000.0,
        deadlock_timeout_ms=350.0,
        cwd=str(tmp_path),
    )
    assert res.verdict == "DEADLOCK", f"Expected DEADLOCK, got {res.verdict}: {res.detail}"


@pytest.mark.asyncio
async def test_interactive_runner_slow_interactor_not_false_deadlock(tmp_path):
    """An interactor doing CPU work longer than deadlock_timeout_ms must NOT trigger DEADLOCK."""
    slow_interactor = await _build(
        tmp_path,
        "slow_int",
        """
#include <chrono>
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int n = inf.readInt();
    // Burn CPU for 750ms before sending initial prompt (exceeds 600ms deadlock threshold)
    auto start = std::chrono::steady_clock::now();
    volatile unsigned long long acc = 0;
    while (std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now() - start).count() < 750) {
        acc += 1;
    }
    std::cout << n << std::endl;
    std::cout.flush();
    int ans = ouf.readInt();
    if (ans == n * 2) quitf(_ok, "ok");
    quitf(_wa, "wa");
}
""",
    )
    sol = await _build(
        tmp_path,
        "sol_double",
        """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    std::cout << (n * 2) << std::endl;
    return 0;
}
""",
    )
    res = await InteractiveRunner.run(
        slow_interactor,
        sol,
        "21\n",
        time_limit_ms=3000.0,
        deadlock_timeout_ms=600.0,
        cwd=str(tmp_path),
    )
    assert res.verdict == "AC", f"Expected AC, got {res.verdict}: {res.detail}"


@pytest.mark.asyncio
async def test_interactive_runner_crash_mid_interaction_is_re(tmp_path):
    """Solution crashing mid-interaction (SIGABRT/SIGSEGV) must be reported as RE, not PE."""
    interactor_exe = await _build(tmp_path, "int_crash", GUESS_INTERACTOR)
    sol_abort = await _build(
        tmp_path,
        "sol_abort",
        """
#include <cstdlib>
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::abort();
}
""",
    )
    res = await InteractiveRunner.run(
        interactor_exe, sol_abort, "100 73 10\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert res.verdict == "RE", f"Expected RE, got {res.verdict}: {res.detail}"


@pytest.mark.asyncio
async def test_runners_and_monitor_support_relative_cwd(tmp_path, monkeypatch):
    """Relative problem_dir / cwd / executable paths must work across all runners."""
    from autocode_mcp.utils.execution_monitor import DynamicExecutionMonitor

    sub = tmp_path / "rel_prob"
    sub.mkdir()
    sol_exe = await _build(
        sub,
        "sol",
        '#include <iostream>\nint main(){int n; std::cin>>n; std::cout<<(n*2)<<"\\n";}',
    )
    chk_exe = await _build(
        sub,
        "chk",
        """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    int n = inf.readInt();
    int out = ouf.readInt();
    if (out == n * 2) quitf(_ok, "ok");
    quitf(_wa, "wa");
}
""",
    )
    int_exe = await _build(
        sub,
        "int",
        """
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int n = inf.readInt();
    std::cout << n << std::endl;
    std::cout.flush();
    int ans = ouf.readInt();
    if (ans == n * 2) quitf(_ok, "ok");
    quitf(_wa, "wa");
}
""",
    )
    in_file = sub / "case.in"
    in_file.write_text("15\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    rel_dir = "rel_prob"
    rel_sol = os.path.relpath(sol_exe, tmp_path)
    rel_chk = os.path.relpath(chk_exe, tmp_path)
    rel_int = os.path.relpath(int_exe, tmp_path)
    rel_in = os.path.relpath(str(in_file), tmp_path)

    r_std = await StandardRunner.run(rel_sol, "15\n", expected_output="30\n", cwd=rel_dir)
    assert r_std.verdict == "AC", r_std.detail

    r_chk = await CheckerRunner.run(rel_chk, rel_sol, "15\n", "30\n", cwd=rel_dir)
    assert r_chk.verdict == "AC", r_chk.detail

    r_int = await InteractiveRunner.run(rel_int, rel_sol, "15\n", cwd=rel_dir)
    assert r_int.verdict == "AC", r_int.detail

    r_mon = await DynamicExecutionMonitor.run_interactive_pipeline(
        [rel_int], [rel_sol], rel_in, time_limit_ms=2000.0, cwd=rel_dir
    )
    assert r_mon["status"] == "ok", r_mon


@pytest.mark.asyncio
async def test_interactive_runner_cancellation_and_mle(tmp_path):
    """Verify CancelledError propagates cleanly with no leaked tasks, and MLE is detected."""
    import asyncio

    interactor_exe = await _build(tmp_path, "int_cancel", GUESS_INTERACTOR)
    sol_spin = await _build(
        tmp_path,
        "sol_spin",
        """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    volatile unsigned long long x = 0;
    while (true) { x++; }
}
""",
    )
    sol_mle = await _build(
        tmp_path,
        "sol_mle",
        """
#include <chrono>
#include <iostream>
#include <thread>
#include <vector>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    const size_t N = 64ULL * 1024 * 1024;
    std::vector<unsigned char> buf(N);
    volatile unsigned char* p = buf.data();
    for (size_t i = 0; i < N; i += 4096) {
        p[i] = static_cast<unsigned char>((i + n) & 0xFF);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    std::cout << "! " << (static_cast<int>(p[0]) % n + 1) << std::endl;
    return 0;
}
""",
    )

    # Cancellation
    task = asyncio.create_task(
        InteractiveRunner.run(
            interactor_exe, sol_spin, "100 50 10\n", time_limit_ms=5000.0, cwd=str(tmp_path)
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # MLE
    res_mle = await InteractiveRunner.run(
        interactor_exe,
        sol_mle,
        "100 50 10\n",
        time_limit_ms=2000.0,
        memory_limit_mb=24.0,
        cwd=str(tmp_path),
    )
    assert res_mle.verdict == "MLE", f"Expected MLE, got {res_mle.verdict}: {res_mle.detail}"

