# ICR-D-NET-01-01 — Worker 간 9100/10250 scrape 정식 수정안

상태: **초안 / 독립 리뷰 전 / APPLY HOLD / RUNTIME NOT RUN**. 이 문서와 코드 변경은 운영 적용 승인이 아니다.

## 1. 문제

- Prometheus Targets 20개 중 6 UP / 14 DOWN (PR #27 본문 기준 관측).
- worker03 Pod(`10.244.39.219`)에서 worker01 `10.1.93.105:9100`으로 가는 scrape가 worker03 노드 IP `10.1.93.108`로 SNAT되어 도착하고, worker01 host-policy chain의 최종 DROP에 걸린다(evidence PR23, commit `2f8a238`의 counter 분석: `.108` source match와 최종 DROP 각 +46 packet / 20초).
- 원인은 v4 Worker GNP ingress에 있다. 10250은 CP 3대(`.102~.104`)에서만 허용하고, 9100은 규칙이 없다. 그래서 다른 Worker에서 온 9100/10250 트래픽은 `action: Deny`로 끝난다.

## 2. 변경

| 파일 | 변경 |
|---|---|
| `code/render.py` | Worker GNP ingress에 `TCP 9100, 10250` ← **다른 Worker 노드 IP/32**(자기 노드 제외) 규칙 1개를 명시적 Deny 앞에 추가. CP GNP·Egress·Felix failsafe·HEP는 변경 없음 |
| `policy_templates/k8s-worker0{1,2,3}.gnp.yaml.in` | 위 renderer로 재생성한 열람본. 기존 renderer로 기존 템플릿 6개를 byte 단위로 재현할 수 있음을 먼저 확인한 뒤 같은 방법으로 생성 |
| `tests/validate.py` | flow matrix 29→44건(허용 3, 거부 12). Worker별 source 집합·자기 노드 제외·Deny 앞 위치 검사 1건 추가 |
| `PACKAGE_SHA256SUMS` | 패키지 전체 파일 해시 목록(아래 §5) |
| `validation/icr-scrape-fix-unit-tests.txt` | 이번 로컬 시험 로그 |

Worker별 새 규칙의 source:

| 대상 | 허용 source (TCP 9100, 10250) |
|---|---|
| k8s-worker01 `.105` | `.107/32`, `.108/32` |
| k8s-worker02 `.107` | `.105/32`, `.108/32` |
| k8s-worker03 `.108` | `.105/32`, `.107/32` |

### 설계 판단과 한계

- **노드 신원 허용이다.** SNAT 후 source는 노드 IP이므로, 이 규칙은 Prometheus만이 아니라 다른 Worker 노드에서 나오는 모든 트래픽(해당 노드의 다른 Pod 포함)에 9100/10250을 연다.
  - **Pod 단위 제한은 일부 namespace에만 있다.** P1-SEC-01 `03-networkpolicy.yaml`(PR #7 HEAD `4e58239`, 9/22 적용분)의 default-deny egress는 `fds-app`·`monitoring-node`·`monitoring-api` 3개 namespace뿐이다. 그 밖의 namespace(`kube-system`, `default`, Calico 등)의 Pod는 egress 제한이 없어 **다른 Worker의 9100/10250에 도달할 수 있다.** 운영 NetworkPolicy 17개 전체를 여기서 다시 열거하지는 않았으므로, 적용 직전 스냅샷으로 확인한다.
  - **실제로 늘어나는 노출:** 지금도 모든 Pod는 자기 노드의 9100에 도달할 수 있다(Pod→자기 Host는 HEP를 거치지 않음). 이 변경으로 도달 범위가 "자기 노드"에서 "Worker 3대 전체"로 넓어진다. node-exporter가 내보내는 것은 호스트 자원 지표뿐이고, node-exporter SA에는 API 권한이 없다(이재환 [MON 의견](https://github.com/321Team/infra-kubernetes/pull/39#issuecomment-5843357366), 2026-09-26 조회).
  - **10250:** kubelet 인증·인가가 추가 방어선이다. 실측 근거는 worker03 1대뿐이다(anonymous=false, webhook=true, authorization=Webhook — [`KUBELET_TLS_WORKER03_20260924.md`](https://github.com/321Team/infra-kubernetes/blob/bac062d9e764dc8f519a70b8e1aaf95ff3f27c8c/docs/change/ICR-D-NET-01-01/KUBELET_TLS_WORKER03_20260924.md), PR #31). worker01·02는 **같은 설정이라고 가정**한 것이며 실측하지 않았다.
  - **수용 기록:** 9100 무인증 노출 확대는 MON Lead가 수용했다([의견](https://github.com/321Team/infra-kubernetes/pull/39#issuecomment-5843357366)). K8S/SEC 관점의 수용은 독립 리뷰에서 판단한다.
- **Prometheus 위치에 묶지 않았다.** Prometheus Pod가 다른 Worker로 재배치되어도 동작하도록 Worker 3대를 모두 source로 둔다. 특정 노드로 고정하는 안(PR #27 canary처럼 `.108`만)은 재스케줄 시 다시 장애가 난다.
- **자기 노드는 제외했다.** 같은 노드의 Pod→Host 트래픽은 이 HostEndpoint(ens160)를 지나지 않는다.
- **Egress는 바꾸지 않았다.** 이 GNP는 `applyOnForward: false`이고 Pod 트래픽은 forward 경로이므로 Host Egress 규칙의 대상이 아니다. PR23 counter의 worker03 egress Allow 증가도 이와 모순되지 않는다. 적용 후 worker03 egress DROP이 관측되면 이 가정이 틀린 것이므로 중단한다(§4).
- **CP 대상 scrape는 이 PR로 해소되지 않는다.** 2026-09-26 04:43Z `up` 조회(미병합 [fds-project-evidence#47](https://github.com/321Team/fds-project-evidence/pull/47) head `bc7d0bc`, `evidence/P1-MON-01/received-20260926T0445Z/run3-mon17fast/prom-queries.txt`)에서 20 target 중 6 UP / 14 DOWN이다.

  | DOWN 구분 | target | 이 PR 적용 후 |
  |---|---|---|
  | CP kubelet·cadvisor | `.102/.103/.104:10250` × 2 job = 6 | **DOWN 유지.** CP GNP의 10250 ingress는 CP 3대만 허용 |
  | Worker kubelet·cadvisor | `.105/.107:10250` × 2 = 4 | UP 기대 |
  | worker03 kubelet·cadvisor | `.108:10250` × 2 = 2 | PR #31(serving TLS) 적용 전에는 TLS IP SAN 오류로 DOWN 가능 |
  | node-exporter | `.105/.107:9100` = 2 (`.108`은 이미 UP) | UP 기대 |

  node-exporter target은 Worker 3대뿐이다(DaemonSet에 CP toleration 없음). 따라서 적용 후 기대치는 **최대 14/20 UP**이다. PR #31이 적용되지 않았다면 12/20이다. **별도 결정 항목:** MON AC "Target 전부 UP"을 위해 CP 6개 target을 어떻게 처리할지 정해야 한다. 선택지는 CP GNP에 Worker 노드 IP → TCP 10250 ingress를 추가하는 것과 Prometheus scrape 대상에서 CP를 제외하는 것이다. 담당은 K8S/SEC Lead와 MON Lead이며, 이 PR의 범위가 아니다.
- **worker03 kubelet serving 인증서 IP SAN 문제는 별개다.** 10250을 열어도 worker03 target은 PR #31(serverTLSBootstrap)이 적용되기 전까지 TLS 오류로 DOWN일 수 있다.

## 3. PR #27 canary와의 관계

- PR #27은 `.108 → .105:9100` 한 경로만 여는 임시 canary GNP(order 90)다. 이 정식안은 그것을 대체한다.
- **순서: 정식안 적용·확인 → canary 회수.** 두 정책 모두 Allow라 충돌하지 않는다. canary를 먼저 회수하면 정식안 적용 전까지 `.108→.105:9100` scrape가 다시 끊긴다. 정식안의 Positive(§4-4)를 확인한 뒤 PR #27 절차대로 UID·resourceVersion precondition으로 canary를 회수하고, 회수 기록을 적용 기록에 남긴다. 두 정책을 병행 상시 운영하지 않는다.
- canary로 얻은 Positive/Negative 결과는 이 정식안의 사전 근거로 쓸 수 있다. 다만 정식안은 source와 port가 더 넓으므로 음성 시험을 다시 해야 한다(§4).

## 4. 적용 전·후 조건 (이 PR 범위 밖, 담당 Lead 실행)

적용은 v4 패키지 절차(renderer → server-side dry-run → 노드별 반영)를 따르며, 아래를 모두 만족해야 한다.

1. §5의 #26 재실행 조건 3건 해소.
2. 정확한 HEAD의 독립 리뷰 승인과 K8S/SEC Lead의 실행 기록(SHA·담당·창·회수 책임).
3. 적용 직전 운영 GNP `fds-k8s-host-k8s-worker0{1,2,3}`의 현재 spec을 보관하고 `kubectl diff` 결과가 **추가 규칙 1개**만인지 확인한다. 다른 차이(9/22 out-of-band 수정 등)가 있으면 중단한다.
4. Positive: 서로 다른 post-change scrape 2회에서 node-exporter `.105/.107/.108:9100` 3/3과 kubelet·cadvisor `.105/.107:10250` 4/4가 health=up이어야 한다. `.108:10250` 2개는 PR #31 적용 여부에 따라 판정하며, 미적용이면 TLS 오류로 따로 기록한다. CP `.102~.104:10250` 6개는 DOWN 유지가 기대값이다(§2). 전체 기대치는 14/20(PR #31 적용 시) 또는 12/20이다.
5. Negative: CP(`.102`), LB(`.109`), 관리 SSH source(`10.250.10.10`) 또는 외부 승인 source에서 Worker 9100 접속이 차단되는지 확인한다. Worker 9101 등 다른 포트가 계속 차단되는지 확인한다. 시험 source는 승인된 것만 쓴다.
6. Regression: Gate 2 9/9 기존 시험, VXLAN 4789, SSH 22, API 6443 경로가 유지되는지 확인한다.
7. 복구: 보관한 GNP spec을 재적용한다(HEP·failsafe는 건드리지 않음).

## 5. #26 재실행 조건 대응

#26 승인 코멘트가 v4 운영 재render·재실행 전에 요구한 조건과 이 PR의 대응:

| 조건 | 이 PR | 상태 |
|---|---|---|
| 패키지 전체 해시 목록 | `PACKAGE_SHA256SUMS` 신설. 자기 자신을 뺀 패키지 내 **모든 파일**(기존 `SHA256SUMS`가 빠뜨린 `SCRIPT_MODIFICATION_HISTORY.md`, `REVIEW_FIX_SHA256SUMS`, `MODIFIED_SHA256SUMS`, `validation/review-fix-unit-tests.txt`, 그리고 기존 `SHA256SUMS` 자체 포함). 기존 `SHA256SUMS`는 v4.0 원본 기록이라 수정하지 않는다(현재 5개 불일치는 저장소 편입 시 README 갱신과 §4 리뷰 수정 4파일 때문이며 예상된 결과) | 이 PR로 해소(리뷰 대기) |
| `ausearch -ts yesterday` 실측 | 아래 읽기 전용 확인 절차를 명시. **실행은 하지 않았다** | 미해소 — 담당 이하영 |
| CP1 GNP UDP 123 out-of-band 규칙 정리·NTP 목적지 확정 | 이 PR 범위 밖 | 미해소 — 담당 이하영 |

### `ausearch -ts yesterday` 읽기 전용 확인 절차

`code/audit-window.py`는 창 시작이 UTC 자정 이전이면 `-ts yesterday`를 쓴다. 이 키워드는 이 audit 빌드(audit-4.2.1, el10)에서 실측되지 않았다(SCRIPT_MODIFICATION_HISTORY §4-1). 실패하면 `ERROR`→`HOLD`로 fail-closed되지만, Gate가 계속 HOLD로 멈출 수 있으므로 작업창 전에 확인한다.

- 대상: 전환 대상 6대 중 최소 1대(audit 빌드가 같음을 `rpm -q audit`로 함께 기록). 가능하면 6대 모두.
- 성격: 조회만 한다. 설정 변경 없음. REVIEW_AUTHORITY_POLICY §3.2의 순수 조회에 해당한다.

```bash
rpm -q audit
sudo env LC_ALL=C TZ=UTC ausearch --input-logs -m AVC,USER_AVC,SELINUX_ERR,USER_SELINUX_ERR -ts yesterday \
  >/tmp/ausearch-yesterday.out 2>/tmp/ausearch-yesterday.err; echo "rc=$?"
wc -c /tmp/ausearch-yesterday.out /tmp/ausearch-yesterday.err
head -c 200 /tmp/ausearch-yesterday.out
cat /tmp/ausearch-yesterday.err
```

판정은 `classify_ausearch()`와 같다.

| 결과 | 판정 |
|---|---|
| rc=0, stdout 비어 있지 않음, stderr 빈 파일 | PASS (`OK`) |
| rc=1, stdout 정확히 `<no matches>`, stderr 빈 파일 | PASS (`NO_MATCH`) |
| 그 외(stderr 있음, rc=1+빈 stdout, rc∉{0,1}) | FAIL — 자정을 넘는 작업창 금지. 창을 UTC 자정 이후 시작·같은 날 종료로 한정하거나 코드 수정 필요 |

결과(rc·바이트 수·stderr 원문·`rpm -q audit`)를 evidence 저장소에 남긴다. AVC 본문은 필요한 범위만 보관한다.

## 6. 검증 결과 (이 PR)

| 단계 | 결과 |
|---|---|
| 기존 renderer의 템플릿 재현 | 6/6 byte 동일 (변경 전 HEAD `4e0c2c4`) |
| `python3 tests/validate.py` | 23/23 PASS (`validation/icr-scrape-fix-unit-tests.txt`). 리뷰 대응 후 flow matrix 40→44건 |
| 변이 시험 | `render.py`만 변경 전으로 되돌리면 flow matrix 3건 + 신규 검사 1건 FAIL → 새 시험이 변경을 실제로 검출함 |
| `python3 -m compileall -q code tests`, `bash -n code/cockpit.sh` | PASS |
| `tests/check-crd.py` | NOT RUN (Calico upstream CRD 파일 미보유). 추가 규칙은 기존 규칙과 같은 필드(protocol/source.nets/destination.ports)만 사용 |
| renderer 실제 출력 생성 | NOT RUN — `runtime-input.json` 실측 입력 미완(설계상 생성 차단) |
| server-side dry-run / Runtime / CI | NOT RUN |
