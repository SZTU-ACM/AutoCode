## Context

Issue #10 calls for combining an AI red-team auditing layer (`autocode-redteam-auditor` + `redteam-hacking` skill) with deterministic, zero-AI kernel upgrades (`expected_verdict` enforcement, isolated branch/line/MC/DC coverage, and `jngen.h` generator/anti-hash utilities). All additive manifest contracts (`SolutionEntry.expected_verdict`, `AuditGates.min_branch_coverage`) are already frozen in `src/autocode_mcp/workflow/models.py`.

All implementation boundaries stay strictly within the server/tool/template/agent/skill layers (`src/autocode_mcp/tools/verify_wrong_kill.py`, `src/autocode_mcp/tools/test_verify.py`, `src/autocode_mcp/utils/coverage.py`, `src/autocode_mcp/templates/jngen.h`, `agents/autocode-redteam-auditor.md`, `skills/redteam-hacking/SKILL.md`); no hook logic (`scripts/`) or MCP tool schemas (`tools/schemas.py`, `server.py`) are altered.

## Architecture & Design Decisions

```text
+-------------------------------------------------------------------------+
| AI Red-Team Layer (agents/autocode-redteam-auditor.md + redteam-hacking)|
|  1. Black-box: statement-only alt/brute -> catches std+ans shared bugs  |
|  2. White-box: Near-AC wrong_*.cpp + expected_verdict in manifest.json  |
|  3. Hack loop: jngen.h + val + sol -> tests/hack/*.in + wrong_*.cpp     |
+------------------------------------+------------------------------------+
                                     | invokes deterministic checks
                                     v
+-------------------------------------------------------------------------+
| Deterministic Kernel (ProblemVerifyTestsTool + utils/coverage.py)       |
|  - verify_wrong_kill.py: compares killing_verdict vs expected_verdict   |
|    -> expected_verdict_matched: bool; fails closed on RE-for-TLE etc.   |
|  - utils/coverage.py: TemporaryDirectory copy+compile+run               |
|    ├─ Clang: -fprofile-instr-generate=<tmp>/... -fcoverage-mapping      |
|    │         (+ -fcoverage-mcdc probe) -> llvm-profdata -> llvm-cov     |
|    └─ GCC fallback: --coverage (cwd=tmp) -> gcov -b                     |
|    -> zero writes to problem_dir; enforces min_branch_coverage if set   |
+-------------------------------------------------------------------------+
```

### 1. Exact Verdict Enforcement (`src/autocode_mcp/tools/verify_wrong_kill.py`)

- Look up `expected_verdict: str | None` from `manifest.solutions` for each `role == "wrong"` entry.
- Record `"expected_verdict": expected_verdict` and `"expected_verdict_matched": bool` on every `details` item (including early build/path/judge error entries for structural consistency).
- Semantics:
  - When `expected_verdict is None`: `expected_verdict_matched = True` and `passed` continues to depend strictly on `killed` (100% backward compatible with all existing suites).
  - When `expected_verdict == "AC"`: `expected_verdict_matched = (not judge_failed) and bool(per_test) and all(v == "AC" for v in per_test)`. Combined with `killed` (under `expected="pass"` vs default `expected="fail"`), a `role="wrong"` entry cannot spuriously pass unless explicitly configured with `expected="pass"`.
  - When `expected_verdict in {"WA", "TLE", "MLE", "RE", "PE"}`: `expected_verdict_matched = (not judge_failed) and (killing_verdict == expected_verdict)`. If a solution intended as `TLE` crashes with `RE` (or produces `WA`, or survives as `AC`), `expected_verdict_matched` is `False`, `entry["reason"]` records the exact mismatch (`expected TLE but killed by RE on <test>`), and `_check_wrong_solution_kill` returns `passed=False`.
- **Tradeoff — first-kill verdict vs full-suite scan**: `_check_wrong_solution_kill` stops on the first chunk that kills an `expected="fail"` solution (`killing_verdict`). If a `TLE` solution crashes with `RE` or fails with `WA` on an earlier test (even within the same chunk), `killing_verdict` captures that unintended verdict and fails `expected_verdict_matched` immediately rather than masking an early crash behind a later `TLE`.

### 2. Isolated Coverage Engine (`src/autocode_mcp/utils/coverage.py`, `src/autocode_mcp/tools/test_verify.py`)

