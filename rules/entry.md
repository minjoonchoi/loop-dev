# Binding recovery and task controls

Use the activation/continuation/off contract in SKILL.md. Preserve its binding in
compaction handoffs; a distinct conversation never inherits activation from project
knowledge or old goals. Recover a lost binding only from this conversation's identified
task/goal. Without ownership evidence report the missing binding; never select by
recency/provider/cwd. If its entrypoint is unavailable, stop routing without fallback
or reactivation. An off request while already inactive/unbound acknowledges without
creating a session. No daemon or vendor hook intercepts messages.

## Pause and resume

Task pause/stop keeps activation. Stop dispatching and save `pause` feedback with the
original input reference/current control version using `context.md`. Keep draft/ready
phase; do not start it. Only the user's later continuation request permits `resume`
feedback. Status questions and skill reactivation do not resume paused work.

Feedback controls dispatch, not goal/task phase. Resolve a separate phase blocker
with `work resume`/`resume` only when its condition is resolved; resume feedback alone
does not clear it. Abandonment uses `work abort`/`abort`; a pause remains resumable and
does not release development working-tree ownership.

For in-flight work, use available host cancellation. A synchronous call may finish
before the host handles stop: inspect logs/applied paths, preserve actual edits and
consumed attempts, then record pause. Dispatch no further calls, apply no pending
candidates, silently roll back nothing and claim no unobserved cancellation.
