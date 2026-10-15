from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop_work.contract import AREAS
from loop_work.engine import Engine
from loop_work.storage import file_hash, head, read_json, write_json


def fake_routing_policy():
    """Explicit fake model choices; these IDs make no claim about account access."""
    return {"version": 1, "requirements": {"low": 1, "medium": 2, "high": 3},
            "profiles": [{"id": provider + "-" + effort, "provider": provider,
                          "model": "fake-" + provider + ("-large" if rank == 3 else "-small"),
                          "effort": effort, "capability_rank": rank, "cost_rank": rank}
                         for provider in ("claude", "codex")
                         for rank, effort in enumerate(("low", "medium", "high"), 1)]}


def use_fake_routing(test, directory):
    path = Path(directory) / "routing-test.json"
    write_json(path, fake_routing_policy())
    environment = patch.dict(os.environ, {"LOOP_ROUTING_PROFILE": str(path)})
    environment.start()
    test.addCleanup(environment.stop)
    return path


def fixture_assessment(knowledge, result):
    """Act as the main assessor only for deterministic legacy-test fixtures.

    Runtime code never accepts a candidate automatically. These tests already
    specify the expected fake output; retain the artifact hash as evidence of
    the concrete candidate being examined, including valid peer questions.
    """
    brief = result["assignment"]
    record = knowledge.get("delegation", brief["id"])
    value = read_json(Path(brief["result"]))
    if record.get("validation_errors"):
        raise AssertionError("Cannot accept an invalid fixture candidate: " + str(record["validation_errors"]))
    if value["status"] not in ("done", "waiting"):
        raise AssertionError("Fixture is not an acceptable result or bounded peer question")
    evidence = [{"ref": str(Path(brief["result"]).resolve()), "sha256": file_hash(Path(brief["result"]))}]
    source = next((Path(record["root"]) / name for name in record["request"]["read_paths"]
                   if (Path(record["root"]) / name).is_file()), None)
    if source is None:
        raise AssertionError("A fixture acceptance needs source evidence outside the worker's own result")
    source.read_bytes()
    evidence.append({"ref": str(source.resolve()), "sha256": file_hash(source)})
    criteria = [("assignment", item["id"]) for item in record["request"]["criteria"]]
    criteria += [("goal", key) for key in record["request"]["planning"]["goal_criteria"]]
    return {"assignment_id": brief["assignment_id"], "candidate_id": brief["candidate_id"],
            "result_digest": brief["result_digest"], "verdict": "accept", "reason": "accepted",
            "summary": "The deterministic fixture satisfies this scoped contribution.",
            "checks": [{"scope": scope, "criterion_id": key, "satisfied": True, "evidence": evidence}
                       for scope, key in criteria]}


def accept_fixture(knowledge, assess, result):
    if result.get("assignment", {}).get("status") != "candidate":
        return result
    return assess(fixture_assessment(knowledge, result))


def complete_fixture_team(team, plan, recover=None):
    """Drive explicitly accepted fake turns without replacing production routing."""
    result = team.run(plan, recover)
    for _ in range(plan["max_turns"] + 1):
        if result["status"] != "awaiting_assessment":
            return result
        ledger = team.load(plan["id"])
        record = team.assignment(ledger["inflight"]["member"])
        if record.get("validation_errors"):
            return result
        team.worker.assess(fixture_assessment(team.knowledge, team.worker.response(record)))
        result = team.run(plan)
    raise AssertionError("Fixture assessor exceeded the immutable team turn budget")


