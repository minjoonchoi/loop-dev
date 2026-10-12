# Common entry: goal setting -> work

This is the shared entry for Claude Code and Codex. The agent owns routing, context
retrieval, internal commands, recording and updates. The user supplies the requested
outcome and only human-owned decisions that cannot be established from existing context.
Apply `writing.md` to status, knowledge and deliverables throughout both stages.

## Interpret the request before selecting an executor

| Intent | Kind and action |
| --- | --- |
| Build/change/fix executable project behavior | development; use workflow.md and its full gates |
| Write/edit a document | document; produce it and check content/deliverable criteria |
| Ask how/why something works or ask for an explanation | explanation; inspect relevant knowledge/source and answer |
| Investigate/compare/analyze | research; gather evidence and report conclusions/uncertainty |
| Ask for a read-only review | review; inspect and report without implementing fixes |
| Ask for a plan only | planning; produce the plan without implementing it |
| Continue the current task | restore its task/goal and session; do not duplicate it |
| Ask why work stopped | explain the actual status; continue only already-authorized unfinished work |
| Pause/stop | record the state and honor the boundary |

Explicit user limits take precedence over inferred intent. Do not treat the presence
of a code path or the word "fix" in an explanatory question as a development request.
This is semantic routing by the conversation model, not a substring parser or host hook.
Progress questions and steering during active work belong to the existing task;
answer them and continue its authorized scope without opening duplicate explanation tasks.
An independent reviewer invoked by the development engine is exempt from orchestration:
return its review only; do not write tasks, knowledge, or recursively invoke reviews.

## 1. Goal setting

1. Restore the same conversation's session ID and unfinished task when relevant. For
   a new task, choose its kind and working directory/related project directories.
2. Read `knowledge context --path /project` (repeat `--path` when the requested task
   spans projects). Check applicable `reusable` facts and earlier answers before new
   investigation. `stale` items need only relevant dependencies checked. Drafts,
   hypotheses, unrelated projects and historical results are not current facts. The
   view has counts and pages; use `--query`/`--offset` before concluding a topic is absent.
3. Establish outcome, deliverable, scope and completion criteria. Resolve technical
   questions yourself. Ask only materially unresolved human-owned decisions. Preserve
   an earlier answer's basis and applies_to boundary, including one-time permissions.
4. If the request and existing knowledge are sufficient, define the goal internally
   and start immediately. Goal setting is not a mandatory questionnaire or approval.

For development, run `init ... --implementer claude|codex [--session SESSION]` and
follow `workflow.md`. `work open --kind development` also creates a development goal.
It is mirrored into the common task history; only development gates can complete it.
For work spanning several code repositories, use a development goal per working tree
in the same conversation; inspect their shared knowledge and cross-project dependencies.
Do not claim the overall request complete until every required goal/integration is verified.

For other kinds, run the following internal actions yourself:

```sh
python3 .loop/loop.py work open --kind explanation --title "Requested explanation" \
  --provider codex --path /absolute/project --session SESSION_ID
python3 .loop/loop.py work goal WORK_ID --input /temporary/goal.json
python3 .loop/loop.py work start WORK_ID
```

Omit `--session` only for the first task of a new conversation. Keep the returned ID
across directories and tasks. Use `claude` inside Claude Code. The JSON structure is
in `work-goal.example.json`; the agent creates it in the knowledge directory or an
ignored temporary location. Do not ask users for IDs, JSON, or procedural prompts.
No Git repository or opposite-provider CLI is needed for these non-development tasks.

## 2. Work

Development repeats implementation -> actual checks -> independent opposite-provider
review -> repair under its frozen target and finite budgets. No questions after start.

Documents, explanations, research, review and planning perform the requested work,
check the output against its criteria, and finish. There is no prescribed iteration
count, mandatory cross-review, or requirement to invoke the other provider. Correct
an actual omission or error when found; do not manufacture a recurring loop.
No extra questions during work: use the defined decisions or record an actual blocker.

Persist newly supported facts/decisions while working with `knowledge remember`,
using `knowledge.example.json`. Read context before analysis and pass its `revision`
as `--observed`. After source edits, refresh, inspect affected dependencies and update
only the relevant summaries. Knowledge maintenance is ordinary agent work for every
kind, not a separate instruction the user needs to remember.

Finish non-development work with `work finish WORK_ID --input /temporary/result.json`,
using `work-result.example.json`. Record the actual output or answer reference, a
concise result and a check for every goal criterion. Inline explanations are legitimate
deliverables; save their useful substance in the result summary. New knowledge can be
included in the result with the current `observed_revision`. The result and knowledge
are committed atomically. The engine never promotes a draft/hypothesis automatically.
Content checks are the active agent's evidence-backed assessment, not independent
model verification; do not describe them as such.

Every task retains its goal, decisions, notes, result and unsuccessful attempts locally.
Use `work note`, `work context`, `work block --reason`, `work resume`, or `work abort
--reason` as necessary. Goal edits after start need a new task. Common `work finish`
cannot bypass development gates. A historical completed task is not a fresh pass for
new code or a new source of publishing/deployment permission.

## Knowledge scope and freshness

- The shared local store defaults to `~/.local/share/loop-dev/knowledge.sqlite3` on
  macOS/Linux. `LOOP_KB_HOME` or `--knowledge-home` can select another local directory.
  Keep it outside source trees. No remote upload or global provider setting is needed.
- `knowledge projects` lists known projects/checkouts. Git origin identity groups
  HTTPS/SSH checkouts of the same remote; different locations remain distinct.
  Local Git worktrees without an origin share their Git common-directory identity.
  Ordinary directories use local filesystem identity, so renaming them retains their
  project. For copies/moves across filesystems without a common remote, explicitly
  `knowledge attach --path PATH --project PROJECT_ID` from evidence; do not guess.
- `global` knowledge applies across projects; `project` applies to one project;
  `checkout` is specific to a working tree; `session`/`task` preserve narrower intent.
  Do not promote one-time answers to global preferences. Choose topic IDs that identify
  the project and subject; a topic's owner/scope cannot be silently changed.
- `confirmed` requires concrete evidence. `draft`, `hypothesis`, and `superseded` remain
  separate. A document draft is not a confirmed product decision; an explanation's
  speculation is not an implementation fact. If nothing new was learned, keep the
  task result without inventing knowledge entries.
- List the complete file/directory read set. A trailing `/` watches directory contents;
  `.` is a broad fallback. Rules and branches are checked for file-dependent knowledge.
  Record `expires_at` with a timezone for time-sensitive external facts. Freshness cannot
  be established for an external system merely because local source hashes match.
- Root/session paths are navigation, not identity or authority. Reattaching a moved
  directory updates its location without discarding task history. Do not choose a
  session by recency, combine different branch facts, or load unrelated facts as defaults.
- Search by `knowledge context --query TEXT`, inspect `knowledge history topic ID` or
  `knowledge history task ID`. Replacements retain earlier versions. Deletion of knowledge
  is not automatic; do not erase evidence to create a successful completion story.
- A normal read-only source explanation/review can write internal task memory, without
  modifying the inspected project. If the user forbids all writes/recording, honor that
  stronger boundary and answer without task/knowledge writes. Never record credentials.
