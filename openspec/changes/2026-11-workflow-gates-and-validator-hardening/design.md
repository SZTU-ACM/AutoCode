## Context

AutoCode enforces a 12-stage problem-authoring workflow on the MCP server (`src/autocode_mcp/workflow/`), while client-side hooks (`scripts/workflow_guard.py`, `scripts/hook_gates.py`, `scripts/hook_state.py`) remain thin compatibility adapters delegating directly to the MCP workflow engine. Track `workflow-gates-and-validator-hardening` closes Issues #14 and #20 inside the workflow gating, solution-audit, validator-build, and problem-pack boundaries without altering `workflow/models.py` or any of the 22 MCP tool input schemas.

Naming boundaries remain strictly separated:
- Server-side workflow gates, state transitions, and tool implementations live under `src/autocode_mcp/workflow/...`, `src/autocode_mcp/tools/...`, and `src/autocode_mcp/utils/exporters/...`.
- Client-side hook helpers live under `scripts/...` and continue importing `PRE_GATES`, `preflight`, and `apply_result` from `autocode_mcp.workflow.enforcement` / `gates` without duplication.

## Architecture & Design Decisions

### 1. Post-Generator Non-Destructive Dynamic Verification (`gates.py`, `enforcement.py`, `reconcile.py`, `solution_audit.py`)

```text
Pre-generator (static gating, generator_built == False):
  solution_build(sol) -> solution_build(brute)
    -> solution_analyze   [sets solution_analyzed=True, std_audited=False]
    -> solution_audit_std [empirical=pending_generator -> std_audited=True, std_empirical_verified=False]
    -> solution_audit_brute [probe=pending_binaries -> brute_audited=True, brute_probed=False]
    -> validator_build -> generator_build [sets generator_built=True]

Post-generator (dynamic verification, generator_built == True & canonical SHA-256 unchanged):
  solution_analyze / solution_audit_std:
    ├─ empirical passes -> std_empirical_verified=True, std_empirical_hash=sha256(sol.cpp)
    │                      PRESERVES std_audited, brute_audited, validator_ready,
    │                      generator_built, checker_ready, interactor_ready, stress_passed
    ├─ empirical skipped / pending_binaries -> std_empirical_verified=False, preserves existing gates
    └─ empirical fails / generator_error / tool fail -> FAIL CLOSED:
         std_empirical_verified=False, std_empirical_hash=None;
         if explicit failure (passed=False or not success) -> clears std_audited & invalidates downstream

  solution_audit_brute:
    ├─ _probe_brute_small_scale status in ("ok", "timeout_circuit_breaker") & passed=True:
    │    -> brute_probed=True, brute_probed_hash=sha256(brute.cpp)
    │       PRESERVES std_audited, brute_audited, validator_ready, generator_built,
    │       checker_ready, interactor_ready, stress_passed
    ├─ pending_binaries / skipped / generator_error -> brute_probed=False, brute_probed_hash=None
    └─ probe crashed / timeout_at_min_scale (passed=False) or tool fail -> FAIL CLOSED:
         brute_probed=False, brute_audited=False & invalidates downstream
```

- **Tradeoff — preserving vs gating `stress_test_run`**: Making `std_empirical_verified` / `brute_probed` a mandatory pre-gate on `stress_test_run` would break established workflows and interactive/communication pipelines that call `solution_analyze` / `solution_audit_*` only before `generator_build`. Instead, keeping the pre-generator gate chain intact while making post-generator runs non-destructive and explicitly tracked (`std_empirical_verified`, `std_empirical_hash`, `brute_probed`, `brute_probed_hash`) lets callers run dynamic verification after `generator_build` (and even after `stress_test_run`) without losing progress, while failing closed whenever a dynamic probe actually fails or source/generator hashes drift.
- **Hash drift & reconciliation**: Because `workflow/hashes.py` and `workflow/state_io.py` are out of scope for this change, `_sync_dynamic_probe_hashes(problem_dir, state)` in `gates.py` runs during `preflight`, `apply_result`, `inspect_workspace_state`, and `reconcile_workspace_state` to clear `std_empirical_verified` / `brute_probed` whenever `sol.cpp`, `brute.cpp`, `gen.cpp`, or `generator_built` drifts.
- **Explicit probe visibility**: `SolutionAuditStdTool` and `SolutionAuditBruteTool` return a structured `dynamic_probe` payload (`{"ran": bool, "status": ..., ...}`) clearly distinguishing `"pending_binaries"` from ran probes (`"passed"`, `"failed"`, `"ok"`, `"timeout_circuit_breaker"`, `"crashed"`, etc.), and `inspect_workspace_state` exposes `dynamic_probes` (`"verified"` / `"pending_binaries"` / `"pending_run"` / `"stale"` / `"not_applicable"`).

