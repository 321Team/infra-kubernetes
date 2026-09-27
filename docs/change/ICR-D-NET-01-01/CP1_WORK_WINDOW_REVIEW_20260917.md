# CP1 교육장 작업창·인수 후 유지 — 통합 리뷰 요청

2026-09-17 / 상태: **CODE_REVIEW_REQUIRED / STATIC_MOCK_ONLY**

기준 소스: `4aedf2503fae5549f9067251290499e3fa09c631`, 기존 PR #11.
사용자는 고정 90분과 반복 자료 제출이 교육장 CP 구현을 지연하므로 작업창·인수 후 유지·후속 CP 연계를 함께 고쳐 착수하도록 요청했다. 이 기록은 새 코드의 리뷰 자료이며 새 현장 실행 승인이 아니다.

## 유지하는 최소 구성과 정본 경계

[리뷰어 최소 구성 요청](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5707194172)의 CP1 최소 변경 → 기존 5-flow → 새 실제 인수 → CP2/CP3 순서를 유지한다.

- CP1 `cali+`, local `10.244.246.64/26` 양방향 주소/인터페이스 교집합, 기존 HOST/public·ens160 보호, 비활성 Permanent staging 후 제거는 동일하다. 새 방화벽 rule/zone/policy 범위를 추가하지 않는다.
- 원 CP1 생산자, legacy node helper/controller, trial analyzer, 노드별 정책은 바꾸지 않는다.
- CP2/CP3 신규 범위는 기존 CP /32→TCP2379–2380·10250이다. LB→CP6443(IF-12)는 기존 보존 항목이다.
- 정본의 최종 named HEP/GNP·6대 firewalld disabled/inactive 목표나 6/6 Ready 후 HEP/GNP 별도 인계를 대체하지 않는다. Runtime-only의 임시 유지 개정이다.
- 새 상시/부팅 service, #13 전체 서비스 확장, Permanent promotion, Join/reset, merge는 범위 밖이다.

## 시간값의 출처와 새 계약

고정 90분은 앞선 현장 실행 wrapper의 `end = start + timedelta(minutes=90)` 입력 생성값이었다. 기준 core의 v1 `check_authorization()`은 최대 7200초와 180초 전 회수를 검사한다. v1 consumer의 900초 잔여시간도 구현값이다. 이 숫자들을 정본의 필수 최적값이나 교육장 실측 완료시간으로 해석하지 않는다.

| 항목 | 새 v2 동작 | 이번에 바꾸지 않는 항목 |
|---|---|---|
| CP1 작업창 | 승인된 당일 시작/종료시각, 고정 90분 및 2시간 전체 상한 없음 | 평일·유인·bounded 조건 |
| 인수 후 유지 | 사전에 승인된 더 늦은 `retention_until` | 무기한/야간/주말/다일 유지 미지원 |
| 복구 여유 | 명시 입력 `recovery_margin_seconds >= 180` | 기존 lock30초/복구150초/서비스180초 예산, 현장 보장값 아님 |
| 첫 회수 시각 | `window_end - margin`; 미인수면 복구 | 실패·불완전 증적의 fail-closed |
| 최종 회수 시각 | `retention_until - margin`; 인수됐어도 복구 | 기존 소유권/whole-state 복구 검사 |
| 후속 CP 허용 | 대상 승인 종료시각이 CP1 최종 회수시각보다 앞인지 확인 | 기존 대상별 최대 2시간 및 PR #4 승인 입력 |
| 과거 작업 | 새 소스로 연장/이관 불가 | v1 승인/receipt/타이머 이력 |

두 개의 일회성 transient timer를 **첫 방화벽 변경 전에** 예약한다. 첫 timer는 유효한 root 인수 기록과 최종 timer가 있는 경우에만 변경 없이 종료한다. 최종 timer는 기존 복구 경로를 실행한다. 인수 시 타이머 재예약 경쟁, 자동 갱신 loop, 상시 watcher를 만들지 않았다.

v2 승인의 `window_start < window_end < retention_until`은 같은 KST 평일이어야 한다. 정해진 4시간/8시간 기본값을 자동 삽입하지 않는다. 운영자와 리뷰어가 실제 담당 가능 종료시각을 입력한다. 새 실행창 개시 시점에는 `margin + 420초` 이상이 아니라 **그보다 많은** 잔여시간이 필요하며, locked status는 deadline 전 360초 이하에서 시작하지 않는다.

## 인수/보존 계약

`accept-retention`은 실제 trial archive, immutable HANDOFF, ACCEPTANCE의 source/job/evidence/lifetime을 검증하고 현재 전체 after-state·timer를 확인한 뒤 root `ACCEPTANCE.json`과 마지막 커밋 표시 `RETENTION.json`을 원자적으로 기록한다.

