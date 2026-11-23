"""Safe Markdown and summary formatters for ``autocode-audit`` CLI output."""

from __future__ import annotations

import re
from typing import Any

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\][^\x07\x1b]*(?:\x07|\x1b\\)|\[[0-?]*[ -/]*[@-~]|[@-Z\\-_])"
)
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LINE_BREAK_RE = re.compile(r"[\r\n\t]+")


def _strip_ansi_and_control(text: str) -> str:
    """Remove ANSI escape sequences and non-printable control characters."""
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    return _CTRL_CHARS_RE.sub("", cleaned)


def _collapse_inline(value: object, *, max_len: int) -> str:
    text = _strip_ansi_and_control("" if value is None else str(value))
    text = _LINE_BREAK_RE.sub(" ", text).strip()
    if max_len >= 4 and len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text


def _escape_md_cell(value: object, *, max_len: int = 300) -> str:
    """Escape untrusted text for safe inclusion inside Markdown prose and GFM table cells."""
    text = _collapse_inline(value, max_len=max_len)
    if not text:
        return "—"
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("`", "\\`")
    )


def _md_code(value: object, *, max_len: int = 120) -> str:
    """Wrap untrusted text as an inline Markdown code span safe inside GFM tables."""
    text = _collapse_inline(value, max_len=max_len)
    if not text:
        return "—"
    text = text.replace("|", "\\|")
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    needs_pad = text.startswith("`") or text.endswith("`")
    return f"{fence} {text} {fence}" if needs_pad else f"{fence}{text}{fence}"


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _fmt_num(val: object, suffix: str = "") -> str:
    if isinstance(val, bool) or not isinstance(val, int | float):
        return "—"
    if isinstance(val, int):
        return f"{val}{suffix}"
    return f"{val:.2f}{suffix}".rstrip("0").rstrip(".") if suffix == "x" else f"{val}{suffix}"


def _signal_status_label(sig: object) -> str:
    if not isinstance(sig, dict):
        return "SKIPPED"
    if sig.get("applicable") is False or sig.get("status") == "not_applicable":
        return "N/A"
    if not sig.get("executed", True):
        return "SKIPPED"
    return "PASS" if sig.get("passed") else "FAIL"


def _signal_detail(sig: object) -> str:
    if not isinstance(sig, dict):
        return "—"
    ev = sig.get("evidence")
    if not isinstance(ev, dict):
        return "—"
    for key in ("message", "reason", "error"):
        val = ev.get(key)
        if isinstance(val, str) and val.strip():
            return _escape_md_cell(val, max_len=200)
    if "total" in ev:
        return _escape_md_cell(f"total={ev['total']}", max_len=200)
    if "path" in ev:
        return _md_code(ev["path"], max_len=120)
    if "kill_rate" in ev and ev["kill_rate"] is not None:
        return _escape_md_cell(f"kill_rate={ev['kill_rate']}", max_len=120)
    return "—"


