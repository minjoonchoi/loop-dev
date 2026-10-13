"""Live conversation transport and per-trial CLI model/call accounting."""
from __future__ import annotations

import fcntl
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
from .storage import LoopError, read_json, write_json


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
    model = profile['models'][provider]
    for index, arg in enumerate(args):
        if arg in ('--model', '-m') and (index + 1 >= len(args) or args[index + 1] != model):
            raise LoopError('Benchmark model profile is fixed')
        if arg.startswith('--model=') and arg.split('=', 1)[1] != model:
            raise LoopError('Benchmark model profile is fixed')
    if not any(x in ('--model', '-m') or x.startswith('--model=') for x in args):
        at = args.index('exec') + 1 if provider == 'codex' else 0
        args[at:at] = ['--model', model]
    if provider == 'codex' and '--json' not in args:
        args.insert(args.index('exec') + 1, '--json')
    ledger = Path(profile['ledger'])
    ledger.mkdir(exist_ok=True)
    with (ledger / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if len(list(ledger.glob('*.json'))) >= profile['max_calls']:
            raise LoopError('Benchmark provider-call budget exhausted')
        path = ledger / (uuid.uuid4().hex + '.json')
        record = {'provider': provider, 'model': model, 'status': 'running', 'pid': os.getpid(),
                  'tokens': None, 'cost_usd': None, 'session': None}
        write_json(path, record)
    started = time.monotonic()
    result = subprocess.run([binary, *args], input=sys.stdin.buffer.read(), capture_output=True)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    try:
        parsed = parse_output(provider, result.stdout.decode())
        record.update({k: parsed[k] for k in ('session', 'tokens', 'cost_usd')})
    except (LoopError, ValueError, UnicodeError, KeyError, TypeError):
        pass
    record.update(status='completed', exit_code=result.returncode, seconds=time.monotonic() - started)
    write_json(path, record)
    raise SystemExit(result.returncode)


def prepare_transport(directory, models, max_calls, executables=None):
    actual = executables or {p: shutil.which(p) for p in models}
    if any(not actual.get(p) for p in models):
        raise LoopError('Both Claude and Codex CLIs are required for live harness benchmarks')
    profile = {'executables': actual, 'models': models, 'max_calls': max_calls,
               'ledger': str(directory / 'calls')}
    write_json(directory / 'profile.json', profile)
    binary_dir = directory / 'bin'
    binary_dir.mkdir()
    package_root = str(Path(__file__).resolve().parent.parent)
    for provider in models:
        path = binary_dir / provider
        path.write_text('#!' + sys.executable + '\nimport sys\nsys.path.insert(0, ' + repr(package_root) +
                        ')\nfrom loop_dev.bench_transport import relay\nrelay(' + repr(provider) + ')\n')
        path.chmod(0o700)
    return {'PATH': str(binary_dir) + os.pathsep + os.environ.get('PATH', ''),
            'LOOP_BENCH_PROFILE': str(directory / 'profile.json'),
            'LOOP_KB_HOME': str(directory / 'knowledge')}


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
    process = execute(argv, root, directory, timeout, prompt, env, kill_tree=True)
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
    return {'provider_calls': len(records), 'calls_complete': bool(records) and all(r.get('status') == 'completed' for r in records),
            'codex_tokens': sum(r['tokens'] for r in codex)
            if codex and all(type(r.get('tokens')) is int for r in codex) else None,
            'claude_cost_usd_estimate': sum(costs.values()) if complete else None,
            'total_cost_usd': None, 'scope': 'CLI-reported usage; total cross-provider billing is unavailable'}
