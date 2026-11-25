"""Deterministic single-point C++ mutation testing for problem test-data strength checks.

Generates targeted boundary and operator mutations on ``solutions/sol.cpp``, compiles
each mutant inside an isolated temporary directory (never touching ``problem_dir``),
and verifies whether the test dataset kills every compiled mutant.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .. import TEMPLATES_DIR
from ..workflow import (
    AutoCodeManifest,
    manifest_is_communication,
    manifest_is_interactive,
)
from .checker_judge import checker_exe_path, run_testlib_checker
from .communication_runner import (
    CommunicationRunner,
    resolve_communicator_binary,
    resolve_grader_source_existing,
    safe_resolve_under_root,
)
from .compiler import compile_cpp, resolve_effective_compiler, run_binary
from .judge_runner import InteractiveRunner
from .platform import get_exe_extension
from .problem_layout import discover_test_pairs

_MUTATION_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("relational_le_to_lt", re.compile(r"(?<![<>!=])<=(?![=>])"), "<"),
    ("relational_ge_to_gt", re.compile(r"(?<![<>!=])>=(?!=)"), ">"),
    ("equality_eq_to_ne", re.compile(r"(?<![<>!=])==(?!=)"), "!="),
    ("equality_ne_to_eq", re.compile(r"!=(?!=)"), "=="),
    ("off_by_one_plus1", re.compile(r"(?<![+eE])\+\s*1(?![.\d])\b"), "+ 0"),
    ("off_by_one_minus1", re.compile(r"(?<![-eE])-\s*1(?![.\d])\b"), "- 0"),
    ("relational_lt_to_le", re.compile(r"(?<=\s)<(?=\s)"), "<="),
    ("relational_gt_to_ge", re.compile(r"(?<=\s)>(?=\s)"), ">="),
]

_SKIP_LINE_RE = re.compile(
    r"^\s*(?:#|//|/\*|\*|using\s+namespace\b|typedef\b|template\s*<|struct\b|class\b|enum\b|namespace\b)"
)

_TEMPLATE_PREFIX_RE = re.compile(
    r"\b(?:vector|pair|tuple|set|multiset|map|multimap|unordered_map|unordered_set|"
    r"queue|stack|array|priority_queue|deque|bitset|greater|less|"
    r"static_cast|dynamic_cast|const_cast|reinterpret_cast|function|optional|variant|span)\s*(?:<[^>]*|$)"
)

_MAX_MUTATION_FILE_BYTES = 16 * 1024 * 1024


def _natural_sort_key(path: Path) -> list[int | str]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]


def _is_contained_non_symlink(problem_dir: str, candidate: Path | str) -> Path | None:
    """Return resolved Path strictly inside ``problem_dir`` with no symlink on the target itself."""
    cand_path = Path(candidate)
    if cand_path.is_symlink():
        return None
    try:
        root = Path(problem_dir).resolve()
        abs_cand = cand_path if cand_path.is_absolute() else (root / cand_path)
        rel = abs_cand.relative_to(root) if abs_cand.is_absolute() else cand_path
    except ValueError:
        try:
            root = Path(problem_dir).resolve()
            resolved = cand_path.resolve()
            resolved.relative_to(root)
            rel = resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
    resolved_str = safe_resolve_under_root(str(problem_dir), str(rel))
    if resolved_str is None:
        return None
    return Path(resolved_str)


def _strip_line_comments_and_strings(
    line: str, in_block_comment: bool = False
) -> tuple[list[tuple[int, int]], bool]:
    """Return ``(spans, new_in_block_comment)`` for character spans outside C++ comments/literals."""
    spans: list[tuple[int, int]] = []
    n = len(line)
    i = 0
    span_start: int | None = None
    in_str = False
    in_char = False
    while i < n:
        ch = line[i]
        nxt = line[i + 1] if i + 1 < n else ""
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if in_str:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if in_char:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                in_char = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            break
        if ch == "/" and nxt == "*":
            if span_start is not None:
                spans.append((span_start, i))
                span_start = None
            in_block_comment = True
            i += 2
            continue
        if ch == '"':
            if span_start is not None:
                spans.append((span_start, i))
                span_start = None
            in_str = True
            i += 1
            continue
        if ch == "'":
            prev = line[i - 1] if i > 0 else ""
            if prev.isdigit() and nxt.isdigit():
                if span_start is None:
                    span_start = i
                i += 1
                continue
            if span_start is not None:
                spans.append((span_start, i))
                span_start = None
            in_char = True
            i += 1
            continue
        if span_start is None:
            span_start = i
        i += 1
    if span_start is not None:
        spans.append((span_start, i))
    return spans, in_block_comment


def generate_cpp_mutants(code: str, *, budget: int = 6) -> list[dict[str, Any]]:
    """Generate up to ``budget`` single-line boundary/operator mutants of C++ source ``code``."""
    safe_budget = max(1, min(int(budget), 12))
    lines = code.splitlines(keepends=True)
    candidates_by_kind: dict[str, list[dict[str, Any]]] = {}
    seen_codes: set[str] = {code}
    in_block_comment = False

    for line_idx, raw_line in enumerate(lines):
        code_spans, in_block_comment = _strip_line_comments_and_strings(raw_line, in_block_comment)
        stripped = raw_line.strip()
        if not code_spans or not stripped or _SKIP_LINE_RE.match(raw_line):
            continue
        # Skip stream I/O lines and template-heavy declaration lines for relational < / >
        is_io_line = bool(re.search(r"\b(?:cin|cout|cerr|scanf|printf|ostream|istream)\b", raw_line))

        for kind, pat, repl in _MUTATION_RULES:
            if is_io_line and kind in ("relational_lt_to_le", "relational_gt_to_ge"):
                continue
            for sp_start, sp_end in code_spans:
                segment = raw_line[sp_start:sp_end]
                for m in pat.finditer(segment):
                    abs_s = sp_start + m.start()
                    abs_e = sp_start + m.end()
                    # Avoid mutating template angle brackets or arrow operators
                    if kind in ("relational_lt_to_le", "relational_gt_to_ge"):
                        prefix = raw_line[:abs_s]
                        if _TEMPLATE_PREFIX_RE.search(prefix):
                            continue
                    mut_line = raw_line[:abs_s] + repl + raw_line[abs_e:]
                    if mut_line == raw_line:
                        continue
                    mut_code = "".join(lines[:line_idx] + [mut_line] + lines[line_idx + 1 :])
                    if mut_code in seen_codes:
                        continue
                    seen_codes.add(mut_code)
                    candidates_by_kind.setdefault(kind, []).append(
                        {
                            "line": line_idx + 1,
                            "mutation_type": kind,
                            "original": raw_line.strip(),
                            "mutated": mut_line.strip(),
                            "code": mut_code,
                        }
                    )
                    # At most 1 candidate per (line, kind) to keep spread diverse
                    break

    # Round-robin across mutation categories so the budget samples diverse bug classes
    selected: list[dict[str, Any]] = []
    kind_queues = [candidates_by_kind[k] for k, _, _ in _MUTATION_RULES if k in candidates_by_kind]
    idx_per_q = [0] * len(kind_queues)
    while len(selected) < safe_budget and kind_queues:
        progressed = False
        for q_idx, queue in enumerate(kind_queues):
            pos = idx_per_q[q_idx]
            if pos < len(queue):
                item = dict(queue[pos])
                item["id"] = f"mut_{len(selected) + 1}"
                selected.append(item)
                idx_per_q[q_idx] = pos + 1
                progressed = True
                if len(selected) >= safe_budget:
                    break
        if not progressed:
            break
    return selected


def _outputs_match_tokens(actual: str, expected: str) -> bool:
    act = actual.replace("\r\n", "\n").strip()
    exp = expected.replace("\r\n", "\n").strip()
    if act == exp:
        return True
    if [ln.rstrip() for ln in act.split("\n")] == [ln.rstrip() for ln in exp.split("\n")]:
        return True
    return act.split() == exp.split()


def _read_bounded_text(path: Path, max_bytes: int = _MAX_MUTATION_FILE_BYTES) -> str | None:
    try:
        with open(path, "rb") as fp:
            data = fp.read(max_bytes + 1)
        if len(data) > max_bytes:
            return None
        return data.decode("utf-8", errors="replace")
    except OSError:
        return None


async def run_mutation_testing(
    problem_dir: str,
    tests_dir: str,
    *,
    manifest: AutoCodeManifest,
    sol_source: Path,
    answer_ext: str = ".ans",
    budget: int = 6,
    verify_with_checker: bool = False,
    checker_bin: str | None = None,
    interactor_bin: str | None = None,
    timeout_sec: float = 3.0,
    max_total_sec: float = 45.0,
) -> dict[str, Any]:
    """Run bounded single-point mutation testing against ``tests_dir`` inside an isolated temp dir."""
    safe_sol = _is_contained_non_symlink(problem_dir, sol_source)
    if safe_sol is None or not safe_sol.is_file():
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "reason": f"solution source missing, symlinked, or outside problem_dir: {sol_source}",
        }
    safe_tests = _is_contained_non_symlink(problem_dir, tests_dir)
    if safe_tests is None or not safe_tests.is_dir():
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "reason": "tests directory missing, symlinked, or outside problem_dir",
        }

    in_files: list[Path] = []
    for p in sorted(safe_tests.iterdir(), key=_natural_sort_key):
        if p.suffix != ".in":
            continue
        resolved_in = _is_contained_non_symlink(problem_dir, p)
        if resolved_in is None or not resolved_in.is_file():
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "reason": f"refusing symlinked or escaped test input: {p.name}",
            }
        in_files.append(resolved_in)

    discovered_answers: dict[Path, Path | None] = {}
    if not in_files:
        for p in sorted(safe_tests.iterdir(), key=_natural_sort_key):
            if p.is_symlink() and _is_contained_non_symlink(problem_dir, p) is None:
                return {
                    "executed": False,
                    "passed": True,
                    "skipped": True,
                    "reason": f"refusing symlinked or escaped test input: {p.name}",
                }
        discovered_pairs = discover_test_pairs(
            Path(problem_dir),
            tests_dir=safe_tests,
            answer_ext=answer_ext,
            allow_missing_answer=True,
        )
        for pair in discovered_pairs:
            resolved_in = _is_contained_non_symlink(problem_dir, pair.input_path)
            if resolved_in is None or not resolved_in.is_file():
                return {
                    "executed": False,
                    "passed": True,
                    "skipped": True,
                    "reason": f"refusing symlinked or escaped test input: {pair.input_path.name}",
                }
            in_files.append(resolved_in)
            discovered_answers[resolved_in] = pair.answer_path

    if not in_files:
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "reason": "no test input files found",
        }

    code = _read_bounded_text(safe_sol, max_bytes=2 * 1024 * 1024)
    if code is None:
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "reason": f"failed to read solution source (or exceeds 2MB): {safe_sol}",
        }

    exe_ext = get_exe_extension()
    compiler = resolve_effective_compiler(None, manifest)
    is_interactive = manifest_is_interactive(manifest)
    is_communication = manifest_is_communication(manifest)
    comm_mode = (
        (manifest.protocol.mode if manifest.protocol else "run_twice")
        if is_communication
        else ""
    )

    eff_checker: str | None = None
    if verify_with_checker and not is_interactive and not is_communication:
        if checker_bin is not None:
            if not Path(checker_bin).is_symlink() and os.path.isfile(checker_bin):
                eff_checker = checker_bin
        else:
            cand_c = _is_contained_non_symlink(problem_dir, checker_exe_path(problem_dir, exe_ext))
            if cand_c is not None and cand_c.is_file():
                eff_checker = str(cand_c)
        if not eff_checker:
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "reason": "checker mutation testing requires a compiled, contained checker binary",
            }

    eff_interactor: str | None = None
    if is_interactive:
        if interactor_bin is not None:
            if not Path(interactor_bin).is_symlink() and os.path.isfile(interactor_bin):
                eff_interactor = interactor_bin
        else:
            cand_i = _is_contained_non_symlink(problem_dir, f"files/interactor{exe_ext}")
            if cand_i is not None and cand_i.is_file():
                eff_interactor = str(cand_i)
        if not eff_interactor:
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "reason": "interactive mutation testing requires a compiled, contained interactor binary",
            }

    eff_communicator: str | None = None
    if is_communication and comm_mode != "grader_linked":
        if interactor_bin is not None:
            if not Path(interactor_bin).is_symlink() and os.path.isfile(interactor_bin):
                eff_communicator = interactor_bin
        else:
            cand_comm = resolve_communicator_binary(problem_dir, manifest, exe_ext)
            if (
                cand_comm is not None
                and not Path(cand_comm).is_symlink()
                and os.path.isfile(cand_comm)
            ):
                eff_communicator = cand_comm
        if not eff_communicator:
            return {
                "executed": False,
                "passed": True,
                "skipped": True,
                "reason": f"communication ({comm_mode}) mutation testing requires a compiled communicator binary",
            }

    mutants = generate_cpp_mutants(code, budget=budget)
    if not mutants:
        return {
            "executed": True,
            "passed": True,
            "total_mutants": 0,
            "compiled_mutants": 0,
            "killed_mutants": 0,
            "survived_mutants": 0,
            "judge_error_mutants": 0,
            "kill_rate": None,
            "survivors": [],
            "details": [],
        }

    include_dirs: list[str] = []
    sol_parent = _is_contained_non_symlink(problem_dir, safe_sol.parent)
    if sol_parent is not None and sol_parent.is_dir():
        include_dirs.append(str(sol_parent))
    extra_sources: list[str] | None = None
    if is_communication and comm_mode == "grader_linked":
        grader_rel = resolve_grader_source_existing(problem_dir, manifest)
        if grader_rel:
            g_res = _is_contained_non_symlink(problem_dir, grader_rel)
            if g_res is not None and g_res.is_file():
                extra_sources = [str(g_res)]
                files_dir = _is_contained_non_symlink(problem_dir, "files")
                if files_dir is not None and files_dir.is_dir():
                    include_dirs.append(str(files_dir))
    include_dirs.append(TEMPLATES_DIR)

    # Pre-validate & cache test input/answer files once (enforcing containment & size caps)
    cached_cases: list[tuple[Path, str, Path | None, str]] = []
    for in_file in in_files:
        inp = _read_bounded_text(in_file)
        if inp is None:
            continue
        ans_path: Path | None = None
        cand_ans: Path | None = None
        if in_file in discovered_answers:
            cand_ans = discovered_answers[in_file]
        else:
            probe_ans = in_file.with_suffix(answer_ext)
            if not probe_ans.exists() and answer_ext != ".out":
                alt = in_file.with_suffix(".out")
                if alt.exists():
                    probe_ans = alt
            if not probe_ans.exists() and answer_ext != ".a":
                alt_a = in_file.with_suffix(".a")
                if alt_a.exists():
                    probe_ans = alt_a
            if probe_ans.exists():
                cand_ans = probe_ans
        if cand_ans is not None and cand_ans.exists():
            safe_ans = _is_contained_non_symlink(problem_dir, cand_ans)
            if safe_ans is None or not safe_ans.is_file():
                return {
                    "executed": False,
                    "passed": True,
                    "skipped": True,
                    "reason": f"refusing symlinked or escaped answer file: {cand_ans.name}",
                }
            ans_path = safe_ans
        ans_text = _read_bounded_text(ans_path) if ans_path is not None else ""
        if ans_text is None:
            continue
        cached_cases.append((in_file, inp, ans_path, ans_text))

    if not cached_cases:
        return {
            "executed": False,
            "passed": True,
            "skipped": True,
            "reason": "no readable test cases within size cap",
        }

    tl_ms = float(manifest.time_limit_ms) if manifest.time_limit_ms > 0 else 2000.0
    per_test_timeout = max(0.5, min(float(timeout_sec), (tl_ms / 1000.0) * 1.5))
    mem_mb = float(manifest.memory_limit_mb) if manifest.memory_limit_mb > 0 else 256.0
    deadline = time.monotonic() + max(5.0, float(max_total_sec))

    compiled_count = 0
    killed_count = 0
    judge_error_count = 0
    budget_exhausted = False
    survivors: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(
        prefix="autocode_mutation_", ignore_cleanup_errors=True
    ) as tmp_dir:
        for mut in mutants:
            if time.monotonic() >= deadline:
                budget_exhausted = True
                break
            mut_id = str(mut["id"])
            mut_src = Path(tmp_dir) / f"{mut_id}.cpp"
            mut_bin = Path(tmp_dir) / f"{mut_id}{exe_ext}"
            mut_src.write_text(str(mut["code"]), encoding="utf-8")

            comp = await compile_cpp(
                str(mut_src),
                str(mut_bin),
                compiler=compiler,
                include_dirs=include_dirs,
                extra_sources=extra_sources,
                use_cache=False,
            )
            if not comp.success:
                details.append(
                    {
                        "id": mut_id,
                        "line": mut["line"],
                        "mutation_type": mut["mutation_type"],
                        "status": "compile_error",
                        "killed": False,
                    }
                )
                continue

            compiled_count += 1
            killed = False
            judge_error = False
            killed_by: str | None = None
            verdict = "AC"

            for in_file, inp, ans_path, ans_text in cached_cases:
                if time.monotonic() >= deadline:
                    budget_exhausted = True
                    break

                if is_communication:
                    jres = await CommunicationRunner.run_from_manifest(
                        problem_dir=problem_dir,
                        manifest=manifest,
                        solution_exe=str(mut_bin),
                        input_data=inp,
                        answer_data=ans_text,
                        time_limit_ms=per_test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        use_system_temp=True,
                        communicator_exe_override=eff_communicator,
                    )
                    if jres.verdict == "FAIL":
                        judge_error = True
                        verdict = "FAIL"
                        break
                    if not jres.accepted:
                        killed = True
                        killed_by = in_file.name
                        verdict = jres.verdict
                        break
                elif is_interactive and eff_interactor:
                    jres = await InteractiveRunner.run(
                        interactor_exe=eff_interactor,
                        solution_exe=str(mut_bin),
                        input_data=inp,
                        answer_data=ans_text,
                        time_limit_ms=per_test_timeout * 1000.0,
                        memory_limit_mb=mem_mb,
                        cwd=tmp_dir,
                    )
                    if jres.verdict == "FAIL":
                        judge_error = True
                        verdict = "FAIL"
                        break
                    if not jres.accepted:
                        killed = True
                        killed_by = in_file.name
                        verdict = jres.verdict
                        break
                else:
                    run = await run_binary(
                        str(mut_bin),
                        inp,
                        timeout=per_test_timeout,
                        memory_mb=mem_mb,
                        cwd=tmp_dir,
                    )
                    if run.timed_out:
                        killed = True
                        killed_by = in_file.name
                        verdict = "TLE"
                        break
                    if not run.success:
                        killed = True
                        killed_by = in_file.name
                        verdict = "RE"
                        break
                    if eff_checker and ans_path is not None:
                        out_file = Path(tmp_dir) / f"{mut_id}_out.txt"
                        out_file.write_text(run.stdout, encoding="utf-8")
                        chk_v, _ = await run_testlib_checker(
                            eff_checker,
                            str(in_file),
                            str(out_file),
                            str(ans_path),
                            timeout=int(max(5, per_test_timeout)),
                        )
                        if chk_v == "FAIL":
                            judge_error = True
                            verdict = "FAIL"
                            break
                        if chk_v != "AC":
                            killed = True
                            killed_by = in_file.name
                            verdict = chk_v
                            break
                    elif not _outputs_match_tokens(run.stdout, ans_text):
                        killed = True
                        killed_by = in_file.name
                        verdict = "WA"
                        break

            if budget_exhausted and not killed and not judge_error:
                details.append(
                    {
                        "id": mut_id,
                        "line": mut["line"],
                        "mutation_type": mut["mutation_type"],
                        "status": "budget_exhausted",
                        "killed": False,
                    }
                )
                compiled_count -= 1
                break

            row = {
                "id": mut_id,
                "line": mut["line"],
                "mutation_type": mut["mutation_type"],
                "original": mut["original"],
                "mutated": mut["mutated"],
                "killed": killed,
                "killed_by": killed_by,
                "verdict": verdict,
                "status": "judge_error" if judge_error else ("killed" if killed else "survived"),
            }
            details.append(row)
            if judge_error:
                judge_error_count += 1
            elif killed:
                killed_count += 1
            else:
                survivors.append(row)

    evaluated_count = compiled_count - judge_error_count
    survived_count = len(survivors)
    kill_rate: float | None = (
        round(killed_count / evaluated_count, 4) if evaluated_count > 0 else None
    )
    return {
        "executed": True,
        "passed": survived_count == 0 and judge_error_count == 0,
        "total_mutants": len(mutants),
        "compiled_mutants": compiled_count,
        "killed_mutants": killed_count,
        "survived_mutants": survived_count,
        "judge_error_mutants": judge_error_count,
        "budget_exhausted": budget_exhausted,
        "kill_rate": kill_rate,
        "survivors": survivors,
        "details": details,
    }
