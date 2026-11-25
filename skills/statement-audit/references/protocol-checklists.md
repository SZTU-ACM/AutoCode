# Interactive & Communication Statement Checklists

Expanded protocol verification checklists for `statement-audit`.

## Interactive Statement Checklist (`interactive: true`)

Require every item below before issuing `decision: go`:

- `Input format` states that the contestant does not receive ordinary static input, and specifies the interactor's hidden input bounds, randomness, and adaptiveness.
- `Output format` lists every contestant command, including final answer syntax and whether the contestant must exit immediately after outputting the final answer.
- `Interaction protocol` defines the exact read/write turn order for every round and the meaning of every judge response.
- Query/round limits are explicit integers, specifying behavior both at the limit and when exceeded.
- The stdout flush rule is explicit: after each query or final answer, the contestant must flush stdout (e.g., `std::cout << ... << std::flush` or `std::endl`).
- Illegal behavior verdicts are explicit for: malformed command, invalid token, out-of-range argument, too many queries, early termination (`premature EOF`), continuing after final answer, and blocked protocol.
- Transcript examples clearly distinguish judge lines vs contestant lines and match the protocol.
- Adaptive judges state the maintained invariant explicitly.

## Communication Statement Checklist (`problem_type: "communication"`)

Require every item below before issuing `decision: go`:

- `## 通信协议` section is present and declares the exact communication mode (`run_twice` / `grader_linked` / `multi_process`).
- Process isolation rules (forbidding cross-pass globals, temp files, or shared state) or exact `problem.h` C++ function signatures are unambiguous.
- Numeric communication budgets (`max_bits`, `max_bytes`, `max_rounds`, `max_queries`) and over-budget verdict/scoring behavior are stated explicitly.
- Channel transformation / noise / adversarial corruption model is defined (or explicitly stated as noiseless).
- Invalid output format, premature EOF, extra output, and multi-stage verdicts are defined.
- Sample section provides a complete multi-stage transcript (e.g., `Pass 1 -> Channel -> Pass 2`) consistent with `communicator.cpp` / `grader.cpp` and `sol.cpp`.
- If `subtasks` exist in `manifest.json`, subtask scores, constraints, and dependencies match the statement.
