# Architecture

## Core decision

The user's entry point is a Claude Code or Codex conversation. It orchestrates goal
setting, scoped assignments, evidence checks, knowledge reconciliation and reporting.
Actual analysis, planning, implementation and writing are delegated to bounded workers.
Portable scripts validate worker results, apply allowed proposals, run deterministic
gates and persist state. The opposite provider to the implementation worker reviews
development. Other work has an evidence-backed content/completion check by the
orchestrator without a mandatory loop. There is no SDK, API-key requirement or daemon.
Worker CLIs can use either provider. Existing vendor/host restrictions on nested calls
are preserved: preflight must establish a usable worker, not bypass such restrictions.

This division makes the same workflow available in both environments while keeping
the user's responsibility to the goal and genuinely human-owned decisions. Calling
the CLI is an internal agent action, not a sequence of prompts the user must provide.

The user explicitly starts the loop-dev skill once. Installed AGENTS.md and CLAUDE.md
then route every input in that conversation through `work.md`, including new goals,
questions, documents, corrections and stops. A goal finishing, a task kind or directory
changing, and context compaction do not disable activation. Only an explicit off
request disables it; a task pause keeps it active. Separate conversations start off.
The shared skill has Claude and Codex discovery files with implicit invocation off.
This is host-agent routing plus persisted state, not a keyword parser or message hook.

| Responsibility | Implementation |
| --- | --- |
| Shared behavioral policy | `rules/workflow.md`, `delegation.md`, `context.md`, `quality.md` |
| Bounded delegated work | `delegation.py`: explicit assignment, read-only worker, validated patch application |
| Common goal setting and work | `work.py`, `rules/work.md`; development delegates to existing gates |
| Cross-project knowledge | `knowledge.py`; local SQLite, scoped records and dependency checks |
| Readable records and derived views | `writing.py`, `rules/writing.md`; deterministic Markdown, concise authored fields |
| Provider entry | `skills/loop-dev/`, installed discovery files, managed AGENTS.md/CLAUDE.md blocks |
| Conversation activation | `harness.py`; explicit on/status/off, shared SQLite session record |
| Goal completeness | `contract.py` validates structural traceability and decisions |
| Context reconstruction | `context.py` derives repository inventory, runtime evidence and next action |
| Reusable conversation analysis | `session.py` stores topics, decisions, dependency fingerprints and goal history |
| State/gates/recovery | `engine.py` |
| Independent review | `providers.py` invokes the opposite provider and validates structured output |
| Bounded subprocesses | `process.py`, argv arrays, stdin EOF, process-group timeout |
| Freshness/concurrency | `storage.py`, hashes, atomic JSON, POSIX flock |

## Session activation

`harness on --provider PROVIDER [--session ID]` explicitly enables the bound session.
Without an ID it creates a fresh conversation; an unknown supplied ID or different
provider is rejected. `harness status ID` reads that specific session. `harness off ID`
disables routing without changing goals, knowledge, files or execution budgets.
The session's `harness` field records `enabled`, `activated_at` and `updated_at`.
Legacy sessions without this field are inactive. Identical toggles do not create
repeated history. Changes serialize through a SQLite transaction.

Every new goal/task receives that ID, even the first. Status, shared knowledge context
and development session context expose current activation from the shared store.
Task completion never toggles it. The host preserves `{session_id, knowledge_home,
entrypoint}` in conversation/compaction handoffs. The absolute CLI path works after
moving to an uninstalled directory, using `--root`/`--path` to select the target.
No provider-wide "latest session" file is used. Knowledge may be shared across chats;
activation is never inherited through that knowledge.

Low-level engine and work commands remain usable independently; `harness on` is an
explicit agent action, not cryptographic evidence that a vendor skill was invoked.
The cooperative host must honor routing and off requests. The scripts cannot intercept
all messages, recover an erased binding without ownership evidence, or override host
permissions. An off input stops further automatic dispatch when the host handles it;
it does not retroactively cancel a synchronous subprocess. Rule fingerprints include
the installed harness skill and metadata, including ignored files, so edits invalidate
frozen development evidence and dependent knowledge. Workers cannot edit `.agents/`.

## Assignment boundary

