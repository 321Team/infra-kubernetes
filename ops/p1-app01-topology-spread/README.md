# P1-APP-01 — fds-app Worker 분산(topologySpreadConstraints) 적용 자료

#15 [상태 갱신 6012339851](https://github.com/321Team/fds-project-baseline/issues/15#issuecomment-6012339851)의 **Lead 화면 관측(원본 미보존)**: 2026-10-06 17:21 KST에 fds-app Pod 6개가 모두 `k8s-worker02`에 있었고, `transaction-api-nodeport` endpoint가 worker01·03에 0개였습니다. `externalTrafficPolicy: Local`이므로 edge01 → `.105`·`.108:30080`은 거부되고, worker02 장애 시 앱 전체가 멈춥니다.

이 17:21 관측은 파일 원본이 없어 판정 근거로 쓰지 않습니다. 적용 창의 판단 근거는 아래 사전 단계(1)에서 파일로 저장하는 Deployment·Pod 배치·EndpointSlice 원본입니다.

이 폴더는 **승인된 창에서만** 쓰는 적용 patch 2개(`spread-*.json`)와 되돌림 patch 2개(`unspread-*.json`), 확인 순서를 담습니다. 병합은 Runtime 실행 승인이 아닙니다.

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

## 배치 가능성 대조 (replicas 3, `minDomains` 미설정)

아래는 사전 단계에서 "worker 3대 taint 없음, control-plane은 `NoSchedule`, 두 앱에 관련 toleration 없음"이 확인됐을 때의 예상입니다. 실제 결과는 사후 단계의 관측으로만 판정합니다.

| 상황 | 계산에 들어가는 노드 | 예상 배치 | 비고 |
|---|---|---|---|
| worker 3대 정상, rolling update | worker 3대 | 새 세대 1·1·1 | `pod-template-hash` 때문에 새 세대끼리만 계산합니다. 기본 strategy(maxSurge 25%→1, maxUnavailable 25%→0)로 한 개씩 늘고 줄어, 가용 replica가 3 아래로 내려가지 않을 것으로 예상합니다(strategy는 사전 단계에서 기록) |
| worker 1대 cordon·drain | 남은 worker 2대 | 2·1 | cordon 노드는 `unschedulable` taint로 빠지므로 skew 1 이내라 Pending이 생기지 않습니다. `minDomains`를 3으로 강제하지 않은 이유입니다 |
| worker 1대 NotReady(cordon 없음) | 남은 worker 2대 | 2·1 | `not-ready:NoSchedule` taint를 견디지 못해 빠집니다. 기본 `NoExecute` toleration 300초가 지나면 축출됩니다 |
| worker 1대만 가용 | 1대 | 3 | 단일 노드, HA 없음 |
| 노드 복귀(uncordon) 뒤 | worker 3대 | **자동 재분산 없음** | 이미 뜬 Pod는 옮기지 않습니다. 균등 배치를 복원하려면 별도 승인된 rollout이 필요합니다 |

어느 경우든 `externalTrafficPolicy: Local`에서 local endpoint가 없는 worker는 해당 Service 트래픽을 받지 못합니다. 분산 설정은 이것을 없애지 않습니다. edge01 backend 제외 통제는 #19 범위입니다.

## MON 23/23 판정 기준 (사전·변경 중·사후·되돌림 공통)

`count(up)`은 값이 아니라 series 수만 세므로 health 판정에 쓰지 않습니다(target 하나가 `up=0`이어도 23이 됨). 아래 네 가지를 **모두** 만족해야 "MON 23/23"입니다. 하나라도 어긋나거나, 조회 실패·빈 결과·응답 형식 이상이면 **미충족**입니다.

1. `count(up)` = 23 (전체 series 23개)
2. `count(up == 1)` = 23 (정상 23개)
3. `up == 0` 결과가 빈 목록 (실패 target 없음)
4. 실제 `(job, instance)` 집합이 아래 **기대 집합 23개와 정확히 같음** (빠짐·추가 모두 미충족)

