# AutoCode

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**AutoCode 是一套确定性的竞赛编程验题引擎与多宿主 AI 出题工作台，原生覆盖标准题（`standard`）、特殊判题题（`spj`）、交互题（`interactive`）与通信题（`communication`）。**

它提供两条可独立使用的主入口：
1. **零 AI 依赖 · 独立题包验题与质量审计引擎（`autocode-audit --standalone`）**：对任意 Polygon / Hydro / QOJ / 国内常见结构（`data/ + std.cpp`）题包执行**零写入只读**质量体检，覆盖题面与 validator 约束一致性、实证复杂度拟合、ASan/UBSan 探针、C++ 单点变异测试、错解杀伤倍率与多 OJ 回读校验。
2. **多宿主 AI 出题工作台（`autocode-mcp`）**：面向 Claude Code、Codex、DeepSeek Harness (`dsh`) 与 Antigravity (`agy`) 的薄壳插件，共享 22 个 MCP 工具与 server 级强制工作流门禁，让 AI 负责生成候选内容、AutoCode 负责每一步必须被验证。

---

## 为什么需要 AutoCode

竞赛题目真正难的不是写出初稿，而是让每个细节都经得起赛前检验：

- 题面描述或数据范围与 `val.cpp` 实际校验上下界对不上。
- 样例输出算错，或交互/通信题面缺少超时、超限、EOF 与刷新协议说明。
- 标准解隐藏边界 bug / 未定义行为（UB），或时间复杂度判断过于乐观（`O(n^2)` 误过中等测试点）。
- 暴力解与标程缺少独立交叉验证，或终测数据过水、杀不掉近正解单点错解。
- 导出 Polygon / Hydro / QOJ 题包时子任务、时限单位（ms vs s）或文件名发生漂移。

AutoCode 的核心原则：**不靠感觉放行，把这些风险前置为确定性的自动检查与门禁。**

## 适合谁

- **赛前审题人与命题组**：用 `autocode-audit --standalone` 直接对人工或 AI 产出的外部题包做独立验题与数据强度审计（无需 `.autocode/` 目录，也不会污染原题包）。
- **个人出题人与竞赛教练**：把题目从 idea 推进到可直接导出的 Polygon / Hydro / QOJ 题包。
- **多宿主 AI 开发者**：在 Claude Code、Codex、DeepSeek Harness (`dsh`) 或 Antigravity (`agy`) 中获得同一套可验证出题工作流。

---

## 快速开始

### 前置要求

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- 支持 C++20 的 `g++`（推荐 GCC 10+；macOS 如使用 Homebrew GCC 可在 `manifest.json` 中配置 `compiler`，否则优先避免 `<bits/stdc++.h>`）

### 入口一：独立验题与审计 CLI（无需 AI 插件，不走 PyPI）

通过 `uv tool install` 直接从 GitHub 仓库或 GitHub Releases wheel 安装独立命令行工具（`autocode-audit`、`autocode-verify`）：

```bash
# 从 GitHub tag 一键安装（推荐）
uv tool install git+https://github.com/SZTU-ACM/AutoCode.git@v3.2.1

# 或者在本仓库内直接运行
uv sync
```

对任意外部题包运行**零写入只读审计**（支持 `auto` / `autocode` / `standard_cn` / `polygon` / `hydro` 布局、可选 `--mutation-check` 单点变异测试，以及 `--format json|markdown|summary`，默认 `json`）：

```bash
# 输出适合人类阅读 / GitHub Step Summary 的 Markdown 审题报告并落盘 JSON
autocode-audit /path/to/problem_dir --standalone --layout auto --mutation-check --format markdown --report audit_report.json
```

要在自己的题目仓库 / 出题 PR 中直接接入 Problem CI，可复制 [examples/ci/autocode-problem-audit.yml](examples/ci/autocode-problem-audit.yml) 到 `.github/workflows/autocode-audit.yml`：每次 PR 自动零写入审题并把 Markdown 报告写入 GitHub Step Summary（`0=go`，`2=no_go` 自动拦阻）。

快速检查 AutoCode 题包目录、编译产物新鲜度与工作流门禁状态：

```bash
autocode-verify /path/to/problem_dir --status --strict-gates
```

### 入口二：安装 AI 宿主插件

所有宿主共享同一套 MCP Server（22 个工具）与工作流门禁：

- **Claude Code**：
  ```bash
  claude plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
  claude plugin install autocode@autocode-marketplace
  ```
- **Codex**：
  ```bash
  codex plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
  codex plugin add autocode@autocode-marketplace
  ```
