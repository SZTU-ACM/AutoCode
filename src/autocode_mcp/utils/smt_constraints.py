"""Formal SMT constraint verification, boundary witness synthesis, and single-source constraints.h."""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ..workflow.models import AutoCodeManifest

SolverBackend = Literal["auto", "z3", "python"]
RelOp = Literal["<=", "<", ">=", ">", "==", "!="]
BinOpKind = Literal["+", "-", "*", "%"]

_MAX_INT_LITERAL_DIGITS = 30
_MAX_EXPR_ABS = 10**30
_Z3_TIMEOUT_MS = 2000
_SOLVER_MAX_ITERS = 64
_SOLVER_MAX_BACKTRACK_STATES = 512

_PREDICATE_CONTAINER_KEYS = frozenset(
    {
        "conditions",
        "predicates",
        "exprs",
        "rules",
        "where",
        "condition",
        "predicate",
        "expr",
        "rule",
    }
)
_METADATA_IGNORE_KEYS = frozenset({"note", "notes", "description", "comment", "comments", "aliases"})


# ---------------------------------------------------------------------------
# Shared safe C++ constant expression parser (also used by audit_constraints)
# ---------------------------------------------------------------------------


def strip_outer_parens(expr: str) -> str:
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


def safe_int_token(token: str) -> int | None:
    """Parse a bounded decimal integer token without risking huge string-int DoS."""
    stripped = token.strip()
    digits = stripped[1:] if stripped.startswith(("+", "-")) else stripped
    if not digits or len(digits) > _MAX_INT_LITERAL_DIGITS or not digits.isdigit():
        return None
    try:
        return int(stripped)
    except ValueError:
        return None


def split_top_level_op(expr: str, ops: str) -> tuple[str, str, str] | None:
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
            if ch in "+-" and left[-1] in "eE" and len(left) >= 2 and (left[-2].isdigit() or left[-2] == "."):
                continue
            if ch in "+-" and left[-1] in "+-*/<(&|,":
                continue
            return left, ch, right
    return None


def parse_cpp_int_expr(expr: str, constants: dict[str, int], *, _depth: int = 0) -> int | None:
    """Safely parse common C++ integer constant expressions without ``eval()``."""
    if _depth > 8:
        return None
    s = strip_outer_parens(expr)
    if not s or len(s) > 128:
        return None
    if s in constants:
        return constants[s]
    if "::" in s:
        short_name = s.split("::")[-1].strip()
        if short_name in constants:
            return constants[short_name]
    cleaned = s.replace("'", "")
    add_split = split_top_level_op(cleaned, "+-")
    if add_split is not None:
        left_s, op, right_s = add_split
        left = parse_cpp_int_expr(left_s, constants, _depth=_depth + 1)
        right = parse_cpp_int_expr(right_s, constants, _depth=_depth + 1)
        if left is not None and right is not None:
            res = left + right if op == "+" else left - right
            return res if abs(res) <= _MAX_EXPR_ABS else None
    mul_split = split_top_level_op(cleaned, "*")
    if mul_split is not None:
        left_s, _, right_s = mul_split
        left = parse_cpp_int_expr(left_s, constants, _depth=_depth + 1)
        right = parse_cpp_int_expr(right_s, constants, _depth=_depth + 1)
        if left is not None and right is not None and abs(left) <= 10**18 and abs(right) <= 10**18:
            res = left * right
            return res if abs(res) <= _MAX_EXPR_ABS else None
    if cleaned.startswith(("+", "-")) and len(cleaned) > 1:
        inner = parse_cpp_int_expr(cleaned[1:], constants, _depth=_depth + 1)
        if inner is not None:
            return -inner if cleaned[0] == "-" else inner
    m_shift = re.fullmatch(r"(-?\d+)[uUlL]*\s*<<\s*(\d+)[uUlL]*", cleaned)
    if m_shift:
        base = safe_int_token(m_shift.group(1))
        shift = safe_int_token(m_shift.group(2))
        if base is not None and shift is not None and 0 <= shift <= 62 and abs(base) <= (1 << 62):
            return base << shift
        return None
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
        return safe_int_token(m_int.group(1))
    return None


# ---------------------------------------------------------------------------
# Public report & AST data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstExpr:
    value: int


@dataclass(frozen=True)
class VarExpr:
    name: str


@dataclass(frozen=True)
class UnaryNegExpr:
    operand: ArithExpr


@dataclass(frozen=True)
class BinOpExpr:
    op: BinOpKind
    left: ArithExpr
    right: ArithExpr


ArithExpr = ConstExpr | VarExpr | UnaryNegExpr | BinOpExpr


@dataclass(frozen=True)
class RelPred:
    op: RelOp
    left: ArithExpr
    right: ArithExpr
    source: str
    direction: Literal["lower", "upper", "eq", "neq", "mod", "rel"] = "rel"


@dataclass(frozen=True)
class WitnessVerification:
    name: str
    assignment: dict[str, int]
    valid: bool
    unsatisfied_predicates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConstraintConflict:
    scope: str
    kind: str
    message: str
    variables: tuple[str, ...] = ()
    witness: dict[str, int] | None = None
    unsat_core: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "kind": self.kind,
            "message": self.message,
            "variables": list(self.variables),
            "witness": dict(self.witness) if self.witness is not None else None,
            "unsat_core": list(self.unsat_core),
        }


@dataclass(frozen=True)
class SubtaskAuditReport:
    subtask_id: int
    subtask_name: str | None
    satisfiable: bool
    contained_in_global: bool
    conflicts: tuple[ConstraintConflict, ...]
    witnesses: dict[str, dict[str, int]]
    intervals: dict[str, tuple[int | None, int | None]]
    solver_used: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "subtask_name": self.subtask_name,
            "satisfiable": self.satisfiable,
            "contained_in_global": self.contained_in_global,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "witnesses": {k: dict(v) for k, v in self.witnesses.items()},
            "intervals": {k: [lo, hi] for k, (lo, hi) in self.intervals.items()},
            "solver_used": self.solver_used,
        }


@dataclass(frozen=True)
class SmtConstraintAuditReport:
    solver_used: str
    z3_available: bool
    global_satisfiable: bool
    validator_consistent: bool
    all_subtasks_satisfiable: bool
    all_subtasks_contained: bool
    passed: bool
    conflicts: tuple[ConstraintConflict, ...]
    global_intervals: dict[str, tuple[int | None, int | None]]
    boundary_witnesses: dict[str, dict[str, int]]
    witness_verifications: tuple[WitnessVerification, ...]
    subtasks: tuple[SubtaskAuditReport, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_used": self.solver_used,
            "z3_available": self.z3_available,
            "global_satisfiable": self.global_satisfiable,
            "validator_consistent": self.validator_consistent,
            "all_subtasks_satisfiable": self.all_subtasks_satisfiable,
            "all_subtasks_contained": self.all_subtasks_contained,
            "passed": self.passed,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "global_intervals": {k: [lo, hi] for k, (lo, hi) in self.global_intervals.items()},
            "boundary_witnesses": {k: dict(v) for k, v in self.boundary_witnesses.items()},
            "witness_verifications": [
                {
                    "name": w.name,
                    "assignment": dict(w.assignment),
                    "valid": w.valid,
                    "unsatisfied_predicates": list(w.unsatisfied_predicates),
                }
                for w in self.witness_verifications
            ],
            "subtasks": [st.to_dict() for st in self.subtasks],
        }


@dataclass(frozen=True)
class HeaderConstantMismatch:
    constant_name: str
    var: str
    bound_kind: str
    header_value: int | None
    manifest_value: int | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "constant_name": self.constant_name,
            "var": self.var,
            "bound_kind": self.bound_kind,
            "header_value": self.header_value,
            "manifest_value": self.manifest_value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ConstraintsHeaderDriftReport:
    exists: bool
    has_drift: bool
    mismatches: tuple[HeaderConstantMismatch, ...]
    missing_constants: tuple[str, ...]
    parsed_constants: dict[str, int]
    expected_constants: dict[str, int]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "has_drift": self.has_drift,
            "mismatches": [m.to_dict() for m in self.mismatches],
            "missing_constants": list(self.missing_constants),
            "parsed_constants": dict(self.parsed_constants),
            "expected_constants": dict(self.expected_constants),
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Z3 dynamic loader & predicate formatting/evaluation helpers
# ---------------------------------------------------------------------------


def _load_z3_module() -> Any | None:
    """Dynamically load optional ``z3`` module without requiring static stubs."""
    try:
        mod = importlib.import_module("z3")
    except ImportError:
        return None
    if getattr(mod, "Solver", None) is None or getattr(mod, "Int", None) is None:
        return None
    return mod


def is_z3_available() -> bool:
    """Return True if optional ``z3-solver`` is importable in the current environment."""
    return _load_z3_module() is not None


def _expr_vars(expr: ArithExpr) -> set[str]:
    if isinstance(expr, ConstExpr):
        return set()
    if isinstance(expr, VarExpr):
        return {expr.name}
    if isinstance(expr, UnaryNegExpr):
        return _expr_vars(expr.operand)
    return _expr_vars(expr.left) | _expr_vars(expr.right)


def _pred_vars(pred: RelPred) -> set[str]:
    return _expr_vars(pred.left) | _expr_vars(pred.right)


def _format_expr(expr: ArithExpr) -> str:
    if isinstance(expr, ConstExpr):
        return str(expr.value)
    if isinstance(expr, VarExpr):
        return expr.name
    if isinstance(expr, UnaryNegExpr):
        return f"-({ _format_expr(expr.operand) })" if isinstance(expr.operand, BinOpExpr) else f"-{_format_expr(expr.operand)}"
    return f"({_format_expr(expr.left)} {expr.op} {_format_expr(expr.right)})"


def _format_pred(pred: RelPred) -> str:
    return f"{_format_expr(pred.left)} {pred.op} {_format_expr(pred.right)}"


def _eval_expr(expr: ArithExpr, assignment: dict[str, int]) -> int | None:
    if isinstance(expr, ConstExpr):
        return expr.value
    if isinstance(expr, VarExpr):
        return assignment.get(expr.name)
    if isinstance(expr, UnaryNegExpr):
        val = _eval_expr(expr.operand, assignment)
        return -val if val is not None else None
    left = _eval_expr(expr.left, assignment)
    right = _eval_expr(expr.right, assignment)
    if left is None or right is None:
        return None
    if expr.op == "+":
        return left + right
    if expr.op == "-":
        return left - right
    if expr.op == "*":
        return left * right
    if right == 0:
        return None
    return left % right


