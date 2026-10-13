---
name: loop-dev
description: Explicitly enable the portable goal-setting and work harness for the current Claude Code or Codex conversation. Once invoked, use it for every subsequent input, across goals, task types and directories, until the user explicitly turns it off. Use only on explicit invocation; ordinary task wording does not activate it.
---

# Loop Dev

Enable the harness for this conversation, not just the accompanying goal. The
conversation orchestrates; tools perform explicit bounded assignments. Do not give
the user an operating checklist or require another skill prefix on later requests.

## Activate once

1. If this is an assigned worker/reviewer call, execute only its assignment. Never
   activate a session, start a nested loop or write orchestration state.
2. Locate the harness relative to this skill: installed project skills live at
   `.agents/skills/loop-dev/` or `.claude/skills/loop-dev/`; the project entrypoint is
   `.loop/loop.py`. In the source repository, `skills/loop-dev/` uses `scripts/loop.py`.
   Retain the entrypoint's absolute path; do not resolve it again from a later cwd.
3. Read the adjacent harness `rules/work.md`, `rules/delegation.md` and
   `rules/writing.md` (`.loop/rules/` in an installed project).
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

- Before routing each input, run `python3 ENTRYPOINT --knowledge-home KNOWLEDGE_HOME
  harness status SESSION_ID`. When enabled, follow `work.md` for **all** inputs:
  development, new unrelated goals, documents, explanations, research, planning,
  progress questions, corrections, acknowledgments and stop requests.
- Goal completion never disables the harness. Changing task type or directory never
  disables it. Ordinary follow-ups need no skill invocation or activation prompt.
- Reuse the bound session with `--session SESSION_ID` on every new task/goal, including
  the first one after activation. Use the absolute entrypoint and `--root PATH` or
  `--path PATH` for another project; read that project's applicable rules. Keep the
  same knowledge home. Do not require another installation merely to stay enabled.
- Restore the same binding after compaction or an explicitly resumed conversation.
  Status and knowledge context expose activation independently of task completion.
  A separate conversation starts inactive; old knowledge is not activation consent.
  If a binding is lost, recover it from this conversation's identified task/goal,
  never the latest session. If no ownership evidence survives, report the missing
  binding rather than guessing or silently creating a replacement.
- Set the goal from the desired outcome. Reuse relevant current knowledge and prior
  answers; resolve only missing human-owned decisions before work starts. Delegate
  analysis, planning, implementation and writing with explicit read/write scope,
  prohibitions, deliverables and criteria. During work, continue without questions.
- Development uses `workflow.md`: implementation, real tests, opposite-provider
  review and bounded repair. Documents/explanations/research/planning use one pass
  plus completion checks. Status, acknowledgments and steering update the current
  context without inventing a new goal or a recurring development loop.
- Save useful evidence, scoped decisions and results; inspect changed dependencies
  before refreshing knowledge. Follow `writing.md`: compact facts, no repeated
  narrative logs. User restrictions, including no recording, take precedence.

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
