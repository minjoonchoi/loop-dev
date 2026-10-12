# Architecture

## Core decision

The user's entry point is a Claude Code or Codex conversation. The active model owns
discovery, clarification, requirements analysis, implementation, root-cause analysis
and tactical replanning. Portable scripts own deterministic gates and durable state.
The opposite CLI provides independent development reviews. Other work has an
evidence-backed content/completion check by the active agent, without a mandatory loop.
There is no provider SDK, nested
same-provider implementer, background daemon or vendor-specific orchestrator.

This division makes the same workflow available in both environments while keeping
the user's responsibility to the goal and genuinely human-owned decisions. Calling
the CLI is an internal agent action, not a sequence of prompts the user must provide.

Installed AGENTS.md and CLAUDE.md make goal setting -> work the default for ordinary
requests. A desired outcome is sufficient: the user does not need to name the harness,
invoke a slash command or explain investigation, clarification, review or continuation.
The entry rules in `work.md` distinguish development from questions, read-only review and explicit
planning-only requests. Follow-ups reuse persisted goal context instead of starting
duplicate goals or asking the user to reconstruct it. This routing is an instruction
to the conversation model, not a keyword parser or provider event hook.

| Responsibility | Implementation |
| --- | --- |
| Shared behavioral policy | `rules/workflow.md`, `context.md`, `quality.md` |
| Common goal setting and work | `work.py`, `rules/work.md`; development delegates to existing gates |
| Cross-project knowledge | `knowledge.py`; local SQLite, scoped records and dependency checks |
| Readable records and derived views | `writing.py`, `rules/writing.md`; deterministic Markdown, concise authored fields |
| Provider entry | Managed blocks in AGENTS.md and CLAUDE.md |
| Goal completeness | `contract.py` validates structural traceability and decisions |
| Context reconstruction | `context.py` derives repository inventory, runtime evidence and next action |
| Reusable conversation analysis | `session.py` stores topics, decisions, dependency fingerprints and goal history |
| State/gates/recovery | `engine.py` |
| Independent review | `providers.py` invokes the opposite provider and validates structured output |
| Bounded subprocesses | `process.py`, argv arrays, stdin EOF, process-group timeout |
| Freshness/concurrency | `storage.py`, hashes, atomic JSON, POSIX flock |

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

The agent reads the relevant source and tests using this index. It logs new findings
with evidence, adjusts tactics within the frozen target, and passes the accumulated
notes to the next independent review. Review failures become repair instructions, and
test failures retain exact commands and log paths. This replaces the user's serial
context-feeding with an evidence-driven agent loop.

Notes are append-only through the CLI and can change tactics, not the frozen scope,
commands, business rules or authority. A discovery that truly requires a new business
decision becomes a blocker and a separate preflight goal rather than a mid-run question.

## Analysis reused within a conversation

A local session spans multiple goals in the same Claude Code or Codex conversation.
`init` creates a session when none is supplied; subsequent goals pass `--session`.
An optional `session open` supports analysis before the first goal. The agent carries
the returned ID in conversation handoffs and goal state. New conversations use new
IDs; the engine never selects the latest cache as a proxy for session ownership.
Explicitly resuming a goal restores its binding. Providers must match the binding.
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

| Implementer conversation | Read-only reviewer |
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
side effects. The active conversation performs the user's pre-authorized delivery
actions and reports their actual outcome. Core `completed` means verified development;
it does not itself assert that a remote deployment or merge occurred.
