"""Unit and real C++ tests for CommunicationRunner (run_twice, grader_linked, multi_process)."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys

import pytest

from autocode_mcp.utils.communication_runner import (
    CommunicationRunner,
    enforce_protocol_budgets,
    measure_payload_bits_bytes,
)
from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.judge_runner import JudgeResult
from autocode_mcp.utils.platform import get_exe_extension

pytestmark = pytest.mark.skipif(not shutil.which("g++"), reason="g++ not available")


def test_measure_payload_and_budget_enforcement():
    assert measure_payload_bits_bytes("010110\n") == (6, 1)
    assert measure_payload_bits_bytes("hello") == (40, 5)

    res = JudgeResult(verdict="AC", metrics={"bits_used": 20, "bytes_used": 3, "rounds": 2})
    enforce_protocol_budgets(res, max_bits=20, max_bytes=3, max_rounds=2)
    assert res.verdict == "AC"

    enforce_protocol_budgets(res, max_bits=16)
    assert res.verdict == "WA"
    assert "bits_used=20 > 16" in res.detail


async def _build(tmp_path, name: str, code: str, *, extra_sources=None, include_dirs=None) -> str:
    src = tmp_path / f"{name}.cpp"
    exe = tmp_path / f"{name}{get_exe_extension()}"
    src.write_text(code, encoding="utf-8")
    res = await compile_cpp(
        str(src), str(exe), extra_sources=extra_sources, include_dirs=include_dirs
    )
    assert res.success, f"Compile {name} failed: {res.stderr}"
    return str(exe)


# 1-bit noisy channel Run-Twice communicator:
# Input: "<value> <flip_pos>" where 0 <= value < 16 (4-bit value) and flip_pos is 0-indexed bit to flip.
# Stage 1 validates binary string, flips bit flip_pos % len, writes noisy codeword to stdout.
# Stage 2 checks Pass 2 decoded value == original value.
NOISY_RUN_TWICE_COMMUNICATOR = """
#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    const char* s = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = s ? std::atoi(s) : 2;

    int val = inf.readInt(0, 15, "val");
    int flip_pos = inf.readInt(0, 1000, "flip_pos");

    if (stage == 1) {
        std::string msg = ouf.readToken();
        if (!ouf.seekEof()) quitf(_dirt, "extra output in pass 1");
        for (char c : msg) {
            if (c != '0' && c != '1') quitf(_pe, "non-binary char in pass 1");
        }
        int bits = static_cast<int>(msg.size());
        int bytes = (bits + 7) / 8;
        if (bits > 0) {
            int idx = flip_pos % bits;
            msg[idx] = (msg[idx] == '0' ? '1' : '0');
        }
        std::cout << msg << "\\n";
        quitf(_ok, "relayed bits_used=%d bytes_used=%d", bits, bytes);
    }

    int decoded = ouf.readInt(0, 15, "decoded");
    if (!ouf.seekEof()) quitf(_dirt, "extra output in pass 2");
    std::string noisy = ans.seekEof() ? "" : ans.readToken();
    int bits = static_cast<int>(noisy.size());
    int bytes = (bits + 7) / 8;
    if (decoded == val) {
        quitf(_ok, "OK bits_used=%d bytes_used=%d rounds=2", bits, bytes);
    }
    quitf(_wa, "expected %d got %d", val, decoded);
}
"""

# 3x repetition code (12 bits for 4-bit value) survives any 1-bit flip!
SOL_REP3_CPP = """
#include <iostream>
#include <string>
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int val, flip_pos;
        std::cin >> val >> flip_pos;
        std::string code;
        for (int b = 0; b < 4; ++b) {
            char bit = ((val >> b) & 1) ? '1' : '0';
            code.push_back(bit);
            code.push_back(bit);
            code.push_back(bit);
        }
        std::cout << code << "\\n";
    } else {
        std::string noisy;
        std::cin >> noisy;
        int val = 0;
        for (int b = 0; b < 4; ++b) {
            int ones = (noisy[3 * b] == '1') + (noisy[3 * b + 1] == '1') + (noisy[3 * b + 2] == '1');
            if (ones >= 2) val |= (1 << b);
        }
        std::cout << val << "\\n";
    }
    return 0;
}
"""

# Cheating solution trying both global variable AND relative file drop across passes!
SOL_CHEAT_CPP = """
#include <fstream>
#include <iostream>
#include <string>
static int global_secret = -1;
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int val, flip_pos;
        std::cin >> val >> flip_pos;
        global_secret = val;
        std::ofstream f("leak.txt");
        f << val << "\\n";
        std::cout << "0\\n";
    } else {
        std::string noisy;
        std::cin >> noisy;
        int leaked = global_secret;
        std::ifstream f("leak.txt");
        if (f >> leaked) {
            std::cout << leaked << "\\n";
        } else {
            std::cout << (leaked >= 0 ? leaked : 0) << "\\n";
        }
    }
    return 0;
}
"""


@pytest.mark.asyncio
async def test_run_twice_noisy_channel_isolation_and_budget(tmp_path, monkeypatch):
    comm_exe = await _build(tmp_path, "communicator", NOISY_RUN_TWICE_COMMUNICATOR)
    sol_ok = await _build(tmp_path, "sol_ok", SOL_REP3_CPP)
    sol_cheat = await _build(tmp_path, "sol_cheat", SOL_CHEAT_CPP)

    # 1. Valid 12-bit repetition code survives 1-bit flip -> AC
    res_ac = await CommunicationRunner.run_twice(
        comm_exe,
        sol_ok,
        "11 5\n",
        max_bits=12,
        cwd=str(tmp_path),
    )
    assert res_ac.verdict == "AC", res_ac.detail
    assert res_ac.metrics["bits_used"] == 12
    assert res_ac.metrics["bytes_used"] == 2
    assert res_ac.metrics["passes"] == 2
    assert len(res_ac.metrics["per_pass_cpu_ms"]) == 2
    assert "[pass_1:first]" in res_ac.transcript
    assert "[channel_1->2]" in res_ac.transcript
    assert "[pass_2:second]" in res_ac.transcript

    # 2. Same valid solution with tighter max_bits=8 -> killed by budget enforcement (WA)
    res_over = await CommunicationRunner.run_twice(
        comm_exe,
        sol_ok,
        "11 5\n",
        max_bits=8,
        cwd=str(tmp_path),
    )
    assert res_over.verdict == "WA"
    assert "budget" in res_over.detail.lower()
    assert res_over.metrics["bits_used"] == 12

    # 3. Anti-cheat: global memory + cwd file leak MUST fail due to 2-process + separate cwd isolation
    res_cheat = await CommunicationRunner.run_twice(
        comm_exe,
        sol_cheat,
        "13 0\n",
        cwd=str(tmp_path),
    )
    assert res_cheat.verdict == "WA"
    assert res_cheat.metrics.get("cwd_files_written", 0) >= 1

    # 4. Dual-binary (Alice + Bob) + relative cwd
    alice_exe = await _build(
        tmp_path,
        "alice",
        """
