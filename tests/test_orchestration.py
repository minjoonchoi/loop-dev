from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from loop_dev.harness import Harness
from loop_dev.knowledge import KnowledgeBase
from loop_dev.orchestration import MAX_CONTEXT_BYTES, Orchestration, worker_context
from loop_dev.storage import LoopError, digest, read_json, write_json


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.kb = KnowledgeBase(self.base / "knowledge")
        self.session = Harness(self.kb).on("codex")["session_id"]
        self.location = self.kb.attach(self.root)
        self.orchestration = Orchestration(self.kb)
        self.task = self.make_task("task-a")

    def make_task(self, key, session=None, **overrides):
        value = {"id": key, "kind": "planning", "title": "Plan the requested change", "phase": "working",
                 "session_id": session or self.session, "provider": "codex", "worker": "codex",
                 "locations": [self.location["id"]], "project_ids": [self.location["project_id"]],
                 "goal": {"outcome": "A concrete implementation plan.", "deliverable": "A plan with source references.",
                          "criteria": [{"id": "C1", "description": "Identify affected interfaces."}],
                          "decisions": [], "open_questions": []},
                 "notes": [], "result": None, "version_token": "unchanged-token",
                 "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00",
                 **overrides}
        self.kb.put("task", value)
        return value

    def payload(self, key="input-1", task=None, **overrides):
        return {"id": key, "task_id": task or self.task["id"], "kind": "constraint",
                "summary": "Keep the public interface compatible.", "original_input_ref": "conversation:user-input-1",
                "applies_to": "The current task's public interface", "supersedes": [], **overrides}

    def save(self, payload=None, expected=None):
        version = self.orchestration.context(self.session)["control_version"] if expected is None else expected
        return self.orchestration.feedback(self.session, self.payload() if payload is None else payload, version)

    def test_durable_capsule_restores_binding_goal_and_evidence_references(self):
        initial = self.orchestration.context(self.session, self.task["id"])
        self.assertEqual(initial["control_version"], 0)
        self.assertTrue(initial["enabled"])
        self.assertEqual(initial["current"]["acceptance"]["count"], 1)
        self.assertEqual(initial["current"]["outcome"], self.task["goal"]["outcome"])
        self.assertEqual(initial["directories"][0]["root"], str(self.root))
        for path in initial["refs"].values():
            self.assertTrue(Path(path).is_file())
        self.assertEqual(self.kb.list("orchestration"), [])
        self.save()
        first = self.orchestration.context(self.session, self.task["id"])
        restored = Orchestration(KnowledgeBase(self.kb.home)).context(self.session, self.task["id"])
        self.assertEqual(first, restored)
        self.assertEqual(first["feedback"][0]["original_input_ref"], "conversation:user-input-1")
        self.assertEqual(first["feedback"][0]["evidence"]["status"], "reference_only")
        self.assertEqual(read_json(Path(first["current"]["record_ref"]))["goal"], self.task["goal"])
        self.assertEqual(self.kb.get("task", self.task["id"]), self.task)

    def test_feedback_cas_idempotence_immutable_ids_and_user_basis(self):
        receipt = self.save(expected=0)
        self.assertEqual(receipt["control_version"], 1)
        self.assertFalse(receipt["reused"])
        original_records = self.kb.list("feedback")
        duplicate = self.save(expected=0)
        self.assertTrue(duplicate["reused"])
        self.assertEqual(duplicate["feedback_revision"], receipt["feedback_revision"])
        self.assertEqual(self.kb.list("feedback"), original_records)
        self.assertEqual(self.kb.history("orchestration", self.session), [])
        with self.assertRaisesRegex(LoopError, "concurrently"):
            self.save(self.payload("input-2"), expected=0)
        with self.assertRaisesRegex(LoopError, "immutable"):
            self.save(self.payload(summary="Change the public interface."), expected=1)
        with self.assertRaisesRegex(LoopError, "exactly"):
            self.save({**self.payload("input-3"), "basis": "delegated"}, expected=1)
        self.assertEqual(self.kb.list("feedback"), original_records)
        self.assertEqual(receipt["feedback"]["basis"], "user")

    def test_task_and_session_isolation_including_same_feedback_id(self):
        other_task = self.make_task("task-b")
        before = worker_context(self.kb, other_task["id"])
        self.save()
        self.assertEqual(worker_context(self.kb, other_task["id"]), before)
        other_session = Harness(self.kb).on("codex")["session_id"]
        foreign = self.make_task("task-c", other_session)
        with self.assertRaisesRegex(LoopError, "belong"):
            self.save(self.payload("input-foreign", foreign["id"]), expected=1)
        with self.assertRaisesRegex(LoopError, "belong"):
            self.orchestration.context(other_session, self.task["id"])
        saved = self.orchestration.feedback(other_session, self.payload(task=foreign["id"]), 0)
        self.assertEqual(saved["control_version"], 1)
        self.assertEqual(len(self.kb.list("feedback")), 2)
        self.assertEqual(self.orchestration.context(self.session)["counts"]["tasks"], 2)
        self.assertEqual(self.orchestration.context(other_session)["counts"]["tasks"], 1)

    def test_concurrent_feedback_writers_cannot_both_accept_the_same_version(self):
        def attempt(key):
            try:
                return self.orchestration.feedback(self.session, self.payload(key), 0)["saved"]
            except LoopError as exc:
                self.assertIn("concurrently", str(exc))
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("first-writer", "second-writer")))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.orchestration.context(self.session)["control_version"], 1)
        self.assertEqual(len(self.kb.list("feedback")), 1)

    def test_explicit_supersession_preserves_history_and_rejects_other_task(self):
        self.save()
        other_task = self.make_task("task-b")
        self.save(self.payload("other-input", other_task["id"]))
        before = self.orchestration.context(self.session)["control_version"]
        with self.assertRaisesRegex(LoopError, "same task"):
            self.save(self.payload("bad", supersedes=["other-input"]), before)
        with self.assertRaisesRegex(LoopError, "same task"):
            self.save(self.payload("missing", supersedes=["not-recorded"]), before)
        self.save(self.payload("correction", kind="correction", summary="Keep only the documented interface compatible.",
                               supersedes=["input-1"]))
        view = worker_context(self.kb, self.task["id"])
        self.assertEqual([x["id"] for x in view["feedback"]], ["correction"])
        self.assertTrue(view["requires_worker"])
        history = read_json(Path(view["feedback_history_ref"]))
        self.assertEqual(len(history), 3)
        self.assertEqual(history[-1]["supersedes"], ["input-1"])

    def test_pause_resume_is_task_specific_and_scope_changes_require_new_task(self):
        other_task = self.make_task("task-b")
        before = worker_context(self.kb, self.task["id"])
        self.save(self.payload("pause", kind="pause", summary="Pause this task."))
        paused = worker_context(self.kb, self.task["id"])
        self.assertTrue(paused["paused"])
        self.assertTrue(paused["blocked"])
        self.assertNotEqual(before["revision"], paused["revision"])
        self.assertEqual(before["substantive_revision"], paused["substantive_revision"])
        self.assertFalse(worker_context(self.kb, other_task["id"])["blocked"])
        self.save(self.payload("resume", kind="resume", summary="Resume the same task."))
        resumed = worker_context(self.kb, self.task["id"])
        self.assertFalse(resumed["blocked"])
        self.assertEqual([x["kind"] for x in resumed["feedback"]], ["resume"])
        self.save(self.payload("scope", kind="scope_change", summary="Include a different public API."))
        self.save(self.payload("retract", kind="correction", summary="Reconsider that scope request.", supersedes=["scope"]))
        self.save(self.payload("resume-again", kind="resume", summary="Resume this task."))
        blocked = worker_context(self.kb, self.task["id"])
        self.assertTrue(blocked["scope_changed"])
        self.assertTrue(blocked["blocked"])
        self.assertIn("new task", blocked["block_reason"])
        self.assertEqual(self.kb.get("task", self.task["id"]), self.task)
        self.assertFalse(worker_context(self.kb, other_task["id"])["scope_changed"])

    def test_local_original_input_is_pinned_and_changed_or_missing_evidence_blocks(self):
        original = self.base / "user-input.txt"
        original.write_text("Keep the public interface compatible.\n", encoding="utf-8")
        receipt = self.save(self.payload(original_input_ref=str(original)))
        first = worker_context(self.kb, self.task["id"])
        self.assertFalse(first["blocked"])
        self.assertEqual(first["feedback"][0]["evidence"]["status"], "current")
        self.assertNotIn("Keep the public interface compatible.\n", json.dumps(first))
        original.write_text("Change the interface.\n", encoding="utf-8")
        stale = worker_context(self.kb, self.task["id"])
        self.assertTrue(stale["blocked"])
        self.assertNotEqual(stale["revision"], first["revision"])
        self.assertEqual(stale["feedback"][0]["evidence"]["status"], "stale")
        self.assertEqual(self.save(self.payload(original_input_ref=str(original)), expected=0)["control_version"], receipt["control_version"])
        self.assertEqual(worker_context(self.kb, self.task["id"])["feedback"][0]["evidence"]["sha256"], first["feedback"][0]["evidence"]["sha256"])
        original.unlink()
        unavailable = worker_context(self.kb, self.task["id"])
        self.assertTrue(unavailable["blocked"])
        self.assertEqual(unavailable["feedback"][0]["evidence"]["status"], "unavailable")
        with self.assertRaisesRegex(LoopError, "unavailable"):
            self.save(self.payload("missing-file", original_input_ref=str(original)))
        self.assertEqual(self.orchestration.context(self.session)["control_version"], 1)

    def test_worker_context_existing_transaction_never_reopens_or_writes(self):
        self.save()
        expected = worker_context(self.kb, self.task["id"])
        with self.kb.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.total_changes
            with patch.object(self.kb, "connection", side_effect=AssertionError("nested connection")):
                self.assertEqual(worker_context(self.kb, self.task["id"], db), expected)
            self.assertEqual(db.total_changes, count)

    def test_large_complete_feedback_blocks_worker_but_capsule_remains_bounded(self):
        for index in range(12):
            self.save(self.payload("long-" + str(index), summary="요" * 280,
                                   applies_to="범" * 400, original_input_ref="conversation:" + "x" * 1800), expected=index)
        with self.assertRaisesRegex(LoopError, "16 KiB"):
            worker_context(self.kb, self.task["id"])
        capsule = self.orchestration.context(self.session, self.task["id"])
        self.assertTrue(capsule["current"]["blocked"])
        self.assertEqual(capsule["counts"]["feedback"], 12)
        self.assertLessEqual(len(capsule["feedback"]), 5)
        self.assertLessEqual(len((json.dumps(capsule, ensure_ascii=False, indent=2) + "\n").encode()), MAX_CONTEXT_BYTES)
        self.save(self.payload("reconciled", supersedes=["long-" + str(index) for index in range(12)]), expected=12)
        self.assertFalse(worker_context(self.kb, self.task["id"])["blocked"])

    def test_main_context_bounds_history_and_never_embeds_logs_notes_or_outputs(self):
        marker = "PRIVATE-LARGE-BODY-" * 1000
        for index in range(80):
            self.make_task("history-" + str(index), phase="completed", notes=[{"text": marker, "evidence": "log.txt"}],
                           result={"summary": "Recorded work.", "outputs": [marker]})
        with self.kb.connection() as db:
            for index in range(25):
                self.kb.put("delegation", {"id": "worker-" + str(index), "task_id": self.task["id"],
                    "request": {"id": "assignment-" + str(index), "instruction": marker},
                    "status": "done", "provider": "codex", "summary": "Assignment inspected.",
                    "started_at": "2026-01-01T00:00:00+00:00", "stdout": marker, "outputs": [marker]}, db)
        capsule = self.orchestration.context(self.session, self.task["id"], 5)
        encoded = json.dumps(capsule, ensure_ascii=False, indent=2)
        self.assertLessEqual(len((encoded + "\n").encode()), MAX_CONTEXT_BYTES)
        self.assertNotIn("PRIVATE-LARGE-BODY", encoded)
        self.assertEqual(capsule["counts"]["tasks"], 81)
        self.assertEqual(capsule["counts"]["workers"], 25)
        self.assertLessEqual(len(capsule["tasks"]), 5)
        self.assertEqual(len(capsule["workers"]), 5)
        self.assertEqual(len(read_json(Path(capsule["refs"]["tasks"]))), 81)
        self.assertEqual(len(read_json(Path(capsule["refs"]["workers"]))), 25)
        self.assertEqual(capsule, self.orchestration.context(self.session, self.task["id"]))

    def test_capsule_repairs_only_visible_inflight_worker_recovery_records(self):
        records = {}
        with self.kb.connection() as db:
            for index in range(6):
                item = {"id": "inflight-" + str(index), "task_id": self.task["id"],
                        "request": {"id": "assignment-" + str(index), "instruction": "Inspect the assigned interface."},
                        "status": "running", "provider": "codex", "summary": "Worker execution in progress.",
                        "started_at": "2026-01-01T00:00:00+00:00", "directory": str(self.base / "runs" / str(index))}
                records[item["id"]] = item
                # Initial delegation persistence deliberately skips document().
                self.kb.put("delegation", item, db)
        view_dir = self.kb.home / "views/delegation"
        self.assertFalse(view_dir.exists())
        capsule = self.orchestration.context(self.session, self.task["id"])
        self.assertEqual(capsule["counts"]["workers"], 6)
        self.assertEqual(len(capsule["workers"]), 5)
        visible = {x["id"] for x in capsule["workers"]}
        self.assertEqual({p.stem for p in view_dir.glob("*.json")}, visible)
        for preview in capsule["workers"]:
            path = Path(preview["record_ref"])
            self.assertEqual(read_json(path), records[preview["id"]])
            path.unlink()
        # A fresh process can restore the same links after an interrupted run.
        restored = Orchestration(KnowledgeBase(self.kb.home)).context(self.session, self.task["id"])
        self.assertEqual(restored, capsule)
        for preview in restored["workers"]:
            self.assertEqual(read_json(Path(preview["record_ref"])), records[preview["id"]])
        self.assertEqual({p.stem for p in view_dir.glob("*.json")}, visible)

    def test_development_gate_reports_feedback_staleness_without_claiming_source_freshness(self):
        gate_path = self.base / "gate.json"
        dev = self.make_task("dev-task", kind="development", goal_id="goal-one", execution_phase="completed",
                             phase="completed", goal={"target": {"outcome": "Implement the required behavior.",
                                "acceptance": [{"id": "A1", "scenario": "Check behavior."}]}, "decisions": []},
                             result={"gate": str(gate_path), "evidence_current": True})
        first_revision = worker_context(self.kb, dev["id"])["revision"]
        write_json(gate_path, {"passed": True, "feedback_revision": first_revision})
        initial = self.orchestration.context(self.session, dev["id"])
        self.assertEqual(initial["current"]["latest_gate"]["freshness"], "not_rechecked")
        self.save(self.payload("dev-feedback", dev["id"]))
        current = self.orchestration.context(self.session, dev["id"])
        self.assertEqual(current["current"]["latest_gate"]["freshness"], "stale_feedback")
        self.assertEqual(current["current"]["acceptance"]["count"], 1)
        self.assertTrue(current["current"]["goal_ref"].endswith("/.loop/goals/goal-one/contract.json"))

    def test_completed_common_result_is_historical_after_feedback_or_source_changes(self):
        source = self.root / "interface.txt"
        source.write_text("Original public interface.\n", encoding="utf-8")
        revision = worker_context(self.kb, self.task["id"])["revision"]
        completed = self.make_task(self.task["id"], phase="completed",
            goal_seal=digest(self.task["goal"]), completion_observation=digest(self.kb.observe([self.location])),
            result={"summary": "The requested plan is recorded.", "outputs": ["conversation:plan"],
                    "feedback_revision": revision,
                    "checks": [{"criterion_id": "C1", "satisfied": True, "evidence": "The plan names the interface."}]})
        initial = self.orchestration.context(self.session, self.task["id"])
        self.assertEqual(initial["current"]["completion"]["freshness"], "current")
        source.write_text("Changed public interface.\n", encoding="utf-8")
        changed_source = self.orchestration.context(self.session, self.task["id"])
        self.assertEqual(changed_source["current"]["completion"]["freshness"], "stale_source")
        self.assertIn("new task or goal linked", changed_source["next"])
        source.write_text("Original public interface.\n", encoding="utf-8")
        self.save(self.payload("after-completion", kind="correction", summary="Account for the undocumented interface."))
        changed_feedback = self.orchestration.context(self.session, self.task["id"])
        self.assertEqual(changed_feedback["current"]["completion"]["freshness"], "stale_feedback")
        self.assertTrue(changed_feedback["current"]["completion"]["historical"])
        self.assertIn("new task or goal linked", changed_feedback["next"])
        self.save(self.payload("pause-completed", kind="pause", summary="Pause any further work."))
        paused = self.orchestration.context(self.session, self.task["id"])
        self.assertIn("new task or goal linked", paused["next"])
        self.assertNotIn("resume", paused["next"])
        self.assertEqual(self.kb.get("task", self.task["id"]), completed)

    def test_malformed_requests_do_not_mutate_control_or_tasks(self):
        for payload in ({}, self.payload(supersedes=["input-1"]), self.payload(kind="worker_message"),
                        self.payload(summary="TODO"), self.payload(original_input_ref="")):
            with self.subTest(payload=payload), self.assertRaises(LoopError):
                self.save(payload, 0)
        for limit in (0, 6, True):
            with self.assertRaises(LoopError):
                self.orchestration.context(self.session, limit=limit)
        with self.assertRaises(LoopError):
            self.orchestration.feedback(self.session, self.payload(), True)
        self.assertEqual(self.kb.list("feedback"), [])
        self.assertEqual(self.kb.list("orchestration"), [])
        self.assertEqual(self.kb.get("task", self.task["id"]), self.task)


if __name__ == "__main__":
    unittest.main()
