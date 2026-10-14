"""Compare successive, sealed on/off experiments without selecting Git revisions."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import fcntl
from pathlib import Path
import statistics

from . import benchmark
from .storage import LoopError, digest, file_hash, read_json, write_json

PROFILE_FIELDS = ('version', 'arms', 'suite_hash', 'models', 'efforts', 'evaluation',
                  'providers', 'repeats', 'seed', 'environment', 'profile', 'versions', 'limits')
SEALED_FILES = ('manifest.json', 'index.json', 'judgments.json', 'check.json', 'evolution.json')


def location(candidate, history=None):
    return Path(history).resolve() if history else (
        candidate.parent / 'loop-dev-benchmarks/history' / digest(str(candidate))[:16])


def read_history(history):
    path = history / 'baselines.json'
    if not path.exists():
        return {'version': 1, 'baselines': {}}
    value = read_json(path)
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('baselines'), dict):
        raise LoopError('Invalid benchmark history; preserve it and inspect baselines.json')
    for key, saved in value['baselines'].items():
        if (not isinstance(key, str) or len(key) != 64 or not isinstance(saved, dict)
                or any(not isinstance(saved.get(k), str) or not saved[k] for k in ('run', 'source_hash', 'manifest_hash'))
                or not isinstance(saved.get('evidence'), dict) or set(saved['evidence']) != set(SEALED_FILES)):
            raise LoopError('Invalid before-run reference in benchmark history')
    return value


def profile(spec):
    if any(spec.get(k) is None for k in PROFILE_FIELDS):
        raise LoopError('Before/after comparison needs the full frozen experiment profile')
    return {k: spec[k] for k in PROFILE_FIELDS}


def entry(root, spec):
    return {'run': str(root), 'source_hash': spec['source']['candidate']['hash'],
            'manifest_hash': digest(spec),
            'evidence': {name: file_hash(root / name) for name in SEALED_FILES}}


def previous(saved, expected_profile):
    from .bench_report import report
    if (not isinstance(saved, dict) or not isinstance(saved.get('run'), str)
            or not Path(saved['run']).is_absolute() or not isinstance(saved.get('evidence'), dict)
            or set(saved['evidence']) != set(SEALED_FILES)):
        raise LoopError('Invalid before-run reference in benchmark history')
    root = Path(saved['run'])
    if any(not seal or seal == 'deleted' or file_hash(root / name) != seal for name, seal in saved['evidence'].items()):
        raise LoopError('Before-run evidence changed or is missing; retain the existing baseline')
    _, spec, _, _ = benchmark.read_run(root)
    if digest(spec) != saved['manifest_hash'] or profile(spec) != expected_profile:
        raise LoopError('Before-run experiment settings do not match the after run')
    if (spec['source']['candidate']['hash'] != saved['source_hash']
            or benchmark.source_snapshot(root / 'sources/candidate')['hash'] != saved['source_hash']):
        raise LoopError('Before-run harness snapshot changed')
    check = read_json(root / 'check.json')
    if (check.get('phase') != 'completed' or not check.get('passed') or check.get('failure')
            or check.get('manifest_hash') != saved['manifest_hash']):
        raise LoopError('Before run did not complete the full quality gate')
    value = report(root, persist=False)
    if not value['passed']:
        raise LoopError('Before run lacks passing live evidence')
    return value


def compare(spec, before, after):
    """Keep case/provider/repeat pairs; a gain elsewhere cannot hide a loss."""
    from .bench_report import interval
    key = lambda s: (s['case'], s['provider'], s['repeat'], s['arm'])
    old, new = ({key(s): s for s in v['samples']} for v in (before, after))
    if old.keys() != new.keys():
        raise LoopError('Before/after samples do not cover the same cases and repetitions')
    policy = spec['suite']['policy']
    cases = {c['id']: c for c in spec['suite']['cases']}
    comparisons, regressions = [], []
    for (case, provider, repeat, arm), a in new.items():
        if arm != 'candidate':
            continue
        b = old[(case, provider, repeat, arm)]
        plain_key = (case, provider, repeat, 'plain')
        quality_delta = a['quality'] - b['quality']
        plain_delta = new[plain_key]['quality'] - old[plain_key]['quality']
        row = {'case': case, 'provider': provider, 'repeat': repeat, 'goal_type': a['goal_type'],
               'sample': a['id'], 'before_sample': b['id'], 'control': 'before',
               'before_quality': b['quality'], 'after_quality': a['quality'],
               'quality_delta': quality_delta, 'plain_delta': plain_delta,
               'harness_gain_delta': quality_delta - plain_delta,
               'before_seconds': b['seconds'], 'after_seconds': a['seconds'],
               'before_calls': b['usage']['provider_calls'], 'after_calls': a['usage']['provider_calls'],
               'before_questions': b['extra_user_requests'], 'after_questions': a['extra_user_requests']}
        comparisons.append(row)
        def add(criterion, reason, evidence, rules):
            regressions.append({**row, 'criterion': criterion, 'reason': reason,
                                'evidence': evidence, 'rule_paths': rules})
        for criterion in cases[case]['criteria']:
            name = criterion['id']
            if a['criteria'][name]['score'] < b['criteria'][name]['score']:
                add(name, 'Before/after criterion regression',
                    a['criteria'][name]['evidence'] + b['criteria'][name]['evidence'],
                    criterion.get('rule_paths', ['rules/quality.md']))
        if a['seconds'] > b['seconds'] * policy['max_duration_ratio'] + policy['duration_slack_seconds']:
            add('elapsed_time', 'Before/after elapsed time exceeds frozen limit',
                [f"{a['seconds']} versus {b['seconds']} seconds"], ['rules/work.md', 'rules/context.md'])
        if a['extra_user_requests'] > b['extra_user_requests']:
            add('user_requests', 'Before/after increase in user input',
                [f"{a['extra_user_requests']} versus {b['extra_user_requests']}"], ['rules/context.md', 'rules/work.md'])
    effects = {name: interval([r[column] for r in comparisons]) for name, column in
               (('candidate', 'quality_delta'), ('plain', 'plain_delta'), ('harness_gain', 'harness_gain_delta'))}
    groups = defaultdict(list)
    for row in comparisons:
        groups[(row['goal_type'], row['provider'])].append(row)
    outcomes = []
    for (kind, provider), rows in sorted(groups.items()):
        outcomes.append({'goal_type': kind, 'provider': provider, 'pairs': len(rows),
            'regression': any(r['goal_type'] == kind and r['provider'] == provider for r in regressions),
            **{field: round(statistics.mean(r[field] for r in rows), 3) for field in
               ('before_quality', 'after_quality', 'quality_delta', 'plain_delta', 'harness_gain_delta',
                'before_seconds', 'after_seconds', 'before_calls', 'after_calls', 'before_questions', 'after_questions')}})
    return {'comparisons': comparisons, 'regressions': regressions, 'effects': effects, 'type_outcomes': outcomes}


def evaluate(root, spec, value, record):
    """Recompute on gate/report; generated verdict text is never trusted as evidence."""
    if (record.get('version') != 1 or record.get('manifest_hash') != digest(spec)
            or record.get('profile_hash') != digest(profile(spec))):
        raise LoopError('Before/after record does not match the frozen experiment')
    result = {**record, 'effects': {}, 'comparisons': [], 'regressions': [], 'type_outcomes': [],
              'missing': list(record.get('blockers', [])), 'verdict': 'inconclusive', 'passed': False}
    if value['missing']:
        result['missing'].append('Current experiment lacks complete live evidence for before/after comparison')
        return result
    if benchmark.source_snapshot(root / 'sources/candidate')['hash'] != spec['source']['candidate']['hash']:
        raise LoopError('After-run harness snapshot changed')
    before = record['before']
    if before is None:
        verdict = 'baseline_created' if value['passed'] else 'inconclusive'
        if not value['passed']:
            result['missing'].append('The first on/off experiment must pass before establishing a baseline')
    else:
        result.update(compare(spec, previous(before, profile(spec)), value))
        changed = before['source_hash'] != spec['source']['candidate']['hash']
        improved = (changed and result['effects']['candidate']['low'] > 0
                    and result['effects']['harness_gain']['low'] > 0)
        verdict = 'regression' if result['regressions'] else 'improved' if improved else 'non_regressing'
    result['verdict'] = 'inconclusive' if result['missing'] else verdict
    result['passed'] = value['passed'] and result['verdict'] in ('baseline_created', 'improved', 'non_regressing')
    return result


def attach(value, evolution):
    value['on_off_verdict'] = value['verdict']
    value['evolution'] = evolution
    value['missing'].extend(evolution['missing'])
    value['regressions'].extend(evolution['regressions'])
    if value['regressions']:
        value['verdict'] = 'regression'
    elif value['missing']:
        value['verdict'] = 'inconclusive'
    elif evolution['before'] is not None:
        value['verdict'] = evolution['verdict']
    value['passed'] = value['passed'] and evolution['passed']
    return value


@contextmanager
def record(root, spec, value, history, started):
    """Freeze the predecessor before publishing; lock only the short history update."""
    key = digest(profile(spec))
    before = started['baselines'].get(key)
    history.mkdir(parents=True, exist_ok=True)
    with (history / 'lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise LoopError('Another benchmark is updating this history; retain the old baseline') from exc
        current = read_history(history)
        blockers = ([] if current['baselines'].get(key) == before else
                    ['Baseline changed during this experiment; no automatic replacement'])
        frozen = {'version': 1, 'manifest_hash': digest(spec), 'profile_hash': key,
                  'history': str(history), 'before': before, 'blockers': blockers}
        evolution = evaluate(root, spec, value, frozen)
        promote = evolution['passed'] and (before is None or before['source_hash'] != spec['source']['candidate']['hash'])
        evolution['baseline_updated'] = promote
        write_json(root / 'evolution.json', {**frozen, 'baseline_updated': promote})
        # The caller writes the final check while the predecessor is still current.
        yield attach(value, evolution)
        if promote:
            current['baselines'][key] = entry(root, spec)
            write_json(history / 'baselines.json', current)
