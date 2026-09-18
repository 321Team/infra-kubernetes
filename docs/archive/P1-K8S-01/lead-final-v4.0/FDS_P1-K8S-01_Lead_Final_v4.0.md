# P1-K8S-01 리드 전달 최종안 v4.0 — 본문·정책·전체 코드

작성 기준일: 2026-09-09. ZIP의 파일별 원문을 통합한 열람본이다. 코드의 권위 원본은 ZIP 내 해당 파일이며, 정책의 실측 입력과 운영 검증은 본문 절차를 따른다.

---

# P1-K8S-01 리드 전달 패키지 v4.0

G2-01~08의 권고·코드 보완을 반영한 최종 전달안이다. **운영 서버에 적용하지 않았으며 Runtime PASS와 실서버 배포 승인을 포함하지 않는다.**

## 전달 내용

| 파일 | 용도 |
|---|---|
| RUNBOOK_FINAL.md | Notion 교체 본문: 입력 확정, GNP 선행, 한 대씩 HEP/failsafe 전환, 해당 노드 재부팅, 보호 복원 후 rollback |
| TEST_MATRIX.md | 20개 Runtime 시험·증적 판정 기준과 실제 실행 명령 |
| ICR_ADDENDUM.md | 정본 §6.6 전환 순서·System API/NTP/이미지·후속 보안 인수의 기존 ICR 반영 문안 |
| TRACEABILITY.md | R1~R5/A1/A2, G2-01~08 수정 위치·코드·잔여 실측 연결 |
| VALIDATION.md | 로컬 정적·모의 검증 결과 및 미수행 범위 |
| code/ | 정책 생성, Calico manifest 준비, node별 API 변경/복구, 호스트 checkpoint, AVC, Cockpit, 통신/회귀 코드 |
| config/ | 실측·결정 입력, Calico image digest lock, 보호 복원 receipt 입력 양식 |
| policy_templates/ | GNP/HEP/node failsafe의 노드별 전체 YAML 열람본. `.yaml.in`은 적용하지 않음 |
| tests/ | 로컬 회귀·실패 조건 및 CRD 일부 스키마 대조 코드 |
| validation/ | 이번 로컬 검증 로그 |
| SHA256SUMS | 패키지 내부 파일 무결성 목록 |

## 적용 상태

문서·코드 보완은 반영했다. NTP 목적지, System/Test 이미지 digest와 공급 방식, 실제 인터페이스·MTU, Felix 유효 설정, DNAT 후 System API 경로, 보호 checkpoint와 복구 경로, 전환 변경 승인 증적은 아직 제공되지 않았다. `runtime-input.json`을 임의 값으로 채우지 않는다. renderer는 입력이 없거나 근거 해시가 맞지 않으면 적용 파일 생성을 중단한다.

`.yaml.in` 열람본에는 NTP·인터페이스의 명시적 미확정 표식이 있다. 이는 최종 운영 파일이 아니다. `code/render.py`가 검토된 입력으로 실제 `.yaml`과 node failsafe `.json`, 테스트 리소스 및 SHA256SUMS를 생성한다. 생성 완료 상태도 `GENERATED_FOR_SERVER_VALIDATION`이며 최종 승인이 아니다. 원문의 광범위 global failsafe patch, 5개 HEP 일괄 apply, HEP 선삭제 복구는 폐기한다.

## 리드 전달문

추가 재리뷰의 G2-01~08을 본문과 코드에 반영한 최종안을 전달합니다. SSH·승인 DNS·역할별 포트·Host Egress를 정합화하고, GNP 선행 및 노드별 failsafe/HEP→전환 시험→재부팅 확인 순서로 변경했습니다. 복구는 이전 보호를 먼저 복원한 뒤 대상 HEP와 node별 failsafe만 되돌리며, AVC 구간 보존과 Cockpit 차단 후 지속성 검증도 포함했습니다. 아직 제공되지 않은 서버 실측값은 입력·검증 단계로 명시해 임의 추정 없이 적용 파일을 생성하도록 구성했습니다. 이 전달본의 문서·코드 반영과 실제 운영 적용 승인은 구분하며, 기존 Gate 0 진행은 유지합니다.

## 검증 재현

작성 호스트에 승인된 Python 3/PyYAML을 준비한다. 테스트는 운영 API 호출이나 호스트 방화벽 변경을 수행하지 않는다.

```bash
sha256sum --check SHA256SUMS
python3 tests/validate.py
python3 tests/check-crd.py --source /path/to/calico-v3.32.1.upstream.yaml
python3 -m compileall -q code tests
bash -n code/cockpit.sh
```

CRD 대조는 파일의 API/필드/타입 등 일부 규칙 검사이며 전체 JSON Schema/CEL 검증을 대신하지 않는다. 정확한 범위는 VALIDATION.md를 따른다. 테스트의 문서용 주소 `192.0.2.123`, `test.invalid` 및 합성 digest는 운영 승인값이 아니다.

---

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

---

# Gate 2 검증·증적 기준

실제 시험 결과는 모두 NOT RUN이다. 아래 표는 수행 명세이며 성공 기록이 아니다. 각 노드에서 HEP 반영/firewalld ON, firewalld OFF, 재부팅 후, 복구 후 단계를 분리해 기록한다.

## 공통 기록

Node·hostname·cluster UID·시도 ID·boot ID·시작/종료 UTC·bundle/config/policy SHA-256·명령 argv·stdout/stderr·RC·Source/Destination/protocol/port·예상/실제 결과·규칙/카운터·검토자를 연결한다. Secret·join token·개인키·kubeconfig 원문은 제외한다. `node-evidence.py collect`의 command별 rc 125는 수집 실패이며 정상으로 간주하지 않는다.

| TC | 대상·수행 | 기대 결과 / 판정 |
|---|---|---|
| T01 | node별 named HEP·GNP·interface·expectedIP·유효 failsafe | API 저장 값과 실제 집행 일치. 기존 selector/tier/Allow·Deny 충돌 없음 |
| T02 | ansible01 `.10`→대상 TCP22 신규 SSH | Source 보존, TCP 및 정상 인증 성공. 기존 multiplexed/ControlMaster 세션 재사용 금지 |
| T03 | 승인되지 않은 실제 Source→동일 대상 TCP22 | 승인 Source 성공·대상 Listener 정상 이후 비승인 packet의 규칙 차단 입증 |
| T04 | LB `.109`→각 CP TCP6443, 6대→LB TCP6443 | 허용, 실제 API 요청 정상. Worker API Listener 허용 없음 |
| T05 | 비승인 Source→CP TCP6443/2379/2380 및 Node10250 | 대상 Listener 존재·승인 경로 성공·대상 도착·차단 카운터 확인. TLS/HTTP401/403은 TCP 차단이 아님 |
| T06 | CP 상호 etcd health/status, CP→6대 kubelet logs/exec | 3 endpoint health=true, 동일 cluster ID·고유 member ID·leader 존재·errors 없음; API logs/exec 정상 |
| T07 | 서로 다른 Node의 Pod IP 양방향 HTTP | 양방향 200, 3대 Worker의 서로 다른 Pod 쌍 6방향 확인. 동일 노드 시험으로 대체 금지 |
| T08 | 테스트 Service ClusterIP와 FQDN HTTP | 두 경로 모두 200. nslookup 성공만으로 Service 통신 PASS 금지 |
| T09 | Pod→CoreDNS 내부 질의, Node→`.52/.53` UDP/TCP53 | 각 승인 DNS의 기대 응답·NXDOMAIN/정상 A 레코드 기준 기록. timeout 또는 SERVFAIL을 성공으로 간주하지 않음 |
| T10 | Node→8.8.8.8 UDP/TCP53 | 직접 질의 차단. DNS failsafe 전체 허용 없음. timeout 단독 증거는 불충분 |
| T11 | Node→실제 승인 NTP UDP123 | chronyc sources/tracking 정상·동기 상태·유효 endpoint 일치. UDP 전송 성공은 NTP 동기 성공이 아님 |
| T12 | Worker→Harbor `.54:443` | 구축 완료 시 CA 검증·Pull 정상, Push DENY는 HBR 인증 권한 시험과 연결. 미구축 시 승인된 image 공급 대체 조건과 인수 미완료를 기록 |
| T13 | Calico/CoreDNS/etcd/API/System/Test 이미지 | digest/플랫폼/runtime reference 일치. 사전반입이면 전환·재부팅 후 외부 Pull 없이 재생성 가능 |
| T14 | VXLAN/MTU 경로와 UDP4789 승인/비승인 | 실제 route·같은 subnet의 비캡슐화 가능성 확인, cross-node 경로 정상. 비승인 UDP datagram의 무응답만으로 차단 판정 금지 |
| T15 | 비승인 TCP179 및 IP protocol4 | 허용 규칙/failsafe 없음 + 대상 도착·실제 Deny 경로 증거. BIRD/Listener 부재만으로 방화벽 차단 판정 금지 |
| T16 | 현재 노드 재부팅 | boot ID 변경, Ready 복귀, firewalld disabled/inactive, T01~15 해당 항목 재수행 |
| T17 | SELinux·AVC | Enforcing, auditd 정상, UTC 시작~재부팅 회귀 종료 구간 4종 이벤트 없음, marker/로그 연속성 검토 |
| T18 | Cockpit | 조치 후 및 재부팅 후 unit별 Id/LoadState/UnitFileState/ActiveState, masked/inactive 또는 미설치; 9090 미청취·조회 명령 성공 |
| T19 | 노드별 복구 | 이전 보호 선복원→HEP 제거→node failsafe 복원, 권한/경로 유효, 승인·비승인·회귀 정상, 다른 노드 무영향 |
| T20 | 전달 트래픽·후속 인수 | P1-SEC 인수 전 외부 NodePort/hostPort/LoadBalancer 미개방; 인수 후 IF-02 preDNAT/forward와 Workload 정책 별도 PASS |

## 실행 코드

### 신규 SSH — ansible01

```bash
# NODE_IP와 NODE_ACCOUNT는 실제 inventory에서 확인한다. 호스트 키 검증을 해제하지 않는다.
ssh -o ControlMaster=no -o ControlPath=none -o BatchMode=yes \
  -o ConnectTimeout=5 "$NODE_ACCOUNT@$NODE_IP" \
  'hostnamectl --static; printf "%s\n" "$SSH_CONNECTION"'
```

Source `.10` 관측과 인증 성공을 함께 기록한다. 임의로 known_hosts를 지우거나 StrictHostKeyChecking을 끄지 않는다.

### TCP·DNS·UDP/IPIP 시험 — 실제 지정 Source에서

```bash
# 승인/비승인 Source 머신 자신의 실제 IP에 bind한다. 주소 spoofing 기능이 아니다.
python3 code/probe.py tcp --source "$TEST_SOURCE_IP" --destination "$NODE_IP" --port 22
python3 code/probe.py tcp --source "$TEST_SOURCE_IP" --destination "$CP_IP" --port 6443
python3 code/probe.py tcp --source "$TEST_SOURCE_IP" --destination "$CP_IP" --port 2379
python3 code/probe.py tcp --source "$TEST_SOURCE_IP" --destination "$CP_IP" --port 2380
python3 code/probe.py tcp --source "$TEST_SOURCE_IP" --destination "$NODE_IP" --port 10250

# 대상 Node 자체의 승인 Resolver 시험. 이름과 기대 레코드는 DNS 정본에서 선택한다.
python3 code/probe.py dns --source "$NODE_IP" --destination 10.1.93.52 --name "$KNOWN_APPROVED_FQDN"
python3 code/probe.py dns --source "$NODE_IP" --destination 10.1.93.52 --dns-tcp --name "$KNOWN_APPROVED_FQDN"
python3 code/probe.py dns --source "$NODE_IP" --destination 10.1.93.53 --name "$KNOWN_APPROVED_FQDN"
python3 code/probe.py dns --source "$NODE_IP" --destination 10.1.93.53 --dns-tcp --name "$KNOWN_APPROVED_FQDN"
python3 code/probe.py dns --source "$NODE_IP" --destination 8.8.8.8 --name example.com
python3 code/probe.py dns --source "$NODE_IP" --destination 8.8.8.8 --dns-tcp --name example.com

# 지정된 비승인 시험 Source에서 한 패킷만 보낸다. 대상 차단 근거와 결합한다.
python3 code/probe.py tcp --source "$TEST_SOURCE_IP" --destination "$NODE_IP" --port 179
python3 code/probe.py udp --source "$TEST_SOURCE_IP" --destination "$NODE_IP" --port 4789
sudo python3 code/probe.py ipip --source "$TEST_SOURCE_IP" --destination "$NODE_IP"
```

`probe.py`는 실패 시 RC2와 오류를 기록한다. 이 실패는 Expected Deny의 후보이며 자동 PASS가 아니다. 잘못된 Source bind, route 불능, 대상 미청취·DNS 장애도 동일하게 실패할 수 있다. UDP4789의 시험 payload는 정상 VXLAN dataplane 검증을 대신하지 않는다. IPIP 전송에는 지정 Linux Source의 raw socket 권한이 필요하며 실제 출발지로만 시험한다.

### 차단 원인 확인 — 대상 노드

```bash
sudo ss -H -ltn 'sport = :22'
sudo iptables-save -c > before-negative.iptables
# 별도 콘솔에서 캡처 시작 후 지정 Source에서 한 번의 시험 수행.
sudo timeout 15 tcpdump -ni "$MEASURED_INTERFACE" -s 128 -w negative.pcap \
  "host $TEST_SOURCE_IP and (tcp port 22 or tcp port 6443 or tcp port 2379 or tcp port 2380 or tcp port 10250 or tcp port 179 or udp port 4789 or ip proto 4)"
sudo iptables-save -c > after-negative.iptables
```

timeout RC124는 캡처 시간 종료로 별도 기록한다. 캡처 실패와 혼동하지 않는다. 지정 시간 동안 unrelated traffic을 최소화하고 해당 interface의 INPUT/OUTPUT dispatch→정확한 HostEndpoint/policy chain→Deny 규칙 카운터 변화를 추적한다. nft backend에 매핑된 iptables면 `nft -a list ruleset`도 함께 연결한다. `calico` 문자열 첫 5줄은 집행 증거가 아니다. 현재 conntrack entry가 영향을 주지 않도록 새로운 5-tuple을 사용하며 전체 conntrack flush는 하지 않는다.

