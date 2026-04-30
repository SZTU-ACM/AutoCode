---
name: stress-strategy
description: Build multi-profile stress tests from brute complexity and constraints, then execute with traceable evidence.
disable-model-invocation: false
---

# Stress Strategy Skill

目标：根据 brute 复杂度和约束自动形成“可复现、可解释”的多轮对拍方案，而不是只跑单一随机轮次。

## 规则

- 至少包含 3 个 profile：`tiny_exhaustive`、`random_small`、`edge_small`。
- `tiny_exhaustive` 用 `type=1`；`random_small` 用 `type=2`；`edge_small` 用 `type=3/4`。
- 当 brute 复杂度高于 `O(n^2)` 时，自动下调 `n_max` 与 `trials`。
- 每个 profile 必须可复现（固定 seed 规则、参数可回放）。

## 执行

调用 `stress_test_run` 时传 `stress_profiles`，并记录每个 profile 的完成轮数与失败点。

## 输出要求

- `decision`: `go` / `no_go`
- `stress_profiles`: 每个 profile 的 `name/trials/types/generator_args`
- `execution_summary`: 每个 profile 的完成情况与失败轮次
- `next_fix_hint`: 若失败，给出优先修复方向（std/brute/generator/validator）
