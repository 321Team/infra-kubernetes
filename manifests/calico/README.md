# Calico HEP/GNP — 현재 적용 경계

2026-09-24 PR #21 리뷰 후속. [리뷰 5292027958](https://github.com/321Team/infra-kubernetes/pull/21#pullrequestreview-5292027958)의 대안 2를 반영한다. 9/22 [Gate2 기록](../../docs/evidence/P1-SEC-01/GATE2_RUNTIME_EXECUTION_20260922.md)은 현재 보호 생산자가 별도 `p1-k8s-01-v4` 패키지이며 이 디렉터리의 HEP/Bootstrap은 적용하지 않았다고 명시한다.

## 두 산출물의 관계

| 파일/생산자 | 대상 | 현재 용도 |
|---|---|---|
| 별도 `p1-k8s-01-v4` | `fds-k8s-k8s-*` HEP 6개·`fds-k8s-host-*` GNP | 9/22 보고된 현장 보호 생산자. 현재 원본/버전과 실제 자원 대응 확인 필요 |
| `01-bootstrap-allow.yaml`, `02~04-worker*-hep.yaml`, `06~08-cp*-hep.yaml` | 구 `role=k8s-worker/k8s-cp` HEP와 bootstrap | 대체 설계 이력. 현장 v4의 재배포 기준이 아니며 현재 클러스터 적용 보류 |
| `05-nodeport-allow.yaml` | `fds.example/role=worker`인 기존 v4 HEP | 별도 NodePort GNP 후보. 이 PR에서만 현장 보고값과 동기화 |

**기존 01→02→03→04→05 적용 순서는 유효하지 않다.** 구 HEP에는 05가 요구하는 라벨이 없어 Allow/Deny 모두 매칭되지 않는다. 디렉터리 전체 apply, 기존 HEP 이름의 중복 생성, 구 bootstrap으로 v4 보호 대체를 하지 않는다. 원래 canary 순서는 [구 Runbook](../../docs/runbooks/P1-SEC-01_HEP_GNP_ROLLOUT.md)에 설계 이력으로 보존한다.

## v4 환경의 NodePort 후속

1. 독립 리뷰가 완료된 정확한 PR #21 SHA와 SEC Lead의 적용 판단을 사용한다. 이 문서 보완은 서버 변경이나 Runtime 인수가 아니다.
2. 승인 관리 경로에서 기존 HEP의 이름·node·interfaceName·expectedIPs·라벨과 현재 nodeport GNP의 전체 spec을 읽기 전용으로 대조한다. 기존 v4 Worker HEP가 정확히 3개이고 대상 노드가 k8s-worker01/02/03인지 확인한다. 0개, 누락/추가, 중복 HEP, 다른 생산자이면 중단한다.
3. 현재 GNP spec이 승인 후보와 같으면 무변경으로 종료한다. 차이가 있으면 변경 전 해당 GNP 원본을 승인 증적 위치에 보존하고 이 단일 GNP의 차이만 검토한다. 기존 6개 GNP·HEP·라벨을 일괄 교체하지 않는다.
4. 변경이 필요한 경우에만 Lead의 기존 변경 절차에서 05 단독의 서버 dry-run과 적용을 검토한다. .50/32 양성, 비승인 Source 음성, ansible01 관리 경로·기존 허용 통신 회귀를 확인한다. 시험 대상 Workload가 없으면 미검증으로 남긴다.
5. 실패 시 이번 단일 GNP 변경 전 spec으로 복구하고 동일 경로를 재확인한다. 변경 전에 없던 자원은 기존 보호가 유지됨을 확인한 뒤 이번 생성분만 회수한다. 기존 v4 보호 자원을 삭제하거나 firewalld를 임의 전환하지 않는다.

v4 패키지의 배포/드리프트 관리에 05를 편입하는 작업은 **미완료**다. 파일의 spec 일치만으로 다음 v4 재배포에서 유지된다고 보장하지 않는다. v4 생산자 식별과 편입은 K8S/SEC Lead, Source·경로 검증 지원은 ICR Lead의 기존 책임으로 연결한다.

## 구 템플릿 기준과 제한

| IP | 노드 | 구 role 라벨 |
|---|---|---|
| 10.1.93.102 | k8s-control01 | k8s-cp |
| 10.1.93.103 | k8s-control02 | k8s-cp |
| 10.1.93.104 | k8s-control03 | k8s-cp |
| 10.1.93.105 | k8s-worker01 | k8s-worker |
| 10.1.93.107 | k8s-worker02 | k8s-worker |
| 10.1.93.108 | k8s-worker03 | k8s-worker |

구 Worker 템플릿의 NIC는 ens160으로 채워져 있으나 현재 운영 적용 근거로 사용하지 않는다. HEP/정책의 생산자 이관은 별도 검토 대상이다. 이 PR은 승인 Source/포트, 양성·음성 시험, 재부팅·복구 AC를 완화하지 않는다.
