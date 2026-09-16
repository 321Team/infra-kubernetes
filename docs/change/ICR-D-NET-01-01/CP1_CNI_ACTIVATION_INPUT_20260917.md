# CP1 CNI 원 영수증 연결 및 실행 입력 준비

상태: `ACTIVATION_INPUT_AND_OBSERVER_BRIDGE_IMPLEMENTED / OPERATIONAL_INTEGRATION_INCOMPLETE / RUNTIME_NOT_RUN`.

이 변경은 이전 방화벽 전환·회수 모듈과 원 영수증 사이의 입력 공백을 메운다. 기존 수집기가 보호 상태를 확인하며 읽던 원 영수증 형식의 전체 방화벽 snapshot을 재사용한다. 별도 조회나 운영자 재수집을 추가하지 않고, 원 영수증 → 현재 전체 before → 정확한 초기 변경 계획 → 고정 lease → membership 관측을 연결한다.

## 구현

- `cp1_cni_activation.py`: 원본 **파일 바이트 해시**를 확인한 뒤 변경하지 않은 `run-node-firewall.py`의 원 영수증 검증기를 실행한다. 소스 파일 4개의 고정 해시도 유지한다. 원 영수증의 `after` 전체와 현재 snapshot이 정확히 같아야 한다.
- 수집기는 각 보호 조회에서 새로 얻은 snapshot만 `original/firewall`과 `original/firewall_after`로 보존한다. 두 번째 조회가 값을 제공하지 않으면 첫 결과를 재사용하지 않는다. 추가 snapshot 명령은 없다.
- linked observer는 public zone·두 plane의 모든 policy·direct rules·NetworkManager 연결·zone을 같은 수집의 다른 projection과 대조한다. 전체 native/save/API/CRI 검증을 함께 통과해야 한다. NetworkManager zone도 고정 상태 해시에 포함한다.
- producer는 명시적인 24개 소스 의존성 목록, 원본·수집·peer·실행 요청 해시, before, 10개 변경 단계, XML 정의, 예상 빈 활성 상태를 하나의 payload로 묶는다. 이 payload 해시를 lease가 참조하므로 자기 참조 해시가 생기지 않는다. `policy_sha256`는 기존 전환 모듈과 같은 XML 문서 해시다.
- wall/boottime의 시작·종료는 요청에 고정한다. 최대 2시간, 회수 여유 180초, 준비 및 인계 시 시작으로부터 30초 미만 조건을 적용한다. 재호출로 기한을 연장하지 않는다. 사람 리뷰 이후 오래된 proposal을 재사용할 수 있다는 뜻이 아니다.
- 인계 직전에 새 전체 capture를 다시 대조한다. before 또는 원본 연결이 달라졌으면 초기 transaction state를 만들지 않는다. 호출자는 이 초기 journal을 기존 파일과 충돌 없이 한 번만 fsync해야 한다.
- 새 managed 관측 경로는 Runtime의 소유 set entries와 policy enable 상태 두 필드만 정규화한다. 그 외 Permanent·외부 zone/policy·원 6개 규칙·보호 상태·관리 연결 변경은 차단한다. 실제 unit·timer·boot recovery 정의로 검증한 활성 guard가 필수이며 이전 schema나 unarmed bootstrap으로 우회하지 않는다.
- baseline의 다른 zone에 Pod 또는 관리 interface가 배정돼 있으면 reload 계획을 거부한다. 기존 관측 경로가 배제하던 classification 충돌을 새 입력 경로도 배제한다.

## 실행 가능한 준비 도구의 범위

`prepare-cp1-cni-managed.py`는 `check`와 `collect`만 제공한다. 두 모드 모두 방화벽·service·unit을 변경하지 않는다.

| 모드 | 입력 및 실행 권한 | 결과 |
|---|---|---|
| `check` | 원본·요청·manifest와 해시, 보관된 evidence와 파일 바이트 해시. 일반 계정 가능 | 원본/전체 상태/소스/시간/계획 일치 여부. 운영 환경의 현재 상태나 provenance를 인증하지 않는다. |
| `collect` | 원본·요청·manifest와 해시. CP1에서 `sudo/root` 읽기 권한 필요 | 기존 local helper로 전체 상태와 CRI를 같은 29초 절대 기한 안에 수집해 입력을 준비한다. 실패 시 수집된 진단 자료와 STOP을 보존한다. |

