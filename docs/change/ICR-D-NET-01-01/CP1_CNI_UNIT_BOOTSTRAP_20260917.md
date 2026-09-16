# CP1 unit 정의 게시·최초 관측 연결 — 2026-09-17

상태: **UNIT_DEFINITION_AND_BOOTSTRAP_BOUNDARIES_IMPLEMENTED / OPERATIONAL_INTEGRATION_INCOMPLETE / RUNTIME_NOT_RUN**.

## 수정한 실제 통합 문제

이전 코드에는 빈 집합의 최초 관측을 seed하는 함수가 있었지만, 실제 `LocalObserver`는 모든 관측에 이미 실행 중인 guard를 요구했다. 따라서 `빈 집합 관측 → seed 저장 → guard 시작` 순서를 실제 관측기로 연결할 수 없었다. 모델 IO만 사용하는 단위 시험에서는 발견되지 않은 문제다.

이번에는 같은 native/save/API/CRI·고정 상태·로드된 unit 정의 검사를 통과한 **빈 Runtime 집합**만 초기 관측할 수 있도록 연결했다. bootstrap 관측은 `guard_armed=false`이며 active mutation cache를 채우지 않는다. 실제 추가는 별도의 일반 관측으로 살아 있는 guard를 확인해야 한다. 먼저 reconcile을 잘못 시도해 철회 상태가 됐다면, guard가 살아나도 같은 실행을 다시 활성화할 수 없다. lease나 관측 상한도 연장하지 않는다.

## unit 정의 게시 모듈

`cp1_cni_unit_files.py`는 root 보관 소스와 lease manifest를 대조한 뒤, 미사용 unit 이름에 정확한 정의를 게시하고 필요할 때 daemon에 정의를 로드한다.

- 검토된 manifest에 운영 entrypoint가 없거나 보관 경로와 unit의 실제 ExecStart 경로가 다르면 최초 쓰기 전 중단한다.
- 기존 파일은 내용이 같아도 인계받지 않는다. symlink/hardlink·writable ancestor·기존 로드 unit/drop-in·실행 중인 unit·대기 job을 거부한다.
- 별도 locked journal에 각 게시 의도를 fsync한 뒤 완성된 임시 inode를 `link`로 새 이름에 게시한다. 기존 파일을 교체하는 rename을 사용하지 않는다.
- 저장된 의도의 정확한 전후 상태만 재개한다. 이미 완료한 파일의 누락이나 알 수 없는 바이트는 덮어쓰지 않는다. 링크 게시 후 디렉터리 sync가 실패하면 완료를 보고하지 않고 재개 시 sync한다.
- SIGKILL 구간에 임시 hardlink가 함께 남으면 자동 완성으로 추정하지 않고 차단한다. 원본 파일/증거를 보존하며 진단 후 처리해야 한다.
- `daemon-reload` 이전에 LOAD_INTENT를 저장한다. 불확실한 반환값·timeout·출력 잘림은 성공으로 처리하지 않는다. 성공 반환 뒤에도 로드된 argv/Timeout/OnFailure 등과 inactive 상태를 별도 대조한다.
- **enablement link 생성·timer 시작·service 실행·방화벽 변경 기능은 없다.** `DEFINITIONS_LOADED`는 `recovery_armed`가 아니다. 현재 운영 entrypoint는 아직 구현되지 않았으므로 이 모듈을 실제 운영 설치에 사용하도록 안내하지 않는다.

guard 조회와 recovery dispatch에서도 Python의 `False == 0`을 성공 종료코드로 잘못 수용하지 않도록 정수형을 명시적으로 검증한다.

## 검증

**318/318 PASS, 실패 0, 오류 0, skip 0.** 직전 279개 대비 신규 39개다.

| 신규 시험 | 개수 | 검증한 위험 |
|---|---:|---|
| 실제 임시 파일시스템·Journal의 unit 정의 게시 | 28 | 중단·disk/fsync 실패·race·충돌·링크·권한·재개·로드 불일치 |
| guard 반환코드 형식 | 2 | boolean을 성공으로 처리하는 오판 |
| 전체 관측기와 bootstrap/Manager 연결 | 9 | 최초 시작 교착·빈 집합 조건·guard 없는 추가·재활성화·신선도·cache |

테스트용 unit 경로는 root private HOME 아래의 임시 디렉터리다. `systemctl` 실행은 mock이며 실제 unit/service/firewall을 변경하지 않았다. entrypoint는 명시적인 무해한 mock 바이트다. native/compatibility 구조 fixture는 실제 자료에서 유래했지만 CRI peer와 unit 상태는 mock이다. 이 결과는 실제 활성화·Pod 교체·전체 회수 성공을 입증하지 않는다.

## 남은 운영 통합

1. 원 영수증·전체 before/expected-after·검토 source/plan을 결합하는 activation producer.
2. 독립 회수를 먼저 설치·검증한 뒤 제한된 순서로 실제 활성화하는 entrypoint.
3. 원 Runtime 6개, 관리 경로, Permanent/Runtime 객체, unit 종료·삭제까지 검증하는 독립 전체 회수.
4. 실제 CP1 인수와 인증된 관측을 후속 controller 전후 gate에 연결.
5. 실제 CRI peer layout/시간 예산·Pod 교체·zero endpoint native 표현의 대상 검증.

정본의 CP1 인수·CP2/3 변경·Join 보류는 유지한다. 기존 원본 네 파일과 영수증 계약은 변경하지 않았다. 이 중간 보완으로 새로운 리뷰 요청이나 VM 재수집 요청을 만들지 않는다. **통합 운영 묶음의 독립 리뷰 준비 완료로 표시하지 않는다.**

## 재현

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
sha256sum --check CP1_CNI_HANDOFF_SHA256SUMS
```

root 소유권을 검사하는 staging 10개와 unit 파일 28개는 non-root 프로세스에서 skip된다. 그런 실행을 이번 318/318 결과와 동일하게 보고하면 안 된다.
