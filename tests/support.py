from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop_dev.contract import AREAS
from loop_dev.engine import Engine
from loop_dev.storage import head, read_json, write_json


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
        for name in ("codex", "claude"):
            path = self.bin / name
            path.write_text(FAKE_CLI)
            path.chmod(0o755)
        self.env = patch.dict(os.environ, {"PATH": str(self.bin) + os.pathsep + os.environ["PATH"]})
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
