# P1-HA-01 Drain 가용성 측정 도구

- 책임: [#22 P1-HA-01](https://github.com/321Team/fds-project-baseline/issues/22) AC "Worker 1대 Drain 중 승인된 가용성 기준을 충족한다" · HA Lead 이재환
- 기준 원문: [#22 결정안](https://github.com/321Team/fds-project-baseline/issues/22#issuecomment-5887966702)(측정 방식·판정 기준). 이 디렉터리는 그 1~6절을 구현한다. 기준을 바꾸려면 결정안부터 다시 승인받는다.
- 상태: 로컬 모의 시험만 수행했다. **운영 실행 전에 이 코드의 독립 리뷰와 결정안 승인(Support·G3)이 모두 필요하다.** 병합·리뷰는 실행 승인이 아니다.

## 파일

| 파일 | 역할 | 실행 위치 |
|---|---|---|
| `probe.py` | 0.5초 고정 스케줄(tick)로 Worker 3대(`.105/.107/.108:30080`)에 `GET /readyz`를 동시에 보낸다. 응답을 기다리지 않는다. 연결 1초·전체 2초 timeout. 한 줄 형식: `tick 예정시각 발사시각 worker 코드 완료시각 소요ms`(epoch 초) | edge01 |
| `judge.py` | probe 로그와 control01 `timeline.txt`로 판정한다. timeline 순서·60초 구간을 먼저 검사하고, A 정상 Worker 실패 요청 0건, B 성공 응답 최대 간격 1.0초 이하, 측정 무결성(구간 전체 측정·누락 tick 0·발사 지연 0.2초 이하·기준선 3대 200)으로 `PASS` / `FAIL` / `NOT_VERIFIED`를 낸다 | edge01 또는 로컬 |
| `selftest.py` | 로컬 자체시험(모의 서버 실제 실행 + 합성 로그). 운영 서버에서 실행하지 않는다 | 로컬만 |
| `SHA256SUMS` | 위 세 파일의 SHA-256 | — |

## fail-close

**`probe.py`**
- 동시 요청(in-flight)을 직접 센다. 다음 tick을 넣으면 상한(기본 24)을 넘는 경우 새 요청을 넣지 않고 `STOP LOAD_GENERATOR_SATURATED`를 출력한 뒤 **종료 코드 2**로 끝낸다. 측정이 끊기므로 판정은 NOT_VERIFIED다.
- 실행 스레드 수 = 상한이라 executor 대기열에 요청이 쌓이지 않는다. 부하가 계획한 구간을 넘어 이어지지 않는다.
- 정상이면 in-flight는 timeout 2초 ÷ 0.5초 × 3대 ≈ 최대 15건이다.
- 끝날 때 남은 요청을 최대 5초 기다리고 `END … outstanding N`을 출력한다. N이 0이 아니면 **종료 코드 3**이다.

**`judge.py`**
- 판정 전에 timeline을 검사한다.
  - 순서: `baseline_start < drain_start < rescheduled_ready <= uncordon < end`
  - 결정안 2절의 60초 구간 세 개: 기준선, 재배치 Ready 뒤, uncordon 뒤
  - 어긋나면 NOT_VERIFIED다.
- B는 판정 구간 안에서 끝난 성공 응답만 센다.
- 종료 코드: 0 PASS, 1 FAIL, 2 NOT_VERIFIED.

## 운영 영향 (`probe.py`)

- 서버 설정·데이터를 바꾸지 않는다. 요청은 `GET /readyz`(transaction-api의 DB ping)만 보내며 초당 6건이다.
- Python 3 표준 라이브러리만 쓴다. 설치·상주·cron 등록이 없고, 결과는 실행자 작업 디렉터리에만 쓴다.
- cordon·drain·uncordon과 배치 복구는 이 도구가 하지 않는다. control01에서 kubectl로 따로 하고 시각을 `timeline.txt`에 남긴다.
- 각 관찰 구간은 60초 기준보다 조금 길게(예: 61초) 둔다. 판정기는 60초 미만을 NOT_VERIFIED로 본다.

## 사용

```bash
# edge01 — 기준선 60초 + drain + 재배치 + 60초 + uncordon + 60초를 덮도록 실행 초를 잡는다
python3 probe.py probe.log 600 > probe.stdout; echo "probe rc=$?"
# 판정 — timeline.txt: baseline_start / drain_start / rescheduled_ready / uncordon / end (epoch 초) 줄
python3 judge.py probe.log timeline.txt 10.1.93.107; echo "judge rc=$?"
```

## 자체시험

```bash
python3 selftest.py
```

2026-09-29 결과(Windows, Python 3.14.7) — `SELFTEST PASS (15/15)`:

| 시험 | 기대 | 결과 |
|---|---|---|
| P1 probe 정상(1대 중단) | 종료 코드 0, outstanding 0, 모든 tick 3건, 발사 지연 < 0.2초 | OK(최대 발사 지연 0.004초) |
| P2 probe 포화(3대 1.5초 지연, 상한 4) | 종료 코드 2, STOP 뒤 요청 0건, 판정 NOT_VERIFIED | OK(STOP tick 1, 이후 요청 0건) |
| J1 정상 | PASS | OK |
| J2 정상 Worker 2대 1.5초 지연 | FAIL(B) | OK |
| J3 정상 Worker 000 1건 | FAIL(A) | OK |
| J4 생성기 늦은 시작 / J5 발사 지연 0.3초 / J6 tick 누락 / J7 기준선 비200 | NOT_VERIFIED | OK |
| J8 기준선 59초 / J9 재배치 Ready 뒤 59초 / J10 uncordon 뒤 59초 / J11 순서 역전 | NOT_VERIFIED | OK(수정 전 판정기는 넷 다 PASS) |
| J12 판정 구간 끝 뒤에 완료된 성공 | B에서 제외 → PASS | OK(수정 전 판정기는 FAIL) |

모의 서버는 로컬 loopback이다. 실제 NodePort·kube-proxy·Local endpoint 동작을 대신하지 않는다. J 시험은 결정안 길이(각 관찰 구간 61초)의 합성 로그다.