- **Zero-write isolation guarantee**: `coverage.py` creates a fresh `tempfile.TemporaryDirectory(prefix="autocode_cov_")`, copies `sol.cpp` (and `files/grader.cpp` when `grader_linked`) into that temporary directory while passing `-I <sol_parent>` (and `-I <problem_dir>/files` if applicable) and `-isystem <TEMPLATES_DIR>`. Compile-time `-fprofile-instr-generate=<tmpdir>/prof_%p_%m.profraw` (Clang) and `cwd=tmpdir` with `--coverage` (GCC) guarantee `.profraw`, `.profdata`, `.gcno`, `.gcda`, `.gcov`, and binaries stay strictly inside `tmpdir`—never writing inside `problem_dir` even for interactive/communication runners or read-only problem packages.
- **Compiler & tool discovery**:
  - Resolves `manifest.compiler` preference when specified, otherwise prefers Clang + `llvm-profdata` + `llvm-cov` (probing `PATH`, `xcrun --find` on macOS, and versioned LLVM binaries) when compatible with the source (e.g., avoiding Apple Clang when `<bits/stdc++.h>` is present), and falls back to GNU GCC (`--coverage` + matching `gcov-N` / `gcov`).
  - Probes `-fcoverage-mcdc` when compiling with Clang and automatically retries without `-fcoverage-mcdc` if unsupported by the host Clang version.
  - Runs GCC coverage test executions sequentially (to avoid `.gcda` concurrent write races) and Clang coverage executions with bounded concurrency (each process gets unique `%p_%m` profile files).
- **Graceful degradation & gating**:
  - When neither Clang `llvm-cov` nor GCC `gcov` toolchain is available on the host, returns `status="unavailable"` with diagnostic details; `passed=True` when optional (`min_branch_coverage is None`) and `passed=False` (fail-closed) when `manifest.audit_gates.min_branch_coverage` is explicitly configured.
  - When `verify_types` is omitted (`None`) in `ProblemVerifyTestsTool.execute`, `"coverage"` is triggered only if `manifest.audit_gates.min_branch_coverage is not None`, preserving fast default execution for existing suites.
  - Because `tools/schemas.py` (`ProblemVerifyTestsInput`) and `tools/verify_fingerprint.py` are out of scope to preserve exact MCP tool schemas, `test_verify.py` handles `"coverage"` cleanly in `execute()`, `_build_quality_signals()`, and `executed_signals` without mutating any MCP tool schema.

### 3. `jngen.h` Template (`src/autocode_mcp/templates/jngen.h`)

- Self-contained C++17 header inside `namespace jngen` using only standard C++17 headers (no `<bits/stdc++.h>`, no calls to C `rand()`/`random()`/`random_shuffle`) so it compiles cleanly under `-std=c++17 -Wall -Wextra` both alongside `#include "testlib.h"` and standalone.
- Provides deterministic `SplitMix64` / `Random` RNG, permutations/arrays/partitions/derangements, trees (`random_tree`, `path_tree`, `star_tree`, `caterpillar_tree`, `binary_tree`, `broom_tree`, `prufer_tree`), graphs/DAGs (`random_graph`, `random_bipartite`, `random_dag`, `grid_graph`, `edge_case_graph` for self-loops/multi-edges/isolated nodes), strings (`random_string`, `palindrome_string`, `fibonacci_string`), and anti-hash generators (`splitmix64_probe`, GCC `libstdc++` `unordered_map` prime-bucket collision sequences, `anti_power_of_two_hash_sequence`, `thue_morse_string` / `thue_morse_collision_pair`, and deterministic single-mod birthday collision finder `anti_single_mod_hash_pair`).

## Risks / Trade-offs

- **Apple Clang vs `<bits/stdc++.h>`**: If `sol.cpp` includes `<bits/stdc++.h>` on macOS without Homebrew GCC installed, neither Apple Clang nor `g++` can compile it; `coverage.py` reports a clear diagnostic (`status="error"` or `"unavailable"` depending on missing compiler vs compile error) without leaving artifacts in `problem_dir`.
- **Single-point `gcov` vs `llvm-cov` precision**: GCC `gcov -b` reports branch counts at basic-block arc granularity without MC/DC (`mcdc_coverage=None`), whereas Clang `llvm-cov` provides exact line, branch, and optional MC/DC metrics. Both normalize to the same JSON schema (`line_coverage`, `branch_coverage`, `mcdc_coverage`, `uncovered_lines`, `uncovered_line_ranges`, `uncovered_branches`).
- **MCP schema boundary**: Keeping `tools/schemas.py` untouched preserves the 22-MCP-tool contract (`test_input_schema.py` passes verbatim); Python callers and `manifest.audit_gates.min_branch_coverage` invoke `"coverage"` through `ProblemVerifyTestsTool.execute` cleanly.
