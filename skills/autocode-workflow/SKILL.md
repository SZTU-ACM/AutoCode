---
name: autocode-workflow
description: Use when creating competitive programming problems with AutoCode MCP tools. Triggers when using solution_build, validator_build, generator_build, or problem_generate_tests tools. Enforces the Validator-Generator-Checker workflow to prevent skipping steps.
disable-model-invocation: false
---

# AutoCode Problem Creation Workflow

Based on the paper "AutoCode: LLMs as Problem Setters for Competitive Programming", this workflow ensures rigorous problem creation with proper validation at each step.

## Workflow Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        AutoCode Problem Creation Pipeline                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  Phase 1: Setup                                                              │
│  ┌─────────────────┐                                                         │
│  │ problem_create  │ Initialize problem directory with testlib.h            │
│  └────────┬────────┘                                                         │
│           │                                                                   │
│  Phase 2: Solutions                                                          │
│  ┌────────┴────────┐     ┌─────────────────┐                                │
│  │ solution_build  │────►│ solution_build  │ (sol AND brute)                │
│  │    (sol)        │     │    (brute)      │                                │
│  └────────┬────────┘     └────────┬────────┘                                │
│           │                       │                                          │
│           └───────────┬───────────┘                                          │
│                       │                                                      │
│  Phase 3: Validation (Algorithm 1)                                           │
│  ┌────────────────────┴────────────────────┐                                │
│  │            validator_build               │ Build + test with 40 cases    │
│  │         (10 valid + 30 near-invalid)     │                               │
│  └────────────────────┬────────────────────┘                                │
│                       │                                                      │
│  Phase 4: Generation (Algorithm 2)                                           │
│  ┌────────────────────┴────────────────────┐                                │
│  │            generator_build               │ Build multi-strategy generator │
│  │   (tiny/random/extreme/tle strategies)   │                               │
│  └────────────────────┬────────────────────┘                                │
│                       │                                                      │
│  Phase 5: Verification                                                       │
│  ┌────────────────────┴────────────────────┐                                │
│  │           stress_test_run                │ Compare sol vs brute          │
│  │         (1000+ rounds, N≤100)            │                               │
│  └────────────────────┬────────────────────┘                                │
│                       │                                                      │
│  Phase 6: Output Verification (Algorithm 3, optional)                        │
│  ┌────────────────────┴────────────────────┐                                │
│  │            checker_build                 │ Build + test with 40 scenarios│
│  │       (for non-exact problems)           │                               │
│  └────────────────────┬────────────────────┘                                │
│                       │                                                      │
│  Phase 7: Sample Validation                                                  │
│  ┌────────────────────┴────────────────────┐                                │
│  │          problem_validate                │ Validate statement samples    │
│  │      (statement_samples + sample_files)  │ and test files                │
│  └────────────────────┬────────────────────┘                                │
│                       │                                                      │
│  Phase 8: Test Generation                                                    │
│  ┌────────────────────┴────────────────────┐                                │
│  │        problem_generate_tests            │ Generate final test data      │
│  │ (dedup + validator filter + extreme>=50%)│                               │
│  └────────────────────┬────────────────────┘                                │
│                       │                                                      │
│  Phase 9: Packaging                                                          │
│  ┌────────────────────┴────────────────────┐                                │
│  │        problem_pack_polygon              │ Export for Codeforces/Polygon │
│  └─────────────────────────────────────────┘                                │
│                                                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Step-by-Step Requirements

### Phase 1: Setup

**Step 1.1: Initialize Problem**
```
Tool: problem_create
Required: problem_dir, problem_name
Output: Directory structure with files/, solutions/, statements/, tests/
Verify: Check return success=true
```

### Phase 2: Solutions

**Step 2.1: Build Optimal Solution**
```
Tool: solution_build(solution_type="sol")
Required: problem_dir, code
Output: solutions/sol.exe
Verify: Check return success=true, binary_path exists
```

**Step 2.2: Build Brute Force Solution**
```
Tool: solution_build(solution_type="brute")
Required: problem_dir, code
Output: solutions/brute.exe
Verify: Check return success=true, binary_path exists
CRITICAL: Both sol AND brute must be built before proceeding
```

### Phase 3: Validator (Algorithm 1: BUILDVALIDATOR)

**Step 3.1: Build Validator**
```
Tool: validator_build
Required: problem_dir, code, test_cases
Test Cases: Generate 40 test cases:
  - 10 valid inputs (normal cases, boundary values)
  - 30 near-valid illegal inputs (slightly out of range, wrong format, etc.)
Output: files/val.exe, score, accuracy
Verify: Check accuracy >= 0.9 (36/40 correct)
```

**Validator Test Case Template:**
```json
[
  {"input": "1 2\n", "expected_valid": true},
  {"input": "0 0\n", "expected_valid": true},
  {"input": "-1000 1000\n", "expected_valid": true},
  {"input": "1001 0\n", "expected_valid": false},
  {"input": "1 2 3\n", "expected_valid": false},
  // ... generate 35 more cases
]
```

