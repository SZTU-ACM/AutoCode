"""Validator-preserving counterexample shrinking for stress testing.

Iteratively minimizes failing competitive-programming inputs using line-level
``ddmin``, count-coupled structural ``ddmin``, token-chunk ``ddmin``, and integer
binary-search reduction toward ``0``/``1``/min. Reductions are accepted ONLY when
both the optional compiled validator accepts (``return_code == 0``) and the
async failure predicate continues to trigger, within a strict bounded budget
and wall-clock timeout.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .compiler import run_binary

_INT_TOKEN_RE = re.compile(r"(?:(?<=\s)|^)[+-]?\d+(?=\s|$)")
_SIMPLE_INT_RE = re.compile(r"^[+-]?\d+$")


@dataclass(frozen=True)
class ShrinkResult:
    """Result returned by :func:`shrink_counterexample`."""

    shrunk_input: str
    original_input: str
    original_size: int
    shrunk_size: int
    shrink_steps: int
    evaluations: int
    timed_out: bool
    budget_exhausted: bool
    validator_checked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "shrunk_input": self.shrunk_input,
            "original_size": self.original_size,
            "shrunk_size": self.shrunk_size,
            "shrink_steps": self.shrink_steps,
            "evaluations": self.evaluations,
            "timed_out": self.timed_out,
            "budget_exhausted": self.budget_exhausted,
            "validator_checked": self.validator_checked,
        }


class _ShrinkBudgetError(Exception):
    """Internal control-flow signal when evaluation budget or timeout is reached."""

    def __init__(self, *, timed_out: bool, budget_exhausted: bool) -> None:
        super().__init__("shrink budget exhausted")
        self.timed_out = timed_out
        self.budget_exhausted = budget_exhausted


def _replace_line_token(line: str, token_index: int, new_token: str) -> str | None:
    """Replace the ``token_index``-th whitespace-delimited token on ``line`` preserving spacing."""
    matches = list(re.finditer(r"\S+", line))
    if token_index < 0 or token_index >= len(matches):
        return None
    m = matches[token_index]
    return line[: m.start()] + new_token + line[m.end() :]


def _join_lines(lines: Sequence[str], *, trailing_newline: bool) -> str:
    if not lines:
        return ""
    body = "\n".join(lines)
    return f"{body}\n" if trailing_newline else body


def _make_chunks(length: int, n_chunks: int) -> list[tuple[int, int]]:
    n = max(2, min(n_chunks, length))
    base, rem = divmod(length, n)
    chunks: list[tuple[int, int]] = []
    pos = 0
    for idx in range(n):
        size = base + (1 if idx < rem else 0)
        if size > 0:
            chunks.append((pos, pos + size))
            pos += size
    return chunks


async def shrink_counterexample(
    input_data: str,
    predicate: Callable[[str], Awaitable[bool]],
    *,
    validator_exe: str | os.PathLike[str] | None = None,
    validator_timeout: float = 5.0,
    max_evaluations: int = 200,
    timeout_sec: float = 10.0,
    memory_mb: float = 256.0,
) -> ShrinkResult:
    """Minimize ``input_data`` while keeping validator acceptance and ``predicate`` true.

    Args:
        input_data: Original failing test input string.
        predicate: Async callable returning ``True`` when the candidate input still
            reproduces the target failure (e.g. ``sol`` vs ``brute`` mismatch).
        validator_exe: Optional path to compiled testlib/custom validator binary.
            When provided, reductions are rejected unless ``return_code == 0``.
        validator_timeout: Per-invocation timeout (seconds) for ``validator_exe``.
        max_evaluations: Maximum candidate evaluations (excluding memoized repeats).
        timeout_sec: Total wall-clock shrinking timeout in seconds.
        memory_mb: Memory cap passed to :func:`run_binary` when invoking validator.
    """
    orig_size = len(input_data)
    val_path = os.fspath(validator_exe) if validator_exe is not None else None
    has_validator = bool(val_path and val_path.strip())
    if not input_data or max_evaluations <= 0 or timeout_sec <= 0:
        return ShrinkResult(
            shrunk_input=input_data,
            original_input=input_data,
            original_size=orig_size,
            shrunk_size=orig_size,
            shrink_steps=0,
            evaluations=0,
            timed_out=False,
            budget_exhausted=False,
            validator_checked=has_validator,
        )

    safe_budget = max(1, int(max_evaluations))
    deadline = time.monotonic() + max(0.05, float(timeout_sec))
    eval_cache: dict[str, bool] = {}
    evaluations = 0
    best = input_data
    shrink_steps = 0
    timed_out = False
    budget_exhausted = False

    async def _evaluate(candidate: str) -> bool:
        nonlocal evaluations
        if candidate in eval_cache:
            return eval_cache[candidate]
        if time.monotonic() >= deadline:
            raise _ShrinkBudgetError(timed_out=True, budget_exhausted=False)
        if evaluations >= safe_budget:
            raise _ShrinkBudgetError(timed_out=False, budget_exhausted=True)

        evaluations += 1
        if has_validator and val_path is not None:
            if not os.path.isfile(val_path):
                eval_cache[candidate] = False
                return False
            rem_time = max(0.1, deadline - time.monotonic())
            v_run = await run_binary(
                val_path,
                candidate,
                timeout=min(float(validator_timeout), rem_time),
                memory_mb=memory_mb,
            )
            if v_run.timed_out or v_run.return_code != 0:
                eval_cache[candidate] = False
                return False

        if time.monotonic() >= deadline:
            raise _ShrinkBudgetError(timed_out=True, budget_exhausted=False)

        ok = bool(await predicate(candidate))
        eval_cache[candidate] = ok
        return ok

    def _record_improvement(candidate: str) -> None:
        nonlocal best, shrink_steps
        if candidate != best:
            best = candidate
            shrink_steps += 1

    async def _ddmin_sequence(
        items: Sequence[Any],
        render: Callable[[list[Any]], str],
    ) -> list[Any]:
        seq = list(items)
        if len(seq) < 2:
            return seq
        n = 2
        while len(seq) >= 2:
            chunks = _make_chunks(len(seq), n)
            reduced = False

            # When granularity > 2, try isolated chunks first to jump directly to small subsets.
            if len(chunks) > 2:
                for start, end in chunks:
                    subset = seq[start:end]
                    if not subset:
                        continue
                    cand = render(subset)
                    if cand and cand != best and len(cand) < len(best):
                        if await _evaluate(cand):
                            _record_improvement(cand)
                            seq = subset
                            n = 2
                            reduced = True
                            break
                if reduced:
                    continue

            # Try complements (removing one chunk at a time).
            for start, end in chunks:
                complement = seq[:start] + seq[end:]
                if not complement:
                    continue
                cand = render(complement)
                if cand and cand != best and len(cand) < len(best):
                    if await _evaluate(cand):
                        _record_improvement(cand)
                        seq = complement
                        n = max(2, n - 1)
                        reduced = True
                        break
            if reduced:
                continue

            if n >= len(seq):
                break
            n = min(len(seq), n * 2)
        return seq

    async def _line_ddmin_pass() -> bool:
        raw_lines = best.splitlines(keepends=True)
        if len(raw_lines) < 2:
            return False
        trailing_nl = best.endswith("\n")

        def _render_lines(subset: list[str]) -> str:
            out = "".join(subset)
            if trailing_nl and out and not out.endswith("\n"):
                out += "\n"
            return out

        before = best
        await _ddmin_sequence(raw_lines, _render_lines)
        return best != before

    async def _count_coupled_ddmin_pass() -> bool:
        """Shrink count headers (e.g. ``n`` or ``T``) paired with ``k * stride`` data groups."""
        before = best
        trailing_nl = best.endswith("\n")
        lines = best.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            return False

        for li, hdr_line in enumerate(lines):
            hdr_tokens = hdr_line.split()
            for ti, tok in enumerate(hdr_tokens):
                if not _SIMPLE_INT_RE.fullmatch(tok):
                    continue
                k = int(tok)
                if k < 2 or k > 100_000:
                    continue

                rem_lines = lines[li + 1 :]
                # Case A: ``k * stride`` lines follow this header line (e.g. edge lists / per-line elements).
                if rem_lines and len(rem_lines) % k == 0 and (len(rem_lines) // k) <= 16:
                    stride = len(rem_lines) // k
                    groups_a = [rem_lines[g * stride : (g + 1) * stride] for g in range(k)]

                    def _render_a(
                        kept: list[list[str]],
                        _li: int = li,
                        _ti: int = ti,
                        _hdr: str = hdr_line,
                    ) -> str:
                        updated_hdr = _replace_line_token(_hdr, _ti, str(len(kept)))
                        if updated_hdr is None:
                            return ""
                        out_lines = [
                            *lines[:_li],
                            updated_hdr,
                            *[ln for grp in kept for ln in grp],
                        ]
                        return _join_lines(out_lines, trailing_newline=trailing_nl)

                    await _ddmin_sequence(groups_a, _render_a)
                    if best != before:
                        return True

                # Case B: immediate next line holds ``k * stride`` tokens (classic ``n\na1..an``).
                if li + 1 < len(lines):
                    next_tokens = lines[li + 1].split()
                    if (
                        next_tokens
                        and len(next_tokens) % k == 0
                        and (len(next_tokens) // k) <= 8
                    ):
                        stride_b = len(next_tokens) // k
                        groups_b = [
                            next_tokens[g * stride_b : (g + 1) * stride_b] for g in range(k)
                        ]

                        def _render_b(
                            kept: list[list[str]],
                            _li: int = li,
                            _ti: int = ti,
                            _hdr: str = hdr_line,
                        ) -> str:
                            updated_hdr = _replace_line_token(_hdr, _ti, str(len(kept)))
                            if updated_hdr is None:
                                return ""
                            merged_next = " ".join(item for grp in kept for item in grp)
                            out_lines = [
                                *lines[:_li],
                                updated_hdr,
                                merged_next,
                                *lines[_li + 2 :],
                            ]
                            return _join_lines(out_lines, trailing_newline=trailing_nl)

                        await _ddmin_sequence(groups_b, _render_b)
                        if best != before:
                            return True

                # Case C: all remaining tokens across ``lines[li + 1 :]`` equal ``k * stride``.
                if rem_lines:
                    rem_tokens = [item for ln in rem_lines for item in ln.split()]
                    if (
                        rem_tokens
                        and len(rem_tokens) % k == 0
                        and (len(rem_tokens) // k) <= 8
                        and len(rem_tokens) != len(rem_lines)
                    ):
                        stride_c = len(rem_tokens) // k
                        groups_c = [
                            rem_tokens[g * stride_c : (g + 1) * stride_c] for g in range(k)
                        ]

                        def _render_c(
                            kept: list[list[str]],
                            _li: int = li,
                            _ti: int = ti,
                            _hdr: str = hdr_line,
                        ) -> str:
                            updated_hdr = _replace_line_token(_hdr, _ti, str(len(kept)))
                            if updated_hdr is None:
                                return ""
                            merged = " ".join(item for grp in kept for item in grp)
                            out_lines = [*lines[:_li], updated_hdr, merged]
                            return _join_lines(out_lines, trailing_newline=trailing_nl)

                        await _ddmin_sequence(groups_c, _render_c)
                        if best != before:
                            return True

                # Case D: single-line ``... k e1 e2 ... ek`` layout.
                after_tokens = hdr_tokens[ti + 1 :]
                if after_tokens and len(after_tokens) % k == 0 and (len(after_tokens) // k) <= 8:
                    stride_d = len(after_tokens) // k
                    groups_d = [
                        after_tokens[g * stride_d : (g + 1) * stride_d] for g in range(k)
                    ]

                    def _render_d(
                        kept: list[list[str]],
                        _li: int = li,
                        _ti: int = ti,
                        _hdr_tokens: list[str] = hdr_tokens,
                    ) -> str:
                        prefix = [*_hdr_tokens[:_ti], str(len(kept))]
                        body_items = [item for grp in kept for item in grp]
                        joined = " ".join([*prefix, *body_items])
                        out_lines = [*lines[:_li], joined, *lines[_li + 1 :]]
                        return _join_lines(out_lines, trailing_newline=trailing_nl)

                    await _ddmin_sequence(groups_d, _render_d)
                    if best != before:
                        return True
        return False

    async def _token_ddmin_pass() -> bool:
        before = best
        trailing_nl = best.endswith("\n")
        lines = best.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()

        # Per-line token ddmin (preserves line boundaries for strict readEoln validators).
        for li in range(len(lines)):
            tokens = lines[li].split()
            if len(tokens) < 2:
                continue

            def _render_line_tokens(kept: list[str], _li: int = li) -> str:
                cur_lines = list(lines)
                cur_lines[_li] = " ".join(kept)
                return _join_lines(cur_lines, trailing_newline=trailing_nl)

            await _ddmin_sequence(tokens, _render_line_tokens)
            if best != before:
                return True

        # Cross-line token ddmin fallback when line breaks are non-essential.
        all_tokens = best.split()
        if len(all_tokens) >= 2 and len(lines) > 1:

            def _render_flat(kept: list[str]) -> str:
                body = " ".join(kept)
                return f"{body}\n" if trailing_nl else body

            await _ddmin_sequence(all_tokens, _render_flat)
        return best != before

    async def _integer_binary_search_pass() -> bool:
        before = best
        matches = list(_INT_TOKEN_RE.finditer(best))
        total_ints = len(matches)
        for idx in range(total_ints):
            cur_matches = list(_INT_TOKEN_RE.finditer(best))
            if idx >= len(cur_matches):
                break
            m = cur_matches[idx]
            raw_tok = m.group(0)
            if len(raw_tok.lstrip("+-")) > 18:
                continue
            cur_val = int(raw_tok)
            if cur_val == 0:
                continue

            def _with_int(new_v: int, _s: int = m.start(), _e: int = m.end()) -> str:
                return best[:_s] + str(new_v) + best[_e:]

            if cur_val > 0:
                cand_zero = _with_int(0)
                if cand_zero != best and await _evaluate(cand_zero):
                    _record_improvement(cand_zero)
                    continue
                if cur_val > 1:
                    cand_one = _with_int(1)
                    if cand_one != best and await _evaluate(cand_one):
                        _record_improvement(cand_one)
                        continue
                if cur_val > 2:
                    lo = 2
                    hi = cur_val - 1
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        cur_matches_inner = list(_INT_TOKEN_RE.finditer(best))
                        if idx >= len(cur_matches_inner):
                            break
                        mi = cur_matches_inner[idx]
                        cand_mid = best[: mi.start()] + str(mid) + best[mi.end() :]
                        if cand_mid != best and await _evaluate(cand_mid):
                            _record_improvement(cand_mid)
                            hi = mid - 1
                        else:
                            lo = mid + 1
            else:
                cand_zero = _with_int(0)
                if cand_zero != best and await _evaluate(cand_zero):
                    _record_improvement(cand_zero)
                    continue
                if cur_val < -1:
                    cand_neg_one = _with_int(-1)
                    if cand_neg_one != best and await _evaluate(cand_neg_one):
                        _record_improvement(cand_neg_one)
                        continue
                if cur_val < -2:
                    lo = cur_val + 1
                    hi = -2
                    while lo <= hi:
                        mid = (lo + hi + 1) // 2
                        cur_matches_inner = list(_INT_TOKEN_RE.finditer(best))
                        if idx >= len(cur_matches_inner):
                            break
                        mi = cur_matches_inner[idx]
                        cand_mid = best[: mi.start()] + str(mid) + best[mi.end() :]
                        if cand_mid != best and await _evaluate(cand_mid):
                            _record_improvement(cand_mid)
                            lo = mid + 1
                        else:
                            hi = mid - 1

        return best != before

    try:
        if not await _evaluate(best):
            return ShrinkResult(
                shrunk_input=best,
                original_input=input_data,
                original_size=orig_size,
                shrunk_size=len(best),
                shrink_steps=0,
                evaluations=evaluations,
                timed_out=False,
                budget_exhausted=False,
                validator_checked=has_validator,
            )

        while True:
            progressed = False
            if await _line_ddmin_pass():
                progressed = True
            if await _count_coupled_ddmin_pass():
                progressed = True
            if await _token_ddmin_pass():
                progressed = True
            if await _integer_binary_search_pass():
                progressed = True
            if not progressed:
                break
    except _ShrinkBudgetError as stop:
        timed_out = stop.timed_out
        budget_exhausted = stop.budget_exhausted

    return ShrinkResult(
        shrunk_input=best,
        original_input=input_data,
        original_size=orig_size,
        shrunk_size=len(best),
        shrink_steps=shrink_steps,
        evaluations=evaluations,
        timed_out=timed_out,
        budget_exhausted=budget_exhausted,
        validator_checked=has_validator,
    )
