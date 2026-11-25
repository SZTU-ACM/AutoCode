## Context

AutoCode's existing exporter subsystem (`src/autocode_mcp/utils/exporters/`) provides atomic staging, hardlink/copy/zip test staging, subtask mapping, topological subtask sorting, and strict symlink/traversal containment in `common.py` for `polygon.py`, `hydro.py`, and `qoj.py`. This change extends export interoperability to international ICPC/Kattis (`2025-09`) and IOI CMS formats, and adds two standalone developer CLIs (`polygon_sync.py` and `render_pdf.py`) plus an opt-in PR audit comment step in `examples/ci/autocode-problem-audit.yml`.

Naming boundaries remain explicit:
- Exporter modules and dispatch stay inside `src/autocode_mcp/utils/exporters/` and `src/autocode_mcp/tools/problem_pack.py`.
- Standalone CLI entrypoints stay inside `src/autocode_mcp/cli/` (`polygon_sync.py`, `render_pdf.py`).
- `tools/schemas.py`, `server.py`, `workflow/models.py`, `scripts/`, and `skills/` remain untouched.

## Architecture & Design Decisions

```text
1. Multi-OJ Exporters
   ProblemPackPolygonTool.execute(..., export_formats=[...])
     ├─ "polygon"            ──> export_polygon(...)    -> exports/polygon/ (or .zip)
     ├─ "hydro"              ──> export_hydro(...)      -> exports/hydro/   (or .zip)
     ├─ "qoj"                ──> export_qoj(...)        -> exports/qoj/     (or .zip)
     ├─ "icpc"|"kattis"|...  ──> export_icpc_kattis(...) -> exports/<fmt>/  (or .zip)
     └─ "cms"                ──> export_cms(...)        -> exports/cms/     (or .zip)
                                      └─ all reuse common.py containment, staging, zip

2. Polygon REST API Sync CLI (python -m autocode_mcp.cli.polygon_sync)
   POLYGON_API_KEY / POLYGON_API_SECRET (env only)
     └─ compute_polygon_api_sig(method, params, secret, rand)
          └─ SHA-512(rand + "/" + method + "?" + sortedParams + "#" + secret)
               ├─ pull -> fetch metadata/files/solutions/tests -> safe_resolve_under_root
               └─ push -> run_push_precheck(problem_dir) -> updateInfo/saveFile/saveSolution/saveTest

3. Typst PDF Statement Renderer (python -m autocode_mcp.cli.render_pdf)
   statements/README.md (+ optional tutorial.md)
     └─ markdown_to_typst(...) [headings, lists, tables, code, $...$/$$...$$ math, escaping]
          ├─ --emit-typ  ──> write .typ source
          └─ shutil.which("typst")
               ├─ found   ──> typst compile <.typ> <.pdf>
               └─ missing ──> actionable install instructions
```

### 1. International Exporters (`icpc_kattis.py`, `cms.py`, `problem_pack.py`)

- **Schema Preservation Tradeoff**: `ProblemPackPolygonInput.export_formats` in `src/autocode_mcp/tools/schemas.py` is asserted by `tests/test_input_schema.py` to remain `["polygon", "hydro", "qoj"]`. Rather than breaking MCP input schema parity, `ProblemPackPolygonTool.execute(...)` widens its internal format dispatcher to accept `"icpc"`, `"kattis"`, `"icpc_kattis"`, and `"cms"` alongside `"polygon"`, `"hydro"`, and `"qoj"`, while exporting `export_icpc_kattis` and `export_cms` directly from `autocode_mcp.utils.exporters`.
- **ICPC/Kattis `2025-09` Mapping**:
  - `problem.yaml` emits `problem_format_version: "2025-09"`, `name`, `type` (`"scoring"` when subtasks exist else `"pass-fail"`), `limits` (`time_limit` seconds, `time_multiplier`, `memory` MiB), and `validation` (`"default"`, `"custom"`, `"custom interactive"`, or `"custom multi-pass"`).
  - Test inputs with `group == "sample"`, `is_sample=True`, or `"sample"` prefix go to `data/sample/*.{in,ans}`; all others go to `data/secret/*.{in,ans}`. Expected output files are always normalized to `.ans` per Kattis rules, while empty/dummy answers in interactive/communication tasks are omitted cleanly.
  - Validators and solutions are staged into `input_validators/`, `output_validators/`, `submissions/accepted/`, and `submissions/wrong_answer/`.
