---
name: problem-difficulty-rating
description: "Calibrate an evidence-grounded CF-style difficulty assessment from objective problem_audit signals, statement insight, and LiveCodeBench-Pro cognitive dimensions."
---

# Problem Difficulty Rating Skill

Use this after `problem_audit(include_difficulty=true)` has produced `difficulty_signals`.

`difficulty_signals` uses `rating_method: "evidence_only_v2"` with `rating: null`, `band: "uncalibrated"`, and `calibration_required: true`. AutoCode intentionally refuses to map time complexity (`O(n)`, `O(2^n)`) or `wrong_solution_count` directly to pseudo-Elo scores.

## Required Input

- `difficulty_signals`:
  - `confidence`: evidence completeness score in `[0.0, 1.0]` (independent of complexity or wrong-solution count).
  - `estimated_complexity` and `algorithm_tags`.
  - `structural_evidence`: `static_complexity`, `empirical_complexity`, `n_max`, `largest_value_bound`, `tl_headroom_ratio`, `limit_case_ratio`, `wrong_solution_count`, and `wrong_kill`.
  - `cognitive_dimensions` (LiveCodeBench Pro inspired): `knowledge_signals`, `logic_signals`, and `observation_signals`.
- Problem statement (`statements/README.md`) and editorial (`statements/tutorial.md`) to assess algorithmic observation/insight depth, plus any `risk_report.warnings`.

## Output Format

- `rating`: Provisional CF-style integer (`800`–`3500`) ONLY when explicitly calibrated from statement insight + benchmark anchors, or `null` if statement/insight context is unavailable.
- `band`: `入门` / `基础` / `中等` / `较难` / `困难` / `高难` (or `uncalibrated` when uncalibrated).
- `calibration_status`: `provisional_llm_calibrated` | `uncalibrated`.
- `confidence`: `low` / `medium` / `high`, mapped from `difficulty_signals.confidence` (evidence completeness) and statement clarity.
- `cognitive_assessment`: concise summary across `knowledge`, `logic`, and `observation`.
- `reasons`: 3-5 bullets citing concrete `structural_evidence` and statement/editorial observation depth.
- `why_not_lower`: one sentence explaining why the problem is not easier.
- `why_not_higher`: one sentence explaining why the problem is not harder.
- `calibration_notes`: uncertainty, missing evidence, and human-review notes.

## Rules

- NEVER infer higher difficulty solely because a solution has slower asymptotic complexity (`O(n^2)`, `O(2^n)`) or because more `wrong` solutions are present.
- NEVER infer beginner difficulty solely because the optimal solution runs in `O(n)` or `O(n log n)` (many high-rating constructive, greedy, and math problems are linear).
- Do not invent submit statistics, pass rates, or historical human contest calibration data.
- If `difficulty_signals.confidence < 0.5` or statement/editorial context is missing, mark the assessment provisional/uncalibrated and list missing evidence.
