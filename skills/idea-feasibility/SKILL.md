---
name: idea-feasibility
description: Use before coding to decide whether a problem idea is judgeable, implementable, and verifiable.
disable-model-invocation: false
---

# Idea Feasibility Skill

用于“立项前审题”。目标是尽早发现不可判题、不可验证、约束缺失等高风险问题，避免进入代码阶段后返工。

## 触发条件

- 用户只给了题目想法，还没稳定输入输出协议。
- 约束或判题方式模糊（尤其是多解题/交互题）。
- 团队准备开始写 `sol/brute/generator/validator` 前。

## 检查清单

1. **可判定性**：答案是否唯一；若不唯一，是否能用 checker 明确定义合法解。
2. **约束完备性**：`n_max`、值域、组数、总规模（如 `sum_n`）是否明确。
3. **可验证性**：是否能设计覆盖边界、极限、反例的测试；是否可复现（seed + type）。
4. **实现可行性**：是否存在显然不可实现或超时风险的要求。
5. **交互可行性（如适用）**：交互协议是否完整、是否可本地模拟。

## 禁止行为

- 不要直接进入代码生成。
- 不要用“后续补充”替代关键约束。

## 必做输出

- `decision`: `go` / `no_go`
- `blocking_issues`: 阻塞问题列表（必须修复）
- `required_clarifications`: 需向用户确认的关键问题（最多 3 条，按优先级）
- `next_actions`: 进入实现前的最小动作清单
