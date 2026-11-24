# AutoCode

[![Release](https://img.shields.io/github/v/release/SZTU-ACM/AutoCode?color=blue)](https://github.com/SZTU-ACM/AutoCode/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/SZTU-ACM/AutoCode/ci.yml?branch=master&label=CI)](https://github.com/SZTU-ACM/AutoCode/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**AutoCode 是面向 ACM / OI 竞赛编程的自动出题与自动验题工具，原生支持标准题、Special Judge、交互题与通信题。**

它围绕两条核心主线展开：

1. **自动出题（AI 辅助命题流水线）**：接入 Claude Code、Codex、DeepSeek Harness (`dsh`) 或 Antigravity (`agy`) 后，将 AI 生成的题面、解法、校验器、生成器、对拍、测试数据与多 OJ 打包放进一条可验证、可审计、会阻止跳步的出题流水线。
2. **自动验题（零 AI 依赖的独立验题引擎）**：通过命令行 `autocode-audit --standalone` 直接对任意现有题包（Polygon / Hydro / QOJ / 国内常见 `data/ + std.cpp` 布局）执行只读质量审计与数据强度检测，不修改原题包目录，也可直接接入命题仓库的 GitHub Actions PR 审题流。

仓库内部包含 `autocode-mcp` MCP Server（22 个原子工具）及 `autocode-audit`、`autocode-verify` 独立命令行工具，所有受支持的 AI 客户端共享同一套工作流 Agent、Skills 与服务端门禁。

## 为什么需要 AutoCode

AI 可以快速给出题目想法和代码，但竞赛题真正难的是“可信”。无论是 AI 自动出题还是人工命题，常见翻车点往往不在第一眼能看出来的地方：

- 题面描述含糊，输入输出协议不完整，或数据范围与 `val.cpp` 实际校验上下界对不上。
- 样例输出算错，或者题面样例没有经过标程与校验器实际验证。
- 标程看起来合理，但隐藏数组越界、整数溢出或未定义行为（UB）。
- 时间复杂度判断过于乐观，实测为 $O(n^2)$ 却被误当成 $O(n \log n)$，或暴力解靠常数小蒙混过关。
- 暴力解与标程同类实现，不能作为可靠 oracle 对拍。
- Generator 只有随机数据，覆盖不到边界、构造、极限和 TLE 场景，或 `extreme` 与 `tle` 样例只是把参数放大而无语义差异。
- 错解没有被终测数据杀掉，或测试数据存在覆盖缺口、无法拦截标程的单点边界变异。
- 交互题缺少刷新/死锁/超限校验，通信题未严格核验轮次与比特/字节预算。
- 导出 Polygon / Hydro / QOJ 题包前才发现题面、题解、manifest、子任务、时限单位（`ms` vs `s`）或判题组件不一致。

AutoCode 的目标是把这些风险前置暴露，而不是等到出题完成甚至开赛后再人工返工。

## AutoCode 如何兜底

AutoCode 把“自动出题”和“自动验题”拆成一组必须通过的质量门禁：

| 风险 | AutoCode 的处理方式 |
|------|---------------------|
| 题意不可判定、约束缺失 | `autocode-idea-auditor` 和 `idea-feasibility` 在实现前做只读审计 |
| 题面样例错误、范围漂移 | `problem_validate` 与审计器核验题面范围与 `val.cpp` 一致性，并用标程实际验证样例 |
| 标程复杂度误判或隐藏 UB | `solution_analyze` 结合多规模实测拟合时间复杂度，支持隔离 ASan / UBSan 探针排查越界与未定义行为 |
| 标程或暴力解不可信 | `solution_audit_std` / `solution_audit_brute` 审计解法假设与暴力独立性 |
| 标程存在边界 bug | `stress_test_run` 用 `sol` / `brute` 多 profile 自动对拍 |
| 测试数据覆盖弱 | `problem_generate_tests` 生成 `tiny / random / extreme / tle` 多策略数据，并保证极限类比例 |
| `type=3` / `type=4` 语义重复 | `problem_verify_tests` 的 `limit_semantics` 检查极限与 TLE 数据差异 |
| 错解未被杀掉或数据存在薄弱点 | `wrong_solution_kill` 统计所有错解拦截结果；`--mutation-check` 对标程做单点变异测试定位数据覆盖缺口 |
| 交互死锁或通信预算越界 | `InteractiveRunner` 检测双进程死锁与异常退出；`CommunicationRunner` 原生校验 `run_twice` / `grader_linked` / `multi_process` 的比特、字节与轮次预算 |
| AI 出题工作流跳步 | 服务端门禁与客户端 hooks 协同拦截，缺少前置验证步骤会直接拒绝后置调用 |
| 现有外部题包赛前复核困难 | `autocode-audit --standalone` 零 AI 依赖只读审题，支持多布局自动识别与 PR 自动门禁 |
| 导出多 OJ 格式漂移 | `problem_pack_polygon` 支持导出 Polygon / Hydro / QOJ 并在导出后自动回读校验 |

核心原则：**AI 负责生成候选内容，AutoCode 负责让每一步必须被验证。**

## 适合谁

AutoCode 适合：

- **需要自动出题的命题人**：想用 AI 加速出题，但要求题面、样例、复杂度、对拍、测试数据与错解拦截全部经过实测验证。
- **赛前审题人与命题组**：想用 `autocode-audit --standalone` 对人工或 AI 产出的外部题包做独立只读自动验题，或接入 GitHub PR 自动审题门禁。
- **竞赛组织者与教练**：需要将题目从 idea 稳定推进到可直接导入 Polygon / Hydro / QOJ 的标准题包。
- **多客户端 AI 开发者**：希望在 Claude Code、Codex、DeepSeek Harness (`dsh`) 或 Antigravity (`agy`) 中使用同一套可验证的命题工作流。

## 快速开始

### 前置要求

- Python 3.10+
- [`uv`](https://docs.astral.sh/uv/)
- 支持 C++20 的 `g++`（推荐 GCC 10+；macOS 如使用 Homebrew GCC 可在 `manifest.json` 中配置 `compiler`）

`testlib.h` 已内置在 `src/autocode_mcp/templates/`，无需额外下载。

### 自动出题：安装 AI 插件

四个受支持的 AI 客户端统一通过官方插件分发仓库 [`SummerOneTwo/autocode-marketplace`](https://github.com/SummerOneTwo/autocode-marketplace) 安装，共享同一套 MCP Server（22 个原子工具）与工作流门禁：

#### 1. Claude Code

```bash
claude plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
claude plugin install autocode@autocode-marketplace
```

#### 2. Codex

```bash
codex plugin marketplace add https://github.com/SummerOneTwo/autocode-marketplace.git
codex plugin add autocode@autocode-marketplace
```

#### 3. DeepSeek Harness (`dsh`)

```bash
git clone https://github.com/SummerOneTwo/autocode-marketplace.git
dsh plugin add ./autocode-marketplace/plugins/autocode-dsh/.dsh-plugin
```

#### 4. Antigravity (`agy`)

```bash
git clone https://github.com/SummerOneTwo/autocode-marketplace.git
mkdir -p ~/.gemini/config/plugins && rm -rf ~/.gemini/config/plugins/autocode
cp -R ./autocode-marketplace/plugins/autocode-agy ~/.gemini/config/plugins/autocode
```

安装完成后，各客户端均会启用同一套 AutoCode 能力：

- 工作流与审计 Agent：`autocode-workflow`、`autocode-idea-auditor`、`autocode-solution-auditor`、`autocode-package-auditor`
- 工作流 Skills：`autocode-workflow`、`idea-feasibility`、`solution-complexity-audit`、`stress-strategy`、`statement-audit`、`testdata-quality`
- 流程门禁：服务端工具链门禁及各客户端对应的 Hooks / Rules
- MCP Server：`autocode-mcp`（22 个原子工具）

#### 让 AI 帮你安装（或更新）

不想手动敲命令？可以把下面这段直接复制发给当前使用的 AI 客户端（Claude Code、Codex、DeepSeek Harness 或 Antigravity），它会按所处客户端从 `autocode-marketplace` 自动完成安装或更新：

```text
请帮我从官方插件分发仓库 https://github.com/SummerOneTwo/autocode-marketplace.git 为当前使用的 AI 客户端安装或更新 AutoCode 插件：
1. 判断当前客户端类型并执行对应安装或更新步骤：
   - Claude Code：添加 marketplace `https://github.com/SummerOneTwo/autocode-marketplace.git`，安装/更新 `autocode@autocode-marketplace`
   - Codex：添加 marketplace `https://github.com/SummerOneTwo/autocode-marketplace.git`，添加/更新 `autocode@autocode-marketplace`
   - DeepSeek Harness (dsh)：克隆或拉取最新 `autocode-marketplace`，挂载 `plugins/autocode-dsh/.dsh-plugin`
   - Antigravity (agy)：克隆或拉取最新 `autocode-marketplace`，将 `plugins/autocode-agy` 复制同步至 `~/.gemini/config/plugins/autocode`
2. 检查前置环境（Python 3.10+、`uv`、`g++`）是否就绪
3. 安装完成后，简要告诉我如何开始第一道竞赛编程题
```

#### 开始自动出题

安装完成后，可以直接描述你的出题目标：

```text
用 AutoCode 创建一道竞赛编程题：给定数组，要求支持若干次区间查询。请先审计题意可行性，再按完整工作流生成题包。
```

工作流 Agent 会按门禁推进。如果直接要求后置步骤（例如“现在打包”），但前置对拍或终测验证尚未通过，服务端门禁与 hook 会拒绝并明确提示缺少哪一步。

### 自动验题：独立命令行工具（零 AI 依赖）

无需配置任何 AI 客户端，可直接安装独立命令行工具（`autocode-audit`、`autocode-verify`）：

```bash
# 从 GitHub Release Tag 直接安装（推荐）
uv tool install git+https://github.com/SZTU-ACM/AutoCode.git@v3.3.0

# 或者从 GitHub Releases 下载预构建 wheel 离线安装
# uv tool install autocode_mcp-3.3.0-py3-none-any.whl
```

对任意外部题包执行**只读自动验题**（支持 `--layout auto|autocode|standard_cn|polygon|hydro`、可选 `--mutation-check` 单点变异测试，以及 `--format json|markdown|summary`）：

```bash
autocode-audit /path/to/problem_dir \
  --standalone --layout auto --mutation-check \
  --format markdown --report audit_report.json
```

若要在命题仓库的 Pull Request 中自动执行审题门禁，可直接复制 [examples/ci/autocode-problem-audit.yml](examples/ci/autocode-problem-audit.yml) 到 `.github/workflows/autocode-audit.yml`：每次 PR 自动只读审题并将 Markdown 报告写入 GitHub Step Summary（`0=go` 放行，`2=no_go` 拦阻）。

> [!WARNING]
> **PR CI 安全边界**：`autocode-audit` 会编译并执行题包中的 C++ 源码（属于不可信 PR 代码执行面）。请**仅**在一次性的（ephemeral）GitHub-hosted runner（如 `ubuntu-latest`）上运行不受信任的 PR 审题，**切勿**在带有持久状态或特权权限的 `self-hosted` runner 上运行，**切勿**向审题 Job 注入敏感 Secrets，且**切勿**将触发器从 `pull_request` 改为 `pull_request_target`。

对 AutoCode 工作区快速检查目录结构、编译产物与工作流门禁状态：

```bash
autocode-verify /path/to/problem_dir --status --strict-gates
```

## 工作流总览

标准题 / 特殊判题题自动出题主路径：

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
  -> problem_audit(mode=full, decision=go)
  -> problem_pack_polygon(polygon / hydro / qoj)
```

交互题差异：

```text
validator_build / checker_build
  替换为 interactor_build
```

交互题不是简单地“没有标准输入输出”。`statements/README.md` 必须把协议写成可执行契约：谁先输出、隐藏参数范围、每种查询和最终答案的格式、judge 响应含义、查询次数上限、每次输出后的 flush、最终答案后的退出规则，以及非法格式、越界参数、查询超限、提前 EOF、未 flush/阻塞时的判定。样例应写成交互 transcript，明确哪些行来自 judge、哪些行来自选手。

`files/interactor.cpp` 必须使用 testlib 的 `registerInteraction(argc, argv)`：用 `inf` 读测试输入，用可选 `ans` 读参考数据，用 `std::cout` 向选手管道发送交互数据并在每次响应后执行 `std::cout.flush()`（或 `std::flush` / `std::endl`），用 `ouf` 读取选手输出，用 `tout`（`argv[2]`）写入可选的诊断日志（不会发送给选手），所有分支用 `quitf(_ok/_wa/_pe/_fail, ...)` 结束。`interactor_build` 支持 `interaction_scenarios`，可用脚本化 `contestant_output` 验证 interactor 对 AC、错误答案、非法命令、越界查询、查询超限和提前 EOF 的判定。

通信题支持：

- 原生支持三类通信模式：两阶段进程隔离（`run_twice`）、头文件与 Grader 联合编译（`grader_linked`）、以及多方并发进程通信（`multi_process`）。
- 内置 `CommunicationRunner` 自动执行通信协议校验，强制核验比特、字节、轮次与查询次数预算。

独立自动验题路径（无需 `.autocode/` 目录，零写入只读）：

```text
外部题包 (Polygon / Hydro / QOJ / standard_cn)
  -> autocode-audit --standalone --layout auto
  -> 结构与约束审计 + ASan/UBSan + 复杂度实测 + 错解矩阵 + 可选单点变异测试
  -> 输出 decision=go / no_go (JSON / Markdown / Summary)
```

关键门禁：

- `brute` 必须在 `sol` 之后构建。
- 非交互题必须通过 `validator_build`，且 `accuracy >= 0.9`。
- 交互题必须先完成可用的 `interactor_build`，并提供协议覆盖完整的 `interaction_scenarios`。
- `stress_test_run` 必须完整跑完所有轮次。
- `problem_generate_tests` 前必须通过 `problem_validate`。
- `problem_pack_polygon` 前必须通过 `problem_verify_tests`，并满足门禁要求的结构化质量信号（如 `limit_semantics`、`wrong_solution_kill`、`validator_check`）。
- 生成最终测试后会自动清除旧的 `tests_verified` 状态，必须重新验证。

## 题目目录和 manifest

`problem_create` 会初始化标准题目目录：

```text
<problem_dir>/
├── .autocode/
│   ├── manifest.json
│   └── runtime.json
├── solutions/
│   ├── sol.cpp
│   └── brute.cpp
├── files/
│   ├── gen.cpp
│   ├── val.cpp
│   ├── checker.cpp        # 或 interactor.cpp / communicator.cpp / grader.cpp + problem.h
│   └── testlib.h
├── statements/
│   ├── README.md
│   └── tutorial.md
└── tests/
    ├── 01.in
    └── 01.ans / 01.out
```

运行期副产物（workflow 状态、测试 manifest、生成 checkpoint、审计结果）统一收口到 `<problem_dir>/.autocode/runtime.json`，且 `.autocode/` 通过自身内部的 `.gitignore`（`*`）自忽略，题目根目录不产生 `.gitignore`。

`manifest.json` 是题目的可读契约，记录题名、题型、时空限制、子任务（`subtasks`）、编译器配置、题面路径、解法角色和测试计划。示例：

```json
{
  "schema_version": "1.0",
  "problem_name": "Example Problem",
  "interactive": false,
  "special_judge": false,
  "stress_comparison": "exact",
  "time_limit_ms": 2000,
  "memory_limit_mb": 256,
  "statement_path": "statements/README.md",
  "tutorial_path": "statements/tutorial.md",
  "constraints": {"n_max": 200000},
  "solutions": [
    {"name": "sol", "role": "main", "language": "cpp", "path": "solutions/sol.cpp", "expected": "pass"},
    {"name": "brute", "role": "brute", "language": "cpp", "path": "solutions/brute.cpp", "expected": "pass"},
    {"name": "wrong_int32", "role": "wrong", "language": "cpp", "path": "solutions/wrong_int32.cpp", "expected": "fail"}
  ],
  "case_plan": [
    {"name": "tiny-1", "type": "1", "seed": 1, "group": "sanity"},
    {"name": "random-1", "type": "2", "seed": 2, "group": "coverage"},
    {"name": "extreme-1", "type": "3", "seed": 3, "group": "limit"},
    {"name": "tle-1", "type": "4", "seed": 4, "group": "limit"}
  ]
}
```

可用 CLI 快速校验：

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
6. 输出格式（交互/通信题附完整协议说明）
7. 样例（多组样例按编号递增）
8. 说明（样例解释统一放在此处；只解释有代表性的样例即可）

## 测试数据质量与自动验题

最终测试数据不是“生成了就算完成”。AutoCode 要求 `problem_verify_tests` 与 `problem_audit` 通过，默认检查：

- `file_count`：每个 `.in` 都有对应答案文件，编号连续。
- `answer_consistency`：用 `sol` 重跑每个 `.in`。若 `manifest.json` 启用 `special_judge: true` 且 `stress_comparison: "checker"`，则用 testlib checker 比对标答；否则与答案文件逐字比对。
- `validator`：用 `val` 检查所有输入合法性。
- `no_empty`：没有空文件。
- `limit_ratio`：最终数据中 `type=3/4` 至少占一半。
- `limit_semantics`：`type=3` 和 `type=4` 不能高度重合。
- `wrong_solution_kill`：对 manifest 中 `role=wrong` 的解法跑终测。默认 `expected` 为 `fail`，要求至少一测未通过 checker 或与 `.ans` 不一致；若某条目标解在终测上应合法，可设 `expected: "pass"`，此时要求全部测例通过。
- **进阶自动验题能力**：支持隔离 ASan / UBSan 标程检测、多规模实证复杂度拟合、C++ 单点变异测试（`--mutation-check`）识别薄弱测试点，以及导出至 Polygon / Hydro / QOJ 后的自动回读校验。

`problem_generate_tests` 支持：

- `answer_ext`：答案后缀，如 `.ans` 或 `.out`。
- `resume=true`：长任务中断后从 checkpoint 续跑。
- `hard_timeout_seconds`：工具级硬超时。
- `problem_cleanup_processes`：清理残留 generator PID 和状态。
- 当 `type=4` 使用 `extra_args`（如 `mode=tle_dense` / `mode=tle_chain`）且 generator 不兼容时，会自动尝试一次去掉 `extra_args` 的回退运行。

实战注意事项：

- 使用 testlib validator 时，结束前必须调用 `inf.readEof()`；如果希望容忍尾部空白，推荐 `inf.seekEof(); inf.readEof();`。
- `stress_test_run` 会在返回中附带 `complexity_context` 与 `n_max_advisory`，便于结合复杂度实测与题意合理设定对拍规模。
- 编写 brute 时必须直接模拟题目约束本身，避免把“必须同时满足的条件”误简化成与标程同构的模型。

## 工具列表

AutoCode 暴露 22 个 MCP 原子工具。日常自动出题时无需手动调用，`autocode-workflow` Agent 会按门禁顺序自动调度。

| 分组 | 工具 |
|------|------|
| 文件 | `file_read`, `file_save` |
| 解法 | `solution_build`, `solution_run`, `solution_analyze`, `solution_audit_std`, `solution_audit_brute` |
| 校验器 | `validator_build`, `validator_select` |
| 生成器 | `generator_build`, `generator_run` |
| 检查器/交互器 | `checker_build`, `interactor_build` |
| 对拍 | `stress_test_run` |
| 题目管理 | `problem_create`, `problem_validate`, `problem_generate_tests`, `problem_cleanup_processes`, `problem_verify_tests`, `problem_audit`, `problem_pack_polygon`, `problem_build_all` |

所有工具返回统一结构：

```json
{
  "success": true,
  "data": {},
  "error": null
}
```

`problem_audit` 聚合题面、manifest、工作流状态、终测质量信号、错解杀伤矩阵与难度评级证据，输出 `decision=go/no_go`、`blocking_issues`、`risk_report`、`quality_signals`、`difficulty_signals` 与 `next_actions`。命令行入口：

```bash
# 对 AutoCode 题目工作区执行全量审计
autocode-audit <problem_dir> --mode full --format markdown --report audit_report.json

# 对任意外部题包执行零写入独立验题
autocode-audit <problem_dir> --standalone --layout auto --mutation-check --format markdown
```

若在 `manifest.json` 中设置 `audit_gates.require_full_audit=true`，`problem_pack_polygon` 会要求最近一次 `problem_audit(mode=full)` 为 `go` 后才允许打包导出。

## 示例目录

仓库包含 4 个跨平台可移植（标准 C++20）的端到端样例与 1 个 Problem CI 模板：

- [examples/exact-sample](examples/exact-sample)：标准精确输出题（含 `int32` 溢出错解对照）。
- [examples/checker-sample](examples/checker-sample)：特殊判题题（SPJ，双合法构造解 + 贪心错解对照）。
- [examples/interactive-sample](examples/interactive-sample)：交互题（二分标程 + 分块独立验证解 + 超查询次数错解 + `interactor.cpp`）。
- [examples/communication-sample](examples/communication-sample)：两阶段（`run_twice`）噪声信道通信题（含子任务依赖、`communicator.cpp`、标程与无纠错错解）。
- [examples/ci/autocode-problem-audit.yml](examples/ci/autocode-problem-audit.yml)：命题仓库 GitHub Actions PR 自动审题工作流模板。

## 本地开发

插件资产与 `autocode-mcp` 源码同仓管理。本地启动 server 用于开发调试：

```bash
uv sync
uv run autocode-mcp
```

当前仅支持本地 stdio 传输，不支持 HTTP/SSE 或远程连接。更多开发者规范与架构说明请参阅 [CLAUDE.md](CLAUDE.md)。

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
- [Releases](https://github.com/SZTU-ACM/AutoCode/releases)
- [Changelog](CHANGELOG.md)
- [Issue Tracker](https://github.com/SZTU-ACM/AutoCode/issues)
