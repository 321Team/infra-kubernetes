# CP1 Canary Kit v1.2

현재 단계: 새 코드 독립 리뷰와 읽기 전용 확인. [선행 이력](../change/ICR-D-NET-01-01/ADVANCE_CHANGE_RECORD_20260910.md)·[실행/복구](ICR-D-NET-01-01-CP-PORTS.md).

## 소스와 환경

server01_ansible / ansible01에서 controller를 sudo 없이 실행한다. 기존 /home/server01_ansible/p1-ans-01의 승인 HEAD 5cbf3b657014b32a20cb5816d90d3ea5fe4951ab, Inventory·키·known_hosts·svc_ansible 및 관리 Source 10.250.10.10을 유지한다. SOURCE_LOCK.json은 배포 commit·파일 해시를 고정하며 실제 독립 승인 확인은 별도다.

## 읽기 전용 점검

배포 ZIP와 sidecar를 /home/server01_ansible에 두고 새 디렉터리에 푼다.

```bash
(
  set -euo pipefail
  cd /home/server01_ansible
  sha256sum --check FDS_CP1_Canary_Kit_v1.2.zip.sha256
  fds_runtime_kit_dir=$(mktemp -d /home/server01_ansible/FDS_CP1_Runtime_Kit.XXXXXX)
  python3 -m zipfile -e FDS_CP1_Canary_Kit_v1.2.zip "$fds_runtime_kit_dir"
  cd "$fds_runtime_kit_dir/FDS_CP1_Canary_Kit_v1.2"
  sha256sum --check docs/change/ICR-D-NET-01-01/SOURCE_SHA256SUMS
  python3 scripts/run-cp-canary.py inspect
)
```

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

오프라인 도구는 승인 기록의 바인딩·필수값을 확인하며 GitHub 승인이나 증적의 진실성을 인증하지 않는다. 운영자는 실제 링크와 검토 범위를 대조한다. RUNTIME_APPLIED_REVIEW_REQUIRED는 Runtime 영수증이며 Permanent·join·최종 보호 인수가 아니다.

## 영수증과 복구

```text
python3 scripts/run-cp-canary.py receipt --run-id ORIGINAL_RUN_ID

python3 scripts/run-cp-canary.py rollback \
  --input /absolute/path/CP1_RECEIPT_RESULT.json --input-sha256 ACTUAL_RECEIPT_FILE_SHA256 \
  --authorization /absolute/path/cp1-rollback-authorization.json --authorization-sha256 ACTUAL_ROLLBACK_AUTH_SHA256
```

root 영수증은 /var/lib/fds/icr-d-net-01-01/cp1-canary/에 보존한다. v1 receipt는 새 코드로 회수하지 않는다. apply/rollback의 mode·input을 각각 기록한다. 미해결 receipt를 삭제하거나 새 run으로 덮어쓰지 않는다.
