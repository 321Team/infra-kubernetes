# CP1 최소 방화벽 변경 + 새 receipt + 관련 시험

[Lead 2026-09-17 10:48:51 KST 의견](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5707194172)을 반영한 증분이다. 부모는 `7de8df845eaa7daf8e11e3c6ecc9126f88f70130`이다.

개발 차단 의견은 없으며 최소 경로와 새 receipt 방향에 동의했다. 이는 아직 작성되지 않았던 이 실행 코드의 APPROVED나 CP1 운영 실행 승인이 아니다.

| 요청 | 구현·검증 |
|---|---|
| 방화벽 코드 게시 | CP1 전용 plan/inspect/apply/status/recover. 비활성 staging 후 Runtime `cali+` 활성화, 동일 journal 회수 |
| 양쪽 `/26`·인터페이스 | cni→cni zone dispatch의 iif/oif cali* + source/destination /26. ALL L4 후보 범위 명시 |
| HOST·외부 보존 | public HOST services/rich rules 복제, forward=no, ens160 IPv4/IPv6 전달 조건 별도 보존. 정적/모의 검증이며 실제 회귀는 미실행 |
| 유지·복원 | 평일 최대 2시간, 종료 180초 전 단일 transient recovery. 활성화 전 새 Permanent 정의 제거. foreign drift 맹목적 reload 차단 |
| 새 receipt | 원 receipt 불변 연결 → 변경 before/after → 원본 trial 재계산 → 새 handoff → 별도 독립 인수 |
| 소비자 연결 | 별도 controller adapter에서 기존 전체 fingerprint 검사 유지 + 새 root 전체 상태/예약/수명 검사 전후 추가 |
| 자체 점검 | 130개 단위·회귀, upstream 생성함수 + 패킷 모델 21조건, 과거 실제 3/5 결과 보존 |

구체적 CLI·양식·신뢰 경계·제한은 [실행 계약](../../runbooks/CP1_MINIMAL_CHANGE.md)을 따른다. 새 소유 객체 생성 때문에 **Permanent 비활성 정의의 임시 쓰기와 두 번의 controlled reload**가 포함된다. 활성화만 Runtime이고, 임시 쓰기를 숨겨서 Runtime 무변경으로 표현하지 않는다. 이 단계는 실행 결정 필드에서도 별도 확인한다.

검토자가 결정할 부분은 다음과 같다.

1. `/26` 전체와 현재/미래 cali veth에 대한 ALL L4 후보 범위 및 public HOST/ens160 보존 설계.
2. 비활성 Permanent staging·reload/원6개 복원·Runtime 활성화·동일 창 회수와 drift 시 콘솔 경계.
3. 원 receipt/상태 비교 유지, 새 인수의 원본 5-flow 재계산·after/hash·수명, 후속 controller의 적용 전후 검사.

현시점 코드 검토와 실제 운영 실행 판정은 구분한다. 실제 daemon/XML/kernel/SELinux 파일 접근, end-to-end timer, HOST/외부 회귀와 실제 5/5는 미실행이다. #11 전체 qualification·#13 service expansion은 이 경로의 선행조건으로 되돌리지 않는다. 단위시험은 위 환경 실측의 대체 증거가 아니다.

PR7 HEAD `4af1393e`의 기존 HOLD(SEC 미해소·NTP/HEP·SSH egress 범위)는 유지한다. PR13 `a1d76e9` Draft/보류도 유지한다. 이번 변경은 두 PR의 차단 해소나 승인으로 표시하지 않는다.
