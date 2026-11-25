#!/usr/bin/env python3
"""Trace-driven reflective skill optimizer with optional GEPA integration.

Reads AutoCode workflow failure traces and ``ProblemAuditTool`` logs (JSON or
JSONL), clusters recurring failure modes (missing negative validator cases,
missing ``std::cout.flush()`` in interactive/communication tasks, skipped
post-generator probes, surviving wrong solutions, duplicate/empty tests, and
statement/SMT constraint drift), optionally delegates candidate evolution to
``gepa`` when installed, and provides a deterministic pure-Python Pareto patch
and prompt-proposal generator when ``gepa`` is absent.
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any


def _try_import_gepa() -> ModuleType | None:
    """Import optional ``gepa`` library when available without hard dependency."""
    try:
        return importlib.import_module("gepa")
    except ImportError:
        return None


@dataclass(frozen=True)
class FailureEvent:
    """Normalized failure trace record extracted from JSON/JSONL logs."""

    trace_id: str
    gate: str
    tool: str
    message: str
    severity: str = "blocking"
    problem_type: str = "standard"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailureRuleSpec:
    """Deterministic pattern matcher and reflective skill remediation template."""

    cluster_id: str
    title: str
    target_skill: str
    target_rel_path: str
    anchor_heading: str
    severity_weight: float
    keywords: tuple[str, ...]
    gates: tuple[str, ...]
    proposed_rule: str
    rationale: str
    prompt_patch: str


@dataclass(frozen=True)
class FailureCluster:
    """Grouped failure mode across one or more traces."""

    cluster_id: str
    title: str
    count: int
    severity_weight: float
    pareto_score: float
    gates: list[str]
    sample_messages: list[str]
    trace_ids: list[str]


@dataclass(frozen=True)
class PatchProposal:
    """Actionable skill edit / reflective prompt proposal for one failure cluster."""

    cluster_id: str
    title: str
    backend: str
    target_skill: str
    target_file: str
    pareto_score: float
    occurrence_count: int
    already_covered: bool
    rationale: str
    proposed_rule: str
    prompt_patch: str
    unified_diff: str
    updated_content: str


@dataclass(frozen=True)
class OptimizationReport:
    """Full trace clustering and skill optimization output."""

    backend: str
    gepa_available: bool
    total_events: int
    clusters: list[FailureCluster]
    proposals: list[PatchProposal]
    applied_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "gepa_available": self.gepa_available,
            "total_events": self.total_events,
            "clusters": [asdict(c) for c in self.clusters],
            "proposals": [asdict(p) for p in self.proposals],
            "applied_files": self.applied_files,
        }


FAILURE_CATALOG: tuple[FailureRuleSpec, ...] = (
    FailureRuleSpec(
        cluster_id="missing_negative_validator_cases",
        title="Validator missing negative test cases or permissive return 0",
        target_skill="autocode-workflow",
        target_rel_path="autocode-workflow/SKILL.md",
        anchor_heading="## Forbidden Actions",
        severity_weight=1.0,
        keywords=(
            "has_negative_cases",
            "negative test case",
            "permissive 'return 0;'",
            "validator_ready",
            "validator_self_test",
            "validator_negative",
        ),
        gates=("validator", "validator_check", "validator_self_test", "validator_ready"),
        proposed_rule=(
            "- Always supply both `expected_valid=True` and `expected_valid=False` fixtures to "
            "`validator_build`; never leave `files/val.cpp` as a permissive `return 0;` stub."
        ),
        rationale=(
            "Agents submitted `validator_build` with positive-only fixtures or a vacuous `return 0;` "
            "validator, leaving downstream `generator_build` blocked or failing `validator_self_test`."
        ),
        prompt_patch=(
            "When calling validator_build, include >=2 near-valid negative inputs "
            "(off-by-one bounds, extra trailing token, bad format) with expected_valid=false."
        ),
    ),
    FailureRuleSpec(
        cluster_id="interactive_missing_stdout_flush",
        title="Interactive interactor missing std::cout.flush() or using tout for contestant",
        target_skill="autocode-workflow",
        target_rel_path="autocode-workflow/SKILL.md",
        anchor_heading="## Forbidden Actions",
        severity_weight=1.0,
        keywords=(
            "std::cout.flush",
            "tout",
            "premature eof",
            "blocked protocol",
            "interactor_self_test",
            "registerinteraction",
            "timed out",
            "flush",
        ),
        gates=("interactor", "interactor_self_test", "interactor_compile", "communication_budget"),
        proposed_rule=(
            "- Interactive interactors MUST prompt contestants on `std::cout` followed by "
            "`std::cout.flush()` / `std::flush` (reserve `tout` strictly for diagnostic logs)."
        ),
        rationale=(
            "Interactive runs stalled or hit premature EOF because the interactor omitted "
            "`std::cout.flush()` or wrote contestant prompts to `tout` instead of `std::cout`."
        ),
        prompt_patch=(
            "In files/interactor.cpp, always use registerInteraction(argc, argv), read from inf/ouf, "
            "and write every contestant prompt via std::cout << ... << '\\n' << std::flush."
        ),
    ),
    FailureRuleSpec(
        cluster_id="post_generator_probe_skip_or_drift",
        title="Post-generator dynamic complexity probe skipped or source/workspace hash drifted",
        target_skill="solution-complexity-audit",
        target_rel_path="solution-complexity-audit/SKILL.md",
        anchor_heading="## Failure Handling",
        severity_weight=0.9,
        keywords=(
            "pending_run",
            "std_empirical_verified",
            "brute_probed",
            "state_drift",
            "ratio_mismatch",
            "empirical_complexity",
            "source newer than binary",
            "stale",
        ),
        gates=("empirical_complexity", "state_drift", "std_empirical", "brute_probe"),
        proposed_rule=(
            "- After `generator_build`, re-run `solution_analyze` / `solution_audit_std` / "
            "`solution_audit_brute` without editing sources so empirical probes execute cleanly; "
            "run `autocode-verify --reconcile` after any out-of-band edit."
        ),
        rationale=(
            "Dynamic complexity verification remained in `pending_run` or workspace state drifted "
            "due to out-of-band source edits without recompilation or reconciliation."
        ),
        prompt_patch=(
            "After generator_build succeeds, re-run solution_audit_std and solution_audit_brute "
            "on unchanged source SHA-256 to clear pending_run dynamic probes non-destructively."
        ),
    ),
    FailureRuleSpec(
        cluster_id="weak_testdata_or_surviving_wrong",
        title="Wrong solution survived all tests or limit_semantics failed",
        target_skill="testdata-quality",
        target_rel_path="testdata-quality/SKILL.md",
        anchor_heading="## Forbidden Behavior",
        severity_weight=0.95,
        keywords=(
            "survived all tests",
            "wrong_solution_kill",
            "expected_verdict",
            "limit_semantics",
            "limit_ratio",
            "scale_distribution",
            "undeclared wrong",
        ),
        gates=("wrong_solution_kill", "limit_semantics", "limit_ratio", "scale_distribution"),
        proposed_rule=(
            "- Do not declare tests complete if any `role=wrong` solution survives or mismatches "
            "`expected_verdict`; persist `val`+`sol`-verified hacks in `tests/hack/` and keep "
            "`type=3` and `type=4` structurally distinct."
        ),
        rationale=(
            "Near-AC wrong solutions passed weak random tests or `expected_verdict` mismatched "
            "(e.g., `RE` instead of `TLE`), or `type=3`/`type=4` lacked structural divergence."
        ),
        prompt_patch=(
            "Verify every wrong_*.cpp against expected_verdict and inspect scale_distribution "
            "and limit_semantics before packaging."
        ),
    ),
    FailureRuleSpec(
        cluster_id="duplicate_or_empty_tests",
        title="Duplicate or empty test files present in tests/",
        target_skill="testdata-quality",
        target_rel_path="testdata-quality/SKILL.md",
        anchor_heading="## Forbidden Behavior",
        severity_weight=0.85,
        keywords=(
            "exact duplicate",
            "duplicate_inputs",
            "no_empty",
            "empty test",
            "file_count",
        ),
        gates=("duplicate_inputs", "no_empty", "file_count"),
        proposed_rule=(
            "- Enforce zero exact byte-identical duplicates and zero empty `.in`/`.ans` files; "
            "deduplicate seeds and verify `file_count` before `problem_audit`."
        ),
        rationale=(
            "Byte-identical duplicate inputs or zero-byte test files caused `duplicate_inputs` or "
            "`no_empty` gate failures."
        ),
        prompt_patch=(
            "Enable deduplication in problem_generate_tests and verify zero exact_duplicates "
            "and zero empty test files."
        ),
    ),
    FailureRuleSpec(
        cluster_id="statement_ambiguity_or_smt_drift",
        title="Statement ambiguity, SMT constraint conflict, or constraints.h drift",
        target_skill="statement-audit",
        target_rel_path="statement-audit/SKILL.md",
        anchor_heading="## Forbidden Behavior",
        severity_weight=0.9,
        keywords=(
            "statement_consistency",
            "constraints.h",
            "smt constraint",
            "second_interpretation",
            "orchid",
            "validator max=",
            "subtask",
        ),
        gates=("statement_consistency", "statement_path", "tutorial_path"),
        proposed_rule=(
            "- Cross-check `statements/README.md`, `.autocode/manifest.json`, `files/constraints.h`, "
            "and `files/val.cpp` via `audit_manifest_constraints` + `check_constraints_header_drift`, "
            "and document a non-diverging `second_interpretation`."
        ),
        rationale=(
            "Statements drifted from `val.cpp`/`constraints.h`, subtasks violated global bounds, "
            "or underspecified wording admitted a diverging second interpretation."
        ),
        prompt_patch=(
            "Run Orchid 4D + Anti-Semantic-Collapse second_interpretation check and verify "
            "constraints.h matches manifest.json before problem_validate."
        ),
    ),
    FailureRuleSpec(
        cluster_id="path_containment_or_forged_state",
        title="Path containment symlink escape or workflow state tampering",
        target_skill="agent-skill-governance",
        target_rel_path="agent-skill-governance/SKILL.md",
        anchor_heading="## Workflow Consistency Rules",
        severity_weight=1.0,
        keywords=(
            "path_containment",
            "symlink",
            "escapes problem_dir",
            "forged",
            "runtime.json",
            "gate_blocked",
        ),
        gates=("path_containment", "gate_blocked", "manifest"),
        proposed_rule=(
            "- Keep all `.autocode/`, `tests/`, `files/`, and `solutions/` paths strictly inside "
            "`problem_dir` (no external symlinks) and never hand-edit `.autocode/runtime.json`."
        ),
        rationale=(
            "External symlinks or out-of-band `.autocode/runtime.json` tampering triggered "
            "`path_containment` or state-reconciliation rejections."
        ),
        prompt_patch=(
            "Never create symlinks escaping problem_dir or mutate .autocode/runtime.json directly; "
            "always progress through MCP tools."
        ),
    ),
)


def _extract_events_from_record(record: dict[str, Any], idx: int) -> list[FailureEvent]:
    """Normalize one JSON/JSONL record (audit report, trace event, or log line)."""
    trace_id = str(record.get("trace_id") or record.get("id") or f"trace-{idx}")
    problem_type = str(record.get("problem_type") or "standard")

    payload = record.get("data") if isinstance(record.get("data"), dict) else record
    if isinstance(payload, dict) and isinstance(payload.get("blocking_issues"), list):
        events: list[FailureEvent] = []
        ptype = str(payload.get("problem_type") or problem_type)
        for b_idx, item in enumerate(payload["blocking_issues"]):
            if not isinstance(item, dict):
                continue
            events.append(
                FailureEvent(
                    trace_id=f"{trace_id}#{b_idx + 1}",
                    gate=str(item.get("gate") or "unknown"),
                    tool=str(record.get("tool") or "problem_audit"),
                    message=str(item.get("reason") or item.get("message") or ""),
                    severity="blocking",
                    problem_type=ptype,
                    raw=item,
                )
            )
        risk = payload.get("risk_report")
        if isinstance(risk, dict) and isinstance(risk.get("warnings"), list):
            for w_idx, item in enumerate(risk["warnings"]):
                if not isinstance(item, dict):
                    continue
                events.append(
                    FailureEvent(
                        trace_id=f"{trace_id}#w{w_idx + 1}",
                        gate=str(item.get("gate") or "warning"),
                        tool=str(record.get("tool") or "problem_audit"),
                        message=str(item.get("reason") or item.get("message") or ""),
                        severity="warning",
                        problem_type=ptype,
                        raw=item,
                    )
                )
        if events:
            return events

    gate = str(record.get("gate") or record.get("failure_mode") or record.get("tool") or "unknown")
    tool = str(record.get("tool") or record.get("tool_name") or "unknown")
    msg_parts = [
        str(record.get(k))
        for k in ("reason", "message", "error", "detail")
        if isinstance(record.get(k), str) and str(record.get(k)).strip()
    ]
    if isinstance(record.get("warnings"), list):
        msg_parts.extend(str(w) for w in record["warnings"])
    message = " | ".join(msg_parts)
    if not message and record.get("decision") == "go":
        return []
    return [
        FailureEvent(
            trace_id=trace_id,
            gate=gate,
            tool=tool,
            message=message or json.dumps(record, ensure_ascii=False),
            severity=str(record.get("severity") or "blocking"),
            problem_type=problem_type,
            raw=record,
        )
    ]


def load_failure_traces(paths: list[Path]) -> list[FailureEvent]:
    """Load and normalize failure events from one or more JSON / JSONL trace files."""
    events: list[FailureEvent] = []
    idx = 0
    for path in paths:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        if path.suffix.lower() == ".jsonl":
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                idx += 1
                obj = json.loads(line)
                if isinstance(obj, dict):
                    events.extend(_extract_events_from_record(obj, idx))
            continue

        parsed = json.loads(text)
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, dict):
                    idx += 1
                    events.extend(_extract_events_from_record(item, idx))
        elif isinstance(parsed, dict):
            if isinstance(parsed.get("traces"), list):
                for item in parsed["traces"]:
                    if isinstance(item, dict):
                        idx += 1
                        events.extend(_extract_events_from_record(item, idx))
            else:
                idx += 1
                events.extend(_extract_events_from_record(parsed, idx))
        else:
            raise ValueError(f"Unsupported JSON trace top-level type in {path}")
    return events


def _classify_event(event: FailureEvent) -> FailureRuleSpec | None:
    """Match a ``FailureEvent`` to the highest-scoring deterministic rule spec."""
    haystack = f"{event.gate} {event.tool} {event.message}".lower()
    best_spec: FailureRuleSpec | None = None
    best_score = 0
    for spec in FAILURE_CATALOG:
        score = 0
        if event.gate.lower() in {g.lower() for g in spec.gates}:
            score += 3
        for kw in spec.keywords:
            if kw.lower() in haystack:
                score += 2
        if score > best_score:
            best_score = score
            best_spec = spec
    return best_spec


def cluster_failure_events(events: list[FailureEvent]) -> list[FailureCluster]:
    """Cluster normalized failure events and compute Pareto priority scores."""
    grouped: dict[str, list[FailureEvent]] = defaultdict(list)
    spec_by_id: dict[str, FailureRuleSpec] = {spec.cluster_id: spec for spec in FAILURE_CATALOG}

    for event in events:
        spec = _classify_event(event)
        cid = spec.cluster_id if spec is not None else "unclassified_failure"
        grouped[cid].append(event)

    clusters: list[FailureCluster] = []
    for cid, group in grouped.items():
        spec = spec_by_id.get(cid)
        severity_w = spec.severity_weight if spec is not None else 0.5
        blocking_bonus = sum(1.0 if e.severity == "blocking" else 0.4 for e in group)
        pareto_score = round(blocking_bonus * severity_w, 4)
        gates = sorted({e.gate for e in group})
        samples = []
        seen_msgs: set[str] = set()
        for e in group:
            msg = e.message.strip()
            if msg and msg not in seen_msgs:
                seen_msgs.add(msg)
                samples.append(msg[:220])
            if len(samples) >= 3:
                break
        clusters.append(
            FailureCluster(
                cluster_id=cid,
                title=spec.title if spec is not None else f"Unclassified failures ({', '.join(gates)})",
                count=len(group),
                severity_weight=severity_w,
                pareto_score=pareto_score,
                gates=gates,
                sample_messages=samples,
                trace_ids=[e.trace_id for e in group],
            )
        )
    clusters.sort(key=lambda c: (-c.pareto_score, -c.count, c.cluster_id))
    return clusters


def _insert_rule_near_anchor(original: str, anchor_heading: str, rule_line: str) -> str:
    """Insert ``rule_line`` cleanly after ``anchor_heading`` section or append at EOF."""
    if rule_line.strip() in original:
        return original
    lines = original.splitlines(keepends=True)
    anchor_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip().lower() == anchor_heading.strip().lower():
            anchor_idx = i
            break
    if anchor_idx is None:
        suffix = "" if original.endswith("\n") or not original else "\n"
        return f"{original}{suffix}\n{anchor_heading}\n\n{rule_line}\n"

    insert_at = anchor_idx + 1
    while insert_at < len(lines) and not lines[insert_at].lstrip().startswith("## "):
        insert_at += 1
    # Back up over trailing blank lines inside the section
    while insert_at > anchor_idx + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    prefix = "".join(lines[:insert_at])
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    suffix = "".join(lines[insert_at:])
    return f"{prefix}{rule_line}\n{suffix}"


def _build_unified_diff(rel_path: str, old_text: str, new_text: str) -> str:
    if old_text == new_text:
        return ""
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/skills/{rel_path}",
        tofile=f"b/skills/{rel_path}",
    )
    return "".join(diff)


def _is_rule_already_covered(skill_text: str, spec: FailureRuleSpec) -> bool:
    """Heuristic check for whether the core remediation keyword is already strongly present."""
    if spec.proposed_rule.strip() in skill_text:
        return True
    normalized = re.sub(r"\s+", " ", skill_text.lower())
    key_markers: dict[str, tuple[str, ...]] = {
        "missing_negative_validator_cases": ("expected_valid=false", "positive and negative"),
        "interactive_missing_stdout_flush": ("std::cout", "registerinteraction"),
        "post_generator_probe_skip_or_drift": ("empirical", "brute"),
        "weak_testdata_or_surviving_wrong": ("wrong_solution_kill", "expected_verdict"),
        "duplicate_or_empty_tests": ("duplicate", "no_empty"),
        "statement_ambiguity_or_smt_drift": ("constraints.h", "second_interpretation"),
        "path_containment_or_forged_state": ("file presence", "hard gates"),
    }
    markers = key_markers.get(spec.cluster_id, ())
    return bool(markers) and all(m in normalized for m in markers)


def run_deterministic_backend(
    clusters: list[FailureCluster],
    skills_dir: Path,
) -> list[PatchProposal]:
    """Pure-Python deterministic Pareto skill-patch and prompt-proposal generator."""
    spec_by_id = {spec.cluster_id: spec for spec in FAILURE_CATALOG}
    proposals: list[PatchProposal] = []
    for cluster in clusters:
        spec = spec_by_id.get(cluster.cluster_id)
        if spec is None:
            continue
        target_path = skills_dir / spec.target_rel_path
        if not target_path.is_file():
            continue
        original = target_path.read_text(encoding="utf-8")
        covered = _is_rule_already_covered(original, spec)
        updated = _insert_rule_near_anchor(original, spec.anchor_heading, spec.proposed_rule)
        diff = _build_unified_diff(spec.target_rel_path, original, updated)
        proposals.append(
            PatchProposal(
                cluster_id=cluster.cluster_id,
                title=cluster.title,
                backend="deterministic",
                target_skill=spec.target_skill,
                target_file=str(Path("skills") / spec.target_rel_path),
                pareto_score=cluster.pareto_score,
                occurrence_count=cluster.count,
                already_covered=covered,
                rationale=spec.rationale,
                proposed_rule=spec.proposed_rule,
                prompt_patch=spec.prompt_patch,
                unified_diff=diff,
                updated_content=updated,
            )
        )
    return proposals


def run_gepa_backend(
    gepa_mod: ModuleType,
    clusters: list[FailureCluster],
    skills_dir: Path,
) -> tuple[str, list[PatchProposal]]:
    """Delegate candidate reflection to ``gepa`` if available, falling back cleanly."""
    base_proposals = run_deterministic_backend(clusters, skills_dir)
    optimize_fn = getattr(gepa_mod, "optimize", None)
    if not callable(optimize_fn):
        return "deterministic", base_proposals

    seed_candidate = {p.target_file: p.prompt_patch for p in base_proposals}
    trainset = [
        {
            "cluster_id": c.cluster_id,
            "gates": c.gates,
            "samples": c.sample_messages,
            "pareto_score": c.pareto_score,
        }
        for c in clusters
    ]
    try:
        gepa_result = optimize_fn(
            seed_candidate=seed_candidate,
            trainset=trainset,
            max_metric_calls=max(1, len(base_proposals)),
        )
    except Exception:
        return "deterministic", base_proposals

    best_cand: dict[str, Any] = {}
    if isinstance(gepa_result, dict):
        raw_best = gepa_result.get("best_candidate", gepa_result)
        if isinstance(raw_best, dict):
            best_cand = raw_best
    elif hasattr(gepa_result, "best_candidate") and isinstance(gepa_result.best_candidate, dict):
        best_cand = gepa_result.best_candidate

    spec_by_id = {spec.cluster_id: spec for spec in FAILURE_CATALOG}
    refined: list[PatchProposal] = []
    for prop in base_proposals:
        rel_key = Path(prop.target_file).relative_to("skills").as_posix()
        custom_prompt = best_cand.get(prop.target_file) or best_cand.get(rel_key)
        spec = spec_by_id.get(prop.cluster_id)
        anchor = spec.anchor_heading if spec is not None else "## Forbidden Behavior"
        if isinstance(custom_prompt, str) and custom_prompt.strip():
            rule = f"- {custom_prompt.strip()}" if not custom_prompt.strip().startswith("-") else custom_prompt.strip()
            target_path = skills_dir / Path(prop.target_file).relative_to("skills")
            original = target_path.read_text(encoding="utf-8")
            updated = _insert_rule_near_anchor(original, anchor, rule)
            diff = _build_unified_diff(
                Path(prop.target_file).relative_to("skills").as_posix(),
                original,
                updated,
            )
            refined.append(
                PatchProposal(
                    cluster_id=prop.cluster_id,
                    title=prop.title,
                    backend="gepa",
                    target_skill=prop.target_skill,
                    target_file=prop.target_file,
                    pareto_score=prop.pareto_score,
                    occurrence_count=prop.occurrence_count,
                    already_covered=(rule.strip() in original),
                    rationale=prop.rationale,
                    proposed_rule=rule,
                    prompt_patch=custom_prompt.strip(),
                    unified_diff=diff,
                    updated_content=updated,
                )
            )
        else:
            refined.append(
                PatchProposal(
                    cluster_id=prop.cluster_id,
                    title=prop.title,
                    backend="gepa",
                    target_skill=prop.target_skill,
                    target_file=prop.target_file,
                    pareto_score=prop.pareto_score,
                    occurrence_count=prop.occurrence_count,
                    already_covered=prop.already_covered,
                    rationale=prop.rationale,
                    proposed_rule=prop.proposed_rule,
                    prompt_patch=prop.prompt_patch,
                    unified_diff=prop.unified_diff,
                    updated_content=prop.updated_content,
                )
            )
    return "gepa", refined


def optimize_skills_from_traces(
    trace_paths: list[Path],
    *,
    skills_dir: Path,
    backend: str = "auto",
    apply_changes: bool = False,
) -> OptimizationReport:
    """Cluster failure traces and generate (or optionally apply) skill patch proposals."""
    events = load_failure_traces(trace_paths)
    clusters = cluster_failure_events(events)
    gepa_mod = _try_import_gepa() if backend in ("auto", "gepa") else None

    if gepa_mod is not None and backend in ("auto", "gepa"):
        chosen_backend, proposals = run_gepa_backend(gepa_mod, clusters, skills_dir)
    else:
        chosen_backend = "deterministic"
        proposals = run_deterministic_backend(clusters, skills_dir)

    applied: list[str] = []
    if apply_changes:
        pending_by_path: dict[Path, str] = {}
        for prop in proposals:
            if not prop.unified_diff or prop.already_covered:
                continue
            rel = Path(prop.target_file).relative_to("skills")
            full = skills_dir / rel
            current = pending_by_path.get(full)
            if current is None:
                current = full.read_text(encoding="utf-8")
            next_text = _insert_rule_near_anchor(
                current,
                next(
                    (s.anchor_heading for s in FAILURE_CATALOG if s.cluster_id == prop.cluster_id),
                    "## Forbidden Behavior",
                ),
                prop.proposed_rule,
            )
            if next_text != current:
                pending_by_path[full] = next_text
        for path, new_content in pending_by_path.items():
            path.write_text(new_content, encoding="utf-8", newline="\n")
            applied.append(str(path))

    return OptimizationReport(
        backend=chosen_backend,
        gepa_available=gepa_mod is not None,
        total_events=len(events),
        clusters=clusters,
        proposals=proposals,
        applied_files=applied,
    )


def format_report_summary(report: OptimizationReport) -> str:
    """Format human-readable trace clustering and patch proposals."""
    lines = [
        f"GEPA Skill Optimizer: backend={report.backend} gepa_available={report.gepa_available} "
        f"events={report.total_events} clusters={len(report.clusters)} proposals={len(report.proposals)}"
    ]
    for cluster in report.clusters:
        lines.append(
            f"  - [{cluster.cluster_id}] count={cluster.count} pareto={cluster.pareto_score} "
            f"gates={cluster.gates}"
        )
    for prop in report.proposals:
        lines.append(
            f"  * Proposal -> {prop.target_file} (pareto={prop.pareto_score}, "
            f"already_covered={prop.already_covered})"
        )
        lines.append(f"    prompt_patch: {prop.prompt_patch}")
    if report.applied_files:
        lines.append(f"Applied edits to: {report.applied_files}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--traces",
        nargs="+",
        type=Path,
        required=True,
        help="One or more JSON/JSONL workflow failure trace or audit log files",
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "skills",
        help="Path to AutoCode skills directory (default: repo skills/)",
    )
    parser.add_argument(
        "--backend",
        choices=("auto", "gepa", "deterministic"),
        default="auto",
        help="Optimizer backend (default: auto, uses gepa when installed else deterministic)",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json"),
        default="summary",
        help="Stdout output format",
    )
    parser.add_argument("--json", action="store_true", help="Shorthand for --format json")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply non-redundant patch proposals to target skills (dry-run by default)",
    )
    args = parser.parse_args(argv)

    fmt = "json" if args.json else args.format
    report = optimize_skills_from_traces(
        [p.expanduser().resolve() for p in args.traces],
        skills_dir=args.skills_dir.expanduser().resolve(),
        backend=args.backend,
        apply_changes=args.apply,
    )
    payload = report.to_dict()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_report_summary(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
