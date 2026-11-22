from __future__ import annotations

import hashlib
import json
import math
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from ..runtime_store import (
    AUDIT,
    TEST_MANIFEST,
    WORKFLOW,
    get_section,
    set_section,
    update_section,
)
from ..utils.checker_judge import checker_exe_path
from ..utils.communication_runner import (
    is_safe_stem,
    resolve_communicator_binary,
    resolve_communicator_source_existing,
    resolve_communicator_source_rel,
    resolve_grader_source_existing,
    resolve_participant_binaries,
    safe_resolve_under_root,
)
from ..utils.exporters import verify_exported_packages
from ..utils.platform import get_exe_extension
from ..workflow import (
    check_gates,
    current_platform,
    inspect_workspace_state,
    load_manifest,
    manifest_is_communication,
    manifest_is_interactive,
    manifest_uses_protocol_judge,
    manifest_uses_testlib_checker,
)
from ..workflow.guard import signal_satisfied as _guard_signal_satisfied
from ..workflow.models import (
    AuditGates,
    AutoCodeManifest,
    ProtocolConfig,
    QualityGates,
    SolutionEntry,
)
from .base import Tool, ToolResult, input_schema_from_model
from .complexity import (
    _higher_complexity,
    estimate_static_complexity,
    extract_claimed_complexity,
    extract_constraint_numbers,
    extract_n_max_from_constraints,
    run_empirical_verification,
)
from .schemas import ProblemAuditInput
from .test_verify import ProblemVerifyTestsTool

_MAX_INT_LITERAL_DIGITS = 30
_MAX_EXPR_ABS = 10**30


def _strip_outer_parens(expr: str) -> str:
    """Strip outer parentheses only when the opening '(' matches the final ')'."""
    s = expr.strip()
    while len(s) >= 2 and s[0] == "(" and s[-1] == ")":
        depth = 0
        wraps_all = True
        for idx, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and idx < len(s) - 1:
                    wraps_all = False
                    break
        if wraps_all and depth == 0:
            s = s[1:-1].strip()
        else:
            break
    return s


def _safe_int_token(token: str) -> int | None:
    stripped = token.strip()
    digits = stripped[1:] if stripped.startswith(("+", "-")) else stripped
    if not digits or len(digits) > _MAX_INT_LITERAL_DIGITS or not digits.isdigit():
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def _split_top_level_op(expr: str, ops: str) -> tuple[str, str, str] | None:
    """Find the rightmost top-level binary operator in ``ops`` (outside parens, skipping unary/sci)."""
    depth = 0
    for idx in range(len(expr) - 1, -1, -1):
        ch = expr[idx]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
        elif depth == 0 and ch in ops:
            left = expr[:idx].rstrip()
            right = expr[idx + 1 :].lstrip()
            if not left or not right:
                continue
            # Ignore exponent sign in scientific notation (e.g. 1e+9, 1E-3)
            if ch in "+-" and left[-1] in "eE" and len(left) >= 2 and (left[-2].isdigit() or left[-2] == "."):
                continue
            # Ignore unary sign right after another operator
            if ch in "+-" and left[-1] in "+-*/<(&|,":
                continue
            return left, ch, right
    return None


def _parse_cpp_int_expr(expr: str, constants: dict[str, int], *, _depth: int = 0) -> int | None:
    """Safely parse common C++ integer constant expressions without eval()."""
    if _depth > 8:
        return None
    s = _strip_outer_parens(expr)
    if not s or len(s) > 128:
        return None
    if s in constants:
        return constants[s]
    # Handle C++ digit separators (e.g. 1'000'000'000LL)
    cleaned = s.replace("'", "")
    # Top-level binary + / - (lowest precedence among supported ops)
    add_split = _split_top_level_op(cleaned, "+-")
    if add_split is not None:
        left_s, op, right_s = add_split
        left = _parse_cpp_int_expr(left_s, constants, _depth=_depth + 1)
        right = _parse_cpp_int_expr(right_s, constants, _depth=_depth + 1)
        if left is not None and right is not None:
            res = left + right if op == "+" else left - right
            return res if abs(res) <= _MAX_EXPR_ABS else None
    # Top-level binary *
    mul_split = _split_top_level_op(cleaned, "*")
    if mul_split is not None:
        left_s, _, right_s = mul_split
        left = _parse_cpp_int_expr(left_s, constants, _depth=_depth + 1)
        right = _parse_cpp_int_expr(right_s, constants, _depth=_depth + 1)
        if left is not None and right is not None and abs(left) <= 10**18 and abs(right) <= 10**18:
            res = left * right
            return res if abs(res) <= _MAX_EXPR_ABS else None
    # Bit shift: base << shift
    m_shift = re.fullmatch(r"(-?\d+)[uUlL]*\s*<<\s*(\d+)[uUlL]*", cleaned)
    if m_shift:
        base = _safe_int_token(m_shift.group(1))
        shift = _safe_int_token(m_shift.group(2))
        if base is not None and shift is not None and 0 <= shift <= 62 and abs(base) <= (1 << 62):
            return base << shift
        return None
    # Scientific notation: 1e9, 2.5e5
    m_sci = re.fullmatch(r"(-?\d{1,12}(?:\.\d{1,12})?)[eE]\+?(\d{1,2})", cleaned)
    if m_sci:
        exp = int(m_sci.group(2))
        if exp <= 18:
            try:
                val = float(m_sci.group(1)) * (10**exp)
            except (ValueError, OverflowError):
                return None
            if math.isfinite(val) and abs(val - round(val)) < 1e-6:
                return int(round(val))
        return None
    m_int = re.fullmatch(r"(-?\d+)[uUlL]*", cleaned)
    if m_int:
        return _safe_int_token(m_int.group(1))
    return None


