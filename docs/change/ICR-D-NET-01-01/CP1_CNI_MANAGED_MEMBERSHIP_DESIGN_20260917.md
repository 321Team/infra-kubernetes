# CP1 Pod 교체·Join 지원 운영안 — 2026-09-17 KST

상태: **DESIGN_ONLY_NOT_EXECUTABLE / 구현 방향 선택 완료**.
사용자가 고정 Pod 관측 후보에서 Pod 재생성·Join 지원 운영안으로 전환하라는 제안에 “착수”라고 지시했다.
이 방향은 다시 선택 요청하지 않는다. 이번 기록은 실제 CP1 변경·CP2/3 적용·Join·HEP/GNP 실행 승인이 아니다.

## 이번에 확인한 사실

1. firewalld v2.4.3의 `FirewallIPSet.set_entries`는 daemon의 `obj.entries`를 바꾸고 backend `set_flush` 뒤 `set_restore` 또는 개별 추가를 호출한다. 원자적인 전체 교체로 사용할 수 없다.
2. nftables backend `set_restore`도 create/flush와 element 추가를 분리한다. 추가는 GLib idle callback에 예약되며 예외를 로그에 남긴다. 따라서 D-Bus 반환 성공이나 `get_entries`만으로 kernel 설치 완료를 증명하지 못한다.
3. `add_entry`/`remove_entry`는 backend 작업과 daemon 목록 갱신을 연결하지만, 다중 backend·부분 실패를 전체 일치로 간주할 수 없다. 실제 nft set과 대조해야 한다.
4. 기존 CP2/3 controller의 `cp1_state_gate`는 원 영수증 after 전체 일치를 요구한다. 동적 집합의 변경을 허용하려면 새 계약을 검증하는 consumer가 필요하다. 기존 비교를 삭제하거나 원 영수증을 편집하지 않는다.

