from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from loop_dev.process import execute
from loop_dev.storage import LoopError, locked
from support import ProjectTest


SOURCE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("loop_install", SOURCE / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallationTests(ProjectTest):
    def test_install_is_idempotent_preserves_rules_and_runs_without_checkout(self):
        (self.root / "CLAUDE.md").write_text("Original Claude instructions\n")
        (self.root / ".gitignore").write_text("my-output/\n.loop/state/\n__pycache__/\n")
        installer.install(self.root)
        original = {name: (self.root / name).read_bytes() for name in ("AGENTS.md", "CLAUDE.md", ".gitignore")}
        installer.install(self.root)
        for name, content in original.items():
            self.assertEqual(content, (self.root / name).read_bytes())
        self.assertIn("Original Claude instructions", (self.root / "CLAUDE.md").read_text())
        self.assertEqual((self.root / "AGENTS.md").read_text().count(installer.START), 1)
        result = subprocess.run([sys.executable, str(self.root / ".loop/loop.py"), "init", "installed",
                                 "--title", "Build from conversation", "--implementer", "codex"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["next"]["action"], "clarify")
        self.assertTrue((self.root / ".loop/templates/contract.example.json").is_file())

    def test_local_engine_changes_and_modified_entry_blocks_are_not_overwritten(self):
        installer.install(self.root)
        engine_file = self.root / ".loop/loop_dev/engine.py"
        engine_file.write_text("# local customization\n")
        with self.assertRaisesRegex(LoopError, "Local changes"):
            installer.install(self.root)
        self.assertEqual(engine_file.read_text(), "# local customization\n")

    def test_active_goal_blocks_install(self):
        self.running()
        with self.assertRaisesRegex(LoopError, "active goal"):
            installer.install(self.root)

    def test_installer_rejects_symlink_escape(self):
        (self.root / "CLAUDE.md").symlink_to(Path(self.temp.name) / "outside.md")
        with self.assertRaisesRegex(LoopError, "escapes"):
            installer.install(self.root)
        self.assertFalse((Path(self.temp.name) / "outside.md").exists())


class ProcessTests(ProjectTest):
    def test_process_has_eof_instead_of_interactive_stdin(self):
        result = execute([sys.executable, "-c", "import sys; assert sys.stdin.read() == ''"],
                         self.root, self.engine.state_dir / "process", 2)
        self.assertEqual(result["exit_code"], 0)

    def test_timeout_terminates_the_process_group(self):
        started = time.monotonic()
        result = execute([sys.executable, "-c", "import time; time.sleep(30)"],
                         self.root, self.engine.state_dir / "timeout", 0.1)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertLess(time.monotonic() - started, 4)

    def test_arguments_are_not_shell_interpolated(self):
        literal = "$(touch should-not-exist) ; `touch also-not-created`"
        result = execute([sys.executable, "-c", "import sys; print(sys.argv[1])", literal],
                         self.root, self.engine.state_dir / "argv", 2)
        self.assertEqual(Path(result["stdout"]).read_text().strip(), literal)
        self.assertFalse((self.root / "should-not-exist").exists())

    def test_repo_lock_rejects_concurrent_command(self):
        with locked(self.root):
            with self.assertRaisesRegex(LoopError, "Another loop command"):
                with locked(self.root):
                    pass

    def test_missing_executable_is_a_failure_not_success(self):
        result = execute([str(self.root / "does-not-exist")], self.root,
                         self.engine.state_dir / "missing", 2)
        self.assertIsNone(result["exit_code"])
        self.assertIn("error", result)
