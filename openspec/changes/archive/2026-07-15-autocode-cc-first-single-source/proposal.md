## Why

AutoCode 的主界面是 Claude Code 插件，但项目仍背着一条并行的"非 CC 支持线"：从 PyPI 拉取的 MCP 本体、面向非 CC 客户端的 MCP `prompts`/`resources`、以及 README 里的 Cursor / OpenCode 文档。这条并行线才是真正的漂移源与维护负担——插件 `plugin.json` 停在 `1.0.5`、而 `pyproject.toml` 已领先到 `1.0.6`，两份版本各自为政。同时，题目目录里散落着 3+ 个隐藏 JSON 副产物，增加噪声。本变更让仓库同时成为代码与分发的单一真源，移除非 CC 暴露面，统一版本，并把所有"非题目的东西"收进一个被 git 忽略的文件。

## What Changes

- **BREAKING（分发）**：丢弃 PyPI 作为 MCP 运行期来源。`.mcp.json` 改为用仓库本地的 `uv run autocode-mcp` 拉起 server（不再 `uvx autocode-mcp`）；README 移除 PyPI 徽章 / 发布叙述与 `uvx` 调用方式。
- 移除 MCP `prompts` 能力：删除 `src/autocode_mcp/prompts/`，从 `server.py` 去掉 `list_prompts`/`get_prompt`，删除 `tests/test_prompts*.py`。
- 移除 MCP `resources` 能力：删除 `src/autocode_mcp/resources/__init__.py`，从 `server.py` 去掉 `list_resources`/`read_resource`，删除相关测试。server 变为纯 Tools 型。
- README：移除 Cursor / OpenCode（非 CC）使用段落，仅保留 CC 插件 + 本地开发叙述。
- 单一版本真源：`pyproject.toml` 的静态 `version` 为唯一版本权威；`src/autocode_mcp/__init__.py` 的 `__version__` 从 `pyproject.toml` 解析；`.claude-plugin/plugin.json` 由 `scripts/sync_plugin_version.py` 在发布时单向从 `pyproject.toml` 同步；修掉当前 `1.0.5` / `1.0.6` 漂移。
- 副产物统一：`.autocode-workflow/state.json`、`tests/.autocode_tests_manifest.json`、`.autocode_generate_state.json` 以及 `audit_report.json` 的内容，合并为题目目录下唯一的 `.autocode/runtime.json`，含 `workflow` / `test_manifest` / `generate_checkpoint` / `audit` 四个键；`.gitignore` 忽略 `.autocode/`。
- openspec 自洽：经核实 `openspec/config.yaml` 的 `rules:` 段仅含格式规则，不存在上述两条 stale 规则（实为 `context:` 指引且准确），故 config.yaml **保持原样不改动**；仅 MODIFY `knowledge-source-single-truth` spec。
- 修改 `knowledge-source-single-truth` spec：删除 prompts↔skills 一致性要求（prompts 已删），保留 Pydantic 派生 schema 要求，并把单一真源原则扩展到"仓库即 CC 插件的唯一真源"。

## Capabilities

### New Capabilities

- `cc-first-distribution`：定义 Claude Code 插件为唯一受支持的分发界面。MCP server 从仓库本地用 `uv run` 拉起（不再从 PyPI 获取）；非 CC 客户端暴露面（MCP `prompts`、`resources`、Cursor/OpenCode 文档）全部移除；单一版本真源（`src/__init__.py`）统管 package / plugin / PyPI 元数据。
- `runtime-byproduct-consolidation`：所有"非题目的"运行期副产物（工作流状态、测试清单、生成断点、审计报告）收进唯一被 git 忽略的文件 `.autocode/runtime.json`，题目目录不再散落多个隐藏 JSON。

### Modified Capabilities

- `knowledge-source-single-truth`：删除"prompts 与 skills 单一真源"要求（prompts 模块已删）；保留 Pydantic 派生 input schema 要求；将单一真源原则扩展到"仓库即 CC 插件的唯一真源"。

## Impact

- 文件：`.mcp.json`、`README.md`、`pyproject.toml`、`.claude-plugin/plugin.json`、`src/autocode_mcp/server.py`、`src/autocode_mcp/prompts/`（删）、`src/autocode_mcp/resources/__init__.py`（删）、`scripts/hook_state.py`、`scripts/sync_plugin_version.py`（新增）、`src/autocode_mcp/tools/{test_verify,stress_test,audit,problem}.py`、`tests/test_prompts*.py`（删）及 resources 相关测试。
- API：MCP 暴露面去掉 `prompts` 与 `resources`，server 变为纯 Tools 型。**22 个工具签名不变**（对外契约保留）。
- 依赖：运行期不再依赖已发布的 PyPI 包；要求 `uv` + 本地 checkout。
- 风险：移除 `prompts`/`resources` 会破坏仍依赖它们的非 CC MCP 客户端（可接受——这些客户端已不在范围内）；`audit_report.json` 的 CLI `--report` 行为需变（报告改存 `.autocode/runtime.json`，`--report` 写出路径在 design 中协调）；版本统一需同步三处。
- Non-goals：不改变 22 个 MCP 工具签名；不改变 12 步门禁工作流或门禁语义；不合并 `scripts/` 文件。
