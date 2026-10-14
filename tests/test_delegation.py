from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from loop_dev.delegation import Delegation, validate_request, validate_result
from loop_dev.storage import LoopError, file_hash, locked, read_json, write_json, write_text
from loop_dev.work import Work
from support import FAKE_CLI, ProjectTest, accept_fixture, fixture_assessment


WORKER_BRANCH = '''if "WORKER DATA:\\n" in prompt:
    import time
    data = json.loads(prompt.split("WORKER DATA:\\n", 1)[1])
    request = data["assignment"]
    if os.getenv("LOOP_WORKER_PAUSE"):
        time.sleep(5)
    if os.getenv("LOOP_WORKER_MUTATE"):
        pathlib.Path(os.environ["LOOP_WORKER_MUTATE"]).write_text("outside change\\n")
    value = {
        "status": "done", "summary": "The assigned work has evidence and deliverables.",
        "outputs": [{"id": x["id"], "content": "VALUE is defined in value.py.", "evidence": ["value.py:1"]}
                    for x in request["deliverables"]],
        "coverage": [{"criterion_id": x["id"], "satisfied": True, "evidence": "value.py:1"}
                     for x in request["criteria"]],
        "changes": [{"path": name, "operation": "delete" if content is None else "write",
                     "base_hash": data["files"].get(name, "deleted"), "content": content or ""}
                    for name, content in json.loads(os.getenv("LOOP_WORKER_EDITS", "{}")).items()],
        "findings": json.loads(os.getenv("LOOP_WORKER_FINDINGS", "[]")), "blockers": []}
    if os.getenv("LOOP_WORKER_RESULT"):
        value = json.loads(os.environ["LOOP_WORKER_RESULT"])
    if "--output-last-message" in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(json.dumps(value))
    else:
        print(json.dumps({"is_error": False, "structured_output": value,
                         "permission_denials": ["denied"] if os.getenv("LOOP_TEST_DENIED") else []}))
    raise SystemExit(0)
'''


def assignment(name="inspect-value", kind="analysis", writes=(), goal_criteria=("A1",)):
    return {"id": name, "kind": kind, "instruction": "Explain the value consumed by check.py and propose only the assigned edits.",
            "read_paths": ["value.py", "check.py"], "write_paths": list(writes),
            "prohibited": ["Do not change the interface or weaken checks."],
            "deliverables": [{"id": "explanation", "description": "A source-backed explanation of the value."}],
            "criteria": [{"id": "C1", "description": "Identify the actual value with a source reference."}],
            "knowledge_ids": [], "depends_on": [],
            "planning": {"goal_criteria": list(goal_criteria),
                         "rationale": "The source definition and consumer form one bounded behavior.",
                         "verification": "Compare the value definition with the consumer's actual assertion.",
                         "complexity": "low", "risk": "low", "uncertainty": "low", "evidence": []}}


