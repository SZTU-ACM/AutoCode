"""Static test-suite quality checks mixin for ``ProblemVerifyTestsTool``."""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..utils.answer_ext import normalize_answer_ext
from ..utils.communication_runner import safe_resolve_under_root
from ..workflow import AutoCodeManifest, load_manifest
from .verify_fingerprint import _LIMIT_GROUPS, _LIMIT_STRATEGY_TYPES, _VerifyHelpersMixin

_MIN_STRUCTURAL_DIVERGENCE = 0.15
_MAX_PARSE_INT_DIGITS = 300
_MAX_SCALE_INT_DIGITS = 20
_NON_SCALE_PARAM_NAMES = frozenset(
    {"seed", "rng_seed", "rand_seed", "random_seed", "salt", "hash_seed"}
)
_NUMERIC_TOKEN_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_INT_TOKEN_RE = re.compile(r"^[+-]?\d+$")

_PROFILE_DIMENSIONS: tuple[str, ...] = (
    "byte_size",
    "line_count",
    "token_count",
    "unique_token_ratio",
    "distinct_token_count",
    "numeric_ratio",
    "max_abs_int",
    "avg_tokens_per_line",
    "max_line_tokens",
    "adjacent_equal_ratio",
    "monotonic_step_ratio",
)
_BOUNDED_RATIO_DIMENSIONS = frozenset(
    {"unique_token_ratio", "numeric_ratio", "adjacent_equal_ratio"}
)
_SIGNED_RATIO_DIMENSIONS = frozenset({"monotonic_step_ratio"})
_SCALE_BUCKET_ORDER: dict[str, int] = {"tiny": 0, "small": 1, "medium": 2, "large": 3}
_SCALE_BUCKET_NAMES: tuple[str, ...] = ("tiny", "small", "medium", "large")


def _parse_int_token(token: str, *, max_digits: int = _MAX_SCALE_INT_DIGITS) -> int | None:
    """Parse an integer token strictly within a safe digit-length budget."""
    if not _INT_TOKEN_RE.match(token):
        return None
    digits = token[1:] if token[0] in "+-" else token
    if len(digits) > max_digits:
        return None
    try:
        return int(token)
    except ValueError:
        return None


def _parse_numeric_token(token: str) -> tuple[float | None, float | None]:
    """Return ``(numeric_value, abs_int_value)`` for a token without overflow or DoS."""
    if _INT_TOKEN_RE.match(token):
        digits = token[1:] if token[0] in "+-" else token
        if len(digits) > _MAX_PARSE_INT_DIGITS:
            # Approximate huge bignums cleanly without hitting Python's int-to-str digit cap.
            sign = -1.0 if token[0] == "-" else 1.0
            try:
                approx = float(f"{digits[:15]}e{len(digits) - 15}")
                if math.isfinite(approx):
                    return sign * approx, approx
            except ValueError:
                pass
            return None, 1e300
        try:
            int_val = int(token)
            fval = float(int_val)
            return (fval if math.isfinite(fval) else None), float(abs(int_val))
        except (ValueError, OverflowError):
            return None, None
    if _NUMERIC_TOKEN_RE.match(token):
        try:
            fval = float(token)
            if math.isfinite(fval):
                return fval, None
        except (ValueError, OverflowError):
            return None, None
    return None, None


def _parse_nonneg_finite_float(val: object) -> float | None:
    """Return ``float(val)`` when ``val`` is a finite non-negative non-bool number."""
    if not isinstance(val, bool) and isinstance(val, int | float):
        fval = float(val)
        if math.isfinite(fval) and fval >= 0.0:
            return fval
    return None


def _parse_int_bound_pair(entry: object) -> tuple[int | None, int | None]:
    """Extract ``(min, max)`` integer bounds from a dict constraint entry."""
    if not isinstance(entry, dict):
        return None, None
    lo = int(entry["min"]) if isinstance(entry.get("min"), int) and not isinstance(entry.get("min"), bool) else None
    hi = int(entry["max"]) if isinstance(entry.get("max"), int) and not isinstance(entry.get("max"), bool) else None
    return lo, hi


def _extract_manifest_primary_bounds(
    manifest: AutoCodeManifest | None,
) -> tuple[int | None, int | None]:
    """Extract primary ``(expected_min, expected_max)`` bounds from ``manifest.constraints``."""
    if manifest is None or not manifest.constraints:
        return None, None
    expected_min: int | None = None
    expected_max: int | None = None
    c = manifest.constraints
    for primary in ("n", "N"):
        lo, hi = _parse_int_bound_pair(c.get(primary))
        if lo is not None:
            expected_min = lo
        if hi is not None:
            expected_max = hi
    if expected_min is None:
        for key in ("n_min", "N_min", "min_n"):
            val = c.get(key)
            if isinstance(val, int) and not isinstance(val, bool):
                expected_min = int(val)
                break
    if expected_max is None:
        for key in ("n_max", "N_max", "max_n", "sum_n_max"):
            val = c.get(key)
            if isinstance(val, int) and not isinstance(val, bool):
                expected_max = int(val)
                break
    if expected_min is None and expected_max is None:
        # Fallback for single non-t constraint (e.g. {"k": {"min": 1, "max": 8}})
        non_t_entries = [
            (k, v)
            for k, v in c.items()
            if isinstance(k, str) and k.strip().lower() not in {"t", "t_max", "t_min"}
        ]
        if len(non_t_entries) == 1:
            expected_min, expected_max = _parse_int_bound_pair(non_t_entries[0][1])
    return expected_min, expected_max


def _scale_bucket_for_int(value: int) -> str:
    if value <= 1:
        return "tiny"
    if value <= 10:
        return "small"
    if value <= 1000:
        return "medium"
    return "large"


