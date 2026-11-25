## Purpose

Define the standalone, zero-AI, read-only auditing capability (`autocode-audit --standalone` / `problem_audit(standalone=True)`) for verifying arbitrary external competitive programming problem packages without requiring `.autocode/` state or mutating the target directory.

## Requirements

### Requirement: Read-only zero-write execution on external packages
`autocode-audit --standalone` and `problem_audit(standalone=True)` SHALL treat the target `problem_dir` as strictly read-only. All temporary compilation, execution, mutation testing, and payload spilling SHALL occur inside isolated temporary directories or outside the target problem workspace (`allow_workspace_spill=False`).

#### Scenario: Clean target directory after standalone audit
- **WHEN** `autocode-audit <external_dir> --standalone` runs on a directory without `.autocode/`
- **THEN** no `.autocode/` directory, compiled binaries, or temporary artifacts are written inside `<external_dir>`

### Requirement: Multi-layout package adaptation and verification checks
The standalone audit SHALL support `--layout auto|autocode|standard_cn|polygon|hydro` (including Polygon `problem.xml` and extensionless `01`/`01.a` pairs), optional `--strict` completeness gating (promoting missing validators or wrong solutions from warnings to blocking issues), pluggable sandbox backends via `--sandbox auto|none|bwrap|nsjail|isolate|apple_container`, path containment and symlink escape checks, live validator/solution/wrong-solution verification (with ASan/UBSan and empirical complexity checks), statement-to-validator consistency checks, and optional `--mutation-check` single-point/AST C++ mutation testing (including communication problems).

#### Scenario: Layout auto-detection and wrong-solution / mutation checks
- **WHEN** an external package in `standard_cn`, `polygon`, or `hydro` layout is audited with `--standalone --layout auto --sandbox auto --mutation-check --strict`
- **THEN** the adapter discovers statement, solutions, validator/checker/interactor, and testdata safely in isolated temp builds and reports deterministic `decision` (`go` | `no_go`), `blocking_issues`, and `quality_signals`

### Requirement: Deterministic CLI exit code contract
`autocode-audit` SHALL return deterministic process exit codes suitable for Problem CI pipelines: `0` when `success=true` and `decision == "go"`, `2` when `success=true` and `decision == "no_go"`, and `1` when execution fails (`success=false`).

#### Scenario: CI gate integration
- **WHEN** `autocode-audit <problem_dir> --standalone` finds blocking quality issues and emits `decision: "no_go"`
- **THEN** the CLI prints structured JSON on stdout and exits with code `2`

### Requirement: Output format selection and injection-safe Markdown reporting
`autocode-audit` SHALL support `--format json|markdown|summary` with `--format json` as the backward-compatible default. When `--format markdown` is selected, dynamic text originating from untrusted problem files, compiler/judge stderr, or mutant snippets SHALL have ANSI/control sequences stripped and Markdown/HTML/table delimiters escaped.

#### Scenario: Problem CI Step Summary output
- **WHEN** `autocode-audit <problem_dir> --standalone --format markdown --report audit_report.json` runs
- **THEN** the CLI writes the full JSON report to `audit_report.json`, prints an escaped Markdown audit report suitable for GitHub Actions `GITHUB_STEP_SUMMARY` to stdout, and preserves exit codes `0` (`go`), `2` (`no_go`), or `1` (`error`)
