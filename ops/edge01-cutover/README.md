# Edge Cutover — edge01 nginx upstream 전환 (변경안·창 계획)

책임 티켓: [baseline#19](https://github.com/321Team/fds-project-baseline/issues/19) P1-SEC-01 edge01 upstream 이관(edge01 서버 담당 이재환). 근거: Review Owner 조건 [6012871977](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6012871977) §2~§4, 9/29 Gate [5883374181](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5883374181), 일정 제안 [6034620578](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6034620578). 후속: [#47 DB 변경 창](https://github.com/321Team/infra-db/pull/7)(Cutover 완료가 선행조건), #20 edge 전체 경로 회귀.

이 PR은 코드·계획 검토용이다. 병합·리뷰 승인은 실행 승인이 아니다. 실행은 이권욱의 창 GO 뒤에만 한다.

## 1. 범위

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
5. 표식 관측이 양쪽에서 실제로 되는지 C0에서 확인됨: edge01 `check`가 `TRACE_READY`(edge 경유 표식이 E-K3S 로그에 1번), control01 `trace <RUN> check<HHMMSS>`가 `TRACE_READY`(Worker 직접 표식 3건이 각 Worker Pod 로그에 1번). 둘 중 하나라도 아니면 C1을 시작하지 않는다.

## 4. 창 계획 (제안: 10/10(토) 오전, 이어서 오후 #47 창)

`RUN`은 같은 값(예: `20261010T1000`). 실행자: 이재환(edge01 담당, 권욱 [6040617837](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6040617837) 수용). P4 수집 지원: 이권욱(ansible01 `svc_ansible` 경로, 같은 코멘트). control01·PC 관측은 이재환.

| 단계 | 상한 | 호스트 | 명령 | 기대 / 성공 기준 | 실패 시 |
|---|---|---|---|---|---|
| C0 사전 | 15분 | control01 | `sudo bash control01-cutover-check.sh pre <RUN>` | 수집 완료(`RESULT …=COLLECTED`). **판정은 아래 C0 판정표에 사람이 기록** | 시작 안 함 |
| | | edge01 | `bash edge01-cutover.sh check <RUN>` | C0 판정표 edge01 행, `RESULT cutover-check=TRACE_READY`(출력의 tag `check<HHMMSS>` 기록) | 시작 안 함 |
| | | control01 | `sudo bash control01-cutover-check.sh trace <RUN> check<HHMMSS>` | `RESULT control01-trace=TRACE_READY` | 시작 안 함 |
| C1 전환 | 15분 | edge01 → control01 | `bash edge01-cutover.sh apply <RUN>` → `sudo bash control01-cutover-check.sh trace <RUN> apply` | edge01 종료 0·`RESULT cutover-apply=EDGE_OK`(① 설정 반영 ② 표식 요청 9/9 200·smoke 201·`/metrics` 404 ③ E-K3S 로그에 apply 표식 0건) **그리고** control01 `RESULT control01-trace=TRACED`(④ 표식 1~9·smoke 10개가 K8s Pod 로그에 각 1번). 둘 다여야 C1 완료 | edge01 종료 3: `rollback` / 종료 4 또는 control01 `UNCONFIRMED`·`ERR`: 완료로 기록하지 않고 실행자·Review Owner가 즉시 `rollback` 또는 보완 관측을 판단 |
| C2 사후 | 20분 | PC·db01·control01·ansible01 | 아래 확인 시험 | 전부 기대값 | `rollback` |
| C3 관찰 | 30분 | — | 5분 간격 PC `/readyz`, MON | 200 유지, MON 이상 없음 | `rollback` |

- 전체 상한 T0+75분, 복구 완료 목표 T0+90분. 상한을 넘기면 되돌리고 멈춘다(연장 없음).
- `apply`는 파일을 바꾸기 전에 모든 게이트를 본다. 새 설정이 `nginx -t`를 통과하지 못하면 사본으로 즉시 되돌리고 reload하지 않는다.
- **"9/9 HTTP 성공", "설정 반영", "실제 새 upstream 경유"를 따로 증명한다.**
  - 설정 반영: reload 뒤 정리 중인 옛 worker(`shutting down`)를 빼고, 현재 worker가 모두 설정 파일 최종 수정 이후에 시작됐는지 본다. 5초 간격으로 최대 7번(약 35초) 다시 본다. 끝내 확인하지 못하면 종료 4.
  - 실제 경유: 요청마다 고유 표식 `?cutover=cutover-<RUN>-<tag>-<번호>`를 붙인다(transaction-api는 모르는 쿼리를 무시). uvicorn 접근 로그에 경로·쿼리·client 주소가 남으므로, **표식이 어느 backend Pod 로그에 몇 번 찍혔는지**로 요청과 backend를 결속한다. 새 경로는 control01 `trace`가 K8s Pod 로그를, 옛 경로는 edge01이 E-K3S Pod 로그를 본다.
  - 로그 조회는 Pod마다 rc·stderr를 따로 남기고(회차별 새 폴더), **하나라도 실패하면 판정 불가(ERR·종료 4)**다. 0건 정상과 조회 실패를 합치지 않는다.
  - 표식은 RUN·tag·번호로 정해지고 check·rollback tag에는 실행 시각(`HHMMSS`)이 붙어, 이전 요청이나 다른 회차가 섞이지 않는다. 기대값은 "각 번호가 정확히 1번"이다(누락·중복 모두 실패).
  - 사전 검증: C0에서 같은 방법으로 양쪽 로그에 표식이 찍히는지 먼저 확인한다(로그 형식·수록 여부를 가정하지 않음).
  - control01 `trace apply`의 `source_50=yes`(모든 표식 줄의 client가 `10.1.93.50`)는 출발지 보존의 보조 관측으로 기록한다. P4를 대체하지 않는다.
  - 순간 소켓 목록(`ss`), 패킷 수, 사전 직접 curl은 판정에 쓰지 않는다.

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
| 표식 관측(E-K3S) | edge01 `check` → `TRACE_READY` | | |
| 표식 관측(K8s) | control01 `trace <RUN> check<HHMMSS>` → `TRACE_READY`(w105→worker01, w107→worker02, w108→worker03 각 1번) | | |

하나라도 기대와 다르거나 판정 불가면 C1을 시작하지 않는다.

### 확인 시험 (C2)

| 구분 | 시험 | 명령 | 기대 |
|---|---|---|---|
| P1 | edge 경로 | PC `curl -s -o /dev/null -w '%{http_code}' http://10.1.93.50/readyz` | 200 |
| P2 | `/metrics` 비노출 | PC 같은 명령으로 `/metrics` | 404 |
| P3 | 업무 smoke DB 행 | C1 `apply`가 보낸 `SIM_cut_<RUN에서 T 제외>_apply` 1건을 db01에서 `SELECT fds_detected, fds_evaluation_skipped FROM transactions WHERE account_id = 'SIM_cut_<…>_apply'` | 행 1, `false`/`false` |
| P4 | source `.50` 보존 | evidence#86과 같은 수집기(infra-ansible `c0d784fa`, collector `42fc3ed4…`) 1회, 새 Pod 기준. 실행 이권욱 | Worker 3대 Pod에서 source `10.1.93.50` |
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

- 중단 기준: edge `/readyz` 실패, 업무 smoke 실패·5xx, N1 FAIL, MON 이상, 상한 초과, C1 종료 4를 실행자·Review Owner가 수용하지 않음.
- 복구: `bash edge01-cutover.sh rollback <RUN>`. apply 직전 사본으로 복원(`cat >`로 inode·권한·문맥 유지) → `nginx -t` → reload → 디스크 유효 upstream `.50:30080` → 설정 반영(약 35초) → 표식 요청 3회 200·표식 smoke 201 → **E-K3S Pod 로그에서 이번 rollback 표식 1~3·smoke가 각 정확히 1번**(조회 실패는 종료 4). 이 모두가 맞아야 `RESULT cutover-rollback=OK`(종료 0)다. 보조로 control01 `trace <RUN> rollback<HHMMSS>`가 `NONE_OK`(K8s 쪽 0건)인지 본다. 반영·경유를 확인하지 못하면 종료 4(UNCONFIRMED)이며 복구 완료로 기록하지 않는다.
- 쓰지 않은 파일 SHA나 HTTP 200만으로 복구를 선언하지 않는다(새 Worker 경로도 200을 낸다).
- E-K3S·옛 DB 허용(pg_hba `.50`)은 Cutover 창에서 바꾸지 않으므로 rollback 경로는 #47 창 W1 전까지 유지된다. `rollback`은 k3s가 active가 아니면 시작하지 않는다.

## 6. #47 창과의 관계

- #47 창(infra-db `ops/p2-hyb01-db-window/`, 병합 `d14d4597`)은 Cutover `RESULT cutover-apply=OK`, C2 통과, C3 관찰 종료 뒤에만 시작한다. #47 W1 `edge01-k3s.sh stop`도 같은 조건(유효 upstream에 `.50:30080` 0, Worker 3/3, reload 반영, 옛 NodePort ESTABLISHED 0)을 다시 확인한다.
- Cutover 뒤 #47 W1 전까지는 E-K3S가 켜져 있지만 edge 트래픽은 받지 않는다.
- **#47 W1(k3s 중지) 이후에는 이 `rollback`만으로 복구되지 않는다.** 옛 경로가 사라졌고(k3s), W2·W3 뒤에는 `.50`의 DB 접근, W4 뒤에는 E-K3S Secret의 비밀번호도 맞지 않는다. 그때 edge 경로를 되돌려야 하면 #47 README §4의 역순 복구(W4 → W1)를 먼저 하고 이 `rollback`을 실행한다. 두 창 사이에 Cutover 결과·RUN·사본 위치를 #47 실행 기록 첫머리에 인계한다.

## 7. 알려진 차이·잔여 (이번 창에서 바꾸지 않음)

- **정본 D18은 edge01 HAProxy**를 고정하지만 실제 edge01은 nginx(9/29 `nginx/1.26.3`)다. 기존 차이이며 upstream-only 창에서 프록시 소프트웨어를 바꾸지 않는다. 처분은 #19·#30에서 판단한다.
- **`X-Forwarded-For`**: 정본은 실제 Client 주소를 XFF로 전달하도록 요구한다. 현재 `proxy_set_header` 유무는 C0 `check` 출력으로 기록만 한다(transaction-api는 요청 본문의 `source_ip`를 쓴다).
- **backend 장애 동작**: nginx 기본값(`max_fails=1`, `fail_timeout=10s`, `proxy_next_upstream error timeout`, `proxy_connect_timeout 60s`)을 쓴다. local endpoint가 없는 Worker는 즉시 거부(RST)라 다음 backend로 넘어가지만, Worker 호스트가 응답 없이 멈추면 첫 요청이 최대 60초 지연될 수 있다. 타임아웃 조정은 별도 변경으로 다룬다.

## 8. 원본 수집·파일 전달

- edge01 `~/edge-cutover-<RUN>/`(로그, `upstream-file.before/after`, `nonces-*.txt`, `ek3s-<tag>-*/` E-K3S Pod 로그 원본·rc, `smoke-*.json`), control01 `/root/edge-cutover-<RUN>/`(`*.log`, `k8s-<tag>-*/` K8s Pod 로그 원본·rc), C0 판정표, PC의 P1·P2·N1 출력(`edge-cutover-<RUN>-n1.txt`), db01 P3 출력, P4 수집기 archive. 비밀 값 없음.
- 증적 PR: fds-project-evidence `evidence/P1-SEC-01/edge-cutover-<RUN>/`, 독립 리뷰어 지정. #19 결과 코멘트는 증적 PR 링크와 함께.
- 전달: 작업 폴더 사본이 아니라 `git show <MERGE_SHA>:<경로> > 파일`로 만든 파일을 보낸다(이 PC는 `core.autocrlf=true`). edge01은 PC에서 `scp`, control01은 `gzip -9cn | base64 -w 76` 붙여넣기 후 `sha256sum` 대조.
