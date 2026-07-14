"""Unit tests for hook gate predicates (scripts/hook_gates.py)."""

from __future__ import annotations

from hook_gates import (
    DEFAULT_QUALITY_GATES,
    PRE_GATES,
    _manifest_dict_uses_testlib_checker,
    _min_limit_ratio_gate_ok,
    _validator_gate_ok,
)


def test_default_quality_gates_present():
    assert DEFAULT_QUALITY_GATES["min_limit_case_ratio"] == 0.5
    assert "require_validator_check" in DEFAULT_QUALITY_GATES


def test_manifest_dict_uses_testlib_checker():
    assert (
        _manifest_dict_uses_testlib_checker({"special_judge": True, "stress_comparison": "checker"})
        is True
    )
    assert (
        _manifest_dict_uses_testlib_checker({"special_judge": True, "stress_comparison": "exact"})
        is False
    )
    assert _manifest_dict_uses_testlib_checker({"special_judge": False}) is False


def test_pre_gates_requires_validator_before_generator():
    reasons = " ".join(reason for _, reason in PRE_GATES["generator_build"])
    assert "validator" in reasons


def test_validator_gate_ok_requires_accuracy():
    assert _validator_gate_ok({"interactive": False, "validator_ready": True, "validator_accuracy": 0.95}, {}) is True
    assert _validator_gate_ok({"interactive": False, "validator_ready": True, "validator_accuracy": 0.5}, {}) is False
    assert _validator_gate_ok({"interactive": True}, {}) is True


def test_min_limit_ratio_gate_ok():
    assert (
        _min_limit_ratio_gate_ok({"quality_gates": {"min_limit_case_ratio": 0.5}, "limit_case_ratio": 0.6}, {}) is True
    )
    assert (
        _min_limit_ratio_gate_ok({"quality_gates": {"min_limit_case_ratio": 0.8}, "limit_case_ratio": 0.5}, {}) is False
    )
    assert _min_limit_ratio_gate_ok({"quality_gates": {}, "limit_case_ratio": None}, {}) is True
