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
    """无循环代码应返回默认 O(n)。"""
    code = """
int main() {
    int a, b;
    cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
"""
    result = analyze_loop_complexity(code)
    assert result == ComplexityLevel.LINEAR


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
