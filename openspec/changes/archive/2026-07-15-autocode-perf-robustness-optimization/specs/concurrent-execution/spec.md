## ADDED Requirements

### Requirement: Bounded concurrent test generation
The system SHALL run the candidate generation, validation, and solution steps of `problem_generate_tests` with a bounded level of concurrency instead of strictly serially.

#### Scenario: Default concurrency
- **WHEN** `problem_generate_tests` runs with N candidates under default settings
- **THEN** gen/validator/sol subprocesses execute with at most the configured concurrency limit in flight, and produce outputs identical to the serial implementation

#### Scenario: Result correctness preserved
- **WHEN** a candidate fails validation or times out
- **THEN** it is rejected with the same status and reason as the serial implementation

### Requirement: Bounded concurrent test verification
The system SHALL verify multiple `.in` files' answer consistency, validator, and wrong-solution-kill checks with bounded concurrency.

#### Scenario: Concurrent verification
- **WHEN** `problem_verify_tests` processes M test files
- **THEN** each file's sol/checker runs are bounded-concurrent and the final verification result equals the serial result

### Requirement: Bounded concurrent stress testing
The system SHALL run `stress_test_run` trial rounds with bounded concurrency while preserving per-round ordering (gen → validator → sol → brute → checker).

#### Scenario: Concurrent rounds
- **WHEN** `stress_test_run` runs T trials
- **THEN** rounds execute with bounded concurrency and mismatch detection behaves identically to serial execution

### Requirement: Configurable concurrency limit
The system SHALL use a concurrency limit that defaults to a safe value (proposed 4) and MUST NOT exceed available memory; exceeding is capped gracefully.

#### Scenario: Limit respected
- **WHEN** the concurrency limit is set to K
- **THEN** no more than K subprocess groups are in flight at any time
