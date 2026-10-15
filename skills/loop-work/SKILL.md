---
name: loop-work
description: Explicitly enable the goal-setting and work harness for the current Claude Code or Codex conversation. After invocation, route every input through it across goals, task types, directories and compaction until explicitly disabled. Ordinary task wording never activates it.
---

# Loop Work

Orchestrate goals, feedback, delegation, evidence checks and reporting. Delegate
investigation, planning, implementation and writing.
An assigned worker/reviewer executes only its assignment: never activate, orchestrate,
write task/knowledge state, ask users or delegate further.

## Activate and retain

Run:

```sh
python3 "SKILL_DIR/loop.py" harness on --provider PROVIDER
```

Use `claude` inside Claude Code, `codex` inside Codex. For a previously bound
conversation add `--session SESSION_ID`; put `--knowledge-home "KNOWLEDGE_HOME"`
before `harness` when bound.
Retain returned `entrypoint`, `rules_home`, `knowledge_home`, `session_id` and task
through compaction. Use absolute `rules_home` for references below. Never select a session by recency,
provider or cwd. An explicit `off` invocation follows the off procedure without
running `on`. A bare invocation enables; invent no task.

## Route every input

After each user input, worker result and compaction, read:

```sh
python3 "ENTRYPOINT" --knowledge-home "KNOWLEDGE_HOME" orchestration context SESSION_ID
```

Add `--task TASK_ID` when selected. Open only capsule evidence needed for the next decision. Reuse current references rather than reloading them.
Never replay full histories or write duplicate recovery summaries.

When enabled, follow [work](rules/work.md) for every request and control input. Keep
the same binding across goal completion, new goals, task types and directories;
pass `--session SESSION_ID` on every task. Select another target with `--root`/`--path`
and read its applicable instructions. A separate conversation starts inactive.

Load only the relevant rule:

- [routing](rules/routing.md): goal-linked units, explicit available model/effort profiles,
  least-cost eligibility and evidenced escalation.
- [delegation](rules/delegation.md): bounded assignments, candidate assessment before
  effects/peer delivery, whole-result rejection and stronger retries within budgets.
- [context](rules/context.md): current knowledge, scoped decisions, source dependencies,
  versioned user feedback and recovery.
- [writing](rules/writing.md): compact evidence-backed records.
- [workflow](rules/workflow.md): development tests, opposite-provider review and repair.
  Other work uses one pass plus completion checks.
- [benchmark](rules/benchmark.md): harness changes/evaluation only.

## Disable

Explicit off runs `harness off SESSION_ID` with the retained binding
and stops automatic routing. Preserve tasks, knowledge, files and budgets. Reactivation reuses that session. Task pause/stop keeps activation; progress questions
and reactivation never resume paused work. Use [entry rules](rules/entry.md) for pause/binding recovery.

If the entrypoint is unavailable, stop routing and report it; never fall back
or silently reactivate. These are host instructions, not hooks.
Honor user recording restrictions and provider permissions.
