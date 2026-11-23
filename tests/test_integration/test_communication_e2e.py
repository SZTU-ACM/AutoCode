"""End-to-end communication problem pipelines (run_twice and grader_linked) with real C++."""

from __future__ import annotations

import json
import os
import shutil
import xml.etree.ElementTree as ET

import pytest

from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.build_all import ProblemBuildAllTool
from autocode_mcp.tools.complexity import SolutionAnalyzeTool
from autocode_mcp.tools.file_ops import FileSaveTool
from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.interactor import InteractorBuildTool
from autocode_mcp.tools.problem import (
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
)
from autocode_mcp.tools.solution import SolutionBuildTool, SolutionRunTool
from autocode_mcp.tools.solution_audit import SolutionAuditStdTool
from autocode_mcp.tools.stress_test import StressTestRunTool
from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool
from autocode_mcp.tools.validation import ProblemValidateTool
from autocode_mcp.tools.validator import ValidatorBuildTool
from autocode_mcp.utils.compiler import compile_cpp
from autocode_mcp.utils.platform import get_exe_extension
from autocode_mcp.workflow import (
    apply_result,
    check_gates,
    load_manifest,
    load_workflow_state,
    save_manifest,
)
from autocode_mcp.workflow.models import SolutionEntry

pytestmark = pytest.mark.skipif(not shutil.which("g++"), reason="g++ not available")

RUN_TWICE_STATEMENT = """# Noisy Bit Channel

## 时间限制与空间限制

- 时间限制：1000ms
- 空间限制：256MB

## 题目描述

本题为 Run-Twice 通信题。给定整数 $v \\in [0, 15]$，请通过最多翻转 1 个比特的噪声信道将其无损传给第二阶段进程。

## 通信协议

- 本题分两次独立进程运行（`first` 与 `second`），禁止共享全局状态或临时文件。
- 第一阶段读入 `first` 与整数 $v, p$，输出不超过 12 比特（`max_bits=12`）的 `01` 串。
- 信道会翻转位置 $p \\bmod |s|$ 处的 1 个比特并传给第二阶段。
- 第二阶段读入 `second` 与噪声串，还原输出整数 $v$。

## 样例

```text
pass_1 (first): 5 2 -> 111000111000
channel (flip 2): 110000111000
pass_2 (second): 110000111000 -> 5
```

## 说明

将每个比特重复 3 次，第二阶段按多数投票解码，总通信量 12 比特。
"""

RUN_TWICE_TUTORIAL = """# Noisy Bit Channel 题解

## 思路概述

采用 3 次重复编码（Repetition Code）：对 4 位整数的每个比特重复发送 3 次，总计 12 比特。

## 正确性说明

信道最多翻转 1 个比特，因此每一组 3 个比特中至少有 2 个比特保持原值，多数投票必定正确还原。

## 复杂度分析

单次编码与解码时间复杂度均为 $O(1)$，通信量严格为 12 bits。
"""

