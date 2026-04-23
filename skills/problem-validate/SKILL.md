---
name: problem-validate
description: Validate statement samples and sample files for competitive programming problems. Ensures the expected outputs in problem statements match the actual solution output.
disable-model-invocation: false
---

# Problem Validation Skill

This skill guides the validation of problem statement samples and sample files to ensure correctness before generating final test data.

## Overview

The `problem_validate` tool verifies that:

1. **Statement Samples**: The expected outputs in the problem statement (README.md) match what the solution actually produces
2. **Sample Files**: The sample files in `tests/` directory (e.g., `01.in`, `01.ans`) are consistent with the solution

This catches common errors like:
- Wrong expected output in problem statement
- Sample files that don't match the problem description
- Solution bugs that would fail on the provided examples

## When to Use

Use this skill after:
- `stress_test_run` has passed (solution is verified correct)
- Before `problem_generate_tests` (ensures samples are correct)

## Validation Types

### 1. Statement Samples (`statement_samples`)

Validates samples embedded in the problem statement (README.md).

**Supported formats:**

```markdown
**样例输入 1**
```text
5
3 -5 2 -8 4
```

**样例输出 1**
```text
2
```
```

Or English format:

```markdown
**Sample Input 1**
```text
5
3 -5 2 -8 4
```

**Sample Output 1**
```text
2
```
```

### 2. Sample Files (`sample_files`)

Validates sample files in the `tests/` directory:
- `01.in`, `01.ans`
- `02.in`, `02.ans`
- etc.

## Usage

### Basic Usage

```json
{
  "problem_dir": "problems/emergency-escape"
}
```

Auto-extracts samples from README.md and validates all sample files.

### With Explicit Samples

```json
{
  "problem_dir": "problems/emergency-escape",
  "statement_samples": [
    {
      "input": "5\n3 -5 2 -8 4",
      "expected_output": "2"
    }
  ]
}
```

### Validate Only Statement Samples

```json
{
  "problem_dir": "problems/emergency-escape",
  "validate_types": ["statement_samples"]
}
```

### Validate Only Sample Files

```json
{
  "problem_dir": "problems/emergency-escape",
  "validate_types": ["sample_files"]
}
```

## Output Interpretation

### Success

```json
{
  "success": true,
  "data": {
    "statement_samples": {
      "validated": true,
      "passed": 2,
      "failed": 0,
      "total": 2
    },
    "sample_files": {
      "validated": true,
      "passed": 3,
      "failed": 0,
      "total": 3
    }
  }
}
```

### Failure

```json
{
  "success": false,
  "error": "Validation failed",
  "data": {
    "statement_samples": {
      "validated": true,
      "passed": 1,
      "failed": 1,
      "total": 2,
      "details": [
        {
          "index": 1,
          "input": "5\n3 -5 2 -8 4",
          "expected": "5",
          "actual": "2",
          "passed": false
        }
      ]
    }
  }
}
```

## Error Recovery

### If Statement Sample Fails

1. Check the `actual` output - this is what the solution produces
2. Verify manually if `actual` is correct
3. If correct, update README.md with the correct expected output
4. If incorrect, the solution has a bug - fix and rebuild

### If Sample File Fails

1. Check if the `.ans` file exists
2. Compare `actual` vs `expected` output
3. Update `.ans` file if solution is correct
4. Or fix solution if it's wrong

## Output Comparison Rules

The tool uses multiple comparison methods:

1. **Exact match**: Direct string comparison
2. **Whitespace-insensitive**: Compares after stripping trailing whitespace from each line
3. **Token match**: Compares after splitting on whitespace
4. **Floating-point tolerance**: Compares numeric values within tolerance (default 1e-9)

This handles common formatting variations while catching actual errors.

## Integration with Workflow

The validation step is enforced by `workflow_guard.py`:

```
stress_test_run -> problem_validate -> problem_generate_tests
```

You cannot skip validation - it must pass before generating final tests.

## Best Practices

1. **Write samples early**: Include samples in README.md during problem creation
2. **Validate after stress test**: Solution is verified, so any mismatch is likely in the statement
3. **Keep samples simple**: Use small, easy-to-verify examples
4. **Include edge cases**: Add boundary samples that test limits
5. **Match format**: Ensure sample format matches actual input/output format
