# Phase 1 실제 engine HTTP Probe → 최종 APP 회귀 실행 준비

이권욱 / CODEX_ASSISTED · 2026-10-14 KST. 사용자 `2~4번 착수`에 따른 준비다.
연계: baseline [#19](https://github.com/321Team/fds-project-baseline/issues/19),
[#15](https://github.com/321Team/fds-project-baseline/issues/15),
[#20](https://github.com/321Team/fds-project-baseline/issues/20),
[#11](https://github.com/321Team/fds-project-baseline/issues/11),
[#30](https://github.com/321Team/fds-project-baseline/issues/30).

적용 기준은 병합된 [APP·HA 카드](https://github.com/321Team/fds-project-baseline/blob/b0a02109353616f3142ffd3b25228a8cdda04c8d/governance/handoffs/phase1-execution-20261010/APP_HA_EXECUTION_CARD_20261013.md)와
[HTTP 재사용 범위](https://github.com/321Team/fds-project-baseline/blob/b0a02109353616f3142ffd3b25228a8cdda04c8d/governance/handoffs/phase1-execution-20261010/HTTP_PROBE_REUSE_20261013.md)다.
DB 장애/engine 전체0/격리 exec시험을 반복하지 않는다. 이 한 사례가 MON 나머지5종을 대표하지 않는다.
HA 실제 결과는 [증적221](https://github.com/321Team/fds-project-evidence/pull/221)에서 별도 리뷰 중이다.

## 현재 확인과 실행 경계

02:21~02:22의 순수 조회에서 API3/engine3 모두 Ready, restart0, Worker마다 각1개,
MON23/23·앱6/6, edge→Worker3 readyz200·업무 metrics404를 확인했다.
프로세스 조회는 환경변수나 Secret을 읽지 않았고 신호0회다.
관측 입력은 `observed-binding.json`이며 **미래의 실행 허가가 아니다**.
실행 직전 새 관측에서 아래 식별값이 바뀌면 자동 대상을 찾거나 바꾸지 않고 중단한다.
현재 source는 `engine_stop.py`; 독립 리뷰 전 운영 배치/실행0회다.

| 항목 | 02:22 관측값 |
|---|---|
| node | k8s-worker02 / 10.1.93.107 / boot f0015ee5-36c9-4184-a985-723170958f2c |
| Pod | fds-app / fds-engine-65479b5bf-4gfgq |
| Pod UID | e349b045-870f-4061-a677-3007efe479e9 |
| containerID | 7b24cf1f8132da141a186eb00d7cbd02e30b9d1e28a2dfdb1147376caf544a45 |
| host PID / start ticks | 1290996 / 19229699; container PID1, UID10001 |
| main process | /venv/bin/python3 run.py |
| image | 10.1.93.54/fds/fds-engine@sha256:8b257adb08b6d381f72b66d3386efea6112a8f072ecfafcbcd463bcd1c3be89d |
| HTTP readiness | /readyz:8001, initial5 / period10 / timeout1 / failure3 |
| HTTP liveness | /livez:8001, initial10 / period20 / timeout1 / failure3; grace30 |
| pidfd | Python3.12.14, open 성공·send_signal API 존재; 실제 신호 시험 없음 |

`pidfd`를 열고 **그 후** CRI의 전체 containerID·Pod UID·PID·image,
proc start/cgroup/namespace/UID/명령을 다시 확인한다. 이후 STOP/CONT는 그 fd만 사용한다.
이는 검사와 신호 사이의 PID 재사용으로 다른 프로세스를 멈추는 경로를 차단하기 위한 것이다.
[Python signal](https://docs.python.org/3.12/library/signal.html#signal.pidfd_send_signal),
[Linux pidfd](https://man7.org/linux/man-pages/man2/pidfd_open.2.html).
실제 성공은 아래 kubelet/Endpoint/복구 원본으로 별도 판정한다.

## 제안 시간창 — 미확정

| 단계 | 제안 | 실제 진입 조건 |
|---|---|---|
| Probe1회 | 10/14 03:00~03:10 KST 시작, 03:35까지 복구·수집 | exact HEAD 독립 리뷰/병합, 새 형상/정상성 확인, 해당 창 콘솔·비중첩 확인 |
| APP1회 | 10/14 05:10~05:20 KST 시작, 06:00까지 수집 | Probe/서비스/MON 회복, 새 DB0행, DB 콘솔 조회·writer 배제, 새 baseline |

시작 마감까지 조건이 안 되면 실행0회. 실패·불명확 응답은 자동 재실행하지 않는다.
00:15~01:05의 HA 창·콘솔 확인을 이 창의 확인으로 쓰지 않는다.
Harbor PR62의 01:46~01:54 H6 기록은 당시 복구 기록이며 이번 창의 비중첩 확인이 아니다.
새 Push/Deploy/노드 drain/Eviction/DB 장애 주입은 포함하지 않는다.

## Probe 적용 위치·순서

1. 기존 fds-ansible01→svc_ansible→worker02 sudo 경로만 사용한다. 새 계정/권한/인증 예외0.
   승인 source를 새 root0700 공급 폴더, 파일0600으로 배치한다. Git blob SHA256와 공급 SHA를 대조한다.
   도구 인자의 source hash/time은 일치 검사다. 사람의 승인·독립 리뷰·비중첩을 인증하지 않는다.
2. control01에서 두 Deployment UID/spec/image·6 Pod UID/restart·Node Ready/noPressure,
   target Ready/Endpoint true·나머지 engine2 Ready를 재수집한다. MON23/23, 앱6 target을 PodIP:port와 대조한다.
   worker02에서 boot/CRI/process 입력을 재대조한다. node/Pod/container가 바뀌면 주입0회, 카드 입력 영향 재검토.
3. control01에서 2초 주기로 Pod/EndpointSlice, target UID의 Events를 수집하기 시작한다.
   시작 전 Events의 resourceVersion/count/lastTimestamp와 종료 후 값을 모두 보존한다.
   오래된 누적 event를 이번 liveness 실패로 재사용하지 않는다. 같은 시각 node kubelet 로그도 UID로 연결한다.
   운영자가 다른 engine2 Ready, API3 Ready, 노드 상태를 계속 감시한다. 관측 실패도 중단 조건이다.
4. worker02 root의 유지되는 콘솔/기존 SSH 세션에서 정확한 새 경로로 한 번 실행한다.
   아래 인자 시각은 **제안값**이며, 실제 범위가 확정된 뒤에만 소비한다. `<SOURCE_SHA256>`는 리뷰된 bytes의 값이다.

```bash
python3 -B -I engine_stop.py apply observed-binding.json \
  --source-sha256 <SOURCE_SHA256> \
  --start 2026-10-14T03:00:00+09:00 \
  --latest-start 2026-10-14T03:10:00+09:00 \
  --end 2026-10-14T03:35:00+09:00 \
  --output /var/tmp/p1-http-probe-20261014-0300
```

출력의 `input.json`, `events.jsonl`, claim 디렉터리는 삭제하거나 재사용하지 않는다.
SIGSTOP1회는 engine 업무8001·metrics9090을 함께 멈춘다. Endpoint 제외 전 in-flight 요청은
fail-open/skipped로 저장될 수 있으므로 업무 writer 및 회귀와 겹치지 않게 한다.
readiness false·Endpoint false, liveness 실패/재시작, 같은 Pod UID의 containerID 교체/restart+1을 관측한다.
`period×threshold`를 실제 조치의 절대 상한으로 보지 않는다.
[Kubernetes Probe 의미](https://kubernetes.io/docs/concepts/workloads/pods/probes/).

## 중단·복구·결과

- 주입 후 원 프로세스가 종료되면 도구는 CONT를 보내지 않는다. kubelet의 새 프로세스에는 신호0회다.
- 최대150초 뒤에도 원 프로세스가 살아 있으면 같은 pidfd로 CONT하고 rc2로 종료한다. 자동 재주입0회.
- 다른 engine/API/노드 이상, target 교체·관측 불가·원하지 않은 영향이면 즉시 worker02에서
  `touch /var/tmp/p1-http-probe-20261014-0300/ABORT` 또는 실행 세션 INT/TERM/HUP로 회수를 요청한다.
  ABORT는 약0.2초 폴링이고 중단 신호는 finally에서 원 fd CONT를 시도한다.
- 프로세스 강제KILL·서버 장애·커널/I/O hang·반복 종료 신호에서는 자동 회수를 보장하지 않는다.
  콘솔 인수자가 원 프로세스/클러스터 상태를 확인한다. 원 container가 여전히 동일하고 살아 있으면
  **동일 binding**으로 아래 회수만 수행한다. 종료/교체됐으면 이 명령은 거부하며 대체 PID를 찾지 않는다.

```bash
python3 -B -I engine_stop.py resume-original observed-binding.json --source-sha256 <SOURCE_SHA256>
```

- Pod 강제삭제/rollout/kill/보안 통제 완화는 복구에 포함하지 않는다. 추가 조치가 필요하면 현 상태·원본을 보존하고 콘솔에서 별도 결정한다.
- 원본 프로세스 종료로 rc0이어도 Probe PASS가 아니다. 동일 UID의 Ready/Endpoint false,
  이번 liveness 실패→kubelet 재시작 사건, 새 container/restart+1, HTTP 정상 회복을 모두 대조한다.
- 종료 확인: engine3/API3 Ready·각 Worker1/1, intended restart 외 변화0, Node 정상,
  edge Worker3 readyz200·업무 metrics404, 정확한 앱6/전체23 target 정상 **60초 연속**.
  03:35까지 원본/복구 확인이 안 끝나면 실패/미확인으로 보존하고 APP 단계로 승격하지 않는다.

## APP 회귀 준비·DB 콘솔 인계

db01(.55)의 svc_ansible SSH는 02:21에 publickey 인증 거부(rc255)였다. DB SQL은 실행되지 않았다.
기존 확인된 `server08 + sudo` 콘솔/`postgres` peer 경로를 사용하며 비밀번호·키를 요청하거나
애플리케이션 Secret을 꺼내 우회하지 않는다. SQL 파일 배치/조회는 그 기존 콘솔에서 수행한다.
현재 schema/DB 권한/새 RUN0행은 **미확인**이다. 이는 APP 전송의 필수 선행 입력이다.

1. [사전 SQL](app-db-before.sql): db01 식별·fdsdb·8열 관련 schema를 확인하고, 확정된 RUN 분 범위가0행인지 조회한다.
   제안 범위 `202610140510`~`202610140520`; 창이 바뀌면 새 범위로 재조회한다.
   실행 직전 시각/콘솔 작성자·rc·COUNT0 원본을 수신하기 전 POST0회다.
2. Probe 회복 후 control01에서 Pod UID/restart/image와 PromQL6개 원본을 새 baseline으로 수집한다.
   `sum by(rule_id)(fds_detected_total)`의 비어 있는 vector는 조회 오류/targetdown과 구분한다.
   source의 Counter는 `.labels(...).inc()` 때 자식이 생성된다. 해당 metric 정의·target 정상·이전 탐지0을
   확인한 경우에만 아직 생성되지 않은 Rule의 시작값을0으로 해석하고 원 vector도 보존한다.
3. edge01에서 **기존 승인된 rg.py bytes를 변경 없이** 새 공급/결과 경로에 사용한다.
   SHA256 `f5dee1069ab96838355cf6269dd6cdff2a08107e880594c5dad98d20424594ed`.
   후보 source/image는 현재 배포 digest를 유지한다. 같은 KST 날짜05:00 이후여야 한다.
   `python3 rg.py run <새 OUT_DIR>`1회 → 즉시 rc·25요청 원본/meta/criteria 보존.
   실제 meta RUN이 사전0행 범위 안인지 대조한다. HTTP0/5xx는 남은 전송 중단, 자동 재실행0.
4. [사후 SQL](app-db-after.sql)은 실제 RUN으로 별도 실행한다. `-A -t -F '|'`의 8열 원본/시각/rc를 수신하여
   `python3 rg.py crosscheck <OUT_DIR> <DB_LOG>`로 실제 Transaction ID를 대조한다. 기존/신규 거래 DELETE0.
5. 의미 판정24PASS·0FAIL·I01 INFO1, 정상 저장22행(I01이201이면23행), skipped=false,
   save_success/evaluated+22 또는+23, save_failure/missing_client_ip0, R01/R02/R07+1,
   R04는 I01 실제결과에 따라+1/+2, 앱 target6 정상 및 Pod UID/restart 변화0을 함께 확인한다.
   scrape 반영 후 값이 안정된 원본을 수집하며 다른 writer의 aggregate증가를 이 RUN으로 귀속하지 않는다.
6. control01→Worker NodePort의 N08 관련 기존 차단 근거와 이번 최종 형상의 대조 범위를 기록한다.
   edge→NodePort 정상만으로 Client→LB→edge 전체 경로/다른 Namespace 전수를 완료 처리하지 않는다.

### db01 명령 예시 (창/경로 확정 후)

```bash
# db01 / server08 console; script와 로그는 기존 비공개 작업 경로에 보존.
date --iso-8601=seconds
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d fdsdb \
  -v run_first=202610140510 -v run_last=202610140520 -f app-db-before.sql
printf 'DB_BEFORE_RC=%s\n' "$?"
# 사후: RUN은 edge meta.json에서 수신한 실제 값으로 지정.
sudo -u postgres psql -X -A -t -F '|' -v ON_ERROR_STOP=1 -d fdsdb \
  -v run=<ACTUAL_RUN> -f app-db-after.sql
printf 'DB_AFTER_RC=%s\n' "$?"
date --iso-8601=seconds
```

`sudo -u postgres`가 읽을 수 있는 기존 경로를 사용한다. 상대 파일 위치/접근이 확인되지 않으면
실행 전 콘솔에서 바로잡고 원본을 덮어쓰지 않는다. 권한을 포괄 허용하지 않는다.
두 SQL은 BEGIN READ ONLY, 5초 statement_timeout이며 실패 시 요청을 진행하지 않는다.

## 검증과 인수 경계

`python3 -B -m unittest discover -s ops/p1-http-probe/tests -v`:
PID 교체/이미 종료/이미 중단/시간창/로그 실패 회수 MOCK와 Linux의 자체 child STOP/CONT/중단/종료 시험.
Windows는 Linux4개를 skip하며 실제 Linux 결과는 CI로 별도 보고한다. CI는 클러스터에 접근하지 않는다.
운영 Probe RUNTIME/DB SQL/APP POST는 미실행이다. 준비·CI/리뷰/병합을 운영 성공으로 합산하지 않는다.
APP 결과 후 DB#11 인수 판단이라는 사용자 결정을 유지한다. W4 전체 HTTP5xx NOT_MEASURED,
새 패킷 시험/다른 Namespace 전수 미검증도 보존한다. 티켓 종결·전체 Phase1 인수는 별도다.
