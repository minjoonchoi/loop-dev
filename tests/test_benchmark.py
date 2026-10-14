from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from loop_dev import benchmark as bench, bench_report as reports, bench_transport as transport, bench_check
from loop_dev.storage import LoopError, digest, file_hash, read_json, write_json

SOURCE = Path(__file__).resolve().parents[1]


def make_suite(base, rubrics=True):
    cases = []
    for key, split in (('tiny-a', 'calibration'), ('tiny-b', 'holdout')):
        fixture = base / 'fixtures' / key
        for root in ('alpha', 'beta') if key == 'tiny-b' else ('app',):
            (fixture / root).mkdir(parents=True)
            (fixture / root / 'source.json').write_text('{"value": 42}\n')
            (fixture / root / 'AGENTS.md').write_text('Keep source files unchanged.\n')
        roots = ['alpha', 'beta'] if key == 'tiny-b' else ['app']
        criteria = [{'id': 'correct', 'mode': 'check', 'description': 'Return the value read from the source.', 'critical': True, 'weight': 50}]
        if rubrics:
            criteria.append({'id': 'usable', 'mode': 'rubric', 'description': 'Explain the value clearly.', 'critical': False,
                             'weight': 50, 'anchors': ['Missing', 'Misleading', 'Incomplete', 'Minor omission', 'Clear and correct']})
        turns = [{'root': roots[0], 'prompt': 'Read source.json and write output.json. nested-probe'}]
        if key == 'tiny-b':
            turns.append({'root': 'beta', 'prompt': 'Now write output.json in this directory from the updated source.json.',
                          'before': {'beta/source.json': '{"value":43}\n'}})
        cases.append({'id': key, 'kind': 'session' if len(roots) > 1 else 'development', 'split': split,
                      'fixture': 'fixtures/' + key, 'roots': roots, 'turns': turns, 'artifacts': [r + '/output.json' for r in roots],
                      'protected': [], 'grader': 'grade.py', 'criteria': criteria})
    (base / 'grade.py').write_text('''import json, sys\nfrom pathlib import Path\np = Path(sys.argv[1])\npaths = list(p.glob("*/source.json"))\npassed = all((x.parent / "output.json").is_file() and json.loads((x.parent / "output.json").read_text()) == json.loads(x.read_text()) for x in paths)\nprint(json.dumps({"correct":{"passed":passed,"evidence":"Compared source values with delivered output objects."}}))\n''')
    value = {'version': 1, 'id': 'test-suite', 'cases': cases, 'max_calls': 10, 'timeout_seconds': 30,
             'policy': {'min_repeats': 3, 'quality_margin': 0, 'min_quality': 85, 'max_duration_ratio': 3, 'duration_slack_seconds': 1}}
    write_json(base / 'suite.json', value)
    return base / 'suite.json'


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.suite_path = make_suite(self.base / 'suite')
        suite, seal, assets = bench.load_suite(self.suite_path)
        self.root = self.base / 'run'
        self.root.mkdir()
        source = bench.source_snapshot(SOURCE)
        bench.export_files(self.root / 'sources/candidate', source['files'])
        self.spec = {'version': 2, 'arms': ['plain', 'candidate'], 'suite': suite, 'suite_hash': seal, 'providers': ['codex'],
                     'models': {'claude': 'fixed-claude', 'codex': 'fixed-codex'}, 'repeats': 3, 'seed': 0,
                     'efforts': {'claude': 'high', 'codex': 'medium'},
                     'source': {'candidate': {'hash': source['hash']}},
                     'mode': 'live', 'planned_trials': 12}
        self.index = {'manifest_hash': digest(self.spec), 'complete': True, 'trials': []}
        self.ratings = {'manifest_hash': digest(self.spec), 'repeats': 2, 'reviewer': {'provider': 'human', 'model': 'reviewer-a'}, 'samples': {}}
        self.rows = []
        for case in suite['cases']:
            for repeat in range(3):
                for arm in bench.ARMS:
                    sample = f"{case['id']}-{repeat}-{arm}"
                    trial = self.root / 'trials' / sample
                    (trial / 'workspace').mkdir(parents=True)
                    (trial / 'workspace/answer.txt').write_text('A concrete answer\n')
                    packet = {'sample': sample, 'criteria': [c for c in case['criteria'] if c['mode'] == 'rubric'], 'artifacts': {'answer.txt': 'A concrete answer'}}
                    write_json(trial / 'packet.json', packet)
                    row = {'id': sample, 'case': case['id'], 'provider': 'codex', 'repeat': repeat, 'arm': arm,
                           'status': 'completed', 'activation_valid': True, 'protected_valid': True, 'seconds': 10,
                           'errors': [], 'checks': {'correct': {'passed': True, 'evidence': 'Observed value matches source.'}},
                           'usage': {'provider_calls': 1, 'calls_complete': True, 'total_cost_usd': None}, 'packet_hash': digest(packet),
                           'workspace_hash': digest(bench.directory_files(trial / 'workspace')),
                           'evidence': {'packet.json': file_hash(trial / 'packet.json')}}
                    self.rows.append(row)
                    self.index['trials'].append({'id': sample, 'hash': digest(row)})
                    review = {'ratings': [{'id': 'usable', 'score': 4, 'evidence': 'The supplied answer explains the result.'}],
                              'extra_user_requests': 0, 'intervention_evidence': 'No extra user input requested in the transcript.'}
                    self.ratings['samples'][sample] = {'packet_hash': digest(packet), 'reviews': [copy.deepcopy(review), copy.deepcopy(review)]}
        self.save()

    def save(self):
        for row, entry in zip(self.rows, self.index['trials']):
            entry['hash'] = digest(row)
            write_json(self.root / 'trials' / row['id'] / 'result.json', row)
        write_json(self.root / 'manifest.json', self.spec)
        write_json(self.root / 'index.json', self.index)
        write_json(self.root / 'judgments.json', self.ratings)

    def test_equal_results_are_non_regressing_not_better(self):
        value = reports.gate(self.root, SOURCE, self.suite_path)
        self.assertTrue(value['passed'])
        self.assertEqual(value['verdict'], 'non_regressing')
        self.assertEqual(value['effects']['plain']['mean'], 0)
        self.assertIn('not proof of improvement', (self.root / 'report.md').read_text())

    def test_repeated_paired_gains_are_distinct_from_ties(self):
        for row in self.rows:
            if row['arm'] == 'plain':
                for review in self.ratings['samples'][row['id']]['reviews']:
                    review['ratings'][0]['score'] = 2
        self.save()
        value = reports.report(self.root)
        self.assertEqual(value['verdict'], 'improved')
        self.assertEqual(value['effects']['plain']['low'], 25)
        self.assertEqual(set(value['effects']), {'plain'})
        self.assertEqual(value['scope']['arms'], ['plain', 'candidate'])

    def test_case_regression_is_not_hidden_by_an_average_gain(self):
        for row in self.rows:
            if row['arm'] == 'plain' and row['case'] == 'tiny-a':
                for review in self.ratings['samples'][row['id']]['reviews']:
                    review['ratings'][0]['score'] = 1
        for row in self.rows:
            if row['case'] == 'tiny-b' and row['arm'] == 'candidate':
                for review in self.ratings['samples'][row['id']]['reviews']:
                    review['ratings'][0]['score'] = 3
        self.save()
        value = reports.report(self.root)
        self.assertGreater(value['effects']['plain']['mean'], 0)
        self.assertEqual(value['verdict'], 'regression')
        self.assertTrue(any(f.get('control') == 'plain' and f['case'] == 'tiny-b' for f in value['regressions']))
        improvement = read_json(self.root / 'improvements.json')
        self.assertEqual(improvement['goal_types'], ['development', 'explanation'])
        types = {r['goal_type']: r for r in improvement['type_outcomes']}
        self.assertEqual(types['development']['status'], 'non_regressing')
        self.assertEqual(types['explanation']['status'], 'regression')
        grouped = [i for i in improvement['improvements'] if i['title'] == 'tiny-b / codex / usable']
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped[0]['sample_ids']), 3)
        self.assertTrue(grouped[0]['hypothesis'])
        self.assertTrue(grouped[0]['next_change'])
        self.assertTrue(grouped[0]['revalidation'])
        self.assertIn('rules/quality.md', grouped[0]['rule_paths'])
        self.assertTrue(all((self.root / p).is_file() for p in grouped[0]['evidence']))

    def test_critical_failure_user_requests_and_cost_are_separate_gates(self):
        row = next(r for r in self.rows if r['arm'] == 'candidate')
        row['checks']['correct']['passed'] = False
        row['seconds'] = 50
        for review in self.ratings['samples'][row['id']]['reviews']:
            review['extra_user_requests'] = 1
        self.save()
        value = reports.report(self.root)
        self.assertFalse(value['passed'])
        self.assertTrue({'correct', 'elapsed_time', 'user_requests'}.issubset({f['criterion'] for f in value['regressions']}))

    def test_missing_judgments_failed_controls_and_treatment_leaks_never_prove_improvement(self):
        (self.root / 'judgments.json').unlink()
        self.assertEqual(reports.report(self.root)['verdict'], 'inconclusive')
        row = next(r for r in self.rows if r['arm'] == 'plain')
        row['status'] = 'failed'
        self.save()
        self.assertEqual(reports.report(self.root)['verdict'], 'inconclusive')
        row['status'] = 'completed'
        row['activation_valid'] = False
        self.save()
        self.assertEqual(reports.report(self.root)['verdict'], 'inconclusive')

    def test_simulation_insufficient_repeats_and_incomplete_pairs_do_not_pass(self):
        self.spec['mode'] = 'simulation'
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.save()
        self.assertFalse(reports.report(self.root)['passed'])
        self.spec['mode'] = 'live'
        self.spec['suite']['policy']['min_repeats'] = 4
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.save()
        self.assertEqual(reports.report(self.root)['verdict'], 'inconclusive')
        self.index['complete'] = False
        self.save()
        self.assertFalse(reports.report(self.root)['passed'])

    def test_legacy_single_type_or_unfinished_type_cannot_pass(self):
        self.spec['suite']['cases'][0]['kind'] = 'explanation'
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.save()
        value = reports.report(self.root)
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertTrue(any('two distinct goal types' in m for m in value['missing']))
        self.spec['suite']['cases'][0]['kind'] = 'development'
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.index['complete'] = False
        self.save()
        reports.report(self.root)
        value = read_json(self.root / 'improvements.json')
        self.assertTrue(all(t['status'] == 'inconclusive' for t in value['type_outcomes']))

    def test_unfinished_provider_calls_and_invalid_controls_are_missing_evidence(self):
        row = next(r for r in self.rows if r['arm'] == 'plain')
        row['usage']['calls_complete'] = False
        self.save()
        self.assertEqual(reports.report(self.root)['verdict'], 'inconclusive')
        row['usage']['calls_complete'] = True
        row['protected_valid'] = False
        self.save()
        value = reports.report(self.root)
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertTrue(any(c['quality_delta'] is None for c in value['comparisons']))

    def test_judge_uses_fixed_model_repeated_packet_bound_assessments(self):
        seen = []
        def call(provider, root, directory, prompt, timeout, schema, outside_git, model, effort):
            packet = json.loads(prompt.split('ARTIFACT DATA:\n')[1])
            seen.append((provider, model, outside_git, effort))
            return copy.deepcopy(self.ratings['samples'][packet['sample']]['reviews'][0])
        with patch('loop_dev.bench_report.providers.structured_call', side_effect=call):
            value = reports.judge(self.root, 'claude', 'fixed-judge', 2, effort='high')
        self.assertEqual(len(seen), 24)
        self.assertEqual(set(seen), {('claude', 'fixed-judge', True, 'high')})
        self.assertTrue(value['passed'])
        self.assertEqual(value['reviewer'], {'provider': 'claude', 'model': 'fixed-judge', 'effort': 'high'})

    def test_frozen_judge_settings_reject_changed_effort_before_calls(self):
        self.spec['evaluation'] = {'provider': 'claude', 'model': 'fixed-judge', 'effort': 'high', 'repeats': 2}
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.save()
        with patch('loop_dev.bench_report.providers.structured_call') as call, self.assertRaisesRegex(LoopError, 'frozen'):
            reports.judge(self.root, 'claude', 'fixed-judge', 2, effort='low')
        call.assert_not_called()
        with self.assertRaisesRegex(LoopError, 'frozen evaluation'):
            reports.report(self.root)

    def test_improvements_preserve_missing_values_and_do_not_invent_rule_changes(self):
        reports.report(self.root)
        value = read_json(self.root / 'improvements.json')
        self.assertEqual(value['improvements'], [])
        self.assertEqual(len(value['comparisons']), 6)
        self.assertEqual(value['arms'], ['plain', 'candidate'])
        self.assertTrue(all(set(t['quality_delta']) == {'plain'} for t in value['type_outcomes']))
        self.assertNotIn('이전 버전', (self.root / 'improvements.md').read_text())
        (self.root / 'judgments.json').unlink()
        reports.report(self.root)
        value = read_json(self.root / 'improvements.json')
        self.assertTrue(all(g['quality_mean'] is None and g['rated'] == 0 for g in value['comparisons']))
        self.assertEqual(value['improvements'][0]['priority'], 'P0')
        self.assertEqual(value['improvements'][0]['rule_paths'], [])

    def test_workflow_failure_cannot_publish_a_historical_pass(self):
        for phase in ('judge', 'gate'):
            output = self.base / ('failed-' + phase)
            def run(*args, **kwargs):
                shutil.copytree(self.root, output)
            with patch('loop_dev.bench_check.benchmark.run', side_effect=run), \
                 patch('loop_dev.bench_check.bench_report.judge', side_effect=LoopError('judge unavailable') if phase == 'judge' else None), \
                 patch('loop_dev.bench_check.bench_report.gate', side_effect=LoopError('Candidate changed')):
                result = bench_check.check(self.spec['models'], self.spec['efforts'], candidate=SOURCE,
                    suite=self.suite_path, output=output)
            self.assertEqual(result['verdict'], 'inconclusive')
            self.assertEqual(result['failure']['phase'], phase)
            self.assertFalse(result['passed'])
            improvement = read_json(output / 'improvements.json')
            self.assertFalse(improvement['passed'])
            self.assertTrue(all(t['status'] == 'inconclusive' for t in improvement['type_outcomes']))
            self.assertEqual(read_json(output / 'check.json')['phase'], phase)

    def test_missing_either_condition_never_passes(self):
        rows, entries, ratings = self.rows[:], self.index['trials'][:], copy.deepcopy(self.ratings['samples'])
        for arm in bench.ARMS:
            missing = next(r['id'] for r in rows if r['arm'] == arm)
            self.rows = [r for r in rows if r['id'] != missing]
            self.index['trials'] = [e for e in entries if e['id'] != missing]
            self.ratings['samples'] = {k: v for k, v in ratings.items() if k != missing}
            self.save()
            value = reports.report(self.root)
            self.assertEqual(value['verdict'], 'inconclusive')
            self.assertFalse(value['passed'])

    def test_manifest_cannot_drop_the_plain_condition(self):
        self.spec['arms'] = ['candidate']
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.save()
        with self.assertRaisesRegex(LoopError, 'both plain and candidate'):
            reports.report(self.root)

    def test_historical_reports_keep_their_original_reference_comparison(self):
        self.spec['version'] = 1
        del self.spec['arms']
        self.spec['source']['reference'] = {'hash': 'old-reference', 'ref': 'old-commit'}
        self.spec['planned_trials'] = 18
        for row in list(self.rows):
            if row['arm'] != 'plain':
                continue
            old = copy.deepcopy(row)
            old.update(id=row['id'].replace('plain', 'reference'), arm='reference')
            trial = self.root / 'trials' / old['id']
            shutil.copytree(self.root / 'trials' / row['id'], trial)
            packet = read_json(trial / 'packet.json')
            packet['sample'] = old['id']
            write_json(trial / 'packet.json', packet)
            old['packet_hash'] = digest(packet)
            old['evidence']['packet.json'] = file_hash(trial / 'packet.json')
            self.rows.append(old)
            self.index['trials'].append({'id': old['id'], 'hash': digest(old)})
            rating = copy.deepcopy(self.ratings['samples'][row['id']])
            rating['packet_hash'] = old['packet_hash']
            self.ratings['samples'][old['id']] = rating
        for row in self.rows:
            if row['arm'] != 'reference':
                for rating in self.ratings['samples'][row['id']]['reviews']:
                    rating['ratings'][0]['score'] = 3
        self.index['manifest_hash'] = self.ratings['manifest_hash'] = digest(self.spec)
        self.save()
        value = reports.report(self.root)
        self.assertEqual(value['effects']['plain']['mean'], 0)
        self.assertEqual(value['effects']['reference']['mean'], -12.5)
        self.assertEqual(value['verdict'], 'regression')
        self.assertIn('reference', value['scope']['arms'])
        self.assertIn('이전 버전 대비', (self.root / 'improvements.md').read_text())

    def test_judge_disagreement_is_conservative_and_malformed_ratings_rejected(self):
        sample = next(r['id'] for r in self.rows if r['arm'] == 'candidate')
        self.ratings['samples'][sample]['reviews'][0]['ratings'][0]['score'] = 2
        self.save()
        value = reports.report(self.root)
        self.assertEqual(next(r for r in value['samples'] if r['id'] == sample)['criteria']['usable']['score'], 2)
        self.assertTrue(next(r for r in value['samples'] if r['id'] == sample)['criteria']['usable']['disagreement'])
        self.ratings['samples'][sample]['reviews'][0]['ratings'][0]['score'] = float('nan')
        self.save()
        with self.assertRaises(LoopError):
            reports.report(self.root)

    def test_changed_manifest_packet_workspace_and_grader_invalidate_evidence(self):
        path = self.root / 'trials' / self.rows[0]['id'] / 'workspace/answer.txt'
        path.write_text('Edited later')
        with self.assertRaisesRegex(LoopError, 'workspace changed'):
            reports.report(self.root)
        path.write_text('A concrete answer\n')
        packet = self.root / 'trials' / self.rows[0]['id'] / 'packet.json'
        original = packet.read_text()
        packet.write_text('{}')
        with self.assertRaisesRegex(LoopError, 'packet changed'):
            reports.report(self.root)
        packet.write_text(original)
        grader = self.suite_path.parent / 'grade.py'
        grader.write_text(grader.read_text() + '\n# changed evaluator\n')
        with self.assertRaisesRegex(LoopError, 'evaluator changed'):
            reports.gate(self.root, SOURCE, self.suite_path)
        write_json(self.root / 'manifest.json', {**self.spec, 'seed': 3})
        with self.assertRaisesRegex(LoopError, 'manifest changed'):
            reports.report(self.root)

    def test_current_candidate_change_and_stale_judgment_are_rejected(self):
        self.ratings['samples'][self.rows[0]['id']]['packet_hash'] = 'old'
        self.save()
        with self.assertRaisesRegex(LoopError, 'stale'):
            reports.report(self.root)
        self.ratings['samples'][self.rows[0]['id']]['packet_hash'] = self.rows[0]['packet_hash']
        self.save()
        candidate = self.root / 'sources/candidate'
        (candidate / 'rules/work.md').write_text('Changed rule\n')
        with self.assertRaisesRegex(LoopError, 'Candidate changed'):
            reports.gate(self.root, candidate, self.suite_path)

    def test_proposals_bind_hypotheses_to_rules_cases_and_fixed_evidence(self):
        path = self.base / 'proposal.json'
        write_json(path, {'hypothesis': 'Repeated context reads delay simple tasks.', 'rule_paths': ['rules/context.md'],
                          'case_ids': ['tiny-a'], 'expected_change': 'Keep quality equal while reducing elapsed time.'})
        value = reports.proposal(self.root, path)
        self.assertEqual(value['record']['suite_hash'], self.spec['suite_hash'])
        self.assertEqual(value['record']['candidate_hash'], self.spec['source']['candidate']['hash'])
        self.assertIn('holdout', value['record']['status'])


FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
provider = pathlib.Path(sys.argv[0]).name
if '--version' in sys.argv:
    print(provider + ' fake-benchmark-cli')
    raise SystemExit(0)
if provider == 'codex' and 'resume' in sys.argv:
    assert sys.argv.index('--json') < sys.argv.index('resume')
    assert sys.argv.index('--output-last-message') < sys.argv.index('resume')
prompt = sys.stdin.read()
if 'ARTIFACT DATA:\n' in prompt:
    packet = json.loads(prompt.split('ARTIFACT DATA:\n')[1])
    answer = {'ratings': [{'id': c['id'], 'score': 4, 'evidence': 'The output object matches the supplied source.'} for c in packet['criteria']],
              'extra_user_requests': 0, 'intervention_evidence': 'No additional questions in the transcript.'}
    if provider == 'codex':
        pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(json.dumps(answer))
    else:
        assert os.environ['CLAUDE_CODE_EFFORT_LEVEL'] == sys.argv[sys.argv.index('--effort') + 1]
        print(json.dumps({'structured_output': answer, 'is_error': False}))
    raise SystemExit(0)
trial = pathlib.Path(os.environ['LOOP_BENCH_PROFILE']).parent
profile = json.loads((trial / 'profile.json').read_text())
effort = profile['efforts'][provider]
if provider == 'claude':
    values = [sys.argv[i+1] for i, a in enumerate(sys.argv) if a == '--effort'] + [a.split('=',1)[1] for a in sys.argv if a.startswith('--effort=')]
    assert values == [effort], values
    assert os.environ['CLAUDE_CODE_EFFORT_LEVEL'] == effort
