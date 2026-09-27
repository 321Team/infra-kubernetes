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
