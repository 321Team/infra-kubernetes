# CP1 Canary Kit v1.3

현재 단계: 2026-09-14 실행창 통제·복구 절차 보완 제출, 새 HEAD 독립 리뷰 및 실제 Runtime 조건 확인. 09-10 현장 Linux 37개·CP inspect 증적 수용은 당시 source 범위로 유지한다. [선행 이력](../change/ICR-D-NET-01-01/ADVANCE_CHANGE_RECORD_20260910.md)·[실행/복구](ICR-D-NET-01-01-CP-PORTS.md).

[2026-09-10 현장 검토](../change/ICR-D-NET-01-01/ONSITE_REVIEW_20260910.md): afc246d로 수집한 mvoxv1pm은 Linux 기록 부족을 해소했다. 실제 apply의15분 입력·승인 source 조건을 위한 새 inspect는 별개다. 배포 ZIP의 SOURCE_LOCK에 적힌 실행 commit과 이후 증적 편입 commit을 구분한다.

## 소스와 환경

server01_ansible / ansible01에서 controller를 sudo 없이 실행한다. 기존 /home/server01_ansible/p1-ans-01의 승인 HEAD 5cbf3b657014b32a20cb5816d90d3ea5fe4951ab, Inventory·키·known_hosts·svc_ansible 및 관리 Source 10.250.10.10을 유지한다. SOURCE_LOCK.json은 배포 commit·파일 해시를 고정하며 실제 독립 승인 확인은 별도다.

## 읽기 전용 점검

이번 v1.3은 PR #2의 고정 Git commit으로 제공한다. 아래 `REVIEWED_CP1_COMMIT`은 실제 리뷰 대상 40자리 HEAD로 치환한다. 실행 전 [원본 보존·복구 §1](CP1_CANARY_RECOVERY.md)을 완료한다. server01_ansible 계정으로 **새 디렉터리**를 사용한다.

```text
git -C /absolute/path/infra-kubernetes worktree add --detach /absolute/path/cp1-v1.3 REVIEWED_CP1_COMMIT
cd /absolute/path/cp1-v1.3
git rev-parse HEAD
sha256sum --check docs/change/ICR-D-NET-01-01/SOURCE_SHA256SUMS
python3 scripts/run-cp-canary.py inspect
```

과거 `FDS_CP1_Canary_Kit_v1.2.zip`과 그 SOURCE_LOCK는 당시 실행 source의 보존·복구 자료다. v1.3 코드가 들어 있다고 간주하거나 lock을 새 HEAD로 바꾸지 않는다.

ARCHIVE와 SIDECAR를 함께 제출한다. 과거 jvgkgfm9는 source·시각이 달라 새 apply 입력으로 사용할 수 없다. 새 Calico 조회 실패는 실제 원인을 검토한다.

## Linux 모의 테스트

```bash
python3 -m unittest discover -s tests -p 'test_cp*.py' -v
```

OS/Python·정확한 source·시각·종료 코드와 출력 전체를 기록한다. 최신 수·결과는 VALIDATION.md를 따른다. 구068661e의19개 테스트 결과와 새 소스 결과를 구분한다. mock 성공은 실서버 방화벽/etcd 연동 인수가 아니다.

## Runtime 적용

정확한 새 HEAD의 독립 승인·실행 결정·보호/복구 검토 후 cp1-runtime-authorization.template.json을 별도 파일로 복사해 실제 값으로 채운다. source/target/mode/input·정책 SHA, operator GWONUK, reviewer hayeounglee, 실제 PR #2 APPROVED URL, 실행 결정 URL, 보호·복구 SHA, 작업창·백업·콘솔·동결 확인을 입력한다. null과 예시를 승인값으로 사용하지 않는다.

```text
python3 scripts/run-cp-canary.py apply \
  --input /absolute/path/k8s-control01.json --input-sha256 ACTUAL_INSPECTION_SHA256 \
  --authorization /absolute/path/cp1-apply-authorization.json --authorization-sha256 ACTUAL_AUTHORIZATION_SHA256
```

승인 JSON 원문과 해시를 원격에 전달한다. 원격이 동일 source/mode/input·검토 기록·작업창을 다시 확인하고 각 명령과 새 쓰기 직전에 남은 시간을 검사한다. UTC 종료 시각은 제외하며, monotonic 예산은 벽시계 역행으로 늘어나지 않는다. 명령 timeout을 남은 창으로 제한하지만 이미 전달된 firewalld 요청의 취소를 보장하지는 않는다. 확인 전 timeout/만료는 소유권 불명으로 남기며 뒤따르는 쓰기를 중단한다. 영수증 보존은 창 종료 뒤에도 수행한다.

오프라인 도구는 승인 기록의 바인딩·필수값을 확인하며 GitHub 승인이나 증적의 진실성을 인증하지 않는다. 운영자는 실제 링크와 검토 범위를 대조한다. RUNTIME_APPLIED_REVIEW_REQUIRED는 Runtime 영수증이며 Permanent·join·최종 보호 인수가 아니다.

## 영수증과 복구

```text
python3 scripts/run-cp-canary.py receipt --run-id ORIGINAL_RUN_ID

python3 scripts/run-cp-canary.py rollback \
  --input /absolute/path/CP1_RECEIPT_RESULT.json --input-sha256 ACTUAL_RECEIPT_FILE_SHA256 \
  --authorization /absolute/path/cp1-rollback-authorization.json --authorization-sha256 ACTUAL_ROLLBACK_AUTH_SHA256
```

root 영수증은 /var/lib/fds/icr-d-net-01-01/cp1-canary/에 보존한다. v1 receipt는 새 코드로 회수하지 않는다. apply/rollback의 mode·input을 각각 기록한다. 미해결 receipt를 삭제하거나 새 run으로 덮어쓰지 않는다.

원본 소스 분실/변경, API 장애, `pending/uncertain`, 부분 복구는 [원본 보존·비상 복구 절차](CP1_CANARY_RECOVERY.md)를 따른다. apply와 rollback의 승인 해시·실행창은 영수증에 각각 기록한다.
