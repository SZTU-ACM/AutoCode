---
name: problem-difficulty-rating
description: "Turn deterministic problem_audit difficulty signals into a CF-style rating with reasons and confidence."
disable-model-invocation: false
---

# Problem Difficulty Rating Skill

Use this after `problem_audit(include_difficulty=true)` has produced `difficulty_signals`.

## Required Input

- `rating`, `band`, and `confidence` from `difficulty_signals`.
- Evidence fields: `estimated_complexity`, `algorithm_tags`, `constraint_scale`, `implementation_evidence`, and `data_strength`.
- Any warnings from `risk_report`.

## Output Format

- `rating`: CF-style integer from 800 to 3500.
- `band`: 入门 / 基础 / 中等 / 较难 / 困难 / 高难.
- `confidence`: low / medium / high, mapped from the numeric confidence.
- `reasons`: 3-5 bullets that cite concrete signals.
- `why_not_lower`: one sentence explaining why the problem is not easier.
- `why_not_higher`: one sentence explaining why the problem is not harder.
- `calibration_notes`: uncertainty and human-review notes.

## Rules

- Do not invent submit statistics or historical calibration data.
- Do not claim the rating is exact; it is a first-pass estimate unless calibrated by real contest data.
- If `confidence < 0.5`, mark the rating provisional and list missing evidence.
- Prefer evidence from `problem_audit` over intuition.