#include <iostream>
#include <string>
int main() {
    std::string h; int val, fp;
    std::cin >> h >> val >> fp;
    std::string s;
    for (int b = 0; b < 4; ++b) {
        char c = ((val >> b) & 1) ? '1' : '0';
        s += c; s += c; s += c;
    }
    std::cout << s << "\\n";
}
""",
    )
    bob_exe = await _build(
        tmp_path,
        "bob",
        """
#include <iostream>
#include <string>
int main() {
    std::string h, noisy;
    std::cin >> h >> noisy;
    int val = 0;
    for (int b = 0; b < 4; ++b) {
        int ones = (noisy[3 * b] == '1') + (noisy[3 * b + 1] == '1') + (noisy[3 * b + 2] == '1');
        if (ones >= 2) val |= (1 << b);
    }
    std::cout << val << "\\n";
}
""",
    )
    sub = tmp_path / "rel"
    sub.mkdir()
    monkeypatch.chdir(tmp_path)
    res_dual = await CommunicationRunner.run_twice(
        os.path.relpath(comm_exe, tmp_path),
        [os.path.relpath(alice_exe, tmp_path), os.path.relpath(bob_exe, tmp_path)],
        "9 2\n",
        max_bits=12,
        cwd="rel",
    )
    assert res_dual.verdict == "AC", res_dual.detail


@pytest.mark.asyncio
async def test_grader_linked_compile_cache_verdicts_and_budget(tmp_path):
    files_dir = tmp_path / "files"
    files_dir.mkdir()
    header = files_dir / "problem.h"
    grader = files_dir / "grader.cpp"

    header.write_text(
        """
