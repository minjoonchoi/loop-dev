"""Bounded main-conversation context and durable, task-scoped user steering.

Goals, task state and gate records remain authoritative.  This module only owns
feedback/control records and deterministic navigation views.  Worker messages are
not user feedback and must never be written through this API.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contract import identifier
from .storage import LoopError, digest, now, read_json, write_json
from .writing import concise


MAX_CONTEXT_BYTES = 16 * 1024
MAX_VISIBLE = 5
CONTROL_KIND = "orchestration"
FEEDBACK_KIND = "feedback"
FEEDBACK_KINDS = {"constraint", "correction", "pause", "resume", "scope_change"}
SUBSTANTIVE_KINDS = {"constraint", "correction", "scope_change"}
FEEDBACK_FIELDS = {"id", "task_id", "kind", "summary", "original_input_ref", "applies_to", "supersedes"}


def _size(value):
    # Match the CLI's human-readable UTF-8 JSON, including its trailing newline.
    return len((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _record(db, kind, key, required=True):
    row = db.execute("SELECT body FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
    if row:
        return json.loads(row[0])
    if required:
        raise LoopError(f"Unknown {kind}: {key}")
    return None


def _records(db, kind):
    return [json.loads(row[0]) for row in db.execute(
        "SELECT body FROM records WHERE kind=? ORDER BY id", (kind,))]


def _valid_id(value, label):
    if not identifier(value):
        raise LoopError(f"Invalid {label}")


def _control(db, session_id):
    return _record(db, CONTROL_KIND, session_id, False) or {
        "id": session_id, "schema_version": 1, "control_version": 0}


def _feedback_id(session_id, feedback_id):
    return "feedback-" + digest([session_id, feedback_id])[:24]


def _ref(knowledge, kind, key):
    return str(knowledge.home / "views" / kind / (key + ".json"))


def _folder(knowledge, session_id):
    return knowledge.home / "views/orchestration" / session_id


def _input_hash(path):
    try:
        if not path.is_file():
            raise OSError("not a regular file")
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                value.update(chunk)
        return value.hexdigest()
    except (OSError, ValueError) as exc:
        raise LoopError(f"Feedback input file is unavailable: {path}") from exc


def _capture_input(reference):
    candidate = reference.removeprefix("sandbox:")
    path = Path(candidate)
    if path.is_absolute():
        return {"kind": "file", "path": str(path), "sha256": _input_hash(path)}
    # Conversation/remote references are retained, not falsely represented as
    # inspected original text.  The orchestrator owns the supplied user summary.
    return {"kind": "reference", "status": "reference_only"}


def _input_evidence(item):
    captured = item.get("original_input", {"kind": "reference", "status": "reference_only"})
    if captured["kind"] != "file":
        return dict(captured)
    try:
        current = _input_hash(Path(captured["path"]))
    except LoopError:
        return {**captured, "status": "unavailable"}
    if current != captured["sha256"]:
        return {**captured, "status": "stale", "observed_sha256": current}
    return {**captured, "status": "current"}


def _public_feedback(knowledge, item):
    return {"id": item["feedback_id"], "seq": item["seq"], "task_id": item["task_id"],
            **{key: item[key] for key in ("kind", "summary", "original_input_ref", "applies_to", "basis")},
            "evidence": _input_evidence(item),
            "record_ref": _ref(knowledge, FEEDBACK_KIND, item["id"])}


def _worker_view(knowledge, task, records, enforce=True):
    records = sorted((x for x in records if x["task_id"] == task["id"]
                      and x["session_id"] == task["session_id"]), key=lambda x: x["seq"])
    superseded = {key for item in records for key in item["supersedes"]}
    substantive = [_public_feedback(knowledge, x) for x in records
                   if x["kind"] in SUBSTANTIVE_KINDS and x["feedback_id"] not in superseded]
    controls = [x for x in records if x["kind"] in ("pause", "resume")]
    latest_control = _public_feedback(knowledge, controls[-1]) if controls else None
    # Scope changes require a new goal/task even if a later correction supersedes
    # their prose.  A resume cannot authorize dispatch against the unchanged goal.
    scope_changes = [x["feedback_id"] for x in records if x["kind"] == "scope_change"]
    paused = bool(controls and controls[-1]["kind"] == "pause")
    scope_changed = bool(scope_changes)
    identity = {"session_id": task["session_id"], "task_id": task["id"]}
    feedback = sorted(substantive + ([latest_control] if latest_control else []), key=lambda x: x["seq"])
    inputs_stale = any(x["evidence"].get("status") in ("stale", "unavailable") for x in feedback)
    # Derived view locations do not change the meaning of an otherwise identical
    # input when the durable knowledge store is intentionally relocated.
    sealed = [{k: v for k, v in x.items() if k != "record_ref"} for x in feedback]
    substantive_sealed = [{k: v for k, v in x.items() if k != "record_ref"} for x in substantive]
    value = {"schema_version": 1, **identity,
             "revision": digest({**identity, "feedback": sealed, "scope_changes": scope_changes}),
             "substantive_revision": digest({**identity, "feedback": substantive_sealed}),
             "requires_worker": bool(substantive), "paused": paused, "scope_changed": scope_changed,
             "blocked": paused or scope_changed or inputs_stale,
             "block_reason": ("Scope changed; define a new task before dispatching work."
                              if scope_changed else "User paused this task; an explicit resume is required." if paused else
                              "Original feedback evidence changed or is unavailable; restore it or record a new superseding user input."
                              if inputs_stale else None),
             "feedback": feedback,
             "feedback_history_ref": str(_folder(knowledge, task["session_id"]) / "feedback.json")}
    if enforce and _size(value) > MAX_CONTEXT_BYTES:
        raise LoopError("Effective feedback exceeds the 16 KiB worker context limit; explicitly reconcile and supersede it before dispatch")
    return value


def worker_context(knowledge, task_id, db=None):
    """Return complete applicable steering; an existing transaction stays read-only.

    Callers may pass their short apply/accept transaction to prevent feedback from
    committing between this check and result acceptance.  No connection, write or
    derived-view export occurs when ``db`` is supplied.
    """
    _valid_id(task_id, "task ID")
    if db is None:
        with knowledge.connection() as connection:
            connection.execute("BEGIN")
            return worker_context(knowledge, task_id, connection)
    task = _record(db, "task", task_id)
    _record(db, "session", task["session_id"])
    return _worker_view(knowledge, task, _records(db, FEEDBACK_KIND))


def _write_view(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        write_json(path, value)


def _small_text(value, maximum=280):
    # Legacy authored fields can be long.  Omit the entire optional preview and
    # retain its record reference instead of silently truncating meaning.
    return value if isinstance(value, str) and len(value) <= maximum and "\n" not in value and "\r" not in value else None


class Orchestration:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def worker_context(self, task_id, db=None):
        return worker_context(self.knowledge, task_id, db)

    def feedback(self, session_id, payload, expected_version):
        """Record an explicit user input; never modify a task goal or acceptance."""
        _valid_id(session_id, "session ID")
        if type(expected_version) is not int or expected_version < 0:
            raise LoopError("Expected control version must be a nonnegative integer")
        if not isinstance(payload, dict) or set(payload) != FEEDBACK_FIELDS:
            raise LoopError("Feedback needs exactly id, task_id, kind, summary, original_input_ref, applies_to and supersedes")
        _valid_id(payload["id"], "feedback ID")
        _valid_id(payload["task_id"], "task ID")
        if not isinstance(payload["kind"], str) or payload["kind"] not in FEEDBACK_KINDS:
            raise LoopError("Feedback kind must be constraint, correction, pause, resume or scope_change")
        for key, maximum in (("summary", 280), ("applies_to", 400), ("original_input_ref", 2048)):
            concise(payload[key], "Feedback " + key, maximum)
        supersedes = payload["supersedes"]
        if (not isinstance(supersedes, list) or not all(identifier(x) for x in supersedes)
                or len(supersedes) != len(set(supersedes)) or payload["id"] in supersedes):
            raise LoopError("Feedback supersedes must contain unique earlier feedback IDs")
        # An unordered list of explicit supersessions has one canonical identity.
        authored = {**payload, "supersedes": sorted(supersedes)}
        key = _feedback_id(session_id, authored["id"])
        with self.knowledge.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            _record(db, "session", session_id)
            task = _record(db, "task", authored["task_id"])
            if task["session_id"] != session_id:
                raise LoopError("Feedback task must belong to this session")
            control = _control(db, session_id)
            previous = _record(db, FEEDBACK_KIND, key, False)
            if previous:
                original = {name: previous["feedback_id"] if name == "id" else previous[name] for name in FEEDBACK_FIELDS}
                if original != authored:
                    raise LoopError("A feedback ID is immutable; use a new ID for a different input")
                item, reused = previous, True
            else:
                if control["control_version"] != expected_version:
                    raise LoopError("Control changed concurrently; restore the latest orchestration context")
                for target in authored["supersedes"]:
                    earlier = _record(db, FEEDBACK_KIND, _feedback_id(session_id, target), False)
                    if not earlier or earlier["task_id"] != task["id"]:
                        raise LoopError("Feedback can supersede only existing feedback from the same task")
                stamp = now()
                item = {**{name: value for name, value in authored.items() if name != "id"},
                        "id": key, "feedback_id": authored["id"], "session_id": session_id,
                        "seq": control["control_version"] + 1, "basis": "user", "created_at": stamp,
                        "original_input": _capture_input(authored["original_input_ref"])}
                control = {**control, "control_version": item["seq"], "updated_at": stamp}
                self.knowledge.put(FEEDBACK_KIND, item, db)
                self.knowledge.put(CONTROL_KIND, control, db)
                reused = False
            records = [x for x in _records(db, FEEDBACK_KIND) if x["session_id"] == session_id]
            view = _worker_view(self.knowledge, task, records, enforce=False)
            # Export under the writer transaction, so a delayed exporter cannot
            # replace a newer feedback history with an older snapshot.
            self._export_feedback(session_id, records, control)
            complete = _size(view) <= MAX_CONTEXT_BYTES
            return {"saved": True, "reused": reused, "session_id": session_id, "task_id": task["id"],
                    "control_version": control["control_version"], "feedback": _public_feedback(self.knowledge, item),
                    "feedback_revision": view["revision"], "blocked": view["blocked"] or not complete,
                    "block_reason": view["block_reason"] if complete else "Effective feedback exceeds the worker context limit; reconcile it before dispatch.",
                    "feedback_history_ref": view["feedback_history_ref"]}

    def _export_feedback(self, session_id, records, control):
        for item in records:
            _write_view(Path(_ref(self.knowledge, FEEDBACK_KIND, item["id"])), item)
        folder = _folder(self.knowledge, session_id)
        _write_view(folder / "feedback.json", sorted(records, key=lambda x: x["seq"]))
        _write_view(folder / "control.json", control)

    def _task_preview(self, task):
        value = {key: task.get(key) for key in ("id", "kind", "phase")}
        title = _small_text(task.get("title"), 120)
        if title:
            value["title"] = title
        value["record_ref"] = _ref(self.knowledge, "task", task["id"])
        return value

    def _worker_preview(self, record):
        request = record.get("request", {})
        value = {"id": record["id"], "task_id": record["task_id"], "assignment_id": request.get("id"),
                 "status": record.get("status"), "provider": record.get("provider"),
                 "record_ref": _ref(self.knowledge, "delegation", record["id"])}
        summary = _small_text(record.get("summary"))
        if summary:
            value["summary"] = summary
        blocker = _small_text(record.get("error"))
        if blocker:
            value["blocker"] = blocker
        for field in ("result", "feedback_revision", "feedback_substantive_revision"):
            if record.get(field):
                value[field + "_ref" if field == "result" else field] = record[field]
        profile = record.get("profile")
        if isinstance(profile, dict):
            value["profile"] = {key: profile[key] for key in ("id", "provider", "model", "effort") if key in profile}
        for field in ("candidate_id", "result_digest", "before", "after"):
            if record.get(field):
                value[field] = record[field]
        selection = _small_text(record.get("selection_reason"))
        if selection:
            value["selection_reason"] = selection
        assessment = record.get("assessment")
        if isinstance(assessment, dict):
            value["assessment"] = {key: assessment[key] for key in ("verdict", "reason") if key in assessment}
            value["assessment"]["ref"] = value["record_ref"] + "#/assessment"
        if record.get("status") == "candidate":
            value["next"] = "Inspect the staged result and evidence; submit an internal accept/reject assessment. No changes are applied yet."
        elif record.get("status") == "rejected":
            value["next"] = "Retry the same assignment or team; the router selects a stronger eligible profile within the remaining budget."
        return value

    def _current(self, task, locations, view):
        goal = task.get("goal") or {}
        development = task.get("kind") == "development"
        task_ref = _ref(self.knowledge, "task", task["id"])
        root = locations.get(task.get("locations", [None])[0], {}).get("root") if task.get("locations") else None
        goal_ref = (str(Path(root) / ".loop/goals" / task["goal_id"] / "contract.json")
                    if development and root and task.get("goal_id") else task_ref + "#/goal")
        criteria = goal.get("target", {}).get("acceptance", []) if development else goal.get("criteria", [])
        result = task.get("result") or {}
        checks = result.get("checks", [])
        completed = sum(x.get("satisfied") is True for x in checks)
        value = {**self._task_preview(task), "goal_ref": goal_ref, "goal_digest": digest(goal),
                 "acceptance": {"count": len(criteria), "ref": goal_ref + ("#/target/acceptance" if development else "/criteria")},
                 "decisions": {"count": len(goal.get("decisions", [])), "ref": goal_ref + ("#/decisions" if development else "/decisions")},
                 "feedback_revision": view["revision"], "blocked": view["blocked"] or task.get("phase") == "blocked",
                 "block_reason": view["block_reason"] or (_small_text(task.get("reason")) if task.get("phase") == "blocked" else None)}
        if not development:
            value["acceptance"]["passed"] = completed
            if task.get("phase") == "completed":
                completion = {"ref": task_ref + "#/result", "historical": True, "freshness": "not_rechecked"}
                recorded_feedback = result.get("feedback_revision")
                feedback_changed = recorded_feedback != view["revision"] and (
                    recorded_feedback is not None or view["feedback"] or view["scope_changed"])
                if feedback_changed or view["blocked"]:
                    completion["freshness"] = "stale_feedback"
                elif task.get("goal_seal") and task["goal_seal"] != digest(goal):
                    completion["freshness"] = "stale_goal"
                elif task.get("completion_observation"):
                    try:
                        observed = self.knowledge.observe([locations[key] for key in task["locations"]])
                        completion["freshness"] = ("current" if digest(observed) == task["completion_observation"]
                                                   else "stale_source")
                    except (KeyError, LoopError, OSError):
                        completion["freshness"] = "unavailable"
                value["completion"] = completion
        outcome = _small_text(goal.get("target", {}).get("outcome") if development else goal.get("outcome"), 400)
        if outcome:
            value["outcome"] = outcome
        if development:
            value["goal_id"] = task.get("goal_id")
            value["state_ref"] = str(Path(root) / ".loop/state" / task["goal_id"] / "state.json") if root else task_ref
        gate_path = result.get("gate")
        if gate_path:
            gate = {"ref": gate_path, "freshness": "not_rechecked"}
            try:
                report = read_json(Path(gate_path))
                gate["passed"] = report.get("passed")
                if report.get("feedback_revision") != view["revision"]:
                    # A legacy gate without a feedback stamp is valid only when
                    # there is no steering to consume; source freshness is still
                    # the existing development gate's responsibility.
                    if report.get("feedback_revision") is not None or view["feedback"] or view["scope_changed"]:
                        gate["freshness"] = "stale_feedback"
                if result.get("evidence_current") is False:
                    gate["freshness"] = "stale"
            except LoopError:
                gate["freshness"] = "unavailable"
            value["latest_gate"] = gate
        return value

    def context(self, session_id, task_id=None, limit=5):
        _valid_id(session_id, "session ID")
        if type(limit) is not int or not 1 <= limit <= MAX_VISIBLE:
            raise LoopError("Orchestration context limit must be 1..5")
        if task_id is not None:
            _valid_id(task_id, "task ID")
        with self.knowledge.connection() as db:
            # The transaction also serializes derived-view repair with feedback
            # writers.  Only an explicitly selected completed common task needs
            # its saved source observation checked; no provider is invoked.
            db.execute("BEGIN IMMEDIATE")
            session = _record(db, "session", session_id)
            control = _control(db, session_id)
            tasks = [x for x in _records(db, "task") if x["session_id"] == session_id]
            tasks.sort(key=lambda x: (x.get("phase") in ("completed", "aborted"), x["id"]))
            selected = next((x for x in tasks if x["id"] == task_id), None) if task_id else None
            if task_id and selected is None:
                raise LoopError("Context task must belong to this session")
            records = [x for x in _records(db, FEEDBACK_KIND) if x["session_id"] == session_id]
            locations = {x["id"]: x for x in _records(db, "location")}
            visible_ids = {task_id} if task_id else {x["id"] for x in tasks if x.get("phase") not in ("completed", "aborted")}
            workers = [x for x in _records(db, "delegation") if x["task_id"] in visible_ids]
            workers.sort(key=lambda x: (x.get("status") in ("candidate", "rejected", "running", "interrupted"),
                                        x.get("started_at", ""), x["id"]), reverse=True)
            task_views = [self._task_preview(x) for x in tasks]
            worker_views = [self._worker_preview(x) for x in workers]
            relevant_tasks = [x for x in tasks if task_id is None or x["id"] == task_id]
            feedback_views = sorted([entry for task in relevant_tasks
                                     for entry in _worker_view(self.knowledge, task, records, enforce=False)["feedback"]],
                                    key=lambda x: x["seq"], reverse=True)
            directory_ids = sorted({key for task in tasks if task["id"] in visible_ids for key in task.get("locations", [])})
            directories = [{key: locations[location_id].get(key) for key in ("id", "project_id", "root")}
                           for location_id in directory_ids if location_id in locations]
            folder = _folder(self.knowledge, session_id)
            index_name = task_id or "session"
            indexes = {"tasks": task_views, "workers": worker_views, "directories": directories}
            refs = {key: str(folder / (index_name + "-" + key + ".json")) for key in indexes}
            refs.update(feedback_history=str(folder / "feedback.json"), control=str(folder / "control.json"))
            for key, values in indexes.items():
                _write_view(Path(refs[key]), values)
            self._export_feedback(session_id, records, control)
            source = Path(__file__).resolve().parents[1]
            entrypoint = source / "loop.py" if (source / "loop.py").is_file() else source / "scripts/loop.py"
            capsule = {"schema_version": 1, "session_id": session_id, "provider": session["provider"],
                       "entrypoint": str(entrypoint), "knowledge_home": str(self.knowledge.home),
                       "enabled": session.get("harness", {}).get("enabled", False),
                       "control_version": control["control_version"], "task_id": task_id,
                       "tasks": task_views[:limit], "workers": worker_views[:limit],
                       "directories": directories[:limit], "feedback": feedback_views[:limit],
                       "counts": {**{key: len(values) for key, values in indexes.items()}, "feedback": len(feedback_views)},
                       "refs": refs,
                       "next": "Select the relevant unfinished task from this session; use its references for scoped assignments."}
            if selected:
                view = _worker_view(self.knowledge, selected, records, enforce=False)
                if _size(view) > MAX_CONTEXT_BYTES:
                    view = {**view, "blocked": True, "block_reason": "Effective feedback exceeds the worker context limit; reconcile it before dispatch."}
                capsule["current"] = self._current(selected, locations, view)
                capsule["feedback_revision"] = view["revision"]
                capsule["next"] = (view["block_reason"] if view["blocked"] else
                    {"goal_setting": "Define the goal and completion criteria from current decisions before starting.",
                     "working": "Assess any staged candidate against its goal before continuing; dispatch or retry scoped assignments with current feedback.",
                     "blocked": "Inspect the recorded blocker and recover only within existing authority.",
                     "completed": "Report the recorded result; refresh development evidence before claiming current completion.",
                     "aborted": "Report the stopped task; do not dispatch new work for it."}.get(selected.get("phase"), "Inspect the linked authoritative task state."))
                pending = [worker for worker in workers if worker.get("status") in ("candidate", "rejected")]
                if pending and not view["blocked"] and selected.get("phase") not in ("completed", "aborted", "blocked"):
                    capsule["next"] = ("Inspect candidate artifacts and submit assess with source-backed checks before applying work."
                                       if any(worker["status"] == "candidate" for worker in pending) else
                                       "Retry the rejected assignment or team unchanged; only that assignment is escalated within its remaining budget.")
                if selected.get("phase") == "completed":
                    current = capsule["current"]
                    stale = (view["blocked"] or current.get("completion", {}).get("freshness", "").startswith("stale")
                             or current.get("completion", {}).get("freshness") == "unavailable"
                             or current.get("latest_gate", {}).get("freshness", "").startswith("stale"))
                    if stale:
                        capsule["next"] = "Report the prior result as historical; any required rework needs a new task or goal linked to this result."
            elif not tasks:
                capsule["next"] = "Set the requested goal and create its task in this session."
            if not capsule["enabled"]:
                capsule["next"] = "Harness is disabled; do not automatically dispatch work."
            # Drop whole navigation entries, with counts and full-index references
            # retained.  No authored decision or evidence string is cut to fit.
            groups = ("tasks", "workers", "directories", "feedback")
            while _size(capsule) > MAX_CONTEXT_BYTES and any(capsule[key] for key in groups):
                largest = max((key for key in groups if capsule[key]), key=lambda key: _size(capsule[key]))
                capsule[largest].pop()
            if _size(capsule) > MAX_CONTEXT_BYTES:
                raise LoopError("Orchestration control references exceed the 16 KiB context limit")
            # A first in-flight attempt is committed before its derived view is
            # normally exported.  Repair only the bounded visible recovery links
            # from this authoritative snapshot, including after a hard shutdown.
            worker_records = {item["id"]: item for item in workers}
            for preview in capsule["workers"]:
                _write_view(Path(preview["record_ref"]), worker_records[preview["id"]])
            _write_view(folder / (index_name + "-context.json"), capsule)
            return capsule
