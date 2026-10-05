# P1-SEC-01 AC-05 Secretbox rollback 증적 도구 (v4.1)

- 책임: [#19 P1-SEC-01](https://github.com/321Team/fds-project-baseline/issues/19) AC "Secret encryption·backup·rotation·**rollback**" 중 rollback Runtime 증적([CR #125](https://github.com/321Team/fds-project-baseline/issues/125)에서 P1 필수·미충족으로 남은 항목).
- 검토 이력: v3 → [소스 차단 B1~B3 5986339672](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986339672) → v4 → [재리뷰 5986833302](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-5986833302)(B3를 서버 측 UID precondition으로 요구, 이 경로 지정) → **v4.1(이 PR)**.
- 상태: **Runtime 코드다. 운영 클러스터에서 한 번도 실행하지 않았다.** 이 PR의 병합·fixture PASS는 Runtime PASS나 AC credit이 아니며, 설정 변경·apiserver 재기동·변경창을 승인하지 않는다. 실행은 PMO/QA 승인 뒤에만 하고, 실행 SHA는 이 PR의 독립 리뷰 완료 SHA와 정확히 같아야 한다.

## 파일

| 파일 | 역할 |
|---|---|
| `sec01-secretbox-rollback-v4.sh` | `precheck`(읽기 전용) / `rollback-test`(대상 CP 1대 apiserver만 재기동, `CONFIRM=YES`) / `postcheck`(읽기 전용). 내부 revision `v4.1-20261005` |
| `test-rollback-v4.sh` | 가짜 클러스터(fixture) 시험. 스텁 `kubectl`·`ss`·`hostname`을 PATH에 두고 클러스터에는 접근하지 않는다 |
| `ac05-rollback-runbook-v4.md` | 시험 절차서(rev `v4.1-20261005`) |
| `validation/fixture-results-v4.1.txt` | fixture 실행 로그 원본: **152 PASS / 0 FAIL** |
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