`delegate GOAL` and `work delegate TASK` run one explicit assignment from a JSON input.
The same protocol is used for both providers: instruction, read paths, exact write
paths, prohibitions, named deliverables, completion criteria, selected knowledge IDs
and prerequisite assignment IDs. A common task with multiple roots must select one
root per invocation. Worker status is distinct from parent goal status.

Workers receive only selected current knowledge and fresh prerequisite outputs, plus
the parent goal, decisions, initial diagnosis, notes, latest gate and prior failure as
context. Source files are indexed inside explicit read scopes; applicable rules are
added to that list. Context is evidence, never additional delegated authority. Workers
cannot recursively invoke the harness, ask users, redelegate or change rules/state.
Missing scope or information returns a precise blocker to the orchestrator.

Both CLI transports are read-only. Proposed edits are complete UTF-8 file contents
or deletions with original hashes. All proposals are checked before the first edit:
exact allowed paths, no wildcards/traversal/symlinks, no instruction/provider/state
edits, no ignored write targets, matching hashes and unchanged parent/source state.
The applier records originals, preserves modes and uses atomic replacement per file.
After apply it checks for unexpected source, branch, rule or parent changes. File
operations are not a multi-file transaction: a filesystem error can leave partial
edits, which remain recorded with originals. Nothing resets or cleans user changes.

Assignment IDs are immutable and bound to a working location. Repeating a completed
request reuses it only with the same parent, source and selected handoff evidence.
Dependencies must have a successful result that still matches their own location,
including handoffs across directories in one task. Read sets support scoped context selection; they are not per-file
OS access controls. Provider permissions and host policy still govern actual reads,
external tools and hooks. Semantic compliance remains an orchestrator/reviewer check.

Task and location locks serialize workers; call budgets are shared across all IDs
in a task. At most three attempts per ID, default 40 calls per task, and per-call
(default 600 seconds) and working-time budgets apply. Retry/resume retains counts;
preflight clarification does not consume working elapsed time. Recovery of an
interrupted call requires inspection and an explicit recovery reason.

Every attempt retains its prompt, schema, process logs and available result/originals.
SQLite records current assignments and prior versions; context includes a compact
index and Markdown is derived from the same records. Findings become task-scoped
drafts, never automatic facts. The orchestrator reconciles evidence before promotion.
`done` never changes a parent goal to completed; development gates and common content
checks retain that responsibility.

## Goal versus current code

`contract.json` describes the desired outcome. `discovery.json` describes code and
rule evidence at preflight, including a gap for every requirement. A new repository,
prototype or nearly complete product can share the same target. The amount of work
changes; acceptance does not. The plan reviewer checks that the target is actionable,
not that it has already been implemented.

The preflight decision areas are scope, architecture, data, interface, dependencies,
security, verification, operations, delivery and autonomy. The model may resolve
technical facts through investigation and choices within existing delegated authority.
It asks the user only about material decisions that cannot be established this way.

## Common work and knowledge

Non-development work moves from `goal_setting` to `working` to `completed`, with
explicit `blocked` and `aborted` outcomes. A goal records outcome, deliverable,
criteria and scoped decisions. Unresolved questions block start; the goal is sealed
on start. Finish requires evidence for each criterion. It invokes no provider or
test process and does not attest independently that a referenced output is correct.
The active agent inspects the deliverable and records that assessment. Failed criteria
remain working; there is no prescribed review/repair cycle or iteration count.

SQLite records projects, checkouts, sessions, tasks and knowledge topics. Origin
normalization groups Git clones without storing URL credentials; checkout identity
and observations keep their sources separate. Non-Git directories are supported.
Same-conversation IDs can cross roots. Scoped decisions retain original authority;
global/project knowledge can be reused across conversations without adopting a
different conversation's session binding. Drafts/hypotheses never become facts on finish.

Knowledge updates compare the read observation before and after preparation. SQLite
transactions and optimistic task version tokens protect concurrent writers; result
and new knowledge commit together. Identical topic content/dependencies do not create
another historical revision. Development remains authoritative in its repository
state: the shared task record mirrors progress, and common finish rejects it.
Common development context refreshes repository state before reporting freshness.