공식 근거:
- [fw_ipset.py v2.4.3](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/fw_ipset.py), Git blob `cca83638482c27d810627a985b229aa921f1dccf`
- [nftables.py v2.4.3](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/nftables.py), Git blob `685f3e25eb50ff4012a3abfd708a640ce44afbf8`
- [D-Bus server v2.4.3](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/server/firewalld.py), Git blob `26769fa7455953fdefeaeea5435aea2a156dd582`
- [현재 consumer](https://github.com/321Team/infra-kubernetes/blob/a8c3bb52ee23f23110f2150250e8afe73cd65cbc/scripts/run-node-firewall.py)

위 내용은 고정 upstream 소스와 consumer의 정적 확인이다. 실제 kernel 또는 daemon 시험을 수행한 결과가 아니다.

## 선택한 설계 방향

기존 public zone과 binding을 유지하고, public→public CONTINUE policy의 source 주소+iif와 destination 주소+oif를 모두 제한한다.
고정 후보의 각 veth별 /26 요소를 그대로 갱신하지 않고 **검증된 실제 Pod IP /32 + 정확한 host veth** 요소로 좁힌다.
허용 가능한 주소의 상한은 CP1 소유 `10.244.246.64/26`이다. 새 Pod도 해당 소유권·경로·인터페이스 검증을 거친 후 들어간다.
L4 ALL은 설계 후보이며, TCP8081/UDP/ICMP를 이전 합성 시험의 DENY 기준으로 재사용하지 않는다.

이 설계는 같은 노드 CNI 허용을 관리한다. 다른 노드의 Pod subnet, VXLAN, HOST INPUT/OUTPUT, ens160 전체 ACCEPT를 추가하지 않는다.
Join으로 새 노드의 Pod block이 생겼다는 이유로 CP1 집합의 주소 범위를 확대하지 않는다. 노드 간 CNI·MTU·포트 및 기능 검증은 별도로 남는다.

### Pod 재생성 수렴

- Kubernetes/로컬 네트워크 이벤트는 재조회 계기이며 이벤트 하나를 권한 근거로 사용하지 않는다.
- 전체 현재 Pod·IPAM/affinity·로컬 /32 route·veth/peer 식별을 대조한다. 정상적인 Pod UID 교체 자체를 영구 실패로 분류하지 않는다.
- 새 Pod가 네트워크를 필요로 하는 readiness probe를 통과해야만 집합에 넣으면 순환 대기가 생길 수 있다. 따라서 **Ready는 membership 선행조건이 아니며**, 실제 인수에서는 별도로 확인한다.
- IP가 아직 없는 Pending Pod는 집합 대상이 아니다. 기존 검증된 Pod의 허용을 이 이유만으로 초기화하지 않는다.
- 삭제·terminal·소유권 상실·식별 불일치 요소를 먼저 제거하고 kernel/daemon 양쪽을 확인한 다음 새 요소를 추가한다. 이름이 같아도 UID/ifindex/peer가 바뀌면 이전 identity를 그대로 승계하지 않는다.
- 새 Pod에 대한 수렴 지연은 존재한다. 이벤트 기반 관리로 무중단·즉시 허용을 보장한다고 표현하지 않는다.
- 오래된 전체 관측, API 실패, watch gap 등에서 새 요소를 추정해 추가하지 않는다. 짧은 제한 시간 안에 완전한 관측을 회복하지 못하면 독립 로컬 복구가 소유 허용을 철회한다.

인터페이스 이름과 /26만으로 실제 Pod 네트워크임을 증명하지 않는다. peer/netns 대조의 구체적인 구현·권한·성능 검증은 남아 있다.
호스트 root의 임의 netdevice/규칙 변경까지 이 관리기가 방어한다고 주장하지 않는다.

### 허용 변경 방식

`setEntries` bulk 교체는 사용하지 않는다. 다음 순서를 durable journal에 남긴다.

1. 전체 관측의 UID/IP/veth/peer 목록과 해시·boot·source·policy·현재 세대를 기록한다.
2. 제거 intent fsync → 개별 remove → daemon/native 확인 → 완료 기록.
3. 추가 대상 관측을 다시 대조 → 추가 intent fsync → 개별 add → daemon/native 확인 → 완료 기록.
4. 전체 예상 요소와 일치할 때만 generation을 확정하고 이전 generation 해시와 연결한다.
5. timeout/partial/불일치 상태에서는 새 세대 성공 receipt를 만들지 않는다. restart는 journal과 실제 상태의 before/after를 대조해 회수하거나 재조정한다.

소유 집합의 모든 요소가 없어져도 대상 없는 상태로 유지할 수 있다. Pod가 반드시 두 개 있어야 관리기가 살아 있는 구조로 만들지 않는다.
집합 수정은 해당 집합만 대상으로 하며 firewall 전체 reload를 매 polling마다 수행하지 않는다.

### 영구 설정과 재부팅

Permanent는 **빈 집합 + disabled 소유 policy**만 보관하는 방향이다. 활성 membership과 enabled policy는 Runtime에 한정한다.
firewalld reload/재부팅 뒤 허용이 자동 재활성화되지 않도록 한다.
초기 설치에 필요한 통제된 reload는 원 Runtime 6개 복원·관리 통신·전체 native 비교와 같은 transaction에 묶는다.
재부팅은 활성 운영 재개가 아니라 회수 진입이다. 다른 변경을 원본으로 덮어쓰지 않고 원래 소유 규칙과 상태가 확인되는 범위에서 회수한다.

## 수명·회수·보호 인계 제안

| 항목 | 구현할 제한 | 아직 검증하지 못한 부분 |
|---|---|---|
| 운영 lease | 한 번의 절대 종료 시각, 최대 7200초. wall + BOOTTIME 중 먼저 만료. 자동 연장 없음 | 실제 일과/Join 소요 시간 안에 완료 가능 여부 |
| membership 관측 | 이벤트 외 최대 15초 주기 조정, 30초 이상 관측 불능 시 철회하는 초안 | 실제 API/호스트 부하·장애에서 시간 상한 충족 여부 |
| CP2/3 진입 | target 준비 480초 + 회수 180초 = 660초 잔여 확보. 완료 후에도 180초 확보 | actual target 실행 시간 |
| lease 갱신 | 동일 코드라도 종료 전 명시적인 유한 갱신 결정과 journal 필요. 무기한 daemon으로 변환하지 않음 | 운영 담당자의 실제 창 지정 |
| 종료 | 소유 허용 철회, Runtime/Permanent 객체 제거, 원 Runtime 6개와 전체 기대 상태 복원, 관리 경로 확인, unit 종료/삭제·예약 해제 | 실제 복구 시험 |
| HEP/GNP 인계 | 6/6 Ready와 별도 승인된 보호 전환 증적을 대조. Ready만으로 회수 해제하지 않음 | 전환 실행은 별도 작업 범위 |

위 수치는 구현 초안이다. 실행 시각/종료 시각이나 Join 완료를 이미 확정했다는 뜻이 아니다.
잔여 시간이 부족하면 새 Join 단계를 시작하지 않는다. 2시간 lease가 전체 Join 기간을 항상 덮는다고 보장하지 않는다.
정상 lease 종료는 원래 상태로 돌아가므로, CP1 CNI 우회 허용에 의존하던 연결의 가용성이 사라질 수 있다. 이를 예방하려면 종료 전에 승인된 갱신 또는 보호 인계가 완료되어야 한다.

회수 오류나 다른 설정 drift가 있으면 `REVIEW_REQUIRED`를 유지한다. 타인 변경을 덮어쓰는 reload나 host-wide conntrack flush를 하지 않는다.
허용 규칙을 제거해도 기존 established conntrack 흐름이 즉시 끊어진다고 보장하지 않는다. 새 연결과 기존 연결을 구분해 negative/회수 증거를 검토한다.

## 생산자·인수·후속 consumer 연결

원 영수증은 불변 원본으로 보존한다. 새 receipt 체인은 다음을 연결해야 한다.

- 원 영수증 byte hash → 승인된 source/정책/plan → activation before/after.
- 각 membership generation의 이전 해시, 현재 관측, intent/result, 실제 native/daemon 상태.
- 실제 CP1 positive·이번 범위의 negative·HOST/외부·Calico deny 회귀와 복구/종료 증거.
- 동일 policy/source에 대한 독립 인수 결정.
- 인증된 CP1 조회로 받은 최신 boot/lease/guard 상태·현재 generation·전체 고정 설정 및 현재 집합.

Pod 교체로 generation이 바뀌어도 고정 설정·policy·보호 상태를 무시하지 않는다.
후속 target 직전과 직후에 전체 원본 + 승인된 변경 + 최신 합법 세대를 재구성해 검증한다.
membership 갱신 중이면 안정된 세대로 다시 확인한다. 변경된 원본이나 누락된 세대를 적당히 CNI 필드 제외로 통과시키지 않는다.
CP2/3 firewall receipt가 Join 승인·etcd 건강·CA/Auth·6/6 Ready를 대신하지 않는다.

현재 `run-node-firewall.py`는 그대로이며 새 운영 상태를 계속 거부한다. 새 schema/producer/live observation/consumer를 함께 구현·시험한 후 연결한다.
이 문서나 draft JSON을 기존 승인 입력으로 넣지 않는다.

## 자체 점검과 한 번의 리뷰 묶음

필수 실패 사례: 새 Pod Ready 순환 대기 방지, Pod 0개, UID/ifindex 재사용, API 410/watch gap, API timeout, 오래된 관측,
삭제 뒤 추가 실패, daemon/native 불일치, journal 각 경계의 프로세스 종료, clock rollback·suspend·boot 변경,
firewalld reload, 독립 guard 장애, foreign drift, 원 Runtime 6개 복원, 만료 직전 target 차단, generation 전후 소비자 대조,
HEP 전환 미완료 시 disarm 거부, 기존 established 흐름과 새 연결의 분리 판정.

정책·manager·독립 회수·producer·consumer·실패 시험을 한 묶음으로 완성한 뒤 검토한다.
기존 R6 전체 도구 qualification을 이 대안의 공통 gate로 요구하지 않으며 이번 변경 자체의 실제 검증도 생략하지 않는다.

## 이번 실행 경계

최신 조회에서 PR #13은 `a8c3bb5` Draft, 댓글/리뷰 등록은 0건이었다.
공식 소스 확인 도중 로컬 실행 환경이 `409 Conflict, environment_offline`으로 끊겼고 읽기 전용 `pwd` 재확인도 실패했다.
따라서 **이번 변경은 설계 문서와 비실행 JSON만**이다. 실행 코드 수정·새 STATIC/MOCK·CI·Runtime 검증은 수행하지 않았다.
이전 124/124는 `a8c3bb5`의 코드 검증 기록으로 보존하며 이번 운영안이 검증되었다고 재사용하지 않는다.
환경 연결 복구 뒤 위 설계를 실제 코드와 시험으로 이어간다. 방향 결정이나 이미 승인된 준비 작업의 재승인은 요구하지 않는다.
