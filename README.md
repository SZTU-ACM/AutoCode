# AutoCode

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**AutoCode 是面向竞赛编程出题人的 AI 出题工作台，支持 Claude Code、Codex、DeepSeek Harness (DSH) 与 Antigravity (`agy`)，原生覆盖标准题、特殊判题题、交互题与通信题。**

从一个想法开始，AutoCode 会协助你完成题面、解法、测试数据、验证和题包整理，把创作过程变成一条清晰、可靠、可复用的工作流。

## 为什么需要 AutoCode

AI 可以快速给出题目想法和代码，但竞赛题真正难的是让每个细节都经得起验证：

- 题面描述含糊，输入输出协议不完整，样例和题意对不上。
- 样例输出算错，或者题面样例没有经过标准解实际验证。
- 标准解看起来合理，但边界条件有 bug。
- 时间复杂度判断过于乐观，`O(n^2)` 被误当成能过大数据。
- 暴力解需要与标准解形成独立的交叉验证。
- 测试数据需要覆盖边界、构造、极限和性能场景。
- 错解需要在最终数据上得到充分检验。
- 题面、题解、样例和最终数据需要保持一致。

AutoCode 的目标是把这些风险前置暴露，而不是等到出题完成后人工返工。

## AutoCode 如何保障质量

AutoCode 会把出题过程拆成相互衔接的质量环节：

| 环节 | AutoCode 提供的帮助 |
|------|---------------------|
| 题意设计 | 检查题目是否清晰、可判定，约束和样例是否完整。 |
| 解法验证 | 复核正确性、复杂度和边界条件，并安排独立解法交叉验证。 |
| 数据构造 | 生成覆盖随机、边界、极限和性能场景的测试数据。 |
| 自动检验 | 检查输入合法性、答案一致性，并用测试数据检验错解。 |
| 题包整理 | 将题面、代码、数据和说明整理成可继续编辑或提交的结构。 |

核心原则：**AI 负责生成候选内容，AutoCode 负责让每一步必须被验证。**

## 适合谁

AutoCode 适合：

- 想用 AI 加速出题，但担心题面、样例、数据和复杂度不可靠的出题人。
- 需要把题目从 idea 推到 Polygon / Hydro / QOJ 可发布题包的竞赛组织者。
- 需要对已有外部题包进行独立只读验题（`autocode-audit --standalone`）的审题人与出题组。
- 希望 AI 遵循完整验证流程的团队。
- 想在 Claude Code、Codex、DeepSeek Harness (DSH) 或 Antigravity (`agy`) 中获得完整、可验证出题工作流的用户。

## 快速开始

### 前置要求

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- 支持 C++20 的 `g++`，推荐 GCC 10+
- Claude Code、Codex CLI、DeepSeek Harness (DSH) 或 Antigravity (`agy`)（至少安装一个）

常用的竞赛编程工具库已经随 AutoCode 一起提供。

### 安装 Claude Code 插件

推荐通过 Claude Code marketplace 安装：

```bash
claude plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
claude plugin install autocode@autocode-marketplace
```

安装完成后即可获得完整的出题工作流，以及题面、解法、测试数据和题包的自动质量检查。

### 安装 Codex 插件

在 Codex 中安装 AutoCode：

```bash
codex plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
codex plugin add autocode@autocode-marketplace
```

### 安装 DeepSeek Harness (DSH) 插件

在 DeepSeek Harness 中加载 AutoCode：

```bash
dsh plugin add ./.dsh-plugin
```

或者在启动时指定补丁配置：

```bash
dsh --patch ./.dsh-plugin/cordis.patch.yml
```

### 安装 Antigravity (`agy`) 插件

通过内置打包脚本生成 Antigravity 薄壳插件包并安装：

```bash
uv run python scripts/build_plugin_bundle.py --target agy --output /tmp/autocode-agy-plugin
```

打包产物在根目录包含 `plugin.json`、`mcp_config.json`、`rules/AGENTS.md`、`agents/`、`assets/` 与全套 `skills/`，共享 22 个 MCP 工具和 server 级工作流门禁。

