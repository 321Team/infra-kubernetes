# NodePort 30080 GNP 후보 (main 기준)

상태: **후보 spec / Runtime 미적용 / v4 자동배포 미편입.**
이 디렉터리 편입이 클러스터 변경, Gate 2 인수, P1-SEC-01 #19 승인, edge01 마이그레이션 재개가 아니다.

리뷰 대조: [`REVIEW_PACK.md`](REVIEW_PACK.md).

## 왜 여기인가

- 이전 PR #21는 PR #7 브랜치 `manifests/calico/05-nodeport-allow.yaml`을 수정했다.
- PR #7 HEAD `c9ec46d`가 `manifests/calico/`를 제거해 #21 base가 `dirty`가 되었다.
- 9/22 현장 보호 생산자는 `p1-k8s-01-v4` HEP/GNP 6쌍이다. 구 HEP 템플릿과 05를 섞으면 selector가 0건이다.
- v4 수정 스크립트 PR #26에 이 spec을 섞지 않는다. #26은 `audit-window.py`/`cluster-change.py` 차단이 남아 있다.

## 후보 spec (9/22 기록과 동일)

| 필드 | 값 |
|---|---|
| name | `nodeport-30080-allow` |
| selector | `fds.example/role == 'worker'` |
| order | 50 |
| preDNAT | true |
| applyOnForward | true |
| Allow | TCP `10.1.93.50/32`(edge01) → 30080 |
| Deny | 나머지 TCP 30080 |

대상 HEP: v4 `fds-k8s-k8s-worker01/02/03`. 구 템플릿 `role=k8s-worker`와는 매칭되지 않는다.

## 증적 경계

- 9/22 적용 기록: PR #7 `GATE2_RUNTIME_EXECUTION_20260922.md` (HEAD `c9ec46d`), PR #21 코멘트 5774948640.
- Gate 2 최종 스냅샷 `fds-project-evidence` main `11e28a82` `03-gnp-list.txt`에는 이 GNP가 **없다**. 스냅샷 README 참고 4: "이후 적용된 변경(예: NodePort 30080 GNP)은 포함하지 않는다."
- 2026-09-24 현장 재조회·원본 `kubectl get ... -o yaml`는 이 PR에 없다.

## 적용 전 중단 조건

1. 독립 리뷰가 이 디렉터리 SHA를 승인하고 SEC Lead가 적용 판단을 남긴다.
2. 읽기 전용으로 v4 Worker HEP 3개의 이름·node·interfaceName·expectedIPs·라벨과 현장 GNP spec을 대조한다. 0개·누락·중복·다른 생산자면 중단.
3. 현장 spec이 이 후보와 같으면 무변경 종료. 차이 있으면 해당 GNP 원본만 증적 보존 후 단일 변경만 검토한다.
4. .50/32 양성, 비승인 Source 음성, ansible01 관리 경로·기존 SSH/VXLAN/API 회귀. Workload가 없으면 미검증으로 남긴다.
5. 실패 시 이번 GNP 변경 전 spec으로 복구. v4 HEP/GNP 6쌍·firewalld를 임의 변경하지 않는다.

P1-SEC-01 #19 정식 승인 전에 edge01 마이그레이션을 재개하지 않는다.
