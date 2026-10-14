"""External outcome checks for team-suite.json; no points for execution method.

Runs on the benchmark's separate artifact copy. Assertions deliberately cover the
original public contracts as well as the final source changes. Prose accuracy and
usability remain subject to the suite's blinded, anchored rubric.
"""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


ROOT = Path(sys.argv[1])
APP = ROOT / 'app'
CASE = sys.argv[3]
RESULTS = {}
EXPENSE_POLICY = {'currency': 'EUR', 'excluded_categories': ['internal']}
STOCK_POLICY = {'max_per_item': 2}
RETRY_SETTINGS = {'max_attempts': 4, 'delay_seconds': 5, 'retry_statuses': [429, 502, 503]}
EXPORT_SETTINGS = {'delimiter': ';', 'columns': ['name', 'id'], 'include_header': False}


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def same_json(actual, expected):
    # Python treats True == 1 and False == 0; public JSON contracts do not.
    return json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def criterion(key, test):
    try:
        # A subject module's debug printing must not corrupt the grader protocol.
        with contextlib.redirect_stdout(io.StringIO()):
            test()
        RESULTS[key] = {'passed': True, 'evidence': test.__doc__}
    except Exception as exc:
        RESULTS[key] = {'passed': False, 'evidence': type(exc).__name__ + ': ' + (str(exc) or key)[:800]}


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def unchanged_configuration(name, expected):
    # These files change between user turns, so the runner cannot freeze their
    # initial bytes. Compare against the scripted final bytes externally instead.
    check((APP / name).read_bytes() == (json.dumps(expected) + '\n').encode('utf-8'),
          'The final user-owned configuration was changed or is stale')


def module(name):
    sys.path.insert(0, str(APP))
    spec = importlib.util.spec_from_file_location(name, APP / (name + '.py'))
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def invalid(call, message):
    try:
        call()
    except ValueError:
        return
    raise AssertionError(message)


def run(argv):
    return subprocess.run([sys.executable, *argv[1:]], cwd=APP, capture_output=True,
                          text=True, encoding='utf-8', timeout=5)


def cli_json(argv, expected, paths):
    original = {str(path): Path(path).read_bytes() for path in paths}
    result = run(argv)
    check(result.returncode == 0, 'Command failed: ' + result.stderr[:300])
    check(same_json(json.loads(result.stdout), expected), 'CLI output disagrees with the public contract/current policy')
    check(all(Path(path).read_bytes() == data for path, data in original.items()), 'CLI changed an input file')


def cli_invalid(argv, paths):
    original = {str(path): Path(path).read_bytes() for path in paths}
    result = run(argv)
    check(result.returncode != 0, 'CLI accepted invalid input')
    check(all(Path(path).read_bytes() == data for path, data in original.items()), 'Invalid input was overwritten')


def expense_expected(entries, policy):
    totals = {}
    for row in entries:
        if row['category'] not in policy['excluded_categories']:
            totals[row['category']] = totals.get(row['category'], 0) + row['amount_minor']
    return {'currency': policy['currency'], 'totals': [
        {'category': category, 'amount_minor': amount} for category, amount in sorted(totals.items())]}


