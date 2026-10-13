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

from loop_dev import benchmark as bench, bench_report as reports, bench_transport as transport
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
        cases.append({'id': key, 'kind': 'session' if len(roots) > 1 else 'explanation', 'split': split,
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
        self.spec = {'version': 1, 'suite': suite, 'suite_hash': seal, 'providers': ['codex'],
                     'models': {'claude': 'fixed-claude', 'codex': 'fixed-codex'}, 'repeats': 3, 'seed': 0,
                     'source': {'candidate': {'hash': source['hash'], 'ref': None}, 'reference': {'hash': 'reference', 'ref': 'fixed'}},
                     'mode': 'live', 'planned_trials': 18}
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
        self.assertEqual(value['effects']['reference']['mean'], 0)

    def test_case_regression_is_not_hidden_by_an_average_gain(self):
        for row in self.rows:
            if row['arm'] == 'plain':
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
        self.assertTrue(any(f.get('control') == 'reference' and f['case'] == 'tiny-b' for f in value['regressions']))

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
        def call(provider, root, directory, prompt, timeout, schema, outside_git, model):
            packet = json.loads(prompt.split('ARTIFACT DATA:\n')[1])
            seen.append((provider, model, outside_git))
            return copy.deepcopy(self.ratings['samples'][packet['sample']]['reviews'][0])
        with patch('loop_dev.bench_report.providers.structured_call', side_effect=call):
            value = reports.judge(self.root, 'claude', 'fixed-judge', 2)
        self.assertEqual(len(seen), 36)
        self.assertEqual(set(seen), {('claude', 'fixed-judge', True)})
        self.assertTrue(value['passed'])
        self.assertEqual(value['reviewer'], {'provider': 'claude', 'model': 'fixed-judge'})

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
trial = pathlib.Path(os.environ['LOOP_BENCH_PROFILE']).parent
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

    def test_full_paired_runner_both_providers_resumes_and_separates_state(self):
        output = self.base / 'experiment'
        value = bench.run(self.suite, SOURCE, 'HEAD', self.models, ['claude','codex'], 1, 7, 'isolated fake CLI test', output, self.actual)
        self.assertEqual(value['verdict'], 'inconclusive')
        self.assertFalse(value['passed'])
        root, spec, index, rows = bench.read_run(output)
        self.assertTrue(index['complete'])
        self.assertEqual(len(rows), 12)
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
        self.assertEqual((self.suite.parent / 'fixtures/tiny-b/beta/source.json').read_text(), '{"value": 42}\n')

    def test_wrapper_enforces_model_and_shared_call_budget(self):
        trial = self.base / 'trial'
        trial.mkdir()
        env = dict(os.environ, **transport.prepare_transport(trial, self.models, 1, self.actual))
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
            bench.run(self.suite, SOURCE, 'HEAD', self.models, ['codex'], 3, 1, 'test', output)
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
        notes = '''import json
from pathlib import Path
def list_notes(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else []
def add(path, text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Invalid text')
    path = Path(path)
    notes = list_notes(path)
    added = {'id': max((n['id'] for n in notes), default=0) + 1, 'text': text}
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
                elif case['id'] == 'small-change':
                    (workspace / 'app/convert.py').write_text('def seconds(minutes):\n    return minutes * 60\n')
                elif case['id'] == 'source-explanation':
                    write_json(workspace / 'app/answer.json', {'ttl_seconds': 180, 'expires_at_boundary': True,
                        'compares_source_hash': True, 'explanation': '현재 소스 해시가 같고 180초 미만이면 재사용합니다.',
                        'limitation': '디스크의 외부 변경을 자동으로 감시하지 않습니다.'})
                elif case['id'] == 'readme-contract':
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
            'bench','plan','--suite',str(self.suite),'--candidate',str(SOURCE),'--reference-ref','HEAD',
            '--codex-model','fixed-codex','--claude-model','fixed-claude','--provider','codex','--environment','isolated test'],
            capture_output=True,text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['planned_trials'], 18)
        self.assertFalse(home.exists())
