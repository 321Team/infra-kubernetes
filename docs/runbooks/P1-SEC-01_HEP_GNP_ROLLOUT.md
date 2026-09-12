# P1-SEC-01 — Calico HEP/GNP 보호 전환 Runbook (SEC-06 대응)

> 대응 리뷰: `fds-project-baseline#19` (issuecomment-5627308483), SEC-06 (Critical)
> baseline 근거: `FDS_Project_Implementation_Baseline_v3.1.18.md` §6.6 "HostEndpoint 정책은
> Bootstrap Allow → canary Worker → Worker 확장 → Control Plane 순서로 적용"

## 원칙

- 이전 단계의 보호가 정상 복원됨을 확인하기 전까지 다음 단계로 진행하지 않는다.
- HEP 제거 또는 `firewalld` 일괄 OFF는 각 단계의 회귀·복구 확인 전에는 금지한다.
- CP 통신 canary(§3)가 HEP 전환 순서 자체를 대체하지 않는다.

## 0단계 — 사전 조건

- [ ] `manifests/04-calico-hep-gnp.yaml`의 `<실제-NIC-이름>` 3곳을 각 Worker의 실제 인터페이스명으로 교체했다.
  ```bash
  # 각 Worker에서 실행하여 실제 인터페이스명 확인
  ip -o link show | awk -F': ' '{print $2}'
  ```
- [ ] D-NET-01 / ICR-D-NET-01-01 고정 변경안과 IP·NIC 값이 일치함을 대조했다.
- [ ] 관리 콘솔(예: Cockpit, IPMI, 물리 콘솔 등 네트워크 장애 시에도 접근 가능한 out-of-band 경로)이 확보돼 있다.

## 1단계 — Bootstrap Allow

```bash
kubectl apply -f manifests/04-calico-hep-gnp.yaml  # bootstrap-allow GlobalNetworkPolicy만 우선 반영되도록
                                                      # HostEndpoint/NodePort 블록은 이 시점에 아직 실제 노드에 걸지 않음
```

- [ ] `bootstrap-allow` GNP만 적용 (HostEndpoint는 아직 생성하지 않음 — HEP가 없으면 Calico는 해당 노드를
      기본적으로 무정책 상태로 두므로, GNP 자체는 선반영해도 트래픽에 영향 없음)
- [ ] SSH/API/etcd/kubelet/Calico/DNS/Harbor 회귀시험 (아래 회귀시험 세트, "Bootstrap 반영 후" 컬럼)

## 2단계 — canary Worker 1대

```bash
kubectl apply -f - <<'EOF'
# manifests/04-calico-hep-gnp.yaml 중 worker01-hep 블록만 우선 적용
EOF
kubectl label crd.projectcalico.org/hostendpoints worker01-hep rollout-stage=canary --overwrite
```

- [ ] Worker01에만 HostEndpoint 생성
- [ ] 회귀시험 세트 실행 (Worker01 대상)
- [ ] **재부팅 시험**: Worker01 재부팅 후 HEP/failsafe가 유지되고 관리 접속이 살아있는지 확인
- [ ] 실패 시 즉시 Worker01 HostEndpoint 삭제 후 이전 상태(순수 firewalld) 복원 확인

```bash
# 롤백
kubectl delete hostendpoint worker01-hep
# firewalld 상태가 이전과 동일한지 확인 (OFF 하지 않았다면 그대로 유지되어 있어야 함)
ssh worker01 "sudo firewall-cmd --state"
```

## 3단계 — Worker 확대 (02, 03)

- [ ] Worker01 canary가 회귀·재부팅·복구까지 전부 PASS인 경우에만 진행
- [ ] Worker02, Worker03 순서로 동일 절차 반복 (한 번에 전체 적용 금지)
- [ ] 3대 전체 완료 후 `nodeport-30080-allow` GNP 적용 및 §3.6 실측 Source IP 검증

```bash
kubectl apply -f manifests/04-calico-hep-gnp.yaml  # nodeport-30080-allow 포함 전체 반영
./scripts/verify-probe-failover.sh allow-flow   # edge01 -> Worker:30080 확인
```

## 4단계 — Control Plane

- [ ] Worker 3대 전체가 안정화된 것을 최소 1 관찰 주기(예: 24h) 확인 후 진행
- [ ] CP1 → CP2 → CP3 순서로 동일하게 canary 방식 적용
- [ ] CP는 etcd/API 가용성에 직접 영향을 주므로 각 CP 적용 후 클러스터 quorum 상태를 반드시 확인

```bash
kubectl get componentstatuses 2>/dev/null || kubectl get --raw='/healthz?verbose'
ssh cp01 "sudo systemctl status etcd 2>/dev/null; sudo crictl ps | grep etcd"
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
| SSH 관리 접속 | `ssh <node> true` | 성공 |
| API Server | `kubectl get --raw=/healthz` | `ok` |
| etcd (CP만) | `sudo crictl ps \| grep etcd` | Running |
| kubelet | `systemctl status kubelet` | active |
| Calico BGP/felix | `calicoctl node status` | 정상 |
| DNS | `kubectl exec -n fds-app deploy/transaction-api -- nslookup kubernetes.default` | 응답 |
| Harbor Pull | `crictl pull 10.1.93.54/fds/transaction-api:latest` (또는 최근 digest) | 성공 |

## 복구 판단 기준

- 임의 단계에서 회귀시험 1개라도 FAIL 시 **즉시 해당 단계를 롤백**하고 다음 단계로 진행하지 않는다.
- "timeout/도구 부재"와 "정책 차단"을 구분한다 (`scripts/verify-probe-failover.sh` 참고 — 도구 부재는 WARN으로 별도 표기, PASS로 기록하지 않음).
