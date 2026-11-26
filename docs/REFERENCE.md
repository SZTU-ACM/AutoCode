# AutoCode 参考文档

本文档汇集 AutoCode 的出题工作流门禁、交互题与通信题开发契约、题目目录与 `manifest.json` 规范、题面格式要求、测试数据质量门禁、22 个 MCP 原子工具列表，以及进阶 CLI 与本地开发说明。

> 返回产品概览与快速开始请参阅 [README.md](../README.md)。

---

## 1. 自动出题工作流与门禁详解

标准题 / Special Judge 题自动出题主路径：

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
  -> problem_pack_polygon(polygon / hydro / qoj / icpc_kattis / cms)
```

交互题差异：

```text
validator_build / checker_build  替换为  interactor_build
```

独立自动验题路径（无需 `.autocode/` 目录，零写入只读）：

```text
外部题包 (Polygon problem.xml / Hydro / QOJ / standard_cn)
  -> autocode-audit --standalone --layout auto --sandbox auto
  -> 结构与约束审计 + ASan/UBSan + 复杂度实测 + 错解矩阵 + 可选单点变异测试
  -> 输出 decision=go / no_go (JSON / Markdown / Summary)
```

### 关键强制门禁

- `brute` 必须在 `sol` 之后构建。
- 非交互题必须通过 `validator_build`，且 `accuracy >= 0.9`。
- 交互题必须先完成可用的 `interactor_build`，并提供协议覆盖完整的 `interaction_scenarios`。
- `stress_test_run` 必须完整跑完所有轮次。
- `problem_generate_tests` 前必须通过 `problem_validate`。
- `problem_pack_polygon` 前必须通过 `problem_verify_tests`，并满足门禁要求的结构化质量信号（如 `limit_semantics`、`wrong_solution_kill`、`validator_check`）。
- 生成最终测试后会自动清除旧的 `tests_verified` 状态，必须重新验证。
- 若在 `manifest.json` 中设置 `audit_gates.require_full_audit=true`，`problem_pack_polygon` 会要求最近一次 `problem_audit(mode=full)` 为 `go` 后才允许打包导出。

---

## 2. 交互题与通信题开发规范

### 交互题契约

交互题不是简单地“没有标准输入输出”。`statements/README.md` 必须把协议写成可执行契约：谁先输出、隐藏参数范围、每种查询和最终答案的格式、judge 响应含义、查询次数上限、每次输出后的 flush、最终答案后的退出规则，以及非法格式、越界参数、查询超限、提前 EOF、未 flush/阻塞时的判定。样例应写成交互 transcript，明确哪些行来自 judge、哪些行来自选手。

`files/interactor.cpp` 必须使用 testlib 的 `registerInteraction(argc, argv)`：用 `inf` 读测试输入，用可选 `ans` 读参考数据，用 `std::cout` 向选手管道发送交互数据并在每次响应后执行 `std::cout.flush()`（或 `std::flush` / `std::endl`），用 `ouf` 读选手输出，用 `tout`（`argv[2]`）写入可选的诊断日志（不会发送给选手），所有分支用 `quitf(_ok/_wa/_pe/_fail, ...)` 结束。

`interactor_build` 支持 `interaction_scenarios`，可用脚本化 `contestant_output` 验证 interactor 对 AC、错误答案、非法命令、越界查询、查询超限和提前 EOF 的判定。

### 通信题与多语言支持

- 原生支持三类通信模式：两阶段进程隔离（`run_twice`）、头文件与 Grader 联合编译（`grader_linked`）、以及多方并发进程通信（`multi_process`）。
- 内置 `CommunicationRunner` 自动执行通信协议校验，强制核验比特、字节、轮次与查询次数预算。
- `lang_runner.py` 原生支持 5 种语言评测（`cpp`、`python`、`pypy`、`rust`、`java`）及分语言时限倍率，并支持通过 `--sandbox` / `manifest.sandbox` 启用 `bwrap` / `nsjail` / `isolate` / `apple_container` 隔离。

---

## 3. 题目目录与 `manifest.json`

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

可用 CLI 快速检查目录结构、编译产物与工作流门禁状态：

```bash
uv run autocode-verify examples/exact-sample
autocode-verify /path/to/problem_dir --status --strict-gates
```

---

## 4. 题面格式规范

默认题面 `statements/README.md` 应遵循固定顺序：

1. 题目
2. 时间/空间限制
3. 题目背景（可选）
4. 题目描述
5. 输入格式（必须包含所有变量范围与总规模约束）
6. 输出格式（交互/通信题附完整协议说明）
7. 样例（多组样例按编号递增）
8. 说明（样例解释统一放在此处；只解释有代表性的样例即可）

---

## 5. 测试数据质量与进阶自动验题

`problem_verify_tests` 与 `problem_audit` 默认检查以下质量门禁：

- `file_count`：每个 `.in` 都有对应答案文件，编号连续。
- `answer_consistency`：用 `sol` 重跑每个 `.in`。若 `manifest.json` 启用 `special_judge: true` 且 `stress_comparison: "checker"`，则用 testlib checker 比对标答；否则与答案文件逐字比对。
- `validator`：用 `val` 检查所有输入合法性。
- `no_empty`：没有空文件。
- `limit_ratio`：最终数据中 `type=3/4` 至少占一半。
- `limit_semantics`：`type=3` 和 `type=4` 不能高度重合，并支持 `test_compactor` 去冗。
- `wrong_solution_kill`：对 manifest 中 `role=wrong` 的解法跑终测，支持精确期望判定 `expected_verdict`（`AC|WA|TLE|MLE|RE|PE`）或 `expected`（`fail`/`pass`），要求至少一测命中预期异常或按严格规则拦截。
- **进阶验题能力**：隔离 ASan/UBSan/MSan/TSan 多 Sanitizer UB 探针、AST/调用图与硬件计数器（`perf` / `cachegrind`）复杂度拟合、C++ 单点与 AST 变异测试（`--mutation-check`）、`llvm-cov`/`gcov` 分支覆盖率与反例缩减、可选 Z3 SMT 约束校验与 `constraints.h` 双向生成、Orchid 四维歧义与原创性查重、以及导出 Polygon / Hydro / QOJ / ICPC-Kattis / CMS 后的自动回读校验。

`problem_generate_tests` 常用参数：

- `answer_ext`：答案后缀，如 `.ans` 或 `.out`。
- `resume=true`：长任务中断后从 checkpoint 续跑。
- `hard_timeout_seconds`：工具级硬超时。
- `problem_cleanup_processes`：清理残留 generator PID 和状态。
- 当 `type=4` 使用 `extra_args`（如 `mode=tle_dense` / `mode=tle_chain`）且 generator 不兼容时，会自动尝试一次去掉 `extra_args` 的回退运行。

实战注意事项：

- 使用 testlib validator 时，结束前必须调用 `inf.readEof()`；如果希望容忍尾部空白，推荐 `inf.seekEof(); inf.readEof();`。
- `stress_test_run` 会在返回中附带 `complexity_context` 与 `n_max_advisory`，便于结合复杂度实测与题意合理设定对拍规模。
- 编写 brute 时必须直接模拟题目约束本身，避免把“必须同时满足的条件”误简化成与标程同构的模型。

---

## 6. MCP 工具列表与统一返回结构

AutoCode 暴露 22 个 MCP 原子工具，附带 `ToolAnnotations`、Pydantic 内联 `outputSchema` / `structuredContent` 与异步 `MCP Tasks`（以及 3 个零 CDN `ui://autocode/*` MCP Apps 交互视图）。日常自动出题时无需手动调用，`autocode-workflow` Agent 会按门禁顺序自动调度。

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