def _extract_effective_scale(data: str, manifest: AutoCodeManifest | None = None) -> int | None:
    """Extract effective problem scale token, respecting multi-test layout and manifest hints."""
    tokens = data.split()
    if not tokens:
        return None

    if manifest is not None and manifest.scale_token_index is not None:
        idx = manifest.scale_token_index
        if 0 <= idx < len(tokens):
            explicit = _parse_int_token(tokens[idx])
            if explicit is not None:
                return explicit

    lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
    if not lines:
        return None

    line1_tokens = lines[0].split()
    first_int = _parse_int_token(line1_tokens[0])

    # Handle protocol inputs whose leading token is a small declared ``mode`` flag (e.g. ``mode X``)
    # when no explicit ``n``/``N`` constraint or ``scale_token_index`` is configured.
    if (
        manifest is not None
        and not manifest.multi_test
        and first_int is not None
        and len(line1_tokens) >= 2
        and isinstance(manifest.constraints.get("mode"), dict)
        and not any(
            k in manifest.constraints
            for k in ("n", "N", "n_min", "n_max", "N_min", "N_max", "min_n", "max_n", "sum_n_max")
        )
    ):
        mode_cfg = manifest.constraints["mode"]
        mode_min = mode_cfg.get("min")
        mode_max = mode_cfg.get("max")
        if (
            isinstance(mode_max, int)
            and not isinstance(mode_max, bool)
            and mode_max <= 10
            and (
                not isinstance(mode_min, int)
                or isinstance(mode_min, bool)
                or mode_min <= first_int <= mode_max
            )
            and first_int <= mode_max
        ):
            second_same_line = _parse_int_token(line1_tokens[1])
            if second_same_line is not None:
                return second_same_line

    if len(line1_tokens) == 1 and first_int is not None and first_int > 0 and len(lines) >= 2:
        line2_tokens = lines[1].split()
        second_int = _parse_int_token(line2_tokens[0]) if line2_tokens else None
        if second_int is not None:
            if manifest is not None and manifest.multi_test:
                return second_int
            _, n_max = _extract_manifest_primary_bounds(manifest)
            within_n_max = (n_max is None) or (second_int <= n_max)
            not_single_line_array = not (
                len(lines) == 2 and first_int > 1 and len(line2_tokens) == first_int
            )
            if within_n_max and second_int > 0 and first_int <= 100_000 and len(line2_tokens) <= 4 and not_single_line_array:
                if len(lines) >= 3 and (
                    first_int == 1
                    or (first_int <= 20 and second_int > first_int)
                    or len(lines) >= 1 + 2 * first_int
                ):
                    return second_int
                if (
                    manifest is None
                    and first_int == 1
                    and len(line2_tokens) <= 2
                    and second_int >= 100
                ):
                    return second_int

    return first_int


def _extract_case_structural_profile(
    raw_bytes: bytes, std_time_ms: float | None = None
) -> dict[str, float]:
    """Compute the 11-dimensional structural profile (plus optional std timing) for an input."""
    text = raw_bytes.decode("utf-8", errors="replace")
    raw_lines = text.splitlines()
    line_count = len(raw_lines)
    tokens = text.split()
    token_count = len(tokens)
    distinct_token_count = len(set(tokens))
    unique_token_ratio = (distinct_token_count / token_count) if token_count else 0.0
    avg_tokens_per_line = (token_count / line_count) if line_count else 0.0
    max_line_tokens = max((len(ln.split()) for ln in raw_lines), default=0)

    numeric_count = 0
    max_abs_int = 0.0
    adj_equal_count = 0
    inc_steps = 0
    dec_steps = 0
    num_pairs = 0
    prev_tok: str | None = None
    prev_num: float | None = None

    for tok in tokens:
        if prev_tok is not None and tok == prev_tok:
            adj_equal_count += 1
        num_val, abs_int = _parse_numeric_token(tok)
        if num_val is not None:
            numeric_count += 1
        if abs_int is not None and abs_int > max_abs_int:
            max_abs_int = abs_int
        if prev_num is not None and num_val is not None:
            num_pairs += 1
            if num_val > prev_num:
                inc_steps += 1
            elif num_val < prev_num:
                dec_steps += 1
        prev_tok = tok
        prev_num = num_val

    numeric_ratio = (numeric_count / token_count) if token_count else 0.0
    adjacent_equal_ratio = (adj_equal_count / (token_count - 1)) if token_count >= 2 else 0.0
    monotonic_step_ratio = ((inc_steps - dec_steps) / num_pairs) if num_pairs > 0 else 0.0

    profile: dict[str, float] = {
        "byte_size": float(len(raw_bytes)),
        "line_count": float(line_count),
        "token_count": float(token_count),
        "unique_token_ratio": float(unique_token_ratio),
        "distinct_token_count": float(distinct_token_count),
        "numeric_ratio": float(numeric_ratio),
        "max_abs_int": float(max_abs_int),
        "avg_tokens_per_line": float(avg_tokens_per_line),
        "max_line_tokens": float(max_line_tokens),
        "adjacent_equal_ratio": float(adjacent_equal_ratio),
        "monotonic_step_ratio": float(monotonic_step_ratio),
    }
    if std_time_ms is not None:
        profile["std_time_ms"] = float(std_time_ms)
    return profile


