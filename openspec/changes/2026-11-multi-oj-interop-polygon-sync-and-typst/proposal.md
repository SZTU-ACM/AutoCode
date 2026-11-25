## Why

AutoCode currently exports Polygon (`problem.xml`), Hydro (`problem.yaml` + `testdata/config.yaml`), and QOJ/UOJ (`problem.conf`) packages, which covers Chinese OJ ecosystems well while leaving international competitive programming platforms and statement/workflow interoperability unserved:
- ICPC and European/global contests frequently exchange packages in the Kattis / ICPC `2025-09` package specification (`problem.yaml`, `data/{sample,secret}/`, `submissions/`, `input_validators/`, `output_validators/`, `problem_statement/problem.en.md`).
- IOI-style national olympiads and training camps rely on the IOI CMS (`task.yaml`, `input/input*.txt`, `output/output*.txt`, `sol/`, `check/`, `gen/`) task layout with subtask score weights.
- Problem authors collaborating with Codeforces Polygon need a credential-safe `polygon_sync` CLI (`pull`/`push`) using canonical `apiSig` SHA-512 request signing without persisting secrets anywhere on disk.
- Authors publishing printable problem statements need a lightweight Markdown-to-Typst PDF rendering CLI (`render_pdf`) that works cleanly with optional tutorials and degrades gracefully when `typst` is absent.
- Problem CI workflows benefit from an opt-in, least-privilege PR failure comment step that surfaces `audit_report.md` without loosening fork/secret boundaries.

## What Changes

- **International Exporters (`src/autocode_mcp/utils/exporters/`)**:
  - `icpc_kattis.py`: Add `export_icpc_kattis(...)` targeting the Kattis/ICPC `2025-09` package spec (`problem_format_version: 2025-09`, `name`, `limits.{time_limit,time_multiplier,memory}`, `validation` mode, `data/sample/*.{in,ans}`, `data/secret/*.{in,ans}`, `submissions/{accepted,wrong_answer}/`, `input_validators/`, `output_validators/`, `problem_statement/problem.en.md`) reusing `exporters/common.py` staging, hardlink/copy/zip modes, path containment, and interactive/communication dummy-answer handling.
  - `cms.py`: Add `export_cms(...)` targeting the IOI CMS task layout (`task.yaml`, `input/input*.txt`, `output/output*.txt`, `sol/`, `check/`, `gen/`, optional `statement/statement.md`) with topological subtask ordering and `GroupMin` score weights when `manifest.subtasks` is declared.
  - `__init__.py` & `src/autocode_mcp/tools/problem_pack.py`: Export `export_icpc_kattis` and `export_cms` in `__all__` and wire `"icpc"` / `"kattis"` / `"icpc_kattis"` / `"cms"` format dispatch cleanly into `ProblemPackPolygonTool.execute(...)` without modifying `tools/schemas.py` (`ProblemPackPolygonInput`) or breaking `"polygon"`, `"hydro"`, `"qoj"`, or communication `.ans` exemption.
- **Polygon REST API Sync CLI (`src/autocode_mcp/cli/polygon_sync.py`)**:
  - Implement `PolygonClient`, canonical `apiSig` SHA-512 request signing (`rand + "/" + methodName + "?" + sortedParams + "#" + apiSecret`), `pull` with strict target-directory containment (rejecting traversal/symlink/zip-slip escapes), and `push` (uploading sources, solutions, tests, limits, and statement after pre-check).
  - Read `POLYGON_API_KEY` and `POLYGON_API_SECRET` strictly from environment variables (or explicit Python API kwargs in tests), guaranteeing credentials never touch disk, `.autocode/`, logs, or CLI output.
- **Typst PDF Statement Renderer (`src/autocode_mcp/cli/render_pdf.py`)**:
  - Convert `statements/README.md` (and optional `tutorial.md` via `--include-tutorial`) into clean Typst `.typ` markup (headings, lists, tables, blockquotes, fenced/inline code, inline/display `$...$` math, and safe escaping of Typst special characters).
  - Invoke `typst compile` when `typst` is installed, support `--emit-typ` source emission, and fail/report cleanly with actionable installation instructions when `typst` is missing.
