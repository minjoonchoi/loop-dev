# Common entry: goal setting -> work

This is the shared entry for Claude Code and Codex. The main session owns goals,
user feedback, routing, bounded assignments, evidence checks and reporting. Isolated
workers perform substantive investigation, planning, implementation and writing.
The user supplies the requested outcome and only human-owned decisions that cannot
be established from existing context.
Apply `writing.md` to status, knowledge and deliverables throughout both stages.

## Session activation comes first

The user explicitly invokes the loop-dev skill once (`/loop-dev` in Claude Code,
`$loop-dev` in Codex). It runs `harness on` and binds the conversation's session ID,
absolute entrypoint and knowledge home. Without that activation, ordinary wording
must not start the harness. Existing low-level CLI operations remain available.

After EVERY user input, worker result and compaction, read
`orchestration context SESSION_ID [--task TASK_ID]` with the retained binding.
If enabled, use this entry for all input, including new unrelated goals and questions.
Completing a goal, changing directories or task types, and compaction do not turn it
off. Keep the same ID and knowledge home; use the retained absolute entrypoint with
`--root PATH`/`--path PATH` after moving. Preserve this binding in compaction handoffs.
A separate conversation starts inactive; never infer its binding from recent records.

Only an explicit request to turn the harness off runs `harness off SESSION_ID` and
ends automatic routing/execution. It preserves tasks, knowledge, files and budgets.
A task pause/stop keeps activation; record its boundary and wait for further intent.
Reinvoking the skill in the same conversation runs `harness on --session SESSION_ID`.
Do not reactivate merely because a later ordinary request sounds like development.
Workers/reviewers do not activate, toggle or recursively enter the harness.

Commands below use `.loop/loop.py` as shorthand while at the installed root. After
moving, use `python3 ENTRYPOINT --knowledge-home KNOWLEDGE_HOME --root NEW_ROOT ...`
with the retained absolute entrypoint; do not look for `.loop` in the new directory.

For a user-requested task pause, stop dispatching and save `pause` feedback with the
original input reference and current control version, following `context.md`.
Draft/ready development goals keep their phase; do not start them. Status questions
and skill reactivation never resume a paused task. Only the user's later request to
continue permits `resume` feedback. Feedback controls dispatch, not task/goal phases:
use existing `work block`/`block` and `work resume`/`resume` when phase recovery is
needed, preserving blockers and budgets. Resume feedback does not clear a phase blocker.
If a worker/gate is already in flight, use host cancellation when available;
otherwise the synchronous call may finish before the stop can be handled. Inspect
its logs and any applied paths, preserve actual changes and consumed attempts,
then record the pause. Do not dispatch further calls, manually apply pending edits,
silently roll back files, or report unobserved cancellation. Explicit abandonment
uses `work abort`/`abort`; a pause is resumable and does not release a running dev goal.

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
| Pause/stop the task | record the state and honor the boundary; keep harness activation |
| Turn the harness off | disable this session; preserve unfinished work without continuing it |
| Constraint or correction | persist scoped, versioned user feedback before the next dispatch |
| Change the frozen goal/scope | record scope-change feedback; block old execution and create a new goal |
| Acknowledgment | do not invent a new task or feedback decision |

Explicit user limits take precedence over inferred intent. Do not treat the presence
of a code path or the word "fix" in an explanatory question as a development request.
Classify an HTML mockup by its requested purpose: a planning/review artifact can be
document work; implementing executable behavior requires development gates. A mixed
planning-and-implementation goal does not lose those checks because it includes documents.
This is semantic routing by the conversation model, not a substring parser or host hook.
Progress questions and steering during active work belong to the existing task;
answer them and continue its authorized scope without opening duplicate explanation tasks.
An explicitly assigned worker or independent reviewer is exempt from orchestration:
execute only the supplied assignment and return its schema. Do not write task/knowledge
state, expand scope, ask users, delegate further or invoke the entry loop.

