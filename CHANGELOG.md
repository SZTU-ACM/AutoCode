# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

- Interactor 工具的交互测试逻辑尚未完全实现（有 TODO 标记）
- Windows 平台不支持内存限制（ulimit）
- 需要 g++ 编译器支持 C++2C 标准