### Phase 4: Generator (Algorithm 2: BUILDGENERATORSUITE)

**Step 4.1: Build Generator**
```
Tool: generator_build
Required: problem_dir, code
Output: files/gen.exe
Verify: Check return success=true, binary_path exists
```

**Generator Protocol:**
```
gen.exe <seed> <type> <n_min> <n_max> <t_min> <t_max>

type values:
  1 = tiny (small exhaustive tests, N ≤ 10)
  2 = random (random data within constraints)
  3 = extreme (edge cases: overflow, precision, hash collisions)
  4 = tle (TLE-inducing data for performance testing)
```

### Phase 5: Stress Test

**Step 5.1: Run Stress Test**
```
Tool: stress_test_run
Required: problem_dir
Recommended: trials=1000, n_max=100 (keep brute fast)
Output: completed_rounds, total_rounds
Verify: Check "All N rounds passed"
CRITICAL: Must pass ALL rounds before proceeding
```

**If stress test fails:**
1. Examine the failing input
2. Compare sol_output vs brute_output
3. Identify which solution is wrong
4. Fix the bug
5. Rebuild the fixed solution
6. Re-run stress test

### Phase 6: Checker (Algorithm 3: BUILDCHECKER, Optional)

**When needed:** Problems with non-exact output (multiple correct answers, floating point, etc.)

**Step 6.1: Build Checker**
```
Tool: checker_build
Required: problem_dir, code, test_scenarios
Test Scenarios: Generate 40 scenarios:
  - AC cases (correct outputs)
  - WA cases (wrong outputs)
  - PE cases (presentation errors)
Output: files/checker.exe, accuracy
Verify: Check accuracy >= 0.9
```

**Checker Test Scenario Template:**
```json
[
  {
    "input": "1 2\n",
    "contestant_output": "3\n",
    "reference_output": "3\n",
    "expected_verdict": "AC"
  },
  {
    "input": "1 2\n",
    "contestant_output": "4\n",
    "reference_output": "3\n",
    "expected_verdict": "WA"
  },
  // ... generate 38 more scenarios
]
```

### Phase 7: Sample Validation

**Step 7.1: Validate Statement Samples**
```
Tool: problem_validate
Required: problem_dir
Optional: statement_samples (if not provided, auto-extract from README.md)
Output: validation results for statement_samples and sample_files
Verify: Check success=true, all samples passed
CRITICAL: Must pass validation before generating final tests
```

**Validation Types:**
- `statement_samples`: Validate samples in problem statement (README.md)
- `sample_files`: Validate sample files in tests/ directory

**If validation fails:**
1. Check the failing sample's expected output
2. Run sol manually to verify correct output
3. Update README.md or sample files as needed
4. Re-run validation

### Phase 8: Test Generation

**Step 8.1: Generate Final Tests**
```
Tool: problem_generate_tests
Required: problem_dir
Recommended: test_count=50, enable_dedup=true, enable_validator_filter=true
Output: tests/01.in ~ tests/50.in + corresponding answer files (`.ans` by default, or configured `answer_ext` such as `.out`)
Verify: Check generated_tests count matches test_count
Quality Gate: In final tests, type 3/4 (extreme + tle) should be >= ceil(test_count/2) when candidates are sufficient
Long-running note: sending new user messages may interrupt MCP execution; prefer waiting, or resume with `resume=true` if interrupted.
```

### Phase 9: Packaging

**Step 9.1: Pack for Polygon**
```
Tool: problem_pack_polygon
Required: problem_dir
Optional: time_limit (default 1s), memory_limit (default 256MB)
Output: problem.xml, organized directory structure
```

## Interactive Problem Workflow (Algorithm 4)

For interactive problems, replace Phase 3 (Validator) and Phase 6 (Checker) with:

**Phase 3-alt: Interactor (Algorithm 4: BUILDINTERACTOR)**

```
Tool: interactor_build
Required: problem_dir, code
Optional: reference_solution_path, mutant_solutions
Output: files/interactor.exe, pass_rate, fail_rate
Verify: 
  - pass_rate = 1.0 (correct solution passes)
  - fail_rate >= 0.8 (wrong solutions rejected)
```

**Mutant Solutions:**
Generate 3-5 mutant solutions with common bugs:
- Off-by-one errors
- Wrong algorithm
- Missing edge cases

## Enforcement Rules

### MANDATORY Sequence

