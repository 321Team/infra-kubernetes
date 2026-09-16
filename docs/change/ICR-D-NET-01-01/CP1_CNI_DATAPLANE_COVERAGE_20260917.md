# 실제 CP1 dataplane 수집 검증과 관측 범위 보완

기준 소스 `72a828eaa1a8603aedd3302b8ac427c0a0525b06`.
상태: **TARGET_DATAPLANE_COVERAGE_VERIFIED_INTEGRATION_INCOMPLETE**.
이 기록은 앞선 WITHDRAWAL_COVERAGE 문서의 “실제 근거 미수집” 상태를 갱신한다.
과거 검증 기록 자체를 변경하거나 운영 적용 완료로 해석하지 않는다.

## 확인한 자료

`FDS_CP1_CNI_Dataplane.QP5Lan.tar.gz`의 sidecar와 실제 SHA256이 일치했다.
압축 구성의 경로와 일반 파일 여부를 검사한 후 `CAPTURE.json`, `EXIT_CODE.txt`를 읽었다.
종료코드는 0이다. 원본 수집 자료와 장비 식별자는 GitHub에 게시하지 않는다.

- 외부 SHA256: `634ae04b98457eefd7e8e4770c53ab5a09208520a773f32351ceb46063a69a01`
- CAPTURE.json SHA256: `1b6d58711ebf0eed46105bf45b1501efaf14a13b650f5758bd745f995e821db8`
- 수집기 SHA256: `163c504eddf687d79c1469283b0ab8f3264c39ae8dae8ac24b7d119feca0adda`
- 수집 구간: 2026-09-16 19:50:36.557110–19:50:40.727264 UTC (9월 17일 04:50 KST).
- 소요 4.17초, 71개 조회 성공, stdout/stderr 잘림 없음, 세 번의 boot 조회 일치.

짝지어진 nft JSON·IPv4/IPv6 iptables-save의 차이는 counter 값이다.
kernel ipsets, link/route 및 나머지 API/환경 조회는 전후 동일하다.
이는 해당 수집 구간의 두 관측이 일치했다는 뜻이다. 원자적 snapshot이나 이후 상태를 보장하지 않는다.

## 실제 backend와 관측 범위

firewalld `2.4.3-4.el10`, nftables `1.1.5-6.el10`, iptables-nft `1.8.11-16.el10`,
ipset `7.22-13.el10`을 확인했다. 실제 `iptables --version`은 `(nf_tables)`이다.
Calico image digest는 기존 자료와 같은 `c061070a27292f8152ae6a0582078eb9059d1b6ed5e57c2052e5c22534734240`이며,
이 digest에서 버전 태그를 임의 추정하지 않았다. kube-proxy desired image는 `v1.36.4`이다.

| 항목 | 수량 | 보완 내용 |
|---|---:|---|
| native tables / chains / rules | 9 / 180 / 341 | 모든 foreign 객체와 순서 보존 |
| IPv4 save | 4 tables / 179 rules | match·target 인자를 그대로 보존 |
| IPv6 save | 3 tables / 22 rules | IPv6 보호 상태도 함께 비교 |
| opaque xt 표현 | 329 | extension 순서·수·target·chain edge·verdict 대응 검사 |
| kernel ipsets | 5 | 정의와 전체 요소 포함, 참조 set 누락 거부 |

