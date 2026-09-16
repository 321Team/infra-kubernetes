# CP1 관측·독립 guard 구성요소 — 2026-09-17 KST

상태: **OBSERVER_GUARD_COMPONENTS_IMPLEMENTED / DEPLOYMENT_BLOCKED**.
기준 commit은 `908b65dab975d6b5ad40dfa9fd347b7cea9607da`다. 기존 170개 검증 기록은 당시 기록으로 보존한다.
사용자가 선택한 Pod 교체 관리 방향을 계속 구현하며, 중간 방향 선택이나 재승인을 요청하지 않는다.

## 구현한 범위

`scripts/cp1_cni_observer.py`는 로컬 읽기 명령과 대조를 연결한다. firewalld Runtime/Permanent의 zone·policy·ipset·direct inventory,
전체 nft JSON, 관리 인터페이스의 zone/NM UUID, Calico 및 namespace 보호 설정을 전후로 수집한다.
실행 환경·boot·query 종료코드·경고·출력 잘림·관측 시간과 전후 일치를 확인한다.
`owned=true`, `guard_armed=true` 같은 입력값을 소유권 증거로 받지 않는다.

- 소유 set은 `hash:net,iface` / IPv4이며 정확한 `/32,caliveth` 요소만 허용한다. daemon/native 요소가 일치해야 한다.
- Permanent set은 비어 있고 Permanent policy는 disabled여야 한다. description은 해당 run에 묶는다.
- Runtime 정책은 public→public, CONTINUE, priority -1과 검증된 단일 rich rule이어야 한다.
- 실제 compiled ACCEPT에서 source IP+iif와 destination IP+oif를 모두 확인한다. 다른 hook·범위 확대·추가 verdict·알 수 없는 참조를 거부한다.
- 소유 동적 요소 및 검증된 정책 활성 상태만 고정 비교에서 분리한다. 타인 규칙·집합·순서·handle은 유지하고 알려진 packet/byte counter 값만 정규화한다.
- 전후 query와 검증 실패 이유를 보존한다. unit 조회 실패 시에도 앞서 수집한 capture를 잃거나 이전 성공 결과를 재사용하지 않는다.

`scripts/cp1_cni_guard.py`는 별도 work/guard/recover service와 timer **문서 생성**, 실제 loaded unit 소유권 대조,
관측/lease 만료 시 해당 run의 recovery unit으로 보내는 **dispatch 함수**를 제공한다.

- unit 파일뿐 아니라 loaded ExecStart·FragmentPath·drop-in·OnFailure·Type·Restart·UMask·KillMode와 시간 제한을 확인한다.
- timer는 시작 1초, 재검사 5초, accuracy 1초, 무작위 지연 0으로 확인한다. 숫자 시간은 typed D-Bus microseconds로 읽는다.
- guard가 실행 중일 때 다음 timer 시각이 0일 수 있다. 이 경우 실제 MainPID와 loaded 15초 제한을 확인한다.
- work의 실행 상한, guard 15초 / recovery 150초 시작 제한과 5초 종료 제한을 대조한다. 절대 lease를 갱신하지 않는다.
- 별도 stop request 저장 뒤 정확히 소유한 recovery unit만 시작한다. 디스크 저장 실패 시에도 그 unit을 호출하되 성공·회수 완료로 보고하지 않는다.

이 파일들이 생성하는 unit의 `run-cp1-cni-managed-node.py`는 **아직 구현되지 않은 통합 entrypoint**다.
unit 문서를 그대로 설치하는 VM 배포 묶음이 아니다. `LocalObserver`의 activation과 unit 파일 읽기에도 향후 root 소유 staging·안전한 reader가 필요하다.
원 `node-firewall.py`, `run-node-firewall.py`, 원 handoff 검사와 canary helper는 변경하지 않았다.

## 자체 점검 결과

- 전체 **197/197 PASS**: 이전 170개 재실행 + 새 observer 14개 + guard 13개.
- 전체 중 188개는 STATIC/MOCK/CLI, 기존 journal 9개는 실제 파일시스템/프로세스 시험이다.
- 별도로 firewalld v2.4.3의 원본 CLI formatter 함수를 해시 확인 후 그대로 실행하여 **5개 출력 사례**를 검사했다.
  settings 객체는 모형이며 daemon·D-Bus client·kernel 시험이 아니다.
- 공식 CLI 소스에서 Runtime `--get-description`이 지원되지 않음을 확인하고 Permanent에만 질의하도록 수정했다.
- timer의 다음 시각만 가까워도 loaded 반복 주기가 길면 거부한다. stale loaded 종료 시간·Restart·Type도 거부한다.
- unit 수집 실패의 evidence 보존, foreign native/protection 변경, query 경고/잘림, daemon/native 불일치가 차단되는지 확인했다.

