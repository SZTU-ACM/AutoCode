"""End-to-end interactive problem pipeline test with real C++ binaries."""

from __future__ import annotations

import json
import os
import shutil
import xml.etree.ElementTree as ET

import pytest

from autocode_mcp.tools.audit import ProblemAuditTool
from autocode_mcp.tools.complexity import SolutionAnalyzeTool
from autocode_mcp.tools.file_ops import FileSaveTool
from autocode_mcp.tools.generator import GeneratorBuildTool
from autocode_mcp.tools.interactor import InteractorBuildTool
from autocode_mcp.tools.problem import (
    ProblemCreateTool,
    ProblemGenerateTestsTool,
    ProblemPackPolygonTool,
)
from autocode_mcp.tools.schemas import ProblemValidateInput
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

STATEMENT_MD = """# Guess Secret

## 时间限制与空间限制

- 时间限制：1000ms
- 空间限制：256MB

## 题目描述

交互器持有正整数 $n$（$1 \\le n \\le 1000$）和秘密整数 $s$（$1 \\le s \\le n$）。
请通过不超过 10 次查询找出 $s$。

## 输入格式

本题为交互题，选手程序不会获得传统静态输入。交互开始时，交互器输出单个整数 $n$（$1 \\le n \\le 1000$）。

## 输出格式

- 查询：`? x`（$1 \\le x \\le n$），每次输出后必须 flush。
- 最终答案：`! s`（$1 \\le s \\le n$），输出后立即结束程序。

## 交互协议

- 交互开始时 judge 首先输出 $n$。
- 每次查询 `? x`，judge 返回 `-1`（$x < s$）、`0`（$x = s$）或 `1`（$x > s$）。
- 最多允许 10 次查询；超限、越界、格式错误、提前 EOF 或未 flush 均判错。
- 输出最终答案 `! s` 后立即结束程序，不得有额外输出。

## 样例

```text
judge: 10
contestant: ? 5
judge: -1
contestant: ? 8
judge: 0
contestant: ! 8
```

## 说明

通过二分查找在 $\\lceil \\log_2 n \\rceil \\le 10$ 次查询内确定 $s$。
"""

TUTORIAL_MD = """# Guess Secret 题解

## 思路概述

维护候选区间 $[l, r]$，每次询问中点 $mid = \\lfloor (l + r) / 2 \\rfloor$。

## 正确性说明

每次询问都将区间长度减半，对于 $n \\le 1000$ 至多需要 10 次查询。

## 复杂度分析

时间复杂度 $O(\\log n)$，空间复杂度 $O(1)$。
"""

INTERACTOR_CPP = """
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int n = inf.readInt(1, 1000, "n");
    int secret = inf.readInt(1, n, "secret");

    std::cout << n << std::endl;
    std::cout.flush();

    int queries = 0;
    const int MAX_Q = 10;
    while (true) {
        std::string cmd = ouf.readToken();
        if (cmd == "?") {
            int x = ouf.readInt(1, n, "x");
            queries++;
            if (queries > MAX_Q) {
                quitf(_wa, "query limit exceeded: %d > %d", queries, MAX_Q);
            }
            int resp = (x < secret ? -1 : (x > secret ? 1 : 0));
            std::cout << resp << std::endl;
            std::cout.flush();
        } else if (cmd == "!") {
            int ans = ouf.readInt(1, n, "ans");
            if (!ouf.seekEof()) {
                quitf(_dirt, "extra output after final answer");
            }
            tout << ans << "\\n";
            if (ans == secret) {
                quitf(_ok, "OK queries=%d", queries);
            } else {
                quitf(_wa, "wrong answer: expected %d, got %d", secret, ans);
            }
        } else {
            quitf(_pe, "malformed command: %s", cmd.c_str());
        }
    }
}
"""

SOL_CPP = """
// Time Complexity: O(log n)
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
"""

WRONG_TOO_MANY_QUERIES_CPP = """
#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    for (int x = 1; x <= n; ++x) {
        std::cout << "? " << x << std::endl;
        int resp;
        if (!(std::cin >> resp)) return 0;
        if (resp == 0) {
            std::cout << "! " << x << std::endl;
            return 0;
        }
    }
    std::cout << "! 1" << std::endl;
    return 0;
}
"""

WRONG_NO_FLUSH_CPP = """
#include <cstdio>
int main() {
    int n;
    if (scanf("%d", &n) != 1) return 0;
    printf("? 1\\n");
    int resp;
    if (scanf("%d", &resp) != 1) return 0;
    printf("! 1\\n");
    return 0;
}
"""

