## Purpose

Consolidate all non-problem runtime artifacts into a single git-ignored file so that a problem directory contains only the problem itself plus one hidden runtime store, eliminating scattered hidden JSON files.

## Requirements

### Requirement: Single runtime byproduct store
All non-problem runtime artifacts SHALL be stored in a single file `.autocode/runtime.json` at the problem directory root, under the keys `workflow`, `test_manifest`, `generate_checkpoint`, and `audit`.

#### Scenario: Consolidated state
- **WHEN** the workflow guard, test verification, stress test, audit, or problem tools read or write runtime state
- **THEN** they read/write `.autocode/runtime.json` under the appropriate key, and no separate `.autocode-workflow/state.json`, `tests/.autocode_tests_manifest.json`, `.autocode_generate_state.json`, or root `audit_report.json` file is created

### Requirement: Byproducts are git-ignored
The `.autocode/` directory SHALL be git-ignored in the problem directory. `problem_create` SHALL write a `.gitignore` inside `.autocode/` whose content is `*` (so `.autocode/` self-ignores, including the `.gitignore` itself); no `.gitignore` SHALL be created at the problem root. The AutoCode repository root does NOT ignore `.autocode/`; problem directories are independent repos.

#### Scenario: Clean version control
- **WHEN** a problem directory is inside a git repository
- **THEN** `.autocode/` is ignored (via its own internal `.gitignore` containing `*`) and the runtime store does not appear in `git status`

### Requirement: Problem directory contains only problem artifacts plus the runtime store
A problem directory SHALL contain the problem artifacts (statement, solutions, validator, generator, checker/interactor, tests, `.autocode/manifest.json`, `problem.xml`) and the single `.autocode/` runtime store; no other hidden runtime JSON files SHALL be scattered at the root or under `tests/`.

#### Scenario: No scattered byproducts
- **WHEN** a full authoring run completes
- **THEN** the only runtime artifact present is `.autocode/runtime.json`; the previous scattered hidden JSON files are absent

### Requirement: Single source for byproduct filenames
All tools SHALL reference a single shared `runtime_store` module for byproduct filenames and paths; no tool SHALL define its own duplicate filename constant (e.g. `_TEST_MANIFEST_FILENAME`, `_GENERATE_STATE_FILENAME`, `_WORKFLOW_STATE`) for the consolidated artifacts.

#### Scenario: No duplicate constants
- **WHEN** `problem.py`, `test_verify.py`, or `audit.py` read or write a runtime artifact
- **THEN** they import the path/key from `runtime_store`, and no per-file copy of the filename constant exists
