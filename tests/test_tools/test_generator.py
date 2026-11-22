"""
Generator 工具组测试。
"""

import os
import tempfile

import pytest

from autocode_mcp.tools.generator import GeneratorBuildTool, GeneratorRunTool

# 简单的 Generator 代码（基于 testlib.h）
GENERATOR_CODE = """
#include "testlib.h"
#include <iostream>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);

    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    int n_min = atoi(argv[3]);
    int n_max = atoi(argv[4]);
    int t_min = atoi(argv[5]);
    int t_max = atoi(argv[6]);

    rnd.setSeed(seed);

    int n = rnd.next(n_min, n_max);

    std::cout << n << std::endl;

    for (int i = 0; i < n; i++) {
        if (i > 0) std::cout << " ";
        std::cout << rnd.next(1, 1000000000);
    }
    std::cout << std::endl;

    return 0;
}
"""

WEAK_GENERATOR_CODE = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = atoi(argv[2]);
    int n_max = atoi(argv[4]);
    if (type == 3) {
        std::cout << n_max << "\\n";
    } else if (type == 4) {
        std::cout << n_max << "\\n";
    }
    return 0;
}
"""


@pytest.mark.asyncio
async def test_generator_build():
    """测试 Generator 构建。"""
    tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )

        assert result.success
        assert os.path.exists(result.data["source_path"])
        assert os.path.exists(result.data["binary_path"])
        assert "semantic_check" in result.data


@pytest.mark.asyncio
async def test_generator_build_uses_existing_default_source():
    """generator_build 缺省 code/source_path 时读取 files/gen.cpp。"""
    tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        files_dir = os.path.join(tmpdir, "files")
        os.makedirs(files_dir)
        with open(os.path.join(files_dir, "gen.cpp"), "w", encoding="utf-8") as f:
            f.write(GENERATOR_CODE)

        result = await tool.execute(problem_dir=tmpdir)

        assert result.success
        assert os.path.exists(result.data["binary_path"])


@pytest.mark.asyncio
async def test_generator_build_invalid_code():
    """测试无效代码编译失败。"""
    tool = GeneratorBuildTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code="invalid c++ code {{{",
        )

        assert not result.success
        assert "compilation" in result.error.lower()


@pytest.mark.asyncio
async def test_generator_run():
    """测试 Generator 运行。"""
    build_tool = GeneratorBuildTool()
    run_tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        # 先构建
        build_result = await build_tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )
        assert build_result.success

        # 运行
        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            strategies=["random"],
            test_count=5,
        )

        assert run_result.success
        assert run_result.data["generated_count"] == 5
        assert len(run_result.data["inputs"]) == 5


@pytest.mark.asyncio
async def test_generator_run_multiple_strategies():
    """测试多策略 Generator 运行。"""
    build_tool = GeneratorBuildTool()
    run_tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        await build_tool.execute(
            problem_dir=tmpdir,
            code=GENERATOR_CODE,
        )

        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            strategies=["tiny", "random"],
            test_count=10,
        )

        assert run_result.success
        assert run_result.data["generated_count"] == 10


@pytest.mark.asyncio
async def test_generator_run_not_found():
    """测试运行不存在的 Generator。"""
    tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            strategies=["random"],
            test_count=5,
        )

        assert not result.success
        assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_generator_run_with_custom_params():
    """测试使用自定义 n_min/n_max 参数运行 Generator。"""
    build_tool = GeneratorBuildTool()
    run_tool = GeneratorRunTool()

    with tempfile.TemporaryDirectory() as tmpdir:
        await build_tool.execute(problem_dir=tmpdir, code=GENERATOR_CODE)

        run_result = await run_tool.execute(
            problem_dir=tmpdir,
            strategies=["random"],
            test_count=3,
            n_min=5,
            n_max=10,
            t_min=1,
            t_max=1,
        )

        assert run_result.success
        assert run_result.data["generated_count"] == 3
        # 验证生成的数据在预期范围内
        for inp in run_result.data["inputs"]:
            lines = inp["input"].strip().split("\n")
            n = int(lines[0])
            assert 5 <= n <= 10


@pytest.mark.asyncio
async def test_generator_build_strict_semantic_check():
    """严格语义检查下，type3/type4 同构应构建失败。"""
    tool = GeneratorBuildTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=WEAK_GENERATOR_CODE,
            strict_semantic_check=True,
        )
        assert not result.success
        assert "semantic" in result.error.lower()


@pytest.mark.asyncio
async def test_generator_build_semantic_check_reports_overlap():
    """语义检查应返回 type3/type4 的结构信号重叠信息。"""
    tool = GeneratorBuildTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=WEAK_GENERATOR_CODE,
        )
        assert result.success
        semantic = result.data["semantic_check"]
        assert "signal_overlap" in semantic
        assert "signal_overlap_advisory" in semantic
        assert "type3_signals" in semantic
        assert "type4_signals" in semantic
        assert semantic["passed"] is False
        assert semantic["type3_lines"]
        assert semantic["type4_lines"]
        assert semantic["trigger_reasons"]
        assert semantic["sample_probe"]["executed"] is True
        assert semantic["sample_probe"]["runtime_identical"] is True


@pytest.mark.asyncio
async def test_generator_semantic_check_long_branches_and_shared_output_no_false_positive():
    """长 branch (>240字符) 与 main 末尾共享单处 cout 不得误判为 type=3/4 相似。"""
    long_distinct_gen = """
