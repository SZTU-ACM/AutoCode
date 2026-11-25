"""Offline deterministic TPR/TNR benchmark for ``ProblemAuditTool(standalone=True)``.

Evaluates ``ProblemAuditTool(standalone=True)`` against labeled ``go``
(``CodeContests+``-style complete packages across ``standard``, ``spj``,
``interactive``, and ``communication``) and ``no_go`` (``Codehacks``-style
defective/hackable packages) fixtures without calling any LLM or modifying any
target fixture directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from autocode_mcp.tools.audit import ProblemAuditTool

Decision = Literal["go", "no_go"]
Layout = Literal["auto", "autocode", "standard_cn", "polygon", "hydro"]
AuditMode = Literal["quick", "full"]


@dataclass(frozen=True)
class BenchmarkCase:
    """Single labeled problem package for standalone benchmark evaluation."""

    case_id: str
    problem_dir: Path
    expected_decision: Decision
    category: str = "standard"
    strict: bool = True
    layout: Layout = "auto"
    mode: AuditMode = "full"
    expected_blocking_gates: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaseOutcome:
    """Observed evaluation result for one benchmark case."""

    case_id: str
    category: str
    expected_decision: Decision
    actual_decision: str
    matched: bool
    blocking_gates: list[str]
    warning_gates: list[str]
    zero_write_passed: bool
    duration_ms: float
    expected_gates_satisfied: bool = True
    error: str | None = None


@dataclass(frozen=True)
class BenchmarkReport:
    """Aggregate confusion matrix, TPR/TNR, and failure breakdown."""

    total: int
    tp: int
    tn: int
    fp: int
    fn: int
    tpr: float
    tnr: float
    precision: float
    accuracy: float
    zero_write_passed: bool
    failure_breakdown: dict[str, int] = field(default_factory=dict)
    mismatched_cases: list[str] = field(default_factory=list)
    outcomes: list[CaseOutcome] = field(default_factory=list)
    total_duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "confusion_matrix": {
                "tp": self.tp,
                "tn": self.tn,
                "fp": self.fp,
                "fn": self.fn,
            },
            "metrics": {
                "tpr": self.tpr,
                "tnr": self.tnr,
                "precision": self.precision,
                "accuracy": self.accuracy,
            },
            "zero_write_passed": self.zero_write_passed,
            "failure_breakdown": self.failure_breakdown,
            "mismatched_cases": self.mismatched_cases,
            "total_duration_ms": self.total_duration_ms,
            "outcomes": [asdict(item) for item in self.outcomes],
        }


def snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Capture non-mutating ``(size, mtime_ns)`` snapshot of ``root`` without following symlinks."""
    snap: dict[str, tuple[int, int]] = {}
    for cur, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        filenames.sort()
        cur_p = Path(cur)
        for dname in dirnames:
            dp = cur_p / dname
            st = dp.lstat()
            snap[f"D:{dp.relative_to(root).as_posix()}"] = (0, st.st_mtime_ns)
        for fname in filenames:
            fp = cur_p / fname
            st = fp.lstat()
            snap[f"F:{fp.relative_to(root).as_posix()}"] = (st.st_size, st.st_mtime_ns)
    return snap


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


_STD_VAL_CPP = """#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n) || n < 1 || n > 200000) return 1;
    for (long long i = 0; i < n; ++i) {
        long long x;
        if (!(std::cin >> x) || x < -1000000000LL || x > 1000000000LL) return 1;
    }
    long long extra;
    if (std::cin >> extra) return 1;
    return 0;
}
"""

_STD_SOL_CPP = """#include <iostream>
int main() {
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    long long n;
    if (!(std::cin >> n)) return 0;
    long long sum = 0;
    for (long long i = 0; i < n; ++i) {
        long long x;
        std::cin >> x;
        sum += x;
    }
    std::cout << sum << '\\n';
    return 0;
}
"""

