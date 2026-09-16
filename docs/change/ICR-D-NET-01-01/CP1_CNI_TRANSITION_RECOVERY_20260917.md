# CP1 방화벽 전이·독립 회수·unit 정리 경계 — 2026-09-17 KST

상태: **FIREWALL_TRANSITION_AND_RECOVERY_BOUNDARIES_IMPLEMENTED / OPERATIONAL_INTEGRATION_INCOMPLETE / RUNTIME_NOT_RUN**.

기존 코드는 개별 membership 철회와 unit 정의 게시까지 구현했지만, 초기 reload에서 원 Runtime 6개가 사라지는 구간 및 Permanent 객체 삭제·최종 unit 정리를 연결하지 못했다. 이번 변경은 그 전후 상태와 중단 복구를 구현한다. 실행 가능한 운영 entrypoint, source/원 영수증/lease를 결합하는 activation producer, 실제 후속 controller 연결은 아직 남아 있다. callback 기반 조립을 실제 운영 연결 완료로 표현하지 않는다.

## 방화벽 전이

- 변경 전 전체 Runtime/Permanent 차이는 원 Runtime 6개와 ens160의 NetworkManager binding 표현뿐이어야 한다. 다른 Runtime-only 변경은 reload 전에 차단한다.
- Permanent 빈 `hash:net,iface` 집합과 disabled public→public CONTINUE policy를 만들고, reload 후 원 Runtime 6개를 각각 복원한다. 새 관리 세션을 확인한 뒤에만 **빈 Runtime policy**를 활성화한다. membership 추가는 별도 검증된 Manager가 담당한다.
- 모든 외부 명령 직전에 정확한 before/after/argv/clock 의도를 fsync한다. 반환 성공뿐 아니라 native/daemon/전체 inventory를 대조한다. timeout 후 after가 관측돼도 활성화 재개 권한으로 바꾸지 않는다.
- 회수는 worker 종료를 확인한 뒤, 소유 Runtime policy 비활성 → 소유 Permanent policy/set 삭제 → 통제된 reload → 원 Runtime 6개 복원 순서다. 중단 의도의 전후 또는 재부팅으로 설명되는 상태만 복구한다.
- membership 인계 여부는 회수 실패 뒤에도 남는다. 이후 재시도에서 membership journal을 생략할 수 없다. Runtime 객체가 이미 제거된 복구 단계에서는 옛 membership 정보로 객체를 다시 만들지 않는다.
- 다른 설정 변경, 불완전 API/native 증거, journal 쓰기 실패에서는 별도 소유권 관측을 통한 제한된 비활성화를 시도한다. 외부 설정을 원본으로 덮어쓰는 reload나 전체 conntrack flush는 하지 않는다.

