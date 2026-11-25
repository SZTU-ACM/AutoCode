"""Structural, difficulty, static quality, and protocol metrics helpers for ``ProblemAuditTool``."""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path
from typing import Any, Literal

from ..runtime_store import TEST_MANIFEST, get_section
from ..utils.communication_runner import (
    is_safe_stem,
    resolve_communicator_source_existing,
    resolve_communicator_source_rel,
    resolve_grader_source_existing,
    safe_resolve_under_root,
)
from ..workflow import (
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_testlib_checker,
)
from ..workflow.guard import signal_satisfied as _guard_signal_satisfied
from .audit_discovery import _AuditDiscoveryMixin
from .complexity import (
    _higher_complexity,
    _strip_cpp_comments_and_strings,
    analyze_cpp_interprocedural,
    estimate_static_complexity,
    extract_constraint_numbers,
    extract_n_max_from_constraints,
)
from .test_verify import ProblemVerifyTestsTool


class _AuditSignalsMixin(_AuditDiscoveryMixin):
    """Structural/quality/difficulty signal assembly and host performance warnings."""

    def _structural_signals(
        self,
        problem_path: Path,
        manifest: AutoCodeManifest,
        *,
        standalone: bool = False,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
    ) -> dict[str, dict]:
        statement = self._safe_file(problem_path, manifest.statement_path)
        tutorial = self._safe_file(problem_path, manifest.tutorial_path)
        sol_source = self._find_main_solution_source(
            problem_path, manifest, standalone=standalone, layout=layout
        )
        has_main_source = sol_source is not None
        main_source_path = str(sol_source) if sol_source else "solutions/sol.cpp"
        if (
            not has_main_source
            and manifest_is_communication(manifest)
            and manifest.protocol is not None
            and manifest.protocol.participant_solutions
        ):
            resolved_parts = [
                safe_resolve_under_root(str(problem_path), f"solutions/{name}.cpp")
                for name in manifest.protocol.participant_solutions
                if is_safe_stem(name)
            ]
            if (
                len(resolved_parts) == len(manifest.protocol.participant_solutions)
                and all(p is not None and Path(p).is_file() for p in resolved_parts)
            ):
                has_main_source = True
                main_source_path = ", ".join(str(p) for p in resolved_parts)
        tests_dir = self._resolve_tests_dir(problem_path, standalone=standalone, layout=layout)
        tests = sorted(tests_dir.glob("*.in")) if tests_dir is not None else []
        signals = {
            "manifest": self._signal(True, {"message": "manifest.json parsed"}),
            "statement_path": self._signal(
                statement is not None,
                {"path": str(statement) if statement is not None else manifest.statement_path},
            ),
            "tutorial_path": self._signal(
                tutorial is not None,
                {"path": str(tutorial) if tutorial is not None else manifest.tutorial_path},
            ),
            "main_solution_source": self._signal(
                has_main_source,
                {"path": main_source_path},
            ),
            "tests_present": self._signal(
                bool(tests),
                {"total": len(tests), "tests_dir": str(tests_dir) if tests_dir is not None else str(problem_path / "tests")},
            ),
        }
        if manifest_is_interactive(manifest):
            interactor = self._find_interactor_source(problem_path, standalone=standalone)
            signals["interactor_source"] = self._signal(
                interactor is not None,
                {"path": str(interactor) if interactor is not None else str(problem_path / "files" / "interactor.cpp")},
            )
        if manifest_is_communication(manifest):
            comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
            if comm_mode == "grader_linked":
                grader_rel = (
                    manifest.protocol.grader_file
                    if (manifest.protocol and manifest.protocol.grader_file)
                    else "files/grader.cpp"
                )
                existing_grader = resolve_grader_source_existing(str(problem_path), manifest)
                signals["grader_source"] = self._signal(
                    existing_grader is not None,
                    {"path": str(problem_path / (existing_grader or grader_rel))},
                )
                if manifest.protocol and manifest.protocol.header_file:
                    hdr_resolved = safe_resolve_under_root(
                        str(problem_path), manifest.protocol.header_file
                    )
                    signals["header_source"] = self._signal(
                        hdr_resolved is not None and Path(hdr_resolved).is_file(),
                        {"path": hdr_resolved or manifest.protocol.header_file},
                    )
            else:
                existing_comm = resolve_communicator_source_existing(str(problem_path), manifest)
                comm_rel = resolve_communicator_source_rel(manifest)
                signals["communicator_source"] = self._signal(
                    existing_comm is not None,
                    {"path": str(problem_path / (existing_comm or comm_rel))},
                )
        if manifest_uses_testlib_checker(manifest):
            checker = self._find_checker_source(problem_path, standalone=standalone)
            signals["checker_source"] = self._signal(
                checker is not None,
                {"path": str(checker) if checker is not None else str(problem_path / "files" / "checker.cpp")},
            )
        return signals

    @staticmethod
    def _host_performance_bias_warnings(
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
    ) -> list[dict[str, str]]:
        """Warn when local CPU speed may mask TLE wrong solutions on target OJ hardware."""
        out: list[dict[str, str]] = []
        wrong_sig = quality_signals.get("wrong_solution_kill", {})
        wrong_ev = wrong_sig.get("evidence", {}) if isinstance(wrong_sig, dict) else {}
        details = wrong_ev.get("details", []) if isinstance(wrong_ev, dict) else []
        if not isinstance(details, list):
            return out

        tl_ms = float(manifest.time_limit_ms) if manifest.time_limit_ms > 0 else 2000.0
        host_tag = f"{sys.platform}/{platform.machine()}"

        for item in details:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "unknown"))
            if item.get("host_speed_warning"):
                out.append(
                    {
                        "gate": "host_performance_bias",
                        "reason": f"[{host_tag}] {item['host_speed_warning']}",
                    }
                )
                continue
            killed = bool(item.get("killed", False))
            exp = str(item.get("expected") or "fail")
            max_ms = item.get("max_time_ms")
            max_ratio = item.get("max_ratio_to_std")
            if (
                exp == "fail"
                and not killed
                and isinstance(max_ms, int | float)
                and not isinstance(max_ms, bool)
                and (
                    float(max_ms) >= 0.25 * tl_ms
                    or (
                        isinstance(max_ratio, int | float)
                        and not isinstance(max_ratio, bool)
                        and float(max_ratio) >= 3.0
                        and float(max_ms) >= 50.0
                    )
                )
            ):
                ratio_part = (
                    f", wrong/std={max_ratio}x"
                    if isinstance(max_ratio, int | float) and not isinstance(max_ratio, bool)
                    else ""
                )
                out.append(
                    {
                        "gate": "host_performance_bias",
                        "reason": (
                            f"[{host_tag}] wrong solution '{name}' survived locally at "
                            f"max_time_ms={max_ms}ms (TL={tl_ms:.0f}ms{ratio_part}); "
                            "fast local CPU may mask TLE on typical OJ hardware."
                        ),
                    }
                )
        return out

    @staticmethod
    def _communication_metrics(
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
    ) -> dict[str, Any]:
        """Aggregate observed communication/protocol metrics and budget utilization."""
        ans_sig = quality_signals.get("answer_consistency", {})
        ans_ev = ans_sig.get("evidence", {}) if isinstance(ans_sig, dict) else {}
        observed = (
            ans_ev.get("protocol_metrics", {})
            if isinstance(ans_ev, dict) and isinstance(ans_ev.get("protocol_metrics"), dict)
            else {}
        )
        protocol = manifest.protocol
        if not manifest_is_communication(manifest) and not observed:
            return {}

        limits: dict[str, int] = {}
        if protocol is not None:
            for field_name in ("max_bits", "max_bytes", "max_rounds", "max_queries"):
                val = getattr(protocol, field_name, None)
                if isinstance(val, int) and not isinstance(val, bool):
                    limits[field_name] = val

        mapping = (
            ("max_bits_used", "max_bits", "bits_ratio"),
            ("max_bytes_used", "max_bytes", "bytes_ratio"),
            ("max_rounds", "max_rounds", "rounds_ratio"),
            ("max_queries", "max_queries", "queries_ratio"),
        )
        utilization: dict[str, float] = {}
        violations: list[str] = []
        for obs_key, lim_key, ratio_key in mapping:
            obs_val = observed.get(obs_key)
            lim_val = limits.get(lim_key)
            if (
                isinstance(obs_val, int)
                and not isinstance(obs_val, bool)
                and isinstance(lim_val, int)
                and not isinstance(lim_val, bool)
                and lim_val > 0
            ):
                utilization[ratio_key] = round(obs_val / lim_val, 4)
                if obs_val > lim_val:
                    violations.append(f"{obs_key}={obs_val} > {lim_key}={lim_val}")

        return {
            "protocol_mode": protocol.mode if protocol else None,
            "observed": observed,
            "limits": limits,
            "utilization": utilization,
            "budget_exceeded": bool(violations),
            "budget_violation_detail": "; ".join(violations),
        }

    def _static_quality_signals(
        self,
        problem_path: Path,
        *,
        standalone: bool = False,
        has_tests_manifest: bool = True,
        layout: Literal["auto", "autocode", "standard_cn", "polygon", "hydro"] = "auto",
        max_near_duplicate_ratio: float = 0.2,
    ) -> dict[str, dict]:
        tests_dir = self._resolve_tests_dir(problem_path, standalone=standalone, layout=layout)
        if tests_dir is None or self._find_escaped_entries(problem_path, tests_dir):
            return {}
        verifier = ProblemVerifyTestsTool()
        checks: dict[str, dict[str, Any]] = {
            "duplicate_inputs": verifier._check_duplicate_inputs(
                str(tests_dir), max_near_ratio=max_near_duplicate_ratio
            ),
            "scale_distribution": verifier._check_scale_distribution(str(tests_dir)),
        }
        out: dict[str, dict] = {
            name: {
                "executed": True,
                "passed": bool(result.get("passed")),
                "evidence": result,
            }
            for name, result in checks.items()
        }
        if standalone and not has_tests_manifest:
            out["purpose_coverage"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {
                    "skipped": True,
                    "reason": "no AutoCode test_manifest present in standalone mode",
                },
            }
        else:
            pc = verifier._check_purpose_coverage(str(tests_dir))
            out["purpose_coverage"] = {
                "executed": True,
                "passed": bool(pc.get("passed")),
                "evidence": pc,
            }
        return out

    @staticmethod
    def _wrap_verify_signal(raw: dict[str, Any]) -> dict[str, Any]:
        executed = not bool(raw.get("skipped", False))
        return {
            "executed": executed,
            "passed": bool(raw.get("passed")) if executed else False,
            "evidence": raw if executed else {},
        }

    def _difficulty_signals(
        self,
        problem_path: Path,
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
    ) -> dict[str, object]:
        """Assemble objective, evidence-only problem difficulty signals without pseudo-Elo tables."""
        sol_source = self._solution_source(problem_path, "sol") or self._find_main_solution_source(
            problem_path, manifest
        )
        code = sol_source.read_text(encoding="utf-8", errors="replace") if sol_source else ""
        static_complexity, tags = estimate_static_complexity(code) if code else ("unknown", [])
        complexity = static_complexity

        empirical_signal = quality_signals.get("empirical_complexity", {})
        empirical_satisfied = self._signal_satisfied(empirical_signal)
        emp_ev = empirical_signal.get("evidence", {}) if isinstance(empirical_signal, dict) else {}
        if not isinstance(emp_ev, dict):
            emp_ev = {}
        emp_verdict = emp_ev.get("verdict")
        fitted_comp = emp_ev.get("fitted_complexity")
        r2 = emp_ev.get("r_squared")
        low_snr_noise = (
            emp_verdict in ("verified_within_noise_floor", "verified_with_cache_jump")
            or (isinstance(r2, int | float) and not isinstance(r2, bool) and float(r2) < 0.5)
            or (
                fitted_comp == "O(1)"
                and static_complexity not in ("unknown", "O(1)", "O(log n)")
            )
        )
        empirical_adopted = bool(empirical_satisfied and fitted_comp and not low_snr_noise)
        if empirical_adopted:
            complexity = str(fitted_comp)

        constraint_numbers = self._constraint_numbers(manifest.constraints)
        raw_largest = max(constraint_numbers) if constraint_numbers else None
        n_max = extract_n_max_from_constraints(manifest.constraints)
        if n_max is None:
            n_max = raw_largest
        largest_value_bound = raw_largest if (raw_largest and n_max and raw_largest > n_max) else None
        wrong_count = sum(1 for s in manifest.solutions if s.role == "wrong")
        limit_ratio = self._limit_ratio(problem_path)
        tl_ms = float(manifest.time_limit_ms) if manifest.time_limit_ms > 0 else 2000.0
        tl_headroom_ratio = self._extract_tl_headroom_ratio(emp_ev, quality_signals, tl_ms)
        wrong_kill_summary = self._summarize_wrong_kill(quality_signals.get("wrong_solution_kill", {}))

        statement_present = self._safe_file(problem_path, manifest.statement_path) is not None
        tutorial_present = self._safe_file(problem_path, manifest.tutorial_path) is not None

        # Confidence measures purely evidence completeness (invariant to wrong_count and complexity value)
        confidence = 0.0
        if code:
            confidence += 0.30
        if n_max or constraint_numbers:
            confidence += 0.15
        if self._signal_satisfied(quality_signals.get("answer_consistency", {})):
            confidence += 0.20
        if self._signal_satisfied(quality_signals.get("limit_semantics", {})):
            confidence += 0.15
        if empirical_satisfied:
            confidence += 0.15
        if statement_present:
            confidence += 0.05
        confidence = round(min(1.0, max(0.0, confidence)), 3)

        cognitive_dimensions = self._build_cognitive_dimensions(
            code=code,
            manifest=manifest,
            tags=tags,
            complexity=complexity,
            static_complexity=static_complexity,
            fitted_complexity=str(fitted_comp) if fitted_comp else None,
            empirical_adopted=empirical_adopted,
            n_max=n_max,
            largest_value_bound=largest_value_bound,
            statement_present=statement_present,
            tutorial_present=tutorial_present,
            wrong_kill_summary=wrong_kill_summary,
        )

        reasons = [
            "rating_method=evidence_only_v2 (uncalibrated; calibration_required=True)",
            f"estimated_complexity={complexity} (static={static_complexity})",
            f"algorithm_tags={tags or ['unknown']}",
        ]
        if n_max is not None:
            reasons.append(f"n_max={n_max}")
        if largest_value_bound is not None:
            reasons.append(f"largest_value_bound={largest_value_bound}")
        if tl_headroom_ratio is not None:
            reasons.append(f"tl_headroom_ratio={tl_headroom_ratio:.4f}")
        if wrong_count:
            reasons.append(f"wrong_solution_count={wrong_count}")
        if limit_ratio is not None:
            reasons.append(f"limit_case_ratio={limit_ratio:.2f}")

        structural_evidence: dict[str, object] = {
            "static_complexity": static_complexity,
            "empirical_complexity": {
                "executed": bool(empirical_signal.get("executed", False))
                if isinstance(empirical_signal, dict)
                else False,
                "passed": bool(empirical_signal.get("passed", False))
                if isinstance(empirical_signal, dict)
                else False,
                "status": emp_ev.get("status"),
                "verdict": emp_verdict,
                "fitted_complexity": fitted_comp,
                "r_squared": r2,
                "fit_metric": emp_ev.get("fit_metric"),
                "adopted": empirical_adopted,
            },
            "n_max": n_max,
            "largest_value_bound": largest_value_bound,
            "time_limit_ms": manifest.time_limit_ms,
            "memory_limit_mb": manifest.memory_limit_mb,
            "tl_headroom_ratio": tl_headroom_ratio,
            "limit_case_ratio": limit_ratio,
            "wrong_solution_count": wrong_count,
            "wrong_kill": wrong_kill_summary,
            "solution_source": str(sol_source) if sol_source else None,
            "solution_lines": len(code.splitlines()) if code else 0,
        }

        return {
            "rating": None,
            "band": "uncalibrated",
            "calibration_required": True,
            "rating_method": "evidence_only_v2",
            "confidence": confidence,
            "estimated_complexity": complexity,
            "algorithm_tags": tags,
            "reasons": reasons,
            "structural_evidence": structural_evidence,
            "cognitive_dimensions": cognitive_dimensions,
            "constraint_scale": {
                "largest_numeric_constraint": n_max,
                **({"largest_value_bound": largest_value_bound} if largest_value_bound else {}),
            },
            "implementation_evidence": {
                "solution_source": str(sol_source) if sol_source else None,
                "solution_lines": len(code.splitlines()) if code else 0,
            },
            "data_strength": {
                "wrong_solution_count": wrong_count,
                "limit_case_ratio": limit_ratio,
            },
        }

    @staticmethod
    def _extract_tl_headroom_ratio(
        emp_ev: dict[str, Any],
        quality_signals: dict[str, dict],
        tl_ms: float,
    ) -> float | None:
        if tl_ms <= 0:
            return None
        direct_ratio = emp_ev.get("max_scale_headroom_ratio")
        if isinstance(direct_ratio, int | float) and not isinstance(direct_ratio, bool):
            return round(float(direct_ratio), 4)
        samples = emp_ev.get("samples")
        if isinstance(samples, list):
            times = [
                float(s["cpu_time_ms"])
                for s in samples
                if isinstance(s, dict)
                and s.get("status") == "ok"
                and isinstance(s.get("cpu_time_ms"), int | float)
                and not isinstance(s.get("cpu_time_ms"), bool)
            ]
            if times:
                return round(max(times) / tl_ms, 4)
        wrong_sig = quality_signals.get("wrong_solution_kill", {})
        wrong_ev = wrong_sig.get("evidence", {}) if isinstance(wrong_sig, dict) else {}
        details = wrong_ev.get("details", []) if isinstance(wrong_ev, dict) else []
        if isinstance(details, list):
            std_times = [
                float(item["std_max_time_ms"])
                for item in details
                if isinstance(item, dict)
                and isinstance(item.get("std_max_time_ms"), int | float)
                and not isinstance(item.get("std_max_time_ms"), bool)
            ]
            if std_times:
                return round(max(std_times) / tl_ms, 4)
        return None

    @staticmethod
    def _summarize_wrong_kill(wrong_sig: object) -> dict[str, Any]:
        if not isinstance(wrong_sig, dict):
            return {
                "executed": False,
                "passed": None,
                "total_checked": 0,
                "killed_count": 0,
                "survived_count": 0,
                "kill_rate": None,
                "details": [],
            }
        executed = bool(wrong_sig.get("executed", False))
        passed = bool(wrong_sig.get("passed")) if executed else None
        ev = wrong_sig.get("evidence", {})
        raw_details = ev.get("details", []) if isinstance(ev, dict) else []
        summarized: list[dict[str, Any]] = []
        killed_count = 0
        if isinstance(raw_details, list):
            for item in raw_details:
                if not isinstance(item, dict):
                    continue
                is_killed = bool(item.get("killed", False))
                if is_killed:
                    killed_count += 1
                summarized.append(
                    {
                        "name": str(item.get("name", "unknown")),
                        "expected": str(item.get("expected") or "fail"),
                        "killed": is_killed,
                        "killing_verdict": item.get("killing_verdict"),
                        "max_time_ms": item.get("max_time_ms"),
                        "max_ratio_to_std": item.get("max_ratio_to_std"),
                    }
                )
        total = len(summarized)
        survived_count = max(0, total - killed_count)
        kill_rate = round(killed_count / total, 4) if total > 0 else None
        return {
            "executed": executed,
            "passed": passed,
            "total_checked": total,
            "killed_count": killed_count,
            "survived_count": survived_count,
            "kill_rate": kill_rate,
            "details": summarized,
        }

    @staticmethod
    def _build_cognitive_dimensions(
        *,
        code: str,
        manifest: AutoCodeManifest,
        tags: list[str],
        complexity: str,
        static_complexity: str,
        fitted_complexity: str | None,
        empirical_adopted: bool,
        n_max: int | None,
        largest_value_bound: int | None,
        statement_present: bool,
        tutorial_present: bool,
        wrong_kill_summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Build LiveCodeBench-Pro-inspired Knowledge / Logic / Observation signal dimensions."""
        ds_tags = {
            "tree_data_structure",
            "fenwick_tree",
            "segment_tree",
            "heap",
            "hash_table",
            "stack_queue",
            "monotonic_stack",
            "monotonic_queue",
            "dsu",
            "bitset",
            "binary_lifting",
        }
        paradigm_tags = {
            "dynamic_programming",
            "bitmask_dp",
            "bitmask",
            "divide_and_conquer",
            "binary_search",
            "sorting",
            "two_pointers",
            "graph_traversal",
            "dijkstra",
            "bfs",
            "dfs",
            "backtracking",
            "permutation_enumeration",
            "recursion",
        }
        cleaned = _strip_cpp_comments_and_strings(code) if code else ""
        summary = (
            analyze_cpp_interprocedural(code, ignore_multitest=True, ignore_small_const=True)
            if code
            else None
        )
        branch_count = len(re.findall(r"\b(?:if|else|switch)\b", cleaned)) if cleaned else 0
        mod_detected = bool(
            re.search(r"\b(?:998244353|1000000007|1'000'000'007|MOD|mod)\b", cleaned)
        )
        requires_64bit = bool(
            (n_max is not None and n_max > 2_147_483_647)
            or (largest_value_bound is not None and largest_value_bound > 2_147_483_647)
        )

        return {
            "knowledge_signals": {
                "algorithm_tags": list(tags),
                "data_structures": [t for t in tags if t in ds_tags],
                "paradigms": [t for t in tags if t in paradigm_tags],
                "requires_64bit_values": requires_64bit,
                "modular_arithmetic_detected": mod_detected,
            },
            "logic_signals": {
                "loop_nesting_depth": summary.max_depth if summary else 0,
                "call_graph_depth": summary.call_graph_depth if summary else 0,
                "function_count": summary.function_count if summary else 0,
                "branch_count": branch_count,
                "solution_lines": len(code.splitlines()) if code else 0,
                "problem_type": manifest.problem_type,
                "has_subtasks": bool(manifest.subtasks),
                "requires_interprocedural_reasoning": bool(
                    summary and summary.call_graph_depth >= 1 and summary.max_depth >= 2
                ),
            },
            "observation_signals": {
                "requires_llm_statement_calibration": True,
                "statement_present": statement_present,
                "tutorial_present": tutorial_present,
                "low_complexity_high_scale": bool(
                    n_max is not None
                    and n_max >= 100_000
                    and complexity in ("O(1)", "O(log n)", "O(n)", "O(n log n)")
                ),
                "wrong_solutions_killed": int(wrong_kill_summary.get("killed_count", 0)),
                "wrong_solutions_survived": int(wrong_kill_summary.get("survived_count", 0)),
                "complexity_divergence": bool(
                    empirical_adopted
                    and static_complexity != "unknown"
                    and fitted_complexity is not None
                    and fitted_complexity != static_complexity
                ),
                "calibration_note": (
                    "Observation depth and mathematical/constructive insight cannot be inferred from "
                    "asymptotic complexity alone; calibrate using statement, editorial, and benchmark anchors."
                ),
            },
        }

    def _solution_source(self, problem_path: Path, name: str) -> Path | None:
        if not is_safe_stem(name):
            return None
        candidates = (
            (f"solutions/{name}.cpp", f"{name}.cpp", "solution/std.cpp", "solutions/std.cpp", "std.cpp")
            if name == "sol"
            else (f"solutions/{name}.cpp", f"solution/{name}.cpp", f"{name}.cpp")
        )
        for rel in candidates:
            found = self._safe_file(problem_path, rel)
            if found is not None:
                return found
        return None

    def _limit_ratio(self, problem_path: Path) -> float | None:
        manifest = get_section(problem_path, TEST_MANIFEST)
        if not isinstance(manifest, dict):
            return None
        tests = manifest.get("tests", [])
        if not isinstance(tests, list) or not tests:
            return None
        limit = sum(
            1
            for item in tests
            if isinstance(item, dict)
            and (
                str(item.get("type_param")) in {"3", "4"}
                or str(item.get("group", "")).lower() in {"limit", "extreme", "tle"}
            )
        )
        return limit / len(tests)

    def _constraint_numbers(self, constraints: dict) -> list[int]:
        return extract_constraint_numbers(constraints)

    def _max_complexity(self, left: str, right: str) -> str:
        return _higher_complexity(left, right)

    def _signal(self, passed: bool, evidence: dict) -> dict:
        return {"executed": True, "passed": passed, "evidence": evidence}

    def _signal_satisfied(self, signal: object) -> bool:
        return _guard_signal_satisfied(signal)