_STD_WRONG_INT32_CPP = """#include <iostream>
int main() {
    int n;
    if (!(std::cin >> n)) return 0;
    int sum = 0;
    for (int i = 0; i < n; ++i) {
        int x;
        std::cin >> x;
        sum += x;
    }
    std::cout << sum << '\\n';
    return 0;
}
"""

_STD_WRONG_OFFBYONE_GT100_CPP = """#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    long long sum = 0;
    for (long long i = 0; i < n; ++i) {
        long long x;
        std::cin >> x;
        sum += x;
    }
    if (n > 100) sum += 1;
    std::cout << sum << '\\n';
    return 0;
}
"""

_STD_BROKEN_SOL_CPP = """#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    std::cout << (n + 1) << '\\n';
    return 0;
}
"""

_SPJ_VAL_CPP = """#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n) || n < 1 || n > 1000000000LL) return 1;
    long long extra;
    if (std::cin >> extra) return 1;
    return 0;
}
"""

_SPJ_CHECKER_CPP = """#include <fstream>
#include <limits>
int main(int argc, char* argv[]) {
    if (argc < 4) return 3;
    std::ifstream inf(argv[1]), ouf(argv[2]), ans(argv[3]);
    long long n = 0, x = 0, y = 0, ax = 0, ay = 0;
    if (!(inf >> n)) return 3;
    if (!(ans >> ax >> ay)) return 3;
    if (!(ouf >> x >> y)) return 1;
    long long extra = 0;
    if (ouf >> extra) return 2;
    if (y > 0 && x > std::numeric_limits<long long>::max() - y) return 1;
    if (y < 0 && x < std::numeric_limits<long long>::min() - y) return 1;
    return (x + y == n) ? 0 : 1;
}
"""

_SPJ_SOL_CPP = """#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    std::cout << 1 << ' ' << (n - 1) << '\\n';
    return 0;
}
"""

_SPJ_WRONG_CPP = """#include <iostream>
int main() {
    long long n;
    if (!(std::cin >> n)) return 0;
    std::cout << 0 << ' ' << 0 << '\\n';
    return 0;
}
"""

_INTERACTIVE_CPP = """#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerInteraction(argc, argv);
    int secret = inf.readInt(1, 100, "S");
    int queries = 0;
    while (true) {
        if (ouf.seekEof()) quitf(_wa, "premature EOF");
        std::string op = ouf.readToken();
        if (op == "?") {
            ++queries;
            if (queries > 20) quitf(_wa, "too many queries");
            int x = ouf.readInt(1, 100, "x");
            int resp = (x < secret) ? -1 : (x > secret ? 1 : 0);
            std::cout << resp << '\\n' << std::flush;
        } else if (op == "!") {
            int guess = ouf.readInt(1, 100, "s");
            if (!ouf.seekEof()) quitf(_pe, "extra output");
            if (guess == secret) quitf(_ok, "OK");
            quitf(_wa, "wrong guess");
        } else {
            quitf(_pe, "unknown token");
        }
    }
}
"""

_INTERACTIVE_SOL_CPP = """#include <iostream>
int main() {
    int lo = 1, hi = 100;
    while (lo <= hi) {
        int mid = (lo + hi) / 2;
        std::cout << "? " << mid << std::endl;
        int resp = 0;
        if (!(std::cin >> resp)) return 0;
        if (resp == 0) {
            std::cout << "! " << mid << std::endl;
            return 0;
        }
        if (resp < 0) lo = mid + 1;
        else hi = mid - 1;
    }
    return 0;
}
"""

_INTERACTIVE_WRONG_CPP = """#include <iostream>
int main() {
    for (int x = 1; x <= 100; ++x) {
        std::cout << "? " << x << std::endl;
        int resp = 0;
        if (!(std::cin >> resp)) return 0;
        if (resp == 0) {
            std::cout << "! " << x << std::endl;
            return 0;
        }
    }
    return 0;
}
"""

