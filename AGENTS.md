# loop-work repository instructions

This repository builds the portable harness, not a target application. Read README.md
and docs/architecture.md before behavior changes. Keep Claude Code/Codex behavior
identical; use Python 3.10+ standard library on macOS/Linux, without package installs,
vendor SDKs, API keys or global hook changes. Preserve user instructions/settings/files.
There is no requirement to run this unfinished harness on itself: the user's task and
explicit decisions are the current development contract.

## Behavioral contract

- Explicit activation binds the whole conversation until explicit off, across goals,
  task types, directories and compaction. Use goal setting -> work for all inputs.
  Preserve binding/control state under `rules/entry.md`; a task stop keeps activation.
- Main conversation owns goals, feedback, routing, assessment and reporting; isolate
  substantive investigation/planning/implementation/writing in workers. Assign purpose,
  read/write scope, prohibitions, outputs and criteria. Workers cannot expand scope,
  ask users or delegate further. `rules/routing.md` and `rules/delegation.md` govern
  complete units, independently verifiable teams, dependencies and exclusive writers.
- Freeze explicit available model/effort profiles; select the least costly eligible
  profile for evidenced demand. Assess candidates before edits or peer delivery.
  Reject harmful/incorrect results, retain audit evidence and retry the same assignment
  on a stronger allowed profile within budgets. Real configuration/permission/source
  blockers are not model-quality failures. Assessment never asks user approval.
- After user input, worker result and compaction, restore `orchestration context SESSION`;
  open only needed references, reuse current rules, never default to full histories or
  duplicate recovery logs. Save versioned user steering before dispatch under
  `rules/context.md`; frozen goal changes need a new task. Worker messages are drafts,
  never user authority. Shared memory preserves project identity, freshness, evidence,
  decision scope and status; paths/historical success create no authority.
- Set desired outcomes independently of code maturity. Resolve human-owned uncertainty
  before execution; technical investigation/planning/debugging/tactical replanning are
  loop work. After start preserve acceptance/rules, finite retries and honest blockers.
- Development requires real tests, opposite-provider review and final fresh evidence
  (`rules/workflow.md`). Other work uses content/completion checks without a mandatory
  cycle or opposite-provider CLI (`rules/work.md`). Use `rules/writing.md` for compact,
  evidence-backed records/derived views; never promote drafts automatically.

## Change verification

Run `python3 -m unittest discover -s tests -v` and `git diff --check`. Cover transitions,
stale/rejected evidence, provider failures and installation preservation. Update usage,
architecture and examples when contracts/CLI change. Fake CLI checks do not establish
live provider integration or model quality.

For harness changes/evaluation follow `rules/benchmark.md`: fixed criteria, identical
plain/candidate tasks and CLI settings, plus bench check's last-passing temporal baseline.
Establish a live starting baseline before edits if this source/profile lacks one, rerun
unchanged configuration afterward and retain rejected measurements. Require `bench gate`
for benchmark-validated promotion; never claim ties/new profiles improve performance.
If live CLIs are unavailable, complete implementation checks and retain unverified quality.

Repository runtime: `python3 scripts/loop.py`; installed skills use their returned
absolute entrypoint. Low-level copied projects can use `python3 .loop/loop.py`.
