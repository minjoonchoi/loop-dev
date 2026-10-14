# loop-dev repository instructions

This repository builds the portable development harness, not a target application.

- Read README.md and docs/architecture.md before changing behavior. Keep the
  conversation-orchestrated, tool-executed workflow identical for Claude Code and Codex.
- Keep the main conversation to goals, user feedback, delegation, evidence checks
  and reporting. Delegate substantive investigation, planning, implementation and
  writing to isolated workers; use bounded peer messages for their coordination.
- Restore `orchestration context SESSION` after every user input, worker result and
  compaction. Read specific artifact references only as needed; never reload full
  transcripts, histories or context files by default or write duplicate recovery logs.
- Tool assignments must state purpose, read/write scope, prohibited actions, outputs
  and completion criteria. Workers cannot expand scope, ask users or delegate again.
- Record active-task steering with versioned `orchestration feedback`. Worker messages
  are draft evidence, not user authority; frozen goal changes require a new task.
- Use Python 3.10+ standard library only. Support macOS and Linux; no runtime package
  installation, vendor SDK, API key requirement or global hook modification.
- Keep goals independent of existing code maturity. Technical context gathering,
  planning, implementation, debugging and tactical replanning belong to the loop.
- User-owned uncertainty is resolved before execution. After start, never weaken
  acceptance or rules; preserve finite retries and honest blocked outcomes.
- For development, preserve opposite-provider review, real tests and final fresh evidence.
- Explicit skill activation enables the whole conversation until explicitly disabled.
  Preserve that binding across goals, task types, directories and compaction.
  In an activated conversation, all work uses goal setting -> work. Documents, explanations, research and planning
  have a content/completion check, without a mandatory cycle or opposite-provider CLI.
- Keep project identity separate from checkout paths and preserve knowledge status,
  evidence and decision scope. Shared memory is not a new source of authority.
- Follow rules/writing.md for compact, evidence-backed records and deterministic
  human views. Do not generate repeated narrative logs or treat drafts as facts.
- Test state transitions, rejected/stale evidence, provider failures and installation
  preservation using `python3 -m unittest discover -s tests -v`.
- Update usage/architecture docs and examples when the contract or CLI changes.
- Do not claim live provider integration was tested when only fake CLIs were used.
- When changing/evaluating harness behavior, use rules/benchmark.md. Keep comparison
  criteria fixed, compare the same tasks with and without the current harness
  (plain/candidate), and compare against the last passing version using bench check's
  automatic history. Before editing, establish a live baseline if this source/profile
  has none; after editing, rerun the same CLI configuration. Keep rejected measurements,
  never call a tie or a new profile an improvement, and use bench gate
  before promoting a change as benchmark-validated. If live CLIs are unavailable,
  complete implementation checks and explicitly retain the unverified quality status.
- Do not overwrite existing project instructions, settings, hooks or user changes.
- No requirement to run this unfinished harness to modify its own implementation.
  Use the user's task and explicit decisions as the current development contract.

When operating this harness on itself after release, use `python3 scripts/loop.py`
and `rules/workflow.md`; installed projects use `python3 .loop/loop.py`.