- **DeepSeek Harness (DSH)**：
  ```bash
  dsh plugin add ./.dsh-plugin
  # 或启动时指定补丁：dsh --patch ./.dsh-plugin/cordis.patch.yml
  ```
- **Antigravity (`agy`)**：
  从 GitHub Releases 下载预构建的 `autocode-agy-bundle`，或通过内置打包脚本生成薄壳插件包：
  ```bash
  uv run python scripts/build_plugin_bundle.py --target agy --output /tmp/autocode-agy-plugin
  ```
  打包产物在根目录包含 `plugin.json`、`mcp_config.json`、`rules/AGENTS.md`、`agents/`、`assets/` 与全套 `skills/`。

安装后即可直接描述出题或审计需求：

```text
用 AutoCode 创建一道竞赛编程题：给定数组，要求支持若干次区间查询。请先审计题意可行性，再按完整工作流生成题包。
```

---

## 核心能力与工作流总览

```text
题意与约束审查
  -> 标准解与独立验证解（含静态分析 + 实证复杂度拟合）
  -> 校验器、数据生成器和判题组件（Checker / Interactor / Communicator / Grader）
  -> 随机对拍与边界测试（可选 isolated ASan/UBSan 探针）
  -> 最终数据生成与质量验证（去重、规模分布、错解杀伤倍率）
  -> 完整审计与多 OJ 打包（Polygon / Hydro / QOJ + 导出回读校验）
```

- **交互题（`interactive`）**：内置双进程双向管道 `InteractiveRunner`，自动检测死锁、超限、崩溃转 `RE` 与仅限制选手进程的 CPU/RSS 资源。
- **通信题（`communication`）**：内置 `CommunicationRunner`，原生支持三类模式——两阶段进程隔离（`run_twice`）、头文件/Grader 联合编译（`grader_linked`）与多方并发进程通信（`multi_process`），强制检查比特/字节/轮次/查询预算。
- **子任务与多 OJ 导出**：`manifest.json` 原生支持 `subtasks` 分值与依赖；`problem_pack_polygon` 支持 `export_formats=["polygon", "hydro", "qoj"]` 与 `export_data_mode="hardlink"|"copy"|"zip"`，并在导出后自动回读校验。
- **跨平台细粒度缓存**：基于源文件 SHA-256 的门禁失效机制与相对路径 `.autocode/runtime.json`，跨平台迁移后纯重编译保留历史审计与对拍状态。

---

## 题目目录结构与题面规范

```text
<problem>/
├── .autocode/
│   ├── manifest.json
│   └── runtime.json
├── solutions/
│   ├── sol.cpp
│   └── brute.cpp
├── files/
│   ├── gen.cpp
│   ├── val.cpp
│   └── checker.cpp        # 或 interactor.cpp / communicator.cpp / grader.cpp + problem.h
├── statements/
│   ├── README.md
│   └── tutorial.md
└── tests/
    ├── 01.in
    └── 01.ans / 01.out
```

`statements/README.md` 强制采用以下章节顺序（所有样例解释统一放在「说明」中，约束须完整写入「输入格式」）：
1. 题目 2. 时间/空间限制 3. 题目背景（可选） 4. 题目描述 5. 输入格式 6. 输出格式（交互/通信题附协议说明） 7. 样例 8. 说明

---

## 活样例仓库

仓库包含 4 个完整、可直接编译验题的端到端样例（均使用跨平台可移植的标准 C++20 头文件）：

- [examples/exact-sample](examples/exact-sample)：标准精确输出题（含 `int32` 溢出错解对照）。
- [examples/checker-sample](examples/checker-sample)：特殊判题题（SPJ，双合法构造解 + 贪心错解对照）。
- [examples/interactive-sample](examples/interactive-sample)：交互题（二分标程 + 分块独立验证解 + 超查询次数错解 + testlib `interactor.cpp`）。
- [examples/communication-sample](examples/communication-sample)：两阶段（`run_twice`）噪声信道通信题（含子任务依赖、`communicator.cpp`、标程与无纠错错解）。

---

## 更多文档与链接

- 开发与门禁契约参考：[CLAUDE.md](CLAUDE.md)
- 贡献指南：[CONTRIBUTING.md](CONTRIBUTING.md)
- 故障排查：[TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- 许可证：MIT License - 详见 [LICENSE](LICENSE)
- 致谢：基于论文 [AutoCode: LLMs as Problem Setters for Competitive Programming](https://arxiv.org/abs/2510.12803) 与 [testlib.h](https://github.com/MikeMirzayanov/testlib)
- 仓库与问题反馈：[SZTU-ACM/AutoCode](https://github.com/SZTU-ACM/AutoCode) · [Issues](https://github.com/SZTU-ACM/AutoCode/issues)