def expense_contract():
    """Reader and summary preserve row order, signed amounts, Unicode, validation and input objects."""
    reader, summary = module('ledger'), module('report')
    entries = [{'category': '여행', 'amount_minor': 1200}, {'category': 'food', 'amount_minor': 75},
               {'category': '여행', 'amount_minor': -200}, {'category': ' zero ', 'amount_minor': 0},
               {'category': 'internal', 'amount_minor': 999}]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'rows.json'
        save_json(path, entries)
        original = path.read_bytes()
        loaded = reader.load_entries(path)
        check(loaded == entries, 'Reader changed row values or order')
        for policy in (EXPENSE_POLICY, {'currency': 'JPY', 'excluded_categories': ['food', ' zero ']}):
            saved = copy.deepcopy((loaded, policy))
            check(same_json(summary.summarize(loaded, policy), expense_expected(entries, policy)), 'Summary interface or aggregation differs')
            check((loaded, policy) == saved, 'Summary mutated rows or policy')
        check(path.read_bytes() == original, 'Reader changed its input file')
        save_json(path, [])
        check(reader.load_entries(path) == [], 'Reader rejected an empty list')
        check(same_json(summary.summarize([], EXPENSE_POLICY), {'currency': 'EUR', 'totals': []}), 'Empty summary differs')
        bad = [{}, None, [None], [{'category': '', 'amount_minor': 1}],
               [{'category': '\t', 'amount_minor': 1}], [{'category': 3, 'amount_minor': 1}],
               [{'category': 'food', 'amount_minor': True}]]
        bad.extend([[{'category': 'food', 'amount_minor': x}] for x in (1.5, '2', None)])
        bad.append([{'category': 'food'}])
        for data in bad:
            save_json(path, data)
            original = path.read_bytes()
            invalid(lambda: reader.load_entries(path), 'Reader accepted an invalid expense value')
            check(path.read_bytes() == original, 'Reader overwrote invalid expense data')
        path.write_bytes(b'{broken')
        invalid(lambda: reader.load_entries(path), 'Reader accepted corrupt JSON')
        check(path.read_bytes() == b'{broken', 'Reader overwrote corrupt JSON')


def expense_current():
    """CLI applies the final EUR/exclusion policy and alternative policies; invalid inputs remain unchanged."""
    unchanged_configuration('policy.json', EXPENSE_POLICY)
    with tempfile.TemporaryDirectory() as directory:
        entries_path, policy_path = Path(directory) / 'rows.json', Path(directory) / 'policy.json'
        rows = [{'category': 'internal', 'amount_minor': 300}, {'category': 'café', 'amount_minor': 10},
                {'category': 'café', 'amount_minor': -2}, {'category': '식사', 'amount_minor': 17}]
        save_json(entries_path, rows)
        for policy, source in ((EXPENSE_POLICY, APP / 'policy.json'),
                               ({'currency': 'GBP', 'excluded_categories': ['café']}, policy_path)):
            if source == policy_path:
                save_json(source, policy)
            argv = ['python3', 'expenses.py', '--entries', str(entries_path), '--policy', str(source)]
            cli_json(argv, expense_expected(rows, policy), [entries_path, source])
        save_json(entries_path, [{'category': 'ok', 'amount_minor': True}])
        cli_invalid(argv, [entries_path, source])
        entries_path.write_bytes(b'{broken')
        cli_invalid(argv, [entries_path, source])


def stock_expected(stock, requests, policy):
    quantities = {}
    for item in requests:
        quantities[item['sku']] = quantities.get(item['sku'], 0) + item['quantity']
    rows = []
    for sku, requested in sorted(quantities.items()):
        allocated = min(requested, stock.get(sku, 0), policy['max_per_item'])
        rows.append({'sku': sku, 'requested': requested, 'allocated': allocated, 'shortage': requested - allocated})
    return {'allocations': rows, 'complete': all(row['shortage'] == 0 for row in rows)}


