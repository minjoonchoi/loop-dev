# Architecture

## Core decision

The user's entry point is a Claude Code or Codex conversation. This main session owns
goals, user feedback, scoped assignments, evidence checks and reporting.
Actual analysis, planning, implementation and writing are delegated to bounded workers.
The main session receives compact role results and artifact references; source analysis,
full worker output and peer exchanges stay in worker contexts and durable records.
Portable scripts validate worker results, apply allowed proposals, run deterministic
gates and persist state. The opposite provider to the implementation worker reviews
development. Other work has an evidence-backed content/completion check by the
orchestrator without a mandatory loop. There is no SDK, API-key requirement or daemon.
Worker CLIs can use either provider. Existing vendor/host restrictions on nested calls
are preserved: preflight must establish a usable worker, not bypass such restrictions.

This division makes the same workflow available in both environments while keeping
the user's responsibility to the goal and genuinely human-owned decisions. Calling
the CLI is an internal agent action, not a sequence of prompts the user must provide.

Harness benchmarking also has a standalone terminal entry point: `scripts/benchmark.py`.
It manages its own provider conversations; users do not need to open a chat or activate
the harness skill before running a benchmark.

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
| Worker teams | `team.py`: fixed roles, addressed peer messages, bounded turns and serialized application |
| Main-session recovery and steering | `orchestration.py`: compact capsules and versioned user feedback |
| Common goal setting and work | `work.py`, `rules/work.md`; development delegates to existing gates |
| Cross-project knowledge | `knowledge.py`; local SQLite, scoped records and dependency checks |
| Readable records and derived views | `writing.py`, `rules/writing.md`; deterministic Markdown, concise authored fields |
| Provider entry | `skills/loop-dev/`, installed discovery files, managed AGENTS.md/CLAUDE.md blocks |
| Conversation activation | `harness.py`; explicit on/status/off, shared SQLite session record |
| Harness outcome benchmarks | `benchmark.py`, `bench_transport.py`, `bench_report.py`; isolated paired runs, external grading, comparison gates |
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

Every new goal/task receives that ID, even the first. `orchestration context SESSION`
is the main session's default input after user inputs, worker results and compaction.
Status and the underlying knowledge/development contexts also expose activation.
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

## Cooperative worker teams

`team GOAL --input FILE [--recover REASON]` and
`work team TASK --input FILE [--path ROOT] [--recover REASON]` run an orchestrator-owned
plan through `Team(knowledge, task_id, root).run(plan, recover=None)`. The plan assigns
fixed roles using the existing assignment schema; `rules/delegation.md` owns its wire
contract. Each role gets an isolated, ephemeral CLI context on each turn, restored
from its assignment, selected current context and addressed messages. Roles and scopes
stay fixed across turns. A worker cannot create roles, redelegate or change the goal.

Workers can return bounded questions, answers and findings to named peers. The
coordinator validates recipients, scope, answer links and source evidence, persists
the exchange, and routes it automatically to the addressed worker's next call.
Messages are draft data, never user instructions or permission to widen a read/write
scope. Unsupported requests become a blocker rather than implicit reassignment.
The main session receives compact member results and references to full artifacts,
so it need not relay or reload every exchange.

The portable implementation serializes worker execution and file application. It
does not depend on vendor-native real-time chat or concurrent writers. Task-wide call
and elapsed budgets, per-assignment attempt limits and a finite team turn limit all
remain in force; recovery cannot reset them. Existing `delegate` remains appropriate
for a single bounded unit. Team completion does not replace development checkpoints,
fresh opposite-provider final review or common content/completion checks.

## Compact orchestration and user feedback

`orchestration context SESSION [--task TASK] [--limit 1..5]` produces a recovery capsule
of at most 16 KiB. It exposes current control state, task/worker summaries, counts and
artifact references without source, transcript, log or output bodies. Full evidence
remains on disk and is loaded only to answer a concrete question. The main session
keeps its binding and the latest capsule; it does not maintain competing handoff files
or replay the full history after each input. Older detailed context commands remain
available for targeted investigation, especially inside worker assignments.

User steering is persisted with `orchestration feedback` and an expected control
version. Immutable feedback IDs make identical retries idempotent; new records reject
stale versions. The record keeps the original input reference, scope and any explicitly
superseded feedback. Affected workers receive the current feedback revision and that
revision is checked before applying their proposals. `rules/context.md` defines the
feedback fields and recovery steps.

Pause/resume controls apply to the identified task; a progress question is not a
resume. A scope change blocks the old task and requires a new goal, without editing
its frozen goal or acceptance. Peer messages and historical summaries cannot create
user authority. Durable capsules reduce the material loaded by the main session;
they do not guarantee a host compaction frequency or improved live-model outcomes.

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

## Detailed context behind the capsule

For targeted investigation, `context` reconstructs target, original gap analysis,
rule paths, source inventory, build manifest excerpts, test paths, milestone progress,
latest gate and accumulated
discoveries. Its `next` action routes the active conversation to clarification,
implementation, final verification, recovery or reporting.