#ifndef PROBLEM_H
#define PROBLEM_H
#include <vector>
std::vector<int> encode(int n);
int decode(const std::vector<int>& bits);
#endif
""",
        encoding="utf-8",
    )
    grader.write_text(
        """
#include <iostream>
#include <vector>
#include "problem.h"
int main() {
    int n;
    if (!(std::cin >> n)) return 3;
    auto msg = encode(n);
    for (int b : msg) {
        if (b != 0 && b != 1) {
            std::cerr << "wrong output format: non-bit\\n";
            return 2;
        }
    }
    int ans = decode(msg);
    int bits = static_cast<int>(msg.size());
    int bytes = (bits + 7) / 8;
    if (ans == n) {
        std::cerr << "ok bits_used=" << bits << " bytes_used=" << bytes << " rounds=2\\n";
        return 0;
    }
    std::cerr << "wrong answer expected " << n << " got " << ans << "\\n";
    return 1;
}
""",
        encoding="utf-8",
    )

    sol_ok = await _build(
        tmp_path,
        "gl_ok",
        """
#include <cstddef>
#include "problem.h"
std::vector<int> encode(int n) {
    std::vector<int> out;
    for (int i = 0; i < 8; ++i) out.push_back((n >> i) & 1);
    return out;
}
int decode(const std::vector<int>& bits) {
    int n = 0;
    for (std::size_t i = 0; i < bits.size(); ++i) if (bits[i]) n |= (1 << i);
    return n;
}
""",
        extra_sources=[str(grader)],
        include_dirs=[str(files_dir)],
    )
    sol_wa = await _build(
        tmp_path,
        "gl_wa",
        """
#include "problem.h"
std::vector<int> encode(int n) { (void)n; return {0}; }
int decode(const std::vector<int>& bits) { (void)bits; return 999; }
""",
        extra_sources=[str(grader)],
        include_dirs=[str(files_dir)],
    )
    sol_crash = await _build(
        tmp_path,
        "gl_crash",
        """
#include <cstdlib>
#include "problem.h"
std::vector<int> encode(int n) { (void)n; std::abort(); }
int decode(const std::vector<int>& bits) { (void)bits; return 0; }
""",
        extra_sources=[str(grader)],
        include_dirs=[str(files_dir)],
    )

    r_ac = await CommunicationRunner.run_grader_linked(sol_ok, "173\n", max_bits=8, cwd=str(tmp_path))
    assert r_ac.verdict == "AC", r_ac.detail
    assert r_ac.metrics["bits_used"] == 8
    assert r_ac.metrics["bytes_used"] == 1

    r_over = await CommunicationRunner.run_grader_linked(sol_ok, "173\n", max_bits=4, cwd=str(tmp_path))
    assert r_over.verdict == "WA"
    assert "budget" in r_over.detail.lower()

    r_wa = await CommunicationRunner.run_grader_linked(sol_wa, "173\n", cwd=str(tmp_path))
    assert r_wa.verdict == "WA"

    r_re = await CommunicationRunner.run_grader_linked(sol_crash, "173\n", cwd=str(tmp_path))
    assert r_re.verdict == "RE"

    # Modifying header/grader MUST NOT return a stale cached binary when recompiling sol_ok!
    grader.write_text(
        """