비승인 Source의 egress가 먼저 차단하면 대상 ingress 시험이 아니다. 시험 Source→대상 실제 도착을 확인하고 각각의 차단 위치를 분리한다. Worker의 etcd egress 거부와 CP의 etcd ingress 거부도 별도 TC로 기록한다.

### Pod·Service·API·etcd

```bash
python3 code/regression.py --kubeconfig "$KCFG" --context "$KCTX" \
  --cluster-uid "$CLUSTER_UID" --out "$NODE-$PHASE-regression.json"
```

테스트 Namespace는 기존 같은 이름이 없어야 하며 기존 Workload 정책과의 영향도를 확인한다. Host 전환 전의 정상 baseline이 있어야 한다. Workload 정책으로 probe가 막혀 있으면 기존 업무 Namespace를 완화하지 말고 P1-SEC의 제한 테스트 정책으로 준비한다. etcd 표준 경로가 다르면 static Pod 명세에서 검증한 경로로 코드와 검토본을 함께 수정한다.

MTU, 실제 NTP 동기, Host 이미지 공급, P1-LB Stage B 장애제외/복구는 regression.py의 API/Pod 검사에 포함되지 않는다. 별도 TC 결과를 연결해야 한다.

## 증적 묶기

원문 로그·정책·유효값·결정 문서·TC 결과를 한 시도 디렉터리에 정리한다. 자격증명 포함 여부 확인 후 파일 목록과 SHA-256을 고정한다. 단순 hash는 작성자 인증이나 독립 승인을 대신하지 않는다. Gate 0·기존 승인 증적은 원본 ID/해시로 연결하며 이 패키지에서 재작성하지 않는다.

---

# 기존 ICR 반영용 변경 문안

상태: **리드 전달안 / 승인 기록 미제공**. 신규 티켓 번호를 임의 발행하거나 기존 정본을 변경한 문서가 아니다. 기존 ICR-D-NET-01-01 및 P1-K8S-01에 아래 정확한 범위의 변경을 연결한다.

## 정본 §6.6 전환 순서 교체 제안

CP 3대와 Worker 3대의 Host 보호는 Calico named HostEndpoint 및 GlobalNetworkPolicy로 통일한다. 전환 과정에서는 필요한 Bootstrap 통신과 Source 제한을 검증한 firewalld 보호를 유지한 상태로 Calico 설치·6대 join·Ready 및 회귀 검증을 완료한다. 허용 GNP를 선행 적용한 후 Worker1→Worker2→Worker3→CP1→CP2→CP3 순서로 해당 노드의 유효 failsafe를 정합화하고 HEP를 활성화한다. 각 노드에서 HEP 집행·Positive/Negative/Regression을 확인한 뒤 firewalld를 비활성화하며, 해당 노드의 재부팅 지속성 확인이 완료된 후 다음 노드로 진행한다. 일시 공존이 실패하면 보호 없이 진행하지 않고 중단한다. 복구는 검증된 이전 보호 또는 승인된 대체 보호를 먼저 복원한 뒤 해당 HEP·node별 failsafe를 되돌린다.

## 변경과 인수 범위

| 항목 | 반영 내용 | 책임·증적 |
|---|---|---|
| 전환 시점 | 기존 설치 전 OFF 문구를 위 보호 인계 순서로 교체 | P1-K8S 리드·ICR 승인자, 공존/실패/복구 검증 |
| Host Port Matrix | IF-07/10/11A/12/12A/12B/12C/13/15의 역할·Source·목적지 제한 | 정책 SHA·flow matrix·유효 failsafe |
| System API 실제 경로 | LB 경로 이외에 필요한 DNAT 후 CP/Workload API 경로를 관측 후 승인 | Consumer·Source·Destination·protocol/port·인증·허용 근거 |
| NTP·이미지 공급 | 실제 chrony endpoint, OID-02/03 공급 방식·Owner·검토일·종료조건 확정 | 사전반입 digest 또는 승인 registry endpoint·CA Trust |
| 전달 트래픽 | Bootstrap Host 정책의 applyOnForward=false 경계 유지 | P1-SEC/IF-02/HO-ANS-04C 인수 전 외부 노출 금지 |
| Cockpit·AVC | Gate 0 증적 인수, 차단 후 재부팅 확인, 구간 기록 보존 | unit별 상태·9090·AVC 로그·boot ID |

## 승인 기록에 채울 값

정확한 변경본/Commit 및 정책 해시, 검토자·승인자·일시, 적용 대상 노드, 작업 시도 ID, 사전 보호와 대체 CP API 경로, NTP·이미지 공급 결정, 임시 정책 Owner·검토/종료 시점, P1-SEC·P1-HBR·P1-LB 인수 기준을 기록한다. 이 문안의 작성일을 승인일로 사용하지 않는다.

Harbor 구축 미완료를 이유로 IF-10 정책을 누락하지 않는다. CP/System Harbor 사용은 IF-10의 Worker 범위를 확대하는 별도 결정이다. DNS 서버의 역사적 Done 기록과 현재 실측 Drift를 구분한다. 기존 Baseline의 Design FROZEN과 Runtime PASS 구분을 유지한다.

---

# 리뷰 반영 추적표

기준: 추가 재리뷰 v3.0. 아래의 '반영'은 제출 문서·코드 수정 상태이며 Runtime PASS를 뜻하지 않는다.

| 기존 ID | 반영 내용 | 산출물 | 운영 시 확인 |
|---|---|---|---|
| G2-01 / R2 | SSH Source `.10/32` 고정, Node→Ansible destination22 삭제, 실제 Source 보존 확인 | render.py, RUNBOOK §1/4/7, T02/03 | 신규 SSH·도착 Source·경로 |
| G2-02 / R1 | GNP 선행, Worker1 이후 나머지 HEP도 한 대씩. node별 failsafe 후 HEP, 재부팅까지 완료 후 다음 노드 | cluster-change.py, RUNBOOK §6~8 | 기존 HEP/자동 HEP/Workload selector·tier 검토, 노드별 무영향 |
| G2-02 정본 정합성 | 설치 전 OFF와 새 순서의 충돌을 명시하고 교체 문구·공존·보호·복구 조건 제공 | ICR_ADDENDUM.md, RUNBOOK §3 | 기존 ICR 승인 기록 연결. 승인 없는 정본 변경 아님 |
| G2-03 / R2 | v3.32.1 legacy CRD v1 사용, 실제 API discovery·server dry-run·저장/의미 검증 분리 | render.py, cluster-change.py, tests/check-crd.py | 설치 API·Admission·Calico 집행 |
| G2-04 / R2 | 노드별 GNP로 역할/목적지 제한, explicit Deny, net 없는 failsafe 제거, global default 변경 삭제 | render.py, policy_templates, T01~06/T10/T15 | node 실제 유효 설정·override·conntrack·우선순위 |
| G2-05 / R2 | 승인 DNS UDP/TCP53, Worker Harbor443 포함, NTP 필수 입력, System API/이미지 경로 입력과 인수조건 | render.py, RUNBOOK §1/6/9, T09~13 | OID-02/03·NTP·API 경로 실측 및 승인 |
| G2-05 전달 경계 | host-only 범위, 외부 NodePort 등 인수 전 미개방, 임시 정책 Owner·종료 조건 | ICR_ADDENDUM.md, RUNBOOK §1.2/9 | P1-SEC/IF-02/HO-ANS-04C 인수 |
| G2-06 / R3 | 실제 규칙·카운터·Source 도착을 차단 증거와 연결, Worker3 포함 3 Pod·Service·DNS, API/etcd health | node-evidence.py, probe.py, regression.py, TEST_MATRIX | Runtime T01~20 |
| G2-06 새 복구 결함 | 모든 실패를 §10으로 통일. 보호 선복원·검증 후 HEP/node failsafe 복원. UID/RV·시도/config 바인딩 | cluster-change.py, node-evidence.py, RUNBOOK §10 | 콘솔/정상 CP loopback API, receipt 근거, 실제 복구 |
| G2-07 / R4 | UTC 날짜·시간 별도 argv, 노드별 시작/종료 파일·marker·boot ID 보존, RC1/오류 구분 | audit-window.py, RUNBOOK §4/8, T17 | 로그 연속성·auditd·시계·4종 이벤트 |
| G2-08 / R5 | 선행 조치 증적 있으면 check, 없으면 apply. 차단 후 현재 노드 재부팅→unit/9090 재검증 | cockpit.sh, RUNBOOK §4/8, T18 | Gate 0 적용 여부·재부팅 후 실제 상태 |
| A1 | Gate 0 진행/승인을 재판정하지 않고 실제 적용 증적과 연결 | RUNBOOK §4, README | 기존 증적 ID/해시 |
| A2 | code block 언어·번호·실행 위치·Source 정본 정리 | 전체 문서 | Notion 본문 중복 초안 삭제·교체 |
| 직접 의존 BGP | vxlan backend, IPIP/VXLAN/CIDR/NAT/MTU, BIRD readiness/liveness 수정, digest pinning | prepare-calico.py, RUNBOOK §5 | 설치 전/기설치 migration 구분, 실제 IPPool·경로 |

## 정보 부족으로 임의 확정하지 않은 값

실제 서버 자료가 필요한 값은 코드 입력과 실행 중단 조건으로 반영했다. 값을 비워둔 상태는 보완을 누락한 것이 아니라, 운영 설정을 추정해 확정하지 않기 위한 경계다. 리드는 실측 자료를 채운 후 생성된 정확한 YAML/JSON과 해시를 고정할 수 있다. 서버 접속·원격 변경·Notion/GitHub 기록 변경·타인에게 메시지 전송은 이번 작업에서 수행하지 않았다.

---

# v4.0 로컬 검증 기록

검증일: 2026-09-09. 환경: Python 3.12, PyYAML 6.0.3. 실제 Kubernetes·Felix·firewalld 서버에는 접속하지 않았다. 다음은 이 작성 환경에서 수행한 검증 결과다.

| 검증 | 결과 | 한계 |
|---|---|---|
| Python 구문 / bash 구문 | PASS | 실제 OS 명령 동작을 보장하지 않음 |
| 15개 로컬 테스트 | PASS | 통신/호스트/API 호출은 합성 입력 또는 mock. Runtime 시험 아님 |
| 대표 GNP flow 29개 | PASS | 방향별 규칙 매칭. conntrack·DNAT·다른 정책·서비스 상태까지 결합한 네트워크 시험 아님 |
| node별 failsafe | PASS | 모든 net /32, Worker etcd·API ingress 미허용, DNS/179 예외 미포함을 생성 결과에서 확인 |
| 잘못된 입력 | PASS | 미입력·광범위 NTP net·API 상호 경로 누락·증적 해시 불일치·bundle 변조 시 중단 |
| 복구 보호 경계 | PASS | 보호 receipt 없음/다른 노드이면 API 복구 변경 0; 정상 receipt 이후 HEP→node failsafe 복원 순서 mock 확인 |
| UID/RV 조건부 삭제 | PASS | 요청 body의 preconditions와 stdin 전달 확인. 실제 API 처리는 미검증 |
| AVC / Cockpit 오류 처리 | PASS | 윤일 경계 날짜·시간 argv, ss 조회 실패를 PASS로 만들지 않는 동작 |
| GNP 6 + HEP 6 + Felix 6 | PASS | 공식 v3.32.1 CRD의 served API 및 중첩 field/type/required/enum/pattern/bounds/allOf/anyOf 일부 검사 |
| Calico manifest 준비 | PASS | backend, IPPool env/CIDR/NAT/MTU, BIRD readiness/liveness 제거, 승인 digest 반영 구조 |
| 전체 JSON Schema/CEL | NOT RUN | tests/check-crd.py는 전체 검증기 아님 |
| server dry-run / Admission / Calico 의미 검증 | NOT RUN | 실제 설치 API가 필요 |
| Positive/Negative/Regression/Reboot/Rollback/AVC/Cockpit | NOT RUN | TEST_MATRIX.md에 Runtime 수행·판정 기준 제공 |

## 검토 자료의 무결성

| 자료 | SHA-256 / 기준 |
|---|---|
| 새 첨부 ZIP | `03d12d144b9d75852e53864bc63620da10e2f10c8bbc42809a1b4e48bcb36d8c` |
| 새 첨부 주 본문 | `30011b52f84fc26b90c5f34bd43dbe39d69718710c867420b12a03dbeb10ab3b` |
| 확인한 Calico v3.32.1 공식 manifest | `dce66b28fc93cd59fbc8edf98d54d5f62d91362c1a46c85cbc97ea59e78f15b8` |
| 정본 | v3.1.17, 고정 Commit `2d93395a0e19ea67e5be199d7183a7edbaa6cf4c` |
| 최신 정본 안내 재확인 | CURRENT.md의 Current RELEASED version=v3.1.17 |

정적 테스트의 NTP `192.0.2.123/32`, `test.invalid`, 합성 digest는 테스트 전용이며 운영 파일에 반입하면 안 된다. 릴리스 파일은 별도의 SHA256SUMS로 고정한다. 본인의 로컬 검증을 독립 리뷰나 운영 승인으로 표시하지 않는다.

## 1차 근거

