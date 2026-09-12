# P1-SEC-01 — Calico HEP/GNP 보호 전환 Runbook (SEC-06 대응, 2차 수정)

> 1차 대응 리뷰: `fds-project-baseline#19` issuecomment-5633339292 (GWONUK, 2026-09-11), SEC-06 (Critical)
> 2차 재검토: issuecomment-5644891728 (GWONUK, 2026-09-12) — 1차 제출은 단일 파일에
> GNP 2개 + Worker HEP 3개가 섞여있어 `kubectl apply -f` 한 번으로 canary 순서가
> 깨지는 결함, Bootstrap 규칙 자체의 모순(BGP Disabled인데 TCP179 허용), CP HEP
> 자원 부재, 복구 절차가 "보호 선복원" 원칙과 충돌하는 문제가 지적됨. 이 2차 수정은
> 파일을 노드별로 분리(`manifests/calico/`)하고 위 결함을 반영한다.
> baseline 근거: `FDS_Project_Implementation_Baseline_v3.1.18.md` §6.6 "HostEndpoint 정책은
> Bootstrap Allow → canary Worker → Worker 확장 → Control Plane 순서로 적용"

## 원칙

- 이전 단계의 보호가 정상 복원됨을 확인하기 전까지 다음 단계로 진행하지 않는다.
- HEP 제거 또는 `firewalld` 일괄 OFF는 각 단계의 회귀·복구 확인 전에는 금지한다.
- CP 통신 canary(§3)가 HEP 전환 순서 자체를 대체하지 않는다.
- **복구는 항상 "① 이전에 승인된 보호 상태 확인/복원 → ② 이번 실행에서 추가한 HEP/GNP만 회수
  → ③ 재검증" 순서를 따른다.** "HEP부터 먼저 삭제"하지 않는다 — 이전 보호가 이미 유지 중인지
  먼저 확인해야, HEP 삭제가 실제로 보호 공백을 만드는지 아닌지 판단할 수 있다.
- Cockpit(`*:9090`)은 P1-K8S 착수 전 비활성화 대상이다. 관리 경로 상실 시에도 **미검증 상태로
  다시 활성화하지 않는다** — out-of-band 콘솔(물리 콘솔/IPMI 등 사전에 승인된 경로)만 사용한다.

## 0단계 — 사전 조건

- [ ] `manifests/calico/02~04, 06~08-*-hep.yaml`의 `<실제-NIC-이름>`을 각 노드의 실제 인터페이스명으로 교체했다.
  ```bash
  # 각 노드에서 실행하여 실제 인터페이스명 확인
  ip -o link show | awk -F': ' '{print $2}'
  ```
- [ ] D-NET-01 / ICR-D-NET-01-01 고정 변경안과 IP·NIC 값이 일치함을 대조했다.
- [ ] out-of-band 콘솔(물리 콘솔/IPMI 등, 네트워크 장애 시에도 접근 가능한 사전 승인 경로)이 확보돼 있다.
- [ ] 현재 6대의 `firewalld` 상태(`active`/`inactive`)를 기록해 "이전에 승인된 보호 상태"의 기준점으로 삼는다.

## 1단계 — Bootstrap Allow

```bash
kubectl apply -f manifests/calico/01-bootstrap-allow.yaml
```

- [ ] `bootstrap-allow` GNP만 적용 (HEP는 아직 없음 — HEP가 없으면 Calico는 해당 노드를
      기본적으로 무정책 상태로 두므로, GNP 자체는 선반영해도 트래픽에 영향 없음)
- [ ] 회귀시험 세트 실행 ("Bootstrap 반영 후")

## 2단계 — canary Worker01

```bash
kubectl apply -f manifests/calico/02-worker01-hep.yaml   # 이 파일 하나만 적용
```

- [ ] Worker01에만 HostEndpoint 생성됨을 `kubectl get hostendpoints`로 확인 (다른 노드는 없어야 함)
- [ ] 회귀시험 세트 실행 (Worker01 대상)
- [ ] **재부팅 시험**: Worker01 재부팅 후 HEP/failsafe가 유지되고 관리 접속이 살아있는지 확인
- [ ] 실패 시 아래 복구 절차 수행

```bash
# 복구: ① 이전 보호(firewalld) 상태가 여전히 0단계 기록과 같은지 먼저 확인
ssh worker01 "sudo firewall-cmd --state"
# ② 같다면(=이미 보호 중) 이번 실행분만 회수
kubectl delete hostendpoint worker01-hep
# ③ 재검증
./scripts/verify-probe-failover.sh deny-flow
```

