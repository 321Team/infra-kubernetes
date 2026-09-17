# CP1 → CP2 → CP3 세션 실행

이 패키지는 새 정책/소스 리뷰 대상이다. 실제 리뷰 확인을 통과해야 실행된다. 승인 값을 직접 채우거나 기존 Job 타이머를 바꾸지 않는다.

## 현장 실행

ansible01의 `server01_ansible` 계정에서 전달된 압축 파일과 `.sha256`을 같은 디렉터리에 두고 SHA256를 확인한 뒤 한 번만 압축을 푼다. 기존 폴더 위에 덮어쓰지 않는다.

압축 폴더에서 다음 명령 하나를 실행한다.

```bash
python3 -I -B RUN_CP_SESSION.py --execute
```

프로그램은 GitHub CLI `gh`의 실제 GWONUK 계정과 새 PR HEAD 승인부터 확인한다. 승인이 아직 없으면 서버 변경 없이 종료한다. 준비 작업만 확인하려면 무인자 실행이 `PLAN_ONLY`다.

실제 실행 시 **현재 KST**를 표시하고 당일 작업·인계·복구 종료시각과 복구 여유 초를 입력받는다. 오전 Kubernetes 작업을 지원하려면 그 작업을 포함하여 운영자가 대응 가능한 시각을 사용한다. 예전 `04:00/06:00` 값을 관성적으로 재사용하지 않는다. 날짜를 다음 날로 자동 해석하지 않는다.

마지막 확인은 한 번이다. CP1·CP2·CP3 기존 백업, 콘솔, 보호/복구 경로와 해당 종료시각까지 대응 가능함, 작업 중 병행 변경 없음이 사실일 때 `ICR-CP-SESSION-GO`를 입력한다. **CP2·CP3 복구 조건은 CP1 백업이 있다는 이유로 자동 충족되지 않는다.** 이미 있는 유효 자료를 재사용하며 같은 백업을 다시 만들라는 요구는 없다.

실행 흐름은 자동이다.

1. 기존 Job 실제 복구 상태 확인 → frozen source/원 receipt → fresh inspect.
2. CP1 apply → 기존 5-flow 1회 → HOST/외부 회귀 → root 세션 결정.
3. CP2 inspect/apply/관리 SSH·전체 상태 확인 → CP2 receipt를 묶어 CP3 동일 처리.
4. 노드별 결과와 유효시각이 담긴 `K8S_HANDOFF.json`, 통합 `.tar.gz` 및 `.sha256` 출력.

검사·입력 파일 복사·해시·authorization 작성·결과 취합을 운영자가 단계마다 수행할 필요가 없다. `gh`가 없거나 인증이 다르면 권한 확인이 불가능하므로 설정을 우회하지 않는다. 실제 리뷰어 부재를 APPROVED로 기록하지 않는다.

## 결과 처리

성공 시 `STATUS=CP_FIREWALL_HANDOFF_REVIEW_PENDING`과 CP2/CP3의 `RUNTIME_APPLIED_REVIEW_REQUIRED`를 확인한다. 제출은 최종 출력의 **archive와 SHA256 한 쌍**이다. Kubernetes 담당자에게 방화벽 준비 결과와 `valid_before`를 함께 인계한다. 이 도구는 Join/reset을 실행하거나 Join 가능 판정을 보증하지 않는다.

`STOP_REVIEW_REQUIRED`면 생성된 `.partial.tar.gz`를 기준으로 멈춘 위치/실제 상태를 확인한다. 읽기 전용 단계에서 멈췄으면 원인을 해결한 뒤 같은 패키지로 다시 실행할 수 있다. 변경 시작 후의 `EXECUTION_ATTEMPT.json`을 지우거나 임의 reload/재시도를 하지 않는다. CP2 실패 뒤 CP3는 자동으로 진행되지 않는다.

기존 Job이 아직 ACTIVE라면 그 Job의 승인된 기한/복구 경로를 유지한다. 새 소스로 기존 Job을 takeover하거나 조기 복구하지 않는다. 새 세션의 최종 복구도 담당자에게 표시한 시각에 수행하며 결과 리뷰가 늦었다는 이유만으로 앞당겨 수행하지 않는다.

이 패키지의 사후 리뷰는 세션 연장·영구화·ICR 종결을 뜻하지 않는다. [정책 및 검증 범위](../change/ICR-D-NET-01-01/CP_SESSION_REVIEW_20260918.md)를 참조한다.
