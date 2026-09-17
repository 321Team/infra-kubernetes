# CP 세션 일괄 실행·사후 결과 검토 제안

상태: 새 정책·소스의 독립 리뷰 요청. 현재 서버 적용이나 ICR 인수 완료를 뜻하지 않는다.

## 해결할 문제와 최소 절차

CP1 v2 실측에서 apply, 5-flow 5/5, HOST/외부 회귀, handoff가 완료돼도 리뷰어 부재로 CP2·CP3 준비가 멈추고 첫 인수 deadline에 회수되는 문제가 있었다. 교육장 오전 Kubernetes 작업을 위한 방화벽 준비를 한 번의 실행으로 연결한다.

1. 이 소스와 아래 실행 위임 정책을 **한 번에 독립 검토**한다.
2. 운영자는 실제 유인 작업 종료시각·복구 예산과 기존 백업/콘솔 등 현장 조건을 한 번 확인한다.
3. 도구가 실제 권한 댓글을 기록하고, 이전 Job 복구 확인 → CP1 새 적용·시험 → CP2 → CP3 → 결과 파일 하나를 생성한다.
4. 독립 결과 검토·최종 ICR 인수는 사후로 남긴다. 승인된 세션 안에서는 CP1 결과 리뷰 대기만으로 CP2·CP3 방화벽 준비를 막지 않는다.

같은 백업을 다시 만들거나 완료 자료를 단계마다 다시 제출하지 않는다. 변경 뒤 검증은 새 상태의 정확성을 확인하므로 자동으로 한 번 수행한다. 읽기 전용 검사 실패는 실행 횟수를 소모하지 않으며, 변경 시도 후에는 자동 재시도하지 않는다.

## 검토할 권한

| 항목 | 신규 v4 세션 |
|---|---|
| 독립 소스/정책 승인 | hayeounglee가 이 PR의 정확한 HEAD를 APPROVED로 리뷰 |
| 위임 범위 | GWONUK의 한 유인 평일 세션: CP1 최소 Runtime apply·시험·복구, CP2 후 CP3 Runtime 방화벽 |
| 대상별 현재 상태 | 도구가 fresh inspect/전체 상태/실제 결과를 검사; 별도 수작업 승인 JSON 편집 불필요 |
| 실제 실행 결정 | 로컬 확인 뒤 GWONUK GitHub 계정으로 정확한 source/대상/시각/범위 댓글 1회 |
| 결과의 독립 인수 | `PENDING`; 운영자 결정을 독립 인수로 표시하지 않음 |
| Join/reset/Worker/HEP/GNP/Permanent 승격 | 이번 실행 위임에 포함되지 않음 |

리뷰 본문에 다음 한 줄을 넣은 **APPROVED 리뷰 1개**로 소스와 위임 의사를 함께 남긴다. 별도 정책 승인 댓글이나 노드별 승인 댓글은 요구하지 않는다.

```text
CP_SESSION_DELEGATION_APPROVED / source <검토한 정확한 40자리 HEAD>
```

도구는 실제 GitHub 사용자, PR HEAD, 최신 독립 리뷰의 계정·상태·commit·본문·시각·URL을 조회한다. 과거 HEAD의 승인, 운영자의 자기 승인, 새 CHANGES_REQUESTED/DISMISSED, 근거 없는 APPROVED JSON은 실행 권한으로 사용하지 않는다. 실제 리뷰가 없으면 SSH와 변경을 시작하지 않는다. JSON은 인증 수단이 아니며 SSH/sudo는 별도의 호스트 접근 경계다.

## 타이머와 복구