- [FDS 정본 v3.1.17](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)
- [CURRENT.md](https://github.com/321Team/fds-project-baseline/blob/main/CURRENT.md)
- [Calico v3.32.1 manifest](https://github.com/projectcalico/calico/blob/v3.32.1/manifests/calico.yaml)
- [Calico v3.32.1 startup: NAT_OUTGOING 처리](https://github.com/projectcalico/calico/blob/v3.32.1/node/pkg/lifecycle/startup/startup.go)
- [HostEndpoint](https://docs.tigera.io/calico/latest/reference/resources/hostendpoint), [GNP](https://docs.tigera.io/calico/latest/reference/resources/globalnetworkpolicy)
- [Felix 설정 우선순위](https://docs.tigera.io/calico/latest/reference/felix/configuration), [Felix 리소스](https://docs.tigera.io/calico/latest/reference/resources/felixconfig)
- [failsafe](https://docs.tigera.io/calico/latest/reference/host-endpoints/failsafe), [conntrack](https://docs.tigera.io/calico/latest/reference/host-endpoints/conntrack), [forwarded traffic](https://docs.tigera.io/calico/latest/reference/host-endpoints/forwarded)
- [VXLAN/BGP](https://docs.tigera.io/calico/latest/networking/configuring/vxlan-ipip), [Calico 시스템 요구사항](https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements)
- [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html)
- [ausearch 공식 매뉴얼](https://github.com/linux-audit/audit-userspace/blob/master/docs/ausearch.8)
- [etcd endpoint health/status](https://etcd.io/docs/v3.6/tasks/operator/how-to-check-cluster-status/)
- [kubectl delete](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_delete/), [delete raw stdin 구현](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/delete/delete.go)

공식 latest 문서는 조회 시점 Calico 3.32 기준으로 참조했고, 배포 파일 구조는 v3.32.1 고정본으로 대조했다. Kubernetes 도구의 실제 설치 버전과 옵션은 배포 전 확인한다.

---

# 부록 A. 입력 양식 전체

null/빈 목록은 실측 또는 결정이 필요한 값이다. 허위 값으로 채워 실행하지 않는다.

## config/calico-image-lock.json

```json
{
  "quay.io/calico/node:v3.32.1": null,
  "quay.io/calico/cni:v3.32.1": null,
  "quay.io/calico/kube-controllers:v3.32.1": null
}
```

## config/protection-receipt.example.json

```json
{
  "node": null,
  "cluster_uid": null,
  "status": "NOT_REVIEWED",
  "evidence": [
    {
      "path": null,
      "sha256": null
    }
  ],
  "attempt_id": null,
  "config_sha256": null
}
```

## config/runtime-input.json

```json
{
  "bundle": "p1-k8s-01-v4",
  "cluster_uid": null,
  "calico_version": "v3.32.1",
  "dataplane": "iptables",
  "mtu": null,
  "system_images": {
    "mode": null
  },
  "probe_image": null,
  "probe_nodes": [
    "k8s-worker01",
    "k8s-worker02",
    "k8s-worker03"
  ],
  "evidence": {
    "transition": {
      "path": null,
      "sha256": null
    },
    "system_images": {
      "path": null,
      "sha256": null
    },
    "forward_boundary": {
      "path": null,
      "sha256": null
    },
    "policy_overlap": {
      "path": null,
      "sha256": null
    }
  },
  "nodes": {
    "k8s-worker01": {
      "ip": "10.1.93.105",
      "interface": null,
      "ntp_nets": [],
      "registry_nets": [],
      "api_ingress_node_sources": [],
      "api_ingress_workload_selectors": [],
      "api_egress_nets": [],
      "evidence": {
        "felix_effective": {
          "path": null,
          "sha256": null
        },
        "source_route": {
          "path": null,
          "sha256": null
        },
        "firewall_checkpoint": {
          "path": null,
          "sha256": null
        },
        "recovery": {
          "path": null,
          "sha256": null
        },
        "api_paths": {
          "path": null,
          "sha256": null
        },
        "ntp": {
          "path": null,
          "sha256": null
        },
        "dataplane": {
          "path": null,
          "sha256": null
        }
      }
    },
    "k8s-worker02": {
      "ip": "10.1.93.107",
      "interface": null,
      "ntp_nets": [],
      "registry_nets": [],
      "api_ingress_node_sources": [],
      "api_ingress_workload_selectors": [],
      "api_egress_nets": [],
      "evidence": {
        "felix_effective": {
          "path": null,
          "sha256": null
        },
        "source_route": {
          "path": null,
          "sha256": null
        },
        "firewall_checkpoint": {
          "path": null,
          "sha256": null
        },
        "recovery": {
          "path": null,
          "sha256": null
        },
        "api_paths": {
          "path": null,
          "sha256": null
        },
        "ntp": {
          "path": null,
          "sha256": null
        },
        "dataplane": {
          "path": null,
          "sha256": null
        }
      }
    },
    "k8s-worker03": {
      "ip": "10.1.93.108",
      "interface": null,
      "ntp_nets": [],
      "registry_nets": [],
      "api_ingress_node_sources": [],
      "api_ingress_workload_selectors": [],
      "api_egress_nets": [],
      "evidence": {
        "felix_effective": {
          "path": null,
          "sha256": null
        },
        "source_route": {
          "path": null,
          "sha256": null
        },
        "firewall_checkpoint": {
          "path": null,
          "sha256": null
        },
        "recovery": {
          "path": null,
          "sha256": null
        },
        "api_paths": {
          "path": null,
          "sha256": null
        },
        "ntp": {
          "path": null,
          "sha256": null
        },
        "dataplane": {
          "path": null,
          "sha256": null
        }
      }
    },
    "k8s-control01": {
      "ip": "10.1.93.102",
      "interface": null,
      "ntp_nets": [],
      "registry_nets": [],
      "api_ingress_node_sources": [],
      "api_ingress_workload_selectors": [],
      "api_egress_nets": [],
      "evidence": {
        "felix_effective": {
          "path": null,
          "sha256": null
        },
        "source_route": {
          "path": null,
          "sha256": null
        },
        "firewall_checkpoint": {
          "path": null,
          "sha256": null
        },
        "recovery": {
          "path": null,
          "sha256": null
        },
        "api_paths": {
          "path": null,
          "sha256": null
        },
        "ntp": {
          "path": null,
          "sha256": null
        },
        "dataplane": {
          "path": null,
          "sha256": null
        }
      }
    },
    "k8s-control02": {
      "ip": "10.1.93.103",
      "interface": null,
      "ntp_nets": [],
      "registry_nets": [],
      "api_ingress_node_sources": [],
      "api_ingress_workload_selectors": [],
      "api_egress_nets": [],
      "evidence": {
        "felix_effective": {
          "path": null,
          "sha256": null
        },
        "source_route": {
          "path": null,
          "sha256": null
        },
        "firewall_checkpoint": {
          "path": null,
          "sha256": null
        },
        "recovery": {
          "path": null,
          "sha256": null
        },
        "api_paths": {
          "path": null,
          "sha256": null
        },
        "ntp": {
          "path": null,
          "sha256": null
        },
        "dataplane": {
          "path": null,
          "sha256": null
        }
      }
    },
    "k8s-control03": {
      "ip": "10.1.93.104",
      "interface": null,
      "ntp_nets": [],
      "registry_nets": [],
      "api_ingress_node_sources": [],
      "api_ingress_workload_selectors": [],
      "api_egress_nets": [],
      "evidence": {
        "felix_effective": {
          "path": null,
          "sha256": null
        },
        "source_route": {
          "path": null,
          "sha256": null
        },
        "firewall_checkpoint": {
          "path": null,
          "sha256": null
        },
        "recovery": {
          "path": null,
          "sha256": null
        },
        "api_paths": {
          "path": null,
          "sha256": null
        },
        "ntp": {
          "path": null,
          "sha256": null
        },
        "dataplane": {
          "path": null,
          "sha256": null
        }
      }
    }
  },
  "attempt_id": null
}
```

# 부록 B. 노드별 정책 YAML 열람본

다음 `.yaml.in`은 적용 파일이 아니다. NTP·인터페이스와 실제 API/registry 경로를 확정한 뒤 renderer가 적용 파일을 생성한다.

## k8s-control01.felix.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: FelixConfiguration
metadata:
  name: node.k8s-control01
spec:
  failsafeInboundHostPorts:
  - protocol: tcp
    port: 22
    net: 10.250.10.10/32
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.104/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.104/32
  failsafeOutboundHostPorts:
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.104/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.104/32
```

## k8s-control01.gnp.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: fds-k8s-host-k8s-control01
  labels:
    fds.example/package: p1-k8s-01-v4
spec:
  order: 100
  selector: fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-control01"
  applyOnForward: false
  types:
  - Ingress
  - Egress
  ingress:
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.250.10.10/32
    destination:
      ports:
      - 22
  - action: Allow
    protocol: TCP
    source:
      nets: &id001      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 10250
  - action: Allow
    protocol: UDP
    source:
      nets: &id002
      - 10.1.93.105/32
      - 10.1.93.107/32
      - 10.1.93.108/32
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 4789
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.1.93.109/32
    destination:
      ports:
      - 6443
  - action: Allow
    protocol: TCP
    source:
      nets: *id001
    destination:
      ports:
      - 2379
      - 2380
  - action: Deny
  egress:
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 6443
      nets:
      - 10.1.93.109/32
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 4789
      nets: *id002
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 53
      nets: &id003
      - 10.1.93.52/32
      - 10.1.93.53/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 53
      nets: *id003
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 123
      nets:
      - REQUIRED_APPROVED_NTP_IPV4/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 2379
      - 2380
      nets: *id001
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 10250
      nets: *id002
  - action: Deny
```

## k8s-control01.hep.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: HostEndpoint
metadata:
  name: fds-k8s-k8s-control01
  labels:
    fds.example/package: p1-k8s-01-v4
    fds.example/bootstrap: p1-k8s-01-v4
    fds.example/node: k8s-control01
    fds.example/role: control-plane
spec:
  node: k8s-control01
  interfaceName: REQUIRED_MEASURED_INTERFACE
  expectedIPs:
  - 10.1.93.102
```

## k8s-control02.felix.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: FelixConfiguration
metadata:
  name: node.k8s-control02
spec:
  failsafeInboundHostPorts:
  - protocol: tcp
    port: 22
    net: 10.250.10.10/32
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.104/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.104/32
  failsafeOutboundHostPorts:
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.104/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.104/32
```

## k8s-control02.gnp.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: fds-k8s-host-k8s-control02
  labels:
    fds.example/package: p1-k8s-01-v4
spec:
  order: 100
  selector: fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-control02"
  applyOnForward: false
  types:
  - Ingress
  - Egress
  ingress:
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.250.10.10/32
    destination:
      ports:
      - 22
  - action: Allow
    protocol: TCP
    source:
      nets: &id001
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 10250
  - action: Allow
    protocol: UDP
    source:
      nets: &id002
      - 10.1.93.105/32
      - 10.1.93.107/32
      - 10.1.93.108/32
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 4789
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.1.93.109/32
    destination:
      ports:
      - 6443
  - action: Allow
    protocol: TCP
    source:
      nets: *id001
    destination:
      ports:
      - 2379
      - 2380
  - action: Deny
  egress:
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 6443
      nets:
      - 10.1.93.109/32
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 4789
      nets: *id002
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 53
      nets: &id003
      - 10.1.93.52/32
      - 10.1.93.53/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 53
      nets: *id003
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 123
      nets:
      - REQUIRED_APPROVED_NTP_IPV4/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 2379
      - 2380
      nets: *id001
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 10250
      nets: *id002
  - action: Deny
```

## k8s-control02.hep.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: HostEndpoint
metadata:
  name: fds-k8s-k8s-control02
  labels:
    fds.example/package: p1-k8s-01-v4
    fds.example/bootstrap: p1-k8s-01-v4
    fds.example/node: k8s-control02
    fds.example/role: control-plane
spec:
  node: k8s-control02
  interfaceName: REQUIRED_MEASURED_INTERFACE
  expectedIPs:
  - 10.1.93.103
```

## k8s-control03.felix.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: FelixConfiguration
metadata:
  name: node.k8s-control03
spec:
  failsafeInboundHostPorts:
  - protocol: tcp
    port: 22
    net: 10.250.10.10/32
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.104/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.104/32
  failsafeOutboundHostPorts:
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.102/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.103/32
  - protocol: tcp
    port: 2379
    net: 10.1.93.104/32
  - protocol: tcp
    port: 2380
    net: 10.1.93.104/32
```

## k8s-control03.gnp.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: fds-k8s-host-k8s-control03
  labels:
    fds.example/package: p1-k8s-01-v4
spec:
  order: 100
  selector: fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-control03"
  applyOnForward: false
  types:
  - Ingress
  - Egress
  ingress:
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.250.10.10/32
    destination:
      ports:
      - 22
  - action: Allow
    protocol: TCP
    source:
      nets: &id001
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 10250
  - action: Allow
    protocol: UDP
    source:
      nets: &id002
      - 10.1.93.105/32
      - 10.1.93.107/32
      - 10.1.93.108/32
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 4789
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.1.93.109/32
    destination:
      ports:
      - 6443
  - action: Allow
    protocol: TCP
    source:
      nets: *id001
    destination:
      ports:
      - 2379
      - 2380
  - action: Deny
  egress:
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 6443
      nets:
      - 10.1.93.109/32
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 4789
      nets: *id002
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 53
      nets: &id003
      - 10.1.93.52/32
      - 10.1.93.53/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 53
      nets: *id003
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 123
      nets:
      - REQUIRED_APPROVED_NTP_IPV4/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 2379
      - 2380
      nets: *id001
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 10250
      nets: *id002
  - action: Deny
```

## k8s-control03.hep.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: HostEndpoint
metadata:
  name: fds-k8s-k8s-control03
  labels:
    fds.example/package: p1-k8s-01-v4
    fds.example/bootstrap: p1-k8s-01-v4
    fds.example/node: k8s-control03
    fds.example/role: control-plane
spec:
  node: k8s-control03
  interfaceName: REQUIRED_MEASURED_INTERFACE
  expectedIPs:
  - 10.1.93.104
```

## k8s-worker01.felix.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: FelixConfiguration
metadata:
  name: node.k8s-worker01
spec:
  failsafeInboundHostPorts:
  - protocol: tcp
    port: 22
    net: 10.250.10.10/32
  failsafeOutboundHostPorts:
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
```

## k8s-worker01.gnp.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: fds-k8s-host-k8s-worker01
  labels:
    fds.example/package: p1-k8s-01-v4
spec:
  order: 100
  selector: fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-worker01"
  applyOnForward: false
  types:
  - Ingress
  - Egress
  ingress:
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.250.10.10/32
    destination:
      ports:
      - 22
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 10250
  - action: Allow
    protocol: UDP
    source:
      nets: &id001
      - 10.1.93.105/32
      - 10.1.93.107/32
      - 10.1.93.108/32
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 4789
  - action: Deny
  egress:
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 6443
      nets:
      - 10.1.93.109/32
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 4789
      nets: *id001
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 53
      nets: &id002
      - 10.1.93.52/32
      - 10.1.93.53/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 53
      nets: *id002
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 123
      nets:
      - REQUIRED_APPROVED_NTP_IPV4/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 443
      nets:
      - 10.1.93.54/32
  - action: Deny
```

## k8s-worker01.hep.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: HostEndpoint
metadata:
  name: fds-k8s-k8s-worker01
  labels:
    fds.example/package: p1-k8s-01-v4
    fds.example/bootstrap: p1-k8s-01-v4
    fds.example/node: k8s-worker01
    fds.example/role: worker
spec:
  node: k8s-worker01
  interfaceName: REQUIRED_MEASURED_INTERFACE
  expectedIPs:
  - 10.1.93.105
```

## k8s-worker02.felix.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: FelixConfiguration
metadata:
  name: node.k8s-worker02
spec:
  failsafeInboundHostPorts:
  - protocol: tcp
    port: 22
    net: 10.250.10.10/32
  failsafeOutboundHostPorts:
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
```

## k8s-worker02.gnp.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: fds-k8s-host-k8s-worker02
  labels:
    fds.example/package: p1-k8s-01-v4
spec:
  order: 100
  selector: fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-worker02"
  applyOnForward: false
  types:
  - Ingress
  - Egress
  ingress:
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.250.10.10/32
    destination:
      ports:
      - 22
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 10250
  - action: Allow
    protocol: UDP
    source:
      nets: &id001
      - 10.1.93.105/32
      - 10.1.93.107/32
      - 10.1.93.108/32
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 4789
  - action: Deny
  egress:
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 6443
      nets:
      - 10.1.93.109/32
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 4789
      nets: *id001
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 53
      nets: &id002
      - 10.1.93.52/32
      - 10.1.93.53/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 53
      nets: *id002
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 123
      nets:
      - REQUIRED_APPROVED_NTP_IPV4/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 443
      nets:
      - 10.1.93.54/32
  - action: Deny
```

## k8s-worker02.hep.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: HostEndpoint
metadata:
  name: fds-k8s-k8s-worker02
  labels:
    fds.example/package: p1-k8s-01-v4
    fds.example/bootstrap: p1-k8s-01-v4
    fds.example/node: k8s-worker02
    fds.example/role: worker
spec:
  node: k8s-worker02
  interfaceName: REQUIRED_MEASURED_INTERFACE
  expectedIPs:
  - 10.1.93.107
```

## k8s-worker03.felix.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: FelixConfiguration
metadata:
  name: node.k8s-worker03
spec:
  failsafeInboundHostPorts:
  - protocol: tcp
    port: 22
    net: 10.250.10.10/32
  failsafeOutboundHostPorts:
  - protocol: tcp
    port: 6443
    net: 10.1.93.109/32
```

## k8s-worker03.gnp.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: GlobalNetworkPolicy
metadata:
  name: fds-k8s-host-k8s-worker03
  labels:
    fds.example/package: p1-k8s-01-v4
spec:
  order: 100
  selector: fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-worker03"
  applyOnForward: false
  types:
  - Ingress
  - Egress
  ingress:
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.250.10.10/32
    destination:
      ports:
      - 22
  - action: Allow
    protocol: TCP
    source:
      nets:
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 10250
  - action: Allow
    protocol: UDP
    source:
      nets: &id001
      - 10.1.93.105/32
      - 10.1.93.107/32
      - 10.1.93.108/32
      - 10.1.93.102/32
      - 10.1.93.103/32
      - 10.1.93.104/32
    destination:
      ports:
      - 4789
  - action: Deny
  egress:
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 6443
      nets:
      - 10.1.93.109/32
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 4789
      nets: *id001
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 53
      nets: &id002
      - 10.1.93.52/32
      - 10.1.93.53/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 53
      nets: *id002
  - action: Allow
    protocol: UDP
    destination:
      ports:
      - 123
      nets:
      - REQUIRED_APPROVED_NTP_IPV4/32
  - action: Allow
    protocol: TCP
    destination:
      ports:
      - 443
      nets:
      - 10.1.93.54/32
  - action: Deny
```

## k8s-worker03.hep.yaml.in

```yaml
# REVIEW TEMPLATE ONLY. Do not apply. Complete runtime-input.json and run code/render.py.
# Additional observed API/registry paths are generated only from reviewed inputs.
apiVersion: crd.projectcalico.org/v1
kind: HostEndpoint
metadata:
  name: fds-k8s-k8s-worker03
  labels:
    fds.example/package: p1-k8s-01-v4
    fds.example/bootstrap: p1-k8s-01-v4
    fds.example/node: k8s-worker03
    fds.example/role: worker
spec:
  node: k8s-worker03
  interfaceName: REQUIRED_MEASURED_INTERFACE
  expectedIPs:
  - 10.1.93.108
```

# 부록 C. 실행·검증·복구 코드 전체

## code/audit-window.py

```python
#!/usr/bin/env python3
"""Persist a UTC audit interval across reboots. Run as root on each node."""
import argparse
import datetime as dt
import json
import os
import socket
import subprocess
import time
import uuid
from pathlib import Path

ENV = dict(os.environ, LC_ALL='C', TZ='UTC')


def call(args):
    return subprocess.run(args, text=True, capture_output=True, env=ENV, timeout=60)


def required(args):
    r = call(args)
    if r.returncode:
        raise RuntimeError(r.stderr or r.stdout or str(args))
    return r.stdout


def time_args(start, end):
    a = dt.datetime.fromtimestamp(start, dt.timezone.utc)
    b = dt.datetime.fromtimestamp(end, dt.timezone.utc)
    return ['-ts', a.strftime('%m/%d/%Y'), a.strftime('%H:%M:%S'),
            '-te', b.strftime('%m/%d/%Y'), b.strftime('%H:%M:%S')]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['start', 'end', 'query'])
    p.add_argument('--dir', type=Path, required=True)
    a = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required')
    os.umask(0o077)
    required(['systemctl', 'is-active', 'auditd'])
    if required(['getenforce']).strip() != 'Enforcing':
        raise SystemExit('SELinux must be Enforcing')
    status = required(['auditctl', '-s'])
    a.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    statefile = a.dir / 'window.json'
    if a.action == 'start':
        if statefile.exists():
            raise SystemExit('existing window: choose a new directory')
        mark = 'FDS_G2_' + uuid.uuid4().hex
        data = {'node': socket.gethostname(), 'start_epoch': int(time.time()),
                'timezone': 'UTC', 'locale': 'C', 'marker': mark,
                'start_boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        required(['auditctl', '-m', mark + '_START'])
        statefile.write_text(json.dumps(data, indent=2) + '\n')
        (a.dir / 'audit-status-start.txt').write_text(status)
        print('START_RECORDED; keep this directory through reboot')
        return
    data = json.loads(statefile.read_text())
    if data['node'] != socket.gethostname():
        raise SystemExit('wrong node')
    if a.action == 'end':
        if 'end_epoch' in data:
            raise SystemExit('already closed; query the same interval instead')
        required(['auditctl', '-m', data['marker'] + '_END'])
        data['end_epoch'] = int(time.time())
        if data['end_epoch'] < data['start_epoch']:
            raise SystemExit('clock moved backwards: interval cannot be accepted')
        data['end_boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        statefile.write_text(json.dumps(data, indent=2) + '\n')
        (a.dir / 'audit-status-end.txt').write_text(status)
    elif 'end_epoch' not in data:
        raise SystemExit('close interval with end first')
    interval = time_args(data['start_epoch'], data['end_epoch'])
    # Two separate date/time argv values for EACH endpoint; never a combined string.
    cmd = ['ausearch', '--input-logs', '-m', 'AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR'] + interval
    r = call(cmd)
    (a.dir / 'avc.stdout').write_text(r.stdout)
    (a.dir / 'avc.stderr').write_text(r.stderr)    (a.dir / 'avc.command.json').write_text(json.dumps({'argv': cmd, 'rc': r.returncode}, indent=2))
    markers = call(['ausearch', '--input-logs', '-m', 'USER'] + interval)
    (a.dir / 'markers.stdout').write_text(markers.stdout)
    (a.dir / 'markers.stderr').write_text(markers.stderr)
    no_matches = r.returncode == 1 and (r.stdout + r.stderr).strip() == '<no matches>'
    retained = markers.returncode == 0 and all(data['marker'] + x in markers.stdout for x in ('_START', '_END'))
    def counters(text):
        return dict(line.split(None, 1) for line in text.splitlines() if len(line.split(None, 1)) == 2)
    before = counters((a.dir / 'audit-status-start.txt').read_text())
    after = counters((a.dir / 'audit-status-end.txt').read_text())
    healthy = all(x.get('enabled') in ('1', '2') and x.get('lost') == '0' for x in (before, after))
    result = {'status': 'NO_AVC_REVIEW_REQUIRED' if no_matches and retained and healthy else 'HOLD',
              'no_matches': no_matches, 'both_markers_retained': retained,
              'audit_counters_healthy': healthy, 'note': 'Review auditd continuity/rotation and clock events separately.'}
    (a.dir / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if result['status'] == 'HOLD':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
```

## code/cluster-change.py

```python
#!/usr/bin/env python3
"""One-node Calico changes from an authenticated CP; never disables firewalld."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import yaml
from render import ORDER, BUNDLE, LABEL, need

FIELDS = ('failsafeInboundHostPorts', 'failsafeOutboundHostPorts')
GROUP = 'crd.projectcalico.org'


def run(argv, data=None):
    p = subprocess.run(argv, input=data, text=True, capture_output=True, timeout=120)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or str(argv))
    return p.stdout


class Cluster:
    def __init__(self, prefix):
        self.prefix = prefix

    def call(self, *args, data=None):
        return run(self.prefix + list(args), data)

    def get(self, kind, name=None, optional=False):
        args = ['get', kind]
        if name:
            args.append(name)
        if optional:
            args.append('--ignore-not-found=true')
        out = self.call(*args, '-o', 'json')
        return json.loads(out) if out.strip() else None

    def remove_exact(self, plural, current):
        m = current['metadata']
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
            'uid': m['uid'], 'resourceVersion': m['resourceVersion']}}
        url = f'/apis/{GROUP}/v1/{plural}/{m["name"]}'
        self.call('delete', '--raw=' + url, '-f', '-', data=json.dumps(options))


def dump(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')
    os.chmod(path, 0o600)


def verify_bundle(root):
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split('  ', 1)
        rel = Path(name)
        need(not rel.is_absolute() and '..' not in rel.parts, 'invalid manifest path')
        p = root / rel
        need(p.is_file() and not p.is_symlink(), 'missing or symbolic bundle file')
        need(hashlib.sha256(p.read_bytes()).hexdigest() == digest, 'bundle hash mismatch: ' + name)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['preflight', 'stage-gnp', 'set-failsafe', 'create-hep',
                                     'rollback-node', 'cleanup-gnp'])
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--state', type=Path, required=True)
    p.add_argument('--kubeconfig', required=True)
    p.add_argument('--context', required=True)
    p.add_argument('--node', choices=ORDER)
    p.add_argument('--server')
    p.add_argument('--tls-server-name')
    p.add_argument('--protection-receipt', type=Path)
    a = p.parse_args()
    verify_bundle(a.bundle)
    config = json.loads((a.bundle / 'config.lock.json').read_text())
    prefix = ['kubectl', '--kubeconfig=' + a.kubeconfig, '--context=' + a.context,
              '--request-timeout=30s']
    if a.server:
        need(a.server == 'https://127.0.0.1:6443' and a.tls_server_name,
             'recovery server requires loopback and a verified certificate SAN')
        prefix += ['--server=' + a.server, '--tls-server-name=' + a.tls_server_name]
    else:
        need(not a.tls_server_name, 'tls-server-name requires explicit recovery server')
    k = Cluster(prefix)
    need(k.get('namespace', 'kube-system')['metadata']['uid'] == config['cluster_uid'],
         'wrong cluster UID')
    a.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    binding = {'cluster_uid': config['cluster_uid'], 'attempt_id': config['attempt_id'],
               'config_sha256': hashlib.sha256((a.bundle / 'config.lock.json').read_bytes()).hexdigest()}
    binding_file = a.state / 'state-binding.json'
    if binding_file.exists():
        need(json.loads(binding_file.read_text()) == binding, 'state directory belongs to a different attempt/config')
    else:
        need(a.action in ('preflight', 'stage-gnp'), 'initialize state with preflight first')
        dump(binding_file, binding)
    heps = k.get('hostendpoints.' + GROUP)['items']
    if a.action in ('preflight', 'stage-gnp'):
        need(not any(h['spec']['node'] in ORDER for h in heps),
             'existing HEP: stop for migration review; never delete it to satisfy preflight')
        nodes = k.get('nodes')['items']
        need(set(n['metadata']['name'] for n in nodes) == set(ORDER), 'node inventory mismatch')
        need(all(any(x['type'] == 'Ready' and x['status'] == 'True'
                     for x in n['status']['conditions']) for n in nodes), 'all six nodes must be Ready')
        weps = json.loads(k.call('get', 'workloadendpoints.' + GROUP, '-A', '-o', 'json'))['items']
        need(not any(x['metadata'].get('labels', {}).get(LABEL) == BUNDLE for x in weps),
             'GNP selector already selects a workload')
        controllers = k.get('kubecontrollersconfigurations.' + GROUP)['items']
        need(not any(x.get('spec', {}).get('controllers', {}).get('node', {})
                     .get('hostEndpoint', {}).get('autoCreate') == 'Enabled' for x in controllers),
             'automatic HEP creation must be reviewed and disabled for this procedure')
        for kind in ('globalnetworkpolicies', 'hostendpoints', 'felixconfigurations',
                     'ippools', 'kubecontrollersconfigurations'):
            q = a.state / ('initial-' + kind + '.json')
            if not q.exists():
                dump(q, k.get(kind + '.' + GROUP))
        if a.action == 'preflight':
            print('PREFLIGHT_STRUCTURAL_OK; effective Felix, policy overlap and recovery review still required')
            return
        for name in ORDER:
            f = a.bundle / 'gnp' / (name + '.yaml')
            obj = yaml.safe_load(f.read_text())
            need(k.get('globalnetworkpolicies.' + GROUP, obj['metadata']['name'], True) is None,
                 'GNP already exists; inspect partial work, do not overwrite')
            k.call('create', '--dry-run=server', '-f', str(f))
        for name in ORDER:
            f = a.bundle / 'gnp' / (name + '.yaml')
            created = json.loads(k.call('create', '-f', str(f), '-o', 'json'))
            dump(a.state / (name + '-gnp-created.json'), created)
        print('GNP_STAGED; HEP has not been created')
        return
    need(a.node is not None, 'one --node is required')
    node = a.node
    hepname, felixname = 'fds-k8s-' + node, 'node.' + node
    fstate = a.state / (node + '-felix-before.json')
    intended = json.loads((a.bundle / 'felix' / (node + '.json')).read_text())['spec']
    hep = k.get('hostendpoints.' + GROUP, hepname, True)
    if a.action == 'set-failsafe':
        need(not any(h['spec']['node'] == node for h in heps), 'HEP already exists on target')
        need(not fstate.exists(), 'backup exists: inspect prior attempt before retry')
        staged = json.loads((a.state / (node + '-gnp-created.json')).read_text())
        live_gnp = k.get('globalnetworkpolicies.' + GROUP, staged['metadata']['name'])
        need(live_gnp['metadata']['uid'] == staged['metadata']['uid'] and
             live_gnp['spec'] == staged['spec'], 'staged GNP drift')
        old = k.get('felixconfigurations.' + GROUP, felixname, True)
        dump(fstate, old)  # Backup precedes mutation, including original absence.
        if old is None:
            value = {'apiVersion': GROUP + '/v1', 'kind': 'FelixConfiguration',
                     'metadata': {'name': felixname}, 'spec': intended}
            k.call('create', '--dry-run=server', '-f', '-', data=json.dumps(value))
            k.call('create', '-f', '-', data=json.dumps(value))
        else:
            patch = [{'op': 'test', 'path': '/metadata/resourceVersion',
                      'value': old['metadata']['resourceVersion']}]
            if 'spec' not in old:
                patch.append({'op': 'add', 'path': '/spec', 'value': {}})
            patch += [{'op': 'add', 'path': '/spec/' + key, 'value': intended[key]} for key in FIELDS]
            k.call('patch', 'felixconfigurations.' + GROUP, felixname,
                   '--type=json', '--patch', json.dumps(patch), '--dry-run=server')
            k.call('patch', 'felixconfigurations.' + GROUP, felixname,
                   '--type=json', '--patch', json.dumps(patch))
        dump(a.state / (node + '-felix-after.json'), k.get('felixconfigurations.' + GROUP, felixname))
        print('NODE_FAILSAFE_WRITTEN; verify effective settings before create-hep')
    elif a.action == 'create-hep':
        need(not any(h['spec']['node'] == node for h in heps), 'existing HEP: do not replace')
        current = k.get('felixconfigurations.' + GROUP, felixname)
        need(all(current.get('spec', {}).get(x) == intended[x] for x in FIELDS), 'failsafe storage drift')
        need((a.state / (node + '-felix-after.json')).exists(), 'missing failsafe checkpoint')
        file = a.bundle / 'hep' / (node + '.yaml')
        k.call('create', '--dry-run=server', '-f', str(file))
        created = json.loads(k.call('create', '-f', str(file), '-o', 'json'))
        dump(a.state / (node + '-hep-created.json'), created)
        print('HEP_CREATED_AND_ENFORCING; verify before any firewalld change')
    elif a.action == 'rollback-node':
        need(a.protection_receipt is not None, 'first restore and test protective firewall on target')
        r = json.loads(a.protection_receipt.read_text())
        need(r['node'] == node and r['status'] == 'PROTECTION_RESTORED_REVIEWED'
             and r['cluster_uid'] == config['cluster_uid'] and r['attempt_id'] == config['attempt_id']
             and r['config_sha256'] == binding['config_sha256'] and r['evidence'], 'invalid protection receipt')
        # Receipt binds real target tests; it is an operator/reviewer assertion, not remote sensing.
        from render import evidence
        for item in r['evidence']:
            evidence(item, a.protection_receipt.resolve().parent)
        if hep:
            recorded = json.loads((a.state / (node + '-hep-created.json')).read_text())
            need(hep['metadata']['uid'] == recorded['metadata']['uid'] and
                 hep['spec'] == recorded['spec'] and hep['metadata']['labels'] == recorded['metadata']['labels'],
                 'HEP drift: refuse deletion')
            k.remove_exact('hostendpoints', hep)
            need(k.get('hostendpoints.' + GROUP, hepname, True) is None, 'HEP still exists')
        if fstate.exists():
            old = json.loads(fstate.read_text())
            cur = k.get('felixconfigurations.' + GROUP, felixname, True)
            need(cur is not None, 'failsafe resource disappeared: inspect before restoration')
            after = json.loads((a.state / (node + '-felix-after.json')).read_text())
            need(cur['metadata']['uid'] == after['metadata']['uid'], 'Felix UID changed: refuse restoration')
            need(all(cur.get('spec', {}).get(x) == intended[x] for x in FIELDS),
                 'failsafe drift or already restored; inspect before retry')
            if old is None:
                need(set(cur.get('spec', {})) == set(FIELDS), 'new unrelated Felix fields: refuse deletion')
                k.remove_exact('felixconfigurations', cur)
            else:
                replacement = copy.deepcopy(cur)
                for key in FIELDS:
                    if key in old.get('spec', {}):
                        replacement['spec'][key] = old['spec'][key]
                    else:
                        replacement['spec'].pop(key, None)
                k.call('replace', '-f', '-', data=json.dumps(replacement))
        dump(a.state / (node + '-rollback-record.json'), {'node': node, 'status': 'CALICO_RESTORED_TEST_REQUIRED'})
        print('CALICO_RESTORED_TEST_REQUIRED; recheck effective inheritance and traffic')
    elif a.action == 'cleanup-gnp':
        need(not any(h['spec']['node'] == node for h in heps), 'HEP still present on target')
        recorded = json.loads((a.state / (node + '-gnp-created.json')).read_text())
        cur = k.get('globalnetworkpolicies.' + GROUP, recorded['metadata']['name'], True)
        if cur:
            need(cur['metadata']['uid'] == recorded['metadata']['uid'] and cur['spec'] == recorded['spec'],
                 'GNP drift: refuse deletion')
            k.remove_exact('globalnetworkpolicies', cur)
        print('UNSELECTED_GNP_REMOVED')


if __name__ == '__main__':
    main()
```

## code/cockpit.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
action=${1:-check}
[[ "$action" == check || "$action" == apply ]] || { echo 'usage: cockpit.sh check|apply' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'run with sudo' >&2; exit 2; }
failed=0
for unit in cockpit.socket cockpit.service; do
  details=$(systemctl show "$unit" -p Id -p LoadState -p UnitFileState -p ActiveState)
  printf '%s\n' "$details"
  load=$(awk -F= '$1=="LoadState"{print $2}' <<<"$details")
  [[ -n "$load" ]] || { echo "QUERY_ERROR: $unit" >&2; exit 2; }
  if [[ "$load" == not-found ]]; then
    echo "NOT_INSTALLED: $unit"
    continue
  fi
  if [[ "$action" == apply ]]; then
    systemctl disable --now "$unit"
    systemctl mask "$unit"
    details=$(systemctl show "$unit" -p Id -p LoadState -p UnitFileState -p ActiveState)
    printf '%s\n' "$details"
  fi
  state=$(awk -F= '$1=="UnitFileState"{print $2}' <<<"$details")
  active=$(awk -F= '$1=="ActiveState"{print $2}' <<<"$details")
  [[ "$state" == masked && "$active" == inactive ]] || failed=1
done
# Capture the return code before interpreting an empty result.
listeners=$(ss -H -ltn 'sport = :9090') || { echo 'SS_QUERY_ERROR' >&2; exit 2; }
printf '%s\n' "$listeners"
[[ -z "$listeners" ]] || failed=1
(( failed == 0 )) || { echo 'COCKPIT_HOLD'; exit 1; }
echo 'COCKPIT_STATE_OK; post-change reboot persistence is a separate recorded check'
```

## code/node-evidence.py

```python
#!/usr/bin/env python3
"""Host-local evidence and firewalld checkpoint/restore. No HEP deletion."""
import argparse
import hashlib
import json
import os
import socket
import subprocess
from pathlib import Path


def run(args, required=True):
    try:
        p = subprocess.run(args, text=True, capture_output=True, timeout=60)
        r = {'argv': args, 'rc': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        r = {'argv': args, 'rc': 125, 'stdout': '', 'stderr': str(e)}
    if required and r['rc']:
        raise RuntimeError(json.dumps(r))
    return r


def tree_hash(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def firewall_view(permanent=False):
    base = ['firewall-cmd'] + (['--permanent'] if permanent else [])
    result = {}
    for arg in ('--get-default-zone', '--list-all-zones', '--list-all-policies',
                '--get-ipsets'):
        # Only the presentation-only active suffix is ignored; no rules are dropped.
        result[arg] = run(base + [arg])['stdout'].replace(' (active)', '')
    for name in result['--get-ipsets'].split():
        result['ipset:' + name] = run(base + ['--info-ipset=' + name])['stdout']
    for arg in ('--get-all-rules', '--get-all-passthroughs'):
        result['direct:' + arg] = run(base + ['--direct', arg])['stdout']
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['collect', 'checkpoint-firewall', 'restore-firewall'])
    p.add_argument('--dir', type=Path, required=True)
    a = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('root required')
    os.umask(0o077)
    if a.action != 'restore-firewall':
        if a.dir.exists():
            raise SystemExit('use a new evidence/checkpoint directory')
        a.dir.mkdir(parents=True, mode=0o700)
    if a.action == 'collect':
        commands = [
            ['hostnamectl', '--static'], ['date', '--iso-8601=seconds'], ['timedatectl'],
            ['ip', '-j', 'address'], ['ip', '-j', 'route'], ['ip', 'route', 'get', '10.250.10.10'],
            ['ss', '-H', '-lntup'], ['getenforce'], ['auditctl', '-s'],
            ['systemctl', 'show', 'firewalld', '-p', 'LoadState', '-p', 'UnitFileState', '-p', 'ActiveState'],
            ['iptables', '--version'], ['iptables-save', '-c'], ['ip6tables-save', '-c'],
            ['nft', '-a', 'list', 'ruleset'], ['chronyc', '-n', 'sources'], ['chronyc', 'tracking']]
        results = [run(cmd, False) for cmd in commands]
        (a.dir / 'commands.json').write_text(json.dumps(results, indent=2))
        print('COLLECTED_REVIEW_REQUIRED; inspect every rc, chain path and counters')
        return
    snapshot = a.dir / 'checkpoint.json'
    fwroot = Path('/etc/firewalld')
    nmroot = Path('/etc/NetworkManager/system-connections')
    if a.action == 'checkpoint-firewall':
        if run(['systemctl', 'is-enabled', 'firewalld'])['stdout'].strip() != 'enabled':
            raise SystemExit('this transition requires enabled firewalld at start')
        run(['firewall-cmd', '--state'])
        run(['firewall-cmd', '--check-config'])
        runtime, permanent = firewall_view(), firewall_view(True)
        (a.dir / 'runtime.json').write_text(json.dumps(runtime, indent=2))
        (a.dir / 'permanent.json').write_text(json.dumps(permanent, indent=2))
        if runtime != permanent:
            raise SystemExit('runtime/permanent differ: reconcile and test before checkpoint; no automatic overwrite')
        archive = a.dir / 'firewalld-config.tar'
        run(['tar', '--acls', '--xattrs', '--selinux', '-cpf', str(archive), '-C', '/', 'etc/firewalld'])
        record = {'node': socket.gethostname(), 'runtime': runtime, 'firewalld_files': tree_hash(fwroot),
                  'nm_file_hashes': tree_hash(nmroot),
                  'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
        snapshot.write_text(json.dumps(record, indent=2))
        (a.dir / 'checkpoint.sha256').write_text(hashlib.sha256(snapshot.read_bytes()).hexdigest() + '\n')
        print('CHECKPOINT_CREATED; attach actual approved/denied traffic evidence separately')
    else:
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != (a.dir / 'checkpoint.sha256').read_text().strip():
            raise SystemExit('checkpoint hash mismatch')
        saved = json.loads(snapshot.read_text())
        if saved['node'] != socket.gethostname():
            raise SystemExit('wrong node checkpoint')
        if hashlib.sha256((a.dir / 'firewalld-config.tar').read_bytes()).hexdigest() != saved['archive_sha256']:
            raise SystemExit('archive hash mismatch')
        if tree_hash(fwroot) != saved['firewalld_files'] or tree_hash(nmroot) != saved['nm_file_hashes']:
            raise SystemExit('configuration drift: retain HEP; restore reviewed files before this action')
        run(['systemctl', 'enable', '--now', 'firewalld'])
        run(['firewall-cmd', '--check-config'])
        run(['firewall-cmd', '--reload'])
        run(['firewall-cmd', '--state'])
        if firewall_view() != saved['runtime']:
            raise SystemExit('restored runtime differs from checkpoint; DO NOT REMOVE HEP')
        (a.dir / 'restore-result.json').write_text(json.dumps({
            'node': saved['node'], 'status': 'FIREWALL_CONFIG_RESTORED_TRAFFIC_TEST_REQUIRED'}, indent=2))
        print('FIREWALL_CONFIG_RESTORED_TRAFFIC_TEST_REQUIRED; keep HEP until protective rules/tests are reviewed')


if __name__ == '__main__':
    main()
```

## code/prepare-calico.py

```python
#!/usr/bin/env python3
"""Prepare a pinned legacy manifest offline; never patch a running DaemonSet."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml


def prepare(docs, mtu, image_lock):
    cm = [x for x in docs if x and x.get('kind') == 'ConfigMap' and x['metadata']['name'] == 'calico-config']
    ds = [x for x in docs if x and x.get('kind') == 'DaemonSet' and x['metadata']['name'] == 'calico-node']
    if len(cm) != 1 or len(ds) != 1:
        raise ValueError('unexpected manifest structure')
    cm[0]['data']['calico_backend'] = 'vxlan'
    cm[0]['data']['veth_mtu'] = str(mtu)
    node = next(x for x in ds[0]['spec']['template']['spec']['containers'] if x['name'] == 'calico-node')
    values = {'CALICO_IPV4POOL_CIDR': '10.244.0.0/16', 'CALICO_IPV4POOL_IPIP': 'Never',
              'CALICO_IPV4POOL_VXLAN': 'CrossSubnet', 'CALICO_IPV4POOL_NAT_OUTGOING': 'true'}
    for key, value in values.items():
        node['env'] = [e for e in node['env'] if e['name'] != key] + [{'name': key, 'value': value}]
    for probe, removed in (('readinessProbe', '-bird-ready'), ('livenessProbe', '-bird-live')):
        argv = node[probe]['exec']['command']
        if removed not in argv:
            raise ValueError('unexpected probe structure: ' + probe)
        node[probe]['exec']['command'] = [x for x in argv if x != removed]
    for item in docs:
        if not item:
            continue
        spec = item.get('spec', {}).get('template', {}).get('spec', {})
        for container in spec.get('containers', []) + spec.get('initContainers', []):
            original = container['image']
            image = image_lock.get(original, '')
            if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', image or ''):
                raise ValueError('approved image digest missing for ' + original)
            container['image'] = image
    return docs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--image-lock', type=Path, required=True)
    p.add_argument('--mtu', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if hashlib.sha256(a.source.read_bytes()).hexdigest() != a.sha256:
        raise SystemExit('upstream file hash mismatch')
    if not 576 <= a.mtu <= 1450 or a.out.exists():
        raise SystemExit('invalid/unreviewed MTU or existing output')
    docs = prepare(list(yaml.safe_load_all(a.source.read_text())), a.mtu,
                   json.loads(a.image_lock.read_text()))
    a.out.write_text(yaml.safe_dump_all(docs, sort_keys=False))
    print('PREPARED_FOR_INSTALL_REVIEW; existing pools require separate migration, not env edits')


if __name__ == '__main__':
    main()
```

## code/probe.py

```python
#!/usr/bin/env python3
"""Record a bound-source TCP or UDP/TCP DNS probe. Failure is NOT policy proof."""
import argparse
import ipaddress
import json
import secrets
import socket
import struct
import time


def read_exact(sock, count):
    out = b''
    while len(out) < count:
        b = sock.recv(count - len(out))
        if not b:
            raise EOFError('short DNS TCP response')
        out += b
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['tcp', 'dns', 'udp', 'ipip'])
    p.add_argument('--source', required=True)
    p.add_argument('--destination', required=True)
    p.add_argument('--port', type=int, default=22)
    p.add_argument('--name', default='kubernetes.default.svc.cluster.local')
    p.add_argument('--dns-tcp', action='store_true')
    a = p.parse_args()
    for value in (a.source, a.destination):
        assert ipaddress.ip_address(value).version == 4
    result = {'time_epoch': int(time.time()), 'source': a.source, 'destination': a.destination,
              'mode': a.mode, 'protocol': 'TCP' if a.mode == 'tcp' or a.dns_tcp else 'UDP',
              'port': 53 if a.mode == 'dns' else a.port}
    if a.mode == 'ipip':
        result['protocol'] = 'IP4'
        result.pop('port')
        try:
            # One valid inner IPv4/UDP packet. Outer protocol 4 is supplied by the kernel.
            h = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 28, 1, 0, 32, 17, 0,
                            socket.inet_aton(a.source), socket.inet_aton(a.destination))
            total = sum(struct.unpack('!10H', h))
            total = (total & 65535) + (total >> 16)
            total = (total & 65535) + (total >> 16)
            h = h[:10] + struct.pack('!H', (~total) & 65535) + h[12:]
            with socket.socket(socket.AF_INET, socket.SOCK_RAW, 4) as s:
                s.bind((a.source, 0))
                s.sendto(h + struct.pack('!4H', 49001, 9, 8, 0), (a.destination, 0))
            result['result'] = 'ONE_PACKET_SENT_NOT_A_POLICY_VERDICT'
        except Exception as exc:
            result.update(result='SEND_FAILED', error=str(exc))
        print(json.dumps(result))
        raise SystemExit(0 if result['result'].startswith('ONE_PACKET') else 2)
    typ = socket.SOCK_STREAM if result['protocol'] == 'TCP' else socket.SOCK_DGRAM
    try:
        with socket.socket(socket.AF_INET, typ) as s:
            s.settimeout(5)
            s.bind((a.source, 0))
            s.connect((a.destination, result['port']))
            result['local_socket'] = list(s.getsockname())
            if a.mode == 'udp':
                s.send(b'FDS_GATE2_NEGATIVE_PROBE')
                result['result'] = 'ONE_PACKET_SENT_NOT_A_POLICY_VERDICT'
            elif a.mode == 'tcp':
                result['result'] = 'TCP_CONNECTED'
            else:
                ident = secrets.randbelow(65536)
                labels = a.name.rstrip('.').encode('idna').split(b'.')
                assert all(0 < len(x) <= 63 for x in labels)
                q = struct.pack('!6H', ident, 0x0100, 1, 0, 0, 0)
                q += b''.join(bytes([len(x)]) + x for x in labels) + b'\0' + struct.pack('!HH', 1, 1)
                if a.dns_tcp:
                    s.sendall(struct.pack('!H', len(q)) + q)
                    answer = read_exact(s, struct.unpack('!H', read_exact(s, 2))[0])
                else:
                    s.send(q)
                    answer = s.recv(4096)
                rxid, flags, questions, answers, _, _ = struct.unpack('!6H', answer[:12])
                assert rxid == ident and flags & 0x8000, 'unexpected DNS response'
                result.update(rcode=flags & 15, answer_count=answers, truncated=bool(flags & 0x0200))
                result['result'] = 'DNS_RESPONSE'
    except Exception as exc:
        result.update(result='CONNECTION_OR_QUERY_FAILED', error=type(exc).__name__ + ': ' + str(exc))
    print(json.dumps(result))
    # Always require explicit interpretation of the result and packet/counter evidence.
    if result['result'] == 'CONNECTION_OR_QUERY_FAILED':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
```

## code/regression.py

```python
#!/usr/bin/env python3
"""API, etcd and all-Worker Pod/Service/DNS checks from an authenticated CP."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from render import ORDER, need


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kubeconfig', required=True)
    p.add_argument('--context', required=True)
    p.add_argument('--cluster-uid', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit('use a new output file for each phase')
    os.umask(0o077)
    log = []
    base = ['kubectl', '--kubeconfig=' + a.kubeconfig, '--context=' + a.context, '--request-timeout=30s']
    def k(*args):
        r = subprocess.run(base + list(args), text=True, capture_output=True, timeout=120)
        log.append({'argv': base + list(args), 'rc': r.returncode, 'stdout': r.stdout, 'stderr': r.stderr})
        a.out.write_text(json.dumps(log, indent=2))
        if r.returncode:
            raise RuntimeError('command failed; see ' + str(a.out))
        return r.stdout
    uid = json.loads(k('get', 'ns', 'kube-system', '-o', 'json'))['metadata']['uid']
    if uid != a.cluster_uid:
        raise SystemExit('wrong cluster')
    nodes = json.loads(k('get', 'nodes', '-o', 'json'))['items']
    need({n['metadata']['name'] for n in nodes} == set(ORDER), 'six-node inventory mismatch')
    need(all(any(x['type'] == 'Ready' and x['status'] == 'True' for x in n['status']['conditions'])
               for n in nodes), 'a node is not Ready')
    k('get', '--raw=/readyz?verbose')
    syspods = json.loads(k('get', 'pods', '-n', 'kube-system', '-o', 'json'))['items']
    for pod in syspods:
        if pod['status'].get('phase') == 'Succeeded':
            continue
        need(any(x['type'] == 'Ready' and x['status'] == 'True'
                   for x in pod['status'].get('conditions', [])), pod['metadata']['name'] + ' is not Ready')
    # kubeadm stacked-etcd standard paths; preflight must verify them against static Pod manifests.
    for node in ORDER[3:]:
        for command in ('health', 'status'):
            output = k('-n', 'kube-system', 'exec', 'etcd-' + node, '-c', 'etcd', '--', 'etcdctl',
              '--endpoints=https://127.0.0.1:2379', '--cacert=/etc/kubernetes/pki/etcd/ca.crt',
              '--cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt',
              '--key=/etc/kubernetes/pki/etcd/healthcheck-client.key',
              'endpoint', command, '--cluster', '--write-out=json')
            endpoints = json.loads(output)
            need(len(endpoints) == 3, 'expected three etcd endpoints')
            if command == 'health':
                need(all(x.get('health') is True for x in endpoints), 'unhealthy etcd endpoint')
            else:
                need(len({x['Status']['header']['cluster_id'] for x in endpoints}) == 1,
                     'etcd cluster ID mismatch')
                need(len({x['Status']['header']['member_id'] for x in endpoints}) == 3,
                     'etcd member IDs not unique')
                need(all(not x['Status'].get('errors') and x['Status'].get('leader')
                         for x in endpoints), 'etcd errors or missing leader')
    pods = {}
    for name in ('probe-a', 'probe-b', 'probe-c'):
        k('-n', 'fds-gate2-probe', 'wait', '--for=condition=Ready', 'pod/' + name, '--timeout=60s')
        pods[name] = json.loads(k('-n', 'fds-gate2-probe', 'get', 'pod', name, '-o', 'json'))
    need({x['spec']['nodeName'] for x in pods.values()} == set(ORDER[:3]), 'one Pod on each Worker required')
    service = json.loads(k('-n', 'fds-gate2-probe', 'get', 'svc', 'probe-b', '-o', 'json'))
    def http(source, target):
        code = 'import urllib.request; r=urllib.request.urlopen(' + repr('http://' + target + ':8080/') + ',timeout=5); print(r.status); assert r.status==200'
        k('-n', 'fds-gate2-probe', 'exec', source, '--', 'python3', '-c', code)
    for source in pods:
        for target in pods:
            if source != target:
                http(source, pods[target]['status']['podIP'])
        http(source, service['spec']['clusterIP'])
        http(source, 'probe-b.fds-gate2-probe.svc.cluster.local')
    for name in pods:
        k('-n', 'fds-gate2-probe', 'exec', name, '--', 'python3', '-c',
          'import socket; print(socket.getaddrinfo("kubernetes.default.svc.cluster.local",443))')
    print('REGRESSION_COMMANDS_OK; inspect etcd fields, MTU, Host DNS/NTP/image and negative tests separately')


if __name__ == '__main__':
    main()
```

## code/render.py

```python
#!/usr/bin/env python3
"""Offline policy renderer. Never contacts or changes a cluster."""
import argparse
import hashlib
import ipaddress
import json
import re
from pathlib import Path

import yaml

API = 'crd.projectcalico.org/v1'
LABEL = 'fds.example/bootstrap'
BUNDLE = 'p1-k8s-01-v4'
ORDER = ['k8s-worker01', 'k8s-worker02', 'k8s-worker03',
         'k8s-control01', 'k8s-control02', 'k8s-control03']
IPS = dict(zip(ORDER, ['10.1.93.105', '10.1.93.107', '10.1.93.108',
                       '10.1.93.102', '10.1.93.103', '10.1.93.104']))
NODES = [x + '/32' for x in IPS.values()]
CP = [IPS[x] + '/32' for x in ORDER if 'control' in x]
DNS = ['10.1.93.52/32', '10.1.93.53/32']
SSH = '10.250.10.10/32'
LB = '10.1.93.109/32'


def need(ok, message):
    if not ok:
        raise ValueError(message)


def hostnet(value):
    net = ipaddress.ip_network(value, strict=True)
    need(net.version == 4 and net.prefixlen == 32, 'IPv4 /32 required: ' + value)
    need(not net.network_address.is_loopback and not net.network_address.is_multicast
         and not net.network_address.is_unspecified, 'invalid remote host: ' + value)
    return value


def evidence(value, base):
    need(isinstance(value, dict) and set(value) == {'path', 'sha256'},
         'evidence requires path and sha256')
    p = Path(value['path'])
    p = p if p.is_absolute() else base / p
    need(p.is_file(), 'missing measured/decision evidence: ' + str(p))
    need(hashlib.sha256(p.read_bytes()).hexdigest() == value['sha256'],
         'evidence hash mismatch: ' + str(p))


def validate(c, base, check_evidence=True):
    need(c['bundle'] == BUNDLE, 'wrong bundle')
    need(re.fullmatch(r'[A-Za-z0-9_-]{4,64}', c.get('attempt_id') or '') is not None,
         'unique attempt_id required')
    need(re.fullmatch(r'[0-9a-f-]{36}', c['cluster_uid'] or '') is not None,
         'measure kube-system namespace UID first')
    need(set(c['nodes']) == set(ORDER), 'exact six nodes required')
    need(c['dataplane'] == 'iptables', 'this code targets verified iptables dataplane only')
    need(c['calico_version'] == 'v3.32.1', 'review changed Calico version first')
    need(c['system_images']['mode'] in ('preloaded', 'approved_registry'), 'select image supply mode')
    need(re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', c['probe_image'] or '') is not None,
         'probe image must be an approved immutable digest with Python 3')
    need(len(c['probe_nodes']) == 3 and set(c['probe_nodes']) == set(ORDER[:3]),
         'one probe on each of the three Workers is required')
    need(isinstance(c['mtu'], int) and 576 <= c['mtu'] <= 1450,
         'record measured MTU; 1450 is a candidate, not a measurement')
    if check_evidence:
        for key in ('transition', 'system_images', 'forward_boundary', 'policy_overlap'):
            evidence(c['evidence'][key], base)
    for name in ORDER:
        n = c['nodes'][name]
        need(n['ip'] == IPS[name], 'inventory mismatch: ' + name)
        need(re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', n['interface'] or '') is not None
             and n['interface'] != 'lo', 'measure named physical interface: ' + name)
        need(n['ntp_nets'], 'approved measured NTP destinations required: ' + name)
        for net in n['ntp_nets'] + n['registry_nets']:
            hostnet(net)
        if c['system_images']['mode'] == 'approved_registry':
            need(n['registry_nets'], 'registry endpoints missing: ' + name)
        else:
            need(not n['registry_nets'], 'preloaded mode must not add registry egress')
        for field in ('api_ingress_node_sources', 'api_egress_nets'):
            for net in n[field]:
                need(hostnet(net) in NODES, 'extra API host must be a known node')
        need(not n['api_ingress_node_sources'] or 'control' in name, 'Worker API ingress forbidden')
        need(all(x in CP for x in n['api_egress_nets']), 'API destination must be a CP')
        # Workload-to-API source must be a reviewed kube-system endpoint selector.
        for selector in n['api_ingress_workload_selectors']:
            need('control' in name and isinstance(selector, str) and selector.strip()
                 and selector.strip() != 'all()', 'unrestricted workload API selector forbidden')
        if check_evidence:
            for key in ('felix_effective', 'source_route', 'firewall_checkpoint',
                        'recovery', 'api_paths', 'ntp', 'dataplane'):
                evidence(n['evidence'][key], base)
    # Host API flows must be allowed in both directions, not only on the client.
    for source in ORDER:
        for dest in c['nodes'][source]['api_egress_nets']:
            target = next(k for k, v in IPS.items() if v + '/32' == dest)
            need(IPS[source] + '/32' in c['nodes'][target]['api_ingress_node_sources'],
                 'missing reciprocal CP ingress for ' + source + ' -> ' + target)


def rule(proto, nets, ports, direction):
    peer = 'source' if direction == 'ingress' else 'destination'
    r = {'action': 'Allow', 'protocol': proto, peer: {'nets': nets},
         'destination': {'ports': ports}}
    if peer == 'destination':
        r['destination']['nets'] = nets
    return r


def obj(kind, name, spec, labels=None):
    m = {'name': name, 'labels': {'fds.example/package': BUNDLE}}
    if labels:
        m['labels'].update(labels)
    return {'apiVersion': API, 'kind': kind, 'metadata': m, 'spec': spec}


def policies(c, name):
    n, cp = c['nodes'][name], 'control' in name
    ing = [rule('TCP', [SSH], [22], 'ingress'),
           rule('TCP', CP, [10250], 'ingress'),
           rule('UDP', NODES, [4789], 'ingress')]
    out = [rule('TCP', [LB], [6443], 'egress'),
           rule('UDP', NODES, [4789], 'egress'),
           rule('UDP', DNS, [53], 'egress'),
           rule('TCP', DNS, [53], 'egress'),
           rule('UDP', n['ntp_nets'], [123], 'egress')]
    if cp:
        ing += [rule('TCP', [LB] + n['api_ingress_node_sources'], [6443], 'ingress'),
                rule('TCP', CP, [2379, 2380], 'ingress')]
        out += [rule('TCP', CP, [2379, 2380], 'egress'),
                rule('TCP', NODES, [10250], 'egress')]
        for selector in n['api_ingress_workload_selectors']:
            ing.append({'action': 'Allow', 'protocol': 'TCP', 'source': {
                'namespaceSelector': 'projectcalico.org/name == "kube-system"',
                'selector': selector}, 'destination': {'ports': [6443]}})
    else:
        out.append(rule('TCP', ['10.1.93.54/32'], [443], 'egress'))
    if n['api_egress_nets']:
        out.append(rule('TCP', n['api_egress_nets'], [6443], 'egress'))
    if n['registry_nets']:
        out.append(rule('TCP', n['registry_nets'], [443], 'egress'))
    # Explicit termination; no dependence on fall-through to another GNP.
    ing.append({'action': 'Deny'})
    out.append({'action': 'Deny'})
    gnp = obj('GlobalNetworkPolicy', 'fds-k8s-host-' + name, {
        'order': 100, 'selector': f'{LABEL} == "{BUNDLE}" && fds.example/node == "{name}"',
        'applyOnForward': False, 'types': ['Ingress', 'Egress'], 'ingress': ing, 'egress': out})
    hep = obj('HostEndpoint', 'fds-k8s-' + name, {
        'node': name, 'interfaceName': n['interface'], 'expectedIPs': [n['ip']]},
        {LABEL: BUNDLE, 'fds.example/node': name,
         'fds.example/role': 'control-plane' if cp else 'worker'})
    inbound = [{'protocol': 'tcp', 'port': 22, 'net': SSH}]
    outbound = [{'protocol': 'tcp', 'port': 6443, 'net': LB}]
    for net in n['api_egress_nets']:
        outbound.append({'protocol': 'tcp', 'port': 6443, 'net': net})
    if cp:
        inbound += [{'protocol': 'tcp', 'port': 6443, 'net': x}
                    for x in [LB] + n['api_ingress_node_sources']]
        for net in CP:
            for port in (2379, 2380):
                inbound.append({'protocol': 'tcp', 'port': port, 'net': net})
                outbound.append({'protocol': 'tcp', 'port': port, 'net': net})
    felix = {'spec': {'failsafeInboundHostPorts': inbound, 'failsafeOutboundHostPorts': outbound}}
    return gnp, hep, felix


def probes(c):
    ns = 'fds-gate2-probe'
    docs = [{'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns,
             'labels': {'pod-security.kubernetes.io/enforce': 'restricted'}}}]
    # The selected, scanned digest must include Python 3; no installation in pods.
    for index, node in enumerate(c['probe_nodes']):
        name = 'probe-' + 'abc'[index]
        docs.append({'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {
            'name': name, 'namespace': ns, 'labels': {'app': name}}, 'spec': {
            'nodeSelector': {'kubernetes.io/hostname': node},
            'automountServiceAccountToken': False,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000,
                                'runAsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'http', 'image': c['probe_image'], 'imagePullPolicy': 'Never',
                            'command': ['python3', '-m', 'http.server', '8080', '--bind', '0.0.0.0'],
                            'workingDir': '/tmp', 'ports': [{'containerPort': 8080}],
                            'securityContext': {'allowPrivilegeEscalation': False,
                             'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                            'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                          'limits': {'cpu': '100m', 'memory': '128Mi'}}}]}})
    docs.append({'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'probe-b', 'namespace': ns},
                 'spec': {'type': 'ClusterIP', 'selector': {'app': 'probe-b'},
                          'ports': [{'port': 8080, 'targetPort': 8080}]}})
    return docs


def write_yaml(path, docs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    c = json.loads(a.config.read_text())
    validate(c, a.config.resolve().parent)
    need(not a.out.exists(), 'output directory exists; use a new version directory')
    a.out.mkdir(parents=True)
    for name in ORDER:
        g, h, f = policies(c, name)
        write_yaml(a.out / 'gnp' / (name + '.yaml'), [g])
        write_yaml(a.out / 'hep' / (name + '.yaml'), [h])
        d = a.out / 'felix'
        d.mkdir(exist_ok=True)
        (d / (name + '.json')).write_text(json.dumps(f, indent=2) + '\n')
    write_yaml(a.out / 'probes.yaml', probes(c))
    (a.out / 'config.lock.json').write_text(json.dumps(c, indent=2) + '\n')
    rows = [hashlib.sha256(f.read_bytes()).hexdigest() + '  ' + str(f.relative_to(a.out))
            for f in sorted(a.out.rglob('*')) if f.is_file()]
    (a.out / 'SHA256SUMS').write_text('\n'.join(rows) + '\n')
    print('GENERATED_FOR_SERVER_VALIDATION; no live changes made')


if __name__ == '__main__':
    main()
```

# 부록 D. 검증 코드와 로그

## tests/check-crd.py

```python
#!/usr/bin/env python3
"""Check generated objects against selected CRD schema keywords; not full JSONSchema/CEL."""
import argparse
import copy
import importlib.util
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
sys.path.insert(0, str(ROOT / 'tests'))
import render
from validate import fixture, calico


def check(v, s, path='$'):
    if v is None and s.get('nullable'):
        return
    t = s.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int,
             'number': (int, float), 'boolean': bool}
    if t in types:
        assert isinstance(v, types[t]), path + ' type'
        if t in ('integer', 'number'):
            assert not isinstance(v, bool), path + ' numeric boolean'
    if 'enum' in s:
        assert v in s['enum'], path + ' enum'
    if 'pattern' in s and isinstance(v, str):
        assert re.search(s['pattern'], v) is not None, path + ' pattern'
    for key, compare in [('minimum', lambda x, y: x >= y), ('maximum', lambda x, y: x <= y)]:
        if key in s and isinstance(v, (int, float)) and not isinstance(v, bool):
            assert compare(v, s[key]), path + ' ' + key
    if isinstance(v, dict):
        for key in s.get('required', []):
            assert key in v, path + ' missing ' + key
        props = s.get('properties', {})
        for key, val in v.items():
            if key in props:
                check(val, props[key], path + '.' + key)
            elif isinstance(s.get('additionalProperties'), dict):
                check(val, s['additionalProperties'], path + '.' + key)
            elif props:
                assert key in props, path + ' unknown field ' + key
    elif isinstance(v, list):
        for i, val in enumerate(v):
            check(val, s.get('items', {}), path + '[' + str(i) + ']')
    for part in s.get('allOf', []):
        check(v, part, path)
    if 'anyOf' in s:
        passed = False
        for part in s['anyOf']:
            try:
                check(v, part, path)
                passed = True
            except AssertionError:
                pass
        assert passed, path + ' anyOf'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    a = p.parse_args()
    docs = list(yaml.safe_load_all(a.source.read_text()))
    schemas = {}
    for o in docs:
        if o and o.get('kind') == 'CustomResourceDefinition':
            for version in o['spec']['versions']:
                if version['served']:
                    schemas[(o['spec']['group'] + '/' + version['name'], o['spec']['names']['kind'])] = version['schema']['openAPIV3Schema']
    c = fixture()
    # Exercise the optional kube-system selector as well as the fixed host rules.
    c['nodes']['k8s-control01']['api_ingress_workload_selectors'] = ['k8s-app == "kube-dns"']
    count = 0
    for name in render.ORDER:
        g, h, f = render.policies(c, name)
        f = {'apiVersion': render.API, 'kind': 'FelixConfiguration',
             'metadata': {'name': 'node.' + name}, 'spec': f['spec']}
        for o in (g, h, f):
            check(o, schemas[(o['apiVersion'], o['kind'])])
            count += 1
    images = {}
    for o in docs:
        spec = (o or {}).get('spec', {}).get('template', {}).get('spec', {})
        for x in spec.get('containers', []) + spec.get('initContainers', []):
            images[x['image']] = 'test.invalid/image@sha256:' + 'b' * 64
    prepared = calico.prepare(copy.deepcopy(docs), 1450, images)
    cm = next(o for o in prepared if o and o.get('kind') == 'ConfigMap' and o['metadata']['name'] == 'calico-config')
    ds = next(o for o in prepared if o and o.get('kind') == 'DaemonSet' and o['metadata']['name'] == 'calico-node')
    node = next(c for c in ds['spec']['template']['spec']['containers'] if c['name'] == 'calico-node')
    assert cm['data']['calico_backend'] == 'vxlan' and cm['data']['veth_mtu'] == '1450'
    env = {e['name']: e.get('value') for e in node['env']}
    assert env['CALICO_IPV4POOL_CIDR'] == '10.244.0.0/16'
    assert env['CALICO_IPV4POOL_IPIP'] == 'Never' and env['CALICO_IPV4POOL_VXLAN'] == 'CrossSubnet'
    assert env['CALICO_IPV4POOL_NAT_OUTGOING'] == 'true'
    assert node['readinessProbe']['exec']['command'] == ['/bin/calico-node', '-felix-ready']
    assert node['livenessProbe']['exec']['command'] == ['/bin/calico-node', '-felix-live']
    assert '@sha256:' in node['image']
    print('PASS:', count, 'GNP/HEP/Felix objects: served API, recursive fields/types/required/enum/pattern/bounds/allOf/anyOf subset')
    print('PASS: Calico offline preparation: VXLAN backend, IPPool env/CIDR/NAT/MTU, both BIRD probes, image digests')
    print('NOT RUN: full JSONSchema, CEL, server admission, Calico semantic validation, runtime enforcement')

if __name__ == '__main__':
    main()

```

## tests/validate.py

```python
#!/usr/bin/env python3
"""Offline tests only. Synthetic inputs never authorize runtime deployment."""
import copy
import hashlib
import importlib.util
import ipaddress
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'code'))
import render


def load(name, file):
    s = importlib.util.spec_from_file_location(name, ROOT / 'code' / file)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


audit = load('audit_window', 'audit-window.py')
change = load('cluster_change', 'cluster-change.py')
calico = load('prepare_calico', 'prepare-calico.py')
host = load('node_evidence', 'node-evidence.py')


def fixture():
    c = json.loads((ROOT / 'config/runtime-input.json').read_text())
    c.update(attempt_id='OFFLINE_TEST_ONLY', cluster_uid='00000000-0000-0000-0000-000000000001',
             mtu=1450, probe_image='test.invalid/python@sha256:' + 'a' * 64)
    c['system_images']['mode'] = 'preloaded'
    for n in c['nodes'].values():
        n.update(interface='ens160', ntp_nets=['192.0.2.123/32'])
    return c


def allows(policy, direction, proto, source, destination, port):
    for r in policy['spec'][direction]:
        if 'protocol' in r and r['protocol'] != proto:
            continue
        matched = True
        for key, value in (('source', source), ('destination', destination)):
            q = r.get(key, {})
            if 'selector' in q or 'namespaceSelector' in q:
                matched = False  # Static IP tests deliberately do not simulate endpoint labels.
            if 'nets' in q and not any(ipaddress.ip_address(value) in ipaddress.ip_network(n) for n in q['nets']):
                matched = False
        if 'ports' in r.get('destination', {}) and port not in r['destination']['ports']:
            matched = False
        if matched:
            return r['action'] == 'Allow'
    return False


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.c = fixture()
        self.w = render.policies(self.c, 'k8s-worker01')[0]
        self.cp = render.policies(self.c, 'k8s-control01')[0]

    def test_fixed_flow_matrix(self):
        cases = [
            (self.w, 'ingress', 'TCP', '10.250.10.10', '10.1.93.105', 22, True),
            (self.w, 'ingress', 'TCP', '10.1.93.23', '10.1.93.105', 22, False),
            (self.cp, 'ingress', 'TCP', '10.1.93.109', '10.1.93.102', 6443, True),
            (self.w, 'ingress', 'TCP', '10.1.93.109', '10.1.93.105', 6443, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 6443, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 2379, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.102', 2379, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.102', 2380, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.107', 10250, False),
            (self.cp, 'egress', 'TCP', '10.1.93.102', '10.1.93.105', 10250, True),
            (self.cp, 'ingress', 'TCP', '10.1.93.103', '10.1.93.102', 2379, True),
            (self.cp, 'ingress', 'TCP', '10.1.93.103', '10.1.93.102', 2380, True),
            (self.cp, 'ingress', 'TCP', '10.1.93.105', '10.1.93.102', 2379, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.250.10.10', 22, False),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.109', 6443, True),
            (self.w, 'egress', 'TCP', '10.1.93.105', '10.1.93.54', 443, True),
            (self.cp, 'egress', 'TCP', '10.1.93.102', '10.1.93.54', 443, False),
            (self.w, 'ingress', 'UDP', '10.1.93.108', '10.1.93.105', 4789, True),
            (self.w, 'ingress', 'UDP', '10.1.93.55', '10.1.93.105', 4789, False),
            (self.w, 'ingress', 'TCP', '10.1.93.102', '10.1.93.105', 179, False),
            (self.w, 'ingress', 4, '10.1.93.102', '10.1.93.105', None, False),
            (self.w, 'egress', 'UDP', '10.1.93.105', '192.0.2.123', 123, True),
            (self.w, 'egress', 'UDP', '10.1.93.105', '192.0.2.124', 123, False)]
        for proto in ('UDP', 'TCP'):
            for dns in ('10.1.93.52', '10.1.93.53'):
                cases.append((self.w, 'egress', proto, '10.1.93.105', dns, 53, True))
            cases.append((self.w, 'egress', proto, '10.1.93.105', '8.8.8.8', 53, False))
        self.assertEqual(len(cases), 29)
        for policy, direction, proto, src, dst, port, expect in cases:
            with self.subTest(direction=direction, src=src, dst=dst, port=port):
                self.assertEqual(allows(policy, direction, proto, src, dst, port), expect)

    def test_failsafe_does_not_reopen_dns_or_worker_cp_ports(self):
        for name in render.ORDER:
            _, _, f = render.policies(self.c, name)
            for direction in f['spec']:
                for r in f['spec'][direction]:
                    self.assertEqual(ipaddress.ip_network(r['net']).prefixlen, 32)
                    self.assertNotIn(r['port'], [53, 179, 10250])
                    if 'worker' in name and direction == 'failsafeInboundHostPorts':
                        self.assertEqual(r['port'], 22)
                    if 'worker' in name:
                        self.assertNotIn(r['port'], [2379, 2380])

    def test_missing_inputs_block_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / 'not-created'
            r = subprocess.run([sys.executable, str(ROOT / 'code/render.py'), '--config',
                                str(ROOT / 'config/runtime-input.json'), '--out', str(out)],
                               capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(out.exists())

    def test_ntp_unknown_and_broad_nets_rejected(self):
        for value in ([], ['0.0.0.0/0'], ['10.1.93.0/24'], ['127.0.0.1/32']):
            c = copy.deepcopy(self.c)
            c['nodes']['k8s-worker01']['ntp_nets'] = value
            with self.assertRaises(ValueError):
                render.validate(c, ROOT, False)

    def test_roles_api_reciprocity_and_workload_scope(self):
        n = self.c['nodes']
        n['k8s-worker01']['api_egress_nets'] = ['10.1.93.102/32']
        with self.assertRaises(ValueError):
            render.validate(self.c, ROOT, False)
        n['k8s-control01']['api_ingress_node_sources'] = ['10.1.93.105/32']
        render.validate(self.c, ROOT, False)
        n['k8s-worker01']['api_ingress_node_sources'] = ['10.1.93.102/32']
        with self.assertRaises(ValueError):
            render.validate(self.c, ROOT, False)

    def test_evidence_hash_validation(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'fact.txt').write_text('SYNTHETIC')
            with self.assertRaises(ValueError):
                render.evidence({'path': 'fact.txt', 'sha256': '0' * 64}, Path(d))

    def test_probe_manifest_restricted_and_no_external_service(self):
        docs = render.probes(self.c)
        pods = [x for x in docs if x['kind'] == 'Pod']
        self.assertEqual(len(pods), 3)
        self.assertNotEqual(pods[0]['spec']['nodeSelector'], pods[1]['spec']['nodeSelector'])
        for pod in pods:
            c = pod['spec']['containers'][0]
            self.assertFalse(pod['spec']['automountServiceAccountToken'])
            self.assertEqual(c['imagePullPolicy'], 'Never')
            self.assertTrue(c['securityContext']['readOnlyRootFilesystem'])
            self.assertEqual(c['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertEqual(docs[-1]['spec']['type'], 'ClusterIP')


class OperationalTests(unittest.TestCase):
    def rollback_scenario(self, receipt_mode):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            gen, state = root / 'generated', root / 'state'
            gen.mkdir(); state.mkdir(); (gen / 'felix').mkdir()
            c = fixture()
            node = render.ORDER[0]
            _, hep, f = render.policies(c, node)
            hep['metadata'].update(uid='hep-uid', resourceVersion='11')
            felix = {'metadata': {'name': 'node.' + node, 'uid': 'felix-uid', 'resourceVersion': '12'},
                     'spec': f['spec']}
            (gen / 'config.lock.json').write_text(json.dumps(c))
            (gen / 'felix' / (node + '.json')).write_text(json.dumps(f))
            lines = [hashlib.sha256(x.read_bytes()).hexdigest() + '  ' + str(x.relative_to(gen))
                     for x in gen.rglob('*') if x.is_file()]
            (gen / 'SHA256SUMS').write_text('\n'.join(lines))
            configsha = hashlib.sha256((gen / 'config.lock.json').read_bytes()).hexdigest()
            change.dump(state / 'state-binding.json', {'cluster_uid': c['cluster_uid'],
                        'attempt_id': c['attempt_id'], 'config_sha256': configsha})
            change.dump(state / (node + '-hep-created.json'), hep)
            change.dump(state / (node + '-felix-before.json'), None)
            change.dump(state / (node + '-felix-after.json'), felix)
            proof = root / 'proof.txt'; proof.write_text('SYNTHETIC TEST ONLY')
            receipt = {'node': node, 'cluster_uid': c['cluster_uid'], 'attempt_id': c['attempt_id'],
                       'config_sha256': configsha, 'status': 'PROTECTION_RESTORED_REVIEWED',
                       'evidence': [{'path': str(proof), 'sha256': hashlib.sha256(proof.read_bytes()).hexdigest()}]}
            if receipt_mode == 'wrong-node': receipt['node'] = 'k8s-worker02'
            rp = root / 'receipt.json'; change.dump(rp, receipt)
            actions = []
            class Fake:
                h = copy.deepcopy(hep)
                f = copy.deepcopy(felix)
                def __init__(self, prefix): pass
                def get(self, kind, name=None, optional=False):
                    if kind == 'namespace': return {'metadata': {'uid': c['cluster_uid']}}
                    if kind.startswith('hostendpoints'):
                        return self.h if name else {'items': [self.h] if self.h else []}
                    if kind.startswith('felixconfigurations'): return self.f
                    raise AssertionError(kind)
                def remove_exact(self, plural, current):
                    actions.append(plural)
                    if plural == 'hostendpoints': self.h = None
                    elif plural == 'felixconfigurations': self.f = None
                    else: raise AssertionError(plural)
            args = ['cluster-change.py', 'rollback-node', '--node', node, '--bundle', str(gen),
                    '--state', str(state), '--kubeconfig', '/SYNTHETIC', '--context', 'SYNTHETIC']
            if receipt_mode != 'missing': args += ['--protection-receipt', str(rp)]
            with patch.object(change, 'Cluster', Fake), patch.object(sys, 'argv', args):
                if receipt_mode in ('missing', 'wrong-node'):
                    with self.assertRaises(ValueError): change.main()
                    self.assertEqual(actions, [])
                else:
                    change.main()
                    self.assertEqual(actions, ['hostendpoints', 'felixconfigurations'])

    def test_missing_protection_receipt_blocks_all_rollback_mutations(self):
        self.rollback_scenario('missing')

    def test_wrong_node_receipt_blocks_all_rollback_mutations(self):
        self.rollback_scenario('wrong-node')

    def test_recovery_order_after_bound_protection_receipt(self):
        self.rollback_scenario('valid')

    def test_audit_dates_are_separate_arguments(self):
        args = audit.time_args(1709164799, 1709164801)
        self.assertEqual(args, ['-ts', '02/28/2024', '23:59:59', '-te', '02/29/2024', '00:00:01'])

    def test_raw_delete_binds_uid_and_resource_version(self):
        k = change.Cluster(['kubectl'])
        current = {'metadata': {'name': 'one', 'uid': 'uid1', 'resourceVersion': '17'}}
        with patch.object(k, 'call') as mocked:
            k.remove_exact('hostendpoints', current)
            args = mocked.call_args
            self.assertIn('-f', args.args)
            self.assertEqual(json.loads(args.kwargs['data'])['preconditions'],
                             {'uid': 'uid1', 'resourceVersion': '17'})

    def test_bundle_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'one').write_text('bad')
            (root / 'SHA256SUMS').write_text('0' * 64 + '  one\n')
            with self.assertRaises(ValueError):
                change.verify_bundle(root)
            (root / 'SHA256SUMS').write_text('0' * 64 + '  ../one\n')
            with self.assertRaises(ValueError):
                change.verify_bundle(root)

    def test_cockpit_empty_ss_error_is_not_pass(self):
        import os
        if os.geteuid() != 0:
            self.skipTest('shell under test requires root; no privilege escalation in tests')
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            (folder / 'systemctl').write_text('#!/bin/bash\nprintf "Id=cockpit.socket\\nLoadState=masked\\nUnitFileState=masked\\nActiveState=inactive\\n"\n')
            (folder / 'ss').write_text('#!/bin/bash\nexit 1\n')
            for f in folder.iterdir(): f.chmod(0o755)
            r = subprocess.run(['bash', str(ROOT / 'code/cockpit.sh'), 'check'],
                               env=dict(os.environ, PATH=d + ':' + os.environ['PATH']), capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)
            self.assertIn('SS_QUERY_ERROR', r.stderr)

    def test_firewall_evidence_capture_records_errors(self):
        r = host.run(['/DOES_NOT_EXIST_FOR_OFFLINE_TEST'], False)
        self.assertEqual(r['rc'], 125)
        self.assertTrue(r['stderr'])


if __name__ == '__main__':
    unittest.main(verbosity=2)

```

## validation/bash-syntax.txt

```text
COMMAND=["bash", "-n", "code/cockpit.sh"]
RC=0
STDOUT:

STDERR:

```

## validation/crd-and-calico.txt

```text
COMMAND=["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3", "tests/check-crd.py", "--source", "../review_input/calico_v3.32.1_official.yaml"]
RC=0
STDOUT:
PASS: 18 GNP/HEP/Felix objects: served API, recursive fields/types/required/enum/pattern/bounds/allOf/anyOf subset
PASS: Calico offline preparation: VXLAN backend, IPPool env/CIDR/NAT/MTU, both BIRD probes, image digests
NOT RUN: full JSONSchema, CEL, server admission, Calico semantic validation, runtime enforcement

STDERR:

```

## validation/python-syntax.txt

```text
COMMAND=["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3", "-m", "compileall", "-q", "code", "tests"]
RC=0
STDOUT:

STDERR:

```

## validation/unit-tests.txt

```text
COMMAND=["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python3", "tests/validate.py"]
RC=0
STDOUT:
CALICO_RESTORED_TEST_REQUIRED; recheck effective inheritance and traffic

STDERR:
test_audit_dates_are_separate_arguments (__main__.OperationalTests.test_audit_dates_are_separate_arguments) ... ok
test_bundle_tamper_rejected (__main__.OperationalTests.test_bundle_tamper_rejected) ... ok
test_cockpit_empty_ss_error_is_not_pass (__main__.OperationalTests.test_cockpit_empty_ss_error_is_not_pass) ... ok
test_firewall_evidence_capture_records_errors (__main__.OperationalTests.test_firewall_evidence_capture_records_errors) ... ok
test_missing_protection_receipt_blocks_all_rollback_mutations (__main__.OperationalTests.test_missing_protection_receipt_blocks_all_rollback_mutations) ... ok
test_raw_delete_binds_uid_and_resource_version (__main__.OperationalTests.test_raw_delete_binds_uid_and_resource_version) ... ok
test_recovery_order_after_bound_protection_receipt (__main__.OperationalTests.test_recovery_order_after_bound_protection_receipt) ... ok
test_wrong_node_receipt_blocks_all_rollback_mutations (__main__.OperationalTests.test_wrong_node_receipt_blocks_all_rollback_mutations) ... ok
test_evidence_hash_validation (__main__.PolicyTests.test_evidence_hash_validation) ... ok
test_failsafe_does_not_reopen_dns_or_worker_cp_ports (__main__.PolicyTests.test_failsafe_does_not_reopen_dns_or_worker_cp_ports) ... ok
test_fixed_flow_matrix (__main__.PolicyTests.test_fixed_flow_matrix) ... ok
test_missing_inputs_block_output (__main__.PolicyTests.test_missing_inputs_block_output) ... ok
test_ntp_unknown_and_broad_nets_rejected (__main__.PolicyTests.test_ntp_unknown_and_broad_nets_rejected) ... ok
test_probe_manifest_restricted_and_no_external_service (__main__.PolicyTests.test_probe_manifest_restricted_and_no_external_service) ... ok
test_roles_api_reciprocity_and_workload_scope (__main__.PolicyTests.test_roles_api_reciprocity_and_workload_scope) ... ok

----------------------------------------------------------------------
Ran 15 tests in 0.072s

OK

```