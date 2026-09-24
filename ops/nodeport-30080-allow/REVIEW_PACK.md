# #36 리뷰 대조 팩 — 2026-09-24 13:50 KST

목적: 독립 리뷰어가 **후보 spec이 9/22 기록·v4 HEP와 일치하는지**, **#28 스냅샷과 시점이 다른지**, **이 PR가 판정할 수 없는 항목**을 같은 디렉터리에서 보게 한다.

판정 대상 HEAD: 이 PR HEAD. 편입 SHA는 커밋 후 `git rev-parse HEAD`로 확정.
작성: GWONUK. 작성자 독립 승인 아님.

## 1. 이 PR에서 판정 가능

| 질문 | 판정 | 근거 |
|---|---|---|
| 후보 YAML spec이 9/22 적용 기록 YAML과 같은가 | **일치** | 아래 절 3. name/selector/order/preDNAT/applyOnForward/Allow·Deny 필드 |
| selector가 v4 Worker HEP 3개와 맞는가 | **일치(#28 `08-hep-full.yaml`)** | `fds.example/role: worker` × 3. CP HEP는 `control-plane` |
| v4 host GNP에 30080을 끼워 넣을 수 있는가 | **불가(기록상)** | v4 worker GNP `applyOnForward: false`, `order: 100`, preDNAT 없음. Gate2 문서가 분리 GNP 사유를 적음 |
| 이 후보가 Gate2 최종 스냅샷에 있는가 | **없음** | #28 `03-gnp-list.txt` 6행만. README 참고 4 |
| 2026-09-24 현장 GNP 존재·spec | **미확인** | 원본 `-o yaml` 미수신 |
| edge01→30080 양성/음성 | **NOT VERIFIED** | Workload 없었고 시험 미실행 |
| #19 승인·edge01 재개 | **아님** | 범위 밖 |

## 2. 원본 위치 (복제본이 아님)

| 자료 | 위치 | SHA |
|---|---|---|
| 9/22 Runtime 실행 기록 | [GATE2_RUNTIME_EXECUTION_20260922.md](https://github.com/321Team/infra-kubernetes/blob/c9ec46d619bf46ae9e4ad221b632494ee67d893f/docs/evidence/P1-SEC-01/GATE2_RUNTIME_EXECUTION_20260922.md) “후속 — NodePort(30080)” | 파일 blob `0f020fbf`, 포함 트리 #7 HEAD `c9ec46d` |
| 9/22 selector 실측 코멘트 | [PR #21 comment 5774948640](https://github.com/321Team/infra-kubernetes/pull/21#issuecomment-5774948640) | — |
| Gate2 최종 GNP 목록 | [03-gnp-list.txt](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/03-gnp-list.txt) | blob `37df73b4`, repo `11e28a82` |
| Gate2 최종 HEP 전문 | [08-hep-full.yaml](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/08-hep-full.yaml) | blob `08b7d093` |
| 스냅샷 범위 설명 | [gate2-final README](https://github.com/321Team/fds-project-evidence/blob/11e28a82c5c60f4a2290aac3d8ce6cca8f66f8f5/evidence/P1-K8S-01/gate2-final-20260922/README.md) 참고 4 | blob `84437167` |
| v4 Worker HEP 템플릿 | [k8s-worker01.hep.yaml.in](https://github.com/321Team/infra-kubernetes/blob/26feb8a21b7b37fb084ed866f62829d90053194b/ops/p1-k8s-01-v4/policy_templates/k8s-worker01.hep.yaml.in) | #26 HEAD `26feb8a`, blob `cb949e91` |
| v4 Worker GNP 템플릿 | [k8s-worker01.gnp.yaml.in](https://github.com/321Team/infra-kubernetes/blob/26feb8a21b7b37fb084ed866f62829d90053194b/ops/p1-k8s-01-v4/policy_templates/k8s-worker01.gnp.yaml.in) | blob `48fdd361` |

이 팩의 `excerpts/`는 위 원본의 필요 행만 인용한다. 증적 저장소 정본은 evidence 저장소다.

## 3. spec 대조

Gate2 기록 YAML(적용 내용 인용) 필드:

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

`nodeport-30080-allow.yaml` spec 블록은 위와 동일하다. 주석만 추가됐다.

#21 코멘트 5774948640 기록: `order: 50`, `preDNAT: true`, `applyOnForward: true`, `selector: "fds.example/role == 'worker'"`.

**한계:** Gate2 문서와 코멘트는 적용 후 `-o yaml` 전문 원본이 아니다. metadata.uid·resourceVersion·creationTimestamp는 확인할 수 없다.

## 4. #28 스냅샷에서 확인한 HEP/GNP

GNP 6개(`03-gnp-list.txt` 전문):

```
NAME                         AGE
fds-k8s-host-k8s-control01   9h
fds-k8s-host-k8s-control02   9h
fds-k8s-host-k8s-control03   9h
fds-k8s-host-k8s-worker01    9h
fds-k8s-host-k8s-worker02    9h
fds-k8s-host-k8s-worker03    9h
```

`nodeport-30080-allow` 행 **없음**.

Worker HEP 3개(`08-hep-full.yaml`):

| name | node | IP | NIC | role |
|---|---|---|---|---|
| fds-k8s-k8s-worker01 | k8s-worker01 | 10.1.93.105 | ens160 | worker |
| fds-k8s-k8s-worker02 | k8s-worker02 | 10.1.93.107 | ens160 | worker |
| fds-k8s-k8s-worker03 | k8s-worker03 | 10.1.93.108 | ens160 | worker |

CP 3개 role는 `control-plane`. 후보 selector는 Worker 3개에만 걸린다.

v4 템플릿(#26, 적용 금지) Worker GNP: `order: 100`, `applyOnForward: false`, selector는 `fds.example/bootstrap == "p1-k8s-01-v4" && fds.example/node == "k8s-worker0N"`. 30080/preDNAT 없음.

## 5. 시점

| 시점 | 사실 |
|---|---|
| 9/22 Gate2 전환 중·직후 | v4 HEP/GNP 6쌍. #28 스냅샷 |
| 9/22 후속(같은 실행 기록 내) | `nodeport-30080-allow` 클러스터 직접 적용. 저장소 파일은 그때 미수정 |
| 9/22 스냅샷 수집 | NodePort GNP **미포함**(스냅샷 README 참고 4) |
| 9/22 PR #21 | 저장소 05를 order 50·selector 동기화. base=#7 |
| 9/24 | #7이 `manifests/calico` 삭제 → #21 dirty → #21 Close → #36 |
| 9/24 13:50 KST | 이 팩 작성. 현장 재조회 **NOT RUN** |

## 6. 리뷰어가 더 받아야 판정할 수 있는 항목

아래는 이 PR 범위 밖이다. 필요 명령은 `field-collect.sh`.

1. `kubectl get globalnetworkpolicy nodeport-30080-allow -o yaml` 전문(있음/없음·spec diff)
2. Worker HEP 3개 라벨 재확인
3. ansible01에서 SSH/6443/4789 회귀(이 GNP와 무관한 기존 경로)
4. Workload가 있으면 .50 양성·비승인 Source 음성

## 7. 이 PR에 넣지 않은 것

- 합성 `-o yaml`
- #28에 없는 GNP를 있었던 것처럼 추가
- v4 6쌍 수정
- Runtime apply
