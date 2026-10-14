"""Linked runtime policy updates expire evidence without widening target reads."""
from pathlib import Path
import shutil
from unittest.mock import patch

from loop_dev import storage
from loop_dev.delegation import Delegation
from loop_dev.session import Session
from loop_dev.storage import LoopError, digest, engine_digest, inside, snapshot, write_json
from loop_dev.work import Work
from support import FAKE_CLI, ProjectTest, fixture_assessment
from test_delegation import WORKER_BRANCH, assignment


class InstallFreshnessTests(ProjectTest):
    def setUp(self):
        super().setUp()
        self.source = Path(self.temp.name) / "runtime"
        original = Path(storage.__file__).resolve().parent.parent
        for name in ("loop_dev", "rules", "skills"):
            shutil.copytree(original / name, self.source / name)
        linked = patch.object(storage, "__file__", str(self.source / "loop_dev/storage.py"))
        linked.start()
        self.addCleanup(linked.stop)
        self.kb = self.engine.knowledge
        for name in ("claude", "codex"):
            (self.bin / name).write_text(FAKE_CLI.replace(
                'data = json.loads(prompt.split(', WORKER_BRANCH + 'data = json.loads(prompt.split(', 1))

    def mutate(self, relative):
        path = self.source / relative
        path.write_text(path.read_text() + "\n# Runtime policy changed\n")

    def topic(self, **overrides):
        return {"id": "value-analysis", "kind": "fact", "scope": "checkout", "status": "confirmed",
                "summary": "VALUE is defined in value.py.", "evidence": ["value.py:1"],
                "paths": ["value.py"], **overrides}

    def test_bounded_policy_seal_detects_code_rules_and_skill_metadata(self):
        before = snapshot(self.root)
        for relative in ("loop_dev/cli.py", "rules/work.md", "skills/loop-dev/SKILL.md",
                         "skills/loop-dev/agents/openai.yaml"):
            with self.subTest(relative=relative):
                seal = engine_digest()
                self.mutate(relative)
                self.assertNotEqual(engine_digest(), seal)
        seal = engine_digest()
        (self.source / "unrelated.txt").write_text("not policy")
        self.assertEqual(engine_digest(), seal)
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(self.kb.list("topic"))

    def test_frozen_development_evidence_expires_on_source_rule_update(self):
        self.prepared()
        state, contract, discovery = self.engine.load()
        self.engine.integrity(state, contract, discovery)
        self.mutate("rules/workflow.md")
        with self.assertRaisesRegex(LoopError, "engine changed"):
            self.engine.integrity(state, contract, discovery)

    def test_knowledge_stales_for_git_and_non_git_without_restamping_decisions(self):
        nongit = Path(self.temp.name) / "plain"
        nongit.mkdir()
        (nongit / "value.py").write_text("VALUE = 1\n")
        for root in (self.root, nongit):
            with self.subTest(root=root):
                location = self.kb.attach(root)
                entry = self.topic(id="analysis-" + root.name)
                saved = self.kb.remember(entry, [location], digest(self.kb.observe([location])))
                decision = self.topic(id="decision-" + root.name, kind="decision", paths=[],
                                      basis="user", applies_to="This checkout only")
                self.kb.remember(decision, [location], digest(self.kb.observe([location])))
                self.mutate("skills/loop-dev/SKILL.md")
                context = self.kb.context([location])
                self.assertIn("harness_changed", next(x for x in context["stale"] if x["id"] == entry["id"])["stale_reasons"])
                self.assertIn(decision["id"], [x["id"] for x in context["reusable"]])
                self.assertEqual(self.kb.get("topic", entry["id"]), saved)

    def test_session_import_preserves_policy_stamp_and_legacy_requires_inspection(self):
        session = Session(self.root, self.state()["session_id"], self.kb)
        entry = {"id": "analysis", "kind": "analysis", "scope": "session", "summary": "VALUE is defined locally.",
                 "evidence": ["value.py:1"], "paths": ["value.py"]}
        session.remember(entry, session.refresh()["revision"])
        self.mutate("rules/context.md")
        current = session.refresh()
        self.assertIn({"kind": "harness_changed"}, current["stale"][0]["stale_reasons"])
        self.assertIn("harness_changed", current["knowledge"]["stale"][0]["stale_reasons"])
        # A pre-upgrade record cannot inherit the current source stamp on import.
        data = session.load()
        data["entries"]["analysis"].pop("harness")
        write_json(session.path, data)
        current = session.refresh()
        self.assertIn({"kind": "harness_changed"}, current["stale"][0]["stale_reasons"])
        self.assertIn("harness_changed", current["knowledge"]["stale"][0]["stale_reasons"])
        self.assertNotIn("harness", session.load()["entries"]["analysis"])

    def test_pending_common_candidates_refuse_policy_drift_in_git_and_non_git(self):
        for is_git in (True, False):
            with self.subTest(git=is_git):
                root = Path(self.temp.name) / ("git-work" if is_git else "plain-work")
                root.mkdir()
                (root / "value.py").write_text("VALUE = 1\n")
                (root / "check.py").write_text("from value import VALUE\nassert VALUE == 1\n")
                if is_git:
                    storage.git(root, "init", "-q")
                work = Work(self.kb)
                task_id = work.open("explanation", "Explain VALUE", "claude", [root])["task"]["id"]
                request = assignment(goal_criteria=("C1",))
                work.define(task_id, {"outcome": "Understand VALUE", "deliverable": "An explanation",
                                     "criteria": request["criteria"], "decisions": [], "open_questions": []})
                work.start(task_id)
                worker = Delegation(self.kb, task_id, root)
                before = worker.observation()
                candidate = work.delegate(task_id, request)
                self.assertEqual(candidate["assignment"]["status"], "candidate")
                payload = fixture_assessment(self.kb, candidate)
                self.mutate("rules/delegation.md")
                after = worker.observation()
                self.assertEqual(before["files"], after["files"])
                self.assertEqual(before["rules"], after["rules"])
                self.assertNotEqual(before["digest"], after["digest"])
                with self.assertRaisesRegex(LoopError, "Sources changed"):
                    work.assess(task_id, payload)
                self.assertEqual(self.kb.get("delegation", candidate["assignment"]["id"])["status"], "candidate")

    def test_same_project_adapter_links_work_but_external_symlinks_still_fail(self):
        adapter = self.root / ".loop/install/adapters/codex"
        adapter.mkdir(parents=True)
        (adapter / "SKILL.md").write_text("Installed adapter\n")
        (adapter / "agents").mkdir()
        (adapter / "agents/openai.yaml").write_text("policy: explicit\n")
        discovery = self.root / ".agents/skills"
        discovery.mkdir(parents=True)
        (discovery / "loop-dev").symlink_to(adapter, target_is_directory=True)
        observed = self.kb.observe([self.kb.attach(self.root)])
        rules = next(iter(observed.values()))["rules"]
        self.assertIn(".agents/skills/loop-dev/SKILL.md", rules)
        self.assertTrue(inside(self.root, ".agents/skills/loop-dev/SKILL.md").is_file())
        (self.root / "external").symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(LoopError, "escapes repository"):
            inside(self.root, "external/rules/work.md")
