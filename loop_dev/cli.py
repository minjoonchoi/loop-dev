from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import sqlite3
import uuid

from . import providers
from .contract import validate
from .engine import Engine
from .harness import Harness
from .session import Session
from .knowledge import KnowledgeBase, project_root
from .work import KINDS, Work
from .storage import LoopError, locked, read_json, repository


def parser():
    result = argparse.ArgumentParser(description="loop-dev: goal setting, work and reusable local knowledge")
    result.add_argument("--root", type=Path, default=Path.cwd(), help="Target repository root")
    result.add_argument("--knowledge-home", type=Path, help="Local knowledge directory (default: LOOP_KB_HOME or ~/.local/share/loop-dev)")
    commands = result.add_subparsers(dest="command", required=True)
    bench = commands.add_parser("bench", help="Compare plain, reference and candidate harness outcomes")
    bench_commands = bench.add_subparsers(dest="bench_command", required=True)
    source = Path(__file__).resolve().parent.parent
    for name in ("plan", "run", "check"):
        sub = bench_commands.add_parser(name)
        sub.add_argument("--suite", type=Path, required=name != "check", default=source / "benchmarks/suite.json")
        sub.add_argument("--candidate", type=Path, required=name != "check", default=source)
        sub.add_argument("--reference-ref", required=name != "check", default="HEAD", help="Existing Git commit in the candidate repository")
        sub.add_argument("--codex-model", required=True)
        sub.add_argument("--claude-model", required=True)
        sub.add_argument("--codex-effort", required=True)
        sub.add_argument("--claude-effort", required=True)
        sub.add_argument("--provider", choices=("claude", "codex"), action="append", required=name != "check")
        sub.add_argument("--repeats", type=int, default=3)
        sub.add_argument("--seed", type=int, default=0)
        sub.add_argument("--environment", required=name != "check", help="Fixed host, credentials and configuration profile description")
        if name in ("run", "check"):
            sub.add_argument("--output", type=Path, required=name == "run")
        if name == "check":
            sub.add_argument("--judge-provider", choices=("claude", "codex"), default="codex")
            sub.add_argument("--judge-model", help="Defaults to the chosen judge provider's model")
            sub.add_argument("--judge-effort", help="Defaults to the chosen judge provider's effort")
            sub.add_argument("--judge-repeats", type=int, default=2)
            sub.add_argument("--dry-run", action="store_true", help="Validate and show the frozen comparison without model calls")
    for name in ("report", "gate", "judge", "proposal"):
        sub = bench_commands.add_parser(name)
        sub.add_argument("run", type=Path)
        if name in ("report", "gate"):
            sub.add_argument("--judgments", type=Path)
        if name == "gate":
            sub.add_argument("--candidate", type=Path, required=True)
            sub.add_argument("--suite", type=Path, required=True)
        if name == "judge":
            sub.add_argument("--provider", choices=("claude", "codex"), required=True)
            sub.add_argument("--model", required=True)
            sub.add_argument("--effort", required=True)
            sub.add_argument("--repeats", type=int, default=2)
        if name == "proposal":
            sub.add_argument("--input", type=Path, required=True)
    harness = commands.add_parser("harness", help="Enable or disable the harness for a known conversation")
    harness_commands = harness.add_subparsers(dest="harness_command", required=True)
    enabled = harness_commands.add_parser("on")
    enabled.add_argument("--provider", choices=("claude", "codex"), required=True)
    enabled.add_argument("--session", help="Current conversation's bound ID; omit only for a new conversation")
    for name in ("status", "off"):
        harness_commands.add_parser(name).add_argument("session_id")
    init = commands.add_parser("init", help="Create a draft goal, without implementing code")
    init.add_argument("goal")
    init.add_argument("--title", required=True)
    init.add_argument("--implementer", choices=("claude", "codex"), required=True)
    init.add_argument("--orchestrator", choices=("claude", "codex"), help="Conversation provider; defaults to implementer")
    init.add_argument("--session", help="Reuse the current conversation's session ID for the next goal")
    session = commands.add_parser("session", help="Persist and reuse analysis in the current conversation")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    session_commands.add_parser("open").add_argument("--provider", choices=("claude", "codex"), required=True)
    session_context = session_commands.add_parser("context")
    session_context.add_argument("session_id")
    session_context.add_argument("--goal")
    remember = session_commands.add_parser("remember")
    remember.add_argument("session_id")
    remember.add_argument("--input", type=Path, required=True)
    remember.add_argument("--observed", required=True, help="Revision returned by the context read before analysis")
    for name in ("context", "status", "validate", "doctor", "plan-review", "freeze", "start", "finish", "resume"):
        commands.add_parser(name).add_argument("goal")
    checkpoint = commands.add_parser("checkpoint")
    checkpoint.add_argument("goal")
    checkpoint.add_argument("--milestone", required=True)
    delegate = commands.add_parser("delegate", help="Execute one explicit bounded worker assignment")
    delegate.add_argument("goal")
    delegate.add_argument("--input", type=Path, required=True)
    delegate.add_argument("--recover", help="Reason for retrying an inspected interrupted worker")
    for name in ("block", "abort"):
        sub = commands.add_parser(name)
        sub.add_argument("goal")
        sub.add_argument("--reason", required=True)
    note = commands.add_parser("note")
    note.add_argument("goal")
    note.add_argument("--kind", choices=("discovery", "decision", "replan"), required=True)
    note.add_argument("--text", required=True)
    note.add_argument("--evidence", required=True)
    work = commands.add_parser("work", help="Goal setting and work without a mandatory development loop")
    work_commands = work.add_subparsers(dest="work_command", required=True)
    opened = work_commands.add_parser("open")
    opened.add_argument("--kind", choices=KINDS, required=True)
    opened.add_argument("--title", required=True)
    opened.add_argument("--provider", choices=("claude", "codex"), required=True)
    opened.add_argument("--worker", choices=("claude", "codex"), help="Worker provider; defaults to conversation provider")
    opened.add_argument("--path", type=Path, action="append")
    opened.add_argument("--session")
    delegated = work_commands.add_parser("delegate")
    delegated.add_argument("task")
    delegated.add_argument("--input", type=Path, required=True)
    delegated.add_argument("--path", type=Path)
    delegated.add_argument("--recover")
    for name in ("context", "start", "resume"):
        work_commands.add_parser(name).add_argument("task")
    for name in ("goal", "finish"):
        sub = work_commands.add_parser(name)
        sub.add_argument("task")
        sub.add_argument("--input", type=Path, required=True)
    for name in ("block", "abort"):
        sub = work_commands.add_parser(name)
        sub.add_argument("task")
        sub.add_argument("--reason", required=True)
    note_work = work_commands.add_parser("note")
    note_work.add_argument("task")
    note_work.add_argument("--text", required=True)
    note_work.add_argument("--evidence", required=True)
    knowledge = commands.add_parser("knowledge", help="Project registry, supported facts and historical work")
    knowledge_commands = knowledge.add_subparsers(dest="knowledge_command", required=True)
    knowledge_commands.add_parser("projects")
    attach = knowledge_commands.add_parser("attach")
    attach.add_argument("--path", type=Path)
    attach.add_argument("--project")
    for name in ("context", "remember"):
        sub = knowledge_commands.add_parser(name)
        sub.add_argument("--path", type=Path, action="append")
        sub.add_argument("--session")
        sub.add_argument("--task")
        if name == "context":
            sub.add_argument("--query")
            sub.add_argument("--limit", type=int, default=20)
            sub.add_argument("--offset", type=int, default=0)
        else:
            sub.add_argument("--input", type=Path, required=True)
            sub.add_argument("--observed", required=True)
    history = knowledge_commands.add_parser("history")
    history.add_argument("kind", choices=("topic", "task", "delegation"))
    history.add_argument("id")
    get = knowledge_commands.add_parser("get")
    get.add_argument("kind", choices=("topic", "task", "delegation"))
    get.add_argument("id")
    return result


