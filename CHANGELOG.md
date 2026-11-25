# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [4.0.0] - 2026-11-25

### Added

- **MCP 2026 协议升级（PR #25, Wave 3）**：
  - 全部 22 个 MCP 工具附带显式 `ToolAnnotations`（`title`、`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`）、Pydantic 派生并内联 `$defs` 的 `outputSchema` 及 `structuredContent`。
  - 新增有界异步 `McpTaskManager`（`tasks/get`、`tasks/list`、`tasks/result`、`tasks/cancel`），支持长耗时工具（`stress_test_run`、`problem_generate_tests`、`problem_audit`、`problem_verify_tests`）的任务化执行与安全取消，同时保持非任务调用同步返回。
  - 新增 3 个零外部 CDN 的自包含 MCP Apps UI 资源（`ui://autocode/complexity-plot`、`ui://autocode/audit-dashboard`、`ui://autocode/protocol-replay`，`text/html;profile=mcp-app`），并通过工具 `_meta.ui.resourceUri` 关联。
  - `autocode-mcp` 新增可选无状态 Streamable HTTP 传输（`--transport http --host 127.0.0.1 --port 8765`，含 loopback Host/Origin DNS Rebinding 防护，默认保持 `stdio`）。
- **红队审计、精确判定、覆盖率与反例缩减/变异引擎（PR #24, Wave 2, #10）**：
  - 新增 `agents/autocode-redteam-auditor.md` 与 `skills/redteam-hacking/SKILL.md`，强化赛前对抗审题与漏洞构造。
  - 内置 `src/autocode_mcp/templates/jngen.h` 竞赛生成器库。
  - `SolutionEntry` 新增精确预期判定 `expected_verdict`（`AC|WA|TLE|MLE|RE|PE`）与 `gcov` 分支覆盖率统计（`utils/coverage.py`）。
  - 新增确定性反例缩减器 `CounterexampleShrinker`（`utils/shrinker.py`）、基于可选 `tree-sitter-cpp`（`--extra ast`）的 AST 变异与多 Sanitizer UB 探针（ASan/UBSan/MSan/TSan），以及测试集压缩器 `utils/test_compactor.py`。
- **可插拔多平台沙箱与形式化约束/原创性审计（PR #24, Wave 2, #21）**：
  - 新增 `src/autocode_mcp/utils/sandbox/` 后端注册表（`bwrap`、`nsjail`、`isolate`、`apple_container`、`none`），`autocode-audit` 新增 `--sandbox auto|none|bwrap|nsjail|isolate|apple_container` 与 `manifest.sandbox` 配置。
  - 新增可选 Z3 SMT 约束校验与 `constraints.h` 双向生成（`--extra formal`、`utils/smt_constraints.py`），以及题面歧义与本地原创性比对（`utils/originality.py`）。
- **多语言评测引擎、进化数据生成与多 OJ 互操作（PR #25, Wave 3, #20）**：
  - 新增 `utils/lang_runner.py` 支持 5 种语言（`cpp`、`python`、`pypy`、`rust`、`java`）及分语言时间倍率。
  - 新增进化测试数据生成器 `scripts/evolve_generator.py` 与 `skills/evolutionary-testdata/SKILL.md`。
  - 新增 ICPC/Kattis（`exports/icpc/`、`exports/kattis/`、`exports/icpc_kattis/`）与 IOI CMS（`exports/cms/`）导出器（`utils/exporters/{icpc_kattis,cms}.py`）。
  - 新增 Polygon REST API 凭据安全同步 CLI（`python -m autocode_mcp.cli.polygon_sync`）与 Markdown→Typst PDF 渲染 CLI（`python -m autocode_mcp.cli.render_pdf`）。
- **独立审计增强、AST/调用图复杂度分析与评测基准套件（PR #23, #25, Waves 1 & 3, #11 #12 #14 #15 #16 #19）**：
  - 新增 `utils/problem_layout.py` 支持 Polygon `problem.xml`、无后缀 `01`/`01.a` 测试对与统一错解发现；`autocode-audit` 新增 `--strict` 严格门控、独立 ASan/UBSan、实证复杂度与通信题变异测试。
  - 新增进程级硬件性能计数器（`perf stat` / `valgrind --tool=cachegrind`）与跨过程 AST/调用图复杂度分析。
  - 新增 `evals/` 基准套件（`standalone_benchmark.py` 离线 TPR/TNR 基准、4 题型 `harbor_tasks/`、`promptfoo.yaml` 多宿主矩阵）与 `scripts/optimize_skills_gepa.py` 技能优化脚本。

### Changed

- **基础契约硬化与惰性编译缓存（PR #22, Wave 0, #13 #17 #18）**：
  - `CompileCache` 改为惰性初始化（import 零磁盘副作用）与原子 `os.replace` 恢复；统一 `resource_limit.py` 跨平台资源限制与时间/内存传播。
  - 重构核心 Skills 渐进式披露结构（`references/` 分层，补全 `skills/agent-skill-governance/SKILL.md`）。
- **工作流门禁与校验器闭环（PR #23, Wave 1）**：
  - 强化 `validator_build` 边界检测、题面与 `val.cpp` 范围一致性检查，以及多场景门禁防篡改回归守护（`tests/test_gate_redteam.py`）。

### Fixed

- **三平台（Linux / macOS / Windows）实战硬化（PR #22–#25）**：
  - **Linux**：修复 `os.wait4` 继承父进程 RSS 导致误报 MLE（改用 `/proc/<pid>/status` `VmHWM` + 1ms 自适应采样 + `prlimit`）；支持 RHEL/TencentOS 默认旧版 `g++ (<10)` 自动升级至含完整 ASan 运行时的 `gcc-toolset`/`devtoolset`；修复 `perf stat` 吞没 `SIGABRT`、`gcov -b` 误计合成 `(throw)` 分支、容器内 `bwrap` 缺少 `--unshare-user-try` 及 `--proc /proc` 探测、`cachegrind` 启动超时、以及 Clang 22 MSan `libstdc++` 误报。
  - **macOS**：修复 Apple Silicon 缓存命中时原子写入新 inode 避免 Gatekeeper/codesign 缓存冲突；保留 `os.wait4` 精确子进程 `ru_maxrss`。
  - **Windows**：新增 `os.replace` `PermissionError` 退避重试、`STATUS_COMMITMENT_LIMIT` MLE 退出码归类、UB 探针 `-static` 链接与 Typst/编译器子进程 UTF-8 强制解码。
  - **文档与安全**：修正交互题 `interactor.cpp` 使用 `std::cout` + `std::cout.flush()` 指引并强化 PR CI 安全边界告警。

## [3.3.0] - 2026-11-23

### Added

- **Problem CI 人类可读审计报告与自动化分发工作流（Phase 4）**：
  - `autocode-audit` 新增 `--format json|markdown|summary` 输出模式（默认保持 `json` 向下兼容，`--report <path>` 与 `0=go` / `2=no_go` / `1=error` 退出码不变），新增安全渲染模块 `src/autocode_mcp/cli/report_format.py`（剥离 ANSI 与 8-bit C1 控制符，先转义反斜杠防 GFM 表格与链接绕过，支持阻断项、信号总表、实证复杂度、错解杀伤倍率、变异测试存活明细与通信预算利用率展示）。
  - 新增即用型 GitHub Actions Problem CI 模板 `examples/ci/autocode-problem-audit.yml`（最小 `contents: read` 权限、零写入 `--standalone --format markdown` 写入 `GITHUB_STEP_SUMMARY`）。
  - 新增 `.github/workflows/release.yml` 自动化发布流水线：推 `v*` tag 后校核 tag 与 `pyproject.toml` 一致性，运行 `uv build`、隔离 venv 打包冒烟、构建并 `--check` `marketplace`/`agy`/`dsh` 三套 bundle tarball，并附 `SHA256SUMS.txt` 发布至 **GitHub Releases**（不发布 PyPI）。
  - 升级 `.github/workflows/ci.yml` 为三平台守护矩阵（`ubuntu-latest`、`macos-latest`、`windows-latest`），补全最小权限与 `uv` 打包冒烟校验。
  - 新增 `tests/test_cli_audit_format.py`、`tests/test_ci_workflows.py` 与 `tests/test_phase2_dogfood_regressions.py`。

