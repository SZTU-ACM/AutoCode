"""Verify the hook gate reuses the single MCP-side manifest truth (task 6.2).

The hook previously kept a duplicated dictionary copy
(``_manifest_dict_uses_testlib_checker``). It must now delegate to
``autocode_mcp.workflow.manifest.manifest_uses_testlib_checker`` so there is
exactly one source of truth for the testlib-checker decision.
"""

from __future__ import annotations

import importlib

from hook_gates import _manifest_uses_testlib_checker

from autocode_mcp.workflow.manifest import manifest_uses_testlib_checker


def test_dict_copy_removed():
    """The duplicated dictionary implementation must no longer exist."""
    mod = importlib.import_module("hook_gates")
    assert not hasattr(mod, "_manifest_dict_uses_testlib_checker")


def _cases() -> list[dict]:
    return [
        {"problem_name": "p", "special_judge": True, "stress_comparison": "checker"},
        {"problem_name": "p", "special_judge": True, "stress_comparison": "exact"},
        {"problem_name": "p", "special_judge": False, "stress_comparison": "checker"},
        {"problem_name": "p", "special_judge": False, "stress_comparison": "exact"},
    ]


def test_hook_matches_mcp_truth():
    """Hook decision equals the MCP-side single source of truth for every case."""
    for payload in _cases():
        from autocode_mcp.workflow.manifest import AutoCodeManifest

        model = AutoCodeManifest.model_validate(payload)
        assert _manifest_uses_testlib_checker(payload) == manifest_uses_testlib_checker(model)


def test_missing_or_corrupt_manifest_is_false():
    """Both sides treat a missing/unparseable manifest as not using testlib checker."""
    assert _manifest_uses_testlib_checker({}) is False
    assert _manifest_uses_testlib_checker(None) is False
    assert manifest_uses_testlib_checker(None) is False