실제 systemd service 기동·firewalld daemon·kernel·대상 VM·실제 Pod 교체·CP1 통신은 이번에 수행하지 않았다.
API/전체 상태 수집이 목표 환경에서 제한 시간 안에 끝나는지도 아직 검증하지 않았다. 로컬 PASS는 운영 가능 또는 인수 PASS가 아니다.

## 발견한 통합 차단 조건

**Calico 동적 dataplane과 고정 전체 native 비교 사이의 경계가 미완성이다.**
회귀시험에서 membership 이외의 Calico endpoint chain 추가를 모형 입력으로 넣으면 `whole fixed-state drift`로 차단된다.
Pod 교체가 실제 목표 환경에서 만드는 native 변화는 아직 검증하지 않았으므로, 실제 CP1에서 같은 실패가 관측됐다고 단정하지 않는다.

모든 Calico/CNI 규칙을 비교에서 제외하면 보호 규칙 변경도 놓칠 수 있다. 따라서 현재는 미확인 차이를 차단한다.
이 모델만으로 “정상 Pod 교체가 수렴한다”고 주장하거나 기존 controller의 원 영수증 일치 조건을 완화할 수 없다.
native handle 재생성도 현재는 차이로 남는다. 전체 observer가 성공하려면 활성화된 고정 projection을 만드는 producer 역시 필요하다.

## 남은 통합 작업과 완료 조건

1. 목표 backend/source에 묶인 Calico endpoint 변경 판정: Pod/IPAM/veth 세대 변경과 대응되는 native 변화만 허용하고,
   HEP/GNP·namespace 정책·비소유 규칙 변경은 계속 차단해야 한다. 단순 CNI 필드 제외로 처리하지 않는다.
2. 원 영수증 hash → source/plan → activation before/after를 생산하는 root staging·policy/set 설치 entrypoint.
   최초 관측 전 guard 동작, manager 종료, reboot, reload 중단도 transaction으로 검증해야 한다.
3. 별도 recovery 실행기: work 종료 → 소유 허용 철회 → Runtime/Permanent 객체 제거 → 원 Runtime 6개 복원 →
   전체 기대 상태·관리 경로 확인 → unit 종료/삭제 및 예약 해제. dispatch 접수나 membership 철회만으로 전체 복구 완료를 만들지 않는다.
4. 실제 CP1 인수와 인증된 최신 generation/보호 상태를 기존 후속 controller의 실행 전후 검증에 연결.
5. 위 변경의 통합 실패 시험·배포 묶음을 완성해 한 번의 리뷰 대상으로 만든다. 이번 구성요소만으로 중간 VM 재시험·추가 리뷰를 요청하지 않는다.

CP1 최종 인수·CP2/3 적용·Join·AC 완료는 계속 보류한다. 6/6 Ready만으로 HEP/GNP 보호 인계나 회수 해제를 수행하지 않는다.

## 재현과 근거

```bash
python3 -B -m unittest discover -s tests -v
python3 -B tests/probe_cp1_cni_observer_upstream.py --command-source /path/to/firewalld-v2.4.3/src/firewall/command.py
sha256sum --check CP1_CNI_HANDOFF_SHA256SUMS
```

시험 로그: `CP1_CNI_OBSERVER_GUARD_TEST_20260917.txt`.
formatter probe 결과: `CP1_CNI_OBSERVER_UPSTREAM_20260917.json`.
검증 수치·해시·미완료 플래그: `CP1_CNI_OBSERVER_GUARD_VALIDATION_20260917.json`.

- [firewalld CLI v2.4.3](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall-cmd.in): Git blob `9c08de548e163ddbb00c9776521062f247e6ca7d`.
- [CLI formatter v2.4.3](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/command.py): Git blob `8d84f4222fc2e27cb2184fe56c0452fd450a9e2a`.
- [nft backend v2.4.3](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/nftables.py): Git blob `685f3e25eb50ff4012a3abfd708a640ce44afbf8`.
- [systemd D-Bus v257](https://github.com/systemd/systemd/blob/v257/man/org.freedesktop.systemd1.xml): Git blob `9cd6a69311a97ee53f17a64bc03c9aff4aa375fa`.
- [busctl v257](https://github.com/systemd/systemd/blob/v257/man/busctl.xml): Git blob `3733d523e5ffc2bf38792303940f2c4642b20c25`.

위 systemd 자료는 인터페이스의 정적 근거이며, 목표 VM systemd 버전·실제 반환값을 검증했다는 뜻이 아니다.