`native_text` 두 조회에는 iptables-nft 소유 테이블 안내와 `XT target MASQUERADE not found` 경고가 있다.
경고를 숨기지 않고 재생 기록에 남겼다. authoritative JSON과 양쪽 save 및 ipset 출력에는 stderr가 없고,
서로 대응하는 규칙을 모두 확인했다. text 출력을 정책 복원 입력으로 사용하지 않는다.
Netfilter도 xt 구문을 불완전한 번역의 fallback으로 설명한다.
[공식 nft 문서](https://netfilter.org/projects/nftables/manpage.html)

## 코드 변경

`cp1_cni_dataplane.py`가 native JSON, 두 save 출력, kernel ipsets를 하나의 고정 상태로 묶는다.
실제 패킷/바이트 counter만 정규화한다. `--comment` 안의 counter처럼 보이는 문자, extension 인자,
set 요소, foreign handle, 객체 순서는 보존한다. 알 수 없는 확장·잘린 출력·누락 set·순서 불일치·
터미널 verdict 불일치는 거부한다. iptables-save가 합성하는 빈 ACCEPT builtin만 제한적으로 대응 허용한다.

`LocalObserver`의 추가 허용 및 같은 boot 전체 회수 관측은 두 save와 kernel ipsets를 모두 두 번 읽는다.
25초 전체 관측 예산 안에서 기존 collector의 argv[0] 보존·출력 제한·timeout 구현을 재사용한다.
전체 자료가 없으면 기존 `XT_COMPAT_COVERAGE_REQUIRED` 차단은 유지된다.
새 projection의 hash를 activation/lease에 반영해야 하므로 기존 activation을 조용히 재해석하지 않는다.
독립적인 좁은 disable 경로는 기존과 같으며 전체 회수 성공으로 간주하지 않는다.

## 실제 자료에서 확인한 후속 모델 범위

현재 non-hostNetwork Pod는 3개이다. Calico endpoint fw/tw chain 외에
`arp/calico-arp`의 veth→chain verdict map 및 Pod IP별 ARP reply 차단 규칙도 존재한다.
CoreDNS Pod IP가 바뀌면 Service/EndpointSlice와 kube-proxy NAT endpoint chain도 바뀐다.
Calico filter chain만 제외하거나 `cali-*`/`KUBE-*` 전체를 hash에서 빼는 방식으로 해결할 수 없다.

public Runtime에는 기존 관리/API 규칙 외에 원 Runtime 6개가 존재하며 Permanent에는 그 6개가 없다.
ens160의 Runtime public 귀속도 유지돼 있다. 캡처의 Pod Ready는 모두 True이나 통신 인수 시험을 대신하지 않는다.
새 managed policy는 아직 설치되지 않았다.

## 자체 검증과 명확한 한계

- 전체 로컬 회귀 **229/229 PASS** (이전 216 + 신규 13).
- 실제 첨부 CAPTURE.json에 대한 오프라인 재생 및 메모리 내 반례 **13/13 PASS**.
- 숨겨진 ipset 범위·match 방향·conntrack·DNAT 변화, ARP IP 변화, API race,
  누락/경고/잘림/boolean 종료코드를 거부하거나 다른 고정 상태로 판정한다.
- 순수 counter 변화는 같은 상태로 판정한다.
- 실제 CP1에 대한 이번 추가 작업은 없다. kernel/firewalld/systemd 변경 시험은 미수행이다.

자료 누락은 해소됐다. **정상 Pod 교체의 Calico/ARP/kube-proxy 변화 허용 모델은 아직 구현되지 않았다.**
따라서 현재 전체 비교는 그런 변화도 고정 상태 drift로 차단한다. 이를 운영 준비 완료로 보고하지 않는다.
root staging/activation/설치, 원 Runtime 6개·관리 경로·객체·unit을 포함한 독립 전체 회수,
실제 CP1 인수 및 인증 관측의 후속 controller 연결도 남아 있다.
완성된 통합 묶음의 리뷰 요청 시점에는 도달하지 않았다. 이번 중간 결과로 새 리뷰 요청을 만들지 않는다.
CP1 인수·CP2/3 변경·Join은 계속 보류한다.

재현: `python3 -B -m unittest discover -s tests -v`.
실제 원본을 보관한 환경에서는 `tests/probe_cp1_cni_dataplane_capture.py --capture CAPTURE.json --sha256 <위 해시>`로
같은 오프라인 재생을 수행할 수 있다. 이 probe는 host 명령을 실행하지 않는다.
