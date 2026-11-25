## Context

External competitive programming packages—especially Codeforces Polygon exports and domestic problem archives—frequently diverge from AutoCode's internal `tests/01.in` + `01.ans` and `solutions/wrong_*.cpp` conventions. At the same time, standalone zero-AI auditing (`autocode-audit --standalone`) must deliver the advertised runtime protections (ASan/UBSan, isolated empirical complexity, communication mutation checks, and strict completeness gating) without ever writing into the target problem directory or modifying shared sibling modules (`audit_signals.py`, `verify_static_checks.py`, `complexity.py`, `schemas.py`).

Naming boundaries remain strictly separated:
- MCP server tools, discovery helpers, and standalone runners live under `src/autocode_mcp/...`.
- Client hook helpers live under `scripts/...` and are untouched by this change.

## Architecture & Design Decisions

### 1. Unified Layout & Polygon XML Discovery (`src/autocode_mcp/utils/problem_layout.py`)

```text
problem.xml (optional, <=5MB, reject <!DOCTYPE / <!ENTITY)
  ├─> <judging/testset> : time-limit, memory-limit, test-count,
  │                       input-path-pattern, answer-path-pattern,
  │                       <checker>, <interactor>, <communication>
  ├─> <assets/solutions>: tag -> role/expected/expected_verdict
  │                       (main, reference, WA/TLE/MLE/wrong)
  └─> <assets/{checker,validators,interactor}>
         │
         ▼  (every path containment-checked via safe_resolve_under_root)
PolygonProblemSpec ──▶ audit_discovery.py (_infer_standalone_manifest / _find_*_source)
                    └─▶ discover_test_pairs() ──▶ audit.py / audit_standalone.py / mutation.py

discover_wrong_solution_files()
  ├─▶ is_wrong_solution_filename()
  │     (wrong_*, wrong-*, wa_*, wa-*, tle_*, tle-*, mle_*, mle-*, re_*, re-*, bf.cpp, wrong/*.cpp)
  ├─▶ audit_discovery.py (_infer_standalone_manifest)
  └─▶ verify_fingerprint.py (_discover_wrong_solution_names, _compute_dataset_fingerprint)
```

- **Tradeoff (XXE rejection)**: `parse_polygon_problem_xml` rejects `<!DOCTYPE` and `<!ENTITY` (case-insensitive) by raising `ValueError` before invoking `xml.etree.ElementTree.fromstring`. Discovery callers catch `(ValueError, OSError)` gracefully so hostile XML cannot crash discovery while `verify_exported_packages` still blocks on invalid `problem.xml`.
- **Tradeoff (Wrong-solution subdir resolution)**: `discover_wrong_solution_files` scans `solutions/`, `solution/`, `solutions/wrong/`, `solution/wrong/`, and `wrong/` for standalone manifest inference (which supplies full relative `s.path` to `audit_standalone.py`), while `verify_fingerprint.py` restricts undeclared disk discovery to `solutions/` direct children because `verify_wrong_kill.py` maps undeclared stems to `solutions/<stem>.cpp`.

### 2. Zero-Write Isolated Polygon Staging (`audit.py`, `audit_standalone.py`)

Because `tools/verify_static_checks.py` and `tools/audit_signals.py` cannot be modified here and expect `*.in` + `<answer_ext>` inside `tests_dir`, standalone audits on non-`.in` test directories (such as Polygon `tests/01` + `01.a`) stage clean copies inside an ephemeral `TemporaryDirectory`:

1. Verify symlink/path containment on the original `tests_dir` inside `problem_path` first (`_find_escaped_entries` + `safe_resolve_under_root`); abort with `path_containment` before reading or copying anything if an escape is detected.
2. Recompute `structural_signals["tests_present"]` and `exp_test_count` in `audit.py` using `discover_test_pairs`.
3. Stage raw file bytes into `<tmp_stage>/tests/<stem>.in` and `<tmp_stage>/tests/<stem><answer_ext>` (plus orphan answer files so `file_count` still catches orphan answers and empty inputs).
4. Point `_check_duplicate_inputs`, `_check_scale_distribution`, and `_run_standalone_live_checks` at `<tmp_stage>/tests/`, while passing the contained real `tests_dir` to `run_mutation_testing` (which checks `_is_contained_non_symlink(problem_dir, tests_dir)` and uses `discover_test_pairs` directly for non-`.in` inputs).