def _extract_statement_numeric_bounds(content: str) -> set[int]:
    """Extract numeric bounds from Markdown/LaTeX statements (10^k, a\\times 10^k, 2^k, 1e9, commas, ints)."""
    bounds: set[int] = set()
    # Strip fenced code blocks so sample inputs/outputs do not mask missing statement bounds
    text = re.sub(r"(?s)```.*?```", " ", content)
    text = re.sub(r"(?s)~~~.*?~~~", " ", text)
    # Normalize LaTeX spacing commands (e.g. 1\,000\,000 -> 1000000, 2\,\times\,10^5 -> 2 \times 10^5)
    text = re.sub(r"(?<=\d)\\[,;:](?=\d)", "", text)
    text = re.sub(r"\\[,;:]", " ", text)

    # 1. a \times 10^{k} or a \cdot 10^k or a * 10^{k} (optionally ± c)
    for m in re.finditer(
        r"(\d{1,12}(?:\.\d{1,12})?)\s*(?:\\times|\\cdot|\*)\s*10\s*\^\s*\{?\s*(\d{1,2})\s*\}?(?:\s*([+-])\s*(\d{1,6}))?",
        text,
    ):
        exp = int(m.group(2))
        if exp <= 18:
            try:
                val = float(m.group(1)) * (10**exp)
            except (ValueError, OverflowError):
                continue
            if math.isfinite(val) and abs(val - round(val)) < 1e-6:
                base_int = int(round(val))
                bounds.add(base_int)
                if m.group(3) and m.group(4):
                    delta = int(m.group(4))
                    bounds.add(base_int + delta if m.group(3) == "+" else base_int - delta)
    # 2. Bare 10^{k} or 10^k (optionally ± c, e.g. 10^9 + 7 or 10^9 - 1)
    for m in re.finditer(
        r"(?<![\d.])10\s*\^\s*\{?\s*(\d{1,2})\s*\}?(?:\s*([+-])\s*(\d{1,6}))?",
        text,
    ):
        exp = int(m.group(1))
        if exp <= 18:
            base = 10**exp
            bounds.add(base)
            if m.group(2) and m.group(3):
                delta = int(m.group(3))
                bounds.add(base + delta if m.group(2) == "+" else base - delta)
    # 3. Powers of 2: 2^{k} or 2^k (optionally ± c)
    for m in re.finditer(
        r"(?<![\d.])2\s*\^\s*\{?\s*(\d{1,2})\s*\}?(?:\s*([+-])\s*(\d{1,6}))?",
        text,
    ):
        exp = int(m.group(1))
        if exp <= 62:
            base = 1 << exp
            bounds.add(base)
            if m.group(2) and m.group(3):
                delta = int(m.group(3))
                bounds.add(base + delta if m.group(2) == "+" else base - delta)
    # 4. Scientific notation: 1e5, 2e5, 1e9
    for m in re.finditer(
        r"(?<![A-Za-z0-9_])(\d{1,12}(?:\.\d{1,12})?)[eE]\+?(\d{1,2})(?![A-Za-z0-9_])",
        text,
    ):
        exp = int(m.group(2))
        if exp <= 18:
            try:
                val = float(m.group(1)) * (10**exp)
            except (ValueError, OverflowError):
                continue
            if math.isfinite(val) and abs(val - round(val)) < 1e-6:
                bounds.add(int(round(val)))
    # 5. Comma-separated numbers: 200,000 / 1,000,000,000
    for m in re.finditer(r"(?<![\d,])\d{1,3}(?:,\d{3}){1,6}(?!\d)", text):
        try:
            bounds.add(int(m.group(0).replace(",", "")))
        except ValueError:
            pass
    # 6. Plain integers
    for m in re.finditer(r"(?<![A-Za-z0-9_])(\d{1,19})(?![A-Za-z0-9_])", text):
        try:
            bounds.add(int(m.group(1)))
        except ValueError:
            pass
    return bounds


def _split_call_args(text: str, open_paren_idx: int) -> list[str] | None:
    """Split a parenthesized C++ call argument list starting at ``open_paren_idx``."""
    if open_paren_idx >= len(text) or text[open_paren_idx] != "(":
        return None
    args: list[str] = []
    cur: list[str] = []
    depth = 0
    in_str = False
    in_char = False
    i = open_paren_idx + 1
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            cur.append(ch)
            if ch == "\\" and i + 1 < n:
                cur.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if in_char:
            cur.append(ch)
            if ch == "\\" and i + 1 < n:
                cur.append(text[i + 1])
                i += 2
                continue
            if ch == "'":
                in_char = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch == "'":
            prev = text[i - 1] if i > 0 else ""
            nxt = text[i + 1] if i + 1 < n else ""
            if prev.isdigit() and nxt.isdigit():
                cur.append(ch)
            else:
                in_char = True
                cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            if depth == 0:
                args.append("".join(cur).strip())
                return args
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    return None


def _extract_validator_bounds(val_path: Path) -> list[dict[str, Any]]:
    """Extract constant upper/lower bounds from testlib inf.readInt/readLong calls in val.cpp."""
    if not val_path.is_file():
        return []
    try:
        src = val_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Strip // line comments and /* */ block comments
    no_comments = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    no_comments = re.sub(r"//[^\n]*", "", no_comments)

    constants: dict[str, int] = {}
    for m in re.finditer(
        r"(?:const|constexpr)\s+(?:unsigned\s+|signed\s+)?(?:long\s+long|long|int|int64_t|int32_t|ll)\s+([A-Za-z_]\w*)\s*=\s*([^;]+);",
        no_comments,
    ):
        parsed = _parse_cpp_int_expr(m.group(2), constants)
        if parsed is not None:
            constants[m.group(1)] = parsed
    for m in re.finditer(r"#\s*define\s+([A-Za-z_]\w*)\s+([^\n]+)", no_comments):
        parsed = _parse_cpp_int_expr(m.group(2), constants)
        if parsed is not None:
            constants[m.group(1)] = parsed

    extracted: list[dict[str, Any]] = []
    for m in re.finditer(r"\binf\.read(?:Int|Long|UnsignedLong)\s*\(", no_comments):
        args = _split_call_args(no_comments, m.end() - 1)
        if not args or len(args) < 2:
            continue
        lo = _parse_cpp_int_expr(args[0], constants)
        hi = _parse_cpp_int_expr(args[1], constants)
        var: str | None = None
        if len(args) >= 3:
            m_var = re.fullmatch(r'"([^"]*)"', args[2].strip())
            if m_var:
                var = m_var.group(1).strip() or None
        if lo is not None or hi is not None:
            extracted.append({"var": var, "min": lo, "max": hi})
    return extracted


