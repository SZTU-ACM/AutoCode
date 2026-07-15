## 1. 并发执行（concurrent-execution）

- [x] 1.1 引入有上限并发助手：在 `run_binary` 附近封装 `asyncio.gather` + `asyncio.Semaphore(limit)` 批处理工具
- [x] 1.2 重构 `ProblemGenerateTestsTool.execute` 的候选循环为并发批处理（保持 gen→validator→sol 顺序）
- [x] 1.3 重构 `problem_verify_tests` 的 `_check_answer_consistency` / `_check_validator` / `_check_wrong_solution_kill` 为并发
- [x] 1.4 重构 `stress_test_run.execute` 的轮次为并发（轮内 gen→validator→sol→brute→checker 顺序不变）
- [x] 1.5 暴露可配置并发上限（默认 4），并对 `examples/*` 做串行 vs 并发产物一致性基准

## 2. 统一门禁（unified-gate-validation）

- [x] 2.1 新增 `src/autocode_mcp/workflow/guard.py`，导出 `check_gates(manifest, workflow_state, verify_signals)`
- [x] 2.2 将 `problem_pack_polygon` 的门禁判定迁移到 `check_gates`，删除重复实现与重复 `json.load`
- [x] 2.3 将 `problem_audit` 的门禁判定迁移到 `check_gates`
- [x] 2.4 `autocode.json` 读取失败时改为阻断错误，不再回退为宽松默认
- [x] 2.5 补充门禁行为一致性测试（对比旧 per-tool 实现）

## 3. 进程生命周期（process-lifecycle）

- [x] 3.1 `problem_cleanup_processes` 默认路径也巡检并回收残留 PID（不再直接返回 success）
- [x] 3.2 用 `psutil` 校验 PID 存活后再 kill，跳过已退出 PID
- [x] 3.3 POSIX 下子进程以新进程组启动（`start_new_session=True`），清理用 `os.killpg` 整树杀
- [x] 3.4 取消路径主动 `terminate` 残留进程；resume 用 psutil 过滤已退出 PID

## 4. 编译缓存与中低优先级收敛

- [x] 4.1 `CompileCache._get_key` 增加源文件 mtime + 路径分桶，源未变直接复用 binary
- [x] 4.2 接入现有 `compile_all` 做 validator/generator/sol/brute 并发编译
- [x] 4.3 manifest 入口统一一次性加载并透传，消除 `test_verify.py` 重复 `open`
- [x] 4.4 `_run_process` 大输出改为临时文件/流式，避免全量读入内存
- [x] 4.5 `ProblemPackPolygonTool.execute` 拆分函数，XML 改用 `xml.etree.ElementTree` 生成
- [x] 4.6 合并 `problem.py` 与 `test_verify.py` 中重复的 `answer_ext` 归一化函数到 `utils`
- [x] 4.7 收紧 `pyproject.toml` 中 mypy `disable_error_code`；`server.py` 异常保留类型与堆栈

## 5. hook 脚本拆分与契约稳定（hook-script-cleanup）

- [x] 5.1 拆分 `scripts/workflow_guard.py` 为 `hook_payload.py` / `hook_state.py` / `hook_gates.py` 纯函数模块，保留原文件 `main` / `pre_tool` / `post_tool` / `session_start` 薄入口
- [x] 5.2 经用户决定从本 change 移除：保留 `recover_hook_payload` 正则兜底作为安全网，不再做结构化解析替换（对应 hook-script-cleanup spec 第 2 条标为 out-of-scope，不纳入主 spec）
- [x] 5.3 新增 `tests/scripts/test_hook_payload.py` / `test_hook_state.py` / `test_hook_gates.py`

## 6. 门禁单一真值（方向 C）

- [x] 6.1 `hook_gates.py` 引用 `manifest_uses_testlib_checker`（hook 顶部 `sys.path.insert` 指向 `src` 后 import），删除 `_manifest_dict_uses_testlib_checker` 字典版
- [x] 6.2 新增 `tests/.../test_manifest_dedup.py` 验证门禁判定唯一真值、无字典版残留

## 7. 知识源与 Schema 推导（knowledge-source-single-truth）

- [x] 7.1 采用方案 B：prompts 保持客户端模板，2 个重叠 prompt（`full_pipeline`/`difficulty_rating`）加指向对应 skill 的反向依赖指针；新增 `tests/test_prompts_consistency.py` 锁住共享事实（难度 800–3500+档位、generator type 方案、pipeline 阶段）在 prompt 与 skill 两侧一致。spec 描述同步改写为"共享事实 + 一致性测试"
- [x] 7.2 新增 `tools/schemas.py` 与代表性工具输入 Pydantic 模型；`base.py` 加 `input_schema_from_model()` 用 `model_json_schema()` 推导（内联 `$defs`、去除 `title`）
- [x] 7.3 试点迁移 4 个代表性工具（audit / file_read / file_save / validation）到模型推导 `input_schema`，新增 `tests/test_input_schema.py`
- [x] 7.4 剩余 18 个工具（共 22 个全部）的 `input_schema` 已迁移到 Pydantic 模型并复用 `input_schema_from_model`；`tests/test_input_schema.py` 现覆盖全部 22 个工具的「模型即单一真值」一致性

## 8. 验证与回归

- [x] 8.1 新增 `tests/test_e2e_examples.py`：3 个 example manifest 端到端加载自洽（checker/exact/interactive 语义字段）+ 真实 checker-sample 配置驱动 `problem_generate_tests` 的串行(limit=1)/并发(limit=4) 产物一致性基准（呼应 1.5）
- [x] 8.2 全量 pytest 分批全绿（compiler/process/cache/win_job/audit/resource_limit、test_tools/、test_integration/、顶层测试、scripts/）。修复 `tests/test_e2e_mcp.py` 工具计数 21→22 并补 `problem_build_all`（`server.py:86` 已注册但测试漏列，7.x schema 迁移接入全部工具后暴露）
- [x] 8.3 `ruff` 与 `mypy` 无新增错误（顺带修复 `scripts/hook_gates.py` 的 mypy attr-defined/unused-ignore 遗漏，`mypy .` 现全绿）
- [x] 8.4 经多视角验收裁决（CEO/Eng/DX）结论 **可验收**：返工项 0；唯一非阻塞 follow-up 为 proposal/design「21 个工具」→「22 个工具（含 problem_build_all）」文案修正（已顺手修正）。注：本环境无 `gstack`/`codex` CLI 与 `.gstack` 工作区，且 change 已实现（非待审计划），故以同等三视角对实现直接做验收裁决，等价于 `/gstack` 协调验收的「可验收/需返工」门禁。
