# CLAUDE.md

此文件为 Claude Code (claude.ai/code) 在此代码仓库中工作时提供指导。

## 项目概述

AutoCode 是一个 Claude Code plugin，默认以远程仓库安装使用；仓库内部同时包含 `autocode-mcp` 这个 MCP server 实现。它基于论文《AutoCode: LLMs as Problem Setters for Competitive Programming》实现竞赛编程出题辅助能力，并提供 Validator-Generator-Checker 工作流约束。

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

# 校验 Claude plugin 结构
claude plugin validate .

# 运行 MCP Server（本地开发/测试）
uv run autocode-mcp
```

## 项目结构

```
AutoCode/
├── .claude-plugin/       # Claude plugin manifest
├── agents/               # Claude plugin agent definitions
├── hooks/                # Claude hook config
├── scripts/              # Hook/runtime helper scripts
├── skills/               # Claude plugin skills
├── src/autocode_mcp/     # MCP server 源代码
│   ├── tools/            # MCP 工具实现
│   ├── templates/        # 内置模板资源
│   ├── prompts/          # 工作流提示词
│   └── utils/            # 工具函数
├── tests/                # 测试用例
├── .mcp.json             # 本地 MCP 接入配置
├── settings.json         # Claude plugin settings
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
2. 构建标准解 (`solution_build`, `solution_type=sol`)
3. 构建暴力解 (`solution_build`, `solution_type=brute`)
4. 构建校验器 (`validator_build`, accuracy >= 0.9)
5. 构建生成器 (`generator_build`)
6. 运行压力测试 (`stress_test_run`, completed_rounds == total_rounds)
7. 按需构建检查器 (`checker_build`, accuracy >= 0.9)
8. 生成测试数据 (`problem_generate_tests`, generated_test_count > 0)
9. 打包 Polygon (`problem_pack_polygon`)

该顺序会被 [hooks/hooks.json](/c:/userProgram/program/AutoCode/hooks/hooks.json) 和 [scripts/workflow_guard.py](/c:/userProgram/program/AutoCode/scripts/workflow_guard.py) 实际强制执行。

## 关键约束

- 包管理强制使用 `uv`（绝对禁用 pip/poetry/conda）
- 对外分发形态优先是 Claude plugin，不是单独的本地 MCP 配置
- 默认主路径是远程 plugin 安装；本地模式只用于开发、测试、验证
- `hooks/` 只放 hook 配置，hook 逻辑脚本放在 `scripts/`
- 模板资源统一放在 `src/autocode_mcp/templates/`，不要再在仓库根目录维护一份重复模板
- C++ 标准使用 C++20（需要 GCC 10+）