### Changed

- **五大核心单文件无环模块化拆解与 `mypy` 严格度收紧（Phase 3，PR-3.1 ~ PR-3.6）**：
  - 将仓库五大超长单文件（共 `11,510` 行）零行为漂移拆解为严格有向无环子模块（全部 `<= 822` 行，全仓 `70` 模块 AST Tarjan SCC `0` 循环导入，并通过 Facade 模式完整保持既有导入与 32+ 测试 monkeypatch 兼容）：
    - `utils/exporters.py`（`1,805` 行）拆分为 `utils/exporters/{__init__,common,hydro,polygon,qoj,verify}.py`（max `606` 行）；
    - `tools/problem.py`（`2,546` 行）拆分为 `problem_helpers.py`、`problem.py`（56 行 Facade）、`problem_cleanup.py`、`problem_create.py`、`problem_generate_helpers.py`、`problem_pack.py`、`problem_generate.py`（max `805` 行）；
    - `tools/audit.py`（`2,659` 行）拆分为 `audit_constraints.py`、`audit_discovery.py`、`audit_signals.py`、`audit_standalone.py` 与 `audit.py`（max `822` 行）；
    - `tools/test_verify.py`（`2,616` 行）拆分为 `verify_fingerprint.py`、`verify_static_checks.py`、`verify_live_checks.py`、`verify_wrong_kill.py` 与 `test_verify.py`（max `715` 行）；
    - `workflow/enforcement.py`（`1,884` 行）拆分为严格分层 DAG：`hashes.py -> state_io.py -> gates.py -> reconcile.py -> enforcement.py`（max `757` 行）。
  - 在 `pyproject.toml` 重新启用 `mypy` 的 `var-annotated` 与 `arg-type` 严格检查，修复全仓 18 处类型误差（零 `# type: ignore` / `cast` 绕过）。

### Fixed

- **32C 64G 云机真题狗粮压测与跨平台引擎硬化（Phase 2 & PR-3.6）**：
  - 修复 Linux GCC 12 `-O1 -fsanitize=address,undefined` 下 `new int[2]` 常量尺寸导致 UBSan `object-size` 抢先终止而掩盖 ASan 探针的问题：改用运行时不透明 `volatile int n`、ASan 探针隔离 `UBSAN_OPTIONS`、新增 Linux `-D_GLIBCXX_ASSERTIONS` 加固回退并在 `stress_test.py` 识别 libstdc++ 断言诊断，实现 `profile="static"`（`asan_verified=True` & `ubsan_verified=True`）。
  - 修复独立通信题（`run_twice` / `grader_linked`）无 `.ans` 文件、独立临时目录未编译 `communicator.cpp`、及 `grader_linked` 错解缺少额外源码链接问题，支持 `CommunicationRunner.run_from_manifest(..., communicator_exe_override=...)`。
  - 增强 `standard_cn` 独立审计发现：按注释/字面量剥离与 `registerInteraction(...)` 语义分类根/子目录 `tpj.cc`/`tpj.cpp`，支持 `data/validator.cpp`、`solution/brute_force.cpp`、`solution/wrong/*.cpp` 与 `exports/{qoj,hydro,polygon}` 限值回退（同时保持未含权缺失字段的空配置回退，不污染 C++20 系统头文件查找路径）。
  - 修复复杂性声明与静态 TLE 可行性判定：暴露 `complexity_match` 与 `complexity_feasible`，当实测静态复杂度在目标时限下明显不可行且宣称严格更优时将 `solution_audit_std` 判失败（`passed=False`），并在 `solution_audit_std` 补充源码注释复杂度提取回退与 `math.isfinite` 保护。
  - 修复 `EmpiricalRatioAnalyzer` 在 Linux `fork+exec` 低启动底噪（2–5 ms）平台下对低常数 $O(n^2)$ 伪装 $O(n)$ 的斜率漏检问题；增强 macOS Gatekeeper / Windows Defender 对新编译二进制首启 CDHash 扫描的容错处理，消除跨平台集成与超时测试抖动。

## [3.2.1] - 2026-11-23

### Added

- **独立验题增强与变异强度检测（PR #5–#6 + Phase 1 收口）**：
  - `problem_audit(standalone=True)` / `autocode-audit --standalone` 新增多布局适配器（`--layout auto|autocode|standard_cn|polygon|hydro`），支持隔离临时目录编译与实时运行 validator/答案/错解检查，并严格保持目标目录零写入。
  - 新增确定性 C++ 单点边界与运算符变异测试引擎 `src/autocode_mcp/utils/mutation.py`（可通过 `autocode-audit --mutation-check` 或 `problem_audit(enable_mutation_testing=True)` 启用）。
  - 新增 `tests/test_docs_sync.py` 自动漂移防线，校验 22 个 MCP 工具列表、模型枚举、CLI 参数、`openspec/specs/` 与文档保持同步；补全 `openspec/specs/multi-host-distribution/spec.md` 与 `openspec/specs/standalone-problem-audit/spec.md`。
  - 4 个示例仓库（`examples/exact-sample`、`checker-sample`、`interactive-sample`、`communication-sample`）全部补全为跨平台可移植的标准 C++20 活样例，并新增真实编译与错解杀伤端到端集成测试。

### Changed

- **跨平台加固、复杂度分析与导出收口（PR #3–#6 + Phase 1 收口）**：
  - 加固 Windows/Linux/macOS 跨平台支持：修复 `WinJobObject` 句柄/浮点超时与定时器粒度、Apple Clang `<bits/stdc++.h>` 提前提示与 `manifest.compiler` 解析、Linux 无 `prlimit` 回退、`RLIMIT_CORE=0` 禁 core dump、强制即时 MLE 终止及 `brute` 最小规模/崩溃严格失败。
  - `stress_test_run` 支持 `enable_sanitizers=True` 在隔离 `.autocode/san_bin/` 运行 ASan/UBSan 与负对照探针（Linux 容器自动回退 `-static-libasan -static-libubsan`）；`problem_generate_tests` 新增 `mode="append"|"register_existing"` 与 `tests/hack/` 保留；`autocode-verify` 新增 `--status`/`--strict-gates`/`--reconcile`；MCP Server 统一将超过 128 KB 的工具响应截断摘要并将完整输出落盘至 `.autocode/logs/`。
  - 细粒度 SHA-256 工作流缓存：单独修改 `sol.cpp` 不失效 `brute` 审计，跨平台迁移后纯重编译保留历史分析与数据状态；`.autocode/runtime.json` 统一使用相对路径。
  - 改进静态复杂度与空间复杂度识别（Dijkstra、后悔贪心堆、1D DP、分治、快读、ST 表），强化半对数指数复杂度拟合与交互查询曲线验证，扩展 validator 与题面命名边界等价比对。
  - 完善 Polygon 自包含包 `exports/polygon/` 与 `exports/polygon.zip` 打包回读校验及 zip-slip 防护；强化 `wrong_solution_kill` 在启用门禁却无错解时的 fail-closed 行为，新增 `wrong_ms / std_ms` 倍率报告与零容忍字节级重复测试输入检查。
  - 重锚 `README.md` 为「零 AI 独立验题 CLI + 多宿主 AI 出题工作台」双入口并同步 `rules/AGENTS.md`；按不发 PyPI 策略移除过时 `twine` 开发依赖，改用 `uv build`、`tests/test_e2e_mcp.py -m packaging` CLI/MCP 冒烟测试与 CI 隔离虚拟环境 wheel 安装检验；`tests/test_packaging.py` 改为动态比对 `pyproject.toml` 版本。

