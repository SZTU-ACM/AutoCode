"""
AutoCode MCP Prompts 模块。

提供预定义的出题工作流提示词模板。
"""

# 完整出题流程提示词
FULL_PIPELINE_PROMPT = """
# AutoCode 出题流程

> 完整的门控工作流与强制 gate（problem_create → … → problem_pack_polygon）以 `autocode-workflow` skill 为准；本提示为精简版步骤概览。

你是一个竞赛编程出题助手。请按照以下步骤完成题目创建：

## 1. 题面设计
- 确定题目核心算法
- 按固定顺序组织题面：
  1) 题目
  2) 时间/空间限制
  3) 题目背景（可选）
  4) 题目描述
  5) 输入格式（必须包含所有变量范围与总规模约束）
  6) 输出格式
  7) 样例（多组样例按编号递增）
  8) 说明（样例解释统一放在这里；只解释有代表性的样例）
- 明确输入输出格式与判定口径
- 设定完整数据范围和约束

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
- 先保证最终测试中至少一半是 extreme/tle（type=3/4，候选不足时尽量满足）
- 再平衡分布
- 采样
- 长任务期间避免发送新消息（可能中断 MCP 调用）；若中断，优先使用 resume/checkpoint 续跑

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
    // 若允许尾部空白，先调用 inf.seekEof() 再调用 inf.readEof()
    // 不能只调用 seekEof()，validator 结束前必须 readEof()
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
- 要求 type=3 与 type=4 分支有实质差异，type=4 应包含针对性卡法，不应仅靠 n_max/t_max 拉满

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
3. 先保证最终测试中 extreme/tle（type=3/4）不少于一半（候选不足时尽量满足）
4. 对剩余名额平衡分布
5. 采样
"""

# Checker 构建提示词
CHECKER_PROMPT = """
# Checker 构建指南

## 基于论文 Algorithm 3: BUILDCHECKER

### AutoCode 调用约定（与对拍一致）
- 命令行：`checker <input_file> <output_file> <answer_file>`（testlib `registerTestlibCmd` 顺序）。
- `autocode.json` 中 `special_judge: true` 且 `stress_comparison: "checker"` 时，`stress_test_run` 只调用一次
  `checker(input, sol输出文件, brute输出文件)`：把 **output** 当作选手输出、**answer** 当作暴力/参考输出；
  checker 应判定 sol 相对 brute 参考是否合法（多解时二者可文本不同）。
- `problem_verify_tests` 在同一配置下用 `checker(input, sol重跑输出, 磁盘标答文件)`；`special_judge` 但 `stress_comparison: "exact"` 时终测仍比字符串。
- 可选：`stress_checker_bidirectional: true` 时对拍再跑 `checker(in, brute, sol)`，仅当 checker 对交换 output/answer 仍语义正确时使用。

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

### 题面必须先写清的交互协议
- 本题是交互题，不是传统静态输入输出题；题面必须明确 judge 和选手谁先输出。
- 明确隐藏状态/输入范围、是否随机或自适应，以及始终成立的不变量。
- 明确每一种选手命令的格式、参数范围、judge 响应格式、响应值含义。
- 明确查询次数上限、总输出大小限制、最终答案格式、输出最终答案后是否必须立即结束。
- 明确每次输出后必须 flush；未 flush 可能导致阻塞、TLE 或 Idleness limit exceeded。
- 明确非法格式、越界查询、查询超限、提前 EOF、读到错误响应、继续输出等情况的 verdict。
- 样例应是 transcript：标出哪些行来自 judge，哪些行来自选手；不要把 transcript 当作普通 stdin/stdout 样例验证。

### testlib interactor 调用约定
- 使用 `registerInteraction(argc, argv)`，命令行是 `interactor <input-file> <output-file> [answer-file]`。
- 用 `inf` 读取测试输入或隐藏参数，用 `ans` 读取可选标准答案。
- 用 `tout` 向选手程序发送数据，写完每一次响应后 `tout.flush()`。
- 用 `ouf` 读取选手程序输出；`ouf.readInt(l, r)` 等范围检查失败应得到 WA/PE，不要手写不完整解析。
- 不要用 `std::cout` 向选手输出；testlib interactor 的选手输出通道是 `tout`。
- 所有 AC/WA/PE/FAIL 必须通过 `quitf(_ok/_wa/_pe/_fail, ...)` 明确退出。

### 变异类型
- 交换 </<=/>=
- off-by-one 错误
- 缺少检查
- 错误的 tie-break
- RNG 误用

### 评分指标
- interaction_scenarios accuracy: 脚本化协议场景的 verdict 匹配率，目标 100%
- pass_rate: 正确解通过的比例
- fail_rate: 变异解被拒绝的比例

### 脚本化交互场景格式
`interactor_build` 可用 `interaction_scenarios` 测 interactor 的协议判定能力：

```json
{
    "input": "测试输入文件内容",
    "answer": "可选标准答案文件内容",
    "contestant_output": "模拟选手完整输出",
    "expected_verdict": "AC/WA/PE/FAIL/TLE"
}
```

必须覆盖：
- 合法完整交互；
- 非法命令 token；
- 参数越界；
- 查询次数刚好上限与超过上限；
- 错误最终答案；
- 提前 EOF 或多余输出；
- 需要 flush 的每个交互回合。

### 代码模板
```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int secret = inf.readInt();
    int queries = 0;
    const int MAX_Q = 20;
    while (true) {
        std::string op = ouf.readToken();
        if (op == "?") {
            if (++queries > MAX_Q) quitf(_wa, "too many queries");
            int x = ouf.readInt(1, 100);
            int response = (x < secret ? -1 : (x > secret ? 1 : 0));
            tout << response << '\\n';
            tout.flush();
        } else if (op == "!") {
            int answer = ouf.readInt(1, 100);
            if (answer == secret) quitf(_ok, "accepted in %d queries", queries);
            quitf(_wa, "wrong final answer");
        } else {
            quitf(_pe, "unknown command");
        }
    }
}
```

### 常见错误
- 用 `cout` 代替 `tout`，导致本地/Polygon testlib interactor 通道错误。
- 题面没有写 query limit 或非法输出后果，导致选手不知道何时停止。
- 只测试正确解，不测试越界查询、超限查询、错误 final answer。
- interactor 对非法 token 死循环或等待更多输入，而不是给出 WA/PE。
- 选手输出最终答案后 interactor 没有立即 `quitf`，继续等待导致挂起。

### 目标
- 脚本化交互场景 accuracy = 100%
- pass_rate = 100%（正确解必须通过）
- fail_rate > 80%（变异解应该被拒绝）
"""

DIFFICULTY_PROMPT = """
# Problem Difficulty Rating

> 难度评级口径（rating 800–3500、档位、证据要求、置信度）以 `problem-difficulty-rating` skill 为准；本提示为精简版说明。

你要根据 `problem_audit` 返回的确定性 signals 给出难度评级说明。

## 必须引用的证据
- `estimated_complexity`
- `algorithm_tags`
- `constraint_scale`
- `implementation_evidence`
- `data_strength`
- `risk_report` 中的 warnings

## 输出要求
- 给出一个 CF-style rating（800 到 3500 之间）
- 给出难度档位：入门 / 基础 / 中等 / 较难 / 困难 / 高难
- 解释为什么不是更低，也不是更高
- 如果证据不足，明确标注 provisional / 需要人工复核

## 约束
- 不要编造提交统计、通过率历史或外部校准数据
- 不要把主观印象写成事实
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
        "difficulty_rating": DIFFICULTY_PROMPT,
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
        "difficulty_rating",
    ]
