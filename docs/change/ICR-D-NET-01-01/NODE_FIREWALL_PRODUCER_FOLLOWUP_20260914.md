# PR #4 생산자 계약 후속 — 2026-09-14 UTC

판정: **새 생산자 pin·실행창 계약 보완 제출 / 독립 리뷰·실서버 검증 대기**.

PR #2가 원격 실행창 통제를 추가하므로 기존 `097ddb56d87b229a9eb465f72ea9f55d16524c51` 고정값을 유지하면 새 CP1 영수증을 후속 Node 실행기가 수용할 수 없다. 구 영수증/Permanent 두 지적은 09-13 COMMENT 리뷰의 CLOSED_STATIC 수용 이력으로 유지하며 같은 결함을 재요구하지 않는다. 이번 변경은 새 생산자의 영향에 대한 별도 후속이다.

## 변경

- CP1 생산자 commit을 `f0388b1a372d19b5079e94ec6949ef443d774d2f`, helper SHA-256을 `0454a238d7451c86362dd98a54d3ed15cbf7f5d78c921cd6b93447581fc40ac2`로 고정한다. 실행 정책, 인수 template, 원본 fixture와 `PR2_PRODUCER_LOCK.json`, runbook을 함께 갱신한다.
- [새 생산자 변경·49개 검증](https://github.com/321Team/infra-kubernetes/blob/f0388b1a372d19b5079e94ec6949ef443d774d2f/docs/change/ICR-D-NET-01-01/CP1_WINDOW_FOLLOWUP_20260914.md)과 [원본 보존·비상 복구](https://github.com/321Team/infra-kubernetes/blob/f0388b1a372d19b5079e94ec6949ef443d774d2f/docs/runbooks/CP1_CANARY_RECOVERY.md)를 연결한다.
- 새 `authorization_window`의 timezone·종료 제외·최대 2시간, receipt 시작과 6개 연산 시작 시각의 창 내 포함을 검사한다. 결과/영수증 보존은 종료 뒤에도 수행될 수 있으므로 `ended_at`은 기존의 started≤ended≤현재 시각 조건을 유지한다.
- schema v2, Runtime 6개 연산의 순서·소유권·해시·전후 상태와 실제 독립 인수 기록 검증, 후속 Node의 Runtime 전용 변경/복구를 유지한다. Permanent 승격은 미구현이다. 구 receipt에 새 source/창 값을 덧씌워 통과시키지 않는다.

## 검증과 영향

Local Linux / Python 3.12.14, **57/57 PASS, exit 0**. 기존 Node 55개 + 새 producer lock/window 계약 2개다. [전체 출력](NODE_FIREWALL_PRODUCER_TEST_RESULT_20260914.txt), [환경·소스/정책/fixture SHA](NODE_FIREWALL_PRODUCER_TEST_ENVIRONMENT_20260914.json).

실제 pin된 생산자 소스를 fake Backend에서 실행한 v2 영수증으로 added/preserved 6개 연산 수용을 확인했다. 새 lock/fixture 바이트 대조와 해시를 다시 계산한 누락·잘못된 작업창·종료 시각 연산 거부를 추가했다. 직전 생산자 `097ddb5`의 receipt 거부, ownership/순서/상태/drift·모의 controller 실행/복구, Permanent 원격 호출 전 중단 경계를 함께 확인했다. fake 승인값은 실제 승인 증거가 아니다.

병합 PR #3 Worker 읽기 전용 수집기·19개 시험 소스는 변경하지 않았으므로 재실행하지 않았다. 종전 74개 기록과 이번 57개를 새로 76개 실행한 것처럼 합산하지 않는다. 09-10 현장 증적은 재수집하지 않았다.

| 영역 | 결과 |
| --- | --- |
| CP1 연계 | 새 commit/helper 계약. 정책 전체 SHA는 pin 변경으로 달라짐 |
| 후속 실행 범위 | 기존 CP2/3·Worker 각각의 Runtime 계획/Source/Port 불변 |
| OS·SSH·Inventory | 접속 방식·Ansible source `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`·기존 PR #1/#3 파일 유지 |
| 보호·정본 | 발행 v3.1.18 §6.6/6.6.1, D34/D35와 기존 AC·보호 인계 순서 유지 |

실행 시 새 PR #4 source·정책 SHA와 실제 CP1 source/receipt/인수 기록을 바인딩한 새 입력을 사용한다. 독립 리뷰어 이하영의 두 PR 최신 HEAD 판정, 실제 CP1 Runtime apply/rollback·Positive/Negative/Regression 인수 및 해당 작업창의 보호/콘솔/백업/복구·실행 결정은 남아 있다. JSON·해시 검사는 GitHub 승인의 진실성을 인증하지 않는다. 실서버 적용·merge·join/reset·NTP 확정·HEP/GNP·Permanent·전체 ICR Done을 수행하거나 승인하지 않았다.
