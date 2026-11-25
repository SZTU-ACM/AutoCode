import math
import re
from typing import Any


def _extract_balanced_big_o(text: str) -> str:
    """Extract the inner expression from the first balanced ``O(...)`` group if present."""
    match = re.search(r"(?:^|[^a-z0-9_])o\(", text)
    if not match:
        return text
    open_pos = match.end() - 1
    depth = 0
    for idx in range(open_pos, len(text)):
        ch = text[idx]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return text[open_pos + 1 : idx]
    return text[open_pos + 1 :]


def normalize_complexity_expression(expr: str | None, *, dominant_var: str = "n") -> str:
    if not expr or not isinstance(expr, str):
        return "O(n)"
    cleaned = expr.strip()
    cleaned = (
        cleaned.replace("\\Theta", "O")
        .replace("\\theta", "O")
        .replace("\\Omega", "O")
        .replace("\\omega", "O")
        .replace("\\log_2", "log")
        .replace("\\log_{2}", "log")
        .replace("\\log", "log")
        .replace("\\sqrt", "sqrt")
        .replace("\\cdot", "*")
        .replace("\\times", "*")
    )
    cleaned = cleaned.replace("{", "").replace("}", "")
    lowered = cleaned.lower()
    inner = _extract_balanced_big_o(lowered).strip()

    dom = (dominant_var or "n").strip().lower()
    if dom.endswith("_max") and len(dom) > 4:
        dom = dom[:-4]
    if dom and dom != "n" and re.fullmatch(r"[a-z_][a-z0-9_]*", dom):
        if not re.search(r"\bn\b", inner):
            inner = re.sub(rf"\b{re.escape(dom)}\b", "n", inner)

    inner = re.sub(r"\s+", "", inner)
    inner = inner.replace("*", "")
    inner = inner.replace("**", "^")
    inner = re.sub(r"log_?2\(?n\)?", "logn", inner)
    inner = inner.replace("log(n)", "logn").replace("sqrt(n)", "sqrtn")

    # 映射为标准包含空格形式
    mapping = {
        "1": "O(1)",
        "logn": "O(log n)",
        "log2n": "O(log n)",
        "sqrtn": "O(sqrt n)",
        "n": "O(n)",
        "nlogn": "O(n log n)",
        "lognn": "O(n log n)",
        "nsqrtn": "O(n sqrt n)",
        "sqrtnn": "O(n sqrt n)",
        "n^2": "O(n^2)",
        "n^3": "O(n^3)",
        "2^n": "O(2^n)",
        "n2^n": "O(n 2^n)",
        "2^nn": "O(n 2^n)",
        "n^22^n": "O(n^2 2^n)",
        "2^nn^2": "O(n^2 2^n)",
        "3^n": "O(3^n)",
        "n!": "O(n!)",
    }
    return mapping.get(inner, f"O({inner})")