_COMM_CPP = """#include <cstdlib>
#include <iostream>
#include <string>
#include "testlib.h"

int main(int argc, char* argv[]) {
    registerTestlibCmd(argc, argv);
    const char* stage_env = std::getenv("AUTOCODE_COMM_STAGE");
    int stage = stage_env ? std::atoi(stage_env) : 2;
    int mode = inf.readInt(0, 1, "mode");
    int x = inf.readInt(0, 15, "X");
    if (stage == 1) {
        std::string msg = ouf.readToken();
        if (!ouf.seekEof()) quitf(_dirt, "extra");
        if (msg.empty() || msg.size() > 12) quitf(_wa, "exceeds budget");
        for (char c : msg) {
            if (c != '0' && c != '1') quitf(_pe, "non-binary");
        }
        int bits = static_cast<int>(msg.size());
        std::string noisy = msg;
        if (mode == 1) {
            std::size_t pos = static_cast<std::size_t>((x * 7 + 3) % bits);
            noisy[pos] = (noisy[pos] == '0') ? '1' : '0';
        }
        std::cout << noisy << "\\n";
        quitf(_ok, "forwarded bits_used=%d bytes_used=%d", bits, (bits + 7) / 8);
    }
    int decoded = ouf.readInt(0, 15, "decoded");
    if (!ouf.seekEof()) quitf(_dirt, "extra");
    std::string chan = ans.seekEof() ? "" : ans.readToken();
    int bits = static_cast<int>(chan.size());
    if (decoded == x) quitf(_ok, "OK bits_used=%d bytes_used=%d rounds=2", bits, (bits + 7) / 8);
    quitf(_wa, "decoded mismatch");
}
"""

_COMM_SOL_CPP = """#include <iostream>
#include <string>
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int mode = 0, x = 0;
        std::cin >> mode >> x;
        std::string out;
        for (int b = 3; b >= 0; --b) {
            char ch = ((x >> b) & 1) ? '1' : '0';
            out.append(3, ch);
        }
        std::cout << out << '\\n';
    } else {
        std::string msg;
        std::cin >> msg;
        int x = 0;
        for (int b = 0; b < 4; ++b) {
            int ones = 0;
            for (int k = 0; k < 3; ++k) {
                if (b * 3 + k < static_cast<int>(msg.size()) && msg[b * 3 + k] == '1') ++ones;
            }
            x = (x << 1) | (ones >= 2 ? 1 : 0);
        }
        std::cout << x << '\\n';
    }
    return 0;
}
"""

_COMM_WRONG_CPP = """#include <iostream>
#include <string>
int main() {
    std::string phase;
    if (!(std::cin >> phase)) return 0;
    if (phase == "first") {
        int mode = 0, x = 0;
        std::cin >> mode >> x;
        std::string out;
        for (int b = 3; b >= 0; --b) out.push_back(((x >> b) & 1) ? '1' : '0');
        std::cout << out << '\\n';
    } else {
        std::string msg;
        std::cin >> msg;
        int x = 0;
        for (char ch : msg) x = (x << 1) | (ch == '1' ? 1 : 0);
        std::cout << x << '\\n';
    }
    return 0;
}
"""

_TUTORIAL_TEXT = (
    "# Tutorial\n\n"
    "Use 64-bit integer arithmetic (`long long`) and linear scan in $O(n)$ time and $O(1)$ extra space. "
    "All boundary values including $n=1$ and $|a_i| = 10^9$ are handled without overflow.\n"
)