def stock_contract():
    """Reader and planner validate quantities, combine exact SKUs, preserve Unicode and leave all inputs unchanged."""
    reader, planner = module('stock'), module('planner')
    stock = {'책': 8, 'A': 1, 'empty': 0}
    requests = [{'sku': '책', 'quantity': 2}, {'sku': 'A', 'quantity': 3},
                {'sku': '책', 'quantity': 2}, {'sku': 'missing', 'quantity': 1}]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'stock.json'
        save_json(path, stock)
        original = path.read_bytes()
        loaded = reader.read_stock(path)
        check(loaded == stock, 'Stock reader changed the inventory mapping')
        for policy in (STOCK_POLICY, {'max_per_item': 10}):
            saved = copy.deepcopy((loaded, requests, policy))
            check(same_json(planner.plan(loaded, requests, policy), stock_expected(stock, requests, policy)), 'Planner contract differs')
            check((loaded, requests, policy) == saved, 'Planner mutated an input object')
        check(path.read_bytes() == original, 'Stock reader changed its input file')
        save_json(path, {})
        check(reader.read_stock(path) == {}, 'Reader rejected empty inventory')
        check(same_json(planner.plan({}, [], STOCK_POLICY), {'allocations': [], 'complete': True}), 'Empty plan differs')
        small = [{'sku': 'A', 'quantity': 1}]
        check(same_json(planner.plan(stock, small, STOCK_POLICY), stock_expected(stock, small, STOCK_POLICY)),
              'A fully available nonempty order is not complete')
        for data in ([], None, {'': 1}, {' ': 1}, {'A': -1}, {'A': True}, {'A': 1.5}, {'A': '1'}):
            save_json(path, data)
            original = path.read_bytes()
            invalid(lambda: reader.read_stock(path), 'Reader accepted invalid inventory')
            check(path.read_bytes() == original, 'Reader overwrote invalid inventory')
        path.write_bytes(b'{broken')
        invalid(lambda: reader.read_stock(path), 'Reader accepted corrupt JSON')
        check(path.read_bytes() == b'{broken', 'Reader overwrote corrupt JSON')
    for data in ({}, None, [None], [{'sku': '', 'quantity': 1}], [{'sku': 4, 'quantity': 1}],
                 [{'sku': 'A'}], [{'sku': 'A', 'quantity': 0}], [{'sku': 'A', 'quantity': -1}],
                 [{'sku': 'A', 'quantity': True}], [{'sku': 'A', 'quantity': 1.5}]):
        saved = copy.deepcopy(data)
        invalid(lambda: planner.plan(stock, data, STOCK_POLICY), 'Planner accepted invalid requests')
        check(data == saved, 'Planner mutated an invalid request')


def stock_current():
    """CLI applies the final per-SKU cap after combining requests, accepts alternative caps and preserves inventory."""
    unchanged_configuration('policy.json', STOCK_POLICY)
    with tempfile.TemporaryDirectory() as directory:
        stock_path, order_path, policy_path = [Path(directory) / p for p in ('stock.json', 'order.json', 'policy.json')]
        stock = {'A': 9, 'B': 1, '책': 3}
        requests = [{'sku': 'A', 'quantity': 2}, {'sku': 'A', 'quantity': 3},
                    {'sku': 'B', 'quantity': 2}, {'sku': '책', 'quantity': 1}, {'sku': 'missing', 'quantity': 2}]
        save_json(stock_path, stock)
        save_json(order_path, requests)
        for policy, source in ((STOCK_POLICY, APP / 'policy.json'), ({'max_per_item': 7}, policy_path)):
            if source == policy_path:
                save_json(source, policy)
            argv = ['python3', 'shipping.py', '--stock', str(stock_path), '--order', str(order_path), '--policy', str(source)]
            cli_json(argv, stock_expected(stock, requests, policy), [stock_path, order_path, source])
        save_json(order_path, [{'sku': 'A', 'quantity': True}])
        cli_invalid(argv, [stock_path, order_path, source])
        save_json(order_path, requests)
        stock_path.write_bytes(b'{broken')
        cli_invalid(argv, [stock_path, order_path, source])


def guide_facts(expected):
    usage = read_json(APP / 'usage.json')
    # JSON equality distinguishes booleans from integers in declared fact types.
    check(same_json(usage['facts'], expected),
          'Recorded facts do not match current source semantics and types')
    found = {}
    for line in (APP / 'GUIDE.md').read_text(encoding='utf-8').splitlines():
        cells = [cell.strip().strip('`') for cell in line.strip().strip('|').split('|')]
        if len(cells) == 2 and cells[0] in expected:
            check(cells[0] not in found, 'Guide repeats a fact, allowing contradictory alternatives')
            found[cells[0]] = json.loads(cells[1])
    check(same_json(found, expected),
          'Guide fact table is incomplete, stale or contradicts usage.json')


def retry_facts():
    """Guide and usage facts agree on four total attempts, three retries, five seconds, statuses and no network call."""
    unchanged_configuration('settings.json', RETRY_SETTINGS)
    guide_facts(dict(RETRY_SETTINGS, max_retries=3, performs_network_request=False))


def export_facts():
    """Guide and usage facts agree on semicolon, name/id order, no header and no file or network side effects."""
    unchanged_configuration('settings.json', EXPORT_SETTINGS)
    guide_facts(dict(EXPORT_SETTINGS, writes_input=False, uses_network=False))


