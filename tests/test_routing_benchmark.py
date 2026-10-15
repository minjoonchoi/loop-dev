from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_benchmark as fixtures
from loop_work import bench_evolution, bench_report, bench_transport as transport, benchmark
from loop_work.storage import LoopError, digest, file_hash, read_json, write_json


def catalog(models, efforts):
    value = copy.deepcopy(transport.routing_profile(models, efforts)['policy'])
    value['requirements'] = {'low': 1, 'medium': 50, 'high': 100}
    for provider in ('claude', 'codex'):
        value['profiles'].append({'id': provider + '-small', 'provider': provider,
            'model': provider + '-small', 'effort': 'low', 'capability_rank': 1, 'cost_rank': 1})
        next(p for p in value['profiles'] if p['id'] == 'fixed-' + provider)['cost_rank'] = 10
    return value


FAKE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
provider = pathlib.Path(sys.argv[0]).name
prompt = sys.stdin.read()
model = sys.argv[sys.argv.index('--model') + 1]
if provider == 'claude':
    efforts = [sys.argv[i + 1] for i, arg in enumerate(sys.argv) if arg == '--effort']
    effort = efforts[0] if efforts else next(arg.split('=', 1)[1] for arg in sys.argv if arg.startswith('--effort='))
    assert os.environ['CLAUDE_CODE_EFFORT_LEVEL'] == effort
else:
    efforts = [arg.split('=', 1)[1].strip('"') for arg in sys.argv if arg.startswith('model_reasoning_effort=')]
    effort = efforts[0]
if prompt == 'fail':
    print('transport unavailable', file=sys.stderr)
    raise SystemExit(4)
answer = json.dumps({'model': model, 'effort': effort})
if provider == 'claude':
    print(json.dumps({'session_id': prompt, 'result': answer, 'is_error': False, 'total_cost_usd': 0.01}))
else:
    print(json.dumps({'type': 'thread.started', 'thread_id': prompt}))
    print(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': answer}}))
    print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 5}}))
