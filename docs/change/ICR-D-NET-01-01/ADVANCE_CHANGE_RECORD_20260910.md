# ICR-D-NET-01-01 선행 변경 기록 — ADV-20260910

> 2026-09-14 현재 적용 기준: [발행 정본 v3.1.18](https://github.com/321Team/fds-project-baseline/blob/72599ae49342b0fb8dbd3d8ee60314afb9f907bb/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md) §6.6/6.6.1. 아래 후보/미발행 표현은 09-10 작성 당시 이력이다. 제한 준비의 근거와 최신 HEAD 독립 리뷰·콘솔/백업/복구/실행창·실행 결정 요건을 구분한다. [09-14 후속 검증](CP1_WINDOW_FOLLOWUP_20260914.md).

기록 ID: **ICR-D-NET-01-01-ADV-20260910** / 2026-09-10 / Lead·최종 승인자 이권욱 / 독립 Reviewer 이하영.

## 사용자 지시와 승인 범위

사용자는 PR #1의 차단사항 확인 후 승인·병합, PR #2 착수를 지시했다. 또한 “기본 정본 불일치의 내용이 정당하고 향후 v18 정본 배포 예정이라면 선행해도 문제되지 않음. 대신 이력에 남기고 충분한 설명필요”라고 조건을 명시했다.

이에 따라 **v3.1.18 발행 전이라는 이유만으로 문서·증적 편입·코드 보완을 막지 않는다.** 이 기록은 조건부 선행 착수의 근거이며 타인의 독립 승인 또는 미실행 Runtime PASS를 대신하지 않는다. 실행할 정확한 HEAD와 당일 보호·복구 조건을 검토하는 기존 절차는 유지한다.

현행 RELEASED는 [docs-v3.1.17](https://github.com/321Team/fds-project-baseline/releases/tag/docs-v3.1.17)이다. [v3.1.18 통합 후보 PR #69](https://github.com/321Team/fds-project-baseline/pull/69)가 존재하지만 아직 Draft/미발행이다. v3.1.17·D34·ICR v1.4의 역사적 문구와 승인 이력을 소급 변경하지 않는다.

## 차이별 판단

| 항목 | 기존 정본·구현 | 선행 판단과 이번 처리 |
|---|---|---|
| CA-01 설치 순서 | D34: Calico 설치 전 firewalld OFF. 11:30 KST 증적: CP1 Calico Running + firewalld active + HEP/GNP 0 | 현재 상태에서 보호 없이 전체 OFF하거나 단일 CP를 재설치하는 조치는 채택하지 않는다. 기존 보호를 유지하며 최소 통신을 검증하는 제한된 CP1 Runtime canary의 준비·독립 검토를 선행한다. 일시 공존의 안전성이 확인된 것은 아니며 실제 적용 조건은 아래에 명시 |
| CA-02 Permanent 순서 | ICR v1.4: Runtime 시험·리뷰 후 Permanent. 구 실행기: Permanent 먼저 | **Permanent 선반영을 정당화하지 않는다.** v1.2는 Runtime 6개 후보만 변경하고 Permanent 쓰기·승격 기능을 제공하지 않는다 |
| 최신 증적 편입 | PR 본문 최신 PASS와 커밋된 1차 INCOMPLETE의 연결 누락 | 첫 실패 이력을 보존하고 jvgkgfm9 검토서·파생 요약·해시·Linux 실행 기록을 새로 편입 |
| 보호의 최종 목표 | 6대 firewalld disabled/inactive + named HEP/GNP, D34 CNI 설정 | 유지. 공존을 정상 운영 구조로 고정하지 않음 |
| HEP/GNP 적용 순서 | Bootstrap Allow → canary Worker → Worker 확대 → CP | 유지. CP1 통신 canary가 이 순서를 대체하지 않음 |
| 기존 Interface | IF-12A/12B CP Source /32·포트 제한 | 유지. /24·직접 API6443·NTP endpoint·IF-06 Prometheus를 임의 추가하지 않음 |

## 기술적 근거와 잔여 위험

[Calico 3.32 요구사항](https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements)은 firewalld 등 별도 규칙 관리자가 Calico와 간섭할 수 있어 비활성화를 권고한다. 따라서 Lead Final v4.0의 공존 전환 제안은 공식적으로 무위험한 기본 절차가 아니다. 보호 공백 방지라는 목적은 타당하지만 실제 dataplane·정책 우선순위·필수 통신·복구 경로에 대한 검증이 필요하다.

현재 관측은 이미 공존 중인 단일 CP1과 CP2/3→CP1 필수 포트 실패다. CP1 Runtime만 최소 /32로 보완하여 이 상태를 제한적으로 검증하는 준비는 가능하다. 이를 6대 동시 공존·join·HEP 적용 승인으로 확대하지 않는다. 실제 예외 실행의 대상·시간창·Owner·종료 조건을 기록해야 한다.

[firewalld Runtime/Permanent 문서](https://firewalld.org/documentation/configuration/runtime-versus-permanent.html)에 따르면 Runtime 변경은 영구 저장되지 않으며 reload/restart/boot로 사라질 수 있다. 따라서 이번 Runtime canary 중 join·재부팅·firewalld reload/restart를 동결한다. 시험 완료 후 보존·회수·보호 인계를 판단하며, 통신 의존성이 생긴 뒤 무조건 규칙을 제거하지 않는다. 2시간은 실행 허용창 상한이며 자동 소멸 규칙이 아니다.

## 실제 CP1 Runtime 실행 조건

1. v1.2의 정확한 HEAD와 실행 정책 해시를 이하영이 독립 검토하고 자신의 APPROVED 리뷰를 남긴다.
2. 기존 ICR #14에 실행 결정, 실행자, 평일 교육장 변경창, 백업·콘솔, 보호 검토·복구 계획 해시를 기록한다. 사용자 선행 착수 지시를 빈 승인 JSON을 채우는 값으로 전용하지 않는다.
3. 새 v1.2 inspect에서 CP1 단일 Node/voter·API 상태, firewall/NM snapshot, Calico Pod/HEP/GNP/Felix/IPPool, nft와 실제 보호 경로를 확인한다. 조회 실패 또는 새 정책/멤버를 발견하면 해당 상태를 검토한다. 단순 collection PASS만으로 공존 검토 PASS로 판정하지 않는다.
4. source/plan/target/input·15분 시각·전체 snapshot·topology가 일치할 때 Runtime의 CP /32→2379–2380·10250 6개 후보만 적용한다. Permanent 변경은 0건이어야 한다.
5. 새 관리 SSH, CP2/3→CP1, API readiness·etcd 상태 및 승인된 CA/Auth·기능·Negative·CNI/필수 통신 회귀를 확인한다. 불명확 결과·drift·관리/API 악화 시 확대를 중지하고 소유한 변경만 검토 후 복구한다.
6. Runtime Positive/Negative/Regression 증적과 리뷰 없이 Permanent 승격·join 확대를 하지 않는다. 이번 PR은 승격 구현을 제공하지 않는다. 인수 뒤 별도 보존/회수/인계 결정으로 연결한다.

승인 JSON은 위 인간의 기록을 입력·대상에 연결하는 장치다. 오프라인 실행기가 GitHub 승인이나 첨부 증적의 진실성을 인증하지 않는다.

## 증적·버전 계약

- 역사적 실행 소스: 068661e076de02669170d3582f3d596d6e1a8eb3, jvgkgfm9 inspect PASS. 원본 실패 2vp6t68c는 별도 이력 유지.
- 새 구현: v1.2 Runtime only, receipt schema fds.cp1-canary-receipt.v2, status RUNTIME_APPLIED_REVIEW_REQUIRED, 최대 6개 작업.
- v1 receipt의 12개 작업을 v2 결과로 바꾸지 않는다. 기존 v1 receipt는 기존 소스와 별도 복구 검토에 사용한다.
- PR #4는 구 PR #2 HEAD/helper/v1 12개 receipt를 요구한다. **v2와 호환되지 않으며 PR #4 적용 보류 유지.** 새 Runtime 인수 뒤 해당 계약과 CA-02를 별도 수정한다.
- 새 HEAD와 다른 역사적 inspect를 apply 입력으로 재사용하지 않는다. 문서 편입 커밋과 실제 실행 소스는 각각 기록한다.

## v3.1.18 편입 대상

| 편입 위치 | 반영할 내용 |
|---|---|
| Baseline §6.6 / D34 후속 기록 | 기존 설치 전 OFF 이력, 실제 공존 drift, 이번 제한적 선행 준비와 향후 승인된 예외의 대상·기간·복구·종료조건을 구분 |
| ICR v1.4 후속 / #14 | Runtime 우선·Permanent 별도, 실제 실행 결정·시험·독립 리뷰 링크, 미완료 조건 |
| #10 / #19 | CP1 단일 구성·CP2 잔여 상태·HEP/GNP 미인수·NTP 미확정 상태와 최신 증적 |
| Evidence index / Release notes / Manifest | 1차 실패와 재점검을 모두 보존하고 새 문서·소스·Linux 로그 해시 연결 |
| 후보 PR #69 | 최신 PR #1 병합, PR #2 보완 HEAD·리뷰 상태, PR #4 계약 불일치와 미실행 표시 반영 |

v18의 단순 문구 변경으로 과거 순서 위반·미실행 시험을 지우지 않는다. 다음 릴리즈는 승인된 변경과 남은 Runtime 조건을 함께 배포해야 한다.

## 결정 상태

**선행 증적 편입·구현 보완: 사용자 지시에 따라 착수. 실제 CP1 Runtime: 새 HEAD 독립 리뷰·현장 입력·실행 조건 대기. Permanent·join·HEP/GNP·전체 ICR 인수: 미완료.**

CA-01은 “정본 미발행만으로 일률 중지”에서 “근거를 기록한 제한적 선행 준비, Runtime 조건은 실제 증적으로 판정”으로 갱신했다. CA-02는 Runtime-only 구현으로 현재 PR의 선반영 경로를 제거했으며 영구 승격 완료로 표시하지 않는다.