def format_audit_markdown(output: dict[str, Any]) -> str:
    """Render an ``autocode-audit`` result dict as an injection-safe Markdown report."""
    lines: list[str] = ["# AutoCode Problem Audit Report", ""]

    if not output.get("success"):
        err = _escape_md_cell(output.get("error") or "Unknown audit failure", max_len=500)
        lines.extend(
            [
                "**Verdict:** ❌ ERROR",
                "",
                "| Field | Value |",
                "|---|---|",
                "| Decision | `error` |",
                f"| Error | {err} |",
            ]
        )
        return "\n".join(lines)

    raw_data = output.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    decision = str(data.get("decision") or "no_go")
    badge = "✅ GO" if decision == "go" else "❌ NO-GO"

    raw_blocking = data.get("blocking_issues")
    blocking: list[Any] = raw_blocking if isinstance(raw_blocking, list) else []
    risk_report = data.get("risk_report")
    warnings: list[Any] = (
        risk_report.get("warnings", [])
        if isinstance(risk_report, dict) and isinstance(risk_report.get("warnings"), list)
        else []
    )
    package_format = (
        risk_report.get("package_format")
        if isinstance(risk_report, dict)
        else None
    )

    lines.extend(
        [
            f"**Verdict:** {badge}",
            "",
            "| Field | Value |",
            "|---|---|",
            f"| Decision | {_md_code(decision)} |",
            f"| Target | {_md_code(data.get('problem_dir') or '.')} |",
            f"| Layout | {_md_code(data.get('detected_layout') or 'auto')} |",
            f"| Mode | {_md_code(data.get('mode') or 'full')} |",
            f"| Standalone | {_md_code(bool(data.get('standalone')))} |",
            f"| Package Format | {_escape_md_cell(package_format or '—')} |",
            f"| Blocking Issues | {len(blocking)} |",
            f"| Warnings | {len(warnings)} |",
            f"| Generated At | {_escape_md_cell(data.get('generated_at') or '—')} |",
        ]
    )

    if blocking:
        lines.extend(["", "## Blocking Issues", "", "| Gate | Reason |", "|---|---|"])
        for item in blocking:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {_md_code(item.get('gate') or 'unknown')} "
                f"| {_escape_md_cell(item.get('reason') or '—')} |"
            )

    if warnings:
        lines.extend(["", "## Warnings", "", "| Gate | Detail |", "|---|---|"])
        for item in warnings:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"| {_md_code(item.get('gate') or 'warning')} "
                f"| {_escape_md_cell(item.get('reason') or item.get('message') or '—')} |"
            )

    structural = data.get("structural_signals")
    quality = data.get("quality_signals")
    signal_rows: list[tuple[str, str, dict[str, Any]]] = []
    if isinstance(structural, dict):
        for name, sig in structural.items():
            if isinstance(sig, dict):
                signal_rows.append(("structural", str(name), sig))
    if isinstance(quality, dict):
        for name, sig in quality.items():
            if isinstance(sig, dict):
                signal_rows.append(("quality", str(name), sig))

    if signal_rows:
        lines.extend(
            [
                "",
                "## Signal Overview",
                "",
                "| Category | Signal | Status | Detail |",
                "|---|---|---|---|",
            ]
        )
        for category, name, sig in signal_rows:
            lines.append(
                f"| {_md_code(category)} | {_md_code(name)} | "
                f"{_signal_status_label(sig)} | {_signal_detail(sig)} |"
            )

    if isinstance(quality, dict):
        emp_sig = quality.get("empirical_complexity")
        if isinstance(emp_sig, dict) and emp_sig.get("executed"):
            emp_ev = _as_dict(emp_sig.get("evidence"))
            samples = emp_ev.get("samples")
            sample_count = len(samples) if isinstance(samples, list) else 0
            lines.extend(
                [
                    "",
                    "## Empirical Complexity",
                    "",
                    "| Metric | Value |",
                    "|---|---|",
                    f"| Passed | {_md_code(bool(emp_sig.get('passed')))} |",
                    f"| Claimed | {_md_code(emp_ev.get('claimed_complexity') or '—')} |",
                    f"| Fitted | {_md_code(emp_ev.get('fitted_complexity') or '—')} |",
                    f"| Verdict | {_md_code(emp_ev.get('verdict') or '—')} |",
                    f"| Fitted alpha | {_fmt_num(emp_ev.get('fitted_alpha'))} |",
                    f"| R^2 | {_fmt_num(emp_ev.get('r_squared'))} |",
                    f"| Scale Samples | {sample_count} |",
                ]
            )
            failure_reason = emp_ev.get("failure_reason")
            if isinstance(failure_reason, str) and failure_reason.strip():
                lines.append(f"| Failure Reason | {_escape_md_cell(failure_reason)} |")

        wrong_sig = quality.get("wrong_solution_kill")
        if isinstance(wrong_sig, dict) and wrong_sig.get("executed"):
            wrong_ev = _as_dict(wrong_sig.get("evidence"))
            details = wrong_ev.get("details")
            if isinstance(details, list) and details:
                lines.extend(
                    [
                        "",
                        "## Wrong Solution Kill Matrix",
                        "",
                        "| Solution | Expected | Killed | Verdict / Test | Max Time (ms) | Ratio vs Std |",
                        "|---|---|---|---|---|---|",
                    ]
                )
                for row in details:
                    if not isinstance(row, dict):
                        continue
                    v_parts: list[str] = []
                    if row.get("killing_verdict"):
                        v_parts.append(str(row["killing_verdict"]))
                    if row.get("killing_test"):
                        v_parts.append(f"@ {row['killing_test']}")
                    elif row.get("reason"):
                        v_parts.append(str(row["reason"]))
                    verdict_cell = _escape_md_cell(" ".join(v_parts) if v_parts else "—", max_len=140)
                    killed_flag = "YES" if row.get("killed") else "NO"
                    lines.append(
                        f"| {_md_code(row.get('name') or 'unknown')} "
                        f"| {_md_code(row.get('expected') or 'fail')} "
                        f"| {killed_flag} "
                        f"| {verdict_cell} "
                        f"| {_fmt_num(row.get('max_time_ms'))} "
                        f"| {_fmt_num(row.get('max_ratio_to_std'), 'x')} |"
                    )

        mut_sig = quality.get("mutation_testing")
        if isinstance(mut_sig, dict) and mut_sig.get("executed"):
            mut_ev = _as_dict(mut_sig.get("evidence"))
            kill_rate = mut_ev.get("kill_rate")
            kill_rate_str = (
                f"{float(kill_rate) * 100:.1f}%"
                if isinstance(kill_rate, int | float) and not isinstance(kill_rate, bool)
                else "—"
            )
            lines.extend(
                [
                    "",
                    "## Mutation Testing",
                    "",
                    "| Metric | Value |",
                    "|---|---|",
                    f"| Compiled Mutants | {_fmt_num(mut_ev.get('compiled_mutants'))} |",
                    f"| Killed Mutants | {_fmt_num(mut_ev.get('killed_mutants'))} |",
                    f"| Survived Mutants | {_fmt_num(mut_ev.get('survived_mutants'))} |",
                    f"| Judge Error Mutants | {_fmt_num(mut_ev.get('judge_error_mutants'))} |",
                    f"| Kill Rate | {kill_rate_str} |",
                ]
            )
            survivors = mut_ev.get("survivors")
            if isinstance(survivors, list) and survivors:
                lines.extend(
                    [
                        "",
                        "| Mutant ID | Line | Type | Original | Mutated |",
                        "|---|---|---|---|---|",
                    ]
                )
                for surv in survivors:
                    if not isinstance(surv, dict):
                        continue
                    lines.append(
                        f"| {_md_code(surv.get('id') or '—')} "
                        f"| {_fmt_num(surv.get('line'))} "
                        f"| {_md_code(surv.get('mutation_type') or '—')} "
                        f"| {_md_code(surv.get('original') or '—', max_len=80)} "
                        f"| {_md_code(surv.get('mutated') or '—', max_len=80)} |"
                    )

    stmt_cons = risk_report.get("statement_consistency") if isinstance(risk_report, dict) else None
    comm_metrics = data.get("communication_metrics")
    comm_obs = comm_metrics.get("observed") if isinstance(comm_metrics, dict) else None
    has_stmt_issue = isinstance(stmt_cons, dict) and stmt_cons.get("passed") is False
    has_comm_obs = isinstance(comm_obs, dict) and bool(comm_obs)
    if has_stmt_issue or has_comm_obs:
        lines.extend(["", "## Statement Consistency & Communication Metrics", ""])
        if has_stmt_issue and isinstance(stmt_cons, dict):
            lines.append(
                f"- Statement consistency: **FAIL** — "
                f"{_escape_md_cell(stmt_cons.get('message') or 'mismatch detected', max_len=300)}"
            )
        if has_comm_obs and isinstance(comm_metrics, dict) and isinstance(comm_obs, dict):
            lines.append(
                f"- Protocol mode: {_md_code(comm_metrics.get('protocol_mode') or '—')} | "
                f"budget_exceeded={bool(comm_metrics.get('budget_exceeded'))}"
            )
            for key, val in sorted(comm_obs.items()):
                lines.append(f"  - {_md_code(key)}: {_escape_md_cell(val, max_len=120)}")

    next_actions = data.get("next_actions")
    if isinstance(next_actions, list) and next_actions:
        lines.extend(["", "## Next Actions", ""])
        for action_item in next_actions:
            if not isinstance(action_item, dict):
                continue
            tool_name = action_item.get("tool_name") or action_item.get("tool") or "action"
            action_desc = action_item.get("action") or "review blocking issue"
            lines.append(
                f"- {_md_code(tool_name)}: {_escape_md_cell(action_desc, max_len=200)}"
            )

    return "\n".join(lines)


