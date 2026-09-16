# CP1 public 유지 후보와 수집 일원화 — 2026-09-17 KST

상태: **DRAFT_NOT_EXECUTABLE / 운영 메커니즘 비교안.**

## 결과와 차단점

전용 cali zone으로 옮기는 초기안은 기존 public INPUT과 외부 forwarding을 함께 재현해야 한다.
빈 zone을 만들어 같은 zone policy만 추가하는 방식으로 HOST·외부 통신 보존을 주장할 수 없다.
이를 해소할 대안으로 **기존 public zone·binding을 유지하고, public→public policy의 양쪽 주소+인터페이스 집합을 교집합으로 검사**하는 후보를 구현했다.

이 후보는 기존 `cali+` 자동 범위를 그대로 구현한 것이 아니다. `hash:net,iface`에는 실제 확인한 인터페이스 이름을 넣는다.
새 Pod·veth가 생겨도 자동 허용되지 않는다. 따라서 **고정 목록의 제한된 관측창**과 **재생성되는 Pod까지 지원하는 지속 정책**은 같은 운영안이 아니다.
후자를 선택하려면 신뢰할 수 있는 membership 갱신·재사용 감지·독립 회수까지 구현해야 한다.
고정 목록을 Join 기간의 지속 정책으로 승격하지 않는다.

이번에 완료한 부분은 읽기 전용 현장 수집기, 비실행 XML 후보 생성기, upstream 함수 검증과 회귀시험이다.
후속 자체 점검에서 zone/ipset/native의 단일 관측 누락을 보완했다. 현재 전후 관측 검사와 한계는 아래 및
`CP1_CNI_POLICY_CONSISTENCY_20260917.md`를 따른다. 최초 109개 검증 기록은 당시 커밋의 기록으로 보존한다.
**실제 적용/회수기, kernel 검증, 새로운 변경 영수증 생산자, 후속 controller 연결은 미구현**이다.
이 후보의 승인이나 현장 성공 결과는 없다. 기존 #11 승인·기존 영수증을 이 후보의 승인으로 사용하지 않는다.

## 공식 소스로 확인한 내용

대상은 firewalld **v2.4.3**, 세 파일의 실제 Git blob과 SHA256을 대조했다.

| 코드 | 확인된 동작 | 설계에 미치는 영향 |
|---|---|---|
| `fw_zone.py:_interface` | default-zone fallback `+`에는 intra-zone forward 규칙을 생성하지 않는다. 명시적 `ens160`에는 생성한다 | public forward=yes를 모든 미지정 인터페이스에 대한 허용으로 해석하지 않는다 |
| `fw_policy.py:_ingress_egress_zone` | default zone의 policy dispatch에는 `+` fallback을 추가한다 | cali→public 무제한 ACCEPT policy를 기존 ens160 외부 경로와 같다고 보지 않는다 |
| `nftables.py:_set_match_fragment` | source 집합은 `saddr . iifname`, destination 집합은 `daddr . oifname` | 주소 범위뿐 아니라 양쪽 실제 인터페이스도 조건에 들어간다 |
| `nftables.py:_set_entry_fragment` | iface 요소 `cali+`를 문자열 그대로 넘긴다 | zone 문법의 `+ → *` 변환을 집합에도 적용된다고 가정하지 않는다 |
| `fw_policy.py:_get_table_chains_for_policy_dispatch` | public→public의 filter 경로는 FORWARD이다 | HOST INPUT/OUTPUT policy를 추가할 필요가 없다 |
| `nftables.py:build_policy_chain_rules` | target CONTINUE에는 최종 ACCEPT/DROP/REJECT를 추가하지 않는다 | 집합에 매치하지 않는 패킷은 기존 처리로 이어진다 |

참조: [zone](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/fw_zone.py),
[policy](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/fw_policy.py),
[nftables backend](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/nftables.py).

upstream 함수가 만든 허용 조건은 다음과 같다. 이 표는 새 규칙의 매치 범위이며 전체 네트워크의 최종 판정표가 아니다.

