# 고정 NTP 결정안 및 후속 형상 변경

2026-09-10 / 상태: **결정안 준비, endpoint 승인 미확보, 미적용**.

기술 근거: Worker full collection 15:11 KST와 supplement 15:34 KST. 6대 chrony.conf SHA `147346c1b876b87dfad3bd436c62bc137c36723137ed5b647cf376a9f54e66aa`가 같고 `pool 2.centos.pool.ntp.org` 및 `/run/chrony-dhcp` sourcedir를 사용한다. 선택된 공인 IP는 총 4종이며 CP 3대는 같은 IP였다. 6대 Leap Normal, 보완 결과 NTPSynchronized=yes다. 이 정상 동기화 관측은 고정 endpoint 승인을 대신하지 않는다.

## 담당과 결정 표

| 항목 | 제안 / 현재 상태 | 담당 연결 |
|---|---|---|
| 운영 고정 source | 조직에서 관리·승인한 내부 source가 있으면 우선 검토. 관리 가능한 내부 source가 없으면 승인된 외부 FQDN/IP 목록을 별도 확정 | #14 이권욱 조정, 네트워크/시간 서비스 운영 주체 확인 |
| 기본·대체 endpoint | 모두 미정. 기존 관측 IP를 승인값으로 전용하지 않음 | #14에서 승인 근거 취합 |
| Kubernetes 동기화 인수 | 아래 수치 초안의 수용/수정 결정 필요 | #10 이하영 |
| 보안 통신 인계 | 6대→승인 목적지 UDP 123와 응답 경로, FQDN이면 DNS 및 주소 변경 관리도 검토 | #14, #19 보안 인계 |
| 형상 반영 | 결정 후 infra-ansible의 별도 후속 PR로 설정 변경·검증·복구 작성 | 기존 Done #65는 유지, 새 작업과 연결 |

| 승인 기록 필드 | 입력할 내용 |
|---|---|
| 운영 주체·장애 연락 경로 | 미정 |
| 기본 source | 승인 FQDN/IP, 내부/외부, 근거 문서 |
| 대체 source와 선택 정책 | 승인 목록, 장애 시 선택/복구 정책, 가용성 조건 |
| pool/DHCP source 처리 | 승인되지 않은 동적 source를 제외할지 명시. `/run` 생성 파일 직접 편집을 영구 설정으로 간주하지 않음 |
| ACL | 출발 노드, 목적지, UDP 123, 응답/상태 추적, DNS가 필요한 경우 resolver |
| 인수 기준 | offset/stratum/reach/관측 기간 및 실패·복구 조건 |
| 승인 | 결정자·검토자·시각·티켓/PR, 설정 버전 |

## 수용 여부를 결정할 수치 초안

운영 기준으로 아직 승인된 값은 아니다. 초안은 6/6 노드에서 chronyd active, Leap Normal, NTPSynchronized=yes, 선택 source가 승인 목록 안에 있는 상태를 요구한다. 초기 안정화 후 60초 간격 6회(5분) 관측에서 `tracking` Last offset 절댓값 ≤100 ms, Stratum 1~15, 선택 source Reach가 0이 아님을 제안한다. source 교체 직후의 안정화 대기 시간, 정상 운영에서 요구할 reach 패턴, 더 엄격한 offset 기준은 #10 및 운영 주체가 확정한다. 노드별 offset으로 노드 간 실제 시차를 계산하지 않는다. 단순 UDP 포트 스캔 대신 chrony의 source/reach/응답 증적으로 통신을 검증한다.

## 결정 이후 실제 작업

1. 승인 표와 수치 기준을 고정하고 실제 설치 chrony 버전을 확인한다. 관련 include/confdir/sourcedir 및 DHCP source 생성 주체까지 조사한 설정 변경 diff를 준비한다.
2. 변경 전 config·서비스 상태·source·tracking·해시를 보존한다. 기존 source 파일의 복구본과 적용·복구 절차를 같이 검토한다.
3. 한 노드에 승인 source를 반영해 안정화·fallback·관리 경로를 확인한 뒤 나머지 노드로 진행한다. 시스템 시간을 임의 강제 step하는 명령을 본 제안의 자동 동작으로 넣지 않는다.
4. 6대 검증 증적과 endpoint 결정 기록을 #14→#10/#19에 연결한다. Calico 보호 인계 시 해당 egress/응답 정책을 반영하고 firewalld 전환과 대조한다.

공식 설정 근거: [chrony.conf](https://chrony-project.org/doc/4.6/chrony.conf.html), [chronyc](https://chrony-project.org/doc/4.6/chronyc.html). pool은 주소 집합을 사용하는 source 지정이므로 관측 당시 선택된 IP를 고정된 승인 서버로 취급하지 않는다.
