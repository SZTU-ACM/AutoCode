# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
