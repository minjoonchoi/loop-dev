"""Name transition preserves owned installation boundaries and existing knowledge."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import install as installer
from loop_work.knowledge import KnowledgeBase
from loop_work.storage import LoopError, file_hash, write_json


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def legacy(self, project=False):
        """Recreate the previous owned layout, including the old Python wrapper."""
        scope = "project" if project else "user"
        base, files, links = installer._link_layout(self.root, scope, installer.SOURCE)
        rename = lambda text: text.replace("loop-work", "loop-dev").replace("loop_work", "loop_dev")
        files = {rename(name): rename(data.decode()).encode() for name, data in files.items()}
        links = {rename(name): (target if name.endswith("/runtime") else rename(target)) for name, target in links.items()}
        blocks = {}
        for name, data in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for name, target in links.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        if project:
            for name, body in (("AGENTS.md", installer.BLOCK), ("CLAUDE.md", installer.BLOCK), (".gitignore", installer.IGNORE_BLOCK)):
                body = rename(body)
                (self.root / name).write_text("user content\n\n" + body)
                blocks[name] = {"block": body.rstrip(), "prefix": "\n\n"}
        manifest = self.root / rename(base) / "manifest.json"
        write_json(manifest, {"version": 2, "kind": "links", "scope": scope, "root": str(self.root),
                            "source": str(installer.SOURCE), "files": {name: file_hash(self.root / name) for name in files},
                            "links": links, "entry_blocks": blocks, "directories": []})
        return manifest

    def test_user_transition_preserves_records_and_old_entrypoint_stops(self):
        manifest = self.legacy()
        kb = KnowledgeBase(self.root / ".local/share/loop-dev")
        before = kb.path.read_bytes()
        with self.assertRaisesRegex(LoopError, "uninstall-legacy"):
            installer.install_links(self.root)
        old = subprocess.run([sys.executable, str(manifest.parent / "loop.py"), "harness", "on", "--provider", "codex"],
                             capture_output=True, text=True)
        self.assertEqual(old.returncode, 2)
        self.assertIn("uninstall-legacy", old.stderr)
        installer.uninstall_links(self.root, legacy=True)
        installer.uninstall_links(self.root, legacy=True)
        installer.install_links(self.root)
        self.assertEqual(kb.path.read_bytes(), before)
        self.assertFalse((self.root / ".agents/skills/loop-dev").exists())
        self.assertTrue((self.root / ".agents/skills/loop-work").is_symlink())

    def test_project_transition_preserves_state_and_user_instructions(self):
        self.legacy(project=True)
        state = self.root / ".loop/state/goal/result.txt"
        state.parent.mkdir(parents=True)
        state.write_text("existing work")
        with self.assertRaisesRegex(LoopError, "uninstall-legacy"):
            installer.install_links(target=self.root)
        installer.uninstall_links(target=self.root, legacy=True)
        installer.install_links(target=self.root)
        self.assertEqual(state.read_text(), "existing work")
        self.assertTrue((self.root / "AGENTS.md").read_text().startswith("user content"))
        self.assertNotIn("loop-dev:start", (self.root / "AGENTS.md").read_text())
        self.assertIn("/.loop/state/", (self.root / ".gitignore").read_text())
        with self.assertRaisesRegex(LoopError, "Installation name differs"):
            installer.uninstall_links(target=self.root, legacy=True)

    def test_modified_legacy_resource_prevents_partial_removal(self):
        manifest = self.legacy()
        (manifest.parent / "loop.py").write_text("user edited")
        before = manifest.read_bytes()
        with self.assertRaisesRegex(LoopError, "Local changes"):
            installer.uninstall_links(self.root, legacy=True)
        self.assertEqual(manifest.read_bytes(), before)
        self.assertTrue((self.root / ".claude/skills/loop-dev").is_symlink())

    def test_knowledge_reuses_old_home_and_requires_choice_if_both_exist(self):
        legacy = KnowledgeBase(self.root / ".local/share/loop-dev")
        with patch("pathlib.Path.home", return_value=self.root), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(KnowledgeBase().home, legacy.home)
            new = KnowledgeBase(self.root / ".local/share/loop-work")
            with self.assertRaisesRegex(LoopError, "Both loop-work and loop-dev"):
                KnowledgeBase()
            with patch.dict(os.environ, {"LOOP_KB_HOME": str(legacy.home)}):
                self.assertEqual(KnowledgeBase().home, legacy.home)
            self.assertTrue(new.path.exists())

    def test_fresh_knowledge_home_uses_new_name(self):
        with patch("pathlib.Path.home", return_value=self.root), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(KnowledgeBase().home, self.root / ".local/share/loop-work")

    def test_benchmark_history_preserved_and_ambiguous_default_rejected(self):
        from loop_work.bench_evolution import location
        from loop_work.storage import digest
        candidate = self.root / "source"
        legacy = self.root / "loop-dev-benchmarks/history" / digest(str(candidate))[:16]
        legacy.mkdir(parents=True)
        self.assertEqual(location(candidate), legacy)
        current = self.root / "loop-work-benchmarks/history" / digest(str(candidate))[:16]
        current.mkdir(parents=True)
        with self.assertRaisesRegex(LoopError, "choose --history"):
            location(candidate)
        self.assertEqual(location(candidate, legacy), legacy)

    def test_old_benchmark_source_hash_is_preserved_exactly(self):
        from loop_work.benchmark import source_snapshot
        from loop_work.storage import digest
        contents = {"scripts/install.py": "# old installer", "scripts/loop.py": "# old CLI",
                    "rules/work.md": "old policy", "loop_dev/engine.py": "# old engine",
                    "skills/loop-dev/SKILL.md": "old skill"}
        for name, content in contents.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        snapshot = source_snapshot(self.root)
        self.assertEqual(snapshot["files"], contents)
        self.assertEqual(snapshot["hash"], digest(contents))

    def test_old_discovery_files_cannot_contaminate_plain_benchmark_fixture(self):
        import shutil
        from loop_work.benchmark import load_suite
        suite = self.root / "suite"
        shutil.copytree(installer.SOURCE / "benchmarks", suite)
        config = json.loads((suite / "suite.json").read_text())
        fixture = suite / config["cases"][0]["fixture"]
        for brand in ("loop-dev", "loop-work"):
            path = fixture / ".agents/skills" / brand / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("installed harness")
            with self.assertRaisesRegex(LoopError, "Fixtures must not contain"):
                load_suite(suite / "suite.json")
            path.unlink()
