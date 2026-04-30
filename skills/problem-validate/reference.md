# Problem Validate Reference

Detailed reference for the `problem_validate` skill.

## Overview

`problem_validate` verifies:

1. statement samples in `README.md`;
2. sample files in `tests/` (for example `01.in` + `01.ans`).

Recommended statement layout for `statements/README.md`:

1. title
2. time/memory limits
3. optional background
4. problem description
5. input format (include all variable ranges and aggregate constraints)
6. output format
7. numbered samples (ascending)
8. explanation (put sample explanations here, not next to sample blocks)

## Validation Types

- `statement_samples`: validate embedded statement examples.
- `sample_files`: validate sample file pairs.

## Common Usage

### Basic

```json
{
  "problem_dir": "problems/emergency-escape"
}
```

### Explicit statement samples

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

### Validate only one type

```json
{
  "problem_dir": "problems/emergency-escape",
  "validate_types": ["statement_samples"]
}
```

```json
{
  "problem_dir": "problems/emergency-escape",
  "validate_types": ["sample_files"]
}
```

## Output Interpretation

### Success pattern

```json
{
  "success": true,
  "data": {
    "statement_samples": {"validated": true, "passed": 2, "failed": 0, "total": 2},
    "sample_files": {"validated": true, "passed": 3, "failed": 0, "total": 3}
  }
}
```

### Failure pattern

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
      "details": [{"index": 1, "expected": "5", "actual": "2", "passed": false}]
    }
  }
}
```

## Error Recovery

### Statement sample mismatch

1. Check `actual` output from solution run.
2. Confirm whether `actual` is correct.
3. If yes, fix statement expected output.
4. If no, fix solution and rebuild.

### Sample file mismatch

1. Confirm answer file exists.
2. Compare `actual` vs `expected`.
3. Update answer file if solution is correct.
4. Otherwise fix solution.

## Comparison Rules

The tool supports:

1. exact match;
2. whitespace-insensitive line comparison;
3. token-based comparison;
4. floating-point tolerance comparison (default `1e-9`).

## Workflow Integration

`stress_test_run -> problem_validate -> problem_generate_tests`

Validation must pass before final test generation.

## Validator EOF Notes

For testlib-based validators:

1. end validator with `inf.readEof()`;
2. if trailing whitespace should be tolerated, use `inf.seekEof(); inf.readEof();`;
3. do not stop at `seekEof()` only.
