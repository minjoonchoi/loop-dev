from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from loop_dev.routing import (escalate_profile, load_policy, policy_seal, select_profile,
                              validate_planning, validate_policy, validate_splits)
from loop_dev.storage import LoopError


def policy():
    return {"version": 1, "requirements": {"low": 1, "medium": 2, "high": 3}, "profiles": [
        {"id": "small-low", "provider": "codex", "model": "model-small-1", "effort": "low", "capability_rank": 1, "cost_rank": 1},
        {"id": "small-medium", "provider": "codex", "model": "model-small-1", "effort": "medium", "capability_rank": 2, "cost_rank": 2},
        {"id": "large-high", "provider": "codex", "model": "model-large-1", "effort": "high", "capability_rank": 3, "cost_rank": 4},
        {"id": "other-low", "provider": "claude", "model": "other-small-1", "effort": "low", "capability_rank": 1, "cost_rank": 1}]}


def planning(**changes):
    return {"goal_criteria": ["C1"], "rationale": "This unit owns the isolated parser output.",
            "verification": "Check parser output against the fixed accepted input examples.",
            "complexity": "low", "risk": "low", "uncertainty": "low", "evidence": [], **changes}


def assignment(name, writes=(), **changes):
    return {"id": name, "kind": "implementation", "instruction": "Implement the assigned parser behavior.",
            "read_paths": ["parser.py", "checks.py"], "write_paths": list(writes),
            "deliverables": [{"id": "parser", "description": "The parser returns the accepted values."}],
            "criteria": [{"id": "local", "description": "The fixed accepted examples are preserved."}],
            "planning": planning(), **changes}


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.goal = {"criteria": [{"id": "C1", "description": "Parse accepted inputs."},
                                   {"id": "C2", "description": "Explain the public interface."}]}

    def test_simple_work_starts_small_and_higher_demand_needs_evidence(self):
        self.assertEqual(select_profile(policy(), "codex", planning())["id"], "small-low")
        self.assertEqual(select_profile(policy(), "claude", planning())["id"], "other-low")
        with self.assertRaisesRegex(LoopError, "factual planning evidence"):
            select_profile(policy(), "codex", planning(complexity="medium"))
        selected = select_profile(policy(), "codex", planning(risk="medium", evidence=["parser.py:12 changes shared input semantics"]))
        self.assertEqual((selected["model"], selected["effort"]), ("model-small-1", "medium"))
        self.assertEqual(select_profile(policy(), "codex", planning(uncertainty="high", evidence=["decision:missing-architecture"]))["id"], "large-high")

    def test_selection_is_economical_with_stable_ties_not_list_order(self):
        value = policy()
        value["profiles"].extend([
            {"id": "cheap-capable", "provider": "codex", "model": "model-new-1", "effort": "medium", "capability_rank": 3, "cost_rank": 2},
            {"id": "a-medium", "provider": "codex", "model": "model-alt-1", "effort": "medium", "capability_rank": 2, "cost_rank": 2}])
        demand = planning(complexity="medium", evidence=["parser.py:20 shared parser branches"])
        self.assertEqual(select_profile(value, "codex", demand)["id"], "a-medium")
        value["profiles"].reverse()
        self.assertEqual(select_profile(value, "codex", demand)["id"], "a-medium")

    def test_insufficient_or_missing_provider_catalog_never_falls_back(self):
        with self.assertRaisesRegex(LoopError, "meets the assignment demand"):
            select_profile(policy(), "claude", planning(risk="high", evidence=["rules:critical-interface"]))
        value = policy()
        value["profiles"] = value["profiles"][:3]
        with self.assertRaisesRegex(LoopError, "No explicit routing profile"):
            select_profile(value, "claude", planning())
        with self.assertRaisesRegex(LoopError, "known worker provider"):
            select_profile(value, "automatic", planning())

    def test_escalation_raises_actual_effort_or_model_without_changing_provider(self):
        second = escalate_profile(policy(), "codex", "small-low")
        self.assertEqual((second["model"], second["effort"]), ("model-small-1", "medium"))
        third = escalate_profile(policy(), "codex", second["id"])
        self.assertEqual((third["model"], third["effort"]), ("model-large-1", "high"))
        with self.assertRaisesRegex(LoopError, "No higher allowed"):
            escalate_profile(policy(), "codex", third["id"])
        with self.assertRaisesRegex(LoopError, "frozen provider policy"):
            escalate_profile(policy(), "claude", "small-low")

    def test_same_model_ranks_cannot_disguise_an_effort_downgrade(self):
        value = policy()
        value["profiles"][0]["effort"] = "high"
        value["profiles"][1]["effort"] = "low"
        with self.assertRaisesRegex(LoopError, "cannot lower recognized effort"):
            validate_policy(value)
        with self.assertRaisesRegex(LoopError, "cannot lower recognized effort"):
            escalate_profile(value, "codex", "small-low")
        value = policy()
        value["profiles"][2]["model"] = "model-small-1"
        middle = escalate_profile(value, "codex", "small-low")
        highest = escalate_profile(value, "codex", middle["id"])
        self.assertEqual((middle["effort"], highest["effort"]), ("medium", "high"))
        value["profiles"][2].update(model="model-large-1", effort="low")
        self.assertEqual(escalate_profile(value, "codex", middle["id"])["effort"], "low")
        value["profiles"][0]["effort"] = "high"
        value["profiles"][1]["capability_rank"] = 1
        validate_policy(value)  # Equal capability does not create an escalation.
        value["profiles"][1].update(effort="future-supported-effort", capability_rank=2)
        self.assertEqual(escalate_profile(value, "codex", "small-low")["effort"], "future-supported-effort")

    def test_fixed_benchmark_profile_cannot_fake_an_escalation(self):
        value = policy()
        value["profiles"] = value["profiles"][:1]
        self.assertEqual(select_profile(value, "codex", planning())["id"], "small-low")
        with self.assertRaisesRegex(LoopError, "No higher allowed"):
            escalate_profile(value, "codex", "small-low")
        duplicate = dict(value["profiles"][0], id="renamed", capability_rank=3)
        value["profiles"].append(duplicate)
        with self.assertRaisesRegex(LoopError, "cannot represent an escalation"):
            validate_policy(value)

    def test_invalid_profile_policy_is_rejected_before_selection(self):
        variants = []
        for field, bad in (("model", "auto"), ("model", "vendor-latest"), ("model", "--model=bad"),
                           ("effort", None), ("effort", "default"), ("effort", "ultra"),
                           ("effort", "ultracode"), ("model", "ultracode"), ("model", "OpusPlan"), ("capability_rank", True),
                           ("cost_rank", 0), ("provider", "unknown")):
            value = policy()
            value["profiles"][0][field] = bad
            variants.append(value)
        value = policy(); value["requirements"]["medium"] = 4; variants.append(value)
        value = policy(); value["requirements"]["low"] = True; variants.append(value)
        value = policy(); value["unfrozen"] = True; variants.append(value)
        value = policy(); value["profiles"][0]["price"] = 1; variants.append(value)
        value = policy(); value["profiles"][1]["id"] = value["profiles"][0]["id"]; variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(LoopError):
                validate_policy(value)

    def test_policy_seals_are_detached_order_invariant_and_change_with_routing(self):
        original = policy()
        normalized = validate_policy(original)
        seal = policy_seal(original)
        original["profiles"].reverse()
        self.assertEqual(policy_seal(original), seal)
        original["profiles"][0]["model"] = "changed-model-2"
        self.assertNotEqual(policy_seal(original), seal)
        self.assertEqual(policy_seal(normalized), seal)

    def test_environment_import_is_explicit_bounded_and_a_detached_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routing.json"
            path.write_text(json.dumps(policy()))
            imported = load_policy(environ={"LOOP_ROUTING_PROFILE": str(path)})
            explicit = load_policy(path, environ={"LOOP_ROUTING_PROFILE": "missing.json"})
            self.assertEqual(imported, explicit)
            path.write_text("not JSON")
            self.assertEqual(imported, validate_policy(policy()))
            with self.assertRaises(LoopError):
                load_policy(path)
            path.write_text(" " * 65537)
            with self.assertRaisesRegex(LoopError, "64 KiB"):
                load_policy(path)
        with self.assertRaisesRegex(LoopError, "Configure an explicit"):
            load_policy(environ={})

    def test_parent_goal_mapping_and_preflight_sentinel_are_not_interchangeable(self):
        self.assertEqual(validate_planning(planning(), self.goal, "working")["goal_criteria"], ["C1"])
        development = {"target": {"acceptance": [{"id": "A1", "scenario": "A parse completes."}]}}
        self.assertEqual(validate_planning(planning(goal_criteria=["A1"]), development, "running")["goal_criteria"], ["A1"])
        with self.assertRaisesRegex(LoopError, "unknown parent"):
            validate_planning(planning(), development, "running")
        preflight = planning(goal_criteria=["goal_setting"])
        validate_planning(preflight, {}, "draft")
        validate_planning(preflight, {}, "goal_setting")
        with self.assertRaisesRegex(LoopError, "only for preflight"):
            validate_planning(preflight, self.goal, "working")
        with self.assertRaisesRegex(LoopError, "cannot mix"):
            validate_planning(planning(goal_criteria=["goal_setting", "C1"]), self.goal, "draft")

    def test_planning_rejects_unbounded_or_vague_control_fields(self):
        variants = [planning(rationale="TODO"), planning(verification=""), planning(evidence=["x"] * 6),
                    planning(evidence=["same", "same"]), planning(goal_criteria=["C1", "C1"]),
                    planning(risk="automatic"), planning(evidence=["x\ny"])]
        value = planning(); value["profile"] = "large-high"; variants.append(value)
        for value in variants:
            with self.subTest(value=value), self.assertRaises(LoopError):
                validate_planning(value, self.goal, "working")

    def test_team_rejects_duplicate_contributions_and_overlapping_ownership(self):
        first = assignment("parser-owner", ["parser.py"])
        second = assignment("docs-owner", ["README.md"], kind="writing",
                            planning=planning(goal_criteria=["C2"]),
                            deliverables=[{"id": "docs", "description": "The public interface has current examples."}])
        validate_splits([first, second], self.goal, "working")
        duplicate = copy.deepcopy(first)
        duplicate.update(id="renamed-owner", instruction="Different wording about the same parser work.")
        duplicate["write_paths"] = first["write_paths"] = []
        duplicate["deliverables"][0]["description"] = "  THE parser returns the accepted values  "
        duplicate["deliverables"][0]["id"] = "renamed-output"
        with self.assertRaisesRegex(LoopError, "Duplicate assignment contribution"):
            validate_splits([first, duplicate], self.goal, "working")
        first["write_paths"] = ["parser.py"]
        second["write_paths"] = ["parser.py"]
        second["depends_on"] = [first["id"]]
        with self.assertRaisesRegex(LoopError, "write ownership overlaps"):
            validate_splits([first, second], self.goal, "working")

    def test_team_needs_real_boundaries_and_single_work_uses_delegate(self):
        first = assignment("parser", ["parser.py"])
        with self.assertRaisesRegex(LoopError, "use delegate"):
            validate_splits([first], self.goal, "working")
        other = assignment("docs", ["README.md"], deliverables=[])
        with self.assertRaisesRegex(LoopError, "independently verifiable"):
            validate_splits([first, other], self.goal, "working")
        other = assignment("docs", ["README.md"], planning=planning(goal_criteria=["unknown"]))
        with self.assertRaisesRegex(LoopError, "unknown parent"):
            validate_splits([first, other], self.goal, "working")


if __name__ == "__main__":
    unittest.main()