### Fixed

- 修复 `grader_linked` 通信题测试在 GCC 12.2.1 `-std=c++20` 下因未显式 `#include <cstddef>` / `std::size_t` 导致的编译失败，以及 `examples/exact-sample` 题面与 `manifest.json` 时限/规模不一致和 `examples/checker-sample` 缺失源码的问题。
- 修复 `problem_create` 对已有 `.autocode/manifest.json` 的静默覆盖风险（需显式 `overwrite=true`），拦截符号链接与目录目标；修复 `generator_build` C++ 分支解析与多种跨平台进程回收、空 stdin 管道挂起及并发输出污染问题。

## [3.2.0] - 2026-11-20

### Added

- **Antigravity (`agy`) 薄壳插件与多目标分发打包**：
  - 新增 `.antigravity-plugin/plugin.json`、根目录 `mcp_config.json` 与 `rules/AGENTS.md`，共享全部 22 个 MCP 工具、Skills 与 server 级工作流门禁。
  - `scripts/build_plugin_bundle.py` 升级支持 `--target marketplace|agy|dsh` 多目标可复现构建与 `--check` 一致性校验（`agy` 目标在 bundle 根目录输出 `plugin.json`、`mcp_config.json`、`rules/AGENTS.md`、`agents/`、`assets/` 与 `skills/`）；`scripts/sync_plugin_version.py` 扩展同步四宿主 manifest 版本。
- **题目模型演进（交互、通信与子任务）**：
  - `AutoCodeManifest` 新增 `problem_type`（`standard` | `spj` | `interactive` | `communication`）、`protocol`（`ProtocolConfig`：`interactive_1v1` / `run_twice` / `multi_process` / `grader_linked`、`passes`、`pass_headers`、`max_bits`、`max_bytes`、`max_rounds`、`max_queries`、`communicator_file`、`grader_file`、`header_file`、`participant_solutions`）、`oracle_mode`（`brute` | `interactor_only` | `reference_solutions`）与 `subtasks`（`SubtaskSpec`：分值、约束、依赖与测试映射）。
  - 工作流门禁适配交互/通信题：`oracle_mode != "brute"` 时可跳过 `brute` 强门禁；允许可选运行 `validator_build` 校验 `.in` 输入。
- **统一评测引擎 `JudgeRunner` 与端到端交互题工作流**：
  - 新增 `src/autocode_mcp/utils/judge_runner.py`（`StandardRunner`、`CheckerRunner`、`InteractiveRunner`），统一实现双进程双向管道交互、计算后死锁检测、仅针对选手进程的 CPU/RSS 限制、崩溃转 `RE` 判定与全路径清理。
  - 贯通交互题全链路：`interactor_build` 真实管道自测、`solution_run`、`stress_test_run`、`problem_validate`、`problem_generate_tests`、`problem_verify_tests`、实证复杂度校验与真实 C++ 端到端测试。
- **原生通信题引擎 `CommunicationRunner`**：
  - 新增 `src/autocode_mcp/utils/communication_runner.py`，原生支持三类通信模式：
    - `run_twice`：物理多进程隔离 + 每阶段独立临时工作目录 + canonical 4 参数 testlib `registerTestlibCmd` + `AUTOCODE_COMM_STAGE`/`AUTOCODE_COMM_ROLE` 环境变量；
    - `grader_linked`：多源码联合编译（`extra_sources` + 本地头文件变更失效缓存）与 grader 判分/`RE` 消歧；
    - `multi_process`：K 方并发进程行级复用协议（`TO`/`CLOSE`/`FROM`/`EOF`，4 MiB 行上限与死锁检测）。
  - 新增内置模板 `communicator_run_twice_template.cpp`、`grader_template.cpp`、`problem_h_template.h` 与完整示例 `examples/communication-sample/`。
  - 全链路支持通信预算（`max_bits`/`max_bytes`/`max_queries`/`max_rounds`）强制校验与 `problem_audit` 的 `communication_metrics` 利用率汇总。
- **多 OJ 导出与独立题包只读审计**：
  - 新增 `src/autocode_mcp/utils/exporters.py`：`problem_pack_polygon` 新增可选 `export_formats`（`polygon` | `hydro` | `qoj`），支持 Polygon `<groups>`/`<dependencies>`/`<tests>` 子任务 XML、Hydro `exports/hydro/`（`problem.yaml`、`problem_zh.md`、`testdata/config.yaml`）与 QOJ/UOJ `exports/qoj/`（`problem.conf`、按子任务连续重排的 `data/1.in..N.in`、`two_stage`/`with_implementer`/`interaction_mode`）。
  - `problem_audit` 新增 `standalone=True` 与 CLI `autocode-audit --standalone`，支持对无 `.autocode/` 的外部 Polygon/Hydro/QOJ 题包执行零写入只读实时校验。

### Fixed

- **核心质量与安全修复**：
  - 修复实证复杂度采样与 OS 底噪防护、静态循环/算法复杂度启发式、Polygon XML 同步、`file_save` 细粒度门禁失效与 macOS `realpath` 路径断言。
  - 统一 `checker_judge.verdict_from_testlib_rc` 映射（含 `7 -> PC`）；修正 `interactor_template.cpp` 与文档中 `registerInteraction` 的流语义（向选手发送须用 `std::cout` + `std::flush`，`tout` 写诊断输出文件）。
  - 修复 `problem_audit._require_special_artifact_gates` 误覆盖 `workflow["verify_signals"]` 导致交互/SPJ 题审计后打包失败的缺陷。
  - 加固全部 manifest 衍生路径（`solutions`、`participant_solutions`、communicator/grader/header 与导出目标目录）的路径穿越与符号链接逃逸防护。

## [3.1.0] - 2026-09-19

### Added

- **多规模阶梯数据采样与经验复杂度拟合**：
  - 新增 `MultiScaleSampler`（多规模阶梯数据采样器），支持自适应 5 点阶梯采样，适配多项式、小规模多项式与指数级规模，杜绝采样点倒挂；支持 testlib 命令行规范参数与多测极端数据分布生成。
  - 新增 `DynamicExecutionMonitor`（动态执行监控器），采集纯 CPU 耗时（`utime + stime`）与物理内存，测量并扣除原生系统启动底噪；支持交互题双向匿名管道并发调度与独立 CPU 耗时核算；完善进程树深度回收与异常清理。
  - 新增 `EmpiricalRatioAnalyzer`（经验倍率拟合分析器），采用对数线性回归拟合幂指数 $\alpha$ 与判定系数 $R^2$；引入动态理论期望倍率与自适应容差校验；支持处理器缓存容量跨越保护（Cache Jump Protection）；支持阶乘复杂度 $O(n!)$ 校验。
  - 重构 `complexity`、`solution_audit` 与 `audit` 工具层：解法审计与全量审计接入多规模经验拟合结果与质量信号门禁，未通过时追加高优先级阻断与修复指引。
