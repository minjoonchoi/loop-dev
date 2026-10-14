from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from unittest.mock import patch

from loop_dev import providers
from loop_dev.orchestration import Orchestration, worker_context
from loop_dev.storage import LoopError, locked, read_json, write_json
from loop_dev.team import Team
from support import FAKE_CLI, ProjectTest
from test_delegation import assignment


TEAM_BRANCH = '''if "WORKER DATA:\\n" in prompt:
    data = json.loads(prompt.split("WORKER DATA:\\n", 1)[1])
    request = data["assignment"]
    team = data["team"]
    mode = os.getenv("LOOP_TEAM_MODE", "normal")
    value = {
        "status": "done", "summary": "The scoped assignment has evidence and deliverables.",
        "outputs": [{"id": x["id"], "content": "VALUE is defined in value.py.", "evidence": ["value.py:1"]}
                    for x in request["deliverables"]],
        "coverage": [{"criterion_id": x["id"], "satisfied": True, "evidence": "value.py:1"}
                     for x in request["criteria"]],
        "changes": [{"path": name, "operation": "write", "base_hash": data["files"].get(name, "deleted"),
                     "content": content} for name, content in
                    json.loads(os.getenv("LOOP_TEAM_EDITS", "{}")).get(request["id"], {}).items()],
        "findings": [], "blockers": [], "messages": []}
    def message(to, kind, summary, reply_to=""):
        return {"to": to, "kind": kind, "summary": summary, "reply_to": reply_to,
                "evidence": [] if kind == "question" else ["value.py:1"]}
    if mode == "exchange":
        if request["id"] == "alpha" and not any(x["kind"] == "answer" for x in team["inbox"]):
            value.update(status="waiting", outputs=[], changes=[], messages=[
                message("beta", "question", "What value does the consumer receive?")])
            value["coverage"][0]["satisfied"] = False
        else:
            value["messages"] = [message(x["from"], "answer", "The consumer receives VALUE equal to one.", x["id"])
                                 for x in team["inbox"] if x["kind"] == "question"]
    elif mode == "cycle":
        value.update(status="waiting", outputs=[], changes=[], messages=[
            message("beta" if request["id"] == "alpha" else "alpha", "question", "Confirm the value before I continue.")])
        value["coverage"][0]["satisfied"] = False
    elif mode == "unknown":
        value.update(status="waiting", outputs=[], messages=[message("stranger", "question", "Confirm the source value.")])
        value["coverage"][0]["satisfied"] = False
    elif mode == "spoof":
        value["messages"] = [message("beta", "answer", "The consumer receives one.", "message-forged")]
    elif mode == "waiting-edit":
        value.update(status="waiting", messages=[message("beta", "question", "Confirm the source value.")])
        value["coverage"][0]["satisfied"] = False
    if os.getenv("LOOP_TEAM_MUTATE"):
        pathlib.Path("value.py").write_text("VALUE = 9\\n")
    if "--output-last-message" in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(json.dumps(value))
    else:
        print(json.dumps({"is_error": False, "structured_output": value, "permission_denials": []}))
    raise SystemExit(0)
'''


def plan(*names, max_turns=12, team_id="inspect-team"):
    return {"id": team_id, "members": [{"assignment": assignment(name)} for name in names], "max_turns": max_turns}


