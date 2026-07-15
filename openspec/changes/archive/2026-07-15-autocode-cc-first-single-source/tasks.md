## 1. 砍 PyPI 分发（A）

- [x] 1.1 改 `.mcp.json`：`command: uvx, args: [autocode-mcp]` → `command: uv, args: ["run", "autocode-mcp"]`
- [x] 1.2 README 改 `uvx` → `uv run`，移除 PyPI 徽章与发布叙述，仅保留 CC 插件 + 本地开发

## 2. 砍非 CC 暴露面（B, B'）

- [x] 2.1 删除 `src/autocode_mcp/prompts/` 目录
- [x] 2.2 `server.py` 移除 `list_prompts` / `get_prompt` handler（MCP 不再暴露 prompts）
- [x] 2.3 删除 `src/autocode_mcp/resources/__init__.py`，`server.py` 移除 `list_resources` / `read_resource`（MCP 变纯 Tools 型）
- [x] 2.4 删除 `tests/test_prompts*.py` 及 resources 相关测试
- [x] 2.5 README 砍 Cursor / OpenCode（裸 MCP 客户端）使用段落
- [x] 2.6 `server.py` 清理 import（F2）：删除 L27 `from . import prompts, resources`，并删除 `mcp.types` 中仅被 prompt/resource handler 使用的导入 `GetPromptResult`(L17)/`Prompt`(L18)/`PromptMessage`(L19)/`ReadResourceResult`(L20)/`Resource`(L21)/`TextResourceContents`(L23)，保留仍被 `call_tool` 使用的 `TextContent` 等类型

## 3. 版本统一（C）

- [x] 3.1 `pyproject.toml` 保留静态 `version`（单一版本权威源）；`src/autocode_mcp/__init__.py` 的 `__version__` 改为从 `pyproject.toml` 解析（发布脚本或 `importlib.metadata`），不再手工维护
- [x] 3.2 `.claude-plugin/plugin.json` 版本由脚本从 `pyproject.toml` 同步（路径精确为 `.claude-plugin/plugin.json`，非仓库根）
- [x] 3.3 验证 `uv run python -c "import autocode_mcp; print(autocode_mcp.__version__)"` 与 `pyproject.toml`/`plugin.json` 三者一致
- [x] 3.4 新增 `scripts/sync_plugin_version.py`：发布/build 时用 `tomllib` 读 `pyproject.toml` version，写回 `.claude-plugin/plugin.json` 的 `version` 与 `src/autocode_mcp/__init__.py` 的 `__version__`，`pyproject.toml` 为唯一权威

## 4. 副产物收口（E）

- [x] 4.1 新增共享 `runtime_store` helper，集中 `.autocode/runtime.json` 路径与 `workflow`/`test_manifest`/`generate_checkpoint`/`audit` 键读写
- [x] 4.2 `scripts/hook_state.py`：`save_state` / `load_state` 改读写 `.autocode/runtime.json` 的 `workflow` 键（含 `full_audit`）
- [x] 4.3 统一三处散落副产物常量到单一 `runtime_store`（消除重复定义，F4）：
  - `tools/problem.py`：`_TEST_MANIFEST_FILENAME`(L61) / `_GENERATE_STATE_FILENAME`(L62) 与 state 路径改为 runtime store 的 `test_manifest` / `generate_checkpoint` 键
  - `tools/test_verify.py`：`_TEST_MANIFEST_FILENAME`(L29) 重复常量改为引用 `runtime_store`
  - `tools/audit.py`：`_WORKFLOW_STATE`(L25) / `_TEST_MANIFEST`(L26) 改为引用 `runtime_store` 的 `workflow` / `test_manifest` 键（注意 `_WORKFLOW_STATE` 当前写 `problem_root/.autocode-workflow/state.json`，不在 `tests/` 下）
- [x] 4.4 `tools/test_verify.py`：改读 runtime store 的 `test_manifest` 键
- [x] 4.5 `tools/stress_test.py`：改读 `workflow` 键的 complexity 上下文
- [x] 4.6 `tools/audit.py`：改读 `workflow`/`test_manifest` 键；`audit` 内容写入 `audit` 键，并协调 `autocode-audit --report` 行为
- [x] 4.7 `.gitignore` 追加 `.autocode/`

## 5. openspec 自洽（D）

- [x] 5.1 （已撤销，F1）`openspec/config.yaml` 的 `rules:` 段仅含格式规则，不存在"22 工具不可改""openspec 必须 git-ignore"两条规则（二者实为 `context:` 指引且准确，且 Non-goals 依赖前者）——故 config.yaml **保持原样，不改动**
- [x] 5.2 确认 `knowledge-source-single-truth` 的 REMOVED/MODIFIED delta 已写入 `specs/knowledge-source-single-truth/spec.md`

## 6. 验证

- [x] 6.1 `openspec validate autocode-cc-first-single-source`
- [x] 6.2 跑目标测试：`uv run pytest tests/scripts tests/test_compiler.py tests/test_tools/test_problem.py tests/test_process.py tests/test_audit.py`
- [x] 6.3 gstack `autoplan` 跑 CEO/design/eng/DX 四视角裁决（MODIFY 已归档 spec 需 CEO 通过）——四视角审查已在变更规划阶段完成（F1-F4 偏差已定位并就地修订，config.yaml 保持不变，pyproject.toml 为版本权威源）

> 注意：所有步骤不得改动 22 个 MCP 工具签名（对外契约保留），仅移除 prompts/resources 两个 MCP 能力并重构内部副产物存储。
