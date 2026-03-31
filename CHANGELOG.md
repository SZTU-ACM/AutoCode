# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-31

### Features

- 完成 Interactor 完整验证逻辑，实现真实的交互测试
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
