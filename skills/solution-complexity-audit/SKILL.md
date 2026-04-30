---
name: solution-complexity-audit
description: Audit std/brute assumptions with MCP evidence, including worst/average complexity risk and stress readiness.
disable-model-invocation: false
---

# Solution Complexity Audit

用于标准解/暴力解完成后的“实现审计阶段”。必须给出结构化证据，不接受口头判断。

## 执行顺序

按以下顺序调用并记录结果：

1. `solution_analyze`：估算 std 的时间/空间复杂度，记录 worst/average 风险。
2. `solution_audit_std`：核对 `claimed_complexity` 与估算复杂度、约束是否冲突。
3. `solution_audit_brute`：确认 brute 适合对拍并推导 `n_max`、`trials`。

## 必查项

- std 是否存在 `high_tle_risk` 或明显边界漏洞。
- brute 是否真的用于“保守正确性校验”，而不是和 std 同类实现。
- 复杂度结论是否与题面约束匹配（尤其 `n_max`/总规模）。
- 是否输出了可直接执行的 `stress_profiles` 建议。

## 通过标准（全部满足）

- std 无高风险 `high_tle_risk`。
- brute 给出明确 `recommended_stress_params`。
- 对拍参数写入后续 `stress_test_run` 调用计划。

## 输出格式

- `decision`: `go` / `no_go`
- `findings`: 结构化问题列表（含 severity）
- `recommended_stress_params`: 推荐对拍参数

## 失败处理

- 若出现高风险项，先修复算法或约束，再重新跑全套审计。
- 不允许跳过此审计直接生成最终测试数据。