Markdown views are derived from committed data. Rendering never asks a second
model to invent a parallel explanation. Topics have a bounded one-line conclusion,
optional implication/details/uncertainty, scope/status and exact evidence references.
Full JSON retains all references; context is paged with counts and retrieval commands.
Documents are replaced only when content changes. A view export failure leaves
committed data intact; `knowledge get`/`work context` regenerates it. Confirmed labels
alone do not establish freshness; applicability is computed on context reads.
These checks constrain shape and duplication, not factual or semantic writing quality.

## Development state machine

| State | Entry condition | Allowed progress |
| --- | --- | --- |
| draft | init | Investigate, clarify, edit contract/discovery, repeat plan review |
| ready | valid contract + current passing opposite-provider plan review | start on the reviewed snapshot |
| running | frozen inputs and code unchanged since preflight | Implement, note discoveries, checkpoint, finish |
| blocked | External/protocol failure, mutation, exhausted budget, explicit blocker | Resume if retryable; otherwise abort/new preflight |
| completed | All milestones + fresh all-checks pass + full review pass | Report while evidence remains current |
| aborted | Explicit abort with reason | Report retained evidence; new goal can own the tree |

Only one running/blocked goal owns a working tree. A repository-level file lock also
prevents concurrent CLI mutations. It does not stop external editors or another
agent from editing files; snapshot comparisons reject evidence if inputs change
during checks/review.

## Frozen inputs and freshness

Freeze records canonical hashes of the target, diagnosis, decisions, declared rules,
discovered repository instruction files and gate-engine modules. New nested AGENTS
or CLAUDE files invalidate the seal too. Existing project-local provider settings are
hashed without copying their content into prompts. Global and managed provider
configuration remains outside the seal; the host remains responsible for its policy.

The work snapshot includes Git HEAD and hashes of tracked, deleted and untracked
nonignored files, including executable bits and symlink targets. Runtime artifacts
under `.loop/state/` are excluded. Ignored build caches/output are not verification
inputs. Declare critical ignored rules explicitly; do not keep implementation source
or acceptance fixtures ignored. Submodules are rejected rather than silently omitted;
run separate goals inside them.

After successful checkpoints, subsequent implementation may change earlier files.
Final finish always re-runs every check and obtains a new review of every acceptance
criterion on the final snapshot. An edit or commit after completion makes `status`
report `evidence_current: false` and instructs the agent not to claim current completion.
The old completed record is retained as historical evidence.

## Automatic context loop

`context` reconstructs target, original gap analysis, rule paths, source inventory,
build manifest excerpts, test paths, milestone progress, latest gate and accumulated
discoveries. Its `next` action routes the active conversation to clarification,
implementation, final verification, recovery or reporting.

The orchestrator uses this index to assign source analysis and implementation. It
inspects returned evidence, adjusts tactics within the frozen target, and passes the accumulated
notes to the next independent review. Review failures become repair instructions, and
test failures retain exact commands and log paths. This replaces the user's serial
context-feeding with an evidence-driven agent loop.

Notes are append-only through the CLI and can change tactics, not the frozen scope,
commands, business rules or authority. A discovery that truly requires a new business
decision becomes a blocker and a separate preflight goal rather than a mid-run question.

## Analysis reused within a conversation

A local session spans multiple goals in the same Claude Code or Codex conversation.
Explicit skill activation creates or enables its session; every goal passes
`--session`. Low-level `init` or `session open` without an ID creates an inactive
session. The agent carries the ID, absolute entrypoint and knowledge home in handoffs.
New conversations use new IDs; the engine never selects the latest cache as a proxy
for session ownership. Identified tasks recover bindings only for their own resumed
conversation. Providers must match the binding.
There is no undocumented host-session lookup, global pointer or vendor-specific hook.

`session.json` contains indexed topic summaries and original evidence, scope/basis
of decisions, linked goals and the last observation. `context.json` separates reusable
entries, stale entries with changed dependencies, other-goal entries and historical
goal records. Goal `context` embeds this view, so it is available before every prompt's
investigation/clarification. Decisions and execution notes are archived automatically;
passing preflight/review evidence updates one broad fallback summary per goal. Fresh
implementation evidence replaces its preflight summary. The agent also persists focused
topics while investigating, answering decisions and editing to preserve granular reuse.

