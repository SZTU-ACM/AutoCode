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


def test_verify_complexity_sub_15ms_clean_quadratic_still_falsified() -> None:
    """Clean high-SNR O(n^2) growth under 15ms must STILL be falsified, not bypassed as noise."""
    samples = [
        {"n": 100, "cpu_time_ms": 0.2, "status": "ok"},
        {"n": 200, "cpu_time_ms": 0.8, "status": "ok"},
        {"n": 400, "cpu_time_ms": 3.2, "status": "ok"},
        {"n": 800, "cpu_time_ms": 12.8, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is False
    assert res["verdict"] == "ratio_mismatch"
    assert res["fitted_complexity"] == "O(n^2)"


def test_verify_complexity_noisy_o1_within_noise_floor_accepted() -> None:
    """Tiny-scale O(1) jitter under 15ms without a clean scaling signal passes within noise floor."""
    samples = [
        {"n": 10, "cpu_time_ms": 2.1, "status": "ok"},
        {"n": 12, "cpu_time_ms": 1.8, "status": "ok"},
        {"n": 15, "cpu_time_ms": 4.5, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(1)", samples, time_limit_ms=1000.0)
    assert res["passed"] is True
    assert res["verdict"] == "verified_within_noise_floor"


def test_verify_complexity_noisy_windows_nlogn_preserves_claimed_complexity() -> None:
    """Windows 计时底噪下的低信噪比 O(n log n) 采样不得退化覆盖为 O(1)。"""
    samples = [
        {"n": 1000, "cpu_time_ms": 15.6, "status": "ok"},
        {"n": 5000, "cpu_time_ms": 31.2, "status": "ok"},
        {"n": 20000, "cpu_time_ms": 15.6, "status": "ok"},
        {"n": 100000, "cpu_time_ms": 31.2, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n log n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is True
    assert res["verdict"] == "verified_within_noise_floor"
    assert res["fitted_complexity"] == "O(n log n)"


def test_verify_complexity_flat_startup_plateau_still_falsifies_quadratic() -> None:
    """即使存在 ~12ms 进程启动固定开销平台，低常数 O(n^2) 伪装 O(n) 仍须被识别并阻断。"""
    samples = [
        {"n": 200, "cpu_time_ms": 12.38, "status": "ok"},
        {"n": 500, "cpu_time_ms": 12.20, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 12.26, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 16.82, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 62.57, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(n)", samples, time_limit_ms=2000.0)
    assert res["passed"] is False
    assert res["verdict"] == "ratio_mismatch"
    assert res["fitted_complexity"] == "O(n^2)"


def test_verify_complexity_linux_low_startup_plateau_falsifies_quadratic_without_linear_fp() -> None:
    """Linux 2–5ms fork+exec 启动底噪平台下，低常数 O(n^2) 伪装 O(n) 须拦截且真 O(n) 不误报。"""
    quad_samples = [
        {"n": 200, "cpu_time_ms": 2.67, "status": "ok"},
        {"n": 500, "cpu_time_ms": 3.58, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 2.84, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 8.70, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 50.0, "status": "ok"},
    ]
    res_quad = EmpiricalRatioAnalyzer.verify_complexity("O(n)", quad_samples, time_limit_ms=2000.0)
    assert res_quad["passed"] is False
    assert res_quad["verdict"] == "ratio_mismatch"

    linear_samples = [
        {"n": 200, "cpu_time_ms": 2.67, "status": "ok"},
        {"n": 500, "cpu_time_ms": 3.58, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 5.20, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 11.70, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 32.70, "status": "ok"},
    ]
    res_linear = EmpiricalRatioAnalyzer.verify_complexity("O(n)", linear_samples, time_limit_ms=2000.0)
    assert res_linear["passed"] is True


def test_verify_complexity_linux_sub_15ms_flat_plateau_falsifies_quadratic_without_linear_fp() -> None:
    """低启动底噪且峰值 < 15ms（如 13.5ms）的干净 O(n^2) 伪装 O(n) 仍须阻断，且线性不误报。"""
    quad_samples = [
        {"n": 200, "cpu_time_ms": 1.5, "status": "ok"},
        {"n": 500, "cpu_time_ms": 1.6, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 1.9, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 4.5, "status": "ok"},
        {"n": 6000, "cpu_time_ms": 13.5, "status": "ok"},
    ]
    res_quad = EmpiricalRatioAnalyzer.verify_complexity("O(n)", quad_samples, time_limit_ms=2000.0)
    assert res_quad["passed"] is False
    assert res_quad["verdict"] == "ratio_mismatch"

    linear_samples = [
        {"n": 200, "cpu_time_ms": 1.5, "status": "ok"},
        {"n": 500, "cpu_time_ms": 1.6, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 2.5, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 5.5, "status": "ok"},
        {"n": 6000, "cpu_time_ms": 11.5, "status": "ok"},
    ]
    res_linear = EmpiricalRatioAnalyzer.verify_complexity("O(n)", linear_samples, time_limit_ms=2000.0)
    assert res_linear["passed"] is True

    # 首个样本存在冷启动毛刺（4.5ms），后段仍呈 O(n^2) 时不得漏放，真线性仍须通过
    cold_spike_quad = [
        {"n": 200, "cpu_time_ms": 4.5, "status": "ok"},
        {"n": 500, "cpu_time_ms": 1.8, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 2.0, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 4.5, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 26.0, "status": "ok"},
    ]
    res_cold_quad = EmpiricalRatioAnalyzer.verify_complexity("O(n)", cold_spike_quad, time_limit_ms=2000.0)
    assert res_cold_quad["passed"] is False
    assert res_cold_quad["verdict"] == "ratio_mismatch"


def test_verify_complexity_high_startup_plateau_with_vm_jitter_falsifies_quadratic_without_linear_fp() -> None:
    """高启动平台（~14ms）带 >2ms 调度抖动时，干净 O(n^2) 仍须拦截，且线性与 O(n log n) 不误报。"""
    ci_quad_samples = [
        {"n": 200, "cpu_time_ms": 13.28, "status": "ok"},
        {"n": 500, "cpu_time_ms": 15.86, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 14.73, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 28.24, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 140.0, "status": "ok"},
    ]
    res_quad = EmpiricalRatioAnalyzer.verify_complexity("O(n)", ci_quad_samples, time_limit_ms=2000.0)
    assert res_quad["passed"] is False
    assert res_quad["verdict"] == "ratio_mismatch"
    assert res_quad["fitted_complexity"] == "O(n^2)"

    linear_samples = [
        {"n": 200, "cpu_time_ms": 13.0, "status": "ok"},
        {"n": 500, "cpu_time_ms": 16.5, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 24.0, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 44.0, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 114.0, "status": "ok"},
    ]
    res_lin = EmpiricalRatioAnalyzer.verify_complexity("O(n)", linear_samples, time_limit_ms=2000.0)
    assert res_lin["passed"] is True

    nlogn_samples = [
        {"n": 200, "cpu_time_ms": 14.0, "status": "ok"},
        {"n": 500, "cpu_time_ms": 16.0, "status": "ok"},
        {"n": 1000, "cpu_time_ms": 16.0, "status": "ok"},
        {"n": 3000, "cpu_time_ms": 21.0, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 40.6, "status": "ok"},
    ]
    res_nlogn = EmpiricalRatioAnalyzer.verify_complexity("O(n log n)", nlogn_samples, time_limit_ms=2000.0)
    assert res_nlogn["passed"] is True


def test_verify_complexity_narrow_scale_span_noise_vs_exponential_blowup() -> None:
    """极窄 N 跨度（<2x，如 n=[11..15]）的温和进程启动抖动应豁免，但真正指数爆炸（16x）仍必须拦截。"""
    jitter_samples = [
        {"n": 11, "cpu_time_ms": 52.0, "status": "ok"},
        {"n": 12, "cpu_time_ms": 48.0, "status": "ok"},
        {"n": 13, "cpu_time_ms": 50.0, "status": "ok"},
        {"n": 14, "cpu_time_ms": 56.0, "status": "ok"},
        {"n": 15, "cpu_time_ms": 64.0, "status": "ok"},
    ]
    res_jitter = EmpiricalRatioAnalyzer.verify_complexity("O(1)", jitter_samples, time_limit_ms=1000.0)
    assert res_jitter["passed"] is True
    assert res_jitter["verdict"] == "verified_within_noise_floor"
    assert res_jitter["fitted_complexity"] == "O(1)"

    exp_samples = [
        {"n": 11, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 12, "cpu_time_ms": 2.0, "status": "ok"},
        {"n": 13, "cpu_time_ms": 4.0, "status": "ok"},
        {"n": 14, "cpu_time_ms": 8.0, "status": "ok"},
        {"n": 15, "cpu_time_ms": 16.0, "status": "ok"},
    ]
    res_exp = EmpiricalRatioAnalyzer.verify_complexity("O(1)", exp_samples, time_limit_ms=1000.0)
    assert res_exp["passed"] is False
    assert res_exp["verdict"] == "ratio_mismatch"


def test_verify_complexity_exponential_bitmask_dp_semi_log_fit() -> None:
    """O(2^n) / O(n 2^n) 状压题应使用半对数指数模型拟合底数而非扭曲幂律斜率。"""
    assert normalize_complexity_expression("O(n * 2^n)") == "O(n 2^n)"
    samples = [
        {"n": 14, "cpu_time_ms": 4.0, "status": "ok"},
        {"n": 16, "cpu_time_ms": 16.5, "status": "ok"},
        {"n": 18, "cpu_time_ms": 67.0, "status": "ok"},
        {"n": 20, "cpu_time_ms": 270.0, "status": "ok"},
    ]
    res = EmpiricalRatioAnalyzer.verify_complexity("O(2^n)", samples, time_limit_ms=3000.0)
    assert res["passed"] is True
    assert res["fit_model"] == "exponential"
    assert res["fitted_complexity"] == "O(2^n)"
    assert 1.9 <= res["exponential_fit"]["base"] <= 2.15


def test_verify_query_complexity_interactive_log_n_and_falsify_linear() -> None:
    """交互题 O(log n) 应按 queries 随 log2(n) 曲线验证，并能识破线性查询与超预算。"""
    log_samples = [
        {"n": 16, "queries": 4, "cpu_time_ms": 1.2, "status": "ok"},
        {"n": 1024, "queries": 10, "cpu_time_ms": 1.1, "status": "ok"},
        {"n": 1048576, "queries": 20, "cpu_time_ms": 1.3, "status": "ok"},
        {"n": 10**12, "queries": 40, "cpu_time_ms": 1.2, "status": "ok"},
        {"n": 10**18, "queries": 60, "cpu_time_ms": 1.2, "status": "ok"},
    ]
    ok_res = EmpiricalRatioAnalyzer.verify_query_complexity(
        "O(log n)", log_samples, max_queries=65, time_limit_ms=2000.0
    )
    assert ok_res["passed"] is True
    assert ok_res["verdict"] == "verified_query_curve"
    assert ok_res["fit_metric"] == "queries"
    assert ok_res["fitted_complexity"] == "O(log n)"
    assert ok_res["query_curve"]["queries_per_log2_n"] == pytest.approx(1.0, abs=0.05)
    assert ok_res["r_squared"] > 0.99

    # Exceeding protocol.max_queries must fail
    over_res = EmpiricalRatioAnalyzer.verify_query_complexity(
        "O(log n)", log_samples, max_queries=50, time_limit_ms=2000.0
    )
    assert over_res["passed"] is False
    assert over_res["verdict"] == "query_budget_exceeded"

    # Linear query count pretending to be O(log n) must fail
    linear_q_samples = [
        {"n": 10, "queries": 10, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 100, "queries": 100, "cpu_time_ms": 1.2, "status": "ok"},
        {"n": 1000, "queries": 1000, "cpu_time_ms": 1.5, "status": "ok"},
        {"n": 10000, "queries": 10000, "cpu_time_ms": 2.0, "status": "ok"},
    ]
    bad_res = EmpiricalRatioAnalyzer.verify_query_complexity(
        "O(log n)", linear_q_samples, time_limit_ms=2000.0
    )
    assert bad_res["passed"] is False
    assert bad_res["verdict"] == "query_ratio_mismatch"
    assert bad_res["fitted_complexity"] == "O(n)"


def test_empirical_analyzer_review_regressions() -> None:
    """回归：O(3^n) 冒充 O(2^n) 必须拦截、MLE/RE 不静默跳过、O(sqrt n) 交互查询拦截、dominant_var 归一化与模板防 DoS。"""
    from pydantic import ValidationError

    from autocode_mcp.utils.scale_sampler import MultiScaleSampler
    from autocode_mcp.workflow.models import EmpiricalComplexityConfig

    # 1. Normalization with dominant_var aliasing & LaTeX braces
    assert normalize_complexity_expression("O(T)", dominant_var="T") == "O(n)"
    assert normalize_complexity_expression("O(2^k)", dominant_var="k") == "O(2^n)"
    assert normalize_complexity_expression("O(k 2^k)", dominant_var="k") == "O(n 2^n)"
    assert normalize_complexity_expression("O(log M)", dominant_var="M") == "O(log n)"
    assert normalize_complexity_expression("O(2^{n})") == "O(2^n)"
    assert normalize_complexity_expression("O(2^n * n)") == "O(n 2^n)"
    assert normalize_complexity_expression(r"O(\sqrt{n})") == "O(sqrt n)"

    # 2. O(3^n) pretending to be O(2^n) must FAIL via authoritative exponential fit
    three_pow_samples = [
        {"n": 10, "cpu_time_ms": 2.0, "status": "ok"},
        {"n": 12, "cpu_time_ms": 18.0, "status": "ok"},
        {"n": 14, "cpu_time_ms": 162.0, "status": "ok"},
        {"n": 16, "cpu_time_ms": 1458.0, "status": "ok"},
    ]
    res_3n = EmpiricalRatioAnalyzer.verify_complexity("O(2^n)", three_pow_samples, time_limit_ms=5000.0)
    assert res_3n["passed"] is False
    assert res_3n["verdict"] == "ratio_mismatch"
    assert res_3n["fit_model"] == "exponential"
    assert res_3n["fitted_complexity"] == "O(3^n)"

    # 3. MLE / runtime_error at max scale must fail instead of silently dropping the sample
    mle_samples = [
        {"n": 1000, "cpu_time_ms": 5.0, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 50.0, "status": "ok"},
        {"n": 100000, "cpu_time_ms": None, "status": "mle"},
    ]
    res_mle = EmpiricalRatioAnalyzer.verify_complexity("O(n)", mle_samples, time_limit_ms=2000.0)
    assert res_mle["passed"] is False
    assert res_mle["verdict"] == "mle_at_scale"

    re_samples = [
        {"n": 1000, "cpu_time_ms": 5.0, "status": "ok"},
        {"n": 10000, "cpu_time_ms": 50.0, "status": "ok"},
        {"n": 100000, "cpu_time_ms": None, "status": "runtime_error"},
    ]
    res_re = EmpiricalRatioAnalyzer.verify_complexity("O(n)", re_samples, time_limit_ms=2000.0)
    assert res_re["passed"] is False
    assert res_re["verdict"] == "runtime_error_at_scale"

    # 4. Two samples where one has n=1 must return insufficient_samples (no dummy alpha=1.0)
    n1_samples = [
        {"n": 1, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 100, "cpu_time_ms": 50.0, "status": "ok"},
    ]
    res_n1 = EmpiricalRatioAnalyzer.verify_complexity("O(n)", n1_samples, time_limit_ms=2000.0)
    assert res_n1["passed"] is False
    assert res_n1["verdict"] == "insufficient_samples"

    # 5. O(sqrt n) query curve claiming O(log n) must be falsified
    sqrt_q_samples = [
        {"n": 16, "queries": 4, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 64, "queries": 8, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 256, "queries": 16, "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 1024, "queries": 32, "cpu_time_ms": 1.0, "status": "ok"},
    ]
    res_sqrt_q = EmpiricalRatioAnalyzer.verify_query_complexity("O(log n)", sqrt_q_samples)
    assert res_sqrt_q["passed"] is False
    assert res_sqrt_q["verdict"] == "query_ratio_mismatch"
    assert res_sqrt_q["fitted_complexity"] == "O(sqrt n)"

    # 6. contestant_messages fallback (= max_queries + 1 due to final `! ans`) must NOT trigger over_budget
    fallback_samples = [
        {"n": 16, "queries": 5, "queries_source": "contestant_messages", "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 1024, "queries": 11, "queries_source": "contestant_messages", "cpu_time_ms": 1.0, "status": "ok"},
        {"n": 1048576, "queries": 21, "queries_source": "contestant_messages", "cpu_time_ms": 1.0, "status": "ok"},
    ]
    res_fb = EmpiricalRatioAnalyzer.verify_query_complexity("O(log n)", fallback_samples, max_queries=20)
    assert res_fb["passed"] is True

    # 7. Malicious format strings & duplicate scale_points rejected
    for bad_tpl in ("{n:10000000000}", "{n.__class__}", "{seed}\n2 {n}"):
        with pytest.raises(ValidationError):
            EmpiricalComplexityConfig(generator_args_template=bad_tpl)
        with pytest.raises(ValueError):
            MultiScaleSampler.format_generator_command("gen", 10, 1, template=bad_tpl)

    with pytest.raises(ValidationError):
        EmpiricalComplexityConfig(scale_points=[10, 10])