- **DeepSeek Harness (DSH) 插件生态支持**：
  - 新增 `.dsh-plugin/package.json`、`.dsh-plugin/cordis.patch.yml`、`.dsh-plugin/index.js`，支持在 DSH 会话中挂载 AutoCode 作为 MCP 服务。
  - 通过 `dsh-agent-instructions` 扩展机制将工作区指导规范与质量门禁自动注入大语言模型系统提示词上下文。
- **动态评测套件扩充**：
  - 新增大文件流式行分页、字节切片寻道与 UTF-8 边界自适应对齐单元测试。
  - 新增符号链接越界访问与路径遍历拦截测试。
  - 新增内存超限（MLE）状态识别与字段透传测试。
  - 新增浮点数得分保留与多级确定性决胜排序测试。

### Changed

- **确定性算法竞赛出题工具链重构与安全加固**：
  - `file_ops`：`FileReadTool` 实现流式行分页读取与字节定位寻道，引入 UTF-8 多字节字符边界校准机制（最多向前延伸 3 字节消除截断乱码）；统一写入显式指定 `newline='\n'`；针对相对路径强制要求提供 `problem_dir` 参数，统一调用 `os.path.realpath` 彻底防御路径穿越与符号链接逃逸。
  - `compiler` / `process`：编译子进程配置 `start_new_session=True` 隔离独立会话；进程深度回收统一调用 `terminate_pid_tree` 结合 `psutil.Process.children(recursive=True)` 递归枚举终止后代进程树，彻底杜绝孤儿进程与文件描述符泄漏；引入 `-isystem` 隔离第三方模板标头目录；临时文件读取配置 10MB 安全上限；在 Linux 环境下准确识别内核终止信号与 `std::bad_alloc` 异常，透传 `memory_limit_exceeded` 状态。
  - `validator`：`ValidatorBuildTool` 增加无测试用例场景防护，核验 `inf.readEof()` 标记；`ValidatorSelectTool` 保留浮点得分精度（`-float(score)` 消除整型截断），实施得分降序、代码长度升序、ID 字典序与二进制路径字典序的四级确定性稳定排序。
  - `test_verify`：实施单测试点动态比例超时模型 `max(0.1, float(manifest.time_limit_ms) / 1000.0 * 1.5)`，缺省时限设置 5.0 秒上限；针对错解预期失败用例（`exp == "fail"`）引入首个非 AC 测试点命中即提前短路机制。
  - `audit`：`next_actions` 统一输出包含 `tool_name`、`action`、`arguments`、`priority` 字段的规范字典，补齐题面一致性门禁动作联动建议。
  - `interactor`：首个任务判定完成后显式取消挂起的未完成协程，释放调度资源。
  - `build_all`：引入 `get_exe_extension()` 平台可执行文件后缀彻底清理过期二进制。

## [3.0.0] - 2026-08-02

### Breaking Changes

- 工作流门禁下沉到 MCP server：存在 `.autocode/manifest.json` 或运行期状态的题目目录现在由 server 直接拒绝乱序调用；Claude hooks 只负责提前提示，Codex 不依赖 hooks。
- 分发规范从 Claude Code plugin 扩展为 Claude Code + Codex plugin。Codex 使用 `.codex-plugin/plugin.json`、共享 Skills、MCP server 和 marketplace bundle；不再假设 Claude 专有 hooks 或 Agent 能力存在。

### Features

- 新增 Codex plugin manifest、Codex marketplace bundle 生成器和 provenance fingerprint。
- 新增结构化门禁拒绝结果：`gate_blocked`、`blocking_issues`、`next_actions`。
- 统一 MCP server、Claude hook adapter 和运行期状态转换，覆盖成功、失败、异常和取消路径。
- 为 Codex/无 hooks 客户端补充 server 级工作流回归测试和 ratio 异常输入防护。
- CI 新增 plugin contract job，构建并检查干净的 Claude/Codex bundle，防止 manifest 或分发资产漂移。
- 将 MCP 依赖限制在当前兼容的 1.x 主版本，避免独立 wheel 安装时被 MCP 2.x API 破坏。

### Distribution

- `autocode-marketplace` 增加 `.agents/plugins/marketplace.json` 与 `plugins/autocode/` Codex bundle；发布前需运行 `scripts/build_plugin_bundle.py --check`。
- marketplace 仓库增加无依赖的 `scripts/validate_marketplace.py` 和 layout CI，用于校验 catalog、双宿主 manifest 与 provenance。

## [2.0.0] - 2026-07-16

### Breaking Changes

- MCP server 移除 `prompts` 与 `resources` 能力面（原公开 MCP 接口定义规范的一部分）：删除对应模块与 handler（`list_prompts` / `get_prompt` / `list_resources` / `read_resource`），仅保留 22 个 Tools。依赖这些端点的外部脚本或裸 MCP 客户端需迁移到 Skills 或 Tools。
- **题目 manifest 迁移到 `.autocode/manifest.json`**：题目级 manifest 从题目根的 `autocode.json` 改为 `.autocode/manifest.json`。已存在的题目根 `autocode.json` 不再被读取，需迁移到 `.autocode/manifest.json`（字段不变）。`problem_create` 现在初始化 `.autocode/manifest.json`（替代历史 `autocode.json` 路径）。

### Features

- **CC-first 单一分发**
  - 移除 PyPI 发布与 `uvx` 运行路径：`.mcp.json` 改为 `command: uv, args: ["run", "autocode-mcp"]`，README 移除 PyPI 徽章与发布叙述，仅保留 Claude Code plugin 与本地开发。
  - MCP server 收敛为纯 Tools：删除 `prompts` 与 `resources` 模块及对应 handler（`list_prompts` / `get_prompt` / `list_resources` / `read_resource`），`server.py` 清理相关 `mcp.types` 导入；22 个工具签名不变（对外接口定义保留）。
  - README 移除 Cursor / OpenCode（裸 MCP 客户端）使用段落，文档仅面向 Claude Code plugin 与本地开发。

### Improvements

- **知识源单一真值收口**
  - 移除 `prompts` 模块后，Claude Code 消费者知识唯一真源为 `skills/*.md`；删除 `tests/test_prompts.py`、`tests/test_resources.py`、`tests/test_prompts_consistency.py`。
  - 版本单一真源：`pyproject.toml` 的静态 `version` 为唯一权威版本；新增 `scripts/sync_plugin_version.py`，`__version__` 与 `plugin.json` 的 `version` 由其派生，不再三处手工维护。
- **运行期副产物收口**
  - 所有非题目运行期产物统一写入题目目录的 `.autocode/runtime.json`（键 `workflow` / `test_manifest` / `generate_checkpoint` / `audit`），消除散落的 `.autocode-workflow/state.json`、`tests/.autocode_tests_manifest.json`、`.autocode_generate_state.json`、根 `audit_report.json`。
  - 新增 `src/autocode_mcp/runtime_store.py` 统一收口文件名/路径；`problem.py` / `test_verify.py` / `audit.py` / `hook_state.py` 迁移到 `runtime_store`，删除各自重复的常量。
  - `problem_create` 现在只创建 `.autocode/` 目录并在其中写入自忽略 `.gitignore`（内容 `*`），使整个运行期副产物目录对题目 git 仓库零痕迹，不再在题目根生成 `.gitignore`（AutoCode 仓库根不忽略 `.autocode/`）。
