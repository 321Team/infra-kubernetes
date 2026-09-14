# PR #2 실행창·복구 후속 — 2026-09-14 UTC

판정: **R1 코드 보완 + R2 복구 절차 보완 제출 / 새 HEAD 독립 리뷰 대기**. 사용자 `후속 작업 시작` 지시에 따른 변경이다. 서버 실행·병합·실제 인수 승인 기록은 아니다.

## 문제와 변경

09-13 검토 대상 `097ddb56d87b229a9eb465f72ea9f55d16524c51`은 controller에서만 작업창을 확인했다. 원격 helper는 승인 해시만 받아 만료를 판별할 수 없었다. 00:59:59 요청, 01:00:00 종료, 각 mock 변경 1초인 기존 재현에서 총 6개 중 4개 변경이 종료 이후 시작하고 성공 상태로 끝났다.

- 승인 JSON의 정확한 원문 바이트와 SHA-256, 입력 해시를 전달하고 remote에서 source/mode/input·검토 기록·조건·실행창을 다시 검증한다. controller와 remote가 동일 validator를 사용한다. 별도 출력 `AUTHORIZATION.json`도 실제 입력 바이트를 보존한다.
- 실행창은 `[start, end)`로 검사한다. Backend의 모든 명령 timeout을 남은 wall-clock/monotonic 시간의 최솟값으로 제한한다. 매 새 쓰기 직전에도 확인하여 snapshot/query/영수증 기록 사이의 만료를 차단한다.
- 변경 결과를 확인 query 전에 영수증에 보존한다. 확인하지 못하면 `add_uncertain`/`remove_uncertain`, 전체 결과는 `PARTIAL_*_REVIEW_REQUIRED`로 남는다. apply·rollback의 승인 해시와 작업창을 각각 보존한다.
- [원본 보존·비상 복구](../../runbooks/CP1_CANARY_RECOVERY.md)에 원본 checkout/bundle 또는 ZIP/lock 확보, 정확한 source 복원, 별도 rollback 창, API/SSH 장애의 콘솔 진단, 개별 복구 결정·종료 증적을 정리했다. source/topology/drift/소유권 가드는 유지한다.

영수증 schema `fds.cp1-canary-receipt.v2`, phase=runtime, 고정 6개 연산과 정책 SHA는 유지한다. 창 기록은 추가 필드다. 새 생산자 helper SHA는 `0454a238d7451c86362dd98a54d3ed15cbf7f5d78c921cd6b93447581fc40ac2`이며 PR #4의 생산자 commit/helper pin과 mock fixture·계약을 함께 갱신한다. 구 생산자 결과를 새 commit으로 재표기하지 않는다.

## 검증

Local Linux / Python 3.12.14, **49/49 PASS, exit 0**: 기존 37개 + R1/R2 관련 새 12개. 변경된 controller/helper와 기존 계획·상태·소유권 회귀를 대상으로 했다. [전체 출력](CP1_WINDOW_TEST_RESULT_20260914.txt), [환경·명령·소스 SHA-256](CP1_WINDOW_TEST_ENVIRONMENT_20260914.json), [새 시험](../../../tests/test_cp_window.py).

| 새 시험 범위 | 확인 |
| --- | --- |
| 원문 승인 전달·정상 실행 | JSON 공백을 포함한 해시 유지, 정상 Runtime 6개, 영수증 바이트 해시 일치 |
| 전송 중 만료 | 종료 정확 시각 및 이후 remote Backend 생성 전 STOP, 조회/쓰기 0 |
| 적용 도중 만료 | 같은 1초 재현에서 변경 시작 1개, 종료 이후 시작 0, 부분 영수증 보존 |
| intent 후 만료·확인 전 만료 | pending 쓰기 차단 / rc를 보존한 uncertain, 자동 회수 차단 |
| 입력·승인 tamper | digest/source/mode/input/target/조건/작업창 불일치 시 remote 시작 차단 |
| 시계·명령 timeout | wall clock 역행으로 elapsed 예산 연장 불가, 전진 시 중단, 명령 timeout ≤ 남은 1초 |
| 별도 복구 | 별도 mode/input/창의 정상 복구, 중간 만료 후 제거 중단·부분 상태 보존 |
| source/API 장애 | 다른 source 또는 API readiness 실패 시 제거 0 |

새 시험은 실제 host command와 socket을 금지하고 가짜 Backend 및 파일을 쓰는 시험용 Journal을 사용한다. production root ownership/fsync와 실제 firewalld/etcd를 통합 실행한 결과는 아니다. 과거 **현장 Linux 37/37 + 읽기 전용 115개** 증적은 09-13 수용 범위를 유지하며 재수집하지 않았다. 새 49개를 현장 증적 수로 합산하지 않는다.

## 영향과 잔여

| 영역 | 영향 |
| --- | --- |
| OS·SSH·DNS·Inventory | 대상/접속/Ansible source `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab` 불변. 원격 명령에 시간 예산 전달 |
| CP 방화벽 | CP1 전용, CP .102/.103/.104 각각 /32→2379–2380·10250 Runtime 최대 6개. 기존 SSH/LB·Permanent 보존 |
| 다른 실행기 | PR #4 생산자 변경에 맞는 새 pin·window 계약 검증 |
| baseline·보호 인계 | 발행 정본 v3.1.18 §6.6/6.6.1 적용. HEP Bootstrap Allow→canary Worker→Workers→CP 순서 유지 |

이하영의 **새 HEAD 독립 리뷰**, 실제 링크/입력의 사람 대조, 실행창에서 유효한 inspect·보호/콘솔/백업/복구·실행 결정, 실제 CP1 apply/rollback·Positive/Negative/Regression 인수가 남는다. 새 코드를 구 승인값으로 실행하지 않는다. PR #4의 구 영수증·Permanent 두 수정에 대한 09-13 CLOSED_STATIC 수용은 당시 범위로 보존한다.

시간 제한은 새 명령 시작과 subprocess 대기를 제한한다. 이미 firewalld에 전달된 요청의 원자적 취소나 만료 시 자동 복원은 보장하지 않는다. 만료 뒤에도 영수증 보존은 수행하며 모호한 상태는 복구 절차로 연결한다. 입력 형식/해시 검사는 GitHub 승인 진위를 인증하지 않는다.

CP2/3·Worker 가입, NTP 목적지/운영 주체 및 수치 인수, HEP/GNP·Permanent 승격, IF-06B·IF-16, 기존 AC/Done 판정은 실제 근거가 생길 때 해당 티켓에서 갱신한다.
