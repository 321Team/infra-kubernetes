# 기존 ICR 반영용 변경 문안

상태: **리드 전달안 / 승인 기록 미제공**. 신규 티켓 번호를 임의 발행하거나 기존 정본을 변경한 문서가 아니다. 기존 ICR-D-NET-01-01 및 P1-K8S-01에 아래 정확한 범위의 변경을 연결한다.

## 정본 §6.6 전환 순서 교체 제안

CP 3대와 Worker 3대의 Host 보호는 Calico named HostEndpoint 및 GlobalNetworkPolicy로 통일한다. 전환 과정에서는 필요한 Bootstrap 통신과 Source 제한을 검증한 firewalld 보호를 유지한 상태로 Calico 설치·6대 join·Ready 및 회귀 검증을 완료한다. 허용 GNP를 선행 적용한 후 Worker1→Worker2→Worker3→CP1→CP2→CP3 순서로 해당 노드의 유효 failsafe를 정합화하고 HEP를 활성화한다. 각 노드에서 HEP 집행·Positive/Negative/Regression을 확인한 뒤 firewalld를 비활성화하며, 해당 노드의 재부팅 지속성 확인이 완료된 후 다음 노드로 진행한다. 일시 공존이 실패하면 보호 없이 진행하지 않고 중단한다. 복구는 검증된 이전 보호 또는 승인된 대체 보호를 먼저 복원한 뒤 해당 HEP·node별 failsafe를 되돌린다.

## 변경과 인수 범위

| 항목 | 반영 내용 | 책임·증적 |
|---|---|---|
| 전환 시점 | 기존 설치 전 OFF 문구를 위 보호 인계 순서로 교체 | P1-K8S 리드·ICR 승인자, 공존/실패/복구 검증 |
| Host Port Matrix | IF-07/10/11A/12/12A/12B/12C/13/15의 역할·Source·목적지 제한 | 정책 SHA·flow matrix·유효 failsafe |
| System API 실제 경로 | LB 경로 이외에 필요한 DNAT 후 CP/Workload API 경로를 관측 후 승인 | Consumer·Source·Destination·protocol/port·인증·허용 근거 |
| NTP·이미지 공급 | 실제 chrony endpoint, OID-02/03 공급 방식·Owner·검토일·종료조건 확정 | 사전반입 digest 또는 승인 registry endpoint·CA Trust |
| 전달 트래픽 | Bootstrap Host 정책의 applyOnForward=false 경계 유지 | P1-SEC/IF-02/HO-ANS-04C 인수 전 외부 노출 금지 |
| Cockpit·AVC | Gate 0 증적 인수, 차단 후 재부팅 확인, 구간 기록 보존 | unit별 상태·9090·AVC 로그·boot ID |

## 승인 기록에 채울 값

정확한 변경본/Commit 및 정책 해시, 검토자·승인자·일시, 적용 대상 노드, 작업 시도 ID, 사전 보호와 대체 CP API 경로, NTP·이미지 공급 결정, 임시 정책 Owner·검토/종료 시점, P1-SEC·P1-HBR·P1-LB 인수 기준을 기록한다. 이 문안의 작성일을 승인일로 사용하지 않는다.

Harbor 구축 미완료를 이유로 IF-10 정책을 누락하지 않는다. CP/System Harbor 사용은 IF-10의 Worker 범위를 확대하는 별도 결정이다. DNS 서버의 역사적 Done 기록과 현재 실측 Drift를 구분한다. 기존 Baseline의 Design FROZEN과 Runtime PASS 구분을 유지한다.
