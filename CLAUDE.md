# CLAUDE.md

此文件为 Claude Code / Cursor Agent 在本仓库工作时提供项目级指导。

## 项目定位

AutoCode 是一个 **Claude Code plugin**，面向竞赛编程出题工作流。仓库内部同时包含 `autocode-mcp` MCP server，但对外主路径是远程 plugin 安装，而不是单独本地 MCP 配置。

它要解决的核心问题不是“让 AI 直接写完一道题”，而是把 AI 生成的题面、解法、validator、generator、checker/interactor、对拍、测试数据和 Polygon 打包放进可验证、可审计、会阻止跳步的流程。

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

# 校验 Claude plugin 结构
claude plugin validate .

# 运行 MCP Server（本地开发/测试）
uv run autocode-mcp

# 校验题目 manifest
uv run autocode-verify examples/exact-sample

# 构建并检查分发包
uv build
uv run twine check dist/*
```

## 项目结构

```text
AutoCode/
├── .claude-plugin/        # Claude plugin manifest
├── agents/                # Claude plugin agent definitions
├── hooks/                 # Claude hook config
├── scripts/               # Hook/runtime helper scripts
├── skills/                # Claude plugin skills
├── examples/              # manifest/examples smoke samples
├── src/autocode_mcp/      # MCP server 源代码
│   ├── cli/               # autocode-verify 等 CLI
│   ├── tools/             # MCP 工具实现
│   ├── templates/         # 内置模板资源
│   ├── workflow/          # autocode.json manifest 模型
│   ├── prompts/           # 工作流提示词
│   └── utils/             # 编译、运行、资源限制等工具函数
├── tests/                 # 测试用例
├── .mcp.json              # 本地 MCP 接入配置（开发/兼容用）
├── settings.json          # Claude plugin 默认 agent
└── pyproject.toml         # Python package / scripts
```

## 工具列表

AutoCode 当前暴露 20 个 MCP 工具：

| 分组 | 工具 |
|------|------|
| 文件 | `file_read`, `file_save` |
| 解法 | `solution_build`, `solution_run`, `solution_analyze`, `solution_audit_std`, `solution_audit_brute` |
| Validator | `validator_build`, `validator_select` |
| Generator | `generator_build`, `generator_run` |
| Checker / Interactor | `checker_build`, `interactor_build` |
| Stress | `stress_test_run` |
| Problem | `problem_create`, `problem_validate`, `problem_generate_tests`, `problem_cleanup_processes`, `problem_verify_tests`, `problem_pack_polygon` |

## 题目目录结构

`problem_create` 初始化后的目录布局：

```text
<problem_dir>/
├── autocode.json
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
    ├── 01.ans / 01.out
    └── .autocode_tests_manifest.json
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

该顺序由 `hooks/hooks.json` 和 `scripts/workflow_guard.py` 实际强制执行。

1. `problem_create`
2. `solution_build(solution_type="sol")`
3. `solution_build(solution_type="brute")`
4. 解法审计：`solution_analyze`、`solution_audit_std`、`solution_audit_brute`
5. 非交互题：`validator_build(accuracy >= 0.9)`；交互题：`interactor_build`
6. `generator_build`
7. `checker_build` 与 `stress_test_run`：`scripts/workflow_guard.py` 规定，非 SPJ 须在 `stress_test_run(completed_rounds == total_rounds)` **之后**再 `checker_build(accuracy >= 0.9)`（非交互）。若在 `autocode.json` 设置 `special_judge: true` 且 `stress_comparison: "checker"`，可在 stress **之前**先完成 `checker_build`，且 `stress_test_run` 会用 checker 判定 sol/brute（详见内置 checker 提示词中的 argv 约定）。
8. `problem_validate(validation_passed)`
9. `problem_generate_tests(generated_test_count > 0)`
10. `problem_verify_tests(passed)`（`special_judge` 时以 checker 校验终测与错解，而非仅字符串比对）
11. `problem_pack_polygon`（存在 `files/checker.cpp` 时生成的 `problem.xml` 会带上 checker）

关键门禁：

- `brute` 必须在 `sol` 之后构建。
- `validator_build` 没有有效 `accuracy` 不算通过。
- 交互题不可运行 `validator_build` / `checker_build`，应使用 `interactor_build`。
- 最终测试生成后会清除旧的 `tests_verified`，必须重新跑 `problem_verify_tests`。
- `problem_pack_polygon` 前必须完成最终测试验证。
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

## Manifest

每个题目应维护 `autocode.json` 作为人类和 CI 都可读的题目契约。模型位于 `src/autocode_mcp/workflow/`，模板位于 `src/autocode_mcp/templates/autocode.json`。

快速校验：

```bash
uv run autocode-verify <problem_dir>
```

## 关键约束

- 包管理强制使用 `uv`；不要引入 pip/poetry/conda 流程。
- 对外文档优先描述 Claude Code plugin；MCP server 是实现与开发入口。
- 默认主路径是远程 plugin 安装；本地模式只用于开发、测试、验证。
- `hooks/` 只放 hook 配置，hook 逻辑脚本放在 `scripts/`。
- 模板资源统一放在 `src/autocode_mcp/templates/`。
- C++ 标准使用 C++20（需要 GCC 10+）。
- 本项目会编译并执行 AI 生成代码，只能在本地可信环境使用。
