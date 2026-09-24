# #36 리뷰 대조 팩 — 2026-09-24 13:50 KST

목적: 독립 리뷰어가 **후보 spec이 9/22 기록·v4 HEP와 일치하는지**, **#28 스냅샷과 시점이 다른지**, **이 PR가 판정할 수 없는 항목**을 같은 디렉터리에서 보게 한다.

판정 대상 HEAD: 이 PR HEAD.
작성: GWONUK. 작성자 독립 승인 아님.

## 1. 이 PR에서 판정 가능

| 질문 | 판정 | 근거 |
|---|---|---|
| 후보 YAML spec이 9/22 적용 기록 YAML과 같은가 | **일치** | 아래 절 3 |
| selector가 v4 Worker HEP 3개와 맞는가 | **일치(#28 `08-hep-full.yaml`)** | `fds.example/role: worker` × 3 |
| v4 host GNP에 30080을 끼워 넣을 수 있는가 | **불가(기록상)** | v4 worker GNP `applyOnForward: false`, `order: 100`, preDNAT 없음 |
| 이 후보가 Gate2 최종 스냅샷에 있는가 | **없음** | #28 `03-gnp-list.txt` 6행. README 참고 4 |
| 2026-09-24 현장 GNP 존재·spec | **미확인** | 원본 `-o yaml` 미수신. 수집은 `RUN_ONEPASS.md` A |
| edge01→30080 양성/음성 | **NOT VERIFIED** | `RUN_ONEPASS.md` C. 리스너 없으면 실행 금지 |
| #19 승인·edge01 재개 | **아님** | 범위 밖 |

## 2. 원본 위치 (복제본이 아님)

| 자료 | 위치 | SHA |
|---|---|---|
| 9/22 Runtime 실행 기록 | [GATE2_RUNTIME_EXECUTION_20260922.md](https://github.com/321Team/infra-kubernetes/blob/c9ec46d619bf46ae9e4ad221b632494ee67d893f/docs/evidence/P1-SEC-01/GATE2_RUNTIME_EXECUTION_20260922.md) | blob `0f020fbf`, #7 HEAD `c9ec46d` |
| 9/22 selector 코멘트 | [PR #21 comment 5774948640](https://github.com/321Team/infra-kubernetes/pull/21#issuecomment-5774948640) | — |
| Gate2 최종 GNP 목록 | [03-gnp-list.txt](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/03-gnp-list.txt) | blob `37df73b4`, `11e28a82` |
| Gate2 최종 HEP 전문 | [08-hep-full.yaml](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/08-hep-full.yaml) | blob `08b7d093` |
| 스냅샷 범위 | [gate2-final README](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/README.md) 참고 4 | blob `84437167` |
| v4 Worker HEP | [k8s-worker01.hep.yaml.in](https://github.com/321Team/infra-kubernetes/blob/26feb8a21b7b37fb084ed866f62829d90053194b/ops/p1-k8s-01-v4/policy_templates/k8s-worker01.hep.yaml.in) | #26 `26feb8a` |
| v4 Worker GNP | [k8s-worker01.gnp.yaml.in](https://github.com/321Team/infra-kubernetes/blob/26feb8a21b7b37fb084ed866f62829d90053194b/ops/p1-k8s-01-v4/policy_templates/k8s-worker01.gnp.yaml.in) | blob `48fdd361` |

## 3. spec 대조

```
name: nodeport-30080-allow
selector: fds.example/role == 'worker'
order: 50
applyOnForward: true
preDNAT: true
types: [Ingress]
Allow TCP 10.1.93.50/32 → 30080
Deny TCP 30080
```

`nodeport-30080-allow.yaml` spec은 위와 동일. 주석만 추가.
Gate2 문서·코멘트는 `-o yaml` 전문이 아니다.

## 4. #28 스냅샷

GNP 6개만. `nodeport-30080-allow` 없음.
Worker HEP 3개 role=`worker`, IP .105/.107/.108, NIC ens160.

## 5. 시점

9/22 스냅샷 수집 후에 NodePort GNP가 적용됐다(README 참고 4).
2026-09-24 현장 재조회는 이 팩 작성 시점 기준 **NOT RUN**.

## 6. 현장 수집 (잔여 착수)

명령: [`RUN_ONEPASS.md`](RUN_ONEPASS.md). 동작 환경에서는 10.1.93.0/24 미접속.

1. A control01: GNP `-o yaml`, HEP, Service/NodePort 30080 존재
2. B ansible01: Worker:22, `.109:6443`
3. C edge01: A에 30080 리스너가 있을 때만. 없으면 NOT_VERIFIED

#28 `02-pods-final.txt`에는 fds-app Pod가 없다. C를 PASS로 가정하지 말 것.

## 7. 안 넣는 것

합성 `-o yaml`, Runtime apply, v4 6쌍 수정, 테스트 워크로드 생성.
