## Context

AutoCode 当前存在两套版本与分发来源：仓库（agents / skills / hooks / `.mcp.json`）与 PyPI（`autocode-mcp` 包）。Claude Code 装插件资产，运行时由 `.mcp.json` 的 `uvx autocode-mcp` 从 PyPI 拉起 server。这导致三类问题：

1. **版本漂移根**：插件资产（`plugin.json` `1.0.5`）与 PyPI 包（`pyproject.toml` `1.0.6`）各自为政，靠人工同步，当前已漂移。
2. **非 CC 第二暴露面**：为读不到 skills 的非 CC 客户端维护 MCP `prompts` 与 `resources`，与 CC 自身的 skills + `file_read` 能力重复。
3. **散落副产物**：题目目录里散布 `.autocode-workflow/state.json`、`tests/.autocode_tests_manifest.json`、`.autocode_generate_state.json`、`audit_report.json` 等多个隐藏 JSON。

本设计让仓库同时成为代码与运行的**唯一真源**，server 从仓库本地 `uv run` 启动，砍掉非 CC 暴露面，统一版本，收口副产物。

## Goals / Non-Goals

**Goals:**
- 仓库同时是代码与运行期 server 的唯一来源（不再依赖 PyPI 运行期包）。
- MCP server 仅暴露 Tools（删除 `prompts` 与 `resources` 能力）。
- 单一版本真源，消除 `1.0.5` / `1.0.6` 漂移。
- 题目目录中"非题目的"运行期副产物收进 `.autocode/runtime.json` 并 git 忽略。

**Non-Goals:**
- 不改变 22 个 MCP 工具签名（对外契约保留）。
- 不改变 12 步门禁工作流或门禁语义（`unified-gate-validation` 不受影响）。
- 不合并 `scripts/` 的 4 个文件。
- 不引入新的运行时依赖或外部服务。

## Decisions

### D1: server 启动方式 `uvx` → `uv run`
`.mcp.json` 由 `command: uvx, args: [autocode-mcp]` 改为 `command: uv, args: ["run", "autocode-mcp"]`，假定仓库（资产 + 源码同仓）位于 workspace 内。
- **理由**：去掉 PyPI 版本源，server 与 agents/skills 同仓同版本，漂移根一次性消除。
- **备选（否决）**：保留 `uvx` 并加版本锁——仍是两套源，只是缩小差距，未根除。

### D2: 删 `prompts` + `resources`，MCP 变纯 Tools
删除 `src/autocode_mcp/prompts/` 与 `src/autocode_mcp/resources/__init__.py`，`server.py` 移除 `list_prompts`/`get_prompt` 与 `list_resources`/`read_resource`；并清理 `server.py` 顶部 import：删除 L27 `from . import prompts, resources` 及 `mcp.types` 中仅服务于上述 handler 的 6 个类型（`GetPromptResult`/`Prompt`/`PromptMessage`/`ReadResourceResult`/`Resource`/`TextResourceContents`，L17/L18/L19/L20/L21/L23），保留 `call_tool` 仍用的 `TextContent` 等。
- **理由**：CC 侧用 skills 提供契约、`file_read` 读取模板与题目文件，prompts/resources 仅服务非 CC 客户端，已被砍文档面印证其为冗余的第二暴露面。已核实 `skills/`、`agents/` 无任何 `template://` 或 MCP Resource 引用。
- **备选（否决）**：保留 `resources` 作为 CC 读模板的备用通道——`file_read` 已完全覆盖。

### D3: 单一版本真源（`pyproject.toml` 为权威）
`pyproject.toml` 保留静态 `version` 作为唯一版本权威；`src/autocode_mcp/__init__.py` 的 `__version__` 改为从 `pyproject.toml` 解析（`importlib.metadata` 或发布脚本），不再手工维护；`.claude-plugin/plugin.json`（路径精确，非仓库根）的 `version` 在发布/build 步骤由 `scripts/sync_plugin_version.py` 单向从 `pyproject.toml` 写回。
- **理由**：`pyproject.toml` 是 uv/Python 包标准版本声明，作为权威最自然；plugin.json 与 `__version__` 均单向派生，消除 `1.0.5`/`1.0.6` 双源漂移。
- **备选（否决）**：CI 断言 `plugin.json == pyproject`——治标，仍两套真源。
- **备选（否决）**：`__init__.py` 为权威、pyproject 反向 dynamic——与 uv 构建依赖 pyproject 的惯例相悖。

