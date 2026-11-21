"""
Complexity 分析工具测试。
"""

import pytest

from autocode_mcp.tools.complexity import (
    ComplexityLevel,
    SolutionAnalyzeTool,
    analyze_loop_complexity,
    detect_algorithm_patterns,
    estimate_memory_usage,
)

# ============ analyze_loop_complexity 测试 ============


def test_no_loop():
    """无循环代码应返回 O(1)。"""
    code = """
int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.CONSTANT


def test_single_loop():
    """单层循环应返回 O(n)。"""
    code = """int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        sum += i;
    }
    return 0;
}"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.LINEAR


def test_nested_two_loops():
    """双层嵌套循环应返回 O(n^2)。"""
    code = """int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            sum += i * j;
        }
    }
    return 0;
}"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.QUADRATIC


def test_nested_three_loops():
    """三层嵌套循环应返回 O(n^3)。"""
    code = """int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            for (int k = 0; k < n; k++) {
                sum += i * j * k;
            }
        }
    }
    return 0;
}"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.CUBIC


def test_deeply_nested_loops():
    """超过三层嵌套应返回 O(2^n)。"""
    code = """int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            for (int k = 0; k < n; k++) {
                for (int l = 0; l < n; l++) {
                    sum += i * j * k * l;
                }
            }
        }
    }
    return 0;
}"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.EXPONENTIAL


def test_while_loop():
    """while 循环应被识别。"""
    code = """int main() {
    int n;
    cin >> n;
    while (n > 0) {
        n--;
    }
    return 0;
}"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.LINEAR


# ============ detect_algorithm_patterns 测试 ============


def test_binary_search_pattern():
    """二分查找模式应被识别。"""
    code = """
int main() {
    int pos = lower_bound(arr, arr + n, target) - arr;
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "binary_search" in patterns
    assert complexity == ComplexityLevel.N_LOG_N


def test_sorting_pattern():
    """排序模式应被识别。"""
    code = """
