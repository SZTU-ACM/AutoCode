# AutoCode MCP Server

基于论文《AutoCode: LLMs as Problem Setters for Competitive Programming》实现的竞赛编程出题辅助 MCP Server。

## 特性

- **Validator-Generator-Checker 框架**：自动化验证数据正确性
- **14 个原子工具**：文件操作、解法构建、压力测试等
- **testlib.h 支持**：完整的 testlib 模板和编译工具
- **MCP 协议**：支持 Claude Code 等 AI 工具集成

## 安装

```bash
uv sync
```

## 开发

```bash
# 运行测试
make test

# 代码检查
make lint

# 类型检查
make typecheck

# 完整检查
make check
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
├── templates/            # C++ 模板文件
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

## 许可证

MIT License
