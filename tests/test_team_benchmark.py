"""Deterministic checks of the optional outcome suite, without model calls."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from loop_dev import benchmark as bench


SOURCE = Path(__file__).resolve().parents[1]
SUITE_PATH = SOURCE / 'benchmarks/team-suite.json'
GRADER = SOURCE / 'benchmarks/grade_team.py'


LEDGER = '''
import json
from pathlib import Path

def load_entries(path):
    rows = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(rows, list):
        raise ValueError('Expected an array')
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('category'), str)
                or not row['category'].strip() or type(row.get('amount_minor')) is not int):
            raise ValueError('Invalid expense row')
    return rows
'''

REPORT = '''
def summarize(entries, policy):
    totals = {}
    for row in entries:
        if row['category'] not in policy['excluded_categories']:
            totals[row['category']] = totals.get(row['category'], 0) + row['amount_minor']
    return {'currency': policy['currency'], 'totals': [
        {'category': key, 'amount_minor': totals[key]} for key in sorted(totals)]}
'''

STOCK = '''
import json
from pathlib import Path

def read_stock(path):
    stock = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(stock, dict) or any(
            not isinstance(sku, str) or not sku.strip() or type(quantity) is not int or quantity < 0
            for sku, quantity in stock.items()):
        raise ValueError('Invalid stock')
    return stock
'''

PLANNER = '''
def plan(stock, requests, policy):
    if not isinstance(requests, list):
        raise ValueError('Expected an order array')
    quantities = {}
    for row in requests:
        if (not isinstance(row, dict) or not isinstance(row.get('sku'), str) or not row['sku'].strip()
                or type(row.get('quantity')) is not int or row['quantity'] <= 0):
            raise ValueError('Invalid request')
        quantities[row['sku']] = quantities.get(row['sku'], 0) + row['quantity']
    allocations = []
    for sku, requested in sorted(quantities.items()):
        allocated = min(requested, stock.get(sku, 0), policy['max_per_item'])
        allocations.append({'sku': sku, 'requested': requested,
                            'allocated': allocated, 'shortage': requested - allocated})
    return {'allocations': allocations, 'complete': all(row['shortage'] == 0 for row in allocations)}
'''


class TeamOutcomeSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.suite, self.seal, self.assets = bench.load_suite(SUITE_PATH)
        self.cases = {case['id']: case for case in self.suite['cases']}
        self.counter = 0

    def workspace(self, case_id):
        self.counter += 1
        destination = self.base / str(self.counter)
        case = self.cases[case_id]
        shutil.copytree(SUITE_PATH.parent / case['fixture'], destination)
        for turn in case['turns']:
            for name, content in turn.get('before', {}).items():
                (destination / name).write_text(content, encoding='utf-8')
        return destination

    def grade(self, case_id, workspace):
        responses = self.base / 'responses.json'
        responses.write_text('[]\n')
        process = subprocess.run([sys.executable, '-I', str(GRADER), str(workspace), str(responses), case_id],
                                 capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(set(result), {c['id'] for c in self.cases[case_id]['criteria'] if c['mode'] == 'check'})
        for item in result.values():
            self.assertEqual(set(item), {'passed', 'evidence'})
            self.assertIs(type(item['passed']), bool)
            self.assertTrue(item['evidence'].strip())
        return result

    def assert_passes(self, case_id, workspace):
        result = self.grade(case_id, workspace)
        self.assertTrue(all(row['passed'] for row in result.values()), result)

    def correct_development(self, case_id, workspace):
        sources = ({'ledger.py': LEDGER, 'report.py': REPORT} if case_id == 'team-expense-flow'
                   else {'stock.py': STOCK, 'planner.py': PLANNER})
        for name, content in sources.items():
            (workspace / 'app' / name).write_text(textwrap.dedent(content), encoding='utf-8')

    def correct_document(self, case_id, workspace, stale=False):
        app = workspace / 'app'
        if stale:
            settings = json.loads((SUITE_PATH.parent / self.cases[case_id]['fixture'] / 'app/settings.json').read_text())
        else:
            settings = json.loads((app / 'settings.json').read_text())
        examples = []
        if case_id == 'team-retry-guide':
            facts = dict(settings, max_retries=settings['max_attempts'] - 1, performs_network_request=False)
            explanation = ('Python 3 calculates a retry decision; it sends no network request and cannot guarantee remote success. '
                           'The limit counts the first attempt, so additional retries are one fewer. '
                           'Before the limit, eligible statuses use a fixed delay; the boundary does not retry.')
            for status, attempt in [(503, 1), (503, settings['max_attempts']), (200, 1), (502, 1)]:
                retry = status in settings['retry_statuses'] and attempt < settings['max_attempts']
                examples.append({'argv': ['python3', 'client.py', '--settings', 'settings.json',
                                          '--status', str(status), '--attempt', str(attempt)],
                                 'expected': {'retry': retry, 'delay_seconds': settings['delay_seconds'] if retry else 0}})
        else:
            facts = dict(settings, writes_input=False, uses_network=False)
            explanation = ('Python 3 renders the configured column order, delimiter and optional header to stdout. '
                           'CSV quotes values containing the delimiter. Empty input emits only the optional header. '
                           'This preview leaves input files unchanged and does not use the network.')
            output = ({'demo.json': 'id,name\n7,윤;서\n8,"Ana, Jr"\n', 'empty.json': 'id,name\n'} if stale
                      else {'demo.json': '"윤;서";7\nAna, Jr;8\n', 'empty.json': ''})
            for name, value in output.items():
                examples.append({'argv': ['python3', 'export.py', '--input', name, '--settings', 'settings.json'],
                                 'expected_stdout': value})
        usage = {'facts': facts, 'examples': examples}
        (app / 'usage.json').write_text(json.dumps(usage, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        lines = ['# Command guide', '', explanation, '', '| Key | Value |', '| --- | --- |']
        lines.extend('| ' + key + ' | ' + json.dumps(value, ensure_ascii=False) + ' |' for key, value in facts.items())
        for item in examples:
            lines.extend(['', '```text', shlex.join(item['argv'])])
            if 'expected' in item:
                lines.append(json.dumps(item['expected']))
            else:
                lines.extend(item['expected_stdout'].splitlines())
            lines.append('```')
        (app / 'GUIDE.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        return usage

    def test_optional_suite_retains_default_policy_and_both_types_in_both_splits(self):
        default, _, _ = bench.load_suite(SOURCE / 'benchmarks/suite.json')
        self.assertEqual(self.suite['id'], 'team-outcomes-v1')
        self.assertEqual(self.suite['policy'], default['policy'])
        self.assertEqual(self.suite['max_calls'], default['max_calls'])
        self.assertEqual(self.suite['timeout_seconds'], default['timeout_seconds'])
        self.assertEqual(bench.goal_types(self.suite), ['development', 'document'])
        self.assertEqual({(c['kind'], c['split']) for c in self.cases.values()},
                         {('development', 'calibration'), ('development', 'holdout'),
                          ('document', 'calibration'), ('document', 'holdout')})
        for case in self.cases.values():
            self.assertEqual(len(case['turns']), 2)
            self.assertTrue(case['turns'][1]['before'])
            self.assertEqual(sum(c['weight'] for c in case['criteria']), 100)
            self.assertEqual(case['grader'], 'grade_team.py')
            self.assertNotIn('arms', case)
            for criterion in case['criteria']:
                if criterion['mode'] == 'check':
                    self.assertTrue(criterion['critical'])
            # The grade code is external, never a fixture visible to either arm.
            self.assertFalse(any('grade_team' in name for name in self.assets[case['id']]['fixture']))
        self.assertEqual(default['id'], 'starter-v1')

    def test_all_starter_outputs_fail_under_final_source_state(self):
        for case_id in self.cases:
            with self.subTest(case=case_id):
                result = self.grade(case_id, self.workspace(case_id))
                self.assertTrue(all(not row['passed'] for row in result.values()), result)

    def test_correct_development_outputs_pass(self):
        for case_id in ('team-expense-flow', 'team-stock-flow'):
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_development(case_id, workspace)
                self.assert_passes(case_id, workspace)

    def test_correct_documents_pass(self):
        for case_id in ('team-retry-guide', 'team-export-guide'):
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_document(case_id, workspace)
                self.assert_passes(case_id, workspace)

    def test_development_rejects_stale_hardcoded_policy(self):
        changes = {'team-expense-flow': ('report.py', "def summarize(entries, policy):",
                                        "def summarize(entries, policy):\n    policy = {'currency': 'USD', 'excluded_categories': []}"),
                   'team-stock-flow': ('planner.py', "policy['max_per_item']", '5')}
        for case_id, (name, old, new) in changes.items():
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_development(case_id, workspace)
                path = workspace / 'app' / name
                path.write_text(path.read_text().replace(old, new))
                result = self.grade(case_id, workspace)
                self.assertFalse(result['current_constraints']['passed'], result)
                self.assertFalse(result['shared_contract']['passed'], result)

    def test_development_rejects_incompatible_producer_consumer_interfaces(self):
        changes = {'team-expense-flow': ('report.py', "row['amount_minor']", "row['amount']"),
                   'team-stock-flow': ('stock.py', '    return stock', '    return list(stock.items())')}
        for case_id, (name, old, new) in changes.items():
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_development(case_id, workspace)
                path = workspace / 'app' / name
                path.write_text(path.read_text().replace(old, new))
                result = self.grade(case_id, workspace)
                self.assertTrue(all(not row['passed'] for row in result.values()), result)

    def test_development_cannot_drop_original_validation_after_feedback(self):
        changes = {'team-expense-flow': ('ledger.py', "type(row.get('amount_minor')) is not int",
                                        "not isinstance(row.get('amount_minor'), int)"),
                   'team-stock-flow': ('planner.py', "type(row.get('quantity')) is not int",
                                      "not isinstance(row.get('quantity'), int)")}
        for case_id, (name, old, new) in changes.items():
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_development(case_id, workspace)
                path = workspace / 'app' / name
                path.write_text(path.read_text().replace(old, new))
                result = self.grade(case_id, workspace)
                self.assertFalse(result['shared_contract']['passed'], result)
                self.assertFalse(result['current_constraints']['passed'], result)

    def test_development_result_preserves_declared_boolean_type(self):
        case_id = 'team-stock-flow'
        workspace = self.workspace(case_id)
        self.correct_development(case_id, workspace)
        path = workspace / 'app/planner.py'
        path.write_text(path.read_text().replace("all(row['shortage'] == 0 for row in allocations)",
                                                "int(all(row['shortage'] == 0 for row in allocations))"))
        result = self.grade(case_id, workspace)
        self.assertTrue(all(not row['passed'] for row in result.values()), result)

    def test_stale_documents_fail_facts_and_executable_examples(self):
        for case_id in ('team-retry-guide', 'team-export-guide'):
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_document(case_id, workspace, stale=True)
                result = self.grade(case_id, workspace)
                self.assertTrue(all(not row['passed'] for row in result.values()), result)

    def test_correct_usage_cannot_mask_a_contradictory_guide_fact(self):
        changes = {'team-retry-guide': ('| max_attempts | 4 |', '| max_attempts | 3 |'),
                   'team-export-guide': ('| include_header | false |', '| include_header | true |')}
        for case_id, (old, new) in changes.items():
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_document(case_id, workspace)
                path = workspace / 'app/GUIDE.md'
                path.write_text(path.read_text().replace(old, new))
                result = self.grade(case_id, workspace)
                self.assertFalse(result['current_facts']['passed'], result)
                self.assertTrue(result['executable_examples']['passed'], result)

    def test_correct_usage_cannot_mask_a_contradictory_guide_output(self):
        changes = {'team-retry-guide': ('"delay_seconds": 5', '"delay_seconds": 2'),
                   'team-export-guide': ('"윤;서";7', '7,윤;서')}
        for case_id, (old, new) in changes.items():
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                self.correct_document(case_id, workspace)
                path = workspace / 'app/GUIDE.md'
                path.write_text(path.read_text().replace(old, new), encoding='utf-8')
                result = self.grade(case_id, workspace)
                self.assertTrue(result['current_facts']['passed'], result)
                self.assertFalse(result['executable_examples']['passed'], result)

    def test_duplicate_guide_facts_do_not_hide_conflicting_versions(self):
        for case_id in ('team-retry-guide', 'team-export-guide'):
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                usage = self.correct_document(case_id, workspace)
                key, value = next(iter(usage['facts'].items()))
                path = workspace / 'app/GUIDE.md'
                with path.open('a', encoding='utf-8') as stream:
                    stream.write('| ' + key + ' | ' + json.dumps(value) + ' |\n')
                self.assertFalse(self.grade(case_id, workspace)['current_facts']['passed'])

    def test_documented_json_output_preserves_declared_boolean_type(self):
        case_id = 'team-retry-guide'
        workspace = self.workspace(case_id)
        usage = self.correct_document(case_id, workspace)
        for item in usage['examples']:
            item['expected']['retry'] = int(item['expected']['retry'])
        (workspace / 'app/usage.json').write_text(json.dumps(usage))
        path = workspace / 'app/GUIDE.md'
        path.write_text(path.read_text().replace('"retry": true', '"retry": 1').replace('"retry": false', '"retry": 0'))
        self.assertFalse(self.grade(case_id, workspace)['executable_examples']['passed'])

    def test_later_configuration_remains_user_owned_for_both_goal_types(self):
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                workspace = self.workspace(case_id)
                if case['kind'] == 'development':
                    self.correct_development(case_id, workspace)
                    key = 'current_constraints'
                else:
                    self.correct_document(case_id, workspace)
                    key = 'current_facts'
                changed_path = next(iter(case['turns'][1]['before']))
                path = workspace / changed_path
                # Even an equivalent JSON rewrite breaks the explicit no-edit request.
                path.write_text(json.dumps(json.loads(path.read_text()), indent=2) + '\n')
                self.assertFalse(self.grade(case_id, workspace)[key]['passed'])

    def test_grader_protocol_integrates_with_runner_on_an_artifact_copy(self):
        case_id = 'team-export-guide'
        workspace = self.workspace(case_id)
        self.correct_document(case_id, workspace)
        trial = self.base / 'trial'
        trial.mkdir()
        (trial / 'responses.json').write_text('[]\n')
        before = bench.directory_files(workspace)
        result = bench.grade(trial, workspace, self.cases[case_id], GRADER)
        self.assertTrue(all(item['passed'] for item in result.values()), result)
        self.assertEqual(bench.directory_files(workspace), before)
        self.assertTrue((trial / 'grading/workspace/app/GUIDE.md').is_file())


if __name__ == '__main__':
    unittest.main()
