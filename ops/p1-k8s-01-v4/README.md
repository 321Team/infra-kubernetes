# P1-K8S-01 리드 전달 패키지 v4.0

> **저장소 편입 상태 (2026-09-24, infra-kubernetes #26)**
> - `5395065`: 전달받은 v4.0 원본 그대로 보관. 해시는 `SHA256SUMS`.
> - `b0d86e9`: 2026-09-22 Gate 2에서 **독립 리뷰 없이 실제 실행한 수정본**(`UNAPPROVED_RUNTIME_SNAPSHOT`). 해시는 `MODIFIED_SHA256SUMS`. **이 버전으로 재실행 금지.**
> - 리뷰 대응 커밋: 사후 리뷰(#26 REQUEST_CHANGES)의 차단 2건을 수정한 버전. 해시는 `REVIEW_FIX_SHA256SUMS`, 내용은 `SCRIPT_MODIFICATION_HISTORY.md` §4.
> - 운영 재render·재실행은 이 HEAD에 대한 재리뷰 승인 전까지 금지한다. 이 저장소 편입은 9/22 실행을 소급 승인하지 않는다.
> - ICR 후속(초안): Worker 간 9100/10250 scrape 정식 수정과 #26 재실행 조건 대응은 `ICR_SCRAPE_FIX.md`. 패키지 전체 해시는 `PACKAGE_SHA256SUMS`(기존 `SHA256SUMS`는 v4.0 원본 기록).

G2-01~08의 권고·코드 보완을 반영한 최종 전달안이다. **운영 서버에 적용하지 않았으며 Runtime PASS와 실서버 배포 승인을 포함하지 않는다.**

## 전달 내용

| 파일 | 용도 |
|---|---|
| RUNBOOK_FINAL.md | Notion 교체 본문: 입력 확정, GNP 선행, 한 대씩 HEP/failsafe 전환, 해당 노드 재부팅, 보호 복원 후 rollback |
| TEST_MATRIX.md | 20개 Runtime 시험·증적 판정 기준과 실제 실행 명령 |
| ICR_ADDENDUM.md | 정본 §6.6 전환 순서·System API/NTP/이미지·후속 보안 인수의 기존 ICR 반영 문안 |
| TRACEABILITY.md | R1~R5/A1/A2, G2-01~08 수정 위치·코드·잔여 실측 연결 |
| VALIDATION.md | 로컬 정적·모의 검증 결과 및 미수행 범위 |
| code/ | 정책 생성, Calico manifest 준비, node별 API 변경/복구, 호스트 checkpoint, AVC, Cockpit, 통신/회귀 코드 |
| config/ | 실측·결정 입력, Calico image digest lock, 보호 복원 receipt 입력 양식 |
| policy_templates/ | GNP/HEP/node failsafe의 노드별 전체 YAML 열람본. `.yaml.in`은 적용하지 않음 |
| tests/ | 로컬 회귀·실패 조건 및 CRD 일부 스키마 대조 코드 |
| validation/ | 이번 로컬 검증 로그 |
| SHA256SUMS | 패키지 내부 파일 무결성 목록 |

## 적용 상태

문서·코드 보완은 반영했다. NTP 목적지, System/Test 이미지 digest와 공급 방식, 실제 인터페이스·MTU, Felix 유효 설정, DNAT 후 System API 경로, 보호 checkpoint와 복구 경로, 전환 변경 승인 증적은 아직 제공되지 않았다. `runtime-input.json`을 임의 값으로 채우지 않는다. renderer는 입력이 없거나 근거 해시가 맞지 않으면 적용 파일 생성을 중단한다.

`.yaml.in` 열람본에는 NTP·인터페이스의 명시적 미확정 표식이 있다. 이는 최종 운영 파일이 아니다. `code/render.py`가 검토된 입력으로 실제 `.yaml`과 node failsafe `.json`, 테스트 리소스 및 SHA256SUMS를 생성한다. 생성 완료 상태도 `GENERATED_FOR_SERVER_VALIDATION`이며 최종 승인이 아니다. 원문의 광범위 global failsafe patch, 5개 HEP 일괄 apply, HEP 선삭제 복구는 폐기한다.

## 리드 전달문

추가 재리뷰의 G2-01~08을 본문과 코드에 반영한 최종안을 전달합니다. SSH·승인 DNS·역할별 포트·Host Egress를 정합화하고, GNP 선행 및 노드별 failsafe/HEP→전환 시험→재부팅 확인 순서로 변경했습니다. 복구는 이전 보호를 먼저 복원한 뒤 대상 HEP와 node별 failsafe만 되돌리며, AVC 구간 보존과 Cockpit 차단 후 지속성 검증도 포함했습니다. 아직 제공되지 않은 서버 실측값은 입력·검증 단계로 명시해 임의 추정 없이 적용 파일을 생성하도록 구성했습니다. 이 전달본의 문서·코드 반영과 실제 운영 적용 승인은 구분하며, 기존 Gate 0 진행은 유지합니다.

## 검증 재현

작성 호스트에 승인된 Python 3/PyYAML을 준비한다. 테스트는 운영 API 호출이나 호스트 방화벽 변경을 수행하지 않는다.

```bash
sha256sum --check SHA256SUMS
python3 tests/validate.py
python3 tests/check-crd.py --source /path/to/calico-v3.32.1.upstream.yaml
python3 -m compileall -q code tests
bash -n code/cockpit.sh
```

CRD 대조는 파일의 API/필드/타입 등 일부 규칙 검사이며 전체 JSON Schema/CEL 검증을 대신하지 않는다. 정확한 범위는 VALIDATION.md를 따른다. 테스트의 문서용 주소 `192.0.2.123`, `test.invalid` 및 합성 digest는 운영 승인값이 아니다.
