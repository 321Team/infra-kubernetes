# P1-K8S-01 Gate 2 리드 전달 최종안 v4.0

작성 기준일: 2026-09-09 · 정본: FDS v3.1.17 · 대상: kubeadm 3CP + 3Worker, Calico v3.32.1 legacy manifest / IPv4

**문서·코드 보완본이다. 실서버 배포 승인이나 Runtime PASS를 의미하지 않는다.** v3.0 재리뷰의 G2-01~08을 본문·정책 생성·검증·복구 코드에 반영했다. 현재 제공되지 않은 실측값은 `config/runtime-input.json`에서 비워 두었으며, 해당 값과 근거 파일의 해시가 채워지기 전에는 적용 YAML을 생성하지 않는다.

Gate 0의 기존 진행·승인·증적을 유지한다. Cockpit 선행 조치가 실제 완료됐으면 재검증만 수행한다. Gate 1의 설치 절차, Gate 2의 static local PV·PSA 등 이번 추가 리뷰 범위 밖의 기존 승인 내용은 별도 유지한다. 이 문서는 Gate 1 AVC 및 Gate 2 네트워크 전환·직접 의존 구간의 교체본이다. 원문 상단 Source는 v3.1.17과 이 보완본으로 변경하고, 기존 5~9번 초안·잘못된 복구 참조·중복 YAML·대화체 메모는 삭제한다.

## 1. 확정 설계와 적용 전 입력

| 노드 | IP | 역할 | 전환 순서 |
|---|---|---|---:|
| k8s-worker01 | 10.1.93.105 | Worker | 1 |
| k8s-worker02 | 10.1.93.107 | Worker | 2 |
| k8s-worker03 | 10.1.93.108 | Worker | 3 |
| k8s-control01 | 10.1.93.102 | CP | 4 |
| k8s-control02 | 10.1.93.103 | CP | 5 |
| k8s-control03 | 10.1.93.104 | CP | 6 |

| 계약 | 반영 정책 |
|---|---|
| IF-07 | ansible01 `10.250.10.10/32` → 6대 TCP22. Node→Ansible TCP22 신규 연결 허용 없음 |
| IF-11A / IF-12 | 6대 → API LB `.109:6443`; LB `.109/32` → CP만 TCP6443 |
| IF-12A | CP 상호 TCP2379/2380. Worker의 etcd ingress/egress 허용 없음 |
| IF-12B | CP → 6대 TCP10250. Worker의 kubelet 신규 egress 허용 없음 |
| IF-12C | 정확한 6개 /32 상호 UDP4789. TCP179 및 IP protocol 4 허용 없음 |
| IF-13 / IF-15 | Node → `.52/.53` UDP/TCP53. Node→8.8.8.8 직접 DNS 허용 없음 |
| IF-10 | Worker → Harbor `.54:443`. CP의 Harbor Pull은 IF-10으로 자동 허용하지 않음 |
| NTP / OID-02·03 | 실제 승인 NTP 목적지, System·Test 이미지 공급 방식은 입력과 승인 근거로 확정 |

정본의 역사적 DNS 상태는 P1-DNS-01 Done이다. 현재 노드 Resolver는 `.52/.53` 사용과 응답을 다시 측정한다. `/etc/hosts`는 등록된 이름에만 적용되며 DNS 서버 장애가 이미지 공급·이름해석에 영향 없다는 의미가 아니다. [정본 §6.6·§15](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)

### 1.1 입력값을 확정하는 방법

`config/runtime-input.json`의 노드 IP·정책 구조는 고정하고 다음 값만 실측·결정 자료로 채운다. `evidence`는 실제 파일 경로와 SHA-256이며 파일 존재·동일성만 검사한다. 그 내용의 적정성과 승인 여부는 검토자가 판단한다. 임의의 빈 파일이나 예제 파일로 대체하지 않는다.