### D4: 副产物收口到 `.autocode/runtime.json`
题目目录建立隐藏目录 `.autocode/`，单一 `runtime.json`，四个顶层键：

```text
.autocode/
└── runtime.json
     {
       "workflow":           { ... },   # 原 .autocode-workflow/state.json（含 full_audit）
       "test_manifest":      { ... },   # 原 tests/.autocode_tests_manifest.json
       "generate_checkpoint":{ ... },   # 原 .autocode_generate_state.json
       "audit":              { ... }    # 原 audit_report.json 内容
     }
```

`.gitignore` 追加 `.autocode/`。遵循"非题目的东西都是副产物"原则：题目本体（题面/解法/校验器/生成器/检查器/交互器/测试/`autocode.json`/`problem.xml`）留在根，其余进 `.autocode/`。

**接口边界（MCP 侧 vs hook 侧）明确**：
- MCP 侧 `src/autocode_mcp/tools/{test_verify,stress_test,audit,problem}.py`：改读 `.autocode/runtime.json` 的对应键。
- hook 侧 `scripts/hook_state.py`：`save_state` / `load_state` 读写 `.autocode/runtime.json` 的 `workflow` 键。

为减少散落改动，抽一个共享的 `runtime_store` 读写 helper（路径常量集中），供两侧调用。注意三个副产物当前的文件名常量分散在 `problem.py`（`_TEST_MANIFEST_FILENAME`/`_GENERATE_STATE_FILENAME`）、`test_verify.py`（`_TEST_MANIFEST_FILENAME`）与 `audit.py`（`_WORKFLOW_STATE`/`_TEST_MANIFEST`）三处重复定义，收口时须全部改为引用 `runtime_store` 单一常量。

### D5: openspec 自洽（F1 已校正）
经核实，`openspec/config.yaml` 的 `rules:` 段仅含 proposal/design/tasks/specs 四类格式规则，**不存在**"22 工具不可改""openspec 必须 git-ignore"两条规则——二者实为 `context:` 指引（L23-26）且准确，且本变更的 Non-goals 正依赖"22 工具签名不可改"，故 **config.yaml 保持原样，不改动**（原「删两条 stale 规则」任务已撤销）。仍 MODIFY `knowledge-source-single-truth` spec（删 prompts 一致性要求，扩展单一真源原则到"仓库即 CC 插件唯一真源"）。

## Risks / Trade-offs

- **[Risk]** 非 CC 客户端（Cursor/OpenCode）若已接入会失效 → **Mitigation**：scope 明确 CC-only，README 注明不再支持裸 MCP 客户端。
- **[Risk]** `uv run autocode-mcp` 要求仓库本地 checkout；仅装插件资产而不 checkout 源码的环境拉不起 server → **Mitigation**：README 明确"插件依赖仓库 checkout，server 从本地 `uv run` 启动"；本次决定全砍 PyPI，故要求本地仓库。
- **[Risk]** 副产物路径重构触碰 5 个文件，回归面大 → **Mitigation**：集中 `runtime_store` helper，路径常量唯一；保留全部 e2e 测试。
- **[Risk]** 移除 prompts/resources 后相关测试失效 → **Mitigation**：删除 `tests/test_prompts*.py` 及 resources 测试；其余测试保留。

## Migration Plan

1. 改 `.mcp.json` 与 README（砍 PyPI / 非 CC 文档）。
2. 删 `prompts/` 与 `resources/`，`server.py` 去 4 个 handler，删相关测试。
3. 版本统一：`pyproject.toml` dynamic + `plugin.json` 同步。
4. 引入 `runtime_store` helper，迁移 5 处读写点；`.gitignore` 加 `.autocode/`。
5. `openspec/config.yaml` 保持原样（F1 校正：无此 stale 规则）+ MODIFY `knowledge-source-single-truth` spec。
6. 验证：`openspec validate` + 目标测试（`compiler`/`problem`/`process`/`audit` + `tests/scripts`）+ gstack `autoplan` CEO 裁决。

**回滚**：每步独立可 `git revert`，无数据迁移（副产物可重生）。

## Open Questions

- `autocode-audit --report <path>` CLI：建议默认写入 `.autocode/runtime.json` 的 `audit` 键；若仍传 `--report` 则写到指定路径（兼容）。待实现时确认。
- 是否下架 PyPI 包属发布运维，不在代码范围。
