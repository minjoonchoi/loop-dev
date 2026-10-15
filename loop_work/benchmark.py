"""Paired, reproducible product-outcome benchmarks; no quality claims from unit mocks."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

from . import bench_transport
from .contract import identifier
from .process import execute
from .storage import LoopError, digest, file_hash, inside, now, read_json, write_json, write_text

ARMS = ('plain', 'candidate')
KINDS = ('development', 'document', 'explanation', 'research', 'review', 'planning', 'session')
SOURCE_PATHS = ('scripts/loop.py', 'scripts/install.py', 'scripts/benchmark.py', 'loop_work/', 'rules/', 'templates/', 'skills/loop-work/')


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise LoopError(f'{label} must be an integer in {low}..{high}')


def number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise LoopError(f'{label} must be a finite number in {low}..{high}')


def relative(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts or value != Path(value).as_posix():
        raise LoopError('Use a canonical relative benchmark path')
    return value


def directory_files(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if '.git' in path.relative_to(root).parts or '__pycache__' in path.parts:
            continue
        if path.is_symlink():
            raise LoopError(f'Benchmark inputs/artifacts cannot contain symlinks: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = file_hash(path)
    return result


def load_suite(path):
    path = Path(path).resolve()
    value = read_json(path)
    if not isinstance(value, dict) or value.get('version') != 1 or not identifier(value.get('id')) or not isinstance(value.get('cases'), list) or not value['cases']:
        raise LoopError('Benchmark suite needs version 1, id and nonempty cases')
    policy = value.get('policy', {})
    if not isinstance(policy, dict):
        raise LoopError('Benchmark policy must be an object')
    integer(policy.get('min_repeats'), 3, 100, 'min_repeats')
    for name, low, high in (('quality_margin', 0, 10), ('min_quality', 0, 100), ('max_duration_ratio', 1, 100),
                            ('duration_slack_seconds', 0, 600)):
        number(policy.get(name), low, high, name)
    integer(value.get('max_calls'), 1, 500, 'max_calls')
    integer(value.get('timeout_seconds'), 1, 14400, 'timeout_seconds')
    ids, seals = [], {}
    for case in value['cases']:
        if not isinstance(case, dict) or not identifier(case.get('id')) or case.get('kind') not in KINDS or case.get('split') not in ('calibration', 'holdout'):
            raise LoopError('Case needs id, kind and calibration/holdout split')
        ids.append(case['id'])
        fixture = inside(path.parent, relative(case['fixture']))
        if not fixture.is_dir():
            raise LoopError('Case fixture directory is missing')
        file_map = directory_files(fixture)
        if any('.loop' in Path(p).parts or p.startswith(('.agents/skills/loop-work/', '.claude/skills/loop-work/',
                                                           '.agents/skills/loop-dev/', '.claude/skills/loop-dev/')) for p in file_map):
            raise LoopError('Fixtures must not contain loop-work installation or state')
        roots = case.get('roots')
        if not isinstance(roots, list) or not roots or not all(isinstance(x, str) for x in roots) or len(set(roots)) != len(roots):
            raise LoopError('Case needs distinct repository roots')
        for root in roots:
            relative(root)
            if not inside(fixture, root).is_dir():
                raise LoopError('Case repository root is missing')
        if any(a != b and Path(b).is_relative_to(Path(a)) for a in roots for b in roots):
            raise LoopError('Benchmark roots cannot overlap')
        turns = case.get('turns')
        if not isinstance(turns, list) or not 1 <= len(turns) <= 20:
            raise LoopError('Case needs 1..20 turns')
        for key in ('artifacts', 'protected', 'context'):
            items = case.get(key, [])
            if not isinstance(items, list) or not all(isinstance(x, str) for x in items) or len(items) != len(set(items)):
                raise LoopError('Artifacts, protected files and context must be distinct path lists')
        for turn in turns:
            if not isinstance(turn, dict) or turn.get('root') not in roots or not isinstance(turn.get('prompt'), str) or not turn['prompt'].strip():
                raise LoopError('Each turn needs a declared root and prompt')
            if not isinstance(turn.get('before', {}), dict):
                raise LoopError('Scripted source changes must be a file-content map')
            for name, content in turn.get('before', {}).items():
                relative(name)
                if (name not in file_map or name in case.get('protected', []) or not isinstance(content, str)
                        or any(part.startswith('.') for part in Path(name).parts)):
                    raise LoopError('Scripted changes must name existing, unprotected fixture source files')
        for name in case.get('artifacts', []):
            relative(name)
        for name in case.get('protected', []):
            if relative(name) not in file_map:
                raise LoopError('Protected fixture file is missing')
        for name in case.get('context', []):
            if relative(name) not in file_map or (fixture / name).stat().st_size > 100000:
                raise LoopError('Rubric context must identify bounded fixture source files')
        criteria = case.get('criteria')
        if not isinstance(criteria, list) or not criteria:
            raise LoopError('Case needs outcome criteria')
        for c in criteria:
            if (not isinstance(c, dict) or not identifier(c.get('id')) or c.get('mode') not in ('check', 'rubric')
                    or type(c.get('critical')) is not bool or not isinstance(c.get('description'), str) or not c['description'].strip()):
                raise LoopError('Invalid benchmark criterion')
            number(c.get('weight'), 0.01, 100, 'weight')
            if 'rule_paths' in c and (not isinstance(c['rule_paths'], list) or not all(
                    isinstance(p, str) and p.startswith('rules/') and '..' not in Path(p).parts for p in c['rule_paths'])):
                raise LoopError('Criterion rule paths must identify harness rules')
            if c['mode'] == 'rubric' and (not isinstance(c.get('anchors'), list) or len(c['anchors']) != 5
                                         or not all(isinstance(x, str) and x.strip() for x in c['anchors'])):
                raise LoopError('Rubrics need five concrete anchors for scores 0..4')
        if len({c['id'] for c in criteria}) != len(criteria):
            raise LoopError('Duplicate criterion ID')
        grader = inside(path.parent, relative(case['grader']))
        if not grader.is_file() or grader.is_symlink():
            raise LoopError('Case needs an external Python grader')
        seals[case['id']] = {'fixture': file_map, 'grader': file_hash(grader)}
    if len(set(ids)) != len(ids) or {c['split'] for c in value['cases']} != {'calibration', 'holdout'}:
        raise LoopError('Suite needs distinct case IDs and both calibration and holdout cases')
    goal_types(value)
    return value, digest([value, seals]), seals


def goal_type(case):
    # Session reuse is a scenario dimension, not a separate user goal type.
    return case['kind'] if case['kind'] != 'session' else 'explanation'


def goal_types(suite):
    kinds = sorted({goal_type(c) for c in suite['cases']})
    if len(kinds) < 2:
        raise LoopError('Benchmark requires at least two distinct goal types; extra cases of one type do not qualify')
    return kinds


def validate_evaluation(value):
    if (not isinstance(value, dict) or set(value) != {'provider', 'model', 'effort', 'repeats'}
            or value['provider'] not in ('claude', 'codex')
            or not isinstance(value['model'], str) or not value['model'].strip() or value['effort'] is None):
        raise LoopError('Pin the independent judge provider, model, effort and repetitions')
    bench_transport.effort_args(value['provider'], value['effort'])
    integer(value['repeats'], 2, 5, 'judge repetitions')


def comparison_arms(spec):
    """New runs compare off/on; historical runs retain their original coverage."""
    if spec.get('version') == 1 and 'arms' not in spec and set(spec.get('source', {})) == {'candidate', 'reference'}:
        return ('plain', 'reference', 'candidate')
    if spec.get('version') != 2 or spec.get('arms') != list(ARMS) or set(spec.get('source', {})) != {'candidate'}:
        raise LoopError('Benchmark manifest must declare both plain and candidate conditions')
    return ARMS


def source_snapshot(root):
    root = Path(root).resolve()
    contents = {}
    legacy = not (root / 'loop_work/engine.py').is_file() and (root / 'loop_dev/engine.py').is_file()
    selectors = tuple(path.replace('loop_work', 'loop_dev').replace('loop-work', 'loop-dev')
                      for path in SOURCE_PATHS) if legacy else SOURCE_PATHS
    for selector in selectors:
        path = root / selector
        candidates = path.rglob('*') if selector.endswith('/') else [path]
        for p in candidates:
            if '__pycache__' in p.parts or p.suffix not in ('.py', '.md', '.json', '.yaml'):
                continue
            if p.is_symlink():
                raise LoopError('Harness source must not use symlinks')
            if p.is_file():
                contents[p.relative_to(root).as_posix()] = p.read_text()
    engine = 'loop_dev/engine.py' if legacy else 'loop_work/engine.py'
    if not {'scripts/install.py', 'scripts/loop.py', 'rules/work.md', engine}.issubset(contents):
        raise LoopError('Harness snapshot lacks required install/runtime files')
    return {'hash': digest(contents), 'files': contents}


def profile_fingerprint():
    # Hash configuration, not authentication files or environment secrets. Global
    # host policies still apply; a dedicated benchmark profile is recommended.
    paths = [Path.home() / p for p in ('.codex/config.toml', '.codex/AGENTS.md', '.claude/settings.json', '.claude/CLAUDE.md')]
    return {str(p): file_hash(p) for p in paths}


def prepare(suite_path, candidate, models, providers, repeats, seed, environment, efforts=None, evaluation=None,
            routing_profile=None):
    suite, seal, assets = load_suite(suite_path)
    integer(repeats, 1, 100, 'repeats')
    integer(seed, 0, 2**31 - 1, 'seed')
    bench_transport.validate_efforts(efforts)
    if evaluation is not None:
        validate_evaluation(evaluation)
    if set(models) != {'codex', 'claude'} or not all(isinstance(x, str) and x.strip() for x in models.values()):
        raise LoopError('Pin both Claude and Codex model IDs, including nested workers/reviewers')
    if not providers or any(p not in models for p in providers) or len(set(providers)) != len(providers):
        raise LoopError('Select distinct conversation providers')
    if not isinstance(environment, str) or not environment.strip():
        raise LoopError('Describe the fixed host/configuration profile')
    current = source_snapshot(candidate)
    routing = bench_transport.routing_profile(models, efforts, routing_profile)
    planned_trials = len(suite['cases']) * len(providers) * repeats * len(ARMS)
    return {'version': 2, 'arms': list(ARMS), 'suite': suite, 'suite_hash': seal, 'assets': assets,
            'source': {'candidate': current},
            'models': models, 'efforts': efforts, 'evaluation': evaluation, 'routing': routing,
            'goal_types': goal_types(suite),
            'providers': providers, 'repeats': repeats, 'seed': seed,
            'environment': environment, 'profile': profile_fingerprint(),
            'planned_trials': planned_trials,
            'limits': {'conversation_cli_calls': planned_trials * suite['max_calls'],
                       'scenario_wall_seconds': planned_trials * suite['timeout_seconds'],
                       'default_judge_calls': planned_trials * (evaluation['repeats'] if evaluation else 2)}}


def export_files(root, contents):
    for name, text in contents.items():
        write_text(inside(root, relative(name)), text)


def commit_fixture(root):
    env = dict(os.environ, GIT_AUTHOR_DATE='2000-01-01T00:00:00Z', GIT_COMMITTER_DATE='2000-01-01T00:00:00Z')
    for args in (('init', '-q', '-b', 'bench'), ('config', 'user.name', 'Benchmark'),
                 ('config', 'user.email', 'benchmark@example.invalid'), ('add', '.'),
                 ('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Benchmark fixture', '--allow-empty')):
        process = subprocess.run(['git', '-C', str(root), *args], env=env, capture_output=True)
        if process.returncode:
            raise LoopError('Could not create isolated fixture repository')


def activation(trial):
    path = trial / 'knowledge/knowledge.sqlite3'
    if not path.is_file():
        return []
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        return [json.loads(r[0]) for r in db.execute("SELECT body FROM records WHERE kind='session'")]


def frozen_files(workspace, case):
    return {name: file_hash(inside(workspace, name)) for name in case.get('protected', [])}


def grade(trial, workspace, case, grader):
    destination = trial / 'grading'
    destination.mkdir()
    # Graders run after the agent on a separate copy, never on the source fixture.
    shutil.copytree(workspace, destination / 'workspace', ignore=shutil.ignore_patterns('.git', '.loop', '.agents', '.claude', '__pycache__'))
    process = execute([sys.executable, '-I', str(grader), str(destination / 'workspace'),
                       str(trial / 'responses.json'), case['id']], destination, destination / 'logs', 30, kill_tree=True)
    write_json(destination / 'process.json', process)
    if process['exit_code'] != 0 or process['timed_out']:
        raise LoopError('External grader failed; this is missing evidence, not a passing score')
    result = read_json(process['stdout'])
    expected = {c['id'] for c in case['criteria'] if c['mode'] == 'check'}
    if not isinstance(result, dict) or set(result) != expected:
        raise LoopError('Grader must cover every check criterion exactly once')
    for item in result.values():
        if (not isinstance(item, dict) or set(item) != {'passed', 'evidence'} or type(item['passed']) is not bool
                or not isinstance(item['evidence'], str) or not item['evidence'].strip()):
            raise LoopError('Invalid external grader evidence')
    return result


def run_trial(root, spec, case, provider, repeat, arm, sources, suite_home, executables):
    sample = uuid.uuid4().hex
    trial = root / 'trials' / sample
    trial.mkdir(parents=True)
    workspace = trial / 'workspace'
    shutil.copytree(suite_home / case['fixture'], workspace)
    for name in case['roots']:
        commit_fixture(workspace / name)
    before = frozen_files(workspace, case)
    env = bench_transport.prepare_transport(trial, spec['models'], spec['suite']['max_calls'], executables,
                                            spec['efforts'], spec.get('routing'))
    transport_hash = file_hash(trial / 'profile.json')
    routing_hash = file_hash(trial / 'routing-profile.json')
    if arm != 'plain':
        setup = execute([sys.executable, str(sources[arm] / 'scripts/install.py'), '--target',
                         str(workspace / case['turns'][0]['root'])], workspace, trial / 'install', 30, env_overrides=env)
        if setup['exit_code'] != 0:
            raise LoopError('Could not install harness snapshot; inspect installation log')
        # Project instructions legitimately acquire only the installer-managed block.
        before = frozen_files(workspace, case)
    record = {'id': sample, 'case': case['id'], 'provider': provider, 'repeat': repeat, 'arm': arm,
              'status': 'running', 'errors': [], 'turns': [], 'checks': {}, 'quality': None,
              'seconds': 0, 'activation_valid': True}
    write_json(trial / 'result.json', record)
    responses, session, harness_session = [], None, None
    started = time.monotonic()
    try:
        for index, turn in enumerate(case['turns']):
            export_files(workspace, turn.get('before', {}))
            remaining = spec['suite']['timeout_seconds'] - (time.monotonic() - started)
            if remaining <= 0:
                raise LoopError('Scenario time budget exhausted')
            prompt = turn['prompt']
            if index == 0:
                prompt += ('\n\nExecution settings shared by both benchmark conditions: the available model/effort '
                           'catalog is at ' + env['LOOP_ROUTING_PROFILE'] + '. Nested CLI calls may use only its '
                           'explicit pairs. All conversation, worker, review and retry calls share one call/time '
                           'budget. Omitted flags use the pinned conversation pair; an explicit allowed selection '
                           'is preserved. Import this catalog before freezing any task execution settings.')
            if index == 0 and arm != 'plain':
                prompt = ('/loop-work ' if provider == 'claude' else '$loop-work ') + prompt
            value = bench_transport.conversation(provider, spec['models'][provider], workspace / turn['root'], trial,
                         trial / 'turns' / str(index), prompt, session, remaining, env)
            session = value['session']
            responses.append({'prompt': turn['prompt'], 'root': turn['root'], 'answer': value['answer']})
            sessions = activation(trial)
            if arm == 'plain':
                valid = not any(s.get('harness', {}).get('enabled') for s in sessions)
            else:
                valid = len(sessions) == 1 and sessions[0].get('provider') == provider and sessions[0].get('harness', {}).get('enabled') is True
                if valid:
                    bound = sessions[0]['id']
                    valid = harness_session is None or harness_session == bound
                    harness_session = bound
            record['activation_valid'] &= valid
            record['turns'].append({'index': index, 'session': session, 'harness_session': harness_session, 'activation_valid': valid})
            write_json(trial / 'responses.json', responses)
        record['status'] = 'completed'
    except (LoopError, OSError, ValueError, sqlite3.Error) as exc:
        record['status'] = 'failed'
        record['errors'].append(str(exc))
    finally:
        record['seconds'] = round(time.monotonic() - started, 3)
        write_json(trial / 'responses.json', responses)
        record['usage'] = bench_transport.usage(trial)
        record['protected_valid'] = frozen_files(workspace, case) == before
        record['routing_valid'] = (file_hash(trial / 'profile.json') == transport_hash
                                   and file_hash(trial / 'routing-profile.json') == routing_hash)
        write_json(trial / 'result.json', record)
    try:
        record['checks'] = grade(trial, workspace, case, suite_home / case['grader'])
    except (LoopError, OSError, ValueError) as exc:
        record['errors'].append(str(exc))
        record['grading_error'] = True
    artifacts = {}
    for name in case.get('artifacts', []):
        path = inside(workspace, name)
        if path.is_symlink():
            raise LoopError('Result artifact is a symlink')
        if path.is_file():
            if path.stat().st_size > 100000:
                raise LoopError('Result artifact is too large for complete rubric review')
            artifacts[name] = path.read_text()
    # No arm/model/rule or harness-state files are included in the judge packet.
    packet = {'sample': sample, 'requests': [{'root': t['root'], 'prompt': t['prompt']} for t in case['turns']],
              'answers': [r['answer'].replace(str(workspace), 'workspace') for r in responses], 'artifacts': artifacts,
              'initial_context': {name: (suite_home / case['fixture'] / name).read_text() for name in case.get('context', [])},
              'source_changes': [{'before_turn': i, 'files': t['before']} for i, t in enumerate(case['turns']) if t.get('before')],
              'criteria': [{k: c[k] for k in ('id', 'description', 'anchors')} for c in case['criteria'] if c['mode'] == 'rubric']}
    write_json(trial / 'packet.json', packet)
    record['packet_hash'] = digest(packet)
    record['workspace_hash'] = digest(directory_files(workspace))
    record['evidence'] = {p.relative_to(trial).as_posix(): file_hash(p) for p in sorted(trial.rglob('*'))
                          if p.is_file() and not p.is_symlink() and not p.is_relative_to(workspace)
                          and p.name != 'result.json' and not p.is_relative_to(trial / 'grading/workspace')
                          and not p.is_relative_to(trial / 'knowledge')}
    write_json(trial / 'result.json', record)
    return record


def run(suite_path, candidate, models, providers, repeats, seed, environment, output, executables=None,
        efforts=None, evaluation=None, routing_profile=None):
    spec = prepare(suite_path, candidate, models, providers, repeats, seed, environment, efforts, evaluation,
                   routing_profile)
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(Path(candidate).resolve()) or output.is_relative_to(Path(suite_path).resolve().parent):
        raise LoopError('Use a new benchmark output directory outside source/suite trees')
    actual = executables or {p: shutil.which(p) for p in models}
    if any(not actual.get(p) for p in models):
        raise LoopError('Both CLI executables are required; no benchmark result was produced')
    output.mkdir(parents=True)
    sources = {}
    for arm in spec['source']:
        sources[arm] = output / 'sources' / arm
        export_files(sources[arm], spec['source'][arm]['files'])
    spec['created_at'] = now()
    spec['mode'] = 'simulation' if executables else 'live'
    spec['versions'] = {}
    for provider, binary in actual.items():
        result = execute([binary, '--version'], output, output / 'versions' / provider, 15)
        if result['exit_code'] != 0:
            raise LoopError('CLI version preflight failed')
        spec['versions'][provider] = Path(result['stdout']).read_text().strip()[:300]
    spec['source'] = {k: {'hash': v['hash']} for k, v in spec['source'].items()}
    write_json(output / 'manifest.json', spec)
    trials = []
    write_json(output / 'index.json', {'manifest_hash': digest(spec), 'trials': [], 'complete': False})
    schedule = [(case, p, r) for case in spec['suite']['cases'] for p in providers for r in range(repeats)]
    rng = random.Random(seed)
    rng.shuffle(schedule)
    try:
        for case, provider, repeat in schedule:
            arms = list(spec['arms'])
            rng.shuffle(arms)
            for arm in arms:
                record = run_trial(output, spec, case, provider, repeat, arm, sources, Path(suite_path).resolve().parent, actual)
                trials.append({'id': record['id'], 'hash': digest(record)})
                write_json(output / 'index.json', {'manifest_hash': digest(spec), 'trials': trials, 'complete': False})
                print(json.dumps({'completed': len(trials), 'total': spec['planned_trials'], 'case': case['id'],
                                  'provider': provider, 'arm': arm, 'status': record['status']}), file=sys.stderr, flush=True)
    finally:
        _, seal, _ = load_suite(suite_path)
        complete = len(trials) == spec['planned_trials'] and seal == spec['suite_hash'] and profile_fingerprint() == spec['profile']
        write_json(output / 'index.json', {'manifest_hash': digest(spec), 'trials': trials, 'complete': complete})
    from .bench_report import report
    return report(output)


def read_run(root):
    root = Path(root).resolve()
    spec, index = read_json(root / 'manifest.json'), read_json(root / 'index.json')
    if index['manifest_hash'] != digest(spec):
        raise LoopError('Benchmark manifest changed; rerun with the intended criteria and inputs')
    comparison_arms(spec)
    if 'routing' in spec:
        bench_transport.validate_routing(spec['routing'], spec['models'], spec['efforts'])
    rows = []
    seen = set()
    for entry in index['trials']:
        if not identifier(entry['id']) or entry['id'] in seen:
            raise LoopError('Invalid or duplicate benchmark sample')
        seen.add(entry['id'])
        trial = root / 'trials' / entry['id']
        record = read_json(trial / 'result.json')
        if digest(record) != entry['hash'] or record['packet_hash'] != digest(read_json(trial / 'packet.json')):
            raise LoopError('Benchmark result or judge packet changed')
        if record['workspace_hash'] != digest(directory_files(trial / 'workspace')):
            raise LoopError('Benchmark workspace changed after evaluation')
        for name, original in record['evidence'].items():
            if file_hash(inside(trial, relative(name))) != original:
                raise LoopError('Benchmark evidence changed: ' + name)
        rows.append(record)
    return root, spec, index, rows
