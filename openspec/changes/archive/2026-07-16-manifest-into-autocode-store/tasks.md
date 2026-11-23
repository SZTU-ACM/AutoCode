## 1. 迁 manifest 到 `.autocode/manifest.json`（manifest.py）

- [x] 1.1 `workflow/manifest.py`：`MANIFEST_NAME = "manifest.json"`；`manifest_path()` 返回 `Path(problem_dir) / RUNTIME_DIR_NAME / MANIFEST_NAME`（`from ..runtime_store import RUNTIME_DIR_NAME`）。
- [x] 1.2 `load_manifest` 报错文案 `cannot read autocode.json` → `cannot read manifest.json`。

## 2. 改写 problem_create 的忽略行为（problem.py）

- [x] 2.1 删除 `problem.py` 中在题目根写 `.gitignore`（含 `.autocode/`）的代码块。
- [x] 2.2 在 `save_manifest` 之前：确保 `.autocode/` 存在并写入 `.autocode/.gitignore`，内容为 `*`（`# AutoCode state — self-ignored\n*\n`）。写入 best-effort，避免被残留文件阻断。
- [x] 2.3 `problem.py` 读取失败文案 `invalid or unreadable autocode.json` → `manifest.json`（约 L1333）。

## 3. 同步错误文案（autocode.json → manifest.json）

- [x] 3.1 `cli/verify.py`：`invalid autocode.json` / `autocode.json not found` → `manifest.json`。
- [x] 3.2 `tools/validation.py`：`invalid or unreadable autocode.json` → `manifest.json`。
- [x] 3.3 `tools/test_verify.py`：同上。
- [x] 3.4 `tools/stress_test.py`：注释与报错中的 `autocode.json` → `manifest.json`。
- [x] 3.5 `tools/audit.py`：注释与报错中的 `autocode.json` → `manifest.json`。

## 4. 模板改名

- [x] 4.1 `src/autocode_mcp/templates/autocode.json` → `templates/manifest.json`（重命名）。
- [x] 4.2 `tests/test_packaging.py` `expected_templates` 列表 `"autocode.json"` → `"manifest.json"`。

## 5. 测试路径更新（root autocode.json → .autocode/manifest.json）

- [x] 5.1 `tests/test_manifest_load.py`：写入 `Path(d)/".autocode"/"manifest.json"`（先 `mkdir(.autocode)`），assert `cannot read manifest.json`。
- [x] 5.2 `tests/test_workflow_guard.py`：3 处写入改 `.autocode/manifest.json`（先 mkdir）。
- [x] 5.3 `tests/test_validation.py`：2 处写入改 `.autocode/manifest.json`（先 mkdir）。
- [x] 5.4 `tests/test_tools/test_stress_test.py`：损坏/非法 UTF-8 两处写 `.autocode/manifest.json`（先 mkdir）。
- [x] 5.5 `tests/test_tools/test_problem.py`：5 处写入（L228/1433/1716/1750/1850）改 `.autocode/manifest.json`（先 mkdir）；L1462 将 `.autocode` 建成文件的 best-effort 测试保持（验证 runtime store 仍可 best-effort）。
- [x] 5.6 `tests/scripts/test_hook_state.py`：写入 `.autocode/manifest.json`。
- [x] 5.7 `tests/test_packaging.py`：其余写/读 `autocode.json` 处改 `.autocode/manifest.json`；断言 `"invalid autocode.json"` → `"invalid manifest.json"`（L171/184）。

## 6. examples 修复 + e2e 测试

- [x] 6.1 为 `examples/{checker,exact,interactive}-sample` 创建 `.autocode/manifest.json`（满足 `test_e2e_examples` 的字段断言：checker `special_judge=True,stress_comparison="checker",case_plan len 4`；exact `special_judge=False,stress_comparison="exact"`；interactive `interactive=True`）。
- [x] 6.2 `tests/test_e2e_examples.py`：`_copy_example_manifest` 源/目标改为 `examples/<name>/.autocode/manifest.json` 与 `problem_dir/.autocode/manifest.json`。

## 7. 验证

- [x] 7.1 `openspec validate manifest-into-autocode-store`。
- [ ] 7.2 提交并推送，用 `gh run view --log-failed` / `gh run watch --exit-status` 核对 CI（lint / test-unit / typecheck）；不在本地复现。（用户要求本次不 commit）