'''


class RoutingBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.models = {'claude': 'fixed-claude', 'codex': 'fixed-codex'}
        self.efforts = {'claude': 'high', 'codex': 'medium'}
        self.actual = {}
        binary = self.base / 'real'
        binary.mkdir()
        for provider in self.models:
            path = binary / provider
            path.write_text(FAKE)
            path.chmod(0o700)
            self.actual[provider] = str(path)

    def setup_transport(self, max_calls=3):
        trial = self.base / 'trial'
        trial.mkdir()
        routing = transport.routing_profile(self.models, self.efforts, catalog(self.models, self.efforts))
        env = dict(os.environ, **transport.prepare_transport(trial, self.models, max_calls,
                                                            self.actual, self.efforts, routing))
        return trial, env

    def invoke(self, trial, env, provider, model, effort, prompt='call'):
        args = [str(trial / 'bin' / provider), *(['exec', '-'] if provider == 'codex' else ['-p']),
                '--model', model, *transport.effort_args(provider, effort)]
        return subprocess.run(args, input=prompt, capture_output=True, text=True, env=env)

    def test_catalog_is_explicit_canonical_and_requires_main_pairs(self):
        fixed = transport.routing_profile(self.models, self.efforts)
        self.assertEqual(fixed['mode'], 'fixed')
        self.assertEqual(len(fixed['policy']['profiles']), 2)
        value = catalog(self.models, self.efforts)
        adaptive = transport.routing_profile(self.models, self.efforts, value)
        value['profiles'].reverse()
        self.assertEqual(adaptive, transport.routing_profile(self.models, self.efforts, value))
        value['profiles'] = [p for p in value['profiles'] if p['id'] != 'fixed-codex']
        with self.assertRaisesRegex(LoopError, 'pinned conversation'):
            transport.routing_profile(self.models, self.efforts, value)

    def test_actual_low_and_higher_pairs_are_preserved_and_share_budget(self):
        trial, env = self.setup_transport()
        low = self.invoke(trial, env, 'codex', 'codex-small', 'low', 'first')
        self.assertEqual(low.returncode, 0, low.stderr)
        self.assertEqual(json.loads(transport.parse_output('codex', low.stdout)['answer']),
                         {'model': 'codex-small', 'effort': 'low'})
        high = self.invoke(trial, env, 'codex', self.models['codex'], self.efforts['codex'], 'second')
        self.assertEqual(high.returncode, 0, high.stderr)
        failure = self.invoke(trial, env, 'claude', 'claude-small', 'low', 'fail')
        self.assertEqual(failure.returncode, 4)
        exhausted = self.invoke(trial, env, 'claude', self.models['claude'], 'high')
        self.assertNotEqual(exhausted.returncode, 0)
        self.assertIn('budget exhausted', exhausted.stderr)
        value = transport.usage(trial)
        self.assertEqual(value['provider_calls'], 3)
        self.assertEqual(value['call_outcomes']['failed_calls'], 1)
        self.assertEqual(len(value['profile_calls']), 3)
        self.assertIsNone(value['worker_outcomes']['retry_calls'])
        self.assertIsNone(value['total_cost_usd'])

    def test_unknown_or_conflicting_pairs_fail_without_override(self):
        trial, env = self.setup_transport()
        for provider in ('claude', 'codex'):
            bad = self.invoke(trial, env, provider, provider + '-small', 'high')
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('outside the frozen', bad.stderr)
        args = ['exec', '--model', 'codex-small', '-c', 'model_reasoning_effort="low"',
                '-c', 'plan_mode_reasoning_effort="high"']
        with self.assertRaisesRegex(LoopError, 'Conflicting'):
            transport.selected_profile(read_json(trial / 'profile.json'), 'codex', args)
        self.assertFalse(list((trial / 'calls').glob('*.json')))

    def test_fixed_and_changed_adaptive_profiles_never_share_evolution_history(self):
        suite = fixtures.make_suite(self.base / 'suite')
        fixed = benchmark.prepare(suite, fixtures.SOURCE, self.models, ['codex'], 3, 1, 'host', self.efforts,
            {'provider': 'codex', 'model': 'judge', 'effort': 'high', 'repeats': 2})
        fixed['versions'] = {'codex': 'test', 'claude': 'test'}
        adaptive = copy.deepcopy(fixed)
        adaptive['routing'] = transport.routing_profile(self.models, self.efforts, catalog(self.models, self.efforts))
        self.assertNotEqual(digest(bench_evolution.profile(fixed)), digest(bench_evolution.profile(adaptive)))
        changed = copy.deepcopy(adaptive)
        policy = changed['routing']['policy']
        policy['requirements']['medium'] = 60
        changed['routing'] = transport.routing_profile(self.models, self.efforts, policy)
        self.assertNotEqual(digest(bench_evolution.profile(adaptive)), digest(bench_evolution.profile(changed)))
        legacy = copy.deepcopy(fixed)
        del legacy['routing']
        self.assertNotIn('routing', bench_evolution.profile(legacy))

    def test_both_arms_receive_identical_catalog_and_actual_profiles_are_reported(self):
        runner = fixtures.RunnerTests()
        runner.setUp()
        self.addCleanup(runner.doCleanups)
        output = runner.base / 'adaptive-run'
        policy = catalog(runner.models, runner.efforts)
        benchmark.run(runner.suite, fixtures.SOURCE, runner.models, ['codex'], 1, 1, 'fake host', output,
                      runner.actual, efforts=runner.efforts, routing_profile=policy)
        _, spec, _, rows = benchmark.read_run(output)
        self.assertEqual(spec['routing']['mode'], 'adaptive')
        self.assertEqual({r['arm'] for r in rows}, {'plain', 'candidate'})
        for row in rows:
            trial = output / 'trials' / row['id']
            self.assertTrue(row['routing_valid'])
            self.assertEqual(read_json(trial / 'routing-profile.json'), spec['routing']['policy'])
            prompt = (trial / 'turns/0/prompt.txt').read_text()
            self.assertIn('Execution settings shared by both', prompt)
            self.assertGreater(row['usage']['role_calls']['conversation']['calls'], 0)
        improvement = read_json(output / 'improvements.json')
        self.assertEqual(improvement['routing'], spec['routing'])
        self.assertTrue(improvement['actual_profile_usage'])
        self.assertIn('CLI 모델 요청별 호출', (output / 'improvements.md').read_text())

    def test_claude_fallback_cannot_escape_pinned_profile_or_rewrite_host_settings(self):
        trial, env = self.setup_transport()
        profile = read_json(trial / 'profile.json')
        for flags in (['--fallback-model', 'outside-catalog'], ['--fallback-model=outside-catalog'],
                      ['--fallback-model', 'fixed-claude,claude-small']):
            args = ['-p', '--model', 'fixed-claude', '--effort', 'high', *flags]
            with self.assertRaisesRegex(LoopError, 'exact selected model'):
                transport.selected_profile(profile, 'claude', args)
        host = self.base / 'host-settings.json'
        saved = {'fallbackModel': ['outside-catalog'], 'permissions': {'deny': ['Bash(git push *)']},
                 'switchModelsOnFlag': True}
        write_json(host, saved)
        original = host.read_bytes()
        for flag in (['--settings', str(host)], ['--settings=' + json.dumps(saved)]):
            args = ['-p', '--model', 'fixed-claude', '--effort', 'high', *flag]
            transport.selected_profile(profile, 'claude', args)
            self.assertEqual(args[args.index('--fallback-model') + 1], 'fixed-claude')
            pinned = json.loads(args[args.index('--settings') + 1])
            self.assertFalse(pinned['switchModelsOnFlag'])
            self.assertEqual(pinned['fallbackModel'], saved['fallbackModel'])
            self.assertEqual(pinned['permissions'], saved['permissions'])
            self.assertEqual(host.read_bytes(), original)
        fake = Path(self.actual['claude'])
        fake.write_text(FAKE.replace("answer = json.dumps", """assert sys.argv[sys.argv.index('--fallback-model') + 1] == model
settings = json.loads(sys.argv[sys.argv.index('--settings') + 1])
assert settings['switchModelsOnFlag'] is False
answer = json.dumps"""))
        valid = self.invoke(trial, env, 'claude', 'claude-small', 'low')
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(json.loads(transport.parse_output('claude', valid.stdout)['answer'])['model'], 'claude-small')

    def test_routing_mutation_cannot_pass_the_gate(self):
        data = fixtures.ReportTests()
        data.setUp()
        self.addCleanup(data.doCleanups)
        data.spec['routing'] = transport.routing_profile(data.spec['models'], data.spec['efforts'])
        data.index['manifest_hash'] = data.ratings['manifest_hash'] = digest(data.spec)
        data.rows[0]['routing_valid'] = False
        data.save()
        result = bench_report.report(data.root)
        self.assertFalse(result['passed'])
        self.assertTrue(any('routing inputs changed' in message for message in result['missing']))

    def test_worker_retries_require_receipts_and_waiting_turns_are_not_retries(self):
        trial = self.base / 'accounted'
        (trial / 'calls').mkdir(parents=True)
        routing = transport.routing_profile(self.models, self.efforts, catalog(self.models, self.efforts))
        profiles = {p['id']: p for p in routing['policy']['profiles']}
        records = []
        for index, status in enumerate(('rejected', 'waiting', 'done'), 1):
            directory = trial / 'knowledge/runs/task' / str(index)
            directory.mkdir(parents=True)
            profile = profiles['codex-small' if index == 1 else 'fixed-codex']
            prompt = 'worker prompt ' + str(index)
            (directory / 'prompt.txt').write_text(prompt)
            result = {'status': 'waiting' if status == 'waiting' else 'done'}
            write_json(directory / 'result.json', result)
            assessment = {'assignment_id': 'unit', 'candidate_id': 'candidate-' + str(index),
                          'result_digest': digest(result), 'verdict': 'reject' if status == 'rejected' else 'accept'}
            write_json(directory / 'assessment.json', {**assessment, 'assessed_at': 'now'})
            record = {'task_id': 'task', 'location_id': 'location', 'request': {'id': 'unit'}, 'attempts': index,
                'directory': str(directory), 'profile': profile, 'routing_seal': routing['hash'],
                'escalated': index == 2, 'status': status, 'assessment': assessment,
                'assessment_ref': str(directory / 'assessment.json'), 'candidate_id': assessment['candidate_id'],
                'assessment_file_hash': file_hash(directory / 'assessment.json'),
                'result': str(directory / 'result.json'), 'result_digest': digest(result),
                'result_file_hash': file_hash(directory / 'result.json'),
                'history': [{'directory': prior['directory'], 'status': prior['status']} for prior in records]}
            records.append(record)
            write_json(directory / 'attempt.json', record)
            write_json(trial / 'calls' / (str(index) + '.json'), {'provider': 'codex', 'model': profile['model'],
                'effort': profile['effort'], 'status': 'completed', 'exit_code': 0, 'response_valid': True,
                'routing_hash': routing['hash'], 'tokens': 15, 'prompt_hash': hashlib.sha256(prompt.encode()).hexdigest(),
                'context': {'role': 'worker', 'task_id': 'task', 'location_id': 'location', 'assignment_id': 'unit',
                    'attempt': index, 'attempt_ref': str(directory / 'attempt.json'),
                    'routing': {'profile': profile, 'policy_seal': routing['hash'], 'escalated': index == 2}}})
        usage = transport.usage(trial)
        self.assertEqual(usage['provider_calls'], 3)
        self.assertEqual(usage['worker_outcomes']['retry_calls'], 1)
        self.assertEqual(usage['worker_outcomes']['rejected_attempts'], 1)
        self.assertEqual(usage['worker_outcomes']['escalated_calls'], 1)
        self.assertEqual(usage['call_outcomes']['failed_calls'], 0)
        # A later stale-source recovery can block an old attempt without erasing
        # its immutable rejection decision or the calls already paid for.
        records[0]['status'] = 'blocked'
        records[1]['escalated'] = False
        records[1]['history'][0]['status'] = 'blocked'
        for record in records:
            write_json(Path(record['directory']) / 'attempt.json', record)
        call = read_json(trial / 'calls/2.json')
        call['context']['routing']['escalated'] = False
        write_json(trial / 'calls/2.json', call)
        stale = transport.usage(trial)
        self.assertEqual(stale['worker_outcomes']['rejected_attempts'], 1)
        self.assertEqual(stale['worker_outcomes']['escalated_calls'], 0)
        Path(records[0]['result']).write_text('{}')
        changed = transport.usage(trial)
        self.assertEqual(changed['provider_calls'], 3)
        self.assertIsNone(changed['worker_outcomes']['retry_calls'])
        self.assertIsNone(changed['worker_outcomes']['rejected_attempts'])


if __name__ == '__main__':
    unittest.main()
