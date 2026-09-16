# CP1 운영 보완·후속 인계 준비 — 2026-09-16

상태: **STATIC_HANDOFF_DEFECT_FIXED / OPERATIONAL_SCOPE_UNRESOLVED / RUNTIME_NOT_RUN**.
사용자 “착수” 지시에 따라 기존 진단 소스 확장 대신 배포 인수 계약을 직접 점검·수정했다.
이 문서는 [단순화 결정](CNI_SCOPE_SIMPLIFICATION_20260916.md)의 후속이며 새 실행 승인이나 CP1 인수 증명이 아니다.

## 1. 먼저 제거한 실행 입력 결함

현재 `scripts/node-firewall.py::CP1_CONTRACT`와 생산자 lock은 `264b273ab8ad79209bc0fec4a85c4497c946dbe3` / helper SHA-256 `a09efb38dbbc313124f6d42858c3ee3e7d676e933abe8ed0550bf8937fb18dcc`를 요구한다.
그러나 배포 `cp1-runtime-acceptance.template.json`과 runbook의 helper 설명은 이전 `f0388b1` / `0454a238…`를 가리켰다.
양식을 그대로 사용하면 실제 인수 내용을 채워도 `CP1 acceptance source/plan differs: source_commit`으로 차단된다.
기존 시험의 `acceptance_for()`는 검증기 상수를 직접 복사하므로 배포 양식의 노후화를 발견하지 못했다.

이번 변경은 양식의 source_commit/source_sha256 두 값과 runbook을 현재 소비자 계약에 맞춘다.
decision·review·run_id·증적 해시는 null, Positive/Negative/Regression은 NOT_VERIFIED로 유지한다.
helper/controller/생산자 실행 코드, 방화벽 정책, 원 영수증, CP1 상태 비교는 수정하지 않는다.
수정 양식은 기존 생산자의 호환 입력일 뿐, CNI 운영 규칙을 추가하는 새 인수 계약은 아니다.

새 시험은 배포 파일을 직접 읽어 다음을 검증한다.

1. 배포 pin이 소비자 계약·기존 생산자 lock/실제 소스 해시와 일치한다.
2. 빈 양식은 승인이 아니며 검증기가 거부한다.
3. source/plan pin은 덮어쓰지 않고 현장 입력만 모의로 채웠을 때 실제 생산자 코드의 모의 영수증과 호환된다. 구 commit 또는 helper 해시를 되돌리면 다시 거부한다.

변경 전 새 시험 포함 contract 파일 28개 중 1 failure·1 error로 문제를 재현했다.
수정 후 `test_node_firewall.py`와 `test_node_firewall_v2_contract.py` **61/61 STATIC/MOCK PASS**.
원본 로그: [실패 재현](CP1_HANDOFF_RED_20260916.txt), [수정 후 시험](CP1_HANDOFF_TEST_20260916.txt).
실행 코드가 같으므로 R6 전체 시험이나 VM 시험은 반복하지 않았다. 이 61개는 새 Runtime·CI·운영 인수 결과가 아니다.

`NODE_FIREWALL_SOURCE_SHA256SUMS`의 변경 양식·runbook·시험 해시를 갱신한다.
특히 앞선 447a267의 runbook 문구 변경도 이 manifest에 반영되지 않았으므로 이번에 함께 일치시킨다.
이번 전달 전 비교 대상과 해시 결과는 [검증 기록](CP1_HANDOFF_VALIDATION_20260916.json)에 기록한다.

## 2. 실제 현장 사실과 적용 가능한 규칙의 경계

- [p3dmjivl](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673301524): 직접 Pod HTTP 양방향 2건 실패, Service·DNS 양쪽 3건 성공. 시험 Pod·namespace·veth는 삭제됐다.
- [7aqtz7d4](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673425463) 및 [kav4i4cv](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673570001): Calico 허용 뒤 firewalld forward 경로가 차단 후보로 확인됐다. 당시 패킷의 유일한 원인을 입증한 것으로 확대하지 않는다.
- [R5 격리 증적](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5697557472): 합성 namespace의 허용/차단 6개 흐름과 회수 확인. 실제 Calico Pod·물리 경로·운영 인수로 대체하지 않는다.

