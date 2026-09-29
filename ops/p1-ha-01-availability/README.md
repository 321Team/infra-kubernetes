# P1-HA-01 Drain 가용성 측정 도구

- 책임: [#22 P1-HA-01](https://github.com/321Team/fds-project-baseline/issues/22) AC "Worker 1대 Drain 중 승인된 가용성 기준을 충족한다" · HA Lead 이재환
- 기준 원문: #22의 가용성 측정 방식·허용 기준 결정안(2026-09-29). 이 디렉터리는 그 결정안 1~6절을 구현한다. 기준을 바꾸려면 결정안부터 다시 승인받는다.
- 상태: 로컬 모의 시험만 수행했다. **운영 실행 전에 이 코드의 독립 리뷰와 결정안 승인(Support·G3)이 모두 필요하다.** 병합·리뷰는 실행 승인이 아니다.

## 파일

| 파일 | 역할 | 실행 위치 |
|---|---|---|
| `probe.py` | 0.5초 고정 스케줄(tick)로 Worker 3대(`.105/.107/.108:30080`)에 `GET /readyz`를 동시에 보낸다. 응답을 기다리지 않는다. 연결 1초·전체 2초 timeout. 한 줄 형식: `tick 예정시각 발사시각 worker 코드 완료시각 소요ms`(epoch 초) | edge01 |
| `judge.py` | probe 로그와 control01 `timeline.txt`로 판정한다. A 정상 Worker 실패 요청 0건, B 성공 응답 최대 간격 1.0초 이하, 측정 무결성(구간 전체 측정·누락 tick 0·발사 지연 0.2초 이하·기준선 3대 200) → `PASS` / `FAIL` / `NOT_VERIFIED` | edge01 또는 로컬 |
| `selftest.py` | 로컬 모의 시험. 127.0.0.2~4:18080에 모의 서버를 띄워 판정 네 가지가 기대대로 나오는지 확인한다 | 로컬만 |
| `SHA256SUMS` | 위 세 파일의 SHA-256 | — |

## 운영 영향 (`probe.py`)

- 서버 설정·데이터를 바꾸지 않는다. 요청은 `GET /readyz`(transaction-api의 DB ping)만 보내며 초당 6건이다.
- Python 3 표준 라이브러리만 쓴다. 설치·상주·cron 등록이 없고, 결과는 실행자 작업 디렉터리에만 쓴다.
- cordon·drain·uncordon과 배치 복구는 이 도구가 하지 않는다. control01에서 kubectl로 따로 하고 시각을 `timeline.txt`에 남긴다.

## 사용

```bash
# edge01 — 기준선 60초 + drain + 재배치 + 60초 + uncordon + 60초를 덮도록 실행 초를 잡는다
python3 probe.py probe.log 600
# 판정 — timeline.txt: baseline_start / drain_start / rescheduled_ready / uncordon / end (epoch 초) 줄
python3 judge.py probe.log timeline.txt 10.1.93.107
```

## 모의 시험

```bash
python3 selftest.py
```

2026-09-29 결과(Windows, Python 3.14.7) — `SELFTEST PASS`:

| 시나리오 | 기대 | 결과 | 주요 값 |
|---|---|---|---|
| 1 정상(1대 중단) | PASS | PASS | A 0건, B 0.523s |
| 2 정상 Worker 1.5초 지연 | FAIL | FAIL | A 0건, B 1.501s |
| 3 생성기가 구간 시작보다 늦게 시작 | NOT_VERIFIED | NOT_VERIFIED | 구간 전체 측정 아님 |
| 4 drain 대상 오지정(정상 Worker 실패와 같음) | FAIL | FAIL | A 12건 |

모의 서버는 로컬 loopback이다. 실제 NodePort·kube-proxy·Local endpoint 동작을 대신하지 않는다.