Dependencies are explicit file paths or directory prefixes. Prefixes detect additions,
deletions and renames; exact missing paths can record absence. Fingerprints include
working-tree changes and rule hashes, not just HEAD. All topics are reconciled after a
branch or rule change. A content-preserving commit retains code analysis, while the
existing completion gate still rejects changed HEAD. Creating `.loop/goals/` metadata
does not invalidate root-wide source summaries; an explicitly named goal file can still
be a dependency. Cached manifest excerpts are read again only if their hashes change.

`remember --observed` rejects a repository revision changed since the analysis context
was read. Updates preserve previous entries in history. Refresh only classifies freshness;
it cannot re-stamp a stale summary. The agent re-investigates relevant changed dependencies
and writes a new summary. Unaffected topics retain their evidence/time. It does not
re-investigate unrelated historical topics merely because they are stale.

Decisions preserve `user`, `repository-rule` or `delegated` basis and an `applies_to`
boundary. Session-wide preferences can survive unrelated code edits; goal-scoped answers
are presented separately and never promoted automatically. Earlier applicable answers
are carried into new contracts instead of being asked again. Whether a new product scope
fits an answer remains a semantic judgment for the agent and preflight reviewer.

Memory is derived context, not an authority source or verification pass. Final tests and
independent reviews remain fresh; reviewers receive reusable summaries as navigation
hints and must inspect actual behavior. Correct invalidation depends on complete declared
dependencies. Ignored application source and external runtime/service changes are outside
that model; explicitly declared ignored rules are hashed, and external facts require fresh
checks. Rules instruct agents to record the complete read set and directory-level absence.

Development session caches remain under ignored `.loop/state/`, protected by the
repository lock and atomic writes. Entries are imported into shared knowledge with
their original dependency stamps; import cannot renew stale evidence. Shared knowledge
defaults to `~/.local/share/loop-dev/` or `LOOP_KB_HOME`, outside source trees. Neither
store changes frozen contracts. Older goal state acquires a stable session binding on
first context read. Reconciliation is request-driven, not a background watcher.

## Review protocol

| Implementation worker | Read-only reviewer |
| --- | --- |
| Claude Code | `codex --ask-for-approval never --sandbox read-only exec ...` |
| Codex | `claude -p --permission-mode dontAsk --tools Read,Glob,Grep ...` |

Each review is a new invocation with no resumed conversation. Its prompt identifies
the reviewer role, frozen criteria, original gaps, rules, changed paths, verification
logs and execution notes. Reviewer instructions explicitly prohibit recursively
starting the workflow from AGENTS/CLAUDE instructions. Claude's tool list is restricted
to reads and MCP tools are excluded for this invocation. Codex uses its read-only
sandbox and existing host configuration; external tools remain subject to host policy.
The engine detects working-tree mutations, but is not an isolation mechanism for
arbitrary externally configured tools or hooks.

Both adapters request the same JSON schema. Runtime validation rejects missing
coverage, duplicate/unknown IDs, blank evidence, malformed findings, execution-time
questions, and pass verdicts with findings. Every criterion needs a coverage record.
All finding severities block. A denied Claude permission or failed process blocks the
review. The model cannot set the persisted completed state through a review result.

## Failure and durability

- Finite attempt and wall-time budgets apply across resume; they cannot be reset by
  ordinary retry. Gate attempts are saved before subprocess invocation.
- Each command closes interactive stdin, inherits configured credentials, captures
  stdout/stderr and has a timeout. Timeout/interruption terminates its process group.
- State JSON is atomically replaced and each event has its own atomic file. An
  unfinished `inflight` gate is detected and requires inspecting logs before resume.
- Source, logs and user edits are retained on failure. There is no reset/clean/force
  push/automatic deletion of the working tree.
- The runtime directory is local and Git-ignored. Cross-machine or clean-clone resume
  requires transferring state intentionally; a goal definition alone is not evidence.

## Explicit limits

Scripts enforce structure, transition ordering, bounded execution and evidence
freshness. Models judge completeness and investigate sources. Neither proves that
arbitrary software is bug-free or that unavailable services can be made available.
The workflow is cooperative, not tamper-proof: a process that can modify the repo
can also edit state or bypass the scripts. Existing host permissions remain essential.

The implementation deliberately keeps commit/PR/deployment outside automatic engine
side effects. The active conversation delegates pre-authorized delivery actions to explicit host
tool calls and reports their actual outcome. Read-only workers cannot publish. Core `completed` means verified development;
it does not itself assert that a remote deployment or merge occurred.