int main() {
    sort(arr, arr + n);
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "sorting" in patterns
    assert complexity == ComplexityLevel.N_LOG_N


def test_dp_pattern():
    """动态规划模式应被识别。"""
    code = """
int main() {
    dp[0] = 1;
    for (int i = 1; i <= n; i++) {
        dp[i] = dp[i-1] + dp[i-2];
    }
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "dynamic_programming" in patterns
    assert complexity == ComplexityLevel.QUADRATIC


def test_hash_table_pattern():
    """哈希表模式应被识别。"""
    code = """
int main() {
    unordered_map<int, int> cnt;
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "hash_table" in patterns


def test_bitmask_pattern():
    """位运算模式应被识别。"""
    code = """
int main() {
    for (int mask = 0; mask < (1 << n); mask++) {
        // process mask
    }
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "bitmask" in patterns
    assert complexity == ComplexityLevel.EXPONENTIAL


def test_multiple_patterns():
    """多个模式应同时被识别。"""
    code = """
int main() {
    sort(arr, arr + n);
    int pos = lower_bound(arr, arr + n, target) - arr;
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "sorting" in patterns
    assert "binary_search" in patterns


def test_no_pattern():
    """无模式时应返回 None。"""
    code = """
int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert complexity is None
    assert patterns == []


# ============ estimate_memory_usage 测试 ============


def test_no_array():
    """无数组代码应返回默认内存。"""
    code = """
int main() {
    int a, b;
    cin >> a >> b;
    return 0;
}
"""
    space, memory_mb = estimate_memory_usage(code)
    assert space == "O(1)"
    assert memory_mb == 64


def test_static_array():
    """静态数组应被正确估算。"""
    code = """
int main() {
    int arr[1000];
    return 0;
}
"""
    space, memory_mb = estimate_memory_usage(code)
    # 1000 * 4 bytes = 4000 bytes ≈ 0 MB, min is 1
    assert memory_mb == 1


def test_vector():
    """vector 应被正确估算。"""
    code = """
int main() {
    vector<int> v(10000);
    return 0;
}
"""
    space, memory_mb = estimate_memory_usage(code)
    # 10000 * 4 bytes = 40000 bytes ≈ 0 MB, min is 1
    assert memory_mb == 1


def test_large_array():
    """大数组应被正确估算。"""
    code = """
int main() {
    int arr[1000000];
    return 0;
}
"""
    space, memory_mb = estimate_memory_usage(code)
    # 1000000 * 4 bytes = 4 MB
    assert memory_mb >= 1


# ============ SolutionAnalyzeTool 测试 ============


@pytest.mark.asyncio
async def test_analyze_simple_code():
    """测试简单代码分析。"""
    tool = SolutionAnalyzeTool()

    code = """
int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        sum += i;
    }
    return 0;
}
"""
    result = await tool.execute(code=code)

    assert result.success
    assert result.data["time_complexity"] == ComplexityLevel.LINEAR
    assert "recommended_n_max" in result.data
    assert "recommended_time_limit_ms" in result.data


@pytest.mark.asyncio
async def test_analyze_uses_existing_default_source(tmp_path):
    """solution_analyze 缺省 code/source_path 时读取 solutions/sol.cpp。"""
    tool = SolutionAnalyzeTool()
    solutions_dir = tmp_path / "solutions"
    solutions_dir.mkdir()
    (solutions_dir / "sol.cpp").write_text(
        """
int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        sum += i;
    }
    return 0;
}
""",
        encoding="utf-8",
    )

    result = await tool.execute(problem_dir=str(tmp_path))

    assert result.success
    assert result.data["time_complexity"] == ComplexityLevel.LINEAR


@pytest.mark.asyncio
async def test_analyze_requires_explicit_source_or_problem_dir():
    """无参调用不应隐式读取服务端 CWD。"""
    tool = SolutionAnalyzeTool()

    result = await tool.execute()

    assert not result.success
    assert "problem_dir" in result.error


@pytest.mark.asyncio
async def test_analyze_rejects_invalid_solution_type(tmp_path):
    """solution_type 不能只依赖 schema enum 校验。"""
    tool = SolutionAnalyzeTool()

    result = await tool.execute(problem_dir=str(tmp_path), solution_type="../sol")

    assert not result.success
    assert "solution_type" in result.error


@pytest.mark.asyncio
async def test_analyze_with_constraints():
    """测试带约束的分析。"""
    tool = SolutionAnalyzeTool()

    code = """int main() {
    int n;
    cin >> n;
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            sum += i * j;
        }
    }
    return 0;
}"""
    result = await tool.execute(
        code=code,
        constraints={"n_max": 100000, "time_limit_ms": 500},
    )

    assert result.success
    assert result.data["time_complexity"] == ComplexityLevel.QUADRATIC
    # 应该有警告，因为 n_max=100000 对 O(n^2) 太大
    assert len(result.data["warnings"]) > 0


@pytest.mark.asyncio
async def test_analyze_sorting_code():
    """测试排序代码分析。"""
    tool = SolutionAnalyzeTool()

    code = """int main() {
    int n;
    cin >> n;
    vector<int> arr(n);
    for (int i = 0; i < n; i++) cin >> arr[i];
    sort(arr.begin(), arr.end());
    return 0;
}"""
    result = await tool.execute(code=code)

    assert result.success
    assert "sorting" in result.data["detected_patterns"]
    assert result.data["time_complexity"] == ComplexityLevel.N_LOG_N


@pytest.mark.asyncio
async def test_generate_test_configs():
    """测试配置生成。"""
    tool = SolutionAnalyzeTool()

    code = "int main() { return 0; }"
    result = await tool.execute(code=code)

    assert result.success
    configs = result.data["suggested_test_configs"]
    assert len(configs) == 6
    # 检查配置结构
    for config in configs:
        assert "type" in config
        assert "n_min" in config
        assert "n_max" in config


@pytest.mark.asyncio
async def test_analyze_constant_code_with_large_constraints_has_no_tle_warning():
    """无循环常数解不应被大 n_max 误报 TLE。"""
    tool = SolutionAnalyzeTool()

    code = """
int main() {
    long long n;
    cin >> n;
    cout << n * n << '\\n';
    return 0;
}
"""
    result = await tool.execute(
        code=code,
        constraints={"n_max": 1000000000, "time_limit_ms": 1000},
    )

    assert result.success
    assert result.data["time_complexity"] == ComplexityLevel.CONSTANT
    assert result.data["warnings"] == []


def test_divide_and_conquer_pattern():
    """分治与归并模式应被识别。"""
    code = """
