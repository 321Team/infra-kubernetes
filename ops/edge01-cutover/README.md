# Edge Cutover — edge01 nginx upstream 전환 (변경안·창 계획)

책임 티켓: [baseline#19](https://github.com/321Team/fds-project-baseline/issues/19) P1-SEC-01 edge01 upstream 이관(edge01 서버 담당 이재환). 근거: Review Owner 조건 [6012871977](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6012871977) §2~§4, 9/29 Gate [5883374181](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5883374181), 일정 제안 [6034620578](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6034620578). 후속: [#47 DB 변경 창](https://github.com/321Team/infra-db/pull/7)(Cutover 완료가 선행조건), #20 edge 전체 경로 회귀.

이 PR은 코드·계획 검토용이다. 병합·리뷰 승인은 실행 승인이 아니다. 실행은 이권욱의 창 GO 뒤에만 한다.

## 1. 범위

### 2026-10-08 오후 보완 — 앱 로그 판정을 요청줄 캡처로 대체 (이권욱 HOLD [6051321109](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6051321109) B1~B3)

- **E-K3S는 유지하며, 앱 로그 기반 판정만 대체한다.** E-K3S는 지금 서비스 경로이고 rollback 대상이다. 바꾸는 것은 "표식이 어느 backend로 갔는가"를 보는 방법뿐이다(K8s·E-K3S transaction-api 접근 로그가 비어 있어 판정 불가, 10/8 확인).
- **B1 대체 관측:** 새 `cutover-capture.sh`가 backend Pod로 **들어가는 HTTP 요청줄**을 캡처한다. Worker 3대는 `tcp dst port 8000 and src host 10.1.93.50`(NodePort DNAT 뒤 Pod 쪽), edge01은 `tcp and dst net 10.42.0.0/16`(E-K3S Pod 쪽)이다. 캡처를 먼저 시작해 `CAPTURE_READY`를 확인한 뒤 nginx `:80`으로 고유 표식 요청을 보내고, `cutover-capture-judge.py`가 표식마다 **어느 노드의 어느 Pod(UID·IP)로 몇 번 들어갔는지**, nginx 경유 서명(`X-Real-IP` 헤더, nginx `proxy_set_header`가 붙임)이 있는지, 캡처 준비·종료 시각이 요청 구간을 덮는지, 캡처 오류(tcpdump rc·커널 drop·해석 rc)가 없는지 판정한다. 기존 P4 collector(`c0d784fa`, 헤더만 `-c 5`, Worker 직접 curl)는 source 보존 증거로만 유지하고 C1 판정에 쓰지 않는다.
- **캡처 범위(기존 헤더-only 수집과 다른 점):** 패킷당 앞 300바이트(snaplen)까지 저장한다. 요청줄과 앞쪽 헤더(Host, X-Real-IP, X-Forwarded-For)가 들어가며 업무 payload 전체는 저장하지 않는다. smoke 요청은 본문 일부(SIM 시험값)가 들어갈 수 있다. 요청 방향·TCP 본문이 있는 패킷만 저장하고 응답은 저장하지 않는다.
- **B2:** 아래 §5의 "원본 결과 → 실패 지점 → 대체 증거 → 판단" 표로 종료 코드를 원인별로 다룬다. 로그 때문에 생기던 rc3/rc4는 없어지고, 앱 로그는 참고 기록이 된다. 단 E-K3S 로그에서 apply 표식이 **발견되면** 옛 경로의 직접 증거이므로 그대로 STOP이다. rollback의 smoke 201·DB 행, C2 P3 DB 1행 `false/false`는 그대로 유지한다.
- **B3:** C0 15 + C1 15 + C2 20 + C3 30 = **80분**(기존 "75분" 표기는 오류)으로 바로잡고, 창 종료 시각에서 거꾸로 **apply 시작 상한 = 창 종료 − 15분(복구 여유) − 65분(C1~C3)**을 GO에 고정한다(§4).

### 2026-10-08 현장 사전검증 보완

- `ps lstart`의 한국어 요일/월을 `date -d`가 해석하지 못한 사례를 확인했다. 프로세스 시각의 생성과 해석 양쪽에 `LC_ALL=C`를 고정한다. 시각을 못 읽으면 PASS로 우회하지 않는다.
- edge `check`는 nginx 구문·reload 반영 실패 또는 E-K3S `TRACE_NOT_READY`에 rc3을 반환한다. control01 `trace`는 `TRACED`/`TRACE_READY`/`NONE_OK`에만 rc0, 조회/해석 실패·누락·중복·미확정에는 rc4를 반환한다. pre/post의 `COLLECTED`는 여전히 사람의 판정이 필요하다.
- 표식 로그 자체가 비어 있으면 HTTP 200과 별개로 관측 조건 미충족이다. 10/8 회차의 Kubernetes transaction-api 3개 로그가 실제 0바이트였고, Worker01 이미지의 `/app/run.py`가 metrics Uvicorn Config의 `log_level="warning"`으로 공용 접근 로그를 억제하는 구조를 확인했다. (오후 보완: 이 때문에 경로 판정을 위의 요청줄 캡처로 대체한다. 앱 배포는 포함하지 않는다.)
- trace 분석 stdout/stderr는 별도 원본 파일로 기록한 뒤 판정한다. 아직 기록 중인 tee 로그를 다시 읽어 판정하는 경합을 제거한다.

- **바꾸는 것:** edge01 nginx `upstream transaction_api` 블록의 4줄뿐이다. `server 10.1.93.50:30080;`(옛 E-K3S)을 주석 처리하고, 이미 주석으로 들어 있는 `# server 10.1.93.105/.107/.108:30080;` 3줄의 주석을 푼다. `nginx -t` 성공 뒤 `systemctl reload nginx`.
- **바꾸지 않는 것:** E-K3S(k3s)는 유지한다(rollback 경로, 중지는 #47 창). listen·location·`/metrics` 404·proxy 설정, Service·EndpointSlice·Calico 정책, TLS/443(edge01에 443 없음, #17 별도), DB 허용(#47).
- 다른 형태의 upstream(유효 server 줄이 `.50:30080` 하나가 아님, Worker 주석 줄이 각 1개가 아님, 예상 밖 server 줄)이면 `apply`는 파일을 바꾸지 않고 멈춘다.

## 2. 현재 사실(근거)

| 항목 | 값 | 출처 |
|---|---|---|
| nginx upstream | `server 10.1.93.50:30080;` 유효, `# server 10.1.93.105/107/108:30080;` 주석, `listen 80 default_server`, `location = /metrics { return 404; }` | evidence `P1-MON-01/received-20260929T0656Z/edge01/edge-metrics.txt`(9/29 `nginx -T` 발췌). 현재 값은 C0 `check`로 다시 확인 |
| `.50/32 → Worker:30080` 허용 | GNP `nodeport-30080-allow`(order 50, preDNAT·applyOnForward, `.50/32`만 허용) **live 적용 중**(9/22 생성). infra-kubernetes `ops/nodeport-30080-allow/`에 live spec과 같은 후보로 편입. PR21(CLOSED)은 이 정책의 옛 경로이며 별도 적용이 필요 없다 | infra-kubernetes main `ops/nodeport-30080-allow/README.md`·`FIELD_20260924-144753.md` |
| Worker local endpoint | 10/7 14:50 #65 분산 적용 뒤 `transaction-api-nodeport` ready endpoint worker01·02·03 각 1, edge01 → `.105/.107/.108:30080/readyz` 200 | #15 [6032106480](https://github.com/321Team/fds-project-baseline/issues/15#issuecomment-6032106480), evidence#136·#138 |
| 22시 SEC 시험 | AC-04 방식 B 첫 완결 회차: 12/12행 수집 완결 = positive 3(Worker 3대 `:30080` CONNECTED) + negative `BLOCKED_VALID` 9. 중복 실행된 두 번째 회차는 이탈 기록(권욱 [6040616297](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6040616297) §3) | evidence#144 |

## 3. 전제조건 (하나라도 아니면 시작하지 않음)

1. 이 PR HEAD의 독립 리뷰와 병합, 실행 파일 SHA-256 = 병합 commit blob(§8).
2. 이권욱의 창 GO(T0·실행자·상한·중단/복구). 단톡 사전 공지. 다른 Runtime(#10·#13·#17·#19 시험, APP·Runner)과 비중첩.
3. C0 판정표(아래)가 모두 기대값. 특히 Worker별 Ready local endpoint 1·1·1과 edge01 → Worker 3대 `/readyz` 200(권욱 6012871977 §2). Pod Running이나 endpoint 합계로 대체하지 않는다.
4. E-K3S active, 현재 edge 경로 200(rollback 경로 확보).
5. 요청줄 캡처가 양쪽 경로를 식별하는지 C0에서 확인됨: `cutover-capture-judge.py check`가 `METHOD_READY`(edge 경유 표식 1이 edge01 캡처에서 E-K3S Pod로 1번·nginx 서명 있음, Worker 직접 표식 w105/w107/w108이 각 Worker 캡처에서 그 노드 Pod로 1번·src `10.1.93.50`·nginx 서명 없음). 아니면 C1을 시작하지 않는다.
6. Worker 캡처는 P4 지원 경로(이권욱, ansible01 `svc_ansible`, become)로 시작·보고한다. 실행자는 Worker 3대 `CAPTURE_READY`를 받은 뒤에만 edge01 `check`/`apply`를 실행한다.

## 4. 창 계획

`RUN`은 같은 값(예: `20261008T1600`). 실행자: 이재환(edge01·control01·PC). Worker 캡처: 이권욱(ansible01 `svc_ansible`, P4 지원 경로). 창 시각·apply 시작 상한은 GO에 고정한다.

### 시간 상한 (B3)

| 구간 | 상한 | 누적(C0 시작 T0 기준) |
|---|---|---|
| C0 사전(캡처 시작·검증 포함) | 15분 | T0+15 |
| C1 전환 | 15분 | T0+30 |
| C2 사후 | 20분 | T0+50 |
| C3 관찰 | 30분 | T0+80 |
| 복구 여유 | 15분 | T0+95 |

- **정상 종료 상한 = T0+80분, 복구 완료 상한 = T0+95분.** 기존 "T0+75/90" 표기는 C0~C3 합(80분)과 맞지 않던 오류다.
- 창 종료가 고정이면 **apply 시작 상한 = 창 종료 − 15분 − 65분(C1~C3)**이다. 이 시각까지 C0 판정이 끝나지 않으면 apply를 시작하지 않고 창을 닫는다. C3를 줄이거나 생략해 맞추지 않는다.
- 상한을 넘기면 되돌리고 멈춘다(연장 없음).

### 단계

| 단계 | 호스트 | 명령 | 기대 / 성공 기준 | 실패 시 |
|---|---|---|---|---|
| C0-a | control01 | `sudo bash control01-cutover-check.sh pre <RUN>` | `RESULT …=COLLECTED`, `POD cluster=k8s …` 3줄. **판정은 C0 판정표에 사람이 기록** | 시작 안 함 |
| C0-b | ansible01 → Worker 3대 | `ansible k8s_workers -b -m script -a "cutover-capture.sh start <RUN> check 900"` | 3대 모두 `CAPTURE_READY` | 시작 안 함 |
| C0-c | edge01 | `sudo bash cutover-capture.sh start <RUN> check 900` | `CAPTURE_READY` | 시작 안 함 |
| C0-d | edge01 | `bash edge01-cutover.sh check <RUN>` | 종료 0, `RESULT cutover-check=EDGE_CAPTURE_READY tag=check<HHMMSS>` | 시작 안 함 |
| C0-e | ansible01 → Worker 3대 | `ansible k8s_workers -b -m script -a "cutover-capture.sh report <RUN> check"` | 3대 `CAPTURE … err=0` | 시작 안 함 |
| C0-f | 판정 | `python3 -I cutover-capture-judge.py check <RUN> check<HHMMSS> check <edge01 check 로그> <Worker 보고> <control01 pre 로그>` | 종료 0, `verdict=METHOD_READY` | 시작 안 함 |
| C1-a | ansible01 → Worker 3대 | `… start <RUN> apply 900` | 3대 `CAPTURE_READY` | 시작 안 함 |
| C1-b | edge01 | `sudo bash cutover-capture.sh start <RUN> apply 900` → `bash edge01-cutover.sh apply <RUN>` | 종료 0, `RESULT cutover-apply=EDGE_OK`(① 설정 반영 ② 표식 9/9 200·smoke 201·`/metrics` 404 ③ edge01 캡처의 E-K3S Pod 쪽 apply 표식 0건) | §5 표 |
| C1-c | ansible01 → Worker 3대 | `… report <RUN> apply` | 3대 `CAPTURE … err=0` | §5 표 |
| C1-d | 판정 | `python3 -I cutover-capture-judge.py apply <RUN> apply apply <edge01 apply 로그> <Worker 보고> <control01 pre 로그>` | 종료 0, `verdict=PATH_OK`. **C1-b와 C1-d가 모두 맞아야 C1 완료** | 종료 3(`OLD_PATH`): 즉시 `rollback` / 종료 4: 완료로 기록하지 않고 실행자·Review Owner가 `rollback` 또는 보완 관측 판단 |
| C2 사후 | PC·db01·control01·ansible01 | 아래 확인 시험 | 전부 기대값 | `rollback` |
| C3 관찰 | — | 5분 간격 PC `/readyz`, MON | 200 유지, MON 이상 없음 | `rollback` |

- Worker 명령의 `cutover-capture.sh`는 병합 commit에서 꺼낸 파일이다(§8). `-m script`가 파일을 Worker에 복사해 root로 실행한다. 판정은 어느 호스트에서 해도 된다(python3만 필요, 서버에 접속하지 않음). 입력 파일은 ansible 출력을 그대로 저장한 것이어도 된다.
- 캡처는 `<초>`(900) 뒤 자동 종료한다. `report`는 실행 중이면 먼저 멈춘다. 같은 RUN·WIN은 다시 시작하지 않으므로 C0를 다시 하면 `check2`처럼 새 WIN을 쓴다(`bash edge01-cutover.sh check <RUN> check2`, 판정 인자도 `check2`).
- `apply`는 파일을 바꾸기 전에 모든 게이트를 본다(edge01 캡처 실행 중 포함). 새 설정이 `nginx -t`를 통과하지 못하면 사본으로 즉시 되돌리고 reload하지 않는다.
- **"9/9 HTTP 성공", "설정 반영", "실제 새 upstream 경유"를 따로 증명한다.**
  - 설정 반영: reload 뒤 정리 중인 옛 worker(`shutting down`)를 빼고, 현재 worker가 모두 설정 파일 최종 수정 이후에 시작됐는지 본다. 5초 간격으로 최대 7번(약 35초) 다시 본다. 끝내 확인하지 못하면 종료 4. nginx는 reload 뒤에도 옛 worker가 기존 연결을 처리할 수 있으므로, 반영 판정과 별개로 **새 요청 각각이 어느 backend로 갔는지**를 아래 캡처로 본다.
  - 실제 경유: 요청마다 고유 표식 `?cutover=cutover-<RUN>-<tag>-<번호>`를 붙이고(transaction-api는 모르는 쿼리를 무시), edge01은 보내기 직전 시각과 HTTP 코드를 `SENT` 줄로 남긴다. backend 쪽 캡처에서 표식이 든 요청줄을 찾아 **표식 → 캡처 호스트 → dst Pod IP → Pod 이름·UID·노드**(control01 `POD` 줄, E-K3S는 edge01 `POD cluster=ek3s` 줄)로 결속한다.
  - nginx 경유와 직접 요청의 구분: nginx는 `X-Real-IP`를 붙인다(D18 Option A 원본 설정 fds-msa `kubernetes/edge-proxy/fds.conf`(9/7)의 `proxy_set_header X-Real-IP $remote_addr`. 9/29 `nginx -T` 수집본은 upstream·listen·`/metrics` 줄만 담아 이 줄이 없다. 운영 설정에 실제로 있는지는 C0 `check`의 `proxy_set_header` 출력과 판정기의 `xri` 값으로 확인하고, 없으면 `METHOD_READY`가 나오지 않아 시작하지 않는다). apply·rollback 표식은 nginx 서명이 있어야 하고, C0의 Worker 직접 표식(w105 등)은 서명이 없어야 한다. 같은 WIN에 다른 tag 표식이 섞이면 판정 불가다.
  - 캡처 오류·누락: tcpdump 종료 코드(0/124 정상), 종료 통계의 커널 drop 0, pcap 해석 rc 0, 캡처 준비 < 첫 요청, 캡처 종료 > 마지막 요청. 하나라도 아니면 판정 불가(종료 4)다. 0건 정상과 캡처 실패를 합치지 않는다.
  - 같은 패킷이 두 인터페이스(브리지·veth)에서 잡히거나 재전송된 경우는 (src, dst, seq, 표식)으로 1번만 센다. 서로 다른 연결로 같은 표식이 두 번 들어오면 중복(판정 불가)이다.
  - 표식은 RUN·tag·번호로 정해지고 check·rollback tag에는 실행 시각(`HHMMSS`)이 붙어, 이전 요청이나 다른 회차가 섞이지 않는다. 기대값은 "각 번호가 정확히 1번"이다(누락·중복 모두 실패).
  - E-K3S·K8s 앱 로그 표식 조회(edge01 `ek3s_trace`, control01 `trace`)는 참고 기록으로 남긴다. apply 표식이 E-K3S 로그에서 발견되면 옛 경로의 직접 증거이므로 STOP이다.
  - 순간 소켓 목록(`ss`), 패킷 수, P4 collector 결과는 C1 판정에 쓰지 않는다.

### C0 판정표 (실행자가 채워 증적에 넣는다)

| 항목 | 기대 | 기록 값 | 판정 |
|---|---|---|---|
| `transaction-api-nodeport` | `type=NodePort etp=Local ports=80->8000:30080` | | |
| Worker별 endpoint | `k8s-worker01/02/03` 각 1, `ready=true terminating=false` | | |
| GNP `nodeport-30080-allow` | `preDNAT=true applyOnForward=true`, ingress에 `10.1.93.50/32` 허용 | | |
| nodePort 32085 사용 Service | 0 | | |
| MON | `count(up)=23`, `count(up==1)=23`, `count(up==0)`=EMPTY 또는 0 | | |
| control01 조회 실패 수 | 0 (ERR·port-forward 실패는 판정 불가, 0개 정상으로 읽지 않음) | | |
| edge01 nginx | `nginx -t` OK, reload 반영 rc=0, 유효 upstream `10.1.93.50:30080` 하나, 정의 파일 1개 | | |
| edge01 경로 | edge `/readyz` 200, `/metrics` 404, Worker 3대 `:30080/readyz` 200, k3s active | | |
| edge01 도구 | `python3` 있음 | | |
| Pod 결속 입력 | control01 `POD cluster=k8s` 3줄(worker01·02·03 각 1), edge01 `POD cluster=ek3s` 1줄 이상 | | |
| 캡처 준비 | Worker 3대·edge01 `CAPTURE_READY`(WIN=check), 준비 시각 기록 | | |
| 캡처 관측(E-K3S) | edge01 `check` → `EDGE_CAPTURE_READY`(표식 1이 E-K3S Pod로 1번, `X-Real-IP` 있음) | | |
| 캡처 관측(전체) | `cutover-capture-judge.py check` → `METHOD_READY`(w105→worker01, w107→worker02, w108→worker03 각 1번, 그 노드 Pod, src `10.1.93.50`, `X-Real-IP` 없음, 4개 캡처 err=0) | | |
| 참고: 앱 로그 | edge01 `TRACE ek3s …`, control01 `trace` 출력(판정 미사용, 원본 보존) | | — |

하나라도 기대와 다르거나 판정 불가면 C1을 시작하지 않는다.

### 확인 시험 (C2)

| 구분 | 시험 | 명령 | 기대 |
|---|---|---|---|
| P1 | edge 경로 | PC `curl -s -o /dev/null -w '%{http_code}' http://10.1.93.50/readyz` | 200 |
| P2 | `/metrics` 비노출 | PC 같은 명령으로 `/metrics` | 404 |
| P3 | 업무 smoke DB 행 | C1 `apply`가 보낸 `SIM_cut_<RUN에서 T 제외>_apply` 1건을 db01에서 `SELECT fds_detected, fds_evaluation_skipped FROM transactions WHERE account_id = 'SIM_cut_<…>_apply'` | 행 1, `false`/`false` |
| P4 | source `.50` 보존 | evidence#86과 같은 수집기(infra-ansible `c0d784fa`, collector `42fc3ed4…`) 1회, 새 Pod 기준. 실행 이권욱 | Worker 3대 Pod에서 source `10.1.93.50` (source 보존 증거. C1 경로 판정에는 쓰지 않는다 — 수집기가 Worker로 직접 curl) |
| N1 | 비승인 출발지 30080 | 아래 N1 절차 | 아래 판정 |
| — | K8s·MON 사후 | control01 `sudo bash control01-cutover-check.sh post <RUN>` | C0 판정표와 같은 값 |

#### N1 절차 (PC, Windows PowerShell 5.1)

출발지는 개발 PC `10.1.93.29`(server08 물리 호스트, `.50/32` 허용 목록 밖). Git Bash의 `timeout`/`/dev/tcp` 대신 PowerShell 내장 cmdlet을 쓴다. 출력 전체를 파일로 남긴다.

```powershell
$o = "$HOME\edge-cutover-<RUN>-n1.txt"
& { Get-Date -Format o
    Find-NetRoute -RemoteIPAddress 10.1.93.105 | Format-List IPAddress,InterfaceAlias,NextHop,DestinationPrefix
    Test-NetConnection 10.1.93.50 -Port 80 -InformationLevel Detailed
    foreach ($w in '10.1.93.105','10.1.93.107','10.1.93.108') { Test-NetConnection $w -Port 30080 -InformationLevel Detailed }
    Get-Date -Format o } *>&1 | Tee-Object -FilePath $o
```

| 관측 | 판정 |
|---|---|
| 대조(`10.1.93.50:80`) `TcpTestSucceeded : True`, `SourceAddress`가 `10.1.93.29`, Worker 3건의 `SourceAddress`도 `10.1.93.29` | 이 조건이 맞을 때만 아래를 판정한다. 아니면 **INCONCLUSIVE** |
| Worker `:30080` `TcpTestSucceeded : False` | 차단과 일치(timeout·거부를 구분하지 않는 관측). 특정 정책이 유일한 차단 주체라는 증명은 아님 |
| Worker `:30080` `TcpTestSucceeded : True` | **FAIL** → 중단 기준 |
| cmdlet 오류·예외, 출력 누락 | **INCONCLUSIVE**(도구·환경 문제, 차단 근거로 쓰지 않음) |

- 시험 거래(`SIM_cut_*`)는 기능 시험 거래로 보존한다.

## 5. 중단·복구

- 중단 기준: edge `/readyz` 실패, 업무 smoke 실패·5xx, N1 FAIL, MON 이상, 상한 초과, C1 종료 4 또는 판정 `UNCONFIRMED`를 실행자·Review Owner가 수용하지 않음, 판정 `OLD_PATH`.
- 복구: Worker 캡처(가능하면)와 edge01 캡처를 `rollback` WIN으로 시작한 뒤 `bash edge01-cutover.sh rollback <RUN>`. 캡처 시작이 실패해도 복원은 진행한다(결과는 종료 4로 남는다). 순서: apply 직전 사본으로 복원(`cat >`로 inode·권한·문맥 유지) → `nginx -t` → reload → 디스크 유효 upstream `.50:30080` → 설정 반영(약 35초) → 표식 요청 3회 200·표식 smoke 201(**기능 복구**) → edge01 캡처에서 이번 rollback 표식 1~3·smoke가 E-K3S Pod로 각 정확히 1번(**옛 경로 경유**). 둘 다 맞아야 `RESULT cutover-rollback=OK`(종료 0)다.
- 복구 뒤 db01에서 rollback smoke 계정 `SIM_cut_<RUN에서 T 제외>_rollback<HHMMSS>`이 1행·`false`/`false`인지 확인한다(앱·DB 경로 회복). Worker 캡처가 있으면 `cutover-capture-judge.py rollback`으로 Worker 쪽 0건도 확인한다(있으면 `NEW_PATH`, 종료 3).
- 쓰지 않은 파일 SHA나 HTTP 200만으로 복구를 선언하지 않는다(새 Worker 경로도 200을 낸다). 기능 복구만 확인되고 옛 경로 경유를 확인하지 못하면 그 한계를 그대로 기록하며 완전한 경로 복구로 올리지 않는다.
- E-K3S·옛 DB 허용(pg_hba `.50`)은 Cutover 창에서 바꾸지 않으므로 rollback 경로는 #47 창 W1 전까지 유지된다. `rollback`은 k3s가 active가 아니면 시작하지 않는다.

### 종료 코드·결과의 원인별 처리 (B2)

원본 rc와 `RESULT`·`STOP:`·`UNCONFIRMED:` 문구는 고치지 않고 그대로 증적에 남긴다. 아래 표는 각 결과를 어떻게 판단하는지다. "제외 로그 항목"은 이번 보완으로 판정에서 빠진 앱 로그 항목이다.

| 단계 | 원본 결과 | 정확한 실패 지점(출력 문구) | 제외 로그 항목 | 대체 증거 | 판단 |
|---|---|---|---|---|---|
| check | rc3 `STOP:` | `sudo 인증 실패` / `nginx -t 실패(사전검증)` / `reload 반영 판정 실패/미확정` / `edge01 캡처가 실행 중이 아님` | 없음 | 없음 | 시작 안 함(원인 해소 후 새 WIN으로 C0 다시) |
| check | rc3 `EDGE_CAPTURE_NOT_READY` | edge01 캡처에서 표식 1이 E-K3S Pod로 1번 들어간 것을 확인 못 함(누락·중복·서명 없음·캡처 오류) | (이전 `TRACE_NOT_READY`: E-K3S 로그 표식) | — | 시작 안 함 |
| check | rc0 `EDGE_CAPTURE_READY` | — | E-K3S 로그 `TRACE ek3s` 줄(참고) | judge check `METHOD_READY` | METHOD_READY일 때만 C1 진행 |
| apply | rc3 `STOP:`(바꾸기 전) | 이미 apply한 RUN / python3 / `nginx -t` / reload 판정 / 정의 파일 수 / 유효 upstream / k3s / 캡처 실행 중 아님 / edge·Worker `/readyz` | 없음 | 없음 | 파일 미변경. 시작 안 함 |
| apply | rc3 `STOP:`(바꾼 뒤) | 설정 쓰기 실패 / 새 설정 `nginx -t` 실패(사본으로 되돌림) / reload 실패 / 디스크 유효 upstream이 Worker 3대 아님 / 9/9 200 아님 / smoke 201 아님 / `/metrics` 404 아님 | 없음 | 없음 | 즉시 `rollback` |
| apply | rc3 `STOP:` 옛 경로 | `apply 표식이 E-K3S 로그에 있음` 또는 `apply 표식이 edge01 캡처에서 E-K3S Pod로 들어감` | 없음(로그에서 **발견**은 그대로 STOP) | — | 즉시 `rollback` |
| apply | rc4 `UNCONFIRMED` | `reload 반영을 약 35초 안에 확인하지 못함` | 없음 | 없음 | 완료 아님. 실행자·Review Owner가 `rollback` 또는 보완 관측 판단 |
| apply | rc4 `UNCONFIRMED` | `edge01 캡처 판정 불가` | (이전: E-K3S 로그 조회 실패 → rc4) | 없음(캡처 자체가 대체 증거) | 완료 아님. 위와 같음 |
| apply | rc0 `EDGE_OK` | — | E-K3S 로그 0건·조회 실패(참고, 판정 미사용) | judge apply `PATH_OK`(Worker 캡처) | `PATH_OK`면 C1 완료, 종료 3(`OLD_PATH`)은 `rollback`, 종료 4는 완료 아님 |
| rollback | rc3 `STOP:` | k3s active 아님 / 설정 복원 실패 / 복원 설정 `nginx -t` 실패 / reload 실패 / 유효 upstream이 `.50:30080` 아님 / GET 3/3 200 아님 / smoke 201 아님 | 없음 | 없음 | 즉시 현장 확인(복구 미완료) |
| rollback | rc4 `UNCONFIRMED` | `복원 설정의 reload 반영을 약 35초 안에 확인하지 못함` | 없음 | 없음 | 복구 미확정. 현장 확인 |
| rollback | rc4 `UNCONFIRMED` | `기능 복구(GET 3/3·smoke 201)는 확인, 옛 경로 경유는 캡처로 확인하지 못함` | (이전: E-K3S 로그 조회 실패·표식 불일치 → rc4) | DB 1행 `false`/`false`, 캡처 원본 | **기능 복구 확인·경로 추적 미확인**으로 기록(완전한 경로 복구 PASS 아님) |
| rollback | rc0 `OK` | — | E-K3S 로그 표식(참고) | edge01 캡처 1~3·smoke 각 1번, DB 1행 | 복구 완료(DB 행 확인 포함) |

## 6. #47 창과의 관계

- #47 창(infra-db `ops/p2-hyb01-db-window/`, 병합 `d14d4597`)은 Cutover `RESULT cutover-apply=OK`, C2 통과, C3 관찰 종료 뒤에만 시작한다. #47 W1 `edge01-k3s.sh stop`도 같은 조건(유효 upstream에 `.50:30080` 0, Worker 3/3, reload 반영, 옛 NodePort ESTABLISHED 0)을 다시 확인한다.
- Cutover 뒤 #47 W1 전까지는 E-K3S가 켜져 있지만 edge 트래픽은 받지 않는다.
- **#47 W1(k3s 중지) 이후에는 이 `rollback`만으로 복구되지 않는다.** 옛 경로가 사라졌고(k3s), W2·W3 뒤에는 `.50`의 DB 접근, W4 뒤에는 E-K3S Secret의 비밀번호도 맞지 않는다. 그때 edge 경로를 되돌려야 하면 #47 README §4의 역순 복구(W4 → W1)를 먼저 하고 이 `rollback`을 실행한다. 두 창 사이에 Cutover 결과·RUN·사본 위치를 #47 실행 기록 첫머리에 인계한다.

## 7. 알려진 차이·잔여 (이번 창에서 바꾸지 않음)

- **정본 D18은 edge01 HAProxy**를 고정하지만 실제 edge01은 nginx(9/29 `nginx/1.26.3`)다. 기존 차이이며 upstream-only 창에서 프록시 소프트웨어를 바꾸지 않는다. 처분은 #19·#30에서 판단한다.
- **`X-Forwarded-For`**: 정본은 실제 Client 주소를 XFF로 전달하도록 요구한다. 현재 `proxy_set_header` 유무는 C0 `check` 출력으로 기록만 한다(transaction-api는 요청 본문의 `source_ip`를 쓴다).
- **backend 장애 동작**: nginx 기본값(`max_fails=1`, `fail_timeout=10s`, `proxy_next_upstream error timeout`, `proxy_connect_timeout 60s`)을 쓴다. local endpoint가 없는 Worker는 즉시 거부(RST)라 다음 backend로 넘어가지만, Worker 호스트가 응답 없이 멈추면 첫 요청이 최대 60초 지연될 수 있다. 타임아웃 조정은 별도 변경으로 다룬다.

## 8. 원본 수집·파일 전달

- edge01 `~/edge-cutover-<RUN>/`(로그, `upstream-file.before/after`, `sent-*.txt`, `capture-edge01-*.txt`·`judge-edge01-*.txt`와 rc, `ek3s-pods-*.txt`, `ek3s-<tag>-*/` E-K3S Pod 로그 원본·rc(참고), `smoke-*.json`), 캡처 원본 edge01·Worker 3대 `/var/tmp/edge-cutover-capture-<RUN>/<WIN>/`(`capture.pcap`, `tcpdump.err`·`tcpdump.rc`, `decoded.txt`·rc, `report.txt`, `meta.txt` — Worker 쪽은 이권욱이 ansible `fetch`로 수집), 판정 출력(`cutover-capture-judge.py` stdout·rc, 입력 파일 목록), control01 `/root/edge-cutover-<RUN>/`(`*.log`, `k8s-<tag>-*/` K8s Pod 로그 원본·rc(참고)), C0 판정표, PC의 P1·P2·N1 출력(`edge-cutover-<RUN>-n1.txt`), db01 P3·rollback DB 출력, P4 수집기 archive. 비밀 값 없음. pcap은 snaplen 300바이트라 업무 본문 전체가 없다.
- 증적 PR: fds-project-evidence `evidence/P1-SEC-01/edge-cutover-<RUN>/`, 독립 리뷰어 지정. #19 결과 코멘트는 증적 PR 링크와 함께.
- 전달: 작업 폴더 사본이 아니라 `git show <MERGE_SHA>:<경로> > 파일`로 만든 파일을 보낸다. edge01에는 `edge01-cutover.sh`·`cutover-capture.sh`·`cutover-capture-judge.py`를 같은 폴더에 둔다. Worker 캡처용 `cutover-capture.sh`는 ansible01에 같은 방법으로 둔다(이 PC는 `core.autocrlf=true`). edge01은 PC에서 `scp`, control01은 `gzip -9cn | base64 -w 76` 붙여넣기 후 `sha256sum` 대조.
