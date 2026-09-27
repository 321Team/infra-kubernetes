# CP1 운영자 예외 권한 — 신규 v3 Job 제안

상태: **구현·정적/모의 검증 후 독립 정책/소스 리뷰 대상**. 운영 위임이 이미 승인됐거나 현재 Job에 적용됐다는 기록이 아니다.

## 요청과 권한 근거

사용자가 2026-09-18 KST 대화에서 담당자 부재를 이유로 사후 승인 착수 및 “예외 권한 반영”을 요청했다. [요청 기록](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5717545625).

[ICR 원문](https://github.com/321Team/fds-project-baseline/blob/121fb744f0d816b3910a0bb07cf2bbe0d8883a16/tickets/v3.1.18/descriptions/ICR-D-NET-01-01.md)에서 이권욱은 ICR Lead이고, [Identity Map](https://github.com/321Team/fds-project-baseline/blob/121fb744f0d816b3910a0bb07cf2bbe0d8883a16/governance/GITHUB_IDENTITY_MAP.md)은 GWONUK 계정을 확인한다. 이 사실을 hayeounglee의 독립 인수 권한이 위임됐다는 근거로 확대하지 않는다. 사용자 요청은 이 **새 제한적 권한의 구현 근거**이며, 기존 독립 승인 서명이 아니다.

## 제안 권한

| 항목 | 계약 |
|---|---|
| 권한자 | GWONUK, 개별 Job의 실제 GitHub 결정 댓글 필요 |
| 선행 위임 | 새 소스·예외 정책의 독립 리뷰와 v3 실행 authorization의 명시적 opt-in |
| 사유 | REVIEWER_UNAVAILABLE와 구체 사유 |
| 범위 | 이미 적용·5-flow·HOST/외부 회귀·handoff 완료한 CP1 Runtime의 임시 유지 |
| 실행 | 해당 Job에 동결된 소스의 retain-by-exception |
| 유지 한계 | 최초 작업창 안에 root 커밋, 처음 정한 retained_withdraw_at까지 |
| 상태 | PROVISIONAL_RETAINED_REVIEW_REQUIRED / cp1_accepted=false / independent_review_status=PENDING |
| 사후 검토 | 실제 운영자 결정·원 handoff·시험·예외 유지·회수 결과를 독립 리뷰어에게 인계 |
| 제외 | CP2/CP3 실행 승인, Join, Permanent, HEP/GNP, 무기한·날짜를 넘긴 연장, 기존 Job 이관 |

신규 v3 authorization은 기존 보호·백업·콘솔·freeze·정확한 source/before/scope·같은 KST 평일 조건을 그대로 요구한다. 새 bundle에 대한 독립 소스 리뷰가 없으면 운영자 예외만으로 apply를 허용하지 않는다. 독립 리뷰가 선행되는 것은 이 **권한 위임 정책**이며, 그 정책 아래 발생하는 개별 결과의 인수는 사후로 남길 수 있다.

## 구현과 증적

- v1/v2 형식과 실행 조건을 보존하고 v3 신규 Job에만 예외 권한을 기록한다. 실행 중인 v2 Job `12f18e7d72f6c7f4`와 해당 root 보관 소스·CHANGE/HANDOFF·두 타이머를 수정하지 않는다.
- retain-by-exception은 실제 trial을 다시 분석하고 기존 handoff를 재구성한다. 양성 근거는 실제 trial archive, negative는 handoff 내 분석, regression은 원 HOST/외부 결과 파일에 묶는다. negative의 cleanup/drift/consistency 한계를 유지하며 deny/cross-node로 확대하지 않는다.
- OPERATOR_EXCEPTION.json과 EXCEPTION_REGRESSION.txt를 저장한 뒤 EXCEPTION_RETENTION.json을 마지막 root 커밋 표시로 원자 기록한다. 첫 deadline을 넘기면 LATE 파일로 옮기고 유지 성공으로 처리하지 않는다.
- 기존 CHANGE/HANDOFF는 불변이다. ACCEPTANCE.json을 운영자 이름으로 채우거나 hayeounglee의 APPROVED를 만들지 않는다.
- 첫 deadline은 유효한 예외 root 기록과 최종 타이머가 있을 때만 임시 유지한다. 최종 deadline은 인수 여부와 관계없이 기존 복구를 실행한다. 타이머 재예약·취소·새 상시 서비스는 없다.
- 같은 입력 재조회는 기한을 늘리지 않는다. 다른 결정으로 덮어쓰기, 기존 독립 인수의 강등, 잘못된 소스/Job/시각/증적/회수 예약은 거부한다.
- 기존 소비자는 provisional 상태를 인수로 인정하지 않는다. CP2/CP3는 실제 독립 인수와 대상별 승인을 계속 요구한다.
- 첫 deadline 전 실제 독립 인수가 확보되면 정상 accept-retention으로 전환할 수 있다. 첫 deadline 뒤 회신은 사후 증적 검토로 기록하며 날짜를 소급해 정상 인수로 바꾸지 않는다. 종료 후 검토는 만료된 Runtime을 되살리지 않는다.

JSON의 계정명·URL·boolean은 GitHub 작성자 인증 수단이 아니다. 운영자는 실제 권한 위임 리뷰와 GWONUK의 해당 Job 결정 댓글을 확인해야 한다. 이번 변경은 승인 없는 JSON 값을 신뢰할 수 있게 만드는 인증 기능이 아니다.

## 현재 Job 및 배포

승인된 부모 소스는 `a95ef4f42b45cb4bfaa7214e8ed366750e127502`다. 이 변경은 별도 후속 PR에서 검토해 그 소스 승인과 현재 실행을 혼동하지 않도록 한다. 현재 v2 Job에는 예외 정책이 들어 있지 않으므로 이 변경을 복사하거나 timer를 정지해 전환하지 않는다. 최초 승인 계약에 따른 회수·정리가 확인된 뒤, 승인된 새 소스와 새 inspect/authorization으로 생성한 v3 Job에서만 사용할 수 있다.

이번 변경의 실제 SSH/방화벽/타이머 실행·인수·CP2/CP3·Join·병합은 미수행이다. 검증 결과는 별도 validation JSON과 시험 로그에 기록한다.

## 검증 결과

관련 정적·모의 시험 **176/176 PASS**(기존 156개 + 신규 예외 시험 20개). 전체 저장소 CI 또는 대상 VM 통합시험을 수행했다는 뜻은 아니다. Python 구문·JSON template·기본 PLAN_ONLY도 확인했다. 방화벽 범위·복구·타이머 관련 13개 함수의 AST와 기존 6개 파일 바이트는 승인 부모와 동일하다.

새 CP1 source bundle: `22a371a9efcd799e5574b37f8aec19e21f7a19c8fe8eb587ea8106247cc064af`.

- [시험 로그](CP1_OPERATOR_EXCEPTION_TEST_20260918.txt)
- [검증 JSON](CP1_OPERATOR_EXCEPTION_VALIDATION_20260918.json)
- [실행 계약](../../runbooks/CP1_MINIMAL_CHANGE.md)
