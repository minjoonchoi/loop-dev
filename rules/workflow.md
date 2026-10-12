# Shared development loop

This is the single workflow for Claude Code and Codex. The conversation that received
the goal is the implementer and orchestrator. The other CLI reviews. The user must
not have to feed source locations, missing dependencies, error explanations, or next
steps one prompt at a time. Run the commands below yourself; do not hand the user a
manual checklist to operate the loop.

## Roles and precedence

- Follow the user's goal and applicable repository instructions. Inspect nested
  `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md` and project rules before editing.
- Read this file, `context.md`, and `quality.md`. Project-specific rules remain in
  their original files; reference them in the contract rather than duplicating them.
- A prompt explicitly assigning a read-only review is a reviewer task. Review only,
  return the requested JSON, and never start a new development loop.
- The implementer can be Claude or Codex. Do not invent the provider or switch it
  after freeze. Use the actual current conversation environment.
- Python 3.10+, Git, Claude Code and Codex CLIs on macOS/Linux are required. Use
  existing CLI authentication and permissions. Do not bypass permission systems.

## 1. Capture the target and discover context

When a user requests goal-driven development, run:

```sh
python3 .loop/loop.py init goal-id --title "Desired result" --implementer codex
```

Use `claude` when working inside Claude Code. Read the returned context file.
For a continuing goal, run `context goal-id` instead of initializing again.

1. Define observable target behavior, users, scope, exclusions, requirements and
   acceptance scenarios before judging the current implementation.
2. Inspect the repository inventory, manifests, applicable rules, actual source,
   callers, tests, data and external interfaces. Run bounded, read-only discovery
   commands as needed. Inspect official documentation for uncertain integrations.
3. Put findings and per-requirement gaps in `discovery.json`. Put desired behavior
   and acceptance in `contract.json`. Empty, partial, mock-only or broken code means
   more work. It is never a reason to silently downgrade the target to an MVP.
4. Decompose work into ordered vertical milestones. Every requirement maps to
   acceptance, every acceptance maps to executable checks and a milestone. Include
   existing regression checks and real integration/E2E checks where needed.
5. Fill the ten decision areas in `.loop/templates/contract.example.json`'s format.
   Record basis accurately: `user`, `repository-rule`, or `delegated`.

## 2. Resolve only human-owned uncertainty

Distinguish information you can discover from decisions only the user can make.
Resolve technical facts yourself. Recommend a default for implementation choices
and record any already-delegated discretion. Ask a consolidated, focused round
about materially unresolved product behavior, business rules, allowed external
actions, infrastructure constraints or data ownership. Ask follow-ups only if the
answers expose another blocking decision. Do not invent user answers or mark an
unknown value as approved. Explain `not applicable` with a reason.

Before implementation, settle scope, architecture, data/migrations, UI/API contract,
dependencies/credentials, security, verification, runtime/operations, delivery and
autonomous decision boundaries. The delivery answer states whether completion means
a verified working tree, commit, PR, merge or deployment. Perform external actions
only within the user's existing authorization; plan them before freeze.

Record the permission/setup needed to avoid interactive prompts during execution.
Pre-existing hooks may still run. Inspect their behavior; do not disable managed
controls. Missing credentials or required infrastructure must be resolved now.

## 3. Cross-review and freeze the executable contract

```sh
python3 .loop/loop.py validate goal-id
python3 .loop/loop.py plan-review goal-id
```

The live plan review invokes the opposite provider, which also checks that required
CLI flags/authentication work. Review must cover every acceptance criterion and
find no unresolved questions or findings. If it fails, read the artifact, investigate
and repair the plan. Only new human-owned choices return to the user, before start.

Once the goal is sufficiently specified under the user's authorization:

```sh
python3 .loop/loop.py freeze goal-id
python3 .loop/loop.py start goal-id
```

Do not add a redundant approval question. Freeze seals target, discovery, decisions,
rules and gate-engine hashes. Start refuses stale code/context. Do not implement
product code before start. Goal/discovery drafting is preflight work.

## 4. Execute the entire loop without further user questions

Repeat until the goal is complete:

1. Run `context goal-id` and read its file, especially `next`, `latest_gate`, notes
   and unfinished acceptance. This reconstructs the next prompt automatically.
2. Investigate technical gaps yourself. Trace the relevant end-to-end path, affected
   consumers, existing abstractions and dependencies. Refresh evidence after edits.
3. Implement the next milestone completely. Include integration, error handling,
   migration/compatibility and documentation required by the target. Do not leave a
   stub, fake success, disabled test or disconnected screen as finished work.
4. Record meaningful discoveries, decisions and adjusted implementation tactics:

   ```sh
   python3 .loop/loop.py note goal-id --kind discovery \
     --text "What was learned and what it changes" --evidence "src/path.py:42 and check-id"
   ```

   Use `decision` or `replan` for choices/tactical replanning within the frozen
   target. Notes do not authorize scope, test-command, rule or business-rule changes.
5. Run the next checkpoint. The script runs real commands, then a fresh opposite-
   provider review. Read the report; it supplies the next corrective work.

   ```sh
   python3 .loop/loop.py checkpoint goal-id --milestone M1
   ```

6. When a check/review fails, diagnose the cause, repair implementation, refresh
   context and retry within the fixed budget. Do not ask the user how to debug it.
   If `blocked` is retryable, repair the external/setup problem within existing
   authority, run `resume`, and continue. Never widen authority to force progress.
7. After all milestones pass, run `finish goal-id`. It runs **all** checks and a
   new full-target review on the current files, not a collection of stale passes.
   Fix final regressions and run finish again if needed.

No extra questions after start. Use frozen decisions and delegated technical
discretion. If an unforeseen issue genuinely requires a new product decision,
credential or forbidden action, record a blocker rather than inventing an answer,
waiting on a prompt, or claiming completion. Report what prevented completion and
the exact recovery condition. New requirements start a new preflight goal.

## 5. Completion, interruption and delivery

- Only `finish` can create `completed`. A reviewer saying "done", a commit, or green
  unit tests alone cannot. Check `status` and require current evidence before reporting.
- Honor the frozen delivery boundary. If a commit changes HEAD after a gate, gate
  evidence becomes stale; make intended commits before the final finish. Treat
  authorized post-verification publishing as delivery and report its actual result.
- For context loss, reopen the same provider and run `context goal-id`; continue
  from persisted milestones, findings and notes without asking the user to repeat them.
- For an interrupted gate, inspect logs, call `checkpoint`/`finish` to detect the
  interruption if necessary, then `resume`. Consumed attempts stay consumed.
- `block --reason` records an unavoidable obstacle. `abort --reason` preserves all
  files and evidence and releases the working tree for another goal. Neither is success.
- Give brief progress updates during execution. Finish with achieved behavior,
  verification evidence, delivery location and concrete remaining limitations.

## Trust boundary

Rules govern the conversation agent; CLI gates enforce recorded transitions and
evidence freshness. These are not an OS sandbox around the parent conversation and
cannot keep a hostile agent/user from editing state or bypassing the CLI. Use the
host tool's permissions for containment. Do not promise that every arbitrary goal
can always complete: denied permissions, unavailable services and finite budgets
must produce honest, recoverable failure.