| 구분 | job | instance | 개수 |
|---|---|---|---|
| 고정 | `kubelet` | `10.1.93.102`·`.103`·`.104`·`.105`·`.107`·`.108` `:10250` | 6 |
| 고정 | `kubelet-cadvisor` | 위와 같은 6개 `:10250` | 6 |
| 고정 | `node-exporter` | `10.1.93.105`·`.107`·`.108` `:9100` | 3 |
| 고정 | `kube-state-metrics` | `kube-state-metrics.monitoring-api.svc.cluster.local:8080` | 1 |
| 고정 | `prometheus` | `localhost:9090` | 1 |
| 앱 | `transaction-api` | 그 시점 `transaction-api` Pod IP 3개 `:9091` | 3 |
| 앱 | `fds-engine` | 그 시점 `fds-engine` Pod IP 3개 `:9090` | 3 |

- 고정 17개는 2026-10-05 기준선(fds-project-evidence `evidence/P1-K8S-01/ro-evidence-20261005/` 안 `up-targets.txt`)과 같은 값입니다. 앱 6개는 Pod가 새로 만들어지면 IP가 바뀌므로, 같은 시점의 `$K -n fds-app get pods -o wide` IP 목록으로 기대 집합을 만듭니다(Running·Ready Pod만, 앱마다 정확히 3개).
- 조회: control01에서 이 세션 전용 포트로 `port-forward svc/prometheus`를 열고, `query=count(up)`, `count(up==1)`, `up==0`, `up` 네 결과를 각각 파일로 저장합니다. 끝나면 자기 포트의 port-forward만 종료합니다. `status`가 `success`가 아니거나 결과가 비면 미충족입니다.
- 변경 직후에는 Prometheus의 target 발견과 scrape에 시간이 걸립니다. 각 앱의 `rollout status` 성공 뒤 **최대 60초 동안 15초 간격으로** 다시 조회하고, 60초 안에 위 네 조건을 모두 만족하지 못하면 중단 기준에 해당합니다. 사전 단계에서는 재조회 없이 한 번에 만족해야 시작합니다.

## 적용 순서 (control01, 승인된 창에서)

전제: `sudo -v`, `K='sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf'`, 이 폴더의 두 JSON을 control01에 두고 SHA-256을 이 저장소 값과 대조.

1. **사전(읽기 전용)**
   - `$K -n fds-app get deploy transaction-api fds-engine -o yaml > pre-deploy.yaml` 후 `sha256sum pre-deploy.yaml`
   - 앱별 기준선을 기록합니다: `$K -n fds-app get deploy <이름> -o jsonpath='{.metadata.uid} {.metadata.resourceVersion} {.metadata.generation} {.spec.template.spec.containers[0].image} {.spec.strategy}{"\n"}'`. 이미지 digest가 승인값과 같은지 대조합니다
   - `$K -n fds-app rollout history deploy/transaction-api`와 `deploy/fds-engine` → **현재 revision 번호를 기록**(롤백 대상 고정)
   - 기존 분산 조건이 없는지: `$K -n fds-app get deploy transaction-api fds-engine -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.topologySpreadConstraints}{"\n"}{end}'`가 두 Deployment 모두 빈 값(있으면 중단하고 strategic merge 결과를 먼저 검토)
   - 노드 taint: `$K get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints,UNSCHED:.spec.unschedulable` — worker 3대는 taint 없음·schedulable, control-plane 3대는 `NoSchedule` taint
   - Pod toleration: `$K -n fds-app get deploy transaction-api fds-engine -o jsonpath='{range .items[*]}{.metadata.name} {.spec.template.spec.tolerations}{"\n"}{end}'`(control-plane·unschedulable을 견디는 toleration이 없어야 함)
   - worker 3대 hostname label(`kubernetes.io/hostname`)과 가용 자원(`$K describe node <worker> | grep -A8 "Allocated resources"`), worker01·03의 Harbor 이미지 pull 가능 여부(최근 같은 이미지 Pull 이벤트 또는 `harbor01:443` 경로)
   - Pod·노드 배치, EndpointSlice 노드별 목록
   - MON 23/23(위 판정 기준 4가지 모두. 사전 단계에서 미충족이면 시작하지 않음)
