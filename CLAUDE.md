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
| FileSaveTool | 保存文件 |
| FileReadTool | 读取文件 |
| FileListTool | 列出文件 |
| SolutionBuildTool | 构建解法 |
| SolutionTestTool | 测试解法 |
| StressTestTool | 压力测试 |
| ProblemInitTool | 初始化题目 |
| ProblemGenerateTestsTool | 生成测试数据 |
| ValidatorBuildTool | 构建校验器 |
| ValidatorSelectTool | 选择最佳校验器 |
| GeneratorBuildTool | 构建生成器 |
| GeneratorRunTool | 运行生成器 |
| CheckerBuildTool | 构建检查器 |
| InteractorBuildTool | 构建交互器 |

## 出题工作流程

1. 初始化题目目录 (`ProblemInitTool`)
2. 实现解法 (`SolutionBuildTool`)
3. 构建校验器 (`ValidatorBuildTool`)
4. 构建生成器 (`GeneratorBuildTool`)
5. 运行压力测试 (`StressTestTool`)
6. 生成测试数据 (`ProblemGenerateTestsTool`)

## 关键约束

- 包管理强制使用 `uv`（绝对禁用 pip/poetry/conda）
- 运行时强制使用 `uv run`
- C++ 标准使用 C++2c
