# CR-P1-MON-01-02 — Alertmanager Egress Kubernetes 보안 설정 (NetworkPolicy)

- 연계 티켓: [fds-project-baseline#75](https://github.com/321Team/fds-project-baseline/issues/75)
- 선행 티켓: [fds-project-baseline#74 (CR-P1-MON-01-01)](https://github.com/321Team/fds-project-baseline/issues/74)
- 상태: **DRAFT — 병합/적용 보류**

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
| 프록시 IP:port | `10.1.93.56:3128`은 [DEC-P1-EGRESS-01(#78)](https://github.com/321Team/fds-project-baseline/issues/78)의 계획값. `infra-ansible` PR #12는 REQUEST_CHANGES 상태이고 실제 Alertmanager egress source 실측 전이라 Squid apply는 HOLD | #74의 실측 완료 + Squid apply/verify PASS + endpoint 공식 인계 |
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
