# P1-SEC-01 AC-03 can-i 보완 수집기 (v3-20261006) — RC가 검증되지 않은 행만 읽기 전용으로 재수집

- 책임: [#19 P1-SEC-01](https://github.com/321Team/fds-project-baseline/issues/19) AC-03(SA별 `kubectl auth can-i` Positive/Negative). 근거: Review Owner [딥리뷰 6010258709](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6010258709) §3(수집기 stdout/RC 신뢰성, "36건" 정정, 증적이 없는 행만 보완, 현재 `prometheus-sa get /metrics` 연결), 이전 [PARTIAL 판정 5995864744](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5995864744)(B19-RBAC-01·02).
- 상태: **이 PR의 병합·로컬 시험 PASS는 Runtime 실행 승인이나 AC credit이 아니다.** 서버에서는 한 번도 실행하지 않았다. 실행 시점은 #10 CP 복구 회차와 겹치지 않게 별도로 정한다.

## 왜 필요한가

#107 증적(`collect-cani-core36b.sh`, 2026-10-04)은 `kubectl auth can-i`의 stdout에서 `grep/head`로 yes/no만 뽑아 **종료코드(RC)와 stderr를 버렸다.** `yes`를 출력하고 비정상 종료해도 PASS로 기록될 수 있어 신뢰성을 증명하지 못한다. 기존 frozen 증적은 수정하지 않고, 부족한 행만 새 수집기로 보완한다.

## 행 대조 (정정: "36건"이 아니라 90행)

`core36`의 3·6은 요구사항 번호이다. #107 수집 표는 **node-exporter-sa 28 + monitoring-node:default 28 + Runner 34 = 90행**이다.

| 주체 | #107 행 | #49 `verify-rbac.sh`(yes=rc0/no=rc1 짝·timeout 검증)와 동일 항목 → 재수집 제외 | 보완 수집 |
|---|---|---|---|
| `ci-cd:cicd-runner-sa` | 34 | 9 | **25** (Tier A) |
| `monitoring-node:default` | 28 | 0 | **28** (Tier A) |
| `monitoring-node:node-exporter-sa` | 28 | 0 | 28 (Tier B) |
| 합계 | 90 | 9 | 81 (A 53 + B 28) |

- 제외 9행(#49, `ci-cd:cicd-runner-sa`): create/delete deployments, update deployment/transaction-api(yes), update deployment/other-app(no), get·list secrets, create pods/exec, create rolebindings, `* *`(fds-app 또는 클러스터).
- **Tier A(기본, 53행)**: 권욱님이 우선 지정한 행 — Runner의 두 Deployment별 get/update/patch, 다른 대상 거부, 기타 kind·권한 상승·타 Namespace 거부, `monitoring-node:default`의 필수 부정 권한 전체.
- **Tier B(`TIER=AB`)**: node-exporter-sa. #49에는 `list pods -A` 1행만 있어 RC 검증 동등 행이 없다. 우선순위 밖이라 기본 제외.
- 행 목록은 #107 `results.tsv`에서 기계적으로 생성해 스크립트에 고정했다(수집 시 환경변수로 바꿀 수 없음). 기대값은 #107과 같다.
- 추가 기록(기대값 없음, `RECORD`): `prometheus-sa`의 non-resource `get /metrics`와 `get nodes --subresource=metrics`(둘은 서로 다른 권한), 적용된 ClusterRoleBinding/RoleBinding/ClusterRole/Role 목록 원본. #49의 `get /metrics` yes는 2026-09-26 값이므로 **현재 상태만** 새로 읽는다. no/오류가 나오면 그대로 보고하며 권한을 추가하지 않는다.
- `can-i`는 authorization 증적이다. 실제 Token 인증·업무 객체 변경 시험으로 확대하지 않는다.

## 사용

control01에서 `sudo -v` 후:

```bash
KUBECTL='sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf' bash collect-cani-gap.sh          # Tier A 53행 (기본)
KUBECTL='sudo kubectl --kubeconfig=/etc/kubernetes/admin.conf' TIER=AB bash collect-cani-gap.sh  # 81행
```

읽기 전용(`auth can-i`, `get`)이며 약 55~83회 호출이다. `CHECK_TIMEOUT_SEC`(기본 10)로 호출당 시한을 바꿀 수 있다.

## 판정과 종료코드

각 행은 **행 ID·RC·stdout·stderr**를 결속해 `raw/<ID>.{stdout,stderr,rc}`와 `results.tsv`(정상 행에도 RC 열)에 남긴다. `grep/head` 추출은 쓰지 않는다.

| 관측 | 결과 |
|---|---|
| stdout `yes` + rc 0, 또는 `no` + rc 1 | 응답으로 인정(기대와 같으면 PASS, 다르면 FAIL) |
| 위 쌍이 아닌 모든 경우(yes+rc2, no+rc0, 빈 출력 등) | **ERROR** (PASS로 올리지 않음) |
| rc 124 | **TIMEOUT** |

| 종료코드 | 의미 |
|---|---|
| 0 | 전부 기대와 일치, 봉인 완료 |
| 10 | FAIL 있음 |
| 11 | ERROR 또는 TIMEOUT 있음(10보다 우선) |
| 12 | 행 누락·ID 중복 (`COLLECTOR_STATUS=INCOMPLETE`) |
| 13 | 봉인(`SHA256SUMS`·`tar`·sidecar) 실패 |
| 2 | 사전 점검 실패(관리자 `can-i` yes/rc0, 가짜 SA no/rc1) — 시험 시작 안 함 |
| 3 | 파일 쓰기 실패(결과 TSV, raw 원본, **완료 기록 `*.completion.txt`**)·잘못된 `TIER` — 즉시 중단 |

ERROR·TIMEOUT은 FAIL과 **별도 집계**하고 비0으로 끝난다. `COLLECTOR_STATUS`/`SEAL_STATUS`/`EXIT_CODE`/`AUX_RC_NONZERO`는 아카이브 밖 `*.completion.txt`에 쓴다. 완료 기록을 쓰지 못하면 성공으로 끝내지 않고 비0(3)으로 종료한다. 보조 수집(Binding/Role, `20-*`·`21-*`)의 비0 RC 개수는 `AUX_RC_NONZERO`로 summary·completion에 노출한다(판정·종료코드에는 쓰지 않으므로 실행 후 해당 `.rc`와 적용 Role 연결을 별도로 확인한다). stdout은 파일로 직접 받아 수신 바이트를 그대로 보존한다. 이 값은 AC 판정이 아니라 Review Owner 판단의 입력이다.

## 시험

`bash test-cani-gap-collector.sh ./collect-cani-gap.sh` — `kubectl` 스텁만 쓰는 로컬 시험이며 클러스터·네트워크에 접근하지 않는다. **28 PASS / 0 FAIL**(`validation/local-test-results.txt`). 다룬 경우: 정상(Tier A 53행·Tier AB 81행), 행별 RC·원본 보존, `SHA256SUMS` 검산, yes+rc2, timeout, no+rc0, 기대 불일치 FAIL, 사전 점검 실패(시험 미시작), `kubectl version` 실패, TSV 쓰기 실패, 잘못된 `TIER`, **완료 기록 쓰기 실패(B1)**, **봉인(tar) 실패(종료코드 13)**, 보조 수집 RC 노출, stdout 바이트 보존. B1 시험은 수정 전 소스에서는 실패(종료코드 0)함을 확인했다.

## 한계

- **서버 실행 전이다.** 실제 `kubectl` 출력 형식(로캘 포함)과 `timeout` 동작은 서버에서 검증되지 않았다.
- 행 누락·중복 경로(종료코드 12)는 코드로 구현했으나 **로컬 시험으로 재현하지 못했다.** 봉인 실패(13)와 완료 기록 쓰기 실패(3)는 시험으로 확인했다.
- `sudo`가 비밀번호를 요구하면 `timeout` 아래에서 막힐 수 있어 실행 전 `sudo -v`가 필요하다.
- 한 시점 결과이다. Admission(PSA 등)·SA 외 주체(사용자·그룹)·TokenRequest 정책은 범위 밖이다.
- 기존 `collect-cani-core36*.sh`와 증적은 수정하지 않았다.

AI 사용: CLAUDE_ASSISTED (행 대조, 수집기·시험 작성 보조). 판단은 이하영.
