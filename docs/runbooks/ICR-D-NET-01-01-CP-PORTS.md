# CP1 Runtime 적용·복구 v1.3

2026-09-14 / 후속 보완·검토 단계, 실서버 미실행. [선행 변경 기록](../change/ICR-D-NET-01-01/ADVANCE_CHANGE_RECORD_20260910.md)에 정본 차이와 사용자 지시를 기록했다.

## 범위와 순서

1. 새 HEAD의 이하영 독립 APPROVED, #14 또는 PR #2의 실행 결정, 평일 최대2시간 작업창·백업·콘솔·보호 검토·복구 계획을 확인한다. 선행 착수 허용을 빈 승인값으로 전용하지 않는다.
2. 현재 고정 소스의 inspect로 CP3대의 firewall/NM/SELinux/nft·원래 규칙과 CP1 단일 Node/voter·API를 재확인한다. CP1 Calico Pod·HEP/GNP·Felix·IPPool 조회를 추가했다. 실패·새 정책·새 member는 별도 검토하며 collection PASS만으로 공존 안전성을 판정하지 않는다.
3. 정확한 source/plan/target/input, 30분 이내 snapshot·topology와 승인 기록 해시가 일치하면 CP1 Runtime만 CP .102/.103/.104 각각 /32→2379–2380·10250 TCP 최대6개를 추가한다.
4. 쓰기 전 intent를 root 전용 영수증에 fsync하고 query/add/query 결과로 실제 소유권을 확인한다. 기존 SSH/LB·Permanent·NM·SELinux는 보존한다.
5. 새 SSH, CP2/3→CP1 TCP, API·etcd 상태, CA/Auth·logs/exec/port-forward, 비승인 Source·Calico/CNI·DNS/필수 통신 회귀 증적을 확인한다. TCP 성공은 인증·서비스 인수를 대체하지 않는다.
6. 실제 Runtime Positive/Negative/Regression과 Reviewer 인수 후 별도 보존·회수·보호 인계를 결정한다. **이 버전에는 Permanent 승격 기능이 없다.** 인수 전 CP2/3 join·확대를 하지 않는다.

현재 정책 SHA: a85a819fed1e18b33e9b82d576c7a983aa39c21e36e270590dc0ee4ac0e85e04. 기존 36개 Runtime/Permanent 후보 계획은 이력 참조다.

## 동결과 중단

같은 작업창에 join/reset/member 변경, 다른 방화벽/NM/Calico 변경, reload/restart/reboot를 병행하지 않는다. Runtime 규칙은 서비스 재시작·reload·재부팅으로 사라질 수 있으므로 운영 지속성 PASS가 아니다. [공식 동작](https://firewalld.org/documentation/configuration/runtime-versus-permanent.html).

관리/API 악화·identity/zone 불일치·drift·조회 실패·모호한 명령 결과에서는 확대를 중단한다. 새 run ID로 미해결 이전 receipt를 우회하지 않는다. 2시간은 실행 허용창 상한이며 자동 소멸 규칙이 아니다. 원격은 승인 원문/해시를 재검증하고 각 명령 및 새 쓰기 직전에 종료 시각과 monotonic 예산을 검사한다. 종료 시각은 제외한다. 만료 시 신규 명령을 중단하고 부분 영수증을 보존하며, 별도 rollback 창 없이 자동 회수하지 않는다.

## 결과와 복구

성공 상태는 RUNTIME_APPLIED_REVIEW_REQUIRED, receipt schema는 fds.cp1-canary-receipt.v2, phase=runtime, 최대6개 작업이다. 전체 인수나 Permanent 반영이 아니다.

복구는 같은 source/정책·원래 run/영수증 해시·별도 rollback 승인 기록을 사용한다. 변경 전 부재였고 추가 성공이 확정된 Runtime 규칙만 역순 회수한다. 불명확 작업·다른 Node/member·snapshot drift·불완전 rollback은 자동 회수를 거부한다.

[원본 checkout/키트 보존, source 복원, API 장애 시 콘솔 분기](CP1_CANARY_RECOVERY.md)를 실행 전에 검토한다.

원래 v1 12개 receipt는 v2로 복구하지 않는다. 기존 소스와 현재 상태를 별도 검토하며 영수증 파일을 삭제해 가드를 우회하지 않는다. 원래 snapshot·새 SSH·API·etcd를 복구 후 확인한다. 통신 의존성이 생겼다면 대체 보호·복구 경로를 먼저 확보한다.

## 보호 인계

D34 최종 목표인 6대 firewalld disabled/inactive + named HEP/GNP는 유지한다. HEP/GNP 순서는 Bootstrap Allow→canary Worker→Worker 확대→CP이며 CP1 통신 canary가 이를 대체하지 않는다.

Calico/firewalld 공존 위험·기간·종료·복구 근거는 선행 변경 기록과 #14에 연결한다. PR #4의 구 영수증 계약·Permanent 선반영 두 지적은 09-13 COMMENT 리뷰에서 CLOSED_STATIC 수용됐다. 이번 생산자 변경에 따른 새 commit/helper hash pin과 호환성 검증은 별도 후속이다. NTP·IF-06/06B/16·6/6 Ready·3voter·보안 인수는 이번 코드 작성으로 완료하지 않는다.
