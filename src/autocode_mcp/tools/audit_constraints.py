"""C++/statement numeric bound parsers and statement-validator consistency mixin."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from ..utils.communication_runner import safe_resolve_under_root
from ..utils.smt_constraints import (
    _MAX_EXPR_ABS as _MAX_EXPR_ABS,
)
from ..utils.smt_constraints import (
    _MAX_INT_LITERAL_DIGITS as _MAX_INT_LITERAL_DIGITS,
)
from ..utils.smt_constraints import (
    audit_manifest_constraints,
    check_constraints_header_drift,
    parse_cpp_header_constants,
    parse_cpp_int_expr,
    safe_int_token,
    split_top_level_op,
    strip_outer_parens,
)
from ..workflow.models import AutoCodeManifest
from .audit_discovery import _AuditDiscoveryMixin
from .complexity import extract_constraint_numbers

# Preserve internal helper names re-exported by tools/audit.py
_strip_outer_parens = strip_outer_parens
_safe_int_token = safe_int_token
_split_top_level_op = split_top_level_op
_parse_cpp_int_expr = parse_cpp_int_expr


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


def _clean_symbolic_cpp_bound(raw: str, constants: dict[str, int]) -> str | None:
    """Normalize a non-constant C++ bound expression into an arithmetic string for SMT parsing."""
    s = strip_outer_parens(raw)
    if not s or len(s) > 128:
        return None
    # Reject function calls (except simple type casts), array indexing, division, ternary, bitwise
    s = re.sub(r"\(\s*(?:long\s+long|long|int|int64_t|int32_t|uint64_t|uint32_t|size_t|ll)\s*\)", " ", s)
    s = s.replace("'", "")
    s = re.sub(r"\b(\d+)[uUlL]+\b", r"\1", s)
    if any(ch in s for ch in ("[", "]", "{", "}", "/", "?", ":", "<", ">", "&", "|", "^", "~")):
        return None
    if re.search(r"[A-Za-z_]\w*\s*\(", s):
        return None
    # Substitute known C++ constants cleanly
    if constants:
        def _sub_const(m: re.Match[str]) -> str:
            ident = m.group(0)
            return str(constants[ident]) if ident in constants else ident

        s = re.sub(r"\b[A-Za-z_]\w*\b", _sub_const, s)
    s = " ".join(s.split())
    if not re.fullmatch(r"[A-Za-z0-9_+\-*() ]+", s):
        return None
    if not re.search(r"\b[A-Za-z_]\w*\b", s):
        return None
    return s


def _extract_validator_bounds(val_path: Path) -> list[dict[str, Any]]:
    """Extract constant and symbolic upper/lower bounds from testlib inf.readInt/readLong calls in val.cpp."""
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
    if re.search(r'#\s*include\s+"(?:files/)?constraints\.h"', no_comments):
        safe_hdr = safe_resolve_under_root(str(val_path.parent), "constraints.h")
        if safe_hdr is not None and Path(safe_hdr).is_file():
            try:
                hdr_text = Path(safe_hdr).read_text(encoding="utf-8", errors="replace")
            except OSError:
                hdr_text = ""
            if hdr_text:
                constants.update(parse_cpp_header_constants(hdr_text))
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
    # 1. Scalar readers: inf.readInt(lo, hi, "var"), inf.readLong(...), inf.readUnsignedLong(...)
    for m in re.finditer(r"\binf\.read(?:Int|Long|UnsignedLong)\s*\(", no_comments):
        args = _split_call_args(no_comments, m.end() - 1)
        if not args or len(args) < 2:
            continue
        lo = _parse_cpp_int_expr(args[0], constants)
        hi = _parse_cpp_int_expr(args[1], constants)
        lo_expr = _clean_symbolic_cpp_bound(args[0], constants) if lo is None else None
        hi_expr = _clean_symbolic_cpp_bound(args[1], constants) if hi is None else None
        var: str | None = None
        if len(args) >= 3:
            m_var = re.fullmatch(r'"([^"]*)"', args[2].strip())
            if m_var:
                var = m_var.group(1).strip() or None
        if lo is not None or hi is not None or lo_expr is not None or hi_expr is not None:
            entry: dict[str, Any] = {"var": var, "min": lo, "max": hi, "source": "read_scalar"}
            if lo_expr is not None:
                entry["min_expr"] = lo_expr
            if hi_expr is not None:
                entry["max_expr"] = hi_expr
            extracted.append(entry)

    # 2. Array readers: inf.readInts(n, lo, hi, "a"), inf.readLongs(n, lo, hi, "a")
    for m in re.finditer(r"\binf\.read(?:Ints|Longs|UnsignedLongs)\s*\(", no_comments):
        args = _split_call_args(no_comments, m.end() - 1)
        if not args or len(args) < 3:
            continue
        lo = _parse_cpp_int_expr(args[1], constants)
        hi = _parse_cpp_int_expr(args[2], constants)
        lo_expr = _clean_symbolic_cpp_bound(args[1], constants) if lo is None else None
        hi_expr = _clean_symbolic_cpp_bound(args[2], constants) if hi is None else None
        var = None
        if len(args) >= 4:
            m_var = re.fullmatch(r'"([^"]*)"', args[3].strip())
            if m_var:
                var = m_var.group(1).strip() or None
        if lo is not None or hi is not None or lo_expr is not None or hi_expr is not None:
            entry_arr: dict[str, Any] = {"var": var, "min": lo, "max": hi, "source": "read_array"}
            if lo_expr is not None:
                entry_arr["min_expr"] = lo_expr
            if hi_expr is not None:
                entry_arr["max_expr"] = hi_expr
            extracted.append(entry_arr)

    # 3. ensuref / ensure range & sum invariants (e.g. ensuref(1 <= sum_n && sum_n <= 200000, ...))
    for m in re.finditer(r"\bensuref?\s*\(", no_comments):
        args = _split_call_args(no_comments, m.end() - 1)
        if not args:
            continue
        cond = args[0]
        for clause in re.split(r"&&", cond):
            clause = _strip_outer_parens(clause)
            m_le = re.fullmatch(r"([A-Za-z_]\w*)\s*(<=|<)\s*(.+)", clause)
            if m_le:
                bound = _parse_cpp_int_expr(m_le.group(3), constants)
                if bound is not None:
                    hi_val = bound if m_le.group(2) == "<=" else bound - 1
                    extracted.append({"var": m_le.group(1), "min": None, "max": hi_val, "source": "ensure"})
                    continue
                sym = _clean_symbolic_cpp_bound(m_le.group(3), constants)
                if sym is not None:
                    sym_hi = sym if m_le.group(2) == "<=" else f"({sym}) - 1"
                    extracted.append({"var": m_le.group(1), "min": None, "max": None, "max_expr": sym_hi, "source": "ensure"})
                    continue
            m_ge = re.fullmatch(r"([A-Za-z_]\w*)\s*(>=|>)\s*(.+)", clause)
            if m_ge:
                bound = _parse_cpp_int_expr(m_ge.group(3), constants)
                if bound is not None:
                    lo_val = bound if m_ge.group(2) == ">=" else bound + 1
                    extracted.append({"var": m_ge.group(1), "min": lo_val, "max": None, "source": "ensure"})
                    continue
                sym = _clean_symbolic_cpp_bound(m_ge.group(3), constants)
                if sym is not None:
                    sym_lo = sym if m_ge.group(2) == ">=" else f"({sym}) + 1"
                    extracted.append({"var": m_ge.group(1), "min": None, "max": None, "min_expr": sym_lo, "source": "ensure"})
                    continue
            m_rev_le = re.fullmatch(r"(.+?)\s*(<=|<)\s*([A-Za-z_]\w*)", clause)
            if m_rev_le:
                bound = _parse_cpp_int_expr(m_rev_le.group(1), constants)
                if bound is not None:
                    lo_val = bound if m_rev_le.group(2) == "<=" else bound + 1
                    extracted.append({"var": m_rev_le.group(3), "min": lo_val, "max": None, "source": "ensure"})
                    continue
                sym = _clean_symbolic_cpp_bound(m_rev_le.group(1), constants)
                if sym is not None:
                    sym_lo = sym if m_rev_le.group(2) == "<=" else f"({sym}) + 1"
                    extracted.append({"var": m_rev_le.group(3), "min": None, "max": None, "min_expr": sym_lo, "source": "ensure"})
                    continue
            m_rev_ge = re.fullmatch(r"(.+?)\s*(>=|>)\s*([A-Za-z_]\w*)", clause)
            if m_rev_ge:
                bound = _parse_cpp_int_expr(m_rev_ge.group(1), constants)
                if bound is not None:
                    hi_val = bound if m_rev_ge.group(2) == ">=" else bound - 1
                    extracted.append({"var": m_rev_ge.group(3), "min": None, "max": hi_val, "source": "ensure"})
                    continue
                sym = _clean_symbolic_cpp_bound(m_rev_ge.group(1), constants)
                if sym is not None:
                    sym_hi = sym if m_rev_ge.group(2) == ">=" else f"({sym}) - 1"
                    extracted.append({"var": m_rev_ge.group(3), "min": None, "max": None, "max_expr": sym_hi, "source": "ensure"})
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



class _AuditConstraintsMixin(_AuditDiscoveryMixin):
    """Statement/validator bound extraction and consistency checks."""

    def _statement_consistency(
        self, problem_path: Path, manifest: AutoCodeManifest, tests_manifest: dict
    ) -> dict[str, object]:
        statement_path = self._safe_file(problem_path, manifest.statement_path)
        tutorial_path = self._safe_file(problem_path, manifest.tutorial_path)
        if statement_path is None:
            return {
                "passed": False,
                "needs_human_review": False,
                "message": "statement file missing",
                "review_reason": "",
            }
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

        val_path = self._find_validator_source(problem_path, standalone=True)
        val_bounds = _extract_validator_bounds(val_path) if val_path is not None else []
        manifest_nums = set(extract_constraint_numbers(manifest.constraints))

        def _parse_manifest_bound_int(raw_v: Any) -> int | None:
            if isinstance(raw_v, bool) or raw_v is None:
                return None
            if isinstance(raw_v, int):
                return raw_v
            if isinstance(raw_v, float):
                if math.isfinite(raw_v) and abs(raw_v - round(raw_v)) < 1e-6:
                    return int(round(raw_v))
                return None
            if isinstance(raw_v, str):
                parsed = _parse_cpp_int_expr(raw_v, {})
                if parsed is not None:
                    return parsed
                nums = sorted(_extract_statement_numeric_bounds(raw_v))
                return nums[-1] if nums else None
            return None

        # Build named constraint map (min, max) by key and aliases for exact variable cross-check
        named_constraints: dict[str, tuple[int | None, int | None]] = {}
        for key, val in manifest.constraints.items():
            if not isinstance(key, str) or not key.strip():
                continue
            lo_c: int | None = None
            hi_c: int | None = None
            aliases: list[str] = [key.strip().lower()]
            if isinstance(val, dict):
                lo_c = _parse_manifest_bound_int(val.get("min", val.get("lower")))
                hi_c = _parse_manifest_bound_int(val.get("max", val.get("upper")))
                raw_aliases = val.get("aliases")
                if isinstance(raw_aliases, list):
                    for a in raw_aliases:
                        if isinstance(a, str) and a.strip():
                            aliases.append(a.strip().lower())
            else:
                parsed_scalar = _parse_manifest_bound_int(val)
                if parsed_scalar is not None:
                    hi_c = parsed_scalar
                    if key.lower().endswith("_max") and len(key) > 4:
                        aliases.append(key[:-4].lower())
            for alias in aliases:
                named_constraints[alias] = (lo_c, hi_c)

        validator_bound_mismatches: list[dict[str, Any]] = []
        for item in val_bounds:
            lo = item.get("min")
            hi = item.get("max")
            var_raw = item.get("var")
            var_key = str(var_raw).strip().lower() if isinstance(var_raw, str) and var_raw.strip() else None
            mismatched_reason: str | None = None

            if var_key and var_key in named_constraints:
                exp_lo, exp_hi = named_constraints[var_key]
                if exp_hi is not None and isinstance(hi, int) and hi != exp_hi:
                    mismatched_reason = f"validator max={hi} != manifest {var_key}.max={exp_hi}"
                elif exp_lo is not None and isinstance(lo, int) and lo != exp_lo:
                    mismatched_reason = f"validator min={lo} != manifest {var_key}.min={exp_lo}"

            if mismatched_reason is None and isinstance(hi, int) and hi >= 10:
                if (
                    hi not in stmt_bounds
                    and (hi + 1) not in stmt_bounds
                    and (not manifest_nums or (hi not in manifest_nums and (hi + 1) not in manifest_nums))
                ):
                    mismatched_reason = f"validator max={hi} not matched in statement or manifest bounds"

            if mismatched_reason is not None:
                validator_bound_mismatches.append({**item, "reason": mismatched_reason})

        smt_report = audit_manifest_constraints(manifest, validator_bounds=val_bounds)
        header_path = self._safe_file(problem_path, "files/constraints.h")
        header_drift = (
            check_constraints_header_drift(header_path, manifest)
            if header_path is not None
            else None
        )
        smt_conflicts = list(smt_report.conflicts)
        has_header_drift = bool(header_drift is not None and header_drift.has_drift)

        has_samples = "样例" in content or "sample" in content.lower()
        has_tests_manifest = bool(tests_manifest.get("tests"))
        tutorial_ready = tutorial_path is not None and len(
            tutorial_path.read_text(encoding="utf-8", errors="replace").strip()
        ) > 80
        evidence_ready = has_samples and tutorial_ready
        passed = (
            evidence_ready
            and not validator_bound_mismatches
            and smt_report.passed
            and not has_header_drift
        )
        needs_human_review = bool(
            missing_terms
            or validator_bound_mismatches
            or not smt_report.passed
            or has_header_drift
            or not passed
        )

        mismatch_parts: list[str] = []
        for mismatch in validator_bound_mismatches:
            var_label = str(mismatch.get("var") or "<unnamed>")
            lo_val = mismatch.get("min")
            hi_val = mismatch.get("max")
            lo_txt = (
                str(lo_val) if isinstance(lo_val, int) and not isinstance(lo_val, bool) else "-inf"
            )
            hi_txt = (
                str(hi_val) if isinstance(hi_val, int) and not isinstance(hi_val, bool) else "+inf"
            )
            reason_txt = str(mismatch.get("reason") or "bound mismatch")
            mismatch_parts.append(f"{var_label} [{lo_txt}, {hi_txt}] ({reason_txt})")

        issue_parts: list[str] = []
        if not evidence_ready:
            issue_parts.append("statement samples or tutorial evidence is incomplete")
        if mismatch_parts:
            mismatch_summary = f"validator bound mismatch(es): {'; '.join(mismatch_parts)}"
            if not manifest_nums and stmt_bounds:
                top_stmt = ", ".join(str(val) for val in sorted(stmt_bounds, reverse=True)[:5])
                mismatch_summary += f" [statement bounds: {top_stmt}]"
            issue_parts.append(mismatch_summary)
        if smt_conflicts:
            smt_summary = "; ".join(c.message for c in smt_conflicts)
            issue_parts.append(f"SMT constraint conflict(s): {smt_summary}")
        if header_drift is not None and header_drift.has_drift:
            issue_parts.append(f"constraints.h drift: {header_drift.summary}")

        review_parts = list(issue_parts)
        if missing_terms:
            review_parts.append(f"missing statement constraint terms: {', '.join(missing_terms)}")

        message = "" if passed else "; ".join(review_parts if missing_terms else issue_parts)
        review_reason = "; ".join(review_parts) if needs_human_review else ""
        return {
            "passed": passed,
            "needs_human_review": needs_human_review,
            "message": message,
            "review_reason": review_reason,
            "has_samples": has_samples,
            "tutorial_ready": tutorial_ready,
            "tests_manifest_present": has_tests_manifest,
            "constraint_terms_missing_from_statement": missing_terms,
            "statement_numeric_bounds": sorted(stmt_bounds)[:50],
            "validator_bounds_checked": len(val_bounds),
            "validator_bound_mismatches": validator_bound_mismatches,
            "smt_constraints": smt_report.to_dict(),
            "constraints_header_drift": (
                header_drift.to_dict()
                if header_drift is not None
                else {
                    "exists": False,
                    "has_drift": False,
                    "mismatches": [],
                    "missing_constants": [],
                    "parsed_constants": {},
                    "expected_constants": {},
                    "summary": "",
                }
            ),
        }