| 조건 | 새 규칙의 동작 |
|---|---|
| IPv4, source/destination 모두 CP1 소유 `10.244.246.64/26`, iif/oif 모두 고정한 cali 목록 | L4 전체 허용 조건에 일치 |
| TCP 8081, UDP, ICMP도 위 경계 안 | 허용 조건에 일치. 기존 R5의 8081 DENY를 재사용하지 않음 |
| 주소를 /26으로 위장한 ens160 또는 vxlan.calico 유입/유출 | 새 허용에 일치하지 않음 |
| source/destination 중 하나가 /26 밖 | 새 허용에 일치하지 않음 |
| 외부 DNS/API/Harbor 방향 | 새 허용에 일치하지 않음. 기존 경로의 성공 여부는 현장 regression으로 확인 |
| HOST INPUT/OUTPUT, IPv6 | 새 filter 규칙의 대상 아님 |
| 목록에 없는 새 cali veth | 자동 포함되지 않음 |

**전체 firewalld transaction·실제 nft kernel·Calico NetworkPolicy 보존을 검증한 결과는 아니다.**
public 정의를 바꾸지 않아도 activation reload가 기존 Runtime 6개를 지울 수 있으므로 원본 복원·전후 native 비교가 필요하다.
기존 policy 우선순위/거부, zone-source 분류, Calico 집행과 실제 패킷의 전체 경로도 아직 확인해야 한다.

## 구현

`scripts/prepare-cp1-cni-policy.py`는 두 모드만 제공한다.

- `collect`: 고정된 기존 CP1 helper의 root/호스트/ens160 식별 검사를 사용한다. 방화벽/protection, 전체 zone/ipset 목록과 정의, Pod, IPAM/affinity, veth/route, workload network policy, native 규칙, boot/version/firewalld 설정을 전후 한 쌍으로 수집한다. 개별 조회가 실패해도 가능한 후속 조회와 오류를 보존한다. 전체 command budget은 240초다.
- `plan`: 외부 SHA256이 일치하는 수집물과 원 영수증을 읽는다. 원본 생산자의 실제 코드로 원 영수증을 검증하고, 이전 after와 현재 firewall의 전체 일치를 요구한다. 소유 IPAM block, confirmed affinity, Ready Pod, /32 host route, 실제 veth·ifindex·peer index, 목록의 전후 동일성을 검사한다. 기존 이름 충돌·누락·잘린 출력·경고 결과는 차단한다.

생성 결과는 `DRAFT_NOT_EXECUTABLE`이고 XML은 JSON 안의 검토용 문자열이다. `/etc/firewalld`에 쓰거나 daemon을 reload하지 않는다.
`apply`, `recover`, `join` 모드는 없다. 결과에는 `authorizes_apply=false`, `authorizes_cp2_change=false`, `authorizes_join=false`를 명시한다.
루트에서 사용하는 수집 자료는 비공개 현장 증적이다. 원본 수집물을 GitHub에 게시하지 않는다.

과거 수집물로도 후보 비교를 할 수 있도록 계획 생성에 15분 재수집 조건을 붙이지 않았다. 대신 관측 종료 시각·경과 시간을 출력하며 `fresh_for_activation=false`를 유지한다.
실제 적용 직전 fresh 확인은 후속 실행기의 책임이다. 초안 리뷰를 기다리는 동안 유효기간이 지나 다시 VM 왕복을 하는 절차를 만들지 않는다.
현재 사용자에게 이 수집기의 실행이나 새 VM 반입을 요청하지 않는다.

전후 zone/ipset 정의·native 구조가 다르면 계획 생성을 중단한다. native 비교에서는 명시적 packet/byte counter 통계만 제외하며,
원문 양쪽 해시는 보존한다. handle·순서·verdict·set entry·timeout·quota는 제외하지 않는다.
두 표본이 같아도 중간에 바뀌었다가 돌아온 상태(ABA)를 배제하거나 원자적 snapshot을 입증하지 못한다.
다른 zone의 명시적/prefix 인터페이스 배정이 대상 veth를 포함하면 중단한다. source 기반 zone은 전부 별도 분류 분석 대상으로 중단하며,
비충돌이라고 추측해 통과시키지 않는다. 이것은 실제 native 우선순위·전체 경로 검증을 대체하지 않는다.

이전 단일 표본 수집물을 새 검사의 성공 입력으로 변환하거나 누락된 두 번째 표본을 복제하지 않는다.
이전 자료는 이전 버전의 후보 비교 자료로 남기고, 새 방식의 수집은 운영 메커니즘 확정 후 실제 필요한 창에 한 번 수행한다.

