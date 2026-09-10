# ICR-D-NET-01-01 — CP 통신 보완 착수

기준일: 2026-09-10 · 상태: **변경안·CP1 실행 도구 준비 완료 / Runtime 미적용 / 독립 리뷰 대기**

주관: [fds-project-baseline #14](https://github.com/321Team/fds-project-baseline/issues/14), 이권욱 Lead · 이하영·이재환 Support.
연계: [#10 P1-K8S-01](https://github.com/321Team/fds-project-baseline/issues/10), 이하영 Lead · 이권욱 Support.

## 착수 이유

2026-09-10 11:30 KST 증적에서 CP 3대의 관리 SSH·Python 모듈·sudo는 통과했다. CP1은 Node Ready 및 API readyz PASS이나 조회한 클러스터에 등록된 Node는 CP1 한 대다. CP2에는 API/controller-manager/scheduler 매니페스트가 남아 있고, CP2·CP3 kubelet은 config.yaml 부재로 반복 종료한다.

세 CP의 public Runtime/Permanent rich rule은 LB `10.1.93.109/32 → TCP 6443` 한 건이다. 정본 IF-12A의 etcd 통신과 IF-12B의 CP→kubelet 허용이 빠져 있다. HEP/GNP도 각각 0개여서 host firewall 보호 인계는 미완료다. 원래 join stdout은 없으므로 방화벽을 timeout의 단일 원인으로 확정하지 않는다.

근거: [수집 검토서](CP_LOG_REVIEW_20260910.md), [증적 인덱스](EVIDENCE_INDEX.json).

## 이번 변경 범위

| 구분 | 결정 |
|---|---|
| 대상 | k8s-control01 .102 / control02 .103 / control03 .104 |
| Zone / interface | 증적 기준 public / ens160. 적용 직전 실측 일치 확인 |
| Source | .102/32, .103/32, .104/32 각각 |
| IF-12A | TCP 2379–2380, CP 3대 목적지 |
| IF-12B 첫 범위 | TCP 10250, CP 3대 목적지. Worker 3대는 별도 증적 보완 |
| 현재 필요한 추가 수 | 노드별 rich rule 6개 × Runtime/Permanent 2종 = 12건, 총 36건 |
| 보존 | SSH 서비스, 기존 LB→CP:6443, 그 밖의 기존 zone 설정 |
| 유지 기간 | CP 운영에 필요한 경로를 보호 인계 완료까지 유지. 2시간 자동 소멸을 완료 구성으로 채택하지 않음 |
| 실행 순서 | CP1 canary → CP2 → CP3. 각 단계 검증 후 다음 노드 |

정본 근거는 [v3.1.17 §15.1](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)의 IF-12A/IF-12B다. 이 작업은 firewalld가 현재 가동 중인 부트스트랩 구간의 최소 통신 보완안이다. 기존 D34의 최종 firewalld OFF 목표나 정본을 소급 변경하지 않는다. 보호 공백을 방지하는 실제 인계 순서와 실행 조건은 #14의 변경 기록으로 관리한다.

## 구현

- [정책](../../../policies/icr-d-net-01-01/cp-firewall-v1.json): Source·Port·대상·정본/Inventory 커밋과 증적 해시 고정.
- [변경안 생성기](../../../scripts/build-cp-firewall-plan.py): 제출 tar.gz, sidecar, 내부 체크섬 전체 범위, 노드 identity·접속·zone 상태를 검증하고 추가 후보를 산출한다. 외부 접속이나 명령 실행 기능은 없다.
- [생성 결과](cp-firewall-plan-20260910.json): 노드/설정 종류별 기존 상태, 상태 해시, query/add/rollback 인자. 이미 있는 규칙은 preserve로 기록하고 add/rollback 후보에서 제외한다.
- [실행·복구 절차](../../runbooks/ICR-D-NET-01-01-CP-PORTS.md): 실시간 상태 재확인, 적용 결과 기록, canary 및 부분 실패 처리.
- [CP1 실행 도구](../../../scripts/run-cp-canary.py): 기본 모드는 CP 3대 읽기 전용 점검이다. 적용·복구는 CP1로 제한한다. [키트 실행 절차](../../runbooks/ICR-D-NET-01-01-CP1-CANARY-KIT.md).
- [검증 결과](VALIDATION.md): 30개 자동 테스트와 실제 제출본에 대한 오프라인 생성·zone 파싱 결과.

생성 결과 SHA-256: `2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7`.

생성 결과의 상태는 `DRAFT_PLAN_REVIEW_REQUIRED`, `runtime_applied=false`다. add/remove 인자는 검토할 변경 데이터를 뜻한다. 후속 커밋에서 명시적인 CP1 적용·복구 실행기를 추가했다. 현재 단계는 읽기 전용 inspect이며 자동 배포 workflow는 없다. 검토된 변경안과 원격 영구 작업 기록을 연결한다. CP2/CP3 적용은 후속 범위다.

## 남은 작업과 담당

| 작업 | 담당 티켓 | 이번 산출물과의 관계 |
|---|---|---|
| CP1 etcd member list/status, CP2 잔여 구성 확인 | #10 이하영, 이권욱 지원 | 포트 변경안 준비와 병행. CP2 join 재개 전에 필요 |
| CP 포트 변경안의 고정 HEAD 리뷰 및 실행 입력 확정 | #14 이권욱 | 본 PR 검토 대상. 동일 HEAD의 독립 리뷰 결과 기록 |
| CP1부터 적용·검증 | #14 / #10 | 실행 전 현재 zone/rules/NM·관리 경로 확인, 적용 결과와 SHA 보관 |
| Worker 3대의 방화벽·리스너·NTP 수집 | #10 | IF-12B 전체와 6대 NTP 판단 완성 |
| CP 직접 6443 필요 경로 판단 | #10 → #14 | 정본 Node→LB/LB→CP 경로와 구분. 이번 허용 정책에 자동 추가하지 않음 |
| 고정 NTP 목적지·대체 서버·UDP 123 | #14 조정, #65 계열 후속 변경 | 관측된 1.230.135.74를 승인값으로 전용하지 않음 |
| HEP/GNP, Positive/Negative, Reboot/Rollback | #14 / #19 / #10 | CP 통신 보완 이후 보호 인계 작업. 이번 계획으로 PASS 처리하지 않음 |
| IF-06B, IF-16, Monitoring 경로 | #14 기존 작업 | 이번 CP 범위 밖 잔여 항목 유지 |

## 이번 작업의 완료와 이슈 전체 완료

이번에 완료한 것은 증적 기반 정책·변경안 생성·CP1 실행 도구 및 복구 절차 준비와 로컬 검증이다. 실제 방화벽 적용, CP2/CP3 join, 6/6 Ready, etcd 3 Member Healthy, Worker 통신, HEP/GNP, 재부팅·복구·독립 리뷰는 아직 완료되지 않았다. 따라서 #14는 In Progress를 유지하며 ICR-AC-01~20의 전체 PASS나 종료일을 새로 부여하지 않는다.

구현 저장소는 `infra-kubernetes`의 현재 main에서 분기한다. 별도 진행 중인 초기 관리 문서 PR #1의 README나 HEAD를 변경하지 않는다. 공통 Inventory는 `infra-ansible`의 검토된 커밋을 참조하며 독립 사본을 만들지 않는다.

## 첫 inspect 결과와 수정

2026-09-10 12:45 KST 제출본에서 CP1 etcd 단일 멤버, CP2 표준 kubelet/etcd 경로 부재, CP2/3→CP1 세 포트 실패를 확인했다. v1.0 수집기의 ANY 정책 오인으로 firewall snapshot은 미완료다. v1.1 정책 조회·진단 보존·상태 집계를 수정했으며 재점검이 필요하다. [검토서](CP1_CANARY_REVIEW_20260910.md). 현재 코드의 독립 리뷰와 실제 적용은 여전히 미완료다.
