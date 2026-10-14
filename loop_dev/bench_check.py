"""One invocation: freeze inputs, run all arms, judge, gate, explain improvements."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import platform
import uuid

from . import benchmark, bench_report, bench_improvements, bench_evolution
from .storage import LoopError, file_hash, write_json


def check(models, efforts, *, candidate=None, suite=None, providers=None,
          repeats=3, seed=0, environment=None, output=None, judge_provider='codex', judge_model=None,
          judge_effort=None, judge_repeats=2, dry_run=False, executables=None, history=None):
    source = Path(__file__).resolve().parent.parent
    candidate = Path(candidate or source).resolve()
    suite = Path(suite or source / 'benchmarks/suite.json').resolve()
    providers = ['claude', 'codex'] if providers is None else providers
    environment = environment or (platform.system() + ' / ' + platform.machine() + '; current CLI host profile')
    if judge_provider not in models:
        raise LoopError('Select Claude or Codex as the common judge')
    evaluation = {'provider': judge_provider, 'model': models[judge_provider] if judge_model is None else judge_model,
                  'effort': efforts.get(judge_provider) if judge_effort is None else judge_effort, 'repeats': judge_repeats}
    spec = benchmark.prepare(suite, candidate, models, providers, repeats, seed, environment, efforts, evaluation)
    spec['source'] = {k: {'hash': v['hash']} for k, v in spec['source'].items()}
    output = Path(output).resolve() if output else candidate.parent / 'loop-dev-benchmarks' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    if output.exists() or output.is_relative_to(candidate) or output.is_relative_to(suite.parent):
        raise LoopError('Use a new benchmark output directory outside source/suite trees')
    history = bench_evolution.location(candidate, history)
    if history.exists() and not history.is_dir():
        raise LoopError('Benchmark history must be a directory')
    if (history.is_relative_to(candidate) or history.is_relative_to(suite.parent)
            or history.is_relative_to(output) or output.is_relative_to(history)):
        raise LoopError('Keep benchmark history outside source, suite and individual output trees')
    started = bench_evolution.read_history(history)
    if dry_run:
        return {**spec, 'dry_run': True, 'output': str(output), 'history': str(history),
                'saved_profiles': len(started['baselines'])}
    def finish(value, failure, phase):
        evolution = value.get('evolution') if value else None
        summary = None if evolution is None else {
            'verdict': evolution['verdict'], 'baseline_updated': evolution.get('baseline_updated', False) and not failure,
            'before': ({k: evolution['before'][k] for k in ('run', 'source_hash')} if evolution['before'] else None),
            'profile_hash': evolution['profile_hash'], 'effects': evolution['effects']}
        result = {'phase': phase, 'verdict': 'inconclusive' if failure else value['verdict'],
                  'passed': not failure and value['passed'], 'failure': failure,
                  'output': str(output), 'history': str(history), 'evaluation': evaluation,
                  'goal_types': benchmark.goal_types(spec['suite']), 'arms': list(benchmark.comparison_arms(spec)),
                  'models': models, 'efforts': efforts, 'manifest_hash': value.get('manifest_hash') if value else None,
                  'on_off_verdict': value.get('on_off_verdict', value['verdict']) if value else None,
                  'evolution': summary,
                  'evolution_hash': file_hash(output / 'evolution.json') if (output / 'evolution.json').exists() else None,
                  'improvements': str(output / 'improvements.md')}
        write_json(output / 'check.json', result)
        bench_improvements.write_report(output, spec, value, failure)
        return result
    phase, value, failure = 'run', None, None
    try:
        # An unjudged run is normally inconclusive; its verdict must not stop judging.
        benchmark.run(suite, candidate, models, providers, repeats, seed, environment,
                      output, executables=executables, efforts=efforts, evaluation=evaluation)
        phase = 'judge'
        bench_report.judge(output, judge_provider, evaluation['model'], judge_repeats, effort=evaluation['effort'])
        phase = 'gate'
        value = bench_report.gate(output, candidate, suite)
        _, spec, _, _ = benchmark.read_run(output)
        phase = 'history'
        with bench_evolution.record(output, spec, value, history, started) as compared:
            # Generate and recheck every view before the accepted pointer is advanced.
            bench_report.report(output)
            result = finish(compared, None, 'completed')
        return result
    except (LoopError, OSError, ValueError) as exc:
        failure = {'phase': phase, 'error': str(exc)}
        # Only reuse evidence after the same hash checks as an ordinary report.
        if (output / 'index.json').is_file():
            try:
                value = bench_report.report(output)
            except (LoopError, OSError, ValueError):
                value = None
    if value:
        _, spec, _, _ = benchmark.read_run(output)
    output.mkdir(parents=True, exist_ok=True)
    return finish(value, failure, phase)