else:
    assert 'model_reasoning_effort=' + json.dumps(effort) in sys.argv
    assert 'plan_mode_reasoning_effort=' + json.dumps(effort) in sys.argv
session = 'worker-session' if prompt == '__NESTED__' else 'conversation-session'
if prompt != '__NESTED__':
    if prompt.startswith(('/loop-dev ', '$loop-dev ')):
        result = subprocess.run([sys.executable, '.loop/loop.py', 'harness', 'on', '--provider', provider], capture_output=True, text=True, check=True)
    if 'nested-probe' in prompt:
        opposite = 'claude' if provider == 'codex' else 'codex'
        args = [opposite, '-p', '--output-format', 'json'] if opposite == 'claude' else [opposite, 'exec', '-']
        subprocess.run(args, input='__NESTED__', text=True, capture_output=True, check=True)
    pathlib.Path('output.json').write_text(pathlib.Path('source.json').read_text())
answer = 'The delivered object matches the source value.'
if provider == 'claude':
    print(json.dumps({'session_id':session,'result':answer,'is_error':False,'total_cost_usd':0.02 if '--resume' in sys.argv else 0.01}))
else:
    if '--output-last-message' in sys.argv:
        pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1]).write_text(answer)
    print(json.dumps({'type':'thread.started','thread_id':session}))
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':answer}}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}))
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.suite = make_suite(self.base / 'suite')
        self.binaries = self.base / 'real'
        self.binaries.mkdir()
        self.actual = {}
        for provider in ('claude', 'codex'):
            path = self.binaries / provider
            path.write_text(FAKE)
            path.chmod(0o700)
            self.actual[provider] = str(path)
        self.models = {'claude': 'fixed-claude', 'codex': 'fixed-codex'}
        self.efforts = {'claude': 'high', 'codex': 'medium'}

    def test_full_check_evaluates_two_types_and_writes_actionable_reports(self):
        output = self.base / 'checked'
        with patch('loop_dev.providers.shutil.which', side_effect=lambda p: self.actual.get(p)):
            value = bench_check.check(self.models, self.efforts, candidate=SOURCE, suite=self.suite,
                output=output, repeats=1, executables=self.actual, judge_provider='claude',
                judge_model='independent-judge', judge_effort='xhigh')
        self.assertEqual(value['phase'], 'completed')
        self.assertEqual(value['verdict'], 'inconclusive')  # fake CLIs never prove quality
        self.assertFalse(value['passed'])
        spec = read_json(output / 'manifest.json')
        self.assertEqual(spec['goal_types'], ['development', 'explanation'])
        self.assertEqual(spec['providers'], ['claude', 'codex'])
        self.assertEqual(spec['evaluation']['model'], 'independent-judge')
        self.assertEqual(spec['evaluation']['effort'], 'xhigh')
        self.assertEqual(spec['arms'], ['plain', 'candidate'])
        self.assertEqual(spec['limits']['default_judge_calls'], 16)
        judgments = read_json(output / 'judgments.json')
        self.assertEqual(len(judgments['samples']), 8)
        processes = list(output.glob('judges/*/*/process.json'))
        self.assertEqual(len(processes), 16)
        self.assertTrue(all(read_json(p)['exit_code'] == 0 for p in processes))
        self.assertTrue(all('xhigh' in read_json(p)['argv'] and 'independent-judge' in read_json(p)['argv'] for p in processes))
        improvement = read_json(output / 'improvements.json')
        self.assertEqual(len(improvement['type_outcomes']), 4)
        self.assertTrue(all(t['completed'] == t['expected'] == 2 for t in improvement['type_outcomes']))
        self.assertTrue(all(r['rated'] == r['expected'] for r in improvement['comparisons']))
        self.assertTrue((output / 'improvements.md').is_file())

    def test_missing_cli_check_produces_setup_report_without_scores(self):
        output = self.base / 'missing-cli'
        with patch('loop_dev.benchmark.shutil.which', return_value=None):
            value = bench_check.check(self.models, self.efforts, candidate=SOURCE, suite=self.suite, output=output)
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertEqual(value['phase'], 'run')
        self.assertIn('Both CLI', value['failure']['error'])
        improvement = read_json(output / 'improvements.json')
        self.assertTrue(all(g['quality_mean'] is None for g in improvement['comparisons']))
        self.assertFalse((output / 'judgments.json').exists())
        with self.assertRaisesRegex(LoopError, 'new benchmark output'):
            bench_check.check(self.models, self.efforts, candidate=SOURCE, suite=self.suite, output=output)

    def test_multiple_cases_of_one_goal_type_are_rejected_before_execution(self):
        original = read_json(self.suite)
        for kinds in (('development', 'development'), ('explanation', 'session')):
            value = copy.deepcopy(original)
            for case, kind in zip(value['cases'], kinds):
                case['kind'] = kind
            write_json(self.suite, value)
            with patch('loop_dev.bench_check.benchmark.run') as run, self.assertRaisesRegex(LoopError, 'two distinct goal types'):
                bench_check.check(self.models, self.efforts, candidate=SOURCE, suite=self.suite, dry_run=True)
            run.assert_not_called()

    def test_standalone_script_dry_run_from_another_directory(self):
        output = self.base / 'dry-output'
        result = subprocess.run([sys.executable, str(SOURCE / 'scripts/benchmark.py'),
            '--codex-model', self.models['codex'], '--codex-effort', 'medium',
            '--claude-model', self.models['claude'], '--claude-effort', 'high',
            '--dry-run', '--output', str(output)], cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value['dry_run'])
        self.assertEqual(value['providers'], ['claude', 'codex'])
        self.assertEqual(value['goal_types'], ['development', 'document', 'explanation'])
        self.assertEqual(value['arms'], ['plain', 'candidate'])
        self.assertEqual(value['planned_trials'], 72)
        self.assertEqual(value['limits']['default_judge_calls'], 144)
        self.assertEqual(set(value['source']), {'candidate'})
        self.assertEqual(value['efforts'], self.efforts)
        self.assertEqual(value['evaluation'], {'provider': 'codex', 'model': 'fixed-codex', 'effort': 'medium', 'repeats': 2})
        self.assertFalse(output.exists())
        for options in ({'output': SOURCE / 'invalid-run'}, {'judge_effort': ''}, {'judge_model': ''}):
            with self.assertRaises(LoopError):
                bench_check.check(self.models, self.efforts, candidate=SOURCE, suite=self.suite, dry_run=True, **options)

    def test_effort_conflicts_fail_before_calls_and_claude_env_is_local(self):
        trial = self.base / 'effort-trial'
        trial.mkdir()
        env = dict(os.environ, **transport.prepare_transport(trial, self.models, 20, self.actual, self.efforts))
        env['CLAUDE_CODE_EFFORT_LEVEL'] = 'low'
        for provider, flags in [('claude', ['--effort', 'low']), ('claude', ['--effort=low']),
            ('codex', ['-c', 'model_reasoning_effort="low"']),
            ('codex', ['--config=plan_mode_reasoning_effort="low"']),
            ('codex', ['-cmodel_reasoning_effort="low"'])]:
            command = [str(trial / 'bin' / provider), *(['exec', '-'] if provider == 'codex' else ['-p']), *flags]
            result = subprocess.run(command, input='__NESTED__', capture_output=True, text=True, env=env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('effort profile is fixed', result.stderr)
        self.assertFalse(list((trial / 'calls').glob('*.json')))
        for flags in ([], ['--effort', 'high'], ['--effort=high']):
            result = subprocess.run([str(trial / 'bin/claude'), '-p', *flags], input='__NESTED__', capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(env['CLAUDE_CODE_EFFORT_LEVEL'], 'low')
        for efforts in ({'codex':'auto', 'claude':'high'}, {'codex':'medium'}, {'codex':'medium','claude':None}):
            with self.assertRaises(LoopError):
                transport.validate_efforts(efforts)

    def test_full_paired_runner_both_providers_resumes_and_separates_state(self):
        output = self.base / 'experiment'
        candidate = self.base / 'source-without-git'
        bench.export_files(candidate, bench.source_snapshot(SOURCE)['files'])
        self.assertFalse((candidate / '.git').exists())
        value = bench.run(self.suite, candidate, self.models, ['claude','codex'], 1, 7, 'isolated fake CLI test', output, self.actual, efforts=self.efforts)
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertFalse(value['passed'])
        root, spec, index, rows = bench.read_run(output)
        self.assertTrue(index['complete'])
        self.assertEqual(len(rows), 8)
        self.assertEqual({r['arm'] for r in rows}, {'plain', 'candidate'})
        self.assertEqual({p.name for p in (output / 'sources').iterdir()}, {'candidate'})
        self.assertEqual(spec['mode'], 'simulation')
        self.assertTrue(all(r['status'] == 'completed' for r in rows))
        self.assertTrue(all(r['activation_valid'] for r in rows))
        self.assertTrue(all(r['checks']['correct']['passed'] for r in rows))
        for row in rows:
            trial = output / 'trials' / row['id']
            packet = read_json(trial / 'packet.json')
            self.assertFalse({'arm','model','source'}.intersection(packet))
            self.assertFalse((trial / 'workspace/grade.py').exists())
            self.assertFalse(any(trial.glob('workspace/*/.loop/benchmarks')))
            if row['arm'] == 'plain':
                self.assertFalse(any(trial.glob('workspace/*/.loop')))
            else:
                self.assertEqual(len(bench.activation(trial)), 1)
                if len(row['turns']) > 1:
                    self.assertEqual(row['turns'][0]['harness_session'], row['turns'][1]['harness_session'])
            calls = [read_json(p) for p in (trial / 'calls').glob('*.json')]
            self.assertTrue({'claude','codex'}.issubset({c['provider'] for c in calls}))
            self.assertTrue(all(c['model'] == self.models[c['provider']] for c in calls))
            self.assertTrue(all(c['effort'] == self.efforts[c['provider']] for c in calls))
        self.assertEqual((self.suite.parent / 'fixtures/tiny-b/beta/source.json').read_text(), '{"value": 42}\n')

    def test_wrapper_enforces_model_and_shared_call_budget(self):
        trial = self.base / 'trial'
        trial.mkdir()
        env = dict(os.environ, **transport.prepare_transport(trial, self.models, 1, self.actual, self.efforts))
        command = [str(trial / 'bin/codex'), 'exec', '-']
        first = subprocess.run(command, input='__NESTED__', text=True, capture_output=True, env=env)
        self.assertEqual(first.returncode, 0, first.stderr)
        second = subprocess.run(command, input='__NESTED__', text=True, capture_output=True, env=env)
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('budget exhausted', second.stderr)
        mismatch = subprocess.run(command + ['--model', 'different'], input='__NESTED__', text=True, capture_output=True, env=env)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn('profile is fixed', mismatch.stderr)

    def test_usage_does_not_double_count_resumed_claude_cost_or_guess_missing_cost(self):
        trial = self.base / 'usage'
        (trial / 'calls').mkdir(parents=True)
        for i, cost in enumerate((0.01,0.03)):
            write_json(trial / 'calls' / f'{i}.json', {'provider':'claude','session':'same','cost_usd':cost})
        write_json(trial / 'calls/2.json', {'provider':'claude','session':'other','cost_usd':0.02})
        write_json(trial / 'calls/3.json', {'provider':'codex','session':'third','tokens':100})
        usage = transport.usage(trial)
        self.assertEqual(usage['claude_cost_usd_estimate'], 0.05)
        self.assertEqual(usage['codex_tokens'], 100)
        self.assertIsNone(usage['total_cost_usd'])
        write_json(trial / 'calls/4.json', {'provider':'codex','tokens':None})
        self.assertIsNone(transport.usage(trial)['codex_tokens'])

    def test_invalid_suite_and_missing_clis_fail_without_inventing_a_run(self):
        suite = read_json(self.suite)
        suite['policy']['quality_margin'] = float('inf')
        write_json(self.suite, suite)
        with self.assertRaises(LoopError): bench.load_suite(self.suite)
        suite['policy']['quality_margin'] = 0
        suite['cases'][0]['fixture'] = '../escape'
        write_json(self.suite, suite)
        with self.assertRaises(LoopError): bench.load_suite(self.suite)
        self.suite = make_suite(self.base / 'other-suite')
        output = self.base / 'no-run'
        with patch('loop_dev.benchmark.shutil.which', return_value=None), self.assertRaisesRegex(LoopError, 'Both CLI'):
            bench.run(self.suite, SOURCE, self.models, ['codex'], 3, 1, 'test', output, efforts=self.efforts)
        self.assertFalse(output.exists())

    def test_checked_in_suite_graders_detect_real_initial_defects(self):
        suite, _, _ = bench.load_suite(SOURCE / 'benchmarks/suite.json')
        for case in suite['cases']:
            if case['kind'] != 'development': continue
            workspace = self.base / case['id']
            shutil.copytree(SOURCE / 'benchmarks' / case['fixture'], workspace)
            result = subprocess.run([sys.executable, '-I', str(SOURCE / 'benchmarks/grade.py'), str(workspace), 'unused', case['id']], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any(not x['passed'] for x in json.loads(result.stdout).values()))

    def test_checked_in_graders_accept_complete_artifacts(self):
        suite, _, _ = bench.load_suite(SOURCE / 'benchmarks/suite.json')
        notes = '''from __future__ import annotations
import json
from pathlib import Path
from dataclasses import dataclass
from helper import FIRST_ID
@dataclass
class Metadata:
    text: str
def list_notes(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []
def add(path, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Invalid text')
    path = Path(path)
    notes = list_notes(path)
    added = {'id': max((n['id'] for n in notes), default=FIRST_ID - 1) + 1, 'text': text}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(notes + [added], ensure_ascii=False))
    return added
'''
        for case in suite['cases']:
            with self.subTest(case=case['id']):
                workspace = self.base / ('golden-' + case['id'])
                shutil.copytree(SOURCE / 'benchmarks' / case['fixture'], workspace)
                if case['id'].startswith('notes-'):
                    (workspace / 'app/notes.py').write_text(notes)
                    (workspace / 'app/helper.py').write_text('FIRST_ID = 1\n')
                elif case['id'] == 'small-change':
                    (workspace / 'app/convert.py').write_text('def seconds(minutes):\n    return minutes * 60\n')
                elif case['id'] == 'source-explanation':
                    write_json(workspace / 'app/answer.json', {'ttl_seconds': 180, 'expires_at_boundary': True,
                        'compares_source_hash': True, 'explanation': '현재 소스 해시가 같고 180초 미만이면 재사용합니다.',
                        'limitation': '디스크의 외부 변경을 자동으로 감시하지 않습니다.'})
                elif case['id'] == 'readme-contract':
                    write_json(workspace / 'app/demo.json', ['previous verification data'])
                    (workspace / 'app/README.md').write_text('Python 3을 사용합니다.\n```sh\npython3 notes.py add --text 안녕 --file demo.json\npython3 notes.py list --file demo.json\n```\n')
                    write_json(workspace / 'app/usage.json', {'commands': [
                        {'argv': ['python3', 'notes.py', 'add', '--text', '안녕', '--file', 'demo.json'], 'expected': ['안녕']},
                        {'argv': ['python3', 'notes.py', 'list', '--file', 'demo.json'], 'expected': ['안녕']}]})
                else:
                    write_json(workspace / 'alpha/settings.json', {'ttl_seconds': 120})
                    write_json(workspace / 'alpha/facts.json', {'ttl_seconds': 60, 'explanation': '첫 설정은 60초입니다.'})
                    write_json(workspace / 'beta/report.json', {'ttl_seconds': 120, 'language': 'ko', 'explanation': '현재 설정은 120초입니다.'})
                result = subprocess.run([sys.executable, '-I', str(SOURCE / 'benchmarks/grade.py'), str(workspace), 'unused', case['id']], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(all(x['passed'] for x in json.loads(result.stdout).values()), result.stdout)

    def test_broken_or_incomplete_grader_is_missing_evidence(self):
        suite, _, _ = bench.load_suite(self.suite)
        workspace = self.suite.parent / suite['cases'][0]['fixture']
        grader = self.base / 'broken-grader.py'
        for code in ('raise RuntimeError("grader unavailable")', 'print("{}")',
                     'print(\'{"correct":{"passed":true,"evidence":""}}\')'):
            with tempfile.TemporaryDirectory(dir=self.base) as directory:
                trial = Path(directory)
                write_json(trial / 'responses.json', [])
                grader.write_text(code)
                with self.assertRaises(LoopError):
                    bench.grade(trial, workspace, suite['cases'][0], grader)

    def test_plan_cli_does_not_create_a_session_or_require_installed_models(self):
        home = self.base / 'untouched-knowledge'
        result = subprocess.run([sys.executable, str(SOURCE / 'scripts/loop.py'), '--knowledge-home', str(home),
            'bench','plan','--suite',str(self.suite),'--candidate',str(SOURCE),
            '--codex-model','fixed-codex','--claude-model','fixed-claude','--codex-effort','medium','--claude-effort','high',
            '--provider','codex','--environment','isolated test'],
            capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['planned_trials'], 12)
        self.assertFalse(home.exists())
