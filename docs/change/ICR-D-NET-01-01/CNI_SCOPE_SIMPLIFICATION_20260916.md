# CNI 후속 범위 단순화 및 운영 보완 준비 — 2026-09-16

상태: **WORKFLOW_ADOPTED / PREPARATION_STARTED / OPERATIONAL_CHANGE_NOT_AUTHORIZED**.
사용자 지시: “정본 및 후보 근거해서 수용 할 수 있는 부분을 적용하고 착수”.
이 지시는 정본과 승인 입력에 부합하는 절차 정리·사전 준비의 근거다. 검증 생략, 신규 광역 허용, CP1 인수, CP2/CP3 적용 또는 Join 승인으로 확대하지 않는다.

## 1. 기준과 이번에 바로 반영하는 사항

- 운영 정본: [v3.1.18 §6.6/6.6.1 및 Interface Register](https://github.com/321Team/fds-project-baseline/blob/72599ae49342b0fb8dbd3d8ee60314afb9f907bb/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md).
- 후보 확인 기준: [v19 PR #73, c66b077](https://github.com/321Team/fds-project-baseline/pull/73). 후보의 정본 파일은 v18과 같은 Git blob이다. 후보 수집 자체는 발행·Runtime 승인이 아니다.
- 승인된 최신 입력: [Lead 결정 5697547621](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621), [후보 수집 원문](https://github.com/321Team/fds-project-baseline/blob/c66b077c41cc2f0ffd71d5c294a8ff9319f239e4/audit/v3.1.19/intake/p1-k8s-mon-lead-inputs-20260916.md).
- 개선/예외 요청: [PR #11 댓글 5698266772](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5698266772), 2026-09-16 22:38:37 KST 수정본. 요청과 승인을 구분한다.

| 제안 | 이번 적용 | 유지하는 조건 |
|---|---|---|
| 진단 도구 단순화 | PR #11의 신규 범용 기능·저수준 daemon 확장·추가 VM 패키지 배포를 보류한다. R5 완료 증적과 R6 수정본은 보존한다 | 해당 실행기를 사용한다면 기존 qualification 검사와 독립 복구 조건은 그대로 필요 |
| 전체 진단 도구와 CP1 운영 보완 분리 | PR #11 전체 완성을 모든 운영 보완 경로의 공통 선행조건으로 두지 않는다. 아래 별도 운영 변경안을 먼저 구체화한다 | 승인된 진단 결과를 운영 인수로 재분류하지 않음; 대안도 실제 변경·인수 증적 필요 |
| 기존 방식 5개 CNI 시험 재사용 | 같은 노드 직접 HTTP 양방향·Service HTTP·DNS 양쪽을 Positive 회귀 목록으로 재사용한다 | 5/5만으로 Negative·관리/API 회귀·복구·CP1 인수나 전체 ICR AC 완료를 선언하지 않음 |
| Kubernetes 작업 병행 | Worker 준비·manifest/코드 검토·자체 선행조건을 충족하는 작업을 병행한다 | 최신 Lead 입력에 따라 HEP/GNP 실제 적용은 3CP+3Worker Ready·6/6 Join 뒤 별도 실행창/승인 |
| 반복 리뷰/수집 축소 | 규칙·인수·복구·영수증 호환성을 한 변경 묶음으로 검토한다. 기존 자료는 해시와 적용 상태를 대조해 재사용한다 | 소스/상태/작업창이 바뀐 실행 직전 입력만 새로 확인; 같은 증적 재제출을 요구하지 않음 |
| 사람이 지켜보므로 독립 복구 제거 | 적용하지 않는다 | 기존 진단 실행기는 연결 단절 대비 복구를 유지. 대안의 보호·복구도 명시해야 함 |
| 일정상 인수 조건 생략 / cali 또는 Pod 서브넷 포괄 허용 | 승인 입력으로 편입하지 않는다 | 구체적 범위·기간·보호·회수·종료조건을 갖춘 별도 결정 필요 |

정본 §6.6.1은 CP1 제한 Runtime 검증과 최종 HEP/GNP 보호 전환을 구분한다.
PR #11의 7개 경로·6개 회수는 그 진단 실행기의 production qualification 계약이다.
이를 모든 대체 운영 절차의 공통 AC로 새로 부과하지 않으며, 현재 실행기의 검사를 삭제하거나 허위 qualification 파일로 우회하지 않는다.
최종 named HEP/GNP 및 6대 firewalld disabled/inactive 목표와 보호 전환 순서는 유지한다. 즉시 일괄 OFF 명령으로 해석하지 않는다.

## 2. 운영 최소 변경안 — 한 번에 확정할 입력

**대상 준비:** CP1 `k8s-control01` / `10.1.93.102`. 아직 실행 가능한 rule 명세나 승인 JSON이 아니다.
시험 Pod가 제거되면 사라지는 일시 /32 허용과, Join 이후에도 필요한 운영 CNI 보호 상태를 구분해야 한다.

| 입력 | 준비된 내용 / 남은 결정 | 담당 |
|---|---|---|
| 목표·범위 | 실제 CP1의 직접 Pod 통신 2건 실패 해소와 기존 Service/DNS·관리 기능 보존. 전체 보안/클러스터 인수와 구분 | 이권욱 / 이하영 |
| 허용 규칙 | 정확한 ingress/egress·source/destination·port·zone/policy·Runtime/Permanent·유지 기간은 아직 미확정. cali wildcard·Pod subnet trusted·전체 ACCEPT를 기본값으로 넣지 않음 | 이권욱 구체화, 이하영 K8S 영향 대조 |
| 보호·복구 | 기존 관리 경로·콘솔·백업을 연결하고 자기 변경의 역연산·부분 실패·연결 중단 대응·만료 시 종료 상태를 정의. HEP 미적용 단계의 보호 공백을 만들지 않음 | 이권욱 |
| 증적 재사용 | CP1 기존 적용/원복/재적용, p3dmjivl 3/5 결과, 7aqtz7d4/kav4i4cv 진단, R5 격리 6개 흐름을 출처별 보존 | 이권욱 |
| 변경 창의 확인 | CP1 receipt/current firewall·NM·Calico/Pod·Node/etcd/관리/API 상태, 대상/소유권, source·해시·실제 작업창·콘솔 확보를 한 번에 대조 | 실행 담당 |
| 인수 시험 | Positive 5개 + 최종 rule이 넓히는 범위의 Negative + 기존 CP 허용/차단·SSH/API·logs/exec/port-forward 회귀 + 실제 복구/종료. timeout만으로 차단 성공을 집계하지 않음 | 이하영/이권욱 |
| 증적 마감 | 실제 원본·결과·검토 범위를 한 묶음으로 제출. 홈폴더에 반출본과 SHA256 생성, root 원본 보존, 비밀·UUID 비식별 | 실행 담당 |
| 후속 소비자 | 아래 영수증 호환성 조건을 해결한 후 CP2 적용 입력을 작성. 기존 원본 영수증/검사를 편집해 통과시키지 않음 | 이권욱 |

이 변경안의 규칙·유지 기간·복구 종료 상태를 결정하기 전에는 mutation 명령을 생성하거나 배포하지 않는다.
이는 새 범용 도구를 개발하기 위한 대기 조건이 아니라 실제 바꿀 설정이 아직 정해지지 않았다는 의미다.

### 코드에서 확인한 후속 노드 차단

[고정 소스 run-node-firewall.py](https://github.com/321Team/infra-kubernetes/blob/1c9d17e3e581bc0ffd84d1c9ca511f59f831ded2/scripts/run-node-firewall.py)의 `cp1_state_gate()`는 다음을 요구한다.

```python
state["stable"]["firewall"] == receipt["after"]
```

따라서 새 CNI 규칙을 유지한 CP1은 기존 receipt.after와 다르면 후속 apply에서 거절된다.
이는 정적 코드 확인이며 CP2 Runtime 실패를 새로 재현한 결과가 아니다.
운영 변경안에는 **승인된 변경 상태의 새 증적/인수 계약과 후속 소비자 호환성**을 포함한다.
구체적인 새 규칙이 확정되면 원 receipt 연결·실제 after 상태·승인 범위 검증을 함께 설계한다.
전체 비교 제거, 임의 허용 목록 예외, 원 영수증 변조, 진단 영수증을 운영 영수증으로 대입하는 방식은 채택하지 않는다.
PR #10의 제한 진단·같은 창 완전 회수·인수 불가 조건은 이 문서로 변경되지 않는다.

## 3. CP2/CP3와 Join 후속 준비

기존 PR #4 계열 도구를 재사용하되 실제 배포 source/manifest는 승인된 후속 수정까지 대조한다.
최초 PR #4의 옛 생산자 pin을 최신 CP1 영수증에 그대로 사용한다고 가정하지 않는다.

| 계약 | 실제 범위 |
|---|---|
| IF-12A | CP .102/.103/.104 상호 및 kube-apiserver→etcd TCP2379–2380 |
| IF-12B | CP .102/.103/.104→Node TCP10250 HTTPS; CA/Auth·기능·Negative 별도 |
| IF-12 | API LB .109→CP .102/.103/.104 TCP6443. PR #4 계열은 기존 LB→6443을 보존하며 새로 추가하지 않음 |
| IF-11A | Node 6대→API LB .109:6443; 위 LB→CP 경로와 구분 |

[후속 실행 절차](../../runbooks/NODE_FIREWALL_FOLLOWUP.md)에 따라 CP1 Runtime 실제 인수·영수증 호환성,
노드별 새 inspect(15분 이내)·보호/콘솔/백업·최대 2시간 창·rollback 입력을 갖춘 뒤 **CP2 한 대 → 결과 검토 → CP3 한 대**로 진행한다.
정적 준비를 위해 지금 15분짜리 inspect를 받아 만료시키지 않는다.
Join/reset/member 변경은 방화벽 적용과 별도다. CP2 잔여 manifest·실제 data 경로·membership을 확인하고 무조건 reset하지 않는다.
포트 개방 성공은 etcd 건강·CA/Auth·6/6 Ready 또는 다음날 Join 성공 보장이 아니다.

NTP는 최신 Lead의 control01 단일 내부 UDP123·offset 100ms·60초×6·sync lost 5분·reboot 5분 내 재동기화 입력을 따른다.
control01 자체 upstream/time authority가 미정이므로 NTP 형상 적용은 보류한다. 읽기 전용 준비 전체를 막지는 않는다.
OID-01은 ansible01 10.250.10.10/32 + svc_ansible, 내부 SNAT 금지·Return Route 유지이며 HEP 실제 적용은 6/6 이후다.

## 4. 이번 완료·잔여

- 완료: 정본/후보/리드 원문 대조, 수용 범위 확정, 단일 운영 입력표·영수증 차단·CP2/CP3 포트/순서 준비, 관련 runbook 우선순위 수정.
- 실행 코드·policy·manifest 변경 없음. R6의 286/286은 기존 STATIC/MOCK 기록이며 이번에 새 Runtime PASS로 집계하지 않는다.
- R5 완료 증적과 R6 소스·검증 기록 보존. 추가 격리 start/recover/반입·CP1 변경·CP2/CP3 적용·HEP/GNP·Join·병합·발행은 수행하지 않았다.
- 남은 실제 결정: CP1 운영 rule/유지 기간/회수 종료 상태, 이에 맞는 증적·소비자 계약, 현재 상태 대조와 해당 변경의 인수.
- v19는 이 절차 채택을 APPROVED_INPUT으로 수집한다. 검증 생략·광역 허용·CP1 ACCEPTED·CP2 실행 예외는 PENDING_DECISION이다. 기존 AC/Done/Runtime 판정을 상향하지 않는다.