def common_command(args, knowledge):
    if args.command == "knowledge":
        cmd = args.knowledge_command
        if cmd == "projects":
            return {"projects": knowledge.list("project"), "locations": knowledge.list("location")}
        if cmd == "attach":
            return knowledge.attach(args.path or args.root, args.project)
        if cmd == "history":
            return {"current": knowledge.get(args.kind, args.id), "history": knowledge.history(args.kind, args.id)}
        if cmd == "get":
            value = knowledge.get(args.kind, args.id)
            knowledge.document(args.kind, args.id)
            return {"record": value, "document": str(knowledge.home / "views" / args.kind / (args.id + ".md"))}
        locations = [knowledge.attach(path) for path in (args.path or [args.root])]
        if cmd == "context":
            return knowledge.context(locations, args.session, args.task, args.query, args.limit, args.offset)
        return knowledge.remember(read_json(args.input), locations, args.observed, args.session, args.task)
    work = Work(knowledge)
    cmd = args.work_command
    if cmd == "open":
        paths = args.path or [args.root]
        if args.kind == "development":
            if len(paths) != 1:
                raise LoopError("Development gates run per working tree; create a goal per repository in the same session")
            root = repository(project_root(paths[0]))
            with locked(root):
                return Engine(root, "goal-" + uuid.uuid4().hex[:12], knowledge).initialize(
                    args.title, args.worker or args.provider, args.session, args.provider)
        return work.open(args.kind, args.title, args.provider, paths, args.session, args.worker)
    if cmd == "delegate":
        return work.delegate(args.task, read_json(args.input), args.path, args.recover)
    if cmd == "goal":
        return work.define(args.task, read_json(args.input))
    if cmd == "finish":
        return work.finish(args.task, read_json(args.input))
    if cmd in ("block", "abort"):
        return work.stop(args.task, args.reason, abort=cmd == "abort")
    if cmd == "note":
        return work.note(args.task, args.text, args.evidence)
    return getattr(work, cmd)(args.task)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "bench":
            from . import benchmark, bench_report
            cmd = args.bench_command
            if cmd == "check":
                from .bench_check import check
                output = check({"codex": args.codex_model, "claude": args.claude_model},
                    {"codex": args.codex_effort, "claude": args.claude_effort},
                    candidate=args.candidate, suite=args.suite, reference_ref=args.reference_ref,
                    providers=args.provider, repeats=args.repeats, seed=args.seed, environment=args.environment,
                    output=args.output, judge_provider=args.judge_provider, judge_model=args.judge_model,
                    judge_effort=args.judge_effort, judge_repeats=args.judge_repeats, dry_run=args.dry_run)
            elif cmd in ("plan", "run"):
                values = [args.suite, args.candidate, args.reference_ref,
                          {"codex": args.codex_model, "claude": args.claude_model},
                          args.provider, args.repeats, args.seed, args.environment]
                efforts = {"codex": args.codex_effort, "claude": args.claude_effort}
                output = benchmark.prepare(*values, efforts=efforts) if cmd == "plan" else benchmark.run(*values, args.output, efforts=efforts)
                if cmd == "plan":
                    output["source"] = {k: {p: v[p] for p in ("hash", "ref")} for k, v in output["source"].items()}
            elif cmd == "judge":
                output = bench_report.judge(args.run, args.provider, args.model, args.repeats, effort=args.effort)
            elif cmd == "gate":
                output = bench_report.gate(args.run, args.candidate, args.suite, args.judgments)
            elif cmd == "proposal":
                output = bench_report.proposal(args.run, args.input)
            else:
                output = bench_report.report(args.run, args.judgments)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 3 if output.get("verdict") == "inconclusive" else 1 if output.get("passed") is False else 0
        knowledge = KnowledgeBase(args.knowledge_home)
        if args.command == "harness":
            harness = Harness(knowledge)
            output = (harness.on(args.provider, args.session) if args.harness_command == "on"
                      else getattr(harness, args.harness_command)(args.session_id))
            base = Path(__file__).resolve().parent.parent
            output["entrypoint"] = str(base / ("loop.py" if (base / "loop.py").is_file() else "scripts/loop.py"))
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        if args.command in ("work", "knowledge"):
            output = common_command(args, knowledge)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0 if output.get("passed", True) else 1
        root = repository(project_root(args.root))
        with locked(root):
            cmd = args.command
            if cmd == "session":
                if args.session_command == "open":
                    output = Session.create(root, args.provider, knowledge).refresh()
                else:
                    session = Session(root, args.session_id, knowledge)
                    if args.session_command == "context":
                        output = session.refresh(args.goal)
                    else:
                        output = session.remember(read_json(args.input), args.observed)
                print(json.dumps(output, ensure_ascii=False, indent=2))
                return 0
            engine = Engine(root, args.goal, knowledge)
            if cmd == "init":
                output = engine.initialize(args.title, args.implementer, args.session, args.orchestrator)
            elif cmd == "delegate":
                output = engine.delegate(read_json(args.input), args.recover)
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
    except (LoopError, OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; inspect status and logs before resuming.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