- **Problem CI PR Comment Guard (`examples/ci/autocode-problem-audit.yml`)**:
  - Add an opt-in, failure-only PR comment step guarded by `failure() && github.event_name == 'pull_request' && vars.AUTOCODE_PR_COMMENT == 'true'` using `actions/github-script@v7` reading `audit_report.md`, while preserving `on: pull_request`, `runs-on: ubuntu-latest`, top-level `permissions: {contents: read}`, and all security boundary warnings.
- **Tests (`tests/test_exporters_icpc_cms_and_sync.py`)**:
  - Add real exporter directory + zip + traversal/symlink containment tests, deterministic Polygon `apiSig` + mocked HTTP `pull`/`push` + zero-credential-persistence tests, Markdown-to-Typst conversion + absent/mocked `typst` CLI tests, and Problem CI YAML security assertions.

## Capabilities

### New Capabilities

- `multi-oj-interop-and-cli`: International package exporters (`icpc_kattis`, `cms`), credential-safe Polygon REST API sync CLI (`python -m autocode_mcp.cli.polygon_sync`), Markdown-to-Typst statement PDF renderer (`python -m autocode_mcp.cli.render_pdf`), and guarded PR failure reporting in the Problem CI template.

### Modified Capabilities

- None to external MCP contracts (`ProblemPackPolygonInput` and all 22 MCP tool schemas remain unchanged).

## Impact

- **Workflow & Gating Alignment (`CLAUDE.md`)**: Preserves all 12 authoring workflow stages, quality/audit gates, communication `.ans` exemption in `problem_pack_polygon`, zero-write standalone audit guarantees, and all 22 MCP tool input schemas (`server.py`, `tools/schemas.py`).
- **Affected Files**:
  - `src/autocode_mcp/utils/exporters/icpc_kattis.py`
  - `src/autocode_mcp/utils/exporters/cms.py`
  - `src/autocode_mcp/utils/exporters/__init__.py`
  - `src/autocode_mcp/tools/problem_pack.py`
  - `src/autocode_mcp/cli/polygon_sync.py`
  - `src/autocode_mcp/cli/render_pdf.py`
  - `examples/ci/autocode-problem-audit.yml`
  - `tests/test_exporters_icpc_cms_and_sync.py`
- **Dependencies**: None added (`pyproject.toml` and `uv.lock` remain unchanged); `polygon_sync` uses Python stdlib `urllib`/`hashlib`/`secrets` and `render_pdf` optionally invokes an external `typst` binary when present.
- **Risks**:
  - *External `ProblemPackPolygonInput` schema drift*: `tests/test_input_schema.py` enforces exact equality with `["polygon", "hydro", "qoj"]` on `ProblemPackPolygonInput.export_formats`. Mitigated by keeping `tools/schemas.py` untouched and supporting `"icpc"`, `"kattis"`, `"icpc_kattis"`, and `"cms"` cleanly in `ProblemPackPolygonTool.execute(...)` and direct exporter functions.
  - *Credential leakage in Polygon API errors or saved artifacts*: Mitigated by reading `POLYGON_API_KEY`/`POLYGON_API_SECRET` strictly from environment/kwargs, redacting signed parameters in error paths, and asserting zero secret presence on disk/stdout.
  - *PR CI workflow security regression*: Mitigated by keeping top-level `permissions: {contents: read}`, `on: pull_request` (no `pull_request_target`), `runs-on: ubuntu-latest`, no `${{ github.event.* }}` interpolation, and gating PR commenting behind `vars.AUTOCODE_PR_COMMENT == 'true'`.

## Non-goals

- Do NOT modify any of the 22 MCP tool input schemas (`server.py`, `tools/schemas.py`, `tools/base.py`).
- Do NOT modify `tools/audit*.py`, `tools/build_all.py`, `tools/solution.py`, `workflow/models.py`, `utils/exporters/verify.py`, `pyproject.toml`, `uv.lock`, or `skills/*`.
- Do NOT add external HTTP or Markdown/PDF Python dependencies (`requests`, `httpx`, `weasyprint`, etc.).
- Do NOT persist Polygon API credentials or `apiSig` values to `.autocode/`, problem files, or logs.