| 입력 | 확인 및 결정 내용 |
|---|---|
| attempt_id | 시도별 고유 ID. 다른 입력·시도의 state/receipt 재사용을 차단 |
| cluster_uid | 승인 CP에서 `kubectl get ns kube-system -o jsonpath='{.metadata.uid}'` |
| interface | 노드별 `ip -j address`의 실제 named interface와 expectedIP. 추가 외부 NIC, IPv6 또는 다른 ingress 경로가 있으면 단일 인터페이스 전제를 재검토 |
| mtu | 실제 Underlay/CNI 경로 시험값. 1450은 후보이며 자동 실측값으로 간주하지 않음 |
| ntp_nets | `chronyc -n sources`, `chronyc tracking`, chrony 설정·DNS 변경 가능성 확인 후 승인 IPv4 /32 목록. 주소가 바뀌는 pool을 현재 한 주소로 영구 고정하지 않음 |
| system_images | `preloaded` 또는 `approved_registry`. OID-02/03의 Owner·경로·이미지 digest·보관·만료·갱신 조건을 결정 문서에 기록 |
| registry_nets | approved_registry 모드에서 노드별 실제 승인 IPv4 /32 HTTPS 목적지. CDN/redirect·미러의 추가 목적지를 임의 확대하지 않음 |
| probe_image | Python 3가 포함된 승인·스캔된 image@sha256. 테스트 Pod는 사전반입 후 `imagePullPolicy: Never` 사용 |
| api_egress_nets | Calico/kube-proxy 등 Host 프로세스에서 실제 필요한 직접 CP API 목적지가 있을 때만 CP /32 입력 |
| api_ingress_node_sources | 해당 CP에서 실제 관측·승인한 Node /32 Source. 상호 egress/ingress가 맞지 않으면 생성 중단 |
| api_ingress_workload_selectors | API 서버에 실제 도달하는 kube-system Workload Source가 필요할 때 승인 endpoint selector 입력. namespace는 kube-system으로 제한. 임의 Pod CIDR 전체 허용 없음 |
| felix_effective | 각 노드 환경변수→설정 파일→node별→selector별→default→버전 기본값 순의 실제 유효 failsafe·dataplane 표 |
| transition / forward_boundary | 아래 전환 순서 변경 및 전달 트래픽 인수 조건의 승인 기록 |
| firewall_checkpoint / recovery | 호스트별 보호 설정, runtime/permanent 일치, 보호 시험, 콘솔·대체 CP API 경로 증적 |
| source_route / api_paths / ntp / dataplane / policy_overlap | Source 보존·DNAT·노드 경로·NTP·실제 dataplane 및 기존 정책 우선순위 검토 결과 |

Kubernetes Service VIP로 연결한 요청이 DNAT 후 CP:6443으로 나타날 수 있다. 이를 모두 LB 경로로 가정하지 않는다. 기본 정책에 없는 직접 CP/API 접근이 필요하면 관측 Source·목적지·port·Consumer를 기존 ICR에 반영하고 해당 입력을 채운다. CoreDNS 등 Workload 경로는 Host 신규 egress와 구분한다. `.10`의 API LB 접근은 IF-11A에 포함되지 않으므로 이 작업을 이유로 신규 허용하지 않는다.

### 1.2 보호 범위와 failsafe 원칙

정책은 노드별 GNP 6개와 HEP 6개로 분리한다. 각 GNP는 자신의 노드 라벨만 선택하고 ingress/egress 마지막에 명시적 Deny를 둔다. 기존 정책의 선행 Allow·Deny, tier, 자동 HEP 및 selector 충돌은 적용 전 별도 확인한다.

