# CP1 후보 사전 점검 보완과 운영 범위 결정 — 2026-09-17 KST

상태: **DRAFT_NOT_EXECUTABLE**. 실제 CP1 변경·kernel 검증·인수·후속 controller 연결 완료를 의미하지 않는다.

## 자체 점검에서 발견하고 수정한 문제

`ac30186` 수집기는 CP1 firewall/protection과 Pod membership을 전후 대조했지만 전체 zone/ipset 정의와 native 규칙은 한 번만 조회했다.
기존 CP1 helper의 `stable.firewall`에는 모든 zone/ipset 정의가 없으므로 그 비교만으로 해당 drift를 발견할 수 없었다.
또한 다른 zone의 interface/source 분류가 대상 veth를 public에서 제외하는 경우를 후보 생성기가 차단하지 않았다.

- 모든 zone/ipset 목록·정의, nft ruleset, boot ID, firewalld version/configuration을 전후 한 쌍으로 수집한다.
- 목록 추가/삭제, 정의 변경, 중복 이름, 미등록 정의, 누락·실패·잘린 조회를 차단한다.
- nft 비교에서 rule counter 및 named counter의 숫자형 packet/byte 통계만 정규화한다. 원문 해시는 모두 남긴다.
- handles, rule/표현식 순서, verdict, set element, timeout, quota 등은 전체 비교에 유지한다.
- public 이외 zone의 정확한 이름·`+` prefix가 대상 veth와 겹치면 차단한다. source 분류는 주소·MAC·ipset 여부에 관계없이 별도 분석 대상으로 차단한다.
- `consistency.atomic_snapshot=false`, `native_semantics_verified=false`, `transient_aba_changes_excluded=false`를 출력해 두 번의 관측을 완전한 증명으로 표현하지 않는다.

zone 출력 필드는 firewalld v2.4.3 `FirewallCommand.print_zone_policy_info` 원문과 대조했다.
해당 함수의 출력은 두 칸 들여쓴 `interfaces:`/`sources:`이고 rich rule은 별도 continuation이다.
[공식 출력 코드](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/command.py), Git blob `8d84f4222fc2e27cb2184fe56c0452fd450a9e2a`.
그 확인은 daemon/kernel 실행 시험이 아니다.

## 한 번의 검토에 묶기 위한 운영 선택

아래 두 안은 구현 범위가 다르다. 첫 번째의 소스 검토 결과를 두 번째의 운영 승인으로 승격하지 않는다.

| 항목 | 현재 고정 veth 후보 | Join·Pod 재생성 지원 운영안 |
|---|---|---|
| 목적 | 현재 CP1 Pod 목록으로 제한한 짧은 관측 | CP1 통신 보완을 후속 노드 준비/Join까지 유지 |
| 새 Pod/veth | 자동 포함 안 됨. 이름 재사용/UID 변경 시 중단·회수 필요 | 변경되는 membership을 안전하게 반영하는 별도 메커니즘 필요 |
| 기간 | 최대 2시간은 초안 상한. 실제 창은 미확정 | Join/보호 인계까지의 종료 조건과 절대 종료 시각 미확정 |
| 회수 | 소유 객체 제거, 원 Runtime 6개와 원본 전체 상태 복원, unit 종료·예약 해제 | 같은 회수와 함께 HEP/GNP 보호 인계·지속 정책 제거 순서 필요 |
| 소비자 | 활성 상태를 기존 CP2/3 controller가 계속 거부 | 새 producer/인수/live 관측을 소비하는 전후 gate 구현 필요 |
| 현재 코드 | 읽기 전용 수집 및 비실행 XML 제안 | 미구현 |

**프로젝트의 Join 진행 목표에는 두 번째가 맞지만, 현재 고정 집합 코드를 그대로 사용해서는 달성되지 않는다.**
반복되는 짧은 관측 시험을 운영 완성으로 표현하지 않고, 다음 구현의 목적을 이 둘 중 하나로 확정해야 한다.
기존 준비 기준 `CNI_SCOPE_SIMPLIFICATION_20260916.md`는 규칙·유지 기간·복구 종료 상태 확정 전에 mutation 명령을 생성·배포하지 않도록 정했다.
이번 “착수”는 준비와 자체 점검을 계속할 근거이며, 새 Pod 자동 범위·무기한 유지·CP2/3 변경까지 이미 승인되었다고 해석하지 않는다.

## 확정 뒤 한 묶음으로 완료할 항목

1. 선택한 인터페이스 범위/주소/L4/planes/유지 종료 상태와 실제 기존 HOST·외부·Calico deny 보존.
2. 독립 로컬 복구, mutation 전 durable intent, partial success/timeout/crash 재개, 소유권과 타인 drift 보호.
3. reload 이후 원 Runtime 6개 복원 및 전체 native delta 확인. 실제 CP1 인수 증적과 연결한 변경 영수증.
4. 원 영수증을 수정하지 않는 producer→acceptance→live-state→후속 controller 전후 검증.
5. 전체 묶음의 자체 실패 시험을 끝낸 뒤 코드 리뷰 1회, 필요한 실제 창의 수집/실행 1회. 실패하면 동일 작업의 상태·증적을 사용한다.

## 검증과 남은 한계

`python3 -m unittest discover -s tests -v`: **124/124 STATIC/MOCK PASS**, 이 수정의 신규 test method는 15개다.
기존 test method 109개도 포함한다. subTest 사례 수는 시험 개수에 더하지 않았다.
추가 테스트는 수집 누락/시간차 drift/zone 분류/허용할 counter 변화/숨기면 안 되는 native 변화에 집중했다.
원 CP1 생산자·node helper·controller·오프라인 인계 검사기 바이트는 그대로다.

과거 18개 upstream 함수 매치 결과는 이전 커밋의 독립 기록이며 이번 실행 개수에 더하지 않았다.
이번 보완은 실제 firewalld/SELinux/Calico/kernel 성공, 실제 packet 증거, 독립 리뷰 또는 CI PASS가 아니다.
새 VM 반입·증적 재수집·재시험 요청은 하지 않는다. 실제 적용/회수기 및 후속 controller 연결은 여전히 미완료다.
