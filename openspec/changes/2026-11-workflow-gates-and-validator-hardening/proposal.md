## Why

Two structural gate and contract defects in the AutoCode workflow allow broken or under-verified problem packages through while sabotaging dynamic verification on the main authoring path:

1. **Pre-/post-generator gate contradiction (Issue #14)**: `workflow/gates.py` forces `solution_analyze -> solution_audit_std -> solution_audit_brute -> validator_build -> generator_build`, yet both `run_empirical_verification` (`tools/complexity.py`) and `_probe_brute_small_scale` (`tools/solution_audit.py`) require the compiled `files/gen` binary (returning `pending_generator` / `pending_binaries` before `generator_build`). Worse, `solution_analyze`, `solution_audit_std`, and `solution_audit_brute` are in `_DOWNSTREAM_INVALIDATING_TOOLS`, and `enforcement.py` unconditionally clears `std_audited` when `solution_analyze` runs and calls `_invalidate_downstream`. Re-running them after `generator_build` destroys already-passed `std_audited`, `stress_passed`, and downstream gates—so dynamic empirical fitting and brute small-scale probing are effectively skipped on the main path.
2. **Vacuous validator, communication packing gap, and overly permissive defaults (Issue #20)**:
   - `ValidatorBuildTool` (`tools/validator.py`) computes `accuracy` without checking whether `test_cases` contains both positive (`expected_valid=True`) and negative (`expected_valid=False`) inputs, allowing a vacuous `int main() { return 0; }` validator to get `accuracy=1.0` and set `validator_ready=True` (`enforcement.py`).
   - `ProblemPackPolygonTool` (`tools/problem_pack.py:416`) exempts only `manifest_is_interactive` from requiring `.ans` files, forgetting `manifest_is_communication` (unlike `test_verify.py:213`), and `exporters/{polygon,hydro,qoj}.py` similarly restrict `allow_missing_answer` to `is_interactive`.
   - `ProblemPackPolygonTool` (`tools/problem_pack.py:738-744`) only warns on undeclared `solutions/wrong_*.cpp`, missing `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, and `bf.cpp`.
   - `QualityGates.require_wrong_solution_kill` defaults to `False`, so manifests that declare `role == "wrong"` solutions without explicitly setting `require_wrong_solution_kill` still skip enforcing `wrong_solution_kill` at audit and pack gates.

## What Changes

- **Non-destructive post-generator dynamic verification (`gates.py`, `enforcement.py`, `reconcile.py`, `solution_audit.py`)**:
  - Preserve the pre-generator static progression `solution_analyze -> solution_audit_std -> solution_audit_brute -> validator_build -> generator_build`.
  - Track `std_empirical_verified`, `std_empirical_hash`, `brute_probed`, and `brute_probed_hash` in workflow state (and clear them cleanly on source/manifest/generator drift or failure).
  - When `solution_analyze`, `solution_audit_std`, or `solution_audit_brute` runs after `generator_built == True` and canonical `sol.cpp` / `brute.cpp` SHA-256 is unchanged, execute dynamic empirical / brute probe verification without resetting `std_audited`, `brute_audited`, `validator_ready`, `generator_built`, `checker_ready`, `interactor_ready`, or `stress_passed`—while failing closed if a dynamic probe fails.
  - Expose explicit `dynamic_probe` status (`ran` vs `pending_binaries` / `skipped` / error) in `solution_audit.py` and `dynamic_probes` inspection/reconciliation in `reconcile.py`, setting `brute_probed=True` when post-generator `_probe_brute_small_scale` completes with `status in ("ok", "timeout_circuit_breaker")`.
- **Anti-vacuous validator guard (`validator.py`, `enforcement.py`, `gates.py`)**:
  - Track `positive_count` (`expected_valid is True`), `negative_count` (`expected_valid is False`), `has_positive_cases`, `has_negative_cases`, and actionable `warnings` / `message` in `ValidatorBuildTool`.
  - Require `parsed is not None and parsed >= 0.9 and bool(data.get("has_positive_cases")) and bool(data.get("has_negative_cases"))` in `enforcement.py` before setting `validator_ready=True`.
- **Communication `.ans` pack exemption & broadened undeclared wrong scan (`problem_pack.py`, `exporters/{polygon,hydro,qoj,verify}.py`)**:
  - Exempt `manifest_is_communication(manifest_model)` alongside `manifest_is_interactive(manifest_model)` in `problem_pack.py:416` and handle optional/missing `.ans` cleanly across `polygon`, `hydro`, `qoj`, and `verify` exporters.
  - Broaden `problem_pack.py` undeclared wrong-solution detection to include `wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, and `bf.cpp`.
- **Smart default wrong-kill gate (`guard.py`, `gates.py`, `reconcile.py`, `skills/`, `agents/`)**:
  - In `guard.py:check_gates` (with `gates.py` preflight and `reconcile.py` parity), treat `require_wrong_solution_kill` as effectively enabled when `manifest.solutions` contains any `role == "wrong"` entry and `"require_wrong_solution_kill" not in manifest.quality_gates.model_fields_set`, while preserving explicit `False` opt-outs and keeping `workflow/models.py` untouched.
  - Synchronize `skills/autocode-workflow/SKILL.md` and `agents/autocode-workflow.md` with the post-generator verification step, positive+negative validator requirement, and smart `require_wrong_solution_kill` default.
- **Regression tests**: Add `tests/test_gates_t1a_hardening.py` covering all four requirements end-to-end.

## Capabilities

### New Capabilities

- `workflow-gates-and-validator-hardening`: Post-generator non-destructive dynamic verification tracking (`std_empirical_verified`, `brute_probed`), anti-vacuous positive+negative validator readiness enforcement, communication-safe packing with broadened wrong-solution scanning, and context-aware `require_wrong_solution_kill` enforcement without breaking existing manifest schemas.

### Modified Capabilities

- `unified-gate-validation`: `check_gates` (`workflow/guard.py`) and `PRE_GATES` (`workflow/gates.py`) remain the single source of truth and gain smart `require_wrong_solution_kill` defaults when `role == "wrong"` entries exist without explicit opt-out.

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves the 12-stage `CLAUDE.md` pipeline, SHA-256 invalidation semantics, `.autocode/runtime.json` relative path hygiene, and all 22 MCP tool input schemas (`server.py`, `tools/schemas.py`).
- **Affected Files**:
  - `src/autocode_mcp/workflow/{gates.py,enforcement.py,reconcile.py,guard.py}`
  - `src/autocode_mcp/tools/{validator.py,solution_audit.py,problem_pack.py}`
  - `src/autocode_mcp/utils/exporters/{polygon.py,hydro.py,qoj.py,verify.py}`
  - `skills/autocode-workflow/SKILL.md`, `agents/autocode-workflow.md`
  - `tests/test_gates_t1a_hardening.py` (plus directly impacted legacy validator fixture if needed)
- **Risks**:
  - *Existing synthetic tests*: Tests calling `apply_result(..., "validator_build", ..., {"accuracy": 1.0})` without positive/negative flags will now fail to set `validator_ready=True`; impacted fixtures must pass realistic positive+negative metadata.
  - *Fail-closed post-generator probes*: If post-generator `solution_analyze` / `solution_audit_std` / `solution_audit_brute` discovers a genuine complexity mismatch or brute crash, `std_audited`/`brute_audited` and downstream state must fail closed rather than silently retaining stale passes.

## Non-goals

- Do NOT modify `src/autocode_mcp/workflow/models.py`, `tools/complexity.py`, `tools/audit*.py`, `tools/verify_*.py`, `utils/mutation.py`, `cli/*`, `pyproject.toml`, or `server.py`.
- Do NOT alter any of the 22 MCP tool input schemas or break `tests/test_docs_sync.py`.
- Do NOT implement Wave 2 Python solution execution (`T2E`) or sandbox execution backends (`T1B`).
