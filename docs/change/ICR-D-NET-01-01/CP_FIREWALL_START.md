# CP1 Runtime canary v1.2

상태: 구현 보완·독립 리뷰 요청 / 실서버 미실행. [선행 변경 기록](ADVANCE_CHANGE_RECORD_20260910.md)에 사용자 지시·정본 차이·기술 근거·v18 반영 조건을 기록했다.

대상은 CP1(10.1.93.102) 한 대이며 CP .102/.103/.104 각각 /32→TCP2379–2380·10250을 Runtime에만 최대6개 추가한다. Permanent는 조회·보존하고 승격 기능은 제공하지 않는다. 기존 SSH/LB·NM·SELinux 조건과 원래 규칙의 소유권을 보존한다.

- 최초 cp-firewall-v1.json·cp-firewall-plan-20260910.json은 36개 후보의 이력 참조이며 현재 적용 명령이 아니다.
- 현재 실행 계약: policies/icr-d-net-01-01/cp1-runtime-policy-v2.json.
- jvgkgfm9 재점검은 수집 완전성 PASS이며 새 코드 실행 증적이 아니다. 첫 실패 이력과 함께 인덱스에 보존했다.
- PR #4는 기존 source/v1 12개 receipt를 요구해 새 v2와 호환되지 않는다. 별도 수정·Runtime 인수 전 적용 보류다.

[실행·복구](../../runbooks/ICR-D-NET-01-01-CP-PORTS.md) · [키트](../../runbooks/ICR-D-NET-01-01-CP1-CANARY-KIT.md) · [검증](VALIDATION.md).
