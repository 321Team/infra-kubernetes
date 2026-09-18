# CP2 적용 전 CP1 상태 요청 누락 수정

## 제출 증적과 판정

증적: `ICR_CP_Session_20260918T010834Z_72393be0.partial.tar.gz`

SHA256: `7b0799cf8117a10371204e8265db8dc31e9bf325bb984f4dac1080d510ec3aed`

외부 체크섬, CP2 inspect/apply 및 trial-kit 내부 체크섬, 5-flow 재분석, 원 receipt/handoff/session 결정 연결을 확인했다. 아래는 제출 증적의 관측 결과이며 이후 실시간 상태를 대신하지 않는다.

| 항목 | 증적상 결과 |
| --- | --- |
| 이전 CP1 Job `12f18e7d72f6c7f4` | 실제 복구 기록과 현재 상태 대조 PASS; recovery_armed=false |
| 새 CP1 Job `c564ae97530dfe80` | Runtime 적용 완료; 독립 결과 인수 PENDING |
| 5-flow | 5/5 PASS, firewall/health drift 없음 |
| HOST·외부 회귀 | 관리 SSH, ens160 public, API readyz, CP1 Ready, 외부 DNS/HTTPS PASS |
| 세션 지속 | 10:11:53 KST `CP_SESSION_ACTIVE_REVIEW_PENDING`, recovery_armed=true |
| CP2 inspect | PASS; CP1 stable SHA256 `69624e6f3802fdb97b33c0a7222267b61dc7a2af343722df45c3d81242b63154` |
| CP2 apply | 10:12:10 KST, 사전검사 STOP; runtime_applied=not_attempted |
| CP3 / Join | 미실행 |
| CP1 예약 회수 | 2026-09-18 15:15 KST; 운영자 종료 15:30, 복구 여유 900초 |

상위 RUN_STATE의 CP2 `APPLY_ATTEMPTED_OUTCOME_PENDING`은 하위 apply 명령을 호출하기 전에 기록되는 값이다. 하위 CP2_APPLY/RESULT.json은 `runtime_applied=not_attempted`이고, 실제 노드 변경 직전에 작성되는 REQUEST.json/TARGET.json이 없다. 따라서 제출된 실행에서는 CP2 방화벽 변경 경계에 도달하지 않았다.

## 원인과 재현

`CP2_APPLY/CP1_STATE_BEFORE.json` 자체는 transport=PASS, status=CP1_STATE_REVIEW_REQUIRED이며 CP2 inspect 때와 전체 stable 상태가 같다. 실패한 곳은 뒤이어 `cp1_minimal_consumer.remote_status()`가 수행하는 두 번째 CP1 상태 요청이다.

consumer는 `{mode: cp1-state, node: k8s-control01}`만 전달한다. 실제 `node-firewall-session.main()`은 읽기 전용 모드에서도 `policy`와 `policy_sha256`을 요구한다. 이 두 값이 빠져 helper가 backend 생성 전에 STOP을 반환하고, 컨트롤러가 이를 `remote result did not satisfy CP1_STATE_REVIEW_REQUIRED`로 표시한다.

두 번째 요청의 raw 응답은 기존 consumer가 저장하지 않았다. 동일 소스의 실제 helper dispatcher에 이 두 필드 요청을 넣은 재현에서는 error=`'policy'`, command_count=0으로 종료한다. 이는 저장된 첫 응답의 오류나 방화벽 drift로 해석할 근거가 없다.

기존 소비자 시험은 remote_status를 모의 응답으로 대체했다. 새 회귀시험은 remote_status 자체를 실행하고 실제 helper dispatcher와 연결해 요청 데이터 누락을 검증한다. 외부 명령과 backend의 현장 조회만 가상화한다.

## 수정 범위

변경하는 제품 코드는 `scripts/run-node-firewall-cni.py` 한 파일이다. 소스·정책 검증 직후 검증된 policy와 policy_sha256을 consumer의 CP1 읽기 요청에 결합한다. callback은 CP1의 cp1-state 요청만 허용하며 호출자가 넣은 다른 정책 값은 전달하지 않는다.

SSH 신원, 원격 정책 검증, 전체 stable 상태, frozen CP1 소스, 활성 세션/타이머, CP2·CP3 권한 및 receipt 검사는 유지한다. CP1 5개 frozen 소스, CP1 bundle `d229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41`, node helper, 방화벽 규칙 및 정책 파일은 바이트 그대로다.

검증: 기존 208개와 실제 consumer/helper 요청 경로 회귀 8개, **216/216 STATIC/MOCK PASS**. 누락 재현, 정상 전달, 잘못된 정책 해시, 호출자 정책 주입, 다른 호스트/변경 모드 거부, 전송 실패, frozen status 실패, 보호 상태 drift를 확인했다. 시험 로그는 `CP2_POLICY_REQUEST_FIX_TEST_20260918.txt`다. 수정 컨트롤러의 현장 Runtime 실행, CP2·CP3 결과, 타이머 실제 발화 및 전체 저장소 CI는 이 검증에 포함하지 않는다.

## 한 번의 수정 리뷰 및 이어서 실행할 범위

기존 소스 승인: https://github.com/321Team/infra-kubernetes/pull/14#pullrequestreview-5242208554

기존 실행 위임: https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723302935

실제 운영자 작업창: https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723463564

이번 수정은 새 컨트롤러 소스이므로 기존 cfae027 승인으로 수정된 파일까지 승인됐다고 표시하지 않는다. 이하영님의 이번 수정 HEAD APPROVED 리뷰에서 다음 재개 범위를 함께 판단한다. 승인 문구를 별도 위치에 다시 작성하도록 요구하지 않는다.

1. 현재 CP1 Job과 이미 성공한 5-flow/회귀/세션 증적을 유지한다. CP1 apply, 시험 또는 자동 회수 타이머를 다시 시작하거나 연장하지 않는다.
2. 실제 frozen CP1 상태와 원 결정/증적을 다시 대조하고, 기존 유인 작업창이 유효할 때 CP2 fresh inspect/authorization → apply/검증 → CP3 동일 처리를 진행한다.
3. CP2·CP3에 기존 범위 외 변경이 없고 topology·보호·전체 상태 검사를 통과해야 한다. 이번 제출에서 CP2 미변경이라는 사실만으로 이후 상태까지 단정하지 않는다.
4. 결과는 `cp1_accepted=false`, `independent_acceptance=PENDING`을 유지한다. Join/reset/영구화/ICR 종결을 수행하지 않는다.

기존 전체 실행 명령은 CP1 적용을 이미 시도했으므로 그대로 재실행하지 않는다. EXECUTION_ATTEMPT.json을 삭제하지 않는다. CP2부터 재개하는 실행 경로에서 기존 증적과 현재 상태를 연결해야 한다.
