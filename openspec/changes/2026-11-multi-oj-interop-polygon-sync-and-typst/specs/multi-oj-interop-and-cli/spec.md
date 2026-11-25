## Purpose

Define international multi-OJ package exporters (ICPC/Kattis `2025-09` and IOI CMS), credential-safe Codeforces Polygon REST API sync (`pull`/`push`), Markdown-to-Typst statement PDF rendering, and opt-in least-privilege PR failure reporting in Problem CI without modifying external MCP tool contracts.

## Requirements

### Requirement: ICPC/Kattis 2025-09 and IOI CMS package exporters with strict containment
The system SHALL provide `export_icpc_kattis` and `export_cms` under `src/autocode_mcp/utils/exporters/`, exported via `__init__.py` and dispatched cleanly for `"icpc"`, `"kattis"`, `"icpc_kattis"`, and `"cms"` in `ProblemPackPolygonTool.execute(...)` while preserving all 22 MCP tool input schemas (`tools/schemas.py`) and communication `.ans` exemption. Both exporters SHALL reuse `exporters/common.py` atomic staging, hardlink/copy/zip modes, path traversal/symlink rejection, and read-back validation.

#### Scenario: ICPC/Kattis 2025-09 export produces compliant directory and zip packages
- **WHEN** `export_icpc_kattis` runs on a valid problem package with sample and secret tests, validator, checker/interactor, and solutions
- **THEN** the output contains `problem.yaml` (`problem_format_version: "2025-09"`, `name`, `limits.time_limit`, `limits.time_multiplier`, `limits.memory`, `validation`), `data/sample/*.{in,ans}`, `data/secret/*.{in,ans}`, `submissions/accepted/`, `submissions/wrong_answer/`, `input_validators/`, `output_validators/`, and `problem_statement/problem.en.md`, and rejects escaping `output_rel_dir` or symlinks

#### Scenario: IOI CMS export emits task layout and subtask score weights
- **WHEN** `export_cms` runs on a problem package declaring `manifest.subtasks`
- **THEN** the output contains `task.yaml` with `time_limit`, `memory_limit`, `score_type: "GroupMin"`, `score_type_parameters`, and `subtasks` score weights alongside `input/input*.txt`, `output/output*.txt`, `sol/`, `check/`, and `gen/`

### Requirement: Credential-safe Codeforces Polygon REST API sync CLI
The system SHALL provide `python -m autocode_mcp.cli.polygon_sync` with canonical SHA-512 `apiSig` signing (`rand + "/" + methodName + "?" + sortedParams + "#" + apiSecret`), `pull` saving downloaded assets strictly inside the target directory, and `push` running pre-checks before uploading limits, sources, solutions, tests, and statements. `POLYGON_API_KEY` and `POLYGON_API_SECRET` SHALL be read strictly from environment variables (or explicit Python API kwargs in tests) and SHALL NEVER be written to disk, `.autocode/`, logs, or CLI output.

#### Scenario: Polygon pull blocks escaping paths and never persists credentials
- **WHEN** `pull` receives a file or solution path containing `..`, an absolute path, or a symlink escape under `target_dir`
- **THEN** `polygon_sync` raises `ValueError` before writing outside `target_dir`, and neither `POLYGON_API_KEY`, `POLYGON_API_SECRET`, nor `apiSig` appears anywhere in `target_dir`, `.autocode/`, or CLI stdout/stderr

#### Scenario: Polygon push validates package pre-checks before making API writes
- **WHEN** `push` is invoked on a problem directory missing tests or main solution or containing an invalid manifest
- **THEN** `run_push_precheck` fails before issuing any upload request to Polygon

### Requirement: Markdown-to-Typst statement PDF renderer CLI
The system SHALL provide `python -m autocode_mcp.cli.render_pdf` converting `statements/README.md` (and optional `tutorial.md` with `--include-tutorial`) into clean Typst markup (headings, lists, tables, fenced/inline code, inline/display `$...$` math, and special character escaping in prose), invoking `typst compile` when `typst` exists, supporting `--emit-typ` source output, and reporting actionable installation instructions when `typst` is absent.

#### Scenario: Absent typst binary reports instructions while --emit-typ writes source
- **WHEN** `render_pdf` runs on a host without `typst` in `PATH`
- **THEN** default invocation exits non-zero with clear Typst installation instructions, whereas `--emit-typ` emits the converted `.typ` file with headings, math, code blocks, and escaped Typst specials intact

### Requirement: Opt-in least-privilege PR comment guard in Problem CI workflow
The system SHALL guard the optional PR comment step in `examples/ci/autocode-problem-audit.yml` under `failure() && github.event_name == 'pull_request' && vars.AUTOCODE_PR_COMMENT == 'true'` using `actions/github-script@v7` reading `audit_report.md`, while preserving `on: pull_request`, `runs-on: ubuntu-latest`, top-level `permissions: {contents: read}`, zero `${{ github.event.* }}` interpolation, and all untrusted PR security warnings.

#### Scenario: Problem CI template remains safe under workflow and docs sync tests
- **WHEN** `tests/test_ci_workflows.py` and `tests/test_docs_sync.py` inspect `examples/ci/autocode-problem-audit.yml`
- **THEN** all permission, runner, event-injection, and security-warning checks pass
