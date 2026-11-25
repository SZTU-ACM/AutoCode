---
name: redteam-hacking
description: "Adversarial red-teaming for competitive programming verification: black-box statement cross-checking, white-box Near-AC wrong solution synthesis with exact expected_verdict, anti-hash hacking via jngen.h, and val-verified hack feedback loops."
---

# Red-Team Hacking Skill

Use this skill to move beyond trivial regex single-point mutations and verify that test data defeats realistic competitive programming mistakes while the statement and standard solution agree on every edge case.

## 1. Black-Box Mode (Statement-Only Cross-Check)

**Goal**: Catch shared "std + jury answers both wrong" errors and statement ambiguities that `answer_consistency` cannot detect on its own.

1. Read ONLY `statements/README.md` (title, constraints, I/O spec, samples, protocol). Do not inspect `solutions/sol.cpp`, `files/gen.cpp`, or `tests/*.ans`.
2. Write an independent brute-force or alternative-algorithm solver (`solutions/alt_blind.cpp` in scratch/temp or registered as `role: "reference", expected: "pass"`).
3. Run the blind solver against `tests/` (or small random inputs via `files/gen` + `files/val`):
   - **Disagreement on valid inputs**: inspect whether `statements/README.md` omitted an assumption (e.g., $u = v$ self-loops, $m = 0$, negative weights, tie-breaking order) or whether `solutions/sol.cpp` + `.ans` share a bug.
   - **Blind trap ideas**: write naive/flawed solutions representing natural contestant misreadings of the statement before reading `sol.cpp`.

## 2. White-Box Mode (Targeted Near-AC Wrong Solutions)

**Goal**: Construct realistic Near-AC wrong solutions (`solutions/wrong_*.cpp`) informed by `solutions/sol.cpp`, `files/val.cpp`, and `problem_verify_tests(verify_types=["coverage"])`.

Register each wrong solution in `.autocode/manifest.json` under `solutions[]` with `role: "wrong"`, `expected: "fail"`, and an explicit `expected_verdict` (`"WA"`, `"TLE"`, `"MLE"`, `"RE"`, or `"PE"`).

### Required Attack Families

| Family | Typical Implementation Bug | Expected Verdict | Hack / Coverage Focus |
|---|---|---|---|
| **Fake greedy** | Sorts by wrong key or takes local optimum without exchange-argument proof | `WA` | Small counterexamples where optimal needs non-greedy swap |
| **Missing DP state** | Drops a dimension/flag (e.g. parity, last-chosen, carry, component root) | `WA` | Multi-step dependency chains triggering collapsed state conflict |
| **32-bit `int` overflow** | Accumulates sum/product in `int` or casts too late (`1LL * a * b` missing) | `WA` | Extreme `type=3` values near $10^9$ producing products $> 2^{31}-1$ |
| **Uncleaned multi-test** | Forgets to reset `vis[]`, `dp[]`, `adj[]`, or `ans` between $T$ test cases | `WA` | Multi-test case with large test #1 followed by tiny test #2 |
| **`unordered_map` collision** | Uses `std::unordered_map<int, ...>` / `<long long, ...>` with identity hash | `TLE` | `jngen::anti_unordered_map_sequence(...)` targeting GCC prime buckets (`107897`, `126271`, `108649`, `216617`) |
| **Single-mod string hash** | Rolling polynomial hash with single 30-bit mod (`1e9+7` / `998244353`) or `ull` overflow | `WA` | `jngen::thue_morse_collision_pair(11)` (`ull`) or `jngen::anti_single_mod_hash_pair(mod, base)` |
| **Graph edge-case omissions** | Assumes simple connected graph; misses self-loops, multi-edges, isolated nodes, disconnected components | `WA` / `RE` | `jngen::edge_case_graph(...)` matching validator constraints |
| **Suboptimal complexity** | $O(n^2)$ / $O(n\sqrt{n})$ with full $n_{\max}$ buffers (must NOT crash via small arrays!) | `TLE` | Anti-sort / star / broom / worst-case DAG at full $n_{\max}$ |

### Exact Verdict Hygiene

- Never leave `expected_verdict` ambiguous when constructing a targeted failure mode:
  - A `wrong_tle_*.cpp` solution MUST allocate buffers large enough for $n_{\max}$ so it fails by `TLE`, never `RE` (`SIGSEGV`) or `MLE`.
  - `verify_wrong_kill` verifies `killing_verdict == expected_verdict` and marks `expected_verdict_matched: false` (failing `wrong_solution_kill`) whenever an unintended verdict occurs (e.g., `expected_verdict="TLE"` killed by `"RE"`).

## 3. Isolated Coverage Analysis

Run:
```python
problem_verify_tests(problem_dir=..., verify_types=["coverage"])
```
or configure `"min_branch_coverage"` in `.autocode/manifest.json` (`audit_gates.min_branch_coverage`).
- Inspect `line_coverage`, `branch_coverage`, `mcdc_coverage` (when supported), `uncovered_line_ranges`, and `uncovered_branches`.
- Any uncovered branch in `solutions/sol.cpp` indicates either dead code in `sol.cpp` or a missing boundary/branch scenario in `tests/`. Add targeted test cases until critical decision branches are covered.
- Coverage compilation and profiling run strictly inside an isolated `TemporaryDirectory`; `.gcda`, `.gcno`, `.profraw`, and `.profdata` are never written inside `problem_dir`.

## 4. Hack Feedback Loop & Deterministic CI Persistence

When any wrong solution survives (`killed: false`), mismatches `expected_verdict`, or misses an important branch:

1. **Synthesize hack input**: Use `#include "jngen.h"` alongside `#include "testlib.h"` (e.g. `anti_unordered_map_sequence`, `thue_morse_collision_pair`, `anti_single_mod_hash_pair`, `broom_tree`, `random_dag`) or hand-craft minimal counterexamples.
2. **Validate strictly with `val` and `sol`**:
   - Run `files/val` on the hack `.in` (`exit_code == 0`); never bypass validator constraints.
   - Generate `.ans` using `solutions/sol` and cross-check with your black-box oracle (and `files/checker` / `files/interactor` when applicable).
   - Confirm `problem_verify_tests(verify_types=["wrong_solution_kill"])` now reports `killed: true` and `expected_verdict_matched: true`.
3. **Persist for zero-AI CI**:
   - Place verified `.in`/`.ans` under `tests/hack/` (automatically preserved by `problem_generate_tests(mode="replace")`) or append via `mode="append"`.
   - Keep the high-signal `solutions/wrong_*.cpp` files and their `expected_verdict` entries in `.autocode/manifest.json` so every subsequent `autocode-audit --standalone` or `problem_verify_tests` run exercises them deterministically with zero LLM cost.
