# CLAUDE.md

此文件为 Claude Code Agent 在本仓库工作时提供项目级指导。

## 项目定位

AutoCode 是一个面向 Claude Code、Codex、DeepSeek Harness (DSH) 与 Antigravity (`agy`) 的多宿主插件系统，面向竞赛编程出题工作流。仓库内部同时包含 `autocode-mcp` MCP server；各宿主共享 22 个 MCP 工具、Skills 与 server 级门禁，Claude 额外使用 Agent 和 hooks，Antigravity 使用薄壳 `.antigravity-plugin/` + `rules/AGENTS.md`。

AutoCode 的核心目标是将人工智能生成的题面、解法、validator、generator、checker/interactor/communicator/grader、对拍、测试数据与多 OJ（Polygon / Hydro / QOJ）打包纳入可验证、可审计且具备严格阶段校验的出题流程，覆盖标准题（`standard`）、特殊判题（`spj`）、交互题（`interactive`）与通信题（`communication`：`run_twice` / `grader_linked` / `multi_process`）。

重点风险：

- AI 题面含糊、样例错误或题解不一致。
- 标准解有隐藏 bug，复杂度判断过于乐观。
- brute 不能作为可靠 oracle。
- 测试数据覆盖不到边界、极限、TLE 和错解杀伤。
- 生成最终数据后未重新验证就打包。

## 开发命令

```bash
# 安装依赖
uv sync

# 运行核心测试
uv run pytest tests/ -q

# 代码检查
uv run ruff check .

# 类型检查
uv run mypy src/

# 校验 Claude plugin 结构（若本机安装了 Claude CLI）
claude plugin validate .

# 校验多宿主 manifest、版本同步与可复现 bundle（marketplace / agy / dsh）
uv run python scripts/sync_plugin_version.py --check
uv run pytest tests/test_plugin_manifest.py tests/test_plugin_bundle.py -q
uv run python scripts/build_plugin_bundle.py --target marketplace --output /tmp/autocode-bundle
uv run python scripts/build_plugin_bundle.py --target marketplace --check --output /tmp/autocode-bundle
uv run python scripts/build_plugin_bundle.py --target agy --output /tmp/autocode-agy-bundle
uv run python scripts/build_plugin_bundle.py --target agy --check --output /tmp/autocode-agy-bundle
uv run python scripts/build_plugin_bundle.py --target dsh --output /tmp/autocode-dsh-bundle
uv run python scripts/build_plugin_bundle.py --target dsh --check --output /tmp/autocode-dsh-bundle

# 运行 MCP Server（本地开发/测试）
uv run autocode-mcp

# 校验题目 manifest 与统一工作流门禁状态（可选 --status / --strict-gates / --reconcile）
uv run autocode-verify examples/exact-sample
uv run autocode-verify examples/communication-sample
uv run autocode-verify <problem_dir> --status --strict-gates
uv run autocode-verify <problem_dir> --reconcile

# 运行完整题包审计（或对任意外部题包运行只读 --standalone 审计，支持布局适配、变异强度检测与 --format markdown 报告）
uv run autocode-audit examples/exact-sample --mode full --report audit_report.json
uv run autocode-audit /path/to/external-problem --standalone --layout auto --mutation-check --format markdown

# 构建 wheel 并运行打包/CLI 冒烟测试（隔离虚拟环境 wheel 安装烟测由 CI test-packaging 守护；不发布 PyPI）
uv build
uv run pytest tests/test_e2e_mcp.py -m packaging -q
```

## 项目结构

