from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

from loop_work import providers
from loop_work.delegation import Delegation
from loop_work.orchestration import Orchestration
from loop_work.storage import LoopError, digest, file_hash, read_json, write_json
from loop_work.team import Team
from support import FAKE_CLI, ProjectTest, fixture_assessment
from test_delegation import WORKER_BRANCH, assignment
from test_team import TEAM_BRANCH, plan


class RoutedDelegationTests(ProjectTest):
    def setUp(self):
        super().setUp()
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(',
                WORKER_BRANCH + 'data = json.loads(prompt.split(', 1))
        self.kb = self.engine.knowledge
        self.task_id = self.engine.refresh()["work_id"]
        self.worker = Delegation(self.kb, self.task_id, self.root)

    def stored(self, result):
        return self.kb.get("delegation", result["assignment"]["id"])

    def decision(self, result, verdict="accept", reason="incorrect"):
        record = self.stored(result)
        if verdict == "accept":
            return fixture_assessment(self.kb, result)
        evidence = [{"ref": record["result"], "sha256": file_hash(Path(record["result"]))}]
        criteria = [("assignment", row["id"]) for row in record["request"]["criteria"]]
        criteria += [("goal", key) for key in record["request"]["planning"]["goal_criteria"]]
        return {"assignment_id": record["request"]["id"], "candidate_id": record["candidate_id"],
                "result_digest": record["result_digest"], "verdict": verdict, "reason": reason,
                "summary": "The candidate conflicts with the required consumer behavior.",
                "checks": [{"scope": scope, "criterion_id": key, "satisfied": False, "evidence": evidence}
                           for scope, key in criteria]}

    def packet(self, result):
        text = (Path(result["assignment"]["directory"]) / "prompt.txt").read_text()
        return json.loads(text.split("WORKER DATA:\n", 1)[1])

    def feedback(self, name="correction", kind="correction"):
        task = self.kb.get("task", self.task_id)
        service = Orchestration(self.kb)
        version = service.context(task["session_id"], self.task_id)["control_version"]
        return service.feedback(task["session_id"], {"id": name, "task_id": self.task_id, "kind": kind,
            "summary": "Keep the original consumer behavior compatible.", "original_input_ref": "conversation:user-correction",
            "applies_to": "The assigned consumer behavior", "supersedes": []}, version)

    def test_goal_harming_candidate_is_quarantined_then_actual_effort_increases(self):
        self.running()
        request = assignment("fix-value", "implementation", ["value.py"])
        finding = {"summary": "The consumer accepts the changed value.", "evidence": ["check.py:1"], "paths": ["check.py"]}
        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"value.py": "VALUE = 2\n"}),
                                     "LOOP_WORKER_FINDINGS": json.dumps([finding])}):
            first = self.worker.run(request)
        self.assertEqual(first["assignment"]["status"], "candidate")
        self.assertEqual(first["assignment"]["profile"]["effort"], "low")
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
        self.assertFalse((Path(first["assignment"]["directory"]) / "explanation.md").exists())
        self.assertEqual(self.kb.context([self.worker.location], task_id=self.task_id)["unconfirmed"], [])
        rejected = self.worker.assess(self.decision(first, "reject", "goal_conflict"))
        self.assertEqual(rejected["assignment"]["status"], "rejected")
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
        second = self.worker.run(request)
        self.assertEqual(second["assignment"]["profile"]["model"], first["assignment"]["profile"]["model"])
        self.assertEqual(second["assignment"]["profile"]["effort"], "medium")
        argv = read_json(Path(second["assignment"]["directory"]) / "process.json")["argv"]
        self.assertEqual(argv[argv.index("--effort") + 1], "medium")
        self.assertTrue(self.packet(second)["routing"]["escalated"])
        self.assertEqual(self.packet(second)["prior_failure"]["reason"], "goal_conflict")
        self.assertTrue(self.worker.assess(self.decision(second))["passed"])
        self.assertEqual(self.state()["phase"], "running")
        self.assertEqual(read_json(Path(first["assignment"]["directory"]) / "attempt.json")["status"], "rejected")

    def test_pending_candidate_survives_restart_without_another_call_or_full_main_payload(self):
        request = assignment()
        first = self.worker.run(request)
        again = Delegation(self.kb, self.task_id, self.root).run(request)
        self.assertTrue(again["reused"])
        self.assertEqual(first["assignment"]["candidate_id"], again["assignment"]["candidate_id"])
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 1)
        self.assertNotIn("request", again["assignment"])
        self.assertNotIn("outputs", json.dumps(again))
        accepted = self.worker.assess(self.decision(first))
        self.assertTrue(accepted["passed"])
        self.assertTrue(self.worker.assess(self.decision(first))["reused"])

    def test_candidate_tampering_is_never_silently_retried_or_accepted(self):
        request = assignment()
        first = self.worker.run(request)
        payload = self.decision(first)
        path = Path(first["assignment"]["result"])
        value = read_json(path)
        value["summary"] = "A changed candidate artifact."
        write_json(path, value)
        for invoke in (lambda: self.worker.assess(payload), lambda: self.worker.run(request, recover="Inspected candidate artifact.")):
            with self.assertRaisesRegex(LoopError, "artifact changed"):
                invoke()
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 1)

    def test_prerequisite_and_reuse_reject_tampered_accepted_artifacts(self):
        request = assignment()
        candidate = self.worker.run(request)
        self.worker.assess(self.decision(candidate))
        stored = self.stored(candidate)
        dependent = assignment("followup", "planning")
        dependent["depends_on"] = [request["id"]]
        result_path = Path(stored["result"])
        original = result_path.read_bytes()
        value = read_json(result_path)
        value["outputs"][0]["content"] = "An unassessed replacement result."
        write_json(result_path, value)
        with self.assertRaisesRegex(LoopError, "artifact changed"):
            self.worker.run(dependent)
        result_path.write_bytes(original)
        receipt_path = Path(stored["assessment_ref"])
        receipt = read_json(receipt_path)
        receipt["summary"] = "An unassessed replacement approval."
        write_json(receipt_path, receipt)
        for work in (request, dependent):
            with self.assertRaisesRegex(LoopError, "receipt changed"):
                self.worker.run(work)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 1)

    def test_stale_decision_cannot_approve_new_higher_profile_candidate(self):
        request = assignment()
        first = self.worker.run(request)
        old_acceptance = self.decision(first)
        self.worker.assess(self.decision(first, "reject"))
        second = self.worker.run(request)
        with self.assertRaisesRegex(LoopError, "stale or different candidate"):
            self.worker.assess(old_acceptance)
        self.assertEqual(self.stored(second)["status"], "candidate")

    def test_feedback_race_blocks_acceptance_and_recovery_keeps_profile(self):
        self.running()
        request = assignment("fix-value", "implementation", ["value.py"])
        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"value.py": "VALUE = 1\n# compatible\n"})}):
            candidate = self.worker.run(request)
        acceptance = self.decision(candidate)
        self.feedback()
        with self.assertRaisesRegex(LoopError, "feedback changed"):
            self.worker.assess(acceptance)
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
        resumed = self.worker.run(request, recover="Inspected unchanged source and current correction.")
        self.assertEqual(resumed["assignment"]["profile"]["effort"], "low")
        self.assertFalse(self.packet(resumed)["routing"]["escalated"])

    def test_transport_failure_does_not_raise_model_or_effort(self):
        request = assignment()
        with patch.dict(os.environ, {"LOOP_TEST_FAIL": "1"}):
            failed = self.worker.run(request)
        self.assertEqual(failed["assignment"]["status"], "blocked")
        retried = self.worker.run(request)
        self.assertEqual(retried["assignment"]["profile"], failed["assignment"]["profile"])
        self.assertFalse(self.packet(retried)["routing"]["escalated"])
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 2)

    def test_model_reported_blocker_can_be_disproved_without_promoting_it(self):
        request = assignment("unsupported-blocker")
        blocked_value = {"status": "blocked", "summary": "The required source is missing.", "outputs": [],
                         "coverage": [{"criterion_id": row["id"], "satisfied": False, "evidence": "value.py is missing."}
                                      for row in request["criteria"]],
                         "changes": [], "findings": [], "blockers": ["value.py is missing."]}
        with patch.dict(os.environ, {"LOOP_WORKER_RESULT": json.dumps(blocked_value)}):
            blocked = self.worker.run(request)
            retained = Path(blocked["assignment"]["result"])
            original = retained.read_bytes()
            write_json(retained, {"status": "blocked", "summary": "A different unassessed blocker."})
            with self.assertRaisesRegex(LoopError, "artifact changed"):
                self.worker.run(request)
            retained.write_bytes(original)
            retry = self.worker.run(request)
        self.assertEqual(blocked["assignment"]["status"], "blocked")
        self.assertTrue(blocked["assignment"]["model_reported_blocker"])
        self.assertTrue(blocked["assignment"]["candidate_id"])
        self.assertIn("Independently verify", blocked["next"])
        self.assertEqual(retry["assignment"]["profile"], blocked["assignment"]["profile"])
        rejected = self.decision(retry, "reject", "unsupported_claim")
        evidence = {"ref": str((self.root / "value.py").resolve()), "sha256": file_hash(self.root / "value.py")}
        for check in rejected["checks"]:
            check["evidence"].append(evidence)
        invalid_acceptance = copy.deepcopy(rejected)
        invalid_acceptance.update(verdict="accept", reason="accepted")
        for check in invalid_acceptance["checks"]:
            check["satisfied"] = True
        with self.assertRaisesRegex(LoopError, "blocker cannot be accepted"):
            self.worker.assess(invalid_acceptance)
        self.assertEqual(self.stored(retry)["status"], "blocked")
        self.worker.assess(rejected)
        stronger = self.worker.run(request)
        self.assertEqual(stronger["assignment"]["profile"]["effort"], "medium")
        self.assertEqual(stronger["assignment"]["attempts"], 3)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 3)
        self.assertFalse((Path(blocked["assignment"]["directory"]) / "explanation.md").exists())
        self.assertTrue(self.worker.assess(self.decision(stronger))["passed"])

    def test_transport_blocker_cannot_be_reclassified_as_model_quality(self):
        request = assignment("auth-blocker")
        with patch.dict(os.environ, {"LOOP_TEST_FAIL": "1"}):
            blocked = self.worker.run(request)
        self.assertNotIn("model_reported_blocker", blocked["assignment"])
        self.assertNotIn("candidate_id", blocked["assignment"])
        with self.assertRaisesRegex(LoopError, "Only a completed candidate"):
            self.worker.assess({"assignment_id": request["id"], "verdict": "reject", "reason": "unsupported_claim"})
        retry = self.worker.run(request)
        self.assertEqual(retry["assignment"]["profile"], blocked["assignment"]["profile"])
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 2)

    def test_claude_call_confines_fallback_without_editing_host_settings(self):
        settings_path = self.root / ".claude/settings.json"
        write_json(settings_path, {"fallbackModel": ["fake-unapproved-expensive"], "switchModelsOnFlag": True})
        original_settings = settings_path.read_bytes()
        binary = self.bin / "claude"
        source = binary.read_text().replace('"content": "VALUE is defined in value.py."',
            '"content": "VALUE is defined in value.py.; effort=" + os.getenv("CLAUDE_CODE_EFFORT_LEVEL", "missing")')
        binary.write_text(source)
        with patch.dict(os.environ, {"CLAUDE_CODE_EFFORT_LEVEL": "high"}):
            candidate = self.worker.run(assignment())
        record = candidate["assignment"]
        argv = read_json(Path(record["directory"]) / "process.json")["argv"]
        selected = record["profile"]["model"]
        self.assertEqual(argv[argv.index("--model") + 1], selected)
        self.assertEqual(argv[argv.index("--fallback-model") + 1], selected)
        self.assertEqual(json.loads(argv[argv.index("--settings") + 1]), {"switchModelsOnFlag": False})
        self.assertIn("effort=low", read_json(Path(record["result"]))["outputs"][0]["content"])
        self.assertEqual(settings_path.read_bytes(), original_settings)
        self.assertNotIn("effective_model", record)

    def test_structural_failure_requires_rejection_before_higher_profile(self):
        request = assignment()
        with patch.dict(os.environ, {"LOOP_WORKER_RESULT": json.dumps({"invalid_result": {"raw_result": "untrusted-foreign-path"}})}):
            candidate = self.worker.run(request)
        self.assertEqual(candidate["assignment"]["status"], "candidate")
        self.assertTrue(candidate["assignment"]["validation_errors"])
        self.assertEqual(self.worker.run(request)["assignment"]["attempts"], 1)
        reject = self.decision(candidate, "reject", "invalid_result")
        accept = copy.deepcopy(reject)
        accept.update(verdict="accept", reason="accepted")
        for check in accept["checks"]:
            check["satisfied"] = True
            check["evidence"].append({"ref": str(self.root.resolve() / "value.py"), "sha256": file_hash(self.root / "value.py")})
        with self.assertRaisesRegex(LoopError, "invalid candidate"):
            self.worker.assess(accept)
        self.worker.assess(reject)
        higher = self.worker.run(request)
        self.assertEqual(higher["assignment"]["profile"]["effort"], "medium")

    def test_malformed_json_is_quality_but_denied_authority_is_not(self):
        contract_path = self.engine.goal_dir / "contract.json"
        contract = read_json(contract_path)
        contract["execution"]["implementer"] = "codex"
        write_json(contract_path, contract)
        for name in ("claude", "codex"):
            source = (self.bin / name).read_text()
            source = source.replace('json.dumps(value)', '"not-json"', 1)
            (self.bin / name).write_text(source)
        malformed = self.worker.run(assignment("malformed"))
        self.assertEqual(malformed["assignment"]["status"], "candidate")
        self.assertIn("malformed JSON", malformed["assignment"]["validation_errors"][0])
        self.assertIn("invalid_result", read_json(Path(malformed["assignment"]["result"])))
        self.worker.assess(self.decision(malformed, "reject", "invalid_result"))
        higher = self.worker.run(assignment("malformed"))
        argv = read_json(Path(higher["assignment"]["directory"]) / "process.json")["argv"]
        self.assertIn('model_reasoning_effort="medium"', argv)
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(',
                WORKER_BRANCH + 'data = json.loads(prompt.split(', 1))
        contract["execution"]["implementer"] = "claude"
        write_json(contract_path, contract)
        with patch.dict(os.environ, {"LOOP_TEST_DENIED": "1"}):
            denied = self.worker.run(assignment("denied"))
        self.assertEqual(denied["assignment"]["status"], "blocked")
        self.assertNotIn("candidate_id", denied["assignment"])

    def test_rejection_evidence_and_external_acceptance_are_required(self):
        candidate = self.worker.run(assignment())
        payload = self.decision(candidate)
        for check in payload["checks"]:
            check["evidence"] = [check["evidence"][0]]
        with self.assertRaisesRegex(LoopError, "outside the candidate"):
            self.worker.assess(payload)
        payload = self.decision(candidate, "reject")
        payload["checks"][0]["evidence"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(LoopError, "evidence changed"):
            self.worker.assess(payload)
        self.assertEqual(self.stored(candidate)["status"], "candidate")

    def test_three_attempts_and_fixed_profile_cannot_fake_an_upgrade(self):
        request = assignment()
        efforts = []
        for _ in range(3):
            candidate = self.worker.run(request)
            efforts.append(candidate["assignment"]["profile"]["effort"])
            self.worker.assess(self.decision(candidate, "reject"))
        self.assertEqual(efforts, ["low", "medium", "high"])
        with self.assertRaisesRegex(LoopError, "higher allowed|budget exhausted"):
            self.worker.run(request)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 3)
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path)
        contract["execution"]["routing"]["profiles"] = [row for row in contract["execution"]["routing"]["profiles"] if row["effort"] == "low"]
        write_json(path, contract)
        fixed = self.worker.run(assignment("fixed"))
        self.worker.assess(self.decision(fixed, "reject"))
        with self.assertRaisesRegex(LoopError, "higher allowed"):
            self.worker.run(assignment("fixed"))
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 4)

    def test_team_rejected_peer_message_never_delivers_or_escalates_other_member(self):
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(',
                TEAM_BRANCH + 'data = json.loads(prompt.split(', 1))
        team = Team(self.kb, self.task_id, self.root)
        request = plan("alpha", "beta")
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "exchange"}):
            first = team.run(request)
            self.assertEqual(first["status"], "awaiting_assessment")
            self.assertEqual(first["pending_message_count"], 0)
            self.assertEqual(team.load(request["id"])["messages"], [])
            self.worker.assess(self.decision({"assignment": first["candidate"]}, "reject", "unsupported_claim"))
            second = team.run(request)
            self.assertEqual(second["candidate"]["assignment_id"], "alpha")
            self.assertEqual(second["candidate"]["profile"]["effort"], "medium")
            self.assertEqual(team.load(request["id"])["messages"], [])
            self.worker.assess(self.decision({"assignment": second["candidate"]}))
            third = team.run(request)
            self.assertEqual(third["candidate"]["assignment_id"], "beta")
            self.assertEqual(third["candidate"]["profile"]["effort"], "low")
            self.assertEqual(len(team.load(request["id"])["messages"]), 1)
            self.assertEqual(third["pending_message_count"], 1)
            self.worker.assess(self.decision({"assignment": third["candidate"]}))
            fourth = team.run(request)
            self.assertEqual(fourth["candidate"]["assignment_id"], "alpha")
            self.assertEqual(fourth["candidate"]["profile"]["effort"], "medium")
            self.worker.assess(self.decision({"assignment": fourth["candidate"]}))
            complete = team.run(request)
        self.assertTrue(complete["passed"], complete)
        self.assertEqual(complete["turns"], 4)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 4)
        self.assertEqual(len(team.load(request["id"])["messages"]), 2)

    def test_team_retries_disproved_blocker_only_on_its_stronger_profile(self):
        branch = TEAM_BRANCH.replace('elif mode == "cycle":', '''elif mode == "blocked":
        value.update(status="blocked", summary="The required source is missing.", outputs=[], changes=[],
                     blockers=["value.py is missing."])
        value["coverage"][0]["satisfied"] = False
    elif mode == "cycle":''')
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(',
                branch + 'data = json.loads(prompt.split(', 1))
        team = Team(self.kb, self.task_id, self.root)
        request = plan("alpha", "beta")
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "blocked"}):
            blocked = team.run(request)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["candidate"]["assignment_id"], "alpha")
        self.assertTrue(blocked["candidate"]["model_reported_blocker"])
        self.assertEqual(team.run(request)["task_worker_calls"], 1)
        rejection = self.decision({"assignment": blocked["candidate"]}, "reject", "unsupported_claim")
        source = {"ref": str((self.root / "value.py").resolve()), "sha256": file_hash(self.root / "value.py")}
        for check in rejection["checks"]:
            check["evidence"].append(source)
        self.worker.assess(rejection)
        stronger = team.run(request)
        self.assertEqual(stronger["candidate"]["assignment_id"], "alpha")
        self.assertEqual(stronger["candidate"]["profile"]["effort"], "medium")
        self.assertEqual(stronger["message_count"], 0)
        self.worker.assess(self.decision({"assignment": stronger["candidate"]}))
        independent = team.run(request)
        self.assertEqual(independent["candidate"]["assignment_id"], "beta")
        self.assertEqual(independent["candidate"]["profile"]["effort"], "low")
        self.worker.assess(self.decision({"assignment": independent["candidate"]}))
        complete = team.run(request)
        self.assertTrue(complete["passed"], complete)
        self.assertEqual(complete["turns"], 3)
        self.assertEqual(complete["task_worker_calls"], 3)
        self.assertEqual(complete["message_count"], 0)
