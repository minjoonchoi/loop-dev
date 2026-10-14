"""Durable, bounded peer turns over portable isolated worker invocations."""

from __future__ import annotations

from pathlib import Path

from .contract import identifier
from .delegation import Delegation, validate_request
from .storage import LoopError, digest, locked, now, read_json, write_json


MAX_MEMBERS = 12
MAX_INBOX = 5


class Team:
    def __init__(self, knowledge, task_id, root):
        self.knowledge = knowledge
        self.task_id = task_id
        self.root = Path(root).resolve()
        self.worker = Delegation(knowledge, task_id, self.root)

    def key(self, team_id):
        if not identifier(team_id):
            raise LoopError("Team needs a valid ID")
        return "team-" + digest([self.task_id, team_id])[:24]

    def assignment(self, member):
        return self.knowledge.get("delegation", "delegate-" + digest([self.task_id, member])[:24], required=False)

    def save(self, record):
        record["updated_at"] = now()
        self.knowledge.put("team", record)
        # SQLite is authoritative; this complete ledger is a repairable artifact.
        write_json(Path(record["artifact"]), record)

    def load(self, team_id):
        record = self.knowledge.get("team", self.key(team_id))
        if record["location_id"] != self.worker.location["id"]:
            raise LoopError("A team is bound to its original working location")
        return record

    def status(self, team_id):
        record = self.load(team_id)
        write_json(Path(record["artifact"]), record)
        current = self.worker.observation()["digest"]
        from .orchestration import worker_context
        control = worker_context(self.knowledge, self.task_id)
        parent_current = digest(self.worker.parent()[1:]) == record["parent_seal"]
        current_evidence = (current == record["source_revision"] and parent_current
                            and control["revision"] == record["feedback_revision"] and not control["blocked"])
        members = [{"id": key, **{name: value.get(name) for name in ("status", "summary", "turns", "result")}}
                   for key, value in record["members"].items()]
        pending = [item for item in record["messages"] if item["delivered_turn"] is None
                   and item.get("epoch", 0) == record.get("epoch", 0)]
        budget = self.knowledge.get("worker-budget", self.task_id, required=False) or {"calls": 0}
        pending_candidate = None
        if record.get("inflight"):
            pending_assignment = self.assignment(record["inflight"]["member"])
            if pending_assignment and pending_assignment["status"] == "candidate":
                pending_candidate = self.worker.response(pending_assignment)["assignment"]
        return {"passed": record["status"] == "done" and current_evidence,
                "team_id": record["team_id"], "task_id": self.task_id, "status": record["status"],
                "summary": record["summary"], "error": (record.get("error") or "")[:280] or None,
                "evidence_current": current_evidence, "turns": record["turns"],
                "max_turns": record["plan"]["max_turns"], "task_worker_calls": budget["calls"],
                "members": members[:5], "member_count": len(members),
                "message_count": len(record["messages"]), "pending_message_count": len(pending),
                "artifact": record["artifact"],
                "candidate": pending_candidate,
                "next": "Inspect the bounded team outcome and run the parent completion checks; team completion is not goal completion."}

    def validate(self, plan):
        from .routing import validate_splits
        if (not isinstance(plan, dict) or set(plan) != {"id", "members", "max_turns"}
                or not identifier(plan["id"]) or type(plan["max_turns"]) is not int
                or not 1 <= plan["max_turns"] <= 200):
            raise LoopError("Team plan needs id, members and max_turns between 1 and 200")
        rows = plan["members"]
        if not isinstance(rows, list) or not 2 <= len(rows) <= MAX_MEMBERS:
            raise LoopError("A team needs between two and twelve bounded members; use delegate for one unit")
        task, state, goal, discovery = self.worker.parent()
        rules = self.knowledge.observe([self.worker.location])[self.worker.location["id"]]["rules"]
        rule_paths = sorted(set(rules) | set(goal.get("rules", []))) if task["kind"] == "development" else sorted(rules)
        members = {}
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"assignment"}:
                raise LoopError("Each team member contains exactly one assignment")
            request = row["assignment"]
            validate_request(request, self.root, state["phase"], task["kind"] == "development", rule_paths)
            if request["id"] in members:
                raise LoopError("Team member assignment IDs must be unique")
            previous = self.assignment(request["id"])
            if previous and (previous["request"] != request or previous["location_id"] != self.worker.location["id"]):
                raise LoopError("An assignment is immutable and bound to its original location")
            if previous and previous.get("team_id") not in (None, plan["id"]):
                raise LoopError("An assignment already belongs to another team; use an independent scoped assignment")
            members[request["id"]] = request
        validate_splits(list(members.values()), goal, state["phase"])
        # Validate the whole graph before reserving any provider call.
        visited, visiting = set(), set()

        def visit(name):
            if name in visiting:
                raise LoopError("Team dependencies contain a cycle")
            if name in visited:
                return
            visiting.add(name)
            for dependency in members[name]["depends_on"]:
                if dependency in members:
                    visit(dependency)
                else:
                    dep = self.assignment(dependency)
                    if not dep or dep["status"] != "done":
                        raise LoopError("Team prerequisite is missing or incomplete: " + dependency)
            visiting.remove(name)
            visited.add(name)

        for name in members:
            visit(name)
        return task, state, goal, discovery, members

    def block(self, record, reason, status="blocked"):
        record.update(status=status, summary="The team cannot continue within its current scope and evidence.", error=str(reason))
        self.save(record)

    def inbox(self, record, member):
        selected = [item for item in record["messages"] if item["to"] == member
                    and item.get("epoch", 0) == record.get("epoch", 0)
                    and (item["delivered_turn"] is None or item["kind"] == "question" and not item["answered_by"])]
        # New answers must not be hidden behind previously delivered requests.
        return sorted(selected, key=lambda item: (item["delivered_turn"] is not None, item["kind"] != "answer", item["turn"]))[:MAX_INBOX]

    def select(self, record, assignments):
        ready = []
        for name, member in record["members"].items():
            inbox = self.inbox(record, name)
            new_input = [item for item in inbox if item["delivered_turn"] is None]
            if member["status"] == "waiting" and not any(item["kind"] in ("answer", "question") for item in new_input):
                continue
            if member["status"] == "done" and not inbox:
                continue
            if member["status"] not in ("pending", "waiting", "done"):
                continue
            if any(dependency in record["members"] and record["members"][dependency]["status"] != "done"
                   for dependency in assignments[name]["depends_on"]):
                continue
            ready.append((not bool(inbox), list(assignments).index(name), name))
        return min(ready)[2] if ready else None

    def packet(self, record, assignments, member, turn, control):
        inbox = self.inbox(record, member)
        for item in inbox:
            if item["kind"] != "question" and (item["source_revision"] != record["source_revision"]
                    or item["parent_seal"] != record["parent_seal"]
                    or item["feedback_revision"] != control["revision"]):
                raise LoopError("A peer answer or finding is stale; refresh its evidence within the existing budget")
        state = record["members"][member]
        return {"id": record["team_id"], "member": member, "turn": turn, "epoch": record.get("epoch", 0),
                "roster": [{"id": key, "kind": item["kind"], "purpose_excerpt": item["instruction"][:280]}
                           for key, item in assignments.items()],
                "inbox": [{key: value for key, value in item.items() if key not in ("delivered_turn", "answered_by")}
                          for item in inbox],
                "pending_questions": [{"id": item["id"], "to": item["to"], "summary": item["summary"]}
                                      for item in record["messages"] if item["from"] == member
                                      and item["kind"] == "question" and not item["answered_by"]
                                      and item.get("epoch", 0) == record.get("epoch", 0)],
                "previous": {"historical": True, "status": state["status"], "summary": state["summary"],
                             "result_reference": state.get("result")},
                "source_revision": record["source_revision"], "parent_seal": record["parent_seal"],
                "feedback_revision": control["revision"]}

    def accept(self, record, member, assignment_record):
        inflight = record["inflight"]
        if (assignment_record.get("team_id") != record["team_id"]
                or assignment_record.get("team_turn") != inflight["turn"]):
            raise LoopError("The recovered worker does not match the reserved team turn")
        state = record["members"][member]
        if assignment_record["status"] == "candidate":
            raise LoopError("The main session must assess the candidate before any team result or message is accepted")
        if assignment_record["status"] == "rejected":
            self.worker.candidate(assignment_record)
            self.worker.assessment_receipt(assignment_record)
            # Only this role is retried. Failed outputs, findings and peer
            # messages remain quarantined in the rejected attempt directory.
            state.update(status="pending", summary="The candidate was rejected; this role needs its next allowed profile.",
                         result=None, after=None)
            record["inflight"] = None
            record.update(status="running", error=None)
            self.save(record)
            return
        state.update(status=assignment_record["status"], summary=assignment_record["summary"],
                     result=assignment_record.get("result"), after=assignment_record.get("after"))
        if assignment_record["status"] in ("done", "waiting"):
            if (assignment_record["after"] != self.worker.observation()["digest"]
                    or assignment_record["parent_seal"] != digest(self.worker.parent()[1:])):
                raise LoopError("Worker evidence changed before its team result could be accepted")
            control = self.worker.control()
            if assignment_record["feedback_revision"] != control["revision"]:
                raise LoopError("User feedback changed before the team accepted this turn")
            value = self.worker.candidate(assignment_record)
            self.worker.assessment_receipt(assignment_record)
            messages = {item["id"]: item for item in record["messages"]}
            for message_id in inflight["inbox"]:
                messages[message_id]["delivered_turn"] = inflight["turn"]
            for index, item in enumerate(value["messages"]):
                message_id = "message-" + digest([record["id"], inflight["turn"], index])[:24]
                # The worker cannot select sender, IDs, provenance, or delivery state.
                message = {**item, "id": message_id, "from": member, "turn": inflight["turn"],
                           "epoch": record.get("epoch", 0),
                           "source_revision": assignment_record["after"],
                           "parent_seal": assignment_record["parent_seal"],
                           "feedback_revision": control["revision"], "delivered_turn": None, "answered_by": None}
                if item["kind"] == "answer":
                    messages[item["reply_to"]]["answered_by"] = message_id
                record["messages"].append(message)
            record["source_revision"] = assignment_record["after"]
        record["inflight"] = None
        if state["status"] in ("blocked", "failed", "interrupted"):
            self.block(record, assignment_record.get("error") or state["summary"],
                       "interrupted" if state["status"] == "interrupted" else "blocked")
        else:
            self.save(record)

    def run(self, plan, recover=None):
        # This lock protects the plan/ledger. Delegation retains task/location locks
        # per turn, so providers never acquire recursively held worker locks.
        if not isinstance(plan, dict):
            raise LoopError("Team plan must be an object")
        key = self.key(plan.get("id"))
        with locked(self.knowledge.home / "team-locks" / key):
            task, state, goal, discovery, assignments = self.validate(plan)
            control = self.worker.control()
            parent_seal = digest([state, goal, discovery])
            record = self.knowledge.get("team", key, required=False)
            if record:
                if record["plan"] != plan or record["location_id"] != self.worker.location["id"]:
                    raise LoopError("Team plans, member scopes, locations and turn limits are immutable")
                if record["goal_seal"] != digest(goal):
                    raise LoopError("The team's parent goal changed; do not silently change worker scope")
            else:
                artifact = self.knowledge.home / "runs" / self.task_id / "teams" / key / "team.json"
                record = {"id": key, "team_id": plan["id"], "task_id": self.task_id,
                          "location_id": self.worker.location["id"], "root": str(self.root), "plan": plan,
                          "goal_seal": digest(goal), "parent_seal": parent_seal,
                          "feedback_revision": control["revision"], "source_revision": self.worker.observation()["digest"],
                          "status": "running", "summary": "The bounded team is executing its assignments.", "error": None,
                          "turns": 0, "epoch": 0, "inflight": None, "messages": [], "artifact": str(artifact), "created_at": now(),
                          "members": {name: {"status": "pending", "summary": "This assignment has not run.", "turns": 0,
                                             "result": None, "after": None} for name in assignments}}
                self.save(record)
            if record["inflight"]:
                inflight = record["inflight"]
                previous = self.assignment(inflight["member"])
                matching = (previous and previous.get("team_id") == plan["id"]
                            and previous.get("team_turn") == inflight["turn"])
                if matching and previous["status"] == "candidate":
                    self.worker.candidate(previous)
                    if (previous["before"] == self.worker.observation()["digest"]
                            and previous["parent_seal"] == parent_seal
                            and previous["feedback_revision"] == control["revision"]):
                        record.update(status="awaiting_assessment", summary="The main session must assess the current candidate before team work continues.")
                        self.save(record)
                        return self.status(plan["id"])
                    if not isinstance(recover, str) or not recover.strip():
                        self.block(record, "Pending candidate evidence changed; inspect and use --recover REASON for a fresh scoped turn")
                        return self.status(plan["id"])
                elif matching and previous["status"] in ("running", "interrupted", "applying"):
                    if not isinstance(recover, str) or not recover.strip():
                        raise LoopError("Interrupted team: inspect logs and stop surviving workers before --recover REASON")
                if (previous and previous.get("team_id") == plan["id"] and previous.get("team_turn") == inflight["turn"]
                        and previous["status"] in ("done", "waiting", "rejected", "blocked", "failed")):
                    try:
                        self.accept(record, inflight["member"], previous)
                    except LoopError as exc:
                        if not isinstance(recover, str) or not recover.strip():
                            self.block(record, str(exc))
                            return self.status(plan["id"])
                        # Inspection authorizes a fresh bounded attempt, not
                        # adoption of the stale result or its peer messages.
                        record.setdefault("recoveries", []).append({"turn": inflight["turn"],
                            "reason": str(exc), "result": previous.get("result"), "inspection": recover})
                        record["members"][inflight["member"]]["status"] = "pending"
                        record["inflight"] = None
                        self.save(record)
                else:
                    if not isinstance(recover, str) or not recover.strip():
                        raise LoopError("Interrupted team: inspect the reserved turn before --recover REASON")
                    record["members"][inflight["member"]]["status"] = "pending"
                    record["inflight"] = None
                    record["recovery"] = recover
                    self.save(record)
            current_source = self.worker.observation()["digest"]
            changed_context = (record["parent_seal"] != parent_seal or record["source_revision"] != current_source
                               or record["feedback_revision"] != control["revision"])
            if record["status"] == "done" and not changed_context:
                return self.status(plan["id"])
            if changed_context:
                if record["status"] != "done" and not (isinstance(recover, str) and recover.strip()):
                    self.block(record, "Team context changed; inspect the retained evidence and use --recover REASON for fresh scoped turns")
                    return self.status(plan["id"])
                # Refresh the same immutable assignments. All old messages and
                # results remain historical; no turn or call budget is reset.
                record["epoch"] = record.get("epoch", 0) + 1
                record.update(parent_seal=parent_seal, source_revision=current_source, feedback_revision=control["revision"])
                for member_state in record["members"].values():
                    member_state["status"] = "pending"
                self.save(record)
            # Failed/blocked attempts need a concrete inspection reason, never a
            # counter reset. Waiting peer turns are resumed automatically below.
            for name, member in record["members"].items():
                if member["status"] in ("blocked", "failed", "interrupted"):
                    if not isinstance(recover, str) or not recover.strip():
                        return self.status(plan["id"])
                    member["status"] = "pending"
            record.update(status="running", error=None)
            self.save(record)
            while True:
                try:
                    control = self.worker.control()
                    if digest(self.worker.parent()[1:]) != record["parent_seal"]:
                        raise LoopError("Parent goal/state changed during the team; restore current context")
                    if self.worker.observation()["digest"] != record["source_revision"]:
                        raise LoopError("Sources changed outside an accepted team turn; inspect current evidence")
                    if control["revision"] != record["feedback_revision"]:
                        raise LoopError("User feedback changed during the team; restore current scoped assignments")
                    member = self.select(record, assignments)
                    unresolved = [item for item in record["messages"] if item["kind"] == "question" and not item["answered_by"]
                                  and item.get("epoch", 0) == record.get("epoch", 0)]
                    if member is None:
                        if all(item["status"] == "done" for item in record["members"].values()) and not unresolved:
                            record.update(status="done", summary="Every team assignment completed within its scope.", error=None)
                            self.save(record)
                        else:
                            self.block(record, "No member can proceed: unresolved peer questions or unmet dependencies")
                        return self.status(plan["id"])
                    if record["turns"] >= plan["max_turns"]:
                        raise LoopError("Team turn budget exhausted; do not reset it or weaken the goal")
                    # Existing dependency freshness checks also cover prior teams
                    # and other roots without accepting their historical output.
                    self.worker.handoff(task, assignments[member])
                    turn = record["turns"] + 1
                    packet = self.packet(record, assignments, member, turn, control)
                    with locked(self.knowledge.home / "task-locks" / self.task_id), \
                            locked(self.knowledge.home / "worker-locks" / self.worker.location["id"]):
                        record["turns"] = turn
                        record["members"][member]["turns"] += 1
                        record["members"][member]["status"] = "running"
                        record["inflight"] = {"member": member, "turn": turn, "inbox": [x["id"] for x in packet["inbox"]]}
                        self.save(record)
                        result = self.worker._run(assignments[member], recover, team=packet)
                    resolved = self.assignment(member)
                    if resolved["status"] == "candidate":
                        record["members"][member].update(status="candidate", summary=resolved["summary"], result=resolved["result"])
                        record.update(status="awaiting_assessment", summary="The main session must assess the current candidate before team work continues.",
                                      error=resolved.get("error"))
                        self.save(record)
                        return self.status(plan["id"])
                    self.accept(record, member, resolved)
                    if record["status"] in ("blocked", "interrupted"):
                        return self.status(plan["id"])
                except (LoopError, OSError, ValueError) as exc:
                    # Keep a reservation when a provider may have run; recovery
                    # imports its durable result rather than dispatching it twice.
                    if record["inflight"]:
                        reservation = record["inflight"]
                        latest = self.assignment(reservation["member"])
                        if not latest or (latest.get("team_id"), latest.get("team_turn")) != (plan["id"], reservation["turn"]):
                            record["members"][reservation["member"]]["status"] = "pending"
                            record["inflight"] = None
                    self.block(record, exc)
                    return self.status(plan["id"])
                except BaseException:
                    self.block(record, "Inspect the interrupted team and surviving worker processes before recovery.", "interrupted")
                    raise
