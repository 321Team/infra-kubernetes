# CR-P1-MON-01-02 — Discord Webhook Secret 절차 (초안)

> 상태: **DRAFT — 절차 설계만, 실제 발급/생성/회전 미실행**
> 연계: [fds-project-baseline#75](https://github.com/321Team/fds-project-baseline/issues/75)
> 담당: 생성/회전 — 이하영, 설정 인계/검증 — 이재환 (이슈#75 담당 구분 유지)

이 문서는 클러스터·프록시가 아직 준비되지 않은 상태에서도 먼저 정할 수 있는
"규칙"만 다룬다. 실제 Discord Webhook URL 발급, Kubernetes Secret 생성, 최초
회전 실행은 클러스터가 준비되고 Alertmanager 실배포가 확정된 뒤에 진행한다.
이 문서 자체가 Secret을 만들거나 값을 채우지 않는다.

## 1. 생성

- Discord Webhook URL은 대상 채널의 Discord 서버 설정(채널 통합 > Webhooks)에서
  발급한다. URL 형식은 `https://discord.com/api/webhooks/<id>/<token>` — `<token>`
  부분이 사실상 비밀번호와 동일한 민감정보이므로 이하 전 과정에서 평문 노출을
  피한다.
- Kubernetes Secret 이름/네임스페이스: `alertmanager-discord-webhook` /
  `monitoring-api` (기존 `grafana-admin-credentials` 등과 동일 네이밍 관례).
- 생성 방식은 SEC-01의 `scripts/create-secrets-safe.sh` 패턴을 그대로 따른다
  (`p1-k8s-01/p1-sec-01-revision` 브랜치 참고):
  - URL을 커맨드라인 인자(`--from-literal`)로 넘기지 않는다 — `ps`/bash
    history/로그에 남는 경로이므로 금지.
  - 사전에 준비한 권한 600 파일에서 `--from-file`로 읽어 생성한다.
  - `umask 077`, xtrace(`set -x`) 실행 자체를 스크립트에서 거부, `trap cleanup
    EXIT INT TERM`으로 임시 파일 폐기까지 기존 스크립트의 안전장치를 재사용한다.
- Alertmanager 설정(`alertmanager.yml`)의 `discord_configs.webhook_url`은
  Secret 참조(`secretKeyRef` 또는 마운트된 파일 경로)로만 넣는다. ConfigMap이나
  평문 YAML에 직접 기입하지 않는다.

## 2. 회전

- 주기: **90일**을 초안 후보로 제안한다 (다른 확정값 없음 — 최종 주기는 운영
  부담과 Discord 측 정책을 보고 조정 가능).
- 트리거: 담당자(이하영)가 주기 도래 시 수동으로 신규 Webhook을 발급하고
  Secret을 교체한다. 자동 회전은 이번 범위에 포함하지 않는다.
- 절차: 신규 Webhook 발급 → 신규 값으로 Secret 교체(`kubectl apply` 또는 동일
  이름 재생성) → Alertmanager Pod 재시작/reload → 신규 Webhook으로 테스트
  알림 1건 발송 확인 → 확인 후에만 구 Webhook을 Discord 측에서 삭제.
- 구 값 삭제를 신규 값 검증 **이전에** 하지 않는다 (검증 중 롤백 경로 확보).

## 3. 폐기 / 유출 대응

- 유출 의심 시 즉시(대기 없이) 아래 순서로 진행한다:
  1. Discord 채널 설정에서 해당 Webhook을 **즉시 삭제**(무효화) — 새 값 준비를
     기다리지 않는다.
  2. 신규 Webhook 발급.
  3. Kubernetes Secret 교체, Alertmanager reload.
  4. 테스트 알림으로 정상 동작 확인.
  5. 유출 경로(어디서 어떻게 노출됐는지) 별도 기록 — 재발 방지용.
- 유출 대응은 "회전"과 절차는 같지만 순서가 다르다: 회전은 "새 값 준비 →
  검증 → 구 값 삭제"이고, 유출 대응은 "구 값 즉시 삭제(무효화) → 새 값 준비"다.
  유출 시엔 구 값이 살아있는 시간을 최소화하는 쪽을 우선한다.

## 4. 인계

- 이하영이 위 절차로 Secret을 생성/회전한 뒤, 이재환에게는 **Secret 자체(평문
  URL)가 아니라** Secret 이름/네임스페이스/키 이름과 마지막 회전 일자만 인계한다.
  이재환의 역할("설정 인계/검증")은 Alertmanager 설정이 그 Secret을 올바르게
  참조하는지 확인하는 것이지, URL 원문을 다루는 것이 아니다.
- 회전/폐기 이력은 별도 기록(예: `docs/evidence/CR-P1-MON-01-02/`)에 시각과
  수행자만 남기고 URL 원문은 어디에도 커밋하지 않는다.

## 5. 미확정 사항

- 90일 회전 주기는 후보값이며 Lead 확정 아님.
- 유출 탐지 방법(누가 어떻게 유출을 알아채는지)은 이번 문서 범위 밖 — 별도
  모니터링/감사 설계가 필요하면 후속 과제로 분리한다.
- 이 문서는 클러스터·Alertmanager 실배포가 없는 상태에서 작성됐으므로, 실제
  Secret 참조 방식(`secretKeyRef` vs volume mount)은 Alertmanager 실배포
  매니페스트가 나온 뒤 재확인한다.
