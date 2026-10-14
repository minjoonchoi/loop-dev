from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from loop_dev.context import inventory
from loop_dev.knowledge import KnowledgeBase
from loop_dev.storage import LoopError, digest, file_hash
from loop_dev.work import Work
from loop_dev.workflows import source_context


class WorkflowContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "product"
        self.root.mkdir()
        self.kb = KnowledgeBase(self.base / "knowledge")
        self.location = self.kb.attach(self.root)
        self.paths = {"docs/PRD.md": "Desired outcome: users can save notes.\n",
                      "docs/planning/notes.md": "Save flow and error states.\n",
                      "mockup/index.html": "<button>Save</button>\n",
                      "backend/openapi.yaml": "openapi: 3.1.0\n",
                      "frontend/Notes.tsx": "export const Notes = () => null;\n",
                      "backend/server.py": "def save(): pass\n",
                      "docs/architecture.md": "The frontend calls the notes service.\n",
                      "notes-from-meeting.md": "A source whose role requires inspection.\n"}
        for name, value in self.paths.items():
            self.write(name, value)

    def write(self, name, value, root=None):
        target = (root or self.root) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
        return target

    def observe(self, locations=None):
        return self.kb.observe(locations or [self.location])

    def remember(self, name, paths, **extra):
        entry = {"id": name, "scope": "project", "kind": "artifact", "status": "confirmed",
                 "summary": "The saved planning record describes the inspected contract.",
                 "evidence": [paths[0] + ":1"], "paths": paths, **extra}
        return self.kb.remember(entry, [self.location], digest(self.observe()))

    def test_goal_navigation_works_without_saved_knowledge_for_all_requested_work_shapes(self):
        cases = [
            ("요구사항 분석 및 PRD 작성", {"requirements"}),
            ("PRD 기반 상세 기획 문서 및 HTML 목업 개발", {"requirements", "planning", "mockup"}),
            ("기획 문서와 목업 기반 FE code 개발", {"planning", "mockup", "frontend"}),
            ("기획 문서와 목업 기반 BE code 개발", {"planning", "mockup", "backend"}),
            ("BE API 인터페이스 연동 FE code 개발", {"backend", "api", "frontend"}),
            ("BE 아키텍처 설계 및 기능 명세 문서 작성", {"backend", "architecture", "planning"}),
            ("전체 시스템 아키텍처 문서 작성 및 최신화", {"architecture"}),
        ]
        with patch.dict("os.environ", {"LOOP_ROUTING_PROFILE": ""}):
            for title, roles in cases:
                with self.subTest(title=title):
                    result = Work(self.kb).open("planning", title, "codex", [self.root])
                    context = result["knowledge"]
                    nav = context["sources"]
                    self.assertEqual(context["reusable"], [])
                    self.assertTrue(roles <= set(nav["goal_roles"]))
                    self.assertTrue(roles <= {role for row in nav["sources"] for role in row["roles"]})
                    self.assertEqual(nav["sources"][0]["relevance"], "goal_role_hint")
                    self.assertEqual(nav["revision"], context["revision"])
                    self.assertEqual(nav["basis"], "path_hints")
                    for row in nav["sources"]:
                        self.assertEqual(row["fingerprint"], file_hash(Path(row["ref"])))
                    # Navigation gives paths, not a second copy of document prose.
                    self.assertNotIn("Desired outcome: users can save notes", json.dumps(nav))

    def test_goal_field_ranking_is_bounded_and_ignores_provider_or_instruction_text(self):
        for number in range(35):
            self.write(f"docs/prd-{number:02}.md", "An earlier requirement.\n")
        goal = {"title": "Update the system", "goal": {"deliverable": "An API integration"},
                "execution": {"model": "architecture"}, "instructions": "architecture"}
        nav = source_context(self.observe(), goal)
        self.assertEqual(nav["goal_roles"], ["api"])
        self.assertEqual(nav["sources"][0]["path"], "backend/openapi.yaml")
        self.assertEqual(len(nav["sources"]), 24)
        self.assertEqual(nav["count"], 43)
        self.assertTrue(nav["truncated"])
        reversed_observation = self.observe()
        original = reversed_observation[self.location["id"]]["files"]
        reversed_observation[self.location["id"]]["files"] = dict(reversed(list(original.items())))
        self.assertEqual(nav, source_context(reversed_observation, goal))
        with self.assertRaises(LoopError):
            source_context(self.observe(), goal, limit=25)

    def test_unknown_names_stay_discoverable_and_preflight_needs_no_goal(self):
        nav = source_context(self.observe())
        unknown = next(row for row in nav["sources"] if row["path"] == "notes-from-meeting.md")
        self.assertEqual(unknown["roles"], ["source"])
        self.assertEqual(nav["goal_roles"], [])
        self.assertFalse(nav["truncated"])
        self.assertIn("No match does not prove absence", " ".join(nav["instructions"]))
        self.write("requirements.txt", "a-dependency==1\n")
        nav = source_context(self.observe())
        requirements = next(row for row in nav["sources"] if row["path"] == "requirements.txt")
        self.assertEqual(requirements["roles"], ["source"])

    def test_changed_and_deleted_inputs_refresh_navigation_and_invalidate_consumers(self):
        saved = self.remember("frontend-inputs", ["docs/PRD.md", "backend/openapi.yaml", "mockup/"])
        before = self.kb.context([self.location])
        self.assertEqual([row["id"] for row in before["reusable"]], [saved["id"]])
        before_hash = next(row["fingerprint"] for row in before["sources"]["sources"]
                           if row["path"] == "docs/PRD.md")
        self.write("docs/PRD.md", "User feedback: save offline first.\n")
        (self.root / "backend/openapi.yaml").unlink()
        self.write("mockup/error.html", "<p>Offline</p>\n")
        after = self.kb.context([self.location])
        paths = {row["path"]: row for row in after["sources"]["sources"]}
        self.assertNotEqual(paths["docs/PRD.md"]["fingerprint"], before_hash)
        self.assertIn("mockup/error.html", paths)
        self.assertNotIn("backend/openapi.yaml", paths)
        self.assertEqual(after["reusable"], [])
        self.assertEqual(set(after["stale"][0]["stale_reasons"]),
                         {"changed: docs/PRD.md", "changed: backend/openapi.yaml", "changed: mockup/"})
        self.assertEqual(self.kb.get("topic", saved["id"])["recorded_at"], saved["recorded_at"])
        self.assertEqual(self.kb.history("topic", saved["id"]), [])

    def test_only_explicit_task_roots_are_discovered_and_preserve_project_identity(self):
        backend = self.base / "service"
        backend.mkdir()
        self.write("api/openapi.yaml", "openapi: 3.1.0\n", root=backend)
        backend_location = self.kb.attach(backend)
        self.remember("product-requirements", ["docs/PRD.md"])
        isolated = self.kb.context([backend_location])
        self.assertEqual([row["path"] for row in isolated["sources"]["sources"]], ["api/openapi.yaml"])
        self.assertEqual(isolated["reusable"], [])
        self.assertEqual(isolated["counts"]["other_contexts"], 1)
        together = self.kb.context([self.location, backend_location])
        self.assertEqual({row["checkout_id"] for row in together["sources"]["sources"]},
                         {self.location["id"], backend_location["id"]})
        self.assertEqual({row["project_id"] for row in together["sources"]["sources"]},
                         {self.location["project_id"], backend_location["project_id"]})
        moved = self.base / "renamed-service"
        backend.rename(moved)
        moved_location = self.kb.attach(moved)
        moved_nav = self.kb.context([moved_location])["sources"]
        self.assertEqual(moved_location["id"], backend_location["id"])
        self.assertEqual(moved_nav["sources"][0]["ref"], str(moved / "api/openapi.yaml"))

    def test_source_navigation_does_not_read_contents_or_follow_sensitive_paths_and_links(self):
        for name in (".env", ".claude/settings.json", ".loop/state/task/result.md", "docs/secrets.md",
                     "keys/private.pem", "credentials/openapi.json", "AGENTS.md"):
            self.write(name, "DO-NOT-INJECT\n")
        outside = self.write("outside-prd.md", "external text", root=self.base)
        (self.root / "docs/linked-prd.md").symlink_to(outside)
        outside_dir = self.base / "outside-docs"
        outside_dir.mkdir()
        self.write("PRD.md", "external document", root=outside_dir)
        (self.root / "linked-docs").symlink_to(outside_dir, target_is_directory=True)
        observed = self.observe()
        observed[self.location["id"]]["files"]["linked-docs/PRD.md"] = "fake-observed-hash"
        observed[self.location["id"]]["files"]["../outside-prd.md"] = "fake-observed-hash"
        with patch.object(Path, "read_text", side_effect=AssertionError("navigation read text")), \
                patch.object(Path, "read_bytes", side_effect=AssertionError("navigation read bytes")):
            nav = source_context(observed)
        self.assertEqual({row["path"] for row in nav["sources"]}, set(self.paths))
        self.assertNotIn("DO-NOT-INJECT", json.dumps(nav))

    def test_development_inventory_exposes_observed_source_navigation(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        inv = inventory(self.root, [])
        self.assertEqual(inv["sources"]["count"], len(self.paths))
        paths = {row["path"]: row for row in inv["sources"]["sources"]}
        self.assertEqual(paths["docs/PRD.md"]["roles"], ["requirements"])
        self.assertEqual(paths["backend/openapi.yaml"]["fingerprint"], file_hash(self.root / "backend/openapi.yaml"))
        self.assertIn("notes-from-meeting.md", paths)


if __name__ == "__main__":
    unittest.main()