RUN_TWICE_COMM_CPP = """
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
            if (c != '0' && c != '1') quitf(_pe, "non-binary char");
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

RUN_TWICE_SOL_CPP = """
// Time Complexity: O(1)
#include <iostream>
#include <string>
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int val, fp;
        std::cin >> val >> fp;
        std::string code;
        for (int b = 0; b < 4; ++b) {
            char bit = ((val >> b) & 1) ? '1' : '0';
            code.append(3, bit);
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

# Over-budget wrong solution: uses 5x repetition (20 bits > max_bits=12)!
RUN_TWICE_WRONG_OVER_BUDGET_CPP = """
#include <iostream>
#include <string>
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int val, fp;
        std::cin >> val >> fp;
        std::string code;
        for (int b = 0; b < 4; ++b) {
            char bit = ((val >> b) & 1) ? '1' : '0';
            code.append(5, bit);
        }
        std::cout << code << "\\n";
    } else {
        std::string noisy;
        std::cin >> noisy;
        int val = 0;
        for (int b = 0; b < 4; ++b) {
            int ones = 0;
            for (int k = 0; k < 5; ++k) ones += (noisy[5 * b + k] == '1');
            if (ones >= 3) val |= (1 << b);
        }
        std::cout << val << "\\n";
    }
    return 0;
}
"""

# Global-variable cheating wrong solution: tries to pass state via static global across passes!
RUN_TWICE_WRONG_CHEAT_CPP = """
#include <iostream>
#include <string>
static int leaked = 0;
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int val, fp;
        std::cin >> val >> fp;
        leaked = val;
        std::cout << "0\\n";
    } else {
        std::string noisy;
        std::cin >> noisy;
        std::cout << leaked << "\\n";
    }
    return 0;
}
"""

RUN_TWICE_VAL_CPP = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(0, 15, "val");
    inf.readSpace();
    inf.readInt(0, 1000, "flip_pos");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""

RUN_TWICE_GEN_CPP = """
#include <cstdlib>
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    int val = rnd.next(1, 15);
    int fp = rnd.next(0, 11);
    if (type == 1) { val = rnd.next(1, 3); fp = 0; }
    else if (type == 3) { val = 15; fp = rnd.next(0, 11); }
    else if (type == 4) { val = 10; fp = 11 - rnd.next(0, 5); }
    std::cout << val << " " << fp << "\\n";
    return 0;
}
"""


async def _run_tracked(problem_dir: str, tool, **kwargs):
    res = await tool.execute(problem_dir=problem_dir, **kwargs)
    apply_result(problem_dir, tool.name, {"problem_dir": problem_dir, **kwargs}, res.success, res.data)
    return res


@pytest.mark.asyncio
async def test_communication_run_twice_full_pipeline_e2e(tmp_path):
    problem_dir = str(tmp_path / "noisy-channel")
    exe_ext = get_exe_extension()

    # 1. Create run_twice communication problem
    create_res = await _run_tracked(
        problem_dir,
        ProblemCreateTool(),
        problem_name="noisy-channel",
        problem_type="communication",
        protocol_mode="run_twice",
        oracle_mode="interactor_only",
    )
    assert create_res.success, create_res.error

    manifest = load_manifest(problem_dir)
    assert manifest is not None
    assert manifest.protocol is not None
    manifest.time_limit_ms = 1000
    manifest.memory_limit_mb = 256
    manifest.protocol.max_bits = 12
    manifest.protocol.max_bytes = 2
    manifest.protocol.max_rounds = 2
    manifest.constraints = {"n_max": 15, "t_max": 1}
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", path="solutions/sol.cpp", expected="pass"),
        SolutionEntry(
            name="wrong_budget", role="wrong", path="solutions/wrong_budget.cpp", expected="fail"
        ),
        SolutionEntry(
            name="wrong_cheat", role="wrong", path="solutions/wrong_cheat.cpp", expected="fail"
        ),
    ]
    save_manifest(problem_dir, manifest)

    # 2. Save statement & tutorial
    save_tool = FileSaveTool()
    assert (
        await _run_tracked(
            problem_dir, save_tool, path="statements/README.md", content=RUN_TWICE_STATEMENT
        )
    ).success
    assert (
        await _run_tracked(
            problem_dir, save_tool, path="statements/tutorial.md", content=RUN_TWICE_TUTORIAL
        )
    ).success

    # 3. Build std + over-budget & cheating wrong solutions
    assert (
        await _run_tracked(
            problem_dir, SolutionBuildTool(), solution_type="sol", code=RUN_TWICE_SOL_CPP
        )
    ).success
    for w_name, w_code in (
        ("wrong_budget", RUN_TWICE_WRONG_OVER_BUDGET_CPP),
        ("wrong_cheat", RUN_TWICE_WRONG_CHEAT_CPP),
    ):
        w_src = os.path.join(problem_dir, "solutions", f"{w_name}.cpp")
        w_bin = os.path.join(problem_dir, "solutions", f"{w_name}{exe_ext}")
        with open(w_src, "w", encoding="utf-8") as wf:
            wf.write(w_code)
        wc = await compile_cpp(w_src, w_bin)
        assert wc.success, wc.stderr

    assert (
        await _run_tracked(problem_dir, SolutionAnalyzeTool(), solution_type="sol")
    ).success
    assert (
        await _run_tracked(problem_dir, SolutionAuditStdTool(), claimed_complexity="O(1)")
    ).success

    # 4. Build communicator (via interactor_build) with stage 1 & stage 2 scenarios + live mutants
    scenarios = [
        {
            "category": "ac",
            "stage": 1,
            "input": "5 2\n",
            "contestant_output": "111000111000\n",
            "expected_verdict": "AC",
        },
        {
            "category": "format_error",
            "stage": 1,
            "input": "5 2\n",
            "contestant_output": "123abc\n",
            "expected_verdict": "PE",
        },
        {
            "category": "ac",
            "stage": 2,
            "input": "5 2\n",
            "answer": "110000111000\n",
            "contestant_output": "5\n",
            "expected_verdict": "AC",
        },
        {
            "category": "wrong_answer",
            "stage": 2,
            "input": "5 2\n",
            "answer": "110000111000\n",
            "contestant_output": "7\n",
            "expected_verdict": "WA",
        },
    ]
    (tmp_path / "noisy-channel" / "files" / "communicator_scenarios.json").write_text(
        json.dumps(scenarios, indent=2), encoding="utf-8"
    )
    comm_res = await _run_tracked(
        problem_dir,
        InteractorBuildTool(),
        code=RUN_TWICE_COMM_CPP,
        interaction_scenarios=scenarios,
        reference_solution_path=os.path.join(problem_dir, "solutions", f"sol{exe_ext}"),
        mutant_solutions=[
            os.path.join(problem_dir, "solutions", f"wrong_budget{exe_ext}"),
            os.path.join(problem_dir, "solutions", f"wrong_cheat{exe_ext}"),
        ],
        test_inputs=["5 2\n", "14 7\n"],
    )
    assert comm_res.success, comm_res.error
    assert comm_res.data["canonical_path"].endswith("communicator.cpp")
    assert comm_res.data["scenario_accuracy"] == 1.0
    assert comm_res.data["pass_rate"] == 1.0
    assert comm_res.data["fail_rate"] == 1.0

    # 5. Build validator & generator
    assert (
        await _run_tracked(
            problem_dir,
            ValidatorBuildTool(),
            code=RUN_TWICE_VAL_CPP,
            test_cases=[
                {"input": "5 2\n", "should_pass": True},
                {"input": "16 0\n", "should_pass": False},
            ],
        )
    ).success
    assert (await _run_tracked(problem_dir, GeneratorBuildTool(), code=RUN_TWICE_GEN_CPP)).success

    # 6. solution_run in communication mode
    run_res = await SolutionRunTool().execute(
        problem_dir=problem_dir, solution_type="sol", input_data="11 4\n"
    )
    assert run_res.success, run_res.error
    assert run_res.data["mode"] == "communication:run_twice"
    assert run_res.data["verdict"] == "AC"
    assert run_res.data["metrics"]["bits_used"] == 12

    # 7. stress_test_run (no brute required under interactor_only)
    stress_res = await _run_tracked(problem_dir, StressTestRunTool(), trials=4, n_max=15, timeout=5)
    assert stress_res.success, stress_res.error
    assert stress_res.data["completed_rounds"] == 4

    # 8. problem_validate (communication protocol text + live communication!)
    val_res = await _run_tracked(
        problem_dir,
        ProblemValidateTool(),
        statement_samples=[{"input": "5 2\n"}, {"input": "13 9\n"}],
    )
    assert val_res.success, val_res.error
    assert val_res.data["communication_protocol"]["passed"] is True
    assert val_res.data["live_communication"]["validated"] is True
    assert val_res.data["live_communication"]["passed"] == 2

    # 9. Generate tests
    test_configs = [
        {"type": "1", "n_min": 1, "n_max": 3, "t_min": 1, "t_max": 1, "seed_offset": 1},
        {"type": "2", "n_min": 4, "n_max": 10, "t_min": 1, "t_max": 1, "seed_offset": 2},
        {"type": "3", "n_min": 15, "n_max": 15, "t_min": 1, "t_max": 1, "seed_offset": 3},
        {"type": "3", "n_min": 12, "n_max": 15, "t_min": 1, "t_max": 1, "seed_offset": 4},
        {"type": "4", "n_min": 10, "n_max": 15, "t_min": 1, "t_max": 1, "seed_offset": 5},
        {"type": "4", "n_min": 8, "n_max": 15, "t_min": 1, "t_max": 1, "seed_offset": 6},
    ]
    gen_tests_res = await _run_tracked(
        problem_dir,
        ProblemGenerateTestsTool(),
        test_count=6,
        test_configs=test_configs,
        oversample_ratio=1.5,
    )
    assert gen_tests_res.success, gen_tests_res.error
    assert len(gen_tests_res.data["generated_tests"]) == 6

    # 10. Verify tests (kills both over-budget AND cheating wrong solutions!)
    verify_res = await _run_tracked(
        problem_dir,
        ProblemVerifyTestsTool(),
        verify_types=[
            "file_count",
            "no_empty",
            "validator",
            "answer_consistency",
            "limit_ratio",
            "limit_semantics",
            "wrong_solution_kill",
            "duplicate_inputs",
            "scale_distribution",
            "purpose_coverage",
            "interactor_self_test",
        ],
    )
    assert verify_res.success, f"Verify failed: {verify_res.data}"
    ans_chk = verify_res.data["results"]["answer_consistency"]
    assert ans_chk["mode"] == "communication:run_twice"
    assert ans_chk["protocol_metrics"]["max_bits_used"] == 12

    kill = verify_res.data["results"]["wrong_solution_kill"]
    assert kill["mode"] == "communication:run_twice"
    assert kill["passed"] is True
    by_name = {d["name"]: d for d in kill["details"]}
    assert by_name["wrong_budget"]["killed"] is True
    assert by_name["wrong_budget"]["killing_verdict"] == "WA"
    assert by_name["wrong_cheat"]["killed"] is True
    assert by_name["wrong_cheat"]["killing_verdict"] == "WA"

    # 11. Full audit (reports communication_metrics + budget utilization!)
    audit_res = await _run_tracked(problem_dir, ProblemAuditTool(), mode="full")
    assert audit_res.success, audit_res.data
    assert audit_res.data["decision"] == "go", audit_res.data.get("blocking_issues")
    cm = audit_res.data["communication_metrics"]
    assert cm["protocol_mode"] == "run_twice"
    assert cm["observed"]["max_bits_used"] == 12
    assert cm["utilization"]["bits_ratio"] == pytest.approx(1.0)
    assert cm["budget_exceeded"] is False

    post_state = load_workflow_state(problem_dir)
    assert not check_gates(manifest, post_state, post_state["verify_signals"])

    # 12. Pack to Polygon
    pack_res = await _run_tracked(problem_dir, ProblemPackPolygonTool())
    assert pack_res.success, f"Pack failed: {pack_res.error} / {pack_res.data}"
    tree = ET.parse(os.path.join(problem_dir, "problem.xml"))
    root = tree.getroot()
    comm_elem = root.find(".//judging/testset/communication")
    assert comm_elem is not None
    assert comm_elem.attrib.get("mode") == "run_twice"
    assert comm_elem.attrib.get("passes") == "2"
    int_elem = root.find(".//judging/testset/interactor")
    assert int_elem is not None
    assert int_elem.attrib.get("path") == "files/communicator.cpp"


