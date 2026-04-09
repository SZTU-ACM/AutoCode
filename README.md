# AutoCode MCP Server

[![PyPI version](https://badge.fury.io/py/autocode-mcp.svg)](https://badge.fury.io/py/autocode-mcp)
[![Python](https://img.shields.io/pypi/pyversions/autocode-mcp.svg)](https://pypi.org/project/autocode-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/Protocol-MCP-blue.svg)](https://modelcontextprotocol.io/)

**基于论文《AutoCode: LLMs as Problem Setters for Competitive Programming》实现的竞赛编程出题辅助 MCP Server。**

AutoCode MCP Server 提供 15 个原子工具，让 AI 助手能够创建、验证和测试竞赛编程题目。它负责编译、执行、压力测试和测试数据生成——让 AI 专注于题目设计和解法逻辑。

## 特性

- **Validator-Generator-Checker 框架** — 基于论文实现输入正确性自动验证、多策略测试生成和输出验证
- **15 个原子工具** — 文件操作、解法构建、压力测试、校验器/生成器/检查器构建等
- **testlib.h 支持** — 完整集成竞赛编程标准库，用于校验器、生成器和检查器
- **多策略生成** — 四种生成策略：tiny（穷举）、random（随机）、extreme（边界情况）、tle（诱导超时）
- **压力测试** — 自动比较最优解和暴力解，可配置测试轮数
- **MCP 协议** — 原生支持 Claude Code、Cursor 等 MCP 兼容的 AI 工具
- **执行控制** — 超时控制、内存限制（Linux）、临时目录隔离（仅限本地可信环境）
- **Polygon 打包** — 导出为 Polygon 格式，适用于 Codeforces 等平台

## 安装

### Claude Code 插件安装（推荐）

本仓库已经按官方 Claude Code plugin 结构组织：

- [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)
- [`settings.json`](settings.json)
- [`agents/autocode-workflow.md`](agents/autocode-workflow.md)
- [`hooks/hooks.json`](hooks/hooks.json)
- [`scripts/workflow_guard.py`](scripts/workflow_guard.py)
- [`.mcp.json`](.mcp.json)

这个插件不只是暴露 MCP server，还会一起安装：

- 默认工作流 agent
- AutoCode 工作流 skill
- 用于阻止跳步的 hooks

如果你要做个人第三方 marketplace，可直接使用同级仓库 `../autocode-marketplace`。它默认走 GitHub 远程安装，不需要上官方市场。

如果当前环境不走 Claude Code 插件安装，也可以直接在 Claude Code 中添加 MCP server：

```bash
claude mcp add autocode -- uvx autocode-mcp
```

### 从 PyPI 安装（推荐）

```bash
pip install autocode-mcp
```

### 使用 uv 安装

```bash
uv tool install autocode-mcp
```

### 从源码安装

```bash
git clone https://github.com/SummerOneTwo/AutoCode.git
cd AutoCode
uv sync
```

### 前置要求

- **Python 3.10+**
- **g++ 编译器**，支持 C++20（推荐 GCC 10+）
- **testlib.h**（已内置在 `src/autocode_mcp/templates/`）

验证安装：

```bash
# 检查 Python 版本
python --version

# 检查 g++ 版本
g++ --version

# 运行测试
uv run pytest tests/ -v
```

## 快速开始

### 1. 安装插件

推荐方式：

- 以 Claude Code plugin 方式安装本仓库。
- 本地开发时可用 `claude --plugin-dir .` 测试插件行为。
- 如果走个人 marketplace，可直接执行：

```bash
claude plugin marketplace add ../autocode-marketplace
claude plugin install autocode@autocode-marketplace
```

注意：marketplace 默认从 `SummerOneTwo/AutoCode` 远程安装，所以测试远程安装前需要先把插件改动推到 GitHub。

直接接入 Claude Code 的备用方式：

```bash
claude mcp add autocode -- uvx autocode-mcp
```

项目级备用方式：

```json
{
  "mcpServers": {
    "autocode": {
      "command": "autocode-mcp"
    }
  }
}
```

### 2. 创建你的第一个题目

在 Claude Code 中，只需说：

> "创建一道竞赛编程题目：给定两个整数 A 和 B，输出它们的和。"

Claude 将使用 AutoCode 工具：
1. 生成题目描述
2. 实现解法（最优解 + 暴力解）
3. 构建校验器和生成器
4. 运行压力测试
5. 生成最终测试数据

### 3. 手动调用工具

你也可以直接调用工具：

```python
# 构建解法
solution_build(
    problem_dir="problems/ab",
    solution_type="sol",
    code="#include <iostream>\nint main() { int a, b; std::cin >> a >> b; std::cout << a + b; }"
)

# 运行压力测试
stress_test_run(problem_dir="problems/ab", trials=100)
```

## 客户端配置

### 传输层与兼容性

**推荐方式**：直接作为 Claude Code plugin 安装。

**当前支持**：仅支持本地 stdio 传输。服务器通过标准输入/输出流通信，适用于本地可信环境。

| 客户端 | 状态 | 说明 |
|--------|------|------|
| Claude Code | ✅ 已验证 | 主要开发环境 |
| Cursor | ⚠️ 配置已提供 | 尚未端到端测试 |
| OpenCode | ⚠️ 配置已提供 | 尚未端到端测试 |

**不支持**：HTTP/SSE 传输、远程连接或多租户环境。

### Claude Code Plugin

推荐的插件文件：

- [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json)
- [`settings.json`](settings.json)
- [`agents/autocode-workflow.md`](agents/autocode-workflow.md)
- [`hooks/hooks.json`](hooks/hooks.json)
- [`scripts/workflow_guard.py`](scripts/workflow_guard.py)

直接 MCP 接入的备用方式：

```bash
claude mcp add autocode -- uvx autocode-mcp
```

手动配置方式：

```json
{
  "mcpServers": {
    "autocode": {
      "command": "autocode-mcp"
    }
  }
}
```

### 其他 MCP 客户端

同一个 MCP server 也可用于其他本地 MCP 客户端，但工作流强制约束的 agent 和 hooks 是 Claude Code plugin 能力。

### Cursor

添加到 Cursor 设置（Settings → MCP）：

```json
{
  "mcp": {
    "servers": {
      "autocode": {
        "command": "autocode-mcp"
      }
    }
  }
}
```

### OpenCode

编辑 `~/.config/opencode/opencode.json`：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "autocode": {
      "type": "local",
      "command": ["autocode-mcp"],
      "enabled": true
    }
  }
}
```

或使用 `uvx` 无需预安装：

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "autocode": {
      "type": "local",
      "command": ["uvx", "autocode-mcp"],
      "enabled": true
    }
  }
}
```

### 从源码运行（开发模式）

用于开发或自定义安装：

```json
{
  "mcpServers": {
    "autocode": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/autocode-mcp", "autocode-mcp"]
    }
  }
}
```

### 验证安装

配置完成后，重启 MCP 客户端并检查工具是否可用。你应该能看到 15 个工具，包括 `solution_build`、`validator_build`、`generator_build` 等。

## 工具参考

AutoCode 提供 15 个原子工具，分为 7 组。所有工具返回统一格式：

```json
{
  "success": true,
  "error": null,
  "data": { ... }
}
```

### 文件操作

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `file_save` | 保存内容到文件 | `path`, `content` |
| `file_read` | 读取文件内容 | `path` |

### 解法工具

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `solution_build` | 编译解法代码 | `problem_dir`, `solution_type` ("sol"/"brute"), `code` |
| `solution_run` | 执行已编译的解法 | `problem_dir`, `solution_type`, `input_data`, `timeout` |

### 校验器工具

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `validator_build` | 构建并测试校验器 | `problem_dir`, `code`, `test_cases` |
| `validator_select` | 从候选中选择最佳校验器 | `candidates` |

### 生成器工具

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `generator_build` | 编译生成器 | `problem_dir`, `code` |
| `generator_run` | 生成测试输入 | `problem_dir`, `strategies`, `test_count`, `validator_path` |

### 检查器工具

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `checker_build` | 构建输出检查器 | `problem_dir`, `code`, `test_scenarios` |

### 交互器工具

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `interactor_build` | 构建交互题的交互器 | `problem_dir`, `code`, `reference_solution_path`, `mutant_solutions` |

### 压力测试

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `stress_test_run` | 比较 sol 和 brute 输出 | `problem_dir`, `trials`, `n_max`, `timeout` |

### 题目管理

| 工具 | 描述 | 关键参数 |
|------|------|----------|
| `problem_create` | 初始化题目目录 | `problem_dir`, `problem_name` |
| `problem_generate_tests` | 生成最终测试数据 | `problem_dir`, `test_count` |
| `problem_pack_polygon` | 打包为 Polygon 格式 | `problem_dir`, `time_limit`, `memory_limit` |

## 工作流教程：A+B 问题

本教程演示如何使用 AutoCode 工具创建一道简单的 A+B 问题。

### 步骤 1：初始化题目

```python
problem_create(
    problem_dir="problems/ab",
    problem_name="A + B"
)
```

### 步骤 2：实现解法

**最优解（sol.cpp）：**
```cpp
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
    return 0;
}
```

**暴力解（brute.cpp）：**
```cpp
#include <iostream>
int main() {
    int a, b;
    std::cin >> a >> b;
    // 对于 A+B 问题与最优解相同，但复杂问题可能更慢
    std::cout << a + b << std::endl;
    return 0;
}
```

构建两个解法：
```python
solution_build(problem_dir="problems/ab", solution_type="sol", code="...")
solution_build(problem_dir="problems/ab", solution_type="brute", code="...")
```

### 步骤 3：构建校验器

```cpp
#include "testlib.h"
int main(int argc, char* argv[]) {
    registerValidation(argc, argv);
    int a = inf.readInt(-1000, 1000, "a");
    inf.readSpace();
    int b = inf.readInt(-1000, 1000, "b");
    inf.readEoln();
    inf.readEof();
    return 0;
}
```

构建并测试：
```python
validator_build(
    problem_dir="problems/ab",
    code="...",
    test_cases=[
        {"input": "1 2\n", "expected_valid": True},
        {"input": "0 0\n", "expected_valid": True},
        {"input": "-1000 1000\n", "expected_valid": True},
        {"input": "1001 0\n", "expected_valid": False},  # 超出范围
        {"input": "1 2 3\n", "expected_valid": False},   # 多余数字
    ]
)
```

### 步骤 4：构建生成器

```cpp
#include "testlib.h"
#include <iostream>
int main(int argc, char* argv[]) {
    registerGen(argc, argv, 1);
    int seed = atoi(argv[1]);
    int type = atoi(argv[2]);
    rnd.setSeed(seed);
    
    int a = rnd.next(-1000, 1000);
    int b = rnd.next(-1000, 1000);
    std::cout << a << " " << b << std::endl;
    return 0;
}
```

构建并运行：
```python
generator_build(problem_dir="problems/ab", code="...")

generator_run(
    problem_dir="problems/ab",
    strategies=["random", "extreme"],
    test_count=20,
    validator_path="problems/ab/val.exe"
)
```

### 步骤 5：压力测试

```python
stress_test_run(
    problem_dir="problems/ab",
    trials=1000,
    n_max=100,
    timeout=30
)
```

预期输出：
```
All 1000 rounds passed
```

### 步骤 6：生成最终测试

```python
problem_generate_tests(
    problem_dir="problems/ab",
    test_count=50
)
```

### 步骤 7：打包为 Polygon 格式

```python
problem_pack_polygon(
    problem_dir="problems/ab",
    time_limit=1,
    memory_limit=256
)
```

## 架构

### Validator-Generator-Checker 框架

```
┌─────────────────┐
│   题面设计      │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  解法构建       │────►│  Validator   │ 验证输入约束
│  (sol + brute)  │     └──────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────────┐
│  Generator      │────►│  Stress Test │ 比较 sol 和 brute
│  多策略生成     │     └──────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Checker        │ 验证输出正确性
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Polygon 打包   │ 导出为平台格式
└─────────────────┘
```

### 设计原则

1. **纯工具模式，无 LLM** — Server 提供编译、执行和验证。所有代码生成由客户端 LLM 完成。

2. **无状态设计** — 每次工具调用独立。状态通过 `problem_dir` 参数管理。

3. **统一返回格式** — 所有工具返回 `{success, error, data}`，便于一致的错误处理。

4. **安全执行** — 超时控制、内存限制（Linux 通过 prlimit）、临时目录隔离。

### 安全边界

⚠️ **重要提示：本工具仅适用于本地可信环境**

#### 文件操作

- **指定 `problem_dir` 参数时**：`file_read` 和 `file_save` 限制在指定目录内访问
- **不指定 `problem_dir` 参数时**：这些工具可以读写**任意路径**的文件
- **建议**：调用文件操作时始终指定 `problem_dir` 以限制访问范围

#### 代码执行

- 编译并执行 AI 生成的 C++ 代码，仅提供时间/内存限制
- 无沙箱隔离（Linux 上仅通过 `prlimit` 限制内存）
- **风险**：畸形或恶意代码可能影响系统

#### 适用场景

✅ **适用于**：
- 本地开发机器
- 竞赛编程出题
- 可信环境下的 AI 辅助编程
- 有定期备份的个人工作站

❌ **不适用于**：
- 多租户环境
- 不可信代码执行
- 生产级代码运行平台
- 无隔离的共享服务器

#### 安全加固建议

如需更强的安全隔离，建议：
- 在 Docker 容器中运行
- 使用虚拟机
- 在操作系统层面限制文件系统权限
- 以非特权用户身份运行

### 生成策略

| 策略 | 类型码 | 用途 |
|------|--------|------|
| `tiny` | 1 | 小数据穷举测试（N ≤ 10） |
| `random` | 2 | 约束范围内的随机数据 |
| `extreme` | 3 | 边界情况：溢出、精度、hash 碰撞 |
| `tle` | 4 | 诱导 TLE 的性能测试数据 |

### 文件结构

```
problems/your-problem/
├── files/
│   ├── testlib.h       # 竞赛编程标准库
│   ├── gen.cpp         # 测试生成器
│   ├── val.cpp         # 输入校验器
│   ├── checker.cpp     # 输出检查器（可选）
│   └── interactor.cpp  # 交互器（交互题）
├── solutions/
│   ├── sol.cpp         # 最优解
│   └── brute.cpp       # 暴力解（用于验证）
├── statements/
│   └── README.md       # 题目描述
├── tests/
│   ├── 01.in           # 测试输入
│   ├── 01.ans          # 期望输出
│   └── ...
└── problem.xml         # Polygon 配置
```

## 开发

### 环境搭建

```bash
git clone https://github.com/SummerOneTwo/AutoCode.git
cd AutoCode
uv sync
```

### 运行测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 运行并生成覆盖率报告
uv run pytest tests/ --cov=src/autocode_mcp --cov-report=html

# 运行特定测试文件
uv run pytest tests/test_compiler.py -v
```

### 代码质量

```bash
# Lint 检查
uv run ruff check .

# 类型检查
uv run mypy src/

# 格式化
uv run ruff format .
```

### 项目结构

```
autocode-mcp/
├── src/autocode_mcp/
│   ├── tools/           # MCP 工具实现
│   │   ├── base.py      # 工具基类
│   │   ├── solution.py  # 解法工具
│   │   ├── validator.py # 校验器工具
│   │   ├── generator.py # 生成器工具
│   │   ├── checker.py   # 检查器工具
│   │   ├── stress_test.py
│   │   └── ...
│   ├── utils/
│   │   ├── compiler.py  # C++ 编译工具
│   │   └── platform.py  # 平台相关辅助函数
│   ├── prompts/         # 工作流提示词模板
│   ├── resources/       # 模板资源
│   └── server.py        # MCP server 入口
├── tests/               # 测试套件
└── pyproject.toml
```

### 添加新工具

1. 在 `src/autocode_mcp/tools/` 创建新文件
2. 继承 `Tool` 基类
3. 实现 `name`、`description`、`input_schema` 和 `execute()`
4. 在 `server.py` 中注册
5. 在 `tests/` 中添加测试

## 贡献

查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献指南。

## 故障排查

查看 [TROUBLESHOOTING.md](TROUBLESHOOTING.md) 了解常见问题和解决方案。

## 许可证

MIT License - 详见 [LICENSE](LICENSE)。

## 致谢

- 基于论文 ["AutoCode: LLMs as Problem Setters for Competitive Programming"](https://arxiv.org/abs/2510.12803)
- 使用 [testlib.h](https://github.com/MikeMirzayanov/testlib) 竞赛编程工具库
- 基于 [Model Context Protocol](https://modelcontextprotocol.io/) 构建

## 链接

- [文档](https://github.com/SummerOneTwo/AutoCode#readme)
- [PyPI](https://pypi.org/project/autocode-mcp/)
- [GitHub](https://github.com/SummerOneTwo/AutoCode)
- [Issue Tracker](https://github.com/SummerOneTwo/AutoCode/issues)