#include "testlib.h"
#include <iostream>
#include <vector>
#include <numeric>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = atoi(argv[2]);
    int n_max = atoi(argv[4]);
    int n = n_max;
    std::vector<int> a(n);
    if (type == 3) {
        // Long anti-hash / monotonic construction > 240 chars before type=4
        for (int i = 0; i < n; ++i) {
            a[i] = (i % 2 == 0) ? (i + 1) : (n - i);
        }
        for (int i = 1; i < n; i += 4) {
            std::swap(a[i - 1], a[i]);
        }
    } else if (type == 4) {
        // Distinct anti-quicksort / bimodal construction with similar length
        std::iota(a.begin(), a.end(), 1);
        for (int i = n / 2; i < n; ++i) {
            a[i] = n - (i - n / 2);
        }
        if (n >= 4) {
            a[0] = n;
            a[n - 1] = 1;
        }
    }
    std::cout << n << "\\n";
    for (int i = 0; i < n; ++i) std::cout << a[i] << " \\n"[i + 1 == n];
    return 0;
}
"""
    tool = GeneratorBuildTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        res = await tool.execute(problem_dir=tmpdir, code=long_distinct_gen, strict_semantic_check=True)
        assert res.success, res.error
        assert res.data["semantic_check"]["passed"] is True

    switch_distinct = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = atoi(argv[2]);
    int n = atoi(argv[4]);
    switch (type) {
        case 3:
            for (int i = 0; i < n; i++) std::cout << i << " ";
            break;
        case 4:
            for (int i = n; i > 0; i--) std::cout << (i * 2) << " ";
            break;
    }
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        res2 = await tool.execute(problem_dir=tmpdir, code=switch_distinct, strict_semantic_check=True)
        assert res2.success, res2.error

    switch_fallthrough = """
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int type = atoi(argv[2]);
    int n = atoi(argv[4]);
    switch (type) {
        case 3:
        case 4:
            std::cout << n << "\\n";
            break;
    }
    return 0;
}
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        res3 = await tool.execute(problem_dir=tmpdir, code=switch_fallthrough, strict_semantic_check=True)
        assert not res3.success


DISTINCT_SHARED_COUT_GENERATOR = """
#include "testlib.h"
#include <iostream>
#include <vector>
#include <algorithm>

int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    int n_min = atoi(argv[3]);
    int n_max = atoi(argv[4]);
    rnd.setSeed(seed);
    int n = rnd.next(n_min, n_max);
    std::vector<long long> vals(n);
    if (type == 1) {
        for (int i = 0; i < n; ++i) vals[i] = i + 1;
    } else if (type == 2) {
        for (int i = 0; i < n; ++i) vals[i] = rnd.next(1, 100);
    } else if (type == 3) {
        // Extreme monotonic boundary sequence with repeated maximum values and huge span
        long long extreme_hi = 1000000000LL;
        long long extreme_lo = 1LL;
        for (int i = 0; i < n; ++i) {
            vals[i] = (i % 2 == 0) ? extreme_hi : extreme_lo;
        }
        std::sort(vals.begin(), vals.end());
    } else if (type == 4) {
        // Adversarial tie/base_pow pattern specifically targeting worst-case pivot & cache thrashing
        int adv_kind = seed % 3;
        long long base_pow = 1LL << 20;
        long long tie_offset = 17LL;
        for (int i = 0; i < n; ++i) {
            if (adv_kind == 0) vals[i] = base_pow - (i * tie_offset);
            else if (adv_kind == 1) vals[i] = base_pow + ((i & 1) ? tie_offset : -tie_offset);
            else vals[i] = (i + 1) * 31LL + tie_offset;
        }
    }
    // Single shared output loop at the end (common clean generator idiom)
    std::cout << n << "\\n";
    for (int i = 0; i < n; ++i) {
        if (i) std::cout << " ";
        std::cout << vals[i];
    }
    std::cout << "\\n";
    return 0;
}
"""


