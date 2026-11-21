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
            return math.pow(2.0, min(n, 1020.0))
        if normalized_expr in ("O(n!)",):
            int_n = max(1, min(int(n), 170))
            return float(math.factorial(int_n))
        return n

    @classmethod
    def calculate_expected_ratio(cls, claimed_complexity: str, n_from: int, n_to: int) -> float:
        norm_expr = normalize_complexity_expression(claimed_complexity)
        if norm_expr == "O(n!)":
            if n_from <= 0:
                n_from = 1
            if n_to <= 0:
                n_to = 1
            if n_to == n_from:
                return 1.0
            if n_to > n_from:
                ratio = 1.0
                for k in range(n_from + 1, n_to + 1):
                    ratio *= k
                    if ratio > 1e12:
                        return 1e12
                return ratio
            else:
                ratio = 1.0
                for k in range(n_to + 1, n_from + 1):
                    ratio *= k
                    if ratio > 1e12:
                        return 0.0
                return 1.0 / ratio if ratio > 0 else 0.0

        if norm_expr == "O(2^n)":
            diff = float(n_to - n_from)
            clamped_diff = min(max(diff, -100.0), 100.0)
            return math.pow(2.0, clamped_diff)

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

        # 若后段高信噪比测点（后 3 点）在有效规模跨度（>=2x）上呈现显著高阶增长，采用后段拟合结果以防前段底噪平线掩盖真实复杂度
        if len(valid_samples) >= 4:
            tail_samples = valid_samples[-3:]
            tail_n0 = int(tail_samples[0].get("n", 0))
            tail_n1 = int(tail_samples[-1].get("n", 0))
            tail_n_span = (float(tail_n1) / float(tail_n0)) if tail_n0 > 0 else 1.0
            tail_times = [float(s["cpu_time_ms"]) for s in tail_samples]
            tail_time_ratio = max(tail_times) / max(min(tail_times), 0.1)
            tail_alpha, tail_r2 = cls.fit_log_linear(tail_samples)
            if tail_alpha > expected_range[1] and (
                norm_claimed in ("O(2^n)", "O(n!)")
                or (tail_n_span >= 2.0 and tail_time_ratio >= 2.0 and tail_r2 >= 0.75)
            ):
                is_alpha_match = False
                alpha = tail_alpha
                fitted_cat = cls.infer_complexity_category(tail_alpha)
                r_squared = tail_r2
            elif tail_n_span >= 2.0:
                # 若前段呈平坦进程启动平台（如 Windows CreateProcess ~12ms 固定开销），
                # 扣除平台底噪后再检查后段净增量斜率，避免低常数 O(n^2) 被启动平台拉平掩盖。
                t0 = float(valid_samples[0]["cpu_time_ms"])
                t1 = float(valid_samples[1]["cpu_time_ms"])
                plateau = min(t0, t1)
                t_last = float(valid_samples[-1]["cpu_time_ms"])
                is_flat_head = (
                    abs(t1 - t0) <= 2.0
                    and (max(t0, t1) / max(plateau, 0.1)) <= 1.25
                    and (t_last - plateau) >= 15.0
                )
                if is_flat_head:
                    offset = max(0.0, plateau - 0.1)
                    adjusted_tail = [
                        {
                            "n": s["n"],
                            "cpu_time_ms": float(s["cpu_time_ms"]) - offset,
                        }
                        for s in valid_samples[-3:]
                        if (float(s["cpu_time_ms"]) - offset) >= 1.5
                    ]
                    if len(adjusted_tail) >= 2:
                        net_alpha, net_r2 = cls.fit_log_linear(adjusted_tail)
                        n_prev = int(adjusted_tail[-2]["n"])
                        n_last = int(adjusted_tail[-1]["n"])
                        t_prev_net = float(adjusted_tail[-2]["cpu_time_ms"])
                        t_last_net = float(adjusted_tail[-1]["cpu_time_ms"])
                        obs_net_ratio = t_last_net / max(t_prev_net, 0.01)
                        exp_last_ratio = cls.calculate_expected_ratio(
                            norm_claimed, n_prev, n_last
                        )
                        if (
                            net_alpha > expected_range[1]
                            and net_r2 >= 0.90
                            and obs_net_ratio >= max(2.0, exp_last_ratio * 1.5)
                        ):
                            is_alpha_match = False
                            alpha = net_alpha
                            fitted_cat = cls.infer_complexity_category(net_alpha)
                            r_squared = net_r2

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
        all_times = [float(s["cpu_time_ms"]) for s in valid_samples]
        peak_time = max(all_times)
        min_time = min(all_times)
        max_n = int(max_sample.get("n", 0))
        min_n = int(valid_samples[0].get("n", 0))
        n_span = (float(max_n) / float(min_n)) if min_n > 0 else 1.0
        headroom_ratio = max_time / time_limit_ms if time_limit_ms > 0 else 1.0
        peak_headroom = peak_time / time_limit_ms if time_limit_ms > 0 else 1.0

        if not is_alpha_match:
            # 当全部样本耗时均处于 OS 计时底噪区间（<=15ms，或低信噪比 <=35ms 且峰值 <5% TL），
            # 仅在不存在干净的高信噪比超阶增长信号（R^2>=0.90 且首尾缩放 >=4x 且最大点 >=2ms）时
            # 才按底噪豁免，既避免 O(1)/O(log n)/O(n log n) 计时粒度抖动误报，又不放过干净的 O(n^2)/O(n^3) 曲线。
            has_clean_high_order_signal = (
                alpha > expected_range[1]
                and r_squared >= 0.90
                and (max_time / max(min_time, 0.1)) >= 4.0
                and max_time >= 2.0
            )
            in_noise_floor = (
                peak_time <= 15.0
                or (peak_time <= 35.0 and r_squared < 0.5)
                or (
                    peak_time <= 80.0
                    and (peak_time / max(min_time, 0.1)) < 2.2
                )
            )
            warmup_decay_only = alpha < expected_range[0] and peak_headroom < 0.15
            narrow_scale_noise = (
                norm_claimed not in ("O(2^n)", "O(n!)")
                and n_span < 2.0
                and peak_headroom < 0.25
                and (peak_time / max(min_time, 0.1)) < 3.0
            )
            if (
                (in_noise_floor and peak_headroom < 0.05)
                or warmup_decay_only
                or narrow_scale_noise
            ) and not has_clean_high_order_signal:
                return {
                    "passed": True,
                    "verdict": "verified_within_noise_floor",
                    "claimed_complexity": norm_claimed,
                    "fitted_complexity": norm_claimed,
                    "fitted_alpha": round(alpha, 2),
                    "r_squared": round(r_squared, 3),
                    "samples": samples,
                    "growth_ratios": growth_ratios,
                    "max_scale_headroom_ratio": round(headroom_ratio, 3),
                    "noise_floor_note": (
                        f"All scale samples completed within OS timer noise floor "
                        f"(peak={peak_time:.2f}ms, r2={r_squared:.3f}, headroom={peak_headroom:.3f})."
                    ),
                }

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

        # 当声称为 O(n) / O(n log n) 等线性对数阶，但全部采样点均落在低信噪比 OS 计时底噪内，
        # fitted_cat 可能因常数启动底噪退化为 O(1)；回退报告 claimed_complexity 避免污染难度评估。
        if (
            norm_claimed in ("O(n)", "O(n log n)", "O(nlogn)", "O(n sqrt n)", "O(nsqrtn)")
            and alpha < 0.50
            and r_squared < 0.70
            and peak_time <= 35.0
            and peak_headroom < 0.05
        ):
            return {
                "passed": True,
                "verdict": "verified_within_noise_floor",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": norm_claimed,
                "fitted_alpha": round(alpha, 2),
                "r_squared": round(r_squared, 3),
                "samples": samples,
                "growth_ratios": growth_ratios,
                "max_scale_headroom_ratio": round(headroom_ratio, 3),
                "noise_floor_note": (
                    f"Low-SNR timer floor masked growth exponent (peak={peak_time:.2f}ms, r2={r_squared:.3f}); "
                    f"preserving claimed complexity {norm_claimed}."
                ),
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