def _aggregate_profiles(profiles: list[dict[str, float]]) -> dict[str, float]:
    """Compute per-dimension mean profile across a list of case profiles."""
    if not profiles:
        return {}
    agg: dict[str, float] = {}
    for dim in _PROFILE_DIMENSIONS:
        agg[dim] = round(sum(p[dim] for p in profiles) / len(profiles), 6)
    timing_vals = [p["std_time_ms"] for p in profiles if "std_time_ms" in p]
    if timing_vals:
        agg["std_time_ms"] = round(sum(timing_vals) / len(timing_vals), 6)
    return agg


def _compute_structural_divergence(
    p3: dict[str, float], p4: dict[str, float]
) -> tuple[float, dict[str, float]]:
    """Compute normalized multi-dimensional structural divergence in ``[0.0, 1.0]``."""
    if not p3 or not p4:
        return 0.0, {}
    dims = list(_PROFILE_DIMENSIONS)
    if "std_time_ms" in p3 and "std_time_ms" in p4:
        dims.append("std_time_ms")

    dim_diffs: dict[str, float] = {}
    for dim in dims:
        v3 = float(p3.get(dim, 0.0))
        v4 = float(p4.get(dim, 0.0))
        if dim in _BOUNDED_RATIO_DIMENSIONS:
            diff = min(1.0, abs(v3 - v4))
        elif dim in _SIGNED_RATIO_DIMENSIONS:
            diff = min(1.0, abs(v3 - v4) / 2.0)
        elif dim == "std_time_ms":
            denom = max(abs(v3), abs(v4), 5.0)
            diff = min(1.0, abs(v3 - v4) / denom)
        else:
            denom = max(abs(v3), abs(v4))
            diff = min(1.0, abs(v3 - v4) / denom) if denom > 0.0 else 0.0
        dim_diffs[dim] = round(diff, 4)

    sorted_diffs = sorted(dim_diffs.values(), reverse=True)
    if not sorted_diffs:
        return 0.0, dim_diffs
    max_diff = sorted_diffs[0]
    top_k = sorted_diffs[:3]
    top_mean = sum(top_k) / len(top_k)
    divergence = round(0.7 * max_diff + 0.3 * top_mean, 4)
    return divergence, dim_diffs


def _extract_manifest_std_timings(manifest: dict[str, Any]) -> dict[str, float]:
    """Collect optional per-case std timings (in ms) from ``tests_manifest``."""
    timings: dict[str, float] = {}
    raw_map = manifest.get("std_timings")
    if isinstance(raw_map, dict):
        for fname, ms in raw_map.items():
            parsed = _parse_nonneg_finite_float(ms) if isinstance(fname, str) else None
            if parsed is not None:
                timings[str(fname)] = parsed
    raw_list = manifest.get("per_test_timings")
    if isinstance(raw_list, list):
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            fname = item.get("file") or item.get("in_file")
            ms = item.get("time_ms") if "time_ms" in item else item.get("std_time_ms")
            parsed = _parse_nonneg_finite_float(ms) if isinstance(fname, str) else None
            if parsed is not None:
                timings[str(fname)] = parsed
    return timings


def _extract_header_int_row(data: str, manifest: AutoCodeManifest | None = None) -> list[int | None]:
    """Extract up to 6 leading integer parameters while preserving exact column indices."""
    lines = [ln.strip() for ln in data.splitlines() if ln.strip()]
    if not lines:
        return []
    line1_tokens = lines[0].split()
    line1: list[int | None] = [_parse_int_token(tok) for tok in line1_tokens[:6]]
    if not any(v is not None for v in line1):
        return []
    if len(line1_tokens) == 1 and line1[0] is not None and line1[0] > 0 and len(lines) >= 2:
        t_val = line1[0]
        line2_toks = lines[1].split()[:6]
        line2_raw: list[int | None] = [_parse_int_token(t) for t in line2_toks]
        if line2_raw and all(v is not None for v in line2_raw):
            line2_lead = line2_raw[0]
            assert line2_lead is not None
            if manifest is not None and manifest.multi_test:
                return line2_raw
            _, n_max = _extract_manifest_primary_bounds(manifest)
            within_n = n_max is None or line2_lead <= n_max
            not_arr = not (len(lines) == 2 and t_val > 1 and len(lines[1].split()) == t_val)
            if within_n and line2_lead > 0 and t_val <= 100_000 and len(line2_toks) <= 4 and not_arr:
                if len(lines) >= 3 and (
                    t_val == 1
                    or (t_val <= 20 and line2_lead > t_val)
                    or len(lines) >= 1 + 2 * t_val
                ):
                    return line2_raw
                if manifest is None and t_val == 1 and len(line2_toks) <= 2 and line2_lead >= 100:
                    return line2_raw
    return line1


