from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from loop_dev.orchestration import MAX_CONTEXT_BYTES, Orchestration
from loop_dev.harness import Harness
from loop_dev.storage import LoopError, file_hash, locked, read_json, write_json
from loop_dev.work import Work
from scripts.install import install
from support import FAKE_CLI, ProjectTest
from test_delegation import WORKER_BRANCH, assignment


SOURCE = Path(__file__).resolve().parents[1]
POLICY = {"version": 1, "profiles": [
    {"id": provider + "-" + level, "provider": provider, "model": provider + "-test-" + level,
     "effort": level, "capability_rank": rank, "cost_rank": rank}
    for provider in ("claude", "codex") for level, rank in (("low", 1), ("medium", 2), ("high", 3))],
    "requirements": {"low": 1, "medium": 2, "high": 3}}


class RoutingIntegrationTests(ProjectTest):
    def setUp(self):
        super().setUp()
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(',
                WORKER_BRANCH + 'data = json.loads(prompt.split(', 1))
        self.kb = self.engine.knowledge
        contract = read_json(self.engine.goal_dir / "contract.json")
        contract["execution"]["routing"] = copy.deepcopy(POLICY)
        write_json(self.engine.goal_dir / "contract.json", contract)
        self.task_id = self.engine.refresh()["work_id"]
        self.session_id = self.state()["session_id"]
        Harness(self.kb).on("claude", self.session_id)

    def cli(self, *args, entry=None, root=None, code=0):
        result = subprocess.run([sys.executable, str(entry or SOURCE / "scripts/loop.py"),
                                 "--knowledge-home", str(self.kb.home), "--root", str(root or self.root), *map(str, args)],
                                cwd=self.temp.name, capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stderr or result.stdout)
        return json.loads(result.stdout if code in (0, 1) else result.stderr)

    def input(self, name, value):
        path = Path(self.temp.name) / (name + ".json")
        write_json(path, value)
        return path

    def request(self, name="inspect-value", goal="A1"):
        request = assignment(name)
        request["planning"] = {"goal_criteria": [goal], "rationale": "One small explanation has one source and one completion check.",
                               "verification": "Compare the stated value with its source definition.",
                               "complexity": "low", "risk": "low", "uncertainty": "low", "evidence": []}
        return request

    def assessment(self, staged, root=None, verdict="accept"):
        record = self.kb.get("delegation", staged["assignment"]["id"])
        source = (root or self.root) / "value.py"
        evidence = [{"ref": str(source), "sha256": file_hash(source)}]
        checks = [{"scope": scope, "criterion_id": key, "satisfied": verdict == "accept", "evidence": evidence}
                  for scope, keys in (("assignment", [x["id"] for x in record["request"]["criteria"]]),
                                      ("goal", record["request"]["planning"]["goal_criteria"])) for key in keys]
        return {"assignment_id": record["request"]["id"], "candidate_id": record["candidate_id"],
                "result_digest": record["result_digest"], "verdict": verdict,
                "reason": "accepted" if verdict == "accept" else "incorrect",
                "summary": "The source definition supports the explanation." if verdict == "accept" else
                           "The candidate explanation does not support the requested behavior.", "checks": checks}

    def common_task(self, explicit=True):
        root = Path(self.temp.name) / "documents"
        root.mkdir(exist_ok=True)
        (root / "value.py").write_text("VALUE = 1\n")
        (root / "check.py").write_text("from value import VALUE\nassert VALUE == 1\n")
        work = Work(self.kb)
        task = work.open("explanation", "Explain the actual value", "claude", [root], self.session_id, "codex")["task"]
        goal = {"outcome": "Explain the actual value.", "deliverable": "A source-backed explanation.",
                "criteria": [{"id": "C1", "description": "Cite the actual value definition."}],
                "decisions": [], "open_questions": []}
        if explicit:
            goal["execution"] = {"routing": copy.deepcopy(POLICY)}
        work.define(task["id"], goal)
        return work, task["id"], root

    @staticmethod
    def result():
        return {"summary": "The value is explained with its source reference.", "outputs": ["conversation:value-answer"],
                "checks": [{"criterion_id": "C1", "satisfied": True, "evidence": "value.py defines VALUE = 1."}]}

    def test_installed_assess_cli_keeps_repository_lock_and_staging_boundary(self):
        install(self.root)
        entry = self.root / ".loop/loop.py"
        staged = self.cli("delegate", "demo", "--input", self.input("request", self.request()), entry=entry, code=1)
        self.assertEqual(staged["assignment"]["status"], "candidate")
        self.assertEqual(staged["assignment"]["profile"]["effort"], "low")
        payload = self.input("assessment", self.assessment(staged))
        with locked(self.root):
            error = self.cli("assess", "demo", "--input", payload, entry=entry, code=2)
        self.assertIn("Another loop command", error["error"])
        accepted = self.cli("assess", "demo", "--input", payload, entry=entry)
        self.assertTrue(accepted["passed"], accepted)
        self.assertEqual(accepted["assignment"]["status"], "done")
        self.assertIn("Never accept solely", (self.root / "AGENTS.md").read_text())

    def test_installed_common_assess_and_capsule_work_without_git(self):
        install(self.root)
        entry = self.root / ".loop/loop.py"
        work, task, root = self.common_task()
        work.start(task)
        staged = self.cli("work", "delegate", task, "--path", root,
                          "--input", self.input("common-request", self.request(goal="C1")), entry=entry, root=root, code=1)
        capsule = self.cli("orchestration", "context", self.session_id, "--task", task, entry=entry, root=root)
        self.assertLessEqual(len(json.dumps(capsule, ensure_ascii=False, sort_keys=True, indent=2).encode()), MAX_CONTEXT_BYTES)
        self.assertEqual(capsule["workers"][0]["candidate_id"], staged["assignment"]["candidate_id"])
        self.assertEqual(capsule["workers"][0]["profile"]["effort"], "low")
        self.assertNotIn("outputs", capsule["workers"][0])
        self.assertIn("assess", capsule["next"])
        accepted = self.cli("work", "assess", task, "--path", root,
                            "--input", self.input("common-assessment", self.assessment(staged, root)), entry=entry, root=root)
        self.assertTrue(accepted["passed"], accepted)
        after = Orchestration(self.kb).context(self.session_id, task)
        self.assertEqual(after["workers"][0]["assessment"]["verdict"], "accept")
        self.assertEqual(after["feedback_revision"], capsule["feedback_revision"])
        self.assertFalse((root / ".git").exists())

    def test_missing_or_invalid_config_blocks_start_before_worker_calls(self):
        with patch.dict(os.environ, {"LOOP_ROUTING_PROFILE": ""}):
            work, task, _ = self.common_task(explicit=False)
        with self.assertRaisesRegex(LoopError, "execution.routing"):
            work.start(task)
        self.assertIsNone(self.kb.get("worker-budget", task, required=False))
        goal = work.load(task)["goal"]
        goal["execution"] = {"routing": {"version": 1, "profiles": [], "requirements": {"low": 1, "medium": 2, "high": 3}}}
        with self.assertRaisesRegex(LoopError, "explicit profiles"):
            work.define(task, goal)
        contract_path = self.engine.goal_dir / "contract.json"
        contract = read_json(contract_path)
        contract["execution"].pop("routing")
        write_json(contract_path, contract)
        with patch.dict(os.environ, {"LOOP_ROUTING_PROFILE": ""}):
            with self.assertRaisesRegex(LoopError, "execution.routing"):
                self.engine.plan_review()
        self.assertNotIn("plan_review", self.state())

    def test_environment_policy_is_imported_before_seal_and_never_reread_for_dispatch(self):
        path = self.input("local-policy", POLICY)
        with patch.dict(os.environ, {"LOOP_ROUTING_PROFILE": str(path)}):
            work, task, _ = self.common_task(explicit=False)
            imported = copy.deepcopy(work.load(task)["goal"]["execution"]["routing"])
            write_json(path, {"invalid": "changed after definition"})
            work.start(task)
            staged = work.delegate(task, self.request(goal="C1"))
        self.assertEqual(staged["assignment"]["profile"]["model"], "codex-test-low")
        self.assertEqual(work.load(task)["goal"]["execution"]["routing"], imported)

    def test_preflight_analysis_uses_policy_snapshot_without_inventing_a_goal(self):
        policy = self.input("preflight-policy", POLICY)
        work = Work(self.kb)
        with patch.dict(os.environ, {"LOOP_ROUTING_PROFILE": str(policy)}):
            task = work.open("research", "Inspect the source before goal definition", "claude", [self.root], self.session_id)["task"]
            write_json(policy, {"invalid": "The environment changed after task creation."})
            self.assertIsNone(work.load(task["id"])["goal"])
            staged = work.delegate(task["id"], self.request(goal="goal_setting"))
        self.assertEqual(staged["assignment"]["status"], "candidate")
        self.assertEqual(staged["assignment"]["profile"]["model"], "claude-test-low")
        work.assess(task["id"], self.assessment(staged))
        self.assertIsNone(work.load(task["id"])["goal"])
        self.assertEqual(work.load(task["id"])["phase"], "goal_setting")

    def test_pending_and_rejected_work_cannot_finish_using_older_success(self):
        work, task, root = self.common_task()
        work.start(task)
        old = work.delegate(task, self.request("old-evidence", "C1"))
        work.assess(task, self.assessment(old, root))
        staged = work.delegate(task, self.request("required-evidence", "C1"))
        with self.assertRaisesRegex(LoopError, "remain unresolved"):
            work.finish(task, self.result())
        work.assess(task, self.assessment(staged, root, "reject"))
        with self.assertRaisesRegex(LoopError, "remain unresolved"):
            work.finish(task, self.result())
        stronger = work.delegate(task, self.request("required-evidence", "C1"))
        self.assertEqual(stronger["assignment"]["profile"]["effort"], "medium")
        work.assess(task, self.assessment(stronger, root))
        self.assertTrue(work.finish(task, self.result())["passed"])

    def test_reviews_use_explicit_opposite_provider_medium_profile(self):
        review = self.engine.plan_review()
        self.assertTrue(review["passed"], review)
        self.assertEqual(review["routing"]["profile"]["id"], "codex-medium")
        process = read_json(Path(review["review"]["artifact"]).parent / "process.json")
        self.assertIn("codex-test-medium", process["argv"])
        self.assertIn('model_reasoning_effort="medium"', process["argv"])
        self.engine.freeze()
        self.engine.start()
        checkpoint = self.engine.gate("M1")
        self.assertTrue(checkpoint["passed"], checkpoint)
        final = self.engine.gate()
        self.assertTrue(final["passed"], final)
        self.assertEqual(final["routing"]["profile"]["id"], "codex-medium")

    def test_review_high_demand_requires_recorded_goal_linked_risk(self):
        request = self.request()
        request["planning"].update(risk="high", evidence=[str(self.root / "value.py") + ": public consumer contract"])
        staged = self.engine.delegate(request)
        self.engine.assess(self.assessment(staged))
        review = self.engine.plan_review()
        self.assertTrue(review["passed"], review)
        self.assertEqual(review["routing"]["profile"]["id"], "codex-high")
        self.assertEqual(review["routing"]["planning"]["risk"], "high")
        self.assertTrue(any(value.startswith("delegation:") for value in review["routing"]["planning"]["evidence"]))

    def test_development_final_gate_rejects_unassessed_candidate(self):
        self.running()
        self.assertTrue(self.engine.gate("M1")["passed"])
        staged = self.engine.delegate(self.request())
        with self.assertRaisesRegex(LoopError, "remain unresolved"):
            self.engine.gate()
        self.assertNotIn("final", self.state()["attempts"])
        self.engine.assess(self.assessment(staged))
        self.assertTrue(self.engine.gate()["passed"])

    def test_development_requires_review_provider_before_preflight(self):
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path)
        contract["execution"]["routing"]["profiles"] = [p for p in POLICY["profiles"] if p["provider"] == "claude"]
        write_json(path, contract)
        with self.assertRaisesRegex(LoopError, "provider codex"):
            self.engine.plan_review()
        self.assertNotIn("plan_review", self.state())
