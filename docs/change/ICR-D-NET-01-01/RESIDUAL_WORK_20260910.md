# PR #2 리뷰 이후 잔여 작업

기준: 2026-09-10. PR #2 리뷰어는 사용자 지정에 따라 **이하영(hayeounglee)**이며 Ready for review 전환·리뷰 요청을 완료했다. 검토 HEAD는 `068661e076de02669170d3582f3d596d6e1a8eb3`이다. 리뷰 승인·병합·방화벽 적용 완료를 뜻하지 않는다.

CP1 최신 inspect 제출본 `FDS_CP1_Canary_jvgkgfm9.tar.gz`는 outer/내부 5개 체크섬과 110개 조회를 검증했다. CP1 canary 입력 확보는 완료했고 runtime 적용은 미실행이다. 이 입력의 15분 유효기간이 지났으므로 실제 적용 창에서 같은 검토 소스로 다시 inspect해야 한다. 지금 반복 수집할 필요는 없다.

## Worker 제출본 검토 — 2026-09-10 15:11 KST

압축파일 SHA-256 `cfbc39b698b7198ec7c3853d8c783527aeb241a5347a0378cc1152ce0481d42e`, 내부 8/8 체크섬 및 소스 HEAD `e83524b9097cee52e254a40fbe3db238eed801a9` 일치. 99개 조회의 rc는 모두 0이나 6대 time_state가 빈 출력이므로 원본 COLLECTED를 완전성 PASS로 인정하지 않는다. 원본을 보존하고 검토 판정은 **PARTIAL / 보완 필요**로 기록한다.

- CP→Worker 10250은 0/9, errno 113. Worker 3대 public의 해당 허용 규칙이 없고 nft에 ens160/public reject 경로가 있다. 동시에 10250 리스너가 없으며 kubelet은 auto-restart 상태다. 포트 개방만으로 복구 완료를 예고하지 않는다.
- Worker→LB 6443은 TCP 연결 3/3 성공. API/TLS/Auth·LB 전체 backend 정상 판정은 별도다.
- 6대 chrony.conf 해시는 동일하고 source는 같은 pool이다. 선택 서버는 CP 3대 동일 + Worker별 3종으로 총 4종. Leap Normal 6/6이며 고정 NTP 승인 증적은 여전히 없다.
- Worker 3대 public에 cockpit 서비스와 nft TCP 9090 accept가 남아 있다. 현재 9090 listener는 없으나 socket/service의 mask 증거는 미수집. #10 Gate 0 확인, #14 불필요 허용 정리, #19 보안 인계로 매핑한다.
- v1.1 supplement로 6대 시간 속성과 Worker kubelet journal·설정 경로 메타데이터·Cockpit unit 상태만 보완한다. PR #2 소스 및 리뷰 요청은 유지한다.

## 순서와 담당

| 순서 | 잔여 작업 | 담당/티켓 | 시작 조건 · 완료 증적 |
|---|---|---|---|
| 병행 1 | Worker 3대 firewall/listener와 6대 NTP, CP→Worker·Worker→LB 수집 | #14 이권욱 수집·정리 / #10 이하영 검토 | v1.0 실서버 수집·업로드 및 무결성 검토 완료. 시간 속성 공백과 Worker kubelet/Cockpit 상세는 v1.1 supplement로 보완 |
| 병행 2 | PR #2 고정 HEAD 독립 리뷰 | 이하영 / infra-kubernetes PR #2 | 정책 최소 범위, 기존 관리 경로 보존, CP1 journal·부분 실패·복구·15분 입력 조건 검토. 동일 HEAD의 리뷰 결과 필요 |
| 다음 1 | CP1 canary 적용·검증 | #14 이권욱 / #10 이하영 연계 | PR #2 리뷰 지적 해결 후 적용 창에서 inspect 갱신. 적용 영수증·runtime/permanent·관리 SSH·API/etcd·TCP 결과 확보 |
| 다음 2 | CP2/CP3 단계별 통신 보완 및 join 재개 판단 | #14 이권욱 통신 / #10 이하영 Kubernetes | CP1 canary 검증 후 CP2→CP3 순서의 별도 적용·복구 구현/검토. 현재 CP1 전용 도구로 범위를 임의 확대하지 않음. CP2 잔여 manifest와 기존 etcd membership을 대조한 재개 절차 필요 |
| 다음 3 | Worker IF-12B 보완 및 join/상태 검증 | #14 이권욱 통신 / #10 이하영 Kubernetes | Worker 수집 결과에서 source/zone/필요 규칙 확정, 검토 후 단계 적용. 6/6 Ready와 etcd 3 member health는 실제 증적으로 판정 |
| 병행 결정 | 승인된 NTP 기본·대체 목적지와 UDP 123 경로 확정 | #14 이권욱 조정 / #10 이하영 동기화 기준 검토 | 아래 승인 표를 채운 후 별도 형상 변경. Done 상태 #65를 재개하지 않고 필요 시 infra-ansible 후속 PR·티켓으로 연결 |
| 후속 | Calico HEP/GNP 보호 인계 | #14 / #19 보안 / #10 Kubernetes | 6대 NTP 기준 충족, 필요한 통신 정상화, 정책·Positive/Negative·관리 경로·rollback/reboot 검증 후 단계 인계 |

고정 NTP는 Calico 전환 전 해결할 조건이다. 현재까지 승인 endpoint 증적은 확보되지 않았으며 CP에서 관측한 공인 IP를 승인값으로 채우지 않는다. CP 직접 6443 추가 필요 여부도 정본 Node→LB→CP 경로와 분리하여 #10 판단을 #14로 연결한다.

## NTP 결정 기록 — 미정 항목

| 필드 | 현 상태 / 필요한 기록 |
|---|---|
| 기본 서버 | 미정: 관리 주체, 승인 FQDN/IP, 내부/외부 구분 |
| 대체 서버·장애 동작 | 미정: fallback 목록, source 수·선택 정책, 무작위 pool/DHCP source 유지 여부 |
| 통신 | 6대→승인 목적지 UDP 123 및 응답 경로. DNS 사용 시 resolver 경로도 연결. 임의의 전 구간 inbound 123 개방을 전제하지 않음 |
| 인수 기준 | Leap/동기화 정상, offset·stratum·reach·관측 기간의 구체 기준을 운영 담당이 기록 |
| 검토·승인 근거 | 결정자·검토자·시각·티켓/PR 링크, 필요 시 기준 문서 버전 |
| 적용·복구 | 변경 전 설정 해시, 단계별 적용·검증, 이전 source 복구 절차와 결과 |

## 유지할 미완료 항목

- #14 전체 ICR-AC-01~20은 완료되지 않았다. 위 CP/Worker 범위 밖 IF-06B·IF-16 등은 기존 티켓의 최신 상태를 따르며 이 작업으로 상태를 재판정하지 않는다.
- HEP/GNP, Negative test, Reboot/Rollback, 보호 인계와 독립 증적 리뷰는 실제 결과가 있어야 완료 처리한다.
- baseline v3.1.18 준비 및 다른 담당자의 변경과 병합 범위를 혼합하지 않는다. 본 수집기는 승인된 기존 Inventory pin을 검사한다.
- PR #1 초기 관리 문서와 PR #2 소스 HEAD를 수정하지 않는 독립 브랜치에서 이 도구를 관리한다.
