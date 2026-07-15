"""High-level problem audit tool.

This tool aggregates deterministic evidence from the AutoCode problem package.
It does not call an LLM; the LLM-facing difficulty explanation can consume the
returned signals.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ..runtime_store import AUDIT, TEST_MANIFEST, WORKFLOW, get_section, set_section
from ..workflow import check_gates, load_manifest, manifest_uses_testlib_checker
from ..workflow.guard import signal_satisfied as _guard_signal_satisfied
from ..workflow.models import AutoCodeManifest
from .base import Tool, ToolResult, input_schema_from_model
from .complexity import analyze_loop_complexity, detect_algorithm_patterns
from .schemas import ProblemAuditInput
from .test_verify import ProblemVerifyTestsTool


class ProblemAuditTool(Tool):
    """Build a deterministic audit report for an AutoCode problem package."""

    @property
    def name(self) -> str:
        return "problem_audit"

    @property
    def description(self) -> str:
        return """生成完整验题报告。

        聚合 manifest.json、workflow state、测试数据 manifest、题面/题解/解法文件、
        problem_verify_tests 质量信号和难度评级 signals。工具只收集确定性证据，
        不调用 LLM。
        """

    @property
    def input_schema(self) -> dict:
        return input_schema_from_model(ProblemAuditInput)

    async def execute(
        self,
        problem_dir: str,
        mode: Literal["quick", "full"] = "full",
        include_difficulty: bool = True,
        report_path: str | None = None,
    ) -> ToolResult:
        problem_path = Path(problem_dir)
        if not problem_path.exists():
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        try:
            manifest = load_manifest(str(problem_path))
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")
        if manifest is None:
            return ToolResult.fail("manifest.json not found")

        workflow_state = get_section(problem_path, WORKFLOW) or {}
        tests_manifest = get_section(problem_path, TEST_MANIFEST) or {}

        blocking: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        next_actions: list[dict[str, object]] = []

        structural_signals = self._structural_signals(problem_path, manifest)
        for name, signal in structural_signals.items():
            if not signal["passed"]:
                blocking.append({"gate": name, "reason": str(signal["evidence"].get("message", ""))})

        verify_signals = workflow_state.get("verify_signals", {})
        if not isinstance(verify_signals, dict):
            verify_signals = {}
        quality_signals: dict[str, dict] = dict(verify_signals)

        static_quality = self._static_quality_signals(problem_path)
        quality_signals.update(static_quality)
        duplicate_signal = static_quality.get("duplicate_inputs", {})
        duplicate_evidence = duplicate_signal.get("evidence", {}) if isinstance(duplicate_signal, dict) else {}
        max_duplicate_ratio = manifest.audit_gates.max_duplicate_input_ratio
        if mode == "full":
            if (
                duplicate_signal.get("executed")
                and duplicate_evidence.get("duplicate_ratio", 0.0) > max_duplicate_ratio
            ):
                blocking.append(
                    {
                        "gate": "duplicate_inputs",
                        "reason": "duplicate input ratio exceeds audit_gates.max_duplicate_input_ratio",
                    }
                )
            for signal_name in ("scale_distribution", "purpose_coverage"):
                signal = static_quality.get(signal_name, {})
                if signal.get("executed") and not signal.get("passed"):
                    blocking.append(
                        {
                            "gate": signal_name,
                            "reason": f"quality signal `{signal_name}` not satisfied",
                        }
                    )

        if mode == "full":
            self._require_full_verify_gates(
                manifest=manifest,
                workflow_state=workflow_state,
                quality_signals=quality_signals,
                blocking=blocking,
                next_actions=next_actions,
            )
            await self._require_special_artifact_gates(
                problem_path=problem_path,
                manifest=manifest,
                quality_signals=quality_signals,
                blocking=blocking,
                next_actions=next_actions,
            )

        statement_consistency = self._statement_consistency(problem_path, manifest, tests_manifest)
        if statement_consistency["needs_human_review"]:
            warnings.append(
                {
                    "gate": "statement_consistency",
                    "reason": "natural-language statement/constraint equivalence needs human review",
                }
            )
        if manifest.audit_gates.require_statement_consistency and not statement_consistency["passed"]:
            blocking.append(
                {"gate": "statement_consistency", "reason": str(statement_consistency["message"])}
            )
        elif not statement_consistency["passed"]:
            warnings.append(
                {
                    "gate": "statement_consistency",
                    "reason": str(statement_consistency["message"]),
                }
            )

        difficulty_signals = (
            self._difficulty_signals(problem_path, manifest, quality_signals) if include_difficulty else {}
        )
        if include_difficulty and mode == "full":
            confidence = float(difficulty_signals.get("confidence", 0.0))
            if confidence < manifest.audit_gates.min_difficulty_confidence:
                warnings.append(
                    {
                        "gate": "difficulty_confidence",
                        "reason": "difficulty confidence is below audit_gates.min_difficulty_confidence",
                    }
                )

        decision = "go" if not blocking else "no_go"
        report = {
            "decision": decision,
            "mode": mode,
            "problem_dir": str(problem_path.resolve()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "blocking_issues": blocking,
            "risk_report": {
                "warnings": warnings,
                "statement_consistency": statement_consistency,
                "package_format": "AutoCode/Polygon",
            },
            "structural_signals": structural_signals,
            "quality_signals": quality_signals,
            "difficulty_signals": difficulty_signals,
            "next_actions": next_actions,
        }

        resolved_report_path = self._write_report(problem_path, report, report_path)
        if resolved_report_path:
            report["report_path"] = str(resolved_report_path)
        if mode == "full":
            self._write_audit_state(problem_path, report)

        return ToolResult.ok(**report)

    def _structural_signals(
        self, problem_path: Path, manifest: AutoCodeManifest
    ) -> dict[str, dict]:
        statement = problem_path / manifest.statement_path
        tutorial = problem_path / manifest.tutorial_path
        sol_source = self._solution_source(problem_path, "sol")
        tests_dir = problem_path / "tests"
        tests = sorted(tests_dir.glob("*.in")) if tests_dir.is_dir() else []
        signals = {
            "manifest": self._signal(True, {"message": "manifest.json parsed"}),
            "statement_path": self._signal(statement.is_file(), {"path": str(statement)}),
            "tutorial_path": self._signal(tutorial.is_file(), {"path": str(tutorial)}),
            "main_solution_source": self._signal(
                sol_source is not None,
                {"path": str(sol_source) if sol_source else "solutions/sol.cpp"},
            ),
            "tests_present": self._signal(bool(tests), {"total": len(tests)}),
        }
        if manifest.interactive:
            interactor = problem_path / "files" / "interactor.cpp"
            signals["interactor_source"] = self._signal(interactor.is_file(), {"path": str(interactor)})
        if manifest_uses_testlib_checker(manifest):
            checker = problem_path / "files" / "checker.cpp"
            signals["checker_source"] = self._signal(checker.is_file(), {"path": str(checker)})
        return signals

    def _static_quality_signals(self, problem_path: Path) -> dict[str, dict]:
        tests_dir = problem_path / "tests"
        if not tests_dir.is_dir():
            return {}
        verifier = ProblemVerifyTestsTool()
        checks = {
            "duplicate_inputs": verifier._check_duplicate_inputs(str(tests_dir)),
            "scale_distribution": verifier._check_scale_distribution(str(tests_dir)),
            "purpose_coverage": verifier._check_purpose_coverage(str(tests_dir)),
        }
        return {
            name: {
                "executed": True,
                "passed": bool(result.get("passed")),
                "evidence": result,
            }
            for name, result in checks.items()
        }

    def _require_full_verify_gates(
        self,
        *,
        manifest: AutoCodeManifest,
        workflow_state: dict,
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        next_actions: list[dict[str, object]],
    ) -> None:
        # 验证信号类质量门禁统一委托 workflow.guard.check_gates 判定，
        # 与 problem_pack_polygon 共享同一真值来源；此处仅补充 next_actions 提示。
        for issue in check_gates(manifest, workflow_state, quality_signals):
            blocking.append({"gate": issue.gate, "reason": issue.reason})
            if issue.gate == "tests_verified":
                next_actions.append({"tool": "problem_verify_tests", "arguments": {}})
            else:
                verify_type = "validator" if issue.gate == "validator_check" else issue.gate
                next_actions.append({"tool": "problem_verify_tests", "arguments": {"verify_types": [verify_type]}})

    async def _require_special_artifact_gates(
        self,
        *,
        problem_path: Path,
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        next_actions: list[dict[str, object]],
    ) -> None:
        verifier = ProblemVerifyTestsTool()
        if not manifest.interactive and manifest.audit_gates.require_validator_self_test:
            signal = quality_signals.get("validator_self_test", {})
            if not self._signal_satisfied(signal):
                result = await verifier.execute(
                    problem_dir=str(problem_path),
                    verify_types=["validator_self_test"],
                    enable_limit_ratio=False,
                )
                data = result.data.get("quality_signals", {}).get("validator_self_test", {})
                quality_signals["validator_self_test"] = data
                if not self._signal_satisfied(data):
                    blocking.append(
                        {"gate": "validator_self_test", "reason": "validator negative fixtures missing or failed"}
                    )
                    next_actions.append(
                        {"tool": "problem_verify_tests", "arguments": {"verify_types": ["validator_self_test"]}}
                    )

        if manifest_uses_testlib_checker(manifest) and manifest.audit_gates.require_checker_self_test:
            result = await verifier.execute(
                problem_dir=str(problem_path),
                verify_types=["checker_self_test"],
                enable_limit_ratio=False,
            )
            signal = result.data.get("quality_signals", {}).get("checker_self_test", {})
            quality_signals["checker_self_test"] = signal
            if not self._signal_satisfied(signal):
                blocking.append({"gate": "checker_self_test", "reason": "checker scenarios missing or failed"})
                next_actions.append(
                    {"tool": "problem_verify_tests", "arguments": {"verify_types": ["checker_self_test"]}}
                )

        if manifest.interactive and manifest.audit_gates.require_interactor_self_test:
            result = await verifier.execute(
                problem_dir=str(problem_path),
                verify_types=["interactor_self_test"],
                enable_limit_ratio=False,
            )
            signal = result.data.get("quality_signals", {}).get("interactor_self_test", {})
            quality_signals["interactor_self_test"] = signal
            if not self._signal_satisfied(signal):
                blocking.append({"gate": "interactor_self_test", "reason": "interactor scenarios missing or failed"})
                next_actions.append(
                    {"tool": "problem_verify_tests", "arguments": {"verify_types": ["interactor_self_test"]}}
                )

    def _statement_consistency(
        self, problem_path: Path, manifest: AutoCodeManifest, tests_manifest: dict
    ) -> dict[str, object]:
        statement_path = problem_path / manifest.statement_path
        tutorial_path = problem_path / manifest.tutorial_path
        if not statement_path.is_file():
            return {"passed": False, "needs_human_review": False, "message": "statement file missing"}
        content = statement_path.read_text(encoding="utf-8", errors="replace")
        missing_terms = []
        for key in manifest.constraints:
            if key and key not in content:
                missing_terms.append(key)
        has_samples = "样例" in content or "sample" in content.lower()
        has_tests_manifest = bool(tests_manifest.get("tests"))
        tutorial_ready = tutorial_path.is_file() and len(
            tutorial_path.read_text(encoding="utf-8", errors="replace").strip()
        ) > 80
        passed = has_samples and tutorial_ready
        return {
            "passed": passed,
            "needs_human_review": True,
            "message": "" if passed else "statement samples or tutorial evidence is incomplete",
            "has_samples": has_samples,
            "tutorial_ready": tutorial_ready,
            "tests_manifest_present": has_tests_manifest,
            "constraint_terms_missing_from_statement": missing_terms,
        }

    def _difficulty_signals(
        self,
        problem_path: Path,
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
    ) -> dict[str, object]:
        sol_source = self._solution_source(problem_path, "sol")
        code = sol_source.read_text(encoding="utf-8", errors="replace") if sol_source else ""
        complexity = analyze_loop_complexity(code) if code else "unknown"
        pattern_complexity, tags = detect_algorithm_patterns(code) if code else (None, [])
        if pattern_complexity:
            complexity = self._max_complexity(complexity, pattern_complexity)

        constraint_numbers = self._constraint_numbers(manifest.constraints)
        n_max = max(constraint_numbers) if constraint_numbers else None
        wrong_count = sum(1 for s in manifest.solutions if s.role == "wrong")
        limit_ratio = self._limit_ratio(problem_path)

        base = {
            "unknown": 1200,
            "O(1)": 800,
            "O(log n)": 900,
            "O(n)": 1100,
            "O(n log n)": 1400,
            "O(n^2)": 1700,
            "O(n^3)": 2100,
            "O(2^n)": 2400,
            "O(n!)": 2600,
        }.get(complexity, 1200)
        if n_max and n_max >= 200000:
            base += 100
        if len(tags) >= 2:
            base += 100
        if wrong_count >= 2:
            base += 100
        if limit_ratio is not None and limit_ratio >= 0.5:
            base += 100
        rating = min(3500, max(800, int(round(base / 100) * 100)))

        confidence = 0.35
        if code:
            confidence += 0.25
        if constraint_numbers:
            confidence += 0.15
        if self._signal_satisfied(quality_signals.get("answer_consistency", {})):
            confidence += 0.1
        if self._signal_satisfied(quality_signals.get("limit_semantics", {})):
            confidence += 0.1
        confidence = min(1.0, confidence)

        reasons = [
            f"estimated_complexity={complexity}",
            f"algorithm_tags={tags or ['unknown']}",
        ]
        if n_max:
            reasons.append(f"largest_numeric_constraint={n_max}")
        if wrong_count:
            reasons.append(f"wrong_solution_count={wrong_count}")
        if limit_ratio is not None:
            reasons.append(f"limit_case_ratio={limit_ratio:.2f}")

        return {
            "rating": rating,
            "band": self._difficulty_band(rating),
            "confidence": confidence,
            "estimated_complexity": complexity,
            "algorithm_tags": tags,
            "constraint_scale": {"largest_numeric_constraint": n_max},
            "implementation_evidence": {
                "solution_source": str(sol_source) if sol_source else None,
                "solution_lines": len(code.splitlines()) if code else 0,
            },
            "data_strength": {
                "wrong_solution_count": wrong_count,
                "limit_case_ratio": limit_ratio,
            },
            "reasons": reasons,
            "why_not_lower": "higher complexity/tags/data-strength evidence prevents a lower default rating",
            "why_not_higher": "no calibrated submission statistics are available in v1",
        }

    def _write_report(self, problem_path: Path, report: dict, report_path: str | None) -> Path | None:
        if not report_path:
            return None
        path = Path(report_path)
        if not path.is_absolute():
            path = problem_path / path
        path.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(path)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _write_audit_state(self, problem_path: Path, report: dict) -> None:
        full_audit = {
            "decision": report["decision"],
            "mode": report["mode"],
            "generated_at": report["generated_at"],
            "report_path": report.get("report_path"),
            "blocking_issue_count": len(report["blocking_issues"]),
            "quality_signals": report["quality_signals"],
        }
        full_audit_passed = report["decision"] == "go" and report["mode"] == "full"
        set_section(str(problem_path), AUDIT, {"full_audit": full_audit, "full_audit_passed": full_audit_passed})

    def _solution_source(self, problem_path: Path, name: str) -> Path | None:
        for candidate in (problem_path / "solutions" / f"{name}.cpp", problem_path / f"{name}.cpp"):
            if candidate.is_file():
                return candidate
        return None

    def _limit_ratio(self, problem_path: Path) -> float | None:
        manifest = get_section(problem_path, TEST_MANIFEST)
        if not isinstance(manifest, dict):
            return None
        tests = manifest.get("tests", [])
        if not isinstance(tests, list) or not tests:
            return None
        limit = sum(1 for item in tests if isinstance(item, dict) and str(item.get("type_param")) in {"3", "4"})
        return limit / len(tests)

    def _constraint_numbers(self, constraints: dict) -> list[int]:
        values: list[int] = []
        for value in constraints.values():
            if isinstance(value, int):
                values.append(value)
            elif isinstance(value, dict):
                for key in ("max", "maximum", "max_value", "n_max", "sum_max"):
                    raw = value.get(key)
                    if isinstance(raw, int):
                        values.append(raw)
        return values

    def _max_complexity(self, left: str, right: str) -> str:
        order = ["O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n^2)", "O(n^3)", "O(2^n)", "O(n!)"]
        if left not in order:
            return right
        if right not in order:
            return left
        return order[max(order.index(left), order.index(right))]

    def _difficulty_band(self, rating: int) -> str:
        if rating < 1000:
            return "入门"
        if rating < 1400:
            return "基础"
        if rating < 1800:
            return "中等"
        if rating < 2200:
            return "较难"
        if rating < 2600:
            return "困难"
        return "高难"

    def _signal(self, passed: bool, evidence: dict) -> dict:
        return {"executed": True, "passed": passed, "evidence": evidence}

    def _signal_satisfied(self, signal: object) -> bool:
        return _guard_signal_satisfied(signal)
