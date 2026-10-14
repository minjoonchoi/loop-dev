"""Bounded improvement reports derived from measurements, without extra model calls."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import statistics

from .benchmark import comparison_arms, goal_type
from .storage import digest, write_json, write_text
from .writing import plain

ARM_LABELS = {'plain': '하네스 미사용', 'candidate': '하네스 사용', 'reference': '이전 버전'}


def mean(values):
    return round(statistics.mean(values), 3) if values else None


def evidence(sample):
    return ['trials/' + sample + '/result.json', 'trials/' + sample + '/packet.json']


def comparisons(spec, value):
    """Expose denominators and every goal type; incomplete samples are not zero scores."""
    samples = value.get('samples', [])
    arms = comparison_arms(spec)
    controls = [arm for arm in arms if arm != 'candidate']
    types = sorted({goal_type(c) for c in spec['suite']['cases']})
    groups, outcomes = [], []
    for kind in [None, *types]:
        cases = [c for c in spec['suite']['cases'] if kind is None or goal_type(c) == kind]
        for provider in spec['providers']:
            for arm in arms:
                rows = [s for s in samples if s['provider'] == provider and s['arm'] == arm
                        and (kind is None or s['goal_type'] == kind)]
                rated = [s for s in rows if s['quality'] is not None and not s['missing'] and not s['grading_error']]
                groups.append({'goal_type': kind or 'all', 'provider': provider, 'arm': arm,
                    'expected': len(cases) * spec['repeats'], 'observed': len(rows), 'rated': len(rated),
                    'completed': sum(s['status'] == 'completed' for s in rows),
                    'quality_mean': mean([s['quality'] for s in rated]),
                    'seconds_mean': mean([s['seconds'] for s in rows]),
                    'provider_calls_mean': mean([s['usage']['provider_calls'] for s in rows]),
                    'extra_user_requests_mean': mean([s['extra_user_requests'] for s in rows if s['extra_user_requests'] is not None]),
                    'user_requests_rated': sum(s['extra_user_requests'] is not None for s in rows)})
            if kind is not None:
                rows = [s for s in samples if s['provider'] == provider and s['goal_type'] == kind]
                findings = [r for r in value.get('regressions', []) if r['provider'] == provider and r['goal_type'] == kind]
                incomplete = (len(rows) != len(cases) * spec['repeats'] * len(arms) or len(types) < 2 or value.get('run_complete') is False
                    or spec.get('mode') != 'live' or spec['repeats'] < spec['suite']['policy']['min_repeats']
                    or any(s['missing'] or s['grading_error'] or s['status'] != 'completed' or not s['activation_valid']
                           or not s['protected_valid'] or not s['usage']['calls_complete'] for s in rows))
                paired = [c for c in value.get('comparisons', []) if c['provider'] == provider and c['goal_type'] == kind]
                outcomes.append({'goal_type': kind, 'provider': provider,
                    'status': 'regression' if findings else 'inconclusive' if incomplete else 'non_regressing',
                    'case_ids': [c['id'] for c in cases], 'regressions': len(findings),
                    'completed': sum(s['status'] == 'completed' for s in rows),
                    'expected': len(cases) * spec['repeats'] * len(arms),
                    'quality_delta': {control: mean([c['quality_delta'] for c in paired if c['control'] == control
                                      and c['quality_delta'] is not None]) for control in controls}})
    return groups, outcomes


def recommendations(spec, value, failure=None):
    items = []
    def add(priority, title, observation, hypothesis, action, verify, samples=(), rules=(), cases=(), kinds=(), measurements=()):
        items.append({'priority': priority, 'title': title, 'observation': observation,
            'hypothesis': hypothesis, 'next_change': action, 'revalidation': verify,
            'sample_ids': sorted(set(samples)), 'case_ids': sorted(set(cases)), 'goal_types': sorted(set(kinds)),
            'rule_paths': sorted(set(rules)), 'measurements': list(measurements),
            'evidence': sorted({p for sample in samples for p in evidence(sample)})})
    if failure:
        add('P0', '실행 조건 복구', failure['phase'] + ': ' + failure['error'],
            '실험이 완료되지 않아 하네스 규칙의 원인을 판단할 수 없습니다.',
            '실패 단계의 설정 또는 도구 오류를 해결합니다. 결과 점수나 통과 기준은 변경하지 않습니다.',
            '같은 목표 유형·모델·effort·평가 기준으로 전체 비교를 다시 실행합니다.')
    if value.get('missing'):
        add('P0', '누락된 평가 근거 확보', '\n'.join(value['missing']),
            '누락된 측정이나 실패한 대조군으로 하네스의 우열을 판단할 수 없습니다.',
            '실행·채점·활성화·사용량 기록 중 누락된 근거를 복구합니다. 시뮬레이션이면 실제 CLI로 실행합니다.',
            '두 유형 이상에서 모든 제공자·실험 조건·반복의 근거가 갖춰지고 gate가 통과해야 합니다.',
            samples=[s['id'] for s in value.get('samples', []) if s['missing'] or s['grading_error']
                     or s['status'] != 'completed' or not s['activation_valid'] or not s['usage']['calls_complete']])
    grouped = defaultdict(list)
    for finding in value.get('regressions', []):
        grouped[(finding['case'], finding['provider'], finding['criterion'])].append(finding)
    for (case, provider, criterion), findings in sorted(grouped.items()):
        if criterion == 'quality_floor' and any(k[:2] == (case, provider) and k[2] not in ('quality_floor', 'elapsed_time', 'user_requests') for k in grouped):
            continue
        if criterion == 'execution':
            priority, hypothesis, action = ('P0', '실행 실패 또는 보호 파일 변경의 원인은 로그 확인이 필요합니다.',
                '해당 시도의 오류와 보호 파일 차이를 확인하고 실행 조건이나 위임 범위 중 확인된 문제만 수정합니다.')
        elif criterion == 'elapsed_time':
            priority, hypothesis, action = ('P2', '반복된 분석이나 불필요한 검토가 지연을 만들었을 가능성이 있습니다. 아직 원인은 입증되지 않았습니다.',
                'turn 로그와 호출 기록에서 반복 작업을 찾아 해당 목표 유형의 불필요한 단계 하나만 줄입니다.')
        elif criterion == 'user_requests':
            priority, hypothesis, action = ('P1', '시작 전 결정 누락이나 저장된 맥락의 재사용 실패일 수 있습니다. 질문 근거를 확인해야 합니다.',
                '추가 질문을 목표 설정 단계의 결정 또는 기존 지식으로 해결할 수 있는지 확인하고 해당 규칙만 수정합니다.')
        else:
            priority, hypothesis, action = ('P1', '요구사항·위임 지시·검토 범위 중 누락이 있을 수 있습니다. 관련 규칙은 조사 대상이며 확정 원인이 아닙니다.',
                '해당 기준의 실패 결과물을 확인하고 요구사항에서 작업·검토까지 빠진 연결 하나를 수정합니다.')
        add(priority, case + ' / ' + provider + ' / ' + criterion,
            '; '.join(sorted({f['reason'] for f in findings})) + '. ' + str(len({f['repeat'] for f in findings}))
            + '개 반복에서 관측. ' + '; '.join(str(x) for x in findings[0]['evidence'])[:350], hypothesis, action,
            '같은 기준으로 해당 사례의 대조 조건 대비 퇴행이 사라지는지 확인하고, 전체 유형과 holdout도 재실행합니다.',
            samples=[f['sample'] for f in findings], rules=[p for f in findings for p in f['rule_paths']],
            cases=[case], kinds=[findings[0]['goal_type']], measurements=findings)
    disagreements = [s for s in value.get('samples', []) if any(c.get('disagreement') for c in s['criteria'].values())]
    if disagreements:
        add('P2', '평가자 불일치 검토', str(len(disagreements)) + '개 결과물에서 반복 평가 점수가 다릅니다.',
            '평가 기준의 모호함 또는 경계 사례일 수 있습니다. 낮은 점수를 유지합니다.',
            'judgments.json의 근거와 결과물을 대조합니다. 유리한 점수가 나올 때까지 재채점하지 않습니다.',
            '기준을 수정한다면 새 실험으로 모든 비교군과 유형을 다시 평가합니다.',
            samples=[s['id'] for s in disagreements], kinds=[s['goal_type'] for s in disagreements])
    items.sort(key=lambda i: (i['priority'], i['title']))
    for item in items:
        item['id'] = 'improve-' + digest([item['title'], item['case_ids'], item['sample_ids']])[:12]
    return items


def write_report(root, spec, value=None, failure=None):
    root = Path(root)
    value = value or {}
    arms = comparison_arms(spec)
    controls = [arm for arm in arms if arm != 'candidate']
    verdict = 'inconclusive' if failure else value.get('verdict', 'inconclusive')
    groups, outcomes = comparisons(spec, value)
    if failure:
        for row in outcomes:
            row['status'] = 'inconclusive'
    items = recommendations(spec, value, failure)
    result = {'verdict': verdict, 'passed': not failure and value.get('passed', False),
        'manifest_hash': value.get('manifest_hash'), 'failure': failure,
        'arms': list(arms),
        'profiles': {p: {'model': spec['models'][p], 'effort': spec.get('efforts', {}).get(p)} for p in ('claude', 'codex')},
        'evaluation': spec.get('evaluation') or value.get('reviewer'),
        'goal_types': sorted({goal_type(c) for c in spec['suite']['cases']}),
        'comparisons': groups, 'type_outcomes': outcomes, 'improvements': items,
        'missing': value.get('missing', []), 'scope': value.get('scope'),
        'limits': ['같은 이름의 effort가 두 제공자에서 같은 연산량을 뜻하지 않습니다.',
                   '모델 설정은 요청값입니다. 지원 여부는 CLI가 검증하며 자동 대체하지 않습니다.',
                   '유형별 평균과 제공자 간 비교는 기술 통계입니다. 개별 사례의 퇴행을 상쇄하지 않습니다.']}
    write_json(root / 'improvements.json', result)
    display = lambda x: '미측정' if x is None else str(x)
    lines = ['# 벤치마크 개선 리포트', '', '**' + verdict + '** · 목표 유형: ' + ', '.join(result['goal_types']), '',
        '| 제공자 | 모델 | effort |', '| --- | --- | --- |']
    for provider, profile in result['profiles'].items():
        lines.append(f"| {provider} | {plain(profile['model'])} | {display(profile['effort'])} |")
    evaluation = result['evaluation']
    if evaluation:
        lines.extend(['', '공통 평가자: ' + plain(' / '.join(str(evaluation.get(k, '미지정')) for k in ('provider', 'model', 'effort', 'repeats')))])
    lines.extend(['', '## 설정별 결과', '', '| 제공자 / 비교군 | 완료 / 예정 | 품질 평균 (채점 수) | 평균 초 | 평균 호출 수 | 평균 추가 질문 |',
                  '| --- | --- | --- | --- | --- | --- |'])
    for row in groups:
        if row['goal_type'] == 'all':
            lines.append(f"| {row['provider']} / {ARM_LABELS[row['arm']]} | {row['completed']} / {row['expected']} | {display(row['quality_mean'])} ({row['rated']}) | {display(row['seconds_mean'])} | {display(row['provider_calls_mean'])} | {display(row['extra_user_requests_mean'])} |")
    lines.extend(['', '품질은 평가 근거가 갖춰진 결과, 시간·호출 수는 관측된 시도 기준입니다. 누락된 값은 0으로 계산하지 않습니다.', '',
                  '## 목표 유형별 결과', '',
                  '| 유형 / 제공자 | 완료 / 예정 | 판정 | ' + ' | '.join('품질 차이: ' + ARM_LABELS[c] + ' 대비' for c in controls) + ' |',
                  '| ' + ' | '.join(['---'] * (3 + len(controls))) + ' |'])
    for row in outcomes:
        deltas = ' | '.join(display(row['quality_delta'][control]) for control in controls)
        lines.append(f"| {row['goal_type']} / {row['provider']} | {row['completed']} / {row['expected']} | {row['status']} | {deltas} |")
    lines.extend(['', '## 개선 우선순위', ''])
    if not items:
        lines.append('관측된 퇴행이 없어 규칙 변경을 제안하지 않습니다. 이 결과만으로 일반적인 품질 우위를 주장할 수 없습니다.')
    for item in items[:5]:
        lines.extend(['### ' + item['priority'] + ' · ' + plain(item['title']), '',
            '- 관측: ' + plain(item['observation'][:500]), '- 가설: ' + plain(item['hypothesis']),
            '- 다음 변경: ' + plain(item['next_change']), '- 재검증: ' + plain(item['revalidation'])])
        if item['rule_paths']:
            lines.append('- 조사할 규칙: ' + ', '.join('`' + p + '`' for p in item['rule_paths']))
        pointers = item['evidence'][:4] or (['check.json'] if failure else ['report.json'])
        lines.extend(['- 근거: ' + ', '.join('[' + p + '](' + p + ')' for p in pointers), ''])
    if len(items) > 5:
        lines.append(f'전체 {len(items)}개 개선 항목과 반복별 측정값은 improvements.json에 있습니다.')
    lines.extend(['', *result['limits'], '', '[유형별 측정값·개선 항목](improvements.json)'])
    if (root / 'report.md').exists():
        lines.append('[개별 비교와 원본 판정](report.md) · [전체 근거](report.json)')
    if failure:
        lines.append('실행이 완료되지 않았습니다. 기존 report 파일이 있어도 현재 통과 근거로 사용하지 않습니다.')
    write_text(root / 'improvements.md', '\n'.join(lines) + '\n')
    return str(root / 'improvements.md')