def _check_log_decade_and_joint_regimes(
    *,
    parsed_effective_scales: list[int],
    header_rows: Sequence[Sequence[int | None]],
    expected_max: int | None,
    total_files: int,
    validator_bounds: list[dict[str, Any]] | None = None,
    manifest: AutoCodeManifest | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Check log10-decade continuity and 2D joint parameter regimes across a test suite."""
    decade_counts: dict[str, int] = {}
    for val in parsed_effective_scales:
        d = int(math.floor(math.log10(max(1, val))))
        key = str(d)
        decade_counts[key] = decade_counts.get(key, 0) + 1

    gap_warnings: list[dict[str, Any]] = []
    n_max_ref = max(expected_max or 0, max(parsed_effective_scales, default=0))
    if n_max_ref >= 100_000 and total_files >= 10:
        max_decade = int(math.floor(math.log10(n_max_ref)))
        run_start: int | None = None
        for d in range(1, max_decade + 1):
            if decade_counts.get(str(d), 0) == 0:
                if run_start is None:
                    run_start = d
            else:
                if run_start is not None and (d - run_start) >= 2:
                    lo_d, hi_d = run_start, d - 1
                    gap_warnings.append(
                        {
                            "kind": "log_decade_gap",
                            "empty_decades": [lo_d, hi_d],
                            "range": f"[10^{lo_d}, 10^{hi_d + 1})",
                            "reason": (
                                f"log-decade scale vacuum: 0 tests in [10^{lo_d}, 10^{hi_d + 1}) "
                                f"(n_max={n_max_ref})"
                            ),
                        }
                    )
                run_start = None
        if run_start is not None and (max_decade + 1 - run_start) >= 2:
            lo_d, hi_d = run_start, max_decade
            gap_warnings.append(
                {
                    "kind": "log_decade_gap",
                    "empty_decades": [lo_d, hi_d],
                    "range": f"[10^{lo_d}, 10^{hi_d + 1})",
                    "reason": (
                        f"log-decade scale vacuum: 0 tests in [10^{lo_d}, 10^{hi_d + 1}) "
                        f"(n_max={n_max_ref})"
                    ),
                }
            )

    # 2D Joint-Regime Check on leading header parameter columns
    if len(header_rows) >= 10 and n_max_ref >= 5000:
        scalar_bounds = [
            b for b in (validator_bounds or []) if isinstance(b, dict) and b.get("source") == "read_scalar"
        ]
        max_cols = max((len(r) for r in header_rows), default=0)
        if scalar_bounds and isinstance(scalar_bounds[0].get("var"), str):
            first_v = str(scalar_bounds[0]["var"]).strip().lower()
            is_mt = bool(manifest is not None and getattr(manifest, "multi_test", False))
            if (is_mt and len(scalar_bounds) > max_cols) or (
                first_v in {"t", "tc", "tt", "test", "tests", "cases", "q_cases", "test_cases"}
                and (is_mt or len(scalar_bounds) > max_cols)
            ):
                scalar_bounds = scalar_bounds[1:]
        primary_var_name = (
            str(scalar_bounds[0].get("var")).strip()
            if scalar_bounds and isinstance(scalar_bounds[0].get("var"), str)
            else "n"
        )
        for col_idx in range(1, min(max_cols, 4)):
            col_pairs: list[tuple[int, int]] = []
            for r in header_rows:
                if len(r) > col_idx:
                    p1_val = r[0]
                    p2_val = r[col_idx]
                    if p1_val is not None and p2_val is not None:
                        col_pairs.append((p1_val, p2_val))
            if len(col_pairs) < 10:
                continue
            p2_vals = [p2 for _, p2 in col_pairs]
            p2_max = max(p2_vals)
            val_meta = scalar_bounds[col_idx] if col_idx < len(scalar_bounds) else None
            bound_var_name = (
                str(val_meta.get("var")).strip().lower()
                if val_meta and isinstance(val_meta.get("var"), str)
                else ""
            )
            if bound_var_name in _NON_SCALE_PARAM_NAMES or bound_var_name.endswith("_seed"):
                continue
            val_max_expr = (
                str(val_meta.get("max_expr")).strip()
                if val_meta and isinstance(val_meta.get("max_expr"), str)
                else ""
            )
            val_min = val_meta.get("min") if val_meta else None
            explicit_pos = bool(
                val_max_expr
                and val_max_expr.lower() == primary_var_name.lower()
                and (val_min is None or (isinstance(val_min, int) and val_min in (0, 1)))
            )
            inferred_pos = bool(
                n_max_ref >= 100_000
                and p2_max >= int(0.5 * n_max_ref)
                and p2_max >= 1000
                and all(1 <= p2 <= p1 for p1, p2 in col_pairs)
                and any(p1 >= int(0.95 * n_max_ref) for p1, _ in col_pairs)
                and all(
                    p2 > 100 and (p1 - p2) > 100
                    for p1, p2 in col_pairs
                    if p1 >= int(0.95 * n_max_ref)
                )
            )
            is_position_col = explicit_pos or inferred_pos
            if is_position_col:
                extreme_pairs = [(p1, p2) for p1, p2 in col_pairs if p1 >= int(0.95 * n_max_ref)]
                if extreme_pairs and not any(
                    p2 <= 100 or (p1 - p2) <= 100 for p1, p2 in extreme_pairs
                ):
                    gap_warnings.append(
                        {
                            "kind": "extreme_boundary_position_missing",
                            "col_index": col_idx,
                            "var": val_meta.get("var") if val_meta else f"col_{col_idx}",
                            "reason": (
                                f"at extreme n >= {int(0.95 * n_max_ref)}, position parameter "
                                f"{(val_meta.get('var') if val_meta else None) or f'col_{col_idx}'} "
                                "never approaches left/right boundary (<= 100 or >= n - 100)"
                            ),
                        }
                    )
                continue

            if p2_max >= 1000:
                mid_hi = min(n_max_ref, 50_000)
                if mid_hi >= 2000:
                    has_joint_mid = any(
                        2000 <= p1 <= mid_hi and p2 >= 0.5 * p2_max
                        for p1, p2 in col_pairs
                    )
                    if not has_joint_mid:
                        gap_warnings.append(
                            {
                                "kind": "joint_regime_gap",
                                "col_index": col_idx,
                                "var": val_meta.get("var") if val_meta else f"col_{col_idx}",
                                "p2_max": p2_max,
                                "reason": (
                                    f"2D joint-regime gap: no test covers n in [2000, {mid_hi}] "
                                    f"with {(val_meta.get('var') if val_meta else None) or f'col_{col_idx}'} "
                                    f">= {int(0.5 * p2_max)}"
                                ),
                            }
                        )
    return decade_counts, gap_warnings


class _VerifyStaticChecksMixin(_VerifyHelpersMixin):
    """File completeness, empty/duplicate checks, limit ratio, and boundary coverage."""

    def _check_file_count(
        self,
        tests_dir: str,
        answer_ext: str,
        *,
        is_interactive: bool = False,
        allow_prefixed_names: bool = False,
    ) -> dict:
        """检查文件完整性：每个 .in 有对应的 answer_ext（交互题允许省略 .ans），文件名连续。"""
        import re as _re

        tests_path = Path(tests_dir)
        in_files = sorted(p.name for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in")
        ans_files = sorted(p.name for p in tests_path.iterdir() if p.is_file() and p.name.endswith(answer_ext))
        ans_file_set = set(ans_files)
        in_file_set = set(in_files)

        missing_ans = []
        for in_file in in_files:
            ans_file = Path(in_file).with_suffix(answer_ext).name
            if ans_file not in ans_file_set:
                missing_ans.append(in_file)

        orphan_ans = []
        for ans_file in ans_files:
            if not ans_file.endswith(answer_ext):
                continue
            base = ans_file[: -len(answer_ext)]
            in_file = f"{base}.in"
            if in_file not in in_file_set:
                orphan_ans.append(ans_file)

        non_numeric: list[str] = []
        numeric_indices: list[int] = []
        prefixes_seen: set[str] = set()
        for f in in_files:
            stem = Path(f).stem
            if stem.isdigit():
                numeric_indices.append(int(stem))
                prefixes_seen.add("")
            elif allow_prefixed_names:
                m_pref = _re.fullmatch(r"([A-Za-z0-9_-]*?[-_]|[A-Za-z_][A-Za-z0-9_-]*?)(\d+)", stem)
                if m_pref:
                    prefixes_seen.add(m_pref.group(1))
                    numeric_indices.append(int(m_pref.group(2)))
                else:
                    non_numeric.append(f)
            else:
                non_numeric.append(f)

        numeric_indices.sort()
        numeric_index_set = set(numeric_indices)
        # In standalone prefixed layouts (e.g. test1..testN, sample1..sample2), only check contiguity when a single prefix is used.
        check_contiguous = (not allow_prefixed_names) or len(prefixes_seen) <= 1
        expected_indices = (
            list(range(1, max(numeric_indices) + 1)) if (numeric_indices and check_contiguous) else []
        )
        missing_indices = [
            idx for idx in expected_indices if idx not in numeric_index_set
        ]
        duplicate_indices = (
            sorted(idx for idx in numeric_index_set if numeric_indices.count(idx) > 1)
            if check_contiguous
            else []
        )

        passed = (
            bool(in_files)
            and (is_interactive or not missing_ans)
            and not orphan_ans
            and not non_numeric
            and not missing_indices
            and not duplicate_indices
        )
        return {
            "passed": passed,
            "total": len(in_files),
            "missing_ans": missing_ans,
            "orphan_ans": orphan_ans,
            "missing_indices": missing_indices,
            "duplicate_indices": duplicate_indices,
            "non_numeric": non_numeric,
            "interactive_answers_optional": is_interactive,
        }

    def _check_no_empty(
        self,
        tests_dir: str,
        *,
        is_interactive: bool = False,
        answer_ext: str = ".ans",
    ) -> dict:
        """检查没有空文件（交互题允许空占位答案文件，但输入 .in 仍不可为空）。"""
        norm_ext = normalize_answer_ext(answer_ext) or ".ans"
        interactive_optional_exts = tuple(sorted({".ans", ".out", norm_ext}))
        empty_files = []
        for f in os.listdir(tests_dir):
            filepath = os.path.join(tests_dir, f)
            if os.path.isfile(filepath) and os.path.getsize(filepath) == 0:
                if is_interactive and f.endswith(interactive_optional_exts):
                    continue
                empty_files.append(f)

        return {
            "passed": len(empty_files) == 0,
            "total": len(os.listdir(tests_dir)),
            "empty_files": empty_files,
        }

    def _check_limit_ratio(self, tests_dir: str, manifest: dict | None = None) -> dict:
        """检查最终测试中 type=3/4 是否不少于一半。

        manifest 可来自一次性加载并透传，缺省时回退为读取 tests manifest。
        """
        if manifest is None:
            manifest = self._read_tests_manifest(tests_dir)
        if not manifest:
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "tests manifest missing or unreadable",
            }

        tests = manifest.get("tests", [])
        if not isinstance(tests, list):
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "invalid manifest format: tests must be a list",
            }

        in_files = sorted(
            (
                f
                for f in os.listdir(tests_dir)
                if f.endswith(".in") and os.path.isfile(os.path.join(tests_dir, f))
            ),
            key=lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)],
        )
        in_file_set = set(in_files)
        type_by_in_file: dict[str, str] = {}
        group_by_in_file: dict[str, str] = {}
        for item in tests:
            if not isinstance(item, dict):
                continue
            in_file = item.get("in_file")
            type_param = item.get("type_param")
            if isinstance(in_file, str) and isinstance(type_param, str):
                type_by_in_file[in_file] = type_param
                group_by_in_file[in_file] = str(item.get("group", "")).strip().lower()

        missing_in_manifest = sorted(f for f in in_files if f not in type_by_in_file)
        if missing_in_manifest:
            return {
                "passed": False,
                "total": len(in_files),
                "limit_case_count": 0,
                "limit_case_minimum_required": (len(in_files) + 1) // 2 if in_files else 0,
                "limit_case_ratio": 0.0,
                "missing_in_manifest": missing_in_manifest,
                "error": "manifest does not cover all .in files",
            }

        total = len(in_files)
        if total == 0:
            return {
                "passed": False,
                "total": 0,
                "limit_case_count": 0,
                "limit_case_minimum_required": 0,
                "limit_case_ratio": 0.0,
                "error": "no .in files found",
            }

        limit_case_count = sum(
            1
            for in_file in in_file_set
            if type_by_in_file[in_file] in _LIMIT_STRATEGY_TYPES
            or group_by_in_file.get(in_file, "") in _LIMIT_GROUPS
        )
        minimum_required = (total + 1) // 2
        ratio = limit_case_count / total

        return {
            "passed": limit_case_count >= minimum_required,
            "total": total,
            "limit_case_count": limit_case_count,
            "limit_case_minimum_required": minimum_required,
            "limit_case_ratio": ratio,
            "limit_strategy_types": sorted(_LIMIT_STRATEGY_TYPES),
        }

    @staticmethod
    def _extract_effective_scale(data: str, manifest: AutoCodeManifest | None = None) -> int | None:
        return _extract_effective_scale(data, manifest)

    @staticmethod
    def _load_optional_problem_manifest(tests_dir: str) -> AutoCodeManifest | None:
        """Load ``AutoCodeManifest`` from ``Path(tests_dir).parent`` (or ``tests_dir``), returning ``None`` if absent."""
        tests_path = Path(tests_dir)
        for root in (tests_path.parent, tests_path):
            resolved = safe_resolve_under_root(str(root), ".autocode/manifest.json")
            if resolved is None or not Path(resolved).is_file():
                continue
            try:
                return load_manifest(str(root))
            except (OSError, ValueError):
                return None
        return None

    def _check_limit_semantics(self, tests_dir: str, manifest: dict | None = None) -> dict:
        """检查 type=3/4 是否存在以及多维结构差异语义门禁。

        manifest 可来自一次性加载并透传，缺省时回退为读取 tests manifest。
        """
        if manifest is None:
            manifest = self._read_tests_manifest(tests_dir)
        if not manifest:
            return {"passed": False, "error": "tests manifest missing or unreadable"}
        tests = manifest.get("tests", [])
        if not isinstance(tests, list):
            return {"passed": False, "error": "invalid manifest format: tests must be a list"}
        type3 = [t for t in tests if isinstance(t, dict) and t.get("type_param") == "3"]
        type4 = [t for t in tests if isinstance(t, dict) and t.get("type_param") == "4"]
        if not type4:
            return {"passed": False, "error": "type=4 cases missing; update generator first"}
        if not type3:
            return {"passed": False, "error": "type=3 cases missing; update generator first"}

        sig3 = {str(t["signature"]) for t in type3 if t.get("signature")}
        sig4 = {str(t["signature"]) for t in type4 if t.get("signature")}
        overlap_ratio = (
            len(sig3 & sig4) / max(1, min(len(sig3), len(sig4))) if (sig3 and sig4) else 0.0
        )

        manifest_timings = _extract_manifest_std_timings(manifest)
        unsafe_files: list[str] = []
        missing_files: list[str] = []

        def _profile_group(entries: list[dict[str, Any]]) -> list[dict[str, float]]:
            profiles: list[dict[str, float]] = []
            for entry in entries:
                in_file = entry.get("in_file")
                if not isinstance(in_file, str) or not in_file.strip():
                    missing_files.append(str(in_file or "<missing>"))
                    continue
                resolved = safe_resolve_under_root(tests_dir, in_file)
                if resolved is None:
                    unsafe_files.append(in_file)
                    continue
                path = Path(resolved)
                if not path.is_file():
                    missing_files.append(in_file)
                    continue
                try:
                    raw_bytes = path.read_bytes()
                except OSError:
                    missing_files.append(in_file)
                    continue
                std_ms = _parse_nonneg_finite_float(entry.get("std_time_ms", entry.get("time_ms")))
                if std_ms is None:
                    if in_file in manifest_timings:
                        std_ms = manifest_timings[in_file]
                    elif path.name in manifest_timings:
                        std_ms = manifest_timings[path.name]
                profiles.append(_extract_case_structural_profile(raw_bytes, std_time_ms=std_ms))
            return profiles

        profiles3 = _profile_group(type3)
        profiles4 = _profile_group(type4)
        type3_profile = _aggregate_profiles(profiles3)
        type4_profile = _aggregate_profiles(profiles4)
        structural_divergence, dimension_diffs = _compute_structural_divergence(
            type3_profile, type4_profile
        )

        unreadable = bool(unsafe_files) or not profiles3 or not profiles4
        passed = (
            (not unreadable)
            and (overlap_ratio < 0.8)
            and (structural_divergence >= _MIN_STRUCTURAL_DIVERGENCE)
        )

        if passed:
            hint = ""
        elif unsafe_files:
            hint = f"存在越界或不安全的测试路径: {', '.join(unsafe_files[:5])}"
        elif not profiles3 or not profiles4:
            hint = "type=3 或 type=4 输入文件缺失或不可读，无法验证结构差异"
        elif overlap_ratio >= 0.8:
            hint = "需要确保 type=4 不是重复输入或仅放大参数，而是有独立卡法"
        else:
            hint = (
                f"需要确保 type=4 不是仅放大参数或同分布随机克隆（当前结构差异度="
                f"{structural_divergence:.4f} < {_MIN_STRUCTURAL_DIVERGENCE:.2f}），而是有独立卡法"
            )

        out: dict[str, Any] = {
            "passed": passed,
            "type3_count": len(type3),
            "type4_count": len(type4),
            "overlap_ratio": overlap_ratio,
            "structural_divergence": structural_divergence,
            "divergence_threshold": _MIN_STRUCTURAL_DIVERGENCE,
            "dimension_diffs": dimension_diffs,
            "type3_profile": type3_profile,
            "type4_profile": type4_profile,
            "hint": hint,
        }
        if unsafe_files:
            out["unsafe_files"] = unsafe_files
        if missing_files:
            out["missing_files"] = missing_files
        return out

    def _check_duplicate_inputs(self, tests_dir: str, *, max_near_ratio: float = 0.2) -> dict:
        tests_path = Path(tests_dir)
        in_files = sorted(
            (p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in"),
            key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)],
        )
        signatures: dict[str, list[str]] = {}
        normalized_signatures: dict[str, list[str]] = {}
        for path in in_files:
            data = path.read_text(encoding="utf-8", errors="replace")
            exact_key = hashlib.sha256(data.encode("utf-8", errors="replace")).hexdigest()
            normalized_key = " ".join(data.split())
            signatures.setdefault(exact_key, []).append(path.name)
            normalized_signatures.setdefault(normalized_key, []).append(path.name)

        exact_duplicates = [names for names in signatures.values() if len(names) > 1]
        near_duplicates = [
            names
            for names in normalized_signatures.values()
            if len(names) > 1 and names not in exact_duplicates
        ]
        duplicate_file_count = sum(len(names) for names in exact_duplicates)
        near_duplicate_file_count = sum(len(names) for names in near_duplicates)
        total = len(in_files)
        ratio = (duplicate_file_count + near_duplicate_file_count) / total if total else 0.0
        near_ratio = near_duplicate_file_count / total if total else 0.0
        # Zero tolerance for byte-identical exact duplicates; ratio threshold applies only to near duplicates.
        passed = len(exact_duplicates) == 0 and near_ratio <= max_near_ratio
        return {
            "passed": passed,
            "total": total,
            "duplicate_file_count": duplicate_file_count,
            "exact_duplicate_count": len(exact_duplicates),
            "exact_duplicate_tolerance": 0,
            "near_duplicate_file_count": near_duplicate_file_count,
            "duplicate_ratio": ratio,
            "near_duplicate_ratio": near_ratio,
            "exact_duplicates": exact_duplicates,
            "near_duplicates": near_duplicates[:20],
            "threshold": max_near_ratio,
        }

    def _check_scale_distribution(
        self,
        tests_dir: str,
        manifest: AutoCodeManifest | None = None,
        *,
        validator_bounds: list[dict[str, Any]] | None = None,
    ) -> dict:
        if manifest is None:
            manifest = self._load_optional_problem_manifest(tests_dir)
        tests_path = Path(tests_dir)
        in_files = sorted(
            (p for p in tests_path.iterdir() if p.is_file() and p.suffix == ".in"),
            key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)],
        )
        buckets = {"empty": 0, "tiny": 0, "small": 0, "medium": 0, "large": 0}
        byte_buckets = {"0-100B": 0, "101B-1KB": 0, "1KB-32KB": 0, "32KB+": 0}
        parsed_first_ints: list[int] = []
        parsed_effective_scales: list[int] = []
        header_rows: list[list[int | None]] = []
        for path in in_files:
            resolved = safe_resolve_under_root(tests_dir, path.name)
            if resolved is None:
                continue
            try:
                raw_bytes = Path(resolved).read_bytes()
            except OSError:
                continue
            size = len(raw_bytes)
            data = raw_bytes.decode("utf-8", errors="replace")
            tokens = data.split()
            if not tokens:
                buckets["empty"] += 1
            else:
                first_int = _parse_int_token(tokens[0])
                if first_int is not None:
                    parsed_first_ints.append(first_int)
                effective_scale = _extract_effective_scale(data, manifest)
                if effective_scale is not None:
                    parsed_effective_scales.append(effective_scale)
                    bucket = _scale_bucket_for_int(effective_scale)
                else:
                    bucket = "medium"
                row = _extract_header_int_row(data, manifest)
                if row:
                    header_rows.append(row)
                # Byte/token scale hints ensure large multi-test inputs (e.g. T=1, n=200000) never land in tiny.
                if size > 32 * 1024 or len(tokens) > 1000:
                    min_bucket = "large"
                elif size > 1024 or len(tokens) > 100:
                    min_bucket = "medium"
                else:
                    min_bucket = "tiny"
                if _SCALE_BUCKET_ORDER[min_bucket] > _SCALE_BUCKET_ORDER[bucket]:
                    bucket = min_bucket
                buckets[bucket] += 1
            if size <= 100:
                byte_buckets["0-100B"] += 1
            elif size <= 1024:
                byte_buckets["101B-1KB"] += 1
            elif size <= 32 * 1024:
                byte_buckets["1KB-32KB"] += 1
            else:
                byte_buckets["32KB+"] += 1

        expected_min, expected_max = _extract_manifest_primary_bounds(manifest)
        narrow_constraint_exempt = False
        if expected_max is not None:
            if expected_max <= 10:
                narrow_constraint_exempt = True
            elif expected_min is not None and _scale_bucket_for_int(expected_min) == _scale_bucket_for_int(expected_max):
                narrow_constraint_exempt = True

        non_empty_scale_buckets = sum(
            1 for key, value in buckets.items() if key != "empty" and value > 0
        )
        passed = bool(in_files) and buckets["empty"] == 0
        if passed and len(in_files) >= 3 and not narrow_constraint_exempt:
            passed = non_empty_scale_buckets >= 2

        log_decade_counts, scale_gap_warnings = _check_log_decade_and_joint_regimes(
            parsed_effective_scales=parsed_effective_scales,
            header_rows=header_rows,
            expected_max=expected_max,
            total_files=len(in_files),
            validator_bounds=validator_bounds,
            manifest=manifest,
        )
        require_continuity = bool(
            manifest is not None and manifest.audit_gates.require_scale_continuity
        )
        if passed and require_continuity and scale_gap_warnings:
            passed = False

        hint = ""
        if not passed:
            if not in_files:
                hint = "未找到测试输入文件"
            elif buckets["empty"] > 0:
                hint = "存在空测试输入文件"
            elif non_empty_scale_buckets < 2 and len(in_files) >= 3 and not narrow_constraint_exempt:
                hint = (
                    "测试规模退化为单一尺度桶（>= 3 组数据时需覆盖至少 2 个不同尺度桶）"
                )
            elif scale_gap_warnings:
                hint = "; ".join(str(w["reason"]) for w in scale_gap_warnings[:3])

        effective_buckets = {k: v for k, v in buckets.items() if v > 0}
        return {
            "passed": passed,
            "total": len(in_files),
            "first_integer_buckets": effective_buckets,
            "effective_scale_buckets": effective_buckets,
            "byte_size_buckets": {k: v for k, v in byte_buckets.items() if v > 0},
            "min_first_integer": min(parsed_first_ints) if parsed_first_ints else None,
            "max_first_integer": max(parsed_first_ints) if parsed_first_ints else None,
            "min_effective_scale": min(parsed_effective_scales) if parsed_effective_scales else None,
            "max_effective_scale": max(parsed_effective_scales) if parsed_effective_scales else None,
            "non_empty_scale_bucket_count": non_empty_scale_buckets,
            "narrow_constraint_exempt": narrow_constraint_exempt,
            "log_decade_counts": log_decade_counts,
            "scale_gap_warnings": scale_gap_warnings,
            "scale_continuity_passed": not scale_gap_warnings,
            "hint": hint,
        }

    def _check_purpose_coverage(
        self, tests_dir: str, manifest: AutoCodeManifest | None = None
    ) -> dict:
        tmanifest = self._read_tests_manifest(tests_dir)
        if not tmanifest:
            return {"passed": False, "error": "tests manifest missing or unreadable"}
        if tmanifest.get("mode") == "register_existing":
            return {
                "passed": True,
                "skipped": True,
                "total": len(tmanifest.get("tests", [])) if isinstance(tmanifest.get("tests"), list) else 0,
                "message": "purpose_coverage skipped for register_existing tests",
            }
        tests = tmanifest.get("tests", [])
        if not isinstance(tests, list) or not tests:
            return {"passed": False, "error": "tests manifest has no tests"}
        type_counts: dict[str, int] = {}
        group_counts: dict[str, int] = {}
        purpose_count = 0
        for item in tests:
            if not isinstance(item, dict):
                continue
            type_param = str(item.get("type_param", "unknown"))
            group = str(item.get("group", "default"))
            if item.get("purpose"):
                purpose_count += 1
            type_counts[type_param] = type_counts.get(type_param, 0) + 1
            group_counts[group] = group_counts.get(group, 0) + 1
        if manifest is not None and manifest.case_plan:
            required_types = {str(cp.type) for cp in manifest.case_plan}
        else:
            required_types = {"1", "2", "3", "4"}
        missing_types = sorted(required_types - set(type_counts))
        return {
            "passed": not missing_types,
            "total": len(tests),
            "type_counts": type_counts,
            "group_counts": group_counts,
            "purpose_count": purpose_count,
            "missing_types": missing_types,
        }

    def _check_boundary_coverage(
        self, tests_dir: str, manifest: AutoCodeManifest | None
    ) -> dict[str, Any]:
        """Verify that formal test inputs cover declared min/max primary scale boundaries."""
        if manifest is None or not manifest.constraints:
            return {
                "passed": True,
                "skipped": True,
                "message": "no numeric constraints declared in manifest",
            }
        expected_min, expected_max = _extract_manifest_primary_bounds(manifest)

        if expected_min is None and expected_max is None:
            return {
                "passed": True,
                "skipped": True,
                "message": "no n_min/n_max primary bounds declared in manifest.constraints",
            }

        tests_path = Path(tests_dir)
        in_files = sorted(
            p for p in tests_path.iterdir() if p.is_file() and not p.is_symlink() and p.suffix == ".in"
        )
        observed_vals: list[int] = []
        for p in in_files:
            resolved = safe_resolve_under_root(tests_dir, p.name)
            if resolved is None:
                continue
            try:
                data = Path(resolved).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scale = _extract_effective_scale(data, manifest)
            if scale is not None:
                observed_vals.append(scale)
        if not observed_vals:
            return {
                "passed": False,
                "expected_min": expected_min,
                "expected_max": expected_max,
                "observed_min": None,
                "observed_max": None,
                "missing_boundaries": ["no_integer_inputs_found"],
            }

        obs_min = min(observed_vals)
        obs_max = max(observed_vals)
        missing_boundaries: list[str] = []
        min_covered = expected_min is None or (expected_min in observed_vals)
        max_covered = expected_max is None or (
            expected_max in observed_vals
            or (expected_max > 0 and obs_max >= int(expected_max * 0.95))
        )
        if not min_covered:
            missing_boundaries.append(f"min({expected_min}) not hit (observed min={obs_min})")
        if not max_covered:
            missing_boundaries.append(f"max({expected_max}) not hit (observed max={obs_max})")
        return {
            "passed": not missing_boundaries,
            "total": len(in_files),
            "expected_min": expected_min,
            "expected_max": expected_max,
            "observed_min": obs_min,
            "observed_max": obs_max,
            "min_covered": min_covered,
            "max_covered": max_covered,
            "missing_boundaries": missing_boundaries,
        }