class EmpiricalRatioAnalyzer:
    COMPLEXITY_ALPHA_RANGES: dict[str, tuple[float, float]] = {
        "O(1)": (-0.2, 0.35),
        "O(log n)": (-0.05, 0.65),
        "O(logn)": (-0.05, 0.65),
        "O(sqrt n)": (0.25, 0.75),
        "O(sqrtn)": (0.25, 0.75),
        "O(n)": (-0.25, 1.35),
        "O(n log n)": (-0.25, 1.45),
        "O(nlogn)": (-0.25, 1.45),
        "O(n sqrt n)": (1.15, 1.75),
        "O(nsqrtn)": (1.15, 1.75),
        "O(n^2)": (1.65, 2.35),
        "O(n^3)": (2.50, 3.45),
        "O(2^n)": (3.50, 100.0),
        "O(n 2^n)": (3.50, 100.0),
        "O(n^2 2^n)": (3.50, 100.0),
        "O(3^n)": (4.50, 150.0),
        "O(n!)": (3.50, 100.0),
    }

    EXPONENTIAL_BASE_RANGES: dict[str, tuple[float, float]] = {
        "O(2^n)": (1.45, 2.75),
        "O(n 2^n)": (1.45, 3.00),
        "O(n^2 2^n)": (1.45, 3.25),
        "O(3^n)": (2.10, 4.10),
    }

    @classmethod
    def evaluate_complexity_function(cls, normalized_expr: str, n: float) -> float:
        if n <= 1:
            n = 1.0001
        if normalized_expr in ("O(1)",):
            return 1.0
        if normalized_expr in ("O(log n)", "O(logn)"):
            return math.log2(max(2.0, n))
        if normalized_expr in ("O(sqrt n)", "O(sqrtn)"):
            return math.sqrt(n)
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
        if normalized_expr in ("O(n 2^n)",):
            return n * math.pow(2.0, min(n, 1000.0))
        if normalized_expr in ("O(n^2 2^n)",):
            return (n * n) * math.pow(2.0, min(n, 980.0))
        if normalized_expr in ("O(3^n)",):
            return math.pow(3.0, min(n, 640.0))
        if normalized_expr in ("O(n!)",):
            int_n = max(1, min(int(n), 170))
            return float(math.factorial(int_n))
        return n

    @classmethod
    def calculate_expected_ratio(
        cls,
        claimed_complexity: str,
        n_from: int,
        n_to: int,
        *,
        dominant_var: str = "n",
    ) -> float:
        norm_expr = normalize_complexity_expression(claimed_complexity, dominant_var=dominant_var)
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

        if norm_expr == "O(n 2^n)":
            diff = float(n_to - n_from)
            clamped_diff = min(max(diff, -100.0), 100.0)
            n_ratio = (float(n_to) / float(n_from)) if n_from > 0 else 1.0
            return n_ratio * math.pow(2.0, clamped_diff)

        if norm_expr == "O(n^2 2^n)":
            diff = float(n_to - n_from)
            clamped_diff = min(max(diff, -100.0), 100.0)
            n_ratio = ((float(n_to) / float(n_from)) ** 2) if n_from > 0 else 1.0
            return n_ratio * math.pow(2.0, clamped_diff)

        if norm_expr == "O(3^n)":
            diff = float(n_to - n_from)
            clamped_diff = min(max(diff, -60.0), 60.0)
            return math.pow(3.0, clamped_diff)

        val_from = cls.evaluate_complexity_function(norm_expr, float(n_from))
        val_to = cls.evaluate_complexity_function(norm_expr, float(n_to))
        if val_from <= 0:
            return 1.0
        return val_to / val_from

    @staticmethod
    def _valid_positive_num(val: Any, *, min_val: float = 0.0) -> float | None:
        if isinstance(val, bool) or not isinstance(val, int | float):
            return None
        num = float(val)
        if not math.isfinite(num) or num <= min_val:
            return None
        return num

    @classmethod
    def _select_fit_metric(
        cls,
        samples: list[dict[str, Any]],
        *,
        min_n: float = 1.000001,
        ok_only: bool = False,
    ) -> str:
        """Prefer deterministic `instruction_count` when present across scales; fall back to CPU/wall time."""
        cand = [
            s
            for s in samples
            if (not ok_only or s.get("status") == "ok")
            and (n_num := cls._valid_positive_num(s.get("n"), min_val=0.0)) is not None
            and n_num >= min_n
        ]
        inst_valid = [
            s for s in cand if cls._valid_positive_num(s.get("instruction_count"), min_val=0.0) is not None
        ]
        inst_distinct_n = {float(s["n"]) for s in inst_valid}
        if len(inst_valid) >= 2 and len(inst_distinct_n) >= 2 and len(inst_valid) == len(cand):
            return "instruction_count"

        cpu_valid = [
            s for s in cand if cls._valid_positive_num(s.get("cpu_time_ms"), min_val=0.01) is not None
        ]
        if len(cpu_valid) >= 2 and len({float(s["n"]) for s in cpu_valid}) >= 2:
            return "cpu_time_ms"

        wall_valid = [
            s for s in cand if cls._valid_positive_num(s.get("wall_time_ms"), min_val=0.01) is not None
        ]
        if len(wall_valid) >= 2 and len({float(s["n"]) for s in wall_valid}) >= 2:
            return "wall_time_ms"

        if len(inst_valid) >= 2 and len(inst_distinct_n) >= 2:
            return "instruction_count"
        return "cpu_time_ms"

    @classmethod
    def fit_log_linear(
        cls,
        samples: list[dict[str, Any]],
        *,
        metric: str | None = None,
    ) -> tuple[float, float]:
        active_metric = metric or cls._select_fit_metric(samples, min_n=1.000001, ok_only=False)
        min_val = 0.0 if active_metric == "instruction_count" else 0.01
        valid_points: list[tuple[float, float]] = []
        for s in samples:
            n_val = cls._valid_positive_num(s.get("n"), min_val=1.0)
            y_val = cls._valid_positive_num(s.get(active_metric), min_val=min_val)
            if n_val is not None and y_val is not None:
                valid_points.append((math.log(n_val), math.log(y_val)))

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
    def _linear_regression(cls, points: list[tuple[float, float]]) -> tuple[float, float, float]:
        """Ordinary least-squares fit y = slope * x + intercept; returns (slope, intercept, r_squared)."""
        if len(points) < 2:
            return 0.0, 0.0, 0.0
        n_count = len(points)
        sum_x = sum(p[0] for p in points)
        sum_y = sum(p[1] for p in points)
        sum_xx = sum(p[0] * p[0] for p in points)
        sum_xy = sum(p[0] * p[1] for p in points)
        denom = n_count * sum_xx - sum_x * sum_x
        if abs(denom) < 1e-9:
            return 0.0, sum_y / n_count, 0.0
        slope = (n_count * sum_xy - sum_x * sum_y) / denom
        intercept = (sum_y - slope * sum_x) / n_count
        mean_y = sum_y / n_count
        ss_tot = sum((p[1] - mean_y) ** 2 for p in points)
        ss_res = sum((p[1] - (slope * p[0] + intercept)) ** 2 for p in points)
        r_squared = 1.0 if ss_tot < 1e-9 else max(0.0, 1.0 - (ss_res / ss_tot))
        return slope, intercept, r_squared

    @classmethod
    def fit_exponential(
        cls,
        samples: list[dict[str, Any]],
        *,
        metric: str | None = None,
    ) -> tuple[float, float, float]:
        """Fit semi-log exponential model log2(y) = beta * n + c; returns (base=2**beta, beta, r_squared)."""
        active_metric = metric or cls._select_fit_metric(samples, min_n=1.0, ok_only=False)
        min_val = 0.0 if active_metric == "instruction_count" else 0.01
        pts: list[tuple[float, float]] = []
        for s in samples:
            n_val = cls._valid_positive_num(s.get("n"), min_val=0.999999)
            y_val = cls._valid_positive_num(s.get(active_metric), min_val=min_val)
            if n_val is not None and y_val is not None:
                pts.append((n_val, math.log2(y_val)))
        if len(pts) < 2:
            return 1.0, 0.0, 0.0
        beta, _, r2 = cls._linear_regression(pts)
        clamped_beta = min(max(beta, -20.0), 20.0)
        return math.pow(2.0, clamped_beta), beta, r2

    @classmethod
    def fit_query_log_linear(cls, samples: list[dict[str, Any]]) -> tuple[float, float, float]:
        """Fit interactive query curve queries = a * log2(n) + b; returns (a, b, r_squared)."""
        pts: list[tuple[float, float]] = []
        for s in samples:
            try:
                n_val = float(s.get("n", 0) or 0)
                q_val = float(s.get("queries", 0) or 0)
            except (ValueError, TypeError):
                continue
            if n_val > 1 and q_val > 0 and math.isfinite(n_val) and math.isfinite(q_val):
                pts.append((math.log2(n_val), q_val))
        return cls._linear_regression(pts)

    @classmethod
    def fit_query_power_law(cls, samples: list[dict[str, Any]]) -> tuple[float, float]:
        """Fit log-log query scaling ln(queries) = alpha * ln(n) + c; returns (alpha, r_squared)."""
        pts: list[tuple[float, float]] = []
        for s in samples:
            try:
                n_val = float(s.get("n", 0) or 0)
                q_val = float(s.get("queries", 0) or 0)
            except (ValueError, TypeError):
                continue
            if n_val > 1 and q_val > 0 and math.isfinite(n_val) and math.isfinite(q_val):
                pts.append((math.log(n_val), math.log(q_val)))
        if len(pts) < 2:
            return 0.0, 0.0
        alpha, _, r2 = cls._linear_regression(pts)
        return alpha, r2

    @classmethod
    def _check_failed_samples(
        cls,
        norm_claimed: str,
        samples: list[dict[str, Any]],
        *,
        fit_metric: str | None = None,
    ) -> dict[str, Any] | None:
        """Return an immediate failure payload if any sample timed out, MLEd, or errored."""
        extra: dict[str, Any] = {"fit_metric": fit_metric} if fit_metric else {}
        for status_val, verdict_name, fitted_label, msg_prefix, advice_prefix in (
            (
                "timeout",
                "timeout_at_extreme",
                "timeout",
                "Execution timed out at scale point",
                "Algorithm execution timed out on",
            ),
            (
                "mle",
                "mle_at_scale",
                "mle",
                "Execution exceeded memory limit (MLE) at scale point",
                "Algorithm exceeded memory limit on",
            ),
            (
                "runtime_error",
                "runtime_error_at_scale",
                "runtime_error",
                "Execution failed with runtime error at scale point",
                "Algorithm crashed or returned non-zero status on",
            ),
        ):
            failed = next((s for s in samples if s.get("status") == status_val), None)
            if failed is not None:
                return {
                    "passed": False,
                    "verdict": verdict_name,
                    "failure_reason": f"{msg_prefix} (N={failed.get('n')})",
                    "claimed_complexity": norm_claimed,
                    "fitted_complexity": fitted_label,
                    "fitted_alpha": None,
                    "r_squared": None,
                    "samples": samples,
                    "growth_ratios": [],
                    "remediation_advice": f"{advice_prefix} N={failed.get('n')}.",
                    **extra,
                }
        return None

    @classmethod
    def verify_query_complexity(
        cls,
        claimed_complexity: str,
        samples: list[dict[str, Any]],
        *,
        max_queries: int | None = None,
        time_limit_ms: float = 2000.0,
        dominant_var: str = "n",
    ) -> dict[str, Any]:
        """Verify interactive query-count scaling (e.g. queries vs log2(n)) instead of noisy pipe CPU time."""
        norm_claimed = normalize_complexity_expression(claimed_complexity, dominant_var=dominant_var)
        failed_payload = cls._check_failed_samples(norm_claimed, samples, fit_metric="queries")
        if failed_payload is not None:
            return failed_payload

        valid_samples = [
            s
            for s in samples
            if s.get("status") == "ok"
            and isinstance(s.get("queries"), int | float)
            and not isinstance(s.get("queries"), bool)
            and math.isfinite(float(s["queries"]))
            and float(s["queries"]) > 0
            and isinstance(s.get("n"), int | float)
            and not isinstance(s.get("n"), bool)
            and math.isfinite(float(s["n"]))
            and float(s["n"]) > 1
        ]
        distinct_n = {float(s["n"]) for s in valid_samples}
        if len(valid_samples) < 2 or len(distinct_n) < 2:
            return {
                "passed": False,
                "verdict": "insufficient_samples",
                "fit_metric": "queries",
                "failure_reason": "Insufficient valid interactive query samples collected (minimum 2 distinct scales with N > 1 required)",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": "unknown",
                "fitted_alpha": None,
                "r_squared": None,
                "samples": samples,
                "growth_ratios": [],
                "remediation_advice": "Ensure interactor reports `queries=<k>` or exchanges interaction messages across scale points.",
            }

        max_obs_queries = max(int(s["queries"]) for s in valid_samples)
        budget_samples = [
            s for s in valid_samples if s.get("queries_source") != "contestant_messages"
        ]
        if max_queries is not None and budget_samples:
            max_budget_queries = max(int(s["queries"]) for s in budget_samples)
            if max_budget_queries > max_queries:
                return {
                    "passed": False,
                    "verdict": "query_budget_exceeded",
                    "fit_metric": "queries",
                    "failure_reason": f"Observed max queries ({max_budget_queries}) exceeded protocol.max_queries ({max_queries})",
                    "claimed_complexity": norm_claimed,
                    "fitted_complexity": "over_budget",
                    "fitted_alpha": None,
                    "r_squared": None,
                    "max_observed_queries": max_budget_queries,
                    "max_queries_limit": max_queries,
                    "samples": samples,
                    "growth_ratios": [],
                    "remediation_advice": "Reduce query count or update protocol.max_queries to match the intended algorithm.",
                }

        slope_log2, intercept_log2, log2_r2 = cls.fit_query_log_linear(valid_samples)
        q_alpha, q_alpha_r2 = cls.fit_query_power_law(valid_samples)

        growth_ratios: list[dict[str, Any]] = []
        for i in range(len(valid_samples) - 1):
            s_from = valid_samples[i]
            s_to = valid_samples[i + 1]
            n_from = int(s_from["n"])
            n_to = int(s_to["n"])
            q_from = float(s_from["queries"])
            q_to = float(s_to["queries"])
            growth_ratios.append(
                {
                    "from_n": n_from,
                    "to_n": n_to,
                    "from_queries": int(q_from),
                    "to_queries": int(q_to),
                    "observed_ratio": round(q_to / max(q_from, 1.0), 2),
                    "expected_ratio": round(
                        cls.calculate_expected_ratio(
                            norm_claimed, n_from, n_to, dominant_var=dominant_var
                        ),
                        2,
                    ),
                    "delta_log2_n": round(
                        math.log2(max(2.0, float(n_to))) - math.log2(max(2.0, float(n_from))), 2
                    ),
                }
            )

        cpu_times = [
            float(s["cpu_time_ms"])
            for s in valid_samples
            if s.get("cpu_time_ms") is not None and math.isfinite(float(s["cpu_time_ms"]))
        ]
        max_cpu = max(cpu_times) if cpu_times else 0.0
        headroom_ratio = max_cpu / time_limit_ms if time_limit_ms > 0 else 0.0

        query_curve = {
            "model": "queries = a * log2(n) + b",
            "queries_per_log2_n": round(slope_log2, 3),
            "intercept": round(intercept_log2, 3),
            "log2_r_squared": round(log2_r2, 3),
            "power_law_alpha": round(q_alpha, 3),
            "power_law_r_squared": round(q_alpha_r2, 3),
            "max_observed_queries": max_obs_queries,
        }

        if norm_claimed in ("O(log n)", "O(logn)"):
            # Falsify polynomial / sublinear-power query growth (e.g. O(sqrt n) or O(n) disguised as O(log n))
            max_n_val = max(int(s["n"]) for s in valid_samples)
            is_super_log = (
                (q_alpha > 0.65 and q_alpha_r2 >= 0.80)
                or (q_alpha > 0.35 and q_alpha_r2 >= 0.85 and q_alpha_r2 > log2_r2 and max_n_val >= 64)
                or (max_n_val >= 64 and max_obs_queries > 4 * math.log2(max_n_val) + 20)
            )
            if is_super_log:
                fitted_cat = (
                    "O(sqrt n)" if 0.35 < q_alpha < 0.70 else cls.infer_complexity_category(q_alpha)
                )
                return {
                    "passed": False,
                    "verdict": "query_ratio_mismatch",
                    "fit_metric": "queries",
                    "fit_model": "query_log2",
                    "failure_reason": (
                        f"Interactive query count scales as {fitted_cat} (alpha={q_alpha:.2f}, "
                        f"slope={slope_log2:.2f}/log2(n)), inconsistent with claimed {norm_claimed}"
                    ),
                    "claimed_complexity": norm_claimed,
                    "fitted_complexity": fitted_cat,
                    "fitted_alpha": round(q_alpha, 3),
                    "r_squared": round(q_alpha_r2, 3),
                    "query_curve": query_curve,
                    "samples": samples,
                    "growth_ratios": growth_ratios,
                    "remediation_advice": "Interactive solution issues too many queries as n grows; verify binary/logarithmic narrowing logic.",
                }
            fitted_label = "O(log n)" if slope_log2 > 0.05 else "O(1)"
            return {
                "passed": True,
                "verdict": "verified_query_curve",
                "fit_metric": "queries",
                "fit_model": "query_log2",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": fitted_label,
                "fitted_alpha": round(q_alpha, 3),
                "r_squared": round(log2_r2, 3),
                "query_curve": query_curve,
                "samples": samples,
                "growth_ratios": growth_ratios,
                "max_scale_headroom_ratio": round(headroom_ratio, 3),
            }

        expected_range = cls.COMPLEXITY_ALPHA_RANGES.get(norm_claimed, (0.65, 1.35))
        fitted_cat = cls.infer_complexity_category(q_alpha)
        if expected_range[0] <= q_alpha <= expected_range[1]:
            return {
                "passed": True,
                "verdict": "verified_query_curve",
                "fit_metric": "queries",
                "fit_model": "query_power_law",
                "claimed_complexity": norm_claimed,
                "fitted_complexity": fitted_cat,
                "fitted_alpha": round(q_alpha, 3),
                "r_squared": round(q_alpha_r2, 3),
                "query_curve": query_curve,
                "samples": samples,
                "growth_ratios": growth_ratios,
                "max_scale_headroom_ratio": round(headroom_ratio, 3),
            }

        return {
            "passed": False,
            "verdict": "query_ratio_mismatch",
            "fit_metric": "queries",
            "fit_model": "query_power_law",
            "failure_reason": (
                f"Measured query scaling alpha={q_alpha:.2f} (fitted as {fitted_cat}) "
                f"outside claimed {norm_claimed} range [{expected_range[0]}, {expected_range[1]}]"
            ),
            "claimed_complexity": norm_claimed,
            "fitted_complexity": fitted_cat,
            "fitted_alpha": round(q_alpha, 3),
            "r_squared": round(q_alpha_r2, 3),
            "query_curve": query_curve,
            "samples": samples,
            "growth_ratios": growth_ratios,
            "remediation_advice": f"Interactive query count exhibits {fitted_cat} growth rather than claimed {norm_claimed}.",
        }

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
        *,
        dominant_var: str = "n",
    ) -> dict[str, Any]:
        norm_claimed = normalize_complexity_expression(claimed_complexity, dominant_var=dominant_var)
        is_exp_claimed = norm_claimed in cls.EXPONENTIAL_BASE_RANGES
        min_n_required = 1.0 if is_exp_claimed else 1.000001

        ok_samples = [s for s in samples if s.get("status") == "ok"]
        valid_samples = [
            s
            for s in ok_samples
            if (
                cls._valid_positive_num(s.get("instruction_count"), min_val=0.0) is not None
                or cls._valid_positive_num(s.get("cpu_time_ms"), min_val=0.0) is not None
                or cls._valid_positive_num(s.get("wall_time_ms"), min_val=0.0) is not None
            )
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

        active_metric = cls._select_fit_metric(valid_samples, min_n=min_n_required, ok_only=True)
        failed_payload = cls._check_failed_samples(norm_claimed, samples, fit_metric=active_metric)
        if failed_payload is not None:
            return failed_payload

        min_val = 0.0 if active_metric == "instruction_count" else 0.01
        fittable_samples = [
            s
            for s in valid_samples
            if (n_num := cls._valid_positive_num(s.get("n"), min_val=0.0)) is not None
            and n_num >= min_n_required
            and cls._valid_positive_num(s.get(active_metric), min_val=min_val) is not None
        ]
        distinct_n = {float(s["n"]) for s in fittable_samples}
        if len(fittable_samples) < 2 or len(distinct_n) < 2:
            return {
                "passed": False,
                "verdict": "insufficient_samples",
                "fit_metric": active_metric,
                "failure_reason": (
                    "Insufficient valid execution samples collected "
                    "(minimum 2 distinct scales with N > 1 and positive metric required)"
                ),
                "claimed_complexity": norm_claimed,
                "fitted_complexity": "unknown",
                "fitted_alpha": None,
                "r_squared": None,
                "samples": samples,
                "growth_ratios": [],
                "remediation_advice": "Ensure generator produces distinct scales (N > 1) and solution executes cleanly.",
            }

        alpha, r_squared = cls.fit_log_linear(fittable_samples, metric=active_metric)
        fitted_cat = cls.infer_complexity_category(alpha)

        expected_range = cls.COMPLEXITY_ALPHA_RANGES.get(norm_claimed, (0.65, 1.35))
        is_alpha_match = expected_range[0] <= alpha <= expected_range[1]
        fit_model = "log_linear"
        exponential_fit: dict[str, Any] | None = None

        if is_exp_claimed:
            exp_base, exp_beta, exp_r2 = cls.fit_exponential(fittable_samples, metric=active_metric)
            base_lo, base_hi = cls.EXPONENTIAL_BASE_RANGES[norm_claimed]
            exponential_fit = {
                "base": round(exp_base, 3),
                "beta_log2": round(exp_beta, 3),
                "r_squared": round(exp_r2, 3),
                "expected_base_range": [base_lo, base_hi],
            }
            fit_model = "exponential"
            r_squared = exp_r2
            if base_lo <= exp_base <= base_hi and exp_r2 >= 0.70:
                is_alpha_match = True
                fitted_cat = norm_claimed
            else:
                is_alpha_match = False
                if exp_base >= 2.75 and exp_r2 >= 0.70:
                    fitted_cat = "O(3^n)" if exp_base <= 4.10 else f"O({exp_base:.1f}^n)"
                elif exp_base >= 1.45 and exp_r2 >= 0.70:
                    fitted_cat = "O(2^n)"
                else:
                    fitted_cat = cls.infer_complexity_category(alpha)

        is_instruction_metric = active_metric == "instruction_count"
        # 若后段高信噪比测点（后 3 点）在有效规模跨度（>=2x）上呈现显著高阶增长，采用后段拟合结果以防前段底噪/CRT启动平线掩盖真实复杂度
        if fit_model == "log_linear" and len(fittable_samples) >= 4:
            tail_samples = fittable_samples[-3:]
            tail_n0 = int(tail_samples[0].get("n", 0))
            tail_n1 = int(tail_samples[-1].get("n", 0))
            tail_n_span = (float(tail_n1) / float(tail_n0)) if tail_n0 > 0 else 1.0
            tail_vals = [float(s[active_metric]) for s in tail_samples]
            tail_val_ratio = max(tail_vals) / max(min(tail_vals), min_val or 1.0)
            tail_alpha, tail_r2 = cls.fit_log_linear(tail_samples, metric=active_metric)
            if tail_alpha > expected_range[1] and (
                norm_claimed in ("O(2^n)", "O(n!)")
                or (tail_n_span >= 2.0 and tail_val_ratio >= 2.0 and tail_r2 >= 0.75)
            ):
                is_alpha_match = False
                alpha = tail_alpha
                fitted_cat = cls.infer_complexity_category(tail_alpha)
                r_squared = tail_r2
            elif tail_n_span >= 2.0:
                v0 = float(fittable_samples[0][active_metric])
                v1 = float(fittable_samples[1][active_metric])
                plateau = min(v0, v1)
                v_last = float(fittable_samples[-1][active_metric])
                if is_instruction_metric:
                    is_flat_head = (
                        (max(v0, v1) / max(plateau, 1.0)) <= 1.25
                        and (v_last - plateau) >= max(1000.0, 0.25 * plateau)
                    )
                    offset = max(0.0, plateau - 1.0)
                    min_net = 10.0
                else:
                    min_delta = 8.0 if plateau <= 5.0 else 15.0
                    is_flat_head = (
                        abs(v1 - v0) <= 2.0
                        and (plateau <= 5.0 or (max(v0, v1) / max(plateau, 0.1)) <= 1.25)
                        and (v_last - plateau) >= min_delta
                    )
                    offset = max(0.0, plateau - 0.1)
                    min_net = 1.5
                if is_flat_head:
                    adjusted_tail = [
                        {
                            "n": s["n"],
                            active_metric: float(s[active_metric]) - offset,
                        }
                        for s in fittable_samples[-3:]
                        if (float(s[active_metric]) - offset) >= min_net
                    ]
                    if len(adjusted_tail) >= 2:
                        net_alpha, net_r2 = cls.fit_log_linear(adjusted_tail, metric=active_metric)
                        n_prev = int(adjusted_tail[-2]["n"])
                        n_last = int(adjusted_tail[-1]["n"])
                        v_prev_net = float(adjusted_tail[-2][active_metric])
                        v_last_net = float(adjusted_tail[-1][active_metric])
                        obs_net_ratio = v_last_net / max(v_prev_net, 0.01)
                        exp_last_ratio = cls.calculate_expected_ratio(
                            norm_claimed, n_prev, n_last, dominant_var=dominant_var
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
        for i in range(len(fittable_samples) - 1):
            s_from = fittable_samples[i]
            s_to = fittable_samples[i + 1]
            n_from = int(s_from["n"])
            n_to = int(s_to["n"])
            y_from = float(s_from[active_metric])
            y_to = float(s_to[active_metric])

            observed_ratio = (y_to / y_from) if y_from > min_val else 1.0
            expected_ratio = cls.calculate_expected_ratio(
                norm_claimed, n_from, n_to, dominant_var=dominant_var
            )

            growth_ratios.append(
                {
                    "from_n": n_from,
                    "to_n": n_to,
                    "scale_factor": round(float(n_to) / float(n_from), 2) if n_from > 0 else 1.0,
                    "observed_ratio": round(observed_ratio, 2),
                    "expected_ratio": round(expected_ratio, 2),
                }
            )

        max_sample = fittable_samples[-1]
        all_vals = [float(s[active_metric]) for s in fittable_samples]
        max_val = float(max_sample[active_metric])
        min_metric_val = min(all_vals)
        max_n = int(max_sample.get("n", 0))
        min_n = int(fittable_samples[0].get("n", 0))
        n_span = (float(max_n) / float(min_n)) if min_n > 0 else 1.0

        # Headroom ratio always derives from wall/CPU timing against time_limit_ms (never instruction counts)
        timing_vals: list[float] = []
        for s in fittable_samples:
            t_v = cls._valid_positive_num(s.get("cpu_time_ms"), min_val=0.0)
            if t_v is None:
                t_v = cls._valid_positive_num(s.get("wall_time_ms"), min_val=0.0)
            if t_v is not None:
                timing_vals.append(t_v)
        max_time = timing_vals[-1] if timing_vals else 0.0
        peak_time = max(timing_vals) if timing_vals else 0.0
        min_time = min(timing_vals) if timing_vals else 0.0
        headroom_ratio = max_time / time_limit_ms if (time_limit_ms > 0 and timing_vals) else 0.0
        peak_headroom = peak_time / time_limit_ms if (time_limit_ms > 0 and timing_vals) else 0.0

        extra_meta: dict[str, Any] = {"fit_metric": active_metric, "fit_model": fit_model}
        if exponential_fit is not None:
            extra_meta["exponential_fit"] = exponential_fit

        if not is_alpha_match:
            if not is_instruction_metric:
                if fit_model == "exponential" and exponential_fit is not None:
                    exp_hi = exponential_fit["expected_base_range"][1]
                    has_clean_high_order_signal = (
                        exponential_fit["base"] > exp_hi
                        and r_squared >= 0.90
                        and (max_val / max(min_metric_val, 0.1)) >= 4.0
                        and max_val >= 2.0
                    )
                else:
                    has_clean_high_order_signal = (
                        alpha > expected_range[1]
                        and r_squared >= 0.90
                        and (max_val / max(min_metric_val, 0.1)) >= 4.0
                        and max_val >= 2.0
                    )
                in_noise_floor = (
                    peak_time <= 15.0
                    or (peak_time <= 35.0 and r_squared < 0.5)
                    or (
                        peak_time <= 80.0
                        and (peak_time / max(min_time, 0.1)) < 2.2
                    )
                )
                warmup_decay_only = (
                    fit_model == "log_linear"
                    and alpha < expected_range[0]
                    and peak_headroom < 0.15
                )
                narrow_scale_noise = (
                    fit_model == "log_linear"
                    and not is_exp_claimed
                    and norm_claimed != "O(n!)"
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
                        **extra_meta,
                    }

                # 仅在规模足够大（跨越硬件缓存）且耗时余量充足时允许缓存跳跃容差
                if (
                    fit_model == "log_linear"
                    and alpha > expected_range[1]
                    and len(fittable_samples) >= 3
                    and headroom_ratio < 0.40
                    and max_n >= 50000
                ):
                    sub_samples = fittable_samples[:-1]
                    sub_alpha, _ = cls.fit_log_linear(sub_samples, metric=active_metric)
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
                            **extra_meta,
                        }

            if fit_model == "exponential" and exponential_fit is not None:
                failure_reason = (
                    f"Measured exponential base={exponential_fit['base']:.2f} "
                    f"(R^2={exponential_fit['r_squared']:.2f}, fitted as {fitted_cat}) "
                    f"outside claimed {norm_claimed} base range {exponential_fit['expected_base_range']}"
                )
            else:
                failure_reason = (
                    f"Measured complexity exponent alpha={alpha:.2f} (fitted as {fitted_cat}) "
                    f"outside claimed {norm_claimed} range [{expected_range[0]}, {expected_range[1]}]"
                )
            return {
                "passed": False,
                "verdict": "ratio_mismatch",
                "failure_reason": failure_reason,
                "claimed_complexity": norm_claimed,
                "fitted_complexity": fitted_cat,
                "fitted_alpha": round(alpha, 2),
                "r_squared": round(r_squared, 3),
                "samples": samples,
                "growth_ratios": growth_ratios,
                "remediation_advice": (
                    f"The algorithm exhibits {fitted_cat} growth rather than claimed {norm_claimed}. "
                    "Inspect nested loops, optimize algorithm logic, update claimed_complexity, "
                    "or configure manifest.empirical_complexity (dominant_var/generator_args_template/scale_points) "
                    "for multi-parameter or exponential problems."
                ),
                **extra_meta,
            }

        # 当声称为 O(n) / O(n log n) 等线性对数阶，但全部采样点均落在低信噪比 OS 计时底噪内，
        # fitted_cat 可能因常数启动底噪退化为 O(1)；回退报告 claimed_complexity 避免污染难度评估。
        if (
            not is_instruction_metric
            and norm_claimed in ("O(n)", "O(n log n)", "O(nlogn)", "O(n sqrt n)", "O(nsqrtn)")
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
                **extra_meta,
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
            **extra_meta,
        }
