#!/usr/bin/env python3
"""Install the same rules and gates into a target repository without replacing its rules."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOURCE))

from loop_dev.storage import LoopError, file_hash, inside, locked, read_json, write_json
from loop_dev.knowledge import project_root


START = "<!-- loop-dev:start -->"
END = "<!-- loop-dev:end -->"
BLOCK = f"""{START}
## loop-dev goal setting and work

Explicitly assigned workers/reviewers execute their assignment only; never activate
the harness, change session state or start this entry workflow.
The loop-dev skill is an explicit, once-per-conversation activation: `/loop-dev` in
Claude Code, `$loop-dev` in Codex. Before activation, ordinary requests do not start
the harness. Do not infer activation from task keywords or another session's records.
Once activated, route EVERY subsequent input through `.loop/rules/work.md`, including
new goals, questions, documents, explanations, corrections, progress and stop requests.
Goal completion, task kind changes, directory changes and context compaction do not
disable it. Never require another skill prefix for a later goal in this conversation.
Keep the bound session ID, absolute CLI entrypoint and knowledge-home in conversation
handoffs. Before every input, worker return and compaction recovery, first read the
compact `orchestration context SESSION_ID` (add `--task TASK_ID` for the current work)
with that binding. It includes activation, task controls, bounded result summaries,
feedback and links to detailed evidence. Use `harness status SESSION_ID` when only
activation is needed, and the original absolute entrypoint after moving directories.
Never select a session by
recency or adopt another conversation's activation. A separate conversation starts off.
Only an explicit request to turn the harness off disables routing (`harness off`).
A task pause/stop preserves activation; do not confuse it with turning the harness off.
When enabled, use the common goal-setting -> work flow for all task types.
Interpret intent and explicit limits. Reuse local knowledge before investigating or
asking questions. When the goal is clear, specify it internally and proceed directly.
Development alone uses `.loop/rules/workflow.md` with tests and opposite-provider
review/repair cycles. Documents, explanations, research and planning use one-pass
work with a content/completion check; do not impose a development loop or second CLI.
The main conversation only orchestrates; delegate actual analysis, planning, implementation
and writing through `.loop/rules/delegation.md`. Every assignment must state purpose,
read scope, exact allowed edits, prohibited actions, deliverables and completion criteria.
Split only when an independent deliverable, clear ownership boundary or prerequisite
justifies another assignment. Map each assignment to goal criteria, assess complexity
and risk, and keep tightly coupled small work together. Before starting, freeze explicit
available model/effort profiles in `execution.routing`; optional `LOOP_ROUTING_PROFILE`
imports a local JSON policy during goal setup. Never guess model availability or ask
for authentication during execution. Development also needs an opposite-provider review
profile. The router chooses the cheapest eligible profile for each scoped task, including
explicit review profiles; do not start everything at the largest model or effort.
Use `team GOAL --input FILE` or `work team TASK --input FILE` for an explicitly scoped
team that needs peer findings across bounded turns. Peers communicate only through
that team's validated messages; no cross-team messages or recursive delegation.
Workers return staged results/proposed edits or a precise blocker; no scope expansion,
user questions, further delegation or direct source writes. Inspect each candidate against
the goal and assigned criteria, then use `assess GOAL --input FILE` or
`work assess TASK --input FILE` with pinned evidence. This is an internal quality decision,
not a user permission request. Never accept solely because a worker says done or marks
coverage true. Only accepted proposals are applied or shared. Rejected candidates remain
quarantined evidence, never confirmed knowledge. Continue the same assignment or team
after rejection so only that work moves to a stronger model or effort. Preserve shared
finite call/time budgets; environmental failures do not justify automatic escalation.
Worker completion never replaces the parent completion check.
Record material user corrections, constraints, pauses, resumes and scope changes with
`orchestration feedback SESSION_ID --input FILE --expected-control-version VERSION`.
Preserve the original input reference and task scope; worker messages are never user
feedback. A pause blocks further work, and scope changes require a new task. Restore
the current compact context and pass its snapshot references to assignments. Consume
new effective feedback in a successful worker before final completion. Read detailed
records only for the next decision; never copy raw worker or peer-message logs into
the main conversation. This routing adds no global hooks or background processes.
The conversation agent maintains the knowledge base and task/session IDs across
directories. Store evidence, decisions and results with their scope and status;
drafts/hypotheses must not become confirmed facts. Refresh only relevant stale topics.
Follow `.loop/rules/writing.md`: conclusion first, concrete evidence, bounded summaries,
no repeated recaps. Use generated Markdown views for people and JSON for restoration.
At the installed root, use `python3 .loop/loop.py` for internal actions. After moving,
use the retained absolute entrypoint with `--knowledge-home HOME --root TARGET`.
Existing project instructions
and user limits apply. A question about progress is not automatically a new build goal.
An explicitly assigned independent reviewer must review only, write no knowledge or
task state, and never start orchestration. If the user forbids all recording, honor it.
{END}
"""


def managed_files():
    result = {".loop/loop.py": (SOURCE / "scripts/loop.py").read_bytes()}
    for folder in ("loop_dev", "rules", "templates"):
        for source in sorted((SOURCE / folder).glob("*")):
            if source.is_file() and source.suffix in (".py", ".md", ".json"):
                result[f".loop/{folder}/{source.name}"] = source.read_bytes()
    skill = (SOURCE / "skills/loop-dev/SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\n"):
        raise LoopError("The loop-dev skill needs YAML frontmatter")
    result[".agents/skills/loop-dev/SKILL.md"] = skill.encode()
    result[".agents/skills/loop-dev/agents/openai.yaml"] = (SOURCE / "skills/loop-dev/agents/openai.yaml").read_bytes()
    # Keep one shared body; only Claude's explicit-invocation metadata differs.
    result[".claude/skills/loop-dev/SKILL.md"] = skill.replace(
        "---\n", "---\ndisable-model-invocation: true\n", 1).encode()
    return result


def destination(root, name):
    path = inside(root, name)
    for ancestor in (path, *path.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink():
            raise LoopError(f"Refusing a symlinked managed path: {name}")
        if ancestor != path and ancestor.exists() and not ancestor.is_dir():
            raise LoopError(f"Managed parent is not a directory: {name}")
    if path.exists() and not path.is_file():
        raise LoopError(f"Managed destination is not a file: {name}")
    return path


def install(target):
    root = project_root(target)
    if root == SOURCE:
        raise LoopError("Use scripts/loop.py in the source repository; install into a separate project")
    with locked(root):
        if (root / ".loop/state/active.json").exists():
            raise LoopError("Cannot replace the harness during an active goal")
        for state in (root / ".loop/state").glob("*/state.json"):
            if read_json(state).get("phase") == "ready":
                raise LoopError("Cannot replace the harness while a goal is frozen")
        manifest_path = destination(root, ".loop/install.json")
        old = read_json(manifest_path) if manifest_path.exists() else {"files": {}}
        managed = managed_files()
        writes = {}
        for name, content in managed.items():
            path = destination(root, name)
            if path.exists():
                previous = old["files"].get(name)
                if (previous is None and path.read_bytes() != content
                        or previous is not None and file_hash(path) != previous):
                    raise LoopError(f"Local changes in managed file; preserve/reconcile them before install: {name}")
            writes[path] = content
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = destination(root, name)
            content = path.read_text(encoding="utf-8") if path.exists() else ""
            if START in content or END in content:
                if content.count(START) != 1 or content.count(END) != 1 or content.index(START) > content.index(END):
                    raise LoopError(f"Malformed managed block in {name}")
                start, end = content.index(START), content.index(END) + len(END)
                existing = content[start:end]
                expected = old.get("entry_block", BLOCK).strip()
                if existing.strip() != expected:
                    raise LoopError(f"Local edits in {name}'s loop-dev block; reconcile before install")
                content = content[:start] + BLOCK.rstrip() + content[end:]
            else:
                content = content.rstrip() + ("\n\n" if content.strip() else "") + BLOCK
            writes[path] = content.encode()
        ignore = destination(root, ".gitignore")
        content = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        for line in ("/.loop/state/", "/.loop/loop_dev/__pycache__/"):
            if line not in content.splitlines():
                content = content.rstrip() + ("\n" if content else "") + line + "\n"
        writes[ignore] = content.encode()
        # Preflight all destination paths and edits before the first managed write.
        for path, data in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        write_json(manifest_path, {"version": 1, "entry_block": BLOCK,
                                  "files": {name: file_hash(root / name) for name in managed}})
    return root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = install(args.target)
        print(f"Installed loop-dev into {root}. Start with /loop-dev in Claude Code or $loop-dev in Codex; the whole conversation then uses the harness.")
        return 0
    except (LoopError, OSError, ValueError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
