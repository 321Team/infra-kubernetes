# NodePort 30080 GNP 후보 (main 기준)

상태: **후보 spec은 2026-09-24 14:47 live spec과 일치.** 이 디렉터리 편입 자체가 apply·Gate2 인수·#19 승인·edge01 재개가 아님.

리뷰: [`REVIEW_PACK.md`](REVIEW_PACK.md). 현장: [`FIELD_20260924-144753.md`](FIELD_20260924-144753.md).

## 왜 여기인가

- #21는 #7 `manifests/calico/05` 수정. #7 HEAD `c9ec46d`가 디렉터리 제거 → dirty.
- 9/22 현장 생산자는 v4 HEP/GNP 6쌍 + 별도 NodePort GNP.
- #26에 이 spec을 섞지 않는다.

## 후보 spec

| 필드 | 값 |
|---|---|
| name | `nodeport-30080-allow` |
| selector | `fds.example/role == 'worker'` |
| order | 50 |
| preDNAT / applyOnForward | true / true |
| Allow | TCP `10.1.93.50/32` → 30080 |
| Deny | 나머지 TCP 30080 |

## 증적 경계

- 14:47 live `-o yaml` rc=0. created `2026-09-22T02:11:55Z`, generation 1.
- #28 `03-gnp-list`에는 이 GNP 없음(스냅샷 이후 적용).
- Service NodePort 30080 없음. 양성/음성 NOT VERIFIED.

## 적용 전 중단 (이 PR 범위 밖)

독립 리뷰 후 SEC Lead 적용 판단. 현장 spec이 후보와 같으면 무변경. #19 승인 전 edge01 재개 금지.
