"""
Complexity 分析工具 - 分析解法复杂度。

基于代码静态分析估算时间/空间复杂度，并推荐测试参数。
"""

import re
from typing import Literal

from .base import Tool, ToolResult


class ComplexityLevel:
    """复杂度等级。"""

    CONSTANT = "O(1)"
    LOG_N = "O(log n)"
    LINEAR = "O(n)"
    N_LOG_N = "O(n log n)"
    QUADRATIC = "O(n^2)"
    CUBIC = "O(n^3)"
    EXPONENTIAL = "O(2^n)"
    FACTORIAL = "O(n!)"


# 复杂度到推荐 n_max 的映射
COMPLEXITY_TO_N_MAX = {
    ComplexityLevel.CONSTANT: 10**9,
    ComplexityLevel.LOG_N: 10**9,
    ComplexityLevel.LINEAR: 10**7,
    ComplexityLevel.N_LOG_N: 10**6,
    ComplexityLevel.QUADRATIC: 5000,
    ComplexityLevel.CUBIC: 500,
    ComplexityLevel.EXPONENTIAL: 20,
    ComplexityLevel.FACTORIAL: 12,
}

# 复杂度到推荐时间限制的映射（毫秒）
COMPLEXITY_TO_TIME_LIMIT = {
    ComplexityLevel.CONSTANT: 1000,
    ComplexityLevel.LOG_N: 1000,
    ComplexityLevel.LINEAR: 1000,
    ComplexityLevel.N_LOG_N: 2000,
    ComplexityLevel.QUADRATIC: 3000,
    ComplexityLevel.CUBIC: 5000,
    ComplexityLevel.EXPONENTIAL: 10000,
    ComplexityLevel.FACTORIAL: 10000,
}


def analyze_loop_complexity(code: str) -> str:
    """分析循环复杂度。

    Args:
        code: C++ 源代码

    Returns:
        估算的复杂度字符串
    """
    # 统计嵌套循环层数
    loop_patterns = [
        r"\bfor\s*\(",
        r"\bwhile\s*\(",
        r"\bfor\s+.*:\s*",  # range-based for
    ]

    max_nesting = 0
    current_nesting = 0

    lines = code.split("\n")
    for line in lines:
        # 计算当前行的循环数
        loop_count = 0
        for pattern in loop_patterns:
            loop_count += len(re.findall(pattern, line))

        # 检测循环结束
        brace_change = line.count("{") - line.count("}")

        # 更新嵌套深度
        current_nesting += loop_count
        max_nesting = max(max_nesting, current_nesting)
        current_nesting = max(0, current_nesting + brace_change)

    # 根据嵌套层数估算复杂度
    if max_nesting == 0:
        return ComplexityLevel.LINEAR  # 默认假设
    elif max_nesting == 1:
        return ComplexityLevel.LINEAR
    elif max_nesting == 2:
        return ComplexityLevel.QUADRATIC
    elif max_nesting == 3:
        return ComplexityLevel.CUBIC
    else:
        return ComplexityLevel.EXPONENTIAL


def detect_algorithm_patterns(code: str) -> tuple[str, list[str]]:
    """检测常见算法模式。

    Args:
        code: C++ 源代码

    Returns:
        (复杂度, 检测到的模式列表)
    """
    patterns = []
    complexity = ComplexityLevel.LINEAR  # 默认

    # 二分查找
    if re.search(r"\bbinary_search\b|\blower_bound\b|\bupper_bound\b", code):
        patterns.append("binary_search")
        complexity = ComplexityLevel.N_LOG_N

    # 排序
    if re.search(r"\bsort\b|\bstable_sort\b|\bpartial_sort\b", code):
        patterns.append("sorting")
        complexity = ComplexityLevel.N_LOG_N

    # 图算法 - BFS/DFS
    if re.search(r"\bbfs\b|\bdfs\b|queue<|stack<", code):
        patterns.append("graph_traversal")
        complexity = ComplexityLevel.LINEAR

    # 动态规划
    if re.search(r"dp\[|memo\[|memoization", code):
        patterns.append("dynamic_programming")
        # DP 复杂度取决于状态数和转移
        complexity = ComplexityLevel.QUADRATIC

    # 哈希表
    if re.search(r"unordered_map|unordered_set|hash_map", code):
        patterns.append("hash_table")
        # 如果主要操作是哈希，可能更优

    # 递归
    if re.search(r"\breturn\s+\w+\s*\([^)]*\)", code) and re.search(
        r"\b\w+\s*\([^)]*\)\s*{", code
    ):
        # 简单的递归检测
        patterns.append("recursion")

    # 位运算
    if re.search(r"1\s*<<\s*\d|bitmask|bitset", code):
        patterns.append("bitmask")
        complexity = ComplexityLevel.EXPONENTIAL

    return complexity, patterns


