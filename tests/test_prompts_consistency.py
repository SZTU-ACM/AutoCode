"""
Consistency tests for the "single source of truth" relationship between the
MCP ``prompts`` module and the agent ``skills/*.md`` (change 7.1, option B).

Design:
- Prompts are concise, client-facing templates returned by the MCP server
  (``server.list_prompts`` / ``server.get_prompt``). Skills are the canonical,
  agent-facing source for overlapping knowledge.
- Where a prompt and a skill share a canonical fact (e.g. the difficulty rating
  bounds, the generator type scheme, the pipeline stages), that fact MUST appear
  in BOTH so drift between the two is caught by this test.
- The two prompts that overlap a skill (``full_pipeline``, ``difficulty_rating``)
  carry an explicit pointer to the skill (reverse dependency, no runtime file
  coupling in the server).
"""
from __future__ import annotations

from pathlib import Path

from autocode_mcp.prompts import get_prompt, list_prompts

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skill_text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


# (prompt_name, skill_relative_path, [shared-fact substrings present in BOTH])
SHARED_FACTS = [
    (
        "difficulty_rating",
        "skills/problem-difficulty-rating/SKILL.md",
        ["800", "3500", "入门", "高难"],
    ),
    (
        "generator",
        "skills/autocode-workflow/SKILL.md",
        ["type=1", "type=4"],
    ),
    (
        "generator",
        "skills/stress-strategy/SKILL.md",
        ["type=3", "type=4"],
    ),
    (
        "full_pipeline",
        "skills/autocode-workflow/SKILL.md",
        ["Validator", "Generator"],
    ),
]


def test_prompt_skill_shared_facts_consistent():
    # Every canonical fact shared between a prompt and a skill must appear in
    # BOTH, so a one-sided edit fails this test (catches prompt/skill drift).
    for prompt_name, skill_rel, facts in SHARED_FACTS:
        prompt_text = get_prompt(prompt_name)
        assert prompt_text, f"prompt {prompt_name!r} resolved to empty text"
        skill_text = _skill_text(skill_rel)
        for fact in facts:
            assert fact in prompt_text, (
                f"shared fact {fact!r} missing in prompt {prompt_name!r}"
            )
            assert fact in skill_text, (
                f"shared fact {fact!r} missing in skill {skill_rel!r}"
            )


def test_overlapping_prompts_point_to_skill():
    # Reverse dependency: the prompt delegates canonical detail to the skill.
    assert "autocode-workflow" in get_prompt("full_pipeline")
    assert "problem-difficulty-rating" in get_prompt("difficulty_rating")


def test_all_listed_prompts_resolve():
    # Sanity: every registered prompt name resolves to non-empty text.
    for name in list_prompts():
        assert get_prompt(name), f"prompt {name!r} resolved to empty text"
