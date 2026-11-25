## Purpose

Harden AutoCode's workflow state machine, validator readiness verification, communication problem packaging, and default quality-gate evaluation so post-generator dynamic probes actually run without destroying prior gates, vacuous validators cannot pass, communication packages without `.ans` export cleanly, and declared wrong solutions trigger mandatory kill checks unless explicitly opted out.

## Requirements

### Requirement: Post-generator non-destructive dynamic verification
The system SHALL preserve the pre-generator gate progression (`solution_analyze -> solution_audit_std -> solution_audit_brute -> validator_build -> generator_build`) and SHALL track `std_empirical_verified`, `std_empirical_hash`, `brute_probed`, and `brute_probed_hash` in workflow state. When `solution_analyze`, `solution_audit_std`, or `solution_audit_brute` runs after `generator_built == True` with an unchanged canonical `solutions/sol.cpp` or `solutions/brute.cpp` SHA-256 digest, the system SHALL execute dynamic verification and update `std_empirical_verified` / `brute_probed` without resetting `std_audited`, `brute_audited`, `validator_ready`, `generator_built`, `checker_ready`, `interactor_ready`, or `stress_passed`, while failing closed if a dynamic probe fails. Both `solution_audit_std` / `solution_audit_brute` and workspace inspection SHALL clearly distinguish whether dynamic probes ran versus `pending_binaries`, and post-generator `solution_audit_brute` SHALL set `brute_probed = True` only when `_probe_brute_small_scale` completes with `status in ("ok", "timeout_circuit_breaker")`.

#### Scenario: Post-generator dynamic audit runs non-destructively and records probe hashes
- **WHEN** `generator_built` is `True` and `solution_analyze`, `solution_audit_std`, or `solution_audit_brute` is invoked with unchanged `solutions/sol.cpp` / `solutions/brute.cpp` SHA-256
- **THEN** empirical complexity verification and `_probe_brute_small_scale` run against the compiled generator, `std_empirical_verified` / `brute_probed` and their hashes are recorded on passing probes (`ok` or `timeout_circuit_breaker` for brute), and `std_audited`, `brute_audited`, `validator_ready`, `generator_built`, `checker_ready`, `interactor_ready`, and `stress_passed` remain intact

#### Scenario: Failing post-generator dynamic probe fails closed
- **WHEN** post-generator `solution_analyze`, `solution_audit_std`, or `solution_audit_brute` observes a failing empirical fit (`passed=False`), brute crash, or minimum-scale timeout
- **THEN** `std_empirical_verified` / `brute_probed` is cleared to `False`, the corresponding audit gate is cleared, and downstream stress/verification state is invalidated

### Requirement: Anti-vacuous validator readiness enforcement
The system SHALL track `positive_count` (`expected_valid is True`), `negative_count` (`expected_valid is False`), `has_positive_cases`, and `has_negative_cases` in `validator_build`, emit an actionable warning and message whenever either positive or negative cases are absent, and require `accuracy >= 0.9`, `has_positive_cases == True`, and `has_negative_cases == True` before setting `validator_ready = True` in workflow enforcement.

#### Scenario: Permissive return-0 validator with positive-only test suite is blocked
- **WHEN** `validator_build` runs on a vacuous `return 0;` validator with only `expected_valid=True` cases and obtains `accuracy == 1.0`
- **THEN** `has_positive_cases` is `True`, `has_negative_cases` is `False`, an actionable warning is returned, and workflow enforcement sets `validator_ready = False`

### Requirement: Communication package packing without answer files and broadened wrong-solution scan
The system SHALL exempt `communication` problems alongside `interactive` problems from requiring `.ans` answer files during `problem_pack_polygon` and multi-OJ exports (`polygon`, `hydro`, `qoj`), and SHALL warn on undeclared wrong solutions matching `wrong_*.cpp`, `wrong-*.cpp`, `wa_*.cpp`, `wa-*.cpp`, `tle_*.cpp`, `tle-*.cpp`, `mle_*.cpp`, `mle-*.cpp`, `re_*.cpp`, `re-*.cpp`, and `bf.cpp` in `solutions/`.

#### Scenario: Communication package with missing answer files and dash-named wrong solution packs with warning
- **WHEN** `problem_pack_polygon` packages a `communication` problem whose `tests/*.in` files lack `.ans` files and whose `solutions/` contains an undeclared `wrong-greedy.cpp` or `bf.cpp`
- **THEN** packing succeeds without failing on missing `.ans` files and `pack_warnings` includes the undeclared wrong solution path

### Requirement: Smart default wrong-solution-kill gate without schema mutation
The system SHALL treat `require_wrong_solution_kill` as effectively enabled (`True`) in `check_gates`, `PRE_GATES` preflight evaluation, and workspace gate inspection whenever `manifest.solutions` contains at least one entry with `role == "wrong"` and `"require_wrong_solution_kill"` was not explicitly set in `manifest.quality_gates.model_fields_set`, while honoring explicit `False` opt-outs and preserving `workflow/models.py` unchanged.

#### Scenario: Manifest with wrong solution and omitted gate enforces wrong_solution_kill while explicit False opts out
- **WHEN** a manifest declares a `role == "wrong"` solution and omits `quality_gates.require_wrong_solution_kill`
- **THEN** `check_gates` and `problem_pack_polygon` preflight enforce `wrong_solution_kill`, whereas setting `"require_wrong_solution_kill": false` explicitly opts out