## 적용·회수 연결 시의 확정할 작업

아래는 구현 완료를 주장하는 표가 아니라 한 번의 구현/검토 묶음에 포함할 계약이다.

| 단계 | 필요한 동작과 중단 처리 |
|---|---|
| 결정 | 고정 목록의 최대 2시간 관측인지, Pod 재생성까지 지원하는 지속 정책인지 확정. Join까지의 유지 종료/HEP 인계는 별도 명시 |
| 준비 | live before·원 영수증·boot·Pod/veth·IPAM·network policy 재대조. 본인 소유 파일/객체의 부재, 독립 로컬 복구의 실행 가능성 확인 |
| 활성화 | intent를 fsync한 뒤 소유 ipset/policy만 추가. configuration validation, 통제된 reload, 원 Runtime 6개 복원. 전체 예상 after와 native diff가 맞기 전 성공 영수증을 발급하지 않음 |
| 이상 | 새 허용을 우선 철회. timeout/partial success를 완료로 간주하지 않음. 남의 drift를 덮어쓰는 reload 금지, 소유 객체만 제거한 뒤 미완료 상태를 보존 |
| 수명 | 만료·boot·Pod UID·ifindex/peer index·IPAM 소유·firewall/protection drift 시 재사용 중단. 이름만 같은 새 veth를 이전 소유로 처리하지 않음 |
| 회수 | 소유 정책 허용 비활성 → 소유 정의 제거 → 통제된 reload/원 Runtime 복원 → 원본 전체 상태와 관리 통신 확인. 별도 로컬 복구 해제 및 unit 종료까지 기록 |
| 인수 | 실제 CP1 5개 기능, 이번 ALL-L4 범위의 negative/regression, Calico deny 유지, packet/native 증거를 같은 변경 상태에 연결 |
| 후속 | 새 producer/source/plan/회수/인수 계약을 함께 고정한 후 CP2/3 controller의 전후 gate에 연결. 원 영수증 편집이나 CNI 필드 비교 제외 금지 |

기존 `check-cp1-cni-handoff.py`의 v1 계약은 zone 기반 delta를 검사하는 오프라인 초안이다.
새 ipset 후보는 그 계약과 **호환되지 않으며**, 검사기를 통과시켜 운영 기능이 연결된 것처럼 보이지 않도록 회귀시험으로 차단한다.
기존 node/controller/CP1 원 생산자 바이트는 변경하지 않았다.

## 검증 재현과 한계

```bash
python3 -m unittest discover -s tests -v
python3 tests/probe_cp1_cni_upstream.py --upstream-dir /path/to/pinned-upstream-files
```

두 번째 명령의 디렉터리에는 공식 v2.4.3 `src/firewall/core/{nftables,fw_policy,fw_zone}.py` 원문 3개를 해당 이름으로 둔다.
probe는 파일 해시를 먼저 검사하고 upstream 메서드 AST를 그대로 실행한다. 저장소의 메서드 복사본이나 손으로 다시 쓴 규칙 생성기를 실행하지 않는다.
테스트 더블은 policy/zone/ipset 모델, rich rule 객체 구성, 단순 packet matcher이다. 실제 XML parser, D-Bus, rule transaction, kernel은 실행하지 않았다.
개발 환경에는 `CAP_NET_ADMIN`과 nft/firewalld 실행 환경이 없으므로 이 결과를 kernel runtime 시험으로 바꾸어 표현하지 않는다.

최초 검증 수치는 신규 25개 + 기존 84개 = **109/109 STATIC/MOCK**, 별도로 upstream 함수의 18개 매치 사례다.
현재 수집 일관성 보완은 추가 15개를 포함한 **124/124 STATIC/MOCK**이다. upstream probe 코드는 바뀌지 않았고 이번에 재실행하지 않았다.
후자의 개수를 통합 Runtime/인수 시험 수에 더하지 않는다. 결과 원본과 정확한 해시는 동봉한 JSON/TXT를 따른다.
실서버 변경·새 VM 시험·새 리뷰 요청·병합·CP1 인수·CP2/3/Join·AC 완료는 수행하지 않았다.
