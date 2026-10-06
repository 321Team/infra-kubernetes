# P1-SEC-01 AC-05 Secretbox rollback 시험 절차서 (v4.4, rev v4.4-20261006, 승인 전)

대상: [#19](https://github.com/321Team/fds-project-baseline/issues/19) AC-05 `Secret encryption·backup·rotation·rollback` 중 **rollback**. [CR #125](https://github.com/321Team/fds-project-baseline/issues/125)에서 "P1 필수 / Runtime 증적 미충족"으로 남은 항목이다.
도구: `sec01-secretbox-rollback-v4.sh` (`precheck` / `rollback-test` / `postcheck`). 이 절차서와 스크립트는 **운영 클러스터에서 한 번도 실행하지 않았다.** v3는 PMO/QA 재리뷰([#19 5986339672](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986339672), 2026-10-05)에서 소스 차단 3건(B1~B3)과 추적성 조건을 받았고, v4는 그 수정본이다. v4 재리뷰([#19 5986833302](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986833302))가 **B3를 서버 측 UID precondition으로 원자적으로 고치라**고 하여 당시 문서와 스크립트는 **v4.1**로 갱신되었다(파일 이름은 그대로, 바이트와 revision이 다르다). v4.1도 가짜 클러스터(fixture)에서만 시험했다. **이 문서는 변경창 승인이 아니다.**

현행 v4.3은 12절 B59-3 탐지·수동 조치와 11절의 Gate·봉인 요건을 함께 따른다. 아래 v4/v4.1 시험 수치는 당시 이력이다.

## 0. v4 변경 이력 (재리뷰 반영)

| 지적 | v4 처리 | 시험(fixture) |
|---|---|---|
| **B1** 조회 실패가 정상 판정으로 바뀜 (etcd 조회 rc 미보존, 잔여 Secret 조회 오류가 0건) | `etcd_prefix`가 조회 rc·값 존재·형식(`k8s:enc:` / `k8s\0`)을 분리해 판정하고 `class=`를 결과 파일에 기록. 조회 실패·빈 값·알 수 없는 형식은 평문으로 인정하지 않고 FAIL. precheck의 잔여 시험 Secret·전체 Secret 목록 조회가 실패하면 0건이 아니라 **NO-GO**. listener 관측은 조회 오류(`ss` 실패·헤더 없는 빈 출력)와 실제 down을 구분하고, 오류는 "재기동 미관측"으로 FAIL | S13~S13d(etcd 실패·빈 값·알 수 없는 형식·복원 후 실패), S14~S14c(Secret 조회 실패 → NO-GO), S2c·S2d(`ss` 오류·빈 출력) |
| **B2** 원복 후 검증이 변경 대상 CP에 결박되지 않음 | 원복 후 `c` **생성**과 `a`·`b` **읽기**를 대상 CP 직접 endpoint(`https://<이 CP IP>:6443`)로 필수 수행. LB 읽기와 다른 CP 읽기는 **별도 명령**으로 유지하고 각각 필수 | S5b(대상 CP만 평문 저장), S6b·S6c(대상 CP 직접 읽기만 실패), S6d·S6e(LB·다른 CP 읽기 실패). 모두 "다른 경로는 정상"인 주입이며 FAIL이어야 통과 |
| **B3** cleanup이 이번 실행 소유가 아닌 Secret을 삭제할 수 있음 | 실행별 고유 이름 `enc-rb-test-<UTC시각>-<난수>-a/b/c`. **생성 응답으로 UID를 받은 것만** 소유로 추적한다. **(v4.1)** 삭제는 GET→이름 DELETE가 아니라 `kubectl delete --raw /api/v1/namespaces/default/secrets/<name> -f -`에 `DeleteOptions.preconditions.uid`(생성 응답 UID)를 실어 **서버가 UID를 검사**하게 한다. 409 Conflict(UID 교체)·NotFound·삭제 실패·결과 불명확이면 **이름 삭제로 fallback하지 않고** 보존·FAIL로 처리한다. 성공 후 GET으로 명시적 NotFound를 확인하고, cleanup 미완료는 최종 FAIL이다. AlreadyExists·응답 유실(소유 불명확)은 삭제 요청 자체를 보내지 않고 수동 확인 대상으로 남긴다 | S15(AlreadyExists), S16(GET과 DELETE 사이 UID 교체 → 서버 Conflict, 외부 객체 보존), S17(응답 유실), **S19(삭제 요청 실패·결과 불명확, fallback 없음)**, S1(삭제 본문의 `preconditions.uid`가 생성 응답 UID와 일치함을 mock에서 검사, 이름 삭제 0회), S8(정리 실패), S7(변경 전 실패 정리) |
| 추적성 2: 재기동 증거 덮어쓰기 | `restart-rollback-*`(진입)와 `restart-restore-*`(원복)로 파일 분리. 각각 `cid-before`·`cid-after`·`observed` | S1(6개 파일 존재, 두 단계 before ID 서로 다름) |
| 추적성 3: v2 잔여 문구, control01 backup 경로 | 8절·9절의 v2 문구를 v4 동작에 맞게 정정. **대상 CP에 실제 있는 설정 사본(`ENC_BACKUP`)과 해시를 precheck 게이트로 추가**. 다른 서버의 backup 경로는 이 CP의 복구 수단으로 인정하지 않음(7절) | S18~S18d(미지정·해시 불일치·경로 없음 → NO-GO, 변경 없음) |
| 추적성 4: 변경창 직전 재확인 | 3절 "변경창 직전 재확인"에 baseline·3CP 상태·설정 해시·복구 수단·동시 Runtime 부재 확인을 명시 | (수동 절차) |
| 추적성 1: 소스 PR·exact SHA·독립 리뷰 | 스크립트·fixture·절차서를 소스 PR로 연결하고 exact SHA를 고정한 뒤 독립 리뷰를 받는다(10절). 이 절차서는 PR 전 초안 | — |

v3의 기존 case(S1~S12: 재기동 관측, B2 계열, 정리, LB 서버 결박, 3CP 해시, etcd health, 재실행 안전)는 v4 fixture에 모두 남아 있고 v4 동작에 맞게 문구만 갱신했다. 검사 수는 v3 55개에서 v4.1 **152개**로 늘었고 **152 PASS / 0 FAIL**이다. 새로 추가한 case 중 B1·B2·B3 계열은 같은 fixture를 v3 스크립트에 적용하면 **실패한다**(v3 결함을 실제로 잡는 시험임을 대조 확인).

## 1. 무엇을 증명하는가

Review Owner 정의: rollback은 키 회전이 아니라 **"잘못된 암호화 구성 변경에서 되돌릴 수 있다"는 복구성 검증**이다.

단순히 `kube-apiserver.yaml.bak-preenc`로 되돌리면 etcd에 이미 `k8s:enc:secretbox:v1:key1:`로 저장된 Secret을 apiserver가 읽지 못한다(그 파일은 서버에서 확인되지 않았다). 안전한 되돌림은 provider 순서를 `identity` 우선으로 바꾸되 `secretbox`를 읽기용으로 남기는 것이다. 이 시험은 CP 1대에서 그 되돌림이 실제로 동작하고 원상 복구되는지 확인한다.

| 확인 항목 | 기대 |
|---|---|
| 시험 전 신규 Secret | etcd 값이 `k8s:enc:secretbox:v1:`(`class=SECRETBOX`) |
| 변경 후 기존 암호화 Secret | 계속 읽힘(목록 차이 0, 시험용 Secret 읽기 성공) |
| 변경 후 신규 Secret | **etcd 조회가 성공**하고 값이 있으며 `k8s\0`로 시작(`class=PLAIN`). 조회 실패·빈 값·알 수 없는 형식은 FAIL |
| 다른 CP·LB에서 평문 Secret 읽기 | 성공(둘 다 별도 명령) |
| 원복 후 설정 | 시험 전 `encryption-config.yaml`과 SHA-256 일치 |
| 원복 후 신규 Secret(**대상 CP 직접 생성**) | 다시 `class=SECRETBOX` |
| 원복 후 읽기 | **대상 CP 직접**(암호화 `a`, 평문 `b`), LB(`b`), 다른 CP(`b`) 모두 성공 |
| 정리 후 | 이번 실행이 만든 시험 Secret 3개만 UID 확인 후 삭제, 시험 전후 Secret 목록 동일 |

**이 시험이 증명하지 않는 것:** etcd snapshot에서의 복원(DR), 예방 rotation(CR #125로 `NOT_MET / RISK_ACCEPTED`), 잘못된 키/깨진 설정에서의 복구.

## 2. 일정·역할 (제안, 확정 필요)

| 항목 | 제안 |
|---|---|
| 변경창 | **미정.** v4 소스 재리뷰와 PMO/QA 변경창 재판정 이후 확정한다. 이전 문서의 "2026-10-06(월)"은 오기이며 2026-10-06은 **화요일**이다. 10-02 16:00 이후는 Review Owner의 변경 중단 조건 때문에 하지 않는다 |
| 대상 CP | **k8s-control03(`10.1.93.104`)** 제안. 어느 CP든 동일하며 한 대만 재기동한다 |
| 실행자 | 이하영(SEC Lead) |
| 확인 참여 | 이권욱(원하는 경우 콘솔 관찰·중단 판단) |
| 증적 PR 독립 리뷰 | 이재환 |
| 소요 | 약 10~15분. 대상 CP의 apiserver가 약 30~90초씩 2회 내려감(다른 CP 2대가 LB로 서비스) |
| 사전 공지 | 팀 채팅. MON Prometheus의 apiserver target이 순간 down 될 수 있음 |

## 3. 사전 확인 (시작 전, 모두 충족해야 GO)

**자동(스크립트 `precheck`, 읽기 전용)** — 하나라도 실패하면 `RESULT: NO-GO`로 종료한다.

| # | 확인 | 합격 |
|---|---|---|
| 1 | `kubectl get nodes` | 6/6 Ready |
| 2 | CP 3대 각각 `GET /readyz` | 3대 모두 `ok` |
| 3 | `etcdctl endpoint health --cluster` | 전부 healthy |
| 4 | 시험 전 Secret 목록 수집 | **조회 성공**이고 비어 있지 않음(조회 실패는 NO-GO) |
| 5 | `enc-rb-test-*` 잔여 Secret | **조회 성공 후** 0개. 조회 실패는 0개로 보지 않고 NO-GO |
| 6 | 대상 CP `encryption-config.yaml` | mode 600, 첫 provider = `secretbox` |
| 6a | `admin.conf` 활성 서버 | 승인 LB `https://10.1.93.109:6443`와 일치 |
| 6b | etcd 엔드포인트 | healthy 3/3, unhealthy 0 |
| 6c | **3CP 설정 해시 동일**(`PEER_SHAS`) | 다른 CP 2대 해시가 이 CP와 모두 같음. 값이 없으면 NO-GO |
| 6d | **`ENC_BACKUP`**(이 CP에 둔 설정 사본) | 파일 존재, mode 600, 해시가 현재 설정과 동일. 없거나 다르면 NO-GO |

**수동(실행자 체크)**

| # | 확인 | 방법 |
|---|---|---|
| 7 | T19 종료·보호 복원이 Review Owner에 의해 확인됨 | #10 코멘트 |
| 8 | Review Owner 승인(변경창, 대상 CP, **v4 소스 exact SHA 리뷰**) | #19 코멘트 |
| 9 | **다른 CP 2대의 해시를 수집해 `PEER_SHAS`로 전달** | 각 CP에서 `sudo sha256sum /etc/kubernetes/enc/encryption-config.yaml` |
| 10 | **대상 CP에 설정 사본 준비** | 대상 CP에서 `cp -p /etc/kubernetes/enc/encryption-config.yaml /root/enc-config-prechange.yaml && chmod 600 /root/enc-config-prechange.yaml && sha256sum /root/enc-config-prechange.yaml` 후 이 경로를 `ENC_BACKUP`으로 전달. control01의 2026-10-02 backup(`/root/p1-sec01-secretbox-backup-20261002T054208Z/`)은 **참고 증적일 뿐 이 CP의 복구 수단이 아니다** |
| 11 | 다른 변경 작업 없음(공유 Runtime, 증적 PR 적용 등) | 팀 채팅 |
| 12 | 콘솔 접근 확보(SSH 불가 시) | 실행자 |

대상 선택 권고(비차단): `kubectl -n kube-system get lease kube-controller-manager kube-scheduler -o custom-columns=NAME:.metadata.name,HOLDER:.spec.holderIdentity`로 리더를 확인하고 가능하면 controller-manager 비리더 CP를 선택한다. Lease는 이동할 수 있고 LB·다른 컨트롤러 쓰기도 있으므로 비리더 선택이 쓰기 격리나 B59-3 검사 대체는 아니다. Lease 변경·강제 리더 이동은 하지 않는다.

**변경창 직전 재확인** (과거 T19 종료 사실만으로 이후 변경창의 비중첩을 보장하지 못한다. 변경창 시작 직전에 다시 확인하고 `#19`에 기록한다):

| # | 재확인 | 방법 |
|---|---|---|
| R1 | 최신 baseline | 승인받은 스크립트·절차서의 exact SHA-256과 현재 파일이 일치. 승인 이후 main·`infra-*` 변경 여부 |
| R2 | 3CP 상태·설정 해시 | `precheck` 결과(`pre-readyz.txt`, `pre-etcd-health.json`, `pre-3cp-config-sha.txt`)가 GO |
| R3 | 복구 수단 | `ENC_BACKUP` 해시 일치(6d), 대상 CP 콘솔 접근, 7절 절차 숙지 |
| R4 | 동시 Runtime 부재 | 팀 채팅·#10·#13·#17의 진행 중 변경(복구시험, AC-04 적용, canary 등)이 없음을 확인 |
| R5 | Review Owner 변경창 승인이 유효 | 승인 코멘트의 일시·대상 CP·SHA와 실제가 일치 |

## 4. 절차 (대상 CP 콘솔에서, `rollback-test`가 자동 수행)

| 단계 | 동작 | 합격 | 실패 시 |
|---|---|---|---|
| 0 | `precheck`와 같은 게이트 | GO | **설정 변경 없이** 종료 |
| 1 | 원본 설정 사본·해시 저장, 재배치 설정 생성 후 **사전 검증**(첫 provider = identity, secretbox 포함) | 통과 | 재기동 없이 종료 |
| 2 | 시험 Secret `a` 생성(UID 확보), etcd 값 확인 | 조회 성공, `class=SECRETBOX` | 재기동 없이 종료. **etcd 출력 형식 가정은 이 단계에서 변경 전에 검증된다** |
| 3 | 재배치 설정 적용, 이 CP의 apiserver만 재기동 | **6443 listener down 관측(관측 오류 아님) + container ID 변경 + 300초 안에 `/readyz` ok** | **자동 복원** |
| 4 | 기존 Secret 목록 동일, `a` 읽기, `b` 생성 후 etcd 값이 `class=PLAIN`, **LB와 다른 CP를 각각 지정해** `b` 읽기 | 모두 통과 | **자동 복원** |
| 5 | 원본 설정 복원·재기동·해시 일치, **대상 CP 직접**으로 `c` 생성 후 `class=SECRETBOX`, **대상 CP 직접** `a`·`b` 읽기, LB·다른 CP `b` 읽기 | 통과 | 중단 후 **7절 수동 복구** |
| 6 | 소유한 시험 Secret 3개 UID 확인 후 삭제, 사후 점검, 시험 전후 Secret 목록 비교 | `RESULT: PASS` | 7절 확인. 소유 불명확·UID 불일치 객체는 삭제하지 않고 수동 확인 |

## 5. 중단 기준 (즉시 중단하고 7절로)

- 사전 확인 중 하나라도 실패(`NO-GO`) — **변경하지 않는다**
- 재배치 설정으로 재기동했는데 300초 안에 Ready가 되지 않음, 또는 listener 관측 오류로 재기동 여부를 판정할 수 없음
- 다른 CP의 `/readyz` 또는 etcd health에 이상이 생김(관찰자가 발견)
- 기존 Secret이 읽히지 않거나 목록이 다름
- 신규 Secret이 여전히 `k8s:enc:`로 저장됨, 또는 etcd 조회 실패·알 수 없는 형식으로 평문 여부를 확인하지 못함
- 원복 후 대상 CP 직접 생성·읽기가 실패하거나, 복원 설정 해시가 시험 전 값과 다름
- 이상 징후가 있으면 실행자나 확인자가 **언제든** 중단을 선언

## 6. 증적 (수집·편입)

스크립트가 `/root/p1-sec01-secretbox-rollback-test-<시각>.<고유값>/`에 남기는 파일: `run.log`, `00-identity.txt`(스크립트 SHA·rev·run_id), `pre-*`/`post-*`(nodes, readyz, etcd health/status, apiserver pods, secrets 목록, 설정 mode·해시, provider 순서, `pre-3cp-config-sha.txt`, `pre-enc-backup.txt`), `13`·`33`·`40`(etcd 저장 형식 `class=`만), `10-owned-secrets.txt`(이름과 UID)·`10-unowned-secrets.txt`(있다면 수동 확인 대상), `30`·`31`·`51`(목록 비교), `32`·`34`·`35`·`41`·`42`·`43`·`44`(읽기 결과), `restart-rollback-*`·`restart-restore-*`(재기동 증거 단계별), `50-cleanup.txt`, `00-orig-sha256.txt`, `99-restored-sha256.txt`.
아카이브 `…rollback-test-<시각>.<고유값>.evidence.tar.gz`(+ `.sha256`)에는 `.bak`·`.orig`·`.rolled`·`.db`(키 포함)와 etcd 원문 임시 파일이 **제외**된다. 증적 편입은 기존 방식(ansible01 취합 → 증적 PR → 독립 리뷰)을 따른다.

## 7. 수동 복구 (자동 복원이 실패했을 때)

대상 CP에서 root로. 다른 CP 2대가 정상이면 클러스터는 그동안 LB로 서비스된다. **이 CP에서 실제 접근 가능한 사본만 쓴다.**

1. **manifest 위치 확인:** `ls /etc/kubernetes/manifests/kube-apiserver.yaml`. 없으면 `ls /etc/kubernetes/manifests-parked/` 에서 `mv /etc/kubernetes/manifests-parked/kube-apiserver.yaml /etc/kubernetes/manifests/`.
2. **설정 복원:** 우선순위는 ① 실행 폴더의 `/root/p1-sec01-secretbox-rollback-test-<시각>.<고유값>/encryption-config.yaml.orig`, ② 사전 준비한 `ENC_BACKUP`(`/root/enc-config-prechange.yaml`). 둘 다 이 CP에 있다. `cp -p <사본> /etc/kubernetes/enc/encryption-config.yaml && chmod 600 /etc/kubernetes/enc/encryption-config.yaml`. 사본이 모두 없을 때만 다른 정상 CP의 같은 파일을 `scp`로 가져오고 해시를 `PEER_SHAS`와 대조한다. control01의 2026-10-02 backup 경로는 이 CP에서 접근할 수 없다면 쓸 수 없다.
3. **해시 확인:** `sha256sum /etc/kubernetes/enc/encryption-config.yaml` 이 시험 전 값(`00-orig-sha256.txt` 또는 `pre-3cp-config-sha.txt`의 `self=`)과 같은지.
4. **재기동:** manifest를 다른 폴더로 옮겼다가 다시 `/etc/kubernetes/manifests/`로 되돌린다(약 30초 간격).
5. **상태 확인:** `kubectl --server=https://<이 CP IP>:6443 get --raw=/readyz`, `crictl ps | grep kube-apiserver`, 실패하면 `crictl logs <id>`·`journalctl -u kubelet -n 100`.
6. **남은 시험 Secret 확인:** `kubectl -n default get secret | grep enc-rb-test-`. 스크립트가 소유 불명확·UID 불일치로 남긴 객체는 `10-owned-secrets.txt`·`10-unowned-secrets.txt`와 대조해 **UID가 같은 것만** 삭제하고, 나머지는 Review Owner에게 보고한다.
7. 그래도 복구되지 않으면 **이 CP를 LB에서 제외된 상태로 두고** 나머지 2대로 서비스를 유지한 채 Review Owner에게 즉시 보고한다.

## 8. 리뷰어가 볼 곳 (스크립트 `sec01-secretbox-rollback-v4.sh`)

1. 설정을 바꾸기 **전** 실패는 재기동 없이 종료되는가(`bail`, `CHANGED` 플래그).
2. 재배치 설정 생성(`awk`)이 `identity`를 앞으로 옮기고 `secretbox`·키를 보존하는가(가짜 설정으로 로컬 확인함).
3. 재기동 후 Ready가 아니면 즉시 복원하는가, 복원 실패 시 실패로 처리하는가(`do_restore` 반환값).
4. Secret **값**을 출력하지 않는가(목록은 `네임스페이스/이름`만, etcd는 `class=`만, 시험 Secret 값은 dummy, etcd 원문 임시 파일은 즉시 삭제하고 아카이브에서 제외).
5. 아카이브에서 키 포함 파일을 제외하는가.
6. 이 CP 한 대만 재기동하는가(`manifests` 이동은 이 노드에서만).
7. `set -e`를 쓰지 않고 단계별 검사로 대체한 판단이 타당한가. **v4에서는 조회 성공과 결과 의미를 분리한 함수(`etcd_prefix`, `secret_names`, `listening`, `secret_state`)가 실패를 빈 값·0건·down으로 바꾸지 않는가.**
8. 시험용 Secret이 어떤 경로로 종료해도 **이번 실행 소유인 것만** 정리되는가(`cleanup_tests`는 모든 종료 경로에서 호출되고, `bail` 경로에서도 소유 확인된 객체는 정리된다. 소유 불명확한 객체는 의도적으로 남기고 FAIL로 센다).
9. 원복 후 검증(5단계)이 `$SKC`(대상 CP 직접)를 쓰는가. LB·다른 CP는 별도 명령인가.

## 9. 알려진 한계

- **v4 시험은 fixture(스텁 `kubectl`/`ss`/`hostname`)에서만 했다.** 실제 kubelet static pod 재생성, containerd ID, etcdctl 출력 형식, `kubectl create -o jsonpath` 응답은 운영에서 검증되지 않았다. 형식 가정은 변경 전(2단계)에서 먼저 검증되며, 어긋나면 변경 없이 종료한다. 변경창 직전에 `precheck`를 읽기 전용으로 먼저 실행해 etcd JSON `health`, `containerID` 가정을 확인한다.
- **(정정)** v4 문서가 "`kubectl delete`는 UID precondition을 노출하지 않는다"고 쓴 것은 **틀렸다.** `kubectl delete --raw <경로> -f -`로 `DeleteOptions` 본문을 보낼 수 있고, infra-kubernetes `acnet-9-cleanup.sh`가 같은 방식을 쓴다. v4.1은 이 방식으로 서버 측 UID precondition 삭제를 구현했다. 실제 `kubectl`·API 서버의 `Conflict` 응답 문구(`Precondition failed`)와 `--raw` 응답 처리는 fixture(mock)로만 확인했고 운영에서는 검증되지 않았다. 변경 전 2단계(시험 Secret 생성)에서는 삭제를 시험하지 않으므로, 삭제 동작의 첫 실제 검증은 변경 후 6단계의 정리에서 일어난다. 그래서 `rollback-test`는 **정리 실패 시 남은 시험 Secret 목록과 수동 정리 절차(7절 6항)**를 항상 남긴다.
- fixture는 `/etc/kubernetes` 경로를 환경변수로 바꿔 쓰며 root 검사를 건너뛴다(`FIXTURE=1`). 운영에서는 쓰지 않는다.
- `etcdctl endpoint health --cluster` 출력 형식 가정(`true`/`false`)은 etcd 3.6 기준 추정이다.
- 소유 불명확·UID 불일치로 남긴 객체는 스크립트가 지우지 않는다. 수동 확인이 필요하며(7절 6항), 같은 이름이 다음 `precheck`의 잔여 검사에 걸릴 수 있다.
- 3CP 설정 해시 동일 확인은 실행자가 수집한 `PEER_SHAS`를 대조하는 방식이다. 스크립트가 다른 CP에 접속해 읽어 오지 않는다.

## 10. 소스 PR과 승인 요건 (추적성 1)

- 스크립트(`sec01-secretbox-rollback-v4.sh`), fixture(`test-rollback-v4.sh`), 이 절차서는 변경창 승인 전에 **소스 저장소 PR**로 올리고 exact commit SHA와 각 파일의 SHA-256을 고정한다. PMO/QA 지정([#19 5986833302](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986833302)): 저장소 `321Team/infra-kubernetes`, 경로 `ops/p1-sec01-secretbox-rollback/`(세 파일 + 자기 자신을 제외한 `SHA256SUMS` + 실행 방법·원본 시험 결과 연결). 바이트가 바뀌면 새 SHA와 revision을 기록하고 이전 코멘트의 SHA를 재사용하지 않는다.
- 승인 요건: 위 SHA 고정, 독립 리뷰 exact-head APPROVED, fixture 전부 PASS(결과 로그 첨부), 3절 수동 확인과 R1~R5 기록.
- 이 문서와 PR은 **설정 변경·재기동을 승인하지 않는다.**

## 11. v4.2-20261005 변경 기록 (지정 구현)

- 근거: #19 comment 5987564325. v4.1의 UID precondition 및 대상 CP 직접 원복 확인은 유지한다.
- Gate 입력은 원 명령 rc 성공 후 검증한다. JSON 파서 `python3`가 대상 CP에 있어야 하며, `etcdctl endpoint health --cluster -w json`의 각 endpoint가 `CP_IPS`와 일치하는 HTTPS/2379인지 구조적으로 검사한다. stderr/rc 원본은 각 `<label>.stderr/.rc`, stdout은 `<label>.txt`로 남긴다(health 원본도 `.txt` 확장자이며 내용은 JSON).
- 봉인 성공 전에는 최종 GO/PASS를 출력하지 않는다. 증적 압축/체크섬/sidecar/완료 기록 실패는 nonzero, INCOMPLETE다. 최종 결과는 `<OUT>.completion.txt`와 콘솔을 확인한다. 봉인 전 `run.log`/`99-operation-provisional.txt`만으로 승인하지 않는다.
- 폴더/아카이브에는 고유 접미사가 붙는다. 수동 복구에서 `<시각>.<고유값>`은 실제 실행 폴더로 치환하고 `.orig` 등 키 포함 로컬 사본은 증적 저장소에 올리지 않는다. archive + checksum + completion을 함께 인계한다.
- 기존 v4.1 152개 assertion 유지, 현행 결과: `validation/fixture-results-v4.2.txt`. 실제 실행 승인은 독립 리뷰 이후 별도다.

## 12. v4.3-20261005 — B59-3 실제 Secret 평문 잔여 탐지와 수동 조치

근거: [독립 리뷰 B59-3](https://github.com/321Team/infra-kubernetes/pull/59#pullrequestreview-5410017432), [Kubernetes 공식 encryption-at-rest 문서](https://kubernetes.io/docs/tasks/administer-cluster/encrypt-data/). identity가 첫 provider인 동안 쓰인 Secret은 설정만 복원해도 자동 재암호화되지 않는다.

- 3단계 설정 변경 직전 `21-secrets-before.txt`, 복원 재기동 직후 `60-secrets-after.txt`에 namespace/name/UID/resourceVersion만 capture(stdout/stderr/rc)한다. 사전 조회 실패·형식 오류는 설정 변경 전에 중단한다.
- 새 객체·UID 교체·RV 변경을 `61-secret-candidates.txt`로 추출한다. 시험 이름 prefix 전체를 제외하지 않고, **이번 실행 생성 응답의 정확한 이름+UID만** 제외한다. `62-secret-audit.txt`에는 후보 이름/UID/RV/class, `62-prefix-N.txt`에는 class만 남긴다. 값 앞부분도 기록하지 않는다.
- 후보가 SECRETBOX이면 정상, PLAIN/다른 암호화 형식/unknown/empty/조회 실패이면 ACFAILS로 최종 nonzero다. 사후 목록 실패도 확인 불가로 FAIL이다. 정상·bail·INT/TERM·EXIT의 복원 경로에서 검사를 시도하고, 실패해도 소유 UID cleanup과 봉인은 계속한다. 복원 자체가 실패하면 저장 상태 관측과 무관하게 전체 FAIL을 유지한다.
- 최종 비교가 끝나기 전에 삭제된 Secret의 과거 평문 revision, 이미 삭제된 객체, 오래된 etcd snapshot/WAL까지 무결하게 암호화되었다는 증명은 아니다. 재암호화는 현재 값에 대한 조치이며 과거 revision·백업 처리나 credential rotation은 별도 사고/보안 판단 대상이다. SIGKILL·호스트 장애 등 trap 미실행 시 7절 복구 후 아래 수동 점검을 수행한다.

### 수동 점검·재암호화 (대상 CP 콘솔, 기존 승인 관리자 권한)

1. 먼저 7절로 대상 CP의 원본 설정·재기동·readyz를 복원하고 다른 CP 상태도 확인한다. identity 상태인 API를 경유해 replace하지 않는다. AC-05는 FAIL/미인수로 유지하고 `62-secret-audit.txt`와 RC를 #19에 보고한다. **이 도구는 실제 Secret을 자동으로 replace하지 않는다.** 아래 실제 객체 재쓰기는 담당자와 대상·창을 확인한 복구 승인 아래 수행한다.
2. `60` 목록/비교가 실패했거나 trap이 실행되지 않았으면 metadata를 다시 수집해 `21`과 UID/RV를 비교한다. 기준선이 없거나 완전성을 입증하지 못하면 현재 전체 Secret의 저장 class를 읽기 전용으로 확인하여 범위를 확정한다. 추측으로 후보0 처리하지 않는다. 삭제된 후보의 EMPTY는 별도 metadata NotFound 및 삭제 이력으로 해소 여부를 판단한다.
3. PLAIN 대상별로 새 GET → replace를 한다. 아래 `SELF_IP`, `NS`, `NAME`은 승인 대상의 실제 값으로 채우고, Secret JSON은 파이프 안에서만 전달한다. xtrace/tee/파일 저장을 사용하지 않는다. GET에 포함된 resourceVersion을 유지하므로 경쟁 갱신 시 Conflict로 중단한다. `--force`나 오래된 JSON 재사용은 금지한다.

```bash
# 대상 CP root, 복원된 직접 endpoint; 실행 전 실제 값 지정
: "${SELF_IP:?}" "${NS:?}" "${NAME:?}"
set +x
set -o pipefail
kubectl --kubeconfig=/etc/kubernetes/admin.conf --server="https://$SELF_IP:6443"   -n "$NS" get secret "$NAME" -o json |
  kubectl --kubeconfig=/etc/kubernetes/admin.conf --server="https://$SELF_IP:6443" replace -f -
rewrite_rc=("${PIPESTATUS[@]}")
printf 'get_rc=%s replace_rc=%s\n' "${rewrite_rc[0]}" "${rewrite_rc[1]}"
# 두 rc가 모두 0이어야 다음 확인으로 진행. 실패하면 STOP·보고.
```

4. 같은 대상의 etcd key를 읽어 **값을 출력하지 않고** class를 확인한다. 정상 기준은 조회 rc=0, classifier rc=0, class=SECRETBOX이다. 아래 명령은 metadata/RC만 증적으로 남기고 raw Secret은 저장하지 않는다. 오류·UNKNOWN·EMPTY·PLAIN이면 복구 미완료로 남긴다.

```bash
kubectl --kubeconfig=/etc/kubernetes/admin.conf --server="https://$SELF_IP:6443"   -n kube-system exec "etcd-$(hostname)" -- etcdctl --endpoints=https://127.0.0.1:2379   --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt   --key=/etc/kubernetes/pki/etcd/server.key get "/registry/secrets/$NS/$NAME" --print-value-only |
  python3 -c 'import sys; v=sys.stdin.buffer.read(); c="SECRETBOX" if v.startswith(b"k8s:enc:secretbox:v1:") else "PLAIN" if v.startswith(b"k8s\0") else "EMPTY" if not v else "UNKNOWN"; print("class="+c); sys.exit(0 if c=="SECRETBOX" else 1)'
verify_rc=("${PIPESTATUS[@]}")
printf 'etcd_rc=%s class_rc=%s\n' "${verify_rc[0]}" "${verify_rc[1]}"
```

5. namespace/name·UID·RV 전후 값, 처리 시각·담당자·승인 링크, GET/replace/etcd/class RC와 최종 class를 별도 복구 증적으로 봉인한다. Secret 값이나 JSON은 포함하지 않는다. 모든 대상과 노드/API/etcd 상태가 해소되어도 원래 FAIL 로그를 덮어쓰지 않고 후속 인수로 연결한다. 재시험은 변경창 승인 범위를 따른다.

## 13. v4.4-20261006 — post 점검 apiserver Pod Ready 제한 시간 반복 확인

2026-10-06 rollback-test는 복원 재기동 관측 1초 뒤의 단회 Pod 조회에서 `Ready=false`가 읽혀 `RESULT: FAIL`이었다([결과 기록](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6008210758)). v4.4는 `post` 점검의 mirror Pod 확인만 `APISERVER_POD_WAIT_SEC`(기본 120초) deadline 안에서 반복하고, 정확한 이름·행 수(`EXPECT_APISERVER_PODS`, 선택 `APISERVER_POD_NAMES`)와 개별 호출 timeout(`CALL_TIMEOUT_SEC`, 기본 15초)을 검사한다. deadline 후에도 미충족이면 기존처럼 FAIL이며 마지막 관측 원본과 시도 기록(`post-apiserver-pods{.txt,.stderr,.rc}`, `post-apiserver-pods-attempts.txt`)을 남긴다. `pre` 점검은 단회·엄격하다.

- 변수: `EXPECT_APISERVER_PODS`의 기본값은 `CP_IPS`의 단어 수(기본 3)이므로 현장에서 `CP_IPS`를 바꿔 넘기면 기대 행 수도 함께 바뀐다. `APISERVER_POD_NAMES`를 지정하면 그 이름 집합과 정확히 같아야 한다. 변경하면 실행 기록에 값을 남긴다.
- 시간 예산: post 점검이 최대 약 120초 더 걸릴 수 있다. 11:45 복구 우선·12:00 회복 목표 계산에 포함한다.
- 이 절은 소스 변경 기록이며 Runtime·재시험 GO가 아니다. 아래 §1~§12의 Gate, 중단 기준, 복구 절차는 v4.3과 같다.