### 3. Standalone ASan/UBSan & Isolated Empirical Complexity (`audit_standalone.py`)

```text
tmp_bin_dir (TemporaryDirectory, zero writes to problem_dir)
  ├─> Sanitizer Probe (non-protocol only):
  │     probe_sanitizer_support_async(compiler)
  │       ├─ unavailable ──▶ quality_signals["sanitizer_check"] = {status: "unavailable", passed: True}
  │       └─ available   ──▶ compile_cpp(opt_level="O1", sanitizers=True, use_cache=False)
  │                            └─ run up to 3 inputs (smallest, median, largest)
  │                               with disable_as_limit=asan_verified & san_probe["env"]
  │                               └─ diagnostic/crash -> blocking_issues ("sanitizer_check")
  │
  └─> Empirical Complexity (mode=="full", non-protocol, generator source present):
        compile gen -> tmp_bin_dir/gen (use_cache=False)
          └─ MultiScaleSampler -> tmp_bin_dir/empirical_tests/scale_*.in
               └─ DynamicExecutionMonitor (cwd=tmp_bin_dir) -> EmpiricalRatioAnalyzer
                    └─ quality_signals["empirical_complexity"]
```

- **Tradeoff (Generator failure & missing claimed complexity in standalone)**: External repositories often lack a `// Time Complexity:` comment or use non-AutoCode generator CLI arguments. When `claimed_complexity` is absent from both `manifest.empirical_complexity` and `sol.cpp` comments, standalone empirical complexity falls back to `estimate_static_complexity(sol_code)`; mismatches against an inferred static fallback emit warnings rather than hard blocks (unless execution hits `timeout_at_scale` / `mle_at_scale`), whereas mismatches against an explicit claim block identically to managed mode. Incompatible generator invocations (`RuntimeError` / `TimeoutExpired`) record `status="generator_error"` with a warning instead of crashing or false-blocking.

### 4. Communication Mutation Passthrough & `--strict` Mode (`utils/mutation.py`, `cli/audit.py`, `audit.py`)

- **`utils/mutation.py`**: For communication problems (`comm_mode != "grader_linked"`), accept a contained non-symlink `interactor_bin` directly (falling back to `resolve_communicator_binary`) as `eff_communicator`, and pass `communicator_exe_override=eff_communicator` to `CommunicationRunner.run_from_manifest`.
- **`--strict` & `completeness_warnings`**: Keep `ProblemAuditInput` (the MCP tool schema) untouched. Add `--strict` to `autocode-audit` CLI and `strict: bool = False` keyword parameter on `ProblemAuditTool.execute`. In standalone mode, always emit `completeness_warnings` for missing non-protocol validators or missing wrong solutions; when `strict=True`, promote those omissions to `blocking_issues` so `decision="no_go"` and CLI exits with code `2`.

## Risks / Trade-offs

- **ASan shadow memory under `RLIMIT_AS`**: On Linux/macOS, ASan reserves terabytes of virtual address space. Mitigated by running with `disable_as_limit=bool(san_probe.get("asan_verified"))` and forwarding `san_probe["env"]`.
- **Cross-platform file cleanup on Windows**: Mitigated by copying test bytes via `read_bytes()` / `write_bytes()` rather than copying read-only file permission bits, and using `TemporaryDirectory(..., ignore_cleanup_errors=True)`.
- **Schema stability**: `ProblemAuditInput` remains strictly identical so `tests/test_input_schema.py` and all 22 MCP tool contracts stay unchanged.
