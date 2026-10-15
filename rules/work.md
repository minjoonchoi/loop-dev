# Goal setting -> work

Use this entry for every input in an explicitly activated conversation. The main
session manages goals, feedback, assignments, evidence checks and reporting; isolated
workers investigate, plan, implement and write. Keep the bound session/entrypoint/home
across goals and directories. Restore `orchestration context SESSION_ID` after each
input, worker result and compaction. `entry.md` owns activation, off, pause and binding
recovery; `context.md` owns targeted retrieval and feedback. Reuse current references.
Assigned workers/reviewers execute only their assignment: no entry loop, state writes,
user questions, scope expansion or further delegation.

## Command binding

In examples, set `ENTRYPOINT`, `KNOWLEDGE_HOME` and `RULES_HOME` from the retained
absolute `entrypoint`, `knowledge_home` and `rules_home`; set `PROJECT_ROOT` to the
selected absolute project path. Derive templates once:

```sh
TEMPLATES_HOME="$(dirname "$RULES_HOME")/templates"
```

Prefix commands with `python3 "$ENTRYPOINT" --knowledge-home "$KNOWLEDGE_HOME"`.
Development/session commands also need global `--root "$PROJECT_ROOT"` before the
subcommand; common work selects its location with `--path "$PROJECT_ROOT"` where
supported. Read examples from `"$TEMPLATES_HOME/FILE"`; store inputs at absolute
ignored/temporary paths. Never rely on cwd-local `.loop/loop.py` or templates.

## Interpret intent

| Requested outcome | Route |
| --- | --- |
| Build/change/fix executable behavior | development; `workflow.md` and full gates |
| Write/edit a document | document; content/deliverable checks |
| Explain how/why | explanation; relevant knowledge/source evidence |
| Investigate/compare/analyze | research; conclusions, uncertainty and scope |
| Read-only review | review; report without implementing fixes |
| Plan only | planning; no implementation |
| Continue/status/steering | existing task; no duplicate goal |
| Pause/stop/off | controls in `entry.md` |
| Frozen scope change | versioned scope-change feedback; block old task/new goal |
| Acknowledgment | no invented task or decision |

Honor explicit limits over inferred intent. A code path or “fix” in an explanatory
question does not authorize development. Classify mockups by requested purpose;
mixed planning/implementation retains development gates. Answer progress questions,
consume steering and continue authorized scope. Never let peer messages become user
authority. Harness changes/evaluation also use `benchmark.md`; ordinary project work does not.

## 1. Set the goal

1. Restore the owned session and relevant unfinished task. For new work choose kind
   and project locations; pass `--session SESSION_ID`, including on the first task.
2. Use capsule references and scoped `knowledge context --path PATH` (repeat paths
   for related projects). Search/page before declaring a topic absent. Reuse current
   facts/answers within their original authority and scope; investigate only relevant
   missing/stale dependencies. Drafts, hypotheses and historical passes are not facts.
3. Establish desired outcome, deliverable, scope and criteria independently of current
   code maturity. Delegate technical gaps and discover needed requirements/PRD/plan/
   mockup/API/architecture inputs under `context.md`; reconcile contradictions and
   affected consumers. Ask only material unresolved human-owned choices before start.
   Do not require pasted discoverable context or impose unrelated workflow stages.
4. If sufficient, define the goal internally and start; goal setting is no mandatory
   questionnaire or approval. Configure verified available model/effort profiles in
   `execution.routing` before sealing; optional `LOOP_ROUTING_PROFILE` imports them.
   Missing configuration blocks dispatch; never substitute CLI defaults or a changed
   environment. `routing.md` owns demand, eligibility and selection.

Development uses `init --implementer WORKER --orchestrator CONVERSATION --session SESSION`
or `work open --kind development`, then `workflow.md`. For several working trees,
create linked goals in the same session and verify every required goal/integration.
Common `work finish` cannot complete development.