def _eval_pred(pred: RelPred, assignment: dict[str, int]) -> bool:
    left = _eval_expr(pred.left, assignment)
    right = _eval_expr(pred.right, assignment)
    if left is None or right is None:
        return False
    if pred.op == "<=":
        return left <= right
    if pred.op == "<":
        return left < right
    if pred.op == ">=":
        return left >= right
    if pred.op == ">":
        return left > right
    if pred.op == "==":
        return left == right
    return left != right


def _flip_op(op: RelOp) -> RelOp:
    mapping: dict[RelOp, RelOp] = {
        "<=": ">=",
        "<": ">",
        ">=": "<=",
        ">": "<",
        "==": "==",
        "!=": "!=",
    }
    return mapping[op]


def _negate_pred(pred: RelPred) -> RelPred:
    neg_map: dict[RelOp, RelOp] = {
        "<=": ">",
        "<": ">=",
        ">=": "<",
        ">": "<=",
        "==": "!=",
        "!=": "==",
    }
    return RelPred(
        op=neg_map[pred.op],
        left=pred.left,
        right=pred.right,
        source=f"not({pred.source})",
        direction=pred.direction,
    )


def _as_exact_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isfinite(value) and abs(value - round(value)) < 1e-6:
            return int(round(value))
        return None
    if isinstance(value, str):
        normalized = _preprocess_math_text(value)
        return parse_cpp_int_expr(normalized, {})
    return None


# ---------------------------------------------------------------------------
# Constraint & predicate string parser (LaTeX + ASCII inequalities & exprs)
# ---------------------------------------------------------------------------


def _preprocess_math_text(text: str) -> str:
    """Normalize LaTeX/Unicode math bounds and scientific powers into ASCII expressions."""
    s = text.strip()
    s = s.replace("$", " ")
    s = s.replace("≤", " <= ").replace("≥", " >= ").replace("≠", " != ")
    s = s.replace("×", " * ").replace("·", " * ")
    s = re.sub(r"(?<=\d)\\[,;:](?=\d)", "", s)
    s = re.sub(r"\\[,;:]", " ", s)
    for latex_op, ascii_op in (
        (r"\\leq\b", "<="),
        (r"\\le\b", "<="),
        (r"\\geq\b", ">="),
        (r"\\ge\b", ">="),
        (r"\\neq\b", "!="),
        (r"\\ne\b", "!="),
        (r"\\times\b", "*"),
        (r"\\cdot\b", "*"),
        (r"\\bmod\b", "%"),
        (r"\\mod\b", "%"),
    ):
        s = re.sub(latex_op, f" {ascii_op} ", s)
    # Expand a * 10^{k} or 10^{k} or 2^{k} into concrete integer literals
    s = re.sub(
        r"(\d{1,12})\s*\*\s*10\s*\^\s*\{?\s*(\d{1,2})\s*\}?",
        lambda m: str(int(m.group(1)) * (10 ** int(m.group(2)))) if int(m.group(2)) <= 18 else m.group(0),
        s,
    )
    s = re.sub(
        r"(?<![\d.])10\s*\^\s*\{?\s*(\d{1,2})\s*\}?",
        lambda m: str(10 ** int(m.group(1))) if int(m.group(1)) <= 18 else m.group(0),
        s,
    )
    s = re.sub(
        r"(?<![\d.])2\s*\^\s*\{?\s*(\d{1,2})\s*\}?",
        lambda m: str(1 << int(m.group(1))) if int(m.group(1)) <= 62 else m.group(0),
        s,
    )
    # Expand |var| <= C and |var| < C into symmetric double-sided inequalities
    s = re.sub(
        r"\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*(<=|<)\s*(-?\d+)",
        lambda m: f"{-int(m.group(3))} {m.group(2)} {m.group(1)} {m.group(2)} {m.group(3)}",
        s,
    )
    # Remove thousands commas inside integers like 1,000,000
    s = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", s)
    return " ".join(s.split())


def _canonicalize_var(raw: str, alias_map: dict[str, str]) -> str:
    cleaned = raw.strip()
    if cleaned in alias_map:
        return alias_map[cleaned]
    lower = cleaned.lower()
    if lower in alias_map:
        return alias_map[lower]
    return cleaned


def _parse_arith_expr(raw: str, alias_map: dict[str, str], *, _depth: int = 0) -> ArithExpr | None:
    if _depth > 16:
        return None
    s = strip_outer_parens(raw)
    if not s:
        return None
    add_split = split_top_level_op(s, "+-")
    if add_split is not None:
        left_s, op_s, right_s = add_split
        left = _parse_arith_expr(left_s, alias_map, _depth=_depth + 1)
        right = _parse_arith_expr(right_s, alias_map, _depth=_depth + 1)
        if left is not None and right is not None:
            bin_op: BinOpKind = "+" if op_s == "+" else "-"
            return BinOpExpr(bin_op, left, right)
    mul_split = split_top_level_op(s, "*%")
    if mul_split is not None:
        left_s, op_s, right_s = mul_split
        left = _parse_arith_expr(left_s, alias_map, _depth=_depth + 1)
        right = _parse_arith_expr(right_s, alias_map, _depth=_depth + 1)
        if left is not None and right is not None:
            bin_op_m: BinOpKind = "*" if op_s == "*" else "%"
            return BinOpExpr(bin_op_m, left, right)
    if s.startswith("-") and len(s) > 1:
        inner = _parse_arith_expr(s[1:], alias_map, _depth=_depth + 1)
        if inner is not None:
            if isinstance(inner, ConstExpr):
                return ConstExpr(-inner.value)
            return UnaryNegExpr(inner)
    if s.startswith("+") and len(s) > 1:
        return _parse_arith_expr(s[1:], alias_map, _depth=_depth + 1)
    int_val = parse_cpp_int_expr(s, {})
    if int_val is not None:
        return ConstExpr(int_val)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s):
        return VarExpr(_canonicalize_var(s, alias_map))
    return None


_CHAIN_REL_RE = re.compile(r"(<=|>=|!=|==|<|>)")


def _as_rel_op(token: str) -> RelOp | None:
    if token == "<=":
        return "<="
    if token == "<":
        return "<"
    if token == ">=":
        return ">="
    if token == ">":
        return ">"
    if token == "==":
        return "=="
    if token == "!=":
        return "!="
    return None


def _classify_pred_direction(op: RelOp, left: ArithExpr, right: ArithExpr) -> Literal["lower", "upper", "eq", "neq", "mod", "rel"]:
    if isinstance(left, BinOpExpr) and left.op == "%":
        return "mod"
    if isinstance(right, BinOpExpr) and right.op == "%":
        return "mod"
    if op == "==":
        return "eq"
    if op == "!=":
        return "neq"
    if isinstance(left, VarExpr) and isinstance(right, ConstExpr):
        return "upper" if op in ("<=", "<") else "lower"
    if isinstance(right, VarExpr) and isinstance(left, ConstExpr):
        return "lower" if op in ("<=", "<") else "upper"
    return "rel"