The conversation orchestrates. Follow `routing.md` to define goal-linked units and
select economical explicit profiles, then `delegation.md` to execute and assess them.
Use one assignment for a small complete unit; split a team only into distinct,
independently verifiable contributions with dependencies and one writer per file. Team
workers exchange accepted, bounded messages through the coordinator; do not relay
their full conversation through the main session. Start from the orchestration capsule,
select current knowledge and inspect compact results before the next handoff. Open
specific artifacts only as needed; do not load full histories or write duplicate recaps.
When the task evaluates or changes the harness itself, also follow `benchmark.md`.
Benchmark-only analysis is research/review; implementing a rule/runtime change is
development. Do not impose a benchmark on ordinary project work.

## 1. Goal setting

1. Restore the same conversation's session ID and unfinished task when relevant. For
   a new task, choose its kind and working directory/related project directories.
2. Use the capsule's references to find relevant knowledge. When needed, read
   `knowledge context --path /project` (repeat `--path` for related projects).
   Check applicable `reusable` facts and earlier answers before new
   investigation. `stale` items need only relevant dependencies checked. Drafts,
   hypotheses, unrelated projects and historical results are not current facts. The
   view has counts and pages; use `--query`/`--offset` before concluding a topic is absent.
3. Establish outcome, deliverable, scope and completion criteria. Delegate unresolved
   technical questions. Ask only materially unresolved human-owned decisions. Preserve
   an earlier answer's basis and applies_to boundary, including one-time permissions.
   Follow `context.md` to discover the relevant requirements, PRD, plans, mockups, API
   and architecture inputs across known project locations. Reconcile missing/stale or
   conflicting artifacts before execution; do not require the user to paste context
   that scoped investigation can recover or force unrelated workflow stages.
4. If the request and existing knowledge are sufficient, define the goal internally
   and start immediately. Goal setting is not a mandatory questionnaire or approval.
5. Configure the available model/effort pool in `execution.routing` before sealing
   the goal; an optional `LOOP_ROUTING_PROFILE` imports it during goal setting. Use
   existing authorized choices and verify provider support. Missing configuration
   blocks dispatch; no implicit CLI setting or later environment fallback is used.

For development, run `init ... --implementer WORKER --orchestrator CONVERSATION --session SESSION` and
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

Pass the ID returned by skill activation on EVERY task, including the first one.
Keep it across directories and tasks. Use `claude` inside Claude Code. The JSON structure is
in `work-goal.example.json`; the agent creates it in the knowledge directory or an
ignored temporary location. Do not ask users for IDs, JSON, or procedural prompts.
No Git repository or opposite-provider CLI is needed for these non-development tasks.
`--provider` binds the conversation; optional `--worker claude|codex` selects the worker
(default: conversation provider). Its CLI must be installed/authenticated. Before start,
use read-only analysis/planning assignments to fill factual gaps.

## 2. Work

Development repeats implementation -> actual checks -> independent opposite-provider
review -> repair under its frozen target and finite budgets. No questions after start.

Documents, explanations, research, review and planning delegate the requested work
with `work delegate` or `work team` as specified in `delegation.md`. These stage a
candidate; inspect its goal/criterion evidence and call `work assess` internally.
Acceptance releases its effects. Rejection discards the whole candidate and the next
same-ID dispatch selects a stronger allowed profile within existing budgets. Continue
the same team until its accepted work is complete, then finish. This adds no user
approval and no mandatory second model judge. There is no prescribed iteration count,
cross-review or requirement to invoke the other provider. Correct actual errors;
do not manufacture a recurring loop for successful small work.
No extra questions during work: use the defined decisions or record an actual blocker.
User feedback received during execution remains part of that task. Record constraints,
corrections and pause/resume controls through `orchestration feedback` before the next
dispatch; `context.md` defines the version and scope checks. A changed frozen goal
requires scope-change feedback and a new task, never a weaker acceptance condition.

Persist newly supported facts/decisions while working with `knowledge remember`,
using `knowledge.example.json`. Read the relevant detailed context before analysis
and pass its `revision` as `--observed`. After source edits, delegate inspection of
affected dependencies and update
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
Use the orchestration capsule for recovery, then targeted `work context` or artifact
references for details. Use `work note`, `work block --reason`, `work resume`, or `work abort
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