- **题目 manifest 收口到 `.autocode/`**
  - 题目级 manifest 从题目根的 `autocode.json` 迁移到 `.autocode/manifest.json`；`workflow/manifest.py` 的 `manifest_path()` 改为返回 `problem_dir/.autocode/manifest.json`，并复用 `runtime_store.RUNTIME_DIR_NAME`。
  - 全部读写路径（`problem.py`、`verify` / `validation` / `test_verify` / `stress_test` / `audit`、hook 脚本 `hook_state.py` / `hook_gates.py`）统一引用 `manifest.json`，错误文案与注释同步更新。
  - 模板 `src/autocode_mcp/templates/autocode.json` 改名为 `templates/manifest.json`；`examples/*-sample` 的 `autocode.json` 重建为 `.autocode/manifest.json`（内容不变）。

### Documentation

- 同步 `openspec/specs/` 三个 capability 终态：`cc-first-distribution`（新建，CC 插件唯一分发面、MCP 纯 Tools、无裸 MCP 客户端文档）、`runtime-byproduct-consolidation`（新建，运行期副产物单一存储与 git-ignore）、`knowledge-source-single-truth`（删除 prompts 需求，补仓库单一真源与版本单一真源）。
- `tests/README.md` 移除已删的 `test_prompts.py` / `test_resources.py`，打包测试改用 `uv pip install dist/*.whl`。
- `CLAUDE.md` 去掉 Cursor 措辞与 `prompts/` 目录树，题目结构图改用 `.autocode/runtime.json`。

### Tests

- 新增 runtime 收口回归测试；删除 prompts/resources 一致性测试；全量 pytest 通过，`ruff` 与 `mypy` 干净。

## [1.0.6] - 2026-07-14

### Improvements

- **性能与健壮性优化（纯内部重构，不改变工具对外契约）**
  - 并发批处理：`problem_generate_tests`、`problem_verify_tests`、`stress_test_run` 改为有界并发执行（默认并发上限 4、可配置），结果与原串行实现一致。
  - 统一门禁：`workflow/guard.py` 暴露 `check_gates`，`problem_pack_polygon` 与 `problem_audit` 共用唯一门禁真值；`autocode.json` 缺失或不可解析时显式阻断，而非静默宽松回退。
  - 进程生命周期加固：`problem_cleanup_processes` 默认回收残留生成器/编译器进程（`psutil` 存活校验、POSIX 整进程树回收），取消路径主动终止在途工作。
  - Hook 脚本拆分：`scripts/workflow_guard.py` 拆为 `hook_payload.py` / `hook_state.py` / `hook_gates.py` 纯函数，门禁复用 `manifest_uses_testlib_checker` 单一实现。
  - 知识源单一真值：全部 22 个工具的 `input_schema` 改由 Pydantic 模型推导（`input_schema_from_model`），消除手写 JSON Schema 漂移；prompts 与 `skills/*.md` 共享 canonical 事实并由一致性测试守护。
  - 新增 `problem_build_all` 工具，一次性构建题面所需的全部二进制（solution/generator/validator/checker/interactor），MCP 工具总数 21 → 22。

### Tests

- 新增并发 / 门禁 / 进程 / 钩子 / hook 去重 / 输入 schema / prompts 一致性等回归测试；全量 pytest 通过，`ruff` 与 `mypy` 干净。

## [1.0.5] - 2026-05-21

### Features

- **完整验题审计链**
  - 新增 `problem_audit` 与 `autocode-audit`，聚合 manifest、workflow state、终测质量信号和难度证据，输出 `decision`、`blocking_issues`、`risk_report`、`quality_signals`、`difficulty_signals` 与 `next_actions`。
  - 新增 `skills/problem-difficulty-rating`，用于把 `problem_audit` 的确定性信号转成 CF-style rating、难度档位、原因与置信度说明。

### Improvements

- `problem_verify_tests` 增强为更强门禁：新增重复/近重复数据、规模分布、case purpose 覆盖和错解杀伤统计。
- `problem_pack_polygon` 可在 full audit 门禁开启时要求最近一次 `problem_audit(mode=full)` 通过。
- README、CLAUDE、agents、prompts、manifest 模板与测试同步更新。

### Tests

- 新增 audit / difficulty / packaging 回归测试，覆盖 manifest 兼容、validator/checker/interactor 自测门禁和 full audit 打包门禁。

## [1.0.4] - 2026-05-21

### Features

- **交互题协议硬化**
  - `interactor_build` 新增 `interaction_scenarios`，用 testlib `registerInteraction` 约定脚本化验证交互器对合法/非法协议的 verdict。
  - `problem_create(interactive=true)` 自动生成交互题 README 骨架与 `files/interactor.cpp` 模板。
  - `problem_validate` 对交互题改走协议校验，不再把 transcript 样例当作普通静态输入输出题处理。
  - `problem_pack_polygon` 在交互题下会打包并声明 `files/interactor.cpp`，不再无条件依赖 `val.cpp`。

### Improvements

- README、CLAUDE、skills、agents、示例题面与题解同步补充交互题规范。
- 交互器模板改为 testlib 语义的 `tout` / `ouf` / `inf` 调用方式。

### Tests

- 新增交互题协议、interactor 场景、workflow gate 与 Polygon 打包回归测试。

## [1.0.3] - 2026-05-04

### Features

- **SPJ（testlib checker）与 manifes你  t 契约**
  - `autocode.json` 增加 `special_judge`、`stress_comparison`（`exact` | `checker`）、可选 `stress_checker_bidirectional`；无 checker 工作流时自动忽略误设的 `stress_checker_bidirectional`。
  - `stress_test_run`：在 `special_judge` + `stress_comparison=checker` 时用 `checker(in, sol_out, brute_out)` 判定对拍；可选双向再跑 `checker(in, brute, sol)`。
  - `problem_verify_tests`：`answer_consistency` 与 `wrong_solution_kill` 在同一配置下走已编译的 `files/checker`；`stress_comparison=exact` 时仍与 `.ans` 字符串比对。
  - `problem_validate`：题面样例与 `tests/` 样例在 checker 路径且 checker 已编译时走 checker。
  - `problem_pack_polygon`：存在 `files/checker.cpp` 时在生成的 `problem.xml` 中声明 checker。
  - `autocode-verify`：输出 `special_judge`、`stress_comparison`；在 checker 工作流下校验 `checker.cpp`/已编译 checker，并以 `spj_warnings` 提示缺失项。

### Bug Fixes

- **`wrong_solution_kill` 与 manifest `expected` 一致（含非 checker）**
  - `expected` 默认 `fail`：须至少一测未通过 checker 或与 `.ans` 不一致才算「杀伤」成功。
  - `expected=pass`：须全部测例通过 checker 或与 `.ans` 一致（用于合法多解等需在终测上保持 AC 的条目）；此前非 checker 分支忽略该字段，已与 checker 分支对齐。
- **`load_manifest` 读盘健壮性**：`autocode.json` 非 UTF-8 或读失败时抛出带上下文的 `ValueError`；`stress_test_run` / `problem_verify_tests` / `problem_validate` 与 `autocode-verify` 统一捕获，避免未处理解码异常。

### Documentation

- 同步 README、`skills/testdata-quality`、`skills/autocode-workflow`、`skills/problem-validate` 与 CLAUDE.md 中 Manifest 说明（SPJ、错解 `expected`、`autocode-verify`）。

### Tests

