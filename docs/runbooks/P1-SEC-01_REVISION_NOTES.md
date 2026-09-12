# P1-SEC-01 — SEC-01~08 Revision 대응 기록

> 1차 대응 리뷰: `fds-project-baseline#19` 실제 지적 댓글 (issuecomment-5633339292, GWONUK, 2026-09-11).
> 이전 PR 코멘트에 5627308483(하영님 구현 제출 댓글)으로 잘못 링크했던 부분은 정정한다.
> 2차 재검토: issuecomment-5644891728 (GWONUK, 2026-09-12), 판정 HOLD / Revision Required, 대상 HEAD 4454098.
> 이 문서는 PR 제출 시점의 대응 상태를 기록한다. Runtime PASS/FAIL 판정은 이 PR이 부여하지 않으며,
> 이권욱님 최종 승인과 실제 클러스터(P1-K8S-01 Platform Ready) 이후의 Runtime 인수로 별도 확정한다.

## 대응 요약 (2차 반영 후)

| ID | 수준 | 1차 리뷰 지적 | 2차 재검토 지적 (HEAD 4454098) | 수정 파일 | 상태 |
|---|---|---|---|---|---|
| SEC-01 | Critical | identity 우선 재작성 = 평문 저장, static Pod mount 미제출 | 키 내용/길이/CP 동일성 미검증, 구조적 mount 대조 아님, 실제 재시작 미확인, 무한 대기, raw 검증기가 구 키도 PASS | `scripts/rotate-secretbox-key.sh` | FIXED_PENDING_REVIEW (2차) |
| SEC-02 | High | TokenRequest stdout, `--docker-password`, `--from-literal` 노출 경로 | umask 늦음, 고정 /tmp 경로, set -e로 정리 미도달 | `scripts/create-secrets-safe.sh`, `scripts/rotate-secretbox-key.sh` | FIXED_PENDING_REVIEW (2차) |
| SEC-03 | High | RETIRED된 IF-24 허용, 현행 IF-06B 미반영 | 방향 수정은 인정(CLOSED_STATIC), monitor01 실주소 등 INPUT HOLD | `manifests/03-networkpolicy.yaml` | INPUT HOLD (코드 변경 불필요, 외부 입력 대기) |
| SEC-04 | High | selector/labels 누락, Monitoring image 누락 | selector/labels 인정(PARTIAL), Service 80→8000(IF-02A) 위반, "완성" 주장 철회 필요 | `manifests/02-securitycontext.yaml` | 1차 FIXED, 2차 IF-02A 포트 수정 완료 / 나머지(UID·PVC 등) BLOCKED_INPUT |
| SEC-05 | High | Default Deny 이후 DNS/API discovery/kubelet egress 불완전 | monitoring-api DNS 허용 없음, API endpoint 미완성 | `manifests/03-networkpolicy.yaml` | FIXED_PENDING_REVIEW (2차) |
| SEC-06 | Critical | eth0 고정이 실측과 불일치, Bootstrap/canary 절차 없음 | 1개 파일에 GNP+HEP 3개 혼재로 canary 순서 붕괴, Bootstrap 규칙 자체 모순(BGP Disabled인데 TCP179) | `manifests/calico/*.yaml`(신규 분리), `docs/runbooks/P1-SEC-01_HEP_GNP_ROLLOUT.md` | FIXED_PENDING_REVIEW (2차) |
| SEC-07 | High | 테스트 대상 IP 오류(.109) | nc/curl 부재·부분 실패·timeout을 PASS로 오판 | `scripts/verify-probe-failover.sh` | FIXED_PENDING_REVIEW (2차) |
| SEC-08 | High | `auth can-i` TYPE/NAME과 subresource 구문 혼동 | API 통신 오류를 "no"로 오인해 전체 PASS | `scripts/verify-rbac.sh` | FIXED_PENDING_REVIEW (2차) |

상태값은 baseline 리뷰 관례(OPEN → FIXED_PENDING_REVIEW → CLOSED_STATIC)를 따른다.
이 PR 제출만으로 CLOSED로 전환하지 않으며, 독립 Reviewer의 HEAD 검토 후 판정한다.
2026-09-13 기준 SEC-01/02/04/05/06/07/08 모두 2차 수정을 제출했다. SEC-03만 외부 입력
대기(INPUT HOLD)로 남아있다.

## 항목별 변경 내용

### SEC-01 — etcd Secretbox 키 회전 (Critical)

- 기존: `identity`를 provider 1번째로 올려 재작성 → 사실상 평문 저장 경로였음.
- 수정: `scripts/rotate-secretbox-key.sh`가 6단계(`add-key → verify-decrypt → promote-key →
  rewrite-secrets → verify-encryption → remove-old-key`)로 분리, 각 단계 수동 확인 후 진행.
- static Pod manifest의 `--encryption-provider-config` 플래그·volume/mount 연결을 `add-key`
  단계에서 스크립트가 직접 grep 검증.
- 원문 비노출 검증은 hexdump grep 대신 Python으로 raw bytes를 파싱해 `k8s:enc:secretbox:v1:<key>:`
  접두어와 key name을 정확히 추출 (16바이트 행 경계 오판 방지).

