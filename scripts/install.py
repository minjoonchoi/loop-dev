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

Use the common goal-setting -> work flow for development, documents, explanations,
research and planning. Read `.loop/rules/work.md` first. The ordinary request is enough;
do not require a keyword, slash command, extra operating prompt or redundant approval.
Interpret intent and explicit limits. Reuse local knowledge before investigating or
asking questions. When the goal is clear, specify it internally and proceed directly.
Development alone uses `.loop/rules/workflow.md` with tests and opposite-provider
review/repair cycles. Documents, explanations, research and planning use one-pass
work with a content/completion check; do not impose a development loop or second CLI.
The conversation agent maintains the knowledge base and task/session IDs across
directories. Store evidence, decisions and results with their scope and status;
drafts/hypotheses must not become confirmed facts. Refresh only relevant stale topics.
Follow `.loop/rules/writing.md`: conclusion first, concrete evidence, bounded summaries,
no repeated recaps. Use generated Markdown views for people and JSON for restoration.
Use `python3 .loop/loop.py` for the internal actions. Existing project instructions
and user limits apply. A question about progress is not automatically a new build goal.
An explicitly assigned independent reviewer must review only, write no knowledge or
task state, and never start orchestration. If the user forbids all recording, honor it.
{END}
"""


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
        manifest_path = inside(root, ".loop/install.json")
        old = read_json(manifest_path) if manifest_path.exists() else {"files": {}}
        managed = {".loop/loop.py": SOURCE / "scripts/loop.py"}
        for folder in ("loop_dev", "rules", "templates"):
            for source in sorted((SOURCE / folder).glob("*")):
                if source.is_file() and source.suffix in (".py", ".md", ".json"):
                    managed[f".loop/{folder}/{source.name}"] = source
        writes = {}
        for name, source in managed.items():
            destination = inside(root, name)
            if destination.exists():
                if destination.is_symlink():
                    raise LoopError(f"Refusing a symlinked managed file: {name}")
                previous = old["files"].get(name)
                if (previous is None and destination.read_bytes() != source.read_bytes()
                        or previous is not None and file_hash(destination) != previous):
                    raise LoopError(f"Local changes in managed file; preserve/reconcile them before install: {name}")
            writes[destination] = source.read_bytes()
        for name in ("AGENTS.md", "CLAUDE.md"):
            path = inside(root, name)
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
        ignore = inside(root, ".gitignore")
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
        print(f"Installed loop-dev into {root}. Open Claude Code or Codex there and describe your goal.")
        return 0
    except (LoopError, OSError, ValueError) as exc:
        print(f"Install failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