#include <iostream>
#include <vector>
#include "problem.h"
int main() {
    int n;
    if (!(std::cin >> n)) return 3;
    auto msg = encode(n);
    int ans = decode(msg);
    if (ans == n) {
        std::cerr << "ok bits_used=7 bytes_used=1 rounds=2\\n";
        return 0;
    }
    std::cerr << "wrong answer\\n";
    return 1;
}
""",
        encoding="utf-8",
    )
    sol_rebuilt = await compile_cpp(
        str(tmp_path / "gl_ok.cpp"),
        sol_ok,
        extra_sources=[str(grader)],
        include_dirs=[str(files_dir)],
    )
    assert sol_rebuilt.success
    assert "(cached)" not in (sol_rebuilt.stderr or "")
    r_rebuilt = await CommunicationRunner.run_grader_linked(sol_ok, "173\n", cwd=str(tmp_path))
    assert r_rebuilt.verdict == "AC"
    assert r_rebuilt.metrics["bits_used"] == 7


MULTI_PROCESS_MANAGER = """
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int a = inf.readInt();
    int b = inf.readInt();

    // Send a to p0 and b to p1
    std::cout << "TO 0 " << a << std::endl;
    std::cout << "TO 1 " << b << std::endl;
    std::cout.flush();

    // Expect FROM 0 <msg>, relay to p1, then expect FROM 1 <sum>
    std::string tag = ouf.readToken();
    if (tag != "FROM") quitf(_pe, "expected FROM from p0, got %s", tag.c_str());
    int src = ouf.readInt();
    if (src != 0) quitf(_pe, "expected message from p0, got p%d", src);
    int m = ouf.readInt();

    std::cout << "TO 1 " << m << std::endl;
    std::cout.flush();

    // p0 may exit and emit EOF 0 asynchronously before p1 replies with FROM 1
    while (true) {
        tag = ouf.readToken();
        if (tag == "EOF") {
            int who = ouf.readInt();
            if (who == 0) continue;
            quitf(_pe, "unexpected EOF from p%d", who);
        }
        break;
    }
    if (tag != "FROM") quitf(_pe, "expected FROM from p1, got %s", tag.c_str());
    src = ouf.readInt();
    if (src != 1) quitf(_pe, "expected message from p1, got p%d", src);
    int total = ouf.readInt();

    std::cout << "CLOSE 0\\nCLOSE 1\\n" << std::flush;
    tout << total << "\\n";
    if (total == a + b) {
        quitf(_ok, "OK rounds=2 bits_used=32");
    }
    quitf(_wa, "expected %d, got %d", a + b, total);
}
"""


@pytest.mark.asyncio
async def test_multi_process_ac_deadlock_crash_and_cancel(tmp_path):
    mgr_exe = await _build(tmp_path, "mgr", MULTI_PROCESS_MANAGER)
    p0_ok = await _build(
        tmp_path,
        "p0_ok",
        """
#include <iostream>
int main() {
    int a;
    if (!(std::cin >> a)) return 0;
    std::cout << a << std::endl;
    return 0;
}
""",
    )
    p1_ok = await _build(
        tmp_path,
        "p1_ok",
        """
#include <iostream>
int main() {
    int b, m;
    if (!(std::cin >> b >> m)) return 0;
    std::cout << (b + m) << std::endl;
    return 0;
}
""",
    )
    p0_no_flush = await _build(
        tmp_path,
        "p0_dl",
        """
#include <cstdio>
int main() {
    int a;
    if (scanf("%d", &a) != 1) return 0;
    printf("%d\\n", a); // missing flush, then wait on stdin -> deadlock!
    int dummy;
    if (scanf("%d", &dummy) != 1) return 0;
    return 0;
}
""",
    )
    p0_abort = await _build(
        tmp_path,
        "p0_abort",
        """
