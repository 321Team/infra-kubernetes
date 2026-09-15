# CP1 CNI FORWARD 보완안 — 2026-09-15

상태: **PROPOSED / 설계 리뷰 대상**. 실행기 구현·Linux firewalld 통합시험·독립 코드 리뷰·실서버 적용은 미완료다. 이 문서나 JSON의 병합은 실행 승인 또는 CP1 ACCEPTED가 아니다.

## 문제와 증적

CP1의 첫 CNI trial은 직접 Pod HTTP 양방향 FAIL, Service HTTP·양쪽 DNS PASS(3/5)였다. 이어 수집한 native firewalld 규칙에는 DNAT 선행 허용과 cali→cali 새 직접 전달 reject 경로가 있다. Calico가 포함된 ip filter FORWARD(priority 0)보다 firewalld base chain(priority +10)이 뒤에서 적용되므로 앞선 ACCEPT만으로 해결되지 않는다. 실패 당시 packet trace는 없어 특정 패킷 hit·유일 원인 확정과 구분한다.

보완 `kav4i4cv`는 **2026-09-15 10:53:07 KST, 3/3 rc=0·잘림 없음**이다. 외부 SHA256 `3e3ac43556035e5895c4040cdbf3a5ecec6cd3bdbc24de50b11965372fc0d3c2`, 내부 RESULT SHA256 `5423b4870efa82ce8f91eda0be3f37986a7c3485df21d4de0afaead614d97ac8`, 수집기 SHA256 `45dcc4b71a95cc495c6a414d0aa84912a8384654181f126a84a0b99d606ffd86` 일치.

- 실제 iptables 제공 RPM은 `iptables-nft-1.8.11-16.el10.x86_64`, 실행 파일은 `xtables-nft-multi`다. argv[0] 보존 보완으로 native iptables-save 수집이 성공했다. 패키지 설치가 필요한 상태가 아니다.
- native 규칙에서 Calico/Kubernetes forwarding chain과 Calico mark 기반 ACCEPT 확인. 현재 IP/iface별 DROP counter나 누적 ACCEPT counter를 이미 삭제된 시험 Pod의 개별 패킷 증거로 사용하지 않는다.
- NetworkManager 디스크 통합 설정에는 Calico interface 제외 항목이 없다. 직전 nmcli에서 vxlan.calico managed=yes였고 cali 3개는 unmanaged였다. 디스크 설정과 실행 중 daemon 설정·udev 상태는 구분한다. 이번 same-node 실패 원인으로 NM을 단정하지 않는다.
- 이 보완은 health·CNI를 재시험하지 않았다. 마지막 기본 상태 확인은 10:39 KST이며, 이전 CNI FAIL 판정은 유지한다.

