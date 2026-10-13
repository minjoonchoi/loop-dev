# 공통 작업과 로컬 지식

에이전트는 새 요청 전에 지식과 이전 결정을 읽고, 부족하거나 바뀐 부분만 조사합니다.
사용자는 목표를 전달합니다. 아래 명령과 JSON 작성은 에이전트의 내부 작업입니다.

## 요청 해석

| 요청 | 처리 |
| --- | --- |
| “기능을 완성해 줘”, “이 오류를 고쳐 줘” | 개발 목표와 검토 루프 |
| “왜 오류가 나는지 설명해 줘” | 설명과 근거 확인; 코드 변경 없음 |
| “문서를 정리해 줘”, “계획만 세워 줘” | 해당 결과 작성과 내용 확인 |
| “왜 멈췄어?”, 실행 중 보완 지시 | 기존 상태 확인·보완; 중복 작업 생성 없음 |

같은 세션 ID를 여러 디렉터리에서 사용합니다. 다른 대화의 ID를 최근 기록이라는 이유로 선택하지
않습니다. 새 대화도 프로젝트 지식은 읽을 수 있지만 세션·작업에 한정된 답변은 분리합니다.

## 세션 활성화

스킬을 명시적으로 시작하면 로컬 세션에 활성화 상태를 저장합니다. 이후 모든 입력을 같은
목표 설정 → 작업 흐름으로 처리하며 작업 완료·새 목표·경로 이동·맥락 압축으로 꺼지지 않습니다.
매 입력 전에 에이전트가 `harness status SESSION_ID`를 확인합니다. `knowledge context`에도
현재 `harness` 상태가 포함됩니다. 세션 ID, 지식 저장소, 실행 파일 절대 경로를 함께 유지합니다.
최초 활성화는 `harness on --provider codex`, 같은 대화의 재활성화는 `--session SESSION_ID`를
추가하며, 명시적 해제는 `harness off SESSION_ID`입니다. 이 명령들은 Git 없이도 동작합니다.
해제는 작업·지식·예산을 지우지 않습니다. 다른 대화는 별도 비활성 세션으로 시작합니다.

## 비개발 작업

```sh
python3 .loop/loop.py work open --kind explanation --title "저장 방식 설명" --provider codex --path /project --session SESSION_ID
python3 .loop/loop.py work goal WORK_ID --input /temporary/goal.json
python3 .loop/loop.py work start WORK_ID
python3 .loop/loop.py work delegate WORK_ID --input /temporary/assignment.json --path /project
python3 .loop/loop.py work finish WORK_ID --input /temporary/result.json
```

스킬 활성화에서 받은 `--session SESSION_ID`를 첫 작업부터 사용합니다. 여러 디렉터리는 `--path`를 반복합니다.
`--provider`는 대화, 선택적인 `--worker`는 작업 CLI이며 생략하면 같은 공급자를 씁니다.
위임은 [위임 규칙](../rules/delegation.md)의 필드를 모두 채웁니다. 여러 경로에 연결된 작업은
위임할 때 한 경로를 명시해야 합니다. 도구 결과를 확인한 후 전체 완료 조건을 기록합니다.
유형은 `document`, `explanation`, `research`, `review`, `planning`입니다.
`development`는 기존 개발 엔진에 위임하므로 `work finish`로 완료할 수 없습니다.

| 입력 | 필드 |
| --- | --- |
| 목표 | `outcome`, `deliverable`, `criteria[{id, description}]`, `decisions`, `open_questions`, 선택적 `execution` |
| 결과 | `summary`, `outputs`, `checks[{criterion_id, satisfied, evidence}]`, 선택적 `knowledge` |
| 결정 | `answer`, `basis`, `applies_to`, 원래 답변의 `evidence` |

`goal_setting → working → completed`로 진행하며 `blocked`·`aborted`도 기록합니다.
미해결 질문이 있으면 시작하지 않습니다. 시작한 목표는 고정됩니다. 모든 완료 조건을 근거와 함께
확인해야 완료할 수 있으며, 미충족 조건은 작업 중 상태로 남습니다. 별도 검토 모델이나 반복 횟수는 없습니다.
내용 충족 여부는 에이전트가 실제 전달물을 확인해 기록하는 것으로, 스크립트의 독립 검증은 아닙니다.

`execution`의 기본 한도는 `max_worker_calls: 40`, `worker_timeout_seconds: 600`,
`max_run_seconds: 14400`입니다. 작업 시작 뒤에는 고정되며 새 위임 ID나 재개로 초기화하지 않습니다.

상태는 `work context WORK_ID`, 발견 사항은 `work note WORK_ID --text TEXT --evidence REF`,
장애는 `work block WORK_ID --reason TEXT`로 기록합니다. 복구는 `work resume WORK_ID`,
종료는 `work abort WORK_ID --reason TEXT`입니다. 완료 결과는 파일 변경 이후에도 과거 기록으로 남습니다.

## 지식 항목

다음은 형식 예시이며, 실제 기록에는 직접 확인한 파일과 결과를 씁니다.

