---
name: stress-strategy
description: Build multi-profile stress tests from brute complexity and constraints, then execute with traceable evidence.
disable-model-invocation: false
---

# Stress Strategy Skill

Goal: build a reproducible and explainable multi-profile stress strategy from brute complexity and constraints, instead of running a single random profile.

## Rules

- Include at least 3 profiles: `tiny_exhaustive`, `random_small`, and `edge_small`.
- Use `type=1` for `tiny_exhaustive`, `type=2` for `random_small`, and `type=3/4` for `edge_small`.
- Keep semantic boundaries explicit:
  - `type=3`: boundary and extreme constraints coverage.
  - `type=4`: targeted worst-case/TLE-inducing patterns, not simple max-parameter scaling.
- When brute complexity is higher than `O(n^2)`, automatically lower `n_max` and `trials`.
- Every profile must be reproducible (fixed seed rules and replayable parameters).

## Execution

When calling `stress_test_run`, pass `stress_profiles` and record completed rounds and failure points for each profile.

## Output Requirements

- `decision`: `go` / `no_go`
- `stress_profiles`: each profile's `name/trials/types/generator_args`
- `execution_summary`: completion status and failed rounds per profile
- `next_fix_hint`: if failed, provide prioritized fix direction (`std/brute/generator/validator`)

## Decision Rules

- `go`: all required profiles complete and no correctness mismatch remains.
- `no_go`: any required profile is incomplete, or mismatches remain unresolved.

## Forbidden Behavior

- Do not run only a single random profile.
- Do not treat `type=4` as a duplicate of `type=3`.
- Do not continue after unresolved mismatches.
