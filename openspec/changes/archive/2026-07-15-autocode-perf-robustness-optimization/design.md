## Context

AutoCode 是一个 Python MCP server，出题流水线的重负载环节（`problem_generate_tests`、`problem_verify_tests`、`stress_test_run`）通过 `asyncio.create_subprocess_exec` 启动 g++/validator/sol/brute 子进程，但调用方是串行 `await`，完全没有利用异步并发。同时门禁（QualityGate / AuditGate）判定在 `problem_pack_polygon` 与 `problem_audit` 中各实现一遍，且 `autocode.json` 读取失败时静默回退为宽松默认，可能绕过质量门禁。进程回收（`problem_cleanup_processes`）默认不清理 PID、不校验存活、POSIX 下无法整树杀子进程。另一方面，`scripts/workflow_guard.py`（Claude Code plugin 分步门禁 hook，约 1000 行）混入了 payload 解析、状态读写、门禁判定、历史记录，并与 `autocode_mcp/workflow/manifest.py` 的 `manifest_uses_testlib_checker` 存在字典版双份真值；`prompts/__init__.py` 硬编码模板与 `skills/*.md` 重叠；工具 `input_schema` 手写 JSON Schema 与 Pydantic 模型重复。

## Goals / Non-Goals

**Goals:**
- 把三个子进程密集型环节改为有上限并发批处理，提速且保持单测试正确性/超时/失败语义不变。
- 抽出 `workflow/guard.py` 作为门禁判定唯一入口，两处复用。
- 加固进程生命周期：默认回收、存活校验、整树杀、取消即终止。
- 顺带收敛编译缓存短路、manifest 单次加载、XML 生成、类型收敛等中低优先级问题。
- 拆分 `scripts/workflow_guard.py` 为 `hook_payload` / `hook_state` / `hook_gates` 纯函数模块，hook 门禁判定复用 `manifest_uses_testlib_checker` 单一真值。
- 知识源去重：`prompts` 委托 `skills/*.md`，工具 `input_schema` 由 Pydantic 推导。

**Non-Goals:**
- 不改变 22 个 MCP 工具的对外签名与入参语义（含 `problem_build_all`）。
- 不改变 `autocode.json` schema 与既有测试数据结果。
- 不新增面向用户的新功能；不做算法层面的题目质量改进。
- 不删除 `.codebuddy` 文件夹，不改变现有分层形态与语言。

## Decisions

1. **并发模型**：用 `asyncio.gather` + `asyncio.Semaphore(limit)` 批处理，limit 默认 4，可配置。理由：改动最小、保留 asyncio 结构；轮次/候选内部顺序（gen→val→sol）不变。备选（多线程池 / 进程池）被否，因为已是 async 环境，引入线程收益低且增加复杂度。
2. **门禁收敛**：新增 `src/autocode_mcp/workflow/guard.py`，导出 `check_gates(manifest, workflow_state, verify_signals)`，返回结构化 `{decision, blocking_issues, signals}`；`problem_pack_polygon` 与 `problem_audit` 均调用它，删除各自的重复实现。
3. **manifest 失败即失败**：读取 `autocode.json` 失败时直接返回阻断错误，不再回退为 `require_*=True` 的宽松默认。
4. **进程回收**：用 `psutil` 校验 PID 存活后再 kill；POSIX 下启动子进程时置于新进程组（`start_new_session=True`），清理用 `os.killpg` 整树杀；Windows 保留 Job Object。取消路径主动 `terminate` 残留进程，`resume` 用 psutil 过滤已退出 PID。
5. **编译缓存短路**：`CompileCache._get_key` 增加源文件 mtime + 路径分桶；源未变直接复用已存在 binary；将现有零引用的 `compile_all` 接入 validator+generator+sol+brute 并发编译。
6. **hook 脚本拆分与命名边界**：`scripts/workflow_guard.py` 拆为 `hook_payload.py`（解析）/ `hook_state.py`（状态）/ `hook_gates.py`（门禁），原文件保留 `main` / `pre_tool` / `post_tool` / `session_start` 薄入口；MCP 侧 `workflow/guard.py` 与 hook 侧名称易混，hook 侧模块一律 `hook_` 前缀以明确边界。
7. **门禁单一真值**：hook 内 `_manifest_dict_uses_testlib_checker` 与 `manifest.py` 的 `manifest_uses_testlib_checker` 合并；hook 顶部 `sys.path.insert` 指向仓库 `src` 后 `from autocode_mcp.workflow.manifest import manifest_uses_testlib_checker`，删除字典版重复。
8. **知识源单一来源**：`prompts/__init__.py` 由硬编码模板改为委托 `skills/*.md`（SKILL.md）为权威；加一致性校验测试锁定两者一致。
9. **input_schema 推导**：新增 `tools/schemas.py` 与各工具输入 Pydantic 模型，`base.py` 加 `input_schema_from_model()` 用 `model_json_schema()` 推导，分批迁移工具，避免与 Pydantic 模型重复维护。

## Risks / Trade-offs

- [并发抬高内存] g++ 并行编译可能 OOM → 限制并发上限并文档化，必要时按可用内存自适应下调。
- [门禁行为漂移] 收敛可能无意改变判定 → 保持字段映射与阈值完全一致，针对 `problem_pack_polygon` 既有用例补测试。
- [进程组改造] 需要子进程以新会话启动 → 调整 `run_binary`/编译子进程创建方式，注意 Windows 兼容性。
- [stdout 流式化] 大输出改临时文件会改变部分调用约定 → 仅在输出超过阈值时启用。
- [hook 契约脆弱] 原 `recover_hook_payload` 正则恢复在改用健壮解析后，须覆盖所有真实契约用例并被单测锁定后再删除兜底。
- [sys.path 注入] hook 经 `sys.path.insert` 导入 `autocode_mcp`，需验证 plugin 运行时 `CLAUDE_PLUGIN_ROOT` 与 `src` 的相对关系。
- [schema 推导漂移] 迁移期间手写与推导并存，分批迁移并保证字段名与类型一致。

## Migration Plan

- 各工具独立改动，复用现有测试套件；回滚即还原对应模块。
- 不修改 `autocode.json` schema，无数据迁移。
- 建议在 `examples/*` 样例上做端到端回归，对比串行与并发产物一致性。
- hook 脚本拆分保持 `hooks.json` 命令、stdin 契约与退出码语义不变；先落地 `hook_*` 模块并单测，再切换 `workflow_guard.py` 为薄入口。

## Open Questions

- 默认并发上限取多少合适（建议 4，可经基准测试微调）？
- 是否把并发上限作为工具可选项暴露给调用方？
- hook 经 `sys.path` 注入导入 `autocode_mcp` 时，plugin 安装路径下 `src` 的相对位置是否稳定（需运行时验证）？
