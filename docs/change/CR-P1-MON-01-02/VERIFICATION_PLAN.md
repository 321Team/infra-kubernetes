# CR-P1-MON-01-02 — 검증 계획 (초안)

> 상태: **DRAFT — 시나리오 설계만, 실행 전**
> 연계: [fds-project-baseline#75](https://github.com/321Team/fds-project-baseline/issues/75)

이 문서는 클러스터·egress-proxy01·Alertmanager 실배포가 모두 준비된 뒤
실행할 시험 목록이다. 지금은 "무엇을 확인해야 하는가"만 정하며, 실제
실행·PASS/FAIL 판정은 이 문서가 하지 않는다. `docs/change/CR-P1-MON-01-02/README.md`의
선행조건(클러스터, podSelector, 프록시 IP:port, Secret)이 전부 해소된 뒤 착수한다.

## 실행 전제

- Alertmanager Pod가 `alertmanager-egress-proxy-and-dns` NetworkPolicy 적용 하에
  Ready 상태.
- egress-proxy01의 Squid가 실제 3128 리스너로 기동 중이고 허용 클라이언트
  목록이 실측값으로 확정됨.
- `SECRET_PROCEDURE.md` 절차로 Discord Webhook Secret이 생성·연결됨.

## 1. Allow 흐름 (Positive)

| # | 시나리오 | 기대 결과 | 판정 기준 |
|---|---|---|---|
| A1 | Alertmanager에서 egress-proxy01:3128로 TCP 연결 | 연결 성공 | `nc`/`curl`로 연결 성공, NetworkPolicy가 이 경로를 막지 않음 확인 |
| A2 | 실제 Alert 1건을 발생시켜 Discord 채널에 도착하는지 확인 | 채널에 메시지 도착 | Discord 채널에서 육안 확인 + Alertmanager 로그의 발송 성공 기록 |
| A3 | kube-dns로 정상 DNS 조회(예: `discord.com` resolve) | 조회 성공 | Pod 내부에서 nslookup/dig 성공 |

## 2. Deny 흐름 (Negative)

| # | 시나리오 | 기대 결과 | 판정 기준 |
|---|---|---|---|
| D1 | Alertmanager에서 egress-proxy01 이외의 임의 외부 IP로 직접 연결 시도 | 연결 실패(차단) | NetworkPolicy에 의해 즉시 거부 — timeout이 아니라 정책 차단임을 구분 (SEC-07 재검토에서 지적된 "도구 실패 vs 정책 차단" 오판 주의) |
| D2 | Alertmanager에서 discord.com 외 다른 도메인으로 프록시 경유 시도 | Squid ACL에서 거부 | 프록시 접근 로그에 `TCP_DENIED` 등 거부 기록 확인 |
| D3 | 허용 클라이언트 목록에 없는 다른 Pod/노드에서 프록시 접속 시도 | Squid에서 거부 | 프록시 접근 로그 확인 |

## 3. 장애/복구

| # | 시나리오 | 기대 결과 | 판정 기준 |
|---|---|---|---|
| F1 | egress-proxy01(Squid) 중단 중 Alert 발생 | Alertmanager가 재시도하거나 에러를 명확히 로그로 남김(사일런트 실패 없음) | Alertmanager 로그에서 실패 원인 확인 가능 |
| F2 | Squid 복구 후 대기 중이던/신규 Alert 정상 발송 | 복구 후 정상 발송 재개 | Discord 채널 도착 확인 |
| F3 | Alertmanager Pod 재시작(worker03 재부팅 등) 후 egress 경로 유지 | 재시작 후에도 A1/A2 재현 | 재시작 후 동일 시험 재실행 |

## 4. Secret 노출 검증

| # | 시나리오 | 기대 결과 | 판정 기준 |
|---|---|---|---|
| S1 | Alertmanager Pod 로그에 Webhook URL 평문 출력 여부 | 출력 없음 | 로그 grep으로 URL 패턴 부재 확인 |
| S2 | Squid 접근 로그에 Webhook URL(요청 경로) 평문 기록 여부 | 기록 없음 또는 마스킹 | Squid logformat 설정과 실제 로그 대조 (infra-ansible DR04 대응 내용과 연계) |

## 미정 사항

- 각 항목의 구체적 실행 스크립트/명령어는 미작성 — 클러스터·프록시 실측값이
  나온 뒤 SEC-01의 `scripts/verify-probe-failover.sh` 계열과 동일한 fail-closed
  설계 원칙(도구 부재/timeout을 정책 차단 성공으로 오판하지 않음)을 따라 작성한다.
- PASS/FAIL 외 "도구 문제로 판정 불가" 상태를 구분하는 exit code 체계는 기존
  SEC-01 스크립트 관례(0=PASS, 1=FAIL, 3=NOT VERIFIED 등)를 재사용할지 이번
  검증 전용으로 새로 정할지 미정.
- 실행 주체(누가 이 시험을 돌릴지)와 실행 시점(Alertmanager 실배포 직후 1회성인지,
  주기적 회귀시험인지)은 미정.
