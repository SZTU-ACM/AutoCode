"""
AutoCode MCP Prompts 模块。

提供预定义的出题工作流提示词模板。
"""

# 完整出题流程提示词
FULL_PIPELINE_PROMPT = """
# AutoCode 出题流程

你是一个竞赛编程出题助手。请按照以下步骤完成题目创建：

## 1. 题面设计
- 确定题目核心算法
- 设计题目背景故事
- 明确输入输出格式
- 设定数据范围和约束

## 2. 解法实现
- 实现 sol.cpp（最优解）
- 实现 brute.cpp（暴力解，用于验证）

## 3. 数据校验器 (Validator)
- 使用 testlib.h 实现 val.cpp
- 验证所有约束条件
- 生成 40 个测试用例（10 valid + 30 near-valid illegal）

## 4. 数据生成器 (Generator)
- 使用 testlib.h 实现 gen.cpp
- 支持多种策略：tiny, random, extreme, tle
- 生成足够多的测试数据

## 5. 压力测试
- 运行对拍测试（sol vs brute）
- 确保至少 1000 轮通过

## 6. Polygon 打包
- 整理文件结构
- 生成 problem.xml

## 重要原则
- 所有代码由你生成，工具只负责编译和执行
- 每个阶段完成后进行验证
- 发现问题及时修复
"""

# 测试生成流程提示词
TEST_GENERATION_PROMPT = """
# 测试数据生成流程

## 1. Validator 构建
基于论文 Algorithm 1，生成 40 个测试用例：
- 10 个有效输入
- 30 个 near-valid illegal 输入（接近有效但违反约束）

## 2. Generator 构建
基于论文 Algorithm 2，实现三种策略：
- G1 (tiny): 小数据穷举
- G2 (random + extreme): 随机 + 极端数据
- G3 (tle): TLE 诱导数据

## 3. 后处理
- 使用 Validator 过滤无效输入
- 去重（基于 signature）
- 平衡分布
- 采样

## 质量指标
- Consistency > 90%
- FPR (False Positive Rate) < 5%
- FNR (False Negative Rate) < 15%
"""

# Validator 构建提示词
VALIDATOR_PROMPT = """
# Validator 构建指南

## 基于论文 Algorithm 1: BUILDVALIDATOR

### 步骤 1: 生成测试用例
生成 40 个测试用例：
- 10 个有效输入（valid inputs）
- 30 个 near-valid illegal inputs

### Near-valid illegal 示例
如果约束是 N ≤ 100000：
- N = 100001（刚好超出上限）
- N = 0（刚好低于下限）
- N = -1（负数）
- N = 1000000000000（极大值）

### 步骤 2: 生成 Validator 代码
使用 testlib.h 实现：
```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    // 验证逻辑
    inf.readEof();
    return 0;
}
```

### 步骤 3: 评分
- 运行所有测试用例
- 计算得分（正确判断的比例）
- 选择得分最高的候选
"""

# Generator 构建提示词
GENERATOR_PROMPT = """
# Generator 构建指南

## 基于论文 Algorithm 2: BUILDGENERATORSUITE

### 参数格式
```
gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>
```

### 策略类型
- type=1 (tiny): 小数据穷举，N ≤ 10
- type=2 (random): 随机数据
- type=3 (extreme): 极端数据（溢出、精度、hash碰撞）
- type=4 (tle): TLE 诱导数据

### 代码模板
```cpp
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    rnd.setSeed(seed);
    // 生成逻辑
    return 0;
}
```

### 后处理
1. Validator 过滤
2. 去重（MD5 signature）
3. 平衡分布
4. 采样
"""

# Checker 构建提示词
CHECKER_PROMPT = """
# Checker 构建指南

## 基于论文 Algorithm 3: BUILDCHECKER

### 测试场景格式
```json
{
    "input": "输入数据",
    "contestant_output": "选手输出",
    "reference_output": "标准答案",
    "expected_verdict": "AC/WA/PE"
}
```

### 生成 40 个测试场景
- 正确答案场景
- 错误答案场景
- 格式错误场景
- 边界情况

### 代码模板
```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    // 比较逻辑
    if (jury == contestant) {
        quitf(_ok, "Correct");
    } else {
        quitf(_wa, "Wrong");
    }
}
```

### 评分
准确率 = 正确判断的场景数 / 总场景数
"""

# Interactor 构建提示词
INTERACTOR_PROMPT = """
# Interactor 构建指南

## 基于论文 Algorithm 4: BUILDINTERACTOR

### 变异类型
- 交换 </<=/>=
- off-by-one 错误
- 缺少检查
- 错误的 tie-break
- RNG 误用

### 评分指标
- pass_rate: 正确解通过的比例
- fail_rate: 变异解被拒绝的比例

### 代码模板
```cpp
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    // 交互逻辑
    std::cout << data << std::endl;
    std::cout.flush();
    int answer = ouf.readInt();
    // 验证
}
```

### 目标
- pass_rate = 100%（正确解必须通过）
- fail_rate > 80%（变异解应该被拒绝）
"""


def get_prompt(name: str) -> str:
    """
    获取提示词模板。

    Args:
        name: 提示词名称

    Returns:
        提示词内容
    """
    prompts = {
        "full_pipeline": FULL_PIPELINE_PROMPT,
        "test_generation": TEST_GENERATION_PROMPT,
        "validator": VALIDATOR_PROMPT,
        "generator": GENERATOR_PROMPT,
        "checker": CHECKER_PROMPT,
        "interactor": INTERACTOR_PROMPT,
    }
    return prompts.get(name, "")


def list_prompts() -> list[str]:
    """
    列出所有可用提示词。

    Returns:
        提示词名称列表
    """
    return [
        "full_pipeline",
        "test_generation",
        "validator",
        "generator",
        "checker",
        "interactor",
    ]