```json
{
  "id": "notes-storage",
  "kind": "fact",
  "scope": "project",
  "status": "confirmed",
  "summary": "메모는 로컬 JSON 파일에 저장되며 다음 실행에서 다시 읽습니다.",
  "why": "프로그램을 종료해도 저장한 메모를 조회할 수 있습니다.",
  "uncertainty": "동시 쓰기 충돌은 아직 검증하지 않았습니다.",
  "evidence": ["src/storage.py:save/load", "tests/test_storage.py:test_reload"],
  "paths": ["src/storage.py", "tests/test_storage.py"]
}
```

| 구분 | 값과 의미 |
| --- | --- |
| 범위 | `global` 공통 선호, `project` 한 프로젝트, `checkout` 한 작업 트리, `session` 한 대화, `task` 한 작업 |
| 상태 | `confirmed` 근거가 있는 결론, `draft` 초안, `hypothesis` 가설, `superseded` 대체된 내용 |
| 종류 | `fact`, `decision`, `artifact`, `finding` |
| 선택 필드 | `why`, `details` 최대 다섯 개, `uncertainty`, 외부 정보의 `expires_at` |

결정은 원래 `basis`와 `applies_to`가 필요합니다. 같은 ID의 범위·소유 프로젝트를 조용히 바꿀 수 없습니다.
소스 분석은 전체 근거 파일을 `paths`에 넣습니다. `src/`는 파일 추가·삭제를 포함하고 `.`은 전체를
감시합니다. 외부 서비스의 실제 상태는 파일 해시로 알 수 없으므로 필요할 때 다시 확인합니다.

```sh
python3 .loop/loop.py knowledge context --path /project --session SESSION_ID
python3 .loop/loop.py knowledge remember --path /project --session SESSION_ID --input /temporary/topic.json --observed REVISION
```

분석 전에 받은 `revision`을 사용합니다. 분석 도중 파일이 달라지면 저장을 거절합니다.
변경 후 근거를 다시 확인해 같은 ID로 갱신합니다. 조회만으로 오래된 결론을 새 사실로 바꾸지 않습니다.
`work finish`에 새 `knowledge`와 `observed_revision`을 넣으면 결과와 지식을 한 트랜잭션으로 기록합니다.

## 조회와 읽기 쉬운 문서

| 명령 | 용도 |
| --- | --- |
| `knowledge context [--query TEXT] [--limit 20] [--offset 0]` | 현재 지식, 재확인 항목, 초안, 작업 이력 |
| `knowledge get topic\|task\|delegation ID` | 전체 단일 기록과 문서 복구 |
| `knowledge history topic\|task\|delegation ID` | 현재 기록과 이전 버전 |
| `knowledge projects` | 프로젝트·작업 위치 목록 |
| `knowledge attach --path PATH [--project PROJECT_ID]` | 디렉터리 연결·이동 후 위치 갱신 |

맥락 JSON은 분류별 기본 20개와 전체 개수, 남은 페이지를 반환합니다. Markdown에는 분류별 다섯 개와
나머지의 조회 경로를 표시합니다. 페이지 밖의 자료도 보존하며, 읽기 전에 없다고 판단하지 않습니다.

`views/task/<id>.md`는 결과·상태·근거·다음 에이전트 행동을, `views/topic/<id>.md`는 결론·범위·상태·근거를
표시합니다. `views/delegation/<id>.md`는 위임 범위·산출물·완료 조건·적용 파일·장애를 표시합니다.
위임의 새 발견은 작업 범위의 초안으로 저장하며, 확인 후에만 확정 지식으로 갱신합니다.
`views/context/`는 조회 시점에 확인한 적용 가능성을 보여줍니다. 각 문서는 전체 JSON을 연결합니다.
확정 상태만으로 현재 유효하다고 간주하지 않으며, 재사용 전에 맥락을 확인합니다.

원본은 SQLite와 개발 상태 JSON입니다. Markdown은 이 데이터로 생성하며 수작업으로 편집하지 않습니다.
반복 조회는 같은 문서를 갱신하고 새 서술 문서를 쌓지 않습니다. 변화 없는 지식은 이전 시각과 버전을 유지합니다.
형식 검사는 빈 값·임시 문구·중복·길이 초과를 거절하고, 표현과 정확성은
[작성 규칙](../rules/writing.md)에 따라 확인합니다. 긴 설명 자체를 잘라내는 제한은 아닙니다.

## 프로젝트 이동과 보존

같은 Git origin의 HTTPS·SSH 주소는 프로젝트 하나로 묶고 작업 트리는 구분합니다.
origin이 없는 Git worktree는 공통 Git 디렉터리, 일반 디렉터리는 로컬 파일시스템 식별자를 사용합니다.
같은 파일시스템에서 디렉터리 이름이 바뀌면 다시 연결해 위치를 갱신합니다. 원격 주소가 없는 복사본이나
파일시스템 간 이동은 기존 프로젝트임을 확인한 뒤 `--project`로 연결합니다.

기본 지식 위치는 `~/.local/share/loop-dev/`이며 `LOOP_KB_HOME` 또는 전역 옵션
`--knowledge-home PATH`로 변경합니다. 개발 상태는 각 저장소의 `.loop/state/`에 남습니다.
백업·이동에는 필요한 두 위치를 포함합니다. 자동 클라우드 동기화는 없습니다.
내부 기록도 금지한 요청은 기록 없이 처리하며, 자격 증명은 지식에 저장하지 않습니다.
