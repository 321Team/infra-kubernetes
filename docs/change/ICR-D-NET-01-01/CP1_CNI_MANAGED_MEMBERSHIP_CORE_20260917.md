# CP1 동적 membership 핵심 구현 및 검증 — 2026-09-17 KST

상태: **CORE_IMPLEMENTED / DEPLOYMENT_BLOCKED**. 설계 기준은 `5b5b30292d450ae1f727acc4b0f9ecd1bc68201e`다.
환경 연결이 복구되어 해당 설계의 핵심 코드와 시험을 작성했다. 이 변경은 실제 CP1 허용 활성화, 전체 복구 서비스,
기존 CP2/3 controller 연결 또는 Join 지원 완료를 의미하지 않는다. 원 생산자·controller 소스와 기존 영수증은 수정하지 않았다.

## 구현한 동작

| 파일 | 실행하는 부분 | 전제와 한계 |
|---|---|---|
| `scripts/collect-cp1-cni-membership.py` | 읽기 전용 Pod/IPAM/affinity, host route/veth, CRI sandbox, netns peer의 전후 수집 | 실제 대상의 `crictl inspectp.info.pid`와 `ip -j -d` 형식·성능은 미검증. 지원하지 않는 형식은 오류 기록 후 실패 |
| `scripts/cp1_cni_membership.py` | 대상 선정, lease/관측 유효기간, durable intent, 제거→추가, restart 대조, generation 생산·소비 계약 | 실제 소유권·전체 고정 설정·native/daemon 상태를 검증하는 신뢰된 adapter 입력 필요 |
| `scripts/cp1_cni_membership_io.py` | 고정 ipset에 대한 개별 add/remove와 소유 policy disable 명령, 관측/lease 잔여 시간 이내 timeout | runner와 실제 전체 상태 observer를 외부에서 주입. 실행 CLI·설치기·독립 guard가 아님 |
| `scripts/cp1_cni_journal.py` | 배타 잠금, symlink/hardlink/FIFO 거부, 0600 임시 파일, file fsync→rename→directory fsync | 특권 호출자는 고정 root 경로와 source를 별도로 검증해야 함. 악의적인 같은 UID writer·고장 난 저장장치를 방어한다고 주장하지 않음 |

수집기는 읽기만 한다. `nsenter`의 대상 명령은 검증된 `ip -j -d address show dev eth0`이며 workload exec를 수행하지 않는다.
조회 결과·오류·전후 원본을 보존하고, `complete=true`로 변환하기 전에 Pod UID, IP, host veth, CRI sandbox 및 peer의 일치를 검증한다.
PID start time과 netns inode의 전후 일치를 확인한다. 샌드박스 관측도 원자적인 커널 snapshot은 아니며,
활성 관리기에 연결할 때 관측 TTL과 독립 guard가 필요하다.

수집 중 resourceVersion 또는 Ready가 변했다는 이유만으로 다시 수집하지 않는다. 전후의 **실제 허용 대상과 소유권·peer**를 대조한다.
Ready=False 및 IP가 배정된 Pending Pod도 조건을 만족하면 대상이며, IP가 없는 Pending Pod는 제외한다.
삭제·terminal·hostNetwork·다른 노드 Pod도 제외한다. 선택된 요소는 CP1 소유 `10.244.246.64/26` 안의 실제 `/32,정확한-veth`다.
새 Pod 전체가 안정될 때까지 기다려 기존 허용을 초기화하는 방식을 쓰지 않는다.

## 거래와 종료 처리

활성화 adapter가 이미 검증한 **빈 소유 집합·enabled policy·armed guard**를 `initial_state`에 전달한다.
이 함수는 policy를 설치하거나 원 영수증의 승인·실제 패킷 의미를 검증하지 않는다. 입력 hash나 `owned=true` 자체는 인증 수단이 아니다.

1. caller는 journal lock을 보유한다. 완전하고 30초 미만인 관측, boot, source/정책/원본 binding, lease, 현재 고정 상태를 검증한다.
2. 오래된 요소를 먼저 개별 제거하고 native/daemon 결과가 모두 기대 상태인지 확인한다.
3. UID/sandbox/ifindex가 바뀐 동일 IP/veth도 제거 후 다시 추가한다. 단순한 문자열 재사용으로 이전 identity를 승계하지 않는다.
4. 각 변경의 before/after intent를 저장한 다음 명령을 실행한다. 추가 직전 관측 유효기간과 lease 잔여 시간이 명령 timeout에도 반영된다.
5. 전체 일치 후에만 이전 generation hash, observation, operation prefix hash를 연결한 새 세대를 저장한다.
6. restart 시 미완료 intent의 실제 상태가 before 또는 after 중 하나인지 확인한다. 중간·제3의 상태와 daemon/native 불일치는 성공으로 추정하지 않는다.

`reconcile_or_withdraw`는 처리 가능한 오류에서 소유권이 확인된 허용 정책을 먼저 비활성화하고 membership을 철회한다.
프로세스 사망은 이 예외 경로를 통과하지 않으므로 독립 로컬 guard가 반드시 필요하다.
core의 `guard_action`은 해당 guard의 판단 함수일 뿐 실제 systemd service가 아니다.