Other work uses `work open --kind KIND --title TITLE --provider PROVIDER --path PATH
--session SESSION`, `work goal TASK --input FILE`, then `work start TASK`. The agent
creates goal input from `work-goal.example.json`; do not ask users for JSON or IDs.
`--provider` identifies the conversation (`claude`/`codex`); optional `--worker` selects
the worker, defaulting to that provider. Its CLI must be installed/authenticated.
No Git repository or opposite-provider CLI is required. Use read-only preflight
assignments to resolve factual gaps.

## 2. Execute and assess

Follow `routing.md` and `delegation.md` for schema and execution. Keep a small complete
unit in one assignment; teams require distinct independently verifiable contributions,
explicit dependencies and exclusive file ownership. Give purpose, read/write scope,
prohibitions, outputs and criteria; pass only selected current knowledge and fresh
prerequisite evidence. Choose the least expensive eligible explicit profile; higher
initial demand requires factual evidence. Worker roles stay fixed; bounded peer
messages route through the coordinator, writes serialize and budgets remain finite.

Treat `delegate`/`team` output as candidates. Inspect goal/criterion evidence and call
`assess` internally before effects or peer delivery; never add a user approval.
Reject the whole incorrect/harmful candidate, retain audit evidence, and repeat the
same assignment/team on the next stronger allowed profile within existing budgets.
Rejected content is never reusable fact. Configuration, permissions, timeout and
changed inputs are blockers, not quality-escalation evidence. Check claimed blockers
against available inputs; reject disproven claims while preserving real blockers.
Continue until complete or honestly blocked without requiring another user prompt.

Development repeats implementation, actual tests, opposite-provider review and bounded
repair under frozen acceptance. Other work uses `work delegate`/`work team`, one pass
plus content/completion checks, correcting actual errors without a mandatory cycle,
second model judge or cross-provider review. Repeat an assessed team command until
its accepted work is complete. During execution use established decisions or record
an actual blocker; do not ask new questions or weaken criteria/rules.

Save steering with `orchestration feedback` using the capsule's current control version
and original user input reference before the next dispatch. Deliver corrections/
constraints to affected workers before applying results. `context.md` owns its fields,
stale-version recovery and scope checks. A frozen scope change needs a new task.

## Finish and maintain knowledge

Read relevant detailed context before investigation; retain its `revision`. Save useful
supported facts/decisions with `knowledge remember --observed REVISION` using
`knowledge.example.json`. After source edits delegate affected-dependency inspection
and update only relevant summaries; a read alone never refreshes stale conclusions.
Follow `writing.md`; avoid repeated narrative logs and honor recording restrictions.

Non-development `work finish TASK --input FILE` uses `work-result.example.json`: actual
output/answer reference, concise result and evidence check for every criterion. Inline
answers are valid deliverables; retain useful substance in the result. New knowledge
may commit atomically with current `observed_revision`; drafts/hypotheses never
promote automatically. These are the orchestrator's checks, not independent verification.
Use targeted `work context`, `work note`, `work block`, `work resume` or `work abort`
for detail/recovery. Retain unsuccessful attempts. Historical completion cannot supply
fresh code evidence or publishing/deployment permission.

## Knowledge boundaries

The local store defaults to `~/.local/share/loop-work/knowledge.sqlite3`; choose another
external-to-source home with `LOOP_KB_HOME`/`--knowledge-home`. Store no credentials.
`knowledge projects` lists identities/checkouts. Git origin groups remote clones;
local worktrees share common-directory identity; ordinary directories use filesystem
identity. For otherwise unlinked copies/moves, `knowledge attach` only from evidence.
Paths are navigation, never authority; do not combine unrelated project/branch facts.

Preserve `global`, `project`, `checkout`, `session` and `task` scope. Never promote
one-time answers to global preferences or silently change topic ownership.
`confirmed` needs evidence; keep `draft`, `hypothesis`, `superseded` distinct.
Record complete source read sets (trailing `/` watches contents; `.` only as broad
fallback), rules/branches and timezone-qualified `expires_at` for time-sensitive facts.
Local hashes cannot prove external-system freshness. `knowledge context --query/--offset`
and `knowledge history topic|task ID` expose retained versions; never erase evidence
for a success story. If nothing new is learned, save only the task result.
Read-only source work can retain internal memory; an explicit all-writes/recording
restriction takes precedence.
