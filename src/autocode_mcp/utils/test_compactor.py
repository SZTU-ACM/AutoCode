"""Additional-Greedy set-cover test suite compaction.

Selects a minimal covering subset of test cases preserving 100% of covered targets
(such as source branches, killed wrong solutions, and killed mutants).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Hashable, Iterable, Mapping
from typing import Any


def _natural_sort_key(name: str) -> list[int | str]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", name)]


def _normalize_row_targets(
    raw_targets: Any,
    *,
    binary_matrix: bool,
) -> set[Hashable]:
    if raw_targets is None:
        return set()
    if isinstance(raw_targets, Mapping):
        return {k for k, flag in raw_targets.items() if bool(flag)}
    if isinstance(raw_targets, list | tuple):
        if binary_matrix or (raw_targets and all(isinstance(x, bool) for x in raw_targets)):
            return {f"target_{idx}" for idx, flag in enumerate(raw_targets) if bool(flag)}
        return set(raw_targets)
    if isinstance(raw_targets, set | frozenset):
        return set(raw_targets)
    if isinstance(raw_targets, Iterable) and not isinstance(raw_targets, str | bytes):
        return set(raw_targets)
    raise TypeError(f"Unsupported coverage entry type: {type(raw_targets).__name__}")


def merge_coverage_matrices(
    *,
    branches: Mapping[str, Any] | None = None,
    wrong_kills: Mapping[str, Any] | None = None,
    mutants: Mapping[str, Any] | None = None,
    binary_matrix: bool = False,
) -> dict[str, set[str]]:
    """Combine branch, wrong-solution kill, and mutant kill matrices into one namespaced matrix."""
    merged: dict[str, set[str]] = {}
    for prefix, matrix in (
        ("branch", branches),
        ("wrong", wrong_kills),
        ("mutant", mutants),
    ):
        if matrix is None:
            continue
        for test_id, raw_vals in matrix.items():
            key = str(test_id)
            bucket = merged.setdefault(key, set())
            for target in _normalize_row_targets(raw_vals, binary_matrix=binary_matrix):
                bucket.add(f"{prefix}:{target}")
    return merged


def compact_test_suite(
    coverage_matrix: Mapping[str, Any],
    *,
    pinned_tests: Iterable[str] | None = None,
    test_costs: Mapping[str, float | int] | None = None,
    prune_redundant: bool = True,
    binary_matrix: bool = False,
) -> dict[str, Any]:
    """Select a minimal covering subset of tests via Additional-Greedy set cover.

    Args:
        coverage_matrix: Mapping of ``test_id`` -> iterable of covered target IDs
            (or mapping ``target_id -> bool`` / boolean mask sequence).
        pinned_tests: Optional test IDs that must remain in ``selected_tests``.
        test_costs: Optional positive per-test costs (when provided, maximizes
            ``newly_covered / cost`` per step).
        prune_redundant: When ``True`` (default), performs a reverse-order
            irredundancy pass over non-pinned selected tests while preserving
            100% of covered targets.
        binary_matrix: Set ``True`` to interpret numeric ``0/1`` sequences as bit masks.
    """
    if not isinstance(coverage_matrix, Mapping):
        raise TypeError("coverage_matrix must be a mapping from test ID to covered targets")

    ordered_ids = sorted((str(k) for k in coverage_matrix.keys()), key=_natural_sort_key)
    normalized: dict[str, set[Hashable]] = {}
    universe: set[Hashable] = set()
    for raw_key in coverage_matrix:
        test_id = str(raw_key)
        targets = _normalize_row_targets(coverage_matrix[raw_key], binary_matrix=binary_matrix)
        normalized[test_id] = targets
        universe.update(targets)

    costs: dict[str, float] = {}
    if test_costs is not None:
        for tid in ordered_ids:
            raw_c = test_costs.get(tid, 1.0)
            if isinstance(raw_c, bool) or not isinstance(raw_c, int | float):
                costs[tid] = 1.0
            else:
                val = float(raw_c)
                costs[tid] = val if (math.isfinite(val) and val > 0.0) else 1e-9

    pinned_order: list[str] = []
    pinned_set: set[str] = set()
    if pinned_tests is not None:
        for item in pinned_tests:
            tid = str(item)
            if tid in normalized and tid not in pinned_set:
                pinned_set.add(tid)
                pinned_order.append(tid)

    uncovered: set[Hashable] = set(universe)
    selected: list[str] = []
    selected_set: set[str] = set()
    steps: list[dict[str, Any]] = []

    for tid in pinned_order:
        gained = normalized[tid] & uncovered
        uncovered -= gained
        selected.append(tid)
        selected_set.add(tid)
        steps.append(
            {
                "test": tid,
                "pinned": True,
                "newly_covered": len(gained),
                "cumulative_targets": len(universe) - len(uncovered),
            }
        )

    while uncovered:
        best_tid: str | None = None
        best_score: tuple[float, int, int, tuple[Any, ...]] | None = None
        for tid in ordered_ids:
            if tid in selected_set:
                continue
            gain = len(normalized[tid] & uncovered)
            if gain <= 0:
                continue
            if test_costs is not None:
                eff_gain = gain / costs[tid]
            else:
                eff_gain = float(gain)
            # Negative natural key isn't trivial, so keep best explicitly via comparison
            cand_key = (eff_gain, gain, len(normalized[tid]))
            if best_score is None:
                best_tid = tid
                best_score = (*cand_key, ())
            else:
                prev_primary = best_score[:3]
                if cand_key > prev_primary:
                    best_tid = tid
                    best_score = (*cand_key, ())
        if best_tid is None:
            break
        gained = normalized[best_tid] & uncovered
        uncovered -= gained
        selected.append(best_tid)
        selected_set.add(best_tid)
        steps.append(
            {
                "test": best_tid,
                "pinned": False,
                "newly_covered": len(gained),
                "cumulative_targets": len(universe) - len(uncovered),
            }
        )

    if prune_redundant and len(selected) > 1:
        cover_counts: Counter[Hashable] = Counter()
        for tid in selected:
            cover_counts.update(normalized[tid])

        kept_reverse: list[str] = []
        for tid in reversed(selected):
            if tid not in pinned_set and all(cover_counts[tgt] > 1 for tgt in normalized[tid]):
                cover_counts.subtract(normalized[tid])
                selected_set.discard(tid)
                continue
            kept_reverse.append(tid)
        selected = list(reversed(kept_reverse))

    covered_final: set[Hashable] = set()
    for tid in selected:
        covered_final.update(normalized[tid])

    dropped = [tid for tid in ordered_ids if tid not in selected_set]
    original_count = len(ordered_ids)
    selected_count = len(selected)
    total_targets = len(universe)
    covered_count = len(covered_final)
    coverage_preserved = 1.0 if total_targets == 0 else round(covered_count / total_targets, 6)
    reduction_ratio = (
        round((original_count - selected_count) / original_count, 4) if original_count > 0 else 0.0
    )

    return {
        "selected_tests": selected,
        "dropped_tests": dropped,
        "original_count": original_count,
        "selected_count": selected_count,
        "dropped_count": len(dropped),
        "total_targets": total_targets,
        "covered_targets": covered_count,
        "coverage_preserved": coverage_preserved,
        "reduction_ratio": reduction_ratio,
        "selection_steps": steps,
    }