firewalld의 reload는 Runtime을 Permanent 기준으로 갱신한다. 따라서 기존 Runtime 규칙의 복원은 선택 단계가 아니다. [firewall-cmd 공식 설명](https://firewalld.org/documentation/man-pages/firewall-cmd.html).

## 관측·시간 경계

`cp1_cni_transition_observer.py`는 생성 전·부분 생성·disabled·활성·삭제 중·복원 후를 검증한다. 원 6개는 실제 native의 정확한 source/port/accept 식과 public INPUT allow chain 끝 위치를 확인한 뒤에만 고정 비교에서 분리한다. 각 단계의 실제 존재 여부는 전체 inventory에 남는다. source/port/prefix 확대·중복·다른 규칙의 위치 변경을 무시하지 않는다.

`LocalTransitionIO`는 고정된 원 node helper를 사용하고 전체 native/save/API 수집과 CRI peer 수집에 **같은 절대 마감 시각**을 전달한다. 전체 호출의 최대 150초 기한, 개별 command 최대 5초, 한 관측 묶음 최대 29초를 재설정하지 않는다. 실제 대상에서 이 시간 안에 끝나는지는 미검증이다. 실패한 관측은 이전 성공 결과를 재사용하지 않는다.

관리 검사는 인증된 controller가 새 nonce로 연 SSH 세션의 결과여야 한다. `svc_ansible`, source `10.250.10.10`, CP1 `10.1.93.102:22`, 현재 boot, 30초 미만 관측, ens160→gateway `10.1.93.58` 복귀 경로를 검증한다. **제출된 JSON의 해시나 boolean만으로 인증을 대체하지 않는다.** 현재 실제 controller transport 연결은 남아 있다.

## 독립 회수와 최종 정리

`Recovery`는 소유 unit의 로드된 정의를 확인하고, worker stop 요청 뒤 실제 unit 종료와 cgroup 비어 있음까지 확인한다. 그 후 global/run lock을 얻고 한 번 더 종료를 대조한 뒤 방화벽을 복원한다. `stop` 종료코드 0만으로 정리 성공을 판단하지 않는다. cgroup v2 `populated=0`은 자식 cgroup까지 포함한다. [Linux 커널 공식 설명](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html#un-populated-notification).

실행 중인 recovery service 자신이 모든 unit의 종료 완료를 증명할 수 없으므로, 방화벽 회수는 `FIREWALL_RESTORED_WAIT_EXTERNAL_FINALIZER`로 끝난다. 전체 회수 완료에는 외부 finalizer의 새 관리 세션, 원 방화벽 일치, 모든 소유 unit의 not-found/inactive/PID0/no-job, cgroup 없음/비어 있음, 파일·예약 링크 부재가 추가로 필요하다. 이 결과도 CP1 인수·CP2/3·Join 승인이 아니다.

`UnitCleanup`은 완료된 소유 unit 게시 journal 및 root 보관 source를 대조하고, **완전히 설치됐던 해당 실행의** 2개 예약 링크와 4개 unit 파일만 삭제한다. 각 unlink 전 intent fsync 및 새 종료/관리/방화벽 증명을 요구한다. 실제 파일시스템에서 중단 전후 재개, 링크·hardlink·권한·교체 race·fsync 실패·재등장·reload timeout을 검사한다. 원 source/journal과 타인 파일은 남긴다. 부분 설치의 회수는 별도 설치 의도 연결이 필요하다.

이 파일 정리 모듈 자체는 service를 정지하거나 recovery를 해제하지 않으며, injected callback은 신뢰된 root/controller 코드여야 한다. callback mock 시험을 실제 cgroup 수집·외부 최종 정리 transport의 구현 완료로 세지 않는다.

## 최초 변경 전 준비 단계 guard

membership이 아직 없는 초기 설치도 감시해야 한다. `supervise_preparation`은 이미 로드·활성화된 독립 recovery unit/timer를 확인하고, 정확한 10단계 계획과 command/intent prefix, 살아 있는 작업 프로세스를 대조한다. 30초 이상 진행이 없거나 command 결과가 불확실하거나 boot/worker/guard가 바뀌면 같은 소유 recovery unit을 요청한다.

준비 단계의 최대 150초는 불변 lease 시작에 묶인다. 마지막 진행 기록이 새로워도 이 기한을 연장하지 않는다. membership 인계 뒤에는 실제 membership journal의 일반 guard로 전환해야 하며, 준비 단계 guard를 계속 호출하면 회수를 요청한다. `ControlGroup`도 unit 조회에 포함한다. **이 판정기 구현은 unit enablement/entrypoint 연결의 완료가 아니다.**

## 검증과 다음 통합

**384/384 PASS, 실패·오류·skip 0.** 직전 게시 318개 대비 고유 시험 66개를 추가했다. 원본 출력·source 해시는 동봉한 TEST/VALIDATION 파일을 따른다. 시험 종류는 다음과 같다.

- 전이 10단계 및 회수 각 단계의 명령 전후 강제 중단·재부팅·불확실한 반환값·deadline·외부 drift.
- 실제 첨부 자료에서 유래한 native/save 형식으로 전체 전이 상태의 고정 비교, 원 6개 표현 반례, paired capture/API/peer 실패.
- 독립 stop→cgroup→lock→복원 순서, 자식 잔존, 종료 후 재시작 race, 관리 source/route/boot/nonce/신선도 불일치.
- root 임시 파일시스템의 unit 정리 중단 복구. systemctl, 외부 SSH, live cgroup과 CRI는 mock이며 운영 서버 변경은 없다.

원본 4개 파일은 바이트 동일하게 유지한다. 운영 통합의 남은 핵심은 원 영수증과 before/expected-after를 결합하는 producer, **준비 단계 guard를 첫 변경 전에 설치·활성화하고 membership guard로 인계하는 운영 entrypoint**, 실제 외부 finalizer/management transport, 후속 controller 전후 gate다. 기존 빈 집합 bootstrap만으로 초기 변경의 recovery 준비가 입증되는 것은 아니다. 이 연결을 생략한 운영 명령이나 새 실행 승인을 배포하지 않는다.

새 중간 리뷰·VM 재수집 요청은 만들지 않는다. CP1 인수·CP2/3 변경·Join 보류를 유지하며 통합 구현과 실패 시험을 한 묶음으로 검토할 계획이다.