현재 [PR #10](https://github.com/321Team/infra-kubernetes/pull/10)의 승인 범위에서 만들 수 있는 변경은 아래 제한 진단이다.
기존 Pod 주소·interface 이름은 재사용하지 않고 새 객체 UID·주소·ifindex·peer·route로 소유권을 대조해야 한다.

| 항목 | 승인된 제한 진단 | 운영 보완에 재사용 가능한 범위 |
|---|---|---|
| 대상 | CP1, 소유가 확인된 시험 Pod A/B 및 해당 cali veth 2개 | 대상 확인·변경 전후 관측 방식 |
| 허용 | 정확한 A/32→B/32 및 B/32→A/32, TCP8080, 해당 ingress/egress만 | 이 두 흐름의 시험 결과 |
| zone/policy | 전용 fds-cni-trial, target default, forward no; source/service/NAT 확대 없음 | 기존 보호를 보존하는 원칙 |
| 주소 블록 | 10.244.246.64/26은 IPAM 소유 확인용 | 전체 /26 허용 근거가 아님 |
| 기간 | 최대 20분, 마지막 3분 회수, CP1 독립 복구 | 이 진단의 기한이며 운영 유지 기간으로 전용 불가 |
| 원복 | policy 비활성화 → 소유 Runtime binding 회수 → 소유 Permanent 정의 삭제 → reload/원 Runtime 6개 복원 → 전체 원상·관리 경로 확인 → 복구 예약 해제 | 자신의 변경만 회수하고 drift를 덮어쓰지 않는 원칙 |
| 종료 | 같은 작업창에 완전 회수, CP1 HEP 이전 제거 | 새 운영 허용 상태를 후속 노드에 인계할 수 없음 |

따라서 **제한 진단을 한 번 더 실행하고 회수하는 방식은 미래 Pod·Join을 위한 운영 CNI 수정안이 아니다.**
이번에는 이 제한 진단을 반복 실행하거나 새 VM 반입 자료를 만들지 않는다.
반대로 cali 전체/Pod 서브넷 허용은 범위 확대이며, 기존 진단 승인의 이름으로 실행하지 않는다.
새 운영 규칙의 주소·포트·interface·존속 기간을 정하지 않은 상태에서 임의 명령을 배포하지 않는다.

## 3. 운영 변경을 유지할 경우 필요한 단일 영수증 연계 명세

아래는 새 운영 변경안을 확정할 때 함께 구현·검토할 계약 명세다. 현재 v1 인수 양식이나 기존 소비자가 지원한다고 표시하지 않는다.
규칙을 먼저 넣고 사후에 인수 양식을 고쳐 통과시키는 순서는 사용하지 않는다.

| 묶음 | 필요한 연결 및 검증 |
|---|---|
| 원 CP1 적용 | 원 제출 파일 SHA-256 + 내부 receipt SHA-256 + 승인 생산자/plan/run_id; 원본 보존 |
| 새 변경 계획 | 별도 source/plan 해시, 정확한 규칙 집합·Runtime/Permanent·대상·주소/포트/interface·실행창·만료/복구·보호 종료 계획 |
| 새 변경 영수증 | 원 영수증 해시 연결, 실제 before/after와 개별 intent/result/소유권, 실제 실행 시각·run_id, 원복 결과 |
| 상태 연결 | 변경 before가 원 인수 상태와 같아야 하며 예상 after는 고정 계획으로 계산. 실제 after 및 현재 CP1 상태를 그 전체 값과 대조 |
| 새 인수 | 원 적용 + 새 변경 영수증을 모두 연결. 현재 유지할 after에 대한 Positive·Negative·Regression 실제 증적, 검토 source·결정·시각 |
| 후속 소비자 | CP2 적용 전·후 새 인수 after의 존속, 해시·대상·작업창·topology/보호 상태 검사. 추가한 policy만 비교에서 빼는 예외 금지 |
| 만료/회수 | 원복·reload로 허용 상태가 사라지거나 기간이 끝나면 CP2/CP3 확대 중단. 가입 후의 복구는 membership 전용 판단을 거쳐야 하며 사전 Join rollback을 전용하지 않음 |

현재 소비자는 원 receipt.after와 CP1 방화벽 상태를 직접 비교한다.
새 CNI policy를 유지하면 이 값이 달라져 기존 입력으로는 차단된다.
형식만 새 v1 인수 JSON을 만들거나 원 receipt.after를 편집해 해결할 수 없다.
새 계약의 성공 경로와 source/receipt/after/expiry 불일치 거부 경로를 **같은 수정 묶음**에서 검증해야 한다.

## 4. 한 번에 끝낼 현장 관측·반출

변경안이 확정된 창에서만 직전 상태를 한 번 수집한다. 지금 15분 유효 inspect를 다시 받아 만료시키지 않는다.
원본 소스·기존 증적은 해시로 재사용하고, 현재 firewall/NM/Calico·Node/etcd·관리/API·원복 경로와 새 객체 소유권만 갱신한다.

- Positive: 실제 직접 Pod HTTP 양방향, Service HTTP, DNS 양쪽의 5개 기존 관측. 실패 항목은 실패로 보존한다.
- Negative: 승인된 규칙 밖 주소/포트/ingress를 실제 tuple과 수신 지점 패킷/trace로 확인한다. 발신 timeout만으로 PASS 처리하지 않는다.
- Regression: CP 허용·차단, SSH/API, logs/exec/port-forward, Node/etcd·기존 Pod 건강 및 NM/SELinux 보호 보존.
- 종료: 승인된 유지 상태 또는 원복 상태, 독립 복구/예약, 소유 객체와 잔여 변경, 후속 소비자 입력 가능 여부를 함께 기록한다.
- 반출: root 원본 보존, 홈 디렉터리에 비식별 사본·내부/외부 SHA-256을 한 번 묶는다. 단순 start/collect 성공을 시험 PASS로 만들지 않는다.

## 5. 남은 실제 결정

준비 결함 수정과 계약 명세는 완료했다. 운영 CNI 실행에 남은 결정은 **유지할 실제 허용 범위와 보호·만료·회수 종료 상태를 묶은 변경 범위**다.
[리뷰어의 일정 예외 요청](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5698266772)은 이 결정을 대신한 승인 이벤트가 아니다.
범위 확정 후 위의 새 영수증/소비자 구현·검증을 같은 변경 묶음으로 마쳐 한 번에 검토한다. 매 진단 결함마다 별도 VM 시험·리뷰를 반복하지 않는다.

정본 v3.1.18 §6.6/6.6.1과 [Lead의 HEP/GNP 6/6 이후 조건](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)은 유지한다.
새 규칙 승인·CP1 실제 인수·CP2/CP3 적용·Join·HEP 실행·병합·정본 발행은 이번 준비로 완료 처리하지 않는다.