VAL_CPP = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    int n = inf.readInt(1, 1000, "n");
    inf.readSpace();
    inf.readInt(1, n, "secret");
    inf.readEoln();
    inf.readEof();
    return 0;
}
"""

GEN_CPP = """
#include <iostream>
#include <cstdlib>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = argc > 2 ? std::atoi(argv[2]) : 2;
    int n_min = argc > 3 ? std::atoi(argv[3]) : 1;
    int n_max = argc > 4 ? std::atoi(argv[4]) : 1000;
    n_min = std::max(1, std::min(1000, n_min));
    n_max = std::max(n_min, std::min(1000, n_max));

    int n = rnd.next(n_min, n_max);
    int secret = rnd.next(1, n);
    if (type == 1) {
        n = std::min(n_max, 5);
        secret = rnd.next(1, n);
    } else if (type == 3) {
        n = n_max;
        secret = (rnd.next(0, 1) == 0 ? 1 : n);
    } else if (type == 4) {
        n = n_max;
        secret = n - rnd.next(0, std::min(n - 1, 5));
    }
    std::cout << n << " " << secret << "\\n";
    return 0;
}
"""


async def _run_tracked(problem_dir: str, tool, **kwargs):
    res = await tool.execute(problem_dir=problem_dir, **kwargs)
    apply_result(problem_dir, tool.name, {"problem_dir": problem_dir, **kwargs}, res.success, res.data)
    return res


@pytest.mark.asyncio
async def test_interactive_full_pipeline_e2e(tmp_path):
    problem_dir = str(tmp_path / "guess-secret")
    exe_ext = get_exe_extension()

    # 1. Create interactive problem with interactor_only oracle_mode (no brute required)
    create_res = await _run_tracked(
        problem_dir,
        ProblemCreateTool(),
        problem_name="guess-secret",
        problem_type="interactive",
        oracle_mode="interactor_only",
    )
    assert create_res.success, create_res.error

    # Configure manifest with time limit, constraints, and wrong solutions
    manifest = load_manifest(problem_dir)
    assert manifest is not None
    manifest.time_limit_ms = 1000
    manifest.memory_limit_mb = 256
    manifest.constraints = {"n_max": 1000, "t_max": 1}
    manifest.solutions = [
        SolutionEntry(name="sol", role="main", path="solutions/sol.cpp", expected="pass"),
        SolutionEntry(
            name="wrong_queries", role="wrong", path="solutions/wrong_queries.cpp", expected="fail"
        ),
        SolutionEntry(
            name="wrong_no_flush",
            role="wrong",
            path="solutions/wrong_no_flush.cpp",
            expected="fail",
        ),
    ]
    save_manifest(problem_dir, manifest)

    # 2. Save statement & tutorial
    save_tool = FileSaveTool()
    r1 = await _run_tracked(
        problem_dir, save_tool, path="statements/README.md", content=STATEMENT_MD
    )
    r2 = await _run_tracked(
        problem_dir, save_tool, path="statements/tutorial.md", content=TUTORIAL_MD
    )
    assert r1.success and r2.success

    # 3. Build std + two wrong solutions
    sol_tool = SolutionBuildTool()
    b_sol = await _run_tracked(problem_dir, sol_tool, solution_type="sol", code=SOL_CPP)
    assert b_sol.success, b_sol.error
    for w_name, w_code in (
        ("wrong_queries", WRONG_TOO_MANY_QUERIES_CPP),
        ("wrong_no_flush", WRONG_NO_FLUSH_CPP),
    ):
        w_src = os.path.join(problem_dir, "solutions", f"{w_name}.cpp")
        w_bin = os.path.join(problem_dir, "solutions", f"{w_name}{exe_ext}")
        with open(w_src, "w", encoding="utf-8") as wf:
            wf.write(w_code)
        wc = await compile_cpp(w_src, w_bin)
        assert wc.success, wc.stderr

    # Analyze & audit sol complexity
    ana_res = await _run_tracked(problem_dir, SolutionAnalyzeTool(), solution_type="sol")
    assert ana_res.success, ana_res.error
    aud_sol = await _run_tracked(
        problem_dir,
        SolutionAuditStdTool(),
        claimed_complexity="O(n)",
    )
    assert aud_sol.success, aud_sol.error

    # 4. Build interactor with scripted scenarios (covering all 7 categories) + live ref & mutants
    scenarios = [
        {
            "category": "ac",
            "input": "10 8\n",
            "contestant_output": "? 5\n? 8\n! 8\n",
            "expected_verdict": "AC",
        },
        {
            "category": "wrong_answer",
            "input": "10 8\n",
            "contestant_output": "! 3\n",
            "expected_verdict": "WA",
        },
        {
            "category": "malformed_command",
            "input": "10 8\n",
            "contestant_output": "BAD 5\n",
            "expected_verdict": "PE",
        },
        {
            "category": "out_of_range",
            "input": "10 8\n",
            "contestant_output": "? 99\n",
            "expected_verdict": "WA",
        },
        {
            "category": "query_limit",
            "input": "100 99\n",
            "contestant_output": "".join(f"? {i}\n" for i in range(1, 12)),
            "expected_verdict": "WA",
        },
        {
            "category": "premature_eof",
            "input": "10 8\n",
            "contestant_output": "? 5\n",
            "expected_verdict": "PE",
        },
        {
            "category": "extra_output",
            "input": "10 8\n",
            "contestant_output": "! 8 trailing\n",
            "expected_verdict": "PE",
        },
    ]
    (tmp_path / "guess-secret" / "files" / "interactor_scenarios.json").write_text(
        json.dumps(scenarios, indent=2), encoding="utf-8"
    )
    int_res = await _run_tracked(
        problem_dir,
        InteractorBuildTool(),
        code=INTERACTOR_CPP,
        interaction_scenarios=scenarios,
        reference_solution_path=os.path.join(problem_dir, "solutions", f"sol{exe_ext}"),
        mutant_solutions=[
            os.path.join(problem_dir, "solutions", f"wrong_queries{exe_ext}"),
            os.path.join(problem_dir, "solutions", f"wrong_no_flush{exe_ext}"),
        ],
        test_inputs=["100 73\n", "1000 999\n"],
    )
    assert int_res.success, int_res.error
    assert int_res.data["scenario_accuracy"] == 1.0
    assert int_res.data["pass_rate"] == 1.0
    assert int_res.data["fail_rate"] == 1.0

    # 5. Build validator & generator
    val_res = await _run_tracked(
        problem_dir,
        ValidatorBuildTool(),
        code=VAL_CPP,
        test_cases=[
            {"input": "10 5\n", "should_pass": True},
            {"input": "1000 1000\n", "should_pass": True},
            {"input": "10 11\n", "should_pass": False},
            {"input": "0 1\n", "should_pass": False},
        ],
    )
    assert val_res.success, val_res.error

    gen_res = await _run_tracked(problem_dir, GeneratorBuildTool(), code=GEN_CPP)
    assert gen_res.success, gen_res.error

    # 6. Live solution_run in interactive mode
    run_res = await SolutionRunTool().execute(
        problem_dir=problem_dir, solution_type="sol", input_data="100 73\n"
    )
    assert run_res.success, run_res.error
    assert run_res.data["mode"] == "interactive"
    assert run_res.data["verdict"] == "AC"
    assert 1 <= run_res.data["metrics"]["queries"] <= 10
    assert "[judge] 100" in run_res.data["transcript"]

    # 7. Stress test (no brute needed under oracle_mode=interactor_only!)
    stress_res = await _run_tracked(
        problem_dir, StressTestRunTool(), trials=6, n_max=1000, timeout=5
    )
    assert stress_res.success, stress_res.error
    assert stress_res.data["completed_rounds"] == 6

    # 8. Validate interactive problem (protocol checks + live interaction + MCP schema check)
    parsed_validate_input = ProblemValidateInput.model_validate(
        {
            "problem_dir": problem_dir,
            "statement_samples": [{"input": "100 73\n"}, {"input": "10 1\n"}],
        }
    )
    assert len(parsed_validate_input.statement_samples or []) == 2
    validate_res = await _run_tracked(
        problem_dir,
        ProblemValidateTool(),
        statement_samples=[{"input": "100 73\n"}, {"input": "10 1\n"}],
    )
    assert validate_res.success, validate_res.error
    assert validate_res.data["interactive_protocol"]["passed"] is True
    assert validate_res.data["live_interaction"]["validated"] is True
    assert validate_res.data["live_interaction"]["passed"] == 2

    # 9. Generate tests (types 1..4 so limit_ratio & limit_semantics & purpose_coverage pass)
    test_configs = [
        {"type": "1", "n_min": 1, "n_max": 5, "t_min": 1, "t_max": 1, "seed_offset": 1},
        {"type": "2", "n_min": 20, "n_max": 100, "t_min": 1, "t_max": 1, "seed_offset": 2},
        {"type": "3", "n_min": 1000, "n_max": 1000, "t_min": 1, "t_max": 1, "seed_offset": 3},
        {"type": "3", "n_min": 900, "n_max": 1000, "t_min": 1, "t_max": 1, "seed_offset": 4},
        {"type": "4", "n_min": 1000, "n_max": 1000, "t_min": 1, "t_max": 1, "seed_offset": 5},
        {"type": "4", "n_min": 950, "n_max": 1000, "t_min": 1, "t_max": 1, "seed_offset": 6},
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

    # 10. Verify tests (including interactive answer_consistency & wrong_solution_kill)
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
    assert verify_res.data["results"]["answer_consistency"]["mode"] == "interactive"
    assert verify_res.data["results"]["answer_consistency"]["protocol_metrics"]["max_queries"] <= 10
    kill = verify_res.data["results"]["wrong_solution_kill"]
    assert kill["mode"] == "interactive"
    assert kill["passed"] is True
    by_name = {item["name"]: item for item in kill["details"]}
    assert by_name["wrong_queries"]["killed"] is True
    assert by_name["wrong_queries"]["killing_verdict"] == "WA"
    assert by_name["wrong_no_flush"]["killed"] is True
    assert by_name["wrong_no_flush"]["killing_verdict"] == "DEADLOCK"

    # 11. Full audit (must NOT clobber workflow tests_verified / verify_signals!)
    audit_res = await _run_tracked(problem_dir, ProblemAuditTool(), mode="full")
    assert audit_res.success, f"Audit failed: {audit_res.data}"
    assert audit_res.data["decision"] == "go", f"Blocking issues: {audit_res.data.get('blocking_issues')}"

    post_audit_state = load_workflow_state(problem_dir)
    assert post_audit_state["tests_verified"] is True
    gate_violations = check_gates(manifest, post_audit_state, post_audit_state["verify_signals"])
    assert not gate_violations, f"Unexpected post-audit gate violations: {gate_violations}"

    # 12. Pack to Polygon
    pack_res = await _run_tracked(problem_dir, ProblemPackPolygonTool())
    assert pack_res.success, f"Pack failed: {pack_res.error} / {pack_res.data}"
    xml_path = os.path.join(problem_dir, "problem.xml")
    assert os.path.isfile(xml_path)
    tree = ET.parse(xml_path)
    root = tree.getroot()
    interactor_elem = root.find(".//judging/testset/interactor")
    assert interactor_elem is not None
    assert interactor_elem.attrib.get("path") == "files/interactor.cpp"


@pytest.mark.asyncio
async def test_interactive_wrong_solution_kill_rejects_judge_fail(tmp_path):
    """If an interactor returns _fail (FAIL), wrong_solution_kill must NOT count it as killed."""
    problem_dir = str(tmp_path / "fail-int")
    exe_ext = get_exe_extension()
    assert (
        await ProblemCreateTool().execute(
            problem_dir=problem_dir,
            problem_name="fail-int",
            problem_type="interactive",
            oracle_mode="interactor_only",
        )
    ).success
    manifest = load_manifest(problem_dir)
    assert manifest is not None
    manifest.solutions = [
        SolutionEntry(name="wrong_sol", role="wrong", path="solutions/wrong_sol.cpp", expected="fail")
    ]
    save_manifest(problem_dir, manifest)

    # Build broken interactor that always returns _fail
    fail_int_code = """
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    quitf(_fail, "broken interactor");
}
"""
    assert (await InteractorBuildTool().execute(problem_dir=problem_dir, code=fail_int_code)).success

    w_src = os.path.join(problem_dir, "solutions", "wrong_sol.cpp")
    w_bin = os.path.join(problem_dir, "solutions", f"wrong_sol{exe_ext}")
    with open(w_src, "w", encoding="utf-8") as wf:
        wf.write("int main() { return 0; }\n")
    assert (await compile_cpp(w_src, w_bin)).success

    tests_dir = os.path.join(problem_dir, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    with open(os.path.join(tests_dir, "01.in"), "w", encoding="utf-8") as tf:
        tf.write("10\n")
    with open(os.path.join(tests_dir, "01.ans"), "w", encoding="utf-8") as af:
        af.write("OK\n")

    verify_res = await ProblemVerifyTestsTool().execute(
        problem_dir=problem_dir,
        verify_types=["wrong_solution_kill"],
        enable_limit_ratio=False,
    )
    assert not verify_res.success
    kill_res = verify_res.data["results"]["wrong_solution_kill"]
    assert kill_res["passed"] is False
    assert kill_res["details"][0]["killed"] is False
    assert kill_res["details"][0].get("judge_failed") is True