@pytest.mark.asyncio
async def test_communication_grader_linked_full_pipeline_e2e(tmp_path):
    problem_dir = str(tmp_path / "grader-comm")

    assert (
        await _run_tracked(
            problem_dir,
            ProblemCreateTool(),
            problem_name="grader-comm",
            problem_type="communication",
            protocol_mode="grader_linked",
            oracle_mode="interactor_only",
        )
    ).success

    manifest = load_manifest(problem_dir)
    assert manifest is not None
    assert manifest.protocol is not None
    manifest.time_limit_ms = 1000
    manifest.memory_limit_mb = 256
    manifest.protocol.max_bits = 8
    manifest.protocol.grader_file = "files/grader.cpp"
    manifest.protocol.header_file = "files/problem.h"
    manifest.constraints = {"n_max": 255, "t_max": 1}
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", path="solutions/sol.cpp", expected="pass"),
        SolutionEntry(
            name="wrong_bits", role="wrong", path="solutions/wrong_bits.cpp", expected="fail"
        ),
    ]
    save_manifest(problem_dir, manifest)

    save_tool = FileSaveTool()
    assert (
        await _run_tracked(
            problem_dir,
            save_tool,
            path="files/problem.h",
            content="""
#ifndef AUTOCODE_PROBLEM_H
#define AUTOCODE_PROBLEM_H
#include <vector>
std::vector<int> encode(int n);
int decode(const std::vector<int>& msg);
#endif
""",
        )
    ).success
    assert (
        await _run_tracked(
            problem_dir,
            save_tool,
            path="files/grader.cpp",
            content="""
#include <iostream>
#include <vector>
#include "problem.h"
int main() {
    int n;
    if (!(std::cin >> n)) return 3;
    auto msg = encode(n);
    int ans = decode(msg);
    int bits = static_cast<int>(msg.size());
    int bytes = (bits + 7) / 8;
    if (ans == n) {
        std::cerr << "ok bits_used=" << bits << " bytes_used=" << bytes << " rounds=2\\n";
        return 0;
    }
    std::cerr << "wrong answer\\n";
    return 1;
}
""",
        )
    ).success
    assert (
        await _run_tracked(
            problem_dir,
            save_tool,
            path="statements/README.md",
            content="""# Grader Comm

## 题目描述

实现 `encode` 与 `decode` 函数。

## 通信协议

- 通过 grader 链接编译，最多传输 8 bits。

## 样例

```text
encode(42) -> 8 bits -> decode -> 42
```
""",
        )
    ).success
    assert (
        await _run_tracked(
            problem_dir,
            save_tool,
            path="statements/tutorial.md",
            content=(
                "# Grader Comm 题解\n\n"
                "## 思路概述\n\n"
                "直接按 8 位二进制编码与解码整数 $n \\in [0, 255]$，总计使用 8 个比特。\n\n"
                "## 正确性说明\n\n"
                "由于 $0 \\le n < 2^8$，按位提取与按位重构是严格双射，解码结果必然与原输入完全一致。\n\n"
                "## 复杂度分析\n\n"
                "时间复杂度 $O(1)$，空间复杂度 $O(1)$，通信量严格不超过 8 bits。\n"
            ),
        )
    ).success

    sol_code = """
// Time Complexity: O(1)
#include <cstddef>
#include "problem.h"
std::vector<int> encode(int n) {
    std::vector<int> out;
    for (int i = 0; i < 8; ++i) out.push_back((n >> i) & 1);
    return out;
}
int decode(const std::vector<int>& msg) {
    int n = 0;
    for (std::size_t i = 0; i < msg.size(); ++i) if (msg[i]) n |= (1 << i);
    return n;
}
"""
    wrong_bits_code = """
#include <cstddef>
#include "problem.h"
std::vector<int> encode(int n) {
    std::vector<int> out;
    for (int i = 0; i < 16; ++i) out.push_back((n >> i) & 1);
    return out;
}
int decode(const std::vector<int>& msg) {
    int n = 0;
    for (std::size_t i = 0; i < msg.size(); ++i) if (msg[i]) n |= (1 << i);
    return n;
}
"""
    val_code = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    inf.readInt(0, 255, "n");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""
    gen_code = """
#include <cstdlib>
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    int n = rnd.next(1, 255);
    if (type == 1) n = rnd.next(0, 5);
    else if (type == 3) n = 255 - rnd.next(0, 3);
    else if (type == 4) n = 170 + rnd.next(0, 10);
    std::cout << n << "\\n";
    return 0;
}
"""

    assert (await _run_tracked(problem_dir, save_tool, path="solutions/sol.cpp", content=sol_code)).success
    assert (
        await _run_tracked(problem_dir, save_tool, path="solutions/wrong_bits.cpp", content=wrong_bits_code)
    ).success
    assert (await _run_tracked(problem_dir, save_tool, path="files/val.cpp", content=val_code)).success
    assert (await _run_tracked(problem_dir, save_tool, path="files/gen.cpp", content=gen_code)).success

    # problem_build_all links sol.cpp AND wrong_bits.cpp with files/grader.cpp + -I files/!
    build_all_res = await _run_tracked(problem_dir, ProblemBuildAllTool())
    assert build_all_res.success, build_all_res.data
    assert "solutions/sol.cpp" in build_all_res.data["compiled"]
    assert "solutions/wrong_bits.cpp" in build_all_res.data["compiled"]

    # solution_build ALSO works directly for grader_linked
    sb_res = await _run_tracked(problem_dir, SolutionBuildTool(), solution_type="sol")
    assert sb_res.success, sb_res.error

    # solution_run
    run_res = await SolutionRunTool().execute(
        problem_dir=problem_dir, solution_type="sol", input_data="173\n"
    )
    assert run_res.success, run_res.error
    assert run_res.data["mode"] == "communication:grader_linked"
    assert run_res.data["metrics"]["bits_used"] == 8

    # stress_test, validate, generate_tests, verify_tests, audit, pack
    assert (await _run_tracked(problem_dir, StressTestRunTool(), trials=4, n_max=255, timeout=5)).success
    val_res = await _run_tracked(
        problem_dir, ProblemValidateTool(), statement_samples=[{"input": "42\n"}]
    )
    assert val_res.success, val_res.error
    assert val_res.data["live_communication"]["passed"] == 1

    test_configs = [
        {"type": "1", "n_min": 1, "n_max": 5, "t_min": 1, "t_max": 1, "seed_offset": 1},
        {"type": "2", "n_min": 10, "n_max": 100, "t_min": 1, "t_max": 1, "seed_offset": 2},
        {"type": "3", "n_min": 250, "n_max": 255, "t_min": 1, "t_max": 1, "seed_offset": 3},
        {"type": "3", "n_min": 240, "n_max": 255, "t_min": 1, "t_max": 1, "seed_offset": 4},
        {"type": "4", "n_min": 170, "n_max": 200, "t_min": 1, "t_max": 1, "seed_offset": 5},
        {"type": "4", "n_min": 180, "n_max": 220, "t_min": 1, "t_max": 1, "seed_offset": 6},
    ]
    assert (
        await _run_tracked(
            problem_dir,
            ProblemGenerateTestsTool(),
            test_count=6,
            test_configs=test_configs,
            oversample_ratio=1.5,
        )
    ).success
    verify_res = await _run_tracked(
        problem_dir,
        ProblemVerifyTestsTool(),
        verify_types=[
            "file_count",
            "no_empty",
            "validator",
            "answer_consistency",
            "limit_ratio",
            "limit_semantics",
            "wrong_solution_kill",
            "duplicate_inputs",
            "scale_distribution",
            "purpose_coverage",
        ],
    )
    assert verify_res.success, verify_res.data
    assert verify_res.data["results"]["wrong_solution_kill"]["passed"] is True

    audit_res = await _run_tracked(problem_dir, ProblemAuditTool(), mode="full")
    assert audit_res.success, audit_res.data
    assert audit_res.data["decision"] == "go", audit_res.data.get("blocking_issues")
    assert audit_res.data["communication_metrics"]["observed"]["max_bits_used"] == 8

    pack_res = await _run_tracked(problem_dir, ProblemPackPolygonTool())
    assert pack_res.success, f"Pack failed: {pack_res.error} / {pack_res.data}"
    tree = ET.parse(os.path.join(problem_dir, "problem.xml"))
    root = tree.getroot()
    comm_elem = root.find(".//judging/testset/communication")
    assert comm_elem is not None
    assert comm_elem.attrib.get("mode") == "grader_linked"
    assert comm_elem.attrib.get("grader") == "files/grader.cpp"
    assert comm_elem.attrib.get("header") == "files/problem.h"


