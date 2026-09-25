# P1-SEC-01 — SEC-01~08 Revision 대응 기록

대응 리뷰: [fds-project-baseline#19](https://github.com/321Team/fds-project-baseline/issues/19) SEC-01~08 (GWONUK, 2026-09-11 최초 지적) 및 이후 PR #7 리뷰.
연결 결정: [#19 Lead 결정 — PR #7 범위 조정](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5807412036), [infra-kubernetes#35 Lead 결정 — 회전 스킵](https://github.com/321Team/infra-kubernetes/issues/35#issuecomment-5807977867).

상태값은 baseline 리뷰 관례(OPEN → FIXED_PENDING_REVIEW → CLOSED_STATIC)를 따른다. 이 PR 제출만으로 CLOSED로 전환하지 않으며, 독립 Reviewer의 HEAD 검토 후 판정한다. 코드 상태(STATIC)와 Runtime AC 인수는 별개다.

## 현재 상태 (이 문서가 속한 HEAD 기준)

| ID | 수준 | 이 PR의 대응 | 상태 | Runtime / 잔여 |
|---|---|---|---|---|
| SEC-01 | Critical | PR 범위 제외(회전 스크립트 삭제). 원 지적(identity 우선 = 평문 저장)은 2026-09-22 최초 구성(`secretbox → identity`)으로 해소 | 원 지적 해소 / 회전 **미대응** | raw etcd `k8s:enc:secretbox:v1:key1:` 확인(아래 Evidence). 키 회전·rollback 증거 없음. `key1` 노출 이력은 잔여 위험(아래) |
| SEC-02 | High | `scripts/create-secrets-safe.sh` — 파일 기반 생성, xtrace 거부, trap 정리. Robot 이름 인자화(운영 `robot$worker`), DB Secret을 `transaction-api-db-credentials`/`DATABASE_URL`로 정정(SSL 파라미터 `require`/`verify-*` 필수 — db01 hostssl), 값 파일 끝 줄바꿈 제거 | FIXED_PENDING_REVIEW | 9/22 실제 Secret은 수동 생성. 이 스크립트 자체는 Runtime 미실행 |
| SEC-03 | High | `manifests/03-networkpolicy.yaml` — IF-24 제거, IF-06B `10.250.10.30/32:443` | CLOSED_STATIC | TLS/Auth/Payload 계약은 P1-MON-01 소관 |
| SEC-04 | High | `manifests/02-securitycontext.yaml` — selector/labels, Service 80→8000(IF-02A), 앱 UID/GID/digest 확정 | App 2개 적용 / Monitoring 5개 BLOCKED_INPUT | Monitoring 이미지 digest·UID 미확정. 이 파일은 APP/MON 원본에 적용할 참고 patch |
| SEC-05 | High | `manifests/03-networkpolicy.yaml` — namespace 공통 DNS, KSM→CP:6443, Prometheus→Worker kubelet 10250, **Prometheus→Worker node-exporter 9100을 Node IP ipBlock으로 수정**(hostNetwork라 namespaceSelector 미매칭) | FIXED_PENDING_REVIEW | 9/22 적용본의 9100 규칙은 namespaceSelector여서 실효가 없었고 운영은 MON 보완 정책(`allow-prometheus-egress-node-exporter`)으로 동작. baseline §11.5에 따라 9100은 Host Firewall과 함께 판정 |
| SEC-06 | Critical | PR 범위 제외(`manifests/calico/*`, ROLLOUT runbook 삭제). 원 지적(eth0 고정, Bootstrap/canary 부재)은 P1-K8S-01 v4 적용(ens160 HEP, 노드별 전환)으로 해소 | 원 지적 해소 | HEP/GNP 저장소 정본: main `ops/p1-k8s-01-v4/` ([PR #26](https://github.com/321Team/infra-kubernetes/pull/26), 병합 `eb55f2e`). 적용 증적: [fds-project-evidence#28](https://github.com/321Team/fds-project-evidence/pull/28) |
| SEC-07 | High | `scripts/verify-probe-failover.sh` 3차 — exit code 계약(0/1/2/3) 일치, Pod 경로를 python3 TCP + DNS 대조군으로 교체(TIMEOUT만 차단 후보), probe-failover를 EndpointSlice ready 폴링으로 교체 | FIXED_PENDING_REVIEW | Runtime 미실행. NodePort 30080 Service가 아직 없어 allow-flow/호스트 대조군은 Service 생성 전까지 NOT VERIFIED |
| SEC-08 | High | `scripts/verify-rbac.sh` — stdout·rc 쌍 판정, 쓰기 실패 중단. P1-MON-01 AC 7에 맞춰 Prometheus·KSM 공통 부정 검사(Secret 읽기·쓰기 Verb·nodes/proxy)와 API 미사용 SA 부정 검사 추가 | FIXED_PENDING_REVIEW | Runtime 미실행 |

## 범위 조정 — 2026-09-24 (P1-SEC-01 Lead 이하영)

실제 운영 클러스터에 적용되지 않은 두 묶음을 이 PR에서 제외했다(커밋 `c9ec46d`).

| 제외 파일 | 이유 | 이후 |
|---|---|---|
| `scripts/rotate-secretbox-key.sh` | 기존 config를 전제한 회전 도구. 9/22 Secretbox는 최초 구성 수동 절차로 적용했고 이 스크립트는 실행되지 않음. 리뷰 B1~B4는 이 파일에 한정 | [#35](https://github.com/321Team/infra-kubernetes/issues/35)에 인계 후 **2026-09-24 Lead 결정으로 스킵(not planned)**. 회전 도구와 키 회전은 진행하지 않는다. 필요 시 #35 재개 |
| `manifests/calico/*`, `docs/runbooks/P1-SEC-01_HEP_GNP_ROLLOUT.md` | 실제 HEP/GNP는 P1-K8S-01 v4 패키지로 적용. 후보 파일은 실제 정책과 불일치 | main `ops/p1-k8s-01-v4/` (PR #26 병합). NodePort 30080 GNP는 [PR #36](https://github.com/321Team/infra-kubernetes/pull/36) |

남은 파일 중 9/22에 실제 적용된 것은 `01-namespace`, `03-networkpolicy`(12개), `05-limitrange`, `06-rbac`, `02-securitycontext`의 앱 Deployment/Service 2쌍이다. `04-storage`(SELinux 사전 준비 미완), `02`의 Monitoring 5개, 스크립트 3개는 Runtime 미적용·미실행이며 코드 상태로만 검토한다.

## 잔여 위험 — Secretbox `key1`

- 9/22 최초 구성 때 CP 간 파일 전송 경로가 없어 키를 콘솔로 수동 입력했다. 키 값은 CP 3대의 shell history와 당시 작업 세션 기록에 평문으로 남았다.
- history 정리 증적은 control02/03만 있다(`06-history-cleanup-confirmed.txt`). control01 정리는 미확인이다.
- baseline §8은 Secretbox 키를 "root-only·Git/Evidence 제외·**백업/회전 대상**"으로 관리하도록 정한다. #35 스킵 결정과 이 요구가 충돌하므로, #19에 위험 수용 결정(범위·기한·승인자)을 기록하거나 #35를 재개해야 한다. 이 PR은 이 결정을 대신하지 않는다.

## 사후 확인 필요 (Runtime, 이 PR 범위 밖)

- `kube-apiserver.yaml.bak-preenc` 백업 위치: `/etc/kubernetes/manifests/` 안이면 kubelet이 static Pod 파일로 읽을 수 있으므로 밖으로 옮긴다.
- 9/22 적용본과 저장소 파일의 동일성: 적용 시 줄 수만 대조했다. `kubectl diff -f manifests/<file>`로 재대조한다(NetworkPolicy 12개는 9/24 운영 스냅샷과 일치 확인).
- NodePort 30080 Service를 만들 때 `externalTrafficPolicy: Local`이어야 `transaction-api-allow`의 `10.1.93.50/32` ingress와 맞는다(Cluster면 출발지가 Node IP로 SNAT됨).
- CI/CD Runner는 `get/update/patch`만 가지므로 `kubectl rollout status`(watch 필요)는 Forbidden이다. P1-CICD-01 Workflow에서 사용하지 않는지 확인한다.

## 항목별 이력 (당시 제출 기준, 현재 상태는 위 표)

### SEC-01 — etcd Secretbox (Critical)

- 기존: `identity`를 provider 1번째로 올려 재작성 → 사실상 평문 저장 경로였음.
- 2~3차: 회전 스크립트(`add-key → … → remove-old-key`)를 제출했으나 9/24 범위 조정으로 삭제. 마지막 버전은 [`2d35809`](https://github.com/321Team/infra-kubernetes/blob/2d35809313c70731c58d210b37ef29ed0a300883/scripts/rotate-secretbox-key.sh).

### SEC-02 — Secret 노출 경로 (High)

- TokenRequest 결과는 stdout이 아닌 임시 파일(권한 600)로만 저장. Harbor/DB/Grafana 값은 `--from-literal`/`--docker-password` 대신 파일 기반으로 생성해 argv/ps 노출 경로 제거.
- xtrace 실행 거부, `trap cleanup EXIT INT TERM`으로 임시 파일 `shred -u`.
- 4차(PR #7 리뷰): Robot 이름 인자화, DB Secret 이름·키 정정(`db-url`), 값 파일 끝 줄바꿈 제거·여러 줄 거부.
- 4차 보강(이재환 리뷰 2026-09-25, DB 담당 확인): db01 `pg_hba.conf`가 `hostssl … fds_app … scram-sha-256`만 허용하므로 `db-url`은 `sslmode=`/`ssl=` 중 `require`·`verify-ca`·`verify-full`이 있어야 하고, 없거나 `disable`·`allow`·`prefer` 등이면 Secret 생성 전에 거부한다. 계정명(`fds_app`)은 URL 입력값이며 스크립트에 고정하지 않는다.

### SEC-03 — IF-24 → IF-06B (High)

- baseline `IF-24 RETIRED`, D34 `IF-06B 유지`. `alertmanager-allow`의 monitor01 egress 제거, `prometheus-remote-write-allow`(→ `10.250.10.30/32:443`)로 교체.

### SEC-04 — 미완성 Manifest (High)

- 6 Deployment + 1 DaemonSet에 selector/labels, Service 80→8000(IF-02A).
- 9/22 앱 2개 UID 10001 / GID 999 / Harbor digest 실측 반영, `DATABASE_URL` secretKeyRef 추가.
- PR #7 리뷰: Prometheus `--config.file`/`--storage.tsdb.path`·resources, KSM `--resources` 제한, node-exporter non-root·seccomp·RO rootfs·Collector 제한 보강(Monitoring은 여전히 참고 patch).

### SEC-05 — Default Deny egress (High)

- namespace 공통 DNS(UDP/TCP 53), KSM→CP 6443, Prometheus→Worker kubelet 10250(Worker 개별 IP).
- PR #7 리뷰: node-exporter가 hostNetwork라 namespaceSelector 규칙이 매칭되지 않음을 확인하고 Worker Node IP ipBlock으로 수정.

### SEC-06 — Calico HEP (Critical)

- 2~3차 후보 manifest·ROLLOUT runbook은 9/24 범위 조정으로 삭제. 운영 정본은 v4 패키지.

### SEC-07 — Probe/Deny 검증 (High)

- 1~2차: 대상 IP 교정, 도구 부재·부분 성공을 PASS로 보지 않음, 대조군 도입.
- 3차(PR #7 리뷰): 위 표 참조.

### SEC-08 — RBAC 검증 (High)

- 1~2차: `--subresource` 구문, stdout·rc 쌍 판정, TSV 쓰기 실패 중단, API timeout.
- 3차(PR #7 리뷰): 부정 검사 확대(위 표).

## 남은 미확정 값

- Monitoring 5개 `<실제-버전>` / `<확인된-UID>` / `<확인된-GID>` — 승인 Harbor digest 확정 후 해당 digest의 실제 identity로 반영.
- Prometheus resources 초기값 — MON Lead 확정.
- node-exporter 비활성 Collector 목록 — MON Lead 확정(P1-MON-01 AC 6 Evidence).

## Evidence

- 9/22 Runtime 실행 기록: `docs/evidence/P1-SEC-01/GATE2_RUNTIME_EXECUTION_20260922.md`(당시 기록 보존). 정본 사본은 [fds-project-evidence#33](https://github.com/321Team/fds-project-evidence/pull/33)(병합 `455d1c1`)이며, 원본 로그 제출은 [evidence#34](https://github.com/321Team/fds-project-evidence/issues/34)에서 추적한다.
- 이 문서는 식별자와 참조만 남긴다. 원본 로그·비밀값은 포함하지 않는다.