def _parse_predicate_clauses(
    text: str,
    alias_map: dict[str, str],
    *,
    source: str,
) -> tuple[list[RelPred], list[str]]:
    """Parse conjunctions and chained comparisons like ``1 <= n < m <= 10^5``."""
    normalized = _preprocess_math_text(text)
    if not normalized:
        return [], []
    # Split on &&, ;, or standalone 'and'
    raw_clauses = re.split(r"\s*(?:&&|;|\band\b)\s*", normalized)
    preds: list[RelPred] = []
    errors: list[str] = []
    for raw_clause in raw_clauses:
        clause = strip_outer_parens(raw_clause.strip())
        if not clause:
            continue
        parts = _CHAIN_REL_RE.split(clause)
        # parts alternates: [expr0, op1, expr1, op2, expr2, ...]
        if len(parts) < 3 or len(parts) % 2 == 0:
            if any(op in clause for op in ("<", ">", "==", "!=", "%")):
                errors.append(f"unparsed constraint clause in {source}: {clause!r}")
            continue
        parsed_exprs: list[ArithExpr] = []
        ok = True
        for idx in range(0, len(parts), 2):
            expr = _parse_arith_expr(parts[idx], alias_map)
            if expr is None:
                ok = False
                break
            parsed_exprs.append(expr)
        if not ok:
            # Skip non-mathematical descriptive prose that happens to mention '<' or '>'
            if not any(ch.isdigit() for ch in clause):
                continue
            errors.append(f"unparsed expression in {source}: {clause!r}")
            continue
        for idx in range(1, len(parts), 2):
            op_raw = parts[idx]
            op = _as_rel_op(op_raw)
            if op is None:
                errors.append(f"unsupported operator {op_raw!r} in {source}")
                continue
            left_e = parsed_exprs[(idx - 1) // 2]
            right_e = parsed_exprs[(idx + 1) // 2]
            # Normalize single-variable `const op var` to `var flipped_op const`
            if isinstance(left_e, ConstExpr) and isinstance(right_e, VarExpr):
                left_e, right_e = right_e, left_e
                op = _flip_op(op)
            preds.append(
                RelPred(
                    op=op,
                    left=left_e,
                    right=right_e,
                    source=source,
                    direction=_classify_pred_direction(op, left_e, right_e),
                )
            )
    return preds, errors


# ---------------------------------------------------------------------------
# Manifest + Validator extraction into RelPred lists
# ---------------------------------------------------------------------------


def _build_alias_map(constraints: dict[str, Any]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    canonical_keys: list[str] = []
    for raw_key in constraints:
        if not isinstance(raw_key, str) or not raw_key.strip():
            continue
        key = raw_key.strip()
        if key.lower() in _PREDICATE_CONTAINER_KEYS or key.lower() in _METADATA_IGNORE_KEYS:
            continue
        if key.lower().endswith(("_max", "_min")) and len(key) > 4:
            base = key[:-4]
            if base not in canonical_keys:
                canonical_keys.append(base)
            alias_map[key] = base
            alias_map[key.lower()] = base
        elif key.lower().endswith("_eq") and len(key) > 3:
            base = key[:-3]
            if base not in canonical_keys:
                canonical_keys.append(base)
            alias_map[key] = base
            alias_map[key.lower()] = base
        else:
            if key not in canonical_keys:
                canonical_keys.append(key)
    for canon in canonical_keys:
        alias_map.setdefault(canon, canon)
        alias_map.setdefault(canon.lower(), canon)
    for raw_key, raw_val in constraints.items():
        if not isinstance(raw_key, str) or not isinstance(raw_val, dict):
            continue
        canon = alias_map.get(raw_key.strip(), raw_key.strip())
        raw_aliases = raw_val.get("aliases")
        if isinstance(raw_aliases, list):
            for alias in raw_aliases:
                if isinstance(alias, str) and alias.strip():
                    alias_map[alias.strip()] = canon
                    alias_map[alias.strip().lower()] = canon
    return alias_map


def _canonical_var_from_key(raw_key: str, alias_map: dict[str, str]) -> tuple[str, str]:
    """Return ``(canonical_var_name, scalar_role)`` where role is ``min``/``max``/``eq``/``default_max``."""
    key = raw_key.strip()
    lower = key.lower()
    if lower.endswith("_min") and len(key) > 4:
        return _canonicalize_var(key[:-4], alias_map), "min"
    if lower.endswith("_max") and len(key) > 4:
        return _canonicalize_var(key[:-4], alias_map), "max"
    if lower.endswith("_eq") and len(key) > 3:
        return _canonicalize_var(key[:-3], alias_map), "eq"
    return _canonicalize_var(key, alias_map), "default_max"


def _extract_constraints_predicates(
    constraints: dict[str, Any],
    alias_map: dict[str, str],
    *,
    scope_prefix: str,
) -> tuple[list[RelPred], list[str], dict[str, set[str]]]:
    """Extract ``RelPred`` constraints, parse errors, and per-var explicit bound directions."""
    preds: list[RelPred] = []
    errors: list[str] = []
    explicit_dirs: dict[str, set[str]] = {}

    def _mark_dir(var: str, direction: str) -> None:
        explicit_dirs.setdefault(var, set()).add(direction)

    def _add_bound(var: str, op: RelOp, val: int, src: str, direction: Literal["lower", "upper", "eq"]) -> None:
        preds.append(RelPred(op=op, left=VarExpr(var), right=ConstExpr(val), source=src, direction=direction))
        _mark_dir(var, direction)

    for raw_key, raw_val in constraints.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            continue
        key = raw_key.strip()
        lower_key = key.lower()
        if lower_key in _METADATA_IGNORE_KEYS:
            continue
        src = f"{scope_prefix}:{key}"

        if lower_key in _PREDICATE_CONTAINER_KEYS:
            items = raw_val if isinstance(raw_val, list) else [raw_val]
            for idx, item in enumerate(items):
                if isinstance(item, str) and item.strip():
                    p_list, p_errs = _parse_predicate_clauses(item, alias_map, source=f"{src}[{idx}]")
                    preds.extend(p_list)
                    errors.extend(p_errs)
                    for p in p_list:
                        for v in _pred_vars(p):
                            _mark_dir(v, p.direction)
            continue

        if isinstance(raw_val, bool):
            # Boolean property markers like {"is_tree": True} are descriptive tags, not int bounds.
            continue

        canon_var, scalar_role = _canonical_var_from_key(key, alias_map)

        if isinstance(raw_val, dict):
            lo_raw = raw_val.get("min", raw_val.get("lower"))
            hi_raw = raw_val.get("max", raw_val.get("upper"))
            eq_raw = raw_val.get("eq", raw_val.get("exact", raw_val.get("value")))
            if lo_raw is not None:
                lo_int = _as_exact_int(lo_raw)
                if lo_int is None:
                    errors.append(f"non-integer lower bound for {src}: {lo_raw!r}")
                else:
                    _add_bound(canon_var, ">=", lo_int, f"{src}.min", "lower")
            if hi_raw is not None:
                hi_int = _as_exact_int(hi_raw)
                if hi_int is None:
                    errors.append(f"non-integer upper bound for {src}: {hi_raw!r}")
                else:
                    _add_bound(canon_var, "<=", hi_int, f"{src}.max", "upper")
            if eq_raw is not None:
                eq_int = _as_exact_int(eq_raw)
                if eq_int is None:
                    errors.append(f"non-integer exact bound for {src}: {eq_raw!r}")
                else:
                    _add_bound(canon_var, "==", eq_int, f"{src}.eq", "eq")
            parity = raw_val.get("parity")
            if isinstance(parity, str) and parity.strip().lower() in ("even", "odd"):
                rem = 0 if parity.strip().lower() == "even" else 1
                preds.append(
                    RelPred(
                        op="==",
                        left=BinOpExpr("%", VarExpr(canon_var), ConstExpr(2)),
                        right=ConstExpr(rem),
                        source=f"{src}.parity",
                        direction="mod",
                    )
                )
                _mark_dir(canon_var, "mod")
            div_raw = raw_val.get("divisible_by", raw_val.get("mod"))
            if isinstance(div_raw, int) and not isinstance(div_raw, bool) and div_raw > 0:
                preds.append(
                    RelPred(
                        op="==",
                        left=BinOpExpr("%", VarExpr(canon_var), ConstExpr(div_raw)),
                        right=ConstExpr(0),
                        source=f"{src}.mod",
                        direction="mod",
                    )
                )
                _mark_dir(canon_var, "mod")
            elif isinstance(div_raw, dict):
                divisor = _as_exact_int(div_raw.get("divisor"))
                remainder = _as_exact_int(div_raw.get("remainder", 0))
                if divisor is not None and divisor > 0 and remainder is not None:
                    preds.append(
                        RelPred(
                            op="==",
                            left=BinOpExpr("%", VarExpr(canon_var), ConstExpr(divisor)),
                            right=ConstExpr(remainder % divisor),
                            source=f"{src}.mod",
                            direction="mod",
                        )
                    )
                    _mark_dir(canon_var, "mod")
            neq_raw = raw_val.get("neq", raw_val.get("not_equal"))
            if neq_raw is not None:
                neq_items = neq_raw if isinstance(neq_raw, list) else [neq_raw]
                for item in neq_items:
                    neq_int = _as_exact_int(item)
                    if neq_int is not None:
                        preds.append(
                            RelPred(
                                op="!=",
                                left=VarExpr(canon_var),
                                right=ConstExpr(neq_int),
                                source=f"{src}.neq",
                                direction="neq",
                            )
                        )
                        _mark_dir(canon_var, "neq")
            for pkey in ("expr", "condition", "predicate", "conditions", "predicates"):
                if pkey in raw_val:
                    raw_exprs = raw_val[pkey]
                    expr_items = raw_exprs if isinstance(raw_exprs, list) else [raw_exprs]
                    for item in expr_items:
                        if isinstance(item, str) and item.strip():
                            p_list, p_errs = _parse_predicate_clauses(item, alias_map, source=f"{src}.{pkey}")
                            preds.extend(p_list)
                            errors.extend(p_errs)
                            for p in p_list:
                                for v in _pred_vars(p):
                                    _mark_dir(v, p.direction)
            continue

        if isinstance(raw_val, list):
            for idx, item in enumerate(raw_val):
                if isinstance(item, str) and item.strip():
                    p_list, p_errs = _parse_predicate_clauses(item, alias_map, source=f"{src}[{idx}]")
                    preds.extend(p_list)
                    errors.extend(p_errs)
                    for p in p_list:
                        for v in _pred_vars(p):
                            _mark_dir(v, p.direction)
            continue

        if isinstance(raw_val, str):
            stripped = raw_val.strip()
            if not stripped:
                continue
            if stripped.startswith(("<=", ">=", "<", ">", "==", "!=")):
                p_list, p_errs = _parse_predicate_clauses(f"{canon_var} {stripped}", alias_map, source=src)
                preds.extend(p_list)
                errors.extend(p_errs)
                for p in p_list:
                    for v in _pred_vars(p):
                        _mark_dir(v, p.direction)
                continue
            scalar_str = _as_exact_int(stripped)
            if scalar_str is not None:
                if scalar_role == "min":
                    _add_bound(canon_var, ">=", scalar_str, src, "lower")
                elif scalar_role == "eq":
                    _add_bound(canon_var, "==", scalar_str, src, "eq")
                else:
                    _add_bound(canon_var, "<=", scalar_str, src, "upper")
                continue
            p_list, p_errs = _parse_predicate_clauses(stripped, alias_map, source=src)
            preds.extend(p_list)
            errors.extend(p_errs)
            for p in p_list:
                for v in _pred_vars(p):
                    _mark_dir(v, p.direction)
            continue

        scalar_int = _as_exact_int(raw_val)
        if scalar_int is not None:
            if scalar_role == "min":
                _add_bound(canon_var, ">=", scalar_int, src, "lower")
            elif scalar_role == "eq":
                _add_bound(canon_var, "==", scalar_int, src, "eq")
            else:
                _add_bound(canon_var, "<=", scalar_int, src, "upper")

    return preds, errors, explicit_dirs


def _extract_validator_predicates(
    validator_bounds: list[dict[str, Any]] | None,
    alias_map: dict[str, str],
) -> list[RelPred]:
    if not validator_bounds:
        return []
    preds: list[RelPred] = []
    for idx, item in enumerate(validator_bounds):
        raw_var = item.get("var")
        if not isinstance(raw_var, str) or not raw_var.strip():
            continue
        var = _canonicalize_var(raw_var.strip(), alias_map)
        src_tag = str(item.get("source") or "val")
        lo = item.get("min")
        hi = item.get("max")
        if isinstance(lo, int) and not isinstance(lo, bool):
            preds.append(
                RelPred(
                    op=">=",
                    left=VarExpr(var),
                    right=ConstExpr(lo),
                    source=f"validator:{src_tag}:{var}[{idx}].min",
                    direction="lower",
                )
            )
        if isinstance(hi, int) and not isinstance(hi, bool):
            preds.append(
                RelPred(
                    op="<=",
                    left=VarExpr(var),
                    right=ConstExpr(hi),
                    source=f"validator:{src_tag}:{var}[{idx}].max",
                    direction="upper",
                )
            )
    return preds


# ---------------------------------------------------------------------------
# Pure-Python linear / interval / modular propagation solver
# ---------------------------------------------------------------------------


def _linearize_expr(expr: ArithExpr) -> tuple[dict[str, int], int] | None:
    """Convert a linear ``ArithExpr`` to ``(var_coeffs, constant)``, or None if nonlinear."""
    if isinstance(expr, ConstExpr):
        return {}, expr.value
    if isinstance(expr, VarExpr):
        return {expr.name: 1}, 0
    if isinstance(expr, UnaryNegExpr):
        inner = _linearize_expr(expr.operand)
        if inner is None:
            return None
        coeffs, const = inner
        return {k: -v for k, v in coeffs.items()}, -const
    if expr.op == "%":
        return None
    left_lin = _linearize_expr(expr.left)
    right_lin = _linearize_expr(expr.right)
    if left_lin is None or right_lin is None:
        return None
    lc, lconst = left_lin
    rc, rconst = right_lin
    if expr.op == "+":
        merged = dict(lc)
        for k, v in rc.items():
            nv = merged.get(k, 0) + v
            if nv == 0:
                merged.pop(k, None)
            else:
                merged[k] = nv
        return merged, lconst + rconst
    if expr.op == "-":
        merged = dict(lc)
        for k, v in rc.items():
            nv = merged.get(k, 0) - v
            if nv == 0:
                merged.pop(k, None)
            else:
                merged[k] = nv
        return merged, lconst - rconst
    # Multiplication: linear iff at least one side is constant
    if not lc:
        return {k: v * lconst for k, v in rc.items() if v * lconst != 0}, lconst * rconst
    if not rc:
        return {k: v * rconst for k, v in lc.items() if v * rconst != 0}, lconst * rconst
    return None


def _combine_modular(m1: int, r1: int, m2: int, r2: int) -> tuple[int, int] | None:
    """Combine x ≡ r1 (mod m1) and x ≡ r2 (mod m2), returning None if incompatible."""
    g = math.gcd(m1, m2)
    if (r1 - r2) % g != 0:
        return None
    lcm = (m1 // g) * m2
    # Extended GCD for (m1 // g) * k ≡ (r2 - r1) // g (mod m2 // g)
    step = m1 // g
    mod_k = m2 // g
    rhs = ((r2 - r1) // g) % mod_k
    k = (rhs * pow(step % mod_k, -1, mod_k)) % mod_k if mod_k > 1 else 0
    combined_r = (r1 + m1 * k) % lcm
    return lcm, combined_r


def _snap_up(val: int, mod: tuple[int, int] | None, neq: set[int]) -> int:
    cur = val
    if mod is not None:
        m, r = mod
        rem = cur % m
        if rem != r:
            cur += (r - rem) % m
    step = mod[0] if mod is not None else 1
    while cur in neq:
        cur += step
    return cur


def _snap_down(val: int, mod: tuple[int, int] | None, neq: set[int]) -> int:
    cur = val
    if mod is not None:
        m, r = mod
        rem = cur % m
        if rem != r:
            cur -= (rem - r) % m
    step = mod[0] if mod is not None else 1
    while cur in neq:
        cur -= step
    return cur


@dataclass
class _PySolveResult:
    satisfiable: bool
    intervals: dict[str, tuple[int | None, int | None]]
    witness: dict[str, int] | None
    conflict_reason: str | None = None


def _collect_all_vars(preds: Sequence[RelPred]) -> list[str]:
    seen: set[str] = set()
    for p in preds:
        seen.update(_pred_vars(p))
    return sorted(seen)


def _propagate_python_intervals(
    preds: Sequence[RelPred],
    initial_bounds: dict[str, tuple[int | None, int | None]] | None = None,
) -> tuple[bool, dict[str, tuple[int | None, int | None]], dict[str, tuple[int, int] | None], dict[str, set[int]], str | None]:
    all_vars = _collect_all_vars(preds)
    if initial_bounds:
        for v in initial_bounds:
            if v not in all_vars:
                all_vars.append(v)
        all_vars.sort()

    intervals: dict[str, tuple[int | None, int | None]] = {
        v: initial_bounds.get(v, (None, None)) if initial_bounds else (None, None) for v in all_vars
    }
    mods: dict[str, tuple[int, int] | None] = dict.fromkeys(all_vars)
    neqs: dict[str, set[int]] = {v: set() for v in all_vars}
    linear_ineqs: list[tuple[dict[str, int], int, str]] = []  # sum(coeff*x) + const <= 0
    product_ineqs: list[tuple[str, str, int, str]] = []  # x * y <= limit (when x, y >= 0)

    for pred in preds:
        # Handle modular constraints: (var % k) == r or r == (var % k)
        mod_side: tuple[BinOpExpr, ArithExpr] | None = None
        if isinstance(pred.left, BinOpExpr) and pred.left.op == "%":
            mod_side = (pred.left, pred.right)
        elif isinstance(pred.right, BinOpExpr) and pred.right.op == "%":
            mod_side = (pred.right, pred.left)
        if mod_side is not None:
            mod_expr, other_expr = mod_side
            if isinstance(mod_expr.left, VarExpr) and isinstance(mod_expr.right, ConstExpr) and isinstance(other_expr, ConstExpr):
                m_val = mod_expr.right.value
                r_val = other_expr.value
                v_name = mod_expr.left.name
                if m_val <= 0:
                    return False, intervals, mods, neqs, f"invalid non-positive modulus in {pred.source}"
                r_norm = r_val % m_val
                if pred.op == "==":
                    cur_mod = mods.get(v_name)
                    if cur_mod is None:
                        mods[v_name] = (m_val, r_norm)
                    else:
                        combined = _combine_modular(cur_mod[0], cur_mod[1], m_val, r_norm)
                        if combined is None:
                            return (
                                False,
                                intervals,
                                mods,
                                neqs,
                                f"contradictory modular constraints on {v_name}: "
                                f"mod {cur_mod[0]}=={cur_mod[1]} vs mod {m_val}=={r_norm}",
                            )
                        mods[v_name] = combined
                    continue
                if pred.op == "!=" and m_val == 2:
                    # x % 2 != r_norm implies x % 2 == (1 - r_norm)
                    opp = 1 - r_norm
                    cur_mod = mods.get(v_name)
                    if cur_mod is None:
                        mods[v_name] = (2, opp)
                    else:
                        combined = _combine_modular(cur_mod[0], cur_mod[1], 2, opp)
                        if combined is None:
                            return (
                                False,
                                intervals,
                                mods,
                                neqs,
                                f"contradictory parity constraint on {v_name} ({pred.source})",
                            )
                        mods[v_name] = combined
                    continue

        # Handle != against single variable and constant
        if pred.op == "!=":
            lin = _linearize_expr(BinOpExpr("-", pred.left, pred.right))
            if lin is not None:
                coeffs, const = lin
                if not coeffs:
                    if const == 0:
                        return False, intervals, mods, neqs, f"contradictory constant inequality {pred.source}"
                    continue
                if len(coeffs) == 1:
                    v_name, coef = next(iter(coeffs.items()))
                    if (-const) % coef != 0:
                        continue  # always != 0 in integers
                    forbidden = (-const) // coef
                    neqs.setdefault(v_name, set()).add(forbidden)
                    continue

        # Linearize <=, <, >=, >, ==
        lin = _linearize_expr(BinOpExpr("-", pred.left, pred.right))
        if lin is None:
            # Support non-negative product upper bounds: x * y <= C or x * y < C
            if (
                pred.op in ("<=", "<")
                and isinstance(pred.left, BinOpExpr)
                and pred.left.op == "*"
                and isinstance(pred.left.left, VarExpr)
                and isinstance(pred.left.right, VarExpr)
                and isinstance(pred.right, ConstExpr)
            ):
                limit = pred.right.value if pred.op == "<=" else pred.right.value - 1
                product_ineqs.append((pred.left.left.name, pred.left.right.name, limit, pred.source))
            elif (
                pred.op in (">=", ">")
                and isinstance(pred.right, BinOpExpr)
                and pred.right.op == "*"
                and isinstance(pred.right.left, VarExpr)
                and isinstance(pred.right.right, VarExpr)
                and isinstance(pred.left, ConstExpr)
            ):
                limit = pred.left.value if pred.op == ">=" else pred.left.value - 1
                product_ineqs.append((pred.right.left.name, pred.right.right.name, limit, pred.source))
            continue
        if lin is not None:
            coeffs, const = lin
            if not coeffs:
                satisfied = (
                    (const <= 0)
                    if pred.op == "<="
                    else (const < 0)
                    if pred.op == "<"
                    else (const >= 0)
                    if pred.op == ">="
                    else (const > 0)
                    if pred.op == ">"
                    else (const == 0)
                )
                if not satisfied:
                    return False, intervals, mods, neqs, f"contradictory constant predicate {pred.source}"
                continue
            if pred.op == "<=":
                linear_ineqs.append((coeffs, const, pred.source))
            elif pred.op == "<":
                linear_ineqs.append((coeffs, const + 1, pred.source))
            elif pred.op == ">=":
                linear_ineqs.append(({k: -v for k, v in coeffs.items()}, -const, pred.source))
            elif pred.op == ">":
                linear_ineqs.append(({k: -v for k, v in coeffs.items()}, -const + 1, pred.source))
            elif pred.op == "==":
                linear_ineqs.append((coeffs, const, pred.source))
                linear_ineqs.append(({k: -v for k, v in coeffs.items()}, -const, pred.source))

    def _tighten_var(v: str, new_lo: int | None, new_hi: int | None) -> tuple[bool, bool]:
        old_lo, old_hi = intervals[v]
        lo = old_lo
        hi = old_hi
        if new_lo is not None and (lo is None or new_lo > lo):
            lo = new_lo
        if new_hi is not None and (hi is None or new_hi < hi):
            hi = new_hi
        if lo is not None:
            lo = _snap_up(lo, mods.get(v), neqs.get(v, set()))
        if hi is not None:
            hi = _snap_down(hi, mods.get(v), neqs.get(v, set()))
        if lo is not None and hi is not None and lo > hi:
            intervals[v] = (lo, hi)
            return False, True
        changed = (lo != old_lo) or (hi != old_hi)
        intervals[v] = (lo, hi)
        return True, changed

    # Initial snap for modular / neq constraints
    for v in all_vars:
        ok, _ = _tighten_var(v, None, None)
        if not ok:
            return False, intervals, mods, neqs, f"empty domain for {v}: interval incompatible with parity/exclusions"

    # Fixed-point propagation across linear inequalities
    for _ in range(_SOLVER_MAX_ITERS):
        any_changed = False
        for coeffs, const, src in linear_ineqs:
            # Compute min possible value of sum(coeffs * x) + const
            term_mins: dict[str, int | None] = {}
            missing_mins: list[str] = []
            finite_min_sum = const
            for v, c in coeffs.items():
                lo, hi = intervals[v]
                if c > 0:
                    tmin = c * lo if lo is not None else None
                else:
                    tmin = c * hi if hi is not None else None
                term_mins[v] = tmin
                if tmin is None:
                    missing_mins.append(v)
                else:
                    finite_min_sum += tmin

            if not missing_mins and finite_min_sum > 0:
                return False, intervals, mods, neqs, f"unsatisfiable linear constraint ({src})"

            if len(missing_mins) > 1:
                continue

            for v, c in coeffs.items():
                if missing_mins and missing_mins[0] != v:
                    continue
                other_min = finite_min_sum - (term_mins[v] or 0)
                # c * x <= -other_min
                if c > 0:
                    cand_hi = (-other_min) // c
                    ok, chg = _tighten_var(v, None, cand_hi)
                else:
                    neg_c = -c
                    # neg_c * x >= other_min -> x >= ceil(other_min / neg_c)
                    cand_lo = -((-other_min) // neg_c)
                    ok, chg = _tighten_var(v, cand_lo, None)
                if not ok:
                    return False, intervals, mods, neqs, f"empty domain for {v} triggered by {src}"
                if chg:
                    any_changed = True
        for x_var, y_var, limit, src in product_ineqs:
            lo_x, _ = intervals[x_var]
            lo_y, _ = intervals[y_var]
            if lo_x is not None and lo_x >= 0 and lo_y is not None and lo_y >= 0:
                if lo_x * lo_y > limit:
                    return False, intervals, mods, neqs, f"unsatisfiable product constraint ({src})"
                if lo_y > 0:
                    ok, chg = _tighten_var(x_var, None, limit // lo_y)
                    if not ok:
                        return False, intervals, mods, neqs, f"empty domain for {x_var} triggered by {src}"
                    any_changed = any_changed or chg
                if lo_x > 0:
                    ok, chg = _tighten_var(y_var, None, limit // lo_x)
                    if not ok:
                        return False, intervals, mods, neqs, f"empty domain for {y_var} triggered by {src}"
                    any_changed = any_changed or chg
        if not any_changed:
            break

    return True, intervals, mods, neqs, None


def _default_probe_value(lo: int | None, hi: int | None, mod: tuple[int, int] | None, neq: set[int], *, prefer: Literal["min", "max"]) -> int:
    if prefer == "min":
        if lo is not None:
            return _snap_up(lo, mod, neq)
        base = 1 if (hi is None or hi >= 1) else (0 if hi >= 0 else hi)
        snapped = _snap_down(base, mod, neq) if (hi is not None and base > hi) else _snap_up(base, mod, neq)
        if hi is not None and snapped > hi:
            return _snap_down(hi, mod, neq)
        return snapped
    if hi is not None:
        return _snap_down(hi, mod, neq)
    base = max(lo if lo is not None else 1, 10**6)
    snapped = _snap_up(base, mod, neq)
    return snapped


def _solve_python_witness(
    preds: Sequence[RelPred],
    *,
    objective_var: str | None = None,
    prefer: Literal["min", "max"] = "min",
    extra_vars: Sequence[str] = (),
) -> _PySolveResult:
    ok, intervals, mods, neqs, reason = _propagate_python_intervals(preds)
    if not ok:
        return _PySolveResult(satisfiable=False, intervals=intervals, witness=None, conflict_reason=reason)

    all_vars = sorted(set(intervals.keys()) | set(extra_vars))
    for v in all_vars:
        intervals.setdefault(v, (None, None))
        mods.setdefault(v, None)
        neqs.setdefault(v, set())

    if not all_vars:
        return _PySolveResult(satisfiable=True, intervals=intervals, witness={})

    # Order objective_var first, then bounded/narrowest variables
    def _sort_key(v: str) -> tuple[int, int, str]:
        lo, hi = intervals[v]
        is_obj = 0 if v == objective_var else 1
        span = (hi - lo) if (lo is not None and hi is not None) else (10**18)
        return (is_obj, span, v)

    var_order = sorted(all_vars, key=_sort_key)
    state_count = 0

    def _search(idx: int, fixed: dict[str, tuple[int | None, int | None]], assigned: dict[str, int]) -> dict[str, int] | None:
        nonlocal state_count
        state_count += 1
        if state_count > _SOLVER_MAX_BACKTRACK_STATES:
            return None
        ok_step, cur_iv, cur_mods, cur_neqs, _ = _propagate_python_intervals(preds, fixed)
        if not ok_step:
            return None
        if idx >= len(var_order):
            if all(_eval_pred(p, assigned) for p in preds):
                return dict(assigned)
            return None

        var = var_order[idx]
        lo, hi = cur_iv.get(var, (None, None))
        mod = cur_mods.get(var)
        neq = cur_neqs.get(var, set())
        step = mod[0] if mod is not None else 1

        candidates: list[int] = []
        primary = _default_probe_value(lo, hi, mod, neq, prefer=prefer)
        candidates.append(primary)
        if prefer == "min":
            cur = primary
            for _ in range(12):
                cur = _snap_up(cur + step, mod, neq)
                if hi is not None and cur > hi:
                    break
                candidates.append(cur)
            if hi is not None and hi not in candidates:
                candidates.append(_snap_down(hi, mod, neq))
        else:
            cur = primary
            for _ in range(12):
                cur = _snap_down(cur - step, mod, neq)
                if lo is not None and cur < lo:
                    break
                candidates.append(cur)
            if lo is not None and lo not in candidates:
                candidates.append(_snap_up(lo, mod, neq))

        seen_cands: set[int] = set()
        for cand in candidates:
            if cand in seen_cands:
                continue
            if lo is not None and cand < lo:
                continue
            if hi is not None and cand > hi:
                continue
            if cand in neq:
                continue
            seen_cands.add(cand)
            next_fixed = dict(fixed)
            next_fixed[var] = (cand, cand)
            next_assigned = dict(assigned)
            next_assigned[var] = cand
            res = _search(idx + 1, next_fixed, next_assigned)
            if res is not None:
                return res
        return None

    witness = _search(0, intervals, {})
    if witness is None:
        return _PySolveResult(
            satisfiable=False,
            intervals=intervals,
            witness=None,
            conflict_reason=reason or "no valid integer assignment satisfies all constraints",
        )
    return _PySolveResult(satisfiable=True, intervals=intervals, witness=witness)


# ---------------------------------------------------------------------------
# Z3 Solver backend (with clean Python fallback for unknown / unbounded)
# ---------------------------------------------------------------------------


def _to_z3_expr(z3_mod: Any, expr: ArithExpr, z3_vars: dict[str, Any]) -> Any:
    if isinstance(expr, ConstExpr):
        return z3_mod.IntVal(expr.value)
    if isinstance(expr, VarExpr):
        return z3_vars[expr.name]
    if isinstance(expr, UnaryNegExpr):
        return -_to_z3_expr(z3_mod, expr.operand, z3_vars)
    left = _to_z3_expr(z3_mod, expr.left, z3_vars)
    right = _to_z3_expr(z3_mod, expr.right, z3_vars)
    if expr.op == "+":
        return left + right
    if expr.op == "-":
        return left - right
    if expr.op == "*":
        return left * right
    return left % right


def _to_z3_pred(z3_mod: Any, pred: RelPred, z3_vars: dict[str, Any]) -> Any:
    left = _to_z3_expr(z3_mod, pred.left, z3_vars)
    right = _to_z3_expr(z3_mod, pred.right, z3_vars)
    if pred.op == "<=":
        return left <= right
    if pred.op == "<":
        return left < right
    if pred.op == ">=":
        return left >= right
    if pred.op == ">":
        return left > right
    if pred.op == "==":
        return left == right
    return left != right


def _extract_z3_model(z3_mod: Any, model: Any, z3_vars: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, z3_var in sorted(z3_vars.items()):
        val = model.eval(z3_var, model_completion=True)
        out[name] = int(val.as_long())
    return out


def _check_z3_sat(
    z3_mod: Any,
    preds: Sequence[RelPred],
    *,
    extra_vars: Sequence[str] = (),
) -> tuple[bool, dict[str, int] | None, tuple[str, ...]]:
    all_vars = sorted(set(_collect_all_vars(preds)) | set(extra_vars))
    z3_vars = {v: z3_mod.Int(v) for v in all_vars}
    solver = z3_mod.Solver()
    solver.set("timeout", _Z3_TIMEOUT_MS)
    for idx, pred in enumerate(preds):
        label = f"{pred.source}#{idx}:{_format_pred(pred)}"
        solver.assert_and_track(_to_z3_pred(z3_mod, pred, z3_vars), label)
    status = solver.check()
    if status == z3_mod.sat:
        model = solver.model()
        return True, _extract_z3_model(z3_mod, model, z3_vars), ()
    if status == z3_mod.unsat:
        core = tuple(str(item) for item in solver.unsat_core())
        return False, None, core
    return False, None, ("z3_unknown",)


def _optimize_z3_witness(
    z3_mod: Any,
    preds: Sequence[RelPred],
    intervals: dict[str, tuple[int | None, int | None]],
    *,
    objective_var: str | None = None,
    prefer: Literal["min", "max"] = "min",
    extra_vars: Sequence[str] = (),
) -> dict[str, int] | None:
    all_vars = sorted(set(_collect_all_vars(preds)) | set(extra_vars))
    if not all_vars:
        return {}
    z3_vars = {v: z3_mod.Int(v) for v in all_vars}
    opt = z3_mod.Optimize()
    opt.set("timeout", _Z3_TIMEOUT_MS)
    for pred in preds:
        opt.add(_to_z3_pred(z3_mod, pred, z3_vars))

    # Keep unbounded directions finite so Optimize() returns concrete integers.
    for v in all_vars:
        lo, hi = intervals.get(v, (None, None))
        if lo is None:
            opt.add(z3_vars[v] >= (min(0, hi) if hi is not None else -10**9))
        if hi is None:
            opt.add(z3_vars[v] <= (max(10**6, lo) if lo is not None else 10**9))

    ordered = [objective_var] + [v for v in all_vars if v != objective_var] if objective_var else all_vars
    for v in ordered:
        if v not in z3_vars:
            continue
        if prefer == "min":
            opt.minimize(z3_vars[v])
        else:
            opt.maximize(z3_vars[v])

    if opt.check() == z3_mod.sat:
        candidate = _extract_z3_model(z3_mod, opt.model(), z3_vars)
        if all(_eval_pred(p, candidate) for p in preds):
            return candidate
    return None


def _resolve_solver_backend(requested: SolverBackend) -> tuple[Literal["z3", "python"], Any | None]:
    z3_mod = _load_z3_module()
    if requested == "python":
        return "python", None
    if requested == "z3":
        if z3_mod is None:
            raise RuntimeError("z3-solver is not installed; install optional extra 'formal' or use solver='python'")
        return "z3", z3_mod
    if z3_mod is not None:
        return "z3", z3_mod
    return "python", None


def _check_sat_with_backend(
    preds: Sequence[RelPred],
    *,
    backend: Literal["z3", "python"],
    z3_mod: Any | None,
    extra_vars: Sequence[str] = (),
) -> tuple[bool, dict[str, int] | None, tuple[str, ...], str | None]:
    if backend == "z3" and z3_mod is not None:
        sat, witness, core = _check_z3_sat(z3_mod, preds, extra_vars=extra_vars)
        if core != ("z3_unknown",):
            return sat, witness, core, ("; ".join(core) if not sat and core else None)
    py_res = _solve_python_witness(preds, extra_vars=extra_vars)
    return py_res.satisfiable, py_res.witness, (), py_res.conflict_reason


# ---------------------------------------------------------------------------
# Witness synthesis & verification
# ---------------------------------------------------------------------------


def _tighten_intervals_with_backend(
    preds: Sequence[RelPred],
    *,
    backend: Literal["z3", "python"],
    z3_mod: Any | None,
) -> dict[str, tuple[int | None, int | None]]:
    _, intervals, _, _, _ = _propagate_python_intervals(preds)
    if backend != "z3" or z3_mod is None:
        return intervals
    refined: dict[str, tuple[int | None, int | None]] = {}
    for var, (lo, hi) in intervals.items():
        new_lo = lo
        new_hi = hi
        if lo is not None:
            w_min = _optimize_z3_witness(z3_mod, preds, intervals, objective_var=var, prefer="min")
            if w_min is not None and var in w_min:
                new_lo = w_min[var]
        if hi is not None:
            w_max = _optimize_z3_witness(z3_mod, preds, intervals, objective_var=var, prefer="max")
            if w_max is not None and var in w_max:
                new_hi = w_max[var]
        refined[var] = (new_lo, new_hi)
    return refined


def _synthesize_witnesses_for_preds(
    preds: Sequence[RelPred],
    intervals: dict[str, tuple[int | None, int | None]],
    *,
    backend: Literal["z3", "python"],
    z3_mod: Any | None,
) -> dict[str, dict[str, int]]:
    if not preds:
        return {}
    all_vars = _collect_all_vars(preds)
    if not all_vars:
        return {}
    witnesses: dict[str, dict[str, int]] = {}

    def _solve_opt(obj_var: str | None, prefer: Literal["min", "max"]) -> dict[str, int] | None:
        if backend == "z3" and z3_mod is not None:
            w = _optimize_z3_witness(z3_mod, preds, intervals, objective_var=obj_var, prefer=prefer)
            if w is not None:
                return w
        py_res = _solve_python_witness(preds, objective_var=obj_var, prefer=prefer)
        return py_res.witness if py_res.satisfiable else None

    w_min = _solve_opt(None, "min")
    if w_min is not None:
        witnesses["min"] = w_min
    w_max = _solve_opt(None, "max")
    if w_max is not None:
        witnesses["max"] = w_max

    for var in all_vars:
        lo, hi = intervals.get(var, (None, None))
        if lo is not None:
            wv_min = _solve_opt(var, "min")
            if wv_min is not None:
                witnesses[f"{var}_min"] = wv_min
        if hi is not None:
            wv_max = _solve_opt(var, "max")
            if wv_max is not None:
                witnesses[f"{var}_max"] = wv_max

    # Add an interior/extreme alternating witness if ranges allow room between min and max
    if "min" in witnesses and "max" in witnesses and witnesses["min"] != witnesses["max"]:
        alt_preds = list(preds)
        for idx, var in enumerate(all_vars):
            lo, hi = intervals.get(var, (None, None))
            if lo is not None and hi is not None and hi - lo >= 2:
                target = lo + 1 if idx % 2 == 0 else hi - 1
                alt_preds.append(RelPred(op="==", left=VarExpr(var), right=ConstExpr(target), source="extreme_probe"))
                sat, w_alt, _, _ = _check_sat_with_backend(alt_preds, backend=backend, z3_mod=z3_mod)
                if not sat:
                    alt_preds.pop()
                elif w_alt is not None:
                    witnesses["near_boundary"] = w_alt
                    break
                else:
                    alt_preds.pop()

    return witnesses


def _subtask_fields(st: Any) -> tuple[int, str | None, dict[str, Any]]:
    if isinstance(st, dict):
        raw_id = st.get("id", 0)
        st_id = int(raw_id) if isinstance(raw_id, int) and not isinstance(raw_id, bool) else 0
        raw_name = st.get("name")
        st_name = str(raw_name) if isinstance(raw_name, str) else None
        raw_c = st.get("constraints")
        st_constraints: dict[str, Any] = dict(raw_c) if isinstance(raw_c, dict) else {}
        return st_id, st_name, st_constraints
    return int(st.id), st.name, dict(st.constraints)


def _collect_system_preds(
    manifest: AutoCodeManifest,
    *,
    subtask_id: int | None = None,
    validator_bounds: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, str], list[RelPred], list[str], dict[int, tuple[list[RelPred], list[str], dict[str, set[str]]]]]:
    alias_map = _build_alias_map(manifest.constraints)
    global_preds, global_errors, _ = _extract_constraints_predicates(
        manifest.constraints, alias_map, scope_prefix="global"
    )
    val_preds = _extract_validator_predicates(validator_bounds, alias_map)

    subtask_map: dict[int, tuple[list[RelPred], list[str], dict[str, set[str]]]] = {}
    for st in manifest.subtasks:
        st_id, _, st_constraints = _subtask_fields(st)
        st_alias = dict(alias_map)
        st_alias.update(_build_alias_map(st_constraints))
        st_preds, st_errs, st_dirs = _extract_constraints_predicates(
            st_constraints, st_alias, scope_prefix=f"subtask:{st_id}"
        )
        subtask_map[st_id] = (st_preds, st_errs, st_dirs)

    if subtask_id is not None and subtask_id not in subtask_map:
        raise ValueError(f"Unknown subtask_id {subtask_id}")

    return alias_map, global_preds + val_preds, global_errors, subtask_map


def verify_witness_assignment(
    assignment: dict[str, int],
    manifest: AutoCodeManifest,
    *,
    subtask_id: int | None = None,
    validator_bounds: list[dict[str, Any]] | None = None,
    solver: SolverBackend = "auto",
) -> WitnessVerification:
    """Verify a concrete variable assignment against manifest, subtask, and validator constraints."""
    backend, z3_mod = _resolve_solver_backend(solver)
    _, base_preds, _, subtask_map = _collect_system_preds(
        manifest, subtask_id=subtask_id, validator_bounds=validator_bounds
    )
    preds = list(base_preds)
    if subtask_id is not None:
        preds.extend(subtask_map[subtask_id][0])

    failed: list[str] = []
    for pred in preds:
        if not _eval_pred(pred, assignment):
            failed.append(f"{pred.source}: {_format_pred(pred)}")

    # Also cross-check with solver (pinned equality assignments)
    if not failed:
        pinned = list(preds) + [
            RelPred(op="==", left=VarExpr(k), right=ConstExpr(v), source="witness")
            for k, v in sorted(assignment.items())
        ]
        sat, _, _, reason = _check_sat_with_backend(pinned, backend=backend, z3_mod=z3_mod)
        if not sat:
            failed.append(reason or "solver rejected witness assignment")

    return WitnessVerification(
        name="custom" if subtask_id is None else f"subtask:{subtask_id}",
        assignment=dict(assignment),
        valid=not failed,
        unsatisfied_predicates=tuple(failed),
    )


def synthesize_boundary_witnesses(
    manifest: AutoCodeManifest,
    *,
    subtask_id: int | None = None,
    validator_bounds: list[dict[str, Any]] | None = None,
    solver: SolverBackend = "auto",
) -> dict[str, dict[str, int]]:
    """Synthesize tight boundary witnesses (``min``, ``max``, per-var extremes) verified by the solver."""
    backend, z3_mod = _resolve_solver_backend(solver)
    _, base_preds, _, subtask_map = _collect_system_preds(
        manifest, subtask_id=subtask_id, validator_bounds=validator_bounds
    )
    preds = list(base_preds)
    if subtask_id is not None:
        preds.extend(subtask_map[subtask_id][0])

    sat, _, _, _ = _check_sat_with_backend(preds, backend=backend, z3_mod=z3_mod)
    if not sat:
        return {}
    intervals = _tighten_intervals_with_backend(preds, backend=backend, z3_mod=z3_mod)
    return _synthesize_witnesses_for_preds(preds, intervals, backend=backend, z3_mod=z3_mod)


# ---------------------------------------------------------------------------
# Full SMT & subtask containment audit
# ---------------------------------------------------------------------------


def _check_subtask_containment(
    subtask_id: int,
    global_preds: Sequence[RelPred],
    sub_preds: Sequence[RelPred],
    sub_dirs: dict[str, set[str]],
    *,
    backend: Literal["z3", "python"],
    z3_mod: Any | None,
) -> list[ConstraintConflict]:
    """Verify every explicit subtask constraint stays contained inside global bounds."""
    if not sub_preds or not global_preds:
        return []
    conflicts: list[ConstraintConflict] = []
    sub_vars = _collect_all_vars(sub_preds)
    sub_var_set = set(sub_vars)

    # Inherit opposite/unmentioned global bounds so partial subtask overrides (e.g. n_max=50)
    # stay bounded below by global n >= 1 rather than falsely wandering to -infinity.
    inherited_global: list[RelPred] = []
    for g in global_preds:
        g_vars = _pred_vars(g)
        if not g_vars.issubset(sub_var_set):
            continue
        if len(g_vars) == 1:
            var = next(iter(g_vars))
            dirs = sub_dirs.get(var, set())
            if g.direction == "lower" and ("lower" in dirs or "eq" in dirs):
                continue
            if g.direction == "upper" and ("upper" in dirs or "eq" in dirs):
                continue
            if g.direction == "eq" and dirs:
                continue
            if g.direction == "mod" and "mod" in dirs:
                continue
        inherited_global.append(g)

    base_sub = list(sub_preds) + inherited_global

    for g in global_preds:
        g_vars = _pred_vars(g)
        if not g_vars.issubset(sub_var_set):
            continue
        if len(g_vars) == 1:
            var = next(iter(g_vars))
            dirs = sub_dirs.get(var, set())
            if g.direction == "lower" and "lower" not in dirs and "eq" not in dirs:
                continue
            if g.direction == "upper" and "upper" not in dirs and "eq" not in dirs:
                continue
            if g.direction == "eq" and not (dirs & {"lower", "upper", "eq"}):
                continue
            if g.direction == "mod" and "mod" not in dirs:
                continue
            if g.direction == "neq" and "neq" not in dirs:
                continue
        else:
            # Coupled multi-var global predicate: only test if subtask constrains variables involved
            if not any(sub_dirs.get(v) for v in g_vars):
                continue

        neg_g = _negate_pred(g)
        probe_preds = [*base_sub, neg_g]
        violates, raw_witness, _, _ = _check_sat_with_backend(
            probe_preds, backend=backend, z3_mod=z3_mod, extra_vars=sub_vars
        )
        if violates:
            # Prefer a tight extreme counterexample when available
            ok_iv, ivs, _, _, _ = _propagate_python_intervals(probe_preds)
            if ok_iv:
                if backend == "z3" and z3_mod is not None:
                    opt_w = _optimize_z3_witness(
                        z3_mod,
                        probe_preds,
                        ivs,
                        objective_var=sorted(g_vars)[0],
                        prefer="max" if g.direction == "upper" else "min",
                    )
                    if opt_w is not None:
                        raw_witness = opt_w
                else:
                    py_opt = _solve_python_witness(
                        probe_preds,
                        objective_var=sorted(g_vars)[0],
                        prefer="max" if g.direction == "upper" else "min",
                    )
                    if py_opt.satisfiable and py_opt.witness is not None:
                        raw_witness = py_opt.witness
            conflicts.append(
                ConstraintConflict(
                    scope=f"subtask:{subtask_id}",
                    kind="subtask_exceeds_global",
                    message=(
                        f"subtask {subtask_id} exceeds global constraint ({_format_pred(g)}); "
                        f"counterexample witness={raw_witness}"
                    ),
                    variables=tuple(sorted(g_vars)),
                    witness=raw_witness,
                )
            )
    return conflicts


def audit_manifest_constraints(
    manifest: AutoCodeManifest,
    *,
    validator_bounds: list[dict[str, Any]] | None = None,
    solver: SolverBackend = "auto",
) -> SmtConstraintAuditReport:
    """Verify satisfiability & containment across manifest, subtasks, and validator bounds."""
    backend, z3_mod = _resolve_solver_backend(solver)
    alias_map = _build_alias_map(manifest.constraints)
    global_preds, global_errors, _ = _extract_constraints_predicates(
        manifest.constraints, alias_map, scope_prefix="global"
    )
    val_preds = _extract_validator_predicates(validator_bounds, alias_map)

    conflicts: list[ConstraintConflict] = []
    for err in global_errors:
        conflicts.append(ConstraintConflict(scope="global", kind="parse_error", message=err))

    # 1. Check global constraints satisfiability alone
    global_sat, _, global_core, global_reason = _check_sat_with_backend(
        global_preds, backend=backend, z3_mod=z3_mod
    )
    if not global_sat:
        conflicts.append(
            ConstraintConflict(
                scope="global",
                kind="unsat",
                message=f"global constraints are unsatisfiable ({global_reason or 'contradictory bounds'})",
                variables=tuple(_collect_all_vars(global_preds)),
                unsat_core=global_core,
            )
        )

    # 2. Check validator bounds self-consistency & joint consistency with global constraints
    validator_consistent = True
    if val_preds:
        val_self_sat, _, val_self_core, val_self_reason = _check_sat_with_backend(
            val_preds, backend=backend, z3_mod=z3_mod
        )
        if not val_self_sat:
            validator_consistent = False
            conflicts.append(
                ConstraintConflict(
                    scope="validator",
                    kind="validator_conflict",
                    message=f"validator bounds are self-contradictory ({val_self_reason or 'min > max'})",
                    variables=tuple(_collect_all_vars(val_preds)),
                    unsat_core=val_self_core,
                )
            )
        elif global_sat:
            joint_sat, _, joint_core, joint_reason = _check_sat_with_backend(
                global_preds + val_preds, backend=backend, z3_mod=z3_mod
            )
            if not joint_sat:
                validator_consistent = False
                conflicts.append(
                    ConstraintConflict(
                        scope="validator",
                        kind="validator_conflict",
                        message=(
                            "validator bounds contradict manifest constraints "
                            f"({joint_reason or 'disjoint ranges'})"
                        ),
                        variables=tuple(_collect_all_vars(global_preds + val_preds)),
                        unsat_core=joint_core,
                    )
                )

    combined_global = global_preds + val_preds if (global_sat and validator_consistent) else global_preds
    global_intervals = (
        _tighten_intervals_with_backend(combined_global, backend=backend, z3_mod=z3_mod)
        if global_sat
        else {}
    )
    boundary_witnesses = (
        _synthesize_witnesses_for_preds(
            combined_global, global_intervals, backend=backend, z3_mod=z3_mod
        )
        if global_sat
        else {}
    )

    witness_verifications: list[WitnessVerification] = []
    for w_name, w_assign in boundary_witnesses.items():
        failed = [
            f"{p.source}: {_format_pred(p)}"
            for p in combined_global
            if not _eval_pred(p, w_assign)
        ]
        witness_verifications.append(
            WitnessVerification(
                name=w_name,
                assignment=w_assign,
                valid=not failed,
                unsatisfied_predicates=tuple(failed),
            )
        )

    # 3. Audit every subtask: internal SAT + joint SAT with global + domain containment
    subtask_reports: list[SubtaskAuditReport] = []
    all_sub_sat = True
    all_sub_contained = True
    for st in manifest.subtasks:
        st_id, st_name, st_constraints = _subtask_fields(st)
        st_alias = dict(alias_map)
        st_alias.update(_build_alias_map(st_constraints))
        st_preds, st_errs, st_dirs = _extract_constraints_predicates(
            st_constraints, st_alias, scope_prefix=f"subtask:{st_id}"
        )
        st_conflicts: list[ConstraintConflict] = []
        for err in st_errs:
            st_conflicts.append(
                ConstraintConflict(scope=f"subtask:{st_id}", kind="parse_error", message=err)
            )

        st_own_sat, _, st_own_core, st_own_reason = _check_sat_with_backend(
            st_preds, backend=backend, z3_mod=z3_mod
        )
        if not st_own_sat:
            all_sub_sat = False
            st_conflicts.append(
                ConstraintConflict(
                    scope=f"subtask:{st_id}",
                    kind="unsat",
                    message=(
                        f"subtask {st_id} constraints are unsatisfiable "
                        f"({st_own_reason or 'contradictory predicates'})"
                    ),
                    variables=tuple(_collect_all_vars(st_preds)),
                    unsat_core=st_own_core,
                )
            )
            st_sat = False
            st_contained = True
            st_intervals: dict[str, tuple[int | None, int | None]] = {}
            st_witnesses: dict[str, dict[str, int]] = {}
        else:
            containment_conflicts = _check_subtask_containment(
                st_id, global_preds, st_preds, st_dirs, backend=backend, z3_mod=z3_mod
            )
            st_contained = not containment_conflicts
            if not st_contained:
                all_sub_contained = False
                st_conflicts.extend(containment_conflicts)

            joint_preds = global_preds + st_preds
            st_joint_sat, _, st_joint_core, st_joint_reason = _check_sat_with_backend(
                joint_preds, backend=backend, z3_mod=z3_mod
            )
            if not st_joint_sat:
                all_sub_sat = False
                st_sat = False
                st_conflicts.append(
                    ConstraintConflict(
                        scope=f"subtask:{st_id}",
                        kind="unsat",
                        message=(
                            f"subtask {st_id} contradicts global constraints "
                            f"({st_joint_reason or 'empty intersection'})"
                        ),
                        variables=tuple(_collect_all_vars(joint_preds)),
                        unsat_core=st_joint_core,
                    )
                )
                st_intervals = {}
                st_witnesses = {}
            else:
                st_sat = True
                st_intervals = _tighten_intervals_with_backend(
                    joint_preds, backend=backend, z3_mod=z3_mod
                )
                st_witnesses = _synthesize_witnesses_for_preds(
                    joint_preds, st_intervals, backend=backend, z3_mod=z3_mod
                )

        conflicts.extend(st_conflicts)
        subtask_reports.append(
            SubtaskAuditReport(
                subtask_id=st_id,
                subtask_name=st_name,
                satisfiable=st_sat,
                contained_in_global=st_contained,
                conflicts=tuple(st_conflicts),
                witnesses=st_witnesses,
                intervals=st_intervals,
                solver_used=backend,
            )
        )

    passed = (
        global_sat
        and validator_consistent
        and all_sub_sat
        and all_sub_contained
        and not any(c.kind == "parse_error" for c in conflicts)
    )
    return SmtConstraintAuditReport(
        solver_used=backend,
        z3_available=z3_mod is not None,
        global_satisfiable=global_sat,
        validator_consistent=validator_consistent,
        all_subtasks_satisfiable=all_sub_sat,
        all_subtasks_contained=all_sub_contained,
        passed=passed,
        conflicts=tuple(conflicts),
        global_intervals=global_intervals,
        boundary_witnesses=boundary_witnesses,
        witness_verifications=tuple(witness_verifications),
        subtasks=tuple(subtask_reports),
    )


# ---------------------------------------------------------------------------
# rbx-style single-source C++ constraints.h generation & drift detection
# ---------------------------------------------------------------------------


def _sanitize_cpp_ident(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw.strip()).upper()
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "VAR"
    if cleaned[0].isdigit():
        return f"C_{cleaned}"
    return cleaned


def _format_cpp_int64_literal(val: int) -> str:
    if val == -(1 << 63):
        return "(-9223372036854775807LL - 1)"
    if -(1 << 63) < val < (1 << 63):
        return f"{val}LL"
    return f"{val}"


def _extract_expected_header_specs(
    manifest: AutoCodeManifest,
) -> tuple[dict[str, int], list[tuple[str, str, str, int, tuple[str, ...]]]]:
    """Return ``(expected_constants, structured_specs)`` from ``manifest.constraints``.

    Each item in ``structured_specs`` is ``(const_name, var_name, bound_kind, value, alt_names)``.
    """
    alias_map = _build_alias_map(manifest.constraints)
    preds, _, _ = _extract_constraints_predicates(
        manifest.constraints, alias_map, scope_prefix="global"
    )
    _, raw_intervals, _, _, _ = _propagate_python_intervals(preds)

    # Track per-var aliases from manifest.constraints dicts
    var_aliases: dict[str, list[str]] = {}
    eq_vars: dict[str, int] = {}
    for raw_key, raw_val in manifest.constraints.items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            continue
        canon, role = _canonical_var_from_key(raw_key, alias_map)
        aliases = var_aliases.setdefault(canon, [])
        if isinstance(raw_val, dict):
            raw_al = raw_val.get("aliases")
            if isinstance(raw_al, list):
                for a in raw_al:
                    if isinstance(a, str) and a.strip() and a.strip() not in aliases:
                        aliases.append(a.strip())
            eq_val = _as_exact_int(raw_val.get("eq", raw_val.get("exact", raw_val.get("value"))))
            if eq_val is not None:
                eq_vars[canon] = eq_val
        elif role == "eq":
            eq_val = _as_exact_int(raw_val)
            if eq_val is not None:
                eq_vars[canon] = eq_val

    expected: dict[str, int] = {}
    specs: list[tuple[str, str, str, int, tuple[str, ...]]] = []

    for var in sorted(raw_intervals.keys()):
        lo, hi = raw_intervals[var]
        upper_var = _sanitize_cpp_ident(var)
        alias_idents = [_sanitize_cpp_ident(a) for a in var_aliases.get(var, [])]

        if var in eq_vars:
            eq_val = eq_vars[var]
            cname = upper_var
            alt = tuple(dict.fromkeys([f"{upper_var}_EQ", *alias_idents]))
            expected[cname] = eq_val
            specs.append((cname, var, "eq", eq_val, alt))
            continue

        if lo is not None:
            cname = f"{upper_var}_MIN"
            alts = [f"MIN_{upper_var}", f"MIN{upper_var}"]
            for a_id in alias_idents:
                alts.extend([f"{a_id}_MIN", f"MIN_{a_id}", f"MIN{a_id}"])
            expected[cname] = lo
            specs.append((cname, var, "min", lo, tuple(dict.fromkeys(alts))))

        if hi is not None:
            cname = f"{upper_var}_MAX"
            alts = [f"MAX_{upper_var}", f"MAX{upper_var}"]
            for a_id in alias_idents:
                alts.extend([f"{a_id}_MAX", f"MAX_{a_id}", f"MAX{a_id}"])
            expected[cname] = hi
            specs.append((cname, var, "max", hi, tuple(dict.fromkeys(alts))))

    return expected, specs


def generate_constraints_header(manifest: AutoCodeManifest) -> str:
    """Generate an ``rbx``-style single-source C++ ``constexpr`` header for ``files/constraints.h``."""
    _, specs = _extract_expected_header_specs(manifest)
    lines: list[str] = [
        "// Auto-generated by AutoCode from .autocode/manifest.json (single-source constraints)",
        "#ifndef AUTOCODE_CONSTRAINTS_H",
        "#define AUTOCODE_CONSTRAINTS_H",
        "",
        "#include <cstdint>",
        "",
        "namespace autocode::constraints {",
        "",
    ]
    if specs:
        for const_name, _, _, value, _ in specs:
            lines.append(
                f"inline constexpr std::int64_t {const_name} = {_format_cpp_int64_literal(value)};"
            )
    else:
        lines.append("// No integer constraint bounds declared in manifest.constraints.")

    # Emit subtask constants inside a dedicated nested namespace when defined
    subtask_lines: list[str] = []
    alias_map = _build_alias_map(manifest.constraints)
    for st in manifest.subtasks:
        st_id, _, st_constraints = _subtask_fields(st)
        st_alias = dict(alias_map)
        st_alias.update(_build_alias_map(st_constraints))
        st_preds, _, _ = _extract_constraints_predicates(
            st_constraints, st_alias, scope_prefix=f"subtask:{st_id}"
        )
        _, st_iv, _, _, _ = _propagate_python_intervals(st_preds)
        for var in sorted(st_iv.keys()):
            lo, hi = st_iv[var]
            uvar = _sanitize_cpp_ident(var)
            if lo is not None:
                subtask_lines.append(
                    f"inline constexpr std::int64_t SUBTASK_{st_id}_{uvar}_MIN = {_format_cpp_int64_literal(lo)};"
                )
            if hi is not None:
                subtask_lines.append(
                    f"inline constexpr std::int64_t SUBTASK_{st_id}_{uvar}_MAX = {_format_cpp_int64_literal(hi)};"
                )
    if subtask_lines:
        lines.append("")
        lines.extend(subtask_lines)

    lines.extend(["", "}  // namespace autocode::constraints", ""])
    for const_name, _, _, _, _ in specs:
        lines.append(f"using autocode::constraints::{const_name};")
    if specs:
        lines.append("")
    lines.append("#endif  // AUTOCODE_CONSTRAINTS_H")
    lines.append("")
    return "\n".join(lines)


def parse_cpp_header_constants(header_text: str) -> dict[str, int]:
    """Extract constant integer definitions from a C++ header without ``eval()``."""
    no_comments = re.sub(r"/\*.*?\*/", "", header_text, flags=re.DOTALL)
    no_comments = re.sub(r"//[^\n]*", "", no_comments)

    constants: dict[str, int] = {}
    decl_re = re.compile(
        r"(?:inline\s+)?(?:static\s+)?(?:const|constexpr)\s+"
        r"(?:std::)?(?:unsigned\s+|signed\s+)?"
        r"(?:long\s+long|long|int|int64_t|int32_t|uint64_t|uint32_t|size_t|ll)\s+"
        r"([A-Za-z_]\w*)\s*=\s*([^;]+);"
    )
    for m in decl_re.finditer(no_comments):
        parsed = parse_cpp_int_expr(m.group(2), constants)
        if parsed is not None:
            constants[m.group(1)] = parsed
    for m in re.finditer(r"#\s*define\s+([A-Za-z_]\w*)\s+([^\n]+)", no_comments):
        parsed = parse_cpp_int_expr(m.group(2), constants)
        if parsed is not None:
            constants[m.group(1)] = parsed
    return constants


def check_constraints_header_drift(
    header_source: Path | str,
    manifest: AutoCodeManifest,
) -> ConstraintsHeaderDriftReport:
    """Compare C++ constants in ``files/constraints.h`` against ``manifest.constraints``."""
    header_text: str | None = None
    if isinstance(header_source, Path):
        if not header_source.is_file():
            expected_only, _ = _extract_expected_header_specs(manifest)
            return ConstraintsHeaderDriftReport(
                exists=False,
                has_drift=False,
                mismatches=(),
                missing_constants=(),
                parsed_constants={},
                expected_constants=expected_only,
                summary="",
            )
        header_text = header_source.read_text(encoding="utf-8", errors="replace")
    else:
        # Distinguish inline header content vs file path string
        if "\n" not in header_source and not any(
            tok in header_source for tok in ("#ifndef", "#define", "constexpr", "const ", ";")
        ):
            cand_path = Path(header_source)
            if not cand_path.is_file():
                expected_only, _ = _extract_expected_header_specs(manifest)
                return ConstraintsHeaderDriftReport(
                    exists=False,
                    has_drift=False,
                    mismatches=(),
                    missing_constants=(),
                    parsed_constants={},
                    expected_constants=expected_only,
                    summary="",
                )
            header_text = cand_path.read_text(encoding="utf-8", errors="replace")
        else:
            header_text = header_source

    parsed_constants = parse_cpp_header_constants(header_text)
    parsed_upper = {k.upper(): (k, v) for k, v in parsed_constants.items()}
    expected_constants, specs = _extract_expected_header_specs(manifest)

    mismatches: list[HeaderConstantMismatch] = []
    missing_constants: list[str] = []

    for const_name, var_name, kind, exp_val, alt_names in specs:
        matched_entry = parsed_upper.get(const_name.upper())
        if matched_entry is None:
            for alt in alt_names:
                matched_entry = parsed_upper.get(alt.upper())
                if matched_entry is not None:
                    break
        if matched_entry is None:
            missing_constants.append(const_name)
            mismatches.append(
                HeaderConstantMismatch(
                    constant_name=const_name,
                    var=var_name,
                    bound_kind=kind,
                    header_value=None,
                    manifest_value=exp_val,
                    reason=f"{const_name} (expected {exp_val}) missing from constraints.h",
                )
            )
            continue
        actual_name, actual_val = matched_entry
        if actual_val != exp_val:
            mismatches.append(
                HeaderConstantMismatch(
                    constant_name=actual_name,
                    var=var_name,
                    bound_kind=kind,
                    header_value=actual_val,
                    manifest_value=exp_val,
                    reason=f"constraints.h {actual_name}={actual_val} != manifest {var_name}.{kind}={exp_val}",
                )
            )

    has_drift = bool(mismatches)
    summary = "; ".join(m.reason for m in mismatches)
    return ConstraintsHeaderDriftReport(
        exists=True,
        has_drift=has_drift,
        mismatches=tuple(mismatches),
        missing_constants=tuple(missing_constants),
        parsed_constants=parsed_constants,
        expected_constants=expected_constants,
        summary=summary,
    )
