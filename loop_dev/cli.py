from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import providers
from .contract import validate
from .engine import Engine
from .storage import LoopError, locked, repository


def parser():
    result = argparse.ArgumentParser(description="loop-dev: conversation-led development gates")
    result.add_argument("--root", type=Path, default=Path.cwd(), help="Target repository root")
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a draft goal, without implementing code")
    init.add_argument("goal")
    init.add_argument("--title", required=True)
    init.add_argument("--implementer", choices=("claude", "codex"), required=True)
    for name in ("context", "status", "validate", "doctor", "plan-review", "freeze", "start", "finish", "resume"):
        commands.add_parser(name).add_argument("goal")
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("goal")
    checkpoint.add_argument("--milestone", required=True)
    for name in ("block", "abort"):
        sub = commands.add_parser(name)
        sub.add_argument("goal")
        sub.add_argument("--reason", required=True)
    note = commands.add_parser("note")
    note.add_argument("goal")
    note.add_argument("--kind", choices=("discovery", "decision", "replan"), required=True)
    note.add_argument("--text", required=True)
    note.add_argument("--evidence", required=True)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        root = repository(args.root)
        with locked(root):
            engine = Engine(root, args.goal)
            cmd = args.command
            if cmd == "init":
                output = engine.initialize(args.title, args.implementer)
            elif cmd in ("status", "context"):
                output = engine.refresh()
            elif cmd == "validate":
                _, contract, discovery = engine.load()
                errors = validate(contract, discovery, root)
                output = {"passed": not errors, "errors": errors}
            elif cmd == "doctor":
                engine.load()
                output = providers.doctor(root, engine.state_dir / "doctor")
                output["passed"] = output["ok"]
            elif cmd == "plan-review":
                output = engine.plan_review()
            elif cmd == "checkpoint":
                output = engine.gate(args.milestone)
            elif cmd == "finish":
                output = engine.gate()
            elif cmd in ("block", "abort"):
                output = getattr(engine, cmd)(args.reason)
            elif cmd == "note":
                output = engine.note(args.kind, args.text, args.evidence)
            else:
                output = getattr(engine, cmd)()
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output.get("passed", True) else 1
    except (LoopError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; inspect status and logs before resuming.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
