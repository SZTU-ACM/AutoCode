#!/usr/bin/env python3
"""ShinkaEvolve-inspired local evolutionary test-generator optimization harness.

Provides a deterministic, zero-LLM-SDK multi-island evolutionary search loop with:
- Multi-island subpopulations (``IslandSpec``) and periodic ring migration
- Behavioral novelty filtering (``NoveltyFilter``) to prevent duplicate behaviors
- Composite fitness evaluation:
  ``killed_wrong_weight * killed_wrongs + branch_coverage_bonus + runtime_pressure + sample_diversity``
- Both an importable Python API and a standalone CLI (``--help`` / ``--demo`` / ``--problem-dir``)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_STRATEGIES: tuple[str, ...] = (
    "corner_extremes",
    "tle_stress_adversarial",
    "anti_greedy_structure",
    "hash_and_overflow",
    "balanced",
)

_EXTRA_TAG_POOLS: dict[str, tuple[str, ...]] = {
    "corner_extremes": ("min_bound", "max_bound", "all_equal", "zero_edge", "single_elem"),
    "tle_stress_adversarial": ("star", "broom", "chain", "anti_sort", "dense_dag"),
    "anti_greedy_structure": ("bipartite", "cycle", "negative_trap", "tie_break", "alternating"),
    "hash_and_overflow": ("anti_hash", "overflow_32", "multi_reset", "prime_bucket"),
    "balanced": ("random_mix", "sparse", "dense", "boundary"),
}


def _validate_non_negative_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a finite non-negative number, got {value!r}")
    num = float(value)
    if not math.isfinite(num) or num < 0.0:
        raise ValueError(f"{label} must be a finite non-negative number, got {value!r}")
    return num


def _validate_finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    num = float(value)
    if not math.isfinite(num):
        raise ValueError(f"{label} must be a finite number, got {value!r}")
    return num


@dataclass(frozen=True)
class IslandSpec:
    """Configuration for one island sub-population in the evolutionary archive."""

    island_id: str
    name: str
    strategy_bias: str = "balanced"
    population_size: int = 6
    elite_count: int = 2
    mutation_rate: float = 0.7
    seed_offset: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.island_id, str) or not self.island_id.strip():
            raise ValueError("island_id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if isinstance(self.population_size, bool) or self.population_size < 1:
            raise ValueError("population_size must be >= 1")
        if (
            isinstance(self.elite_count, bool)
            or self.elite_count < 1
            or self.elite_count > self.population_size
        ):
            raise ValueError("elite_count must satisfy 1 <= elite_count <= population_size")
        rate = _validate_non_negative_float(self.mutation_rate, "mutation_rate")
        if rate > 1.0:
            raise ValueError("mutation_rate must be <= 1.0")


@dataclass(frozen=True)
class FitnessWeights:
    """Weights for composite generator evaluation fitness."""

    killed_wrong_weight: float = 10.0
    branch_coverage_weight: float = 5.0
    runtime_pressure_weight: float = 2.0
    sample_diversity_weight: float = 1.5
    invalid_penalty: float = 50.0

    def __post_init__(self) -> None:
        _validate_non_negative_float(self.killed_wrong_weight, "killed_wrong_weight")
        _validate_non_negative_float(self.branch_coverage_weight, "branch_coverage_weight")
        _validate_non_negative_float(self.runtime_pressure_weight, "runtime_pressure_weight")
        _validate_non_negative_float(self.sample_diversity_weight, "sample_diversity_weight")
        _validate_non_negative_float(self.invalid_penalty, "invalid_penalty")


@dataclass(frozen=True)
class GeneratorCandidate:
    """Candidate test-generator parameterization evolved across islands."""

    candidate_id: str
    island_id: str
    generation: int
    type_param: str
    n_min: int
    n_max: int
    t_min: int = 1
    t_max: int = 1
    extra_args: tuple[str, ...] = ()
    parent_id: str | None = None
    strategy_tag: str = "balanced"

    def to_cli_args(self, seed: int) -> list[str]:
        """Format standard testlib generator CLI arguments."""
        return [
            str(seed),
            self.type_param,
            str(self.n_min),
            str(self.n_max),
            str(self.t_min),
            str(self.t_max),
            *self.extra_args,
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "island_id": self.island_id,
            "generation": self.generation,
            "type_param": self.type_param,
            "n_min": self.n_min,
            "n_max": self.n_max,
            "t_min": self.t_min,
            "t_max": self.t_max,
            "extra_args": list(self.extra_args),
            "parent_id": self.parent_id,
            "strategy_tag": self.strategy_tag,
        }


@dataclass(frozen=True)
class EvaluationMetrics:
    """Observed generator quality and behavioral descriptors for one candidate."""

    killed_wrongs: int = 0
    killed_wrong_names: tuple[str, ...] = ()
    branch_coverage_bonus: float = 0.0
    runtime_pressure: float = 0.0
    sample_diversity: float = 0.0
    validator_passed: bool = True
    sol_passed: bool = True
    behavior_signature: str = ""
    behavior_features: tuple[str, ...] = ()
    sample_outputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class FitnessBreakdown:
    """Decomposed composite fitness score."""

    killed_wrongs: int
    killed_wrong_component: float
    branch_coverage_bonus: float
    runtime_pressure: float
    sample_diversity: float
    penalty: float
    total_fitness: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "killed_wrongs": self.killed_wrongs,
            "killed_wrong_component": round(self.killed_wrong_component, 6),
            "branch_coverage_bonus": round(self.branch_coverage_bonus, 6),
            "runtime_pressure": round(self.runtime_pressure, 6),
            "sample_diversity": round(self.sample_diversity, 6),
            "penalty": round(self.penalty, 6),
            "total_fitness": round(self.total_fitness, 6),
        }


@dataclass(frozen=True)
class EvaluatedCandidate:
    """Candidate paired with its evaluation metrics, fitness, and novelty score."""

    candidate: GeneratorCandidate
    metrics: EvaluationMetrics
    fitness: FitnessBreakdown
    novelty_distance: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "killed_wrong_names": list(self.metrics.killed_wrong_names),
            "validator_passed": self.metrics.validator_passed,
            "sol_passed": self.metrics.sol_passed,
            "behavior_signature": self.metrics.behavior_signature,
            "novelty_distance": round(self.novelty_distance, 6),
            "fitness": self.fitness.to_dict(),
        }


@dataclass(frozen=True)
class GenerationStats:
    """Per-generation evolutionary telemetry."""

    generation: int
    best_fitness: float
    mean_fitness: float
    accepted_novel: int
    rejected_duplicates: int
    island_bests: dict[str, float]
    total_killed_wrongs_covered: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "best_fitness": round(self.best_fitness, 6),
            "mean_fitness": round(self.mean_fitness, 6),
            "accepted_novel": self.accepted_novel,
            "rejected_duplicates": self.rejected_duplicates,
            "island_bests": {k: round(v, 6) for k, v in self.island_bests.items()},
            "total_killed_wrongs_covered": self.total_killed_wrongs_covered,
        }


@dataclass(frozen=True)
class EvolutionConfig:
    """Top-level configuration for the multi-island evolutionary search."""

    generations: int = 5
    islands: tuple[IslandSpec, ...] = ()
    seed: int = 42
    novelty_threshold: float = 0.15
    migrate_interval: int = 2
    migration_count: int = 1
    fitness_weights: FitnessWeights = field(default_factory=FitnessWeights)
    n_limit: int = 1000
    t_limit: int = 10

    def __post_init__(self) -> None:
        if isinstance(self.generations, bool) or self.generations < 1:
            raise ValueError("generations must be >= 1")
        if isinstance(self.seed, bool):
            raise ValueError("seed must be an integer")
        thresh = _validate_non_negative_float(self.novelty_threshold, "novelty_threshold")
        if thresh > 1.0:
            raise ValueError("novelty_threshold must be <= 1.0")
        if isinstance(self.migrate_interval, bool) or self.migrate_interval < 1:
            raise ValueError("migrate_interval must be >= 1")
        if isinstance(self.migration_count, bool) or self.migration_count < 0:
            raise ValueError("migration_count must be >= 0")
        if isinstance(self.n_limit, bool) or self.n_limit < 1:
            raise ValueError("n_limit must be >= 1")
        if isinstance(self.t_limit, bool) or self.t_limit < 1:
            raise ValueError("t_limit must be >= 1")


@dataclass(frozen=True)
class EvolutionResult:
    """Final output of the multi-island evolutionary generator search."""

    best_candidate: EvaluatedCandidate | None
    archive: tuple[EvaluatedCandidate, ...]
    island_bests: dict[str, EvaluatedCandidate]
    history: tuple[GenerationStats, ...]
    accepted_novel: int
    rejected_duplicates: int
    total_evaluated: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "best_candidate": self.best_candidate.to_dict() if self.best_candidate else None,
            "archive_size": len(self.archive),
            "archive": [item.to_dict() for item in self.archive],
            "island_bests": {k: v.to_dict() for k, v in self.island_bests.items()},
            "history": [step.to_dict() for step in self.history],
            "accepted_novel": self.accepted_novel,
            "rejected_duplicates": self.rejected_duplicates,
            "total_evaluated": self.total_evaluated,
        }


def default_islands(count: int = 3, population_size: int = 6) -> tuple[IslandSpec, ...]:
    """Create a diverse set of ``count`` evolution islands."""
    if isinstance(count, bool) or count < 1:
        raise ValueError("island count must be >= 1")
    elite_count = min(2, population_size)
    specs: list[IslandSpec] = []
    for idx in range(count):
        bias = _DEFAULT_STRATEGIES[idx % len(_DEFAULT_STRATEGIES)]
        specs.append(
            IslandSpec(
                island_id=f"island_{idx}",
                name=f"Island {idx} ({bias})",
                strategy_bias=bias,
                population_size=population_size,
                elite_count=elite_count,
                mutation_rate=min(0.95, 0.55 + 0.1 * (idx % 4)),
                seed_offset=idx * 1009,
            )
        )
    return tuple(specs)


def compute_composite_fitness(
    metrics: EvaluationMetrics,
    weights: FitnessWeights | None = None,
) -> FitnessBreakdown:
    """Compute composite fitness:
    ``killed_wrong_weight * killed_wrongs + branch_coverage_bonus + runtime_pressure + sample_diversity``.
    """
    w = weights or FitnessWeights()
    if isinstance(metrics.killed_wrongs, bool) or metrics.killed_wrongs < 0:
        raise ValueError(f"killed_wrongs must be a non-negative integer, got {metrics.killed_wrongs!r}")

    killed_count = max(metrics.killed_wrongs, len(set(metrics.killed_wrong_names)))
    killed_component = w.killed_wrong_weight * float(killed_count)
    cov_bonus = _validate_finite_float(metrics.branch_coverage_bonus, "branch_coverage_bonus")
    rt_pressure = _validate_finite_float(metrics.runtime_pressure, "runtime_pressure")
    diversity = _validate_finite_float(metrics.sample_diversity, "sample_diversity")
    penalty = 0.0 if (metrics.validator_passed and metrics.sol_passed) else w.invalid_penalty
    total = killed_component + cov_bonus + rt_pressure + diversity - penalty
    return FitnessBreakdown(
        killed_wrongs=killed_count,
        killed_wrong_component=killed_component,
        branch_coverage_bonus=cov_bonus,
        runtime_pressure=rt_pressure,
        sample_diversity=diversity,
        penalty=penalty,
        total_fitness=total,
    )


def metrics_from_observations(
    *,
    killed_wrong_names: Sequence[str] = (),
    branch_coverage: float = 0.0,
    sol_time_ms: float = 0.0,
    time_limit_ms: float = 2000.0,
    sample_outputs: Sequence[str] = (),
    validator_passed: bool = True,
    sol_passed: bool = True,
    extra_features: Sequence[str] = (),
    weights: FitnessWeights | None = None,
) -> EvaluationMetrics:
    """Build normalized ``EvaluationMetrics`` and behavioral signature from raw test observations."""
    w = weights or FitnessWeights()
    cov_clamped = max(0.0, min(1.0, _validate_non_negative_float(branch_coverage, "branch_coverage")))
    tl_ms = max(1.0, _validate_non_negative_float(time_limit_ms, "time_limit_ms"))
    sol_ms = _validate_non_negative_float(sol_time_ms, "sol_time_ms")
    pressure_ratio = min(1.0, sol_ms / tl_ms)

    unique_outputs = {out.strip() for out in sample_outputs if out.strip()}
    diversity_ratio = (
        len(unique_outputs) / float(len(sample_outputs)) if sample_outputs else 0.0
    )

    killed_tuple = tuple(sorted(set(killed_wrong_names)))
    output_hashes = tuple(
        sorted(hashlib.sha256(out.encode("utf-8", errors="replace")).hexdigest()[:10] for out in unique_outputs)
    )
    cov_bucket = int(round(cov_clamped * 10.0))
    rt_bucket = int(round(pressure_ratio * 10.0))
    features = tuple(
        sorted(
            {
                *(f"kill:{name}" for name in killed_tuple),
                *(f"out:{h}" for h in output_hashes[:6]),
                *extra_features,
                f"cov_b:{cov_bucket}",
                f"rt_b:{rt_bucket}",
                f"val:{int(validator_passed)}",
                f"sol:{int(sol_passed)}",
            }
        )
    )
    sig_payload = json.dumps(
        {
            "killed": killed_tuple,
            "features": features,
            "cov_b": cov_bucket,
            "rt_b": rt_bucket,
            "val": validator_passed,
            "sol": sol_passed,
        },
        sort_keys=True,
    )
    sig = hashlib.sha256(sig_payload.encode("utf-8")).hexdigest()[:16]

    return EvaluationMetrics(
        killed_wrongs=len(killed_tuple),
        killed_wrong_names=killed_tuple,
        branch_coverage_bonus=w.branch_coverage_weight * cov_clamped,
        runtime_pressure=w.runtime_pressure_weight * pressure_ratio,
        sample_diversity=w.sample_diversity_weight * diversity_ratio,
        validator_passed=validator_passed,
        sol_passed=sol_passed,
        behavior_signature=sig,
        behavior_features=features,
        sample_outputs=tuple(sample_outputs),
    )


def compute_behavior_distance(
    features_a: frozenset[str],
    features_b: frozenset[str],
) -> float:
    """Compute normalized Jaccard behavioral distance in ``[0.0, 1.0]``."""
    if not features_a and not features_b:
        return 0.0
    union = features_a | features_b
    if not union:
        return 0.0
    inter = features_a & features_b
    return 1.0 - (len(inter) / float(len(union)))


class NoveltyFilter:
    """Archive-backed behavioral novelty gate preventing duplicate generator behaviors."""

    def __init__(self, novelty_threshold: float = 0.15) -> None:
        thresh = _validate_non_negative_float(novelty_threshold, "novelty_threshold")
        if thresh > 1.0:
            raise ValueError("novelty_threshold must be <= 1.0")
        self.novelty_threshold = thresh
        self._seen_signatures: set[str] = set()
        self._archive_features: list[frozenset[str]] = []

    def descriptor(self, metrics: EvaluationMetrics) -> tuple[str, frozenset[str]]:
        """Extract canonical ``(behavior_signature, feature_set)`` from evaluation metrics."""
        killed_sorted = sorted(set(metrics.killed_wrong_names))
        cov_b = int(round(metrics.branch_coverage_bonus * 10.0))
        rt_b = int(round(metrics.runtime_pressure * 10.0))
        div_b = int(round(metrics.sample_diversity * 10.0))
        sig = metrics.behavior_signature.strip()
        if not sig:
            raw = json.dumps(
                {
                    "killed": killed_sorted,
                    "killed_n": metrics.killed_wrongs,
                    "features": sorted(set(metrics.behavior_features)),
                    "cov_b": cov_b,
                    "rt_b": rt_b,
                    "div_b": div_b,
                    "val": metrics.validator_passed,
                    "sol": metrics.sol_passed,
                },
                sort_keys=True,
            )
            sig = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        feat_set = frozenset(
            {
                *metrics.behavior_features,
                *(f"kill:{name}" for name in killed_sorted),
                f"sig:{sig}" if not metrics.behavior_features else f"cov_b:{cov_b}",
            }
        )
        return sig, feat_set

    def evaluate(
        self,
        metrics: EvaluationMetrics,
        *,
        record: bool = True,
    ) -> tuple[bool, float, str]:
        """Check whether ``metrics`` exhibits sufficient behavioral novelty.

        Returns ``(accepted, min_distance, signature)``.
        """
        sig, feat_set = self.descriptor(metrics)
        if sig in self._seen_signatures:
            return False, 0.0, sig

        if not self._archive_features:
            if record:
                self._seen_signatures.add(sig)
                self._archive_features.append(feat_set)
            return True, 1.0, sig

        min_dist = min(
            compute_behavior_distance(feat_set, prev)
            for prev in self._archive_features
        )
        accepted = min_dist >= self.novelty_threshold
        if accepted and record:
            self._seen_signatures.add(sig)
            self._archive_features.append(feat_set)
        return accepted, min_dist, sig


CandidateEvaluator = Callable[[GeneratorCandidate], EvaluationMetrics]


class DefaultDeterministicEvaluator:
    """Self-contained deterministic evaluator for smoke tests and offline optimization."""

    def __init__(
        self,
        *,
        n_limit: int = 1000,
        t_limit: int = 10,
        weights: FitnessWeights | None = None,
    ) -> None:
        self.n_limit = max(1, n_limit)
        self.t_limit = max(1, t_limit)
        self.weights = weights or FitnessWeights()

    def __call__(self, candidate: GeneratorCandidate) -> EvaluationMetrics:
        tags = set(candidate.extra_args)
        killed: list[str] = []
        if candidate.n_min <= 2 and candidate.type_param in {"1", "3"}:
            killed.append("wrong_small_edge")
        if candidate.n_max >= int(self.n_limit * 0.85) and candidate.type_param in {"3", "4"}:
            killed.append("wrong_tle_quadratic")
        if "overflow_32" in tags or (candidate.type_param == "3" and candidate.n_max >= self.n_limit):
            killed.append("wrong_int_overflow")
        if "anti_hash" in tags or "prime_bucket" in tags:
            killed.append("wrong_hash_collision")
        if any(t in tags for t in ("star", "broom", "chain", "anti_sort")):
            killed.append("wrong_tree_diameter_or_sort")
        if any(t in tags for t in ("bipartite", "negative_trap", "tie_break")):
            killed.append("wrong_greedy_assumption")
        if candidate.t_max >= 2 and "multi_reset" in tags:
            killed.append("wrong_uncleaned_multitest")

        coverage_signals = 0.2
        if candidate.type_param in {"1", "3", "4"}:
            coverage_signals += 0.2
        coverage_signals += min(0.6, 0.15 * len(tags))
        coverage = min(1.0, coverage_signals)

        scale_ratio = min(1.0, candidate.n_max / float(self.n_limit))
        t_ratio = min(1.0, candidate.t_max / float(self.t_limit))
        structural_boost = 0.25 if any(t in tags for t in ("star", "broom", "anti_sort", "dense_dag")) else 0.0
        sol_ms = min(2000.0, 2000.0 * min(1.0, 0.45 * scale_ratio + 0.30 * t_ratio + structural_boost))

        outputs = [
            f"{candidate.type_param}:{candidate.n_min}-{candidate.n_max}:{','.join(sorted(tags))}:s{seed}"
            for seed in range(1, 5)
        ]
        extra_feats = sorted(
            {
                f"type:{candidate.type_param}",
                *(f"tag:{t}" for t in tags),
                f"scale_q:{int(round(scale_ratio * 4))}",
            }
        )
        return metrics_from_observations(
            killed_wrong_names=killed,
            branch_coverage=coverage,
            sol_time_ms=sol_ms,
            time_limit_ms=2000.0,
            sample_outputs=outputs,
            validator_passed=(candidate.n_min <= candidate.n_max and candidate.t_min <= candidate.t_max),
            sol_passed=True,
            extra_features=extra_feats,
            weights=self.weights,
        )


class ProblemDirEvaluator:
    """Live local evaluator running compiled/Python artifacts inside a ``problem_dir``."""

    def __init__(
        self,
        problem_dir: str | Path,
        *,
        weights: FitnessWeights | None = None,
        sample_seeds: Sequence[int] = (1, 2, 3),
    ) -> None:
        self.problem_dir = os.path.abspath(os.fspath(problem_dir))
        self.weights = weights or FitnessWeights()
        self.sample_seeds = tuple(sample_seeds)

    def __call__(self, candidate: GeneratorCandidate) -> EvaluationMetrics:
        return asyncio.run(self._evaluate_async(candidate))

    async def _evaluate_async(self, candidate: GeneratorCandidate) -> EvaluationMetrics:
        from autocode_mcp.utils.lang_runner import (
            infer_language_from_path,
            resolve_time_limit_multiplier,
            run_source,
        )
        from autocode_mcp.utils.platform import get_exe_extension
        from autocode_mcp.workflow import load_manifest

        exe_ext = get_exe_extension()
        manifest = load_manifest(self.problem_dir)
        gen_exe = os.path.join(self.problem_dir, "files", f"gen{exe_ext}")
        val_exe = os.path.join(self.problem_dir, "files", f"val{exe_ext}")
        sol_target = os.path.join(self.problem_dir, "solutions", f"sol{exe_ext}")
        sol_lang = "cpp"
        if not os.path.isfile(sol_target):
            py_sol = os.path.join(self.problem_dir, "solutions", "sol.py")
            if os.path.isfile(py_sol):
                sol_target = py_sol
                sol_lang = "python"

        if not os.path.isfile(gen_exe):
            return DefaultDeterministicEvaluator(weights=self.weights)(candidate)

        tl_ms = float(manifest.time_limit_ms) if (manifest and manifest.time_limit_ms > 0) else 2000.0
        mem_mb = float(manifest.memory_limit_mb) if (manifest and manifest.memory_limit_mb > 0) else 256.0
        sol_tl_sec = (tl_ms / 1000.0) * resolve_time_limit_multiplier(sol_lang, manifest)

        outputs: list[str] = []
        sol_answers: list[str] = []
        val_ok = True
        sol_ok = True
        max_sol_ms = 0.0

        for seed in self.sample_seeds:
            gen_res = await run_source(
                gen_exe,
                "",
                language="cpp",
                timeout=5.0,
                memory_mb=512.0,
                args=candidate.to_cli_args(seed),
            )
            if not gen_res.success or not gen_res.stdout.strip():
                val_ok = False
                continue
            outputs.append(gen_res.stdout)

            if os.path.isfile(val_exe):
                val_res = await run_source(
                    val_exe, gen_res.stdout, language="cpp", timeout=5.0, memory_mb=256.0
                )
                if not val_res.success:
                    val_ok = False

            sol_ans = ""
            if os.path.isfile(sol_target):
                sol_res = await run_source(
                    sol_target,
                    gen_res.stdout,
                    language=sol_lang,
                    timeout=sol_tl_sec,
                    memory_mb=mem_mb,
                    manifest=manifest,
                )
                if not sol_res.success:
                    sol_ok = False
                else:
                    sol_ans = sol_res.stdout.strip()
                max_sol_ms = max(max_sol_ms, float(sol_res.time_ms))
            sol_answers.append(sol_ans)

        killed_wrongs: list[str] = []
        if manifest is not None and outputs:
            for entry in manifest.solutions:
                if entry.role != "wrong":
                    continue
                w_lang = entry.language or (infer_language_from_path(entry.path) or "cpp")
                w_target = (
                    os.path.join(self.problem_dir, entry.path)
                    if w_lang == "python"
                    else os.path.join(self.problem_dir, "solutions", f"{entry.name}{exe_ext}")
                )
                if not os.path.isfile(w_target):
                    continue
                w_tl_sec = (tl_ms / 1000.0) * resolve_time_limit_multiplier(w_lang, manifest)
                for sample_in, sol_ans in zip(outputs, sol_answers, strict=True):
                    w_res = await run_source(
                        w_target,
                        sample_in,
                        language=w_lang,
                        timeout=w_tl_sec,
                        memory_mb=mem_mb,
                        manifest=manifest,
                    )
                    if not w_res.success or (sol_ans and w_res.stdout.strip() != sol_ans):
                        killed_wrongs.append(entry.name)
                        break

        cov_est = min(1.0, 0.25 + 0.15 * len(set(candidate.extra_args)) + (0.2 if candidate.type_param in {"3", "4"} else 0.0))
        return metrics_from_observations(
            killed_wrong_names=killed_wrongs,
            branch_coverage=cov_est,
            sol_time_ms=max_sol_ms,
            time_limit_ms=tl_ms,
            sample_outputs=outputs,
            validator_passed=val_ok and bool(outputs),
            sol_passed=sol_ok,
            extra_features=(f"type:{candidate.type_param}", *(f"tag:{t}" for t in candidate.extra_args)),
            weights=self.weights,
        )


def _seed_island_candidates(
    island: IslandSpec,
    config: EvolutionConfig,
    rng: random.Random,
) -> list[GeneratorCandidate]:
    tag_pool = _EXTRA_TAG_POOLS.get(island.strategy_bias, _EXTRA_TAG_POOLS["balanced"])
    type_choices = {
        "corner_extremes": ("1", "3"),
        "tle_stress_adversarial": ("4", "3"),
        "anti_greedy_structure": ("2", "3", "1"),
        "hash_and_overflow": ("3", "4"),
        "balanced": ("1", "2", "3", "4"),
    }.get(island.strategy_bias, ("1", "2", "3", "4"))

    seeded: list[GeneratorCandidate] = []
    for idx in range(island.population_size):
        t_param = type_choices[idx % len(type_choices)]
        if island.strategy_bias == "corner_extremes":
            n_min = 1
            n_max = config.n_limit if idx % 2 == 1 else min(config.n_limit, 10)
        elif island.strategy_bias == "tle_stress_adversarial":
            n_min = max(1, int(config.n_limit * 0.7))
            n_max = config.n_limit
        elif island.strategy_bias == "anti_greedy_structure":
            n_min = 2
            n_max = min(config.n_limit, 16 + idx * 4)
        else:
            n_min = rng.randint(1, max(1, config.n_limit // 4))
            n_max = rng.randint(n_min, config.n_limit)

        tag = tag_pool[idx % len(tag_pool)]
        t_max = min(config.t_limit, 1 + (idx % max(1, min(3, config.t_limit))))
        seeded.append(
            GeneratorCandidate(
                candidate_id=f"{island.island_id}_g0_{idx}",
                island_id=island.island_id,
                generation=0,
                type_param=t_param,
                n_min=n_min,
                n_max=n_max,
                t_min=1,
                t_max=t_max,
                extra_args=(tag,),
                parent_id=None,
                strategy_tag=island.strategy_bias,
            )
        )
    return seeded


def _mutate_candidate(
    parent: GeneratorCandidate,
    partner: GeneratorCandidate | None,
    island: IslandSpec,
    generation: int,
    cand_idx: int,
    config: EvolutionConfig,
    rng: random.Random,
) -> GeneratorCandidate:
    tag_pool = _EXTRA_TAG_POOLS.get(island.strategy_bias, _EXTRA_TAG_POOLS["balanced"])
    tags = list(parent.extra_args)
    n_min = parent.n_min
    n_max = parent.n_max
    t_max = parent.t_max
    type_param = parent.type_param

    if partner is not None and rng.random() < 0.35:
        for pt in partner.extra_args:
            if pt not in tags:
                tags.append(pt)
                break

    if rng.random() < island.mutation_rate:
        op = rng.choice(("add_tag", "swap_tag", "scale_n", "flip_type", "bump_t"))
        if op == "add_tag":
            cand_tag = rng.choice(tag_pool)
            if cand_tag not in tags:
                tags.append(cand_tag)
            tags = tags[-3:]
        elif op == "swap_tag" and tags:
            tags[rng.randrange(len(tags))] = rng.choice(tag_pool)
            tags = list(dict.fromkeys(tags))
        elif op == "scale_n":
            if island.strategy_bias in {"tle_stress_adversarial", "hash_and_overflow"}:
                n_min = max(1, int(config.n_limit * rng.uniform(0.6, 0.95)))
                n_max = config.n_limit
            elif island.strategy_bias == "corner_extremes":
                n_min = 1
                n_max = (
                    config.n_limit
                    if rng.random() < 0.7
                    else min(config.n_limit, rng.randint(1, 5))
                )
            else:
                n_min = rng.randint(1, max(1, config.n_limit // 2))
                n_max = rng.randint(n_min, config.n_limit)
        elif op == "flip_type":
            bias_types = {
                "corner_extremes": ("1", "3"),
                "tle_stress_adversarial": ("3", "4"),
                "anti_greedy_structure": ("1", "2", "3"),
                "hash_and_overflow": ("3", "4"),
            }.get(island.strategy_bias, ("1", "2", "3", "4"))
            type_param = rng.choice(bias_types)
        elif op == "bump_t":
            t_max = min(config.t_limit, max(1, parent.t_max + rng.choice((1, 2, -1))))

    if not tags:
        tags = [rng.choice(tag_pool)]

    return GeneratorCandidate(
        candidate_id=f"{island.island_id}_g{generation}_{cand_idx}",
        island_id=island.island_id,
        generation=generation,
        type_param=type_param,
        n_min=min(n_min, n_max),
        n_max=max(n_min, n_max),
        t_min=1,
        t_max=max(1, t_max),
        extra_args=tuple(tags),
        parent_id=parent.candidate_id,
        strategy_tag=island.strategy_bias,
    )


def _rank_population(items: Sequence[EvaluatedCandidate]) -> list[EvaluatedCandidate]:
    return sorted(
        items,
        key=lambda item: (-item.fitness.total_fitness, item.candidate.candidate_id),
    )


def run_evolution(
    config: EvolutionConfig | None = None,
    *,
    evaluator: CandidateEvaluator | None = None,
) -> EvolutionResult:
    """Execute the deterministic ShinkaEvolve-inspired multi-island evolution loop."""
    cfg = config or EvolutionConfig()
    islands = cfg.islands or default_islands()
    eval_fn: CandidateEvaluator = evaluator or DefaultDeterministicEvaluator(
        n_limit=cfg.n_limit,
        t_limit=cfg.t_limit,
        weights=cfg.fitness_weights,
    )
    rng = random.Random(cfg.seed)
    novelty_filter = NoveltyFilter(novelty_threshold=cfg.novelty_threshold)

    island_pops: dict[str, list[EvaluatedCandidate]] = {}
    archive: list[EvaluatedCandidate] = []
    history: list[GenerationStats] = []
    accepted_novel = 0
    rejected_duplicates = 0
    total_evaluated = 0
    covered_wrongs: set[str] = set()

    def _evaluate_and_filter(cand: GeneratorCandidate) -> tuple[EvaluatedCandidate, bool]:
        nonlocal accepted_novel, rejected_duplicates, total_evaluated
        total_evaluated += 1
        metrics = eval_fn(cand)
        is_novel, distance, sig = novelty_filter.evaluate(metrics, record=True)
        normalized_metrics = (
            metrics
            if metrics.behavior_signature
            else EvaluationMetrics(
                killed_wrongs=metrics.killed_wrongs,
                killed_wrong_names=metrics.killed_wrong_names,
                branch_coverage_bonus=metrics.branch_coverage_bonus,
                runtime_pressure=metrics.runtime_pressure,
                sample_diversity=metrics.sample_diversity,
                validator_passed=metrics.validator_passed,
                sol_passed=metrics.sol_passed,
                behavior_signature=sig,
                behavior_features=metrics.behavior_features,
                sample_outputs=metrics.sample_outputs,
            )
        )
        fitness = compute_composite_fitness(normalized_metrics, cfg.fitness_weights)
        evaluated = EvaluatedCandidate(
            candidate=cand,
            metrics=normalized_metrics,
            fitness=fitness,
            novelty_distance=distance,
        )
        if is_novel:
            accepted_novel += 1
            archive.append(evaluated)
            covered_wrongs.update(normalized_metrics.killed_wrong_names)
        else:
            rejected_duplicates += 1
        return evaluated, is_novel

    # Generation 0: seed each island
    for island in islands:
        island_rng = random.Random(cfg.seed + island.seed_offset)
        seeded = _seed_island_candidates(island, cfg, island_rng)
        pop: list[EvaluatedCandidate] = []
        for cand in seeded:
            ev, is_novel = _evaluate_and_filter(cand)
            if is_novel or not pop:
                pop.append(ev)
        island_pops[island.island_id] = _rank_population(pop)[: island.population_size]

    def _record_stats(gen: int, gen_accepted: int, gen_rejected: int) -> None:
        all_items = [ev for pop in island_pops.values() for ev in pop]
        best = max((ev.fitness.total_fitness for ev in all_items), default=0.0)
        mean = (
            sum(ev.fitness.total_fitness for ev in all_items) / float(len(all_items))
            if all_items
            else 0.0
        )
        island_bests = {
            isl_id: (_rank_population(pop)[0].fitness.total_fitness if pop else 0.0)
            for isl_id, pop in island_pops.items()
        }
        history.append(
            GenerationStats(
                generation=gen,
                best_fitness=best,
                mean_fitness=mean,
                accepted_novel=gen_accepted,
                rejected_duplicates=gen_rejected,
                island_bests=island_bests,
                total_killed_wrongs_covered=len(covered_wrongs),
            )
        )

    _record_stats(0, accepted_novel, rejected_duplicates)

    for gen in range(1, cfg.generations):
        prev_accepted = accepted_novel
        prev_rejected = rejected_duplicates

        for island in islands:
            cur_pop = _rank_population(island_pops[island.island_id])
            elites = cur_pop[: island.elite_count]
            next_pop: list[EvaluatedCandidate] = list(elites)

            needed = max(1, island.population_size - len(elites))
            attempts = 0
            max_attempts = needed * 3
            cand_counter = 0
            while len(next_pop) < island.population_size and attempts < max_attempts:
                attempts += 1
                pool = cur_pop if cur_pop else elites
                p1 = rng.choice(elites) if elites and rng.random() < 0.7 else rng.choice(pool)
                p2 = rng.choice(pool) if len(pool) > 1 else None
                child = _mutate_candidate(
                    p1.candidate,
                    p2.candidate if p2 is not None else None,
                    island,
                    gen,
                    cand_counter,
                    cfg,
                    rng,
                )
                cand_counter += 1
                ev, is_novel = _evaluate_and_filter(child)
                if is_novel:
                    next_pop.append(ev)

            island_pops[island.island_id] = _rank_population(next_pop)[: island.population_size]

        # Ring migration across islands to share high-fitness novel elites
        if len(islands) > 1 and cfg.migration_count > 0 and gen % cfg.migrate_interval == 0:
            migrants: list[tuple[str, list[EvaluatedCandidate]]] = []
            for idx, src_island in enumerate(islands):
                dst_island = islands[(idx + 1) % len(islands)]
                src_ranked = _rank_population(island_pops[src_island.island_id])
                migrants.append((dst_island.island_id, src_ranked[: cfg.migration_count]))
            for dst_id, incoming in migrants:
                dst_spec = next(isl for isl in islands if isl.island_id == dst_id)
                existing_sigs = {ev.metrics.behavior_signature for ev in island_pops[dst_id]}
                merged = list(island_pops[dst_id])
                for mig in incoming:
                    if mig.metrics.behavior_signature not in existing_sigs:
                        merged.append(mig)
                        existing_sigs.add(mig.metrics.behavior_signature)
                island_pops[dst_id] = _rank_population(merged)[: dst_spec.population_size]

        _record_stats(
            gen,
            accepted_novel - prev_accepted,
            rejected_duplicates - prev_rejected,
        )

    ranked_archive = tuple(_rank_population(archive))
    island_best_map = {
        isl_id: _rank_population(pop)[0]
        for isl_id, pop in island_pops.items()
        if pop
    }
    best_overall = ranked_archive[0] if ranked_archive else None

    return EvolutionResult(
        best_candidate=best_overall,
        archive=ranked_archive,
        island_bests=island_best_map,
        history=tuple(history),
        accepted_novel=accepted_novel,
        rejected_duplicates=rejected_duplicates,
        total_evaluated=total_evaluated,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "ShinkaEvolve-inspired deterministic multi-island evolutionary test-generator "
            "optimization harness with behavioral novelty filtering and composite fitness."
        )
    )
    parser.add_argument(
        "--problem-dir",
        type=Path,
        default=None,
        help="Optional AutoCode problem directory to evaluate compiled/Python artifacts live (read-only).",
    )
    parser.add_argument("--generations", type=int, default=5, help="Number of evolution generations (>=1).")
    parser.add_argument("--islands", type=int, default=3, help="Number of isolated evolution islands (>=1).")
    parser.add_argument("--population-size", type=int, default=6, help="Population size per island (>=1).")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic PRNG seed.")
    parser.add_argument(
        "--novelty-threshold",
        type=float,
        default=0.15,
        help="Minimum behavioral Jaccard distance [0.0, 1.0] to accept into archive.",
    )
    parser.add_argument(
        "--killed-wrong-weight",
        type=float,
        default=10.0,
        help="Composite fitness weight for each killed wrong solution.",
    )
    parser.add_argument("--n-limit", type=int, default=1000, help="Upper N scale bound for candidates.")
    parser.add_argument("--t-limit", type=int, default=10, help="Upper T multi-test bound for candidates.")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run using the deterministic built-in benchmark evaluator even without --problem-dir.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit full JSON evolution report to stdout.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional file path to write the JSON evolution report (never written unless requested).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    weights = FitnessWeights(killed_wrong_weight=args.killed_wrong_weight)
    islands = default_islands(count=args.islands, population_size=args.population_size)
    cfg = EvolutionConfig(
        generations=args.generations,
        islands=islands,
        seed=args.seed,
        novelty_threshold=args.novelty_threshold,
        fitness_weights=weights,
        n_limit=args.n_limit,
        t_limit=args.t_limit,
    )

    evaluator: CandidateEvaluator
    if args.problem_dir is not None and not args.demo:
        if not args.problem_dir.is_dir():
            print(f"ERROR: problem_dir not found: {args.problem_dir}", file=sys.stderr)
            return 2
        evaluator = ProblemDirEvaluator(args.problem_dir, weights=weights)
    else:
        evaluator = DefaultDeterministicEvaluator(
            n_limit=args.n_limit,
            t_limit=args.t_limit,
            weights=weights,
        )

    result = run_evolution(cfg, evaluator=evaluator)
    payload = result.to_dict()

    if args.output is not None:
        out_path = args.output.expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        best = result.best_candidate
        best_score = best.fitness.total_fitness if best else 0.0
        best_id = best.candidate.candidate_id if best else "none"
        print(
            f"Evolution complete: generations={cfg.generations}, islands={len(islands)}, "
            f"evaluated={result.total_evaluated}, novel={result.accepted_novel}, "
            f"rejected_duplicates={result.rejected_duplicates}, "
            f"best={best_id} (fitness={best_score:.2f})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