def _collect_constraint_entry_ints(value: Any) -> list[int]:
    """Extract numeric bounds declared in a single manifest.constraints entry."""
    nums: list[int] = []
    if isinstance(value, bool):
        return nums
    if isinstance(value, int):
        nums.append(value)
    elif isinstance(value, float):
        if abs(value - round(value)) < 1e-6:
            nums.append(int(round(value)))
    elif isinstance(value, str):
        nums.extend(sorted(_extract_statement_numeric_bounds(value)))
    elif isinstance(value, dict):
        for k in ("min", "max", "lower", "upper"):
            if k in value:
                nums.extend(_collect_constraint_entry_ints(value[k]))
    elif isinstance(value, list):
        for item in value:
            nums.extend(_collect_constraint_entry_ints(item))
    return nums


def _rel_posix(problem_path: Path, raw: str | Path | None) -> str | None:
    if raw is None:
        return None
    p = Path(raw)
    try:
        return p.resolve().relative_to(problem_path.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


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
        standalone: bool = False,
    ) -> ToolResult:
        problem_path = Path(problem_dir)
        if not problem_path.is_dir():
            return ToolResult.fail(f"Problem directory not found: {problem_dir}")

        manifest_inferred = False
        try:
            manifest = load_manifest(str(problem_path))
        except (ValidationError, OSError, ValueError) as exc:
            return ToolResult.fail(f"invalid or unreadable manifest.json: {exc}")
        if manifest is None:
            if standalone:
                manifest = self._infer_standalone_manifest(problem_path)
                manifest_inferred = True
            else:
                return ToolResult.fail("manifest.json not found")

        workflow_state = {} if standalone else (get_section(problem_path, WORKFLOW) or {})
        tests_manifest = get_section(problem_path, TEST_MANIFEST) or {}

        blocking: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        next_actions: list[dict[str, object]] = []

        structural_signals = self._structural_signals(problem_path, manifest, standalone=standalone)
        if manifest_inferred:
            structural_signals["manifest"] = self._signal(
                True, {"message": "inferred in standalone mode (no .autocode/manifest.json)"}
            )
        optional_inferred_gates = {"tutorial_path", "statement_path", "main_solution_source"}
        for name, signal in structural_signals.items():
            if not signal["passed"]:
                reason = str(signal["evidence"].get("message", ""))
                if manifest_inferred and name in optional_inferred_gates:
                    warnings.append(
                        {"gate": name, "reason": reason or f"{name} missing in standalone package"}
                    )
                else:
                    blocking.append({"gate": name, "reason": reason})

        verify_signals = workflow_state.get("verify_signals", {})
        if not isinstance(verify_signals, dict):
            verify_signals = {}
        quality_signals: dict[str, dict] = dict(verify_signals)

        has_tests_manifest = bool(isinstance(tests_manifest, dict) and tests_manifest.get("tests"))
        static_quality = self._static_quality_signals(
            problem_path,
            standalone=standalone,
            has_tests_manifest=has_tests_manifest,
        )
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

        if standalone:
            await self._run_standalone_live_checks(
                problem_path=problem_path,
                manifest=manifest,
                tests_manifest=tests_manifest if isinstance(tests_manifest, dict) else {},
                mode=mode,
                quality_signals=quality_signals,
                blocking=blocking,
                warnings=warnings,
            )
        elif mode == "full":
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
            empirical_signal = await self._empirical_complexity_signal(problem_path, manifest)
            quality_signals["empirical_complexity"] = empirical_signal
            if empirical_signal.get("executed") and not empirical_signal.get("passed"):
                evidence = empirical_signal.get("evidence")
                failure_reason = (
                    str(
                        evidence.get("failure_reason")
                        or evidence.get("message")
                        or "empirical complexity verification failed"
                    )
                    if isinstance(evidence, dict)
                    else "empirical complexity verification failed"
                )
                blocking.append(
                    {
                        "gate": "empirical_complexity",
                        "reason": failure_reason,
                    }
                )
                next_actions.append(
                    {
                        "tool_name": "solution_analyze",
                        "tool": "solution_analyze",
                        "action": "verify_empirical_complexity",
                        "recommended_arguments": {
                            "problem_dir": str(problem_path),
                            "solution_type": "sol",
                        },
                        "arguments": {
                            "problem_dir": str(problem_path),
                            "solution_type": "sol",
                        },
                        "priority": "high",
                    }
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
            next_actions.append(
                {
                    "tool_name": "problem_validate",
                    "tool": "problem_validate",
                    "action": "check_statement_consistency",
                    "recommended_arguments": {"problem_dir": str(problem_path)},
                    "arguments": {"problem_dir": str(problem_path)},
                    "priority": "high",
                }
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

        communication_metrics = self._communication_metrics(manifest, quality_signals)
        if mode == "full" and communication_metrics.get("budget_exceeded"):
            blocking.append(
                {
                    "gate": "communication_budget",
                    "reason": str(
                        communication_metrics.get(
                            "budget_violation_detail", "communication budget exceeded"
                        )
                    ),
                }
            )

        warnings.extend(self._host_performance_bias_warnings(manifest, quality_signals))

        if not standalone:
            ws_state = inspect_workspace_state(str(problem_path))
            if ws_state.get("platform_mismatch") or ws_state.get("stale_reasons"):
                drift_reason = (
                    "; ".join(ws_state.get("stale_reasons", []))
                    or "workspace build state drift detected"
                )
                if ws_state.get("platform_mismatch") and mode == "full":
                    blocking.append({"gate": "state_drift", "reason": drift_reason})
                else:
                    warnings.append({"gate": "state_drift", "reason": drift_reason})

        # Verify exported packages (Polygon / Hydro / QOJ) if present on disk
        has_exports = (
            (problem_path / "problem.xml").is_file()
            or (problem_path / "exports" / "hydro").exists()
            or (problem_path / "exports" / "qoj").exists()
            or (problem_path / "exports" / "hydro.zip").is_file()
            or (problem_path / "exports" / "qoj.zip").is_file()
        )
        if has_exports:
            tests_dir_for_exp = self._resolve_tests_dir(problem_path, standalone=standalone)
            exp_test_count = (
                len(list(tests_dir_for_exp.glob("*.in")))
                if tests_dir_for_exp is not None
                else None
            )
            raw_packaged_tl = workflow_state.get("packaged_time_limit_ms")
            packaged_tl = (
                int(raw_packaged_tl)
                if isinstance(raw_packaged_tl, int)
                and not isinstance(raw_packaged_tl, bool)
                and raw_packaged_tl > 0
                else None
            )
            expected_export_tl = packaged_tl if packaged_tl is not None else manifest.time_limit_ms
            if packaged_tl is not None and packaged_tl != manifest.time_limit_ms:
                warnings.append(
                    {
                        "gate": "exported_packages",
                        "reason": (
                            f"exported package uses overridden time_limit_ms={packaged_tl} "
                            f"(manifest.time_limit_ms={manifest.time_limit_ms})"
                        ),
                    }
                )
            pkg_check = verify_exported_packages(
                str(problem_path),
                expected_time_limit_ms=expected_export_tl,
                expected_memory_limit_mb=manifest.memory_limit_mb,
                expected_test_count=exp_test_count or None,
            )
            quality_signals["exported_packages"] = {
                "executed": True,
                "passed": bool(pkg_check.get("passed")),
                "evidence": pkg_check,
            }
            for w_msg in pkg_check.get("warnings", []):
                warnings.append({"gate": "exported_packages", "reason": str(w_msg)})
            if not pkg_check.get("passed"):
                err_reason = "; ".join(str(e) for e in pkg_check.get("errors", [])) or "exported package verification failed"
                if mode == "full":
                    blocking.append({"gate": "exported_packages", "reason": err_reason})
                else:
                    warnings.append({"gate": "exported_packages", "reason": err_reason})

        decision = "go" if not blocking else "no_go"
        report = {
            "decision": decision,
            "mode": mode,
            "standalone": standalone,
            "manifest_inferred": manifest_inferred,
            "problem_dir": str(problem_path.resolve()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "blocking_issues": blocking,
            "risk_report": {
                "warnings": warnings,
                "statement_consistency": statement_consistency,
                "package_format": "Standalone/Polygon" if manifest_inferred else "AutoCode/Polygon",
            },
            "structural_signals": structural_signals,
            "quality_signals": quality_signals,
            "difficulty_signals": difficulty_signals,
            "communication_metrics": communication_metrics,
            "next_actions": next_actions,
        }

        resolved_report_path = self._write_report(problem_path, report, report_path)
        if resolved_report_path:
            report["report_path"] = str(resolved_report_path)
        if mode == "full" and not standalone:
            self._write_audit_state(problem_path, report)

        return ToolResult.ok(**report)

    @staticmethod
    def _safe_file(problem_path: Path, rel: str) -> Path | None:
        resolved = safe_resolve_under_root(str(problem_path), rel)
        if resolved is None:
            return None
        p = Path(resolved)
        return p if p.is_file() else None

    @staticmethod
    def _safe_dir(problem_path: Path, rel: str) -> Path | None:
        resolved = safe_resolve_under_root(str(problem_path), rel)
        if resolved is None:
            return None
        p = Path(resolved)
        return p if p.is_dir() else None

    @classmethod
    def _resolve_tests_dir(cls, problem_path: Path, *, standalone: bool = False) -> Path | None:
        default_dir = cls._safe_dir(problem_path, "tests")
        if not standalone or default_dir is not None:
            return default_dir
        for alt_name in ("testdata", "data"):
            alt_dir = cls._safe_dir(problem_path, alt_name)
            if alt_dir is not None:
                return alt_dir
        return None

    def _infer_standalone_manifest(self, problem_path: Path) -> AutoCodeManifest:
        """Infer an in-memory manifest for external problem packages without `.autocode/manifest.json`."""
        problem_name = problem_path.resolve().name or "standalone"
        problem_type: Literal["standard", "spj", "interactive", "communication"] = "standard"
        interactive = False
        special_judge = False
        stress_comparison: Literal["exact", "checker"] = "exact"
        oracle_mode: Literal["brute", "reference_solutions", "interactor_only"] = "brute"
        protocol: ProtocolConfig | None = None

        def _first_existing(candidates: tuple[str, ...]) -> str | None:
            for rel in candidates:
                if self._safe_file(problem_path, rel) is not None:
                    return rel
            return None

        if _first_existing(("files/interactor.cpp", "interactor.cpp", "testdata/interactor.cpp")):
            problem_type = "interactive"
            interactive = True
            oracle_mode = "interactor_only"
        elif (
            grader_rel := _first_existing(
                ("files/grader.cpp", "grader.cpp", "testdata/grader.cpp")
            )
        ):
            problem_type = "communication"
            oracle_mode = "interactor_only"
            header_file = _first_existing(
                ("files/problem.h", "problem.h", "testdata/problem.h")
            )
            protocol = ProtocolConfig(
                mode="grader_linked",
                grader_file=grader_rel,
                header_file=header_file,
            )
        elif (
            comm_rel := _first_existing(
                ("files/communicator.cpp", "communicator.cpp", "testdata/communicator.cpp")
            )
        ):
            problem_type = "communication"
            oracle_mode = "interactor_only"
            protocol = ProtocolConfig(
                mode="run_twice",
                communicator_file=comm_rel,
            )
        elif _first_existing(("files/checker.cpp", "chk.cpp", "testdata/checker.cpp")):
            problem_type = "spj"
            special_judge = True
            stress_comparison = "checker"

        statement_path = (
            _first_existing(("statements/README.md", "problem_zh.md", "statement.md", "README.md"))
            or "statements/README.md"
        )
        tutorial_path = (
            _first_existing(("statements/tutorial.md", "tutorial.md"))
            or "statements/tutorial.md"
        )

        solutions: list[SolutionEntry] = []
        if self._safe_file(problem_path, "solutions/sol.cpp") is not None:
            solutions.append(
                SolutionEntry(name="sol", path="solutions/sol.cpp", role="main", expected="pass")
            )
        elif self._safe_file(problem_path, "sol.cpp") is not None:
            solutions.append(
                SolutionEntry(name="sol", path="sol.cpp", role="main", expected="pass")
            )
        if self._safe_file(problem_path, "solutions/brute.cpp") is not None:
            solutions.append(
                SolutionEntry(name="brute", path="solutions/brute.cpp", role="brute", expected="pass")
            )
        sols_dir = self._safe_dir(problem_path, "solutions")
        if sols_dir is not None:
            for wrong_file in sorted(sols_dir.glob("wrong_*.cpp")):
                if (
                    is_safe_stem(wrong_file.stem)
                    and self._safe_file(problem_path, f"solutions/{wrong_file.name}") is not None
                ):
                    solutions.append(
                        SolutionEntry(
                            name=wrong_file.stem,
                            path=f"solutions/{wrong_file.name}",
                            role="wrong",
                            expected="fail",
                        )
                    )

        return AutoCodeManifest(
            problem_name=problem_name,
            problem_type=problem_type,
            interactive=interactive,
            special_judge=special_judge,
            stress_comparison=stress_comparison,
            oracle_mode=oracle_mode,
            protocol=protocol,
            statement_path=statement_path,
            tutorial_path=tutorial_path,
            solutions=solutions,
            quality_gates=QualityGates(
                require_stress_passed=False,
                require_validation_passed=False,
                require_tests_verified=False,
                require_limit_semantics=False,
                require_validator_check=False,
                require_wrong_solution_kill=False,
            ),
            audit_gates=AuditGates(
                require_full_audit=False,
                require_validator_self_test=False,
                require_checker_self_test=False,
                require_interactor_self_test=False,
                require_statement_consistency=False,
            ),
        )

    def _structural_signals(
        self,
        problem_path: Path,
        manifest: AutoCodeManifest,
        *,
        standalone: bool = False,
    ) -> dict[str, dict]:
        statement = self._safe_file(problem_path, manifest.statement_path)
        tutorial = self._safe_file(problem_path, manifest.tutorial_path)
        sol_source = self._solution_source(problem_path, "sol")
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
        tests_dir = self._resolve_tests_dir(problem_path, standalone=standalone)
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
            interactor = self._safe_file(problem_path, "files/interactor.cpp")
            if standalone and interactor is None:
                interactor = self._safe_file(problem_path, "interactor.cpp") or self._safe_file(
                    problem_path, "testdata/interactor.cpp"
                )
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
            checker = self._safe_file(problem_path, "files/checker.cpp")
            if standalone and checker is None:
                checker = self._safe_file(problem_path, "chk.cpp") or self._safe_file(
                    problem_path, "testdata/checker.cpp"
                )
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
            has_max_ms = isinstance(max_ms, int | float) and not isinstance(max_ms, bool)
            has_max_ratio = isinstance(max_ratio, int | float) and not isinstance(max_ratio, bool)
            if (
                exp == "fail"
                and not killed
                and has_max_ms
                and (
                    float(max_ms) >= 0.25 * tl_ms
                    or (
                        has_max_ratio
                        and float(max_ratio) >= 3.0
                        and float(max_ms) >= 50.0
                    )
                )
            ):
                ratio_part = f", wrong/std={max_ratio}x" if has_max_ratio else ""
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

    @staticmethod
    def _find_escaped_entries(problem_path: Path, directory: Path) -> list[str]:
        real_root = str(problem_path.resolve())
        escaped: list[str] = []
        for entry in sorted(directory.iterdir()):
            try:
                entry.resolve().relative_to(real_root)
            except ValueError:
                escaped.append(entry.name)
        return escaped

    def _static_quality_signals(
        self,
        problem_path: Path,
        *,
        standalone: bool = False,
        has_tests_manifest: bool = True,
    ) -> dict[str, dict]:
        tests_dir = self._resolve_tests_dir(problem_path, standalone=standalone)
        if tests_dir is None or self._find_escaped_entries(problem_path, tests_dir):
            return {}
        verifier = ProblemVerifyTestsTool()
        checks: dict[str, dict[str, Any]] = {
            "duplicate_inputs": verifier._check_duplicate_inputs(str(tests_dir)),
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
                "executed": False,
                "passed": False,
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

    async def _run_standalone_live_checks(
        self,
        *,
        problem_path: Path,
        manifest: AutoCodeManifest,
        tests_manifest: dict[str, Any],
        mode: Literal["quick", "full"],
        quality_signals: dict[str, dict],
        blocking: list[dict[str, str]],
        warnings: list[dict[str, str]],
    ) -> None:
        """Run read-only live checks in standalone mode without mutating ``problem_dir``."""
        tests_dir = self._resolve_tests_dir(problem_path, standalone=True)
        if tests_dir is None:
            return

        # Reject symlinked test files escaping problem_dir before reading or running binaries on them.
        escaped_tests = self._find_escaped_entries(problem_path, tests_dir)
        if escaped_tests:
            blocking.append(
                {
                    "gate": "path_containment",
                    "reason": f"test files escape problem_dir via symlink: {escaped_tests[:10]}",
                }
            )
            return

        exe_ext = get_exe_extension()
        escaped_bins: list[str] = []
        for rel_bin in (
            f"files/val{exe_ext}",
            f"files/checker{exe_ext}",
            f"files/interactor{exe_ext}",
            f"interactor{exe_ext}",
            f"solutions/sol{exe_ext}",
            f"sol{exe_ext}",
        ):
            raw_bin = problem_path / rel_bin
            if raw_bin.is_symlink() and self._safe_file(problem_path, rel_bin) is None:
                escaped_bins.append(rel_bin)
        if escaped_bins:
            blocking.append(
                {
                    "gate": "path_containment",
                    "reason": f"executable symlinks escape problem_dir: {escaped_bins}",
                }
            )
            return

        verifier = ProblemVerifyTestsTool()
        is_interactive = manifest_is_interactive(manifest)
        answer_ext = verifier._resolve_answer_ext(str(tests_dir), None, tests_manifest or None)
        if not (isinstance(tests_manifest, dict) and tests_manifest.get("answer_ext")):
            has_ans = any(tests_dir.glob("*.ans"))
            has_out = any(tests_dir.glob("*.out"))
            if has_out and not has_ans:
                answer_ext = ".out"

        file_count = verifier._check_file_count(
            str(tests_dir), answer_ext, is_interactive=is_interactive
        )
        quality_signals["file_count"] = self._wrap_verify_signal(file_count)
        if not file_count.get("passed"):
            blocking.append(
                {
                    "gate": "file_count",
                    "reason": "test input/answer files are missing, unpaired, or non-contiguous",
                }
            )

        no_empty = verifier._check_no_empty(
            str(tests_dir), is_interactive=is_interactive, answer_ext=answer_ext
        )
        quality_signals["no_empty"] = self._wrap_verify_signal(no_empty)
        if not no_empty.get("passed"):
            blocking.append(
                {
                    "gate": "no_empty",
                    "reason": f"empty test files detected: {no_empty.get('empty_files', [])}",
                }
            )

        if mode != "full":
            return

        real_root = str(problem_path.resolve())
        val_exe = self._safe_file(problem_path, f"files/val{exe_ext}")
        if val_exe is not None:
            val_res = await verifier._check_validator(str(problem_path), str(tests_dir), 60)
            quality_signals["validator_check"] = self._wrap_verify_signal(val_res)
            if not val_res.get("skipped") and not val_res.get("passed"):
                blocking.append(
                    {
                        "gate": "validator_check",
                        "reason": "validator rejected one or more test inputs",
                    }
                )

        is_comm = manifest_is_communication(manifest)
        spj_checker = manifest_uses_testlib_checker(manifest)

        judge_ready = True
        if is_interactive:
            judge_ready = (
                self._safe_file(problem_path, f"files/interactor{exe_ext}") is not None
                or self._safe_file(problem_path, f"interactor{exe_ext}") is not None
            )
        elif is_comm:
            comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
            if comm_mode != "grader_linked":
                judge_ready = resolve_communicator_binary(str(problem_path), manifest, exe_ext) is not None
        elif spj_checker:
            chk_bin = checker_exe_path(str(problem_path), exe_ext)
            try:
                Path(chk_bin).resolve().relative_to(real_root)
                judge_ready = Path(chk_bin).is_file()
            except ValueError:
                judge_ready = False

        sol_ready = (
            self._safe_file(problem_path, f"solutions/sol{exe_ext}") is not None
            or self._safe_file(problem_path, f"sol{exe_ext}") is not None
        )
        if (
            not sol_ready
            and is_comm
            and manifest.protocol is not None
            and manifest.protocol.participant_solutions
        ):
            parts = resolve_participant_binaries(str(problem_path), manifest, None, exe_ext)
            sol_ready = bool(parts) and all(Path(p).is_file() for p in parts)

        std_timings_live: dict[str, float] | None = None
        if sol_ready and judge_ready:
            ans_res = await verifier._check_answer_consistency(
                str(problem_path),
                str(tests_dir),
                "sol",
                answer_ext,
                60,
                verify_with_checker=spj_checker,
                manifest=manifest,
                use_system_temp=True,
            )
            quality_signals["answer_consistency"] = self._wrap_verify_signal(ans_res)
            if isinstance(ans_res.get("per_test_timings"), list):
                std_timings_live = {
                    str(item["file"]): float(item["time_ms"])
                    for item in ans_res["per_test_timings"]
                    if isinstance(item, dict)
                    and isinstance(item.get("file"), str)
                    and isinstance(item.get("time_ms"), int | float)
                }
            if not ans_res.get("passed"):
                blocking.append(
                    {
                        "gate": "answer_consistency",
                        "reason": str(
                            ans_res.get("error")
                            or "solution output inconsistent with test answers/judge"
                        ),
                    }
                )
        else:
            warnings.append(
                {
                    "gate": "answer_consistency",
                    "reason": "compiled solution or judge binary not found; skipped live answer_consistency in standalone mode",
                }
            )

        compiled_wrongs = [
            s.name
            for s in manifest.solutions
            if s.role == "wrong"
            and is_safe_stem(s.name)
            and self._safe_file(problem_path, f"solutions/{s.name}{exe_ext}") is not None
        ]
        if compiled_wrongs and judge_ready:
            wrong_res = await verifier._check_wrong_solution_kill(
                problem_dir=str(problem_path),
                tests_dir=str(tests_dir),
                wrong_solution_names=compiled_wrongs,
                answer_ext=answer_ext,
                timeout=60,
                verify_with_checker=spj_checker,
                manifest=manifest,
                use_system_temp=True,
                std_timings=std_timings_live,
            )
            quality_signals["wrong_solution_kill"] = self._wrap_verify_signal(wrong_res)
            if not wrong_res.get("passed"):
                blocking.append(
                    {
                        "gate": "wrong_solution_kill",
                        "reason": str(wrong_res.get("message") or "wrong solution survived all tests"),
                    }
                )

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
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": "run_tests_verification",
                        "recommended_arguments": {},
                        "arguments": {},
                        "priority": "high",
                    }
                )
            else:
                verify_type = "validator" if issue.gate == "validator_check" else issue.gate
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": f"verify_{verify_type}",
                        "recommended_arguments": {"verify_types": [verify_type]},
                        "arguments": {"verify_types": [verify_type]},
                        "priority": "high",
                    }
                )

    @staticmethod
    def _wrap_verify_signal(raw: dict[str, Any]) -> dict[str, Any]:
        executed = not bool(raw.get("skipped", False))
        return {
            "executed": executed,
            "passed": bool(raw.get("passed")) if executed else False,
            "evidence": raw if executed else {},
        }

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
        is_interactive = manifest_is_interactive(manifest)
        is_communication = manifest_is_communication(manifest)
        uses_protocol = manifest_uses_protocol_judge(manifest)
        comm_needs_communicator = is_communication and (
            manifest.protocol is None or manifest.protocol.mode != "grader_linked"
        )
        if uses_protocol:
            quality_signals["validator_self_test"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "validator self-test is not applicable for protocol problems"},
            }
        elif manifest.audit_gates.require_validator_self_test:
            signal = quality_signals.get("validator_self_test", {})
            if not self._signal_satisfied(signal):
                tests_dir = problem_path / "tests"
                if tests_dir.is_dir():
                    raw = await verifier._check_validator_self_test(
                        str(problem_path), str(tests_dir), 60
                    )
                else:
                    raw = {"passed": False, "skipped": True, "message": "tests/ directory not found"}
                data = self._wrap_verify_signal(raw)
                quality_signals["validator_self_test"] = data
                if not self._signal_satisfied(data):
                    blocking.append(
                        {"gate": "validator_self_test", "reason": "validator negative fixtures missing or failed"}
                    )
                    next_actions.append(
                        {
                            "tool_name": "problem_verify_tests",
                            "tool": "problem_verify_tests",
                            "action": "verify_validator_self_test",
                            "recommended_arguments": {"verify_types": ["validator_self_test"]},
                            "arguments": {"verify_types": ["validator_self_test"]},
                            "priority": "high",
                        }
                    )

        if not manifest_uses_testlib_checker(manifest):
            quality_signals["checker_self_test"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "checker self-test is not applicable when stress_comparison != checker"},
            }
        elif manifest.audit_gates.require_checker_self_test:
            raw = await verifier._check_checker_self_test(str(problem_path), 60)
            signal = self._wrap_verify_signal(raw)
            quality_signals["checker_self_test"] = signal
            if not self._signal_satisfied(signal):
                blocking.append({"gate": "checker_self_test", "reason": "checker scenarios missing or failed"})
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": "verify_checker_self_test",
                        "recommended_arguments": {"verify_types": ["checker_self_test"]},
                        "arguments": {"verify_types": ["checker_self_test"]},
                        "priority": "high",
                    }
                )

        if not (is_interactive or comm_needs_communicator):
            quality_signals["interactor_self_test"] = {
                "applicable": False,
                "status": "not_applicable",
                "executed": False,
                "passed": True,
                "evidence": {"reason": "interactor self-test is not applicable for non-interactor problems"},
            }
        elif manifest.audit_gates.require_interactor_self_test:
            raw = await verifier._check_interactor_self_test(
                str(problem_path), 60, manifest=manifest
            )
            signal = self._wrap_verify_signal(raw)
            quality_signals["interactor_self_test"] = signal
            if not self._signal_satisfied(signal):
                blocking.append({"gate": "interactor_self_test", "reason": "interactor scenarios missing or failed"})
                next_actions.append(
                    {
                        "tool_name": "problem_verify_tests",
                        "tool": "problem_verify_tests",
                        "action": "verify_interactor_self_test",
                        "recommended_arguments": {"verify_types": ["interactor_self_test"]},
                        "arguments": {"verify_types": ["interactor_self_test"]},
                        "priority": "high",
                    }
                )

    async def _empirical_complexity_signal(
        self, problem_path: Path, manifest: AutoCodeManifest
    ) -> dict[str, Any]:
        sol_source = self._solution_source(problem_path, "sol")
        claimed_complexity: str | None = None
        if (
            manifest.empirical_complexity is not None
            and isinstance(manifest.empirical_complexity.claimed_complexity, str)
            and manifest.empirical_complexity.claimed_complexity.strip()
        ):
            claimed_complexity = manifest.empirical_complexity.claimed_complexity.strip()

        workflow_state = get_section(problem_path, WORKFLOW) or {}
        if not claimed_complexity and isinstance(workflow_state, dict):
            saved_claimed = workflow_state.get("std_claimed_complexity")
            if isinstance(saved_claimed, str) and saved_claimed.strip():
                claimed_complexity = saved_claimed.strip()

        sol_bytes: bytes | None = None
        if sol_source and sol_source.is_file():
            try:
                sol_bytes = sol_source.read_bytes()
            except OSError:
                sol_bytes = None
            if not claimed_complexity and sol_bytes is not None:
                code = sol_bytes.decode("utf-8", errors="replace")
                claimed_complexity = extract_claimed_complexity(code)

        constraints: dict[str, Any] = {"time_limit_ms": float(manifest.time_limit_ms)}
        n_max = extract_n_max_from_constraints(manifest.constraints)
        if n_max is not None:
            constraints["n_max"] = n_max

        # Deterministic cache key: (sol/gen/protocol hashes, compiler, empirical_cfg, claimed_complexity, platform)
        cache_key: str | None = None
        gen_path = self._safe_file(problem_path, "files/gen.cpp")
        if sol_bytes is not None and gen_path is not None:
            try:
                gen_bytes = gen_path.read_bytes()
                proto_rels: list[str] = [
                    "files/grader.cpp",
                    "files/problem.h",
                    "files/interactor.cpp",
                    "files/communicator.cpp",
                ]
                if manifest.protocol is not None:
                    for extra_rel in (
                        manifest.protocol.grader_file,
                        manifest.protocol.header_file,
                        manifest.protocol.communicator_file,
                    ):
                        if extra_rel and extra_rel not in proto_rels:
                            proto_rels.append(extra_rel)
                proto_hashes: dict[str, str] = {}
                for rel in proto_rels:
                    pf = self._safe_file(problem_path, rel)
                    if pf is not None:
                        proto_hashes[rel] = hashlib.sha256(pf.read_bytes()).hexdigest()
                cfg_json = json.dumps(
                    {
                        "emp": manifest.empirical_complexity.model_dump(mode="json")
                        if manifest.empirical_complexity
                        else {},
                        "constraints": constraints,
                        "claimed": claimed_complexity,
                        "compiler": manifest.compiler,
                        "protocol_hashes": proto_hashes,
                        "platform": current_platform(),
                    },
                    sort_keys=True,
                )
                cache_key = hashlib.sha256(
                    hashlib.sha256(sol_bytes).hexdigest().encode()
                    + hashlib.sha256(gen_bytes).hexdigest().encode()
                    + cfg_json.encode()
                ).hexdigest()
            except OSError:
                cache_key = None

        exe_ext = get_exe_extension()
        gen_bin_ready = self._safe_file(problem_path, f"files/gen{exe_ext}") is not None
        sol_bin_ready = self._safe_file(problem_path, f"solutions/sol{exe_ext}") is not None
        if (
            not sol_bin_ready
            and manifest_is_communication(manifest)
            and manifest.protocol is not None
            and manifest.protocol.participant_solutions
        ):
            parts = resolve_participant_binaries(str(problem_path), manifest, None, exe_ext)
            sol_bin_ready = bool(parts) and all(Path(p).is_file() for p in parts)

        if cache_key and gen_bin_ready and sol_bin_ready and isinstance(workflow_state, dict):
            emp_cache = workflow_state.get("empirical_cache")
            if (
                isinstance(emp_cache, dict)
                and emp_cache.get("key") == cache_key
                and isinstance(emp_cache.get("signal"), dict)
                and bool(emp_cache["signal"].get("passed"))
            ):
                cached_sig = dict(emp_cache["signal"])
                ev = cached_sig.get("evidence")
                if isinstance(ev, dict):
                    cached_sig["evidence"] = {**ev, "cached": True}
                return cached_sig

        res = await run_empirical_verification(
            str(problem_path),
            "sol",
            claimed_complexity,
            constraints,
        )

        if res.get("status") in ("pending_generator", "pending_interactor", "skipped"):
            return {
                "executed": False,
                "passed": True,
                "evidence": res,
            }

        passed = bool(res.get("passed"))
        signal = {
            "executed": True,
            "passed": passed,
            "evidence": res,
        }
        if cache_key and passed:
            update_section(
                str(problem_path),
                WORKFLOW,
                {"empirical_cache": {"key": cache_key, "signal": signal}},
            )
        return signal

    def _statement_consistency(
        self, problem_path: Path, manifest: AutoCodeManifest, tests_manifest: dict
    ) -> dict[str, object]:
        statement_path = self._safe_file(problem_path, manifest.statement_path)
        tutorial_path = self._safe_file(problem_path, manifest.tutorial_path)
        if statement_path is None:
            return {"passed": False, "needs_human_review": False, "message": "statement file missing"}
        content = statement_path.read_text(encoding="utf-8", errors="replace")
        stmt_bounds = _extract_statement_numeric_bounds(content)

        missing_terms: list[str] = []
        for key, val in manifest.constraints.items():
            if not key:
                continue
            entry_ints = _collect_constraint_entry_ints(val)
            if entry_ints:
                upper = max(entry_ints)
                if upper not in stmt_bounds:
                    missing_terms.append(f"{key} (expected bound {upper})")
            elif key not in content:
                missing_terms.append(key)

        val_path = self._safe_file(problem_path, "files/val.cpp")
        val_bounds = _extract_validator_bounds(val_path) if val_path is not None else []
        manifest_nums = set(extract_constraint_numbers(manifest.constraints))
        validator_bound_mismatches: list[dict[str, Any]] = []
        for item in val_bounds:
            hi = item.get("max")
            if isinstance(hi, int) and hi >= 10:
                if (
                    hi not in stmt_bounds
                    and (hi + 1) not in stmt_bounds
                    and (not manifest_nums or (hi not in manifest_nums and (hi + 1) not in manifest_nums))
                ):
                    validator_bound_mismatches.append(item)

        has_samples = "样例" in content or "sample" in content.lower()
        has_tests_manifest = bool(tests_manifest.get("tests"))
        tutorial_ready = tutorial_path is not None and len(
            tutorial_path.read_text(encoding="utf-8", errors="replace").strip()
        ) > 80
        passed = has_samples and tutorial_ready
        needs_human_review = bool(missing_terms or validator_bound_mismatches or not passed)
        return {
            "passed": passed,
            "needs_human_review": needs_human_review,
            "message": "" if passed else "statement samples or tutorial evidence is incomplete",
            "has_samples": has_samples,
            "tutorial_ready": tutorial_ready,
            "tests_manifest_present": has_tests_manifest,
            "constraint_terms_missing_from_statement": missing_terms,
            "statement_numeric_bounds": sorted(stmt_bounds)[:50],
            "validator_bounds_checked": len(val_bounds),
            "validator_bound_mismatches": validator_bound_mismatches,
        }

    def _difficulty_signals(
        self,
        problem_path: Path,
        manifest: AutoCodeManifest,
        quality_signals: dict[str, dict],
    ) -> dict[str, object]:
        sol_source = self._solution_source(problem_path, "sol")
        code = sol_source.read_text(encoding="utf-8", errors="replace") if sol_source else ""
        complexity, tags = estimate_static_complexity(code) if code else ("unknown", [])

        empirical_signal = quality_signals.get("empirical_complexity", {})
        if self._signal_satisfied(empirical_signal):
            emp_ev = empirical_signal.get("evidence", {})
            emp_verdict = emp_ev.get("verdict")
            fitted_comp = emp_ev.get("fitted_complexity")
            r2 = emp_ev.get("r_squared")
            low_snr_noise = (
                emp_verdict in ("verified_within_noise_floor", "verified_with_cache_jump")
                or (isinstance(r2, int | float) and float(r2) < 0.5)
                or (
                    fitted_comp == "O(1)"
                    and complexity not in ("unknown", "O(1)", "O(log n)")
                )
            )
            if fitted_comp and not low_snr_noise:
                complexity = str(fitted_comp)

        constraint_numbers = self._constraint_numbers(manifest.constraints)
        raw_largest = max(constraint_numbers) if constraint_numbers else None
        n_max = extract_n_max_from_constraints(manifest.constraints)
        if n_max is None:
            n_max = raw_largest
        largest_value_bound = raw_largest if (raw_largest and n_max and raw_largest > n_max) else None
        wrong_count = sum(1 for s in manifest.solutions if s.role == "wrong")
        limit_ratio = self._limit_ratio(problem_path)

        base = {
            "unknown": 1200,
            "O(1)": 800,
            "O(log n)": 900,
            "O(n)": 1100,
            "O(n log n)": 1400,
            "O(n sqrt n)": 1550,
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
        if n_max or constraint_numbers:
            confidence += 0.15
        if self._signal_satisfied(quality_signals.get("answer_consistency", {})):
            confidence += 0.1
        if self._signal_satisfied(quality_signals.get("limit_semantics", {})):
            confidence += 0.1
        if self._signal_satisfied(empirical_signal):
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
            "report_path": _rel_posix(problem_path, report.get("report_path")),
            "blocking_issue_count": len(report["blocking_issues"]),
            "quality_signals": report["quality_signals"],
        }
        full_audit_passed = report["decision"] == "go" and report["mode"] == "full"
        set_section(str(problem_path), AUDIT, {"full_audit": full_audit, "full_audit_passed": full_audit_passed})

    def _solution_source(self, problem_path: Path, name: str) -> Path | None:
        if not is_safe_stem(name):
            return None
        for rel in (f"solutions/{name}.cpp", f"{name}.cpp"):
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
