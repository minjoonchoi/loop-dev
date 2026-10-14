from __future__ import annotations

import copy
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_benchmark as fixtures
from loop_dev import bench_check, bench_evolution as evolution, bench_report as reports, bench_transport, benchmark as bench
from loop_dev.storage import LoopError, digest, file_hash, read_json, write_json


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.ReportTests()
        self.data.setUp()
        self.addCleanup(self.data.doCleanups)
        self.base, self.root = self.data.base, self.data.root
        self.history = self.base / 'history'
        spec = bench.prepare(self.data.suite_path, fixtures.SOURCE, self.data.spec['models'],
            ['codex'], 3, 0, 'test host', self.data.spec['efforts'],
            {'provider': 'codex', 'model': 'fixed-codex', 'effort': 'medium', 'repeats': 2})
        spec['source'] = self.data.spec['source']
        spec.update(mode='live', versions={'claude': 'test-1', 'codex': 'test-1'})
        self.data.spec = spec
        self.data.ratings['reviewer'] = {'provider': 'codex', 'model': 'fixed-codex', 'effort': 'medium'}
        self.save()

    def save(self):
        self.data.index['manifest_hash'] = self.data.ratings['manifest_hash'] = digest(self.data.spec)
        self.data.save()

    def publish(self, started=None):
        root, spec = self.data.root, self.data.spec
        started = evolution.read_history(self.history) if started is None else started
        raw = reports.report(root, persist=False)
        with evolution.record(root, spec, raw, self.history, started) as result:
            reports.report(root)
            write_json(root / 'check.json', {'phase': 'completed', 'passed': result['passed'],
                'failure': None, 'manifest_hash': digest(spec), 'evolution_hash': file_hash(root / 'evolution.json')})
        return result

    def next_run(self, name='after', changed=True):
        root = self.base / name
        shutil.copytree(self.data.root, root)
        for filename in ('check.json', 'evolution.json', 'report.json', 'report.md', 'improvements.json', 'improvements.md'):
            (root / filename).unlink(missing_ok=True)
        self.data.root = root
        if changed:
            path = root / 'sources/candidate/rules/work.md'
            path.write_text(path.read_text() + '\nBounded change for ' + name + '.\n')
        self.data.spec['source']['candidate']['hash'] = bench.source_snapshot(root / 'sources/candidate')['hash']
        self.save()

    def rate(self, arm, score, case=None):
        for row in self.data.rows:
            if row['arm'] == arm and (case is None or row['case'] == case):
                for review in self.data.ratings['samples'][row['id']]['reviews']:
                    review['ratings'][0]['score'] = score
        self.save()

    def test_first_run_saves_baseline_without_claiming_before_after_improvement(self):
        value = self.publish()
        self.assertTrue(value['passed'])
        self.assertEqual(value['evolution']['verdict'], 'baseline_created')
        self.assertTrue(value['evolution']['baseline_updated'])
        self.assertIsNone(value['evolution']['before'])
        self.assertEqual(len(evolution.read_history(self.history)['baselines']), 1)
        self.assertIn('첫 기준 수집', (self.root / 'improvements.md').read_text())

    def test_changed_harness_tie_advances_verified_baseline_without_improvement_claim(self):
        self.publish()
        before_files = {p.name: file_hash(p) for p in self.root.iterdir() if p.is_file()}
        self.next_run()
        value = self.publish()
        self.assertEqual(value['verdict'], 'non_regressing')
        self.assertTrue(value['evolution']['baseline_updated'])
        self.assertEqual(value['evolution']['before']['run'], str(self.root))
        self.assertEqual(before_files, {p.name: file_hash(p) for p in self.root.iterdir() if p.is_file()})
        self.assertEqual(next(iter(evolution.read_history(self.history)['baselines'].values()))['run'], str(self.data.root))
        self.assertEqual(len(value['evolution']['type_outcomes']), 2)
        self.assertTrue(reports.gate(self.data.root, self.data.root / 'sources/candidate', self.data.suite_path)['passed'])

    def test_quality_gain_requires_changed_harness_and_gain_over_plain_drift(self):
        self.rate('candidate', 3)
        self.rate('plain', 3)
        self.publish()
        self.next_run()
        self.rate('candidate', 4)
        value = self.publish()
        self.assertEqual(value['verdict'], 'improved')
        self.assertEqual(value['evolution']['effects']['candidate']['low'], 12.5)
        self.assertEqual(value['evolution']['effects']['harness_gain']['low'], 12.5)

    def test_same_source_variation_does_not_replace_baseline_or_count_as_improvement(self):
        self.rate('candidate', 3)
        self.rate('plain', 3)
        self.publish()
        before = evolution.read_history(self.history)
        self.next_run(changed=False)
        self.rate('candidate', 4)
        value = self.publish()
        self.assertEqual(value['verdict'], 'non_regressing')
        self.assertFalse(value['evolution']['baseline_updated'])
        self.assertEqual(evolution.read_history(self.history), before)

    def test_shared_model_gain_is_not_attributed_to_harness_change(self):
        self.rate('candidate', 3)
        self.rate('plain', 3)
        self.publish()
        self.next_run()
        self.rate('candidate', 4)
        self.rate('plain', 4)
        value = self.publish()
        self.assertEqual(value['verdict'], 'non_regressing')
        self.assertEqual(value['evolution']['effects']['harness_gain']['mean'], 0)

    def test_type_regression_blocks_even_when_on_off_passes_and_average_increases(self):
        self.rate('plain', 3)
        self.rate('candidate', 3, 'tiny-a')
        self.publish()
        baseline = evolution.read_history(self.history)
        self.next_run()
        self.rate('candidate', 4, 'tiny-a')
        sample = next(r['id'] for r in self.data.rows if r['arm'] == 'candidate' and r['case'] == 'tiny-b')
        for review in self.data.ratings['samples'][sample]['reviews']:
            review['ratings'][0]['score'] = 3
        self.save()
        value = self.publish()
        self.assertEqual(value['on_off_verdict'], 'improved')
        self.assertGreater(value['evolution']['effects']['candidate']['mean'], 0)
        self.assertEqual(value['verdict'], 'regression')
        self.assertFalse(value['passed'])
        self.assertEqual(evolution.read_history(self.history), baseline)
        self.assertEqual(value['evolution']['regressions'][0]['goal_type'], 'explanation')
        self.assertEqual(reports.gate(self.data.root, self.data.root / 'sources/candidate', self.data.suite_path)['verdict'], 'regression')
        self.assertTrue(read_json(self.data.root / 'improvements.json')['improvements'])

    def test_question_and_time_regressions_are_independent_of_quality(self):
        self.publish()
        self.next_run()
        for row in self.data.rows:
            row['seconds'] = 40
            for review in self.data.ratings['samples'][row['id']]['reviews']:
                review['extra_user_requests'] = 1
        self.save()
        value = self.publish()
        self.assertEqual(value['on_off_verdict'], 'non_regressing')
        self.assertEqual(value['verdict'], 'regression')
        self.assertEqual({r['criterion'] for r in value['evolution']['regressions']}, {'elapsed_time', 'user_requests'})

    def test_fully_measured_on_off_failure_still_reports_before_after_losses(self):
        self.publish()
        baseline = evolution.read_history(self.history)
        self.next_run()
        self.rate('candidate', 3)
        value = self.publish()
        self.assertEqual(value['on_off_verdict'], 'regression')
        self.assertEqual(value['evolution']['verdict'], 'regression')
        self.assertEqual(value['evolution']['effects']['candidate']['mean'], -12.5)
        self.assertFalse(value['passed'])
        self.assertEqual(evolution.read_history(self.history), baseline)

    def test_different_effort_suite_or_environment_needs_a_separate_baseline(self):
        self.publish()
        for field in ('efforts', 'suite_hash', 'environment', 'versions', 'evaluation', 'seed'):
            with self.subTest(field=field):
                original = copy.deepcopy(self.data.spec)
                self.next_run(field)
                if field == 'efforts':
                    self.data.spec['efforts']['claude'] = 'xhigh'
                    self.data.spec['routing'] = bench_transport.routing_profile(self.data.spec['models'], self.data.spec['efforts'])
                elif field == 'evaluation':
                    self.data.spec['evaluation']['model'] = 'another-judge'
                    self.data.ratings['reviewer']['model'] = 'another-judge'
                elif field == 'versions':
                    self.data.spec[field]['claude'] = 'test-2'
                else:
                    self.data.spec[field] = 1 if field == 'seed' else 'different'
                self.save()
                value = self.publish()
                self.assertEqual(value['evolution']['verdict'], 'baseline_created')
                self.assertIsNone(value['evolution']['before'])
                self.data.spec = original
                self.data.ratings['reviewer']['model'] = original['evaluation']['model']

    def test_tampered_before_evidence_or_missing_run_cannot_be_replaced(self):
        self.publish()
        baseline = evolution.read_history(self.history)
        self.next_run()
        before_packet = next(self.root.glob('trials/*/packet.json'))
        original = before_packet.read_text()
        before_packet.write_text('{}')
        with self.assertRaisesRegex(LoopError, 'changed'):
            self.publish()
        self.assertEqual(evolution.read_history(self.history), baseline)
        before_packet.write_text(original)
        (self.root / 'judgments.json').unlink()
        with self.assertRaisesRegex(LoopError, 'changed or is missing'):
            self.publish()

    def test_removing_temporal_comparison_cannot_bypass_later_gate(self):
        self.publish()
        self.next_run()
        self.publish()
        (self.data.root / 'evolution.json').unlink()
        with self.assertRaisesRegex(LoopError, 'Before/after evidence changed'):
            reports.gate(self.data.root, self.data.root / 'sources/candidate', self.data.suite_path)

    def test_concurrent_baseline_advance_makes_the_other_run_inconclusive(self):
        self.publish()
        started = evolution.read_history(self.history)
        self.next_run('first')
        self.publish()
        accepted = evolution.read_history(self.history)
        self.next_run('second')
        value = self.publish(started)
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertFalse(value['passed'])
        self.assertIn('Baseline changed', value['missing'][0])
        self.assertEqual(evolution.read_history(self.history), accepted)

    def test_incomplete_or_simulated_runs_never_create_baselines(self):
        for mode in ('simulation', 'live'):
            if mode == 'live':
                self.next_run('incomplete')
            self.data.spec['mode'] = mode
            self.data.index['complete'] = mode != 'live'
            self.save()
            value = self.publish()
            self.assertFalse(value['passed'])
            self.assertEqual(evolution.read_history(self.history)['baselines'], {})

    def test_full_check_automatically_records_and_reuses_history(self):
        output = self.base / 'check-first'
        def run(*args, **kwargs):
            shutil.copytree(self.root, output)
        with patch('loop_dev.bench_check.benchmark.run', side_effect=run), \
             patch('loop_dev.bench_check.bench_report.judge'):
            first = bench_check.check(self.data.spec['models'], self.data.spec['efforts'], candidate=fixtures.SOURCE,
                suite=self.data.suite_path, providers=['codex'], environment='test host', output=output, history=self.history)
            output = self.base / 'check-second'
            second = bench_check.check(self.data.spec['models'], self.data.spec['efforts'], candidate=fixtures.SOURCE,
                suite=self.data.suite_path, providers=['codex'], environment='test host', output=output, history=self.history)
        self.assertTrue(first['passed'])
        self.assertEqual(first['evolution']['verdict'], 'baseline_created')
        self.assertTrue(second['passed'])
        self.assertEqual(second['evolution']['verdict'], 'non_regressing')
        self.assertEqual(second['evolution']['before']['run'], first['output'])
        self.assertFalse(second['evolution']['baseline_updated'])

    def test_dry_run_never_creates_history_and_rejects_overlapping_paths(self):
        kwargs = dict(candidate=fixtures.SOURCE, suite=self.data.suite_path, output=self.base / 'dry', dry_run=True)
        value = bench_check.check(self.data.spec['models'], self.data.spec['efforts'], history=self.history, **kwargs)
        self.assertEqual(value['history'], str(self.history))
        self.assertFalse(self.history.exists())
        self.assertFalse(Path(value['output']).exists())
        for history in (fixtures.SOURCE / 'history', self.base / 'dry' / 'history', self.base):
            with self.assertRaisesRegex(LoopError, 'history outside'):
                bench_check.check(self.data.spec['models'], self.data.spec['efforts'], history=history, **kwargs)

    def test_history_write_failure_cannot_leave_a_passing_check_or_gate(self):
        output = self.base / 'history-failure'
        def write(path, value):
            if path.name == 'baselines.json':
                raise OSError('History storage unavailable')
            return write_json(path, value)
        with patch('loop_dev.bench_check.benchmark.run', side_effect=lambda *a, **kw: shutil.copytree(self.root, output)), \
             patch('loop_dev.bench_check.bench_report.judge'), \
             patch('loop_dev.bench_evolution.write_json', side_effect=write):
            value = bench_check.check(self.data.spec['models'], self.data.spec['efforts'], candidate=fixtures.SOURCE,
                suite=self.data.suite_path, providers=['codex'], environment='test host', output=output, history=self.history)
        self.assertFalse(value['passed'])
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertEqual(value['failure']['phase'], 'history')
        self.assertFalse(value['evolution']['baseline_updated'])
        self.assertEqual(evolution.read_history(self.history)['baselines'], {})
        self.assertFalse(reports.gate(output, fixtures.SOURCE, self.data.suite_path)['passed'])
        self.assertFalse(read_json(output / 'improvements.json')['passed'])

    def test_invalid_history_fails_before_any_model_call(self):
        write_json(self.history / 'baselines.json', {'version': 1, 'baselines': {'bad': {'run': 'missing'}}})
        with patch('loop_dev.bench_check.benchmark.run') as run, self.assertRaisesRegex(LoopError, 'Invalid before-run'):
            bench_check.check(self.data.spec['models'], self.data.spec['efforts'], candidate=fixtures.SOURCE,
                suite=self.data.suite_path, history=self.history)
        run.assert_not_called()
