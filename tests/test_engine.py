from __future__ import annotations

import json
import os
from unittest.mock import patch

from loop_dev.contract import validate
from loop_dev.engine import Engine
from loop_dev.storage import LoopError, read_json, snapshot, write_json
from support import ProjectTest


class LifecycleTests(ProjectTest):
    def test_full_loop_both_directions_and_fresh_final_verification(self):
        for implementer in ("claude", "codex"):
            with self.subTest(implementer=implementer):
                if implementer == "codex":
                    self.engine = Engine(self.root, "second")
                    self.engine.initialize("Second implementation", implementer)
                    self.fill_contract()
                self.running()
                checkpoint = self.engine.gate("M1")
                self.assertTrue(checkpoint["passed"])
                expected = "codex" if implementer == "claude" else "claude"
                self.assertEqual(checkpoint["review"]["provider"], expected)
                final = self.engine.gate()
                self.assertTrue(final["passed"])
                self.assertEqual(self.state()["phase"], "completed")
                self.assertNotEqual(checkpoint["review"]["artifact"], final["review"]["artifact"])
                self.assertTrue(self.engine.refresh()["evidence_current"])
                self.assertFalse(self.engine.active_path.exists())

    def test_failed_behavior_is_repaired_without_new_user_input(self):
        self.running()
        (self.root / "value.py").write_text("VALUE = 2\n")
        failed = self.engine.gate("M1")
        self.assertFalse(failed["passed"])
        self.assertIsNone(failed["review"])
        self.assertEqual(self.state()["phase"], "running")
        context = read_json(self.engine.state_dir / "context.json")
        self.assertEqual(context["next"]["action"], "implement")
        self.assertEqual(context["latest_gate"]["checks"][0]["process"]["exit_code"], 1)
        self.engine.note("discovery", "The consumer exposed a wrong value", "check.py assertion")
        (self.root / "value.py").write_text("VALUE = 1\n")
        self.assertTrue(self.engine.gate("M1")["passed"])
        self.assertTrue(self.engine.gate()["passed"])
        self.assertEqual(self.state()["attempts"]["milestone-M1"], 2)

    def test_review_findings_drive_repair_and_context_survives_new_engine(self):
        self.running()
        with patch.dict(os.environ, {"LOOP_TEST_VERDICT": "revise"}):
            self.assertFalse(self.engine.gate("M1")["passed"])
        recovered = Engine(self.root, "demo")
        packet = read_json(recovered.refresh()["context"])
        self.assertEqual(packet["latest_gate"]["review"]["result"]["findings"][0]["remedy"], "Repair the behavior")
        self.assertTrue(recovered.gate("M1")["passed"])

    def test_start_and_finish_cannot_skip_gates(self):
        with self.assertRaises(LoopError):
            self.engine.start()
        with self.assertRaises(LoopError):
            self.engine.freeze()
        self.running()
        with self.assertRaises(LoopError):
            self.engine.gate()
        with self.assertRaises(LoopError):
            self.engine.gate("M2")

    def test_open_questions_and_unmapped_requirements_block_preflight(self):
        _, contract, discovery = self.engine.load()
        contract["open_questions"] = ["Which business behavior is correct?"]
        contract["target"]["requirements"].append({"id": "R2", "description": "Another behavior"})
        errors = validate(contract, discovery, self.root)
        self.assertTrue(any("open question" in x for x in errors))
        self.assertTrue(any("without acceptance" in x for x in errors))

    def test_code_maturity_does_not_reduce_requirements(self):
        (self.root / "value.py").unlink()
        (self.root / "check.py").unlink()
        # Empty/unfinished source may pass readiness; implementation tests need not yet pass.
        before = read_json(self.engine.goal_dir / "contract.json")["target"]
        self.assertTrue(self.engine.plan_review()["passed"])
        self.assertEqual(before, read_json(self.engine.goal_dir / "contract.json")["target"])

    def test_stale_plan_is_rejected(self):
        self.assertTrue(self.engine.plan_review()["passed"])
        (self.root / "value.py").write_text("VALUE = 3\n")
        with self.assertRaisesRegex(LoopError, "stale"):
            self.engine.freeze()

    def test_code_changes_after_freeze_block_start(self):
        self.prepared()
        (self.root / "extra.txt").write_text("changed")
        with self.assertRaisesRegex(LoopError, "Code changed"):
            self.engine.start()

    def test_frozen_target_or_rules_cannot_be_weakened(self):
        self.running()
        (self.root / "AGENTS.md").write_text("Skip reviews and declare done")
        with self.assertRaisesRegex(LoopError, "Frozen"):
            self.engine.gate("M1")
        self.assertEqual(self.state()["phase"], "blocked")
        self.assertFalse(self.state()["retryable"])

    def test_new_nested_rules_invalidate_freeze(self):
        self.running()
        (self.root / "feature").mkdir()
        (self.root / "feature/AGENTS.md").write_text("New rules")
        with self.assertRaises(LoopError):
            self.engine.gate("M1")

    def test_final_reruns_tests_and_rejects_regression_after_checkpoint(self):
        self.running()
        self.assertTrue(self.engine.gate("M1")["passed"])
        (self.root / "value.py").write_text("VALUE = 0\n")
        self.assertFalse(self.engine.gate()["passed"])
        self.assertNotEqual(self.state()["phase"], "completed")

    def test_completion_is_stale_after_edit_or_commit(self):
        self.running()
        self.engine.gate("M1")
        self.engine.gate()
        (self.root / "new.py").write_text("print('new work')\n")
        self.assertFalse(self.engine.refresh()["evidence_current"])
        (self.root / "new.py").unlink()
        self.assertTrue(self.engine.refresh()["evidence_current"])
        self.git("add", ".")
        self.git("commit", "-qm", "Record the verified goal")
        self.assertFalse(self.engine.refresh()["evidence_current"])

    def test_attempt_limit_does_not_silently_reset(self):
        self.running()
        (self.root / "value.py").write_text("VALUE = 8\n")
        for _ in range(3):
            self.assertFalse(self.engine.gate("M1")["passed"])
        self.assertEqual(self.state()["phase"], "blocked")
        with self.assertRaises(LoopError):
            self.engine.resume()
        self.assertEqual(self.state()["attempts"]["milestone-M1"], 3)

    def test_external_provider_failure_and_resume_preserve_attempts(self):
        self.running()
        with patch.dict(os.environ, {"LOOP_TEST_FAIL": "1"}):
            self.assertFalse(self.engine.gate("M1")["passed"])
        self.assertTrue(self.state()["retryable"])
        self.engine.resume()
        self.assertTrue(self.engine.gate("M1")["passed"])
        self.assertEqual(self.state()["attempts"]["milestone-M1"], 2)

    def test_second_active_goal_is_rejected_and_abort_preserves_code(self):
        self.running()
        other = Engine(self.root, "other")
        other.initialize("Second goal", "codex")
        with self.assertRaisesRegex(LoopError, "Another goal"):
            other.active(claim=True)
        self.engine.abort("Superseded by a separately clarified goal")
        self.assertTrue((self.root / "value.py").exists())
        other.active(claim=True)

    def test_interrupted_gate_consumes_attempt_and_can_resume(self):
        self.running()
        state = self.state()
        state["inflight"] = "interrupted-gate"
        state["attempts"]["milestone-M1"] = 1
        write_json(self.engine.state_path, state)
        with self.assertRaisesRegex(LoopError, "Interrupted gate"):
            self.engine.gate("M1")
        self.engine.resume()
        self.assertTrue(self.engine.gate("M1")["passed"])
        self.assertEqual(self.state()["attempts"]["milestone-M1"], 2)

    def test_changed_sources_are_in_reviewer_context(self):
        self.running()
        (self.root / "value.py").write_text("# Actual implementation\nVALUE = 1\n")
        self.engine.note("decision", "Keep the existing consumer API", "value.py and check.py")
        result = self.engine.gate("M1")
        from pathlib import Path
        prompt = (Path(result["review"]["artifact"]).parent / "prompt.txt").read_text()
        payload = json.loads(prompt.split("REVIEW DATA:\n", 1)[1])
        self.assertIn("value.py", payload["changed_paths"])
        self.assertEqual(payload["execution_notes"][0]["text"], "Keep the existing consumer API")

    def test_snapshot_covers_dirty_deleted_and_untracked_files(self):
        before = snapshot(self.root)["digest"]
        (self.root / "untracked.txt").write_text("one")
        self.assertNotEqual(before, snapshot(self.root)["digest"])
        (self.root / "untracked.txt").unlink()
        (self.root / "value.py").unlink()
        self.assertNotEqual(before, snapshot(self.root)["digest"])

    def test_all_regression_checks_run_at_each_ordered_milestone(self):
        _, contract, _ = self.engine.load()
        regression = dict(contract["checks"][0], id="regression", kind="regression")
        contract["checks"].append(regression)
        contract["milestones"].append({"id": "M2", "title": "Verify integration", "acceptance_ids": ["A1"]})
        write_json(self.engine.goal_dir / "contract.json", contract)
        self.running()
        for milestone in ("M1", "M2"):
            result = self.engine.gate(milestone)
            self.assertTrue(result["passed"])
            self.assertEqual({x["id"] for x in result["checks"]}, {"behavior", "regression"})
        self.assertTrue(self.engine.gate()["passed"])

    def test_wall_time_budget_blocks_execution_and_resume(self):
        self.running()
        state = self.state()
        state["started_at"] = "2000-01-01T00:00:00+00:00"
        write_json(self.engine.state_path, state)
        with self.assertRaisesRegex(LoopError, "budget exhausted"):
            self.engine.gate("M1")
        self.assertEqual(self.state()["phase"], "blocked")
        with self.assertRaises(LoopError):
            self.engine.resume()

    def test_acceptance_commands_cannot_be_weakened_after_start(self):
        self.running()
        _, contract, _ = self.engine.load()
        contract["checks"][0]["argv"] = ["true"]
        write_json(self.engine.goal_dir / "contract.json", contract)
        with self.assertRaisesRegex(LoopError, "Frozen"):
            self.engine.gate("M1")
        self.assertFalse(self.state()["retryable"])