@pytest.mark.asyncio
async def test_communication_dual_binary_pack_polygon(tmp_path):
    """Dual-binary participant_solutions (without solutions/sol.cpp) must pack cleanly to Polygon."""
    problem_dir = str(tmp_path / "dual-comm")
    assert (
        await ProblemCreateTool().execute(
            problem_dir=problem_dir,
            problem_name="dual-comm",
            problem_type="communication",
            protocol_mode="run_twice",
            oracle_mode="interactor_only",
        )
    ).success
    manifest = load_manifest(problem_dir)
    assert manifest is not None
    assert manifest.protocol is not None
    manifest.protocol.participant_solutions = ["alice", "bob"]
    manifest.quality_gates.require_tests_verified = False
    manifest.quality_gates.require_limit_semantics = False
    manifest.quality_gates.require_wrong_solution_kill = False
    manifest.quality_gates.require_validator_check = False
    manifest.audit_gates.require_full_audit = False
    save_manifest(problem_dir, manifest)

    # Create alice.cpp, bob.cpp, communicator.cpp, and a test case (NO solutions/sol.cpp!)
    (tmp_path / "dual-comm" / "solutions" / "alice.cpp").write_text(
        "int main(){return 0;}\n", encoding="utf-8"
    )
    (tmp_path / "dual-comm" / "solutions" / "bob.cpp").write_text(
        "int main(){return 0;}\n", encoding="utf-8"
    )
    (tmp_path / "dual-comm" / "files" / "communicator.cpp").write_text(
        "int main(){return 0;}\n", encoding="utf-8"
    )
    (tmp_path / "dual-comm" / "tests" / "01.in").write_text("5 2\n", encoding="utf-8")
    (tmp_path / "dual-comm" / "tests" / "01.ans").write_text("OK\n", encoding="utf-8")

    pack_res = await ProblemPackPolygonTool().execute(problem_dir=problem_dir)
    assert pack_res.success, f"Pack failed: {pack_res.error} / {pack_res.data}"

    tree = ET.parse(os.path.join(problem_dir, "problem.xml"))
    root = tree.getroot()
    sol_paths = [el.attrib.get("path") for el in root.findall(".//assets/solutions/solution/source")]
    assert "solutions/alice.cpp" in sol_paths
    assert "solutions/bob.cpp" in sol_paths
    assert "solutions/sol.cpp" not in sol_paths

