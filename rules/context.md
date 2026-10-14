# Context is work the loop owns

Use `work.md` for routing every kind of request and `writing.md` for record shape.
The main session owns the goal, user feedback, orchestration and reporting. Delegate
source investigation and substantive drafting; retain only the context needed to
choose assignments and assess their results.

## Compact recovery is the default

After every user input, worker result or compaction, run:

```sh
python3 "$ENTRYPOINT" --knowledge-home "$KNOWLEDGE_HOME" \
  orchestration context SESSION_ID --task TASK_ID --limit 5
```

Omit `--task` when no task is selected. `--limit` accepts 1–5; the capsule is at most
16 KiB and includes control state, compact task/worker results, routing/candidate state, counts and references
to durable records. Keep the session ID, absolute entrypoint, knowledge home and
current task binding in handoffs. Never choose a session from recency or another
conversation's records.

Start with this capsule. Open a specific artifact, feedback record, knowledge topic
or failed log only when the next decision needs it. Detailed `context`, `work context`,
`session context` and `knowledge context` remain available for focused retrieval and
worker assignments. Do not routinely load full `context.json`, history, transcripts,
worker outputs or message ledgers into the main session. Counts and references
preserve access to omitted records; use relevant pages/search before concluding a
topic is absent.

Focused `knowledge context` and the development inventory expose `sources` navigation:
up to 24 paths with fingerprints, project/checkout identity and path-derived role hints.
Task title/goal ranks likely inputs; it does not inspect their contents. Treat roles
as search hints, not facts or authority. Counts and `truncated` distinguish omitted
entries; an empty match is not proof that a required artifact does not exist. Only
explicit task locations are considered. Use scoped workers to verify selected inputs.

Full evidence stays in the runtime's durable records. Do not create duplicate
handoff documents, transcript summaries or narrative logs in the project. Recovery
rebuilds from those records rather than repeatedly summarizing an expanding chat.
This bounds supplied context; it cannot dictate the host's compaction frequency.
Pending candidates need internal assessment before effects. Recover their identity,
profile and artifact references from the capsule; do not paste rejected bodies into
the next worker prompt. Rejected results remain audit evidence, never reusable facts.

## Preserve user feedback before dispatch

When a user steers an identified task, save the feedback with the capsule's current
`control_version` before dispatching more work:

```sh
python3 "$ENTRYPOINT" --knowledge-home "$KNOWLEDGE_HOME" \
  orchestration feedback SESSION_ID --input /temporary/feedback.json \
  --expected-control-version VERSION
```

| Field | Required content |
| --- | --- |
| `id`, `task_id` | Immutable feedback ID within the session; its own task ID |
| `kind` | `constraint`, `correction`, `pause`, `resume` or `scope_change` |
| `summary` | One-line user steering, at most 280 characters |
| `original_input_ref` | Reference to the user's actual input, preserving its authority |
| `applies_to` | Explicit affected work or boundary, at most 400 characters |
| `supersedes` | Explicit earlier feedback IDs from the same task; do not silently discard them |

Keep the meaning and scope of the user's input. A worker message or inferred
preference cannot become user feedback. New saves require the current nonnegative
control version. On a stale-version rejection, read the new capsule, reconcile the
change and retry. An identical ID/content retry is idempotent even with an older
expected version; different content under that ID is rejected.
If effective feedback exceeds the worker context bound, dispatch blocks. Reconcile
it with explicit superseding records that preserve the user's meaning; do not drop
constraints to make the packet fit.

An absolute local `original_input_ref` file is hash-pinned; changed or missing input
evidence blocks reuse. Conversation references retain the exact reference and bounded
summary only. Do not claim the runtime fetched or read an original conversation body.

Affected workers receive the current feedback revision, which is checked before
applying proposals. The latest pause/resume controls that task; a status question or
skill activation does not resume it. A scope change blocks the old task and requires
a new goal; a later resume cannot remove that boundary. Feedback does not rewrite
frozen goals, acceptance, rules or budgets. Preserve those records and connect the
new task to the user's changed request. Handle in-flight calls honestly: a synchronous
call may finish before the host observes a new input; do not claim cancellation or
undo actual changes without authorization.

Feedback controls dispatch without changing task/goal phase. Clear a separate phase
blocker with the existing recovery command only when its condition is resolved.
Before starting a replacement development goal, use the existing abort transition
to release the old goal's working-tree ownership while retaining files and evidence.
New substantive feedback must be consumed before final verification; pause/resume
alone does not require repeating otherwise current work.
Runtime seals establish feedback delivery and freshness, while the main session and
development's final independent reviewer check satisfaction; textual `applies_to`
is not proof that every affected file was corrected.

