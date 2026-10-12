from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from loop_dev.engine import Engine
from loop_dev.session import Session
from loop_dev.storage import LoopError, read_json, snapshot, write_json
from support import ProjectTest


class SessionTests(ProjectTest):
    def setUp(self):
        super().setUp()
        self.session = Session(self.root, self.state()["session_id"])

    def entry(self, topic="value", paths=None, **overrides):
        return {"id": topic, "kind": "analysis", "scope": "session",
                "summary": "value.py exports VALUE, consumed by check.py",
                "evidence": ["value.py:1; check.py:1-2"],
                "paths": ["value.py", "check.py"] if paths is None else paths, **overrides}

    def remember(self, entry):
        return self.session.remember(entry, self.session.refresh("demo")["revision"])

    def test_next_goal_in_same_session_reuses_analysis_without_rewriting_it(self):
        self.remember(self.entry())
        stored = self.session.load()["entries"]["value"]
        second = Engine(self.root, "followup")
        result = second.initialize("Extend the feature", "claude", self.session.id)
        restored = read_json(result["context"])
        self.assertEqual(restored["session"]["reusable"][0]["summary"], stored["summary"])
        self.assertEqual(restored["session"]["reusable"][0]["recorded_at"], stored["recorded_at"])
        self.assertEqual(restored["session"]["stale"], [])
        recovered = Engine(self.root, "followup")
        self.assertEqual(recovered.refresh()["session_id"], self.session.id)
        self.assertEqual(self.session.load()["entries"]["value"], stored)
        self.assertEqual(read_json(result["context"])["goal"]["target"]["outcome"], "")

    def test_edit_invalidates_only_dependent_topics_and_refresh_cannot_revalidate(self):
        self.remember(self.entry())
        self.remember(self.entry("rules-summary", ["AGENTS.md"]))
        old = self.session.load()["entries"]["value"]
        (self.root / "value.py").write_text("VALUE = 2\n")
        packet = self.session.refresh("demo")
        self.assertEqual(packet["delta"]["modified"], ["value.py"])
        self.assertEqual([x["id"] for x in packet["reusable"]], ["rules-summary"])
        self.assertEqual([x["id"] for x in packet["stale"]], ["value"])
        self.assertEqual(self.session.refresh("demo")["stale"][0]["generation"], 1)
        self.assertEqual(self.session.load()["entries"]["value"], old)
        updated = self.remember(self.entry(summary="VALUE now equals 2; consumer still expects 1"))
        self.assertEqual(updated["stale"], [])
        self.assertEqual(self.session.load()["entries"]["value"]["generation"], 2)
        self.assertEqual(len(list((self.session.directory / "history").glob("value-*.json"))), 1)

    def test_directory_membership_and_missing_file_dependencies_detect_add_delete_rename(self):
        folder = self.root / "src"
        folder.mkdir()
        (folder / "one.py").write_text("ONE = 1\n")
        self.remember(self.entry("module", ["src/"]))
        self.remember(self.entry("missing-config", ["config.json"]))
        (folder / "one.py").rename(folder / "two.py")
        (self.root / "config.json").write_text("{}\n")
        packet = self.session.refresh()
        self.assertEqual({x["id"] for x in packet["stale"]}, {"module", "missing-config"})
        changed = packet["stale"][0]["stale_reasons"][0]["paths"]
        self.assertEqual(changed["added"], ["src/two.py"])
        self.assertEqual(changed["deleted"], ["src/one.py"])

    def test_new_rules_and_branch_switch_invalidate_but_content_unchanged_commit_does_not(self):
        self.remember(self.entry())
        self.git("add", ".")
        self.git("commit", "-qm", "Record goals without changing source")
        self.assertEqual(len(self.session.refresh()["reusable"]), 1)
        self.git("checkout", "-qb", "another-branch")
        self.assertEqual(self.session.refresh()["stale"][0]["stale_reasons"][0]["kind"], "branch_changed")
        self.remember(self.entry())
        (self.root / "nested").mkdir()
        (self.root / "nested/AGENTS.md").write_text("A new local rule\n")
        packet = self.session.refresh()
        self.assertEqual(packet["stale"][0]["stale_reasons"][0]["kind"], "rules_changed")

    def test_deleted_declared_rule_marks_stale_without_losing_session_context(self):
        self.engine.refresh()
        self.remember(self.entry())
        (self.root / "AGENTS.md").unlink()
        packet = read_json(self.engine.refresh()["context"])
        self.assertEqual(packet["session"]["stale"][0]["stale_reasons"][0]["kind"], "rules_changed")

    def test_answers_retain_basis_and_scope_across_goals_and_technical_edits(self):
        global_answer = self.entry("delivery-policy", [], kind="decision", area="delivery", basis="user",
                                   summary="For local changes in this session, deliver a verified working tree",
                                   applies_to="Local development goals in this conversation; no publishing authority")
        self.remember(global_answer)
        self.remember({**global_answer, "id": "one-goal-answer", "scope": "goal", "goal_id": "demo"})
        self.engine.refresh()
        (self.root / "value.py").write_text("VALUE = 2\n")
        second = Engine(self.root, "followup")
        packet = read_json(second.initialize("A different result", "claude", self.session.id)["context"])["session"]
        self.assertEqual([x["id"] for x in packet["reusable"]], ["delivery-policy"])
        self.assertEqual(packet["reusable"][0]["basis"], "user")
        self.assertEqual([x["id"] for x in packet["other_goal_entries"]], ["one-goal-answer"])
        self.assertEqual(packet["goal_history"][0]["decision_scope"], "goal")
        self.assertEqual(packet["goal_history"][0]["decisions"], self.engine.load()[1]["decisions"])

    def test_separate_sessions_and_provider_mismatch_do_not_leak_analysis(self):
        self.remember(self.entry())
        isolated = Session.create(self.root, "claude")
        self.assertEqual(isolated.refresh()["reusable"], [])
        with self.assertRaisesRegex(LoopError, "one provider"):
            Engine(self.root, "wrong-provider").initialize("New goal", "codex", self.session.id)
        self.assertFalse((self.root / ".loop/goals/wrong-provider").exists())

    def test_analysis_write_rejects_stale_observation_and_unsafe_dependencies(self):
        observed = self.session.refresh()["revision"]
        (self.root / "value.py").write_text("VALUE = 3\n")
        with self.assertRaisesRegex(LoopError, "changed since context"):
            self.session.remember(self.entry(), observed)
        for paths in (["../outside"], ["src/../value.py"], [".loop/state/active.json"], []):
            with self.subTest(paths=paths), self.assertRaises(LoopError):
                self.remember(self.entry(paths=paths))
        (self.root / "source-link").symlink_to(self.root / "value.py")
        with self.assertRaisesRegex(LoopError, "symlink"):
            self.remember(self.entry(paths=["source-link"]))

    def test_unchanged_manifest_excerpt_is_reused_and_changed_manifest_is_read(self):
        manifest = self.root / "package.json"
        manifest.write_text('{"name":"first"}')
        first = self.session.refresh()
        read = Path.read_text
        reads = []

        def track(path, *args, **kwargs):
            if path == manifest:
                reads.append(path)
            return read(path, *args, **kwargs)

        with patch.object(Path, "read_text", track):
            repeated = self.session.refresh()
            self.assertEqual(reads, [])
            manifest.write_text('{"name":"second"}')
            changed = self.session.refresh()
        self.assertEqual(first["inventory"]["manifest_excerpts"], repeated["inventory"]["manifest_excerpts"])
        self.assertEqual(reads, [manifest])
        self.assertIn("second", changed["inventory"]["manifest_excerpts"]["package.json"])

    def test_gate_results_are_captured_but_cache_never_skips_final_checks(self):
        self.running()
        self.engine.note("discovery", "Consumer requires VALUE = 1", "check.py:2")
        self.assertTrue(self.engine.gate("M1")["passed"])
        packet = self.session.refresh("demo")
        self.assertTrue(any("checkpoint" in x["summary"] for x in packet["reusable"]))
        self.assertEqual(packet["goal_history"][0]["notes"][0]["text"], "Consumer requires VALUE = 1")
        (self.root / "value.py").write_text("VALUE = 9\n")
        failed = self.engine.gate()
        self.assertFalse(failed["passed"])
        self.assertEqual(failed["checks"][0]["process"]["exit_code"], 1)
        self.assertTrue(self.session.refresh()["stale"])
        (self.root / "value.py").write_text("VALUE = 1\n")
        self.assertTrue(self.engine.gate()["passed"])
        current = self.session.refresh()
        self.assertEqual(len(current["reusable"]), 1)
        self.assertTrue(any("final" in x["summary"] for x in current["reusable"]))
        self.assertEqual(current["goal_history"][0]["phase"], "completed")

    def test_review_receives_only_current_topics_as_hints(self):
        self.remember(self.entry())
        self.remember(self.entry("consumer", ["check.py"]))
        self.running()
        (self.root / "value.py").write_text("# New implementation\nVALUE = 1\n")
        report = self.engine.gate("M1")
        prompt = (Path(report["review"]["artifact"]).parent / "prompt.txt").read_text()
        payload = json.loads(prompt.split("REVIEW DATA:\n", 1)[1])
        self.assertEqual([x["id"] for x in payload["session_analysis"]], ["consumer"])
        self.assertIn("value.py", payload["changed_paths"])

    def test_legacy_goal_gets_stable_session_once_and_memory_does_not_change_snapshots(self):
        state = self.state()
        del state["session_id"]
        write_json(self.engine.state_path, state)
        before = snapshot(self.root)
        first = self.engine.refresh()
        second = Engine(self.root, "demo").refresh()
        self.assertEqual(first["session_id"], second["session_id"])
        self.assertEqual(before, snapshot(self.root))

    def test_cli_persists_memory_and_reuses_session_in_next_request(self):
        launcher = Path(__file__).resolve().parents[1] / "scripts/loop.py"

        def cli(*args):
            result = subprocess.run([sys.executable, "-B", str(launcher), "--root", str(self.root), *args],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        opened = cli("session", "open", "--provider", "codex")
        path = self.root / ".loop/state/analysis-input.json"
        write_json(path, self.entry())
        saved = cli("session", "remember", opened["id"], "--input", str(path), "--observed", opened["revision"])
        self.assertEqual(saved["reusable"][0]["id"], "value")
        goal = cli("init", "next-request", "--title", "Extend behavior", "--implementer", "codex", "--session", opened["id"])
        context = cli("session", "context", opened["id"], "--goal", goal["goal"])
        self.assertEqual(context["reusable"][0]["summary"], self.entry()["summary"])
        self.assertNotIn("state", self.git("status", "--short").decode())