@pytest.mark.asyncio
async def test_generator_build_distinct_type34_with_shared_cout_passes_strict():
    """type=3/type=4 分支逻辑独立且使用尾部统一 cout 时，即使严格语义检查也应通过（避免误报）。"""
    tool = GeneratorBuildTool()
    with tempfile.TemporaryDirectory() as tmpdir:
        result = await tool.execute(
            problem_dir=tmpdir,
            code=DISTINCT_SHARED_COUT_GENERATOR,
            strict_semantic_check=True,
        )
        assert result.success, result.error
        semantic = result.data["semantic_check"]
        assert semantic["passed"] is True
        assert semantic["signal_overlap"] < 0.35
        assert semantic["sample_probe"]["executed"] is True
        assert semantic["sample_probe"]["runtime_distinct"] is True
        assert semantic["type3_lines"]
        assert semantic["type4_lines"]


def test_generator_semantic_check_downgrades_to_warning_when_runtime_distinct():
    """静态相似度较高但实测样本分布不同（或低 signal_overlap）时应降级为 warning 而非硬拦截。"""
    tool = GeneratorBuildTool()
    code = """
    if (type == 3) {
        int val = compute_bound(n_max, alpha_mode, 1);
        emit(val);
    } else if (type == 4) {
        int val = compute_bound(n_max, alpha_mode, 2);
        emit(val);
    }
    """
    res = tool._check_type34_semantics(
        code,
        sample_probe={"executed": True, "runtime_distinct": True, "runtime_identical": False},
    )
    assert res["passed"] is True
    assert res["severity"] == "warning"
    assert res["advisory"] is True
    assert res["type3_lines"] and res["type4_lines"]
    assert res["trigger_reasons"]


def test_generator_semantic_parser_fallthrough_digit_sep_comments_compound_if_and_no_break():
    """回归：case fallthrough 判同构、C++14 数字分隔符不破坏词法、注释/assert 忽略、复合 if 括号配对、no_break 不截断 case。"""
    tool = GeneratorBuildTool()

    # 1. Pure switch/case fallthrough case 3: case 4: must fail as identical body
    fallthrough_code = """
    switch (type) {
        case 3:
        case 4:
            emit_max_boundary(n_max);
            break;
    }
    """
    res_ft = tool._check_type34_semantics(fallthrough_code)
    assert res_ft["passed"] is False
    assert res_ft["severity"] == "error"

    # 2. C++14 digit separators + comments + assert + compound if + no_break identifier in switch
    tricky_code = """
    // if (type == 3) { fake_comment_branch(); }
    assert(type == 3 || type == 4);
    if ((n > 10) && type == 3) {
        long long limit = 1'000'000'000LL;
        build_monotonic_extreme(limit);
    } else if ((n > 10) && type == 4) {
        long long mask = 0xDEAD'BEEF;
        build_anti_hash_killer(mask);
    }
    """
    b3 = tool._extract_type_branches(tricky_code, 3)
    b4 = tool._extract_type_branches(tricky_code, 4)
    assert len(b3) == 1 and "build_monotonic_extreme" in b3[0]["body"]
    assert len(b4) == 1 and "build_anti_hash_killer" in b4[0]["body"]
    res_ok = tool._check_type34_semantics(tricky_code)
    assert res_ok["passed"] is True

    # 3. Identifier ending in 'break' (no_break;) must NOT terminate case 3 early
    no_break_code = """
    switch (type) {
        case 3: {
            int no_break = 1;
            no_break;
            build_extreme_after_no_break();
            break;
        }
        case 4: {
            build_tle_adversarial();
            break;
        }
    }
    """
    cb3 = tool._extract_type_branches(no_break_code, 3)
    assert len(cb3) == 1 and "build_extreme_after_no_break" in cb3[0]["body"]

    # 4. Distinct static bodies with probe-only runtime_identical at tiny scale -> warning (passed=True)
    res_small_probe = tool._check_type34_semantics(
        tricky_code,
        sample_probe={
            "executed": True,
            "runtime_distinct": False,
            "runtime_identical": True,
            "same_seed_match_ratio": 1.0,
        },
    )
    assert res_small_probe["passed"] is True
    assert res_small_probe["severity"] == "warning"
