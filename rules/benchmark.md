# Evaluate the harness by delivered outcomes

Use this rule when evaluating or changing loop-dev itself. Ordinary application
work does not need a benchmark experiment. See the source repository's
`docs/benchmark.md` and `benchmarks/suite.json` for the protocol and starter cases.

## Before execution

- Restore prior benchmark decisions. Fix the current harness snapshot,
  suite, concrete model IDs and explicit efforts for BOTH providers, judge
  provider/model/effort, environment, repetitions and call/time budgets. Use
  `scripts/benchmark.py` (`bench check`) for run → judge → gate → improvement report;
  `--dry-run` validates the full configuration without model calls.
  The CLI manages provider sessions and skill activation; no interactive conversation
  or baseline commit selection is required from the user.
- Use identical fixtures, requests, profiles and budgets for plain/candidate.
  Use a dedicated host profile without an automatically activated global harness.
  Preserve existing permissions, credentials and hooks; do not bypass nested-CLI limits.
- Keep outcome checks outside the agent workspace. Freeze the rubric, weights,
  critical criteria and gate thresholds before looking at candidate scores.
- Include realistic development, small edits, non-development and session reuse.
  Evaluate at least two distinct goal types; multiple cases of one type do not qualify.
  Session reuse is a scenario dimension, not another goal type. The starter suite
  covers development, document and explanation; its session case counts as explanation.
  Keep both calibration and holdout cases; public examples alone are not evidence of
  general superiority. Add representative private cases for release evaluation.
- Resolve missing user-owned choices before start. Technical setup and intermediate
  commands belong to the agent. Do not request procedural prompts from the user.

## Evaluate and report

- Run both conditions in fresh workspaces and conversations; resume the same provider
  session for later turns within a case. Keep knowledge separate between arms.
- Run external checks, then `bench judge` on blinded packets with fixed anchored
  criteria and repeated independent assessments. Inspect disagreement and evidence.
  Scores reward the requested result, never compliance theater, tool count, self-reported
  completion or long explanations. Short work must not acquire unnecessary loops.
- Preserve failed attempts. Missing CLI/auth/permissions, interrupted runs, missing
  ratings, invalid activation, altered evidence and simulations do not count as passes.
  Never claim superiority because a control could not execute.
- Compare each harness-assisted result with its plain counterpart. Report quality, extra user requests,
  elapsed time and known usage separately; unavailable billing remains unknown.
  Retain results per goal type and provider/model/effort. Every selected type must
  execute across all arms and repetitions. An aggregate gain cannot hide a type's loss.
- Run `bench gate` on the current source and suite before claiming a harness change
  is benchmark-validated or promoting it under that claim. Nonzero means no promotion.
  `non_regressing` is not `improved`; qualify every claim by suite/models/repetitions.
  Unit/fake-CLI success proves implementation behavior, not live model quality.

## Improve rules from evidence

1. Read the failing artifact, criterion and logs. Rule paths in a report identify
   investigation targets, not proven causes. Separate transport/setup failures from
   product defects, stale context, omitted work and unnecessary process overhead.
   Start with `improvements.md`; its JSON keeps all grouped findings. Each item must
   distinguish measured observations from hypotheses and specify a bounded next change
   and revalidation. Resolve missing evidence before attributing problems to rules.
2. Save one bounded hypothesis with case IDs, affected rule paths and expected
   observable change using `bench proposal`. Treat it as a hypothesis in knowledge.
3. Delegate only the identified change with exact scope and completion criteria.
   Never weaken acceptance, remove failing cases or put case-specific answers in rules.
4. Rerun both conditions under unchanged criteria, including all holdout cases.
   Repeated tuning against exposed holdouts requires fresh independently held cases
   before a broader claim. Keep rejected proposals and results as historical evidence.
5. Accept only with fresh passing evidence and the existing delivery authority.
   A changed source/suite invalidates the old gate. Do not auto-merge or auto-deploy.

Keep human summaries to verdict, affected cases, evidence and next bounded change.
Generate tables from structured results; do not write a second narrative execution log.
