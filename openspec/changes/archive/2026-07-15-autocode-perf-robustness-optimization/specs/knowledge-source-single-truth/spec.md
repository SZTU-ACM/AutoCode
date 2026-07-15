## ADDED Requirements

### Requirement: Single knowledge source for prompts
The MCP `prompts` module and the agent `skills/*.md` SHALL share a single source of truth for overlapping knowledge. Prompts are concise, client-facing templates that point to the canonical skill for detail (reverse dependency, no runtime file coupling in the server). A consistency test SHALL verify that shared canonical facts (e.g. difficulty rating bounds, generator type scheme, pipeline stages) appear in BOTH the prompt and the skill, so a one-sided edit fails the test.

#### Scenario: Prompt-skill consistency
- **WHEN** a skill's documented canonical fact (e.g. the difficulty rating range or the generator type scheme) changes
- **THEN** the corresponding prompt still carries the same fact and `tests/test_prompts_consistency.py` passes (shared-fact consistency holds)

### Requirement: Pydantic-derived tool input schema
Tool `input_schema` SHALL be derived from Pydantic input models via `model_json_schema()` (helper `input_schema_from_model`), eliminating hand-written JSON Schema drift.

#### Scenario: Derived schema correctness
- **WHEN** a tool declares its input via a Pydantic model
- **THEN** the registered `input_schema` matches the model's JSON schema with correct field names and types

> **Status (7.2 / 7.3 / 7.4): IMPLEMENTED.** `tools/schemas.py` defines input models for all 22 tools and `base.input_schema_from_model` derives the schema (inlines `$defs`, strips `title`); every tool's `input_schema` is now the derived schema and covered by `tests/test_input_schema.py` (which asserts each tool's schema equals its model and is `$ref`/`$defs`-free).

> **Status (7.1): IMPLEMENTED.** Prompts stay concise client-facing templates; the two overlapping prompts (`full_pipeline`, `difficulty_rating`) now carry an explicit pointer to their canonical skill (`autocode-workflow`, `problem-difficulty-rating`) — reverse dependency, no runtime file coupling. `tests/test_prompts_consistency.py` asserts shared canonical facts (difficulty 800–3500 + bands; generator `type=1`…`type=4` scheme across `autocode-workflow`/`stress-strategy`; pipeline `Validator`/`Generator` stages) appear in BOTH the prompt and the skill.
