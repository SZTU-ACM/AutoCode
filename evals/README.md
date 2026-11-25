# AutoCode Evaluation Suite (`evals/`)

This directory provides reproducible, multi-layer evaluation harnesses for AutoCode's deterministic verification engine, multi-host prompts, and end-to-end problem-setting workflows:

1. **Standalone Deterministic TPR/TNR Benchmark (`evals/standalone_benchmark.py`)**
   - Runs `ProblemAuditTool(standalone=True)` in-process (zero LLM calls, zero target-directory writes) against labeled `go` (`CodeContests+`-style clean packages across `standard`, `spj`, `interactive`, `communication`) and `no_go` (`Codehacks`-style defective/hackable packages: surviving wrong solutions, byte-identical duplicate tests, broken solution outputs, missing validator/wrong-solution completeness under `--strict`, and symlink escapes).
   - Computes exact **TPR**, **TNR**, precision, accuracy, confusion matrix (`tp`, `tn`, `fp`, `fn`), per-gate failure breakdown, and filesystem zero-write verification.

2. **Harbor / Inspect-AI Benchmark Tasks (`evals/harbor_tasks/*/task.yaml`)**
   - End-to-end agent evaluation tasks covering all four AutoCode problem types:
     - `evals/harbor_tasks/standard/task.yaml`
     - `evals/harbor_tasks/spj/task.yaml`
     - `evals/harbor_tasks/interactive/task.yaml`
     - `evals/harbor_tasks/communication/task.yaml`
   - Each task defines isolated sandbox configuration, prompt instructions, required artifacts, and deterministic acceptance checks (`autocode-audit --standalone --strict`, compiled wrong-solution kills, and zero-write checks).

3. **Promptfoo Multi-Host Prompt Matrix (`evals/promptfoo.yaml`)**
   - Evaluates prompt and skill consistency across AutoCode's four supported agent hosts:
     - `claude-code`
     - `codex`
     - `dsh` (DeepSeek Harness)
     - `antigravity` (`agy`)

## Running Locally

### 1. Offline Standalone Benchmark (No API Keys Required)

```bash
uv run python evals/standalone_benchmark.py --format summary
uv run python evals/standalone_benchmark.py --format json --min-tpr 1.0 --min-tnr 1.0
```

You can also point the runner at an external labeled dataset JSON manifest:

```bash
uv run python evals/standalone_benchmark.py --dataset /path/to/benchmark_manifest.json --format json
```

Each entry in an external JSON manifest accepts:

```json
[
  {
    "case_id": "codecontests_plus_001",
    "path": "/abs/or/relative/path/to/problem_dir",
    "expected_decision": "go",
    "category": "standard",
    "strict": true,
    "layout": "auto",
    "mode": "full"
  }
]
```

### 2. Red-Team Gate Sabotage & Eval Regression Suite

```bash
uv run pytest tests/test_gate_redteam.py -v
```

### 3. Harbor / Inspect-AI Runner (When Installed in Containerized Harness)

Each `evals/harbor_tasks/<type>/task.yaml` uses standard `schema_version: "1.0"`, `sandbox`, `instruction`, and `acceptance_checks` fields compatible with Harbor / Inspect-AI adapters. Acceptance verification inside the task container executes:

```bash
uv run autocode-verify <problem_dir> --status
uv run autocode-audit <problem_dir> --standalone --strict --format json
```

### 4. Promptfoo Host Comparison (Optional CLI)

```bash
npx promptfoo eval -c evals/promptfoo.yaml
```
