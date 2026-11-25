"""Isolated C++ line, branch, and MC/DC coverage runner (Clang llvm-cov + GCC gcov fallback)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import TEMPLATES_DIR
from ..utils.communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_grader_source_existing,
    safe_resolve_under_root,
)
from ..utils.compiler import (
    _find_suggested_gcc_compilers,
    detect_compiler_info,
    has_bits_stdcpp_include,
    resolve_effective_compiler,
    run_batch,
    run_binary,
    validate_compiler_spec,
)
from ..utils.judge_runner import InteractiveRunner
from ..utils.platform import get_exe_extension
from ..workflow import (
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
)

_LLVM_SHOW_LINE_RE = re.compile(r"^\s*(\d+)\|\s*([^|]*)\|")
_GCOV_LINE_RE = re.compile(r"^\s*(-|#{5}|={5}|\d+\*?):\s*(\d+):")
_GCOV_BRANCH_TAKEN_RE = re.compile(r"^\s*branch\s+\d+\s+taken\s+(\d+)")
_GCOV_BRANCH_NEVER_RE = re.compile(r"^\s*branch\s+\d+\s+never\s+executed\b")
_GCOV_BRANCH_THROW_RE = re.compile(r"\(throw\)\s*$")
_COVERAGE_CONCURRENCY = 4


@dataclass(frozen=True)
class CoverageBackend:
    """Resolved host coverage compiler and analysis tools."""

    kind: str  # "clang_llvm_cov" | "gcc_gcov"
    compiler: str
    llvm_profdata: str | None = None
    llvm_cov: str | None = None
    gcov: str | None = None


def _is_executable(path: str | None) -> bool:
    if not path or not os.path.isfile(path):
        return False
    return os.name == "nt" or os.access(path, os.X_OK)


def find_llvm_tool(tool_name: str) -> str | None:
    """Locate ``llvm-profdata`` or ``llvm-cov`` on PATH, versioned names, or macOS xcrun."""
    direct = shutil.which(tool_name)
    if _is_executable(direct):
        return direct

    for ver in (21, 20, 19, 18, 17, 16, 15, 14):
        cand = shutil.which(f"{tool_name}-{ver}")
        if _is_executable(cand):
            return cand

    if sys.platform == "darwin":
        xcrun_cmd = shutil.which("xcrun")
        if xcrun_cmd:
            try:
                proc = subprocess.run(
                    [xcrun_cmd, "--find", tool_name],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3.0,
                    check=False,
                )
                found = (proc.stdout or "").strip()
                if proc.returncode == 0 and _is_executable(found):
                    return found
            except (OSError, subprocess.SubprocessError):
                pass
            for prefix in (
                "/Library/Developer/CommandLineTools/usr/bin",
                "/opt/homebrew/opt/llvm/bin",
                "/usr/local/opt/llvm/bin",
            ):
                cand = os.path.join(prefix, tool_name)
                if _is_executable(cand):
                    return cand
    else:
        for prefix in ("/usr/bin", "/usr/local/bin"):
            cand = os.path.join(prefix, tool_name)
            if _is_executable(cand):
                return cand
    return None


def find_gcov_tool(gcc_compiler: str | None = None) -> str | None:
    """Locate ``gcov`` (preferring matching sibling ``gcov`` for toolsets/MinGW, then versioned)."""
    if gcc_compiler:
        normalized = gcc_compiler.replace("\\", "/")
        comp_dir = os.path.dirname(normalized)
        base = os.path.basename(normalized)
        match = re.search(r"-(\d+(?:\.\d+)?)(?:\.exe)?$", base, re.IGNORECASE)
        gcov_ext = ".exe" if sys.platform == "win32" else ""
        if comp_dir:
            sibling_cands: list[str] = []
            if match:
                sibling_cands.append(f"gcov-{match.group(1)}{gcov_ext}")
            sibling_cands.append(f"gcov{gcov_ext}")
            if gcov_ext:
                sibling_cands.append("gcov")
            for sib in sibling_cands:
                cand = os.path.join(os.path.dirname(gcc_compiler), sib)
                if _is_executable(cand):
                    return cand

        if match:
            ver_tool = f"gcov-{match.group(1)}"
            found = shutil.which(ver_tool)
            if _is_executable(found):
                return found
            for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
                cand = os.path.join(prefix, ver_tool)
                if _is_executable(cand):
                    return cand

    direct = shutil.which("gcov")
    if _is_executable(direct):
        return direct
    if sys.platform == "darwin":
        xcrun_cmd = shutil.which("xcrun")
        if xcrun_cmd:
            try:
                proc = subprocess.run(
                    [xcrun_cmd, "--find", "gcov"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3.0,
                    check=False,
                )
                found = (proc.stdout or "").strip()
                if proc.returncode == 0 and _is_executable(found):
                    return found
            except (OSError, subprocess.SubprocessError):
                pass
    return None


def _find_clang_compiler(explicit_compiler: str | None) -> str | None:
    candidates: list[str] = []
    if explicit_compiler:
        info = detect_compiler_info(explicit_compiler)
        if info.get("available") and info.get("is_clang"):
            candidates.append(explicit_compiler)
    for name in ("clang++", "clang++-20", "clang++-19", "clang++-18", "clang++-17", "clang++-16"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)
    if sys.platform == "darwin" and shutil.which("xcrun"):
        for prefix in ("/opt/homebrew/opt/llvm/bin/clang++",):
            if _is_executable(prefix) and prefix not in candidates:
                candidates.append(prefix)
    for cand in candidates:
        info = detect_compiler_info(cand)
        if info.get("available") and info.get("is_clang"):
            return cand
    return None


def _find_gcc_compilers(explicit_compiler: str | None) -> list[str]:
    candidates: list[str] = []
    if explicit_compiler:
        info = detect_compiler_info(explicit_compiler)
        if info.get("available") and (info.get("is_gcc") or info.get("is_clang")):
            candidates.append(explicit_compiler)
    for suggested in _find_suggested_gcc_compilers():
        if suggested not in candidates:
            candidates.append(suggested)
    default_gpp = shutil.which("g++")
    if default_gpp and default_gpp not in candidates:
        candidates.append(default_gpp)
    out: list[str] = []
    for cand in candidates:
        info = detect_compiler_info(cand)
        if info.get("available"):
            out.append(cand)
    return out


def discover_coverage_backends(
    *,
    explicit_compiler: str | None = None,
    needs_bits_stdcpp: bool = False,
) -> list[CoverageBackend]:
    """Return ordered coverage backends available on the host."""
    validated_explicit: str | None = None
    if explicit_compiler is not None and explicit_compiler.strip():
        try:
            validated_explicit = validate_compiler_spec(explicit_compiler)
        except ValueError:
            validated_explicit = None

    clang_backend: CoverageBackend | None = None
    clang_cmd = _find_clang_compiler(validated_explicit)
    if clang_cmd is not None:
        llvm_profdata = find_llvm_tool("llvm-profdata")
        llvm_cov = find_llvm_tool("llvm-cov")
        if llvm_profdata and llvm_cov:
            clang_backend = CoverageBackend(
                kind="clang_llvm_cov",
                compiler=clang_cmd,
                llvm_profdata=llvm_profdata,
                llvm_cov=llvm_cov,
            )

    gcc_backends: list[CoverageBackend] = []
    for gcc_cmd in _find_gcc_compilers(validated_explicit):
        gcov_cmd = find_gcov_tool(gcc_cmd)
        if gcov_cmd:
            gcc_backends.append(
                CoverageBackend(
                    kind="gcc_gcov",
                    compiler=gcc_cmd,
                    gcov=gcov_cmd,
                )
            )

    prefer_gcc = False
    if validated_explicit:
        exp_info = detect_compiler_info(validated_explicit)
        if exp_info.get("is_gcc"):
            prefer_gcc = True
    if needs_bits_stdcpp and clang_backend is not None:
        clang_info = detect_compiler_info(clang_backend.compiler)
        if not clang_info.get("supports_bits_stdcpp"):
            prefer_gcc = True

    ordered: list[CoverageBackend] = []
    if prefer_gcc:
        ordered.extend(gcc_backends)
        if clang_backend is not None:
            ordered.append(clang_backend)
    else:
        if clang_backend is not None:
            ordered.append(clang_backend)
        ordered.extend(gcc_backends)
    return ordered


def format_line_ranges(lines: list[int]) -> list[str]:
    """Compress sorted 1-based line numbers into compact range strings (e.g. ['4-6', '9'])."""
    if not lines:
        return []
    sorted_lines = sorted(set(lines))
    ranges: list[str] = []
    start = prev = sorted_lines[0]
    for ln in sorted_lines[1:]:
        if ln == prev + 1:
            prev = ln
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = ln
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ranges


def _resolve_solution_source(
    problem_dir: str,
    sol_name: str,
    manifest: AutoCodeManifest | None,
) -> tuple[str | None, str | None]:
    """Resolve safe relative and absolute paths to the target C++ solution source."""
    candidates: list[str] = []
    if manifest is not None:
        for entry in manifest.solutions:
            if entry.name == sol_name and entry.path:
                if entry.language != "cpp" or entry.path.lower().endswith(".py"):
                    return (
                        entry.path,
                        f"Coverage only supports C++ solutions (got language={entry.language!r} for {entry.name!r})",
                    )
                candidates.append(entry.path)
        if sol_name == "sol":
            for entry in manifest.solutions:
                if entry.role == "main" and entry.path and entry.path not in candidates:
                    if entry.language != "cpp" or entry.path.lower().endswith(".py"):
                        return (
                            entry.path,
                            f"Coverage only supports C++ solutions (got language={entry.language!r} for {entry.name!r})",
                        )
                    candidates.append(entry.path)
    for fallback in (f"solutions/{sol_name}.cpp", f"{sol_name}.cpp"):
        if fallback not in candidates:
            candidates.append(fallback)

    for rel in candidates:
        raw_path = Path(problem_dir) / rel
        if raw_path.is_symlink():
            return rel, f"Refusing symlinked solution source: {rel}"
        resolved = safe_resolve_under_root(problem_dir, rel)
        if resolved is None:
            continue
        if os.path.isfile(resolved):
            return rel, None
    return None, f"Solution source for {sol_name!r} not found under {problem_dir}"


async def _run_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    timeout_sec: float = 30.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except (asyncio.TimeoutError, TimeoutError):
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except Exception:
            pass
        return -1, "", f"Command timed out after {timeout_sec}s: {cmd[0]}"
    return (
        proc.returncode if proc.returncode is not None else -1,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


def _parse_llvm_show_uncovered_lines(show_stdout: str) -> list[int]:
    uncovered: list[int] = []
    for raw_line in show_stdout.splitlines():
        match = _LLVM_SHOW_LINE_RE.match(raw_line)
        if not match:
            continue
        lineno = int(match.group(1))
        count_text = match.group(2).strip()
        if count_text == "0":
            uncovered.append(lineno)
    return sorted(set(uncovered))


def _parse_llvm_export_payload(
    export_stdout: str,
    show_stdout: str,
    *,
    mcdc_supported: bool,
) -> dict[str, Any]:
    parsed = json.loads(export_stdout)
    data_list = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data_list, list) or not data_list:
        raise ValueError("llvm-cov export JSON missing 'data' entries")
    first_data = data_list[0]
    files = first_data.get("files") if isinstance(first_data, dict) else None
    if not isinstance(files, list) or not files:
        raise ValueError("llvm-cov export JSON missing 'files' entries")
    file_entry = files[0]
    summary = file_entry.get("summary") if isinstance(file_entry, dict) else None
    if not isinstance(summary, dict):
        raise ValueError("llvm-cov export JSON missing 'summary'")

    raw_lines = summary.get("lines")
    lines_meta: dict[str, Any] = raw_lines if isinstance(raw_lines, dict) else {}
    raw_branches_meta = summary.get("branches")
    branches_meta: dict[str, Any] = raw_branches_meta if isinstance(raw_branches_meta, dict) else {}
    raw_mcdc_meta = summary.get("mcdc")
    mcdc_meta: dict[str, Any] = raw_mcdc_meta if isinstance(raw_mcdc_meta, dict) else {}

    lines_total = int(lines_meta.get("count", 0))
    lines_covered = int(lines_meta.get("covered", 0))
    branches_total = int(branches_meta.get("count", 0))
    branches_covered = int(branches_meta.get("covered", 0))
    mcdc_total = int(mcdc_meta.get("count", 0))
    mcdc_covered = int(mcdc_meta.get("covered", 0))

    line_coverage = round(lines_covered / lines_total, 4) if lines_total > 0 else 1.0
    branch_coverage = round(branches_covered / branches_total, 4) if branches_total > 0 else 1.0
    mcdc_coverage = (
        round(mcdc_covered / mcdc_total, 4)
        if (mcdc_supported and mcdc_total > 0)
        else None
    )

    uncovered_lines = _parse_llvm_show_uncovered_lines(show_stdout)
    uncovered_branches: list[dict[str, Any]] = []
    raw_branches = file_entry.get("branches")
    if isinstance(raw_branches, list):
        for b in raw_branches:
            if not isinstance(b, list) or len(b) < 6:
                continue
            line_start = int(b[0])
            col_start = int(b[1])
            line_end = int(b[2])
            col_end = int(b[3])
            true_count = int(b[4])
            false_count = int(b[5])
            uncovered_sides = [
                side
                for side, cnt in (("true", true_count), ("false", false_count))
                if cnt == 0
            ]
            if uncovered_sides:
                uncovered_branches.append(
                    {
                        "line": line_start,
                        "col": col_start,
                        "end_line": line_end,
                        "end_col": col_end,
                        "true_count": true_count,
                        "false_count": false_count,
                        "uncovered_sides": uncovered_sides,
                    }
                )

    return {
        "line_coverage": line_coverage,
        "branch_coverage": branch_coverage,
        "mcdc_coverage": mcdc_coverage,
        "mcdc_supported": mcdc_supported,
        "lines_covered": lines_covered,
        "lines_total": lines_total,
        "branches_covered": branches_covered,
        "branches_total": branches_total,
        "mcdc_covered": mcdc_covered,
        "mcdc_total": mcdc_total,
        "uncovered_lines": uncovered_lines,
        "uncovered_line_ranges": format_line_ranges(uncovered_lines),
        "uncovered_branches": uncovered_branches,
    }


def _parse_gcov_text(gcov_text: str) -> dict[str, Any]:
    lines_total = 0
    lines_covered = 0
    uncovered_lines: list[int] = []
    branches_total = 0
    branches_covered = 0
    uncovered_branches: list[dict[str, Any]] = []
    last_lineno: int | None = None

    for raw_line in gcov_text.splitlines():
        line_match = _GCOV_LINE_RE.match(raw_line)
        if line_match:
            marker = line_match.group(1).strip()
            lineno = int(line_match.group(2))
            if lineno == 0:
                continue
            last_lineno = lineno
            if marker == "-":
                continue
            lines_total += 1
            if marker.startswith(("#", "=")):
                uncovered_lines.append(lineno)
            else:
                lines_covered += 1
            continue

        if last_lineno is None:
            continue
        if _GCOV_BRANCH_THROW_RE.search(raw_line):
            continue
        taken_match = _GCOV_BRANCH_TAKEN_RE.match(raw_line)
        if taken_match:
            count_or_pct = int(taken_match.group(1))
            branches_total += 1
            if count_or_pct > 0:
                branches_covered += 1
            else:
                uncovered_branches.append(
                    {
                        "line": last_lineno,
                        "taken_count": 0,
                        "reason": "branch taken 0%",
                    }
                )
            continue
        if _GCOV_BRANCH_NEVER_RE.match(raw_line):
            branches_total += 1
            uncovered_branches.append(
                {
                    "line": last_lineno,
                    "taken_count": 0,
                    "reason": "branch never executed",
                }
            )

    line_coverage = round(lines_covered / lines_total, 4) if lines_total > 0 else 1.0
    branch_coverage = round(branches_covered / branches_total, 4) if branches_total > 0 else 1.0
    return {
        "line_coverage": line_coverage,
        "branch_coverage": branch_coverage,
        "mcdc_coverage": None,
        "mcdc_supported": False,
        "lines_covered": lines_covered,
        "lines_total": lines_total,
        "branches_covered": branches_covered,
        "branches_total": branches_total,
        "mcdc_covered": 0,
        "mcdc_total": 0,
        "uncovered_lines": sorted(set(uncovered_lines)),
        "uncovered_line_ranges": format_line_ranges(uncovered_lines),
        "uncovered_branches": uncovered_branches,
    }


def _unavailable_result(
    *,
    reason: str,
    min_branch_coverage: float | None,
    source_rel: str | None = None,
) -> dict[str, Any]:
    passed = min_branch_coverage is None
    msg = reason if passed else f"{reason}; cannot verify configured min_branch_coverage={min_branch_coverage}"
    return {
        "status": "unavailable",
        "passed": passed,
        "skipped": False,
        "backend": None,
        "line_coverage": None,
        "branch_coverage": None,
        "mcdc_coverage": None,
        "mcdc_supported": False,
        "lines_covered": 0,
        "lines_total": 0,
        "branches_covered": 0,
        "branches_total": 0,
        "mcdc_covered": 0,
        "mcdc_total": 0,
        "uncovered_lines": [],
        "uncovered_line_ranges": [],
        "uncovered_branches": [],
        "min_branch_coverage": min_branch_coverage,
        "tests_executed": 0,
        "source_path": source_rel,
        "isolated_dir_used": True,
        "message": msg,
    }


def _error_result(
    *,
    reason: str,
    backend: str | None,
    min_branch_coverage: float | None,
    source_rel: str | None,
    tests_executed: int = 0,
) -> dict[str, Any]:
    return {
        "status": "error",
        "passed": False,
        "skipped": False,
        "backend": backend,
        "line_coverage": None,
        "branch_coverage": None,
        "mcdc_coverage": None,
        "mcdc_supported": False,
        "lines_covered": 0,
        "lines_total": 0,
        "branches_covered": 0,
        "branches_total": 0,
        "mcdc_covered": 0,
        "mcdc_total": 0,
        "uncovered_lines": [],
        "uncovered_line_ranges": [],
        "uncovered_branches": [],
        "min_branch_coverage": min_branch_coverage,
        "tests_executed": tests_executed,
        "source_path": source_rel,
        "isolated_dir_used": True,
        "message": reason,
    }


async def _execute_tests_against_binary(
    *,
    problem_dir: str,
    manifest: AutoCodeManifest | None,
    cov_bin: str,
    in_files: list[Path],
    answer_ext: str,
    tmpdir: str,
    test_timeout_sec: float,
    mem_mb: float,
    env: dict[str, str] | None,
    sequential: bool,
    interactor_exe_override: str | None,
) -> tuple[int, str | None]:
    """Run all test inputs against the coverage-instrumented binary inside ``tmpdir``."""
    is_interactive = manifest_is_interactive(manifest)
    is_communication = manifest is not None and manifest_is_communication(manifest)
    exe_ext = get_exe_extension()

    interactor_bin = ""
    if is_interactive:
        if interactor_exe_override and os.path.isfile(interactor_exe_override):
            interactor_bin = interactor_exe_override
        else:
            for cand in (
                os.path.join(problem_dir, "files", f"interactor{exe_ext}"),
                os.path.join(problem_dir, f"interactor{exe_ext}"),
            ):
                if os.path.isfile(cand):
                    interactor_bin = cand
                    break
        if not interactor_bin:
            return 0, f"interactive problem requires files/interactor{exe_ext}"
    elif is_communication and manifest is not None:
        comm_mode = manifest.protocol.mode if manifest.protocol else "run_twice"
        if comm_mode != "grader_linked":
            if interactor_exe_override and os.path.isfile(interactor_exe_override):
                interactor_bin = interactor_exe_override
            else:
                interactor_bin = resolve_communicator_binary(problem_dir, manifest, exe_ext) or ""
            if not interactor_bin:
                return 0, f"communication problem ({comm_mode}) requires compiled communicator binary"

    async def _run_one(in_file: Path) -> bool:
        if in_file.is_symlink():
            return False
        input_data = in_file.read_text(encoding="utf-8", errors="replace")
        ans_file = in_file.with_suffix(answer_ext)
        answer_data = (
            ans_file.read_text(encoding="utf-8", errors="replace")
            if ans_file.is_file() and not ans_file.is_symlink()
            else ""
        )
        if is_communication and manifest is not None:
            jres = await CommunicationRunner.run_from_manifest(
                problem_dir=problem_dir,
                manifest=manifest,
                solution_exe=cov_bin,
                input_data=input_data,
                answer_data=answer_data,
                time_limit_ms=test_timeout_sec * 1000.0,
                memory_limit_mb=mem_mb,
                use_system_temp=True,
                communicator_exe_override=interactor_bin or None,
            )
            return jres.verdict != "FAIL"
        if is_interactive:
            jres = await InteractiveRunner.run(
                interactor_exe=interactor_bin,
                solution_exe=cov_bin,
                input_data=input_data,
                answer_data=answer_data,
                time_limit_ms=test_timeout_sec * 1000.0,
                memory_limit_mb=mem_mb,
                cwd=tmpdir,
            )
            return jres.verdict != "FAIL"
        run = await run_binary(
            cov_bin,
            input_data,
            timeout=test_timeout_sec,
            memory_mb=mem_mb,
            env=env,
            cwd=tmpdir,
        )
        return not (run.error and run.return_code == -1)

    if sequential or is_interactive or is_communication:
        executed = 0
        for item in in_files:
            if await _run_one(item):
                executed += 1
        return executed, None

    outcomes = await run_batch(in_files, _run_one, limit=_COVERAGE_CONCURRENCY)
    return sum(1 for ok in outcomes if ok), None


async def run_solution_coverage(
    problem_dir: str,
    tests_dir: str,
    *,
    sol_name: str = "sol",
    answer_ext: str = ".ans",
    timeout: int = 60,
    manifest: AutoCodeManifest | None = None,
    interactor_exe_override: str | None = None,
) -> dict[str, Any]:
    """Measure line, branch, and optional MC/DC coverage of ``sol.cpp`` in complete isolation.

    All compilation objects, profile files (``.profraw``, ``.profdata``, ``.gcno``,
    ``.gcda``, ``.gcov``), and test execution working directories reside strictly
    inside a disposable ``tempfile.TemporaryDirectory`` outside ``problem_dir``.
    """
    min_branch_cov: float | None = (
        float(manifest.audit_gates.min_branch_coverage)
        if (manifest is not None and manifest.audit_gates.min_branch_coverage is not None)
        else None
    )

    sol_rel, sol_err = _resolve_solution_source(problem_dir, sol_name, manifest)
    if sol_err is not None or sol_rel is None:
        return _unavailable_result(
            reason=sol_err or f"Solution source for {sol_name!r} unavailable",
            min_branch_coverage=min_branch_cov,
            source_rel=sol_rel,
        )

    src_abs = Path(problem_dir) / sol_rel
    try:
        src_text = src_abs.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _error_result(
            reason=f"Failed to read {sol_rel}: {exc}",
            backend=None,
            min_branch_coverage=min_branch_cov,
            source_rel=sol_rel,
        )

    explicit_compiler: str | None = None
    if manifest is not None and isinstance(manifest.compiler, str) and manifest.compiler.strip():
        explicit_compiler = resolve_effective_compiler(None, manifest)
    needs_bits = has_bits_stdcpp_include(src_text)
    backends = discover_coverage_backends(
        explicit_compiler=explicit_compiler,
        needs_bits_stdcpp=needs_bits,
    )
    if not backends:
        return _unavailable_result(
            reason="Host coverage tools unavailable (clang++/llvm-profdata/llvm-cov and g++/gcov not found)",
            min_branch_coverage=min_branch_cov,
            source_rel=sol_rel,
        )

    tests_path = Path(tests_dir)
    if not tests_path.is_dir():
        return _error_result(
            reason=f"Tests directory not found: {tests_dir}",
            backend=None,
            min_branch_coverage=min_branch_cov,
            source_rel=sol_rel,
        )

    in_files = sorted(
        (p for p in tests_path.iterdir() if p.is_file() and not p.is_symlink() and p.suffix == ".in"),
        key=lambda p: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)],
    )
    if not in_files:
        passed_empty = min_branch_cov is None or min_branch_cov <= 0.0
        return {
            "status": "ok",
            "passed": passed_empty,
            "skipped": False,
            "backend": backends[0].kind,
            "line_coverage": 0.0,
            "branch_coverage": 0.0,
            "mcdc_coverage": None,
            "mcdc_supported": False,
            "lines_covered": 0,
            "lines_total": 0,
            "branches_covered": 0,
            "branches_total": 0,
            "mcdc_covered": 0,
            "mcdc_total": 0,
            "uncovered_lines": [],
            "uncovered_line_ranges": [],
            "uncovered_branches": [],
            "min_branch_coverage": min_branch_cov,
            "tests_executed": 0,
            "source_path": sol_rel,
            "isolated_dir_used": True,
            "message": "No .in test files found in tests directory",
        }

    is_comm = manifest is not None and manifest_is_communication(manifest)
    comm_mode = (manifest.protocol.mode if (is_comm and manifest and manifest.protocol) else "")
    grader_src_abs: Path | None = None
    if is_comm and manifest is not None and comm_mode == "grader_linked":
        grader_rel = resolve_grader_source_existing(problem_dir, manifest)
        if grader_rel:
            resolved_g = safe_resolve_under_root(problem_dir, grader_rel)
            if resolved_g and Path(resolved_g).is_file() and not Path(resolved_g).is_symlink():
                grader_src_abs = Path(resolved_g)

    tl_sec = (
        max(5.0, float(manifest.time_limit_ms) / 1000.0 * 3.0)
        if (manifest and getattr(manifest, "time_limit_ms", None))
        else max(5.0, min(30.0, float(timeout)))
    )
    test_timeout_sec = min(float(max(1, timeout)), tl_sec)
    mem_mb = (
        max(512.0, float(manifest.memory_limit_mb) * 2.0)
        if (manifest and getattr(manifest, "memory_limit_mb", None))
        else 512.0
    )

    exe_ext = get_exe_extension()
    include_flags: list[str] = ["-I", str(src_abs.parent)]
    files_dir = os.path.join(problem_dir, "files")
    if os.path.isdir(files_dir):
        include_flags.extend(["-I", files_dir])
    if os.path.exists(TEMPLATES_DIR):
        include_flags.extend(["-isystem", TEMPLATES_DIR])

    last_error = "Coverage compilation/execution failed"
    last_backend: str | None = None

    for backend in backends:
        last_backend = backend.kind
        with tempfile.TemporaryDirectory(prefix="autocode_cov_") as raw_tmp:
            tmpdir = os.path.abspath(raw_tmp)
            copied_sol = os.path.join(tmpdir, "sol.cpp")
            Path(copied_sol).write_text(src_text, encoding="utf-8", newline="\n")
            extra_srcs: list[str] = []
            if grader_src_abs is not None:
                copied_grader = os.path.join(tmpdir, "grader.cpp")
                Path(copied_grader).write_text(
                    grader_src_abs.read_text(encoding="utf-8", errors="replace"),
                    encoding="utf-8",
                    newline="\n",
                )
                extra_srcs.append(copied_grader)
            cov_bin = os.path.join(tmpdir, f"sol_cov{exe_ext}")

            if backend.kind == "clang_llvm_cov":
                assert backend.llvm_profdata is not None and backend.llvm_cov is not None
                prof_pattern = os.path.join(tmpdir, "prof_%p_%m.profraw")
                base_cmd = [
                    backend.compiler,
                    "-std=c++20",
                    "-O0",
                    f"-fprofile-instr-generate={prof_pattern}",
                    "-fcoverage-mapping",
                ]
                mcdc_supported = True
                compile_cmd = [
                    *base_cmd,
                    "-fcoverage-mcdc",
                    *include_flags,
                    copied_sol,
                    *extra_srcs,
                    "-o",
                    cov_bin,
                ]
                comp_rc, _, comp_err = await _run_subprocess(compile_cmd, cwd=tmpdir, timeout_sec=30.0)
                if comp_rc != 0 and ("mcdc" in comp_err.lower() or "unknown" in comp_err.lower() or "unsupported" in comp_err.lower()):
                    mcdc_supported = False
                    compile_cmd = [
                        *base_cmd,
                        *include_flags,
                        copied_sol,
                        *extra_srcs,
                        "-o",
                        cov_bin,
                    ]
                    comp_rc, _, comp_err = await _run_subprocess(compile_cmd, cwd=tmpdir, timeout_sec=30.0)

                if comp_rc != 0:
                    last_error = f"Clang coverage compile failed: {comp_err.strip()[:400]}"
                    continue

                run_env = dict(os.environ)
                run_env["LLVM_PROFILE_FILE"] = prof_pattern
                tests_executed, run_err = await _execute_tests_against_binary(
                    problem_dir=problem_dir,
                    manifest=manifest,
                    cov_bin=cov_bin,
                    in_files=in_files,
                    answer_ext=answer_ext,
                    tmpdir=tmpdir,
                    test_timeout_sec=test_timeout_sec,
                    mem_mb=mem_mb,
                    env=run_env,
                    sequential=False,
                    interactor_exe_override=interactor_exe_override,
                )
                if run_err is not None:
                    return _error_result(
                        reason=run_err,
                        backend=backend.kind,
                        min_branch_coverage=min_branch_cov,
                        source_rel=sol_rel,
                        tests_executed=tests_executed,
                    )

                profraw_files = sorted(
                    os.path.join(tmpdir, name)
                    for name in os.listdir(tmpdir)
                    if name.endswith(".profraw")
                )
                if not profraw_files:
                    last_error = "No .profraw coverage data produced during test execution"
                    continue
                profdata_path = os.path.join(tmpdir, "merged.profdata")
                m_rc, _, m_err = await _run_subprocess(
                    [backend.llvm_profdata, "merge", "-sparse", *profraw_files, "-o", profdata_path],
                    cwd=tmpdir,
                    timeout_sec=30.0,
                )
                if m_rc != 0:
                    last_error = f"llvm-profdata merge failed: {m_err.strip()[:400]}"
                    continue

                e_rc, e_out, e_err = await _run_subprocess(
                    [backend.llvm_cov, "export", cov_bin, f"-instr-profile={profdata_path}", copied_sol],
                    cwd=tmpdir,
                    timeout_sec=30.0,
                )
                if e_rc != 0:
                    last_error = f"llvm-cov export failed: {e_err.strip()[:400]}"
                    continue

                s_rc, s_out, _ = await _run_subprocess(
                    [
                        backend.llvm_cov,
                        "show",
                        "-show-line-counts",
                        "-show-instantiations=false",
                        cov_bin,
                        f"-instr-profile={profdata_path}",
                        copied_sol,
                    ],
                    cwd=tmpdir,
                    timeout_sec=30.0,
                )
                if s_rc != 0:
                    s_rc, s_out, _ = await _run_subprocess(
                        [backend.llvm_cov, "show", "-show-line-counts", cov_bin, f"-instr-profile={profdata_path}", copied_sol],
                        cwd=tmpdir,
                        timeout_sec=30.0,
                    )

                try:
                    metrics = _parse_llvm_export_payload(
                        e_out,
                        s_out if s_rc == 0 else "",
                        mcdc_supported=mcdc_supported,
                    )
                except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                    last_error = f"Failed to parse llvm-cov output: {exc}"
                    continue

                passed = True
                if min_branch_cov is not None and metrics["branch_coverage"] < min_branch_cov:
                    passed = False
                msg = (
                    f"Coverage ({backend.kind}): line={metrics['line_coverage']:.1%}, "
                    f"branch={metrics['branch_coverage']:.1%}"
                )
                if min_branch_cov is not None:
                    msg += f" (min_branch_coverage={min_branch_cov:.1%})"
                return {
                    "status": "ok",
                    "passed": passed,
                    "skipped": False,
                    "backend": backend.kind,
                    "compiler": backend.compiler,
                    **metrics,
                    "min_branch_coverage": min_branch_cov,
                    "tests_executed": tests_executed,
                    "source_path": sol_rel,
                    "isolated_dir_used": True,
                    "message": msg,
                }

            # GCC / gcov fallback
            assert backend.gcov is not None
            compile_cmd = [
                backend.compiler,
                "-std=c++20",
                "-O0",
                "--coverage",
                *include_flags,
                "sol.cpp",
                *(os.path.basename(p) for p in extra_srcs),
                "-o",
                cov_bin,
            ]
            if sys.platform == "win32":
                compile_cmd.append("-static")
            comp_rc, _, comp_err = await _run_subprocess(compile_cmd, cwd=tmpdir, timeout_sec=30.0)
            if comp_rc != 0:
                last_error = f"GCC coverage compile failed: {comp_err.strip()[:400]}"
                continue

            tests_executed, run_err = await _execute_tests_against_binary(
                problem_dir=problem_dir,
                manifest=manifest,
                cov_bin=cov_bin,
                in_files=in_files,
                answer_ext=answer_ext,
                tmpdir=tmpdir,
                test_timeout_sec=test_timeout_sec,
                mem_mb=mem_mb,
                env=None,
                sequential=True,
                interactor_exe_override=interactor_exe_override,
            )
            if run_err is not None:
                return _error_result(
                    reason=run_err,
                    backend=backend.kind,
                    min_branch_coverage=min_branch_cov,
                    source_rel=sol_rel,
                    tests_executed=tests_executed,
                )

            gcno_files = sorted(
                os.path.join(tmpdir, name)
                for name in os.listdir(tmpdir)
                if name.endswith(".gcno") and "sol" in name
            )
            if not gcno_files:
                gcno_files = sorted(
                    os.path.join(tmpdir, name)
                    for name in os.listdir(tmpdir)
                    if name.endswith(".gcno")
                )
            if not gcno_files:
                last_error = "No .gcno graph file generated for gcov"
                continue

            gcov_rc, _, gcov_err = await _run_subprocess(
                [backend.gcov, "-b", gcno_files[0]],
                cwd=tmpdir,
                timeout_sec=30.0,
            )
            if gcov_rc != 0:
                last_error = f"gcov execution failed: {gcov_err.strip()[:400]}"
                continue

            gcov_file = Path(tmpdir) / "sol.cpp.gcov"
            if not gcov_file.is_file():
                for cand in sorted(Path(tmpdir).glob("*.gcov")):
                    head = cand.read_text(encoding="utf-8", errors="replace").splitlines()[:3]
                    if any("0:Source:" in ln and ln.rstrip().endswith("sol.cpp") for ln in head):
                        gcov_file = cand
                        break
            if not gcov_file.is_file():
                last_error = "gcov did not emit sol.cpp.gcov"
                continue

            metrics = _parse_gcov_text(gcov_file.read_text(encoding="utf-8", errors="replace"))
            passed = True
            if min_branch_cov is not None and metrics["branch_coverage"] < min_branch_cov:
                passed = False
            msg = (
                f"Coverage ({backend.kind}): line={metrics['line_coverage']:.1%}, "
                f"branch={metrics['branch_coverage']:.1%}"
            )
            if min_branch_cov is not None:
                msg += f" (min_branch_coverage={min_branch_cov:.1%})"
            return {
                "status": "ok",
                "passed": passed,
                "skipped": False,
                "backend": backend.kind,
                "compiler": backend.compiler,
                **metrics,
                "min_branch_coverage": min_branch_cov,
                "tests_executed": tests_executed,
                "source_path": sol_rel,
                "isolated_dir_used": True,
                "message": msg,
            }

    return _error_result(
        reason=last_error,
        backend=last_backend,
        min_branch_coverage=min_branch_cov,
        source_rel=sol_rel,
    )