| Step | Tool | Prerequisites | Must Verify Before Next |
|------|------|---------------|-------------------------|
| 1 | `problem_create` | None | `success=true` |
| 2a | `solution_build(sol)` | Step 1 | `success=true`, sol.exe exists |
| 2b | `solution_build(brute)` | Step 2a | `success=true`, brute.exe exists |
| 3 | `validator_build` | Step 2b | `accuracy >= 0.9` |
| 4 | `generator_build` | Step 3 | `success=true`, gen.exe exists |
| 5 | `stress_test_run` | Step 4 | `"All N rounds passed"` |
| 6 | `checker_build` (optional) | Step 5 | `accuracy >= 0.9` |
| 7 | `problem_validate` | Step 5 or 6 | `success=true`, all samples passed |
| 8 | `problem_generate_tests` | Step 7 | `generated_tests == test_count` and `type3+type4 >= ceil(test_count/2)` (if candidates sufficient) |
| 9 | `problem_pack_polygon` | Step 8 | `success=true` |

### FORBIDDEN Actions

1. **NEVER** call `generator_build` before `validator_build`
2. **NEVER** call `stress_test_run` before building BOTH sol AND brute
3. **NEVER** call `problem_validate` before stress test passes
4. **NEVER** call `problem_generate_tests` before validation passes
5. **NEVER** skip stress test verification
6. **NEVER** proceed if any step returns `success=false`

## Error Recovery

### Compilation Errors
1. Read the `compile_log` from the result
2. Fix the C++ code
3. Retry the same tool call

### Test Failures
1. Examine `test_results` or `input_data` from the result
2. Identify the root cause
3. Fix the relevant code (solution, validator, or generator)
4. Rebuild and re-test

### Stress Test Mismatch
1. The result contains `input_data`, `sol_output`, `brute_output`
2. Manually verify which output is correct
3. Fix the buggy solution
4. Rebuild and re-run stress test

### Validation Failure
1. The result contains `statement_samples` or `sample_files` details
2. Check which sample failed (expected vs actual output)
3. Verify correct output by running sol manually
4. Update README.md or sample files with correct output
5. Re-run validation

## Quality Checklist

Before considering the problem complete:

- [ ] Problem directory created with proper structure
- [ ] Optimal solution compiles and runs
- [ ] Brute force solution compiles and runs
- [ ] Validator passes 90%+ test cases
- [ ] Generator produces valid inputs
- [ ] Stress test passes 1000+ rounds
- [ ] (If applicable) Checker passes 90%+ scenarios
- [ ] Statement samples validated (problem_validate passed)
- [ ] Sample files validated (problem_validate passed)
- [ ] Final test data generated (50+ tests)
- [ ] Final test data has at least 50% extreme/tle cases when candidate pool allows
- [ ] type=3/type=4 generation logic is semantically different (not just max-parameter duplication)
- [ ] Polygon package created

## Example Complete Workflow

```
# Phase 1: Setup
result = problem_create(problem_dir="problems/ab", problem_name="A+B")
assert result["success"] == True

# Phase 2: Solutions
result = solution_build(problem_dir="problems/ab", solution_type="sol", code=sol_code)
assert result["success"] == True

result = solution_build(problem_dir="problems/ab", solution_type="brute", code=brute_code)
assert result["success"] == True

# Phase 3: Validator
result = validator_build(problem_dir="problems/ab", code=val_code, test_cases=validator_tests)
assert result["success"] == True
assert result["accuracy"] >= 0.9

# Phase 4: Generator
result = generator_build(problem_dir="problems/ab", code=gen_code)
assert result["success"] == True

# Phase 5: Stress Test
result = stress_test_run(problem_dir="problems/ab", trials=1000, n_max=100)
assert result["completed_rounds"] == result["total_rounds"]

# Phase 6: Checker (optional)
result = checker_build(problem_dir="problems/ab", code=checker_code, test_scenarios=checker_tests)
assert result["accuracy"] >= 0.9

# Phase 7: Validate Samples
result = problem_validate(problem_dir="problems/ab")
assert result["success"] == True

# Phase 8: Generate Tests
result = problem_generate_tests(problem_dir="problems/ab", test_count=50)
assert len(result["generated_tests"]) == 50

# Phase 9: Package
result = problem_pack_polygon(problem_dir="problems/ab", time_limit=1, memory_limit=256)
assert result["success"] == True
```

## When User Requests to Skip Steps

If the user asks to skip steps (e.g., "just generate tests"), you MUST:

1. **Explain the workflow requirements**: "The AutoCode workflow requires [X] before [Y] because..."
2. **Show current progress**: "You have completed: [list]. Missing: [list]"
3. **Offer to run missing steps**: "Would you like me to run the missing steps now?"
4. **Never proceed without verification**: Each step's output is critical for quality

## Tool Reference

| Tool | Paper Algorithm | Purpose |
|------|-----------------|---------|
| `validator_build` | Algorithm 1 | Build and validate input constraints |
| `generator_build` | Algorithm 2 | Build multi-strategy test generator |
| `checker_build` | Algorithm 3 | Build output verification |
| `interactor_build` | Algorithm 4 | Build interactive problem handler |
| `stress_test_run` | - | Verify solution correctness |
| `problem_validate` | - | Validate statement samples and sample files |
| `problem_generate_tests` | - | Generate final test dataset |