앞선 기록: [CNI trial](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673301524), [진단](https://github.com/321Team/infra-kubernetes/pull/2#issuecomment-5673425463).

## 제안: CP1 시험 전달만 허용하는 별도 firewalld policy

원래 6개 Runtime host rule과 public zone의 INPUT 허용을 그대로 보존하는 별도 FORWARD policy를 제안한다. 전체 Pod 대역 source binding·trusted zone·global ACCEPT 또는 firewalld 중지는 이 안에 포함하지 않는다.

| 항목 | 제안값 및 범위 |
|---|---|
| 대상 | CP1 한 대 |
| Policy 이름 | fds-cp1-cni-probe |
| ingress / egress | ANY / ANY; HOST 제외로 FORWARD에 한정 |
| target / priority | CONTINUE / -500; 불일치 트래픽은 기존 zone 판정으로 진행 |
| source / destination | CP1에 관측된 동일 IPAM block의 Pod 주소 범위. 배포 전 실제 CP1 소유 block 및 시험 Pod IP 대조 |
| 허용 프로토콜 | TCP/8080 한정, 해당 Pod→Pod HTTP 시험용 |
| Permanent 상태 | 새 policy 정의는 disabled로 보관 |
| Runtime 상태 | 보존·복구 절차 검증 후 해당 policy만 활성화 |
| 인수 범위 | same-node HTTP/8080·Service·DNS 제한 증적. 전체 CNI/전체 포트/cross-node/MTU/HEP·GNP 인수 아님 |
| 책임/회수 | 이권욱 실행·증적, 이하영 독립 리뷰. 최종 보호 전환 시 회수하며 유지 여부는 Runtime 변경안 리뷰에 명시 |

ANY는 HOST를 포함하지 않는다. source와 destination을 모두 제한하고, port를 명시하므로 양쪽 방향의 해당 block 내 TCP/8080만 제안한다. Pod 주소를 위조한 외부 유입은 source CIDR만으로 인증되는 것이 아니므로 아래 Negative·anti-spoof 검토에서 다뤄야 한다. 그 검증을 충족하지 못하면 이 후보는 적용하지 않고 최종 HEP/GNP 전환안으로 재설계한다.

공식 API에서 새 policy 생성은 Permanent 작업이며 로딩에는 reload가 필요하다. **따라서 이 제안은 기존의 순수 Runtime 6-rule 추가 승인과 다른 변경 범위다.** Permanent 허용을 선반영하지 않도록 disabled로 생성하되, metadata 추가·reload 및 기존 Runtime 보존까지 새 실행안의 리뷰 범위로 포함한다.

근거: [policy의 ANY/HOST 및 FORWARD](https://firewalld.org/documentation/man-pages/firewalld.policies.html), [firewall-cmd의 new-policy/disable/priority/reload](https://firewalld.org/documentation/man-pages/firewall-cmd.html), [nftables base chain 판정](https://wiki.nftables.org/wiki-nftables/index.php/Configuring_chains).

## 실행·복구 설계 요구사항

다음은 실행기 구현 사양이며 지금 복사·실행할 수 있는 runbook이 아니다.

1. 원 최종 CP1 영수증·현재 Runtime/Permanent·active policy/zone·direct rule·NM binding을 고정한다. 임의 차이를 복구 대상으로 덮어쓰지 않고 중단한다. 기존 백업/콘솔 근거를 연결하며 같은 원본 증적을 다시 수집하도록 요구하지 않는다.
2. mutation 전에 CP1 로컬 복구 작업을 독립적으로 예약한다. 컨트롤러/SSH가 끊겨도 실행되며 root 소유 source·증적·원상복구 명령을 고정한다. 예약 성공과 취소/실행 결과를 기록한다. 실행 시작 기준 시간 제한·정리 여유를 사용하고 이전 날짜의 고정 종료시각을 재사용하지 않는다.
3. 이름 충돌이 없는 신규 Permanent policy를 먼저 disabled 상태로 만들고 source/destination/port/priority/target을 검증한다. 중간 실패 시 자신이 만든 객체만 회수한다.
4. 통제된 reload는 기존 Runtime-only 6개 규칙을 제거한다. **일시적 가용성 영향이 있을 수 있다.** 재적용 대상을 원 영수증과 현재 비교에서 정확히 산출하고 즉시 복원한 뒤 관리 경로/API와 6개 규칙이 원 상태인지 먼저 확인한다. `runtime-to-permanent`, runtime 전체의 무조건 복사 또는 수집된 iptables/nft ruleset 통째 복원은 사용하지 않는다.
5. Runtime에서 신규 policy의 disable만 제거하고 실제 nft chain에서 source·destination·TCP8080 제한과 INPUT/기타 FORWARD 불변을 검증한다. 기존 Calico 판정은 보존한다.
6. CNI 5개 시험 외에 source/destination block 밖·TCP8080 밖의 흐름, 관리/외부 유입 및 spoofing 처리, 기존 CP 허용/차단·기능을 대조한다. 준비된 source에서 해당 경로를 실제로 시험할 수 없는 경우 Negative를 PASS로 표시하지 않는다.
7. rollback 시험은 Runtime policy 비활성화 → 자신이 만든 Permanent policy 제거 → 필요한 통제 reload/원 Runtime 6개 복원 → baseline의 모든 관련 필드 일치 및 관리/API 확인 순서다. 실패 시 자동 복구 또는 콘솔 복구로 전환하고 실제 결과를 남긴다.
8. 최종 유지가 승인된 경우 별도 재적용과 최종 receipt를 발행한다. 복구 작업 취소는 최종 검증 및 승인된 유지 조건을 충족했을 때만 한다. Permanent policy는 disabled로 남으므로 이후 reload/재부팅 시 신규 CNI 허용과 기존 6개 Runtime 규칙은 사라질 수 있다. 이를 지속성 PASS로 기록하지 않는다.

## CP1 영수증과 후속 consumer 변경

현행 `scripts/run-node-firewall.py::cp1_state_gate`는 현재 CP1 firewall과 기존 최종 receipt.after의 완전 동일성을 검사한다. 신규 policy 정의·활성 상태가 추가되면 정상적으로 이 검사를 통과하지 않는다. 검사를 없애거나 기존 영수증을 편집하면 안 된다.

구현안은 원 영수증 hash → 신규 변경의 before/after → rollback 결과 → 최종 재적용 결과 → 제한 범위의 실제 인수 기록을 연결하는 별도 증적 계약을 정의해야 한다. consumer는 승인된 추가 변화만 계산해 정확히 비교하며, 임의 extra rule·source·policy·Permanent enable·host protection 차이를 거부해야 한다. 원 producer 실행 소스와 새로운 보완 producer/consumer 소스의 hash를 각각 기록한다.

필수 시험: 원 receipt 변조, before 불일치, block/port 확대, Permanent enable, 만료·부분 실행, 복구 실패, 오래된 최종 상태, 인수 범위 불일치 거부. 기존 producer/consumer 회귀를 유지한다. **현행 소스의 단순 재실행만으로 새 상태를 승인할 수 없다.**

## NetworkManager 보완은 별도 변경

권장 설정 범위는 Calico의 cali/tunl/vxlan.calico interface를 관리 대상에서 제외하는 것이다. 현재 디스크 파일·udev·daemon 적용 상태의 차이를 확인하고, 기존 설정을 덮어쓰지 않는 파일 단위 변경·복구를 별도로 준비한다. NetworkManager 전체 재시작이나 live tunnel 상태 변경을 이번 FORWARD canary에 섞지 않는다. cross-node 확대 전 관련 전제와 대조한다.

근거: [Calico 시스템 요구사항](https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements), [NetworkManager print-config의 디스크/daemon 차이](https://networkmanager.dev/docs/api/latest/NetworkManager.html).

## 리뷰 요청 및 완료 기준

- [x] 현장 보완 3/3·해시·source 검토와 수집기 결함 해소 확인.
- [x] 현재 FORWARD 거부 구조 및 consumer 불일치 지점 확인.
- [ ] 제한 policy 후보의 보안 범위·anti-spoof/Negative 실측 방식 수용 여부.
- [ ] Permanent metadata/reload 및 Runtime 복원에 대한 변경 범위 수용 여부.
- [ ] 실행기·자동 복구·증적 계약 구현, 모의 및 실제 firewalld 통합시험.
- [ ] 정확한 실행 HEAD 독립 리뷰·실행 조건 고정.
- [ ] 실제 변경/복구/재적용·CNI·Negative 증적 및 CP1 최종 인수.

ICR #14 및 P1-K8S #10은 In Progress 유지. 기존 AC, 코드 병합, 사람의 최종 승인과 이번 수집 성공을 혼동하지 않는다. 보완 수집 3개 전체 재실행은 필요하지 않다.