### 2. Anti-Vacuous Validator Guard (`validator.py`, `enforcement.py`, `gates.py`)

- `ValidatorBuildTool.execute` counts `positive_count` (`expected_valid is True`) and `negative_count` (`expected_valid is False`), returning `has_positive_cases` (`positive_count > 0`), `has_negative_cases` (`negative_count > 0`), and actionable `warnings` + `message` when either is missing.
- `enforcement.py` requires `parsed is not None and parsed >= 0.9 and bool(data.get("has_positive_cases")) and bool(data.get("has_negative_cases"))` before setting `validator_ready = True`, and persists `validator_has_positive_cases` / `validator_has_negative_cases`.
- **Tradeoff — legacy synthetic tests in `_validator_gate_ok`**: `_validator_gate_ok` continues checking `validator_ready` and `validator_accuracy >= 0.9`, while rejecting `validator_has_positive_cases is False` or `validator_has_negative_cases is False` if recorded. This preserves backward compatibility with unit tests that construct synthetic state dicts (`validator_ready=True, validator_accuracy=1.0`) while preventing real or `apply_result`-driven vacuous validators from passing.

### 3. Communication `.ans` Pack Exemption & Broadened Wrong-Solution Scan (`problem_pack.py`, `exporters/{polygon,hydro,qoj,verify}.py`)

- `problem_pack.py:416` exempts `manifest_is_communication(manifest_model)` alongside `manifest_is_interactive(manifest_model)` from requiring `.ans` files on disk, aligning packaging with `test_verify.py:213` and `audit_standalone.py`.
- `exporters/polygon.py`, `exporters/hydro.py`, and `exporters/qoj.py` compute `answers_optional = is_interactive or is_communication` and pass `allow_missing_answer=answers_optional` to `_collect_safe_test_pairs`, omitting missing or dummy `.ans` placeholders while still preserving genuine non-empty answer files when present.
- `exporters/verify.py` allows optional answer files when the exported Polygon XML contains `<interactor>` or `<communication>` or when QOJ `problem.conf` sets `interaction_mode on` or non-empty `communication_mode`, while continuing to require paired answer files strictly for standard and SPJ packages.
- `problem_pack.py` broadens `solutions/` undeclared wrong-solution scanning to include `wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, and `bf.cpp`.

### 4. Context-Aware Default `require_wrong_solution_kill` Without Modifying `models.py` (`guard.py`, `gates.py`, `reconcile.py`)

- `guard.py` introduces `effective_require_wrong_solution_kill(manifest: AutoCodeManifest) -> bool`:
  1. If `"require_wrong_solution_kill" in manifest.quality_gates.model_fields_set`: return `bool(manifest.quality_gates.require_wrong_solution_kill)` (so explicit `False` always opts out).
  2. Else if `any(s.role == "wrong" for s in manifest.solutions)`: return `True`.
  3. Else: return `bool(manifest.quality_gates.require_wrong_solution_kill)`.
- `check_gates` (`guard.py`), `preflight` (`gates.py`), `apply_result` (`enforcement.py`), and `inspect_workspace_state` (`reconcile.py`) all evaluate `wrong_solution_kill` through `effective_require_wrong_solution_kill(manifest)` for single-truth parity.

## Risks / Trade-offs

- **Broken post-generator probe fails closed**: Re-running `solution_analyze` / `solution_audit_std` / `solution_audit_brute` after `generator_build` must not leave `std_audited` or `brute_audited` intact if the empirical fit or brute probe explicitly fails (`passed=False` or non-zero failure); doing so triggers fail-closed invalidation.
- **Read-back export verification for missing `.ans`**: `verify_exported_packages` (`utils/exporters/verify.py`) is checked to ensure `polygon` / `hydro` / `qoj` exports remain verified consistently; if `verify.py` is outside the authorized change set, any limitation in read-back checks for `.ans`-less communication packages is surfaced explicitly to the coordinator.
- **Unmodified `workflow/models.py`**: Keeping `QualityGates.require_wrong_solution_kill = False` in `models.py` preserves schema compatibility; manifests dumped via `model_dump()` without `exclude_unset` persist explicit values, while omitted manifest keys cleanly trigger the smart default via `model_fields_set`.