- 非法 UTF-8 manifest、`stress_checker_bidirectional` 归一化、`wrong_solution_kill` exact 路径下 `expected=pass`/`fail` 回归用例。

## [1.0.2] - 2026-05-01

### Bug Fixes

- **stress_test_run 可观测性与参数建议**
  - validator 失败时返回结构化诊断信息（`validator_return_code`、`validator_stderr`、`validator_stdout`）。
  - 对拍结果增加 `complexity_context` 与 `n_max_advisory`，由复杂度审计证据驱动参数决策；兼容保留 `n_max_warning` 别名。
  - Windows 输入换行归一化改为单次稳定转换，减少 CRLF/LF 混用导致的 strict validator 偶发失败。
- **workflow_guard 审计上下文回写修复**
  - `solution_analyze` 后按 `estimated_complexity`（并兼容回退字段）写入 `std_complexity`。
  - `solution_audit_brute` 回写 `brute_complexity` 与 `recommended_stress_params`，供后续 `stress_test_run` 建议使用。
- **problem_generate_tests 兼容性增强**
  - `type=4` 使用 `extra_args`（如 `mode=tle_dense`/`mode=tle_chain`）失败时，自动回退到无 `extra_args` 重试一次。
  - 返回新增 `generator_tle_extra_args_fallbacks` 统计，便于定位生成器参数兼容问题。
- **generator 语义检查误判收敛**
  - `signal_overlap` 从硬失败条件降级为 advisory 信号（`signal_overlap_advisory`），避免仅因标识符重叠造成误拒。

### Improvements

- **validator/generator 模板与提示词**
  - validator prompt/template 明确 `inf.readEof()` 必须调用；若容忍尾部空白，推荐 `inf.seekEof(); inf.readEof();`。
  - generator template 为 `type=4` 增加结构性卡法示例，不再仅建议“参数拉满”。
- **文档与技能同步**
  - README、workflow skill 与示例题面同步更新：brute 需直接模拟原始约束；`n_max` 决策改为基于审计证据与 advisory 字段。
  - workflow skill 补充 `type=4 extra_args` 不兼容时的回退行为说明。

### Tests

- 新增/扩展回归测试覆盖：
  - stress validator 失败详情、`n_max_advisory` 兼容字段、`state.json` 缺失/损坏路径；
  - workflow_guard 审计字段回写；
  - `type=4 extra_args` 回退行为；
  - generator 语义检查 advisory 字段；
  - Windows 换行归一化单元测试与 strict validator 链路验证。

## [1.0.1] - 2026-04-30

### Bug Fixes

- **质量门禁默认契约对齐**
  - 将 `require_wrong_solution_kill` 默认值调整为 `false`（`workflow/models`、模板与 hook 同步），避免默认流程在未显式启用错解杀伤门禁时被错误阻断。
- **validator 信号语义修复**
  - `problem_verify_tests` 生成 `quality_signals` 时，若 `validator` 检查为 `skipped`，不再标记为 `executed=true/passed=true`，避免“未执行校验却通过门禁”的旁路。
- **回归测试精度增强**
  - 收紧交互题 `generator_build` 门禁测试断言，确保命中 `interactor_build` 前置约束。
  - 修正审计门禁测试断言目标，与缺失项一致，降低文案细化导致的伪回归噪音。

## [1.0.0] - 2026-04-30

### Features

- **Plugin-first 出题工作流**
  - README、CLAUDE.md、默认 workflow agent 与 workflow skill 重新整理为 Claude Code plugin 主路径，优先面向出题人解释 AI 出题常见风险与 AutoCode 的验证门禁。
  - `workflow_guard.py` 重构为表驱动门禁，补充 `problem_verify_tests`、`interactor_build`、`solution_analyze`、`validator_select` 的流程约束，并识别交互题路径。
- **题目契约与快速校验**
  - 新增 `autocode.json` manifest 与 `src/autocode_mcp/workflow/` 读写模型，`problem_create` 自动初始化 manifest 与 `statements/tutorial.md` 草稿。
  - 新增 CLI：`autocode-verify`，用于快速校验题目 manifest、题面和题解路径完整性。
  - 新增 `examples/` 下三个 manifest 样例目录：`exact-sample`、`checker-sample`、`interactive-sample`。
- **解法与复杂度审计**
  - 新增 `solution_audit_std`、`solution_audit_brute`，用于审计标准解质量、复杂度风险和 brute 是否适合作为对拍 oracle。
  - `solution_analyze` 增强输出：`claimed_complexity`、worst/average、`memory_estimate`、`risk_notes`、`recommended_stress_params`。
- **对拍与测试数据质量**
  - `stress_test_run` 支持 `stress_profiles` 多轮对拍配置并返回 profile 报告。
  - `problem_verify_tests` 支持 `wrong_solution_kill` 检查类型，验证错解是否被测试点杀掉。
- **只读审计 Agent 与 Skills**
  - 新增 skills：`idea-feasibility`、`solution-complexity-audit`、`stress-strategy`、`testdata-quality`、`statement-audit`。
  - 新增 agents：`autocode-idea-auditor`、`autocode-solution-auditor`、`autocode-package-auditor`。

### Improvements

- **质量门禁一致性修复**
  - `workflow_guard.py` 与 `problem_pack_polygon` 的门禁语义对齐：统一读取并执行 `quality_gates`，避免 Hook 与工具层行为不一致。
  - `problem_verify_tests` 结果回写状态时同步记录并校验 `limit_case_ratio`，确保后续打包门禁可基于阈值生效。
  - `problem_pack_polygon` 增加最小自校验：测试输入/答案配对、题面与主解存在性、工作流验证状态检查。
- **Agent / Skill 治理规范收敛**
  - 新增 `skills/agent-skill-governance/SKILL.md`，定义 `agents/` 与 `skills/` 的统一语言、术语、结构、决策规则与审查清单。
  - `agents/autocode-workflow.md` 去重并转为 orchestration 主责，workflow 细节收拢到 `skills/autocode-workflow/SKILL.md` 以降低双维护漂移。
  - `skills/problem-validate/SKILL.md` 轻量化为标准 skill 形态，并新增 `skills/problem-validate/reference.md` 承载详细工具参考。
- **技能文档一致性增强**
  - 为 `stress-strategy`、`statement-audit`、`testdata-quality` 补齐 `Forbidden Behavior`。
  - 统一 `type=3` 与 `type=4` 的语义边界定义（`type=4` 为针对性 worst-case / TLE 模式，不是简单参数拉满）。
  - `solution-complexity-audit` 合并重叠判定语义，明确 `high_tle_risk` 判定方向（应为 `false` 或 `low`）。

## [0.9.0] - 2026-04-29

### Features

- **`problem_generate_tests` 稳定性与可恢复性**
  - 新增 `answer_ext`（答案文件后缀，默认 `.ans`，可配置为 `.out` 等），贯穿生成、清理、manifest 与 Polygon 打包路径。
  - 新增 `resume`、`hard_timeout_seconds`、`checkpoint_every`：支持 checkpoint 落盘、硬超时后保留状态、中断后可续跑。
  - 子进程 PID 跟踪（`active_pids`），供精准清理残留生成器进程。
- **`problem_cleanup_processes` 工具**：按状态文件中记录的 PID 清理残留生成器（Windows `taskkill` / POSIX `kill`），更新 `active_pids` 时保留 checkpoint 其它字段，避免破坏 `resume`。
- **`problem_verify_tests`**
  - 支持 `answer_ext` 与 manifest 推断；`file_count` 正确处理多段后缀（如 `.a.out`）。
  - 新增 `limit_semantics`：基于 manifest 中 type=3/4 的 signature 重叠度做语义质量提示。
