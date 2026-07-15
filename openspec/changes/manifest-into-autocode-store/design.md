## Architecture

A problem directory now contains only problem content plus a single hidden, self-ignored AutoCode state directory:

```
<problem_dir>/
├── statements/        # committed problem content
├── solutions/         # committed
├── tests/             # committed
├── files/             # committed (checker/gen/val/testlib)
├── problem.xml        # committed (packaging artifact)
└── .autocode/         # AutoCode state — git-ignored via its own .gitignore
    ├── .gitignore     # content: "*"
    ├── manifest.json  # recipe (input): case plan, solutions, gates, difficulty
    └── runtime.json   # traces (output): workflow / test_manifest / generate_checkpoint / audit
```

### Why a self-ignore `.gitignore` inside `.autocode/` (not a root `.gitignore`)

The earlier design wrote a root `.gitignore` listing `.autocode/`. Two problems:
1. It pollutes the problem repo root with an AutoCode-generated file.
2. `problem_create` must not assume the problem lives in *this* repo — problem dirs are independent repos/working copies, so a generated root `.gitignore` is the only lever we had.

A `.autocode/.gitignore` with content `*` is self-contained:
- `*` ignores every file/dir under `.autocode/`, **including the `.gitignore` itself** (git always parses `.gitignore` regardless of ignore rules, but the file is not tracked).
- Net effect: `.autocode/` shows up as having zero trackable entries → `git status` is silent about it, with **no** root `.gitignore` needed.
- If a host repo *also* adds `.autocode/` to its own `.gitignore`, nothing breaks (idempotent).

### Why keep `manifest.json` separate from `runtime.json`

`manifest.json` is the *recipe* (regeneration input); `runtime.json` is the *trace* (run output). They have different lifecycles:
- Clearing / overwriting `runtime.json` (e.g. `problem_cleanup`) must never wipe the recipe.
- `problem_create` writes the recipe once; runtime tools append traces continuously.

Merging them would force "clear traces ⇒ clear recipe", a real data-loss risk. So both live under `.autocode/` but stay as two files, both covered by the same `*` ignore rule.

## Tradeoffs

- **Pro**: problem repo root stays clean (no AutoCode files at all); single source of truth for "all AutoCode state lives under `.autocode/`"; deterministic-ignore without assuming repo layout.
- **Con**: cloning a problem without the (now ignored) `manifest.json` loses the deterministic regeneration recipe. This is acceptable per the user's decision — the problem itself (tests/solutions) remains complete; only AutoCode's regenerate/re-audit metadata is local.
- **Con**: the `.autocode/.gitignore` file itself is ignored, so it is invisible in `git status`; that is the intended behavior, not a bug.

## Risks & mitigations

- **External readers of `autocode.json`** break → mitigated by the error-message cleanup and (post-approval) a quick grep across the repo + docs; MCP signatures unchanged so no contract break.
- **`examples/*` missing recipe** (pre-existing) → repaired by creating `.autocode/manifest.json` for the three samples and updating `test_e2e_examples._copy_example_manifest` to read/write the new path.
- **Tests writing to a not-yet-created `.autocode/`** → each test that writes the manifest must `mkdir(.autocode, exist_ok=True)` before writing (mirrors `save_manifest`'s own behavior).