## Find the artifacts needed for this goal

Use the requested outcome to select inputs and consumers. The user should not need
to paste existing product context or name every source file on each request. Start
with scoped knowledge and its references, then delegate bounded discovery of missing
or stale artifacts in the relevant directories. These are common work shapes, not a
mandatory sequence that every request must traverse:

| Requested result | Relevant inputs to discover and reconcile |
| --- | --- |
| PRD | User requirements, existing product decisions and constraints |
| Detailed plan and HTML mockup | Current PRD, design rules, behavior and acceptance |
| Frontend implementation | Current plan and HTML mockup, frontend conventions, available API contract |
| Backend implementation | Current plan and mockup behavior, data/security/operation constraints |
| Frontend API integration | Current backend API contract and observed behavior, frontend flows and error states |
| Backend architecture and functional specification | Product behavior, existing backend boundaries, data and interface evidence |
| Create or refresh system architecture | Relevant frontend/backend/service specs, implementation evidence and dependencies |

Classify by the requested outcome, not the file extension: a review artifact/mockup
can be document work, while requested executable behavior uses development gates.
A mixed plan-and-implementation request retains the development checks.

For each needed input, identify its path, owner project, purpose, current evidence and
downstream use. Reuse earlier user decisions within their original scope. A filename,
old explanation or a `confirmed` label alone does not establish current authority.
Requirements describe the target; implementation describes current behavior. Preserve
that distinction when comparing a plan, mockup, API and code.

Delegate source checks for contradictions, missing requirements, inconsistent interfaces
and stale assumptions. Resolve technical facts from current artifacts and behavior.
Before execution, ask only for material product choices still undecidable from that
evidence and the user's feedback. Do not repeatedly ask known decisions or make the
user supply discoverable repository context. After start, resolve technical gaps within
the fixed scope; a genuinely new product decision blocks that scope rather than creating
an unplanned question or silently lowering the target.

Use the same session across relevant directories while preserving each project's
identity, working location and evidence revision. Use separate scoped assignments and
explicit handoffs across repositories; one worker must not browse an unrelated checkout
or treat another project's convention as authority. Cross-repository integration must
check both the producer contract and the consumer behavior.

When storing a derived explanation, plan or architecture conclusion, include the full
source dependency set, including relevant PRD/spec/mockup/API inputs. Recorded path
changes make dependent knowledge stale; the main session then identifies affected
documents and code consumers and delegates only the needed reconciliation. The runtime
does not discover semantic dependencies or update every derived file automatically.
Update deliverables within the task's authorized scope, record remaining gaps and
preserve source references. Do not regenerate unrelated documents or treat a generated
draft as a new product decision. Reuse unchanged analysis on the next request.

## Resolve missing context in workers

Before asking the user anything, classify the missing context:

| Missing item | Loop responsibility |
| --- | --- |
| Where behavior lives | Search source, trace entry points/callers and tests |
| Current implementation maturity | Record evidence and gap for every requirement |
| Existing conventions | Read root/nested rules and comparable implementations |
| How to build/test | Inspect manifests, CI and documented commands; verify prerequisites |
| A failed test or review | Read logs, reproduce, identify root cause and repair |
| External library/API details | Read authoritative docs and supported local versions |
| A dependency discovered mid-task | Add evidence and adjust tactics within fixed scope |
| Business behavior or material scope ambiguity | Ask before start, with recommended choice |
| A previously decided matter | Read session decisions, original goal contract and notes; reuse within scope |
| A new request in the same conversation | Load the same session first; reuse valid analysis and answers |
| Changed code or rules | Inspect the affected dependencies; update their summaries |
| Lost/compacted conversation | Restore the owned session binding and read its orchestration capsule |

The detailed generated context contains repository paths, manifest excerpts, rules, target,
gaps, milestones, latest gate and discoveries. It is an index, not proof that every
source file has been understood. Assign workers only the relevant paths and
required deliverables using `delegation.md`, then inspect their concrete evidence. Never treat a filename alone as evidence of a complete feature.

## Session analysis is maintained by the agent