## 3단계 — Worker 확대 (02, 03)

- [ ] Worker01 canary가 회귀·재부팅·복구까지 전부 PASS인 경우에만 진행
- [ ] 아래 두 파일을 **개별로, 순서대로** 적용 (한 번에 여러 파일 지정 금지)

```bash
kubectl apply -f manifests/calico/03-worker02-hep.yaml
# 회귀시험 세트 실행 (Worker02 대상) ...
kubectl apply -f manifests/calico/04-worker03-hep.yaml
# 회귀시험 세트 실행 (Worker03 대상) ...
```

- [ ] 3대 전체 완료 후에만 NodePort 규칙 적용

```bash
kubectl apply -f manifests/calico/05-nodeport-allow.yaml
./scripts/verify-probe-failover.sh allow-flow   # edge01 -> Worker:30080, 3/3 PASS 필요
```

## 4단계 — Control Plane

- [ ] Worker 3대 전체가 안정화된 것을 최소 1 관찰 주기 확인 후 진행. 관찰 주기 길이는 팀이
      운영 여건에 맞춰 정하며, 이 문서가 특정 시간(예: 24h)을 승인된 고정 조건으로 강제하지 않는다.
- [ ] 아래 세 파일을 **개별로, 순서대로** 적용

```bash
kubectl apply -f manifests/calico/06-cp01-hep.yaml
# quorum·회귀시험 확인 ...
kubectl apply -f manifests/calico/07-cp02-hep.yaml
# quorum·회귀시험 확인 ...
kubectl apply -f manifests/calico/08-cp03-hep.yaml
# quorum·회귀시험 확인 ...
```

- [ ] CP는 etcd/API 가용성에 직접 영향을 주므로 각 CP 적용 후 클러스터 quorum과 etcd 프로세스
      상태를 반드시 확인한다. **etcd 프로세스가 Running이라는 것만으로 quorum이 유지된다고
      판정하지 않는다** — `endpoint status`로 실제 leader/term을 확인한다.

```bash
kubectl get --raw='/healthz?verbose'
ssh cp01 "sudo ETCDCTL_API=3 etcdctl --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key \
  endpoint status --cluster -w table"
```

## 5단계 — firewalld 최종 비활성화

- [ ] 전체(6대) HEP/GNP가 회귀·재부팅·복구 PASS로 확인된 이후에만 진행
- [ ] 한 대씩 `firewalld disable && firewalld stop` 적용 후 즉시 회귀시험, 문제 없으면 다음 노드
- [ ] baseline 목표: "최종 6대 firewalld disabled/inactive 및 named HEP/GNP" 상태로 종료

```bash
ssh <node> "sudo systemctl disable --now firewalld"
./scripts/verify-probe-failover.sh allow-flow
./scripts/verify-probe-failover.sh deny-flow
```

## 회귀시험 세트 (각 단계마다 반복)

| 확인 항목 | 명령 | 기대 결과 |
|---|---|---|
| SSH 관리 접속 | `ssh <node> true` | 성공 (exit 0) |
| API Server | `kubectl get --raw=/healthz` | `ok` |
| etcd (CP만) | `etcdctl endpoint status --cluster` | 전 endpoint 응답, leader 1개 |
| kubelet | `systemctl status kubelet` | active |
| Calico felix/VXLAN | `calicoctl node status` (felix 상태), `ip link show | grep vxlan` | felix active, vxlan.calico 인터페이스 존재 (BGP Disabled 구성이므로 BGP peer 확인은 대상 아님) |
| DNS | `kubectl exec -n fds-app deploy/transaction-api -- nslookup kubernetes.default` | 응답 |
| Harbor Pull | `crictl pull 10.1.93.54/fds/transaction-api:latest` (또는 최근 digest) | 성공 |

## 복구 판단 기준

- 임의 단계에서 회귀시험 1개라도 FAIL 시 **즉시 해당 단계를 롤백**하고 다음 단계로 진행하지 않는다.
- 롤백은 항상 "이전 보호 상태 확인 → 이번 실행분 HEP/GNP만 회수 → 재검증" 순서다.
- "timeout/도구 부재"와 "정책 차단"을 구분한다 (`scripts/verify-probe-failover.sh`, `verify-rbac.sh`
  참고 — 도구 부재·API 오류는 ERROR로 별도 표기, PASS로 기록하지 않음).
