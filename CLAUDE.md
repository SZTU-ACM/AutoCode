# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此代码仓库中工作时提供指导。

## 项目概述

AutoCode MCP Server 是基于论文《AutoCode: LLMs as Problem Setters for Competitive Programming》实现的竞赛编程出题辅助工具，提供 Validator-Generator-Checker 框架。

## 开发命令

```bash
# 安装依赖
uv sync

# 运行测试
uv run pytest tests/ -v

# 代码检查
uv run ruff check .

# 类型检查
uv run mypy src/

# 运行 MCP Server
uv run autocode-mcp
```

## 项目结构

```
AutoCode/
├── src/autocode_mcp/     # 源代码
│   ├── tools/            # MCP 工具实现
│   ├── resources/        # 模板资源
│   ├── prompts/          # 工作流提示词
│   └── utils/            # 工具函数
├── tests/                # 测试用例
├── templates/            # C++ 模板文件 (testlib.h 等)
└── pyproject.toml        # 项目配置
```

## 工具列表

| 工具 | 描述 |
|------|------|
| file_read | 读取文件 |
| file_save | 保存文件 |
| solution_build | 构建解法 |
| solution_run | 执行解法 |
| solution_analyze | 分析解法复杂度 |
| validator_build | 构建校验器 |
| validator_select | 选择最佳校验器 |
| generator_build | 构建生成器 |
| generator_run | 运行生成器 |
| checker_build | 构建检查器 |
| interactor_build | 构建交互器 |
| stress_test_run | 压力测试 |
| problem_create | 初始化题目 |
| problem_generate_tests | 生成测试数据 |
| problem_pack_polygon | 打包为 Polygon 格式 |

## 出题工作流程

1. 初始化题目目录 (`problem_create`)
2. 实现解法 (`solution_build`)
3. 构建校验器 (`validator_build`)
4. 构建生成器 (`generator_build`)
5. 运行压力测试 (`stress_test_run`)
6. 生成测试数据 (`problem_generate_tests`)

## 关键约束

- 包管理强制使用 `uv`（绝对禁用 pip/poetry/conda）
- 运行时强制使用 `uv run`
- C++ 标准使用 C++20（需要 GCC 10+）
