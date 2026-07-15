## ADDED Requirements

### Requirement: Cleanup reclaims residual processes by default
`problem_cleanup_processes` SHALL, by default, inspect and terminate residual generator/compiler processes instead of returning success without action.

#### Scenario: Default cleanup
- **WHEN** `problem_cleanup_processes` runs with default settings and residual PIDs exist
- **THEN** it terminates those processes and reports them in the result

### Requirement: Liveness-checked termination
The system SHALL verify a PID is still alive (via `psutil`) before attempting to kill it.

#### Scenario: Stale PID
- **WHEN** a recorded PID has already exited
- **THEN** cleanup skips it without error

### Requirement: Whole process-tree reclamation
On POSIX systems the system SHALL terminate the entire child process tree (process group) of a generator, not only the top PID.

#### Scenario: Child leakage
- **WHEN** a generator spawned child processes
- **THEN** cleanup kills the group so no child survives

### Requirement: Cancel path terminates outstanding work
When `problem_generate_tests` is cancelled, the system SHALL actively terminate outstanding generator/compiler processes, and resume SHALL filter already-exited PIDs.

#### Scenario: Cancellation
- **WHEN** generation is cancelled
- **THEN** no generator/compiler subprocess survives after the call returns