### 独立只读验题（外部题包）

无需 `.autocode/` 目录，即可对任意 Polygon / Hydro / QOJ 题包运行只读验题与数据一致性检查：

```bash
uv run autocode-audit /path/to/problem_dir --standalone
```

### 开始使用

安装完成后，可以直接描述你的出题目标：

```text
用 AutoCode 创建一道竞赛编程题：给定数组，要求支持若干次区间查询。请先审计题意可行性，再按完整工作流生成题包。
```

AutoCode 会根据你的目标推进完整工作流，并在每个关键阶段进行质量检查。

## 工作流总览

AutoCode 会按以下顺序协助完成一题：

```text
题意与约束审查
  -> 标准解与独立验证解
  -> 校验器、数据生成器和判题组件
  -> 随机对拍与边界测试
  -> 最终数据质量检查
  -> 题面、代码、数据整理成题包
```

标准题（`standard`）、特殊判题题（`spj`）、交互题（`interactive`）与通信题（`communication`）都可以沿用这套流程：

- **交互题**：内置双进程双向管道 `InteractiveRunner`，自动检测死锁、超限与崩溃，完整覆盖交互协议、查询次数、flush 要求与异常退出。
- **通信题**：内置 `CommunicationRunner`，原生支持三类主流通信题模式——两阶段进程隔离（`run_twice`）、头文件/Grader 联合编译（`grader_linked`）与多方并发进程通信（`multi_process`），并在验题与审计阶段强制检查比特/字节/轮次/查询预算。
- **子任务与多 OJ 导出**：`manifest.json` 原生支持 `subtasks` 分值与依赖配置，`problem_pack_polygon` 支持通过 `export_formats=["polygon", "hydro", "qoj"]` 一键导出 Polygon（含 `<groups>`/`<tests>`）、Hydro（`exports/hydro/`）与 QOJ/UOJ（`exports/qoj/`）。

## 题目结构

每道题都会整理成清晰的目录，方便继续编辑、复核和提交：

```text
<problem>/
├── solutions/
│   ├── sol.cpp
│   └── brute.cpp
├── files/
│   ├── gen.cpp
│   ├── val.cpp
│   └── checker.cpp
├── statements/
│   ├── README.md
│   └── tutorial.md
└── tests/
    ├── 01.in
    └── 01.ans / 01.out
```

特殊判题题会加入 `files/checker.cpp`，交互题会加入 `files/interactor.cpp`，通信题会按模式加入 `files/communicator.cpp` 或 `files/grader.cpp` + `files/problem.h`，导出多 OJ 题包时生成在 `exports/hydro/` 与 `exports/qoj/` 下。

## 题面建议

一份完整的题面通常包含：

1. 题目
2. 时间/空间限制
3. 题目背景（可选）
4. 题目描述
5. 输入格式（必须包含所有变量范围与总规模约束）
6. 输出格式
7. 样例（多组样例按编号递增）
8. 说明（样例解释统一放在此处；只解释有代表性的样例即可）

## 质量保障

AutoCode 会在关键阶段自动复核：

- 题面、输入输出格式和样例是否互相一致。
- 标准解的正确性、复杂度和边界条件。
- 校验器是否覆盖题目约束，生成的数据是否有效。
- 标准解与独立验证解在多种场景下的结果是否一致。
- 随机、构造、边界、极限和性能数据是否形成有效覆盖。
- 代表性错解是否被最终测试识别。
- 题面、代码、答案和目录结构是否可以直接整理成题包。

## 示例目录

仓库包含四个不同题型的样例：

- `examples/exact-sample`：标准精确输出题。
- `examples/checker-sample`：特殊判题题。
- `examples/interactive-sample`：交互题。
- `examples/communication-sample`：两阶段（Run-Twice）噪声信道通信题（含子任务依赖、`communicator.cpp` 与标程）。

这些样例可以作为不同题型的目录和文件组织参考。

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
- [GitHub](https://github.com/SZTU-ACM/AutoCode)
- [Issue Tracker](https://github.com/SZTU-ACM/AutoCode/issues)
