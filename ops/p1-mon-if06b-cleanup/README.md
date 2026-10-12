# IF06B 조건부 회수 판단 모듈 — 실행기 미포함

CODEX_ASSISTED · 2026-10-12. `P1-MON-01` / [baseline#17](https://github.com/321Team/fds-project-baseline/issues/17) 및 K8S·SEC 협업 범위의 로컬 사전준비다. [기존 canary 카드](../../docs/change/P1-MON-01/P1_MON_01_IF06B_WORKER03_CANARY_20261002.md)의 이름·UID·최신 resourceVersion 조건을 판단 코드로 분리한다. 기존 정책 YAML이나 실행 허용은 변경하지 않는다.

## 완료한 범위

`decision.py`는 파일·네트워크·프로세스·현재 시계에 접근하지 않는 순수 함수 모듈이다. 확인된 생성 응답과 별도로 검토한 spec 해시를 받아 소유 영수증을 만들고, 신뢰된 관측에 대해 WAIT / DELETE_INTENT / ABSENT / HANDOFF를 반환한다. 배포 명령이나 API 실행 CLI는 없다.

- 정확 이름 `p1-mon-if06b-worker03-to-monitor01-canary`의 HTTP201 생성 응답만 소유 근거로 받는다. 생성 timeout 뒤 GET으로 발견한 이름을 이 회차 소유로 채택하지 않는다.
- session·cluster·host boot·guardian epoch가 같은지, 관측이 최신인지 확인한다. 시간은 해당 boot의 monotonic 값이며 생성·만료·회수 종료·관측 유효기간을 호출자가 모두 제공한다. 실제 값이나 기본 예산은 생성하지 않는다.
- UID와 검토한 spec이 같을 때만, 방금 확인한 불투명 문자열 resourceVersion과 UID를 모두 가진 DeleteOptions를 만든다. UID 변경·spec 변경·finalizer·이미 삭제 중인 객체는 HANDOFF다.
- intent를 저장한 후에는 timeout·409·응답 유실 때 DELETE를 재발행하지 않는다. HTTP200/202도 회수 완료가 아니다. 정확 API 대상의 명시적인 NotFound readback만 ABSENT다. 403·빈 응답·잘못된 404는 성공으로 취급하지 않는다.
- ABSENT는 해당 canary의 부재만 뜻한다. 전체 정책·연결성·기준 복귀 또는 AC 성공은 별도 관측이 필요하다.

Kubernetes의 [DeleteOptions](https://kubernetes.io/docs/reference/kubernetes-api/definitions/delete-options-v1-meta/)는 UID/resourceVersion precondition 불일치를409로 거부한다. GET 이후 객체가 바뀌어도 조건 없는 삭제나 이름만으로 된 fallback을 만들지 않는다.

## 통합 전 남은 작업

**이 모듈만으로 독립 만료 회수가 동작하지 않는다.** MON의 `docs/IF06B_REMAINING_TEST_INPUTS_20261009.md`에 남은 구현·독립 생존 검증 조건은 계속 미완료다.

미구현 어댑터는 다음 계약을 만족해야 한다. 이것은 새 팀 정책/권한이나 실행 승인 요청이 아니라 이 코드의 명시적인 신뢰 전제다.

1. 실제 검토한 manifest·API endpoint·현재 cluster와 관측의 출처를 결속한다. 호출자가 준 해시는 승인 증거를 스스로 만들지 않는다. API 응답의 기본값 적용 뒤 spec pin과 비교할 방법도 실제 서버에서 대조한다.
2. 시험 프로세스와 독립적으로 생존하는 실행 위치·프로세스 감독·연결 경로를 확정하고, 제한된 기존 자격증명으로 실제 create/get/delete 권한을 확인한다. credential을 산출물에 저장하지 않는다.
3. 생성 직전부터 단일 작성자와 보호된 durable journal을 유지한다. 생성 응답 전 유실, 생성 성공 뒤 영수증 저장 실패, intent 저장 뒤 전송 여부 미상은 현재 모듈로 자동 해결하지 않는다. 원본을 보존하고 회수 인수로 전환한다.
4. `DELETE_INTENT.next_receipt`를 영속화하고 성공을 확인한 뒤에만 한 번 전송한다. 저장 실패·부팅/epoch 변화·기한 경과면 전송하지 않는다. API 요청 timeout과 readback 예산도 현장 값으로 제한한다.
5. ACK 뒤에도 읽기만으로 명시적인 부재를 확인하고, 기존 기준 정책·허용/차단 연결성을 따로 확인한다. 모호한 결과를 강제 삭제·finalizer 제거·무조건 재시도로 해결하지 않는다.

따라서 자동 회수·IF06B 실행·MON/SEC 인수는 HOLD다. 실제 관리 환경·관측 지점·권한·회수 예산과 입력 검토 후 어댑터/통합 시험이 필요하다. 기존 카드의 canary 최대180초·관측120초 제안은 이 모듈의 승인 기본값이 아니다.

## 검증

저장소 루트에서 Python3.10+로 다음 대상만 실행한다.

```text
python3 -B -m unittest discover -s ops/p1-mon-if06b-cleanup/tests -v
```

모든 객체·시간·UID는 SYNTHETIC이다. 시험은 정상 만료와 조기 중단, stale/future 관측, 잘못된 입력, boot/epoch/cluster/session 변경, 다른 UID/spec,403/409/유실, finalizer, ACK 후 readback을 검증한다. 클러스터·소켓·API 호출 없이 수행한다. Linux CI는 이 범위만 실행하며 Runtime 검증·독립 REVIEW·MERGE·AC와 구분한다.

실서버 적용은 없다. 코드가 이상하면 사용을 보류하고 diff/시험 결과를 검토한다. 이미 생성된 실제 canary나 원본 영수증이 있을 때 이 모듈을 임의로 끼워 넣어 회수를 재개하지 않는다.
