---
name: testdata-quality
description: Verify final tests with hard quality gates: integrity, consistency, validator, limit semantics, and wrong-solution kill.
disable-model-invocation: false
---

# Test Data Quality Skill

最终测试数据必须通过：

1. `problem_verify_tests` 的 `file_count` / `answer_consistency` / `validator` / `no_empty`。
2. `limit_ratio` 与 `limit_semantics`（type=3 与 type=4 不可语义重合）。
3. `wrong_solution_kill`：每个错解至少被一个测试点杀掉。

## 必查补充

- 测试点是否有明显重复（低信息增益）。
- type=3 与 type=4 是否语义区分，而非仅参数放大。
- 参考解/错解的通过与失败是否符合预期角色。

## 输出模板

- `decision`: `go` / `no_go`
- `failed_checks`: 失败项列表（含原因）
- `coverage_report`: 规模分布、极限比例、错解杀伤统计
- `repair_priority`: 修复优先级（P0/P1/P2）
- `reverify_plan`: 修复后重验顺序
