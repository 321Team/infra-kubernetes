# Node Runtime 인가 기록의 신뢰 경계

2026-09-18: [중앙 정책과 Lead 판단](../REVIEW_POLICY.md)을 반영한 신규 입력 계약. 기존 기록은 해당 schema의 검증을 유지한다. 실제 운영 적용·인수 기록이 아니다.

## 코드 리뷰와 실행·인수

- 기존 node-firewall-authorization.v2는 과거 PR4 검토자·URL·조건을 그대로 검증한다.
- 신규 node-firewall-authorization.v3는 source_review에 정확한 실행 SHA의 코드 작성자 GWONUK·독립 검토자 hayeounglee·APPROVED·실제 PR review URL/시각을 연결한다. 동일 SHA의 코드 승인은 반복 실행에 재사용할 수 있고 새 코드 변경은 새 리뷰가 필요하다.
- lead_decision은 ICR-D-NET-01-01 Lead GWONUK의 실행 결정(EXECUTE)이다. 코드 승인 이후의 시각, 중앙 policy commit, 책임 범위와 실제 결정 댓글을 연결한다. 인가 JSON에 서로 다른 두 승인 역할을 하나로 쓰지 않는다.
- CP1 인수의 기존 fds.cp1-runtime-acceptance.v1은 독립 승인 원본을 유지한다. 신규 v2는 Lead 결정(ACCEPTED)을 받으며 같은 receipt/source/plan/run 및 Positive·Negative·Regression PASS/원본 해시 검사는 그대로다. 새 인수 시각은 실제 결과 이후여야 한다.
- 양식: [Node Lead 실행](../../policies/icr-d-net-01-01/node-firewall-lead-authorization.template.json), [CP1 Lead 인수](../../policies/icr-d-net-01-01/cp1-lead-acceptance.template.json). 빈 decision/시각/hash와 미확인 시험은 유효한 승인으로 처리하지 않는다. 과거 양식은 과거 source pin이므로 새 기본 양식을 사용한다.

## 사람과 프로그램이 확인하는 범위

JSON은 일관성 검사 자료이며 사용자 인증·서명·GitHub API 승인 조회가 아니다. 이름·APPROVED·true·SHA를 편집할 수 있다는 한계를 유지한다. Lead/실행자는 인증된 GitHub 세션에서 코드 승인 작성자·HEAD·후속 변경 요청, 실제 Lead 결정과 원본 증적을 확인해야 한다. 역할 문자열이나 URL 형식만으로 권한을 인증했다고 표시하지 않는다. 작성자 자신의 검토나 AI 점검은 독립 코드 승인이 아니다.

ICR Lead GWONUK와 P1-K8S-01 Lead hayeounglee를 혼동하지 않는다. 이번 controller 입력의 scope는 ICR-D-NET-01-01에 한정하며 타 업무의 인수권한을 새로 부여하지 않는다. 증적 저장소 편입·정정 PR은 계속 독립 리뷰 대상이다.

## 유지되는 실행·복구 통제

source/target/mode·정책/계획/사전 상태/receipt/잔여 메타데이터 hash, 최신 inspect, 제한 작업창, 평일, freeze·백업·콘솔·보호·복구 확인, SSH/known_hosts/sudo, 고정 /32 포트·Runtime 전용·영구 반영 금지 및 drift/rollback 검사를 유지한다. 결과 문자열의 REVIEW_REQUIRED는 결과 확인 필요를 뜻하며 독립 인수를 자동 생성하지 않는다.

기존 producer cp-canary-node.py와 CP1_CONTRACT는 byte/pin을 보존한다. 현재 controller는 그 고정 CP1 canary receipt만 받는다. 세션/Join 등의 다른 receipt나 이미 가입한 노드에 대한 인가를 일반 승인 정책으로 우회시키지 않는다. 진행 중 Job·source pin·타이머 계약은 소급 변경하지 않으며 새 코드와 입력으로 전환하는 후속 실행 범위·복구를 Lead가 결정한다.

STATIC/MOCK/CI 통과는 서버 적용·Runtime PASS·인수 완료가 아니다. 관련 [Node 절차](NODE_FIREWALL_FOLLOWUP.md)는 기술 실행·복구 참조로 유지하고 사람 간 일반 승인 절차는 현재 중앙 정책을 따른다.
