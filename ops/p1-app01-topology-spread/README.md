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
| `nodeTaintsPolicy` | `Honor` | Pod가 견디지 못하는 taint가 있는 노드를 분산 계산에서 뺌. cordon(`node.kubernetes.io/unschedulable:NoSchedule`)·control-plane(`NoSchedule`)·NotReady(`not-ready:NoSchedule`)가 해당될 것으로 **예상하지만, 실제 포함 여부는 노드 taint와 Pod toleration으로 정해지므로 사전 단계에서 기록·확인**함. 기본값(`Ignore`)이면 drain 중 0개인 cordon 노드 때문에 옮길 Pod가 Pending이 될 수 있음 |
| `matchLabelKeys` | `pod-template-hash` | rolling update 때 새 ReplicaSet끼리만 분산을 계산(옛 Pod 때문에 막히지 않음) |

`DoNotSchedule`이므로, 남은 Worker에 자원이 없으면 Pod가 다른 노드에 겹쳐 뜨는 대신 Pending이 됩니다. 이것은 의도한 동작입니다. 사후 확인에서 Pending 0을 봅니다.

## 적용 순서 (control01, 승인된 창에서)

전제: `sudo -v`, `K='sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf'`, 이 폴더의 두 JSON을 control01에 두고 SHA-256을 이 저장소 값과 대조.

1. **사전(읽기 전용)**
   - `$K -n fds-app get deploy transaction-api fds-engine -o yaml > pre-deploy.yaml`
   - `$K -n fds-app rollout history deploy/transaction-api`와 `deploy/fds-engine` → **현재 revision 번호를 기록**(롤백 대상 고정)
   - 기존 분산 조건이 없는지: `$K -n fds-app get deploy -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.topologySpreadConstraints}{"
"}{end}'`가 두 Deployment 모두 빈 값(있으면 중단하고 strategic merge 결과를 먼저 검토)
   - 노드 taint: `$K get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints,UNSCHED:.spec.unschedulable` — worker 3대는 taint 없음·schedulable, control-plane 3대는 `NoSchedule` taint
   - Pod toleration: `$K -n fds-app get deploy transaction-api fds-engine -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.tolerations}{"
"}{end}'`(control-plane·unschedulable을 견디는 toleration이 없어야 함)
   - worker 3대 hostname label(`kubernetes.io/hostname`)과 가용 자원(`$K describe node <worker> | grep -A8 "Allocated resources"`), worker01·03의 Harbor 이미지 pull 가능 여부(최근 같은 이미지 Pull 이벤트 또는 `harbor01:443` 경로)
   - Pod·노드 배치, EndpointSlice 노드별 목록
   - MON 23/23
2. **변경(Deployment별로, 하나 끝나고 다음)**
   - `$K -n fds-app patch deploy/transaction-api --type=strategic --patch-file=spread-transaction-api.json --dry-run=server` (먼저 server dry-run)
   - `$K -n fds-app patch deploy/transaction-api --type=strategic --patch-file=spread-transaction-api.json`
   - `$K -n fds-app rollout status deploy/transaction-api --timeout=180s`
   - `fds-engine`도 같은 방식으로 진행
3. **사후(읽기 전용)**
   - 두 Deployment `rollout status` 완료, Pod가 Worker마다 1개씩, Pending 0
   - `transaction-api-nodeport` EndpointSlice가 worker01·02·03 각 1개(ready=true)
   - edge01에서 `.105`·`.107`·`.108:30080/readyz` 각 200
   - MON 23/23(transaction-api·fds-engine target 포함)
4. **중단·롤백:** rollout이 180초 안에 끝나지 않거나 Pending·readiness 실패가 남으면, 그 사이 다른 template 변경이 없었는지(`rollout history`) 확인한 뒤 `$K -n fds-app rollout undo deploy/<이름> --to-revision=<사전 기록 번호>`. 분산 필드가 빠졌는지 `pre-deploy.yaml`과 대조합니다. undo 성공만으로 복구 완료를 선언하지 않고, 사후 단계(EndpointSlice·edge01 `/readyz`·MON)를 다시 확인합니다.

## 한계

- 이 저장소에는 매니페스트 CI가 없습니다. 오프라인 확인(YAML 파싱, patch JSON = manifest 블록 일치)만 했고, **server dry-run과 실제 적용은 NOT RUN**입니다.
- 원인(언제·왜 한 노드에 모였는지)은 이 변경의 범위가 아닙니다.
- `maxSkew: 1`의 1·1·1 배치는 **스케줄 가능한 worker 3대 · 같은 세대 replicas 3**일 때만 성립합니다. worker가 줄면(drain·장애) 노드별 endpoint 보장은 유지되지 않으며, `externalTrafficPolicy: Local`은 그대로 둡니다.
