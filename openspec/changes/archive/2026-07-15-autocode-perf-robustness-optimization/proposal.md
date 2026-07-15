## Why

AutoCode 的多个重负载环节目前是串行执行子进程，且关键逻辑（门禁判定、进程回收）存在重复实现与健壮性缺口，导致出题流水线在大体量题目上明显偏慢，并可能在取消/异常路径下泄漏编译器与生成器进程。此外，`scripts/workflow_guard.py`（分步门禁 hook 脚本，约 1000 行）同时承担 payload 解析、状态读写、门禁判定、历史记录多种职责，且与 `src/autocode_mcp/workflow/manifest.py` 存在门禁判定双份真值；`prompts/__init__.py` 硬编码模板与 `skills/*.md` 知识源重叠；工具 `input_schema` 手写 JSON Schema 与 Pydantic 模型重复。本次变更统一做性能并发化、门禁逻辑收敛、进程生命周期加固与工程整洁度收敛，属于内部质量优化，不改变对外 MCP 工具契约。

## What Changes

- 将 `problem_generate_tests`、`problem_verify_tests`、`stress_test_run` 中串行 `await` 的子进程调用，改为基于 `asyncio.gather` + `asyncio.Semaphore` 的有上限并发批处理（生成、校验、对拍不再逐一轮次阻塞）。
- 抽出统一的 `workflow/guard.py`，集中实现 QualityGate / AuditGate 判定，供 `problem_pack_polygon` 与 `problem_audit` 复用，消除两套重复实现；并在 `autocode.json` 读取失败时**失败而非静默回退为宽松默认**，避免绕过质量门禁。
- 加固 `problem_cleanup_processes`：默认路径也巡检并回收残留 PID；清理前用 `psutil` 校验进程存活；Linux/macOS 用进程组（`os.killpg`）整树回收子进程；取消路径主动 terminate 残留进程，resume 时过滤已退出 PID。
- 编译缓存增加源文件 mtime/路径分桶短路，源未变时直接复用已存在二进制；接入 `compile_all` 做多文件并发编译。
- 收敛若干中低优先级问题：manifest 统一一次性加载透传、stdout 大输出走临时文件/流式、`pack_polygon` 拆函数并改用 `xml.etree` 生成、合并重复的 `answer_ext` 归一化函数、收紧 mypy 配置。
- 拆分 `scripts/workflow_guard.py`（分步门禁 hook 脚本）为 `hook_payload.py` / `hook_state.py` / `hook_gates.py` 纯函数模块，原文件仅作薄编排入口；hook 门禁判定统一 `import manifest_uses_testlib_checker`，删除字典版重复；hook 契约改用健壮结构化解析替换脆弱正则恢复。
- 知识源去重：`prompts/__init__.py` 硬编码模板改为委托 `skills/*.md` 为单一来源；工具 `input_schema` 由 Pydantic `model_json_schema()` 推导，消除手写 Schema 漂移。

## Capabilities

### New Capabilities
- `concurrent-execution`: 重负载子进程（测试生成、测试校验、对拍）以有上限并发批处理运行，保持单测试正确性、超时与失败语义不变。
- `unified-gate-validation`: 门禁（QualityGate / AuditGate）判定集中到 `workflow/guard.py`，`problem_pack_polygon` 与 `problem_audit` 共用同一判定入口；manifest 损坏时判定失败而非放行；hook 侧门禁判定亦统一引用 `manifest_uses_testlib_checker` 单一真值。
- `process-lifecycle`: 定义进程/子进程树的创建、取消与回收保证，确保正常与异常路径下均无编译器/生成器残留进程。
- `hook-script-cleanup`: 拆分分步门禁 hook 脚本（payload / state / gates 纯函数化），与 MCP 侧 `workflow/guard.py` 命名边界清晰分离（hook 侧统一 `hook_*` 前缀）。
- `knowledge-source-single-truth`: MCP `prompts` 委托 `skills/*.md` 为单一知识来源；工具入参 Schema 由 Pydantic 推导，消除双份维护。

### Modified Capabilities

（无既有 spec，全部为新能力。）

## Impact

- 受影响代码（性能/健壮性）：`tools/problem.py`、`tools/test_verify.py`、`tools/stress_test.py`、`tools/audit.py`、`utils/compiler.py`、`utils/cache.py`、`utils/win_job.py`、`utils/resource_limit.py`、`file_ops.py`、`server.py`，新增 `workflow/guard.py`。
- 受影响代码（整洁度）：`scripts/workflow_guard.py`（改为薄入口）、新增 `scripts/hook_payload.py` / `hook_state.py` / `hook_gates.py`、`src/autocode_mcp/prompts/__init__.py`、`src/autocode_mcp/tools/base.py`、新增 `src/autocode_mcp/tools/schemas.py`；门禁判定统一引用 `src/autocode_mcp/workflow/manifest.py`。
- 依赖：确认 `psutil` 已被依赖（`pyproject.toml` 已含）；不新增第三方依赖；Pydantic v2 已用于 Schema 推导。
- 风险：并发化需控制并发上限避免 OOM 与 g++ 内存争用；门禁收敛需保证与现有 `autocode.json` 门禁字段行为一致；不改变 22 个 MCP 工具对外签名（含 `problem_build_all`）。
- 命名边界：`src/autocode_mcp/workflow/guard.py`（MCP 工具侧门禁，方向 A）与 `scripts/workflow_guard.py`（Claude Code plugin 分步门禁 hook，方向 B）功能不同、名称相近；hook 侧拆出的模块一律以 `hook_` 前缀命名，避免混淆。
