"""One invocation: freeze inputs, run all arms, judge, gate, explain improvements."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import platform
import uuid

from . import benchmark, bench_report, bench_improvements
from .storage import LoopError, write_json


def check(models, efforts, *, candidate=None, suite=None, providers=None,
          repeats=3, seed=0, environment=None, output=None, judge_provider='codex', judge_model=None,
          judge_effort=None, judge_repeats=2, dry_run=False, executables=None):
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
    if dry_run:
        return {**spec, 'dry_run': True, 'output': str(output)}
    phase, value, failure = 'run', None, None
    try:
        # An unjudged run is normally inconclusive; its verdict must not stop judging.
        benchmark.run(suite, candidate, models, providers, repeats, seed, environment,
                      output, executables=executables, efforts=efforts, evaluation=evaluation)
        phase = 'judge'
        bench_report.judge(output, judge_provider, evaluation['model'], judge_repeats, effort=evaluation['effort'])
        phase = 'gate'
        value = bench_report.gate(output, candidate, suite)
        phase = 'completed'
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
    result = {'phase': phase, 'verdict': 'inconclusive' if failure else value['verdict'],
              'passed': not failure and value['passed'], 'failure': failure,
              'output': str(output), 'evaluation': evaluation, 'goal_types': benchmark.goal_types(spec['suite']),
              'arms': list(benchmark.comparison_arms(spec)),
              'models': models, 'efforts': efforts, 'manifest_hash': value.get('manifest_hash') if value else None,
              'improvements': str(output / 'improvements.md')}
    write_json(output / 'check.json', result)
    bench_improvements.write_report(output, spec, value, failure)
    return result