- **`generator_build`**
  - 可选 `enable_semantic_check` / `strict_semantic_check`：对 type=3/type=4 分支做静态语义差异检查（含 `case N:`、`N == type` 等常见写法）；不确定时以 advisory 提示而非一律失败。
- **MCP 服务端**：工具调用被取消时返回结构化结果并提示 `resume`。
- **执行与 Windows Job**：`run_binary` / `run_binary_with_args` 支持 `process_start_hook`；取消路径强制终止子进程；`WinJobObject.close()` 文档与注释对齐 `KILL_ON_JOB_CLOSE` 语义。

### Improvements

- **`problem_pack_polygon`**：从 manifest 读取的 `answer_ext` 经规范化校验，写入 `problem.xml` 时做 XML 转义，避免脏数据破坏打包文件。
- **文档与工作流**：README、CLAUDE.md、workflow skill、agent、prompts、`workflow_guard` 同步说明新参数、新工具与长耗时任务注意事项。

## [0.8.0] - 2026-04-28

### Improvements

- **最终测试数据配比约束**: `problem_generate_tests` 采样策略更新为优先保证最终测试集中 `type=3/4`（extreme + tle）不少于一半（候选不足时尽量满足），并返回 `limit_case_count`、`limit_case_minimum_required`、`limit_case_quota_met` 统计字段。
- **验证阶段硬约束**: `problem_verify_tests` 新增 `limit_ratio` 校验（默认启用），基于生成 manifest 强制检查最终测试中 `type=3/4` 是否达到至少一半，不满足将直接验证失败；可通过 `enable_limit_ratio=false` 显式关闭。
- **文档与工作流同步**: 更新 README、workflow skill、agent 提示与 prompts 文案，统一说明“最终测试至少一半极限数据”的质量门槛。

## [0.7.0] - 2026-04-27

### Features

- **source_path 直接编译**: 当使用 `source_path` 参数时，直接从原始文件编译，不再覆盖到标准位置。标准位置仍保留副本以供其他工具使用。所有构建工具返回 `canonical_path`（标准位置副本）和 `source_path`（实际编译源）。
- **resolve_source() 公共函数**: 提取 5 个构建工具中的源码解析逻辑到 `mixins.py` 的 `resolve_source()` 函数和 `ResolvedSource` 数据类，消除约 100 行重复代码。
- **name 参数**: `solution_build` 和 `solution_run` 新增 `name` 参数，支持自定义文件名（如 `name="brute_force"` 替代默认 `brute`）。
- **sol_name / brute_name**: `stress_test_run` 新增 `sol_name` 和 `brute_name` 参数，支持查找自定义命名的解法二进制文件。
- **output_dir 参数**: `problem_generate_tests` 新增 `output_dir` 参数，可指定测试数据输出目录（默认 `problem_dir/tests`）。
- **extra_args 参数**: `stress_test_run`、`generator_run`、`problem_generate_tests` 的 `test_configs` 新增 `extra_args` 参数，支持传递自定义命令行参数给 generator。协议扩展为 `gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max> [extra_args...]`。
- **types 参数**: `stress_test_run` 新增 `types` 参数，支持在对拍中循环使用多种生成策略（如 `["1","2","3","4"]`）。
- **problem_verify_tests 工具**: 新增测试数据验证工具，检查文件配对、答案一致性（重新运行 sol）、validator 验证、无空文件等。
- **stress_test_run 统计信息**: 对拍通过/失败时返回详细统计，包括 sol/brute 运行时间分布、N 值分布、最慢轮次等。
- **构建结果透明度**: 所有构建工具返回 `binary_size` 和 `canonical_path`，`source_path` 返回实际编译源文件路径。

### Improvements

- **smart mode 文档**: `problem_generate_tests` 的 `constraints` 参数说明更明确，返回 `effective_test_configs` 展示实际使用的配置。
- **workflow_guard 自定义命名**: `infer_state()` 支持自定义解法文件名（前缀匹配），新增 `tests_verified` 状态字段。
- **工作流步骤更新**: 新增 `problem_verify_tests(passed)` 步骤，位于 `problem_generate_tests` 和 `problem_pack_polygon` 之间。

## [0.6.0] - 2026-04-25

### Features

- **source_path 参数**: 所有构建工具（solution_build, generator_build, validator_build, checker_build, interactor_build）新增 `source_path` 参数，可直接指定源文件路径，无需传入完整源码字符串。`code` 参数不再为必填，与 `source_path` 二选一。
- **source_path 编码回退**: 自动处理非 UTF-8 编码的源文件，先尝试 UTF-8 读取，失败后回退到 latin-1（宽松解码，不会抛异常但可能产生乱码）。
- **source_path 相对 include 支持**: 当 `source_path` 指向外部文件时，自动将源文件父目录加入编译 include 路径，确保 `#include "helper.h"` 等相对引用正常工作。

### Improvements

- **stress_test_run 错误信息增强**: Generator 失败时现在包含 `seed`、`cmd_args`、`stdout`、`stderr`、`last_input`（上一次成功生成的输入数据），便于调试。
- **stress_test_run 失败模式区分**: 超时、空输出、崩溃三种失败模式现在给出不同的提示信息，不再统一附加 "Check that the generator accepts command-line arguments"。
- **generator_args 文档完善**: `stress_test_run` 的 `generator_args` 参数现在明确说明调用协议 `gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>`，以及各字段的含义和可选值。
- **n_max 参数关系澄清**: 顶层 `n_max` 参数说明中注明其同时作为 `generator_args.n_max` 的默认值，成功结果中新增 `effective_n_max` 字段。
- **题目目录结构文档**: CLAUDE.md 新增题目目录结构说明，明确 `solutions/`、`files/`、`statements/`、`tests/` 的用途和文件命名。

## [0.5.0] - 2026-04-24

### Features

- **新增 problem_validate 工具**
  - 验证题面中的样例答案是否正确（运行 sol）
  - 验证 tests/ 目录下的样例文件是否与 sol 输出一致
  - 支持多种样例格式：Markdown code block、纯文本格式（`样例输入：`/`Sample Input:`）
  - 新增 `skills/problem-validate/SKILL.md` 验证 skill 文档

- **工作流变更**
  - 新增验证步骤：`stress_test_run -> problem_validate -> problem_generate_tests`
  - `problem_generate_tests` 前必须先通过 `problem_validate` 验证
  - 更新 `agents/autocode-workflow.md` 和 `skills/autocode-workflow/SKILL.md`

### Bug Fixes

- **Windows 平台 testlib 程序兼容性**
  - 修复 Windows 上 testlib strict 模式期望 CRLF 换行符的问题
  - 将输入数据的 LF 转换为 CRLF 以满足 validator 的 `readEoln()` 要求

- **problem_validate 工具修复**
  - 无样例时正确返回失败而非成功
  - 重新验证失败后正确清除缓存状态

### Tests

- 新增 `tests/test_validation.py`（15 个测试用例）
- 测试数量从 173 增至 176

## [0.4.0] - 2026-04-09

## [0.4.1] - 2026-04-09

### Features

- 按官方 Claude Code plugin 结构补全插件：`.claude-plugin/plugin.json`、`settings.json`、`agents/`、`hooks/`
- 新增工作流强制 hook，会拦截跳过 `problem_create/solution_build/validator_build/generator_build/stress_test_run/problem_generate_tests/problem_pack_polygon` 的调用
- 将 README / README_CN 的默认安装路径调整为 Claude Code plugin 安装，其它 MCP 客户端作为兼容入口

