"""Live conversation transport and per-trial CLI model/call accounting."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from .process import execute
from .providers import effort_args
from .storage import LoopError, digest, file_hash, read_json, write_json


def routing_profile(models, efforts, value=None):
    """Freeze the same explicit capability catalog for both experimental arms."""
    from .routing import load_policy, validate_policy
    if value is None:
        mode = 'fixed'
        value = {'version': 1, 'profiles': [
            {'id': 'fixed-' + provider, 'provider': provider, 'model': models[provider],
             'effort': efforts[provider], 'capability_rank': 100, 'cost_rank': 1}
            for provider in ('claude', 'codex')],
            'requirements': {'low': 100, 'medium': 100, 'high': 100}}
    else:
        mode = 'adaptive'
        if isinstance(value, (str, Path)):
            value = load_policy(value)
    policy = validate_policy(value)
    pairs = {(p['provider'], p['model'], p['effort']) for p in policy['profiles']}
    if any((p, models[p], efforts[p]) not in pairs for p in ('claude', 'codex')):
        raise LoopError('Routing catalog must explicitly include both pinned conversation model/effort pairs')
    return {'mode': mode, 'policy': policy, 'hash': digest(policy)}


def validate_routing(value, models, efforts):
    if not isinstance(value, dict) or set(value) != {'mode', 'policy', 'hash'} or value['mode'] not in ('fixed', 'adaptive'):
        raise LoopError('Invalid frozen benchmark routing profile')
    expected = routing_profile(models, efforts, value['policy'] if value['mode'] == 'adaptive' else None)
    if value != expected:
        raise LoopError('Benchmark routing profile changed or is not normalized')
    return value


def selected_profile(profile, provider, args):
    """Validate explicit flags without replacing an allowed worker selection."""
    models, efforts, configured = [], [], set()
    for index, arg in enumerate(args):
        if arg in ('--model', '-m'):
            if index + 1 >= len(args):
                raise LoopError('Missing benchmark model value')
            models.append(args[index + 1])
        elif arg.startswith('--model='):
            models.append(arg.split('=', 1)[1])
        elif arg.startswith('-m') and len(arg) > 2 and not arg.startswith('--'):
            models.append(arg[2:].lstrip('='))
        if provider == 'claude':
            if arg == '--effort':
                if index + 1 >= len(args):
                    raise LoopError('Missing benchmark effort value')
                efforts.append(args[index + 1])
            elif arg.startswith('--effort='):
                efforts.append(arg.split('=', 1)[1])
        else:
            setting = None
            if arg in ('-c', '--config'):
                if index + 1 >= len(args):
                    raise LoopError('Missing Codex configuration value')
                setting = args[index + 1]
            elif arg.startswith('--config='):
                setting = arg.split('=', 1)[1]
            elif arg.startswith('-c') and len(arg) > 2:
                setting = arg[2:].lstrip('=')
            if setting:
                key, _, value = setting.partition('=')
                if key.strip() == 'model':
                    models.append(value.strip().strip('\"\''))
                if key.strip() in ('model_reasoning_effort', 'plan_mode_reasoning_effort'):
                    configured.add(key.strip())
                    efforts.append(value.strip().strip('\"\''))
    if len(set(models)) > 1 or len(set(efforts)) > 1:
        raise LoopError('Conflicting benchmark model/effort flags')
    model = models[0] if models else profile['models'][provider]
    effort = efforts[0] if efforts else profile['efforts'][provider]
    routing = validate_routing(profile.get('routing') or routing_profile(profile['models'], profile['efforts']),
                               profile['models'], profile['efforts'])
    if routing['mode'] == 'fixed':
        if model != profile['models'][provider]:
            raise LoopError('Benchmark model profile is fixed')
        if effort != profile['efforts'][provider]:
            raise LoopError('Benchmark effort profile is fixed')
    selected = next((p for p in routing['policy']['profiles']
                     if (p['provider'], p['model'], p['effort']) == (provider, model, effort)), None)
    if selected is None:
        raise LoopError('Model/effort pair is outside the frozen benchmark routing catalog')
    if provider == 'claude':
        pin_claude_fallback(args, model)
    at = args.index('exec') + 1 if provider == 'codex' else 0
    if not models:
        args[at:at] = ['--model', model]
    if provider == 'claude' and not efforts:
        args[at:at] = effort_args(provider, effort)
    if provider == 'codex':
        for key in ('model_reasoning_effort', 'plan_mode_reasoning_effort'):
            if key not in configured:
                args[at:at] = ['-c', key + '=' + json.dumps(effort)]
        if '--json' not in args:
            args.insert(at, '--json')
    return selected


def pin_claude_fallback(args, model):
    """Override persisted availability fallback without changing host files.

    Claude documents a per-call fallback model override, but not an empty
    disabling value. Pin its only target to the already selected model.
    """
    fallback, settings, settings_positions = [], [], []
    for index, arg in enumerate(args):
        if arg == '--fallback-model':
            if index + 1 >= len(args):
                raise LoopError('Missing benchmark fallback model value')
            fallback.append(args[index + 1])
        elif arg.startswith('--fallback-model='):
            fallback.append(arg.split('=', 1)[1])
        if arg == '--settings':
            if index + 1 >= len(args):
                raise LoopError('Missing Claude settings value')
            settings.append(args[index + 1])
            settings_positions.extend((index, index + 1))
        elif arg.startswith('--settings='):
            settings.append(arg.split('=', 1)[1])
            settings_positions.append(index)
    if any(value != model for value in fallback):
        raise LoopError('Benchmark fallback model must be the exact selected model; alternate chains are not allowed')
    if len(settings) > 1:
        raise LoopError('Use one Claude settings input so per-call model-switch settings are unambiguous')
    value = {}
    if settings:
        source = settings[0]
        try:
            value = json.loads(source) if source.lstrip().startswith('{') else read_json(Path(source))
        except (ValueError, OSError) as exc:
            raise LoopError('Cannot inspect Claude per-call settings') from exc
        if not isinstance(value, dict):
            raise LoopError('Claude per-call settings must be an object')
    value['switchModelsOnFlag'] = False
    for index in reversed(settings_positions):
        del args[index]
    args[:0] = ['--settings', json.dumps(value, separators=(',', ':'))]
    if not fallback:
        args[:0] = ['--fallback-model', model]


def request_context(prompt, args):
    """Record declared invocation provenance, never infer semantic acceptance."""
    if os.environ.get('LOOP_BENCH_CONVERSATION') == '1':
        return {'role': 'conversation'}
    if '\nWORKER DATA:\n' in prompt:
        try:
            packet = json.loads(prompt.split('\nWORKER DATA:\n', 1)[1])
            return {'role': 'worker', 'task_id': packet['task_id'], 'location_id': packet.get('location_id'),
                    'assignment_id': packet['assignment']['id'], 'attempt': packet.get('attempt'),
                    'routing': packet.get('routing'), 'attempt_ref': packet.get('attempt_ref')}
        except (ValueError, KeyError, TypeError):
            pass
    try:
        schema = (read_json(Path(args[args.index('--output-schema') + 1])) if '--output-schema' in args
                  else json.loads(args[args.index('--json-schema') + 1]) if '--json-schema' in args else {})
        if {'verdict', 'findings', 'coverage'} <= set(schema.get('properties', {})):
            return {'role': 'review'}
    except (LoopError, OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    if '--output-schema' in args or '--json-schema' in args:
        return {'role': 'structured'}
    return {'role': 'nested'}


def parse_output(provider, text):
    if provider == 'claude':
        value = json.loads(text)
        if not isinstance(value, dict) or value.get('is_error') or value.get('permission_denials'):
            raise LoopError('Claude failed or denied a required permission')
        return {'session': value.get('session_id'), 'answer': value.get('result'),
                'tokens': None, 'cost_usd': value.get('total_cost_usd')}
    events = [json.loads(line) for line in text.splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise LoopError('Codex returned an invalid event envelope')
    if any(e.get('type') in ('error', 'turn.failed') for e in events):
        raise LoopError('Codex reported a failed turn')
    completed = [e for e in events if e.get('type') == 'turn.completed']
    if not completed:
        raise LoopError('Codex did not complete the turn')
    usage = [e.get('usage', {}) for e in completed]
    tokens = sum(x['input_tokens'] + x['output_tokens'] for x in usage) if all(
        type(x.get(k)) is int for x in usage for k in ('input_tokens', 'output_tokens')) else None
    messages = [e.get('item', {}).get('text') for e in events if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'agent_message']
    sessions = [e['thread_id'] for e in events if e.get('type') == 'thread.started' and e.get('thread_id')]
    return {'session': sessions[-1] if sessions else None, 'answer': messages[-1] if messages else None,
            'tokens': tokens, 'cost_usd': None}


def relay(provider):
    """Used by trial-local PATH wrappers, including nested workers from older releases."""
    profile = read_json(os.environ['LOOP_BENCH_PROFILE'])
    args = sys.argv[1:]
    binary = profile['executables'][provider]
    if '--version' in args:
        os.execv(binary, [binary, *args])
    if provider == 'codex' and 'exec' not in args or provider == 'claude' and not any(x in args for x in ('-p', '--print')):
        raise LoopError('Benchmark wrappers accept noninteractive calls only')
    selected = selected_profile(profile, provider, args)
    model, effort = selected['model'], selected['effort']
    payload = sys.stdin.buffer.read()
    context = request_context(payload.decode(errors='replace'), args)
    ledger = Path(profile['ledger'])
    ledger.mkdir(exist_ok=True)
    with (ledger / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(list(ledger.glob('*.json'))) >= profile['max_calls']:
            raise LoopError('Benchmark provider-call budget exhausted')
        path = ledger / (uuid.uuid4().hex + '.json')
        record = {'provider': provider, 'model': model, 'effort': effort, 'status': 'running', 'pid': os.getpid(),
                  'tokens': None, 'cost_usd': None, 'session': None, 'profile_id': selected['id'],
                  'selection_scope': 'requested model and effort; backend effective settings are unverified',
                  'context': context, 'routing_hash': profile['routing']['hash'],
                  'prompt_hash': hashlib.sha256(payload).hexdigest()}
        write_json(path, record)
    started = time.monotonic()
    env = dict(os.environ)
    env.pop('LOOP_BENCH_CONVERSATION', None)
    if provider == 'claude':
        env['CLAUDE_CODE_EFFORT_LEVEL'] = effort
    result = subprocess.run([binary, *args], input=payload, capture_output=True, env=env)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    record['response_valid'] = False
    try:
        parsed = parse_output(provider, result.stdout.decode())
        record['response_valid'] = True
        record.update({k: parsed[k] for k in ('session', 'tokens', 'cost_usd')})
    except (LoopError, ValueError, UnicodeError, KeyError, TypeError):
        pass
    record.update(status='completed', exit_code=result.returncode, seconds=time.monotonic() - started)
    write_json(path, record)
    raise SystemExit(result.returncode)


def prepare_transport(directory, models, max_calls, executables=None, efforts=None, routing=None):
    validate_efforts(efforts)
    actual = executables or {p: shutil.which(p) for p in models}
    if any(not actual.get(p) for p in models):
        raise LoopError('Both Claude and Codex CLIs are required for live harness benchmarks')
    routing = validate_routing(routing, models, efforts) if routing else routing_profile(models, efforts)
    profile = {'executables': actual, 'models': models, 'efforts': efforts, 'max_calls': max_calls,
               'ledger': str(directory / 'calls'), 'routing': routing}
    write_json(directory / 'profile.json', profile)
    write_json(directory / 'routing-profile.json', routing['policy'])
    binary_dir = directory / 'bin'
    binary_dir.mkdir()
    package_root = str(Path(__file__).resolve().parent.parent)
    for provider in models:
        path = binary_dir / provider
        path.write_text('#!' + sys.executable + '\nimport sys\nsys.path.insert(0, ' + repr(package_root) +
                        ')\nfrom loop_work.bench_transport import relay\nrelay(' + repr(provider) + ')\n')
        path.chmod(0o700)
    return {'PATH': str(binary_dir) + os.pathsep + os.environ.get('PATH', ''),
            'LOOP_BENCH_PROFILE': str(directory / 'profile.json'),
            'LOOP_ROUTING_PROFILE': str(directory / 'routing-profile.json'),
            'LOOP_KB_HOME': str(directory / 'knowledge')}


def validate_efforts(efforts):
    if not isinstance(efforts, dict) or set(efforts) != {'codex', 'claude'} or any(v is None for v in efforts.values()):
        raise LoopError('Pin explicit effort levels for both Claude and Codex')
    for provider, value in efforts.items():
        effort_args(provider, value)


def conversation(provider, model, root, trial, directory, prompt, session, timeout, env):
    if provider == 'codex':
        argv = [str(trial / 'bin/codex'), '--cd', str(root), '--ask-for-approval', 'never', '--sandbox', 'workspace-write',
                'exec', '--model', model, '--json', '--output-last-message', str(directory / 'answer.txt'),
                *(['resume', session] if session else []), '-']
        # On resume, the original workspace permission profile is retained.
        if not session:
            argv[argv.index('exec'):argv.index('exec')] = ['--add-dir', str(trial)]
    else:
        argv = [str(trial / 'bin/claude'), '-p', '--model', model, '--output-format', 'json',
                '--permission-mode', 'dontAsk', '--allowedTools', 'Read,Glob,Grep,Write,Edit,Bash,Skill',
                '--disallowedTools', 'AskUserQuestion,mcp__*', '--strict-mcp-config', '--mcp-config',
                '{"mcpServers":{}}', '--add-dir', str(trial), *(['--resume', session] if session else [])]
    directory.mkdir(parents=True)
    (directory / 'prompt.txt').write_text(prompt)
    process = execute(argv, root, directory, timeout, prompt, {**env, 'LOOP_BENCH_CONVERSATION': '1'}, kill_tree=True)
    write_json(directory / 'process.json', process)
    if process['exit_code'] != 0 or process['timed_out']:
        raise LoopError('Conversation failed or exhausted its time budget; inspect process logs')
    value = parse_output(provider, Path(process['stdout']).read_text())
    if provider == 'codex' and (directory / 'answer.txt').is_file():
        value['answer'] = (directory / 'answer.txt').read_text()
    returned = value['session'] or session
    if not isinstance(returned, str) or not returned.strip() or session and returned != session:
        raise LoopError('Missing or changed conversation binding')
    if not isinstance(value['answer'], str) or not value['answer'].strip():
        raise LoopError('Conversation did not return an answer')
    return {**value, 'session': returned, 'process': process}


def worker_outcomes(trial, records):
    """Count retries/discards only from linked runtime receipts, including rejects."""
    workers = [row for row in records if row.get('context', {}).get('role') == 'worker']
    verified, evidence = {}, []
    trial = Path(trial).resolve()

    def local_file(value, parent=None):
        if not isinstance(value, str):
            raise ValueError('Missing receipt path')
        path = Path(value)
        if (not path.is_absolute() or path.resolve() != path or not path.is_relative_to(trial)
                or parent is not None and path.parent != parent
                or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024):
            raise ValueError('Unbounded or foreign receipt')
        return path

    for call in workers:
        context = call['context']
        try:
            path = local_file(context.get('attempt_ref'))
            record = read_json(path)
            directory = path.parent
            profile = record['profile']
            route = context['routing']
            if (record['task_id'] != context['task_id'] or record['location_id'] != context['location_id']
                    or record['request']['id'] != context['assignment_id'] or record['attempts'] != context['attempt']
                    or record['directory'] != str(directory) or profile != route['profile']
                    or record.get('escalated') != route.get('escalated')
                    or (profile['provider'], profile['model'], profile['effort']) !=
                       (call['provider'], call['model'], call['effort'])
                    or record['routing_seal'] != call['routing_hash'] or route['policy_seal'] != call['routing_hash']
                    or hashlib.sha256(local_file(str(directory / 'prompt.txt'), directory).read_bytes()).hexdigest()
                       != call['prompt_hash']):
                continue
            result, assessment = None, None
            if record.get('result'):
                result_path = local_file(record['result'], directory)
                result = read_json(result_path)
                if file_hash(result_path) != record['result_file_hash'] or digest(result) != record['result_digest']:
                    continue
            if record.get('assessment_ref') or record.get('assessment') or record['status'] in ('done', 'waiting', 'rejected'):
                assessment_path = local_file(record['assessment_ref'], directory)
                assessment = read_json(assessment_path)
                payload = {k: v for k, v in assessment.items() if k != 'assessed_at'}
                if (result is None or payload != record['assessment']
                        or payload['assignment_id'] != context['assignment_id']
                        or payload['candidate_id'] != record['candidate_id']
                        or payload['result_digest'] != record['result_digest']
                        or payload['verdict'] not in ('accept', 'reject')
                        or record['status'] in ('done', 'waiting') and payload['verdict'] != 'accept'
                        or record['status'] == 'rejected' and payload['verdict'] != 'reject'
                        or record.get('assessment_file_hash') != file_hash(assessment_path)):
                    continue
            if record['status'] not in ('done', 'waiting', 'rejected', 'blocked', 'interrupted', 'failed', 'candidate', 'applying'):
                continue
            verified[str(path)] = record
            evidence.append({'call_prompt_hash': call['prompt_hash'], 'attempt_ref': str(path),
                             'attempt_hash': file_hash(path), 'attempt': record,
                             'assessment': assessment, 'result': result})
        except (LoopError, OSError, ValueError, KeyError, TypeError):
            continue
    retries, escalations = 0, 0
    complete = bool(workers) and len(verified) == len(workers)
    for record in verified.values():
        history = record.get('history', [])
        if record['attempts'] == 1:
            if history or record.get('escalated'):
                complete = False
            continue
        prior = history[-1] if history else {}
        previous = verified.get(str(Path(prior.get('directory', '')) / 'attempt.json'))
        if (previous is None or previous['attempts'] + 1 != record['attempts']
                or previous['task_id'] != record['task_id'] or previous['location_id'] != record['location_id']
                or previous['request']['id'] != record['request']['id'] or prior.get('status') != previous['status']):
            complete = False
            continue
        if previous['status'] in ('rejected', 'blocked', 'failed', 'interrupted'):
            retries += 1
        if record.get('escalated'):
            old, new = previous['profile'], record['profile']
            if (previous['status'] != 'rejected' or new['capability_rank'] <= old['capability_rank']
                    or (new['provider'], new['model'], new['effort']) == (old['provider'], old['model'], old['effort'])):
                complete = False
                continue
            escalations += 1
    snapshot = trial / 'accounting/worker-outcomes.json'
    write_json(snapshot, {'version': 1, 'verified': evidence})
    return {'observed_worker_calls': len(workers), 'verified_worker_calls': len(verified),
            'retry_calls': retries if complete else None,
            'rejected_attempts': sum(r.get('assessment', {}).get('verdict') == 'reject'
                                     for r in verified.values()) if complete else None,
            'escalated_calls': escalations if complete else None, 'evidence': str(snapshot.relative_to(trial)),
            'scope': 'Linked runtime attempt and assessment receipts; absent/incomplete attribution remains unknown'}


def usage(trial):
    records = [read_json(p) for p in sorted((trial / 'calls').glob('*.json'))]
    codex = [r for r in records if r['provider'] == 'codex']
    claude = [r for r in records if r['provider'] == 'claude']
    # Claude reports cumulative conversation cost on resume: take the maximum
    # per session, then sum sessions. Missing values remain unknown, never zero.
    costs = {}
    def valid_cost(record):
        cost = record.get('cost_usd')
        return record.get('session') and type(cost) in (int, float) and math.isfinite(cost) and cost >= 0

    complete = bool(claude) and all(valid_cost(r) for r in claude)
    for r in claude:
        if valid_cost(r):
            costs[r['session']] = max(costs.get(r['session'], 0), r['cost_usd'])
    def measured(rows):
        finished = [r for r in rows if r.get('status') == 'completed']
        return {'calls': len(rows), 'completed_calls': len(finished),
                'failed_calls': sum(r['exit_code'] != 0 or r['response_valid'] is False for r in finished)
                    if all('exit_code' in r and 'response_valid' in r for r in finished) else None,
                'unfinished_calls': len(rows) - len(finished)}

    pairs = sorted({(r['provider'], r.get('model'), r.get('effort')) for r in records},
                   key=lambda row: tuple(str(item) for item in row))
    profiles = [{'provider': provider, 'model': model, 'effort': effort,
                 **measured([r for r in records if (r['provider'], r.get('model'), r.get('effort')) == (provider, model, effort)])}
                for provider, model, effort in pairs]
    roles = {role: measured([r for r in records if r.get('context', {}).get('role', 'unknown') == role])
             for role in sorted({r.get('context', {}).get('role', 'unknown') for r in records})}
    return {'provider_calls': len(records), 'calls_complete': bool(records) and all(r.get('status') == 'completed' for r in records),
            'call_outcomes': measured(records), 'profile_calls': profiles, 'role_calls': roles,
            'profile_scope': 'CLI requested model and effort; backend effective settings are unverified',
            'worker_outcomes': worker_outcomes(trial, records),
            'codex_tokens': sum(r['tokens'] for r in codex)
            if codex and all(type(r.get('tokens')) is int for r in codex) else None,
            'claude_cost_usd_estimate': sum(costs.values()) if complete else None,
            'total_cost_usd': None, 'scope': 'CLI-reported usage; total cross-provider billing is unavailable'}
