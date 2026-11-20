"""
Interactor 工具组测试。
"""

import os
import tempfile

import pytest

from autocode_mcp.tools.interactor import InteractorBuildTool

# 简单的 Interactor 代码（基于 testlib.h registerInteraction 约定）
INTERACTOR_CODE = """
#include <iostream>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);

    // 读取输入
    int n = inf.readInt();

    // 按 testlib registerInteraction 约定：通过 std::cout 发送给选手，并写入 tout 供记录
    std::cout << n << std::endl;
    std::cout.flush();
    tout << n << std::endl;

    // 读取选手输出
    int answer = ouf.readInt();

    // 验证答案
    if (answer == n * 2) {
        quitf(_ok, "Correct");
    } else {
        quitf(_wa, "Expected %d, got %d", n * 2, answer);
    }
}
"""


@pytest.mark.asyncio
async def test_interactor_build():
    """测试 Interactor 构建。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_interactor_build_uses_existing_default_source():
    """interactor_build 缺省 code/source_path 时读取 files/interactor.cpp。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(files_dir)
        with open(os.path.join(files_dir, "interactor.cpp"), "w", encoding="utf-8") as f:
            f.write(INTERACTOR_CODE)

        result = await tool.execute(problem_dir=tmpdir)

        assert result.success
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_interactor_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_interactor_build_no_validation():
    """测试不带验证的 Interactor 构建。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
        )

        assert result.success
        # 没有提供参考解和变异解，应该直接返回成功
        assert "binary_path" in result.data


@pytest.mark.asyncio
async def test_interactor_build_with_scripted_scenarios():
    """测试 testlib registerInteraction 的脚本化场景验证。"""
    tool = InteractorBuildTool()

    scenarios = [
        {
            "input": "5\n",
            "contestant_output": "10\n",
            "answer": "",
            "expected_verdict": "AC",
        },
        {
            "input": "5\n",
            "contestant_output": "15\n",
            "answer": "",
            "expected_verdict": "WA",
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            interaction_scenarios=scenarios,
        )

        assert result.success
        report = result.data["interaction_scenarios"]
        assert report["validated"] is True
        assert report["total"] == 2
        assert report["accuracy"] == 1.0
        assert report["details"][0]["interactor_output"] == "5\n"


@pytest.mark.asyncio
async def test_interactor_build_fails_when_scripted_scenario_mismatches():
    """脚本化场景不匹配时不应把 interactor_build 视为成功。"""
    tool = InteractorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            interaction_scenarios=[
                {
                    "input": "5\n",
                    "contestant_output": "15\n",
                    "expected_verdict": "AC",
                }
            ],
        )

        assert not result.success
        assert result.data["interaction_scenarios"]["accuracy"] == 0.0


SIMPLE_SOLUTION = """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n * 2 << std::endl;
    return 0;
}
"""

WRONG_SOLUTION = """
#include <iostream>
int main() {
    int n;
    std::cin >> n;
    std::cout << n * 3 << std::endl;
    return 0;
}
"""


@pytest.mark.asyncio
async def test_interactor_with_reference_solution():
    """测试 Interactor 与参考解法的交互验证。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.platform import get_exe_extension

    tool = InteractorBuildTool()
    sol_tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 构建 interactor
        build_result = await tool.execute(problem_dir=tmpdir, code=INTERACTOR_CODE)
        assert build_result.success

        from autocode_mcp.utils.compiler import compile_cpp

        # 构建正确的参考解法与错误变异解法
        sol_ok = await sol_tool.execute(problem_dir=tmpdir, solution_type="sol", code=SIMPLE_SOLUTION)
        assert sol_ok.success
        correct_sol_exe = os.path.join(tmpdir, "solutions", f"sol{get_exe_extension()}")
        wrong_src = os.path.join(tmpdir, "solutions", "wrong_triple.cpp")
        wrong_sol_exe = os.path.join(tmpdir, "solutions", f"wrong_triple{get_exe_extension()}")
        with open(wrong_src, "w", encoding="utf-8") as wf:
            wf.write(WRONG_SOLUTION)
        wrong_compile = await compile_cpp(wrong_src, wrong_sol_exe)
        assert wrong_compile.success, wrong_compile.stderr

        # 走真实双向管道验证正确解与错误解
        result = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            reference_solution_path=correct_sol_exe,
            mutant_solutions=[wrong_sol_exe],
            test_inputs=["5\n", "12\n"],
        )

        assert result.success
        assert result.data["pass_rate"] == 1.0
        assert result.data["pass_count"] == 2
        assert result.data["pass_total"] == 2
        assert result.data["fail_rate"] == 1.0
        assert result.data["fail_count"] == 1
        assert result.data["fail_total"] == 1
        assert result.data["reference_results"][0]["verdict"] == "AC"
        assert "[judge] 5" in result.data["reference_results"][0]["transcript"]
        assert "[contestant] 10" in result.data["reference_results"][0]["transcript"]
        assert result.data["mutant_results"][0]["killed"] is True
        assert result.data["mutant_results"][0]["verdict"] == "WA"


