# CP1 CNI FORWARD 진단용 예외안 — 2026-09-15

상태: **진단용 예외 조건부 승인 확보 / 인터페이스 제한 설계 재리뷰 대상**. 이하영은 [2026-09-15 12:23:37 KST 댓글](https://github.com/321Team/infra-kubernetes/pull/10#issuecomment-5674188689)에서 HEAD `994e0cf189403291858fa6f198dead87627f01d6`의 4조건을 확인하고 예외를 승인하면서, 실행기 구현 전 Calico 인터페이스에 바인딩된 zone으로 제한하라는 조건을 추가했다. 이번 개정은 그 설계 반영이며 추가 조건의 수용 완료나 정식 PR APPROVED를 스스로 선언하지 않는다. 실행기·Linux firewalld 통합시험·실서버 적용은 미완료다.

## 정본과 진단용 예외의 경계

[이하영 리뷰 요청](https://github.com/321Team/infra-kubernetes/pull/10#issuecomment-5674028014)의 4개 조건을 모두 채택한다. 본 안은 HEP/GNP 객체를 변경하지 않는 독립 진단이며, 정본 §6.6의 **Bootstrap Allow → canary Worker → Worker 확장 → Control Plane** 순서를 대체하거나 앞당기는 보호 전환이 아니다. 기존 CP1 host-port Runtime 승인 범위를 넘어서는 FORWARD·Permanent metadata·reload는 이 티켓의 별도 예외 결정 대상이다.

1. **AC 불인정:** 이 policy의 PASS/FAIL과 회수 완료는 P1-K8S-01·P1-SEC-01·ICR-D-NET-01-01의 어떤 Acceptance Criteria도 충족시키지 않는다. CP1 ACCEPTED·CNI 장애 해소·CP2 착수 허용을 자동 생성하지 않는다. 성공 결과는 원인 분석 자료로만 사용한다.
2. **Worker 작업과 병행:** Worker HEP/GNP 작업의 선행조건으로 두거나 일정을 지연시키지 않는다. 진단과 무관한 Worker 준비·검토·승인된 작업은 진행한다. 동일 CP1의 방화벽 변경과 복구가 겹치지 않도록 조정하되 이 진단 때문에 Worker 작업을 대기시키지 않는다.
3. **진단 후 회수:** 성공·실패와 관계없이 해당 진단 작업창 안에 Runtime policy·인터페이스 바인딩을 회수하고 Permanent policy·전용 zone 정의를 삭제한다. 최종 기한은 **CP1이 HEP/GNP 전환 단계에 도달하기 전**이다. disabled Permanent 정의나 비활성 전용 zone도 잔여로 인정하므로 남겨두지 않는다. CP1 보호 전환 착수와 겹치면 진단을 시작하지 않으며, 이미 진행 중이면 회수를 우선한다.
4. **티켓 단위 예외:** ADV-20260910과 같은 성격의 제한적 선행 준비로 사유·기간·보호·복구·종료조건을 기록한다. ADV-20260910의 기존 승인이 이 안을 자동 승인하는 것은 아니다. **baseline §6.6은 개정하지 않으며**, 이 예외를 다른 노드·포트·작업으로 확대하지 않는다.

| 예외 결정 항목 | 제출 내용 |
|---|---|
| 사유 | 직접 Pod HTTP 실패와 후순위 firewalld FORWARD 거부 구조의 관련성을 제한된 변경으로 진단 |
| 기간 | 승인된 단일 진단 작업창. 실행 시작부터 최대 20분·마지막 3분 회수/사후 확인을 설계값으로 제출하며, 구현·통합시험에서 검증한다. 이전 날짜의 고정 종료시각을 재사용하거나 자동 연장하지 않음 |
| 보호 | 시험 Pod 2개의 정확한 host veth·CP1 관측 IPAM block·TCP8080 제한, 물리 NIC/터널/기존 시스템 Pod 제외, 기존 Calico 판정과 public INPUT 규칙 보존, Negative 검증, mutation 전 독립 로컬 복구 예약 |
| 복구 | Runtime policy 비활성화·시험 인터페이스 바인딩 제거, Permanent policy·전용 zone 삭제, 필요한 reload 후 원래 Runtime 규칙 6개 복원, 원 영수증 상태·관리/API 대조 |
| 종료조건 | 임시 policy·zone·바인딩의 Runtime/Permanent 부재·원 방화벽 상태 일치·시험 자원 정리·관리/API 확인을 증적으로 남김. 시간 초과/복구 실패는 완료로 기록하지 않고 자동/콘솔 복구와 미회수 상태를 기록 |
| 승인 기록 | 위 댓글의 승인자·시각·검토 HEAD·추가 조건을 연결. 기존 4조건의 수용은 보존하고 이번 인터페이스 설계 수용·정식 PR APPROVED·실행 승인과 구분 |

진단 결과가 PASS여도 policy를 유지하거나 최종 재적용하지 않는다. 회수 후 원 CNI 장애가 해소됐다고 가정하지 않으며, 운영 수정·CP1 최종 인수는 별도 승인된 보호 전환/수정안과 해당 상태의 실제 증적으로 판정한다.

정본 근거: [발행 v3.1.18 §6.6/§6.6.1](https://github.com/321Team/fds-project-baseline/blob/72599ae49342b0fb8dbd3d8ee60314afb9f907bb/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md#66-node-firewall--management-endpoint-review).

## 문제와 증적

CP1의 첫 CNI trial은 직접 Pod HTTP 양방향 FAIL, Service HTTP·양쪽 DNS PASS(3/5)였다. 이어 수집한 native firewalld 규칙에는 DNAT 선행 허용과 cali→cali 새 직접 전달 reject 경로가 있다. Calico가 포함된 ip filter FORWARD(priority 0)보다 firewalld base chain(priority +10)이 뒤에서 적용되므로 앞선 ACCEPT만으로 해결되지 않는다. 실패 당시 packet trace는 없어 특정 패킷 hit·유일 원인 확정과 구분한다.

보완 `kav4i4cv`는 **2026-09-15 10:53:07 KST, 3/3 rc=0·잘림 없음**이다. 외부 SHA256 `3e3ac43556035e5895c4040cdbf3a5ecec6cd3bdbc24de50b11965372fc0d3c2`, 내부 RESULT SHA256 `5423b4870efa82ce8f91eda0be3f37986a7c3485df21d4de0afaead614d97ac8`, 수집기 SHA256 `45dcc4b71a95cc495c6a414d0aa84912a8384654181f126a84a0b99d606ffd86` 일치.

- 실제 iptables 제공 RPM은 `iptables-nft-1.8.11-16.el10.x86_64`, 실행 파일은 `xtables-nft-multi`다. argv[0] 보존 보완으로 native iptables-save 수집이 성공했다. 패키지 설치가 필요한 상태가 아니다.
- native 규칙에서 Calico/Kubernetes forwarding chain과 Calico mark 기반 ACCEPT 확인. 현재 IP/iface별 DROP counter나 누적 ACCEPT counter를 이미 삭제된 시험 Pod의 개별 패킷 증거로 사용하지 않는다.
- NetworkManager 디스크 통합 설정에는 Calico interface 제외 항목이 없다. 직전 nmcli에서 vxlan.calico managed=yes였고 cali 3개는 unmanaged였다. 디스크 설정과 실행 중 daemon 설정·udev 상태는 구분한다. 이번 same-node 실패 원인으로 NM을 단정하지 않는다.
- 이 보완은 health·CNI를 재시험하지 않았다. 마지막 기본 상태 확인은 10:39 KST이며, 이전 CNI FAIL 판정은 유지한다.

앞선 기록: [CNI trial](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673301524), [진단](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673425463).

## 제안: CP1 시험 전달만 허용하는 별도 firewalld policy

원래 6개 Runtime host rule과 public zone의 INPUT 규칙을 보존하는 별도 FORWARD policy를 제안한다. policy의 ingress/egress에는 전용 zone 이름을 넣고, 그 zone에는 이번 시험 Pod의 host veth 2개만 Runtime으로 바인딩한다. 시험 veth의 zone 분류 변경에 따른 INPUT 제한은 아래 영향 범위에 명시한다. 전체 Pod 대역 source binding·전체 cali wildcard binding·trusted zone·global ACCEPT 또는 firewalld 중지는 포함하지 않는다.

| 항목 | 제안값 및 범위 |
|---|---|
| 대상 | CP1 한 대 |
| Policy 이름 | fds-cp1-cni-probe |
| ingress / egress | fds-cni-trial / fds-cni-trial; ANY/HOST 사용 안 함 |
| 전용 zone | fds-cni-trial: target=default, forward=no, source/서비스/포트/rich rule/NAT 추가 없음 |
| 인터페이스 | 이번 실행의 시험 Pod 2개에 대응하는 정확한 host cali veth 이름만 Runtime 바인딩. ens160·lo·vxlan.calico·기존 Pod veth 제외 |
| target / priority | policy CONTINUE / -500; 불일치 흐름은 zone/후속 판정으로 진행. 전용 zone의 intra-zone 자동 허용은 비활성화 |
| source / destination | CP1에 관측된 동일 IPAM block의 Pod 주소 범위. 배포 전 실제 CP1 소유 block 및 시험 Pod IP 대조 |
| 허용 프로토콜 | TCP/8080 한정, 해당 Pod→Pod HTTP 시험용 |
| Permanent 상태 | disabled policy 및 인터페이스/source가 비어 있는 zone 정의만 사용, 둘 다 종료 시 삭제 |
| Runtime 상태 | 예외 승인·실행안 검토·실행 결정·보존/복구 검증 후 진단 중에만 활성화 |
| 증적 범위 | same-node HTTP/8080·Service·DNS 진단 관측. K8S·SEC·ICR 모든 AC 및 CP1 최종 인수에 사용 불가 |
| 책임/회수 | 이권욱 실행·증적, 이하영 독립 리뷰. 작업창 내 policy·zone·바인딩 회수, 늦어도 CP1 HEP/GNP 전환 단계 도달 전 Permanent 삭제까지 완료 |

같은 전용 zone을 양쪽에 지정해 시험 veth 간 전달로 제한하고 source/destination block과 TCP8080 조건을 함께 유지한다. source CIDR은 신원 인증이 아니므로 실제 ingress/egress 제한과 Negative 증적이 필요하다. 이 policy가 물리 NIC 유입을 새로 허용하지 않는다는 것과 시스템 전체 anti-spoof가 입증됐다는 것은 다르다. 기존 Calico의 앞선 판정도 계속 적용되며 HEP/GNP 0개라는 사실만으로 이 policy가 유일한 방어선이라고 판단하지 않는다.

새 policy·zone의 Permanent 정의와 로딩을 위한 reload는 기존 순수 Runtime 6-rule 추가 승인과 다른 변경 범위다. Permanent policy는 disabled, zone의 Permanent 인터페이스/source 목록은 빈 상태로 생성한다. zone은 기본 zone으로 지정하지 않으며, Runtime 바인딩과 metadata·reload·회수를 새 실행안의 리뷰 범위에 포함한다.

근거: [policy의 ANY/HOST 및 FORWARD](https://firewalld.org/documentation/man-pages/firewalld.policies.html), [firewall-cmd의 new-policy/disable/priority/reload](https://firewalld.org/documentation/man-pages/firewall-cmd.html), [nftables base chain 판정](https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains).

## 인터페이스 제한·영향·검증

아래는 추가 조건을 좁은 범위로 구현하기 위한 설계다. 현장 적용 성공이나 해당 firewalld 패키지의 통합시험 결과가 아니다.

- **정확한 인터페이스 식별:** 새로운 시험 Namespace의 소유 UID·Pod UID·CP1 배치·Pod IP를 고정하고, Pod 내부 eth0 ifindex/iflink와 host veth의 ifindex/peer 관계 및 Pod IP /32 route를 대조한다. `cali` 접두사만으로 소유권을 인정하지 않는다. 정확히 2개의 veth 이름을 실행 입력으로 생성하며 미확인·재생성·이름/ifindex 재사용 시 허용을 진행하지 않는다. 과거 시험의 삭제된 veth 이름을 하드코딩하지 않는다.
- **wildcard·터널 제외:** 리뷰의 `cali+`/`vxlan.calico` 후보보다 좁게 시험용 cali veth 2개만 선택한다. 이번 시험은 같은 노드의 직접 Pod 경로이므로 cross-node 터널을 바인딩할 필요가 없다. `vxlan.calico`는 관측상 NM managed=yes이기도 하므로 그대로 둔다. 추후 터널 시험은 별도 범위다. 이 선택의 추가 조건 충족 여부를 이번 재리뷰에서 확인한다.
- **Runtime 바인딩의 전제:** 선택한 새 veth에 기존 명시적 Runtime/Permanent zone binding·source 분류 충돌·NM 관리·ifcfg 파일이 있으면 자동 이동하거나 설정을 덮어쓰지 않는다. 인터페이스 옵션은 NM/ifcfg에 영향을 줄 수 있으므로 무조건 Runtime-only라고 가정하지 않는다. 전제를 확인한 새 veth에만 Runtime `add-interface`를 사용하고, 적용 전후 NM/ifcfg·Permanent interface/source 목록의 불변을 확인한다. 광역 `change-interface` 또는 NetworkManager 프로필 변경을 사용하지 않는다.
- **전용 zone의 우회 차단:** Runtime/Permanent 모두 `forward=no`를 명시적으로 설정·조회하며, target=ACCEPT/trusted·source CIDR/IPSet binding·포트/서비스·rich allow·masquerade/forward-port를 넣지 않는다. zone의 ingress/egress에 다른 인터페이스가 섞이거나 default zone이 바뀌면 중단·회수한다. 빈 source 목록을 유지해 source만 위조한 ens160 트래픽이 시험 zone으로 분류되지 않게 한다.
- **INPUT 영향의 한계:** public 규칙·ens160 바인딩·기존 시스템 Pod의 zone 분류는 유지한다. 다만 시험 veth는 빈 전용 zone으로 분류되므로 시험 Pod→host의 기존 public 서비스 접근이 더 제한될 수 있다. 따라서 ‘모든 INPUT 불변’이라고 기록하지 않는다. 새 zone에 SSH/API 등을 추가해 우회하지 않고, 관리/API·기존 시스템 Pod·시험 Service/DNS를 별도로 대조한다. 이 제한으로 시험이 성립하지 않으면 기존 zone을 복원하고 결과를 실패/미완료로 남긴다.
- **Negative의 범위:** 통합시험은 실제 생성된 nft 규칙에서 ingress와 egress가 모두 해당 veth 집합으로 제한되는지 확인한다. 시험 veth↔veth TCP8080, 시험 veth↔veth TCP8081, block 밖 주소, ens160→시험 veth(위조 Pod source 포함), 시험 veth→ens160, vxlan.calico→시험 veth, 시험 veth→기존 Pod veth를 구분한다. 기존 conntrack/DNAT 선행 허용과 섞이지 않는 새 직접 Pod IP 흐름으로 검사하고 전역 conntrack flush는 하지 않는다. 패킷이 해당 ingress에 도달했다는 근거 없이 송신 측 timeout만으로 차단 PASS를 기록하지 않는다. 기존 선행 규칙이 허용한 경로를 이번 policy의 허용/차단으로 오인하지 않는다. 현장에서 구현되지 않은 물리/터널 경로는 NOT_TESTED이며 필요한 Negative 범위를 충족하기 전 Runtime 진단 준비 완료로 판정하지 않는다.

공식 근거: [zone의 target·interface·intra-zone forwarding](https://firewalld.org/documentation/man-pages/firewalld.zone.html), [intra-zone forwarding 동작](https://firewalld.org/2020/04/intra-zone-forwarding), [interface binding의 NM/ifcfg 영향](https://firewalld.org/documentation/man-pages/firewall-cmd.html#Options_to_Handle_Bindings_of_Interfaces). 위 동작을 CP1에 적용한 영향 범위와 exact-veth 선택은 본 설계의 판단이며 현장 검증과 구분한다.

## 실행·복구 설계 요구사항

다음은 조건부 승인에 추가된 인터페이스 제한을 구체화한 구현 사양이며 지금 복사·실행할 수 있는 runbook이 아니다. 20분/3분 설계값은 아직 새 실행기의 시간 제한이나 자동 복구로 구현되지 않았다.

1. 원 최종 CP1 영수증·현재 Runtime/Permanent·active policy/zone·direct rule·NM binding을 고정한다. 임의 차이를 복구 대상으로 덮어쓰지 않고 중단한다. 관리/API·기존 시스템 Pod 기준을 확보하고, 시험 자원 생성 후 위 식별 방식으로 두 veth와 사전 binding 없음·NM 비관리·ifcfg 부재를 확인한다. policy/zone 이름 충돌이면 기존 객체를 재사용하지 않는다. 기존 백업/콘솔 근거를 연결하며 같은 원본 증적을 다시 수집하도록 요구하지 않는다.
2. mutation 전에 CP1 로컬 복구 작업을 독립적으로 예약한다. 컨트롤러/SSH가 끊겨도 실행되며 root 소유 source·증적·원상복구 명령을 고정한다. 예약 성공과 취소/실행 결과를 기록한다. 실행 시작 기준 최대 20분·마지막 3분 회수/사후 확인을 원격까지 승계하고 명령 제한시간을 잔여시간 안에 둔다. 정상 종료와 복구 작업이 경합해도 중복 변경이 없도록 구현·시험한다. 회수 시점부터 신규 진단 변경을 중단하며, 복구 실패를 이유로 진단 허용을 연장하지 않는다.
3. 신규 Permanent 전용 zone을 먼저 비어 있는 상태(target=default, forward=no, interface/source 없음)로 만들고, 그 이름을 ingress/egress로 지정한 신규 Permanent policy를 disabled로 만든다. source/destination/port/priority/target 및 기존 객체 불변을 확인한다. 중간 실패 시 자신이 만든 객체만 policy→zone 의존 순서로 회수한다.
4. 통제된 reload는 기존 Runtime-only 6개 규칙을 제거한다. **일시적 가용성 영향이 있을 수 있다.** 재적용 대상을 원 영수증과 현재 비교에서 정확히 산출하고 즉시 복원한 뒤 관리 경로/API와 6개 규칙이 원 상태인지 먼저 확인한다. `runtime-to-permanent`, runtime 전체의 무조건 복사 또는 수집된 iptables/nft ruleset 통째 복원은 사용하지 않는다.
5. policy가 disabled인 상태에서 전용 zone의 forward=no·기본 zone 불변을 확인하고 검증한 시험 veth 2개만 Runtime 바인딩한다. 두 바인딩·source 없음·Permanent 비바인딩·NM/ifcfg 불변을 다시 확인한 뒤 policy의 Runtime disable을 제거한다. 실제 nft에서 양쪽 interface 분류·source·destination·TCP8080 제한을 확인한다. Calico 및 기존 public 규칙·비대상 바인딩을 보존하고 시험 Pod INPUT 영향은 앞 절의 범위로 평가한다.
6. CNI 5개 시험 외에 source/destination block 밖·TCP8080 밖의 흐름, 관리/외부 유입 및 spoofing 처리, 기존 CP 허용/차단·기능을 대조한다. 준비된 source에서 해당 경로를 실제로 시험할 수 없는 경우 Negative를 PASS로 표시하지 않는다.
7. 성공·실패와 관계없이 Runtime policy 비활성화 → 소유 veth의 Runtime 바인딩 제거 및 사전 미바인딩 상태 복원 → 신규 Permanent policy 삭제 → 신규 Permanent zone 삭제 → 필요한 통제 reload/원 Runtime 6개 복원 순서로 회수한다. Runtime zone의 잔여 참조·인터페이스·source가 없는지 확인하며, 다른 주체가 변경한 객체/동명이 재사용된 interface를 임의 삭제하지 않는다. 사라진 시험 veth를 복구 명목으로 재생성하지 않고 잔여 binding 유무를 대조한다. 실패 시 자동 복구 또는 콘솔 복구로 전환하고 실제 결과를 남긴다.
8. 시험 Namespace/Pod/Service의 소유 UID를 확인해 정리하고 policy·zone·binding의 Runtime/Permanent 부재, 원 영수증 상태·관리/API·기존 시스템 Pod와 NM/ifcfg 불변을 확인한다. disabled policy·빈 zone 잔여도 완료가 아니다. 복구 작업 취소는 이 확인 이후에만 한다. 신규 policy의 유지·최종 재적용·자동 연장 경로를 구현하지 않는다. 원래 Runtime 6개 복원은 진단 policy 재적용과 구분하며, 원 6개의 reload/재부팅 지속성은 별도 미완료 상태다.

## 진단 증적과 기존 CP1 영수증·consumer 보존

현행 `scripts/run-node-firewall.py::cp1_state_gate`는 현재 CP1 firewall과 기존 최종 receipt.after의 완전 동일성을 검사한다. 신규 policy 정의·활성 상태가 추가되면 정상적으로 이 검사를 통과하지 않는다. 검사를 없애거나 기존 영수증을 편집하면 안 된다.

이번 예외에서는 **후속 consumer를 확장하지 않는다**. 진단 중인 CP1 상태를 후속 노드 적용용으로 인정하지 않고, 회수 후 원 receipt.after와 동일한 상태로 복원한다. 기존 영수증·승인 입력·일치 검사를 그대로 유지한다. 회수/일치 성공도 남은 CP1 인수 조건을 면제하거나 CP2 적용을 승인하지 않는다.

별도 진단 결과는 원 영수증 hash → 진단 before/active 상태 → 제한 시험 결과 → Runtime/Permanent 회수 → 원 상태 복원 비교 및 자원 정리를 연결한다. 결과 형식에 `DIAGNOSTIC_ONLY`, `acceptance_criteria_credit=false`, `authorizes_cp1_acceptance=false`, `authorizes_cp2_change=false`를 명시하고 운영 인수 영수증으로 대입하지 않는다. 원 producer와 진단 수집/실행 소스의 hash는 각각 기록한다. 이는 증적 형식의 설계이며 새 영수증/실행기를 구현했다는 뜻이 아니다.

필수 구현 시험: 원 receipt 변조, before 불일치, block/port 확대, ANY/HOST 재도입, 비시험/물리/터널 interface 혼입, veth 재생성·식별 불일치, zone forward=yes·source binding·Permanent interface 혼입, NM/ifcfg 부수 변경, 일부 binding만 성공한 경우, Permanent enable, 만료·부분 실행, 복구 실패, policy/zone/binding 잔여, policy 유지/재적용 시도, 진단 증적의 인수 입력 오용 거부. 기존 consumer가 진단 중 상태를 거부하고, 완전 회수 후에만 원 상태 일치 검사를 통과하는지 확인한다. 방화벽 일치 검사 통과와 실제 CP1 인수 판정은 별개다.

## NetworkManager 보완은 별도 변경

권장 설정 범위는 Calico의 cali/tunl/vxlan.calico interface를 관리 대상에서 제외하는 것이다. 현재 디스크 파일·udev·daemon 적용 상태의 차이를 확인하고, 기존 설정을 덮어쓰지 않는 파일 단위 변경·복구를 별도로 준비한다. NetworkManager 전체 재시작이나 live tunnel 상태 변경을 이번 FORWARD canary에 섞지 않는다. cross-node 확대 전 관련 전제와 대조한다.

근거: [Calico 시스템 요구사항](https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements), [NetworkManager print-config의 디스크/daemon 차이](https://networkmanager.dev/docs/api/latest/NetworkManager.html).

## 리뷰 요청 및 완료 기준

- [x] 현장 보완 3/3·해시·source 검토와 수집기 결함 해소 확인.
- [x] 현재 FORWARD 거부 구조 및 consumer 불일치 지점 확인.
- [x] 리뷰의 4개 예외 조건, 성공 여부와 무관한 회수, consumer 확장 제외를 문서·proposal JSON에 반영.
- [x] HEAD 994e0cf의 4조건에 대한 이하영 조건부 예외 승인 댓글 확보. 추가 인터페이스 조건 및 정식 PR APPROVED와 구분.
- [x] ANY 제거·전용 zone·시험 veth 2개 Runtime binding·부수 영향·완전 회수 설계를 문서/JSON에 반영.
- [ ] 이번 새 HEAD의 exact-veth 선택과 zone 영향/회수 설계에 대한 추가 조건 수용·독립 재리뷰 확인.
- [ ] 제한 policy 후보의 보안 범위·anti-spoof/Negative 실측 방식 수용 여부.
- [ ] Permanent metadata/reload 및 Runtime 복원에 대한 변경 범위 수용 여부.
- [ ] 추가 조건 반영 확인 후 실행기·자동 복구·진단 증적 구현, 모의 및 실제 firewalld 통합시험.
- [ ] 정확한 실행 HEAD 독립 리뷰·실행 조건 고정.
- [ ] 실제 진단·Negative·자원 정리·policy/zone/binding Runtime/Permanent 완전 회수·원 상태 복원 증적 확인. 이 항목은 운영 AC 인수가 아님.

ICR #14 및 P1-K8S #10은 In Progress 유지. 기존 AC, 코드 병합, 사람의 최종 승인과 이번 수집 성공을 혼동하지 않는다. 보완 수집 3개 전체 재실행은 필요하지 않다.