class TeamTests(ProjectTest):
    def setUp(self):
        super().setUp()
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(', TEAM_BRANCH + 'data = json.loads(prompt.split(', 1))
        self.kb = self.engine.knowledge
        self.task_id = self.engine.refresh()["work_id"]
        self.team = Team(self.kb, self.task_id, self.root)

    def packet(self, member):
        record = self.team.assignment(member)
        prompt = (Path(record["directory"]) / "prompt.txt").read_text()
        return json.loads(prompt.split("WORKER DATA:\n", 1)[1])

    def ledger(self, name="inspect-team"):
        return self.kb.get("team", self.team.key(name))

    def feedback(self, key, kind="correction", reference="conversation:user-input"):
        session = self.kb.get("task", self.task_id)["session_id"]
        control = self.kb.get("orchestration", session, required=False) or {"control_version": 0}
        return Orchestration(self.kb).feedback(session, {
            "id": key, "task_id": self.task_id, "kind": kind, "summary": "Keep the assigned interface compatible.",
            "original_input_ref": reference, "applies_to": "The current assigned interface", "supersedes": []}, control["control_version"])

    def test_real_question_answer_followup_is_automatic_and_addressed(self):
        request = plan("alpha", "beta", "gamma")
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "exchange"}):
            result = self.team.run(request)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 4)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 4)
        self.assertEqual(self.team.assignment("alpha")["attempts"], 2)
        inbox = self.packet("alpha")["team"]["inbox"]
        self.assertEqual(inbox[0]["kind"], "answer")
        self.assertEqual(inbox[0]["from"], "beta")
        self.assertEqual(self.packet("gamma")["team"]["inbox"], [])
        messages = self.ledger()["messages"]
        self.assertEqual(messages[0]["answered_by"], messages[1]["id"])
        self.assertEqual(messages[1]["reply_to"], messages[0]["id"])
        self.assertEqual(self.state()["phase"], "draft")
        self.assertNotIn("messages", result)
        self.assertNotIn("instruction", json.dumps(result))

    def test_normal_team_reuses_completed_result_and_context_is_compact(self):
        request = plan("one", "two", "three", "four", "five", "six")
        result = self.team.run(request)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["member_count"], 6)
        self.assertEqual(len(result["members"]), 5)
        self.assertTrue(Path(result["artifact"]).is_file())
        again = Team(self.kb, self.task_id, self.root).run(request)
        self.assertTrue(again["passed"])
        self.assertEqual(again["turns"], 6)
        self.assertEqual(again["task_worker_calls"], 6)

    def test_both_providers_use_fresh_read_only_invocations(self):
        for provider in ("claude", "codex"):
            contract_path = self.engine.goal_dir / "contract.json"
            contract = read_json(contract_path)
            contract["execution"]["implementer"] = provider
            write_json(contract_path, contract)
            self.engine.refresh()
            name = "inspect-" + provider
            result = self.team.run(plan(name, team_id="team-" + provider))
            self.assertTrue(result["passed"], result)
            record = self.team.assignment(name)
            argv = read_json(Path(record["directory"]) / "process.json")["argv"]
            self.assertIn("--ephemeral" if provider == "codex" else "--no-session-persistence", argv)
            self.assertIn("read-only" if provider == "codex" else "Read,Glob,Grep", argv)
            prompt = (Path(record["directory"]) / "prompt.txt").read_text()
            self.assertIn("Do not start repository orchestration, call another agent/CLI, ask the user", prompt)

    def test_dependency_order_and_cycle_missing_dependency_reject_before_call(self):
        request = plan("beta", "alpha")
        request["members"][0]["assignment"]["depends_on"] = ["alpha"]
        result = self.team.run(request)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.team.assignment("alpha")["team_turn"], 1)
        self.assertEqual(self.team.assignment("beta")["team_turn"], 2)
        cyclic = plan("cycle-a", "cycle-b", team_id="cyclic")
        cyclic["members"][0]["assignment"]["depends_on"] = ["cycle-b"]
        cyclic["members"][1]["assignment"]["depends_on"] = ["cycle-a"]
        with self.assertRaisesRegex(LoopError, "cycle"):
            self.team.run(cyclic)
        missing = plan("missing", team_id="missing")
        missing["members"][0]["assignment"]["depends_on"] = ["unknown"]
        with self.assertRaisesRegex(LoopError, "missing"):
            self.team.run(missing)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 2)

    def test_recipient_and_answer_spoofing_fail_without_delivery(self):
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "unknown"}):
            result = self.team.run(plan("alpha", "beta", team_id="unknown"))
        self.assertFalse(result["passed"])
        self.assertEqual(self.ledger("unknown")["messages"], [])
        self.assertEqual(self.team.assignment("alpha")["status"], "failed")
        # Exercise forged reply in a fresh assignment namespace.
        request = plan("sender", "beta", team_id="forged")
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "spoof"}):
            result = self.team.run(request)
        self.assertFalse(result["passed"])
        self.assertIn("delivered question", result["error"])

    def test_lock_rejection_does_not_create_an_interrupted_worker(self):
        request = plan("alpha", max_turns=1)
        with locked(self.kb.home / "task-locks" / self.task_id):
            blocked = self.team.run(request)
        self.assertFalse(blocked["passed"])
        self.assertEqual(blocked["turns"], 0)
        self.assertEqual(blocked["task_worker_calls"], 0)
        self.assertIsNone(self.ledger()["inflight"])
        self.assertTrue(self.team.run(request)["passed"])

    def test_waiting_cannot_apply_edits_or_expand_scope(self):
        self.running()
        request = plan("alpha", "beta")
        request["members"][0]["assignment"] = assignment("alpha", "implementation", ["value.py"])
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "waiting-edit", "LOOP_TEAM_EDITS": json.dumps({"alpha": {"value.py": "VALUE = 2\n"}})}):
            result = self.team.run(request)
        self.assertFalse(result["passed"])
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
        self.assertIn("Waiting", result["error"])
        invalid = plan("invalid", team_id="invalid")
        invalid["members"][0]["assignment"] = assignment("invalid", "implementation", ["AGENTS.md"])
        with self.assertRaisesRegex(LoopError, "instructions"):
            self.team.run(invalid)

    def test_turn_limit_and_shared_task_budget_never_reset(self):
        request = plan("alpha", "beta", max_turns=2)
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "cycle"}):
            result = self.team.run(request)
            again = self.team.run(request, recover="Inspected all turns; no process remains")
        self.assertFalse(result["passed"])
        self.assertIn("turn budget", result["error"])
        self.assertEqual(again["turns"], 2)
        self.assertEqual(again["task_worker_calls"], 2)
        altered = copy.deepcopy(request)
        altered["max_turns"] = 3
        with self.assertRaisesRegex(LoopError, "immutable"):
            self.team.run(altered)
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path)
        contract["execution"]["max_worker_calls"] = 2
        write_json(path, contract)
        self.engine.refresh()
        blocked = self.team.run(plan("new-member", team_id="new-team"))
        self.assertFalse(blocked["passed"])
        self.assertIn("budget exhausted", blocked["error"])
        self.assertEqual(blocked["task_worker_calls"], 2)

    def test_source_drift_rejects_worker_and_preserves_external_edit(self):
        with patch.dict(os.environ, {"LOOP_TEAM_MUTATE": "1"}):
            result = self.team.run(plan("alpha", "beta"))
        self.assertFalse(result["passed"])
        self.assertFalse(result["evidence_current"])
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 9\n")
        self.assertEqual(result["task_worker_calls"], 1)

    def test_feedback_input_is_exact_and_changed_input_invalidates_result(self):
        original_input = Path(self.temp.name) / "request.txt"
        original_input.write_text("Keep the assigned interface compatible.\n")
        self.feedback("input", reference=str(original_input))
        result = self.team.run(plan("alpha"))
        self.assertTrue(result["passed"], result)
        packet = self.packet("alpha")
        self.assertEqual(packet["feedback_input_files"], [str(original_input)])
        self.assertNotIn(str(original_input.parent), packet["readable_paths"])
        self.assertEqual(self.team.assignment("alpha")["feedback_substantive_revision"], worker_context(self.kb, self.task_id)["substantive_revision"])
        original_input.write_text("A different instruction.\n")
        with self.assertRaisesRegex(LoopError, "Original feedback evidence"):
            self.team.run(plan("beta", team_id="new-team"))
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 1)

    def test_feedback_during_worker_prevents_apply_and_pause_resume_refreshes_scoped_turn(self):
        self.running()
        request = plan("alpha")
        request["members"][0]["assignment"] = assignment("alpha", "implementation", ["value.py"])
        original = providers.structured_call

        def pause_after_call(*args, **kwargs):
            value = original(*args, **kwargs)
            self.feedback("pause", "pause")
            return value

        with patch.dict(os.environ, {"LOOP_TEAM_EDITS": json.dumps({"alpha": {"value.py": "VALUE = 2\n"}})}):
            with patch("loop_dev.providers.structured_call", side_effect=pause_after_call):
                blocked = self.team.run(request)
            self.assertFalse(blocked["passed"])
            self.assertIn("paused", blocked["error"])
            self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
            self.feedback("resume", "resume")
            result = self.team.run(request, recover="Inspected the paused worker; no process remains")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 2)
        self.assertEqual(result["task_worker_calls"], 2)
        self.assertEqual(self.ledger()["epoch"], 1)
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 2\n")
        self.assertEqual(self.state()["phase"], "running")

    def test_context_refresh_keeps_old_messages_historical_and_does_not_reset_limits(self):
        request = plan("alpha", "beta", max_turns=6)
        original_accept = self.team.accept

        def steer_after_waiting(record, member, worker):
            original_accept(record, member, worker)
            if worker["status"] == "waiting":
                self.feedback("correction")

        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "exchange"}):
            with patch.object(self.team, "accept", side_effect=steer_after_waiting):
                blocked = self.team.run(request)
            self.assertFalse(blocked["passed"])
            self.assertEqual(blocked["task_worker_calls"], 1)
            old_message = self.ledger()["messages"][0]["id"]
            result = self.team.run(request, recover="Inspected feedback and retained the same assignment scope")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 4)
        self.assertEqual(result["task_worker_calls"], 4)
        self.assertEqual(self.team.assignment("alpha")["attempts"], 3)
        delivered = self.packet("beta")["team"]["inbox"]
        self.assertNotEqual(delivered[0]["id"], old_message)
        self.assertIsNone(self.ledger()["messages"][0]["delivered_turn"])
        self.assertEqual(len(self.ledger()["messages"]), 3)

    def test_scope_change_cannot_be_cleared_by_team_recovery(self):
        request = plan("alpha")
        self.feedback("scope", "scope_change")
        self.feedback("resume", "resume")
        with self.assertRaisesRegex(LoopError, "Scope changed"):
            self.team.run(request, recover="No process remains")
        self.assertIsNone(self.kb.get("worker-budget", self.task_id, required=False))

    def test_interruption_requires_inspection_and_retains_both_counters(self):
        request = plan("alpha")
        with patch("loop_dev.providers.structured_call", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.team.run(request)
        with self.assertRaisesRegex(LoopError, "--recover"):
            self.team.run(request)
        result = Team(self.kb, self.task_id, self.root).run(request, recover="Inspected worker logs; no process remains")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 2)
        self.assertEqual(result["task_worker_calls"], 2)
        self.assertEqual(self.team.assignment("alpha")["attempts"], 2)

    def test_recovery_imports_completed_turn_without_reexecution_or_duplicate_message(self):
        request = plan("alpha", "beta")
        with patch.dict(os.environ, {"LOOP_TEAM_MODE": "exchange"}):
            with patch.object(self.team, "accept", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.team.run(request)
            result = Team(self.kb, self.task_id, self.root).run(request, recover="Inspected completed worker and stopped processes")
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["turns"], 3)
        self.assertEqual(result["task_worker_calls"], 3)
        self.assertEqual(result["message_count"], 2)

    def test_stale_factual_message_does_not_reach_later_member(self):
        request = plan("alpha", "beta", "gamma")
        original = providers.structured_call

        def run(provider, root, directory, prompt, *args):
            value = original(provider, root, directory, prompt, *args)
            data = json.loads(prompt.split("WORKER DATA:\n", 1)[1])
            if data["assignment"]["id"] == "alpha":
                value["messages"] = [{"to": "gamma", "kind": "finding", "reply_to": "", "summary": "The source value is one.", "evidence": ["value.py:1"]}]
            return value

        self.running()
        request["members"][1]["assignment"] = assignment("beta", "implementation", ["value.py"])
        # beta is a prerequisite of gamma, so its write occurs before delivery.
        request["members"][2]["assignment"]["depends_on"] = ["beta"]
        with patch("loop_dev.providers.structured_call", side_effect=run), patch.dict(os.environ, {"LOOP_TEAM_EDITS": json.dumps({"beta": {"value.py": "VALUE = 2\n"}})}):
            result = self.team.run(request)
        self.assertFalse(result["passed"])
        self.assertIn("peer answer or finding is stale", result["error"])
        self.assertIsNone(self.team.assignment("gamma"))
        self.assertEqual(result["task_worker_calls"], 2)
