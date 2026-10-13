# Evidence-based quality gates

## Readiness

- Requirements describe user-visible results independently of current code.
- Acceptance specifies observable scenarios, including important failure paths.
- Commands exercise actual behavior. `echo`, unconditional success, empty test
  suites, mocks as the only integration evidence, and checking that a file merely
  exists are not sufficient proof of a working feature.
- All requirements have acceptance; all acceptance has commands and milestones.
- Decisions resolve conflicting instructions and clarify external dependencies,
  expected UX/API/data behavior, delivery and autonomous fallback boundaries.

## Each milestone

- Complete a vertical behavior, including integration and its error cases.
- Run its acceptance commands and all declared regression commands.
- Opposite-provider review inspects source and test strength as well as exit codes.
- All findings block progress, including minor findings; fix them instead of
  changing their severity or suppressing a test.
- Review output covers every requested acceptance ID exactly once and cites evidence.
- A failed/missing/malformed/denied/timed-out review never counts as a pass.

## Final

- Every milestone passed; every requirement remains in scope.
- Run all checks and a new full-target independent review on the current snapshot.
- Validate real wiring, persistence, error handling, compatibility and operational
  instructions wherever the goal requires them. No placeholder-based completion.
- Current target, decisions, rules and gate engine match frozen hashes.
- Test commands use ignored output directories and do not rewrite source inputs.
- Record any unavoidable external blocker as blocked, never as done.

The engine validates structure, execution results and freshness. The reviewer judges
semantic completeness and whether tests genuinely cover the target. Neither can
prove a bug-free program; reviewers must cite evidence instead of optimistic claims.

Changes to the harness itself also use `benchmark.md`: evaluate delivered outcomes
against plain execution and a fixed reference. Internal gate/test success alone
cannot prove that the harness improves users' results. Keep live quality validation
distinct from unit and simulated transport verification.