def _create_standard_package(
    pkg: Path,
    *,
    sol_code: str = _STD_SOL_CPP,
    val_code: str | None = _STD_VAL_CPP,
    wrong_code: str | None = _STD_WRONG_INT32_CPP,
    tests: list[tuple[str, str, str]] | None = None,
) -> Path:
    _write_text(
        pkg / "statements" / "README.md",
        "# Array Sum\n\nGiven $1 \\le n \\le 200000$ and $|a_i| \\le 10^9$, output $\\sum a_i$.\n\n"
        "## 样例\n\n```text\n1\n5\n```\n",
    )
    _write_text(pkg / "statements" / "tutorial.md", _TUTORIAL_TEXT)
    _write_text(pkg / "solutions" / "sol.cpp", sol_code)
    if val_code is not None:
        _write_text(pkg / "files" / "val.cpp", val_code)
    if wrong_code is not None:
        _write_text(pkg / "solutions" / "wrong_candidate.cpp", wrong_code)

    rows = tests or [
        ("01", "1\n7\n", "7\n"),
        ("02", "5\n1 2 3 4 5\n", "15\n"),
        ("03", "200\n" + " ".join(["100"] * 200) + "\n", "20000\n"),
        ("04", "2000\n" + " ".join(["1000000000"] * 2000) + "\n", "2000000000000\n"),
    ]
    for stem, inp, ans in rows:
        _write_text(pkg / "tests" / f"{stem}.in", inp)
        _write_text(pkg / "tests" / f"{stem}.ans", ans)
    return pkg


def _create_spj_package(pkg: Path) -> Path:
    _write_text(
        pkg / "statements" / "README.md",
        "# Pair Sum\n\nGiven $1 \\le n \\le 10^9$, output any $x, y$ with $x + y = n$.\n\n"
        "## 样例\n\n```text\n5\n```\n",
    )
    _write_text(pkg / "statements" / "tutorial.md", _TUTORIAL_TEXT)
    _write_text(pkg / "files" / "val.cpp", _SPJ_VAL_CPP)
    _write_text(pkg / "files" / "checker.cpp", _SPJ_CHECKER_CPP)
    _write_text(pkg / "solutions" / "sol.cpp", _SPJ_SOL_CPP)
    _write_text(pkg / "solutions" / "wrong_zero.cpp", _SPJ_WRONG_CPP)
    for stem, n in (("01", 1), ("02", 8), ("03", 5000)):
        _write_text(pkg / "tests" / f"{stem}.in", f"{n}\n")
        _write_text(pkg / "tests" / f"{stem}.ans", f"1 {n - 1}\n")
    return pkg


def _create_interactive_package(pkg: Path) -> Path:
    _write_text(
        pkg / "statements" / "README.md",
        "# Guess Secret\n\n## 交互协议\n\nQuery `? x` at most 20 times for $1 \\le S \\le 100$. Flush stdout.\n\n"
        "## 样例\n\n```text\n? 50\n! 50\n```\n",
    )
    _write_text(pkg / "statements" / "tutorial.md", _TUTORIAL_TEXT)
    _write_text(pkg / "files" / "interactor.cpp", _INTERACTIVE_CPP)
    _write_text(pkg / "solutions" / "sol.cpp", _INTERACTIVE_SOL_CPP)
    _write_text(pkg / "solutions" / "wrong_over_limit.cpp", _INTERACTIVE_WRONG_CPP)
    for stem, s in (("01", 1), ("02", 10), ("03", 90)):
        _write_text(pkg / "tests" / f"{stem}.in", f"{s}\n")
    return pkg


