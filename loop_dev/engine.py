"""Persistent gates for a conversation-led implementation loop."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import uuid

from . import context, providers
from .contract import identifier, template, validate
from .process import execute
from .session import Session
from .knowledge import KnowledgeBase
from .writing import concise, task_document, task_summary
from .storage import (LoopError, digest, engine_digest, head, inside, now, read_json,
                      rule_manifest, snapshot, write_json, write_text)


class Engine:
    def __init__(self, root, goal_id, knowledge=None):
        if not identifier(goal_id) or goal_id == "sessions":
            raise LoopError("Invalid goal ID; use letters, digits, dashes or underscores")
        self.root = root
        self.id = goal_id
        self.goal_dir = inside(root, ".loop/goals/" + goal_id)
        self.state_dir = inside(root, ".loop/state/" + goal_id)
        self.state_path = self.state_dir / "state.json"
        self.active_path = root / ".loop/state/active.json"
        self.knowledge = knowledge or KnowledgeBase()

    def initialize(self, title, implementer, session_id=None, orchestrator=None):
        concise(title, "Goal title", 120)
        orchestrator = orchestrator or implementer
        if self.goal_dir.exists() or self.state_dir.exists():
            raise LoopError(f"Goal already exists: {self.id}")
        session = Session(self.root, session_id, self.knowledge) if session_id else Session.create(self.root, orchestrator, self.knowledge)
        session.load(orchestrator)
        self.goal_dir.mkdir(parents=True)
        contract = template(self.id, title, implementer)
        contract["execution"]["orchestrator"] = orchestrator
        for name in (".loop/rules/workflow.md", "rules/work.md", "rules/writing.md", "rules/workflow.md", "rules/delegation.md",
                     "rules/context.md", "rules/quality.md", "AGENTS.md", "CLAUDE.md"):
            if (self.root / name).is_file():
                contract["rules"].append(name)
        write_json(self.goal_dir / "contract.json", contract)
        write_json(self.goal_dir / "discovery.json", {
            "baseline_head": head(self.root), "findings": [], "gaps": []})
        state = {"version": 1, "id": self.id, "session_id": session.id, "phase": "draft", "created_at": now(),
                 "milestones": [], "attempts": {}, "notes": [], "latest_gate": None}
        self.save(state, "initialized")
        return self.refresh()

    def load(self):
        state = read_json(self.state_path)
        contract = read_json(self.goal_dir / "contract.json")
        discovery = read_json(self.goal_dir / "discovery.json")
        if not isinstance(state, dict) or state.get("id") != self.id:
            raise LoopError("Invalid goal state")
        if not isinstance(contract, dict) or not isinstance(discovery, dict):
            raise LoopError("Contract and discovery must be JSON objects")
        return state, contract, discovery

    def save(self, state, event):
        state["updated_at"] = now()
        write_json(self.state_path, state)
        # One atomic file per event avoids a partially appended JSONL record after a crash.
        write_json(self.state_dir / "events" / f"{uuid.uuid4().hex}.json",
                   {"at": state["updated_at"], "event": event, "phase": state["phase"],
                    "latest_gate": state.get("latest_gate")})

    def inputs(self, contract, discovery):
        return {"contract": digest(contract), "discovery": digest(discovery),
                "rules": rule_manifest(self.root, contract["rules"]), "engine": engine_digest()}

    def check_valid(self, contract, discovery):
        errors = validate(contract, discovery, self.root)
        if not isinstance(contract, dict) or contract.get("id") != self.id:
            errors.append("contract.id must match the goal directory")
        if errors:
            raise LoopError("Not implementation-ready:\n- " + "\n- ".join(errors))

    def integrity(self, state, contract, discovery):
        if "frozen_inputs" not in state:
            raise LoopError("Goal is not frozen")
        frozen = state["frozen_inputs"]
        if (digest(contract) != frozen["contract"] or digest(discovery) != frozen["discovery"]
                or self.inputs(contract, discovery) != frozen):
            raise LoopError("Frozen goal, discovery, rules, or engine changed. Restore them or start a new preflight goal.")

    def active(self, claim=False):
        if self.active_path.exists():
            other = read_json(self.active_path).get("goal_id")
            if other != self.id:
                raise LoopError(f"Another goal owns this working tree: {other}")
        elif claim:
            write_json(self.active_path, {"goal_id": self.id})

    def release(self):
        if self.active_path.exists() and read_json(self.active_path).get("goal_id") == self.id:
            self.active_path.unlink()

    def session(self, state, contract, discovery):
        if not state.get("session_id"):
            # Upgrade an older goal once, without guessing which conversation owns other caches.
            state["session_id"] = Session.create(self.root, contract["execution"].get("orchestrator", contract["execution"]["implementer"]), self.knowledge).id
            self.save(state, "session-attached")
        session = Session(self.root, state["session_id"], self.knowledge)
        session.sync_goal(state, contract, discovery)
        return session

    def refresh(self):
        state, contract, discovery = self.load()
        session_context = self.session(state, contract, discovery).refresh(self.id)
        value = context.packet(self.root, self.goal_dir, self.state_dir, state, contract, discovery, session_context)
        if state["phase"] != "draft":
            try:
                self.integrity(state, contract, discovery)
                value["integrity_ok"] = True
            except LoopError as exc:
                value["integrity_ok"] = False
                value["integrity_error"] = str(exc)
                value["next"] = {"action": "report_blocked", "instruction": str(exc)}
        if state["phase"] == "completed":
            value["evidence_current"] = (value.get("integrity_ok", False)
                                          and snapshot(self.root)["digest"] == state["completion_snapshot"])
            if not value["evidence_current"]:
                value["next"] = {"action": "report_stale", "instruction": "Files changed after completion; new work needs a new goal."}
        path = self.state_dir / "context.json"
        work = self.knowledge.record_development(self.root, state, contract, discovery, value.get("evidence_current"),
                                               value["next"]["instruction"])
        value["work"] = {"id": work["id"], "kind": "development", "phase": work["phase"]}
        value["delegations"] = self.knowledge.delegations(work["id"])
        history = value["session"]["knowledge"]["tasks"]
        history[:] = [item for item in history if item["id"] != work["id"]]
        history.insert(0, task_summary(work))
        write_json(path, value)
        document = self.state_dir / "status.md"
        rendered = task_document({**work, "delegations": value["delegations"]}).replace(
            f"[Full record]({work['id']}.json)", "[Full context](context.json)")
        if not document.exists() or document.read_text(encoding="utf-8") != rendered:
            write_text(document, rendered)
        return {"goal": self.id, "phase": state["phase"], "session_id": state["session_id"],
                "work_id": work["id"], "context": str(path), "document": str(document), "next": value["next"],
                **{key: value[key] for key in ("integrity_ok", "evidence_current", "integrity_error") if key in value}}

    def plan_review(self):
        state, contract, discovery = self.load()
        if state["phase"] != "draft":
            raise LoopError("Plan review is only allowed before freeze")
        self.active()
        self.check_valid(contract, discovery)
        state.pop("plan_review", None)
        self.save(state, "plan-review-started")
        if discovery["baseline_head"] != head(self.root):
            raise LoopError("Update discovery for the current Git HEAD before plan review")
        inputs = self.inputs(contract, discovery)
        before = snapshot(self.root)
        run_dir = self.state_dir / "preflight" / uuid.uuid4().hex
        health = providers.doctor(self.root, run_dir / "doctor")
        write_json(run_dir / "doctor.json", health)
        if not health["ok"]:
            raise LoopError(f"Both CLIs must be available before starting; inspect {run_dir / 'doctor.json'}")
        ids = [x["id"] for x in contract["target"]["acceptance"]]
        session = self.session(state, contract, discovery)
        prompt = context.review_prompt(self.root, contract, discovery, "plan", ids, {}, before,
                                       session_context=session.refresh(self.id))
        review = providers.review(providers.reviewer(contract["execution"]["implementer"]), self.root,
                                  run_dir / "review", prompt, contract["execution"]["review_timeout_seconds"], ids, "plan")
        if snapshot(self.root)["digest"] != before["digest"] or self.inputs(*self.load()[1:]) != inputs:
            raise LoopError("Repository changed during read-only plan review; review is invalid")
        report = {"phase": "plan", "at": now(), "passed": review["passed"], "review": review,
                  "inputs": inputs, "snapshot": before["digest"], "doctor": health}
        path = run_dir / "gate.json"
        write_json(path, report)
        state["latest_gate"] = str(path)
        state["plan_review"] = str(path)
        self.save(state, "plan-reviewed")
        session.capture_review(state, contract, discovery, report)
        self.refresh()
        return report

    def delegate(self, request, recover=None):
        from .delegation import Delegation
        self.refresh()
        return Delegation(self.knowledge, self.knowledge.development_id(self.knowledge.attach(self.root), self.id), self.root).run(request, recover)

    def freeze(self):
        state, contract, discovery = self.load()
        if state["phase"] != "draft":
            raise LoopError("Only a draft goal can be frozen")
        self.active()
        self.check_valid(contract, discovery)
        if not state.get("plan_review"):
            raise LoopError("Run the live cross-provider plan-review before freeze")
        report = read_json(Path(state["plan_review"]))
        inputs = self.inputs(contract, discovery)
        current = snapshot(self.root)
        if not report["passed"] or report["inputs"] != inputs or report["snapshot"] != current["digest"]:
            raise LoopError("Plan review failed or is stale; resolve findings and run plan-review again")
        state.update(phase="ready", frozen_inputs=inputs, frozen_at=now(), ready_snapshot=current["digest"],
                     baseline_snapshot=current, implementer=contract["execution"]["implementer"])
        write_json(self.state_dir / "frozen.json", {"contract": contract, "discovery": discovery, "inputs": inputs})
        self.save(state, "frozen")
        return self.refresh()

    def start(self):
        state, contract, discovery = self.load()
        if state["phase"] != "ready":
            raise LoopError("Only a frozen, ready goal can start")
        self.integrity(state, contract, discovery)
        if snapshot(self.root)["digest"] != state["ready_snapshot"]:
            raise LoopError("Code changed since preflight; create a new preflight goal")
        self.active(claim=True)
        state.update(phase="running", started_at=now(), retryable=False)
        self.save(state, "started")
        return self.refresh()

    def remaining(self, state, contract):
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state["started_at"])).total_seconds()
        return contract["execution"]["max_run_seconds"] - elapsed

    def gate(self, milestone=None):
        state, contract, discovery = self.load()
        if state["phase"] != "running":
            raise LoopError("Only a running goal can execute a gate")
        self.active(claim=True)
        try:
            self.integrity(state, contract, discovery)
        except LoopError as exc:
            self.block(str(exc), retryable=False)
            raise
        if state.get("inflight"):
            self.block("A previous gate was interrupted; inspect its logs before resuming", retryable=True)
            raise LoopError("Interrupted gate detected; inspect logs and run resume")
        done = [x["id"] for x in state["milestones"]]
        todo = [x for x in contract["milestones"] if x["id"] not in done]
        if milestone is None and todo:
            raise LoopError("All milestones must pass before finish")
        if milestone is not None and (not todo or todo[0]["id"] != milestone):
            raise LoopError("Checkpoint must match the next incomplete milestone")
        key = "final" if milestone is None else "milestone-" + milestone
        attempts = state["attempts"].get(key, 0)
        if attempts >= contract["execution"]["max_gate_attempts"] or self.remaining(state, contract) <= 0:
            self.block("Execution budget exhausted; do not reduce the goal to force a pass", retryable=False)
            raise LoopError("Execution budget exhausted")
        state["attempts"][key] = attempts + 1
        run_dir = self.state_dir / "gates" / f"{key}-{attempts + 1}-{uuid.uuid4().hex[:8]}"
        state["inflight"] = str(run_dir)
        self.save(state, "gate-started")
        phase = "final" if milestone is None else "checkpoint"
        ids = ([x["id"] for x in contract["target"]["acceptance"]] if milestone is None
               else todo[0]["acceptance_ids"])
        selected_checks = {cid for item in contract["target"]["acceptance"] if item["id"] in ids
                           for cid in item["check_ids"]}
        checks = [x for x in contract["checks"] if milestone is None
                  or x["id"] in selected_checks or x["kind"] == "regression"]
        report = {"phase": phase, "milestone": milestone, "attempt": attempts + 1, "at": now(),
                  "passed": False, "checks": [], "review": None, "error": None}
        try:
            before = snapshot(self.root)
            for check in checks:
                remaining = self.remaining(state, contract)
                if remaining <= 0:
                    raise LoopError("Execution budget exhausted")
                result = execute(check["argv"], inside(self.root, check["cwd"]), run_dir / "checks" / check["id"],
                                 min(check["timeout_seconds"], max(0.01, remaining)))
                report["checks"].append({"id": check["id"], "passed": result["exit_code"] == 0 and not result["timed_out"],
                                         "process": result})
            after_checks = snapshot(self.root)
            if after_checks["digest"] != before["digest"]:
                raise LoopError("Checks changed repository inputs. Use ignored output paths; inspect changes and retry.")
            self.integrity(state, *self.load()[1:])
            if all(x["passed"] for x in report["checks"]):
                remaining = self.remaining(state, contract)
                if remaining <= 0:
                    raise LoopError("Execution budget exhausted")
                prompt = context.review_prompt(self.root, contract, discovery, phase, ids, report["checks"],
                                               state["baseline_snapshot"], state["notes"],
                                               self.session(state, contract, discovery).refresh(self.id))
                report["review"] = providers.review(
                    providers.reviewer(state["implementer"]), self.root, run_dir / "review", prompt,
                    min(contract["execution"]["review_timeout_seconds"], max(0.01, remaining)), ids, phase)
                if snapshot(self.root)["digest"] != before["digest"]:
                    raise LoopError("Read-only reviewer changed repository inputs; review is invalid")
                self.integrity(state, *self.load()[1:])
                report["passed"] = report["review"]["passed"]
            report["snapshot"] = before["digest"]
        except (LoopError, OSError) as exc:
            report["error"] = str(exc)
        except BaseException:
            self.block("Gate interrupted; inspect logs before resume", retryable=True)
            raise
        report_path = run_dir / "gate.json"
        write_json(report_path, report)
        state.pop("inflight", None)
        state["latest_gate"] = str(report_path)
        exhausted = (state["attempts"][key] >= contract["execution"]["max_gate_attempts"]
                     or self.remaining(state, contract) <= 0)
        integrity_ok = True
        try:
            self.integrity(state, *self.load()[1:])
        except LoopError:
            integrity_ok = False
        if report["passed"] and integrity_ok:
            if milestone is not None:
                state["milestones"].append({"id": milestone, "snapshot": report["snapshot"], "gate": str(report_path)})
            else:
                state.update(phase="completed", completed_at=now(), completion_snapshot=report["snapshot"])
                self.release()
        elif exhausted or not integrity_ok:
            state.update(phase="blocked", retryable=False,
                         block_reason=report["error"] or "Gate attempt/time budget exhausted")
        elif report["error"] or (report["review"] and report["review"]["result"]["verdict"] == "blocked"):
            state.update(phase="blocked", retryable=True,
                         block_reason=report["error"] or report["review"]["result"]["summary"])
        self.save(state, "gate-passed" if report["passed"] else "gate-failed")
        self.session(state, contract, discovery).capture_review(state, contract, discovery, report)
        report["status"] = self.refresh()
        return report

    def block(self, reason, retryable=True):
        state, _, _ = self.load()
        if state["phase"] not in ("running", "blocked"):
            raise LoopError("Only a running or blocked goal can be blocked")
        state.update(phase="blocked", block_reason=reason, retryable=retryable)
        self.save(state, "blocked")
        return {"phase": "blocked", "reason": reason, "retryable": retryable, "status": self.refresh()}

    def resume(self):
        state, contract, discovery = self.load()
        if state["phase"] != "blocked" or not state.get("retryable"):
            raise LoopError("This goal is not retryable; a changed contract or exhausted budget needs a new preflight goal")
        self.active(claim=True)
        self.integrity(state, contract, discovery)
        if self.remaining(state, contract) <= 0:
            raise LoopError("Run time budget exhausted")
        state.pop("inflight", None)
        state.update(phase="running", block_reason=None, retryable=False)
        self.save(state, "resumed")
        return self.refresh()

    def abort(self, reason):
        state, _, _ = self.load()
        if state["phase"] in ("completed", "aborted"):
            raise LoopError("Goal is already terminal")
        state.update(phase="aborted", block_reason=reason, aborted_at=now())
        self.save(state, "aborted")
        self.release()
        return {"phase": "aborted", "reason": reason, "note": "All code and evidence retained", "status": self.refresh()}

    def note(self, kind, text, evidence):
        state, contract, discovery = self.load()
        if state["phase"] != "running":
            raise LoopError("Execution notes require a running goal")
        self.integrity(state, contract, discovery)
        concise(text, "Execution finding")
        if not evidence.strip():
            raise LoopError("Execution findings need evidence")
        if any(x["kind"] == kind and x["text"] == text and x["evidence"] == evidence for x in state["notes"]):
            return self.refresh()
        state["notes"].append({"at": now(), "kind": kind, "text": text, "evidence": evidence})
        self.save(state, "context-updated")
        return self.refresh()
