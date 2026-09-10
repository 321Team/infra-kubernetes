# CP1 Runtime v1.2 검증 기록

## 최신 현장 기록 — 2026-09-10 19:15–19:16 KST

ansible01의 Linux 6.12.0-260.el10 / Python 3.12.13에서 **37/37 PASS**(실행 도구26 + 계획11). 구19개 테스트 식별자 전부 포함. source afc246d의 27개 파일·원격 Git blob, 외부/내부13개·중첩5개 증적 체크섬 일치. CP 3대 inspect 일반37+snapshot78=115개 조회 rc=0. CP2/3→CP1 TCP0/6 및 HEP/GNP0은 남아 있어 실제 운영 인수는 미완료다.

[현장 검토서](ONSITE_REVIEW_20260910.md) · [현장 Linux 원문·소스 해시](ONSITE_TEST_20260910.json) · [점검 요약](ONSITE_INSPECTION_SUMMARY_20260910.json). 리뷰어의 현장 Linux 증적 요청은 확보·검증 완료이며 독립 APPROVED 및 Runtime apply/rollback은 별도다. 이번 커밋은 증적·문서만 갱신하며 코드·테스트·정책을 변경하지 않는다.

아래 로컬 검증·당시 현장 미실행 문구는 afc246d 보완 시점의 이력이다. 현장 실행 여부는 위 최신 기록을 적용한다.

## 이전 로컬 검증·준비 이력

2026-09-10 / 로컬 Linux 검증 / 실제 ansible01·CP1 Runtime 적용 미실행.

| 항목 | 결과·근거 |
|---|---|
| 원래068661e canary 테스트 | 19/19 PASS, Linux 6.18.35 / Python3.12.14. [원문 기록](LINUX_TEST_068661e_20260910.json) |
| 새 v1.2 canary 테스트 | 26/26 PASS |
| 역사적 계획 생성기 테스트 | 11/11 PASS |
| 새 소스 합계 | **37/37 PASS**, rc=0. [정확한 파일 SHA와 전체 출력](LINUX_TEST_V12_20260910.json) |
| Runtime 경계 | apply/rollback 모두 Permanent 쓰기0건, 최대6개 Runtime 작업 |
| receipt 경계 | v1 receipt·Permanent 작업 삽입·다른 실행 정책의 회수/적용 거부 |
| 승인 기록 바인딩 | source/target/mode/input·정책·검토자·review URL·실행 결정·필수조건·시간창 검증 |
| 보존·실패 | 기존 규칙, drift·member 변경·timeout·의도 저장 실패 시 중단/불명확 소유권 보존 |
| 실제 v1.1 재점검 | jvgkgfm9 수집 완전성 PASS, 내부5/5·110개 질의rc0. 현재 v1.2 실행 증적은 아님 |
| 새 현장 검사 | v1.2 inspect·실제 적용/복구·보호 공존·CA/Auth/Negative·reboot는 미실행 |

재현 명령:
```bash
python3 -m unittest discover -s tests -p 'test_cp*.py' -v
```

LINUX_TEST_V12는 사전 커밋 단계에서 실제 검증한 파일별 SHA를 기록한다. 부모068661e를 새 구현의 실행 commit으로 표시하지 않는다. 저장소 편입 commit은 이 파일을 포함하는 새 Git commit이며 PR에 따로 기록한다.

첫 검증에서 Runtime-only 전환 후 기존 “12개 중1개 보존=11개 쓰기” 테스트 기대치가 남아 실패했다. Runtime6개 중1개 보존=5개 쓰기로 수정했고, 기존 규칙 미삭제 및 Permanent 무변경 검증을 유지한 최종37개가 통과했다. 실패 결과를 실서버 결함이나 최종PASS로 혼용하지 않는다.

## 리뷰 지적 반영

- [증적 누락 지적](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5615857463): 첫2vp6t68c 기록은 보존하고 [재점검 검토서](CP1_REINSPECTION_REVIEW_20260910.md), [파생 요약](CP1_REINSPECTION_SUMMARY_20260910.json), EVIDENCE_INDEX의 최신 항목을 추가했다.
- [Linux 검증 요청](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5615889673): 원래 소스19개와 새 소스37개 결과를 환경·파일 해시·시각·rc·전체 출력과 함께 편입했다. ansible01/CP1에서 실행한 기록은 아니므로 현장 확인 요구를 대체하지 않는다.
- CA-01: [선행 변경 기록](ADVANCE_CHANGE_RECORD_20260910.md)에 사용자 허용·타당성·공존 위험·범위·v18 반영 조건을 명시했다.
- CA-02: 현재apply는Runtime만 변경하며 영구 승격 미구현이다. 시험·리뷰 전 Permanent 선반영 경로를 제거했다.

## 검증의 한계

Backend·journal은 대부분 모의이며, 실제 SSH/sudo·root lock/fsync·firewalld·Calico dataplane·etcd 연동을 이 시험이 입증하지 않는다. NTP·실제 Source·포트/서비스·Negative·복구/재부팅 인수는 실제 증적이 필요하다. Offline 승인 JSON은 실제 GitHub 승인 인증 기능이 아니다. 원시 운영 로그는 기존 보관 위치에 두고 저장소에는 식별·해시·검토서와 필요한 비밀정보 없는 파생 요약만 포함한다.