```text
AutoCode/
├── .claude-plugin/        # Claude plugin manifest
├── .codex-plugin/         # Codex plugin manifest
├── .dsh-plugin/           # DeepSeek Harness (DSH) plugin bundle & configuration
├── .antigravity-plugin/   # Antigravity (agy) thin-shell plugin manifest
├── rules/                 # Antigravity (agy) AGENTS.md rules
├── agents/                # Claude / Antigravity plugin agent definitions
├── hooks/                 # Claude hook config
├── scripts/               # Hook/runtime helper & bundle/version sync scripts
├── skills/                # Shared skills (Claude / Codex / DSH / Antigravity)
├── examples/              # exact / checker / interactive / communication smoke samples
├── src/autocode_mcp/      # MCP server 源代码
│   ├── cli/               # autocode-verify / autocode-audit CLI
│   ├── tools/             # 22 个 MCP 工具实现
│   ├── templates/         # 内置模板（testlib.h、interactor、communicator、grader、problem.h 等）
│   ├── workflow/          # manifest.json 模型与权威门禁（enforcement.py / guard.py）
│   └── utils/             # 编译、JudgeRunner、CommunicationRunner、Hydro/QOJ 导出器等
├── tests/                 # 测试用例（含真实 C++ 交互/通信端到端测试）
├── .mcp.json              # 本地 MCP 接入配置（开发/兼容用）
├── mcp_config.json        # Antigravity (agy) MCP 配置（bundle 根输出）
├── settings.json          # Claude plugin 默认 agent
├── scripts/build_plugin_bundle.py # Multi-target bundle builder (marketplace / agy / dsh)
└── pyproject.toml         # Python package / scripts（唯一版本真源）
```

## 工具列表

AutoCode 当前暴露 22 个 MCP 工具：

| 分组 | 工具 |
|------|------|
| 文件 | `file_read`, `file_save` |
| 解法 | `solution_build`, `solution_run`, `solution_analyze`, `solution_audit_std`, `solution_audit_brute` |
| Validator | `validator_build`, `validator_select` |
| Generator | `generator_build`, `generator_run` |
| Checker / Interactor | `checker_build`, `interactor_build` |
| Stress | `stress_test_run` |
| Problem | `problem_create`, `problem_validate`, `problem_generate_tests`, `problem_cleanup_processes`, `problem_verify_tests`, `problem_audit`, `problem_pack_polygon`, `problem_build_all` |

## 题目目录结构

`problem_create` 初始化后的目录布局：

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
│   ├── checker.cpp
│   ├── interactor.cpp
│   └── testlib.h
├── statements/
│   ├── README.md
│   └── tutorial.md
└── tests/
    ├── 01.in
    └── 01.ans / 01.out
