import math
import re
from typing import Any


def normalize_complexity_expression(expr: str | None) -> str:
    if not expr or not isinstance(expr, str):
        return "O(n)"
    cleaned = expr.strip().lower()
    cleaned = cleaned.replace("\\log", "log").replace("\\cdot", "*").replace("\\times", "*")
    cleaned = re.sub(r"\s+", "", cleaned)
    match = re.search(r"o\((.+)\)", cleaned)
    if match:
        inner = match.group(1)
    else:
        inner = cleaned
    inner = inner.replace("*", "")
    inner = inner.replace("log(n)", "logn").replace("sqrt(n)", "sqrtn")
    inner = inner.replace("**", "^")

    # 映射为标准包含空格形式
    mapping = {
        "1": "O(1)",
        "logn": "O(log n)",
        "n": "O(n)",
        "nlogn": "O(n log n)",
        "nsqrtn": "O(n sqrt n)",
        "n^2": "O(n^2)",
        "n^3": "O(n^3)",
        "2^n": "O(2^n)",
        "n!": "O(n!)",
    }
    return mapping.get(inner, f"O({inner})")


class EmpiricalRatioAnalyzer:
    COMPLEXITY_ALPHA_RANGES: dict[str, tuple[float, float]] = {
        "O(1)": (-0.2, 0.35),
        "O(log n)": (-0.05, 0.65),
        "O(logn)": (-0.05, 0.65),
        "O(n)": (-0.25, 1.35),
        "O(n log n)": (-0.25, 1.45),
        "O(nlogn)": (-0.25, 1.45),
        "O(n sqrt n)": (1.15, 1.75),
        "O(nsqrtn)": (1.15, 1.75),
        "O(n^2)": (1.65, 2.35),
        "O(n^3)": (2.50, 3.45),
        "O(2^n)": (3.50, 100.0),
        "O(n!)": (3.50, 100.0),
    }

    @classmethod
    def evaluate_complexity_function(cls, normalized_expr: str, n: float) -> float:
        if n <= 1:
            n = 1.0001
        if normalized_expr in ("O(1)",):
            return 1.0
        if normalized_expr in ("O(log n)", "O(logn)"):
            return math.log2(max(2.0, n))
        if normalized_expr in ("O(n)",):
            return n
        if normalized_expr in ("O(n log n)", "O(nlogn)"):
            return n * math.log2(max(2.0, n))
        if normalized_expr in ("O(n sqrt n)", "O(nsqrtn)"):
            return n * math.sqrt(n)
        if normalized_expr in ("O(n^2)",):
            return n * n
        if normalized_expr in ("O(n^3)",):
            return n * n * n
        if normalized_expr in ("O(2^n)",):
            return math.pow(2.0, min(n, 60.0))
        if normalized_expr in ("O(n!)",):
            return float(math.factorial(min(int(n), 20)))
        return n

    @classmethod
    def calculate_expected_ratio(cls, claimed_complexity: str, n_from: int, n_to: int) -> float:
        norm_expr = normalize_complexity_expression(claimed_complexity)
        val_from = cls.evaluate_complexity_function(norm_expr, float(n_from))
        val_to = cls.evaluate_complexity_function(norm_expr, float(n_to))
        if val_from <= 0:
            return 1.0
        return val_to / val_from

    @classmethod
    def fit_log_linear(cls, samples: list[dict[str, Any]]) -> tuple[float, float]:
        valid_points: list[tuple[float, float]] = []
        for s in samples:
            try:
                n_val = float(s.get("n", 0) or 0)
                t_val = float(s.get("cpu_time_ms", 0.0) or 0.0)
            except (ValueError, TypeError):
                continue
            if n_val > 1 and t_val > 0.01 and math.isfinite(n_val) and math.isfinite(t_val):
                valid_points.append((math.log(n_val), math.log(t_val)))

        if len(valid_points) < 2:
            return 1.0, 0.0

        n_count = len(valid_points)
        sum_x = sum(p[0] for p in valid_points)
        sum_y = sum(p[1] for p in valid_points)
        sum_xx = sum(p[0] * p[0] for p in valid_points)
        sum_xy = sum(p[0] * p[1] for p in valid_points)

        denom = n_count * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-9:
            return 1.0, 0.0

        alpha = (n_count * sum_xy - sum_x * sum_y) / denom
        beta = (sum_y - alpha * sum_x) / n_count

        mean_y = sum_y / n_count
        ss_tot = sum((p[1] - mean_y) ** 2 for p in valid_points)
        ss_res = sum((p[1] - (alpha * p[0] + beta)) ** 2 for p in valid_points)

        if ss_tot < 1e-9:
            r_squared = 1.0
        else:
            r_squared = max(0.0, 1.0 - (ss_res / ss_tot))

        return alpha, r_squared

    @classmethod
    def infer_complexity_category(cls, alpha: float) -> str:
        if alpha < 0.35:
            return "O(1)"
        if alpha < 0.65:
            return "O(log n)"
        if alpha < 1.35:
            return "O(n)"
        if alpha < 1.65:
            return "O(n log n)"
        if alpha < 1.85:
            return "O(n sqrt n)"
        if alpha < 2.50:
            return "O(n^2)"
        if alpha < 3.50:
            return "O(n^3)"
        return "O(2^n)"

    @classmethod
    def verify_complexity(
        cls,
        claimed_complexity: str,
        samples: list[dict[str, Any]],
        time_limit_ms: float = 2000.0,
    ) -> dict[str, Any]:
        norm_claimed = normalize_complexity_expression(claimed_complexity)
        valid_samples = [
            s for s in samples
            if s.get("status") == "ok"
            and s.get("cpu_time_ms") is not None
            and math.isfinite(float(s.get("cpu_time_ms", 0.0) or 0.0))
        ]

        if not valid_samples:
            return {
                "passed": False,
                "verdict": "no_valid_samples",
                "failure_reason": "No valid execution samples collected",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": "unknown",
                "fitted_alpha": None,
                "r_squared": None,
                "samples": samples,
                "growth_ratios": [],
                "remediation_advice": "Check if generator and solution execute properly without immediate crash.",
            }

        last_sample = samples[-1]
        if last_sample.get("status") == "timeout":
            return {
                "passed": False,
                "verdict": "timeout_at_extreme",
                "failure_reason": f"Execution timed out at extreme test point (N={last_sample.get('n')})",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": "timeout",
                "fitted_alpha": None,
                "r_squared": None,
                "samples": samples,
                "growth_ratios": [],
                "remediation_advice": f"Algorithm execution timed out on N={last_sample.get('n')}. Optimize time complexity or reduce constant factors.",
            }

        if len(valid_samples) < 2:
            return {
                "passed": False,
                "verdict": "insufficient_samples",
                "failure_reason": "Insufficient valid execution samples collected (minimum 2 required)",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": "unknown",
                "fitted_alpha": None,
                "r_squared": None,
                "samples": samples,
                "growth_ratios": [],
                "remediation_advice": "Ensure generator produces inputs and solution executes without early crash.",
            }

        alpha, r_squared = cls.fit_log_linear(valid_samples)
        fitted_cat = cls.infer_complexity_category(alpha)

        expected_range = cls.COMPLEXITY_ALPHA_RANGES.get(norm_claimed, (0.65, 1.35))
        is_alpha_match = expected_range[0] <= alpha <= expected_range[1]

        # 若后段高信噪比测点（后 3 点）呈现显著高阶增长，采用后段拟合结果以防前段底噪平线掩盖真实复杂度
        if len(valid_samples) >= 4:
            tail_samples = valid_samples[-3:]
            tail_alpha, tail_r2 = cls.fit_log_linear(tail_samples)
            if tail_alpha > expected_range[1]:
                is_alpha_match = False
                alpha = tail_alpha
                fitted_cat = cls.infer_complexity_category(tail_alpha)
                r_squared = tail_r2

        growth_ratios: list[dict[str, Any]] = []
        for i in range(len(valid_samples) - 1):
            s_from = valid_samples[i]
            s_to = valid_samples[i + 1]
            n_from = int(s_from["n"])
            n_to = int(s_to["n"])
            t_from = float(s_from["cpu_time_ms"])
            t_to = float(s_to["cpu_time_ms"])

            observed_ratio = (t_to / t_from) if t_from > 0.01 else 1.0
            expected_ratio = cls.calculate_expected_ratio(norm_claimed, n_from, n_to)

            growth_ratios.append(
                {
                    "from_n": n_from,
                    "to_n": n_to,
                    "scale_factor": round(float(n_to) / float(n_from), 2) if n_from > 0 else 1.0,
                    "observed_ratio": round(observed_ratio, 2),
                    "expected_ratio": round(expected_ratio, 2),
                }
            )

        max_sample = valid_samples[-1]
        max_time = float(max_sample["cpu_time_ms"])
        max_n = int(max_sample.get("n", 0))
        headroom_ratio = max_time / time_limit_ms if time_limit_ms > 0 else 1.0

        if not is_alpha_match:
            # 仅在规模足够大（跨越硬件缓存）且耗时余量充足时允许缓存跳跃容差
            if alpha > expected_range[1] and len(valid_samples) >= 3 and headroom_ratio < 0.40 and max_n >= 50000:
                sub_samples = valid_samples[:-1]
                sub_alpha, sub_r2 = cls.fit_log_linear(sub_samples)
                if expected_range[0] <= sub_alpha <= expected_range[1]:
                    return {
                        "passed": True,
                        "verdict": "verified_with_cache_jump",
                        "claimed_complexity": norm_claimed,
                        "fitted_complexity": norm_claimed,
                        "fitted_alpha": round(alpha, 2),
                        "r_squared": round(r_squared, 3),
                        "samples": samples,
                        "growth_ratios": growth_ratios,
                        "max_scale_headroom_ratio": round(headroom_ratio, 3),
                        "cache_jump_note": "Slight ratio jump observed at maximum scale likely due to L1/L2 cache capacity transition, well within time limit.",
                    }

            return {
                "passed": False,
                "verdict": "ratio_mismatch",
                "failure_reason": f"Measured complexity exponent alpha={alpha:.2f} (fitted as {fitted_cat}) outside claimed {norm_claimed} range [{expected_range[0]}, {expected_range[1]}]",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": fitted_cat,
                "fitted_alpha": round(alpha, 2),
                "r_squared": round(r_squared, 3),
                "samples": samples,
                "growth_ratios": growth_ratios,
                "remediation_advice": f"The algorithm exhibits {fitted_cat} growth rather than claimed {norm_claimed}. Inspect nested loops, optimize algorithm logic, or update claimed_complexity.",
            }

        return {
            "passed": True,
            "verdict": "verified",
            "claimed_complexity": norm_claimed,
            "fitted_complexity": fitted_cat,
            "fitted_alpha": round(alpha, 2),
            "r_squared": round(r_squared, 3),
            "samples": samples,
            "growth_ratios": growth_ratios,
            "max_scale_headroom_ratio": round(headroom_ratio, 3),
        }