The main session starts from the compact orchestration capsule, then uses a specific
reference when an assignment or evidence check needs more detail. It
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
goal records. Goal `context` embeds this detailed view for targeted reads; it is not a
default main-session prompt. Decisions and execution notes are archived automatically;
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

## Harness outcome evaluation

Benchmarking is separate from the harness's own completion gates. `scripts/benchmark.py`
invokes `bench check`: freeze configuration, run, judge, gate and produce improvements.
`bench_check.py` owns this sequence and records an inconclusive result when a phase fails;
an unjudged run does not stop the sequence. `bench plan/run`
freezes a suite and current harness file snapshot; every case/provider/
repetition runs plain and candidate in seeded random order. No baseline commit is selected
or read. The source snapshot includes uncommitted changes and is hashed automatically. Inputs, model
profiles and budgets match. Trial-local CLI wrappers enforce model IDs and efforts and reserve
a shared call budget for the conversation and its nested workers/reviewers. Existing
host policy and authentication remain in effect. No SDK or global hook is installed.
The suite must contain at least two distinct goal types. Session reuse is a scenario
dimension; the starter session case counts as explanation, alongside development and document.

Each trial gets fresh repositories, knowledge and an explicitly identified provider
conversation. Later turns resume that conversation, including directory transitions.
The first harness turn invokes its installed skill; later turns are ordinary prompts.
Activation checks reject missing bindings or a contaminated plain control. Installation
and graders live outside the timed scenario. Timeout cleanup targets the launched
process group and observed descendants, including nested groups; it is not an OS sandbox.

After execution, an external checker runs on a separate artifact copy. A random-ID
packet contains only requests, answers, declared fixture source context/changes, deliverables and anchored rubric
criteria. A fixed read-only judge independently assesses each packet at least twice.
The full check freezes judge provider/model/effort/repetitions before running any trial;
later assessments must match that profile. Both vendors' results use the same judge.
Disagreements use the lower score. Human assessments use the same packet-bound schema.
Treatment inference from prose and model judging errors remain possible.

Reports compare each candidate to its plain counterpart and retain separate quality, user
intervention, duration and available usage evidence. Mandatory outcomes, a quality
floor, paired losses and excess overhead block promotion regardless of average gain.
Incomplete execution/evidence, failed controls and fake-CLI runs cannot establish an
improvement. `bench gate` rechecks hashes of artifacts, logs, candidate and suite.
Bootstrap intervals describe the selected paired observations, not unseen tasks.
`bench_improvements.py` deterministically groups repeated failures, reports each goal
type's coverage and comparison, and ranks setup/evidence, quality/intervention and
duration issues. Observations, causal hypotheses, related rules, bounded next changes
and revalidation remain distinct JSON fields. The human view shows at most five items;
the full JSON preserves evidence without additional model calls. No rule change is
invented for equal valid results, and a failed final gate cannot leave a passing check summary.

`bench_evolution.py` keeps automatic before/after baselines outside the source and suite.
Before changing the harness, the orchestrator establishes a passing live measurement
of the starting source if one is unavailable. Each full check selects the last passing
run with identical suite hash, models/efforts, judge settings, providers, repeats,
seed, budgets, host configuration and CLI versions. A new profile starts a separate
series and cannot establish improvement over a different profile.
The after run still executes only plain/candidate; the before run's sealed evidence
is reused, without another arm or Git revision lookup. Reports pair case/provider/
repeat outcomes across runs and show quality, elapsed time, calls, extra questions
and the change in on/off benefit. Any criterion loss, additional questions or duration
limit violation blocks advancement. Improvement requires positive descriptive intervals
for both candidate quality and the change in on/off benefit, with a changed source.
Ties are non-regressing, never improved. Same-source reruns do not replace a baseline.

`evolution.json` freezes the predecessor and its manifest, index, judgments, final check
and comparison seals. `bench gate` revalidates that evidence as well as the current
source/suite; removing a temporal comparison cannot restore an on/off-only pass.
`baselines.json` stores one accepted pointer per profile. A short file lock and comparison
with the pointer observed before execution prevent concurrent runs from silently replacing
each other. All reports and final checks are written before advancing the pointer.
Regression, missing evidence and simulations retain the old pointer. Old run directories
remain required evidence. The deterministic improvement report includes both comparisons;
no extra model calls, rule rewriting, merge or deployment follow from history updates.

`bench proposal` stores a bounded rule hypothesis with affected cases, their scores,
regressions and frozen source/suite hashes. It does not edit rules automatically.
The orchestrator delegates a scoped change and reruns both conditions, calibration
and holdout cases under unchanged criteria. Public starter holdouts are regression
examples; broader claims need representative independently held tasks. Unit CI does
not run paid providers or substitute for this live quality gate. See `rules/benchmark.md`.

Version 2 manifests explicitly fix `arms` to `plain` and `candidate`. Historical
version 1 reports keep their original three conditions when read; a missing historical
reference cannot be silently treated as a complete two-condition experiment.

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
