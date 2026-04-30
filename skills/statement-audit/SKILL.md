---
name: statement-audit
description: Audit statement, tutorial, and samples for consistency and publication readiness before packaging.
disable-model-invocation: false
---

# Statement Audit Skill

使用 `problem_validate` 检查题面与样例文件，确保题目文档可直接用于比赛。

## 检查点

- 输入输出协议完整，无歧义。
- 样例输入输出与 `sol` 实际运行一致。
- `tutorial.md` 包含思路、正确性、复杂度，不是占位文本。
- 特殊判题（checker/interactor）规则在题面中明确说明。

## 额外要求

- 术语与变量命名在题面、题解、代码中保持一致。
- 样例应覆盖至少一个边界场景。
- 若是交互题，题面必须写明交互时序、刷新/结束条件、非法输出后果。

## 输出格式

- `decision`: `go` / `no_go`
- `doc_issues`: 文档问题清单
- `sample_issues`: 样例问题清单
- `rewrite_targets`: 需要重写的段落标题