저장 실패가 있어도 증명된 소유 policy를 비활성화하는 경로를 제공하며, 이 경우 완료 영수증을 만들지 않는다.
foreign ownership은 건드리지 않는다. boot·고정 설정 drift에서는 소유 grant disable 후 별도 복구 검토 상태를 유지하고
다른 설정을 원본으로 덮어쓰지 않는다. timeout 반환만으로 disable 성공을 판정하지 않고 별도 실제 관측을 요구한다.

철회 결과는 **`MEMBERSHIP_WITHDRAWN_REVIEW_REQUIRED`**다. 다음을 의미하지 않는다:

- Runtime/Permanent 정의 제거와 원 Runtime 6개 규칙 복원.
- 관리 통신 확인, work/guard/recovery unit 종료·삭제 및 예약 해제.
- 기존 established conntrack의 즉시 종료.

`full_recovery_complete`, CP1 인수, CP2 변경, Join 승인 필드는 항상 false다. 철회 후 같은 lease로 자동 재활성화하지 않는다.
최대 256세대/4096개 operation/8MiB journal의 명시적 상한이 있다. 변경 없는 heartbeat는 세대나 관측 이력을 계속 늘리지 않는다.
상한 초과는 새 변경을 막으며 lease를 자동 연장하지 않는다. 실제 처리량·2시간 운영 가용성을 보증하는 값은 아니다.

## 후속 검증 계약

생산자가 만든 operation 전체를 빈 집합부터 재생하고 generation별 prefix·hash·관측·시각 순서를 검증한다.
`observation_from_capture`는 수집기의 전후 raw 결과를 다시 선정하여 manager 입력으로 연결한다.
소비 함수는 별도로 고정한 lease hash, 최신 committed generation, fresh Pod membership, native/daemon, 고정 상태, guard를 대조한다.
target 직전 잔여 660초, 직후 180초 조건을 적용하며 target은 아직 미가입 상태여야 한다.
이는 CP2/3 firewall 준비 전후의 **데이터 계약 검증**이며 실제 Join 전후를 실행하는 코드가 아니다.

실제 원 영수증 검증, activation receipt, 독립 CP1 인수, 인증된 원격 CP1 조회 및 기존 `run-node-firewall.py` 연결은 남아 있다.
새 파일을 기존 승인 입력으로 넣거나 CNI 필드를 제외하여 기존 비교를 통과시키지 않는다. 기존 controller는 계속 기존 계약을 적용한다.

## 자체 검증 결과와 분류

`python3 -B -m unittest discover -s tests -v`: **170/170 PASS**.

- 기존 124개를 현재 트리에서 다시 실행했다.
- 새 STATIC/MOCK/CLI 37개: selector, collector→manager→consumer, UID/IP 재사용, 삭제 후 추가 순서,
  remove/add 각각의 변경 직전·직후 중단과 재개, 제3의 실제 상태, daemon/native 부분 실패, 저장 실패,
  warning/truncation/nonzero result, stale/clock rollback/suspend/boot/lease/guard, future generation,
  lease 외부 binding, 관측 기한을 넘긴 느린 observer, 변경 없는 heartbeat와 기록 상한.
- 새 실제 파일시스템·프로세스 9개: private 파일/경로, nofollow/hardlink/FIFO,
  프로세스 간 잠금, SIGKILL 후 fsync intent 보존과 잠금 해제, rename 이전 실패,
  directory fsync 실패의 불확실성, duplicate/nonfinite/oversized JSON 거부.

SIGKILL 시험은 실제 OS 프로세스와 임시 파일에 한정하며 kernel firewall 변경 도중의 현장 복구 시험으로 계산하지 않는다.
추가된 테스트의 세부 subcase를 별도 테스트 수로 부풀리지 않는다. 기록은 `CP1_CNI_MANAGED_MEMBERSHIP_TEST_20260917.txt`와
`CP1_CNI_MANAGED_MEMBERSHIP_VALIDATION_20260917.json`에 남긴다. GitHub CI는 별도 조회하며 이 로컬 기록으로 대체하지 않는다.

## 배포 전 남은 구현

| 순서 | 남은 통합 작업 | 완료 증거 |
|---|---|---|
| 1 | activation, 원 영수증 불변 binding, policy/set 정의 및 전체 native/Runtime/Permanent/보호 상태 observer | 정확한 허용 delta와 foreign drift를 구분하는 실제 producer/observer 기록 |
| 2 | root 고정 경로·source staging, 독립 systemd guard, reload/boot/만료 시 전체 회수 | 원 규칙·관리 경로·unit 삭제·예약 해제까지 완료한 증거 |
| 3 | 실제 인수와 인증된 최신 CP1 조회를 기존 후속 consumer에 연결 | target 직전·직후의 whole-state 검증 및 기존 회귀 통과 |
| 4 | 통합 실패 시험·일관된 패키지 작성 후 한 번의 리뷰 묶음 | 배포 대상 source와 같은 검증/리뷰, 이후 승인된 실행창 |

실제 CP1 인수·CP2/3 적용/Join·AC 완료는 계속 보류다. 방향 재승인이나 중간 VM 수집을 요청하지 않는다.