class DelegationTests(ProjectTest):
    def setUp(self):
        super().setUp()
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace('data = json.loads(prompt.split(', WORKER_BRANCH + 'data = json.loads(prompt.split(', 1))
        self.kb = self.engine.knowledge
        self.task_id = self.engine.refresh()["work_id"]

    def packet(self, result):
        prompt = (Path(result["assignment"]["directory"]) / "prompt.txt").read_text()
        return json.loads(prompt.split("WORKER DATA:\n", 1)[1])

    def accepted(self, request, recover=None):
        return accept_fixture(self.kb, self.engine.assess, self.engine.delegate(request, recover))

    def accepted_work(self, work, task_id, request, path=None):
        return accept_fixture(self.kb, lambda payload: work.assess(task_id, payload, path),
                              work.delegate(task_id, request, path))

    def test_read_only_analysis_reuses_result_and_saves_draft_findings(self):
        request = assignment()
        with patch.dict(os.environ, {"LOOP_WORKER_FINDINGS": json.dumps([
                {"summary": "The consumer reads VALUE from value.py.", "evidence": ["check.py:1"], "paths": ["check.py"]}])}):
            result = self.accepted(request)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.state()["phase"], "draft")
        self.assertEqual(self.packet(result)["readable_paths"], ["AGENTS.md", "check.py", "value.py"])
        again = self.accepted(request)
        self.assertTrue(again["reused"])
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 1)
        context = self.kb.context([self.kb.attach(self.root)], task_id=self.task_id)
        self.assertEqual(context["unconfirmed"][0]["status"], "draft")
        self.assertFalse(context["reusable"])
        saved = self.engine.refresh()
        self.assertEqual(read_json(Path(saved["context"]))["delegations"][0]["status"], "done")
        self.assertIn("Delegated work", Path(saved["document"]).read_text())
        self.assertTrue((self.kb.home / "views/delegation" / (result["assignment"]["id"] + ".md")).exists())

    def test_worker_changes_apply_and_final_gate_remains_required(self):
        self.running()
        (self.root / "value.py").chmod(0o755)
        request = assignment("fix-value", "implementation", ["value.py", "notes/새문서.md"])
        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"value.py": "VALUE = 1\n# completed\n", "notes/새문서.md": "설명\n"})}):
            result = self.accepted(request)
        self.assertTrue(result["passed"], result)
        self.assertEqual((self.root / "value.py").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / "notes/새문서.md").read_text(), "설명\n")
        self.assertEqual(self.state()["phase"], "running")
        self.assertFalse(self.state()["milestones"])
        self.assertTrue(self.engine.gate("M1")["passed"])
        self.assertTrue(self.engine.gate()["passed"])
        with self.assertRaises(LoopError):
            self.accepted(assignment("late"))

    def test_conversation_provider_and_worker_are_separate(self):
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path)
        contract["execution"]["implementer"] = "codex"
        write_json(path, contract)
        self.running()
        result = self.accepted(assignment())
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["assignment"]["provider"], "codex")
        self.assertEqual(self.kb.get("task", self.task_id)["provider"], "claude")
        process = read_json(Path(result["assignment"]["directory"]) / "process.json")
        self.assertIn("read-only", process["argv"])
        self.assertTrue(self.engine.gate("M1")["passed"])
        gate = read_json(Path(self.state()["latest_gate"]))
        self.assertEqual(gate["review"]["provider"], "claude")

    def test_prestart_and_paths_cannot_expand_authority(self):
        with self.assertRaisesRegex(LoopError, "before start"):
            self.accepted(assignment(kind="implementation", writes=["value.py"]))
        for name in ("../outside", "/tmp/outside", "src/../value.py", "src/*", "AGENTS.md", "src/CLAUDE.md", ".loop/state/hack", ".claude/settings.json", ".agents/skills/loop-dev/SKILL.md", ".agents/skills/loop-dev/agents/openai.yaml"):
            with self.subTest(name=name), self.assertRaises(LoopError):
                validate_request(assignment(kind="implementation", writes=[name]), self.root, "running", True, ["AGENTS.md"])
        (self.root / "alias").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(LoopError, "symlink"):
            validate_request(assignment(kind="implementation", writes=["alias/value.py"]), self.root, "running", True, [])
        with self.assertRaisesRegex(LoopError, "development"):
            validate_request(assignment(kind="implementation"), self.root, "working", False, [])

    def test_invalid_change_rejects_all_proposals(self):
        self.running()
        request = assignment("scoped-edit", "implementation", ["value.py"])
        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"value.py": "VALUE = 2\n", "unassigned.txt": "extra"})}):
            result = self.engine.delegate(request)
        self.assertFalse(result["passed"])
        self.assertEqual(result["assignment"]["status"], "candidate")
        self.assertIn("out-of-scope", " ".join(result["assignment"]["validation_errors"]))
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
        self.assertFalse((self.root / "unassigned.txt").exists())
        self.assertFalse(result["assignment"]["applied"])

    def test_invalid_or_stale_worker_claims_rejected(self):
        request = assignment()
        result = self.accepted(request)
        valid = read_json(Path(result["assignment"]["result"]))
        before = Delegation(self.kb, self.task_id, self.root).observation()
        variants = []
        value = copy.deepcopy(valid); value["coverage"] = []; variants.append(value)
        value = copy.deepcopy(valid); value["coverage"][0]["satisfied"] = False; variants.append(value)
        value = copy.deepcopy(valid); value["outputs"][0]["id"] = "unassigned"; variants.append(value)
        value = copy.deepcopy(valid); value["outputs"][0]["evidence"] = []; variants.append(value)
        value = copy.deepcopy(valid); value["status"] = "blocked"; variants.append(value)
        value = copy.deepcopy(valid); value["changes"] = [{"path": "value.py", "operation": "write", "base_hash": "fake", "content": "broken"}]; variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(LoopError):
                validate_result(value, request, self.root, before)

    def test_direct_source_mutation_is_detected_and_preserved(self):
        with patch.dict(os.environ, {"LOOP_WORKER_MUTATE": "value.py"}):
            result = self.accepted(assignment())
        self.assertFalse(result["passed"])
        self.assertIn("Sources changed", result["assignment"]["error"])
        self.assertEqual((self.root / "value.py").read_text(), "outside change\n")
        self.assertFalse(result["assignment"]["applied"])

    def test_blocked_result_has_no_patch_side_effects(self):
        first = self.accepted(assignment())
        result = read_json(Path(first["assignment"]["result"]))
        result.update(status="blocked", blockers=["The allowed read scope omits the interface definition."],
                      changes=[{"path": "value.py", "operation": "write", "base_hash": file_hash(self.root / "value.py"), "content": "VALUE = 2\n"}])
        self.running()
        with patch.dict(os.environ, {"LOOP_WORKER_RESULT": json.dumps(result)}):
            blocked = self.accepted(assignment("limited-edit", "implementation", ["value.py"]))
        self.assertFalse(blocked["passed"])
        self.assertEqual(blocked["assignment"]["status"], "blocked")
        self.assertEqual((self.root / "value.py").read_text(), "VALUE = 1\n")
        self.assertEqual(self.state()["phase"], "running")

    def test_fresh_dependency_and_selected_knowledge_only(self):
        first = self.accepted(assignment())
        location = self.kb.attach(self.root)
        topic = {"id": "known-value", "kind": "fact", "scope": "project", "status": "confirmed", "summary": "VALUE is defined locally.",
                 "evidence": ["value.py:1"], "paths": ["value.py"]}
        self.kb.put_topic(self.kb.prepare_topic(topic, self.kb.observe([location])))
        request = assignment("plan-value", "planning")
        request.update(depends_on=["inspect-value"], knowledge_ids=["known-value"])
        result = self.accepted(request)
        self.assertTrue(result["passed"], result)
        packet = self.packet(result)
        self.assertEqual(packet["knowledge"][0]["id"], "known-value")
        self.assertEqual(packet["prerequisites"][0]["outputs"][0]["id"], "explanation")
        (self.root / "value.py").write_text("VALUE = 2\n")
        with self.assertRaisesRegex(LoopError, "knowledge.*stale"):
            self.accepted(request)
        request.update(id="new-plan", knowledge_ids=[])
        with self.assertRaisesRegex(LoopError, "prerequisite.*stale"):
            self.accepted(request)
        altered = assignment(); altered["instruction"] = "Different scope"
        with self.assertRaisesRegex(LoopError, "immutable"):
            self.accepted(altered)

    def test_failure_budgets_and_interrupted_recovery(self):
        request = assignment()
        with patch.dict(os.environ, {"LOOP_TEST_FAIL": "1"}):
            for count in range(1, 4):
                result = self.accepted(request)
                self.assertFalse(result["passed"])
                self.assertEqual(result["assignment"]["attempts"], count)
        with self.assertRaisesRegex(LoopError, "budget exhausted"):
            self.accepted(request)
        with patch("loop_dev.providers.structured_call", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.accepted(assignment("interrupted"))
        with self.assertRaisesRegex(LoopError, "--recover"):
            self.accepted(assignment("interrupted"))
        recovered = self.accepted(assignment("interrupted"), "Inspected logs; no process remains")
        self.assertTrue(recovered["passed"])
        self.assertEqual(recovered["assignment"]["attempts"], 2)
        self.assertEqual(self.kb.get("worker-budget", self.task_id)["calls"], 5)

    def test_task_wide_budget_cannot_be_reset_with_new_assignment_ids(self):
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path); contract["execution"]["max_worker_calls"] = 1; write_json(path, contract)
        self.assertTrue(self.accepted(assignment())["passed"])
        with self.assertRaisesRegex(LoopError, "budget exhausted"):
            self.accepted(assignment("different-id"))

    def test_worker_timeout_records_failure_and_consumes_attempt(self):
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path); contract["execution"]["worker_timeout_seconds"] = 1; write_json(path, contract)
        with patch.dict(os.environ, {"LOOP_WORKER_PAUSE": "1"}):
            result = self.accepted(assignment())
        self.assertFalse(result["passed"])
        process = read_json(Path(result["assignment"]["directory"]) / "process.json")
        self.assertTrue(process["timed_out"])
        self.assertEqual(result["assignment"]["attempts"], 1)

    def test_common_work_in_non_git_directory_uses_explicit_worker(self):
        directory = Path(self.temp.name) / "notes"; directory.mkdir()
        (directory / "value.py").write_text("VALUE = 1\n")
        work = Work(self.kb)
        opened = work.open("explanation", "Explain VALUE", "claude", [directory], worker="codex")
        task_id = opened["task"]["id"]
        work.define(task_id, {"outcome": "Understand VALUE", "deliverable": "An explanation", "criteria": assignment()["criteria"], "decisions": [], "open_questions": []})
        work.start(task_id)
        result = self.accepted_work(work, task_id, assignment(goal_criteria=("C1",)))
        self.assertTrue(result["passed"], result)
        process = read_json(Path(result["assignment"]["directory"]) / "process.json")
        self.assertIn("--skip-git-repo-check", process["argv"])
        self.assertFalse((directory / ".loop").exists())
        self.assertEqual(work.load(task_id)["phase"], "working")
        finished = work.finish(task_id, {"summary": "VALUE is defined in value.py.", "outputs": [result["assignment"]["result"]],
                    "checks": [{"criterion_id": "C1", "satisfied": True, "evidence": "value.py:1"}]})
        self.assertTrue(finished["passed"])
        self.assertEqual(len(finished["delegations"]), 1)

    def test_common_writing_and_explicit_directory_selection(self):
        first = Path(self.temp.name) / "first"; first.mkdir()
        second = Path(self.temp.name) / "second"; second.mkdir()
        for directory in (first, second):
            (directory / "value.py").write_text("VALUE = 1\n")
        work = Work(self.kb)
        task_id = work.open("document", "Write an explanation", "claude", [first, second])["task"]["id"]
        work.define(task_id, {"outcome": "Document VALUE", "deliverable": "README", "criteria": assignment()["criteria"], "decisions": [], "open_questions": []})
        work.start(task_id)
        request = assignment("write-note", "writing", ["README.md"], ("C1",))
        with self.assertRaisesRegex(LoopError, "--path"):
            self.accepted_work(work, task_id, request)
        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"README.md": "VALUE is one.\n"})}):
            result = self.accepted_work(work, task_id, request, second)
        self.assertTrue(result["passed"], result)
        self.assertEqual((second / "README.md").read_text(), "VALUE is one.\n")
        self.assertFalse((first / "README.md").exists())
        with self.assertRaisesRegex(LoopError, "belong"):
            self.accepted_work(work, task_id, request, self.root)
        location = self.kb.attach(second)
        with locked(self.kb.home / "worker-locks" / location["id"]):
            with self.assertRaisesRegex(LoopError, "Another loop command"):
                self.accepted_work(work, task_id, assignment("concurrent"), second)

    def test_cli_delegation_round_trip(self):
        path = Path(self.temp.name) / "assignment.json"; write_json(path, assignment())
        command = [sys.executable, "scripts/loop.py", "--root", str(self.root), "delegate", "demo", "--input", str(path)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        candidate = json.loads(result.stdout)
        self.assertEqual(candidate["assignment"]["status"], "candidate")
        assessment = Path(self.temp.name) / "assessment.json"
        write_json(assessment, fixture_assessment(self.kb, candidate))
        result = subprocess.run([sys.executable, "scripts/loop.py", "--root", str(self.root),
                                 "assess", "demo", "--input", str(assessment)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["passed"])

    def test_assignments_bind_to_location_and_cross_directory_dependencies_stay_fresh(self):
        first = Path(self.temp.name) / "first"; first.mkdir()
        second = Path(self.temp.name) / "second"; second.mkdir()
        for root in (first, second):
            (root / "value.py").write_text("VALUE = 1\n")
        work = Work(self.kb)
        task_id = work.open("research", "Compare values", "claude", [first, second])["task"]["id"]
        self.assertTrue(self.accepted_work(work, task_id, assignment("first-source", goal_criteria=("goal_setting",)), first)["passed"])
        with self.assertRaisesRegex(LoopError, "immutable"):
            self.accepted_work(work, task_id, assignment("first-source", goal_criteria=("goal_setting",)), second)
        request = assignment("compare-sources", goal_criteria=("goal_setting",))
        request["depends_on"] = ["first-source"]
        result = self.accepted_work(work, task_id, request, second)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.packet(result)["prerequisites"][0]["location_id"], self.kb.attach(first)["id"])
        (first / "value.py").write_text("VALUE = 2\n")
        with self.assertRaisesRegex(LoopError, "prerequisite.*stale"):
            self.accepted_work(work, task_id, request, second)

    def test_changed_selected_knowledge_invalidates_cached_output(self):
        location = self.kb.attach(self.root)
        topic = {"id": "explicit-context", "kind": "fact", "scope": "project", "status": "confirmed",
                 "summary": "The value is read by a local consumer.", "evidence": ["check.py:1"], "paths": ["check.py"]}
        self.kb.put_topic(self.kb.prepare_topic(topic, self.kb.observe([location])))
        request = assignment(); request["knowledge_ids"] = [topic["id"]]
        self.assertTrue(self.accepted(request)["passed"])
        topic["summary"] = "The consumer asserts VALUE equals one."
        self.kb.put_topic(self.kb.prepare_topic(topic, self.kb.observe([location])))
        result = self.accepted(request)
        self.assertTrue(result["passed"])
        self.assertFalse(result.get("reused"))
        self.assertEqual(result["assignment"]["attempts"], 2)
        self.assertEqual(self.packet(result)["knowledge"][0]["summary"], topic["summary"])

    def test_ignored_write_target_rejected_before_call(self):
        self.running()
        (self.root / ".gitignore").write_text(".loop/state/\nignored/\n")
        with patch("loop_dev.providers.structured_call", side_effect=AssertionError("must not call")):
            with self.assertRaisesRegex(LoopError, "ignored"):
                self.accepted(assignment("ignored-output", "implementation", ["ignored/new.py"]))
        self.assertFalse((self.root / "ignored/new.py").exists())

    def test_delete_keeps_original_bytes_and_partial_apply_is_not_success(self):
        self.running()
        (self.root / "notes.txt").write_bytes(b"first\r\nsecond\r\n")
        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"notes.txt": None})}):
            result = self.accepted(assignment("delete-note", "implementation", ["notes.txt"]))
        self.assertTrue(result["passed"], result)
        self.assertFalse((self.root / "notes.txt").exists())
        backup = read_json(Path(result["assignment"]["directory"]) / "originals.json")[0]
        self.assertEqual(backup["content"].encode(), b"first\r\nsecond\r\n")

        # macOS temporary roots can be symlink aliases. Simulate that on Linux too
        # so the injected external edit targets the same real file on both hosts.
        logical_root = Path(self.temp.name) / "project-alias"
        logical_root.symlink_to(self.root, target_is_directory=True)

        def external_change(path, value):
            write_text(path, value)
            if path.resolve() == (logical_root / "value.py").resolve():
                (self.root / "check.py").write_text("outside edit\n")

        with patch.dict(os.environ, {"LOOP_WORKER_EDITS": json.dumps({"value.py": "VALUE = 2\n"})}), \
                patch("loop_dev.delegation.write_text", side_effect=external_change):
            failed = self.accepted(assignment("racing-edit", "implementation", ["value.py"]))
        self.assertFalse(failed["passed"])
        self.assertIn("Unexpected source", failed["assignment"]["error"])
        self.assertEqual(failed["assignment"]["applied"], ["value.py"])
        self.assertEqual((self.root / "check.py").read_text(), "outside edit\n")

    def test_installed_worker_entry_and_common_cli(self):
        from test_process_install import installer
        installer.install(self.root)
        request = Path(self.temp.name) / "assignment.json"
        write_json(request, assignment(goal_criteria=("goal_setting",)))
        cli = [sys.executable, str(self.root / ".loop/loop.py"), "--root", str(self.root)]

        def run(*args, code=0):
            result = subprocess.run(cli + list(args), cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, code, result.stderr)
            return json.loads(result.stdout)

        opened = run("work", "open", "--kind", "explanation", "--title", "Explain the value", "--provider", "claude", "--worker", "codex")
        candidate = run("work", "delegate", opened["task"]["id"], "--input", str(request), code=1)
        assessment = Path(self.temp.name) / "installed-assessment.json"
        write_json(assessment, fixture_assessment(self.kb, candidate))
        result = run("work", "assess", opened["task"]["id"], "--input", str(assessment))
        self.assertTrue(result["passed"])
        self.assertEqual(result["assignment"]["provider"], "codex")
        record = run("knowledge", "get", "delegation", result["assignment"]["id"])
        self.assertIn("Working directory", Path(record["document"]).read_text())
        self.assertIn("Explicitly assigned workers/reviewers", (self.root / "CLAUDE.md").read_text())
        self.assertTrue((self.root / ".loop/templates/assignment.example.json").exists())