#include <cstdlib>
#include <iostream>
int main() {
    int a;
    std::cin >> a;
    std::abort();
}
""",
    )

    # 1. AC
    r_ac = await CommunicationRunner.run_multi_process(
        mgr_exe, [p0_ok, p1_ok], "17 25\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert r_ac.verdict == "AC", r_ac.detail
    assert r_ac.judge_stdout.strip() == "42"
    assert r_ac.metrics["participants"] == 2
    assert r_ac.metrics["bits_used"] == 32
    assert "[manager->p0] 17" in r_ac.transcript
    assert "[p1->manager] 42" in r_ac.transcript

    # 2. DEADLOCK
    r_dl = await CommunicationRunner.run_multi_process(
        mgr_exe,
        [p0_no_flush, p1_ok],
        "17 25\n",
        time_limit_ms=2000.0,
        deadlock_timeout_ms=350.0,
        cwd=str(tmp_path),
    )
    assert r_dl.verdict == "DEADLOCK", f"Expected DEADLOCK, got {r_dl.verdict}: {r_dl.detail}"

    # 3. Participant crash -> RE (not PE)
    r_re = await CommunicationRunner.run_multi_process(
        mgr_exe, [p0_abort, p1_ok], "17 25\n", time_limit_ms=2000.0, cwd=str(tmp_path)
    )
    assert r_re.verdict == "RE", f"Expected RE, got {r_re.verdict}: {r_re.detail}"

    # 4. Cancellation safety
    task = asyncio.create_task(
        CommunicationRunner.run_multi_process(
            mgr_exe,
            [p0_no_flush, p1_ok],
            "17 25\n",
            time_limit_ms=5000.0,
            deadlock_timeout_ms=4000.0,
            cwd=str(tmp_path),
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_safe_resolve_and_manifest_path_traversal_blocked(tmp_path):
    """Path traversal and symlink escapes must be rejected in manifest, runner, and build_all."""
    from pydantic import ValidationError

    from autocode_mcp.tools.build_all import ProblemBuildAllTool
    from autocode_mcp.utils.communication_runner import safe_resolve_under_root
    from autocode_mcp.workflow.models import AutoCodeManifest, ProtocolConfig, SolutionEntry

    outside = tmp_path / "outside.cpp"
    outside.write_text("int main(){return 0;}\n", encoding="utf-8")
    prob = tmp_path / "prob"
    (prob / "files").mkdir(parents=True)
    (prob / "solutions").mkdir(parents=True)

    # Direct traversal & absolute paths rejected
    assert safe_resolve_under_root(str(prob), "../outside.cpp") is None
    assert safe_resolve_under_root(str(prob), str(outside)) is None

    # Symlink escape rejected
    link = prob / "files" / "grader.cpp"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege not enabled")
        raise
    assert safe_resolve_under_root(str(prob), "files/grader.cpp") is None

    # Model-level rejection of unsafe paths and stems
    with pytest.raises(ValidationError):
        SolutionEntry(name="bad", role="main", path="../outside.cpp")
    with pytest.raises(ValidationError):
        ProtocolConfig(mode="run_twice", participant_solutions=["../alice"])

    # build_all rejects symlink-escaped grader_file without touching outside file
    manifest = AutoCodeManifest(
        problem_name="sym-test",
        problem_type="communication",
        protocol=ProtocolConfig(mode="grader_linked", grader_file="files/grader.cpp"),
        oracle_mode="interactor_only",
    )
    (prob / ".autocode").mkdir(exist_ok=True)
    (prob / ".autocode" / "manifest.json").write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )
    (prob / "solutions" / "sol.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    res = await ProblemBuildAllTool().execute(problem_dir=str(prob))
    assert not res.success
    assert "escapes" in (res.error or "").lower()
    assert outside.exists()


@pytest.mark.asyncio
async def test_local_header_change_invalidates_cache_without_extra_sources(tmp_path):
    """Modifying a local header in source_path's dir must bypass compile cache even with include_dirs=None."""
    from autocode_mcp.utils.judge_runner import StandardRunner

    hdr = tmp_path / "local_val.h"
    src = tmp_path / "prog.cpp"
    exe = tmp_path / f"prog{get_exe_extension()}"

    hdr.write_text("#define MAGIC_VAL 111\n", encoding="utf-8")
    src.write_text(
        '#include <iostream>\n#include "local_val.h"\nint main(){ std::cout << MAGIC_VAL << "\\n"; }\n',
        encoding="utf-8",
    )

    c1 = await compile_cpp(str(src), str(exe), include_dirs=None, extra_sources=None)
    assert c1.success, c1.stderr
    r1 = await StandardRunner.run(str(exe), "", cwd=str(tmp_path))
    assert r1.stdout.strip() == "111"

    # Change ONLY the header (source file untouched!) and recompile
    hdr.write_text("#define MAGIC_VAL 222\n", encoding="utf-8")
    c2 = await compile_cpp(str(src), str(exe), include_dirs=None, extra_sources=None)
    assert c2.success, c2.stderr
    assert "(cached)" not in (c2.stderr or "")
    r2 = await StandardRunner.run(str(exe), "", cwd=str(tmp_path))
    assert r2.stdout.strip() == "222"