def _create_communication_package(pkg: Path) -> Path:
    _write_text(
        pkg / "statements" / "README.md",
        "# Noisy Channel ECC\n\n## 通信协议\n\n`run_twice`, $0 \\le X \\le 15$, $0 \\le \\text{mode} \\le 1$, max_bits=12.\n\n"
        "## 样例\n\n```text\n1 5\n```\n",
    )
    _write_text(pkg / "statements" / "tutorial.md", _TUTORIAL_TEXT)
    _write_text(pkg / "files" / "communicator.cpp", _COMM_CPP)
    _write_text(pkg / "solutions" / "sol.cpp", _COMM_SOL_CPP)
    _write_text(pkg / "solutions" / "wrong_no_ecc.cpp", _COMM_WRONG_CPP)
    _write_text(pkg / "tests" / "01.in", "0 3\n")
    _write_text(pkg / "tests" / "02.in", "1 12\n")
    manifest = {
        "schema_version": "1.0",
        "problem_name": "Noisy Channel ECC",
        "problem_type": "communication",
        "interactive": False,
        "special_judge": False,
        "stress_comparison": "exact",
        "oracle_mode": "interactor_only",
        "time_limit_ms": 2000,
        "memory_limit_mb": 256,
        "constraints": {"X": {"min": 0, "max": 15}, "mode": {"min": 0, "max": 1}},
        "protocol": {
            "mode": "run_twice",
            "passes": 2,
            "pass_headers": ["first", "second"],
            "max_bits": 12,
            "max_bytes": 2,
            "max_rounds": 2,
            "communicator_file": "files/communicator.cpp",
        },
        "quality_gates": {
            "require_stress_passed": False,
            "require_validation_passed": False,
            "require_tests_verified": False,
            "require_limit_semantics": False,
            "require_validator_check": False,
            "require_wrong_solution_kill": True,
        },
        "solutions": [
            {"name": "sol", "role": "main", "language": "cpp", "path": "solutions/sol.cpp", "expected": "pass"},
            {"name": "wrong_no_ecc", "role": "wrong", "language": "cpp", "path": "solutions/wrong_no_ecc.cpp", "expected": "fail"},
        ],
    }
    _write_text(pkg / ".autocode" / "manifest.json", json.dumps(manifest, indent=2))
    return pkg


def build_default_benchmark_cases(root: Path) -> list[BenchmarkCase]:
    """Materialize labeled ``go`` and ``no_go`` fixtures under ``root``."""
    return [
        BenchmarkCase(
            case_id="cc_plus_standard_good",
            problem_dir=_create_standard_package(root / "cc_plus_standard_good"),
            expected_decision="go",
            category="standard",
            strict=True,
        ),
        BenchmarkCase(
            case_id="cc_plus_spj_good",
            problem_dir=_create_spj_package(root / "cc_plus_spj_good"),
            expected_decision="go",
            category="spj",
            strict=True,
        ),
        BenchmarkCase(
            case_id="cc_plus_interactive_good",
            problem_dir=_create_interactive_package(root / "cc_plus_interactive_good"),
            expected_decision="go",
            category="interactive",
            strict=True,
        ),
        BenchmarkCase(
            case_id="cc_plus_communication_good",
            problem_dir=_create_communication_package(root / "cc_plus_communication_good"),
            expected_decision="go",
            category="communication",
            strict=True,
        ),
        BenchmarkCase(
            case_id="codehacks_surviving_wrong",
            problem_dir=_create_standard_package(
                root / "codehacks_surviving_wrong",
                wrong_code=_STD_WRONG_OFFBYONE_GT100_CPP,
                tests=[
                    ("01", "1\n7\n", "7\n"),
                    ("02", "5\n1 2 3 4 5\n", "15\n"),
                    ("03", "20\n" + " ".join(["3"] * 20) + "\n", "60\n"),
                ],
            ),
            expected_decision="no_go",
            category="codehacks_weak_tests",
            strict=True,
            expected_blocking_gates=("wrong_solution_kill",),
        ),
        BenchmarkCase(
            case_id="codehacks_duplicate_tests",
            problem_dir=_create_standard_package(
                root / "codehacks_duplicate_tests",
                tests=[
                    ("01", "1\n7\n", "7\n"),
                    ("02", "1\n7\n", "7\n"),
                    ("03", "2000\n" + " ".join(["1000000000"] * 2000) + "\n", "2000000000000\n"),
                ],
            ),
            expected_decision="no_go",
            category="codehacks_duplicate_inputs",
            strict=True,
            expected_blocking_gates=("duplicate_inputs",),
        ),
        BenchmarkCase(
            case_id="codehacks_broken_sol_mismatch",
            problem_dir=_create_standard_package(
                root / "codehacks_broken_sol_mismatch",
                sol_code=_STD_BROKEN_SOL_CPP,
            ),
            expected_decision="no_go",
            category="codehacks_answer_mismatch",
            strict=True,
            expected_blocking_gates=("answer_consistency",),
        ),
        BenchmarkCase(
            case_id="codehacks_incomplete_strict",
            problem_dir=_create_standard_package(
                root / "codehacks_incomplete_strict",
                val_code=None,
                wrong_code=None,
            ),
            expected_decision="no_go",
            category="codehacks_strict_completeness",
            strict=True,
            expected_blocking_gates=("validator_check", "wrong_solution_kill"),
        ),
    ]


