# P1-SEC-01 AC-04 보완 시험 수집기 — 관리/승인 Source → Worker 9100/9101/10250 차단 확인 (v2)

- 책임: [#19 P1-SEC-01](https://github.com/321Team/fds-project-baseline/issues/19) AC "NodePort/9100/10250 HEP/GNP PASS"의 기존 증적 공백(관리 Source `ansible01`, 승인 Source `edge01` 출발의 Worker 9100/9101/10250 차단). 근거: [AC-04 대응표 5986741272](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986741272), PMO/QA [딥리뷰 5986838826](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986838826)(B-EXTRA-1·B-EXTRA-2, 이 경로 지정).
- 상태: **이 PR의 병합·로컬 시험 PASS는 Runtime 실행 승인이나 AC credit이 아니다.** 실행 여부는 PMO/QA가 이 exact SHA를 보고 재판정한다. 서버에서는 한 번도 실행하지 않았다.

## 이 도구가 하는 것

TCP 접속 시도만 한다(설정 변경·Pod 생성·임의 포트 없음). Source 하나에서 Worker 3대(`10.1.93.105/.107/.108`)로 **정확히 12행**: Worker마다 대조 1(mgmt `:22`, edge `:30080`) + 차단 기대 3(`:9100`, `:9101`, `:10250`). 접속 제한 3초.

| 실행 위치 | 명령 | 기대 Source |
|---|---|---|
| ansible01 | `bash collect-ac04-extra-negative.sh mgmt` | hostname `ansible01`, route src `10.250.10.10` |
| edge01 | `bash collect-ac04-extra-negative.sh edge` | hostname `edge01`, route src `10.1.93.50` |

## v2가 고친 것(딥리뷰 B-EXTRA-1·B-EXTRA-2)

| 지적 | v2 |
|---|---|
| 실패·미검증·시험 누락이 rc=0 | 종료 코드를 판정에 연결: **0**=유효한 차단 확인만, **10**=INCONCLUSIVE(대조 불충족·REFUSED·ERROR), **11**=FAIL(차단 기대인데 CONNECTED), **12**=INCOMPLETE(행 누락·중복·쓰기·봉인 실패), **2**=STOP(접속 전 중단) |
| 대조 실패여도 같은 Worker의 TIMEOUT이 PASS | 같은 Worker의 대조가 CONNECTED가 아니면 그 Worker의 TIMEOUT은 **관측값으로만 보존**하고 유효한 차단으로 세지 않는다(INCONCLUSIVE). worker03 edge 대조의 Local endpoint 부재는 알려진 한계([#15](https://github.com/321Team/fds-project-baseline/issues/15#issuecomment-5980422387))로 기록하되 다른 Worker 성공으로 승격하지 않는다 |
| 행 수를 출력으로만 확인 | 정확한 source×대상×포트 집합(12행: 대조 3 + 차단 9)과 중복·누락을 코드가 검사 |
| Source·범위 결속 없음 | `hostname -s`와 Worker별 `ip -4 route get`의 `src`를 예상값과 대조하고 다르면 **접속 전 STOP**. 대상·포트·제한시간·대조 포트는 코드에 고정(환경변수로 바꿀 수 없음). 문자열을 `bash -c`에 합치지 않고 위치 인자로 전달 |
| 짧은 실패를 전부 REFUSED로 분류 | stderr와 rc를 보존하고 `Connection refused`만 REFUSED, 그 외(`No route` 등)는 ERROR |
| 파일 쓰기·봉인 실패가 성공으로 끝남 | 쓰기·`SHA256SUMS`·`tar`·sidecar 실패는 INCOMPLETE(rc=12) |
| collector 성공과 AC 판정이 한 필드 | `COLLECTOR_STATUS`(COMPLETE/INCOMPLETE)와 `AC_VERDICT`(BLOCKED_VALID/FAIL/INCONCLUSIVE/NOT_EVALUATED)를 `99-summary.txt`에 분리해 출력. AC 판정은 PMO/QA 몫이며 이 값은 그 입력일 뿐이다 |

## 시험

`bash test-ac04-collector.sh ./collect-ac04-extra-negative.sh` — `hostname`·`ip`·`timeout`·`tar`를 PATH 스텁으로 바꾼 로컬 시험이며 네트워크·서버에 접근하지 않는다. **25 PASS / 0 FAIL**(`validation/local-test-results.txt`). 다룬 경우: 정상(mgmt·edge), 모든 접속 CONNECTED, 대조까지 전부 TIMEOUT, 환경변수로 범위 변경 시도(무시됨), route src 불일치·hostname 불일치(접속 호출 0회), REFUSED, No route(ERROR), worker03만 대조 실패, tar 봉인 실패, `/dev/tcp` 미지원 bash, 잘못된 인자.

## 한계

- `TIMEOUT`은 Calico 정책과 상위 방화벽(mgmt-gw01 등) 중 **차단 주체를 구분하지 못한다.** 구분을 주장하지 않는다. 이 시험은 차단 기대 Source·포트의 도달 불가를 한 시점에서 관측할 뿐이다.
- 로컬 스텁 시험이며 실제 `ip route get` 출력 형식과 대상 서버의 bash `/dev/tcp` 동작은 서버에서 검증되지 않았다(형식이 어긋나면 접속 전에 STOP 한다).
- 짧은 TCP 접속이라도 DROP counter·pcap에 섞일 수 있다. #10 Fresh Gate/capture 구간, #13 AC-03 등 관련 증적 수집과 **겹치지 않는 시간**에 실행한다(PMO/QA 요구).
- LB(`10.1.93.109`) 출발 시험은 이 도구의 범위가 아니다. LB 접속 가능 여부의 읽기 전용 확인과 불가 사유 기록은 별도로 한다(`NOT_VERIFIED/BLOCKED` 근거이며 차단 PASS를 대체하지 않는다).
