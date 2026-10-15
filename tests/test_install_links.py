from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import install as installer
from loop_work.storage import LoopError, rule_manifest


SOURCE = Path(__file__).resolve().parents[1]


class LinkInstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="loop links ")
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "home with spaces"
        self.home.mkdir()
        self.project = self.base / "project with spaces"
        self.project.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, entrypoint, *args, cwd=None):
        return subprocess.run([sys.executable, str(entrypoint), "--knowledge-home", str(self.home / "kb"),
                               *args], cwd=cwd or self.project, capture_output=True, text=True)

    def test_user_install_is_idempotent_discovers_both_adapters_and_preserves_home(self):
        for name in ("AGENTS.md", "CLAUDE.md", ".claude/settings.json", ".codex/config.toml"):
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("user rules and settings\n")
        kb = self.home / ".local/share/loop-work/knowledge.sqlite3"
        kb.parent.mkdir(parents=True)
        kb.write_bytes(b"existing knowledge")
        managed = installer.install_links(self.home)
        first = (managed / "manifest.json").read_bytes()
        wrapper_time = (managed / "loop.py").stat().st_mtime_ns
        installer.install_links(self.home)
        self.assertEqual(first, (managed / "manifest.json").read_bytes())
        self.assertEqual(wrapper_time, (managed / "loop.py").stat().st_mtime_ns)
        for provider, discovery in (("codex", ".agents"), ("claude", ".claude")):
            skill = self.home / discovery / "skills/loop-work"
            self.assertTrue(skill.is_symlink())
            self.assertEqual(skill.resolve(), managed / "adapters" / provider)
            self.assertTrue((skill / "loop.py").is_symlink())
            self.assertEqual((skill / "rules").resolve(), SOURCE / "rules")
        self.assertIn("disable-model-invocation: true", (managed / "adapters/claude/SKILL.md").read_text())
        self.assertIn("allow_implicit_invocation: false", (managed / "adapters/codex/agents/openai.yaml").read_text())
        installer.uninstall_links(self.home)
        installer.uninstall_links(self.home)
        for name in ("AGENTS.md", "CLAUDE.md", ".claude/settings.json", ".codex/config.toml"):
            self.assertEqual((self.home / name).read_text(), "user rules and settings\n")
        self.assertEqual(kb.read_bytes(), b"existing knowledge")
        self.assertFalse((self.home / ".agents/skills/loop-work").is_symlink())

    def test_wrapper_retains_install_binding_after_cwd_changes_and_is_removed(self):
        managed = installer.install_links(self.home)
        adapter = self.home / ".agents/skills/loop-work/loop.py"
        on = self.run_cli(adapter, "harness", "on", "--provider", "codex")
        self.assertEqual(on.returncode, 0, on.stderr)
        activation = json.loads(on.stdout)
        self.assertEqual(activation["entrypoint"], str(managed / "loop.py"))
        elsewhere = self.base / "other directory"
        elsewhere.mkdir()
        context = self.run_cli(activation["entrypoint"], "orchestration", "context", activation["session_id"], cwd=elsewhere)
        self.assertEqual(context.returncode, 0, context.stderr)
        self.assertEqual(json.loads(context.stdout)["entrypoint"], activation["entrypoint"])
        copied_wrapper = self.base / "kept-wrapper.py"
        copied_wrapper.write_bytes((managed / "loop.py").read_bytes())
        installer.uninstall_links(self.home)
        stopped = self.run_cli(activation["entrypoint"], "harness", "status", activation["session_id"])
        self.assertNotEqual(stopped.returncode, 0)
        guarded = self.run_cli(copied_wrapper, "harness", "on", "--provider", "codex")
        self.assertEqual(guarded.returncode, 2)
        self.assertIn("installation is unavailable", guarded.stderr)
        self.assertTrue((self.home / "kb/knowledge.sqlite3").is_file())

    def test_project_install_rules_are_inside_and_uninstall_preserves_records_and_edits(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        original = "Project instructions without terminal newline"
        (self.project / "AGENTS.md").write_text(original)
        (self.project / ".gitignore").write_text("my-ignore\n")
        managed = installer.install_links(target=self.project)
        self.assertFalse((self.project / ".loop/rules").exists())
        self.assertEqual((self.project / ".loop/loop.py").resolve(), managed / "loop.py")
        rules = rule_manifest(self.project, [])
        self.assertIn(".agents/skills/loop-work/SKILL.md", rules)
        for name in (".loop/goals/example/goal.json", ".loop/state/example/state.json"):
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("retained record\n")
        (self.project / "AGENTS.md").write_text((self.project / "AGENTS.md").read_text() + "\nLater user instructions\n")
        (managed / "user-note.txt").write_text("keep me")
        installer.uninstall_links(target=self.project)
        installer.uninstall_links(target=self.project)
        self.assertEqual((self.project / "AGENTS.md").read_text(), original + "\nLater user instructions\n")
        self.assertEqual((self.project / ".gitignore").read_text(), "my-ignore\n/.loop/state/\n")
        self.assertEqual((managed / "user-note.txt").read_text(), "keep me")
        for name in (".loop/goals/example/goal.json", ".loop/state/example/state.json"):
            self.assertEqual((self.project / name).read_text(), "retained record\n")

    def test_every_install_conflict_is_preflighted_including_dangling_links(self):
        blocked = self.home / ".claude/skills/loop-work"
        blocked.parent.mkdir(parents=True)
        blocked.symlink_to(self.base / "missing-owner")
        with self.assertRaisesRegex(LoopError, "Unowned"):
            installer.install_links(self.home)
        self.assertTrue(blocked.is_symlink())
        self.assertFalse((self.home / ".agents").exists())
        self.assertFalse((self.home / ".local/share/loop-work/install").exists())
        blocked.unlink()
        managed = installer.install_links(self.home)
        (managed / "adapters/claude/SKILL.md").write_text("my changed skill")
        wrapper = (managed / "loop.py").read_bytes()
        for action in (installer.install_links, installer.uninstall_links):
            with self.assertRaisesRegex(LoopError, "Local changes"):
                action(self.home)
            self.assertEqual((managed / "loop.py").read_bytes(), wrapper)
            self.assertTrue((self.home / ".agents/skills/loop-work").is_symlink())

    def test_modified_project_block_prevents_partial_uninstall(self):
        managed = installer.install_links(target=self.project)
        path = self.project / "CLAUDE.md"
        path.write_text(path.read_text().replace("only on explicit", "also without explicit"))
        with self.assertRaisesRegex(LoopError, "Local edits"):
            installer.uninstall_links(target=self.project)
        self.assertTrue((managed / "loop.py").is_file())
        self.assertIn(installer.START, (self.project / "AGENTS.md").read_text())
        self.assertTrue((self.project / ".agents/skills/loop-work").is_symlink())

    def test_project_instruction_bytes_modes_and_ignore_block_are_preserved(self):
        original = {"AGENTS.md": b"Original instructions\r\nSecond line\r\n", "CLAUDE.md": b"Claude instructions\r\n",
                    ".gitignore": b"my-ignore/\r\n"}
        for name, content in original.items():
            path = self.project / name
            path.write_bytes(content)
            path.chmod(0o640)
        installer.install_links(target=self.project)
        for name, content in original.items():
            self.assertTrue((self.project / name).read_bytes().startswith(content))
            self.assertEqual((self.project / name).stat().st_mode & 0o777, 0o640)
        self.assertIn(installer.IGNORE_START, (self.project / ".gitignore").read_text())
        installer.uninstall_links(target=self.project)
        for name, content in original.items():
            self.assertEqual((self.project / name).read_bytes(), content)
            self.assertEqual((self.project / name).stat().st_mode & 0o777, 0o640)

    def test_modified_gitignore_or_manifest_prefix_prevents_partial_uninstall(self):
        managed = installer.install_links(target=self.project)
        ignore = self.project / ".gitignore"
        content = ignore.read_text()
        ignore.write_text(content.replace("/.loop/install/", "/.loop/custom/"))
        with self.assertRaisesRegex(LoopError, "Local edits"):
            installer.uninstall_links(target=self.project)
        self.assertTrue((managed / "loop.py").is_file())
        ignore.write_text(content)
        manifest = managed / "manifest.json"
        value = json.loads(manifest.read_text())
        value["entry_blocks"]["AGENTS.md"]["prefix"] = "user instructions"
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(LoopError, "Invalid"):
            installer.uninstall_links(target=self.project)
        self.assertTrue((managed / "loop.py").is_file())

    def test_install_operations_are_serialized_without_new_lock_files(self):
        with installer._installation_lock(self.home):
            with self.assertRaisesRegex(LoopError, "Another"):
                installer.install_links(self.home)
            with self.assertRaisesRegex(LoopError, "Another"):
                installer.uninstall_links(self.home)
        self.assertEqual(list(self.home.iterdir()), [])

    def test_noncanonical_manifest_directories_never_authorize_scope_removal(self):
        managed = installer.install_links(self.home)
        manifest = managed / "manifest.json"
        original = json.loads(manifest.read_text())
        for directory in ("", "./", ".", "../", "/", ".local//share", ".local/share/"):
            with self.subTest(directory=directory):
                modified = dict(original, directories=[directory])
                manifest.write_text(json.dumps(modified))
                for operation in (installer.install_links, installer.uninstall_links):
                    with self.assertRaisesRegex(LoopError, "Invalid"):
                        operation(self.home)
                    self.assertTrue(self.home.is_dir())
                    self.assertTrue((managed / "loop.py").is_file())
                    self.assertTrue((self.home / ".agents/skills/loop-work").is_symlink())
                    self.assertTrue((self.home / ".claude/skills/loop-work").is_symlink())
        manifest.write_text(json.dumps(original))
        installer.uninstall_links(self.home)
        self.assertTrue(self.home.is_dir())

    def test_source_updates_are_live_and_source_move_can_be_relinked(self):
        source = self.base / "source original"
        source.mkdir()
        for folder in ("loop_work", "rules", "skills"):
            shutil.copytree(SOURCE / folder, source / folder, ignore=shutil.ignore_patterns("__pycache__"))
        with patch.object(installer, "SOURCE", source):
            managed = installer.install_links(self.home)
        (source / "rules/work.md").write_text("Updated work rules\n")
        self.assertEqual((managed / "adapters/codex/rules/work.md").read_text(), "Updated work rules\n")
        (source / "skills/loop-work/SKILL.md").write_text((source / "skills/loop-work/SKILL.md").read_text() + "\nUpdated canonical skill\n")
        moved = self.base / "source moved"
        source.rename(moved)
        failed = self.run_cli(managed / "loop.py", "harness", "on", "--provider", "codex")
        self.assertEqual(failed.returncode, 2)
        self.assertIn("rerun make install", failed.stderr)
        with patch.object(installer, "SOURCE", moved):
            installer.install_links(self.home)
        self.assertEqual((managed / "runtime").resolve(), moved)
        for provider in ("claude", "codex"):
            self.assertIn("Updated canonical skill", (managed / "adapters" / provider / "SKILL.md").read_text())
        recovered = self.run_cli(managed / "loop.py", "harness", "on", "--provider", "codex")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        installer.uninstall_links(self.home)

    def test_uninstall_accepts_missing_owned_links_but_rejects_replaced_links(self):
        managed = installer.install_links(self.home)
        missing = self.home / ".agents/skills/loop-work"
        missing.unlink()
        replaced = self.home / ".claude/skills/loop-work"
        replaced.unlink()
        replaced.symlink_to(self.base / "elsewhere")
        with self.assertRaisesRegex(LoopError, "Local changes"):
            installer.uninstall_links(self.home)
        self.assertTrue((managed / "loop.py").is_file())
        replaced.unlink()
        installer.uninstall_links(self.home)
        self.assertFalse((managed / "loop.py").exists())

    def test_legacy_copy_api_and_skill_helper_remain_self_contained(self):
        installer.install(self.project)
        for discovery, provider in ((".agents", "codex"), (".claude", "claude")):
            unrelated = self.project / discovery / "scripts/loop.py"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("raise SystemExit('unrelated provider script must not execute')\n")
            result = self.run_cli(self.project / discovery / "skills/loop-work/loop.py", "harness", "on", "--provider", provider)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["entrypoint"], str(self.project / ".loop/loop.py"))
        with self.assertRaisesRegex(LoopError, "copied legacy installation"):
            installer.uninstall_links(target=self.project)
        with self.assertRaisesRegex(LoopError, "Unowned"):
            installer.install_links(target=self.project)
        self.assertTrue((self.project / ".loop/loop.py").is_file())

    @unittest.skipUnless(shutil.which("make"), "make unavailable")
    def test_make_passes_spaces_and_shell_syntax_as_literal_target_data(self):
        literal = self.base / "literal $(touch OWNED) `touch OTHER` ; ' \" target"
        literal.mkdir()
        for action in ("install", "install", "uninstall", "uninstall"):
            result = subprocess.run(["make", "TARGET=" + str(literal), action], cwd=SOURCE,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((SOURCE / "OWNED").exists())
        self.assertFalse((SOURCE / "OTHER").exists())
        self.assertFalse((literal / ".agents/skills/loop-work").is_symlink())


if __name__ == "__main__":
    unittest.main()
