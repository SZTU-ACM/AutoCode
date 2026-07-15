## Purpose

Consolidate all non-problem AutoCode state into a single git-ignored directory so that a problem directory contains only the problem itself plus one hidden `.autocode/` store; the manifest recipe is also an ignored AutoCode byproduct (not a committed problem artifact), and no root `.gitignore` is generated.

## MODIFIED Requirements

### Requirement: Single runtime byproduct store
All AutoCode-managed state for a problem SHALL live under a single hidden directory `.autocode/` at the problem root, containing `manifest.json` (the problem recipe, formerly `autocode.json` at the root) and `runtime.json` (runtime traces); no `autocode.json` SHALL exist at the problem root, and no scattered hidden JSON files SHALL remain at the root or under `tests/`.

#### Scenario: Consolidated state
- **WHEN** the workflow guard, test verification, stress test, audit, or problem tools read or write AutoCode state
- **THEN** they read/write `.autocode/manifest.json` or `.autocode/runtime.json`; no root `autocode.json`, no `.autocode-workflow/state.json`, no `tests/.autocode_tests_manifest.json`, no `.autocode_generate_state.json`, and no root `audit_report.json` is created

### Requirement: Byproducts are git-ignored
`problem_create` SHALL NOT generate a `.gitignore` at the problem root. Instead it SHALL write a self-ignoring `.autocode/.gitignore` containing `*` so every file under `.autocode/` (both `manifest.json` and `runtime.json`) is ignored and `.autocode/` produces no untracked entries in `git status`.

#### Scenario: Clean version control
- **WHEN** a problem directory is inside a git repository
- **THEN** `.autocode/` is ignored purely via its own internal `.gitignore` (content `*`), no root `.gitignore` is present, and `git status` reports nothing under `.autocode/`

### Requirement: Problem directory contains only problem artifacts plus the runtime store
A problem directory SHALL contain the problem artifacts (statement, solutions, validator, generator, checker/interactor, tests, `problem.xml`) and the single `.autocode/` AutoCode store; `autocode.json` SHALL NOT be a committed problem artifact — the manifest recipe is a git-ignored byproduct under `.autocode/`.

#### Scenario: No scattered byproducts and no committed recipe
- **WHEN** a full authoring run completes
- **THEN** the only AutoCode footprint is the ignored `.autocode/` directory; the previous scattered hidden JSON files and the committed root `autocode.json` are both absent

### Requirement: Single source for byproduct filenames
All tools SHALL reference a single shared `runtime_store` module for runtime filenames/paths; `workflow/manifest.py` SHALL own the manifest filename and path via `MANIFEST_NAME` / `manifest_path` (now under `.autocode/`); no tool SHALL define its own duplicate filename constant for the consolidated artifacts.

#### Scenario: No duplicate constants
- **WHEN** `problem.py`, `test_verify.py`, `audit.py`, or `manifest.py` read or write an AutoCode artifact
- **THEN** they import the path/key from `runtime_store` (runtime) or `manifest.py` (manifest), and no per-file copy of the filename constant exists