def guide_examples():
    """Extract complete command/output pairs from the requested fenced text blocks."""
    lines = (APP / 'GUIDE.md').read_text(encoding='utf-8').splitlines()
    result = []
    in_block = False
    block = []
    for line in lines:
        if line.strip().startswith('```'):
            if in_block and block and block[0].strip().startswith('python3 '):
                result.append((shlex.split(block[0]), '\n'.join(block[1:]) + ('\n' if len(block) > 1 else '')))
            in_block, block = not in_block, []
        elif in_block:
            block.append(line)
    check(not in_block, 'Guide has an unclosed code fence')
    return result


def arguments(argv, command, flags):
    check(isinstance(argv, list) and all(isinstance(x, str) for x in argv), 'Example argv must be a string list')
    check(argv[:2] == ['python3', command] and len(argv) == 2 + 2 * len(flags), 'Unexpected example command shape')
    options = dict(zip(argv[2::2], argv[3::2]))
    check(set(options) == set(flags) and len(options) == len(flags), 'Example has missing, repeated or unknown options')
    check(options['--settings'] == 'settings.json', 'Example does not use the current settings file')
    return options


def retry_examples():
    """Four literal documented commands match recorded JSON, the new boundary and the changed 502 retry behavior."""
    examples = read_json(APP / 'usage.json')['examples']
    documented = guide_examples()
    check(len(examples) == len(documented) == 4, 'Expected four documented retry examples')
    seen = set()
    for item, (argv, output) in zip(examples, documented):
        check(item['argv'] == argv, 'Guide command differs from usage.json')
        options = arguments(argv, 'client.py', ['--settings', '--status', '--attempt'])
        pair = (int(options['--status']), int(options['--attempt']))
        check(pair not in seen, 'Repeated retry example')
        seen.add(pair)
        retry = pair[0] in RETRY_SETTINGS['retry_statuses'] and 1 <= pair[1] < RETRY_SETTINGS['max_attempts']
        expected = {'retry': retry, 'delay_seconds': 5 if retry else 0}
        check(same_json(item['expected'], expected) and same_json(json.loads(output), expected),
              'Guide or recorded retry output is stale, contradictory or has incorrect types')
        cli_json(argv, expected, [APP / 'settings.json'])
    check(seen == {(503, 1), (503, 4), (200, 1), (502, 1)}, 'Missing the required current retry scenarios')


def export_examples():
    """Both literal commands match recorded CSV stdout, including Unicode quoting and empty output; samples remain unchanged."""
    examples = read_json(APP / 'usage.json')['examples']
    documented = guide_examples()
    check(len(examples) == len(documented) == 2, 'Expected two documented export examples')
    expected = {'demo.json': '"윤;서";7\nAna, Jr;8\n', 'empty.json': ''}
    seen = set()
    for item, (argv, output) in zip(examples, documented):
        check(item['argv'] == argv, 'Guide command differs from usage.json')
        options = arguments(argv, 'export.py', ['--input', '--settings'])
        name = options['--input']
        check(name in expected and name not in seen, 'Missing, repeated or unexpected sample input')
        seen.add(name)
        check(item['expected_stdout'] == output == expected[name], 'Guide or recorded CSV output is stale or contradictory')
        paths = [APP / 'settings.json', APP / 'demo.json', APP / 'empty.json']
        original = [path.read_bytes() for path in paths]
        result = run(argv)
        check(result.returncode == 0 and result.stdout == expected[name], 'Documented export does not produce its promised stdout')
        check([path.read_bytes() for path in paths] == original, 'Export command changed configuration or samples')
    check(seen == set(expected), 'Both regular and empty sample inputs are required')


for key, test in {
    'team-expense-flow': [('shared_contract', expense_contract), ('current_constraints', expense_current)],
    'team-stock-flow': [('shared_contract', stock_contract), ('current_constraints', stock_current)],
    'team-retry-guide': [('current_facts', retry_facts), ('executable_examples', retry_examples)],
    'team-export-guide': [('current_facts', export_facts), ('executable_examples', export_examples)],
}[CASE]:
    criterion(key, test)
print(json.dumps(RESULTS, ensure_ascii=False))