def load_benchmark_cases_from_json(dataset_path: Path) -> list[BenchmarkCase]:
    """Load external labeled benchmark cases from a JSON manifest file."""
    raw = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("benchmark dataset manifest must be a JSON array")
    cases: list[BenchmarkCase] = []
    base = dataset_path.parent
    for idx, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"dataset entry #{idx} must be a JSON object")
        raw_path = Path(str(item["path"]))
        problem_dir = raw_path if raw_path.is_absolute() else (base / raw_path).resolve()
        exp = item["expected_decision"]
        if exp not in ("go", "no_go"):
            raise ValueError(f"invalid expected_decision {exp!r} in entry #{idx}")
        expected_gates_raw = item.get("expected_blocking_gates", [])
        expected_gates = (
            tuple(str(g) for g in expected_gates_raw)
            if isinstance(expected_gates_raw, list)
            else ()
        )
        cases.append(
            BenchmarkCase(
                case_id=str(item.get("case_id") or problem_dir.name),
                problem_dir=problem_dir,
                expected_decision=exp,
                category=str(item.get("category") or "external"),
                strict=bool(item.get("strict", True)),
                layout=item.get("layout", "auto"),
                mode=item.get("mode", "full"),
                expected_blocking_gates=expected_gates,
            )
        )
    return cases


