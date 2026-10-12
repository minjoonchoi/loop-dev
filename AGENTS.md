# loop-dev repository instructions

This repository builds the portable development harness, not a target application.

- Read README.md and docs/architecture.md before changing behavior. Keep the
  conversation-led workflow identical for Claude Code and Codex.
- Use Python 3.10+ standard library only. Support macOS and Linux; no runtime package
  installation, vendor SDK, API key requirement or global hook modification.
- Keep goals independent of existing code maturity. Technical context gathering,
  planning, implementation, debugging and tactical replanning belong to the loop.
- User-owned uncertainty is resolved before execution. After start, never weaken
  acceptance or rules; preserve finite retries and honest blocked outcomes.
- Preserve opposite-provider review, real test execution and final fresh evidence.
- Test state transitions, rejected/stale evidence, provider failures and installation
  preservation using `python3 -m unittest discover -s tests -v`.
- Update usage/architecture docs and examples when the contract or CLI changes.
- Do not claim live provider integration was tested when only fake CLIs were used.
- Do not overwrite existing project instructions, settings, hooks or user changes.
- No requirement to run this unfinished harness to modify its own implementation.
  Use the user's task and explicit decisions as the current development contract.

When operating this harness on itself after release, use `python3 scripts/loop.py`
and `rules/workflow.md`; installed projects use `python3 .loop/loop.py`.
