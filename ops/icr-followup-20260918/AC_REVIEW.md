# ICR AC 대조 및 3CP 이후 실행 인계 — 2026-09-18

검토 기준: 2026-09-18 13:04 KST 추가 현장 수집본과 GitHub 기록 대조. 검토자는 제출 증적을 검증했으며 별도 서버 재시험을 실행하지 않았다.

## 적용 기준과 실제 완료

현재 발행 정본은 [docs-v3.1.19](https://github.com/321Team/fds-project-baseline/releases/tag/docs-v3.1.19), commit `966fc5365874b1d259a9810fc876b57c3bfa0e08`이며 2026-09-18 09:20:13 KST 발행됐다. [ICR AC 원문](https://github.com/321Team/fds-project-baseline/blob/966fc5365874b1d259a9810fc876b57c3bfa0e08/tickets/v3.1.19/descriptions/ICR-D-NET-01-01.md)의 조건 20개는 v18과 동일하다. 문서 안 당시 CANDIDATE/NOT RELEASED 상태와 9/15 실패 기록은 보존된 스냅샷이며 현재 운영 상태로 적용하지 않는다.

[최신 Join 실측 검증](https://github.com/321Team/infra-kubernetes/blob/5e9e09e7a1820c230bb5724ce05897c7689ef065/ops/cp-join-20260918/results/20260918T033022Z_16219c68779b3d13/README.md)에서 다음 구현 완료가 확인됐다.

- CP2 known manifest 3개 백업·격리 후 CP2/CP3 Join 각각 rc=0.
- 2026-09-18 12:33:36.720 KST CP1·CP2·CP3 Ready, etcd voter/health 3/3.
- CP1 Job `c564ae97530dfe80`의 Runtime 유지 인계, 기존 15:15 회수 timer 중지, 인계 전후 Runtime SHA 동일.
- Join Job `16219c68779b3d13`의 임시 인증정보 회수 결과 `REVOKED`.
- archive SHA256 `3108ecacc750a8b830e31f558c2cf363f00ea877beed46759389c7d610ba3262`, 내부 15/15 및 승인 실행 소스 SHA 동일.

정책·소스 승인과 [운영자 실행 결정](https://github.com/321Team/infra-kubernetes/pull/16#issuecomment-5724697399)에 따라 성공한 3CP와 Runtime을 유지한다. [통합 사후 결과 리뷰](https://github.com/321Team/infra-kubernetes/pull/16#issuecomment-5724813354)는 PENDING이다. 같은 승인을 다시 요청하거나 완료한 CP Join·CP1 apply/5-flow·복구 자산 확인을 반복하지 않는다.

## AC별 판정

현재 증거로 조건 충족을 확인한 AC는 1/20(AC-14 Runtime PASS)이며 PARTIAL 5, NOT_VERIFIED 12, BLOCKED 1, 마지막 확인 FAIL/DRIFT 1이다. 전체 독립 인수 완료를 뜻하지 않는다. 이는 ICR 최종 인수조건의 수이며 구축 공수나 3CP 구현 진행률이 아니다. 이번 3CP 성공을 완료 실적으로 따로 기록한다.

| AC | 정본 조건 | 현재 판정·남은 증거 |
|---|---|---|
| ICR-AC-01 | 6개 Node 변경 전 firewall/listener/route 설정과 SHA Evidence를 확보한다. | **PARTIAL** — 기존 6대 방화벽·리스너와 Worker 3대 route 증거 재사용. 확인한 CP2/3 resume·Join 증적에는 route 수집이 없어 최초 변경 전 CP route 원본 연결이 남음. 새 조회를 과거 증거로 대체하지 않음. |
| ICR-AC-02 | firewalld가 disabled/inactive이고 Calico Pod/Node 통신이 정상이다. | **NOT_VERIFIED** — CP1 최신 5/5 흐름 및 HOST/외부 회귀 PASS, CP2/3 Runtime·Join 성공. firewalld는 유지 중이며 6대 최종 HEP/GNP 전환과 전체 Calico 통신 인수는 미완료. 과거 3/5 실패를 현재 결과로 사용하지 않음. |
| ICR-AC-03 | named HEP/`expectedIPs`/Bootstrap Allow가 승인안과 일치한다. | **NOT_VERIFIED** — named HEP/expectedIPs/Bootstrap Allow 최종 적용·보호 인계 미실시. 6/6 Ready 후의 기존 실행 조건 유지. |
| ICR-AC-04 | `.50 → Worker:30080`은 허용되고 비승인 Source는 차단된다. | **NOT_VERIFIED** — 승인 Source .50의 30080 허용 및 비승인 Source 차단 증거 필요. |
| ICR-AC-05 | Prometheus에서 Worker node-exporter 9100 Target이 `UP`이다. | **NOT_VERIFIED** — Prometheus에서 Worker 3대 node-exporter Target UP 증거 필요. |
| ICR-AC-06 | Prometheus에서 Worker kubelet 10250을 CA+SA로 인증한다. | **NOT_VERIFIED** — Prometheus의 CA+SA 인증과 Worker 3대 kubelet 접근 증거 필요. |
| ICR-AC-07 | 비승인 Workload의 9100/10250 접근이 차단된다. | **NOT_VERIFIED** — 비승인 Workload의 9100/10250 차단 시험 필요. |
| ICR-AC-08 | Control Plane의 logs/exec/port-forward가 정상이다. | **PARTIAL** — 기존 CP1 logs/exec/port-forward 성공 증거 재사용. 이번 API Ready·etcd health는 전체 CP의 해당 기능 회귀를 대체하지 않음. |
| ICR-AC-09 | Worker에서 Harbor `.54:443` Pull과 Pod 시작이 성공한다. | **NOT_VERIFIED** — Worker 3대 Harbor Pull과 실제 Pod 시작 증거 필요. |
| ICR-AC-10 | Cockpit 9090이 노출되지 않는다. | **PARTIAL** — 기존 Worker 3대 Cockpit masked/inactive·9090 listener 부재 재사용. 전체 범위 비노출 인수와 잔여 허용 정리 미완료. |
| ICR-AC-11 | monitor01에서 Cluster Workload로 신규 Ingress를 시작할 수 없다. | **NOT_VERIFIED** — monitor01의 Cluster Workload 신규 Ingress 차단 증거 필요. |
| ICR-AC-12 | IF-06B Remote Write의 TLS/Auth/Payload Allowlist가 충족되고 비승인 Egress가 차단된다. | **BLOCKED** — IF-06B Receiver/TLS/Auth/Payload Allowlist·비승인 Egress 및 실제 Source/Return Path 인수 미완료. |
| ICR-AC-13 | edge/harbor/db에 `10.250.10.0/24 via .58`가 적용되고 `.30` route-get이 통과한다. | **FAIL_LAST_OBSERVED_DRIFT** — 마지막 확인은 edge/harbor/db route DRIFT. 신규 시정 증거가 없어 해소로 올리지 않음. 현시각 서버를 재조회한 결과는 아님. |
| ICR-AC-14 | Worker 3대에서 동일 경로 조회 회귀가 통과한다. | **RUNTIME_PASS** — 2026-09-18 13:04 KST Worker01/02/03에서 monitor01(.30) route-get rc0, source .105/.107/.108 및 gateway .58 일치. AC 원문 범위 충족. 최종 보호 전환 후에만 인정한다는 추가 조건을 제거. 이후 관련 변경의 회귀와 전체 독립 인수는 별도. |
| ICR-AC-15 | mgmt-gw IP Forward/Zone/Policy/NAT가 승인안과 일치하고 내부 SNAT가 없다. | **NOT_VERIFIED** — mgmt-gw Forward/Zone/Policy/NAT와 내부 Source 보존 인수 필요. |
| ICR-AC-16 | monitor01 `.30:443` Receiver/Protocol/TLS/Auth가 정본과 일치한다. | **NOT_VERIFIED** — monitor01 Receiver/Protocol/TLS/Auth 실제 구성 및 인수 필요. |
| ICR-AC-17 | 패킷 관찰의 실제 Source와 양방향 Return Path가 정본과 일치한다. | **NOT_VERIFIED** — 실제 허용 흐름의 패킷 Source와 양방향 반환 경로 확인 필요. |
| ICR-AC-18 | Reboot 후 정책이 유지되고 재시험이 통과한다. | **NOT_VERIFIED** — 최종 보호·경로 정책의 Reboot 지속성 미검증. 현재 CP Runtime 유지나 백업 준비 중 재기동은 대체 증거가 아님. |
| ICR-AC-19 | Rollback을 재현할 수 있다. | **PARTIAL** — 기존 CP1 실제 Runtime rollback·baseline 복구 증거 재사용. 최신 Join은 성공하여 rollback 호출 없음. 전체 ICR 변경의 복구 재현성은 남음. 90분 제한은 이 AC의 요구가 아님. |
| ICR-AC-20 | Evidence에 평문 비밀정보가 없다. | **PARTIAL** — 기존 마스킹 보완과 제출본별 검증 재사용. 최신 Join archive 외부 SHA·내부15/15·승인 실행 소스 일치 및 공유본 비밀정보 검사 완료. 전체 최종 Evidence 종결은 미완료. |

AC-01의 미확인 과거 route 원본은 이미 제출한 자료에서 연결할 항목이다. CP2/3 resume 및 최신 Join BEFORE를 확인했으나 route 자료는 없었다. 모든 기존 저장소 자료에 원본이 없다고 단정하지 않으며, 현재 route 조회를 최초 변경 전 자료로 소급하지 않는다.

## 절차를 줄이는 적용 원칙

[정본에 편입된 단순화 입력](https://github.com/321Team/fds-project-baseline/blob/966fc5365874b1d259a9810fc876b57c3bfa0e08/audit/v3.1.19/intake/icr-cni-simplification-20260916.md)은 기존 5-flow·적용/복구 증적 재사용, 도구 qualification과 운영 보완 분리, 같은 완료 증적의 반복 수집 방지를 명시한다.

- 전체 ICR AC 완료를 CP·Worker 준비/가입의 추가 전제로 부과하지 않는다.
- AC-18은 최종 정책의 재기동 지속성, AC-19는 복구 재현성을 요구한다. 둘 다 고정 90분 타이머를 요구하지 않는다.
- 이번 성공 Join의 복구 미호출(`recovery_complete=null`)은 실패가 아니다. 기존 실제 rollback 근거를 보존하고, 아직 시험하지 않은 전체 ICR 복구를 PASS로 확대하지 않는다.
- 과거 CP1 frozen source나 이전 receipt를 바꾸거나 이전 자동 회수를 재실행하지 않는다. 실제 후속 변경이 필요하면 현재 3CP·인계 상태를 입력으로 사용한다.

## 후속 실행 순서

| 작업 묶음 | 담당·진입 조건 | 실행과 종료 판단 |
|---|---|---|
| Worker 및 LB 후속 | K8S #10 / LB #9. 3CP 구축 완료를 입력으로 즉시 준비 | 필요한 현재 Node/Calico·Worker 서비스·LB TLS 인증 경로만 한 번 확인. Worker 미가입/NotReady/Ready를 구분하고 정확한 현재 상태에 맞춰 Worker 3대 가입·회귀를 진행. LB backend 3/3·장애복구는 LB 기존 Stage B 절차로 별도 인수 |
| ICR 경로 후속 | ICR #14, Worker 작업과 병행 | IF-16 edge/harbor/db route DRIFT의 현재 구성·소유자·변경 범위를 연결하여 시정. IF-06B Receiver/TLS/Auth/Source 입력을 Monitoring 담당과 연결 |
| 최종 보호 전환·통신 시험 | [기존 Lead 결정](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)의 3CP+3Worker Ready / 6/6 이후 | named HEP/GNP/Bootstrap 보호 및 승인 Source/거부 흐름을 같은 변경 묶음으로 검증. firewalld OFF는 그 보호 인계 범위에서 수행 |
| ICR 종결 | 해당 변경 증거가 모인 뒤 | 최종 지속성·복구·비밀정보 검사·Evidence Index를 남은 범위에만 보완하고 통합 독립 인수 및 Lead 최종 판정 |

현재 PR #7 HEP/GNP/SEC 보완과 NTP upstream 입력은 각 기존 검토 범위로 남긴다. 이를 Worker 준비의 새 선행조건으로 추가하지 않는다. 구 Worker 실행기는 과거 CP1 receipt/인수 계약을 사용하므로 현재 Runtime에 그대로 재실행하지 않는다.

## 현장 현재성 확인의 정확한 범위

[RUN_ICR_NEXT.py](RUN_ICR_NEXT.py)는 위 첫 작업의 미확인 현재 상태를 한 번 묶는 운영 보조 파일이다. CP1과 Worker 3대 조회 및 기존 CP1 자격증명을 통한 LB API 조회만 수행한다. Node 구성 변경, Join/reset, 방화벽·HEP·서비스·타이머·NTP 변경 기능은 없다.

이 파일은 PR #16 승인 실행 소스를 변경하거나 그 코드 승인 범위에 포함됐다고 주장하지 않는다. 코드의 정적/로컬 검증 결과와 현장 실행 결과를 분리한다. 현장 제출본에서 조회4대 완료, Worker3 미가입과 LB 인증 성공을 확인했다. [13:04 실측과 AC-14 판정](results/20260918T040403Z_zgby6ckp/README.md)을 후속 입력으로 사용한다. 이 환경에는 현장 SSH 키가 없으며 Worker 변경·Join은 실행하지 않았다.


## AC-14 판정 정정

이전 안내의 “최종 보호 전환 후에만 인정”은 정본 AC-14에 없는 추가 조건이었다. 이번 Worker3대 route-get 증거로 현재 AC-14 Runtime PASS를 기록한다. 같은 경로를 다시 수집하도록 요구하지 않는다. AC-13(edge/harbor/db), AC-15(NAT), AC-17(패킷 관찰), AC-18(지속성)과 전체 독립 인수는 이 결과로 완료하지 않는다.
