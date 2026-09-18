# AutoCode

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**AutoCode 是面向竞赛编程出题人的 AI 出题工作台，支持 Claude Code、Codex 和 DeepSeek Harness (DSH)。**

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
- 需要把题目从 idea 推到可打包 Polygon 结构的竞赛组织者。
- 希望 AI 遵循完整验证流程的团队。
- 想在 Claude Code、Codex 或 DeepSeek Harness (DSH) 中获得完整、可验证出题工作流的用户。

## 快速开始

### 前置要求

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- 支持 C++20 的 `g++`，推荐 GCC 10+
- Claude Code、Codex CLI 或 DeepSeek Harness (DSH)（至少安装一个）

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

普通题、特殊判题题和交互题都可以沿用这套流程。对于交互题，AutoCode 会额外关注交互协议、查询限制、错误行为和程序结束条件，帮助你把协议写得清楚、测得充分。

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

特殊判题题会加入判题程序，交互题会加入交互程序，其余结构保持一致。

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

仓库包含三个不同题型的样例：

- `examples/exact-sample`：标准精确输出题。
- `examples/checker-sample`：特殊判题题。
- `examples/interactive-sample`：交互题。

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