- 새 v4 Job은 `window_end - recovery_margin_seconds`에 **한 번의 최종 복구**를 예약한다. 별도의 리뷰 대기 조기 회수 시각이 없다.
- 종료시각은 운영자가 실제 작업·Kubernetes 인계까지 대응 가능한 **당일 KST 평일 시각**으로 정한다. 고정 90분 제한이 아니다.
- 여유 최소 180초는 기존 복구 서비스의 `TimeoutStartSec=180s`와 연계된 구현 하한이다. 180초 내 현장 복구 성공을 보증하지 않으며 필요한 현장 대응 예산을 넣는다. AC-19가 90분이나 특정 회수시각을 요구한다는 주장은 하지 않는다.
- 최종 타이머는 첫 방화벽 변경 전에 확인한다. 사후 리뷰 도착이나 CP2·CP3 완료는 이 시각을 연장하지 않는다.
- 기존 v1/v2/v3 Job에 소급 적용·타이머 교체를 하지 않는다. 현재 `12f18e7d72f6c7f4`는 원래 frozen source로 실제 `RESTORED_REVIEW_REQUIRED`, cleanup 완료, timer 해제, 전체 before-state 일치가 확인된 뒤 새 Job을 시작한다. 시각만 보고 복구 완료로 간주하지 않는다.
- CP2·CP3는 기존 Runtime 규칙 적용/정확한 역순 복구 방식을 유지한다. **CP1 세션 타이머가 CP2·CP3 규칙까지 회수하는 것은 아니다.** 해당 노드의 rollback receipt와 관리 SSH 결과를 함께 남긴다.
- 새 세션에는 `accept-retention`을 사용하지 않는다. 사후 결과 승인은 증적 검토이며 세션 연장/영구화는 아니다.

## 정본과 적용 범위

CP1 규칙·보호·복구 동작, local `10.244.246.64/26`/`cali+` 최소 구성과 5-flow runner를 유지한다. CP2·CP3는 기존 PR #4 정책의 CP `.102/.103/.104` `/32` → TCP `2379–2380`, `10250` 여섯 Runtime rich rule/노드를 적용하며 기존 LB `.109`→6443 및 Permanent 상태를 보존한다.

원 `node-firewall.py`, `run-node-firewall.py`, analyzer, PR2 producer는 바이트를 보존한다. 세션 권한을 해석하는 helper는 `node-firewall-session.py`로 분리하여 기존 receipt의 source pin을 깨지 않는다. legacy 승인 경로는 그대로 검증한다.

기존 CP1 예외(v3)는 CP1만 유지하는 범위였다. 이 후보는 **추가된 CP2·CP3 실행 위임과 타이머 정책을 새로 승인받아야** 하며 이전 승인으로 자동 승계하지 않는다. 정본 네트워크 범위를 바꾸지는 않지만 기존 실행·인수 절차를 변경하므로 독립 정책 검토가 필요하다.

## 성공·실패와 오전 인계

성공 상태는 `CP_FIREWALL_HANDOFF_REVIEW_PENDING`이며 `cp1_accepted=false`, `independent_acceptance=PENDING`이다. CP2 완료 receipt의 해시를 CP3 authorization에 묶는다. CP1 전체 live-state/타이머를 대상 변경 전후에 확인하고, CP2 실패 시 CP3를 실행하지 않는다. 실패가 발생한 노드의 원본 증적을 통합 partial archive에도 보존한다.

`K8S_HANDOFF.json`은 CP1 5-flow/회귀, 노드별 방화벽 적용 결과, 실제 유효 종료시각과 미검증 범위를 전달한다. **방화벽 준비 완료는 kubeadm Join 성공·교차 노드 CNI·etcd 정상·재부팅 지속성·ICR 전체 AC 완료를 뜻하지 않는다.** Kubernetes 담당자의 기존 Join 절차와 완료 판단을 대체하지 않는다.

오전 차단 해소는 새 정책 승인과 현장 실행 PASS, 담당자가 사용할 시간까지 유효한 실제 작업창이 모두 있어야 확인할 수 있다. 현재 후보의 정적 시험만으로 이를 완료 처리하지 않는다.

실행 방법: [한 번에 실행하는 CP 세션](../../runbooks/CP_SESSION.md).
