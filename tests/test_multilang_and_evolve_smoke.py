"""Smoke and integration tests for multi-language runner, Python solutions, and evolutionary generator harness."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from autocode_mcp.tools.build_all import ProblemBuildAllTool
from autocode_mcp.tools.solution import SolutionBuildTool, SolutionRunTool
from autocode_mcp.utils.lang_runner import (
    DEFAULT_TIME_LIMIT_MULTIPLIERS,
    compile_or_prepare,
    infer_language_from_path,
    resolve_time_limit_multiplier,
    run_source,
)
from autocode_mcp.utils.sandbox import SandboxExecutionPlan, prepare_sandbox_mounts, sandbox_context
from autocode_mcp.workflow.manifest import save_manifest
from autocode_mcp.workflow.models import (
    AutoCodeManifest,
    SolutionEntry,
    validate_executable_solution_language,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_evolve_module() -> Any:
    script_path = REPO_ROOT / "scripts" / "evolve_generator.py"
    spec = importlib.util.spec_from_file_location("evolve_generator", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evolve_generator"] = mod
    spec.loader.exec_module(mod)
    return mod


def _assert_no_pycache(root: Path) -> None:
    for item in root.rglob("*"):
        assert item.name != "__pycache__", f"Unexpected __pycache__ directory in {item}"
        assert item.suffix not in {".pyc", ".pyo"}, f"Unexpected bytecode file {item}"


# ============== lang_runner & multiplier contracts ==============


def test_infer_language_and_multiplier_resolution():
    assert infer_language_from_path("solutions/sol.py") == "python"
    assert infer_language_from_path("solutions/sol.cpp") == "cpp"
    assert infer_language_from_path("solutions/sol.cc") == "cpp"
    assert infer_language_from_path("solutions/sol.rs") == "rust"
    assert infer_language_from_path("solutions/Main.java") == "java"
    assert infer_language_from_path("solutions/sol.rb") is None

    assert DEFAULT_TIME_LIMIT_MULTIPLIERS == {
        "cpp": 1.0,
        "rust": 1.0,
        "java": 2.0,
        "pypy": 2.5,
        "python": 5.0,
    }
    assert resolve_time_limit_multiplier("cpp") == 1.0
    assert resolve_time_limit_multiplier("rust") == 1.0
    assert resolve_time_limit_multiplier("java") == 2.0
    assert resolve_time_limit_multiplier("pypy") == 2.5
    assert resolve_time_limit_multiplier("python") == 5.0

    manifest = AutoCodeManifest(
        problem_name="Multilang",
        time_limit_multipliers={"python": 3.5, "pypy": 2.0},
    )
    assert resolve_time_limit_multiplier("python", manifest) == 3.5
    assert resolve_time_limit_multiplier("pypy", manifest) == 2.0
    assert resolve_time_limit_multiplier("java", manifest) == 2.0

    for bad_val in (True, False, 0.0, -2.0, float("nan"), float("inf"), "3.0"):
        bad_manifest = SimpleNamespace(time_limit_multipliers={"python": bad_val})
        with pytest.raises(ValueError):
            resolve_time_limit_multiplier("python", bad_manifest)

    assert validate_executable_solution_language("python") == "python"
    assert validate_executable_solution_language("cpp") == "cpp"
    with pytest.raises(ValueError, match="Unsupported"):
        validate_executable_solution_language("ruby")


@pytest.mark.asyncio
async def test_lang_runner_python_zero_pycache_and_sandbox_awareness(tmp_path: Path):
    sol_dir = tmp_path / "solutions"
    sol_dir.mkdir()
    helper_py = sol_dir / "helper_mod.py"
    helper_py.write_text("def add_one(x: int) -> int:\n    return x + 1\n", encoding="utf-8")

    sol_py = sol_dir / "sol.py"
    sol_py.write_text(
        "import sys\n"
        "from helper_mod import add_one\n"
        "n = int(sys.stdin.read().strip())\n"
        "print(add_one(n))\n",
        encoding="utf-8",
    )

    prep = await compile_or_prepare(str(sol_py), language="python")
    assert prep.success, prep.error
    assert prep.binary_path == str(sol_py.resolve())
    _assert_no_pycache(tmp_path)

    bad_py = sol_dir / "bad.py"
    bad_py.write_text("def broken(:\n    pass\n", encoding="utf-8")
    bad_prep = await compile_or_prepare(str(bad_py), language="python")
    assert not bad_prep.success
    assert "SyntaxError" in (bad_prep.error or bad_prep.stderr)
    _assert_no_pycache(tmp_path)

    class RecordingSandbox:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        @property
        def name(self) -> str:
            return "recording"

        def is_available(self) -> bool:
            return True

        def wrap_command(
            self,
            cmd: Sequence[str],
            *,
            cwd: str | None = None,
            ro_paths: Sequence[str] = (),
            rw_paths: Sequence[str] = (),
            allow_network: bool = False,
            memory_limit_mb: float | None = None,
            time_limit_ms: float | None = None,
        ) -> SandboxExecutionPlan:
            clean_cwd, mounts = prepare_sandbox_mounts(
                cwd=cwd,
                ro_paths=ro_paths,
                rw_paths=rw_paths,
            )
            self.calls.append(
                {
                    "cmd": list(cmd),
                    "cwd": clean_cwd,
                    "ro_paths": [m.path for m in mounts if not m.read_write],
                    "rw_paths": [m.path for m in mounts if m.read_write],
                    "allow_network": allow_network,
                    "memory_limit_mb": memory_limit_mb,
                    "time_limit_ms": time_limit_ms,
                }
            )
            return SandboxExecutionPlan(
                argv=list(cmd),
                cwd=clean_cwd,
                backend=self.name,
                ro_paths=tuple(ro_paths),
                rw_paths=tuple(rw_paths),
                allow_network=allow_network,
            )

    recorder = RecordingSandbox()
    with sandbox_context(recorder):
        run_res = await run_source(str(sol_py), stdin="41\n", timeout=3.0, memory_mb=256)

    assert run_res.success, run_res.error
    assert run_res.stdout.strip() == "42"
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["allow_network"] is False
    assert str(sol_dir.resolve()) in call["ro_paths"]
    assert call["rw_paths"] == []
    _assert_no_pycache(tmp_path)


# ============== Real .py build_all, solution_build, solution_run ==============


@pytest.mark.asyncio
async def test_python_solutions_build_all_and_run_ac_wa_tle_scaled(tmp_path: Path):
    """Build real .py solutions via SolutionBuildTool/ProblemBuildAllTool and test AC, WA, and scaled TLE."""
    prob = tmp_path / "py_prob"
    files_dir = prob / "files"
    sol_dir = prob / "solutions"
    files_dir.mkdir(parents=True)
    sol_dir.mkdir(parents=True)

    (files_dir / "gen.cpp").write_text(
        "#include <iostream>\nint main() { std::cout << \"3 4\\n\"; return 0; }\n",
        encoding="utf-8",
    )
    (sol_dir / "sol.py").write_text(
        "import sys, time\n"
        "data = sys.stdin.read().split()\n"
        "if data == ['sleep']:\n"
        "    time.sleep(0.45)\n"
        "    print('slept')\n"
        "else:\n"
        "    a, b = map(int, data)\n"
        "    print(a + b)\n",
        encoding="utf-8",
    )
    (sol_dir / "brute.py").write_text(
        "import sys, time\n"
        "data = sys.stdin.read().split()\n"
        "if data == ['slow_brute']:\n"
        "    time.sleep(0.9)\n"
        "    print('brute_ok')\n"
        "else:\n"
        "    a, b = map(int, data)\n"
        "    print(a + b)\n",
        encoding="utf-8",
    )
    (sol_dir / "wrong_wa.py").write_text(
        "import sys\n"
        "a, b = map(int, sys.stdin.read().split())\n"
        "print(a + b + 1)\n",
        encoding="utf-8",
    )
    (sol_dir / "wrong_tle.py").write_text(
        "import time\n"
        "time.sleep(1.5)\n"
        "print('too_slow')\n",
        encoding="utf-8",
    )

    manifest = AutoCodeManifest(
        problem_name="Python Sum",
        time_limit_ms=250,
        memory_limit_mb=256,
        time_limit_multipliers={"cpp": 1.0, "python": 3.0},
        solutions=[
            SolutionEntry(name="sol", role="main", language="python", path="solutions/sol.py", expected_verdict="AC"),
            SolutionEntry(name="brute", role="brute", language="python", path="solutions/brute.py", expected_verdict="AC"),
            SolutionEntry(name="wrong_wa", role="wrong", language="python", path="solutions/wrong_wa.py", expected="fail", expected_verdict="WA"),
            SolutionEntry(name="wrong_tle", role="wrong", language="python", path="solutions/wrong_tle.py", expected="fail", expected_verdict="TLE"),
        ],
    )
    save_manifest(str(prob), manifest)

    # 1. ProblemBuildAllTool must check all declared .py solutions alongside C++ files
    build_all = ProblemBuildAllTool()
    ba_res = await build_all.execute(problem_dir=str(prob))
    assert ba_res.success, ba_res.error
    assert set(ba_res.data["compiled"].keys()) == {
        "files/gen.cpp",
        "solutions/sol.py",
        "solutions/brute.py",
        "solutions/wrong_wa.py",
        "solutions/wrong_tle.py",
    }
    _assert_no_pycache(prob)

    # 2. SolutionBuildTool resolves .py default path and builds explicitly via source_path
    sbuild = SolutionBuildTool()
    sb_sol = await sbuild.execute(problem_dir=str(prob), solution_type="sol")
    assert sb_sol.success, sb_sol.error
    assert sb_sol.data["language"] == "python"
    assert sb_sol.data["binary_path"].endswith("sol.py")

    sb_tle = await sbuild.execute(
        problem_dir=str(prob),
        solution_type="wrong",
        name="wrong_tle",
        source_path="solutions/wrong_tle.py",
    )
    assert sb_tle.success, sb_tle.error
    assert sb_tle.data["language"] == "python"

    # Invalid Python syntax must fail cleanly in both SolutionBuildTool and ProblemBuildAllTool
    bad_py = sol_dir / "broken_ref.py"
    bad_py.write_text("if True print('oops')\n", encoding="utf-8")
    sb_bad = await sbuild.execute(
        problem_dir=str(prob),
        solution_type="reference",
        name="broken_ref",
        source_path=str(bad_py),
    )
    assert not sb_bad.success
    assert "compilation" in (sb_bad.error or "").lower()
    bad_py.unlink()

    # 3. SolutionRunTool: AC, WA, brute, and scaled TLE
    srun = SolutionRunTool()

    # AC (fast)
    ac_res = await srun.execute(problem_dir=str(prob), solution_type="sol", input_data="3 5\n")
    assert ac_res.success, ac_res.error
    assert ac_res.data["stdout"].strip() == "8"
    assert ac_res.data["language"] == "python"
    assert ac_res.data["time_limit_multiplier"] == 3.0
    assert ac_res.data["effective_timeout_sec"] == pytest.approx(0.75)

    # AC (0.45s > 0.25s base C++ limit, < 0.75s scaled Python limit!)
    scaled_ac_res = await srun.execute(problem_dir=str(prob), solution_type="sol", input_data="sleep\n")
    assert scaled_ac_res.success, scaled_ac_res.error
    assert scaled_ac_res.data["stdout"].strip() == "slept"

    # WA
    wa_res = await srun.execute(
        problem_dir=str(prob),
        solution_type="wrong",
        name="wrong_wa",
        input_data="3 5\n",
    )
    assert wa_res.success, wa_res.error
    assert wa_res.data["stdout"].strip() == "9"

    # TLE scaled by time_limit_multipliers["python"] (0.25s * 3.0 = 0.75s < 1.5s)
    tle_res = await srun.execute(
        problem_dir=str(prob),
        solution_type="wrong",
        name="wrong_tle",
        input_data="3 5\n",
    )
    assert not tle_res.success
    assert "timed out" in (tle_res.error or "").lower()
    assert tle_res.data["effective_timeout_sec"] == pytest.approx(0.75)

    # brute is not capped by manifest.time_limit_ms
    brute_res = await srun.execute(
        problem_dir=str(prob),
        solution_type="brute",
        input_data="slow_brute\n",
        timeout=2,
    )
    assert brute_res.success, brute_res.error
    assert brute_res.data["stdout"].strip() == "brute_ok"

    _assert_no_pycache(prob)


@pytest.mark.asyncio
async def test_pure_python_build_all_partial_failure_and_missing_toolchain_paths(tmp_path: Path):
    """Pure-Python problem_build_all validates without C++ sources and reports bad .py syntax."""
    prob = tmp_path / "pure_py"
    sol_dir = prob / "solutions"
    sol_dir.mkdir(parents=True)
    (sol_dir / "sol.py").write_text("print(42)\n", encoding="utf-8")
    (sol_dir / "brute.py").write_text("def bad(:\n    pass\n", encoding="utf-8")

    ba_res = await ProblemBuildAllTool().execute(problem_dir=str(prob))
    assert not ba_res.success
    assert "solutions/sol.py" in ba_res.data["compiled"]
    assert "solutions/brute.py" in ba_res.data["failures"]
    _assert_no_pycache(prob)

    # Fix brute.py -> pure-Python build_all succeeds cleanly
    (sol_dir / "brute.py").write_text("print(42)\n", encoding="utf-8")
    ba_ok = await ProblemBuildAllTool().execute(problem_dir=str(prob))
    assert ba_ok.success, ba_ok.error
    assert set(ba_ok.data["compiled"].keys()) == {"solutions/sol.py", "solutions/brute.py"}
    _assert_no_pycache(prob)

    # Missing explicit compiler/interpreter fails cleanly without crashing
    for lang, src_name, bad_bin in (
        ("pypy", "sol.py", "nonexistent_pypy3_tool"),
        ("rust", "sol.rs", "nonexistent_rustc_tool"),
        ("java", "Main.java", "nonexistent_javac_tool"),
    ):
        dummy_src = tmp_path / src_name
        dummy_src.write_text("placeholder\n", encoding="utf-8")
        res = await compile_or_prepare(str(dummy_src), language=lang, compiler=bad_bin)
        assert not res.success
        assert res.error and "not found" in res.error.lower()


@pytest.mark.asyncio
async def test_lang_runner_rust_if_available(tmp_path: Path):
    """Compile and execute a real Rust program via lang_runner when rustc is installed."""
    rustc = shutil.which("rustc")
    if not rustc or subprocess.run([rustc, "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("rustc not available on host")

    rs_src = tmp_path / "sum.rs"
    rs_bin = tmp_path / ("sum.exe" if sys.platform == "win32" else "sum")
    rs_src.write_text(
        'use std::io::{self, Read};\n'
        'fn main() {\n'
        '    let mut buf = String::new();\n'
        '    io::stdin().read_to_string(&mut buf).unwrap();\n'
        '    let s: i64 = buf.split_whitespace().map(|x| x.parse::<i64>().unwrap()).sum();\n'
        '    println!("{}", s);\n'
        '}\n',
        encoding="utf-8",
    )
    comp = await compile_or_prepare(str(rs_src), str(rs_bin), language="rust")
    assert comp.success, comp.stderr or comp.error
    run_res = await run_source(str(rs_bin), stdin="10 32\n", language="rust", timeout=5)
    assert run_res.success, run_res.stderr or run_res.error
    assert run_res.stdout.strip() == "42"


# ============== scripts/evolve_generator.py & skill smoke tests ==============


def test_evolve_generator_multi_island_fitness_and_novelty_filter():
    ev = _load_evolve_module()

    weights = ev.FitnessWeights(killed_wrong_weight=10.0)
    m = ev.EvaluationMetrics(
        killed_wrongs=2,
        killed_wrong_names=("w1", "w2"),
        branch_coverage_bonus=3.5,
        runtime_pressure=1.2,
        sample_diversity=0.8,
    )
    fb = ev.compute_composite_fitness(m, weights)
    assert fb.total_fitness == pytest.approx(2 * 10.0 + 3.5 + 1.2 + 0.8)

    # NoveltyFilter rejects exact and near-duplicate behaviors while accepting distinct ones
    nf = ev.NoveltyFilter(novelty_threshold=0.25)
    m_dup = ev.EvaluationMetrics(
        killed_wrongs=2,
        killed_wrong_names=("w1", "w2"),
        branch_coverage_bonus=3.5,
        runtime_pressure=1.2,
        sample_diversity=0.8,
        behavior_features=("type:3", "tag:max_bound", "cov_b:7"),
    )
    m_novel = ev.EvaluationMetrics(
        killed_wrongs=3,
        killed_wrong_names=("w1", "w2", "w3"),
        branch_coverage_bonus=4.5,
        runtime_pressure=1.8,
        sample_diversity=1.4,
        behavior_features=("type:4", "tag:broom", "kill:w3", "cov_b:9"),
    )
    ok1, dist1, _ = nf.evaluate(m_dup)
    ok2, dist2, _ = nf.evaluate(m_dup)
    ok3, dist3, _ = nf.evaluate(m_novel)
    assert ok1 is True and dist1 == 1.0
    assert ok2 is False and dist2 == 0.0
    assert ok3 is True and dist3 >= 0.25

    islands = (
        ev.IslandSpec(island_id="isl_a", name="Corners", strategy_bias="corner_extremes", population_size=4, elite_count=1),
        ev.IslandSpec(island_id="isl_b", name="TLE", strategy_bias="tle_stress_adversarial", population_size=4, elite_count=1),
        ev.IslandSpec(island_id="isl_c", name="Hash", strategy_bias="hash_and_overflow", population_size=4, elite_count=1),
    )
    cfg = ev.EvolutionConfig(
        generations=4,
        islands=islands,
        seed=123,
        novelty_threshold=0.15,
        fitness_weights=weights,
        n_limit=1000,
    )
    res1 = ev.run_evolution(cfg)
    res2 = ev.run_evolution(cfg)
    assert res1.to_dict() == res2.to_dict()
    assert res1.best_candidate is not None
    assert res1.best_candidate.fitness.total_fitness > 0.0
    assert set(res1.island_bests.keys()) == {"isl_a", "isl_b", "isl_c"}
    assert res1.rejected_duplicates > 0
    assert res1.accepted_novel == len(res1.archive)
    assert len(res1.history) == 4
    assert res1.history[-1].best_fitness >= res1.history[0].best_fitness


@pytest.mark.asyncio
async def test_evolve_generator_problem_dir_evaluator_with_python_solutions(tmp_path: Path):
    """ProblemDirEvaluator runs real gen/val/sol.py/wrong_wa.py inside a problem_dir without __pycache__."""
    ev = _load_evolve_module()
    prob = tmp_path / "evo_prob"
    (prob / "files").mkdir(parents=True)
    (prob / "solutions").mkdir(parents=True)

    (prob / "files" / "gen.cpp").write_text(
        "#include <iostream>\n"
        "#include <cstdlib>\n"
        "int main(int argc, char** argv) {\n"
        "    int seed = argc > 1 ? std::atoi(argv[1]) : 1;\n"
        "    int nmax = argc > 4 ? std::atoi(argv[4]) : 5;\n"
        "    int val = (nmax >= 100) ? 1000 : (seed % 10 + 1);\n"
        "    std::cout << val << ' ' << 2 << '\\n';\n"
        "    return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    (prob / "files" / "val.cpp").write_text(
        "#include <iostream>\n"
        "int main() { int a, b; if (!(std::cin >> a >> b)) return 1; return (a >= 1 && a <= 1000) ? 0 : 1; }\n",
        encoding="utf-8",
    )
    (prob / "solutions" / "sol.py").write_text(
        "import sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b)\n",
        encoding="utf-8",
    )
    (prob / "solutions" / "wrong_large.py").write_text(
        "import sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b if a < 100 else 0)\n",
        encoding="utf-8",
    )
    manifest = AutoCodeManifest(
        problem_name="Evo Live",
        time_limit_ms=500,
        solutions=[
            SolutionEntry(name="sol", role="main", language="python", path="solutions/sol.py"),
            SolutionEntry(name="wrong_large", role="wrong", language="python", path="solutions/wrong_large.py", expected="fail", expected_verdict="WA"),
        ],
    )
    save_manifest(str(prob), manifest)

    ba_res = await ProblemBuildAllTool().execute(problem_dir=str(prob))
    assert ba_res.success, ba_res.error

    evaluator = ev.ProblemDirEvaluator(prob, sample_seeds=(1, 2))
    cand_small = ev.GeneratorCandidate("c_small", "isl", 0, "1", 1, 10)
    cand_large = ev.GeneratorCandidate("c_large", "isl", 0, "3", 100, 100)

    m_small = await evaluator._evaluate_async(cand_small)
    m_large = await evaluator._evaluate_async(cand_large)
    assert m_small.validator_passed and m_small.sol_passed
    assert m_small.killed_wrongs == 0
    assert m_large.validator_passed and m_large.sol_passed
    assert "wrong_large" in m_large.killed_wrong_names
    assert ev.compute_composite_fitness(m_large).total_fitness > ev.compute_composite_fitness(m_small).total_fitness
    _assert_no_pycache(prob)


def test_evolve_generator_cli_help_and_demo_json():
    help_proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "evolve_generator.py"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_proc.returncode == 0, help_proc.stderr
    assert "--novelty-threshold" in help_proc.stdout
    assert "--killed-wrong-weight" in help_proc.stdout

    demo_proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "evolve_generator.py"),
            "--demo",
            "--generations",
            "3",
            "--islands",
            "3",
            "--population-size",
            "4",
            "--seed",
            "7",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert demo_proc.returncode == 0, demo_proc.stderr
    payload = json.loads(demo_proc.stdout)
    assert payload["best_candidate"] is not None
    assert payload["best_candidate"]["fitness"]["total_fitness"] > 0
    assert len(payload["island_bests"]) == 3
    assert payload["rejected_duplicates"] >= 0
    assert len(payload["history"]) == 3


def test_zero_llm_sdk_imports_and_evolutionary_skill_frontmatter():
    forbidden_sdks = ("openai", "anthropic", "google.genai", "google.generativeai", "litellm", "langchain")
    for rel in (
        "src/autocode_mcp/utils/lang_runner.py",
        "scripts/evolve_generator.py",
    ):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for sdk in forbidden_sdks:
            assert f"import {sdk}" not in text, f"Forbidden LLM SDK {sdk!r} in {rel}"
            assert f"from {sdk}" not in text, f"Forbidden LLM SDK {sdk!r} in {rel}"

    skill_path = REPO_ROOT / "skills" / "evolutionary-testdata" / "SKILL.md"
    assert skill_path.is_file()
    raw = skill_path.read_text(encoding="utf-8")
    assert raw.startswith("---\n")
    _, fm_raw, body = raw.split("---\n", 2)
    fm = yaml.safe_load(fm_raw)
    assert isinstance(fm, dict)
    assert fm["name"] == "evolutionary-testdata"
    assert isinstance(fm["description"], str) and fm["description"].strip()
    for section in (
        "Purpose",
        "Trigger Conditions",
        "Step-by-Step Execution Guidance",
        "Required Output Format",
        "Decision Rules",
        "Forbidden Behavior",
    ):
        assert section in body
    assert "decision" in body and "go" in body and "no_go" in body
    assert "scripts/evolve_generator.py" in body