`applyOnForward: false`는 Host 자체 송수신 범위다. Pod·NodePort 전체 보호 완료를 의미하지 않는다. P1-SEC-01/IF-02/HO-ANS-04C 인수 전까지 외부 NodePort/LoadBalancer/hostPort/Ingress 개방을 금지한다. 후속 정책은 `.50/32 → Worker:30080`에 대한 preDNAT/applyOnForward 및 Workload 허용을 함께 검토한다. 이 패키지에 `applyOnForward: true`만 추가해 전달 트래픽을 차단하지 않는다. 기존 외부 노출 Service가 이미 있으면 이 시작 조건이 성립하지 않으므로 해당 보호를 먼저 인수한다. [전달 트래픽 범위](https://docs.tigera.io/calico/latest/reference/host-endpoints/forwarded)

global `FelixConfiguration/default`는 수정하지 않는다. GNP 선행 후 **해당 노드의 `node.<nodename>` failsafe만** 변경하고 유효값 확인 후 HEP를 생성한다. SSH는 `.10/32`, API는 LB와 승인된 관측 경로, etcd는 CP만 남긴다. 모든 항목에 net 제한이 있으며 UDP53 전체 예외는 없다. DNS·NTP·Harbor는 GNP로 허용한다. 연결 응답은 conntrack 상태를 따르므로 Node→Ansible destination22를 SSH 응답이라고 추가하지 않는다. [failsafe](https://docs.tigera.io/calico/latest/reference/host-endpoints/failsafe), [연결 추적](https://docs.tigera.io/calico/latest/reference/host-endpoints/conntrack)

node별 리소스를 저장한 것만으로 환경변수·파일 override가 사라지지 않는다. higher-priority failsafe override가 있으면 이 절차로 덮어쓰지 않고 먼저 해당 노드에서 조정·복구 방식을 검토한다. 필드 부재나 필드 존재만으로 유효값을 판단하지 않는다. [Felix 우선순위](https://docs.tigera.io/calico/latest/reference/felix/configuration)

## 2. 실행 위치와 실패 분기

| 절 | 작업 | 실행 위치 |
|---|---|---|
| 3 | 전환 순서·공존 조건 확정, 사전 준비 | 리드·ICR 책임자 / 노드 콘솔 / CP |
| 4 | AVC 시작·Cockpit 적용/확인·firewalld checkpoint | 6대 각각 |
| 5 | Calico 준비, init/join, 6/6 Ready | 승인 CP / 각 join 대상 |
| 6 | 실측 입력·정책 생성·API 사전 검증·GNP 선행 | 작성 호스트 / 승인 CP |
| 7 | **현재 노드 한 대** failsafe→HEP→시험→firewalld OFF | 승인 CP + 대상 노드 |
| 8 | 현재 노드 재부팅·동일 시험·AVC 종료 후 다음 노드 | 대상 노드 + 승인 CP + 시험 Source |
| 9 | 전체 최종 검증·이관 | 승인 CP / 리드 |
| 10 | **모든 단계의 실패 시 복구** | 실패 노드 VMware 콘솔 + 정상 CP 콘솔 |

어느 단계든 실패하면 확대를 중단하고 **§10 복구**로 이동한다. 실패 시 재부팅을 실행하지 않는다. 기존 HEP가 있는 노드는 신규 전환 경로로 처리하지 않으며 별도 migration diff를 작성한다. 기존 HEP를 삭제해서 preflight를 통과시키지 않는다.

## 3. 전환 시작 조건

정본 §6.6은 Calico 설치 전 firewalld OFF를 기록하고 있다. 이번 보호 공백 방지 절차는 **보호가 검증된 firewalld 유지→Calico·6대 join→노드별 인계**로 순서를 변경한다. `ICR_ADDENDUM.md`의 교체 문구와 공존·복구 조건을 기존 ICR에 반영한 기록이 있어야 한다. 이 문서 작성 자체가 정본 변경 승인이나 정본 신규 발행은 아니다.

Calico와 별도 firewall 관리자의 공존에는 규칙 충돌 가능성이 있으므로 일시 공존을 정상 운영 구조로 고정하지 않는다. API·etcd·kubelet·DNS·Pod 경로를 허용하는 이전 보호 설정이 준비돼 있어야 하며, 실패하면 보호 없이 firewalld를 끄지 않는다. runtime/permanent가 서로 다르면 검토·정합화 후 checkpoint를 다시 만든다. [Calico 요구사항](https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements), [firewalld runtime/permanent](https://firewalld.org/documentation/man-pages/firewall-cmd.html)

필수 준비물은 각 노드 VMware 콘솔, 정상 CP 2곳의 기존 관리자 kubeconfig, 각 CP에서 검증한 loopback API 경로, root 전용 checkpoint, 사전반입 이미지·도구, 승인·비승인 시험 Source다. 코드 실행 환경은 Python 3와 PyYAML 6.0.3, bash, kubectl이며 호스트 조회에는 firewalld·audit·iproute·iptables·nft·chrony 도구가 필요하다. 패키지를 실행 중 임의 다운로드·자동 설치하지 않는다.

반드시 확인할 기존 Gate 인수 항목: HO-ANS-01 cgroup 일치, HO-ANS-02A Enforcing Runtime·Calico, HO-ANS-03 고유 식별자, HO-ANS-04A 포트/통신, P1-LB Stage A 승인 증적. 이전 PASS는 현재 Drift 확인을 생략하는 근거로 쓰지 않는다.

## 4. 노드별 기록 시작 및 Cockpit·firewalld 준비

아래 경로는 이 패키지를 `/opt/fds/P1-K8S-01-v4`에 배치한 예다. 실제 배포 경로를 작업표에 한 번 확정한다. 같은 디렉터리를 덮어쓰지 않고 시도별 ID를 사용한다.

```bash
# 각 대상 노드. Gate 1은 설치 전, Gate 2는 init/join/Calico 작업 전에 시작한다.
sudo python3 /opt/fds/P1-K8S-01-v4/code/audit-window.py start \
  --dir /var/log/fds-evidence/P1-K8S-01/gate2-attempt01

# Gate 0 실제 차단 완료 증적이 있으면 check 결과와 연결한다.
sudo bash /opt/fds/P1-K8S-01-v4/code/cockpit.sh check
# 미차단으로 확인되고 해당 차단 조치를 수행할 단계인 경우에만:
sudo bash /opt/fds/P1-K8S-01-v4/code/cockpit.sh apply

sudo python3 /opt/fds/P1-K8S-01-v4/code/node-evidence.py checkpoint-firewall \
  --dir /var/lib/fds/P1-K8S-01/firewall-attempt01
```

Cockpit check 실패를 무시하고 다음 단계로 이동하지 않는다. 미설치는 unit별 NOT_INSTALLED로 기록한다. checkpoint 생성은 보호 시험의 PASS가 아니다. SSH 승인/비승인, 필수 통신, 인터페이스·zone·policy·direct·ipset·NM binding 검토를 함께 기록한다. source 보존은 신규 세션의 `SSH_CONNECTION`, `ip route get 10.250.10.10`, 대상 캡처로 확인한다. 관측 주소가 `.23`이면 임의 허용하지 않고 라우팅/SNAT Drift를 조사한다.

Gate 1 AVC도 동일 코드를 별도 `gate1-attempt01` 디렉터리에 start/end로 사용한다. 날짜와 시간을 별도 인자로 처리하며 UTC/LC_ALL=C, audit marker와 구간 파일을 보존한다. audit 로그 기록 지연이 있으면 기존 종료 구간을 `query`로 재조회한다. 구간을 새로 시작해 실패를 지우지 않는다. 종료 조회의 `<no matches>`만으로 audit 정상성이나 전체 Gate PASS를 선언하지 않는다.

## 5. Calico 설치와 6대 join의 직접 의존 수정

기존 kubeadm 설치·init/join의 승인된 버전·토큰 취급 절차를 유지한다. init의 `--pod-network-cidr=10.244.0.0/16`, API Endpoint `10.1.93.109:6443`과 Calico pool을 일치시킨다. join token·certificate key·admin.conf 내용은 로그·Git·이 전달 패키지에 넣지 않는다.

신규 Calico 설치에만 아래 준비 코드를 사용한다. 이미 설치된 환경에서 실행한 출력물을 그대로 apply하여 DaemonSet 전체 rollout이나 pool 변경을 시작하지 않는다. 기존 환경은 실제 ConfigMap·DS probe·IPPool diff와 영향도를 먼저 검토한다.

```bash
# 작성 호스트: 공식 v3.32.1 파일을 가져와 해시 확인 후 로컬 준비.
curl --fail --location \
  https://raw.githubusercontent.com/projectcalico/calico/v3.32.1/manifests/calico.yaml \
  --output calico-v3.32.1.upstream.yaml
sha256sum calico-v3.32.1.upstream.yaml
# 이 검토에서 확인한 bytes: dce66b28fc93cd59fbc8edf98d54d5f62d91362c1a46c85cbc97ea59e78f15b8
# config/calico-image-lock.json에는 세 종류의 실제 승인 digest를 먼저 입력.
python3 code/prepare-calico.py --source calico-v3.32.1.upstream.yaml \
  --sha256 dce66b28fc93cd59fbc8edf98d54d5f62d91362c1a46c85cbc97ea59e78f15b8 \
  --image-lock config/calico-image-lock.json --mtu "$MEASURED_MTU" \
  --out calico-v3.32.1.prepared.yaml
```

준비 코드는 `calico_backend=vxlan`, IPIP Never, VXLAN CrossSubnet, IPv4 pool `10.244.0.0/16`, NAT outgoing true, MTU 및 digest를 반영한다. BIRD readiness뿐 아니라 BIRD liveness도 제거하고 Felix probe를 유지한다. env 변경만으로 이미 생성된 IPPool이 바뀐다고 가정하지 않는다. [VXLAN/BGP 설정](https://docs.tigera.io/calico/latest/networking/configuring/vxlan-ipip), [공식 manifest](https://github.com/projectcalico/calico/blob/v3.32.1/manifests/calico.yaml)

CP1 init 후 준비·검토된 Calico manifest를 적용한다. 클러스터에 CRD가 없는 최초 설치에서는 단일 전체 manifest의 server dry-run이 CRD를 실제 등록하지 않음을 고려해 CRD 설치·Established 확인 후 나머지 API 검증을 수행한다. client dry-run 성공은 server admission이나 Runtime 성공이 아니다.

```bash
# 승인 CP. 실제 kubeconfig/context 사용, 내용 출력 금지.
kubectl --kubeconfig "$KCFG" --context "$KCTX" apply -f calico-v3.32.1.prepared.yaml
kubectl --kubeconfig "$KCFG" --context "$KCTX" -n kube-system \
  rollout status daemonset/calico-node --timeout=180s
kubectl --kubeconfig "$KCFG" --context "$KCTX" get ippools.crd.projectcalico.org -o yaml
```

CP2→CP3→Worker1→Worker2→Worker3를 한 대씩 join하고 Ready를 확인한다. Calico가 이미 설치된 순서이므로 NotReady를 정상 완료로 기록하지 않는다. 6/6 Ready, Calico/CoreDNS Ready, API 실제 요청·etcd health가 통과해야 §6으로 진행한다. 같은 /24의 CrossSubnet 경로에서는 캡슐화되지 않을 수 있으므로 정상 통신에 UDP4789 패킷이 보이지 않는다는 이유만으로 실패로 판정하지 않는다. 노드 간 Pod 경로·MTU와 실제 route/encapsulation을 함께 확인한다.

## 6. 실측 입력, 테스트 리소스, API 검증과 GNP 선행

먼저 기본 regression과 아래 내용을 수집한다. 전체 환경변수나 Secret, kubeconfig 내용을 증적에 덤프하지 않는다. Felix 관련 값은 failsafe·dataplane·API/Typha endpoint에 필요한 항목만 추려 기록한다.

```bash
# 승인 CP
kubectl --kubeconfig "$KCFG" --context "$KCTX" api-resources --api-group=crd.projectcalico.org
kubectl --kubeconfig "$KCFG" --context "$KCTX" get felixconfigurations.crd.projectcalico.org -o yaml
kubectl --kubeconfig "$KCFG" --context "$KCTX" get hostendpoints.crd.projectcalico.org -o yaml
kubectl --kubeconfig "$KCFG" --context "$KCTX" get globalnetworkpolicies.crd.projectcalico.org -o yaml
kubectl --kubeconfig "$KCFG" --context "$KCTX" get workloadendpoints.crd.projectcalico.org -A -o yaml
kubectl --kubeconfig "$KCFG" --context "$KCTX" get kubecontrollersconfigurations.crd.projectcalico.org -o yaml
```

node별 Felix 실측 표에 `failsafeInboundHostPorts`, `failsafeOutboundHostPorts`, `BPFEnabled`, nftables mode, iptables backend 및 `DefaultEndpointToHostAction`의 유효값을 기록한다. 버전 기본값은 최하위 fallback이다. 환경변수는 `FELIX_FAILSAFEINBOUNDHOSTPORTS`, `FELIX_FAILSAFEOUTBOUNDHOSTPORTS` 등의 해당 값만 확인하고, 설정 파일 경로는 실제 프로세스 인자로 식별한다. DaemonSet 명세만으로 실제 컨테이너의 유효값을 확정하지 않는다. 이 구현은 iptables dataplane용이며 nftables-native/eBPF로 측정되면 기존 코드로 집행 PASS를 만들지 않는다.

```bash
# 작성 호스트. 입력·근거 파일을 완성한 뒤 새 디렉터리로 생성.
python3 code/render.py --config config/runtime-input.json --out generated-attempt01
cd generated-attempt01
sha256sum --check SHA256SUMS
```

생성된 `gnp/`, `hep/`, `felix/`, `probes.yaml`, `config.lock.json`과 SHA256SUMS를 함께 검토·고정하고 승인 CP로 전달한다. Workload API selector 및 image endpoint 확장은 기존 ICR 근거가 있는지 검토한다. `policy_templates/*.yaml.in`은 리드 열람용이며 적용 파일로 쓰지 않는다.

CP에서 실제 `KCFG`, `KCTX`, 패키지 `PKG`, 생성본 `GEN`, node별 API backup용 `STATE`를 지정한다. context는 `kubectl --kubeconfig "$KCFG" config current-context`로 확인하되 임의 context로 작업하지 않는다. kubeconfig를 Worker로 복사하지 않는다.

```bash
python3 "$PKG/code/cluster-change.py" preflight --bundle "$GEN" --state "$STATE" \
  --kubeconfig "$KCFG" --context "$KCTX"
# 정책 전체의 서버 검증. create-hep는 이 시점에 실제 실행하지 않는다.
for node in k8s-worker01 k8s-worker02 k8s-worker03 k8s-control01 k8s-control02 k8s-control03; do
  kubectl --kubeconfig "$KCFG" --context "$KCTX" create --dry-run=server -f "$GEN/gnp/$node.yaml"
  kubectl --kubeconfig "$KCFG" --context "$KCTX" create --dry-run=server -f "$GEN/hep/$node.yaml"
done
python3 "$PKG/code/cluster-change.py" stage-gnp --bundle "$GEN" --state "$STATE" \
  --kubeconfig "$KCFG" --context "$KCTX"

# 승인 digest를 해당 Worker의 containerd k8s.io namespace에 사전반입하고 검증한 뒤:
kubectl --kubeconfig "$KCFG" --context "$KCTX" create --dry-run=server -f "$GEN/probes.yaml"
kubectl --kubeconfig "$KCFG" --context "$KCTX" create -f "$GEN/probes.yaml"
```

실행 셸은 `set -euo pipefail`을 사용한다. 위 반복은 **dry-run과 GNP 준비에만** 허용한다. HEP 생성·firewalld OFF·재부팅의 6대 일괄 반복문을 만들지 않는다. 서버 검증은 admission/스키마 검사이며 Calico 의미·실집행 확인은 계속 필요하다. [GNP API](https://docs.tigera.io/calico/latest/reference/resources/globalnetworkpolicy)

이미지 사전반입은 승인 tar·digest와 runtime reference의 매핑을 확인한다. 예를 들어 containerd에 `ctr -n k8s.io images import APPROVED_IMAGE_TAR`로 반입한 뒤 `crictl images --digests`로 실제 kubelet 사용 경로를 확인한다. 명령 성공만으로 참조 digest·필요 플랫폼·Calico/CoreDNS/etcd/kube-apiserver 이미지 전체가 준비됐다고 판정하지 않는다. 각 Worker에 probe-a/b/c를 하나씩 배치해 Worker3 전환도 실제 경로 시험에 포함한다. 테스트 Pod에는 패키지 다운로드나 root 권한을 추가하지 않는다.

## 7. 현재 노드 한 대의 보호 전환

현재 노드는 최초 `k8s-worker01`이다. §8의 해당 노드 재부팅·시험·증적 확인이 끝난 뒤에만 표의 다음 노드로 바꾼다. shell 변수 `NODE`에 정확한 한 이름을 설정하고 실행 위치마다 실제 hostname을 확인한다.

1. 이전 보호 checkpoint와 콘솔·대체 CP API를 확인한다. 현재 노드에서 `node-evidence.py collect`로 전환 전 전체 규칙·카운터·상태를 새 디렉터리에 수집한다.
2. 승인 CP에서 node별 failsafe만 기록한다. 코드는 기존 node 리소스의 부재/내용을 먼저 저장하며 두 failsafe 필드만 바꾼다.

```bash
python3 "$PKG/code/cluster-change.py" set-failsafe --node "$NODE" \
  --bundle "$GEN" --state "$STATE" --kubeconfig "$KCFG" --context "$KCTX"
```

3. 대상 노드의 실제 유효 failsafe가 생성본과 같고 다른 노드의 값은 바뀌지 않았는지 확인한다. 기존 SSH와 별도로 `.10`에서 신규 SSH를 확인한다. 실패하면 §10으로 이동한다.
4. 승인 CP에서 해당 HEP 하나를 생성한다.

```bash
python3 "$PKG/code/cluster-change.py" create-hep --node "$NODE" \
  --bundle "$GEN" --state "$STATE" --kubeconfig "$KCFG" --context "$KCTX"
```

5. **이때부터 HEP가 집행된다.** firewalld ON 상태에서 인터페이스→HEP→GNP→failsafe의 실제 chain 연결·규칙·카운터를 확인하고 `TEST_MATRIX.md`의 Positive/Negative/Regression을 수행한다. 공존 상태에서 negative가 실패했다는 사실만으로 Calico 차단을 단정하지 않는다. [HEP 동작](https://docs.tigera.io/calico/latest/reference/resources/hostendpoint)
6. 대상 노드의 검증된 HEP 보호가 작동하고 복구가 준비됐을 때만 대상 노드에서 아래 명령을 실행한다.

```bash
test "$(hostnamectl --static)" = "$NODE"
sudo systemctl disable --now firewalld
systemctl show firewalld -p Id -p LoadState -p UnitFileState -p ActiveState
# 필수 예상값: UnitFileState=disabled, ActiveState=inactive
sudo python3 /opt/fds/P1-K8S-01-v4/code/node-evidence.py collect \
  --dir /var/log/fds-evidence/P1-K8S-01/after-off-attempt01
```

7. firewalld OFF 후 동일한 신규 SSH·비승인 접근·DNS UDP/TCP·NTP·이미지·Pod/Service/API/etcd 시험을 재수행한다. Calico chain 일부 문자열이나 기존 연결 유지로 PASS 처리하지 않는다. 해당 노드의 모든 필수 시험이 통과해야 §8로 진행한다.

## 8. 현재 노드 재부팅 지속성 검증

재부팅 전에 Cockpit 조치 완료/미설치 결과와 audit 시작 파일을 확인한다. 승인 Source 신규 접속·비승인 차단·회귀시험이 정상인 **현재 노드 한 대**만 재부팅한다.

```bash
test "$(hostnamectl --static)" = "$NODE"
sudo reboot
```

해당 노드 복귀 후 firewalld disabled/inactive, HEP·GNP·유효 failsafe, 실제 dataplane, 신규 SSH·Negative를 다시 확인한다. `regression.py`와 Host DNS/NTP·필수 image 공급, Enforcing·Cockpit을 재검증한다.

```bash
# 대상 노드
sudo bash /opt/fds/P1-K8S-01-v4/code/cockpit.sh check
sudo python3 /opt/fds/P1-K8S-01-v4/code/node-evidence.py collect \
  --dir /var/log/fds-evidence/P1-K8S-01/after-reboot-attempt01
sudo python3 /opt/fds/P1-K8S-01-v4/code/audit-window.py end \
  --dir /var/log/fds-evidence/P1-K8S-01/gate2-attempt01

# 승인 CP. 이 파일명은 노드/시도/단계를 구분해 지정한다.
python3 "$PKG/code/regression.py" --kubeconfig "$KCFG" --context "$KCTX" \
  --cluster-uid "$CLUSTER_UID" --out "$NODE-after-reboot-regression.json"
```

audit 결과 `NO_AVC_REVIEW_REQUIRED`는 4개 이벤트 없음·marker 보존·audit 상태의 자동 검사 결과다. auditd 정지/로그 회전·유실·시간 역행 여부와 전체 구간을 검토한 뒤 판정한다. 재부팅 전후 boot_id가 같으면 재부팅 증적이 아니다. 어느 항목도 실패를 N/A로 바꿔 넘기지 않는다.

**확대 순서:** Worker1 전체 완료→Worker2 전체 완료→Worker3 전체 완료→CP1 전체 완료→CP2 전체 완료→CP3 전체 완료. CP 전환마다 etcd 3 endpoint health/status와 API /readyz 실제 요청을 확인한다. LB Backend UP·인증 API·장애제외·복구는 P1-LB Stage B와 연결하며 단순 Pod Running 또는 etcd member list로 대체하지 않는다.

## 9. 최종 판정과 후속 인수

6대의 정책 해시·node별 유효 failsafe·Source 보존·Positive/Negative/Regression/Reboot/AVC/Cockpit·복구 확인을 `TEST_MATRIX.md`와 연결한다. 문서 준수, 스키마 성공, Runtime 증적, 리드/최종 승인 판정을 구분한다.

Harbor 미구축 상태라면 Host 정책에 IF-10은 반영하되 현재 Harbor Runtime PASS를 만들지 않는다. 대신 승인된 사전반입/대체 공급 조건과 인수 시점을 증적에 남긴다. IF-10 실제 CA Trust·Worker Pull·Worker Push DENY는 P1-HBR/P1-K8S 인수 조건으로 남는다. CP/System registry 선택·추가 egress는 OID-02/03의 승인 범위여야 한다.

Bootstrap 임시 정책의 Owner는 P1-K8S 리드, 후속 Host/Workload/NodePort 통제 인수는 P1-SEC-01 및 관련 ICR이다. 만료일/검토일과 인수 증적을 실제 결정 기록에 넣고, 영구 정책이 동일 허용·차단·회귀·복구를 충족한 뒤 노드별로 인계한다. 대체 정책이 없는데 임시 정책을 만료일만으로 삭제하지 않는다. 기존 static local PV, Control Plane taint, PSA 요건은 원 티켓 AC에 따라 별도 판정한다.

## 10. 실패 시 복구 — 모든 실패 분기의 목적지

**순서: 확대 중단→대상 콘솔→이전 보호 복원·검증→정상 CP API에서 대상 HEP 제거→해당 failsafe 복원→회귀·보호 재검증.** 보호 없이 HEP를 먼저 삭제하지 않는다. 전체 HEP 삭제, GNP Allow All, iptables/nft flush, kubeadm reset, VM 일괄 스냅샷 복귀를 일반 복구 명령으로 사용하지 않는다.

### 10.1 대상 노드에서 이전 firewalld 보호 복원

VMware 콘솔로 대상 노드에 접속한다. 아직 firewalld ON인 실패도 기존 보호가 정상인지 확인한다.

```bash
sudo python3 /opt/fds/P1-K8S-01-v4/code/node-evidence.py restore-firewall \
  --dir /var/lib/fds/P1-K8S-01/firewall-attempt01
```

코드는 checkpoint·archive 해시, hostname, firewalld/NM 파일 변경 여부를 확인하고 enable/start/reload 후 원래 runtime과 비교한다. runtime-only 상태를 잃지 않도록 시작 시 permanent와 동일한 checkpoint를 요구했다. 파일 Drift가 있으면 보호를 유지한 채 별도 diff를 검토하고, 이 작업에서 변경된 파일만 backup으로 복원한 다음 다시 수행한다. NM 연결·경로를 무조건 백업으로 덮어쓰지 않는다.

firewalld 파일 복원이 필요한 경우의 콘솔 명령 예시는 다음과 같다. checkpoint가 만든 신뢰 가능한 tar와 기록 SHA를 먼저 확인하고, 대상 노드의 HEP 보호가 유지되는 상태에서 실행한다. 복구 중 다른 팀의 변경이 발견되면 전체 덮어쓰기로 해결하지 않는다.

```bash
# root 콘솔, 검토된 config Drift에만 사용. 기존 디렉터리는 보존한다.
test -d /etc/firewalld
test ! -e /etc/firewalld.pre-fds-restore-attempt01
mv /etc/firewalld /etc/firewalld.pre-fds-restore-attempt01
tar --acls --xattrs --selinux -xpf \
  /var/lib/fds/P1-K8S-01/firewall-attempt01/firewalld-config.tar -C /
# 이후 restore-firewall을 다시 실행하고 실제 보호 시험 수행.
```

복원된 firewall의 규칙 경로·대상 인터페이스·Source 제한을 확인하고 승인/비승인 시험·카운터를 연결한다. Calico가 먼저 차단하는 경우 firewalld 자체 차단 증거를 자동 획득했다고 볼 수 없으므로 사전 검증된 checkpoint와 복원 규칙 일치를 함께 검토한다. 공존 충돌로 이전 보호의 정상 작동이 확보되지 않으면 HEP를 삭제하지 않는다. 승인된 상위 보호/격리 경로를 확보한 후 별도 복구한다.

### 10.2 정상 CP의 API 복구 경로

Worker 콘솔에는 관리자 kubeconfig를 배포하지 않는다. 실패 대상과 다른 정상 CP의 VMware 콘솔에서, 그 CP의 기존 kubeconfig와 인증서 SAN으로 사전에 검증한 loopback API를 사용한다.

```bash
# 정상 CP의 root 콘솔. SAN은 해당 CP 인증서에 실제 포함된 IP 또는 DNS 이름.
openssl x509 -in /etc/kubernetes/pki/apiserver.crt -noout -ext subjectAltName
kubectl --kubeconfig /etc/kubernetes/admin.conf --context "$RECOVERY_CONTEXT" \
  --server=https://127.0.0.1:6443 --tls-server-name="$VERIFIED_API_SAN" \
  --request-timeout=30s get --raw='/readyz?verbose'
```

TLS 검증 우회는 사용하지 않는다. 이 경로의 조회뿐 아니라 HEP/Felix 변경 권한·server dry-run을 사전 검증한다. CP 한 대씩 전환하므로 나머지 정상 CP와 etcd quorum을 복구에 사용한다. 정상 CP에서도 API 쓰기가 불가능하면 이 절차의 적용 한계를 벗어난 장애이며 보호/격리를 유지한 채 CP/etcd 복구 Runbook으로 전환한다. API가 안 되는데 같은 kubectl 명령을 반복하지 않는다.

### 10.3 대상 HEP·failsafe 복원

`config/protection-receipt.example.json`을 실제 노드·cluster UID·attempt_id·config.lock.json의 SHA-256·보호 복원 시험 근거로 작성한다. 검토 후 status를 `PROTECTION_RESTORED_REVIEWED`로 기록한다. 이 파일은 사람이 확인한 보호 인계 증적이며 자동 원격 상태 측정 기능이 아니다. 과거 시도 receipt를 재사용하지 않는다.

```bash
# 정상 CP의 검증된 loopback 관리 경로에서 수행.
python3 "$PKG/code/cluster-change.py" rollback-node --node "$NODE" \
  --bundle "$GEN" --state "$STATE" \
  --kubeconfig /etc/kubernetes/admin.conf --context "$RECOVERY_CONTEXT" \
  --server https://127.0.0.1:6443 --tls-server-name "$VERIFIED_API_SAN" \
  --protection-receipt "$NODE-protection-receipt.json"
```

코드는 이 시도에서 만든 HEP의 UID/spec/labels와 현재 값을 비교하고 UID/resourceVersion 조건부 삭제를 요청한다. 원래 node별 Felix가 있었다면 두 failsafe 필드만 이전 값/부재로 복원하고 다른 필드는 유지한다. 원래 없었다면 이 시도에서 추가한 failsafe 전용 리소스만 조건부 제거해 기존 상속 상태로 돌린다. global/default 및 환경변수·파일 설정은 이 작업에서 변경하지 않았으므로 복구 중 임의 변경하지 않는다. [kubectl raw delete](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_delete/), [구현의 stdin 처리](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/delete/delete.go)

기존 리소스의 Drift, API timeout 후 적용 여부 불명, snapshot 저장 실패가 있으면 자동 재시도를 하지 않고 현재 UID/spec·audit와 checkpoint를 대조한다. backup은 변경 전 먼저 저장되지만 여러 API 변경의 원자적 트랜잭션은 아니다.

### 10.4 복구 판정

원래 보호 설정·승인 신규 SSH·비승인 차단·Node/DNS/NTP·API/etcd·Pod/Service 및 다른 노드의 무영향을 재검증한다. HEP NotFound나 일부 Calico chain 잔존은 보호 복구 PASS가 아니다. failsafe도 이전 유효 상속 상태로 돌아왔는지 확인한다. 장애 원인과 재시도 버전을 확정하기 전 다음 노드로 진행하지 않는다.

HEP가 없어 더 이상 선택되지 않는 이번 GNP만 정리할 필요가 있으면 해당 노드 이름으로 수행한다.

```bash
python3 "$PKG/code/cluster-change.py" cleanup-gnp --node "$NODE" \
  --bundle "$GEN" --state "$STATE" --kubeconfig "$KCFG" --context "$KCTX"
```

정상 전환 완료 노드의 GNP/HEP는 유지한다. 전체 전환을 철회해야 하면 한 대씩 보호를 먼저 복원하고 동일 절차로 되돌린다. 테스트 리소스도 증적 수집·인수 완료 후 이번 패키지가 만든 Namespace/Pod/Service만 확인하여 정리한다.