def format_audit_summary(output: dict[str, Any]) -> str:
    """Render an ``autocode-audit`` result dict as a compact single-block summary."""
    if not output.get("success"):
        err = _collapse_inline(output.get("error") or "Unknown audit failure", max_len=300)
        return f"ERROR: {err}"

    raw_data = output.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    decision = str(data.get("decision") or "no_go").upper()
    layout = _collapse_inline(data.get("detected_layout") or "auto", max_len=40)
    mode = _collapse_inline(data.get("mode") or "full", max_len=20)
    target = _collapse_inline(data.get("problem_dir") or ".", max_len=160)

    raw_blocking = data.get("blocking_issues")
    blocking: list[Any] = raw_blocking if isinstance(raw_blocking, list) else []
    risk_report = data.get("risk_report")
    warnings: list[Any] = (
        risk_report.get("warnings", [])
        if isinstance(risk_report, dict) and isinstance(risk_report.get("warnings"), list)
        else []
    )

    lines: list[str] = [
        f"[{decision}] target={target} layout={layout} mode={mode} "
        f"standalone={bool(data.get('standalone'))} blocking={len(blocking)} warnings={len(warnings)}"
    ]
    for item in blocking:
        if isinstance(item, dict):
            gate = _collapse_inline(item.get("gate") or "unknown", max_len=60)
            reason = _collapse_inline(item.get("reason") or "—", max_len=200)
            lines.append(f"  BLOCK [{gate}]: {reason}")
    for item in warnings:
        if isinstance(item, dict):
            gate = _collapse_inline(item.get("gate") or "warning", max_len=60)
            reason = _collapse_inline(item.get("reason") or item.get("message") or "—", max_len=200)
            lines.append(f"  WARN  [{gate}]: {reason}")

    quality = data.get("quality_signals")
    if isinstance(quality, dict):
        mut_sig = quality.get("mutation_testing")
        if isinstance(mut_sig, dict) and mut_sig.get("executed"):
            mut_ev = _as_dict(mut_sig.get("evidence"))
            lines.append(
                f"  MUTATION: killed={mut_ev.get('killed_mutants', 0)}/"
                f"{mut_ev.get('compiled_mutants', 0)} "
                f"survived={mut_ev.get('survived_mutants', 0)} "
                f"kill_rate={mut_ev.get('kill_rate')}"
            )
    return "\n".join(lines)
