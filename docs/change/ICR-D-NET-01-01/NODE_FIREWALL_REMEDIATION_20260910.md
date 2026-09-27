# PR #4 독립 리뷰 대응 및 v2 보완 이력

2026-09-10. 대상: [infra-kubernetes PR #4](https://github.com/321Team/infra-kubernetes/pull/4). 원본 HEAD de9a3a30fd691ac30a0e03c4212c21300653e088. 검토 기준: [이하영 리뷰](https://github.com/321Team/infra-kubernetes/pull/4#issuecomment-5617233986), [전제 대조·CA 응답](https://github.com/321Team/infra-kubernetes/pull/4#issuecomment-5617452251).

사용자가 요청한 “4번 착수”로 코드·문서 보완과 새 HEAD 재리뷰 준비를 수행했다. 이전 리뷰 결정을 승인으로 바꾸지 않는다. PR #1/#3이 병합된 main 7d44afc3afff4d322cb4dbeff4433d61e23e111a의 12개 파일을 보존한다.

## 리뷰 항목 매핑

| 항목 | 반영한 내용 | 남은 검증/결정 |
|---|---|---|
| CP1 영수증 불일치 | 최신 PR #2 097ddb56의 v2 Runtime 6개 연산을 고정. helper·두 plan 해시, 순서·소유권·결과·전후 상태·시각 검증. 실제 생산자 바이트를 fixture로 고정해 모의 backend에서 영수증 생성 | 실제 CP1 적용 영수증 및 독립 인수. 구형 068661e0가 v2라는 리뷰 전제는 앞선 대조에서 정정함 |
| CP1 Runtime 인수 연결 | 별도 CP1 인수 스키마로 실제 receipt 파일/내용 해시·run ID·리뷰·Positive/Negative/Regression 증적 결합 | 오프라인 JSON은 GitHub 리뷰 진위 인증이 아님. 실제 작성자·대상 HEAD 확인 |
| CA-02 적용 순서 | Runtime 전용 계획·v2 receipt/authorization/policy. CP 6·Worker 4후보/노드. Permanent mutation 없음; promote-permanent는 SSH 전 중단 | Runtime 실측 인수 후 별도 Permanent 또는 보호 인계 종료 절차 결정·구현 |
| policy JSON 비권위 | Controller/원격이 v2 파일을 고정 실행 범위와 비교. 각 대상 변조 거부 테스트 | v1 JSON은 기존 이력으로 보존하며 미사용 |
| 전체 snapshot 동치 | 대상 기존 전체 비교 유지. CP1 보호 상태는 의미 있는 spec·UID·준비/재시작 정보만 투영 | 실제 무관 필드 drift 사례는 미확보. 검증 약화로 우회하지 않음 |
| 명령 누적/SSH 제한 | 원격별 480초·1,200회, 개별 timeout에 남은 예산·변경 창 반영. metrics·부분 증적 보존 | 실제 RHEL 명령 수/실행 시간 측정 및 timeout 후 회수 검증 |
| 잔여 manifest advisory | 10개 표준 경로 메타데이터를 snapshot·승인에 해시 결합, 변경 중 drift 차단 | 사용자 지정 경로/기존 join 구성은 #10 판단. 자동 삭제/reset 없음 |
| 동시 변경 | 동일 controller 전체 대상 lock + 기존 node-local/journal 차단 | 분산 lock 미제공. 다른 관리자/외부 join 동결 필요 |
| kubectl 탐색 | /usr/local/bin 추가, root 소유·경로 권한·실행 가능 검증 유지 | 현장 설치 경로 및 sudo Python -I 허용 여부 |
| NTP/join | NTP endpoint·100 ms/60초×6은 미승인 초안 유지. join과 Runtime 종료 계획 분리 | 승인 endpoint·수치·운영 주체, #10의 실제 재개 판단 |
| LF/env/redact/zone | .gitattributes LF, 제어기 env 제한·원격 Python -I, 비밀값 패턴 시험 보강, forward/masquerade 실측 검토값 결합 | 새 현장 읽기 전용 검사와 보안 검토 |
| v1 journal·복구 | 기존 경로 보존·legacy 읽기 전용 회수. v2 rollback은 새 inspect·별도 승인·원래 영수증 결합 | v1 자동 rollback 거부. 실제 부분 실패/복구·재부팅 증적 필요 |

## CA-01~07와 실제 apply 판단

| ID | 현재 정리 | apply/후속 판단 조건 |
|---|---|---|
| CA-01 | 발행 v3.1.17/D34 유지. 사용자 승인에 따라 불일치 이유·v18 예정과 선행 준비 이력 연결 | 실제 보호·콘솔·백업·복구·순서 정합성을 검토하고 실행 창 기록. 선행 코드 승인이 보호 전환 인수는 아님 |
| CA-02 | 본 도구는 Runtime만 변경하도록 수정 | CP1 Runtime 실제 독립 인수와 PR #4 새 HEAD 리뷰 후 해당 Runtime 창 판단. Permanent는 별도 |
| CA-03 | 최신 제출 PR #2 inspect에서 CP1 Calico Running·HEP/GNP 0 관측. CP1 상태 수집·drift 검증 추가 | 창 직전 재관측과 실제 보호 검토. Running 또는 해시 일치만으로 보호 완료 판정 금지 |
| CA-04 | 승인 기록의 역할과 한계 명시, 정확한 입력/대상/코드 결합 강화 | 실제 이하영 리뷰·운영 판단의 진위와 HEAD를 사람이 대조 |
| CA-05 | 고정 통신 범위 보존 | 실제 Positive/Negative/Regression, CA/Auth·기능 시험. 포트 범위 일치를 IF-06 또는 엔진 인수로 확대 금지 |
| CA-06 | CP2 잔여 메타데이터 검토와 drift gate 강화 | #10의 원래 join/사용자 지정 data-dir 판단, 필요 시 별도 정리·보존·복구안 |
| CA-07 | 고정 NTP 미승인 상태와 기존 초안 유지 | #14 목적지/운영 주체, #10 수치 인수, #19 egress. Runtime 검토를 NTP 승인으로 확대 금지 |

v18 전 선행 준비 승인 근거: [PR #2 ADVANCE_CHANGE_RECORD](https://github.com/321Team/infra-kubernetes/blob/097ddb56d87b229a9eb465f72ea9f55d16524c51/docs/change/ICR-D-NET-01-01/ADVANCE_CHANGE_RECORD_20260910.md). 정본 HEP 순서·최종 6대 firewalld disabled/inactive·기존 AC/Done #65는 변경하지 않았다.

## 검증과 다음 순서

로컬 Linux 모의 테스트 74개 PASS: PR #4 기존 보완 33 + v2 계약/실패 분기 22 = 55개, 병합된 PR #3 Worker 수집기 19개 회귀. 고정 PR #2 생산자 fixture를 실행했지만 실제 서버 적용 시험이 아니다. 원문 로그와 환경·소스 해시는 NODE_FIREWALL_TEST_RESULT.txt 및 NODE_FIREWALL_TEST_ENVIRONMENT.json에 보존한다.

다음은 새 HEAD 이하영 독립 리뷰 → PR #2 실제 CP1 Runtime 적용·독립 인수 → 실행 창의 PR #4 새 inspect 및 보호/복구 판단 → 한 대상 Runtime 검증 순서다. 리뷰어의 현장 검증 요구가 충족됐다고 주장하지 않으며 현재 자동 병합·실서버 apply는 수행하지 않는다.
