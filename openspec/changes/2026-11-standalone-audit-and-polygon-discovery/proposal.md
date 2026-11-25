## Why

`autocode-audit --standalone` currently exhibits two clusters of gaps against real external problem packages and its own specification (`openspec/specs/standalone-problem-audit/spec.md` and `README.md`):

1. **Incomplete Polygon package adaptation and wrong-solution discovery (Issue #12)**:
   - Standard Codeforces Polygon exports frequently store tests without `.in` extensions (e.g., `tests/01` + `tests/01.a` declared via `<judging/testset/input-path-pattern>` and `<answer-path-pattern>`) and declare solutions (`main`, `accepted`/`correct`, `wrong-answer`, `time-limit-exceeded*`, `memory-limit-exceeded`, `rejected`/`incorrect`/`failed`/`presentation-error`), checkers, validators, and interactors inside `<assets>` in `problem.xml`. Previously, `audit_discovery.py` only extracted `<time-limit>` and `<memory-limit>`, while static/live checks and mutation testing hardcoded `*.in`, causing false `tests_present` failures and missed solutions/judges.
   - Wrong-solution auto-discovery in `audit_discovery.py` and `verify_fingerprint.py` only matched `wrong_*.cpp` / `wa_*.cpp`, missing hyphenated and common competitive-programming patterns such as `wrong-*.cpp` (even the repository's own `examples/checker-sample/solutions/wrong-greedy.cpp`), `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, `bf.cpp`, and `wrong/*.cpp`.

2. **Missing standalone ASan/UBSan, isolated empirical complexity, communication mutation passthrough, and strict completeness enforcement (Issue #11)**:
   - `audit_standalone.py` lacked any isolated ASan/UBSan compilation/execution probe on representative inputs, and `audit.py` skipped empirical complexity entirely whenever `standalone=True`.
   - `utils/mutation.py` ignored `interactor_bin` for communication problems and omitted `communicator_exe_override=interactor_bin` when invoking `CommunicationRunner.run_from_manifest`, causing standalone `--mutation-check` to skip `communication` packages such as `examples/communication-sample`.
   - Inferred standalone manifests disabled `require_validator_check` and `require_wrong_solution_kill`, letting packages with neither validator nor wrong solutions pass silently with `decision="go"` without surfacing completeness warnings or offering a `--strict` enforcement mode.
   - `audit.py` ignored structured `review_reason` strings on `statement_consistency` when `needs_human_review=True`.

## What Changes

- **Unified Polygon XML parser & layout discovery (`src/autocode_mcp/utils/problem_layout.py`)**:
  - Implement XXE-guarded (`<!DOCTYPE` / `<!ENTITY` rejected) `parse_polygon_problem_xml(problem_path: Path)` parsing `<judging/testset>` (`time-limit`, `memory-limit`, `test-count`, `input-path-pattern`, `answer-path-pattern`, `<checker>`, `<interactor>`, `<communication>`), `<assets/solutions>` (mapping `main` -> `main`, `accepted`/`correct` -> `reference`, `wrong-answer` -> `wrong`/`WA`, `time-limit-exceeded*` -> `wrong`/`TLE`, `memory-limit-exceeded` -> `wrong`/`MLE`, `rejected`/`incorrect`/`failed`/`presentation-error` -> `wrong`), `<assets/checker>`, `<assets/validators>`, and `<assets/interactor>`, with every path validated under `problem_path` via `safe_resolve_under_root`.
  - Implement `discover_test_pairs(...)` supporting both `*.in` + `.ans`/`.out`/`.a` and extensionless Polygon `tests/01` + `01.a` or custom `problem.xml` patterns.
  - Implement `is_wrong_solution_filename(...)` and `discover_wrong_solution_files(...)` covering `wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, `bf.cpp`, and `wrong/*.cpp`, and wire them into `audit_discovery.py` and `verify_fingerprint.py`.
- **Zero-write temporary Polygon staging (`src/autocode_mcp/tools/audit.py`, `src/autocode_mcp/tools/audit_standalone.py`)**:
  - When `standalone=True` and tests do not use `.in`, validate containment on the original test directory first, then stage clean `.in` + `<answer_ext>` copies into a `TemporaryDirectory` so `_static_quality_signals` and live checks run with zero writes in `problem_path`; populate `structural_signals["tests_present"]` and `exp_test_count` accurately.
- **Standalone ASan/UBSan & isolated empirical complexity (`src/autocode_mcp/tools/audit_standalone.py`)**:
  - Compile `sol` in `tmp_bin_dir` with ASan/UBSan (`-fsanitize=address,undefined -fno-omit-frame-pointer` via `probe_sanitizer_support_async`) and execute up to 3 representative inputs (smallest/median/largest); record `unavailable` gracefully if the toolchain lacks sanitizer runtimes, or emit `blocking_issues` when ASan/UBSan flags a failure.
  - When `mode == "full"`, non-protocol, and a generator source (`files/gen.cpp`, `gen.cpp`, etc.) exists and compiles in `tmp_bin_dir`, run isolated empirical complexity (`MultiScaleSampler` + `EmpiricalRatioAnalyzer`) strictly inside `tmp_bin_dir` (never writing to `problem_path/.autocode/empirical_tests`) and populate `quality_signals["empirical_complexity"]`.
- **Communication mutation passthrough & non-`.in` discovery (`src/autocode_mcp/utils/mutation.py`)**:
  - Honor `interactor_bin` for communication problems and pass `communicator_exe_override=eff_communicator` to `CommunicationRunner.run_from_manifest`, and fall back to `discover_test_pairs` when `tests_dir` contains non-`.in` Polygon inputs.
- **Completeness warnings, `--strict` mode, and `review_reason` propagation (`src/autocode_mcp/cli/audit.py`, `src/autocode_mcp/tools/audit.py`)**:
  - Add `--strict` CLI flag and `strict: bool = False` keyword parameter on `ProblemAuditTool.execute` (keeping `ProblemAuditInput` schema untouched).
  - Emit `completeness_warnings` in standalone reports when a validator (non-protocol) or wrong solutions are missing, and promote those omissions to `blocking_issues` (`decision="no_go"`, exit code `2`) when `strict=True`.
  - Surface `statement_consistency.get("review_reason")` in `audit.py` warnings when `needs_human_review=True`.

## Capabilities

### New Capabilities

- `standalone-polygon-and-strict`: Safe XXE-guarded Polygon `problem.xml` asset/testset discovery, unified wrong-solution filename discovery, zero-write temporary `.in` staging for extensionless/custom Polygon datasets, standalone isolated ASan/UBSan and empirical complexity execution, communication `--mutation-check` override forwarding, and `--strict` standalone completeness gating.

### Modified Capabilities

- Reinforces `openspec/specs/standalone-problem-audit/spec.md` with real Polygon layout parity, ASan/UBSan and empirical complexity checks, and optional strict completeness enforcement without breaking backward-compatible CLI defaults or schemas.

## Impact

- **Workflow & Contract Alignment (`CLAUDE.md`)**: Preserves all 12 workflow stages and keeps all 22 MCP tool input schemas (`ProblemAuditInput`, `server.py`) unchanged.
- **Affected Files**:
  - `src/autocode_mcp/utils/problem_layout.py` (new)
  - `src/autocode_mcp/tools/audit_discovery.py`
  - `src/autocode_mcp/tools/audit_standalone.py`
  - `src/autocode_mcp/tools/audit.py`
  - `src/autocode_mcp/tools/verify_fingerprint.py`
  - `src/autocode_mcp/cli/audit.py`
  - `src/autocode_mcp/utils/mutation.py`
  - `README.md`, `CLAUDE.md`
  - `tests/test_standalone_polygon_and_strict.py` (new)
- **Dependencies**: None added (`xml.etree.ElementTree`, `tempfile`, `MultiScaleSampler`, `EmpiricalRatioAnalyzer`, and `probe_sanitizer_support_async` already exist in stdlib / codebase).
- **Risks**:
  - *ASan virtual address reservation under `RLIMIT_AS`*: Mitigated by passing `disable_as_limit=bool(san_probe.get("asan_verified"))` and `san_probe["env"]` to `run_binary`, matching `stress_test.py`.
  - *External `gen.cpp` with non-AutoCode CLI flags*: Mitigated in standalone empirical complexity by catching generator execution/compile errors gracefully without false-blocking external packages.
  - *Untrusted `problem.xml` XXE / path traversal / symlink escapes*: Mitigated by rejecting `<!DOCTYPE`/`<!ENTITY` before parsing, enforcing 5 MB XML cap, validating test patterns, and running `safe_resolve_under_root` on every declared path and test file before staging or execution.

## Non-goals

- Do NOT modify any of the 22 MCP tool input schemas (`tools/schemas.py`, `server.py`).
- Do NOT modify `workflow/models.py`, `workflow/gates.py`, `tools/verify_static_checks.py`, `tools/audit_constraints.py`, `tools/audit_signals.py`, `tools/complexity.py`, `cli/report_format.py`, or `pyproject.toml`.
- Do NOT alter mutation operators or mutant generation budgets in `utils/mutation.py`.
- Do NOT write any files or `.autocode/` subdirectories into target problem directories during standalone audits.
