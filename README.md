# AutoCode

[![PyPI version](https://img.shields.io/pypi/v/autocode-mcp.svg)](https://pypi.org/project/autocode-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/autocode-mcp.svg)](https://pypi.org/project/autocode-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**AutoCode 是面向竞赛编程出题人的 Claude Code plugin。**

它不是单纯让 AI “写一道题”，而是把 AI 生成的题面、解法、校验器、生成器、对拍、测试数据和打包流程放进一条可验证、可审计、会阻止跳步的出题流水线。

仓库内部包含 `autocode-mcp` 这个 MCP server 实现，但默认分发和使用形态是 Claude Code plugin：安装后会同时获得工作流 Agent、Skills、Hooks 和 20 个 MCP 原子工具。

## 为什么需要 AutoCode

AI 可以快速给出题目想法和代码，但竞赛题真正难的是“可信”。常见翻车点通常不在第一眼能看出来的地方：

- 题面描述含糊，输入输出协议不完整，样例和题意对不上。
- 样例输出算错，或者题面样例没有经过标准解实际验证。
- 标准解看起来合理，但边界条件有 bug。
- 时间复杂度判断过于乐观，`O(n^2)` 被误当成能过大数据。
- 暴力解和标准解同类实现，不能作为可靠 oracle 对拍。
- Generator 只有随机数据，覆盖不到边界、构造、极限和 TLE 场景。
- extreme 和 tle 样例只是“把参数放大”，没有语义差异。
- 错解没有被最终测试杀掉，数据强度不够。
- 打包前才发现题面、题解、manifest、样例和最终数据不一致。

AutoCode 的目标是把这些风险前置暴露，而不是等到出题完成后人工返工。

## AutoCode 如何兜底

AutoCode 把“AI 出题”拆成一组必须通过的质量门禁：

| 风险 | AutoCode 的处理方式 |
|------|---------------------|
| 题意不可判定、约束缺失 | `autocode-idea-auditor` 和 `idea-feasibility` 在实现前做只读审计 |
| 题面样例错误 | `problem_validate` 用解法验证题面样例与样例文件 |
| 标准解复杂度误判 | `solution_analyze` 输出复杂度、内存估计、风险提示和对拍建议 |
| 标准解或暴力解不可信 | `solution_audit_std` / `solution_audit_brute` 审计解法假设与 brute 能力 |
| std 有隐藏 bug | `stress_test_run` 用 sol/brute 多 profile 对拍 |
| 测试数据覆盖弱 | `problem_generate_tests` 生成 tiny/random/extreme/tle 多策略测试，并优先保证极限类比例 |
| type=3/type=4 语义重复 | `problem_verify_tests` 的 `limit_semantics` 检查极限/TLE 数据差异 |
| 错解未被杀 | `problem_verify_tests` 的 `wrong_solution_kill` 验证错解杀伤 |
| 工作流跳步 | Claude hooks 调用 `scripts/workflow_guard.py`，缺少前置步骤会直接拒绝工具调用 |
| 打包前状态不清 | `autocode.json` 记录题目契约，`autocode-verify` 可快速检查基础完整性 |

核心原则：**AI 负责生成候选内容，AutoCode 负责让每一步必须被验证。**

## 适合谁

AutoCode 适合：

- 想用 AI 加速出题，但担心题面、样例、数据和复杂度不可靠的出题人。
- 需要把题目从 idea 推到可打包 Polygon 结构的竞赛组织者。
- 希望 AI 严格遵守 Validator-Generator-Checker 流程的团队。
- 想在 Claude Code 中获得“完整出题工作流 Agent + 工具”的用户。

## 快速开始

### 前置要求

- Python 3.10+
- 支持 C++20 的 `g++`，推荐 GCC 10+
- Claude Code

`testlib.h` 已内置在 `src/autocode_mcp/templates/`，无需额外下载。

### 安装 Claude Code plugin

推荐通过 Claude Code marketplace 安装：

```bash
claude plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
claude plugin install autocode@autocode-marketplace
```

安装后插件会启用：

- 默认 Agent：`autocode-workflow`
- 审计 Agent：`autocode-idea-auditor`、`autocode-solution-auditor`、`autocode-package-auditor`
- 工作流 Skills：`autocode-workflow`、`idea-feasibility`、`solution-complexity-audit`、`stress-strategy`、`statement-audit`、`testdata-quality`
- Hooks：`SessionStart`、`PreToolUse`、`PostToolUse`
- MCP server：`autocode-mcp`

### 在 Claude Code 中使用

安装完成后，可以直接描述你的出题目标：

```text
用 AutoCode 创建一道竞赛编程题：给定数组，要求支持若干次区间查询。请先审计题意可行性，再按完整工作流生成题包。
```

工作流 Agent 会按门禁推进。如果你直接要求后置步骤，例如“现在打包”，但前置测试还没验证通过，hook 会拒绝并提示缺少哪一步。

## 工作流总览

非交互题主路径：

```text
problem_create
  -> solution_build(sol)
  -> solution_build(brute)
  -> solution_analyze / solution_audit_std / solution_audit_brute
  -> validator_build(accuracy >= 0.9)
  -> generator_build
  -> stress_test_run(completed_rounds == total_rounds)
  -> checker_build(需要特殊判题时)
  -> problem_validate
  -> problem_generate_tests
  -> problem_verify_tests(passed)
  -> problem_pack_polygon
```

交互题差异：

```text
validator_build / checker_build
  替换为 interactor_build
```

关键门禁：

- `brute` 必须在 `sol` 之后构建。
- 非交互题必须通过 `validator_build`，且 `accuracy >= 0.9`。
- 交互题必须先完成可用的 `interactor_build`。
- `stress_test_run` 必须完整跑完所有轮次。
- `problem_generate_tests` 前必须通过 `problem_validate`。
- `problem_pack_polygon` 前必须通过 `problem_verify_tests`，并满足门禁要求的结构化质量信号（如 `limit_semantics`、`wrong_solution_kill`、`validator_check`）。
- 生成最终测试后会自动清除旧的 `tests_verified` 状态，必须重新验证。

## 题目目录和 manifest

`problem_create` 会初始化标准题目目录：

```text
<problem_dir>/
├── autocode.json
├── solutions/
│   ├── sol.cpp
│   └── brute.cpp
├── files/
│   ├── gen.cpp
│   ├── val.cpp
│   ├── checker.cpp
│   ├── interactor.cpp
│   └── testlib.h
├── statements/
│   ├── README.md
│   └── tutorial.md
└── tests/
    ├── 01.in
    ├── 01.ans / 01.out
    └── .autocode_tests_manifest.json
```

`autocode.json` 是题目的可读契约，记录题名、是否交互、时空限制、题面路径、题解路径、解法角色和测试计划。示例：

```json
{
  "schema_version": "1.0",
  "problem_name": "Example Problem",
  "interactive": false,
  "time_limit_ms": 2000,
  "memory_limit_mb": 256,
  "statement_path": "statements/README.md",
  "tutorial_path": "statements/tutorial.md",
  "solutions": [
    {"name": "sol", "role": "main", "language": "cpp", "path": "solutions/sol.cpp"},
    {"name": "brute", "role": "brute", "language": "cpp", "path": "solutions/brute.cpp"}
  ],
  "case_plan": [
    {"name": "tiny-1", "type": "1", "seed": 1, "group": "sanity"},
    {"name": "random-1", "type": "2", "seed": 2, "group": "coverage"},
    {"name": "extreme-1", "type": "3", "seed": 3, "group": "limit"},
    {"name": "tle-1", "type": "4", "seed": 4, "group": "limit"}
  ]
}
```

可用 CLI 快速检查：

```bash
uv run autocode-verify examples/exact-sample
```

## 题面格式规范

默认题面 `statements/README.md` 应遵循固定顺序：

1. 题目
2. 时间/空间限制
3. 题目背景（可选）
4. 题目描述
5. 输入格式（必须包含所有变量范围与总规模约束）
6. 输出格式
7. 样例（多组样例按编号递增）
8. 说明（样例解释统一放在此处；只解释有代表性的样例即可）

## 测试数据质量

最终测试数据不是“生成了就算完成”。AutoCode 会要求 `problem_verify_tests` 通过，默认检查：

- `file_count`：每个 `.in` 都有对应答案文件，编号连续。
- `answer_consistency`：重新运行 `sol`，确认答案一致。
- `validator`：用 `val` 检查所有输入合法性。
- `no_empty`：没有空文件。
- `limit_ratio`：最终数据中 `type=3/4` 至少占一半。
- `limit_semantics`：`type=3` 和 `type=4` 不能高度重合。
- `wrong_solution_kill`：配置错解时，错解必须至少被一个测试点杀掉。

`problem_generate_tests` 支持：

- `answer_ext`：答案后缀，如 `.ans` 或 `.out`。
- `resume=true`：长任务中断后从 checkpoint 续跑。
- `hard_timeout_seconds`：工具级硬超时。
- `problem_cleanup_processes`：清理残留 generator PID 和状态。
- 当 `type=4` 使用 `extra_args`（如 `mode=tle_dense` / `mode=tle_chain`）且 generator 不兼容时，会自动尝试一次去掉 `extra_args` 的回退运行；结果中可通过 `generator_tle_extra_args_fallbacks` 查看回退次数。

实战注意事项：

- 使用 testlib validator 时，结束前必须调用 `inf.readEof()`；如果希望容忍尾部空白，推荐 `inf.seekEof(); inf.readEof();`。
- `stress_test_run` 会在返回中附带 `complexity_context`（来自 `.autocode-workflow/state.json`，由 `solution_analyze` / `solution_audit_brute` 等步骤写入）以及 `n_max_advisory`；**请由 LLM 根据证据与题意决定** `n_max` 等参数。兼容旧调用方时仍提供同内容的 `n_max_warning` 别名。
- 编写 brute 时必须直接模拟题目约束本身，避免把“必须同时满足的条件”误简化成“可任选子集”的模型。

## 工具列表

AutoCode 暴露 20 个 MCP 工具。一般用户不需要手动调用它们，`autocode-workflow` Agent 会按门禁顺序调用。

| 分组 | 工具 |
|------|------|
| 文件 | `file_read`, `file_save` |
| 解法 | `solution_build`, `solution_run`, `solution_analyze`, `solution_audit_std`, `solution_audit_brute` |
| 校验器 | `validator_build`, `validator_select` |
| 生成器 | `generator_build`, `generator_run` |
| 检查器/交互器 | `checker_build`, `interactor_build` |
| 对拍 | `stress_test_run` |
| 题目管理 | `problem_create`, `problem_validate`, `problem_generate_tests`, `problem_cleanup_processes`, `problem_verify_tests`, `problem_pack_polygon` |

所有工具返回统一结构：

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

## 示例目录

仓库包含三个用于验证 manifest 和文档结构的样例：

- `examples/exact-sample`：标准精确输出题。
- `examples/checker-sample`：特殊判题题。
- `examples/interactive-sample`：交互题。

这些样例主要用于展示 `autocode.json` 契约和 `autocode-verify` 检查，不是完整比赛题包。

## 直接使用 MCP server

Claude Code plugin 是推荐入口。如果你只想把 `autocode-mcp` 当作本地 MCP server 使用，可以用下面方式。

### 本地开发

```bash
uv sync
uv run autocode-mcp
```

### Cursor

```json
{
  "mcp": {
    "servers": {
      "autocode": {
        "command": "uvx",
        "args": ["autocode-mcp"]
      }
    }
  }
}
```

### OpenCode

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "autocode": {
      "type": "local",
      "command": ["uvx", "autocode-mcp"],
      "enabled": true
    }
  }
}
```

当前仅支持本地 stdio 传输，不支持 HTTP/SSE、远程连接。

## 贡献

查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献指南。

## 故障排查

查看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 了解常见问题和解决方案。

## 许可证

MIT License - 详见 [LICENSE](LICENSE)。

## 致谢

- 基于论文 ["AutoCode: LLMs as Problem Setters for Competitive Programming"](https://arxiv.org/abs/2510.12803)
- 使用 [testlib.h](https://github.com/MikeMirzayanov/testlib) 竞赛编程工具库

## 链接

- [文档](https://github.com/SZTU-ACM/AutoCode#readme)
- [PyPI](https://pypi.org/project/autocode-mcp/)
- [GitHub](https://github.com/SZTU-ACM/AutoCode)
- [Issue Tracker](https://github.com/SZTU-ACM/AutoCode/issues)