```

## 题面格式规范（强制）

后续生成 `statements/README.md` 时，统一采用如下顺序：

1. 题目
2. 时间/空间限制
3. 题目背景（可选）
4. 题目描述
5. 输入格式（必须完整包含所有变量范围与总规模约束）
6. 输出格式
7. 样例（多个样例按编号递增）
8. 说明（所有样例解释统一放在此处；仅需解释有代表性的样例）

额外要求：

- 不要把样例解释写在样例块旁边或其它章节。
- 输入格式章节必须出现完整约束，不得只在题目描述中零散给出。

## 强制工作流

该顺序由 `src/autocode_mcp/workflow/enforcement.py` 和 MCP server 实际强制执行；Claude 的 `hooks/hooks.json` / `scripts/workflow_guard.py` 只提供提前提示与兼容适配，Codex 不依赖 hooks。

1. `problem_create`（支持 `problem_type`: `standard` | `spj` | `interactive` | `communication`，`protocol_mode`: `interactive_1v1` | `run_twice` | `multi_process` | `grader_linked`，`oracle_mode`: `brute` | `interactor_only` | `reference_solutions`）
2. `solution_build(solution_type="sol")`（`grader_linked` 模式自动与 `files/grader.cpp` 和 `files/problem.h` 联合编译；`multi_process` 支持多角色 `participant_solutions`）
3. `solution_build(solution_type="brute")`（当 `oracle_mode == "brute"` 时必选；交互/通信题设为 `oracle_mode="interactor_only"` 或 `"reference_solutions"` 时可跳过 brute 门禁）
4. 解法审计：`solution_analyze`、`solution_audit_std`、`solution_audit_brute`（`oracle_mode == "brute"` 时必选）
5. 判题/校验组件：
   - 标准题 / SPJ：`validator_build(accuracy >= 0.9)`（`special_judge=true` + `stress_comparison="checker"` 时可在对拍前先 `checker_build`）。
   - 交互题 / 通信题（`run_twice` / `multi_process`）：`interactor_build`（可搭配可选 `validator_build` 校验 `.in` 测试输入；禁止 `checker_build`）。
   - 通信题（`grader_linked`）：编写 `files/grader.cpp` 与 `files/problem.h` 后通过 `solution_build` 联合编译（可选 `validator_build`）。
6. `generator_build`
7. `checker_build` 与 `stress_test_run`：非 SPJ 须在 `stress_test_run(completed_rounds == total_rounds)` **之后**再 `checker_build(accuracy >= 0.9)`。支持一键 `enable_sanitizers=True` 在隔离的 `.autocode/san_bin/` 中自动探测并运行 ASan/UBSan（Linux 预加载容器自动回退 `-static-libasan -static-libubsan` 并强制通过负对照探针）。交互题通过 `InteractiveRunner` 真实双向管道运行；通信题通过 `CommunicationRunner` 按 `run_twice`（独立进程隔离多阶段）/ `grader_linked` / `multi_process` 运行并校验通信预算。
8. `problem_validate(validation_passed)`（交互/通信题自动验证协议与实跑样例；超长预览自动截断并将完整详情落盘 `.autocode/logs/`）
9. `problem_generate_tests(generated_test_count > 0)`（支持 `mode="replace"|"append"|"register_existing"`，自定义生成器类型如 `"5"`/`"hack"`，自动保留 `locked: true` 与 `tests/hack/*.in` 人工 Hack 测试点）
10. `problem_verify_tests(passed)`（默认跑齐当前 `manifest.json` 门禁要求的全部检查项，支持 `verify_types=["all"]` 与 `boundary_coverage`；分项重跑按数据/源码内容指纹合并更新 `verify_signals` 而不覆盖其它已通过项；自动扫描并编译缺失/过期的 `solutions/wrong_*.cpp`，报告 `wrong_ms / std_ms` 倍率与 `max_memory_mb`；建议在常规错解外额外构造 2~3 份**近正解单点变异错解**检验数据强度）
11. `problem_audit(mode="full")`（收集最终 `go`/`no_go`、质量信号、基于 LaTeX 数值范围与 `val.cpp` 上下界比对的 `statement_consistency`、按 `(sol, gen, config, platform)` 哈希缓存的 `empirical_complexity`、导出包回读一致性检查、`state_drift` 跨平台告警、`communication_metrics`、`host_performance_bias` 与难度证据；不适用自测标记 `status: "not_applicable"`；支持 `standalone=True` / `autocode-audit --standalone` 对任意外部题包只读审计）
12. `problem_pack_polygon`（统一推荐 `time_limit_ms` 毫秒参数，严禁误传毫秒到废弃秒参数 `time_limit`：超出 `60s` 或相对 `manifest.time_limit_ms` 偏离 >10 倍默认拒绝；支持 `export_formats=["polygon", "hydro", "qoj"]` 与 `export_data_mode="hardlink"|"copy"|"zip"`；Hydro 不自动注入内部工具 tag；导出完成后自动回读校验 Polygon/Hydro/QOJ 的 TL、ML 与文件完整性）

关键门禁：

- `brute` 必须在 `sol` 之后构建（除非 `oracle_mode != "brute"`）。`workflow` 门禁按文件 SHA-256 细粒度缓存：仅修改/重编 `sol.cpp` 只失效 `sol` 侧门禁（`solution_analyze` / `solution_audit_std`），不失效未改动的 `solution_audit_brute`；跨平台迁移目录后若源文件 SHA-256 未变，纯重编译（`solution_build` / `problem_build_all`）保留已完成的代码分析、审计、对拍与数据生成状态，仅需重新验证二进制与打包。`.autocode/runtime.json` 统一保存相对路径（`.`），不写入宿主绝对路径。
- `validator_build` 没有有效 `accuracy` 不算通过。
- 交互题/通信题不可运行 `checker_build`；允许可选运行 `validator_build` 校验 `.in` 文件；`interactive` 与 `run_twice`/`multi_process` 使用 `interactor_build`。
- 交互题题面必须完整写明协议：谁先输出、隐藏参数范围、查询/最终答案格式、judge 响应含义、查询上限、flush 要求、最终答案后的退出规则，以及非法格式、越界参数、查询超限、提前 EOF、未 flush/阻塞和额外输出的判定。样例必须是 transcript，不能伪装成普通 stdin/stdout。
- `files/interactor.cpp` 必须使用 `registerInteraction(argc, argv)`；用 `inf`/`ans` 读输入与参考数据，用 `std::cout` + `std::flush`（或 `std::endl`）向选手管道发送交互数据，用 `ouf` 读选手输出，用 `tout` 写可选诊断日志到 `argv[2]`。`interactor_build` 应带 `interaction_scenarios` 覆盖 AC、WA、PE、越界、超限和 EOF。
- 通信题题面必须包含 `## 通信协议`（写明模式、物理进程隔离或函数接口签名、最大比特/字节/轮次/查询预算、信道模型与超限判定）与多阶段 transcript 样例；`run_twice` 的 `files/communicator.cpp` 使用 `registerTestlibCmd(argc, argv)`（严格 4 参数）并通过环境变量 `AUTOCODE_COMM_STAGE` / `AUTOCODE_COMM_ROLE` 区分阶段。
- 最终测试生成后会清除旧的 `tests_verified`，必须重新跑 `problem_verify_tests`。
- `problem_pack_polygon` 前必须完成最终测试验证。
- 若 `audit_gates.require_full_audit=true`，`problem_pack_polygon` 前必须有最近一次通过的 full audit。
- `special_judge: true` 且 `stress_comparison: "checker"` 时：`stress_test_run` 前须 `checker_build` 通过；`problem_verify_tests` 的终测/错解用 checker。仅 `special_judge` 而 `stress_comparison: "exact"` 时终测仍比字符串。可选 `stress_checker_bidirectional: true` 使对拍再验证 `checker(in,brute,sol)`（checker 须支持对称语义）。

## Agent 与 Skill

默认 Agent：

- `agents/autocode-workflow.md`：主流程协调与门禁执行。

只读审计 Agent：

- `agents/autocode-idea-auditor.md`：立项前审查题意是否可判定、可验证、可生成。
- `agents/autocode-solution-auditor.md`：std/brute 复杂度、正确性假设和对拍策略审计。
- `agents/autocode-package-auditor.md`：打包前检查题面、测试、错解杀伤和 manifest 一致性。

核心 Skills：

- `skills/autocode-workflow/SKILL.md`
- `skills/idea-feasibility/SKILL.md`
- `skills/solution-complexity-audit/SKILL.md`
- `skills/stress-strategy/SKILL.md`
- `skills/statement-audit/SKILL.md`
- `skills/testdata-quality/SKILL.md`
- `skills/problem-validate/SKILL.md`
- `skills/problem-difficulty-rating/SKILL.md`

## Manifest

每个题目应维护 `manifest.json`（位于 `.autocode/`）作为人类和 CI 都可读的题目配置约定。模型位于 `src/autocode_mcp/workflow/`，模板位于 `src/autocode_mcp/templates/manifest.json`。

常用字段补充：

- `problem_type`（`standard` | `spj` | `interactive` | `communication`）、`oracle_mode`（`brute` | `interactor_only` | `reference_solutions`）、`protocol`（`mode`、`passes`、`pass_headers`、`max_bits`、`max_bytes`、`max_rounds`、`max_queries`、`communicator_file`、`grader_file`、`header_file`、`participant_solutions`）与 `subtasks`（子任务分值、约束、依赖、`test_indices`/`groups`）：驱动交互/通信引擎与多 OJ 子任务导出。
- `special_judge` / `stress_comparison`（`exact` | `checker`）/ 可选 `stress_checker_bidirectional`：控制对拍、终测、错解杀伤与样例校验是否走 testlib checker（须 `checker_build` 且存在 `files/checker`）。仅 `special_judge` 而 `stress_comparison=exact` 时终测等仍以字符串比对标答文件为主。
- `solutions` 中 `role=wrong` 的条目可设 `expected`：`fail`（默认，至少一测应判非 AC 或与 `.ans` 不一致）或 `pass`（全部测例须 AC 或与 `.ans` 一致），与 `problem_verify_tests` 的 `wrong_solution_kill` 语义一致。
- `load_manifest` 若遇 `manifest.json` 无法按 UTF-8 读取，会抛出 `ValueError`（含原因）；MCP 工具与 `autocode-verify` 会返回结构化错误信息，杜绝未捕获的裸 traceback。
- `audit_gates` 控制 full audit、validator/checker/interactor 自测、题面一致性与难度置信度等发布前门禁。
- `compiler`（可选）：配置自定义 C++ 编译器路径/命令（如 macOS Homebrew `g++-14`）；所有构建工具默认按 `显式 compiler 参数 > manifest.compiler > g++` 解析，`problem_create` 与 `solution_build` 会自动检测 Apple Clang 并提前提示避免 `<bits/stdc++.h>`。
- `empirical_complexity`（可选）：为多参数题、状压题或交互题显式配置 `dominant_var`、`scale_points`、`generator_args_template`、`extra_vars`、`scale_mode`（`auto`/`linear`/`exponential`/`log`）与 `metric`（`auto`/`cpu_time_ms`/`queries`）。
- `quality_gates.wrong_tle_kill_factor`（默认 `1.0`）与 `quality_gates.wrong_slowdown_kill_ratio`（可选）：控制 `wrong_solution_kill` 的逻辑 TLE 阈值与相对标程倍率击杀策略。
- `difficulty` 记录规则/LLM 难度评级的结果字段；`problem_audit(include_difficulty=true)` 会先产出确定性 signals。

快速校验：

```bash
uv run autocode-verify <problem_dir>
```

`autocode-verify` 会输出 `problem_type`、`oracle_mode`、`protocol_mode`、`subtask_count`、`spj_warnings` 与 `protocol_warnings`，便于 CI 解析。

完整审计 / 外部题包独立审计（`--format json|markdown|summary`，默认 `json`；Problem CI 示例模板位于 `examples/ci/autocode-problem-audit.yml`）：

```bash
uv run autocode-audit <problem_dir> --mode full --report audit_report.json
uv run autocode-audit <external_problem_dir> --standalone --format markdown
```

## 关键约束

- 包管理强制使用 `uv`；不要引入 pip/poetry/conda 流程。
- 对外文档同时描述 Claude Code、Codex、DeepSeek Harness (DSH) 与 Antigravity (`agy`) 插件；MCP server 是各宿主共享的实现与开发入口。
- 默认主路径是远程 plugin 安装；本地模式只用于开发、测试、验证。
- `hooks/` 只放 hook 配置，hook 逻辑脚本放在 `scripts/`。
- 模板资源统一放在 `src/autocode_mcp/templates/`。
- C++ 标准使用 C++20（需要 GCC 10+ 或兼容 Clang；macOS 默认 Apple Clang 不支持 `<bits/stdc++.h>`，请优先使用标准头文件或在 `manifest.json` 中配置 `compiler` 如 `g++-14`）。
- 本项目会编译并执行 AI 生成代码，只能在本地可信环境使用。
