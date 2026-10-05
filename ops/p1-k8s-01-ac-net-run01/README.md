# P1-K8S-01 `ac-net-run01` — Pod/Service 통신·DNS·UDP4789·TCP179/IPIP 시험 도구

- 책임: [#10 P1-K8S-01](https://github.com/321Team/fds-project-baseline/issues/10) AC "Calico/CoreDNS 정상", "Pod/Service DNS와 Cross-node Pod 통신 PASS", 핵심 요구 "UDP 4789 중심 Port Matrix, TCP 179/IPIP 기본 미허용"의 **잔여 증적**
- 계획·승인 기록: [시험 계획 5977712768](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5977712768), [T14 보완 5978355596](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5978355596), [PMO/QA 통합 리뷰 5979627598](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5979627598)(B1~B4)
- 상태: **Runtime 코드다. 이 PR의 병합·CI/STATIC PASS는 Runtime PASS나 AC credit이 아니다.** 실행은 PMO/QA의 Fresh Gate GO 뒤에만 하고, 실행 SHA는 이 PR의 독립 리뷰 완료 SHA와 정확히 같아야 한다.

## 이 도구가 하는 일
| 시험 | 방법 | 파일 |
|---|---|---|
| T09 Pod→CoreDNS | 각 Pod의 `socket.getaddrinfo`로 `kubernetes.default…`(기대 `10.96.0.1`), `kube-dns.kube-system…`, 없는 이름(NXDOMAIN), `quay.io` | `acnet-2-tests.sh` |
| T07 교차 노드 Pod 통신 | 3개 Pod가 다른 2개 Pod IP:8080으로 HTTP GET, **6방향** | `acnet-2-tests.sh` |
| T08 Service | 6방향 × (ClusterIP:80, FQDN:80) HTTP GET | `acnet-2-tests.sh` |
| T14 UDP 4789 | 6대 route·`vxlan.calico` 읽기 전용 조회, edge01 → worker01 UDP 4789 **1회** + `ens160` tcpdump + 규칙 counter 전·후 | `acnet-3-edge.sh` |
| T15 TCP179/IPIP | edge01 → worker01 TCP179 **1회**, IP protocol 4 raw **1회**(가능할 때만) + 같은 캡처·counter | `acnet-3-edge.sh` |

## 파일
| 파일 | 역할 | 실행 위치 |
|---|---|---|
| `manifests/ac-net-run01.yaml` | Namespace 1, Pod 3(worker01/02/03), Service 3. **이미지 digest 고정, `imagePullPolicy: Never`**, restricted PSA | 제출 |
| `acnet-0-precheck.sh` | **읽기 전용** Fresh Gate: namespace NotFound, digest가 3 Worker에 정확히 있는지, 노드 Ready, readyz | ansible01 |
| `acnet-1-gate-create.sh` | 기준선 기록 → dry-run → create-only → **namespace UID 기록** → Pod Ready 대기 | control01 |
| `acnet-2-tests.sh` | T09·T07·T08 | control01 |
| `acnet-3-edge.sh`, `acnet-worker-counters.sh` | T14·T15 | ansible01 (+ worker01 helper) |
| `acnet-9-cleanup.sh` | namespace UID에 결박된 정리 | control01 |
| `SHA256SUMS` | 위 파일의 SHA-256 | — |

## PMO/QA 차단사항 대응
| ID | 요구 | 대응 |
|---|---|---|
| **B1** | Runtime 코드를 Git PR + exact SHA로 | 이 PR. 실행 SHA = 리뷰 완료 SHA. 서버에는 ansible01에서 `gh repo clone`한 exact SHA를 해시 확인 후 `ansible copy`로 전달한다(control01은 GitHub 접속 불가) |
| **B2** | 이미지 공급 경로를 확정적으로 | `image@sha256:a7093136…cc33`(현재 `transaction-api` Deployment와 같은 digest) 고정, **`imagePullPolicy: Never`**, `acnet-0-precheck.sh`가 3 Worker에 digest 존재를 확인(없으면 `PRECHECK_FAIL`, **pull·Secret 생성으로 우회하지 않고 중단**), Pod 보안 설정은 매니페스트에 명시 |
| **B3** | 정리를 Namespace UID에 결박 | 시작 전 NotFound 확인 → 생성 직후 UID 기록 → 삭제 직전 현재 UID 일치, 라벨 확인, Pod 3·Service 3 외 객체 있으면 **삭제 STOP**, 삭제 후 NotFound와 라벨 잔여 0 확인 |
| **B4** | IPIP 실측 불가 시 PASS 대체 금지 | raw 송신이 불가능하거나 도착이 없으면 `T15_IPIP=NOT_VERIFIED`로 출력. 구성 증적(`ipipMode=Never`, 장치·모듈 없음)은 **보조 증적**으로만 쓴다. 스크립트는 PASS를 선언하지 않는다 |
| T14 | 도착 + 같은 src/dst/proto에 연결되는 규칙 counter | tcpdump 도착, **noise 30초 구간**의 Calico DROP counter 변화량과 시험 구간 변화량을 비교해 `COUNTER_ATTRIBUTION`을 `CONSISTENT…`/`NOT_SEPARATED`로 기록. 분리가 안 되면 PARTIAL/LIMITATION |

## 실행 순서
1. **Fresh Gate**(PMO/QA 요구): Node 6/6 Ready, readyz, boot_id 6대·앱 Pod restart·HEP/GNP/Felix UID/generation 기준선, MON 23/23, 콘솔·재부팅 금지·동시 Runtime 없음, `acnet-0-precheck.sh` `PRECHECK_PASS`
2. `acnet-1-gate-create.sh`(생성) → `acnet-2-tests.sh`(T09·T07·T08) → `acnet-3-edge.sh`(T14·T15) → `acnet-9-cleanup.sh`
3. 정리 후 기준선·MON 23/23 복구 확인 → 서버 원본 묶음 → 해시 대조 → 증적 PR(독립 evidence reviewer)

## STOP 조건 (하나라도 발생하면 즉시 중단)
- 앱 Pod 재시작 증가, Node NotReady, readyz 이상, 예상하지 않은 boot_id 변경
- HEP/GNP/Felix UID·generation 변화, 설명 안 되는 drift
- 테스트 Pod가 `ErrImageNeverPull`/`ImagePullBackOff`/PSA 거부 등으로 Ready가 아니면 **Secret을 만들거나 pull로 우회하지 않는다**
- 승인 관리 경로(ansible01 SSH, control01 → 10250) 상실
- 동시 cluster/security/MON Runtime 변경 발생
- 정리 단계의 UID·라벨·객체 집합 검사 실패(→ 삭제하지 않고 보고)

## 한계 (정직하게)
- `COUNTER_ATTRIBUTION`은 휴리스틱이다(Calico DROP counter는 규칙 전체 합계이며 noise 구간과 비교). 증명이 아니다.
- edge01에서 `sudo -n`이 안 되면 IPIP raw 송신은 불가하고 `T15_IPIP=NOT_VERIFIED`로 남는다.
- 이 도구는 시험 전 Node→`.52/.53`(6/6 수집 완료, 별도 증적)과 Pod 외부 DNS 시험을 대신하지 않는다.
- 시험 시작/종료 시각과 대상은 #10에 먼저 기록한다. 이 README는 실행 승인이 아니다.

## 코드 리뷰 반영 (supreme33-kr CHANGES_REQUESTED, 대상 HEAD `1d400d2a`)
| ID | 지적 | 반영 |
|---|---|---|
| **B-57-01** | TCP179의 RST·SYN-ACK·ICMP 응답을 "차단"과 구분하지 않음 | 캡처 필터에 `icmp`를 추가하고 요약에 `tcp179_synack`·`tcp179_rst`·`icmp_from_tgt`를 센다. **응답이 하나라도 있으면 `T15_TCP179=REACHABLE_OR_REJECTED`(Calico drop 모양이 아님)**, SYN만 도착하고 응답이 0일 때만 `SYN_ARRIVED_NO_REPLY(drop-shaped)`로 기록한다. SYN이 안 보이면 `NOT_ARRIVED` |
| **B-57-02** | 캡처 유효성 Gate 없음 | tcpdump를 `timeout --signal=INT --preserve-status 85`로 정상 종료시키고, async 작업을 `async_status`로 받아 `finished`·`rc`를 확인한다. 캡처 파일의 `listening on ens160`, `0 packets dropped by kernel`, `packets captured` 통계 줄, **ansible01 SSH 제어 패킷(liveness)** 이 모두 있어야 `CAPTURE=VALID`. 하나라도 어긋나면 `CAPTURE=INVALID`이고 T14·T15 도착 관측과 counter 판정은 **`NOT_VERIFIED`** |
| 비차단 1 | Pod Ready 실패 시 STOP | `acnet-1-gate-create.sh`가 `kubectl wait` 종료 코드를 확인해 0이 아니면 Pod 상태를 기록하고 **중단**(pull·Secret 생성으로 우회하지 않고 `acnet-9-cleanup.sh`와 #10 보고로 이어짐) |
| 비차단 2 | 삭제 UID precondition | `kubectl delete --raw /api/v1/namespaces/ac-net-run01 -f -`에 `DeleteOptions.preconditions.uid=<기록값>`을 넣어 확인과 삭제 사이의 틈을 없앴다. 삭제 거부 시 STOP. **(이 호출 형식은 서버에서 아직 실행해 보지 않았다 — 첫 실행 전 dry 확인 대상)** |
| 비차단 3 | 기타 리소스 조회 fail-open | 조회 종료 코드를 따로 확인하고 0이 아니면 삭제 STOP |
| 비차단 4 | 이미지 내부 가정 | Pod가 Ready가 아니면 1번대로 STOP. `python3`·`/tmp`가 있다는 가정은 첫 Ready 확인으로 검증 |
| 답변 보완 1 | `crictl images`만으로는 참조 해석을 보증 못함 | `acnet-0-precheck.sh`가 각 Worker에서 **`crictl inspecti <image>@<digest>`의 종료 코드**를 확인(0이 아니면 `PRECHECK_FAIL`) |
| 답변 보완 3 | edge01 sudo 사용 | `SUDO_OK`이면 이 스크립트가 edge01에서 root로 **IP protocol 4 패킷 1개**를 raw 송신한다. **시작 코멘트에 edge01 sudo 사용 여부를 적는다** |
| 답변 보완 4 | 규칙별 counter | `acnet-worker-counters.sh <label>`가 규칙별 DROP counter 스냅샷(`t15-drop-c0/c1/c2.txt`)을 남기고, edge 스크립트가 noise 구간(c0→c1)과 시험 구간(c1→c2)의 **규칙별 diff**를 출력한다 |

로컬 검증(STATIC): 가짜 `ansible`·`sleep`을 PATH에 둔 모의 실행으로 `acnet-3-edge.sh`의 판정 줄을 4개 시나리오(유효·응답 없음, RST 응답, `listening` 줄 없음, async job rc≠0)로 확인했다. 서버 실행은 하지 않았다.

## PMO/QA delta 리뷰 반영 (GWONUK CHANGES_REQUESTED, 대상 HEAD `48996457`)
| ID | 지적 | 반영 |
|---|---|---|
| **P1-1** | `acnet-1-gate-create.sh`: namespace 조회가 timeout·Forbidden일 때도 "없음"으로 취급해 기존 namespace를 `apply`로 건드리고 그 UID를 기록할 수 있음, 조회 뒤 경쟁 생성도 같음, `apply` 종료 코드 미확인 | **`apply`를 쓰지 않는다.** 매니페스트를 Namespace 문서와 나머지로 나누어 **Namespace를 create-only**(`kubectl create -f … -o jsonpath={.metadata.uid}`)로 만든다. AlreadyExists·timeout·Forbidden·그 밖의 모든 오류는 **STOP이고 UID를 기록하지 않는다.** 기록하는 UID는 **이번 실행의 create 응답 UID**뿐이며 형식(UUID)도 확인한다. 이후 fixtures도 create-only이고 dry-run·create의 종료 코드를 `PIPESTATUS`로 즉시 확인한다. namespace 생성 뒤의 부분 실패는 UID가 기록된 상태로 STOP하고 `acnet-9-cleanup.sh`로 이어진다 |
| **P1-2** | `acnet-9-cleanup.sh`: `{ …; exit 1 } \| tee` 때문에 UID 불일치 STOP도 최종 rc=0, 삭제 후 모든 오류를 부재로 취급, 24회 뒤에도 Terminating이면 성공 종료 | 블록의 종료 코드를 `${PIPESTATUS[0]}`로 호출자에게 전달한다. 삭제 후에는 **명시적 `NotFound`** 가 나올 때만 부재로 인정하고, NotFound가 아닌 오류는 `CLEANUP_INCOMPLETE`(rc≠0), 대기(기본 24회) 뒤에도 남아 있으면(Terminating) `CLEANUP_INCOMPLETE`(rc≠0)이다. 라벨 잔여 조회는 **성공(rc=0)이고 결과 0건**일 때만 통과한다. UID·라벨·객체 집합 조회 실패도 모두 STOP(rc≠0). 마지막에 `CLEANUP_COMPLETE`가 있어야 완료다 |
| 요구 | 회귀 테스트 | `acnet-selftest.sh`(모의 `kubectl`·`sleep`, API 접근·클러스터 변경 없음)를 추가했다. 요청된 경로(조회/생성 timeout, 정리 UID 불일치 rc, 삭제 뒤 계속 Terminating)와 경쟁 생성(AlreadyExists)·fixtures 생성 실패를 포함해 22개 시나리오를 확인한다. 이전 HEAD 스크립트에 같은 테스트를 돌리면 20개가 실패하므로(재현 확인) 테스트가 버그를 실제로 잡는다 |

`AC_NET_R`·`AC_NET_K`·`AC_NET_WAIT_TRIES`는 자체시험용 훅이다(기본값이 운영 경로). **서버에서 실행하는 시험이 아니다.**

`acnet-selftest.sh` 실행: `bash acnet-selftest.sh` (로컬, 종료 코드 0이면 전 시나리오 통과).

## PMO/QA 재리뷰 반영 2 (GWONUK CHANGES_REQUESTED, 대상 HEAD `e2900b88`)
| ID | 지적 | 반영 |
|---|---|---|
| **B57-NEW1** | `acnet-1-gate-create.sh`의 awk 분할이 모든 `---` 구분자를 버려 Pod 3·Service 3이 한 문서로 붙음(실제 매니페스트에서 `rest.yaml`에 Service 하나만 남거나 파싱 오류). mock이 파일을 파싱하지 않아 통과했음 | **첫 경계만 제거하고 나머지 `---`는 유지**한다(`if(d>2) print "---" > rest`). 분할 직후 **Namespace 1 / 추가 Namespace 0 / Pod 3 / Service 3 / 구분자 5개**와 6개 fixture 이름을 확인하고 하나라도 어긋나면 STOP한다. 자체시험의 **mock `kubectl`이 `-f` 파일을 실제로 파싱**해(문서마다 `kind:`가 정확히 1개, 문서 수 1·6) 구분자가 사라지면 happy path가 실패하고, 시험은 작은 모의 파일이 아니라 **실제 매니페스트**를 쓴다 |
| **B57-NEW2** | namespace 생성 후 fixtures가 부분 생성된 상태(빈 namespace, probe-a만)는 UID가 정확해도 cleanup이 "6개와 정확히 일치"를 요구해 STOP — 안내한 복구가 불가능 | cleanup은 **자신의 create-response UID와 라벨을 유지한 채**, 존재하는 Pod/Service가 **허용된 6개의 부분집합(빈 집합 포함)** 이면 UID precondition 삭제를 허용한다. 허용 목록 밖 객체(외부 객체·다른 이름)가 하나라도 있으면 계속 STOP. `get -o name`의 stdout만 쓰고 stderr(`No resources found…`)는 분리해 빈 집합을 오인하지 않는다 |
| 회귀 | 요청된 재검증 | 자체시험을 **22개 → 28개**로 늘렸다: 실제 매니페스트 분할 1+6, 빈 namespace cleanup, Pod 1개 부분 생성 cleanup, 외부 객체 거부(부분집합+외부, 외부만), 기존 UID 불일치·조회 오류·Terminating 비정상 종료. 이전 HEAD(`e2900b88`) 스크립트에 같은 시험을 돌리면 **9개가 실패**(happy path와 분할 구조, 부분 cleanup 포함)하므로 테스트가 두 버그를 실제로 잡는다 |

이 자체시험 결과를 Runtime 성공으로 보지 않는다(모의 kubectl, 서버 실행 없음).

## 2026-10-05 precheck fail-closed 수정 (독립 리뷰 대기)

근거: [#10 Fresh Gate/조기 시작 요청](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5987631809). 구현: 이권욱(CODEX_ASSISTED). 기존 승인 SHA `a24f0a23fa6ddb43a58bfc3de612e8677b359fc5`의 precheck는 nodes/readyz를 출력만 하고 실패를 `fail`에 반영하지 않아, 해당 조회가 실패해도 PRECHECK_PASS/rc0이 가능했다. 로컬 PATH stub과 경로만 격리한 원본 코드로 재현했다.

- Ansible transport rc와 원격 검사 성공 marker가 둘 다 있어야 통과한다. 노드 이름 중복 없는 정확히 6행·전부 Ready, readyz 명령 rc0·응답 ok를 검사한다. SchedulingDisabled와 빈/추가 노드도 거부한다.
- namespace는 성공한 `--ignore-not-found -o name`의 빈 stdout만 부재로 인정한다. 조회 오류를 NotFound로 해석하지 않는다. image list/inspecti도 명령 오류를 성공 marker로 덮지 않는다.
- 출력 폴더는 `umask 077` + 고유 `mktemp -d`. 항목별 stdout/stderr/rc, run.log, SHA256SUMS, result.txt를 보존한다. 인수는 process rc0 + PRECHECK_PASS + 원본 검산을 함께 확인한다. 쓰기/tee/해시 실패는 nonzero이며 콘솔에 PRECHECK_PASS를 출력하지 않는다.
- 자체시험: `python3 acnet-precheck-selftest.py` (로컬 Python3 필요, 운영 precheck에는 Python3 불필요). 실제 원격 shell 문자열을 가짜 kubectl/crictl에 실행하며 서버에 접근하지 않는다. 테스트 복사본에서는 사용자 홈의 설정·inventory·출력 경로만 임시 폴더로 치환한다. 17개 정상/실패 case. 기존 생성·cleanup 회귀 `bash acnet-selftest.sh` 28개도 유지한다.
- 생성/시험/정리 스크립트와 manifest는 변경하지 않았다. 수정 precheck의 source 승인은 기존 a24f0a23 승인과 별도이며, 구현자 본인이 독립 승인하지 않는다. Runtime GO·변경창과 AC 인수도 별도다.
- 수정 전 SHA를 실행하는 경우 기존 PRECHECK_PASS 하나만으로 Fresh Gate를 대체하지 않는다. 기존 Gate 계약의 노드·readyz 원본/rc를 별도로 확인하는 운영 판단은 #10의 해당 실행 승인 기록을 따른다.

## 2026-10-05 `ErrImageNeverPull` 대응 — pull secret 복사 + `IfNotPresent` (승인 대기, 서버 미적용)
- 증상: 같은 digest 이미지가 3 Worker에 있고 정확한 reference `crictl inspecti`가 rc=0인데, worker02·03의 kubelet이 `imagePullPolicy: Never` Pod에 `ErrImageNeverPull`을 냄(worker01은 정상). 2회 재현([#10 보고](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5988467389)).
- 원인(강한 정황, 확정 아님): worker02·03 kubelet의 `image_manager/pulled/sha256-e3f8f418…`(= `sha256("sha256:1972a9af…")`) 기록이 이 이미지를 `fds-app/harbor-pull-secret`, `fds-gate2-probe/harbor-pull-secret`으로 받은 것으로 적고 있고, worker01에는 그 기록이 없다. 시험 Pod는 그 secret이 없어 `Never`에서 거절된다([#10 5988570688](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5988570688)).
- 변경(시험 객체 쪽만, 노드·containerd·kubelet은 건드리지 않음): ① Pod 3개에 `imagePullSecrets: [{name: harbor-pull-secret}]`과 `imagePullPolicy: IfNotPresent`, ② `acnet-1`이 namespace 생성 직후 `fds-app/harbor-pull-secret`을 시험 namespace로 복사(값은 출력하지 않음, 원본과 데이터 동일 여부는 내부 비교만, 라벨 `fds.test/run`), 실패 시 fixtures를 만들지 않고 STOP, ③ `acnet-9`의 허용 목록에 `secret/harbor-pull-secret` 1개만 추가(다른 secret은 STOP), namespace 삭제로 함께 제거.
- 한계: kubelet이 새 namespace의 복사본을 받아들이는지는 **실제 실행으로만 확인**된다. secret uid 차이만으로 `Never`가 반드시 거절된다고 단정하지 않으며, 기록(credentialMapping·hash)과 실제 동작은 구분한다. `IfNotPresent`는 **레지스트리 인증 요청과, kubelet이 필요하다고 판단하면 해당 digest pull이 발생할 수 있고 로컬 cache만 쓴다고 보장하지 않는다**(수용 범위: 복사본 1개, 해당 namespace, 고정 digest).
- B62-1(독립 리뷰): 복사 검증의 source/copy GET 종료 코드를 파이프로 가리지 않고, 두 값이 비어 있지 않을 때만 shell 변수 안에서 비교한다(값·base64·해시는 인자·환경변수·파일·로그로 내보내지 않음). 조회 실패·빈 값은 fixtures 생성 전에 STOP.
- 모의 시험: `bash acnet-selftest.sh` 시나리오에 secret 복사 성공/값 비출력/순서, 원본 읽기 실패·잘못된 타입·생성 실패·라벨 실패·데이터 불일치, cleanup의 secret 허용·타 secret 거부를 추가했다. 이 변경은 [#10 승인](https://github.com/321Team/fds-project-baseline/issues/10)과 독립 리뷰 뒤에만 새 exact SHA로 실행한다.

## 2026-10-05 후속 수정 — precheck 오탐, 캡처 대기, UDP 집계 (승인 대기, 서버 미적용)
오늘 시험([#10 결과 보고](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5989192506))에서 드러난 도구 결함을 [PMO/QA 결정](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5989234529)과 [독립 증적 리뷰](https://github.com/321Team/fds-project-evidence/pull/114#pullrequestreview-5411104434)의 지적대로 고친다. 시험 객체·정책·권한은 바꾸지 않으며, **오늘 실행한 source(`e469761c`)나 증적에 덮어쓰지 않고** 새 source로만 쓴다. 재시험 GO가 아니다.

| 결함 | 오늘 관측 | 수정 | 회귀 시험 |
|---|---|---|---|
| `acnet-0-precheck.sh` 오탐 | worker의 `crictl` 경고(stderr)가 ansible 출력에서 성공 표시 `ACNET_IMAGE_OK` 바로 뒤에 붙어 `grep -Fx`가 실패, `PRECHECK_FAIL`(3대) | 원격 명령에서 `crictl` stderr를 원격 임시 파일로 보내 stdout에 섞지 않고, **실패할 때만** 접두어를 붙여 출력. 표시는 여전히 한 줄 전체 일치이며 종료 코드도 그대로 본다(완화하지 않음) | `acnet-precheck-selftest.py`: ansible stub이 실제 출력(stdout 끝 개행 제거 + stderr 직접 이어붙임)을 재현. 정상+stderr 경고는 PASS, 표시가 줄 끝에 붙은 경우·빈 stdout·실패 RC+표시는 FAIL. **이전 precheck에 같은 시험을 돌리면 `stderr_warn`이 실패**하며 오늘의 `exact image unresolved`가 재현된다 |
| `acnet-3-edge.sh` 캡처 대기 | 고정 `sleep 25` 뒤 `async_status`를 1회만 조회(`job_finished=0`) → `CAPTURE=INVALID` | `finished=1`이 될 때까지 5초 간격으로 **최대 `ACNET_CAP_POLL_MAX`(기본 30)회** 폴링. **상태 조회 명령의 종료 코드가 비0이면 그 원문·rc를 남기고 즉시 폴링을 멈추며 본문의 `finished`/`rc`를 성공 근거로 쓰지 않는다(B63-1)**. 마감 초과·`rc` 누락/비0·job id 없음도 `INVALID`/`NOT_VERIFIED`이고, 최종 `CAPTURE=VALID`에는 조회 명령 rc 0이 함께 필요하다. 상태 원문은 `head`로 자르지 않고 전부 남기며 `polls`·`poll_rc`·`finished`·`job_rc`를 기록 | `acnet-edge-selftest.py`: 4회째 완료, 끝나지 않음(폴링 횟수 한정), 조회 실패, `rc` 없음, `rc=7`, job id 없음(폴링 0회), 상태 원문 보존, **정상 모양 본문(finished=1, rc=0)+조회 명령 rc 4**, **첫 조회 비0 뒤 성공하도록 준비된 stub(추가 조회 없이 INVALID)** |
| UDP 4789 집계 | 목적지 4789 뒤 문자열 `UDP`만 세어 `VXLAN (invalid)` 출력을 놓침(`udp4789_arrived=0`) | edge01 → worker01:4789로 가는 패킷을 `UDP`/`VXLAN` 등 뒤따르는 문자열과 무관하게 세고, IP의 점을 이스케이프하고 앞에 공백을 두어 `110.x`·`10a1b93c50` 같은 유사 주소를 배제 | 같은 시험: 두 가지 출력 형태(VXLAN, UDP)=2, 역방향·다른 포트(4790)·다른 출발지·유사 주소는 미포함. 이전 `acnet-3`에 돌리면 **8개 중 5개 실패** |
- **이번 PR이 고치지 않는 것:** 오늘 15:00 기준선 비교가 실패·빈 입력을 "차이 없음"으로 보고한 경로는 **이 패키지의 코드가 아니라** 실행자가 세션에서 쓴 임시 비교 명령이다. 원인을 재현하지 못했고 추정으로 다른 코드를 바꾸지 않는다([PMO/QA](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5989234529) §5-4). 원인 확인 전에는 수집 RC·파싱 성공·객체 개수를 확인한 표본에서만 비교 결과를 해석한다.
- 30회는 **조회 횟수 한도**이며 SSH/Ansible 호출 시간까지 포함한 정확한 wall-clock 150초 보장이 아니다. Runtime 승인 시 별도 중단 시각을 유지한다. UDP 집계는 기존 capture filter의 UDP4789 범위에서 `VXLAN (invalid)`도 도착 건수로 세는 **보정**이며 정상 VXLAN 디캡슐화나 정책 DROP의 입증이 아니다.
- 고치지 않은 한계: IPIP는 edge01 `sudo` 없이는 보낼 수 없고(`NOT_VERIFIED` 유지), counter 귀속은 이 수정으로 해결되지 않는다.
- **검증:** `acnet-selftest.sh` 67 PASS(변경 없음), `acnet-precheck-selftest.py` 20 OK(17+3), `acnet-edge-selftest.py` 10 OK, `bash -n` 통과. 작성자의 Windows PC에서는 시험 harness가 `bash`를 WSL로 해석하므로 Git Bash 절대 경로·UTF-8·POSIX 경로로 바꾼 복사본으로 실행했다(코드 변경 없음). **Linux에서 재실행을 리뷰어께 요청한다.** 서버 실행은 없다.
