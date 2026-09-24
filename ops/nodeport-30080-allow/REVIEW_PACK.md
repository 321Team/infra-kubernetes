# #36 리뷰 대조 팩 — 2026-09-24 14:54 KST

목적: 독립 리뷰어가 후보 spec·#28 시점·현장 14:47 조회·이 PR가 판정하지 않는 항목을 같은 디렉터리에서 보게 한다.

판정 대상 HEAD: 이 PR HEAD(팩 포함 커밋). 작성 GWONUK. 작성자 승인 아님.

## 1. 이 PR에서 판정할 항목

| 질문 | 판정 | 근거 |
|---|---|---|
| 후보 YAML vs 9/22 기록 YAML | **일치** | 절 3 |
| 후보 YAML vs 14:47 live spec | **일치** | [`FIELD_20260924-144753.md`](FIELD_20260924-144753.md) |
| selector vs #28 Worker HEP 3개 | **일치** | `fds.example/role: worker` |
| v4 host GNP에 30080 편입 | **불가(기록)** | v4 worker GNP `applyOnForward: false`, order 100, preDNAT 없음 |
| #28 스냅샷에 이 GNP | **없음** | 6행. README 참고 4 |
| 14:47 현장 GNP | **존재** | created `2026-09-22T02:11:55Z`, generation 1, 목록 7행 |
| Service NodePort 30080 | **없음** | `NO_30080_IN_SVC` |
| .50 양성/음성 | **NOT VERIFIED** | 리스너 없음 |
| #19 승인·edge01 재개 | **아님** | 범위 밖 |

## 2. 원본

| 자료 | 위치 |
|---|---|
| 14:47 현장 판정 | [`FIELD_20260924-144753.md`](FIELD_20260924-144753.md) |
| 14:47 live spec 인용 | [`excerpts/01-gnp-live-20260924.txt`](excerpts/01-gnp-live-20260924.txt) |
| 9/22 Runtime 기록 | [GATE2_RUNTIME_EXECUTION_20260922.md](https://github.com/321Team/infra-kubernetes/blob/c9ec46d619bf46ae9e4ad221b632494ee67d893f/docs/evidence/P1-SEC-01/GATE2_RUNTIME_EXECUTION_20260922.md) blob `0f020fbf` |
| Gate2 GNP 목록 | [03-gnp-list.txt](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/03-gnp-list.txt) `11e28a82` |
| Gate2 HEP | [08-hep-full.yaml](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/08-hep-full.yaml) |
| v4 Worker GNP | [k8s-worker01.gnp.yaml.in](https://github.com/321Team/infra-kubernetes/blob/26feb8a21b7b37fb084ed866f62829d90053194b/ops/p1-k8s-01-v4/policy_templates/k8s-worker01.gnp.yaml.in) |

## 3. spec (후보 = live)

```
name: nodeport-30080-allow
uid: f9de0f2e-aa29-4545-8d46-ea2e31c4a3f0
creationTimestamp: 2026-09-22T02:11:55Z
generation: 1
selector: fds.example/role == 'worker'
order: 50
applyOnForward: true
preDNAT: true
types: [Ingress]
Allow TCP 10.1.93.50/32 → 30080
Deny TCP 30080
```

## 4. #28 vs 14:47 목록

#28: host GNP 6개만.
14:47: 같은 6개 + `nodeport-30080-allow` 2d3h.

## 5. 시점

9/22 스냅샷 수집 후 같은 날 11:11 KST 적용. 14:47에 generation 1 그대로 조회.

## 6. 이번 판정하지 말 것

경로 시험, #19 인수, v4 6쌍 수정, Runtime apply, 테스트 워크로드 생성.