async def evaluate_case(case: BenchmarkCase, tool: ProblemAuditTool) -> CaseOutcome:
    """Audit one fixture with ``ProblemAuditTool(standalone=True)`` and verify zero writes."""
    before = snapshot_tree(case.problem_dir) if case.problem_dir.is_dir() else {}
    t0 = time.perf_counter()
    res = await tool.execute(
        problem_dir=str(case.problem_dir),
        mode=case.mode,
        standalone=True,
        layout=case.layout,
        strict=case.strict,
        include_difficulty=False,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    after = snapshot_tree(case.problem_dir) if case.problem_dir.is_dir() else {}
    zero_write = before == after

    if not res.success:
        return CaseOutcome(
            case_id=case.case_id,
            category=case.category,
            expected_decision=case.expected_decision,
            actual_decision="error",
            matched=False,
            blocking_gates=[],
            warning_gates=[],
            zero_write_passed=zero_write,
            duration_ms=elapsed_ms,
            expected_gates_satisfied=False,
            error=res.error or "audit failed",
        )

    data = res.data
    actual = str(data.get("decision") or "error")
    blocking = [str(item.get("gate") or "unknown") for item in data.get("blocking_issues", [])]
    warnings = [
        str(item.get("gate") or "unknown")
        for item in data.get("risk_report", {}).get("warnings", [])
    ]
    gates_ok = all(g in blocking for g in case.expected_blocking_gates)
    matched = (actual == case.expected_decision) and gates_ok
    return CaseOutcome(
        case_id=case.case_id,
        category=case.category,
        expected_decision=case.expected_decision,
        actual_decision=actual,
        matched=matched,
        blocking_gates=blocking,
        warning_gates=warnings,
        zero_write_passed=zero_write,
        duration_ms=elapsed_ms,
        expected_gates_satisfied=gates_ok,
    )


async def run_benchmark(cases: list[BenchmarkCase]) -> BenchmarkReport:
    """Run ``ProblemAuditTool(standalone=True)`` across ``cases`` and compute TPR/TNR."""
    tool = ProblemAuditTool()
    t0 = time.perf_counter()
    outcomes: list[CaseOutcome] = []
    for case in cases:
        outcomes.append(await evaluate_case(case, tool))
    total_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    tp = tn = fp = fn = 0
    failure_breakdown: dict[str, int] = {}
    mismatched: list[str] = []
    zero_write_all = True

    for out in outcomes:
        if not out.zero_write_passed:
            zero_write_all = False
        for gate in out.blocking_gates:
            failure_breakdown[gate] = failure_breakdown.get(gate, 0) + 1
        if not out.matched:
            mismatched.append(out.case_id)
        if out.expected_decision == "go":
            if out.actual_decision == "go":
                tp += 1
            else:
                fn += 1
        else:
            if out.actual_decision == "no_go":
                tn += 1
            else:
                fp += 1

    tpr = round(tp / (tp + fn), 4) if (tp + fn) else 1.0
    tnr = round(tn / (tn + fp), 4) if (tn + fp) else 1.0
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    accuracy = round((tp + tn) / len(outcomes), 4) if outcomes else 0.0

    return BenchmarkReport(
        total=len(outcomes),
        tp=tp,
        tn=tn,
        fp=fp,
        fn=fn,
        tpr=tpr,
        tnr=tnr,
        precision=precision,
        accuracy=accuracy,
        zero_write_passed=zero_write_all,
        failure_breakdown=dict(sorted(failure_breakdown.items())),
        mismatched_cases=mismatched,
        outcomes=outcomes,
        total_duration_ms=total_ms,
    )


def run_default_benchmark() -> BenchmarkReport:
    """Create temporary default fixtures, run the benchmark, and clean up automatically."""
    with tempfile.TemporaryDirectory(prefix="autocode_bench_") as tmpdir:
        cases = build_default_benchmark_cases(Path(tmpdir))
        return asyncio.run(run_benchmark(cases))


def format_summary(report: BenchmarkReport) -> str:
    """Render a compact human-readable benchmark report."""
    lines = [
        f"AutoCode Standalone Benchmark: total={report.total}, TPR={report.tpr:.3f}, "
        f"TNR={report.tnr:.3f}, accuracy={report.accuracy:.3f}, zero_write={report.zero_write_passed}",
        f"Confusion Matrix: TP={report.tp} TN={report.tn} FP={report.fp} FN={report.fn}",
        f"Failure Breakdown by Gate: {json.dumps(report.failure_breakdown, ensure_ascii=False)}",
    ]
    for out in report.outcomes:
        status = "OK" if out.matched else "MISMATCH"
        lines.append(
            f"  [{status}] {out.case_id} ({out.category}): expected={out.expected_decision} "
            f"actual={out.actual_decision} gates={out.blocking_gates} "
            f"zero_write={out.zero_write_passed} ({out.duration_ms:.1f}ms)"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="Optional JSON manifest of external labeled problem fixtures (defaults to built-in suite)",
    )
    parser.add_argument(
        "--format",
        choices=("summary", "json"),
        default="summary",
        help="Output format (default: summary)",
    )
    parser.add_argument("--json", action="store_true", help="Shorthand for --format json")
    parser.add_argument("--output", type=Path, default=None, help="Optional path to save JSON report")
    parser.add_argument("--min-tpr", type=float, default=1.0, help="Minimum required TPR")
    parser.add_argument("--min-tnr", type=float, default=1.0, help="Minimum required TNR")
    args = parser.parse_args(argv)

    fmt = "json" if args.json else args.format
    if args.dataset is not None:
        cases = load_benchmark_cases_from_json(args.dataset.expanduser().resolve())
        report = asyncio.run(run_benchmark(cases))
    else:
        report = run_default_benchmark()

    payload = report.to_dict()
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if fmt == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_summary(report))

    thresholds_ok = (
        report.tpr >= args.min_tpr
        and report.tnr >= args.min_tnr
        and report.zero_write_passed
        and not report.mismatched_cases
    )
    return 0 if thresholds_ok else 1


if __name__ == "__main__":
    sys.exit(main())
