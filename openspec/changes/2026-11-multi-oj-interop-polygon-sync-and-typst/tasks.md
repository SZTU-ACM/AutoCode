## 1. OpenSpec Documentation

- [x] 1.1 Create `.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, and `specs/multi-oj-interop-and-cli/spec.md` under `openspec/changes/2026-11-multi-oj-interop-polygon-sync-and-typst/` compliant with `openspec/config.yaml`.

## 2. International Exporters (`icpc_kattis.py`, `cms.py`, `problem_pack.py`)

- [x] 2.1 Implement `export_icpc_kattis` and `_validate_icpc_kattis_export` in `src/autocode_mcp/utils/exporters/icpc_kattis.py` matching Kattis/ICPC `2025-09` layout (`problem.yaml`, `data/sample/`, `data/secret/`, `submissions/accepted/`, `submissions/wrong_answer/`, `input_validators/`, `output_validators/`, `problem_statement/problem.en.md`) using `common.py` containment and staging helpers.
- [x] 2.2 Implement `export_cms` and `_validate_cms_export` in `src/autocode_mcp/utils/exporters/cms.py` matching IOI CMS layout (`task.yaml`, `input/input*.txt`, `output/output*.txt`, `sol/`, `check/`, `gen/`, optional `statement/statement.md`) with subtask score weight support.
- [x] 2.3 Export `export_icpc_kattis` and `export_cms` in `src/autocode_mcp/utils/exporters/__init__.py` and register `"icpc"`, `"kattis"`, `"icpc_kattis"`, and `"cms"` dispatch in `src/autocode_mcp/tools/problem_pack.py` without changing `tools/schemas.py`.

## 3. Polygon Sync, Typst PDF Renderer, and PR CI Template

- [x] 3.1 Implement `src/autocode_mcp/cli/polygon_sync.py` (`compute_polygon_api_sig`, `PolygonClient`, `pull_problem`, `push_problem`, `run_push_precheck`, CLI `main`) with SHA-512 `apiSig`, env-only credentials, zero disk/stdout secret leakage, and strict path containment.
- [x] 3.2 Implement `src/autocode_mcp/cli/render_pdf.py` (`markdown_to_typst`, `render_statement_pdf`, CLI `main`) supporting `--include-tutorial`, `--emit-typ`, safe Typst escaping, `typst compile` invocation, and clean actionable guidance when `typst` is absent.
- [x] 3.3 Update `examples/ci/autocode-problem-audit.yml` with the opt-in failure-only `AUTOCODE_PR_COMMENT` PR comment step while keeping `tests/test_docs_sync.py` and `tests/test_ci_workflows.py` green.

## 4. Verification & Full Regression Suite

- [x] 4.1 Create `tests/test_exporters_icpc_cms_and_sync.py` covering real `export_icpc_kattis` and `export_cms` directory/zip exports and containment checks, deterministic Polygon `apiSig` and mocked HTTP `pull`/`push` with zero credential persistence, Markdown-to-Typst conversion with missing/mocked `typst`, and CI YAML security assertions.
- [x] 4.2 Run targeted checks (`uv run pytest tests/test_exporters_icpc_cms_and_sync.py tests/test_utils/test_exporters.py tests/test_docs_sync.py tests/test_ci_workflows.py tests/test_input_schema.py -q`) and full gates (`uv run ruff check .`, `uv run mypy src/`, `uv run python scripts/sync_plugin_version.py --check`, `uv run pytest tests/ -q`).
