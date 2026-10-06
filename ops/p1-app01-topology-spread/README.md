# P1-APP-01 — fds-app Worker 분산(topologySpreadConstraints) 적용 자료

#15 [상태 갱신 6012339851](https://github.com/321Team/fds-project-baseline/issues/15#issuecomment-6012339851): 2026-10-06 17:21 KST에 fds-app Pod 6개가 모두 `k8s-worker02`에 있었고, `transaction-api-nodeport` endpoint가 worker01·03에 0개였습니다. `externalTrafficPolicy: Local`이므로 edge01 → `.105`·`.108:30080`은 거부되고, worker02 장애 시 앱 전체가 멈춥니다.

이 폴더는 **승인된 창에서만** 쓰는 patch 2개와 확인 순서를 담습니다. 병합은 Runtime 실행 승인이 아닙니다.

## 무엇을 바꾸나

두 Deployment(`transaction-api`, `fds-engine`)의 Pod template에 아래 분산 조건 **하나만** 추가합니다. 이미지·env·securityContext·replicas는 바꾸지 않습니다. `manifests/02-securitycontext.yaml`도 같은 내용으로 맞췄습니다.

| 필드 | 값 | 이유 |
|---|---|---|
| `topologyKey` | `kubernetes.io/hostname` | Worker마다 1개 |
| `maxSkew` | 1 | replicas 3 / Worker 3 → 1·1·1 |
| `whenUnsatisfiable` | `DoNotSchedule` | `ScheduleAnyway`는 점수 가중치일 뿐이라 이미지가 이미 있는 노드(ImageLocality)로 다시 몰릴 수 있음 |
| `nodeTaintsPolicy` | `Honor` | cordon·NotReady(`unschedulable`/`not-ready` taint)·control-plane 노드를 계산에서 뺌. 기본값(`Ignore`)이면 drain 중 0개인 cordon 노드 때문에 옮길 Pod가 Pending이 됨 |
| `matchLabelKeys` | `pod-template-hash` | rolling update 때 새 ReplicaSet끼리만 분산을 계산(옛 Pod 때문에 막히지 않음) |

`DoNotSchedule`이므로, 남은 Worker에 자원이 없으면 Pod가 다른 노드에 겹쳐 뜨는 대신 Pending이 됩니다. 이것은 의도한 동작입니다. 사후 확인에서 Pending 0을 봅니다.

## 적용 순서 (control01, 승인된 창에서)

전제: `sudo -v`, `K='sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf'`, 이 폴더의 두 JSON을 control01에 두고 SHA-256을 이 저장소 값과 대조.

1. **사전(읽기 전용)**
   - `$K -n fds-app get deploy transaction-api fds-engine -o yaml > pre-deploy.yaml`
   - `$K -n fds-app rollout history deploy/transaction-api`와 `deploy/fds-engine`
   - Pod·노드 배치, EndpointSlice 노드별 목록
   - MON 23/23
2. **변경(Deployment별로, 하나 끝나고 다음)**
   - `$K -n fds-app patch deploy/transaction-api --type=strategic --patch-file=spread-transaction-api.json --dry-run=server` (먼저 server dry-run)
   - `$K -n fds-app patch deploy/transaction-api --type=strategic --patch-file=spread-transaction-api.json`
   - `$K -n fds-app rollout status deploy/transaction-api --timeout=180s`
   - `fds-engine`도 같은 방식으로 진행
3. **사후(읽기 전용)**
   - Pod가 Worker마다 1개씩, Pending 0
   - `transaction-api-nodeport` EndpointSlice가 worker01·02·03 각 1개(ready=true)
   - edge01에서 `.105`·`.107`·`.108:30080/readyz` 각 200
   - MON 23/23(transaction-api·fds-engine target 포함)
4. **중단·롤백:** rollout이 180초 안에 끝나지 않거나 Pending·readiness 실패가 남으면 `$K -n fds-app rollout undo deploy/<이름>`. 이전 revision으로 돌아가며, 분산 필드도 함께 빠집니다. `pre-deploy.yaml`로 대조합니다.

## 한계

- 이 저장소에는 매니페스트 CI가 없습니다. 오프라인 확인(YAML 파싱, patch JSON = manifest 블록 일치)만 했고, **server dry-run과 실제 적용은 NOT RUN**입니다.
- 원인(언제·왜 한 노드에 모였는지)은 이 변경의 범위가 아닙니다.
