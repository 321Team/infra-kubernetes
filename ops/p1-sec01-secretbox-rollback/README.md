# P1-SEC-01 AC-05 Secretbox rollback 증적 도구 (v4.4)

- 책임: [#19 P1-SEC-01](https://github.com/321Team/fds-project-baseline/issues/19) AC "Secret encryption·backup·rotation·**rollback**" 중 rollback Runtime 증적([CR #125](https://github.com/321Team/fds-project-baseline/issues/125)에서 P1 필수·미충족으로 남은 항목).
- 검토 이력: v3 → [소스 차단 B1~B3 5986339672](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986339672) → v4 → [재리뷰 5986833302](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986833302)(B3를 서버 측 UID precondition으로 요구, 이 경로 지정) → v4.1 → v4.2 → **v4.3(B59-3 독립 리뷰 반영)**.
- 상태: **Runtime 코드다. 운영 클러스터에서 한 번도 실행하지 않았다.** 이 PR의 병합·fixture PASS는 Runtime PASS나 AC credit이 아니며, 설정 변경·apiserver 재기동·변경창을 승인하지 않는다. 실행은 PMO/QA 승인 뒤에만 하고, 실행 SHA는 이 PR의 독립 리뷰 완료 SHA와 정확히 같아야 한다.

## 파일

| 파일 | 역할 |
|---|---|
| `sec01-secretbox-rollback-v4.sh` | `precheck`(읽기 전용) / `rollback-test`(대상 CP 1대 apiserver만 재기동, `CONFIRM=YES`) / `postcheck`(읽기 전용). 내부 revision `v4.4-20261006` |
| `test-rollback-v4.sh` | 가짜 클러스터(fixture) 시험. 스텁 `kubectl`·`ss`·`hostname`을 PATH에 두고 클러스터에는 접근하지 않는다 |
| `ac05-rollback-runbook-v4.md` | 시험 절차서(rev `v4.4-20261006`) |
| `validation/fixture-results-v4.4.txt` | PR66 `dfb9a3c` 작성자의 전체 254 PASS/0 FAIL 이력. 통합 fixture는 273 단언이며 전체273은 미실행 |
| `validation/parser-consolidation-20261007.txt` | 통합 후보에서 실제 재실행한 파서 27 PASS/0 FAIL. 이전 HEAD의 전체 결과와 구분 |
| `SHA256SUMS` | 위 파일의 SHA-256(자기 자신 제외) |

## 실행 방법(승인된 변경창에서만, 대상 CP 콘솔 root)

```bash
ENC_BACKUP=<이 CP에 둔 설정 사본, mode 600, 해시 동일> PEER_SHAS="<다른 CP 2대의 enc 설정 sha256>" bash sec01-secretbox-rollback-v4.sh precheck
ENC_BACKUP=... PEER_SHAS="..." CONFIRM=YES bash sec01-secretbox-rollback-v4.sh rollback-test
ORIG_SHA=<시험 전 sha256> bash sec01-secretbox-rollback-v4.sh postcheck
```

fixture 시험(로컬, 약 7분): `bash test-rollback-v4.sh ./sec01-secretbox-rollback-v4.sh` (`ONLY=S1,S16` 처럼 case를 골라 실행할 수 있다)

## v4.1이 바꾼 것(재리뷰 5986833302 B3)

- 시험 Secret 삭제를 GET→이름 DELETE가 아니라 `kubectl delete --raw /api/v1/namespaces/default/secrets/<name> -f -` + `DeleteOptions.preconditions.uid`(생성 응답 UID)로 **서버가 UID를 검사**하게 했다(`acnet-9-cleanup.sh`와 같은 방식).
- 409 Conflict·NotFound·삭제 실패·결과 불명확이면 **이름 삭제로 fallback하지 않는다.** 성공 후 GET으로 명시적 NotFound를 확인하고, cleanup 미완료는 최종 FAIL이다.
- 삭제 본문(`preconditions.uid`)과 응답 앞부분을 `50-cleanup.txt`에 남긴다.
- fixture 추가: S1(삭제 본문의 UID가 생성 응답 UID와 일치, 이름 삭제 0회), S16(GET과 DELETE 사이 UID 교체 → 서버 Conflict, 외부 객체 보존), S19(삭제 요청 실패·결과 불명확, fallback 없음). 기존 AlreadyExists·응답 유실·정리 실패 검사는 유지.
- v4 문서의 "kubectl은 UID precondition을 노출하지 않는다"는 서술은 **틀렸다**(절차서 9절에 정정).

## 한계(그대로)

- 실제 `kubectl`/API 서버의 `Conflict` 응답 문구, `--raw` 응답 처리, kubelet static pod 재생성, containerd ID, etcdctl 출력 형식은 **운영에서 검증되지 않았다.** etcd 출력 형식은 변경 전 2단계에서 먼저 검증된다(어긋나면 변경 없이 종료). 삭제의 첫 실제 검증은 변경 후 6단계 정리에서 일어난다.
- 3CP 설정 해시 동일 확인은 실행자가 수집한 `PEER_SHAS` 대조다.
- 예방 rotation, etcd snapshot 복원(DR), 잘못된 키·깨진 설정에서의 복구는 이 시험이 증명하지 않는다.

## v4.2-20261005 — 지정 구현 후속

근거: [지정 구현 요청 5987564325](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5987564325), [#59 리뷰](https://github.com/321Team/infra-kubernetes/pull/59#pullrequestreview-5409625292). 구현: 이권욱(CODEX_ASSISTED), 독립 리뷰: 이재환(`supreme33-kr`) 요청 예정. 과거 `fixture-results-v4.1.txt`는 당시 결과로 보존한다.

- Gate의 nodes·각 CP readyz·etcd health/status·apiserver Pods·Secret 목록·kubeconfig server는 원 명령 stdout/stderr/rc를 먼저 수집한다. rc!=0인 정상처럼 보이는 출력으로 진행하지 않는다. rollback 중 대상 CP Secret 목록 실패도 즉시 복구 경로로 간다.
- **실행 전 python3 필수.** etcd health JSON 배열, 필수 endpoint/health, `CP_IPS`의 각 `https://<IP>:2379` 집합, 중복 없음, boolean true를 검증한다. 파서 부재/실패나 형식 불일치는 변경 전 Gate에서 거부한다. fixture의 가상 a/b/c endpoint는 실제 형식의 CP 주소로 교체했으며 기존 152개 assertion은 유지한다.
- `finalize`에서 tar rc, archive 비어 있지 않음, SHA-256 sidecar 쓰기 및 검산을 검사한다. 실패하면 `EVIDENCE_STATUS=INCOMPLETE`, nonzero이며 전체 GO/PASS는 출력하지 않는다. 소유 UID cleanup/설정 복구는 봉인 실패와 별개로 수행한다.
- 실행 폴더는 `mktemp -d`로 고유하게 생성한다. `<OUT>.completion.txt`는 archive **밖의 최종 기록**이다. `99-operation-provisional.txt`는 봉인 전 실행 결과만 보존한다. run.log를 봉인 후 갱신하지 않으며 최종 결과는 콘솔과 completion에 남긴다. 인수 시 실제 process rc=0, GO/PASS, EVIDENCE_STATUS=COMPLETE, archive sidecar 검산을 함께 확인한다.
- Gate 실패: ENC/manifest 변경·Secret 생성 없이 nonzero. 변경 이후 조회/봉인 실패: 원상복구·UID cleanup 결과를 기록하고 전체 FAIL로 종료한다. **변경 후 실패에 대해 '변경 자체가 없었다'고 주장하지 않는다.**
- 증적 저장 실패 후 성공 기록을 수동으로 덮어쓰지 않는다. 기존 출력과 오류를 보존하고 원인 해소 후 새 실행으로 수집한다. 운영 재실행은 기존 변경창/승인 범위를 따른다. 자동 복구 실패 시 runbook 7절을 적용한다.

추가 시험: 정상 출력+nonzero(health/readyz/nodes/status/Pods/목록/config), malformed/중복/예상 밖/필수 필드 누락 JSON, 파서 실패, tar/checksum/sidecar 실패, rollback 목록 실패, precheck/postcheck 봉인 실패. 실제 서버 실행은 포함하지 않는다.

## v4.3-20261005 — B59-3 후속

[독립 리뷰](https://github.com/321Team/infra-kubernetes/pull/59#pullrequestreview-5410017432)의 실제 Secret 평문 잔여를 탐지한다. 변경 직전/복원 직후 metadata의 UID·RV로 생성·교체·갱신 후보를 추출하고, 정확한 소유 UID만 제외한 뒤 etcd 저장 class를 확인한다. PLAIN/조회 실패/불명확 형식은 최종 nonzero이며 원복·cleanup은 계속한다. 실제 Secret 바이트를 증적으로 노출하지 않도록 기존 앞 40바이트 출력도 class-only로 바꿨다.

수동 재암호화의 전제·경쟁 갱신 처리·원문 비저장·복구 기록·과거 revision 한계는 runbook 12절을 따른다. 자동 재암호화와 Runtime 승인은 포함하지 않는다. 구현자 이권욱과 독립 Reviewer 이재환을 분리하며 변경분 재리뷰를 요청한다.

## v4.4-20261006 — post 점검 apiserver Pod Ready 보완 (2026-10-06 rollback-test FAIL 후속)

근거: [결과 기록 6008210758](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6008210758)(`RESULT: FAIL`: 복원 재기동 관측 1초 뒤 `kube-apiserver-k8s-control03` `Ready=false` 단회 조회), Review Owner [딥리뷰 6010258709](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6010258709) §1("`checks post`의 mirror Pod Ready 단회 조회가 부족한 부분. 기존 검사를 삭제하거나 FAIL을 무시하는 방식으로 고치지 않는다").

| 변경 | 내용 |
|---|---|
| 반복 확인 | `post` 점검의 apiserver mirror Pod 확인만 `APISERVER_POD_WAIT_SEC`(기본 120초) deadline 안에서 `POLL_SEC` 간격으로 반복한다. **`pre` 점검은 현재 상태를 단회로 엄격히 본다**(시작 Gate 약화 없음) |
| 정확한 이름·행 수 | 행 수 = `EXPECT_APISERVER_PODS`(기본 `CP_IPS` 개수 = 3), 이름은 `kube-apiserver-*`이고 중복 없음, **자기 Pod(`kube-apiserver-$HOST`) 존재**, 모두 `Ready=true`. `APISERVER_POD_NAMES`(공백 구분)를 지정하면 그 집합과 정확히 같아야 한다 |
| 개별 호출 timeout | 호출마다 `timeout ${CALL_TIMEOUT_SEC}s`(기본 15초). 응답 없음은 rc 124로 기록한다 |
| 실패 보존 | deadline까지 미충족이면 **기존처럼 FAIL**이다. 마지막 관측은 `post-apiserver-pods.{txt,stderr,rc}`, 시도별 요약(시각·시도 번호·rc·사유)은 `post-apiserver-pods-attempts.txt`, FAIL 사유에 시도 횟수·deadline·마지막 rc가 남는다 |
| 일시 실패 집계 | deadline 안의 일시적 NotReady·조회 실패는 FAIL로 세지 않는다(`capture()`를 쓰지 않음). deadline 후에도 미충족이면 1회 FAIL |

- **GO 조건과 시간 예산:** 최악의 경우 post 점검이 최대 `APISERVER_POD_WAIT_SEC`(120초) + 마지막 호출 시간만큼 길어진다. 11:45 복구 우선·12:00 회복 목표 계산에 반영한다. SIGKILL로 시간을 맞추지 않는 원칙은 그대로다.
- 이번 변경은 **Runtime 승인이나 재시험 GO가 아니다.** 원래 승인된 v4.3(HEAD `504b2523…`, script `10144527…`)의 승인·증적은 그대로 유지하며, v4.4로 재시험하려면 소스 승인과 새 창·LB 복구 담당·평문 잔여 처리 계획이 별도로 필요하다(Review Owner 6010258709).
- 변경하지 않은 것: 복원·정리 로직, B59-3 탐지, 봉인, Gate, 다른 점검 항목.


## 2026-10-07 B2 소스 통합 및 추가 fixture

기준 실행 소스는 PR66 `dfb9a3c9d10baf22c2efe7143f1d5538eb4bff29`이며 script SHA256
`e80d8936cd6e846ebc04d999e9df68052497fbccfc7786d3f5bf8bd3584bb61e`, 내부 REV `v4.4-20261006`을 그대로 유지한다.
PR67과 PR66에 같은 수정이 들어온 뒤 [검토 5430584477](https://github.com/321Team/infra-kubernetes/pull/67#pullrequestreview-5430584477)의 통합안 1을 적용했다.
PR67은 이제 추가 fixture·증적 보완이며 원래 작성자 브랜치의 실행 스크립트를 바꾸지 않는다.

- 기존 254 단언을 그대로 두고 precheck/rollback Gate/post 실패 후 원복·UID 정리·봉인/read-only postcheck의 19 단언을 추가했다.
- awk 스텁은 파서 파일의 pre/post 접두어를 반영하므로 읽기 전용 postcheck에도 정확히 실패를 주입한다. 다른 awk 호출은 기존처럼 실제 바이너리로 전달한다.
- 현재 실제 재실행은 파서 **27 PASS/0 FAIL**와 STATIC3개다. 선택 실행은 전체 fixture의 같은 helper와 파서 절을 추출해 쓰며 ONLY 미선택 절의 진단 잡음을 피한다.
- 전체 통합 fixture **273개는 미실행**이다. PR66 작성자254와 옛 PR67 `3270aac`의 전체265 결과는 각각 해당 HEAD의 이력이다.
  `validation/*v4.4-b2*`는 옛3270 후보의 source SHA `df915e72...`에 결속된 원본이며 현재 실행 번들 해시로 사용하지 않는다.
- 기존3270과 현재dfb9 소스는 전체 주석 행과 REV 대입만 제외하면 모든 실행 행이 일치한다. 기존 영향 없는 회귀 결과는 이 근거로 재사용하며 새 전체273 실행으로 표시하지 않는다.
- 상세 명령/환경/시각/종료코드/해시/재사용 비교는 `validation/*consolidation-20261007*`에 기록했다. 현재 소스·runbook 및 기존 원본 로그는 바이트 그대로다.

새 HEAD에 대한 사람의 독립 판단과 최종 승인·원 PR 반영·병합·현장 재시험 GO는 각각 별도다.
이 문서와 모의시험만으로 AC-05 충족이나 시험 승인으로 표시하지 않는다. AI 사용: CODEX_ASSISTED.