도구는 JSON을 stdout으로 출력한다. 원본 snapshot의 식별자가 들어 있으므로 운영자가 `umask 077`로 보호한 파일에 보관하는 전제다. 운영 활성화 명령으로 배포한 것은 아니며, 이번 개발 과정에서 CP1에 실행하지 않았다.

결과는 `CHECKED_ACTIVATION_INPUT_REVIEW_REQUIRED`이고 모든 apply/인수/CP2/Join authority는 false다. 해시 인자를 같은 untrusted JSON에서 얻는 것은 승인·인증이 아니다. 실제 운영 호출자는 검토된 source와 요청, local collector provenance, 불변 activation digest를 독립적으로 확보하고 root staging 및 lock을 적용해야 한다.

## 검증

로컬 전체 단위·회귀 **414/414 PASS**, 실패·오류·skip 0. 직전 384개 대비 고유 시험 30개를 추가했으며 기존 수집기 시험도 확장했다. 전체 출력과 source 해시는 함께 게시한 TEST/VALIDATION 파일을 따른다. 새 시험은 다음을 포함한다.

- 바이트 고정된 원 producer를 완전한 모의 backend에 실행해 얻은 영수증을 새 입력 생성기에 연결한다. 테스트에서 임의의 성공 영수증으로 교체하지 않는다.
- 원본 바이트 변경, 중복 JSON 키, source/plan/status/operation 변조, 의존성 누락·추가, 독립 해시 불일치, scope·boot·시간 만료를 거부한다.
- 기존 조회의 public/policy/관리 상태와 전체 관측 간 불일치, incomplete/API/CRI/native 손실, 잘못된 zone 분류를 차단한다.
- 결과의 plan/XML/expected-after를 바꾸고 해시를 다시 계산해도 컴파일된 계획 대조에서 차단한다. 불변 activation hash와 lease는 별도로 검증한다.
- 원 producer → 새 activation → 초기 transition → raw native/API/CRI observer → 실제 membership transaction 로직 → 전체 방화벽 baseline 복원의 모의 통합시험을 수행한다.
- 실제 별도 Python 프로세스의 `-I -B` offline entrypoint를 실행한다. 해시가 틀리면 비정상 종료 및 STOP이 나오는지 확인한다.
- 전체/peer 수집의 절대 기한 공유, root 권한 없는 live 수집 거부, 실패 자료 보존, 원 수집 snapshot의 두 번째 표본 누락을 확인한다.

native/save fixture는 제공된 실제 자료에서 유래했다. 방화벽 명령·systemd·CRI·관리 세션은 mock이며 실제 target 성공을 뜻하지 않는다. 기존 controller는 새 CNI 변경 상태를 계속 거부한다. 원본 파일 4개와 기존 controller 계약은 변경하지 않았다.

## 남은 연결

원 영수증과 입력 생성·상태 관측의 연결은 구현됐다. 실제 운영 활성화 완료까지는 root entrypoint가 아래를 연결해야 한다.

1. 인증된 운영 결정과 source/root staging, 충돌 없는 journal/XML 저장, 공통 lock.
2. 첫 변경 전에 독립 회수 unit을 게시·arm하고, 준비 guard에서 fsync된 membership journal로 인계. 부분 설치 회수도 연결.
3. 실제 cgroup 수집, 새 SSH 관리 세션, recovery service 외부의 인증된 finalizer 및 소유 unit 삭제.
4. 인수 증적과 새 관측을 후속 controller 전후 gate에 연결하고 실제 대상에서 시간 예산·CRI·Pod 교체를 검증.

`run-cp1-cni-managed-node.py`는 아직 없으며 unit 게시 모듈은 해당 entrypoint가 없는 묶음을 거부한다. 읽기 전용 준비 도구를 이 운영 entrypoint 대신 배치하지 않는다. 중간 모듈마다 리뷰를 다시 요청하지 않고, 운영 연결을 검토할 수 있는 통합 결과를 준비한다.