`problem_audit` 聚合题面、manifest、工作流状态、终测质量信号、错解杀伤矩阵与难度评级证据，输出 `decision=go/no_go`、`blocking_issues`、`risk_report`、`quality_signals`、`difficulty_signals` 与 `next_actions`：

```bash
# 对 AutoCode 题目工作区执行全量审计
autocode-audit <problem_dir> --mode full --format markdown --report audit_report.json

# 对任意外部题包执行零写入独立验题
autocode-audit <problem_dir> --standalone --layout auto --sandbox auto --mutation-check --strict --format markdown
```

---

## 7. 附加 CLI、可选增强与本地开发

### 附加 CLI 与评测套件

- **Polygon REST 同步**：`uv run python -m autocode_mcp.cli.polygon_sync --help`（凭据仅从环境变量读取，支持拉取与预检推送）。
- **Typst PDF 题面渲染**：`uv run python -m autocode_mcp.cli.render_pdf <problem_dir>`（将 Markdown 题面/题解转为 `.typ` 并调用 `typst` 编译 PDF）。
- **进化测试数据与技能优化**：`scripts/evolve_generator.py` 与 `scripts/optimize_skills_gepa.py`。
- **离线基准评测套件**：`uv run python evals/standalone_benchmark.py --format summary`（详见 [evals/README.md](../evals/README.md)）。

### 可选增强依赖

`testlib.h` 与 `jngen.h` 已内置在 `src/autocode_mcp/templates/`，无需额外下载。如需启用 `tree-sitter-cpp` AST 语义变异（`--extra ast`）与 `z3-solver` SMT 形式化约束分析（`--extra formal`），可在插件或源码环境的 `uv` 中执行：

```bash
uv sync --extra ast --extra formal
# 或安装全部可选扩展
uv sync --all-extras
```

### 本地开发与启动

插件资产与 `autocode-mcp` 源码同仓管理，默认使用标准 `stdio` 传输，同时支持本地无状态 Streamable HTTP 传输：

```bash
uv sync --all-extras
uv run autocode-mcp

# 可选：启动本地无状态 Streamable HTTP 传输（默认绑定 127.0.0.1:8765，含 Host/Origin 防护）
uv run autocode-mcp --transport http --host 127.0.0.1 --port 8765
```

更多贡献规范与测试细节请参阅 [CLAUDE.md](../CLAUDE.md)。
