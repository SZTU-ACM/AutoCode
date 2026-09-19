import pytest

from autocode_mcp.utils.ratio_analyzer import (
    EmpiricalRatioAnalyzer,
    normalize_complexity_expression,
)


def test_normalize_complexity_expression() -> None:
    assert normalize_complexity_expression("O(n log n)") == "O(n log n)"
    assert normalize_complexity_expression("O(N \\log N)") == "O(n log n)"
    assert normalize_complexity_expression("o(n^2)") == "O(n^2)"
    assert normalize_complexity_expression("  O( N * log(N) ) ") == "O(n log n)"
    assert normalize_complexity_expression("O(1)") == "O(1)"
    assert normalize_complexity_expression("O(2^n)") == "O(2^n)"
    assert normalize_complexity_expression("O(n!)") == "O(n!)"
    assert normalize_complexity_expression(None) == "O(n)"


def test_calculate_expected_ratio() -> None:
    r_linear = EmpiricalRatioAnalyzer.calculate_expected_ratio("O(n)", 1000, 10000)
    assert pytest.approx(r_linear, 0.01) == 10.0

    r_quad = EmpiricalRatioAnalyzer.calculate_expected_ratio("O(n^2)", 1000, 10000)
    assert pytest.approx(r_quad, 0.01) == 100.0

    r_cubic = EmpiricalRatioAnalyzer.calculate_expected_ratio("O(n^3)", 100, 1000)
    assert pytest.approx(r_cubic, 0.01) == 1000.0

    r_fact = EmpiricalRatioAnalyzer.calculate_expected_ratio("O(n!)", 20, 22)
    assert pytest.approx(r_fact, 0.01) == 21.0 * 22.0

    r_exp = EmpiricalRatioAnalyzer.calculate_expected_ratio("O(2^n)", 60, 65)
    assert pytest.approx(r_exp, 0.01) == 32.0


def test_fit_log_linear_linear_samples() -> None:
    samples = [
        {"n": 1000, "cpu_time_ms": 1.0},
        {"n": 2000, "cpu_time_ms": 2.05},
        {"n": 5000, "cpu_time_ms": 4.98},
        {"n": 10000, "cpu_time_ms": 10.1},
        {"n": 20000, "cpu_time_ms": 20.3},
    ]
    alpha, r2 = EmpiricalRatioAnalyzer.fit_log_linear(samples)
    assert 0.95 <= alpha <= 1.05
    assert r2 > 0.99


def test_fit_log_linear_quadratic_samples() -> None:
    samples = [
        {"n": 100, "cpu_time_ms": 0.5},
        {"n": 200, "cpu_time_ms": 2.0},
        {"n": 500, "cpu_time_ms": 12.5},
        {"n": 1000, "cpu_time_ms": 50.1},
    ]
    alpha, r2 = EmpiricalRatioAnalyzer.fit_log_linear(samples)
    assert 1.95 <= alpha <= 2.05
    assert r2 > 0.99


def test_verify_complexity_success_linear() -> None:
    samples = [
        {"n": 1000, "cpu_time_ms": 1.2, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 3.7, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 12.5, "status": "ok"},
        {"n": 30000, "cpu_time_ms": 38.0, "status": "ok"},
        {"n": 100000, "cpu_time_ms": 130.0, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is True
    assert res["verdict"] == "verified"
    assert res["claimed_complexity"] == "O(n)"
    assert res["fitted_alpha"] is not None
    assert 0.90 <= res["fitted_alpha"] <= 1.20
    assert len(res["growth_ratios"]) == 4


def test_verify_complexity_falsify_quadratic_pretending_linear() -> None:
    samples = [
        {"n": 1000, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 9.1, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 102.0, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is False
    assert res["verdict"] == "ratio_mismatch"
    assert "remediation_advice" in res
    assert res["fitted_complexity"] == "O(n^2)"


def test_verify_complexity_timeout_at_extreme() -> None:
    samples = [
        {"n": 1000, "cpu_time_ms": 2.0, "status": "ok"},
        {"n": 5000, "cpu_time_ms": 25.0, "status": "ok"},
        {"n": 20000, "cpu_time_ms": None, "status": "timeout"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n log n)", samples, time_limit_ms=1000.0)
    assert res["passed"] is False
    assert res["verdict"] == "timeout_at_extreme"
    assert "timed out" in res["failure_reason"]


def test_verify_complexity_cache_jump_tolerance() -> None:
    samples = [
        {"n": 1000, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 5000, "cpu_time_ms": 5.2, "status": "ok"},
        {"n": 20000, "cpu_time_ms": 21.0, "status": "ok"},
        {"n": 100000, "cpu_time_ms": 600.0, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is True
    assert res["verdict"] == "verified_with_cache_jump"


def test_verify_complexity_no_valid_samples() -> None:
    samples = [
        {"n": 1000, "cpu_time_ms": None, "status": "runtime_error"},
        {"n": 5000, "cpu_time_ms": None, "status": "mle"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is False
    assert res["verdict"] == "no_valid_samples"


def test_verify_complexity_factorial() -> None:
    samples = [
        {"n": 6, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 7, "cpu_time_ms": 7.0, "status": "ok"},
        {"n": 8, "cpu_time_ms": 56.0, "status": "ok"},
        {"n": 9, "cpu_time_ms": 504.0, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n!)", samples, time_limit_ms=2000.0)
    assert res["passed"] is True
    assert res["verdict"] == "verified"