@pytest.mark.asyncio
async def test_run_twice_max_queries_and_cumulative_cpu_tle(tmp_path):
    """run_twice enforces max_queries and cumulative CPU across passes."""
    q_comm = await _build(
        tmp_path,
        "q_comm",
        """
#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    const char* s = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = s ? std::atoi(s) : 2;
    int n = inf.readInt();
    if (stage == 1) {
        std::string msg = ouf.readToken();
        std::cout << msg << "\\n";
        quitf(_ok, "stage1");
    }
    int ans_val = ouf.readInt();
    if (ans_val == n) quitf(_ok, "OK queries=5 bits_used=4");
    quitf(_wa, "wa");
}
""",
    )
    sol_fast = await _build(
        tmp_path,
        "sol_fast",
        """
#include <iostream>
#include <string>
int main() {
    std::string p; int x;
    std::cin >> p >> x;
    std::cout << x << "\\n";
}
""",
    )
    sol_slow_each = await _build(
        tmp_path,
        "sol_slow_each",
        """
#include <chrono>
#include <iostream>
#include <string>
int main() {
    std::string p; int x;
    std::cin >> p >> x;
    // Burn deterministic ~110ms CPU in EACH pass (individually < 160ms, cumulatively ~220ms > 160ms)
    auto start = std::chrono::steady_clock::now();
    volatile unsigned long long acc = 0;
    while (std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now() - start).count() < 110) {
        acc += 1;
    }
    std::cout << (x + (acc == 0 ? 1 : 0)) << "\\n";
}
""",
    )

    # max_queries enforced
    r_q_ok = await CommunicationRunner.run_twice(
        q_comm, sol_fast, "7\n", max_queries=5, cwd=str(tmp_path)
    )
    assert r_q_ok.verdict == "AC", r_q_ok.detail

    r_q_over = await CommunicationRunner.run_twice(
        q_comm, sol_fast, "7\n", max_queries=3, cwd=str(tmp_path)
    )
    assert r_q_over.verdict == "WA"
    assert "queries=5 > 3" in r_q_over.detail

    # Cumulative CPU TLE across passes (each pass ~110ms < 160ms, total ~220ms > 160ms)
    r_tle = await CommunicationRunner.run_twice(
        q_comm, sol_slow_each, "7\n", time_limit_ms=160.0, cwd=str(tmp_path)
    )
    assert r_tle.verdict == "TLE", f"Expected TLE, got {r_tle.verdict}: {r_tle.detail}"


@pytest.mark.asyncio
async def test_run_multi_process_oversized_line_and_crash_not_masked_by_cleanup(tmp_path):
    """Oversized line >4MiB returns PE cleanly; p0 crash is NOT masked when p1 is cleanup-killed."""
    mgr_exe = await _build(tmp_path, "mgr_edge", MULTI_PROCESS_MANAGER)

    p0_huge = await _build(
        tmp_path,
        "p0_huge",
        """
#include <iostream>
#include <string>
int main() {
    int a;
    if (!(std::cin >> a)) return 0;
    std::string blob(5 * 1024 * 1024, '1');
    std::cout << blob << std::flush;
    return 0;
}
""",
    )
    p0_abort = await _build(
        tmp_path,
        "p0_abort2",
        """
#include <cstdlib>
#include <iostream>
int main() {
    int a;
    std::cin >> a;
    std::abort();
}
""",
    )
    p1_spin = await _build(
        tmp_path,
        "p1_spin",
        """
#include <iostream>
int main() {
    int b;
    std::cin >> b;
    volatile unsigned long long x = 0;
    while (true) { x++; }
}
""",
    )
    p1_ok = await _build(
        tmp_path,
        "p1_ok2",
        """
#include <iostream>
int main() {
    int b, m;
    if (!(std::cin >> b >> m)) return 0;
    std::cout << (b + m) << std::endl;
    return 0;
}
""",
    )

    # 1. Oversized line >4 MiB without newline -> PE (not unhandled ValueError crash!)
    r_huge = await CommunicationRunner.run_multi_process(
        mgr_exe, [p0_huge, p1_ok], "5 6\n", time_limit_ms=3000.0, cwd=str(tmp_path)
    )
    assert r_huge.verdict == "PE", f"Expected PE, got {r_huge.verdict}: {r_huge.detail}"

    # 2. p0 aborts (SIGABRT) while p1 spins (killed by post-manager cleanup) -> RE (not PE!)
    r_re = await CommunicationRunner.run_multi_process(
        mgr_exe, [p0_abort, p1_spin], "5 6\n", time_limit_ms=3000.0, cwd=str(tmp_path)
    )
    assert r_re.verdict == "RE", f"Expected RE, got {r_re.verdict}: {r_re.detail}"
    assert "p0" in r_re.detail