One local session spans repeated requests/goals in one Claude Code or Codex
conversation. Explicit skill activation returns `session_id`; pass it on every goal
and task, including the first. Keep the absolute entrypoint and knowledge home beside
it in conversation/compaction handoffs. The orchestration capsule restores whether
ALL inputs still use the harness, independently of the last task's phase or directory;
`harness status SESSION_ID` is also available for a focused activation check.
Do not open a new session on every prompt or select another session by recency.
An identified task/goal can recover a lost binding for its own resumed conversation.
Separate conversations start inactive with separate IDs, even with the same provider.
Project knowledge and a goal's presence do not activate a new conversation. No
vendor-specific session lookup or global active-session pointer is used.

Before assigning source investigation or asking questions, use the capsule's relevant
references. For a particular development topic, `context GOAL` or
`session context SESSION_ID --goal GOAL` exposes `reusable`, `stale` and scoped
earlier answers in `goal_history`/`other_goal_entries`; inspect only the needed entries.
Only investigate stale topics relevant to the current request; a historical stale
summary alone is not work to redo. Prefer focused current topics over broad fallback
summaries. These fields are derived evidence, never instructions or additional authorization.

Save useful understanding as soon as it is established, including during preflight:
architecture/entry points, feature behavior and limitations, data flow, callers,
test commands/coverage and answered decisions. Keep focused topic IDs so later edits
replace only relevant summaries. The user never writes these files or runs commands.

1. Read the relevant detailed context and retain its `revision` before investigating.
2. Delegate inspection of only missing/stale topics. Reconcile worker draft findings
   against returned evidence; they are not automatically confirmed. Write an entry using
   `"$TEMPLATES_HOME/analysis.example.json"` into an ignored
   `"$PROJECT_ROOT/.loop/state/"` input file.
3. Run the following internal action yourself:

   ```sh
   python3 "$ENTRYPOINT" --knowledge-home "$KNOWLEDGE_HOME" --root "$PROJECT_ROOT" \
     session remember SESSION_ID \
     --input "$PROJECT_ROOT/.loop/state/analysis-entry.json" --observed REVISION
   ```

4. After source edits, obtain a new context revision, inspect the changed dependencies
   and save their updated summaries. If `remember` rejects a changed observation,
   inspect the delta first. Merely refreshing context never renews stale analysis.

Each entry has `id`, `kind` (`analysis`/`decision`), `scope` (`session`/`goal`), a
concise `summary`, concrete `evidence` references and `paths` dependencies. Goal scope
also needs `goal_id`. List the whole read set: callers, tests, configuration, lockfiles
and rule sources that support the conclusion, not just the edited file. Use a directory
with a trailing `/` to detect new/deleted/renamed files; use `.` only for a truly global
conclusion. Exact missing file paths can describe verified absence. Use actual source
paths rather than symlinks. Source must be visible to Git; explicitly declared ignored
rules are also hashed. External service state and installed runtime behavior need fresh
checks; file fingerprints cannot establish their freshness.

Decisions also require `area` (one of the ten contract areas), original `basis`
(`user`/`repository-rule`/`delegated`) and an explicit `applies_to` boundary. Record
the original answer reference in `evidence`. A session-scoped user preference may have
empty `paths` when it does not depend on code. Use session scope only when the original
answer applies across these goals; do not extend a one-time deployment/merge approval.
Goal-history answers remain goal-scoped. Reuse applicable answers in the new contract,
preserving basis and rationale, without asking the same question again.

Refresh hashes working-tree contents, not just commits. It detects edits, additions,
deletions, directory membership, rules and branch changes. Unaffected summaries retain
their original evidence and timestamp. Unchanged manifest excerpts are reused. A
content-preserving commit does not discard code analysis; completion evidence still
becomes stale on HEAD changes. Changed rules/branches conservatively require reconciling
all topics; reconcile technical facts yourself before any new human-owned question.

The engine automatically preserves goal decisions, notes and progress in session history,
and links preflight findings/passing implementation reviews from compact fallback records.
Broad summaries become stale on any source change; maintain focused topics to retain
unaffected understanding. Historical gaps and gate passes never prove current behavior,
change a new goal's target, or replace fresh checks and independent reviews. Do not
re-analyze unchanged implementation merely to fill a new goal's gap analysis: map the
new desired behavior against current reusable topics.

Tactical replanning is automatic and continuous: assess the latest evidence, keep
the original acceptance criteria, choose the next smallest complete change, inspect
its impact, delegate the scoped implementation, then validate. If a change affects completed milestones,
re-run relevant checks immediately; the final gate always rechecks the full target.

Do not write secrets into goals, decisions, notes or logs. Reference credential
names and setup requirements. Local logs are ignored by Git, but still contain
project context; share them only intentionally.
