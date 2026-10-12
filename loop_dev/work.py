"""Goal setting and one-pass work for documents, explanations and research."""

from __future__ import annotations

import json
import uuid

from .contract import identifier
from .knowledge import KnowledgeBase
from .storage import LoopError, digest, now
from .writing import concise


KINDS = ("development", "document", "explanation", "research", "review", "planning")


class Work:
    def __init__(self, knowledge=None):
        self.knowledge = knowledge or KnowledgeBase()

    def open(self, kind, title, provider, paths, session_id=None):
        if kind not in KINDS or kind == "development":
            raise LoopError("Development work uses init and its existing development gates")
        concise(title, "Task title", 120)
        if not paths:
            raise LoopError("A task needs a title and at least one working directory")
        session = self.knowledge.session(provider, session_id)
        locations = [self.knowledge.attach(path) for path in paths]
        value = {"id": "work-" + uuid.uuid4().hex, "kind": kind, "title": title,
                 "phase": "goal_setting", "session_id": session["id"], "provider": provider,
                 "locations": sorted({x["id"] for x in locations}),
                 "project_ids": sorted({x["project_id"] for x in locations}),
                 "goal": None, "notes": [], "result": None, "created_at": now(), "updated_at": now(),
                 "version_token": uuid.uuid4().hex}
        self.knowledge.put("task", value)
        return self.context(value["id"])

    def load(self, task_id, mutable=True):
        task = self.knowledge.get("task", task_id)
        if mutable and task["kind"] == "development":
            raise LoopError("Development work must use its repository's plan-review/freeze/start/checkpoint/finish gates")
        return task

    def locations(self, task):
        return [self.knowledge.get("location", key) for key in task["locations"]]

    def save(self, task, topics=()):
        with self.knowledge.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM records WHERE kind='task' AND id=?", (task["id"],)).fetchone()
            if not row or json.loads(row[0])["version_token"] != task["version_token"]:
                raise LoopError("Task changed concurrently; restore its latest context")
            task.update(version_token=uuid.uuid4().hex, updated_at=now())
            for topic in topics:
                self.knowledge.put_topic(topic, db)
            self.knowledge.put("task", task, db)
        self.knowledge.document("task", task["id"])
        for topic in topics:
            self.knowledge.document("topic", topic["id"])
        return task

    def define(self, task_id, goal):
        task = self.load(task_id)
        if task["phase"] != "goal_setting":
            raise LoopError("A working goal is fixed; changed requirements need a new task")
        if not isinstance(goal, dict):
            raise LoopError("Goal must be an object")
        for key in ("outcome", "deliverable"):
            concise(goal.get(key), "Goal " + key, 400)
        criteria = goal.get("criteria")
        if (not isinstance(criteria, list) or not criteria or not all(isinstance(c, dict) and identifier(c.get("id"))
                and isinstance(c.get("description"), str) and c["description"].strip() for c in criteria)
                or len({c["id"] for c in criteria}) != len(criteria)):
            raise LoopError("Goal needs unique criteria with IDs and descriptions")
        if not isinstance(goal.get("open_questions"), list) or not isinstance(goal.get("decisions"), list):
            raise LoopError("Goal needs explicit open_questions and decisions lists (empty is allowed)")
        for item in goal["decisions"]:
            if (not isinstance(item, dict) or item.get("basis") not in ("user", "repository-rule", "delegated")
                    or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("answer", "applies_to", "evidence"))):
                raise LoopError("Decisions need answer, basis, applies_to and original evidence")
        task["goal"] = goal
        self.save(task)
        return self.context(task_id)

    def start(self, task_id):
        task = self.load(task_id)
        if task["phase"] != "goal_setting" or not task["goal"]:
            raise LoopError("Define the goal before starting")
        if task["goal"]["open_questions"]:
            raise LoopError("Resolve user-owned questions before work starts")
        task.update(phase="working", goal_seal=digest(task["goal"]),
                    baseline=self.knowledge.observe(self.locations(task)), started_at=now())
        self.save(task)
        return self.context(task_id)

    def finish(self, task_id, result):
        task = self.load(task_id)
        if task["phase"] != "working" or digest(task["goal"]) != task["goal_seal"]:
            raise LoopError("Only work on an unchanged, started goal can finish")
        if (not isinstance(result, dict) or not isinstance(result.get("summary"), str) or not result["summary"].strip()
                or not isinstance(result.get("outputs"), list) or not result["outputs"]
                or not all(isinstance(x, str) and x.strip() for x in result["outputs"])):
            raise LoopError("Result needs a summary and output/answer references")
        concise(result["summary"], "Result summary")
        checks = result.get("checks")
        ids = {c["id"] for c in task["goal"]["criteria"]}
        if (not isinstance(checks, list) or not all(isinstance(c, dict) and identifier(c.get("criterion_id"))
                and type(c.get("satisfied")) is bool and isinstance(c.get("evidence"), str) and c["evidence"].strip() for c in checks)
                or len(checks) != len(ids) or {c["criterion_id"] for c in checks} != ids):
            raise LoopError("Result checks must cover each completion criterion exactly once with evidence")
        if not all(c["satisfied"] for c in checks):
            task["last_result"] = result
            self.save(task)
            return {"passed": False, "task_id": task_id, "phase": "working",
                    "instruction": "Address the unmet criterion within scope; no mandatory review or repetition count."}
        locations = self.locations(task)
        observations = self.knowledge.observe(locations)
        entries = result.get("knowledge", [])
        if not isinstance(entries, list) or not all(isinstance(x, dict) for x in entries):
            raise LoopError("Result knowledge must be an entry list")
        if len({x.get("id") for x in entries if isinstance(x.get("id"), str)}) != len(entries):
            raise LoopError("Result knowledge IDs must be unique strings")
        if entries and result.get("observed_revision") != digest(observations):
            raise LoopError("Read knowledge context after edits before saving new facts")
        topics = [self.knowledge.prepare_topic(entry, observations, task["session_id"], task_id) for entry in entries]
        if self.knowledge.observe(locations) != observations:
            raise LoopError("Inputs changed while completing work; inspect the changes")
        task.update(phase="completed", completed_at=now(), result={**result, "historical": True},
                    completion_observation=digest(observations))
        self.save(task, topics)
        return {"passed": True, **self.context(task_id)}

    def note(self, task_id, text, evidence):
        task = self.load(task_id)
        if task["phase"] not in ("goal_setting", "working") or not text.strip() or not evidence.strip():
            raise LoopError("Notes need active work, text and evidence")
        concise(text, "Finding")
        if any(x["text"] == text and x["evidence"] == evidence for x in task["notes"]):
            return self.context(task_id)
        task["notes"].append({"text": text, "evidence": evidence, "at": now()})
        self.save(task)
        return self.context(task_id)

    def stop(self, task_id, reason, abort=False):
        task = self.load(task_id)
        if task["phase"] in ("completed", "aborted") or not reason.strip():
            raise LoopError("Only unfinished work can be stopped with a reason")
        concise(reason, "Blocker")
        previous = task.get("previous_phase") if task["phase"] == "blocked" else task["phase"]
        task.update(previous_phase=previous, phase="aborted" if abort else "blocked", reason=reason)
        self.save(task)
        return self.context(task_id)

    def resume(self, task_id):
        task = self.load(task_id)
        if task["phase"] != "blocked":
            raise LoopError("Only blocked work can resume")
        if task.get("goal_seal") and task["goal_seal"] != digest(task["goal"]):
            raise LoopError("Restore the goal before resuming")
        task["phase"] = task["previous_phase"]
        task.pop("reason", None)
        self.save(task)
        return self.context(task_id)

    def context(self, task_id):
        task = self.load(task_id, mutable=False)
        if task["kind"] == "development":
            from pathlib import Path
            from .engine import Engine
            from .storage import locked
            root = Path(self.locations(task)[0]["root"])
            with locked(root):
                Engine(root, task["goal_id"], self.knowledge).refresh()
            task = self.load(task_id, mutable=False)
        self.knowledge.document("task", task_id)
        context = self.knowledge.context(self.locations(task), task["session_id"], task_id)
        instructions = {
            "goal_setting": "Reuse knowledge, define the outcome/deliverable/criteria, ask only unresolved human-owned choices, then start.",
            "working": "Perform the requested work, check its criteria, save supported knowledge and finish. No mandatory iterative loop or cross-provider review.",
            "completed": "Report the recorded result. A new request gets its own goal and reuses applicable knowledge.",
            "blocked": "Report the actual obstacle; recover within existing authority and resume. Do not ask mid-work questions.",
            "aborted": "Preserve the historical record; do not report success.",
        }
        return {"task": task, "knowledge": context, "document": str(self.knowledge.home / "views/task" / (task_id + ".md")),
                "next": instructions.get(task["phase"], "Inspect the saved state")
                if task["kind"] != "development" else "Use repository context and development gates; knowledge history cannot mark development complete."}
