---
name: loop-dev
description: Explicitly enable the portable goal-setting and work harness for the current Claude Code or Codex conversation. Once invoked, use it for every subsequent input, across goals, task types and directories, until the user explicitly turns it off. Use only on explicit invocation; ordinary task wording does not activate it.
---

# Loop Dev

Enable the harness for this conversation, not just the accompanying goal. Keep the
main session to goals, user feedback, orchestration, evidence checks and reporting.
Delegate substantive investigation, planning, implementation and writing to isolated
workers. Do not give the user an operating checklist or require another skill prefix
on later requests.

## Activate once

1. If this is an assigned worker/reviewer call, execute only its assignment. Never
   activate a session, start a nested loop or write orchestration state.
2. Locate the harness relative to this skill: installed project skills live at
   `.agents/skills/loop-dev/` or `.claude/skills/loop-dev/`; the project entrypoint is
   `.loop/loop.py`. In the source repository, `skills/loop-dev/` uses `scripts/loop.py`.
   Retain the entrypoint's absolute path; do not resolve it again from a later cwd.
3. Read the adjacent harness `rules/work.md` once (`.loop/rules/` in an installed
   project). Read `rules/delegation.md` and `rules/routing.md` when assigning workers, `rules/context.md`
   for recovery/feedback/knowledge details, and `rules/writing.md` when saving records.
   Load `rules/workflow.md` for development. Reuse these instructions while current;
   do not reload every reference or the full execution history on every turn.
4. For explicit activation, run `python3 ENTRYPOINT harness on --provider PROVIDER`.
   Use `claude` inside Claude Code or `codex` inside Codex. If this conversation
   already has a bound session ID, add `--session SESSION_ID`; never create another
   session for a new goal, resumed activation or directory change. Use its same
   `--knowledge-home KNOWLEDGE_HOME` when one is already bound.
5. Retain the returned `session_id`, `knowledge_home` and absolute `entrypoint` in
   conversation state and compaction handoffs. The local session record is the
   activation authority. Never select a session by recency, provider or cwd.
6. If the invocation includes a request, handle it immediately. A bare invocation
   only enables the session: acknowledge briefly without inventing a task or asking
   an activation questionnaire. An explicit `off` argument instead follows Disable
   below; do not run `on` first.

## Handle every later input

- After every user input, worker result and compaction, run
  `python3 ENTRYPOINT --knowledge-home KNOWLEDGE_HOME orchestration context SESSION_ID`
  with `--task TASK_ID` when selected. Start with this at-most-16-KiB capsule, its
  control version and artifact references. When enabled, follow `work.md` for **all** inputs:
  development, new unrelated goals, documents, explanations, research, planning,
  progress questions, corrections, acknowledgments and stop requests.
- Goal completion never disables the harness. Changing task type or directory never
  disables it. Ordinary follow-ups need no skill invocation or activation prompt.
- Reuse the bound session with `--session SESSION_ID` on every new task/goal, including
  the first one after activation. Use the absolute entrypoint and `--root PATH` or
  `--path PATH` for another project; read that project's applicable rules. Keep the
  same knowledge home. Do not require another installation merely to stay enabled.
- Restore the same binding after compaction or an explicitly resumed conversation.
  The orchestration capsule exposes activation independently of task completion.
  A separate conversation starts inactive; old knowledge is not activation consent.
  If a binding is lost, recover it from this conversation's identified task/goal,
  never the latest session. If no ownership evidence survives, report the missing
  binding rather than guessing or silently creating a replacement.
- Set the goal from the desired outcome. Reuse relevant current knowledge and prior
  answers; resolve only missing human-owned decisions before work starts. Delegate
  analysis, planning, implementation and writing with explicit read/write scope,
  prohibitions, deliverables and criteria. During work, continue without questions.
- Discover the current PRD, plan, mockup, API and architecture inputs needed for that
  outcome through scoped knowledge and source investigation. Follow `context.md` to
  reconcile contradictions and refresh affected consumers across projects. Do not
  require repeated manual context, infer authority from stale artifacts or force all
  product-development stages into an unrelated request.
- Keep a small complete unit in one `delegate`; use `team` only for distinct, separately
  verifiable goal contributions with dependencies and exclusive write ownership (`work`
  subcommands for common tasks). Record each assignment's goal link and demand under
  `routing.md`. Freeze explicit available model/effort profiles before start; choose
  the least expensive eligible profile, with factual evidence for higher initial demand.
- Treat worker output as a staged candidate. Read goal/criterion evidence and call
  `assess` internally; do not ask the user to approve it. Accept before effects or peer
  delivery, or reject the whole result and rerun the same assignment/team with the
  next stronger allowed profile. Retain discarded evidence and shared budgets. Handle
  configuration, permission, timeout and changed-input blockers without escalation.
  Verify model-reported blocker claims against available inputs; reject disproven claims
  through `assess`, but preserve real blockers and never accept blocked work as done.
  Continue until done or honestly blocked; no additional user prompt is required.
- Keep each worker's role/scope fixed. Let the coordinator route accepted bounded peer
  questions, answers and findings. Workers cannot spawn, redelegate, ask users or change
  goals. Each call has an isolated CLI context; writes are serialized and budgets finite.
- Persist task steering with `orchestration feedback` using the capsule's current
  control version and the original user input reference; follow `context.md` for
  the exact fields. Send constraints/corrections to affected workers before applying
  their results. Record pause/resume explicitly. A frozen scope change blocks the
  old task and needs a new goal; peer messages never provide user authority.
- Development uses `workflow.md`: implementation, real tests, opposite-provider
  review and bounded repair. Documents/explanations/research/planning use one pass
  plus completion checks. Status, acknowledgments and steering update the current
  context without inventing a new goal or a recurring development loop.
- Read compact role results and artifact references. Open only the specific evidence
  needed for an orchestration decision; leave source analysis, full logs, messages
  and outputs in worker contexts and durable records. Never default to loading full
  `context.json`/history/transcripts or create duplicate handoff summaries.
- Save useful evidence, scoped decisions and results; delegate changed-dependency
  inspection before refreshing knowledge. Follow `writing.md`: compact facts, no
  repeated narrative logs. User restrictions, including no recording, take precedence.

## Disable only on request

An explicit request to turn the harness off, including `/loop-dev off` or
`$loop-dev off`, runs `harness off SESSION_ID` with the retained entrypoint and home.
Do not continue automatic task execution after that input. Preserve all goals,
knowledge, files and retry counts; disabling is not completion, cancellation or a
budget reset. No daemon is running to kill. An explicit later invocation re-enables
this same session. If already inactive and no session is bound, acknowledge without
creating one. A task pause/stop keeps the harness enabled; honor the task boundary
and handle the next input through the harness. Use the pause recipe in `work.md`;
reactivation or a progress question does not authorize resuming paused work.

These are host-agent instructions plus durable local state, not a vendor hook that
intercepts messages. Never bypass provider permissions or claim a model obeyed this
routing without observing it.
