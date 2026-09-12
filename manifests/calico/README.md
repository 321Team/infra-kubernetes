# Calico HEP/GNP — 단계별 적용 파일 (SEC-06 2차 수정)

> 대응 리뷰: `fds-project-baseline#19` issuecomment-5644891728 (GWONUK, 2026-09-12), SEC-06
> 1차 제출의 `manifests/04-calico-hep-gnp.yaml` 한 파일에 GNP 2개 + Worker HEP 3개가 섞여있어,
> `kubectl apply -f`를 한 번만 실행해도 canary 순서를 건너뛰고 Worker 3대가 동시에 적용되는
> 결함이 있었다. 이번 2차 수정은 **파일을 단계별로 분리**해 "한 파일 = 한 단계"가 되도록 한다.
> 적용 순서와 회귀시험은 `docs/runbooks/P1-SEC-01_HEP_GNP_ROLLOUT.md`를 따른다.

## 파일 목록과 적용 순서

| 순서 | 파일 | 내용 | 비고 |
|---|---|---|---|
| 1 | `01-bootstrap-allow.yaml` | GlobalNetworkPolicy만 (HEP 없음) | HEP가 없으면 Calico는 해당 노드에 정책을 집행하지 않으므로 먼저 적용해도 안전 |
| 2 | `02-worker01-hep.yaml` | Worker01 HostEndpoint 1개 | canary — 이 파일만 단독 적용 |
| 3 | `03-worker02-hep.yaml` | Worker02 HostEndpoint 1개 | Worker01 회귀·재부팅·복구 PASS 후에만 적용 |
| 4 | `04-worker03-hep.yaml` | Worker03 HostEndpoint 1개 | Worker02와 동일 조건 |
| 5 | `05-nodeport-allow.yaml` | NodePort 30080 GlobalNetworkPolicy | Worker 3대 전체 안정화 후 |
| 6 | `06-cp01-hep.yaml` | CP01 HostEndpoint 1개 | Worker 최소 1 관찰 주기 안정화 후 |
| 7 | `07-cp02-hep.yaml` | CP02 HostEndpoint 1개 | CP01 회귀 PASS 후 |
| 8 | `08-cp03-hep.yaml` | CP03 HostEndpoint 1개 | CP02와 동일 조건 |

**절대 여러 파일을 한 번에 `kubectl apply -f manifests/calico/`로 통째로 적용하지 않는다.**
각 파일을 개별 지정해서 하나씩 적용하고, 매번 회귀시험을 실행한다.

```bash
# 예시 — 한 파일씩 개별 지정
kubectl apply -f manifests/calico/01-bootstrap-allow.yaml
# 회귀시험 실행 ...
kubectl apply -f manifests/calico/02-worker01-hep.yaml
# 회귀시험 실행 ...
```

## IP/노드 기준값 (baseline 확정)

`FDS_Project_Implementation_Baseline_v3.1.18.md` L498-504:

| IP | 노드 | role 라벨 |
|---|---|---|
| 10.1.93.102 | k8s-control01 | k8s-cp |
| 10.1.93.103 | k8s-control02 | k8s-cp |
| 10.1.93.104 | k8s-control03 | k8s-cp |
| 10.1.93.105 | k8s-worker01 | k8s-worker |
| 10.1.93.107 | k8s-worker02 | k8s-worker |
| 10.1.93.108 | k8s-worker03 | k8s-worker |

`interfaceName`은 여전히 `<실제-NIC-이름>` placeholder다 — 각 노드에서 `ip -o link show`로
확인 후 실측값으로 교체해야 하며, 확인 전 상태로 적용하지 않는다.