FAKE_CLI = '''#!/usr/bin/env python3
import json, os, pathlib, sys
if "--version" in sys.argv:
    print(pathlib.Path(sys.argv[0]).name + " fake-test-cli")
    raise SystemExit(0)
if os.getenv("LOOP_TEST_FAIL"):
    print("test authentication failure", file=sys.stderr)
    raise SystemExit(7)
prompt = sys.stdin.read()
data = json.loads(prompt.split("REVIEW DATA:\\n", 1)[1])
if os.getenv("LOOP_TEST_MUTATE"):
    pathlib.Path("value.py").write_text("VALUE = 9\\n")
verdict = os.getenv("LOOP_TEST_VERDICT", "pass")
value = {
    "verdict": verdict, "summary": "Fake CLI review for deterministic tests only",
    "findings": [] if verdict == "pass" else [{"severity":"major", "target":"value.py",
        "evidence":"Behavior is incorrect", "remedy":"Repair the behavior"}],
    "questions": ["Unresolved product choice"] if os.getenv("LOOP_TEST_QUESTION") else [],
    "coverage": [{"acceptance_id": item["id"], "satisfied": verdict == "pass",
                  "evidence":"tests/check.py has an actual assertion"} for item in data["criteria_to_review"]]
}
if os.getenv("LOOP_TEST_MISSING_COVERAGE"):
    value["coverage"] = []
if os.getenv("LOOP_TEST_BAD_JSON"):
    output = "not-json"
else:
    output = json.dumps(value)
if "--output-last-message" in sys.argv:
    pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1]).write_text(output)
else:
    if os.getenv("LOOP_TEST_BAD_JSON"):
        print(output)
    else:
        print(json.dumps({"is_error": False, "structured_output": value,
                         "permission_denials": ["test denial"] if os.getenv("LOOP_TEST_DENIED") else []}))
'''


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        use_fake_routing(self, Path(self.temp.name))
        for name in ("codex", "claude"):
            path = self.bin / name
            path.write_text(FAKE_CLI)
            path.chmod(0o755)
        self.env = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                                          "LOOP_KB_HOME": str(Path(self.temp.name) / "knowledge")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.git("init", "-q")
        self.git("config", "user.email", "tests@example.invalid")
        self.git("config", "user.name", "Loop Tests")
        (self.root / ".gitignore").write_text(".loop/state/\n__pycache__/\n")
        (self.root / "AGENTS.md").write_text("Keep meaningful tests and target behavior.\n")
        (self.root / "value.py").write_text("VALUE = 1\n")
        (self.root / "check.py").write_text("from value import VALUE\nassert VALUE == 1, VALUE\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.engine = Engine(self.root, "demo")
        self.engine.initialize("Complete a real behavior", "claude")
        self.fill_contract()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout

    def fill_contract(self):
        path = self.engine.goal_dir / "contract.json"
        contract = read_json(path)
        contract["target"].update(
            outcome="The consumer observes the required value",
            users=["local developer"], in_scope=["value behavior and its consumer"],
            out_of_scope=["network and production delivery"],
            requirements=[{"id": "R1", "description": "Consumer gets the required value"}],
            acceptance=[{"id": "A1", "requirement_ids": ["R1"], "scenario": "Import and assert actual behavior",
                         "check_ids": ["behavior"]}])
        contract["decisions"] = [{"area": area, "answer": "Use the local Python behavior",
                                  "basis": "user", "rationale": "The test fixture defines this scope"} for area in AREAS]
        contract["checks"] = [{"id": "behavior", "argv": [sys.executable, "-B", "check.py"], "cwd": ".",
                               "timeout_seconds": 5, "kind": "acceptance"}]
        contract["milestones"] = [{"id": "M1", "title": "Complete behavior", "acceptance_ids": ["A1"]}]
        write_json(path, contract)
        write_json(self.engine.goal_dir / "discovery.json", {"baseline_head": head(self.root),
                   "findings": ["value.py is imported by check.py; AGENTS.md requires meaningful tests"],
                   "gaps": [{"requirement_id": "R1", "current_state": "value.py is present",
                             "work_needed": "Verify the actual consumer behavior"}]})

    def prepared(self):
        self.assertTrue(self.engine.plan_review()["passed"])
        self.engine.freeze()

    def running(self):
        self.prepared()
        self.engine.start()

    def state(self):
        return read_json(self.engine.state_path)
