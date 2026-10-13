from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop_dev.engine import Engine
from loop_dev.harness import Harness
from loop_dev.knowledge import KnowledgeBase
from loop_dev.storage import HARNESS_SKILL_FILES, LoopError, read_json, rule_manifest
from loop_dev.work import Work
from scripts import install as installer
from support import ProjectTest


SOURCE = Path(__file__).resolve().parents[1]


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'documents'
        self.root.mkdir()
        self.kb = KnowledgeBase(self.base / 'knowledge')
        self.harness = Harness(self.kb)
        self.work = Work(self.kb)

    def test_new_and_legacy_sessions_are_off_and_do_not_adopt_an_active_session(self):
        first = self.harness.on('codex')
        legacy = self.kb.session('codex')
        self.assertNotIn('harness', legacy)
        self.assertFalse(self.harness.status(legacy['id'])['enabled'])
        task = self.work.open('explanation', 'Explain source', 'codex', [self.root])
        self.assertFalse(task['knowledge']['harness']['enabled'])
        second = self.harness.on('codex')
        self.assertEqual(len({first['session_id'], second['session_id'], task['task']['session_id'], legacy['id']}), 4)
        self.assertTrue(self.harness.status(first['session_id'])['enabled'])

    def test_completion_new_task_kinds_and_directory_changes_keep_activation(self):
        enabled = self.harness.on('codex')
        session_id = enabled['session_id']
        for index, kind in enumerate(('explanation', 'document', 'research', 'review', 'planning')):
            root = self.base / ('project-' + str(index))
            root.mkdir()
            opened = self.work.open(kind, 'Describe the expected output', 'codex', [root], session_id)
            task_id = opened['task']['id']
            self.work.define(task_id, {
                'outcome': 'Describe the requested result.', 'deliverable': 'An inline answer.',
                'criteria': [{'id': 'C1', 'description': 'State the result in a concrete sentence.'}],
                'decisions': [], 'open_questions': []})
            self.work.start(task_id)
            result = self.work.finish(task_id, {
                'summary': 'The requested result is described.', 'outputs': ['conversation:answer'],
                'checks': [{'criterion_id': 'C1', 'satisfied': True, 'evidence': 'The answer states the result.'}]})
            self.assertEqual(result['task']['phase'], 'completed')
            self.assertEqual(result['knowledge']['harness'], enabled)
            self.assertIn('all inputs in this conversation', Path(result['knowledge']['document']).read_text())
            # A fresh runtime reading persisted state is sufficient; no in-memory singleton.
            self.assertEqual(Harness(KnowledgeBase(self.kb.home)).status(session_id), enabled)
        self.assertEqual(len(self.kb.list('session')), 1)

    def test_toggle_is_idempotent_and_preserves_work_knowledge_and_budgets(self):
        enabled = self.harness.on('claude')
        session_id = enabled['session_id']
        task = self.work.open('planning', 'Plan a change', 'claude', [self.root], session_id)['task']
        self.work.stop(task['id'], 'User paused this task')
        self.kb.put('worker-budget', {'id': task['id'], 'calls': 3})
        locations = [self.kb.attach(self.root)]
        topic = {'id': 'local-choice', 'kind': 'decision', 'scope': 'session', 'status': 'confirmed',
                 'summary': 'Describe the plan in Korean.', 'paths': [], 'evidence': ['conversation:user'],
                 'basis': 'user', 'applies_to': 'This conversation'}
        self.kb.remember(topic, locations, self.kb.context(locations)['revision'], session_id, task['id'])
        knowledge_before = self.kb.get('topic', topic['id'])
        task_before = self.kb.get('task', task['id'])
        history = self.kb.history('session', session_id)
        self.assertEqual(self.harness.on('claude', session_id), enabled)
        self.assertEqual(self.kb.history('session', session_id), history)
        self.assertTrue(self.harness.status(session_id)['enabled'])
        off = self.harness.off(session_id)
        self.assertFalse(off['enabled'])
        history = self.kb.history('session', session_id)
        self.assertEqual(self.harness.off(session_id), off)
        self.assertEqual(self.kb.history('session', session_id), history)
        self.assertFalse(self.work.context(task['id'])['knowledge']['harness']['enabled'])
        self.assertTrue(self.harness.on('claude', session_id)['enabled'])
        self.assertEqual(self.kb.get('task', task['id']), task_before)
        self.assertEqual(self.kb.get('worker-budget', task['id'])['calls'], 3)
        self.assertEqual(self.kb.get('topic', topic['id']), knowledge_before)

    def test_unknown_invalid_and_other_provider_bindings_do_not_create_sessions(self):
        enabled = self.harness.on('claude')
        before = self.kb.list('session')
        for operation in (lambda: self.harness.status('unknown'), lambda: self.harness.off('unknown'),
                          lambda: self.harness.on('codex', 'unknown'),
                          lambda: self.harness.on('codex', enabled['session_id']),
                          lambda: self.harness.status('../invalid')):
            with self.assertRaises(LoopError):
                operation()
        self.assertEqual(self.kb.list('session'), before)

    def test_source_and_installed_cli_keep_binding_outside_git_and_installation(self):
        installer.install(self.root)
        other = self.base / 'other'
        other.mkdir()
        for entrypoint in (SOURCE / 'scripts/loop.py', self.root / '.loop/loop.py'):
            def run(*args):
                result = subprocess.run([sys.executable, str(entrypoint), '--knowledge-home', str(self.kb.home),
                                         *args], cwd=other, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            on = run('harness', 'on', '--provider', 'codex')
            self.assertTrue(on['enabled'])
            self.assertEqual(Path(on['entrypoint']), entrypoint.resolve())
            self.assertEqual(run('harness', 'status', on['session_id']), on)
            self.assertFalse(run('harness', 'off', on['session_id'])['enabled'])
            again = run('harness', 'on', '--provider', 'codex', '--session', on['session_id'])
            self.assertEqual(again['session_id'], on['session_id'])
        self.assertEqual(list(other.iterdir()), [])

    def test_installed_skill_changes_stale_dependent_knowledge(self):
        installer.install(self.root)
        (self.root / 'guide.md').write_text('Observed explanation.\n')
        locations = [self.kb.attach(self.root)]
        entry = {'id': 'guide', 'kind': 'fact', 'scope': 'project', 'status': 'confirmed',
                 'summary': 'The guide explains the result.', 'evidence': ['guide.md:1'], 'paths': ['guide.md']}
        before = self.kb.context(locations)
        self.kb.remember(entry, locations, before['revision'])
        skill = self.root / '.agents/skills/loop-dev/agents/openai.yaml'
        skill.write_text(skill.read_text() + '\n# changed policy\n')
        self.assertIn('rules_or_branch_changed', self.kb.context(locations)['stale'][0]['stale_reasons'])


class SkillInstallationTests(ProjectTest):
    def test_explicit_provider_discovery_and_shared_body_survive_upgrade(self):
        (self.root / '.claude/skills/unrelated').mkdir(parents=True)
        unrelated = self.root / '.claude/skills/unrelated/SKILL.md'
        unrelated.write_text('Keep this skill.\n')
        settings = self.root / '.claude/settings.json'
        settings.write_text('{"hooks": {}}\n')
        legacy = {name: value for name, value in installer.managed_files().items() if name not in HARNESS_SKILL_FILES}
        old_block = installer.START + '\nOld ordinary-request entry\n' + installer.END + '\n'
        with patch.object(installer, 'managed_files', return_value=legacy), patch.object(installer, 'BLOCK', old_block):
            installer.install(self.root)
        installer.install(self.root)
        canonical = (SOURCE / 'skills/loop-dev/SKILL.md').read_text()
        self.assertEqual((self.root / HARNESS_SKILL_FILES[0]).read_text(), canonical)
        claude = (self.root / HARNESS_SKILL_FILES[2]).read_text()
        self.assertEqual(claude.replace('disable-model-invocation: true\n', '', 1), canonical)
        self.assertIn('allow_implicit_invocation: false', (self.root / HARNESS_SKILL_FILES[1]).read_text())
        manifest = read_json(self.root / '.loop/install.json')
        self.assertTrue(set(HARNESS_SKILL_FILES).issubset(manifest['files']))
        self.assertIn('route EVERY subsequent input', (self.root / 'AGENTS.md').read_text())
        self.assertNotIn('Old ordinary-request entry', (self.root / 'AGENTS.md').read_text())
        self.assertEqual(unrelated.read_text(), 'Keep this skill.\n')
        self.assertEqual(settings.read_text(), '{"hooks": {}}\n')
        before = {name: (self.root / name).read_bytes() for name in HARNESS_SKILL_FILES}
        installer.install(self.root)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in HARNESS_SKILL_FILES})

    def test_same_name_skill_conflicts_and_local_edits_abort_before_managed_writes(self):
        for index, name in enumerate(HARNESS_SKILL_FILES):
            root = Path(self.temp.name) / ('collision-' + str(index))
            path = root / name
            path.parent.mkdir(parents=True)
            path.write_text('My existing skill\n')
            with self.assertRaisesRegex(LoopError, 'Local changes'):
                installer.install(root)
            self.assertEqual(path.read_text(), 'My existing skill\n')
            self.assertFalse((root / '.loop/loop.py').exists())
            self.assertFalse((root / 'AGENTS.md').exists())
        installer.install(self.root)
        path = self.root / HARNESS_SKILL_FILES[1]
        path.write_text('My edited policy\n')
        before = (self.root / '.loop/install.json').read_bytes()
        with self.assertRaisesRegex(LoopError, 'Local changes'):
            installer.install(self.root)
        self.assertEqual((self.root / '.loop/install.json').read_bytes(), before)
        self.assertEqual(path.read_text(), 'My edited policy\n')

    def test_symlinked_skill_directory_and_invalid_parent_fail_preflight(self):
        for alias in (True, False):
            root = Path(self.temp.name) / ('alias' if alias else 'file-parent')
            (root / '.agents').mkdir(parents=True)
            if alias:
                (root / 'other').mkdir()
                (root / '.agents/skills').symlink_to(root / 'other', target_is_directory=True)
            else:
                (root / '.agents/skills').write_text('Existing file\n')
            with self.assertRaisesRegex(LoopError, 'symlinked|not a directory'):
                installer.install(root)
            self.assertFalse((root / '.loop/loop.py').exists())

    def test_ignored_skill_policy_changes_invalidate_frozen_development(self):
        installer.install(self.root)
        with (self.root / '.gitignore').open('a') as stream:
            stream.write('.agents/\n.claude/\n')
        self.assertTrue(set(HARNESS_SKILL_FILES).issubset(rule_manifest(self.root, [])))
        self.running()
        path = self.root / HARNESS_SKILL_FILES[1]
        path.write_text(path.read_text() + '\n# altered policy\n')
        with self.assertRaisesRegex(LoopError, 'Frozen'):
            self.engine.gate('M1')
        self.assertEqual(self.state()['phase'], 'blocked')

    def test_development_completion_next_goal_and_off_keep_the_same_session(self):
        state = self.state()
        harness = Harness(self.engine.knowledge)
        enabled = harness.on('claude', state['session_id'])
        self.running()
        self.assertTrue(self.engine.gate('M1')['passed'])
        self.assertTrue(self.engine.gate()['passed'])
        packet = read_json(self.engine.state_dir / 'context.json')
        self.assertEqual(packet['session']['harness'], enabled)
        other = Engine(self.root, 'next-goal', self.engine.knowledge)
        other.initialize('Another goal', 'claude', state['session_id'])
        self.assertEqual(read_json(other.state_dir / 'context.json')['session']['harness'], enabled)
        before = self.state()
        harness.off(state['session_id'])
        self.assertEqual(self.state(), before)
        self.assertFalse(read_json(self.engine.refresh()['context'])['session']['harness']['enabled'])