### Design Rationale

- 用 Claude Code 官方插件结构替代错误的 Codex 插件结构
- 不再只提供 MCP 包装，而是同时提供默认 agent、skills 与 hooks，对工作流做硬约束

### Notes & Caveats

- 当前插件仍依赖本地 `stdio` MCP server 提供实际工具执行能力
- Claude Code 的 workflow enforcement 依赖 plugin agent 与 hooks，其它 MCP 客户端不会自动获得这部分能力

### Breaking Changes

- **配置单位变更**
  - `problem_pack_polygon` 的 `time_limit` 参数单位从毫秒改为**秒**
  - `problem_pack_polygon` 的 `memory_limit` 参数单位从字节改为**MB**
  - 与 `problem.yaml` 和 `ResourceLimit` 保持一致

- **目录结构变更**
  - `solution_build` 保存文件到 `solutions/` 子目录
  - `generator_build` 保存文件到 `files/` 子目录
  - `validator_build` 保存文件到 `files/` 子目录
  - `checker_build` 保存文件到 `files/` 子目录
  - `interactor_build` 保存文件到 `files/` 子目录
  - 所有工具支持向后兼容：优先查找子目录，回退到根目录

### Bug Fixes

- **打包配置修复 (P0)**
  - 将 `templates/` 移入 `src/autocode_mcp/templates/`
  - 修复 wheel 包不包含模板文件的问题
  - 更新 `TEMPLATES_DIR` 路径计算逻辑

- **MCP 协议修复 (P0)**
  - `call_tool` 返回类型从 `list[TextContent]` 改为 `CallToolResult`
  - 正确设置 `isError` 标记，客户端可区分成功/失败
  - 添加 `structuredContent` 字段提供结构化数据
  - `get_prompt` 返回类型从 `str` 改为 `GetPromptResult`
  - `read_resource` 返回类型从 `str` 改为 `ReadResourceResult`

- **Generator 协议统一 (P1)**
  - `stress_test_run` 新增 `generator_args` 参数
  - 支持完整协议: `gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>`
  - 默认使用完整协议（type=2 random）

- **Verdict 完善 (P1)**
  - `checker_build` 根据 testlib.h 返回码正确区分 AC/WA/PE/TLE
  - `interactor_build` 支持 PE 判断

### Tests

- 新增 `tests/test_packaging.py` 验收测试 (7 个测试用例)
- 测试数量从 131 增至 138

### Documentation

- 更新 README 文件结构说明，反映新的目录布局

## [0.3.1] - 2026-04-08

### Bug Fixes

- **Server 模块**
  - 修复 `SolutionAnalyzeTool` 导入路径错误（从 `complexity.py` 导入而非 `solution.py`）
  - 更新 docstring 中的工具数量（14 → 15）
  - 补充测试验证 `SolutionAnalyzeTool` 注册

- **Utils 模块**
  - 新增 macOS 资源限制实现（使用 `resource` 模块 + `preexec_fn`）
  - 改进异常处理：将裸 `except Exception: pass` 改为捕获具体异常类型
  - 添加日志记录（`logging` 模块），便于调试
  - `win_job.py` 中捕获 `pywintypes.error` 而非通用 `Exception`

- **Tools 模块**
  - 完善 `constraints` 参数验证：新增 `t_max`、`sum_n_max` 验证
  - 新增 `test_configs` 参数验证：验证 `type`、`n_min`、`n_max`、`t_min`、`t_max` 字段

- **类型注解**
  - `RunToolMixin.run()` 添加返回类型注解 `-> RunResult`
  - `solution_type` 参数类型限制为 `Literal["sol", "brute"]`
  - `solution.py` 中 `solution_type` 参数类型统一

### Tests

- 新增 `test_problem_generate_tests_test_configs_validation` 测试用例
- 测试数量从 129 增至 131

## [0.3.0] - 2026-04-03

### Features

- **安全机制增强（ACM）**
  - 新增 `ResourceLimit` 数据类，统一资源限制接口
  - 新增 `get_resource_limit()` 函数，支持优先级链：工具参数 > problem.yaml > 默认值
  - 新增 `WinJobObject` 类，实现 Windows 内存/CPU 限制
  - 暴力解法内存限制为可用内存上限，超时 60s
  - 标准解法内存限制 256MB，超时从 problem.yaml 读取

- **代码精简**
  - 新增 `BuildToolMixin` 和 `RunToolMixin`，减少重复代码约 35%
  - 重构 `SolutionBuildTool`、`SolutionRunTool`、`ValidatorBuildTool`、`GeneratorBuildTool`、`CheckerBuildTool`

- **性能优化**
  - `compile_all()` 支持并发编译，默认 4 个并发
  - 新增 `CompileCache` 类，基于内容 hash 的编译缓存

### Design Rationale

- **资源限制策略**：暴力解法需要更多资源（可用内存 + 60s），标准解法遵循题目限制
- **Mixin 模式**：提取公共编译/执行逻辑，减少代码重复
- **编译缓存**：避免重复编译相同代码，提升开发效率

### Dependencies

- 新增 `psutil>=5.9.0`：获取系统可用内存
- 新增 `pywin32>=306; sys_platform == 'win32'`：Windows Job Objects 支持
- 新增 `pyyaml>=6.0.0`：解析 problem.yaml 配置

### Notes & Caveats

- Windows 内存限制通过 Job Objects 实现，需要适当的进程权限
- macOS 平台仅支持超时控制，不支持内存限制
- 编译缓存默认存储在 `.cache/compile/` 目录

## [0.2.0] - 2026-03-31

### Features

- 添加 Interactor 基础验证逻辑，支持变异测试
- 添加 compiler.py 单元测试（14 个测试用例）
- 创建平台工具模块 `platform.py`，消除 `exe_ext` 判断的代码重复
- 拆分 `StressTestRunTool.execute` 函数，提高代码可读性

### Improvements

- 测试代码覆盖从约 50-60% 提升至 80%+
- 消除 10 处 `exe_ext` 重复代码
- 通过工具函数封装提高可维护性

### Code Quality

- 将平台相关逻辑集中到 `platform.py` 模块
- 重构过长函数，拆分为更小的辅助方法
- 更新类型注解和导入声明

### Breaking Changes

- 无

## [0.1.0] - 2025-03-30

### Features

- 初始化 AutoCode MCP Server 基础架构
- 实现 14 个原子工具：
  - File 工具组：`file_read`, `file_save`
  - Solution 工具组：`solution_build`, `solution_run`
  - Stress Test 工具组：`stress_test_run`
  - Problem 工具组：`problem_create`, `problem_generate_tests`, `problem_pack_polygon`
  - Validator 工具组：`validator_build`, `validator_select`
  - Generator 工具组：`generator_build`, `generator_run`
  - Checker 工具组：`checker_build`
  - Interactor 工具组：`interactor_build`
- 添加 testlib.h 和 C++ 代码模板
- 实现 MCP Resources 和 Prompts
- 添加 51 个测试用例

### Design Rationale

- **纯工具模式**：Server 不调用任何 LLM，由 Client 提供智能编排
- **无状态设计**：每次调用独立，状态由 `problem_dir` 参数管理
- **统一返回格式**：`{success, error, data}`

### Notes & Caveats

- Windows 平台不支持内存限制（ulimit）
- 需要 g++ 编译器支持 C++2c 标准
