# P1-MON-01 IF-06B `.108` 출발지 음성 시험용 임시 Canary GNP — 2026-10-02 KST

Status: REVIEW READY / INDEPENDENT REVIEW PENDING / APPLY HOLD / RUNTIME NOT RUN.
개정: 2026-10-04 r2 — [GWONUK 리뷰 5404757897](https://github.com/321Team/infra-kubernetes/pull/53#pullrequestreview-5404757897) B-MON-01 반영(monitor01 캡처 양방향화·Flags 보존·캡처 유효성 Gate). r3 — [재리뷰 5404856196](https://github.com/321Team/infra-kubernetes/pull/53#pullrequestreview-5404856196) 반영(캡처 종료 코드를 해당 PID에서만 보존, 이름 기반 pkill 제거, 모의 시험 추가).
분류: 코드·실행 설정(manifest) + 실행 제안. 문서만의 PR이 아니다([중앙 리뷰 권한 정책 §2](https://github.com/321Team/fds-project-baseline/blob/99b48c13ec83a0d130018e62b6751a0b9cb78066/governance/REVIEW_AUTHORITY_POLICY.md)).

책임: MON #17(시험·판정, MON Lead 이재환), K8S/SEC(정책 적용·삭제, 이하영), PMO/QA(적용 전 소스·사후 증적 대조, 이권욱). 기존 담당·AC는 바뀌지 않는다.

## 1. 왜 필요한가

[#17 PMO/QA 결정 5947882932](https://github.com/321Team/fds-project-baseline/issues/17#issuecomment-5947882932)에 따라 기존 worker03 호스트 GNP를 직접 수정하지 않고 **별도 임시 GNP 1개**로 `.108` 출발지 시험 3건을 진행한다.

- worker03 호스트에서 monitor01 `10.250.10.30`으로 나가는 SYN이 `fds-k8s-host-k8s-worker03`(order 100, 마지막 Egress `Deny`)에 막혀 3건이 NOT VERIFIED였다([evidence#98](https://github.com/321Team/fds-project-evidence/pull/98), `received-20261002T0611Z`).
- 평소 remote write는 Pod 트래픽이라 `applyOnForward=false`인 이 정책을 거치지 않고 SNAT된 `.108`로 도착한다. 그래서 운영 경로는 영향이 없고 시험 경로만 막혀 있다.

## 2. 후보 manifest

동봉 파일: `P1_MON_01_IF06B_WORKER03_TO_MONITOR01_CANARY_20261002.yaml` (GlobalNetworkPolicy 1개).

| 항목 | 값 |
|---|---|
| 이름 | `p1-mon-if06b-worker03-to-monitor01-canary` |
| tier / order | `default` / **90** (기존 host GNP order 100 앞) |
| selector | `fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-worker03"` — worker03 HostEndpoint 1개만 |
| applyOnForward | `false` |
| types | `Egress`만 (Ingress 규칙 없음) |
| 규칙 | Allow, TCP, destination `10.250.10.30/32`, 포트 `443`·`8443` — **한 줄뿐** |

넣지 않은 것: catch-all Allow/Deny, 다른 Worker·CP, Pod CIDR, `10.250.10.0/24` 전체, 다른 목적지·포트, 기존 GNP·HEP·firewalld·NAT·IPPool·NetworkPolicy 변경. 선례(order 90 별도 GNP, 기존 객체 불변)는 `MON_WORKER01_9100_CANARY_20260924.yaml`과 같은 구조다.

보안 한계: 허용 기준은 노드 출발지 `.108`이다. 시험 시간 동안 worker03 호스트의 어떤 프로세스든 `.30:443·8443`으로 나갈 수 있다. 그래서 시간 제한·시험 직후 삭제가 필수이며 영구 정책으로 유지하지 않는다. 허용이 monitor01 쪽 통제(nginx 승인 출발지·Basic Auth, firewalld)를 대신하지 않는다.

## 3. 시험 3건과 합격 기준 (QA GO 5946270938·5947882932 반영)

모든 시험은 worker03 기존 승인 접근(`server07`)에서 한다. 새 SSH·firewalld·NetworkPolicy·Pod 변경은 없다. 아래 명령은 **리뷰용 제안이며 지금 실행하지 않는다.**

| # | 시험 | 성공 기준 |
|---|---|---|
| 0 | 실행 직전 `ip -4 route get 10.250.10.30` | `src 10.1.93.108` |
| T1 | `.108 → .30:443` 잘못된 Basic Auth | TLS 성공(검증 우회 없음) + **HTTP 401**, nginx access log에 출발지 `.108` |
| T2 | `.108 → .30:8443` 직접 | curl 연결 실패 + **같은 유효 캡처**에서 `.108 → .30:8443` SYN(`Flags [S]`) 도착, 같은 flow의 `.30 → .108` SYN-ACK(`Flags [S.]`) 없음. 응답이 RST(`Flags [R.]`)·ICMP(prohibited/unreachable)·무응답 중 무엇인지 그대로 기록한다. SYN이 monitor01에 도착했으므로 worker03 GNP 차단은 아니다. 다만 "monitor01 쪽에서 막혔다"는 판정은 이 패킷 관측과 시험 전후 monitor01 firewalld runtime/permanent 규칙 기록을 함께 읽어서만 하고, 패킷만으로 특정 규칙의 drop이라고 단정하지 않는다. 443이 401로 통과하므로 evidence#98의 "443도 같이 막힘" 모호성이 사라진다 |
| T3 | monitor01 tcpdump(헤더만, T1·T2와 같은 캡처) | 출발지 **10.1.93.108** 확인 |
| 캡처 유효성 | T1이 같은 캡처의 양성 대조 | T1 flow에서 `.108 → .30:443` `[S]`와 `.30:443 → .108` `[S.]`가 **둘 다** 보여야 캡처가 유효하다(필터가 양방향을 통과시키고 Flags가 출력됨을 시험 자체로 확인). 둘 중 하나라도 없거나, `cap-rc.txt`가 `rc=0`이 아니거나(비정상 종료), `listening on`이 없거나, `dropped by kernel`이 0이 아니거나, T1·T2 시각이 `cap-start`~`cap-end` 창 밖이면 T2·T3은 `NOT VERIFIED` |

공통 조건: `-k`·`--insecure` 금지, CA는 승인 Root(SHA-256 `20ddfa5e92c346aaf46fc30179fbc0b069e256effd2615a9f4308a8fe719053f`)와 일치할 때만 사용, 잘못된 값은 실제·과거 비밀번호를 쓰지 않고 임의 값을 만들어 쓰고 `unset`, tcpdump는 payload 옵션 없이 헤더만.

```bash
# worker03 (server07) — 제안, NOT RUN
WRONG="$(openssl rand -hex 8)"
curl --cacert fds-root-ca.crt --noproxy '*' --connect-timeout 5 --max-time 10 --retry 0 \
  -sS -o /dev/null -w 'http=%{http_code} remote_ip=%{remote_ip} local_ip=%{local_ip} ssl_verify=%{ssl_verify_result}\n' \
  -X POST -u "fds-heartbeat:${WRONG}" --data-binary '' https://10.250.10.30/api/v1/write      # T1 기대 http=401
unset WRONG
curl --cacert fds-root-ca.crt --noproxy '*' --connect-timeout 5 --max-time 8 --retry 0 \
  -sS -o /dev/null -w 'http=%{http_code}\n' https://10.250.10.30:8443/                         # T2 기대 연결 실패
```

```bash
# monitor01 — 제안, NOT RUN. 양방향 TCP 헤더 + 두 호스트 사이 ICMP. -q·-A·-X·-w 쓰지 않음(헤더 텍스트만, payload 없음)
CAP_WINDOW=180   # 초. 이 창 안에서 T1 → T2를 끝낸다
date -Is | tee cap-start.txt
sudo timeout --signal=INT --preserve-status "$CAP_WINDOW" tcpdump -nn -tttt -l -i ens160 '(host 10.1.93.108 and host 10.250.10.30) and ((tcp and (port 443 or port 8443)) or icmp)' > cap.txt 2> cap-stats.txt &
CAP_PID=$!
echo "cap_pid=$CAP_PID window_s=$CAP_WINDOW" | tee cap-pid.txt
# "listening on ens160" 이 cap-stats.txt 에 나온 것을 확인한 뒤 worker03에서 T1 → T2 순서로 실행
# 창이 끝나면 timeout 이 tcpdump 에만 SIGINT 를 보내고, 아래 wait 는 이 PID 하나만 기다려 실제 종료 코드를 받는다
if wait "$CAP_PID"; then CAP_RC=0; else CAP_RC=$?; fi
echo "rc=$CAP_RC" | tee cap-rc.txt
date -Is | tee cap-end.txt
cat cap-stats.txt   # packets captured / received by filter / dropped by kernel
```

- 필터 근거: `host A and host B`는 방향과 무관하게 두 주소 사이 패킷을 모두 통과시키므로 역방향 SYN-ACK·RST가 같은 캡처에 남는다. `-q`는 TCP Flags를 출력하지 않으므로 쓰지 않는다. ICMP를 함께 받는 이유는 firewalld의 reject가 RST 대신 ICMP prohibited로 응답할 수 있어서다(T19 run02 HEP 삭제 단계의 "호스트로 갈 루트가 없음" 관측과 같은 형태).
- 종료 경로: 이름 기반 `pkill`을 쓰지 않는다. `timeout --signal=INT`가 자기 자식인 이 tcpdump에만 SIGINT를 보내고, `--preserve-status`로 tcpdump의 종료 코드를 그대로 돌려주며, `sudo`는 명령의 종료 코드를 그대로 전달한다. `wait "$CAP_PID"`는 이 job 하나만 기다린다(인자 없는 `wait`는 모든 job을 기다리고 0을 돌려주므로 쓰지 않는다). `if/else`로 받으므로 `wait` 실패 시에도 rc가 기록된다. 정상 종료 기대값은 `rc=0`이다.
- `wait`는 캡처를 시작한 **같은 셸**에서만 동작한다. 그 셸 세션이 끊기면 rc를 얻을 수 없으므로 캡처를 `NOT VERIFIED`로 둔다. 시험이 창 안에 끝나지 않아도 창을 늘려 다시 돌리지 않고 `NOT VERIFIED`로 둔다.
- 모의 시험: 동봉 `P1_MON_01_IF06B_CAPTURE_EXIT_MOCK_TEST.sh`가 같은 구조를 가짜 `sudo`·`tcpdump`로 돌려 정상 SIGINT 종료(`rc=0`), 비정상 종료(`rc=1`), 신호 처리 없는 종료(`rc=130`)를 구분하는지, 그리고 기존 인자 없는 `wait`가 실패를 0으로 삼키는지를 재현한다.
- worker03 curl은 시험마다 stdout·stderr·RC를 따로 파일로 남긴다(예: `t1.out` `t1.err` `t1.rc`).

T1의 TLS 서버 이름 검증은 IP SAN `.30`으로 한다(PKI evidence#18). T2에서 8443 직접 접근 연결 실패는 curl 단독으로는 원인을 구분하지 못하므로 같은 유효 캡처의 SYN 도착·응답 형태와 함께 읽는다. 캡처가 유효하지 않으면 T2를 다시 돌려 맞추지 않고 `NOT VERIFIED`로 남긴다(5절 2항 "반복하지 않는다"와 같음).

## 4. 실행 Gate (모두 충족 전 APPLY HOLD)

1. 이 manifest의 exact HEAD 독립 리뷰와 최종 승인(저장소 README의 변경 검토·승인 절차). 병합은 실행 승인이 아니다.
2. **T19 종료 → `PROTECTION_RESTORED_REVIEWED` → 클러스터 baseline 안정 확인** 뒤에만 시작. T19과 같은 시간에 하지 않는다.
3. 실행 직전: worker03 HostEndpoint `fds-k8s-k8s-worker03`·`fds-k8s-host-k8s-worker03` 현재 spec/UID/resourceVersion 기록(selector 라벨·order 100·종단 Deny 확인), 같은 selector를 선택하는 다른 GNP와 order 90 객체가 없는지 확인, 후보 이름 absent(명시적 NotFound), 서버 측 dry-run.
4. monitor01·worker03 baseline: Target `up` 23/23, remote write 204 지속·실패 0, heartbeat 지연 정상, firewalld runtime/permanent 사전 기록.
5. 변경창(시작·종료), 정책 적용·삭제 담당(하영), 시험·수집 담당(재환), 복구 접근(콘솔) 기록. 창은 짧게 잡는다(생성→시험 3건→삭제).

## 5. 실행과 정리

1. `kubectl create`로 후보만 생성한다(`apply`·`replace` 금지, AlreadyExists면 중단). 응답의 UID·resourceVersion·spec과 KST 시각을 보존한다.
2. T0·T1·T2·T3을 각 한 번씩. 선호하는 결과를 얻으려고 반복하지 않는다. T1이 도달하지 못하거나 T3에 출발지가 보이지 않으면 중단·삭제한다.
3. 기존 remote write 204·Target 23/23·heartbeat에 이상이 없는지 확인한다.
4. **즉시 삭제:** 보존한 UID가 현재 객체와 같음을 확인한 뒤, 보존한 UID·resourceVersion을 precondition으로 raw DELETE(이름만으로 get 후 delete 금지, 라벨 삭제 금지). 이후 명시적 NotFound, 기존 HEP/GNP spec 불변, firewalld diff 0, 새 연결 기준으로 baseline Deny 복귀를 확인한다. 이미 맺어진 연결은 정책 삭제로 끊기지 않을 수 있으므로 새 연결로 본다. conntrack 초기화·Prometheus 재시작은 하지 않는다.

```bash
# 삭제 — 제안, NOT RUN. 본문은 stdin(JSON), UID·resourceVersion은 생성 응답에서 보존한 값
/usr/bin/sudo -n /usr/bin/kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s \
  delete --raw=/apis/crd.projectcalico.org/v1/globalnetworkpolicies/p1-mon-if06b-worker03-to-monitor01-canary -f -
# stdin: {"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"RECORDED_CREATED_UID","resourceVersion":"LATEST_VERIFIED_RESOURCE_VERSION"}}
```

409면 precondition을 빼거나 force로 대체하지 않고 다시 읽어 조사한다. 정리가 끝나지 않으면 cleanup FAIL로 기록하고 기존 복구·에스컬레이션 경로를 쓴다.

## 6. 증적 (새 수집회차·새 evidence PR, evidence#98 소급 금지)

reviewed manifest SHA·exact commit, host/user/KST, 시험 전 정책 인벤토리(HEP/GNP spec·UID), dry-run·create·delete RC, 생성 UID/resourceVersion, T0~T3 원본(시험별 stdout/stderr/rc), monitor01 tcpdump(헤더만: `cap.txt`, 시작·종료 시각, 종료 코드, captured/received/dropped 통계), nginx access log 최소 구간, 전후 GNP/HEP, 시험 전후 firewalld runtime/permanent diff, remote write 204·Target 23/23·heartbeat 회귀, cleanup 결과, 원본 SHA-256.

## 7. 판정 범위와 한계

- 시험이 통과해도 #17 AC를 자동 체크하지 않는다. 결과는 새 증적 PR 병합 뒤 MON Lead가 줄별로 판단하고, SEC 마지막 조건(wrong auth 401)과 ICR/QA 인수 조건(8443 직접 차단·출발지 보존)을 구분해 기록한다.
- 이 임시 허용은 영구 egress 승인, ICR AC·T19·SEC 증적 확대가 아니다.
- worker03 호스트 egress 차단의 직접 증명(Calico drop counter/log)은 이 시험의 범위가 아니다.
- 시험 중 장애 시 중단 기준: 기존 remote write 실패 증가, Target `up` 감소, 예상 밖 접근, 객체 생성 결과 불명확. 모두 즉시 삭제 경로로 간다.

## 8. 검증 상태

STATIC: YAML 구문·단일 리소스·selector/destination/port/tier/order 범위 확인(manifest에 Ingress·catch-all 없음). r2 캡처 필터: 양방향·Flags 출력·ICMP 포함을 문서로 검토했다. 오프라인 pcap fixture 재생 검증은 작성 PC에 tcpdump가 없어 NOT RUN이며, 대신 T1을 같은 캡처의 양성 대조로 두어 시험 시점에 필터 유효성을 증명하게 했다(3절 "캡처 유효성"). r3 캡처 종료 코드: `P1_MON_01_IF06B_CAPTURE_EXIT_MOCK_TEST.sh` 로컬 모의 실행(GNU bash 5.3.9, GNU coreutils timeout 8.32) 결과 `bare_wait_rc=0`, `pid_wait_rc=1`, `normal rc=0`, `crash rc=1`, `nohandler rc=130`. 실제 RHEL `sudo`의 신호·종료 코드 전달은 모의 범위 밖이며 시험 당일 `cap-rc.txt`와 `cap-stats.txt`로 확인한다. SERVER-SIDE DRY-RUN: NOT RUN. CI: 이 저장소에 워크플로가 없어 NOT RUN. RUNTIME: NOT RUN. REVIEW: PENDING(AI 작성 보조는 독립 리뷰가 아니다). MERGE/APPLY: NOT DONE.

AI 사용: CLAUDE_ASSISTED