long long merge_count(vector<int>& a, int l, int r) {
    if (r - l <= 1) return 0;
    int m = (l + r) / 2;
    return merge_count(a, l, m) + merge_count(a, m, r);
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "divide_and_conquer" in patterns
    assert complexity == ComplexityLevel.N_LOG_N


def test_tree_data_structure_pattern():
    """树状数组与线段树模式应被识别。"""
    code = """
struct Fenwick {
    vector<int> tree;
    void add(int i, int delta) {}
};
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "tree_data_structure" in patterns
    assert complexity == ComplexityLevel.N_LOG_N


def test_loop_inside_if_not_miscounted_as_quadratic():
    """if/else/struct 花括号不应计入循环嵌套深度。"""
    code = """
struct Solver {
    void run(int n) {
        if (n > 0) {
            if (n < 100) {
                for (int i = 0; i < n; i++) {
                    sum += i;
                }
            }
        }
    }
};
"""
    assert analyze_loop_complexity(code) == ComplexityLevel.LINEAR


def test_braceless_and_allman_nested_loops():
    """无花括号嵌套循环与 Allman 换行花括号应正确识别嵌套深度。"""
    braceless = """
int main() {
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            sum += i + j;
}
"""
    allman = """
int main()
{
    for (int i = 0; i < n; i++)
    {
        for (int j = 0; j < n; j++)
        {
            sum += i + j;
        }
    }
}
"""
    assert analyze_loop_complexity(braceless) == ComplexityLevel.QUADRATIC
    assert analyze_loop_complexity(allman) == ComplexityLevel.QUADRATIC


def test_bitset_not_misclassified_as_exponential():
    """std::bitset 不应误判为 O(2^n) 指数复杂度。"""
    code = """
#include <bitset>
int main() {
    std::bitset<100000> bs;
    for (int i = 0; i < n; i++) bs.set(i);
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "bitset" in patterns
    assert "bitmask" not in patterns
    assert complexity != ComplexityLevel.EXPONENTIAL


def test_sort_plus_bfs_preserves_higher_pattern_complexity():
    """同时出现 sort 与 bfs 时应保留更高复杂度 O(n log n)，不被后续低阶模式覆盖。"""
    code = """
int main() {
    sort(a, a + n);
    queue<int> q;
    bfs(q);
    return 0;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert "sorting" in patterns
    assert "graph_traversal" in patterns
    assert complexity == ComplexityLevel.N_LOG_N


def test_cpp20_digit_separators_and_tricky_loop_structures():
    """C++20 数字分隔符、同行嵌套、do-while 与跨行 for 头不应破坏循环嵌套解析。"""
    digit_sep_code = """
const int MOD = 1'000'000'007;
int main() {
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < n; j++) {
            ans = (ans + i + j) % MOD;
        }
    }
}
"""
    assert analyze_loop_complexity(digit_sep_code) == ComplexityLevel.QUADRATIC

    same_line_braceless = "int main() { for (int i=0;i<n;i++) for (int j=0;j<n;j++) sum += i; }"
    assert analyze_loop_complexity(same_line_braceless) == ComplexityLevel.QUADRATIC

    same_line_braced = "int main() { for (int i=0;i<n;i++) { if (cond) { for (int j=0;j<n;j++) { s++; } } } }"
    assert analyze_loop_complexity(same_line_braced) == ComplexityLevel.QUADRATIC

    do_while_nested = """
int main() {
    do {
        for (int i = 0; i < n; i++) {
            sum += i;
        }
    } while (cond);
}
"""
    assert analyze_loop_complexity(do_while_nested) == ComplexityLevel.QUADRATIC

    multiline_header = """
int main() {
    for (int i = 0;
         i < n;
         i++) {
        for (int j = 0; j < n; j++) {
            sum += 1;
        }
    }
}
"""
    assert analyze_loop_complexity(multiline_header) == ComplexityLevel.QUADRATIC


def test_constant_shift_comments_and_lowercase_bit_not_misclassified():
    """常量位移 1<<30、注释关键词与小写 bit 变量不应误报高阶复杂度。"""
    code = """
