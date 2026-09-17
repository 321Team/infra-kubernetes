# CR-P1-MON-01-02 — Alertmanager Egress Kubernetes 보안 설정 (NetworkPolicy)

- 연계 티켓: [fds-project-baseline#75](https://github.com/321Team/fds-project-baseline/issues/75)
- 선행 티켓: [fds-project-baseline#74 (CR-P1-MON-01-01)](https://github.com/321Team/fds-project-baseline/issues/74)
- 상태: **DRAFT — 병합/적용 보류**
- 2026-09-17 갱신: 아래 "프록시 IP:port" 행의 `infra-ansible` PR #12 상태를 최신화(작성 당시
  REQUEST_CHANGES → 이후 병합 완료로 확인). 병합됐다고 Squid apply HOLD가 풀린 것은
  아니며, 막힌 이유가 "코드 미승인"에서 "실측 IP 미확보로 인한 게이트 미해제"로 바뀐 것.

## 이 PR이 지금 하는 것

#75 본문에 "#74 endpoint 인수 전 정책 초안·시험 설계는 진행할 수 있다"고 명시되어 있어,
아래 초안만 먼저 작성한다.

- [`manifests/monitoring-api/alertmanager-egress-networkpolicy.yaml`](../../../manifests/monitoring-api/alertmanager-egress-networkpolicy.yaml):
  Alertmanager -> egress-proxy01(CONNECT/443) + kube-dns(53) 만 허용하는
  `networking.k8s.io/v1` NetworkPolicy 초안.

## 이 PR이 아직 하지 않는 것 (실제 적용 전 선행조건)

| 항목 | 현재 상태 | 무엇이 있어야 확정되는가 |
| --- | --- | --- |
| 쿠버네티스 클러스터 | P1-K8S-01 Gate 0(사전점검)만 통과. 실제 클러스터 미부트스트랩, 이 리포에 Gate 1/2 실행 코드 없음 | 클러스터 실제 구동 |
| Alertmanager Pod/podSelector | `app: alertmanager` 가정뿐, 검증할 실배포 매니페스트 부재. `fds-msa` 리포는 321Team org에 아직 존재하지 않음 | 실제 Alertmanager Deployment/StatefulSet 매니페스트 |
| 프록시 IP:port | `10.1.93.56:3128`은 [DEC-P1-EGRESS-01(#78)](https://github.com/321Team/fds-project-baseline/issues/78)의 계획값. `infra-ansible` [PR #12](https://github.com/321Team/infra-ansible/pull/12)는 2026-09-16 GWONUK 최종 APPROVE로 **병합 완료**(코드 승인)됐으나, 이 승인은 코드 병합 승인일 뿐 Squid Runtime apply/서비스 인수를 승인한 것은 아니다. 실제 Alertmanager egress source(Calico/NAT 이후 IP)를 아직 실측하지 않아 코드 내 `cr_mon_proxy_clients_confirmed` 게이트가 `true`로 설정되지 않았고, 이 게이트 없이는 Squid apply 자체가 진행되지 않는다. Squid apply는 여전히 HOLD | 실측된 egress source IP → `cr_mon_proxy_clients_confirmed` 게이트 해제 → Squid apply/verify PASS → endpoint 공식 인계 |
| kube-dns 라벨 | `k8s-app: kube-dns`는 kubeadm 기본값 가정, 이 클러스터 실측 아님 | 클러스터 실제 구동 후 확인 |
| Secret 주입/회전/폐기 절차 | 미정 | 담당자(이하영: 생성/회전, 이재환: 설정 인계/검증) 논의 |
| 검증 계획(허용/차단/알림 흐름) | 미정 | 클러스터·프록시 모두 준비된 뒤 실행 |

## 진행 방식

이 브랜치/PR을 닫지 않고, 위 선행조건이 하나씩 풀릴 때마다 같은 PR에 커밋을 이어서
추가한다 (재-review 대상이 된다는 뜻; PR 템플릿에 따라 새 커밋마다 재검토·새 HEAD
판정을 남긴다). 모든 선행조건이 풀리고 나면 이 문서의 표를 갱신하고 실제 적용/검증
증적을 채운다.

## 검증 수행 여부

이 초안 작성 시점에는 로컬 환경에 `kubectl`/`python3`(PyYAML)이 없어 문법 검증조차
수행하지 못했다. 육안 검토만 했고, 정적 검사·모의 시험·실서버 시험 어느 것도
PASS로 기록하지 않는다.