### SEC-02 — Secret 노출 경로 (High)

- `scripts/create-secrets-safe.sh`: TokenRequest 결과는 stdout이 아닌 임시 파일(권한 600)로만 저장.
  Harbor/DB/Grafana 비밀번호는 `--from-literal`/`--docker-password` 대신 파일 기반(`--from-file`,
  직접 구성한 `dockerconfigjson`)으로 생성해 argv/ps 노출 경로 제거.
- xtrace(`bash -x`) 실행 시 스크립트 자체가 거부하도록 가드 추가.
- `trap cleanup EXIT INT TERM`으로 중단 시 임시 파일 자동 폐기(`shred -u`).

### SEC-03 — IF-24 → IF-06B 전환 (High)

- baseline 근거: `FDS_Project_Implementation_Baseline_v3.1.18.md` L1063 "IF-24 ... RETIRED",
  L1002 D34 "IF-06B 유지".
- `manifests/03-networkpolicy.yaml`: `alertmanager-allow`의 monitor01 egress(Webhook) 제거,
  별도 `prometheus-remote-write-allow`(IF-06B, Prometheus → monitor01 TCP 443)로 교체.

### SEC-04 — 미완성 Manifest (High)

- `manifests/02-securitycontext.yaml`: 6 Deployment + 1 DaemonSet 전체에 `spec.selector`,
  `template.metadata.labels` 추가. Monitoring 5개 워크로드에 `image` 필드 명시(버전은
  `<실제-버전>` placeholder — 실측 확인 전 적용 금지).
- transaction-api/fds-engine에 대응 Service 추가로 독립 apply 가능하도록 완성.

### SEC-05 — Default Deny egress 구멍 (High)

- `manifests/03-networkpolicy.yaml`: DNS(UDP/TCP 53, kube-dns Pod 경로), kubelet(10250, Host 경로
  — `<Worker-Node-CIDR>` ipBlock으로 명시), IF-06B Remote Write(443)를 각각 분리된
  NetworkPolicy로 완성. `namespaceSelector`만으로 hostNetwork 경로를 커버하지 못하는 문제를
  ipBlock 병행으로 해결.

### SEC-06 — Calico HEP eth0 매핑 (Critical)

- `manifests/04-calico-hep-gnp.yaml`: `interfaceName`을 `<실제-NIC-이름>` placeholder로 변경,
  적용 전 `ip -o link show`로 실측 필수.
- `docs/runbooks/P1-SEC-01_HEP_GNP_ROLLOUT.md`: Bootstrap Allow → canary Worker → Worker 확대 →
  CP → firewalld 최종 비활성화 순서와 각 단계 회귀시험·롤백 절차를 문서화.

### SEC-07 — 테스트 대상 IP 오류 (High)

- `scripts/verify-probe-failover.sh`: `10.1.93.109:30080` → 실제 Worker(`10.1.93.105/107/108`)로 교체.
- `allow-flow`/`deny-flow`/`probe-failover` 3개 서브커맨드로 분리, "도구 부재"와 "정책 차단
  성공"을 구분해 기록(WARN vs PASS/FAIL). 모든 Endpoint 제외 시에만 503이 강제된다는 점을
  주석으로 명시하고 해석을 단정하지 않음.

### SEC-08 — `auth can-i` 구문 오류 (High)

- `scripts/verify-rbac.sh`: `nodes/metrics` 같은 TYPE/NAME 결합 대신 `--subresource=metrics`
  플래그로 교정. 예상 거부(`no`)의 non-zero exit code를 정상 케이스로 처리(`set -e` 미사용)하고
  결과를 TSV로 기록해 Evidence화.

## 남은 미확정 값 (이 PR이 채우지 않음)

아래는 baseline 원본이 명시한 사전확인(§3) 완료 전까지 placeholder로 유지한다. 실측값 확보 후
별도 커밋으로 반영하며, 이번 PR에서 임의로 채우지 않는다:

- `<실제-버전>` — P1-K8S-01 Platform Ready 후 `kubectl version`으로 확인할 K8s Minor Version
- `<확인된-UID>` / `<확인된-GID>` — 각 워크로드 공식 이미지의 non-root 지원 UID/GID
- `<approved-digest>` — transaction-api/fds-engine의 Harbor Trivy 스캔 통과 후 SHA-256 Digest
- `<실제-NIC-이름>` — Worker 3대의 실제 네트워크 인터페이스명
- `<monitor01-실제-IP>`, `<Worker-Node-CIDR>` — D-NET-01/ICR 실측 확정 후 반영
- `<PVC용량의-80%>` — Prometheus PVC 실제 용량 확정 후 계산

## Evidence

이 PR 자체는 코드/스크립트 변경이며 Runtime 실행 증적을 포함하지 않는다. 실제 실행 결과는
`docs/evidence/P1-SEC-01/` 경로(별도 Evidence 저장 위치)에 SHA-256과 함께 수집하며, 이
Revision Notes에는 식별자와 참조만 남긴다 (README "소스와 증적" 원칙).
