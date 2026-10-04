# OID-02 preloaded 입력 반영 — 2026-10-04

상태: PR #54 병합 완료 / 이미지 증빙 미완 / Runtime HOLD. 범위: Phase 1, ICR OID-02.

## 결정과 구현

[Owner 결정](https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-5977963213)에 따라
`ops/p1-k8s-01-v4/config/runtime-input.json`의 `system_images.mode`를
`null`에서 `preloaded`로 바꾼다. renderer는 기존 구현을 그대로 사용한다.
현재 패키지의 PACKAGE_SHA256SUMS에서 해당 입력 파일 해시만 갱신한다.
역사적 SHA256SUMS / MODIFIED_SHA256SUMS / REVIEW_FIX_SHA256SUMS는 수정하지 않는다.

모드 결정 반영은 실행 가능한 입력 완성을 뜻하지 않는다. cluster_uid, attempt_id,
MTU, interface, probe image, 노드별 승인 입력과 증빙 path/hash는 기존 미확정 상태다.
결정 댓글을 실제 이미지 반입 검증 증빙으로 대체하지 않는다.

## 네트워크 경계

- 모든 노드의 registry_nets는 빈 배열을 유지한다. 기존 validator는 preloaded 모드에서 registry_nets 추가를 거부한다.
- 시스템 이미지 공급을 위한 CP→Harbor IF-10 확대, 외부 registry 허용, Harbor 설정 변경은 없다.
- 기존 Worker→Harbor 애플리케이션 이미지 공급 허용은 유지된다.
- OID-03 Bootstrap Package/Registry Egress는 별도 미해결 항목이다.

## OID-02 완료조건 추적

| 항목 | 현재 상태 / 다음 근거 |
| --- | --- |
| 모드·사유 | Owner 결정 및 입력 반영 완료; 이미지 증빙 미완 |
| 경로 승인 | B/IF-10 확대와 C/외부 egress를 사용하지 않는 경계에 대한 승인자 확인 필요 |
| renderer 입력 | PR #54 승인·병합 완료 (`c51a9ff5f8eff104915f5f8fee41906b077b2b2a`) |
| 이미지 목록·digest·출처 | 6대 노드의 전체 digest, 반입 원본 위치·해시·검증 방법 필요 |
| BLOCK 해제 | 위 항목 및 독립적인 OID-03 처분 근거 필요; 자동 해제하지 않음 |

Owner 관측의 공백은 kube-controllers의 control02/03 재스케줄 대비,
CoreDNS의 Worker 스케줄 대비, 재구성·업그레이드용 반입 원본이다.
[추가 관측](https://github.com/321Team/fds-project-baseline/issues/14#issuecomment-5977996917)의
worker01 Harbor 이름과 다른 노드 quay 이름은 전체 digest와 출처를 대조해야 한다.
이름이나 digest 접두부만으로 동일성·출처를 확정하지 않는다.

## 검증

기준 main: `b4303f2df512a8791d2421203cf159567d076b2a`.

- `python3 ops/p1-k8s-01-v4/tests/validate.py PolicyTests -v`: 9개 통과.
- 기존 flow matrix, CP Harbor 차단 / Worker Harbor 허용, 미완성 입력 출력 차단, 증빙 해시 검증 포함.
- synthetic fixture에서 preloaded + registry_nets를 추가한 입력 거부 확인.
- config와 현재 PACKAGE_SHA256SUMS 항목의 SHA-256 일치 확인.

전체 운영 테스트, 실제 노드 이미지 검증, 클러스터 적용은 수행하지 않았다.
OID-02 Owner 이하영 / Lead 이권욱, 기존 Due 2026-10-07 유지.
Phase 1 Close 2026-10-06 전에 미해결 항목의 처리 또는 명시적 수용·이월 판단이 필요하며,
이 문서는 그 승인을 대신하지 않는다.

후속 수집·대조: [OID-02 이미지 증빙 인계](OID02_IMAGE_EVIDENCE_HANDOFF_20261004.md).
