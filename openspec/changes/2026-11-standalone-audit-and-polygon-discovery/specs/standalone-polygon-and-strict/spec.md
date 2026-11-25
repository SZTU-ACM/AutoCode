## Purpose

Define standalone zero-write Polygon `problem.xml` and testset adaptation, unified wrong-solution discovery, isolated ASan/UBSan and empirical complexity checks, communication mutation override forwarding, and strict standalone completeness gating.

## Requirements

### Requirement: Safe Polygon problem.xml parsing and unified wrong-solution discovery
The system SHALL parse Polygon `problem.xml` via an XXE-guarded parser that rejects `<!DOCTYPE` and `<!ENTITY` declarations, validates every referenced path under `problem_dir` with `safe_resolve_under_root`, extracts `<judging/testset>` (`time-limit`, `memory-limit`, `input-path-pattern`, `answer-path-pattern`), `<assets/solutions>` tags (`main` -> `main`, `accepted`/`correct` -> `reference`, `wrong-answer` -> `wrong`/`WA`, `time-limit-exceeded*` -> `wrong`/`TLE`, `memory-limit-exceeded` -> `wrong`/`MLE`, `rejected`/`incorrect`/`failed`/`presentation-error` -> `wrong`), `<assets/checker>`, `<assets/validators>`, and `<assets/interactor>`, and SHALL discover wrong solutions matching `wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, `bf.cpp`, and `wrong/*.cpp`.

#### Scenario: Polygon extensionless package and hyphenated wrong solution discovery
- **WHEN** `autocode-audit --standalone` runs on a Polygon package with `problem.xml` (`tests/%02d` + `tests/%02d.a`) or on `examples/checker-sample` containing `solutions/wrong-greedy.cpp`
- **THEN** the parser extracts limits, judges, and tagged solutions safely without XXE/traversal, and discovery detects both extensionless Polygon test pairs and `wrong-greedy.cpp` without modifying `problem_dir`

### Requirement: Zero-write isolated staging for non-.in Polygon datasets
The system SHALL verify path containment on the source test directory and stage clean temporary `.in` + `<answer_ext>` copies inside a `TemporaryDirectory` when `standalone=True` and tests do not use `.in`, updating `structural_signals["tests_present"]` and `exp_test_count` in `audit.py` so static quality checks, live judge checks, and `verify_exported_packages` run seamlessly with zero writes inside `problem_dir`.

#### Scenario: Extensionless Polygon test suite passes full standalone audit read-only
- **WHEN** `problem_audit(standalone=True, mode="full")` audits a Polygon package containing `tests/01` + `tests/01.a`
- **THEN** `structural_signals["tests_present"]` reports the accurate test count, static/live checks execute against the temporary staged copies, `exp_test_count` matches `problem.xml`, and the target directory filesystem snapshot is unchanged

### Requirement: Standalone isolated ASan/UBSan and empirical complexity probes
The system SHALL compile and run the main C++ solution with ASan/UBSan inside `tmp_bin_dir` on up to 3 representative inputs (smallest, median, largest) during `standalone=True, mode="full"` audits—recording `unavailable` gracefully when the toolchain lacks sanitizer runtimes and adding a `blocking_issues` entry if ASan/UBSan flags a runtime/UB failure—and SHALL run isolated empirical complexity (`MultiScaleSampler` + `EmpiricalRatioAnalyzer`) inside `tmp_bin_dir` when a non-protocol package provides a compilable generator source without writing to `problem_dir/.autocode/empirical_tests`.

#### Scenario: Standalone ASan probe catches OOB and empirical probe avoids target directory writes
- **WHEN** `autocode-audit --standalone` runs on a standard package whose `sol.cpp` has a heap/vector out-of-bounds access or whose `files/gen.cpp` compiles cleanly
- **THEN** ASan/UBSan fails with `decision="no_go"` on the OOB solution (or records `unavailable` when unsupported by the toolchain), and empirical complexity executes strictly inside `tmp_bin_dir` while leaving `problem_dir/.autocode/empirical_tests` absent

### Requirement: Communication standalone mutation testing and strict completeness enforcement
The system SHALL forward `interactor_bin` as `communicator_exe_override` in `utils/mutation.py` for communication problems and support non-`.in` input discovery in `run_mutation_testing`, SHALL expose `--strict` on `autocode-audit` and `strict: bool = False` on `ProblemAuditTool.execute` (without altering `ProblemAuditInput`), SHALL emit `completeness_warnings` in standalone reports when non-protocol validators or wrong solutions are missing, and SHALL promote those omissions to `blocking_issues` (`decision="no_go"`, exit code `2`) when `strict=True`.

#### Scenario: Standalone communication mutation check and strict completeness blocking
- **WHEN** `autocode-audit --standalone --mutation-check` runs on a `communication` package and `autocode-audit --standalone --strict` runs on a package missing a validator or wrong solutions
- **THEN** communication mutation testing executes using the isolated temporary communicator binary, standalone reports list `completeness_warnings` for missing validator/wrong solutions, and `--strict` promotes those omissions to `blocking_issues` with `decision="no_go"` and exit code `2`
