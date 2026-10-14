from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from loop_dev import providers
from loop_dev.orchestration import Orchestration, worker_context
from loop_dev.storage import LoopError, locked, read_json, write_json
from loop_dev.work import Work
from loop_dev.team import Team
from scripts.install import install
from support import FAKE_CLI, ProjectTest, accept_fixture, fixture_assessment
from test_delegation import WORKER_BRANCH, assignment
from test_team import plan as team_plan


SOURCE = Path(__file__).resolve().parents[1]


class TeamIntegrationTests(ProjectTest):
    def setUp(self):
        super().setUp()
        worker = WORKER_BRANCH.replace('    if os.getenv("LOOP_WORKER_RESULT"):',
                                      '    if "team" in data:\n        value["messages"] = []\n'
                                      '    if os.getenv("LOOP_WORKER_RESULT"):')
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(',
                                                       worker + 'data = json.loads(prompt.split(', 1))
        self.kb = self.engine.knowledge
        self.task_id = self.engine.refresh()["work_id"]
        self.session_id = self.state()["session_id"]
        self.orchestration = Orchestration(self.kb)

    def cli(self, *args, entry=None, root=None, code=0):
        result = subprocess.run([sys.executable, str(entry or SOURCE / "scripts/loop.py"),
                                 "--knowledge-home", str(self.kb.home), "--root", str(root or self.root), *map(str, args)],
                                cwd=Path(self.temp.name), capture_output=True, text=True)
        if code is None:
            self.assertIn(result.returncode, (0, 1), result.stderr or result.stdout)
        else:
            self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        return json.loads(result.stdout if code in (0, 1, None) else result.stderr)

    def input(self, name, value):
        path = Path(self.temp.name) / (name + ".json")
        write_json(path, value)
        return path

    def plan(self, name="value-team", goal_criteria=("A1",)):
        return team_plan("inspect-a", "inspect-b", team_id=name, max_turns=4, goal_criteria=goal_criteria)

    def complete_cli_team(self, *args, entry=None, root=None, task_id=None):
        """Use the installed CLI for every explicit fixture assessment and resume."""
        for _ in range(6):
            result = self.cli(*args, entry=entry, root=root, code=None)
            if result["status"] != "awaiting_assessment":
                return result
            ledger = read_json(Path(result["artifact"]))
            team = Team(self.kb, result["task_id"], root or self.root)
            record = team.assignment(ledger["inflight"]["member"])
            payload = self.input(record["candidate_id"], fixture_assessment(self.kb, team.worker.response(record)))
            if task_id:
                self.cli("work", "assess", task_id, "--input", payload, "--path", root,
                         entry=entry, root=root)
            else:
                self.cli("assess", "demo", "--input", payload, entry=entry, root=root)
        self.fail("The fixture team exceeded its immutable turn budget")

    def feedback(self, kind, name, task_id=None, session_id=None, supersedes=()):
        session_id = session_id or self.session_id
        control = self.orchestration.context(session_id)["control_version"]
        return self.orchestration.feedback(session_id, {
            "id": name, "task_id": task_id or self.task_id, "kind": kind,
            "summary": "User requested " + name + ".", "original_input_ref": "conversation:user-" + name,
            "applies_to": "This task only.", "supersedes": list(supersedes)}, control)

    def common_task(self, roots=None, start=True):
        root = Path(self.temp.name) / "documents"
        if not root.exists():
            root.mkdir()
            (root / "value.py").write_text("VALUE = 1\n")
            (root / "check.py").write_text("from value import VALUE\nassert VALUE == 1\n")
        work = Work(self.kb)
        task = work.open("explanation", "Explain the value", "claude", roots or [root], self.session_id, "codex")["task"]
        work.define(task["id"], {"outcome": "Explain the actual value.", "deliverable": "A source-backed explanation.",
                                  "criteria": [{"id": "C1", "description": "Cite the value definition."}],
                                  "decisions": [], "open_questions": []})
        task = work.start(task["id"])["task"] if start else work.load(task["id"])
        return work, task, root

    @staticmethod
    def result():
        return {"summary": "The value is explained with its source reference.", "outputs": ["conversation:value-answer"],
                "checks": [{"criterion_id": "C1", "satisfied": True, "evidence": "value.py:1 defines VALUE."}]}

    def test_installed_team_cli_routes_and_preserves_repository_lock(self):
        original = (self.root / "AGENTS.md").read_text()
        install(self.root)
        entry = self.root / ".loop/loop.py"
        plan = self.input("team", self.plan())
        with locked(self.root):
            error = self.cli("team", "demo", "--input", plan, entry=entry, code=2)
        self.assertIn("Another loop command", error["error"])
        result = self.complete_cli_team("team", "demo", "--input", plan, entry=entry)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["member_count"], 2)
        self.assertEqual(self.state()["phase"], "draft")
        instructions = (self.root / "AGENTS.md").read_text()
        self.assertTrue(instructions.startswith(original))
        self.assertIn("Before every input, worker return and compaction recovery", instructions)
        self.assertIn("only orchestrates", instructions)
        self.assertIn("no global hooks", instructions)
        self.assertTrue((self.root / ".loop/loop_dev/team.py").is_file())
        self.assertTrue((self.root / ".loop/loop_dev/orchestration.py").is_file())

    def test_installed_common_team_and_orchestration_work_outside_git(self):
        install(self.root)
        entry = self.root / ".loop/loop.py"
        work, task, root = self.common_task()
        plan = self.input("common-team", self.plan("document-team", ("C1",)))
        with locked(self.kb.home / "task-locks" / task["id"]):
            error = self.cli("work", "team", task["id"], "--input", plan, "--path", root,
                             entry=entry, root=root, code=1)
        self.assertIn("Another loop command", error["error"])
        self.assertEqual(error["task_worker_calls"], 0)
        self.assertEqual(error["turns"], 0)
        result = self.complete_cli_team("work", "team", task["id"], "--input", plan, "--path", root,
                                        entry=entry, root=root, task_id=task["id"])
        self.assertTrue(result["passed"], result)
        self.assertEqual(work.load(task["id"])["phase"], "working")
        context = self.cli("orchestration", "context", self.session_id, "--task", task["id"], "--limit", 1,
                           entry=entry, root=root)
        feedback = self.input("feedback", {"id": "pause-doc", "task_id": task["id"], "kind": "pause",
                              "summary": "Pause this explanation.", "original_input_ref": "conversation:user-pause",
                              "applies_to": "This explanation only.", "supersedes": []})
        self.cli("orchestration", "feedback", self.session_id, "--input", feedback,
                 "--expected-control-version", context["control_version"], entry=entry, root=root)
        self.assertTrue(worker_context(self.kb, task["id"])["paused"])
        self.assertFalse((root / ".git").exists())

    def test_common_team_rejects_unowned_or_ambiguous_path(self):
        work, task, root = self.common_task()
        foreign = Path(self.temp.name) / "foreign"
        foreign.mkdir()
        with self.assertRaisesRegex(LoopError, "does not belong"):
            work.team(task["id"], self.plan(), foreign)
        multi, task, _ = self.common_task([root, foreign])
        with self.assertRaisesRegex(LoopError, "Select --path"):
            multi.team(task["id"], self.plan())

    def test_pause_and_sticky_scope_change_block_final_gate(self):
        self.running()
        self.assertTrue(self.engine.gate("M1")["passed"])
        self.feedback("pause", "pause-value")
        with self.assertRaisesRegex(LoopError, "paused"):
            self.engine.gate()
        self.feedback("resume", "resume-value")
        self.feedback("scope_change", "new-scope")
        self.feedback("resume", "resume-after-scope")
        with self.assertRaisesRegex(LoopError, "Scope changed"):
            self.engine.gate()
        self.assertEqual(self.state()["phase"], "running")
        self.assertNotIn("final", self.state()["attempts"])

    def test_final_gate_requires_worker_to_consume_new_substantive_feedback(self):
        self.running()
        self.assertTrue(self.engine.gate("M1")["passed"])
        self.feedback("correction", "cite-consumer")
        with self.assertRaisesRegex(LoopError, "successful worker"):
            self.engine.gate()
        worker = accept_fixture(self.kb, self.engine.assess, self.engine.delegate(assignment("consume-correction")))
        self.assertTrue(worker["passed"], worker)
        self.feedback("pause", "temporary-pause")
        self.feedback("resume", "continue-value")
        result = self.engine.gate()
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["feedback_revision"], worker_context(self.kb, self.task_id)["revision"])
        self.assertEqual(result["review"]["provider"], "codex")

    def test_feedback_during_final_review_invalidates_acceptance(self):
        self.running()
        self.assertTrue(self.engine.gate("M1")["passed"])
        original = worker_context(self.kb, self.task_id)["revision"]
        review = providers.review

        def changed(*args, **kwargs):
            result = review(*args, **kwargs)
            self.feedback("correction", "new-review-input")
            return result

        with patch("loop_dev.engine.providers.review", side_effect=changed):
            result = self.engine.gate()
        self.assertFalse(result["passed"])
        self.assertIn("feedback changed", result["error"])
        self.assertEqual(result["feedback_revision"], original)
        self.assertEqual(self.state()["phase"], "blocked")
        prompt = (Path(result["review"]["artifact"]).parent / "prompt.txt").read_text()
        packet = json.loads(prompt.split("REVIEW DATA:\n", 1)[1])
        self.assertEqual(packet["feedback_revision"], original)
        self.assertEqual(packet["user_feedback"]["revision"], original)
        self.assertFalse(read_json(Path(self.state()["latest_gate"]))["passed"])

    def test_plan_review_and_freeze_reject_new_feedback(self):
        review = providers.review

        def changed(*args, **kwargs):
            result = review(*args, **kwargs)
            self.feedback("constraint", "retain-source")
            return result

        with patch("loop_dev.engine.providers.review", side_effect=changed):
            result = self.engine.plan_review()
        self.assertFalse(result["passed"])
        self.assertIn("feedback changed", result["error"])
        self.assertNotIn("plan_review", self.state())
        self.assertTrue(self.engine.plan_review()["passed"])
        self.feedback("correction", "clarify-source")
        with self.assertRaisesRegex(LoopError, "stale user feedback"):
            self.engine.freeze()

    def test_new_feedback_marks_completed_development_evidence_stale(self):
        self.running()
        self.engine.gate("M1")
        self.assertTrue(self.engine.gate()["passed"])
        self.feedback("correction", "later-correction")
        self.assertFalse(self.engine.refresh()["evidence_current"])

    def test_common_finish_rejects_controls_and_unconsumed_feedback(self):
        work, task, _ = self.common_task()
        self.feedback("pause", "pause-common", task["id"])
        with self.assertRaisesRegex(LoopError, "paused"):
            work.finish(task["id"], self.result())
        self.feedback("resume", "resume-common", task["id"])
        self.feedback("constraint", "cite-value", task["id"])
        with self.assertRaisesRegex(LoopError, "successful worker"):
            work.finish(task["id"], self.result())
        delegated = work.delegate(task["id"], assignment("consume-common", goal_criteria=("C1",)))
        self.assertTrue(accept_fixture(self.kb, lambda payload: work.assess(task["id"], payload), delegated)["passed"])
        self.assertTrue(work.finish(task["id"], self.result())["passed"])

    def test_common_start_and_resume_cannot_bypass_task_controls(self):
        work, task, _ = self.common_task(start=False)
        self.feedback("pause", "pause-before-start", task["id"])
        with self.assertRaisesRegex(LoopError, "paused"):
            work.start(task["id"])
        self.assertEqual(work.load(task["id"])["phase"], "goal_setting")
        work.stop(task["id"], "Await the explicit user resume.")
        with self.assertRaisesRegex(LoopError, "paused"):
            work.resume(task["id"])
        self.feedback("resume", "resume-before-start", task["id"])
        work.resume(task["id"])
        self.feedback("scope_change", "changed-before-start", task["id"])
        with self.assertRaisesRegex(LoopError, "Scope changed"):
            work.start(task["id"])

    def test_common_finish_fences_feedback_at_the_completion_commit(self):
        work, task, _ = self.common_task()
        save = work.save

        def changed(*args, **kwargs):
            self.feedback("constraint", "arrived-at-finish", task["id"])
            return save(*args, **kwargs)

        with patch.object(work, "save", side_effect=changed):
            with self.assertRaisesRegex(LoopError, "feedback changed"):
                work.finish(task["id"], self.result())
        self.assertEqual(work.load(task["id"])["phase"], "working")

    def test_tampered_worker_result_cannot_acknowledge_feedback(self):
        work, task, _ = self.common_task()
        self.feedback("correction", "actual-evidence", task["id"])
        delegated = work.delegate(task["id"], assignment("consume-evidence", goal_criteria=("C1",)))
        delegated = accept_fixture(self.kb, lambda payload: work.assess(task["id"], payload), delegated)
        self.assertTrue(delegated["passed"], delegated)
        path = Path(delegated["assignment"]["result"])
        value = read_json(path)
        value["summary"] = "A changed receipt cannot establish consumed user feedback."
        write_json(path, value)
        with self.assertRaisesRegex(LoopError, "worker result is missing or changed"):
            work.finish(task["id"], self.result())
