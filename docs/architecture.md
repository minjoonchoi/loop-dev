# Architecture

## Core decision

The user's entry point is a Claude Code or Codex conversation. The active model owns
discovery, clarification, requirements analysis, implementation, root-cause analysis
and tactical replanning. Portable scripts own deterministic gates and durable state.
The opposite CLI provides independent reviews. There is no provider SDK, nested
same-provider implementer, background daemon or vendor-specific orchestrator.

This division makes the same workflow available in both environments while keeping
the user's responsibility to the goal and genuinely human-owned decisions. Calling
the CLI is an internal agent action, not a sequence of prompts the user must provide.

| Responsibility | Implementation |
| --- | --- |
| Shared behavioral policy | `rules/workflow.md`, `context.md`, `quality.md` |
| Provider entry | Managed blocks in AGENTS.md and CLAUDE.md |
| Goal completeness | `contract.py` validates structural traceability and decisions |
| Context reconstruction | `context.py` derives repository inventory, runtime evidence and next action |
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

## State machine

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