def estimate_memory_usage(code: str) -> tuple[str, int]:
    """估算内存使用。

    Args:
        code: C++ 源代码

    Returns:
        (空间复杂度描述, 估算的内存 MB)
    """
    # 检测大数组
    array_patterns = [
        r"(\w+)\s*\[(\d+)\]",  # int arr[1000]
        r"vector<\w+>\s+(\w+)\s*\((\d+)\)",  # vector<int> v(1000)
        r"array<\w+,\s*(\d+)>",  # array<int, 1000>
    ]

    total_elements = 0
    for pattern in array_patterns:
        matches = re.findall(pattern, code)
        for match in matches:
            try:
                # 获取数字部分
                if isinstance(match, tuple):
                    size = int(match[-1])
                else:
                    size = int(match)
                total_elements += size
            except (ValueError, IndexError):
                pass

    # 估算内存（假设每个元素 4 字节）
    memory_bytes = total_elements * 4
    memory_mb = max(1, memory_bytes // (1024 * 1024))

    if total_elements == 0:
        return "O(1) - O(n)", 64
    elif total_elements < 10000:
        return "O(n)", memory_mb
    elif total_elements < 1000000:
        return "O(n)", memory_mb
    else:
        return "O(n) - large", memory_mb


class SolutionAnalyzeTool(Tool):
    """分析解法复杂度。"""

    @property
    def name(self) -> str:
        return "solution_analyze"

    @property
    def description(self) -> str:
        return """分析 C++ 解法代码的时间/空间复杂度。

        基于静态分析估算：
        - 时间复杂度（循环嵌套、算法模式）
        - 空间复杂度（数组、容器大小）
        - 推荐的测试参数

        前置条件：
        1. 已有解法代码（可以是未编译的源码）

        建议下一步：
        - 根据推荐的 n_max 调整测试数据生成参数
        - 根据推荐的 time_limit 设置题目时间限制
        """

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "C++ 源代码",
                },
                "constraints": {
                    "type": "object",
                    "description": "已知的题目约束（可选）",
                    "properties": {
                        "n_max": {"type": "integer"},
                        "time_limit_ms": {"type": "integer"},
                    },
                },
            },
            "required": ["code"],
        }

    async def execute(
        self,
        code: str,
        constraints: dict | None = None,
    ) -> ToolResult:
        """执行复杂度分析。"""
        # 1. 分析循环复杂度
        loop_complexity = analyze_loop_complexity(code)

        # 2. 检测算法模式
        pattern_complexity, patterns = detect_algorithm_patterns(code)

        # 3. 选择更优的复杂度估计
        # 优先使用模式检测的结果
        complexity_order = [
            ComplexityLevel.CONSTANT,
            ComplexityLevel.LOG_N,
            ComplexityLevel.LINEAR,
            ComplexityLevel.N_LOG_N,
            ComplexityLevel.QUADRATIC,
            ComplexityLevel.CUBIC,
            ComplexityLevel.EXPONENTIAL,
            ComplexityLevel.FACTORIAL,
        ]

        loop_idx = complexity_order.index(loop_complexity)
        pattern_idx = complexity_order.index(pattern_complexity)

        # 如果模式检测到更优的复杂度，使用它
        if pattern_idx < loop_idx:
            final_complexity = pattern_complexity
        else:
            final_complexity = loop_complexity

        # 4. 估算内存
        space_complexity, memory_mb = estimate_memory_usage(code)

        # 5. 生成推荐参数
        recommended_n_max = COMPLEXITY_TO_N_MAX.get(final_complexity, 10000)
        recommended_time_ms = COMPLEXITY_TO_TIME_LIMIT.get(final_complexity, 1000)

        # 如果有题目约束，验证是否合理
        warnings = []
        if constraints:
            if constraints.get("n_max"):
                if constraints["n_max"] > recommended_n_max:
                    warnings.append(
                        f"Warning: n_max={constraints['n_max']} may cause TLE "
                        f"for {final_complexity} algorithm. Recommended: {recommended_n_max}"
                    )
            if constraints.get("time_limit_ms"):
                if constraints["time_limit_ms"] < recommended_time_ms:
                    warnings.append(
                        f"Warning: time_limit={constraints['time_limit_ms']}ms may be too tight "
                        f"for {final_complexity} algorithm. Recommended: {recommended_time_ms}ms"
                    )

        return ToolResult.ok(
            time_complexity=final_complexity,
            space_complexity=space_complexity,
            estimated_memory_mb=memory_mb,
            detected_patterns=patterns,
            recommended_n_max=recommended_n_max,
            recommended_time_limit_ms=recommended_time_ms,
            warnings=warnings,
            suggested_test_configs=self._generate_test_configs(
                recommended_n_max, constraints
            ),
            message=f"Analyzed complexity: {final_complexity}",
        )

    def _generate_test_configs(
        self, n_max: int, constraints: dict | None
    ) -> list[dict]:
        """生成推荐的测试配置。

        Args:
            n_max: 推荐的 n 最大值
            constraints: 题目约束

        Returns:
            测试配置列表
        """
        # 使用约束中的 n_max 或推荐值
        actual_n_max = constraints.get("n_max", n_max) if constraints else n_max

        configs = [
            # 边界情况
            {"type": "1", "n_min": 1, "n_max": 1, "t_min": 1, "t_max": 1},
            {"type": "1", "n_min": 1, "n_max": 10, "t_min": 1, "t_max": 1},
            # 随机数据
            {"type": "2", "n_min": 10, "n_max": actual_n_max // 10, "t_min": 1, "t_max": 1},
            {"type": "2", "n_min": actual_n_max // 10, "n_max": actual_n_max // 2, "t_min": 1, "t_max": 1},
            # 极限数据
            {"type": "3", "n_min": actual_n_max // 2, "n_max": actual_n_max, "t_min": 1, "t_max": 1},
            {"type": "3", "n_min": actual_n_max, "n_max": actual_n_max, "t_min": 1, "t_max": 1},
        ]

        return configs
