# CP Join 2026-09-18 — ARCHIVAL ONLY / DO NOT RUN

이 디렉터리는 infra-kubernetes PR #16의 실제 실행 소스와 그 직계 실행 결과를 추적성 보존 목적으로 main에 편입한 **역사 기록**이다.

## 실행 금지

- **DO NOT RUN / 재실행 금지.**
- 현재 클러스터 상태에 대한 실행 승인, Runtime 변경 지시, 재조인 절차가 아니다.
- 이 파일들의 존재를 현재 P1-K8S-01 Runtime 승인 또는 AC PASS로 해석하지 않는다.

## Provenance

- original PR: #16 `[CP Join] CP2 정리·CP2→CP3 Join·성공 유지와 실패 회수 통합`
- reviewed execution source: `bab9608557205b3bf5294b729d0960517d7a2847`
- independent review: hayeounglee APPROVED, review `5243927101`
- result/evidence commit: `5e9e09e7a1820c230bb5724ce05897c7689ef065`
- result commit parent: `bab9608557205b3bf5294b729d0960517d7a2847`
- `RUN_CP_JOIN.py` SHA256 recorded at execution: `460b5f8603fe2798e6f379b85efd5cb56926fd9f37308fb961c204e14c6d8147`
- execution window: 2026-09-18 KST

## Historical result boundary

당시 결과 증적은 CP2/CP3 Join 성공, 최종 3 Control Plane Ready, etcd 3 voter / endpoint health 3/3, 임시 인증정보 회수를 기록한다. 이는 **당시 실행 결과**이며 현재 상태를 자동 보증하지 않는다.

실패 복구 시험, reboot/permanent 지속성, Worker/LB 장애전환, 이후 HEP/GNP 변경, 현재 main의 후속 변경은 별도 검증 범위다.

## Preservation method

이 archival PR은 result commit `5e9e09e7...` 트리의 `ops/cp-join-20260918/**` blob을 이 경로로 그대로 재사용한다. 따라서 원본 source 및 result 파일 내용은 수정하지 않고 경로만 archival namespace로 이동해 보존한다.
