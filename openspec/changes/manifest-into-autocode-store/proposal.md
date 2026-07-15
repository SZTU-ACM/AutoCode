## Why

The problem manifest (`autocode.json`) is currently written to the problem root **and committed**. It holds the *recipe* for generating the problem (case-plan seeds, solution paths, special-judge / stress-comparison switches, quality / audit gate config, difficulty) — i.e. AutoCode pipeline metadata, not problem content. A complete problem is `statements/` + `tests/` + `solutions/` + `files/` (checker/generator/interactor/testlib). Cloning a problem without `autocode.json` still yields a valid problem; only the *deterministic regeneration / re-audit recipe* is lost.

Meanwhile the runtime traces already live in a single git-ignored `.autocode/runtime.json`. So today the AutoCode state is split into two places with **different lifecycle and ignore semantics**:
- `autocode.json` — authored/committed (high churn on every regenerate, pollutes `git diff`).
- `.autocode/runtime.json` — tool-generated/ignored.

This change unifies the two under one hidden, self-ignored `.autocode/` directory and stops polluting the problem repo root with either an `autocode.json` file or an AutoCode-generated `.gitignore`.

User decision (2026-07-16): adopt direction (b) — move the manifest into `.autocode/manifest.json`, keep it git-ignored, and stop writing a root `.gitignore`; instead emit a self-ignore rule inside `.autocode/`.

## What Changes

- **Manifest relocation**: `workflow/manifest.py` `MANIFEST_NAME` becomes `manifest.json` and `manifest_path()` returns `<problem_dir>/.autocode/manifest.json` (reusing `runtime_store.RUNTIME_DIR_NAME`). `save_manifest` already `mkdir`s the parent, so `.autocode/` is created on first write.
- **No root `.gitignore`**: `problem_create` SHALL NOT write a `.gitignore` at the problem root.
- **Self-ignore inside `.autocode/`**: `problem_create` SHALL write `<problem_dir>/.autocode/.gitignore` containing exactly `*` so **every** file under `.autocode/` (both `manifest.json` and `runtime.json`) is ignored. Because `*` also matches the `.gitignore` file itself, `.autocode/` produces **zero** untracked entries in `git status` — no root `.gitignore` required.
- **Error-message cleanup**: all user-facing "autocode.json" strings (in `cli/verify.py`, `tools/validation.py`, `tools/test_verify.py`, `tools/stress_test.py`, `tools/audit.py`, `tools/problem.py`, and `workflow/manifest.py`) become "manifest.json" for consistency.
- **Template rename**: `src/autocode_mcp/templates/autocode.json` → `src/autocode_mcp/templates/manifest.json` (the template is not actually consumed by `problem_create`, which builds the manifest via `default_manifest`; the file exists only as a documented sample and is asserted by `test_all_template_files_exist`).
- **Tests + examples**: every test that writes/reads `autocode.json` at the problem root is updated to `.autocode/manifest.json`. The three `examples/*` sample problems are (re)created with `.autocode/manifest.json` (the root `autocode.json` they previously referenced is absent in the current tree, so `test_e2e_examples` is already broken — this change repairs it by moving the sample recipe to the new path).

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `runtime-byproduct-consolidation`: the manifest recipe is now a git-ignored byproduct under `.autocode/manifest.json` (previously a committed root `autocode.json`); the single AutoCode state directory `.autocode/` is self-ignoring via an internal `.gitignore`, and `problem_create` no longer writes a root `.gitignore`.

## Impact

- Files: `src/autocode_mcp/workflow/manifest.py`, `src/autocode_mcp/tools/problem.py`, `src/autocode_mcp/cli/verify.py`, `src/autocode_mcp/tools/{validation,test_verify,stress_test,audit}.py`, `src/autocode_mcp/templates/autocode.json` → `manifest.json`, tests (`test_packaging`, `test_manifest_load`, `test_workflow_guard`, `test_validation`, `test_e2e_examples`, `test_tools/test_stress_test`, `test_tools/test_problem`, `scripts/test_hook_state`), `examples/{checker,exact,interactive}-sample/.autocode/manifest.json` (new).
- Behavior: `problem_create` output no longer contains a root `autocode.json` or a root `.gitignore`; `.autocode/` is the sole AutoCode footprint and is fully git-ignored via its own `.gitignore`.
- Risk: any external tool/scripts that read `<problem_dir>/autocode.json` directly will break (they must read `.autocode/manifest.json`). The 22 MCP tool *signatures* are unchanged — `manifest_path` is internal.
- Non-goals: does not merge `manifest.json` into `runtime.json` (recipe vs trace have different lifecycles; keeping them separate means clearing runtime never wipes the recipe). Does not change ignore semantics of anything outside `.autocode/`. Does not alter the 22 tool signatures or the 12-step workflow.

## Pre-existing finding

`examples/checker-sample`, `examples/exact-sample`, `examples/interactive-sample` currently contain **only** `statements/` — there is no root `autocode.json` on disk. `tests/test_e2e_examples.py` reads `examples/<name>/autocode.json`, so that test is already failing (or skipped) today. This change moves the sample recipe to `examples/<name>/.autocode/manifest.json` and updates the test's copy helper, repairing the break.