// avoid O(2^n) bitmask and dp[i]
int main() {
    const int INF = 1 << 30;
    int bit = 0;
    for (int i = 0; i < n; i++) bit ^= a[i];
    return INF + bit;
}
"""
    complexity, patterns = detect_algorithm_patterns(code)
    assert complexity is None
    assert patterns == []


def test_extract_claimed_complexity_word_boundary_and_nested_parens():
    from autocode_mcp.tools.complexity import extract_claimed_complexity

    assert extract_claimed_complexity("// TODO(fix) ZERO(x)") is None
    assert extract_claimed_complexity("// Complexity: O(n log(n))") == "O(n log(n))"


def test_extract_n_max_ignores_bool_negative_and_prefers_n_over_value_range():
    from autocode_mcp.tools.complexity import extract_n_max_from_constraints

    assert extract_n_max_from_constraints({"multi_test": True, "min_val": -100}) is None
    assert (
        extract_n_max_from_constraints(
            {"n": {"min": 1, "max": 200000}, "a_i": {"min": 0, "max": 10**9}}
        )
        == 200000
    )
    assert extract_n_max_from_constraints({"n_max": 50000, "v_max": 10**9}) == 50000
    assert extract_n_max_from_constraints({"m": {"min": 1, "max": 100000}, "a_i": 10**9}) == 100000


def test_priority_queue_and_dynamic_stl_and_static_complexity_accuracy():
    """验证 priority_queue 归类、动态 STL 空间复杂度、Dijkstra/单调队列静态识别与 n_max 细分 TL。"""
    from autocode_mcp.tools.complexity import (
        estimate_static_complexity,
        recommend_time_limit_ms,
    )

    dijkstra_code = """
#include <queue>
#include <vector>
using namespace std;
int main() {
    int n, m;
    cin >> n >> m;
    vector<vector<pair<int, int>>> adj(n);
    vector<int> dist(n, 1e9);
    priority_queue<pair<int, int>> pq;
    pq.push({0, 0});
    while (!pq.empty()) {
        auto [d, u] = pq.top();
        pq.pop();
        for (auto &[v, w] : adj[u]) {
            if (dist[u] + w < dist[v]) pq.push({-dist[v], v});
        }
    }
    return 0;
}
"""
    comp, pats = estimate_static_complexity(dijkstra_code)
    assert comp == ComplexityLevel.N_LOG_N
    assert "heap" in pats
    assert "graph_traversal" not in pats
    space, _ = estimate_memory_usage(dijkstra_code)
    assert space == "O(n)"

    mono_stack_code = """
#include <stack>
#include <vector>
using namespace std;
int main() {
    int n; cin >> n;
    vector<int> a(n);
    stack<int> st;
    for (int i = 0; i < n; i++) {
        while (!st.empty() && a[st.top()] <= a[i]) st.pop();
        st.push(i);
    }
}
"""
    mono_comp, _ = estimate_static_complexity(mono_stack_code)
    assert mono_comp == ComplexityLevel.LINEAR

    dp2d_code = """
#include <vector>
using namespace std;
int main() {
    int n, m;
    vector<vector<int>> dp(n + 1, vector<int>(m + 1, 0));
    return 0;
}
"""
    space2d, mem2d = estimate_memory_usage(dp2d_code)
    assert space2d == "O(n^2)"
    assert mem2d >= 128

    assert recommend_time_limit_ms(ComplexityLevel.N_LOG_N, {"n_max": 100000}) == 1000
    assert recommend_time_limit_ms(ComplexityLevel.N_LOG_N, {"n_max": 200000}) == 1500
    assert recommend_time_limit_ms(ComplexityLevel.N_LOG_N, {"n_max": 1000000}) == 2000


@pytest.mark.asyncio
async def test_analyze_suggested_test_configs_include_type4_and_ordered_bounds():
    """suggested_test_configs 应包含 type=4 极限档且对极小 n_max（如 12）保持 n_min <= n_max。"""
    tool = SolutionAnalyzeTool()
    res = await tool.execute(code="int main() { return 0; }", constraints={"n_max": 12})
    assert res.success
    configs = res.data["suggested_test_configs"]
    assert len(configs) == 6
    assert any(c["type"] == "4" for c in configs)
    for cfg in configs:
        assert 1 <= cfg["n_min"] <= cfg["n_max"] <= 12