2. **변경(순서 고정: `transaction-api` → 확인 → `fds-engine`)**
   - 서비스 영향: 앱마다 Pod 3개가 rolling update로 다시 만들어집니다. 사전 단계에서 기록한 strategy가 기본값(maxSurge 1, maxUnavailable 0)이면 한 번에 1개씩 늘고 줄어 **가용 replica 3 미만이 없어야** 합니다. strategy가 다르면 변경하지 않고 중단합니다
   - `$K -n fds-app patch deploy/transaction-api --type=strategic --patch-file=spread-transaction-api.json --dry-run=server -o yaml | grep -A12 topologySpreadConstraints` (server dry-run 결과에서 추가 필드만 바뀌는지 확인)
   - `$K -n fds-app patch deploy/transaction-api --type=strategic --patch-file=spread-transaction-api.json`
   - `$K -n fds-app rollout status deploy/transaction-api --timeout=180s` → 성공하면 이 앱의 Pod 배치·Ready·EndpointSlice를 확인한 **뒤에만** 다음 앱으로 갑니다
   - 같은 앱의 되돌림 patch가 지금 상태에 맞는지 `$K -n fds-app patch deploy/transaction-api --type=json --patch-file=unspread-transaction-api.json --dry-run=server`로 확인합니다(실제 제거 아님). 실패하면 다음 앱으로 가지 않고 중단·보고합니다
   - 같은 방식으로 `fds-engine`
3. **사후(읽기 전용)**
   - 두 Deployment `rollout status` 완료, Pod가 Worker마다 1개씩, Pending 0
   - `transaction-api-nodeport` EndpointSlice가 worker01·02·03 각 1개(ready=true)
   - edge01에서 `.105`·`.107`·`.108:30080/readyz` 각 200. 명령·시각·RC·HTTP code를 파일로 남겨, #19 AC-04 edge 행 재수집 때 "Worker 3대 local endpoint" 전제의 근거로 같은 원본을 재사용할 수 있게 합니다(AC-04 판정 자체는 #19 범위)
   - MON 23/23(위 판정 기준 4가지 모두. 앱 6개 target은 새 Pod IP 기준)
4. **중단 기준(하나라도 해당하면 다음 앱으로 가지 않고 되돌림):** `rollout status`가 180초 안에 끝나지 않음 / 변경 중 `AVAILABLE`이 3 미만으로 내려감 / 사후에 Pending 1개 이상 또는 Ready 아닌 Pod 1개 이상 / `transaction-api` 적용 뒤 `transaction-api-nodeport` ready endpoint가 Worker 3대에 각 1개가 아님 / edge01 `/readyz` 3개 중 200이 아닌 응답 1개 이상 / MON 23/23 판정 기준(전체 23·정상 23·`up==0` 없음·기대 집합 일치) 중 하나라도 미충족, 또는 조회 실패·빈 결과(변경 직후는 60초 재조회 뒤 판정).
5. **되돌림(이번에 넣은 필드만 제거):**
   - 먼저 이미지·generation 외 template이 기준선과 같은지 확인합니다. 다른 변경이 끼어들었으면 **되돌리지 말고 중단·보고**합니다
   - `$K -n fds-app patch deploy/<이름> --type=json --patch-file=unspread-<이름>.json --dry-run=server` → 이어서 실제 실행
   - `unspread-*.json`은 JSON patch의 `test` 연산으로 **이번에 넣은 값과 정확히 같을 때만** 필드를 지웁니다. 값이 다르면 API가 거부하므로 다른 사람이 바꾼 내용을 덮어쓰지 않습니다
   - `rollout undo`는 쓰지 않습니다. 이전 template 전체(이미지·env 포함)를 되돌릴 수 있기 때문입니다
   - 되돌린 뒤 `rollout status`, Ready, EndpointSlice, edge01 `/readyz`, MON을 다시 확인합니다. 단일 노드 집중 위험이 그대로 남는다는 점을 기록합니다

## 한계

- 이 저장소에는 매니페스트 CI가 없습니다. 오프라인 확인(YAML 파싱, patch JSON = manifest 블록 일치, unspread의 `test` 값 = spread 값)만 했고, **server dry-run과 실제 적용은 NOT RUN**입니다.
- 원인(언제·왜 한 노드에 모였는지)은 이 변경의 범위가 아닙니다.
- `maxSkew: 1`의 1·1·1 배치는 **스케줄 가능한 worker 3대 · 같은 세대 replicas 3**일 때만 성립합니다. worker가 줄면(drain·장애) 노드별 endpoint 보장은 유지되지 않으며, `externalTrafficPolicy: Local`은 그대로 둡니다.