- 원 CHANGE/HANDOFF는 수정하지 않는다. 과거 `cp1_accepted=false` 필드를 임의 true로 변경하지 않는다.
- 첫 deadline 전 실제 인수·root 커밋이 있어야 한다. 늦은 커밋은 별도 LATE 파일로 보존하며 유지 성공으로 인정하지 않는다.
- 같은 인수 입력의 재호출은 기한을 늘리지 않는다. 다른 인수/증적, 잘못된 소유자·심볼릭 링크·변조·누락 기록은 유지 근거가 되지 않는다.
- 소비자는 실제 status `ACCEPTED_RETAINED`, root acceptance/handoff hash, 최종 timer, whole CP1 after-state를 대조한다.
- JSON의 reviewer/APPROVED/해시는 실제 GitHub 승인자를 인증하지 않는다. 실제 리뷰 링크·작성자·검토 소스를 대조하는 기존 신뢰 경계를 유지한다.

## 줄이는 절차와 남기는 판단

1. 코드·창 정책·후속 범위를 기존 PR #11에서 한 묶음으로 리뷰한다. 준비 중 fresh inspect/실행창을 미리 시작하지 않는다.
2. 기존 백업/콘솔/복구 증적은 유효성을 확인해 재사용한다. 구 job 정리 여부 읽기 전용 확인은 새 착수 preflight 안에 넣고 별도 승인 라운드를 추가하지 않는다. 미확인 회수를 성공으로 가정하지 않는다.
3. 착수 후 새 inspect→apply→기존 5-flow/회귀/필요 negative→handoff→실제 인수→root 유지 커밋을 같은 작업 흐름에 묶는다. 같은 증적을 형식 때문에 반복 제출하지 않는다.
4. CP2 결과 검토 후 CP3를 같은 승인된 유지창에 배치한다. 기존 PR #4 대상별 실행 승인/상태 해시가 필요하며 이번 PR #11의 소스 리뷰를 그 승인으로 자동 승계하지 않는다.

새 실측 결과의 독립 인수는 미래 결과를 사전 PASS로 쓰는 방식으로 없애지 않는다. negative drift/잔여 확인은 실제 deny/cross-node 시험이나 최종 AC 전체 통과와 구별한다. 실제 실행창의 시각/승인/새 상태는 코드 리뷰 이후 확정할 수 있다.

## 검증 및 한계

- 관련 선택 unittest **156/156 PASS**: 기존 119 + 새 작업창/인수/타이머 경계/소비자 시험 37. 이전 다른 upstream/XML 검증 수치와 합산하지 않는다.
- 명시적 장시간 당일 창, 잘못된 시각/주말/여유, v1 호환, 두 timer 선행 예약, 예약 부분 실패, 변경 실패별 전체 복구, 첫/최종 deadline, 인수 누락·변조·늦음, root 파일 원자 커밋/재조회, foreign drift, 대상 종료시각 경계, 실제 어댑터 함수의 전후 검사를 포함한다.
- 원자 파일 IO는 실제 임시 파일로 시험한다. firewall/systemd/SSH 외부 동작은 대역이다. 실제 RHEL/systemd daemon, nft kernel, timer 발화·재부팅·현장 복구 지연은 미검증이다.
- 관련 핵심 보호/정책 함수와 legacy 파일의 불변 검사는 [검증 JSON](CP1_WORK_WINDOW_VALIDATION_20260917.json)에 기록한다. 새 시험 [로그](CP1_WORK_WINDOW_TEST_20260917.txt).

재현(저장소 루트):

```bash
(cd tests && python3 -m unittest -v test_cp1_minimal_change test_cp1_work_window test_cp1_cni_trial_analysis test_node_firewall)
python3 -I -B scripts/run-cp1-minimal-change.py
git diff --check
```

이 변경에서 실서버 명령, 타이머 변경, 기존 job 수정, 새 Runtime 실행·인수, CP2/CP3/Join, CI PASS, 병합을 수행했다고 주장하지 않는다. 기존 job `d9c986fcbcd3b201`의 만료 이후 실제 회수 완료도 이 로컬 시험으로 증명되지 않는다.

## 요청하는 리뷰 결정

이하영(`hayeounglee`)에게 **새 소스 + 당일 가변 작업창 + 실제 인수 후 bounded 유지 + CP2/CP3 소비자 연계 + 위 절차 단순화**를 함께 검토 요청한다. 기존 승인 댓글을 새 HEAD 승인으로 재사용하지 않는다. 소스/정책 수용과 실제 실행창 승인, 실측 결과 인수의 범위를 리뷰에 구분해 주기 바란다.
