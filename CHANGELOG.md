# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