- **IOI CMS Mapping**:
  - `task.yaml` emits `name`, `title`, `time_limit` (seconds), `memory_limit` (MiB), `n_input`, `task_type`, `score_mode`, and when `manifest.subtasks` is non-empty, `score_type: "GroupMin"`, `score_type_parameters`, and explicit `subtasks` weights/dependencies ordered topologically via `_toposort_subtasks`.
  - Test pairs are staged contiguously into `input/input0.txt`..`input{N-1}.txt` and `output/output0.txt`..`output{N-1}.txt`, with solution/validator/checker/generator sources in `sol/`, `check/`, and `gen/`.

### 2. Polygon Sync Security & Containment (`polygon_sync.py`)

- **Zero-Persistence Credentials**: `POLYGON_API_KEY` and `POLYGON_API_SECRET` are read solely from environment variables (or explicit Python API kwargs in tests). Neither CLI flags nor disk files ever accept or store credentials; error messages redact signed parameters so `apiKey`, `apiSecret`, and `apiSig` never appear in stdout, stderr, logs, or `.autocode/`.
- **Canonical `apiSig`**: Generates a 6-character alphanumeric `rand` (or accepts deterministic `rand` for unit tests), sorts all `(key, str(value))` pairs excluding `apiSig`, joins with `&`, and prefixes `rand` to lowercase SHA-512 hex of `rand + "/" + methodName + "?" + sortedParams + "#" + apiSecret`.
- **Strict Path Containment**: Every downloaded file, statement, solution, or archive entry is validated against null bytes, `..` traversal, Windows drives, absolute paths, and symlinks (`safe_resolve_under_root` + path-segment symlink check) before writing under `target_dir`.
- **Fail-Closed Push Pre-Check**: `push` validates manifest parseability, positive limits, statement presence, main solution presence, non-empty test pairs, symlink containment, and existing workflow `tests_verified` status (unless `allow_unverified=True`) before issuing any HTTP write request.

### 3. Typst Renderer & CI Template (`render_pdf.py`, `examples/ci/autocode-problem-audit.yml`)

- **Span-Aware Markdown-to-Typst Conversion**: Rather than brittle global regex replacement (which corrupts `$...$` math or `` `...` `` code), a line/block scanner isolates fenced code blocks, inline code, display `$$...$$`, and inline `$...$` math first, translates common LaTeX math commands (`\le`, `\ge`, `\times`, `\frac`, `\sqrt`, braces in `_{...}`/`^{...}`) into Typst math syntax, and escapes Typst markup specials (`\`, `#`, `$`, `*`, `_`, `@`, `<`, `>`, `[`, `]`, `~`, `` ` ``) strictly in prose spans.
- **Graceful Absence vs Failure**: When `typst` is missing, `render_pdf` prints actionable installation instructions and exits non-zero unless `--emit-typ` is supplied, in which case the `.typ` file is written cleanly with an informative notice about installing `typst` for PDF compilation.
- **Safe PR CI Comment Step**: Uses `if: ${{ failure() && github.event_name == 'pull_request' && vars.AUTOCODE_PR_COMMENT == 'true' }}` and `actions/github-script@v7` reading `audit_report.md` via Node `fs`, avoiding `${{ github.event.* }}` interpolation and preserving top-level `permissions: {contents: read}`, `on: pull_request`, `runs-on: ubuntu-latest`, and all untrusted PR boundary warnings.

## Risks / Mitigations

- **Risk: Overwriting or escaping files during Polygon `pull`**: Mitigated by pre-validating every relative path and every zip member before creating parent directories or writing bytes, plus refusing symlinks inside `target_dir`.
- **Risk: Corrupting LaTeX formulas during Typst escaping**: Mitigated by isolating math/code spans before prose escaping and normalizing whitespace around inline `$...$` vs display `$ ... $` delimiters.
- **Risk: Breaking existing Polygon/Hydro/QOJ exports or tests**: Mitigated by leaving `polygon.py`, `hydro.py`, `qoj.py`, `verify.py`, and `schemas.py` untouched and testing all formats together.
