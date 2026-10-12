# Shared development loop

Enter through `work.md` to select the task type. Apply `writing.md` to status,
analysis and results; keep detailed evidence in linked records.

This is the single workflow for Claude Code and Codex. The conversation that received
the goal is the implementer and orchestrator. The other CLI reviews. The user must
not have to feed source locations, missing dependencies, error explanations, or next
steps one prompt at a time. Run the commands below yourself; do not hand the user a
manual checklist to operate the loop.

## Automatic entry: the goal is enough

Use this workflow by default when the user requests a project feature, change,
improvement or fix. An ordinary request such as "로그인 기능을 완성해 줘" is enough.
Do not require the user to mention loop-dev, use a slash command, describe the
development procedure, say "investigate it yourself", or request tests/reviews.
Do not ask whether to use the loop. Those actions are standing responsibilities
of the harness, not additional user requirements or activation phrases.

| User intent | Agent action |
| --- | --- |
| Build, implement, improve, change or fix project behavior | Enter preflight automatically, then execute the full loop |
| Follow up on an existing goal or say "continue" | Restore its context and continue the appropriate phase |
| Ask a question, request explanation, or ask for read-only review | Answer/review within scope; do not create a development goal |
| Explicitly request planning only, a pause, or no code changes | Honor that boundary; do not start implementation |

When there is an existing goal, inspect `.loop/state/active.json` and the goal's
persisted context before creating anything. If there is no active goal, reuse the
matching draft/ready goal from the conversation and stored state when identifiable.
Otherwise choose a meaningful safe goal ID yourself. Do not ask the user to supply
IDs, JSON, file locations or script commands merely to operate the harness.

Keep the same local `session_id` throughout this conversation, including after a goal
completes. Before investigating each follow-up or asking anything, load `context`
for the current goal or `session context SESSION_ID`. Read `reusable`, `stale`, and
the earlier decisions in `goal_history`. Reuse unchanged facts/answers; investigate
only missing topics and affected dependencies. Follow `context.md` to save updates.
The last goal's state stores the session ID for recovery after context compaction.
Never guess session ownership from the newest file or a provider-wide global pointer.

A broad desired outcome starts discovery and preflight, not speculative code edits.
Derive the missing technical context and acceptance criteria. Ask only necessary
human-owned decisions before start, following the policy below. Do not expand the
product scope beyond the user's goal or wait for repeated "next" instructions.

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

For a new ordinary development request, run this command yourself:

```sh
python3 .loop/loop.py init goal-id --title "Desired result" --implementer codex
```

Use `claude` when working inside Claude Code. Read the returned context file.
For a continuing goal, run `context goal-id` instead of initializing again.
The first `init` creates a local session and returns its ID. For a **new goal in this
same conversation**, pass `--session SESSION_ID` to `init`; do not create a new session
for each request. If analysis starts before any goal, `session open --provider codex`
creates the conversation's session. Keep its ID in conversation handoffs. The agent
performs these actions; the user only supplies goals and unresolved human decisions.

1. Define observable target behavior, users, scope, exclusions, requirements and
   acceptance scenarios before judging the current implementation.
2. Reuse applicable current session analysis first. Inspect the inventory, rules,
   source, callers, tests, data and interfaces for missing or stale topics. Record
   concise summaries and dependencies with `session remember`. Run bounded, read-only
   discovery commands as needed. Inspect official documentation for uncertain integrations.
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
Check prior answers and their original scope/basis first. Do not repeat an answered
question because a new prompt or goal began. Resolve technical cache staleness
yourself; a changed file alone is not a reason to ask the user again. Carry applicable
answers into the new contract with their provenance. A goal-specific answer is not
new session-wide authorization; a changed product boundary may need a new decision.
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

1. Run `context goal-id` and read its file, especially `session.reusable`,
   `session.stale`, `next`, `latest_gate`, notes and unfinished acceptance.
   This reconstructs the next prompt automatically without a new repository-wide analysis.
2. Investigate technical gaps yourself using saved end-to-end paths and dependencies.
   After edits, refresh context and update affected analysis topics from the actual
   changes. Do not simply restamp old summaries or re-investigate unaffected topics.
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
  Recover `session_id` too and use it for later goals in this same conversation.
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