@pytest.mark.asyncio
async def test_interactor_judge_fail_not_counted_as_mutant_kill():
    """Interactor exiting with FAIL (_fail) must NOT count as killing a mutant."""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.platform import get_exe_extension

    failing_interactor = """
#include <iostream>
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    quitf(_fail, "internal interactor failure");
}
"""
    tool = InteractorBuildTool()
    sol_tool = SolutionBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        sol_ok = await sol_tool.execute(problem_dir=tmpdir, solution_type="sol", code=WRONG_SOLUTION)
        assert sol_ok.success
        mutant_exe = os.path.join(tmpdir, "solutions", f"sol{get_exe_extension()}")

        res = await tool.execute(
            problem_dir=tmpdir,
            code=failing_interactor,
            mutant_solutions=[mutant_exe],
            test_inputs=["5\n"],
        )
        assert res.success
        assert res.data["fail_rate"] == 0.0
        assert res.data["mutant_results"][0]["killed"] is False
        assert res.data["mutant_results"][0]["verdict"] == "FAIL"
        assert res.data["mutant_results"][0]["judge_failed"] is True


@pytest.mark.asyncio
async def test_interactor_build_persists_scenarios_for_verify_tests():
    """interactor_build 传入并验证通过的 interaction_scenarios 应持久化到 .autocode/ 并被自检复用。"""
    from autocode_mcp.tools.test_verify import ProblemVerifyTestsTool

    tool = InteractorBuildTool()
    verifier = ProblemVerifyTestsTool()
    scenarios = [
        {"input": "5\n", "contestant_output": "10\n", "expected_verdict": "AC", "category": "ac"},
        {"input": "5\n", "contestant_output": "9\n", "expected_verdict": "WA", "category": "wrong_answer"},
        {"input": "5\n", "contestant_output": "abc\n", "expected_verdict": "PE", "category": "malformed_command"},
        {"input": "5\n", "contestant_output": "999\n", "expected_verdict": "WA", "category": "out_of_range"},
        {"input": "5\n", "contestant_output": "1\n", "expected_verdict": "WA", "category": "query_limit"},
        {"input": "5\n", "contestant_output": "", "expected_verdict": "PE", "category": "premature_eof"},
        {"input": "5\n", "contestant_output": "bad", "expected_verdict": "PE", "category": "extra_output"},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        res = await tool.execute(problem_dir=tmpdir, code=INTERACTOR_CODE, interaction_scenarios=scenarios)
        assert res.success, res.error
        assert os.path.isfile(os.path.join(tmpdir, ".autocode", "interactor_scenarios.json"))
        self_test = await verifier._check_interactor_self_test(tmpdir, 10)
        assert self_test["passed"] is True
        assert self_test["total"] == 7


async def test_interactor_resolves_relative_and_cpp_reference_and_default_sol():
    """传入相对路径或 .cpp 源码路径时应自动映射/编译为可执行文件，且省略时自动探测 solutions/sol。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.judge_runner import InteractiveRunner
    from autocode_mcp.utils.platform import get_exe_extension

    tool = InteractorBuildTool()
    sol_tool = SolutionBuildTool()
    exe_ext = get_exe_extension()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. 只存在 solutions/sol.cpp（未提前编译）+ 相对 .cpp 路径 -> 自动编译而非报 PermissionError
        os.makedirs(os.path.join(tmpdir, "solutions"), exist_ok=True)
        sol_cpp = os.path.join(tmpdir, "solutions", "sol.cpp")
        with open(sol_cpp, "w", encoding="utf-8") as f:
            f.write(SIMPLE_SOLUTION)
        mutant_cpp = os.path.join(tmpdir, "solutions", "wrong_m.cpp")
        with open(mutant_cpp, "w", encoding="utf-8") as f:
            f.write(WRONG_SOLUTION)

        res_cpp = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            reference_solution_path="solutions/sol.cpp",
            mutant_solutions=["solutions/wrong_m.cpp"],
            test_inputs=["5\n", "7\n"],
        )
        assert res_cpp.success, res_cpp.error
        assert res_cpp.data["pass_rate"] == 1.0
        assert res_cpp.data["fail_rate"] == 1.0
        assert res_cpp.data["reference_solution_binary"].endswith(f"sol{exe_ext}")

        # 2. 传入相对二进制路径 solutions/sol
        res_rel = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            reference_solution_path=f"solutions/sol{exe_ext}",
            test_inputs=["9\n"],
        )
        assert res_rel.success, res_rel.error
        assert res_rel.data["pass_rate"] == 1.0

        # 3. 省略 reference_solution_path 但传入 test_inputs -> 自动解析已编译 solutions/sol
        sol_ok = await sol_tool.execute(problem_dir=tmpdir, solution_type="sol", code=SIMPLE_SOLUTION)
        assert sol_ok.success
        res_auto = await tool.execute(
            problem_dir=tmpdir,
            code=INTERACTOR_CODE,
            test_inputs=["6\n"],
        )
        assert res_auto.success, res_auto.error
        assert res_auto.data["pass_total"] == 1
        assert res_auto.data["pass_rate"] == 1.0

        # 4. 非可执行普通文件（非 .cpp）应返回清晰提示而非 PermissionError
        note_file = os.path.join(tmpdir, "solutions", "not_exec.txt")
        with open(note_file, "w", encoding="utf-8") as f:
            f.write("plain text\n")
        if os.name != "nt":
            os.chmod(note_file, 0o644)
            res_nonexec = await tool.execute(
                problem_dir=tmpdir,
                code=INTERACTOR_CODE,
                reference_solution_path="solutions/not_exec.txt",
                test_inputs=["5\n"],
            )
            assert not res_nonexec.success
            assert "not executable" in (res_nonexec.error or "").lower()

            # 5. InteractiveRunner 直接收到 .cpp 源码时也返回 FAIL 而非抛出 PermissionError
            direct_res = await InteractiveRunner.run(
                interactor_exe=res_auto.data["binary_path"],
                solution_exe=sol_cpp,
                input_data="5\n",
                time_limit_ms=2000.0,
                cwd=tmpdir,
            )
            assert direct_res.verdict == "FAIL"
            assert "not executable" in direct_res.detail.lower()


@pytest.mark.asyncio
async def test_interactor_path_containment_mutant_dedup_and_oserror_fail(tmp_path):
    """回归：绝对路径逃逸、派生二进制符号链接逃逸应阻断；.cpp mutant 不应与自动解析的 solutions/sol 冲突；OSError 返回 FAIL。"""
    import shutil

    if not shutil.which("g++"):
        pytest.skip("g++ not available")

    from autocode_mcp.tools.solution import SolutionBuildTool
    from autocode_mcp.utils.judge_runner import InteractiveRunner
    from autocode_mcp.utils.platform import get_exe_extension

    tool = InteractorBuildTool()
    sol_tool = SolutionBuildTool()
    exe_ext = get_exe_extension()

    pdir = tmp_path / "prob"
    outside = tmp_path / "outside"
    (pdir / "solutions").mkdir(parents=True)
    outside.mkdir(parents=True)

    # 1. Absolute path outside problem_dir must be rejected
    out_cpp = outside / "evil.cpp"
    out_cpp.write_text(SIMPLE_SOLUTION, encoding="utf-8")
    res_escape = await tool.execute(
        problem_dir=str(pdir),
        code=INTERACTOR_CODE,
        reference_solution_path=str(out_cpp),
        test_inputs=["5\n"],
    )
    assert not res_escape.success
    assert "escapes" in (res_escape.error or "").lower()

    # 2. Symlink at derived binary path escaping problem_dir must be rejected
    if os.name != "nt":
        in_cpp = pdir / "solutions" / "linked.cpp"
        in_cpp.write_text(SIMPLE_SOLUTION, encoding="utf-8")
        outside_bin = outside / "victim"
        outside_bin.write_bytes(b"victim")
        (pdir / "solutions" / f"linked{exe_ext}").symlink_to(outside_bin)
        res_sym = await tool.execute(
            problem_dir=str(pdir),
            code=INTERACTOR_CODE,
            reference_solution_path="solutions/linked.cpp",
            test_inputs=["5\n"],
        )
        assert not res_sym.success
        assert "escapes" in (res_sym.error or "").lower()
        assert outside_bin.read_bytes() == b"victim"

    # 3. mutant_solutions=["solutions/sol.cpp"] must NOT auto-select solutions/sol as reference too
    sol_ok = await sol_tool.execute(problem_dir=str(pdir), solution_type="sol", code=WRONG_SOLUTION)
    assert sol_ok.success
    res_dedup = await tool.execute(
        problem_dir=str(pdir),
        code=INTERACTOR_CODE,
        mutant_solutions=["solutions/sol.cpp"],
        test_inputs=["5\n"],
    )
    assert res_dedup.success, res_dedup.error
    assert res_dedup.data["pass_total"] == 0
    assert res_dedup.data["fail_rate"] == 1.0

    # 4. Executable file with invalid binary format (triggers OSError ENOEXEC) returns FAIL instead of raising
    if os.name != "nt":
        bad_exec = pdir / "solutions" / "bad_magic_exec"
        bad_exec.write_bytes(b"\x00\x01\x02\x03not-an-elf-or-macho")
        os.chmod(bad_exec, 0o755)
        jres = await InteractiveRunner.run(
            interactor_exe=res_dedup.data["binary_path"],
            solution_exe=str(bad_exec),
            input_data="5\n",
            time_limit_ms=2000.0,
            cwd=str(pdir),
        )
        assert jres.verdict == "FAIL"
        assert "failed to start" in jres.detail.lower() or "exec" in jres.detail.lower()