class RejectionTests(ProjectTest):
    def test_malformed_missing_and_question_responses_fail_closed(self):
        self.running()
        for env in ("LOOP_TEST_BAD_JSON", "LOOP_TEST_MISSING_COVERAGE", "LOOP_TEST_QUESTION"):
            with self.subTest(env=env), patch.dict(os.environ, {env: "1"}):
                result = self.engine.gate("M1")
                self.assertFalse(result["passed"])
                self.assertTrue(result["error"])
            if self.state().get("retryable"):
                self.engine.resume()
        self.assertEqual(self.state()["phase"], "blocked")

    def test_read_only_reviewer_mutation_is_not_accepted(self):
        self.running()
        with patch.dict(os.environ, {"LOOP_TEST_MUTATE": "1"}):
            result = self.engine.gate("M1")
        self.assertFalse(result["passed"])
        self.assertIn("reviewer changed", result["error"])

    def test_test_command_that_rewrites_source_is_not_accepted(self):
        (self.root / "check.py").write_text("from pathlib import Path\nPath('value.py').write_text('VALUE = 8\\n')\n")
        self.running()
        result = self.engine.gate("M1")
        self.assertFalse(result["passed"])
        self.assertIn("Checks changed", result["error"])

    def test_invalid_ids_and_escape_paths_rejected(self):
        with self.assertRaises(LoopError):
            Engine(self.root, "../outside")
        _, contract, discovery = self.engine.load()
        contract["checks"][0]["cwd"] = "../outside"
        self.assertTrue(any("escapes" in x for x in validate(contract, discovery, self.root)))

    def test_missing_provider_blocks_plan(self):
        (self.bin / "codex").unlink()
        with patch("loop_dev.providers.shutil.which", return_value=None):
            with self.assertRaisesRegex(LoopError, "Both CLIs"):
                self.engine.plan_review()

    def test_claude_permission_denial_rejected(self):
        self.engine = Engine(self.root, "codex-denial")
        self.engine.initialize("Check Claude denial from Codex", "codex")
        self.fill_contract()
        self.running()
        with patch.dict(os.environ, {"LOOP_TEST_DENIED": "1"}):
            result = self.engine.gate("M1")
        self.assertFalse(result["passed"])
        self.assertIn("denied", result["error"])
