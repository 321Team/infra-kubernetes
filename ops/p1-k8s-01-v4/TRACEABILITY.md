# 리뷰 반영 추적표

기준: 추가 재리뷰 v3.0. 아래의 '반영'은 제출 문서·코드 수정 상태이며 Runtime PASS를 뜻하지 않는다.

| 기존 ID | 반영 내용 | 산출물 | 운영 시 확인 |
|---|---|---|---|
| G2-01 / R2 | SSH Source `.10/32` 고정, Node→Ansible destination22 삭제, 실제 Source 보존 확인 | render.py, RUNBOOK §1/4/7, T02/03 | 신규 SSH·도착 Source·경로 |
| G2-02 / R1 | GNP 선행, Worker1 이후 나머지 HEP도 한 대씩. node별 failsafe 후 HEP, 재부팅까지 완료 후 다음 노드 | cluster-change.py, RUNBOOK §6~8 | 기존 HEP/자동 HEP/Workload selector·tier 검토, 노드별 무영향 |
| G2-02 정본 정합성 | 설치 전 OFF와 새 순서의 충돌을 명시하고 교체 문구·공존·보호·복구 조건 제공 | ICR_ADDENDUM.md, RUNBOOK §3 | 기존 ICR 승인 기록 연결. 승인 없는 정본 변경 아님 |
| G2-03 / R2 | v3.32.1 legacy CRD v1 사용, 실제 API discovery·server dry-run·저장/의미 검증 분리 | render.py, cluster-change.py, tests/check-crd.py | 설치 API·Admission·Calico 집행 |
| G2-04 / R2 | 노드별 GNP로 역할/목적지 제한, explicit Deny, net 없는 failsafe 제거, global default 변경 삭제 | render.py, policy_templates, T01~06/T10/T15 | node 실제 유효 설정·override·conntrack·우선순위 |
| G2-05 / R2 | 승인 DNS UDP/TCP53, Worker Harbor443 포함, NTP 필수 입력, System API/이미지 경로 입력과 인수조건 | render.py, RUNBOOK §1/6/9, T09~13 | OID-02/03·NTP·API 경로 실측 및 승인 |
| G2-05 전달 경계 | host-only 범위, 외부 NodePort 등 인수 전 미개방, 임시 정책 Owner·종료 조건 | ICR_ADDENDUM.md, RUNBOOK §1.2/9 | P1-SEC/IF-02/HO-ANS-04C 인수 |
| G2-06 / R3 | 실제 규칙·카운터·Source 도착을 차단 증거와 연결, Worker3 포함 3 Pod·Service·DNS, API/etcd health | node-evidence.py, probe.py, regression.py, TEST_MATRIX | Runtime T01~20 |
| G2-06 새 복구 결함 | 모든 실패를 §10으로 통일. 보호 선복원·검증 후 HEP/node failsafe 복원. UID/RV·시도/config 바인딩 | cluster-change.py, node-evidence.py, RUNBOOK §10 | 콘솔/정상 CP loopback API, receipt 근거, 실제 복구 |
| G2-07 / R4 | UTC 날짜·시간 별도 argv, 노드별 시작/종료 파일·marker·boot ID 보존, RC1/오류 구분 | audit-window.py, RUNBOOK §4/8, T17 | 로그 연속성·auditd·시계·4종 이벤트 |
| G2-08 / R5 | 선행 조치 증적 있으면 check, 없으면 apply. 차단 후 현재 노드 재부팅→unit/9090 재검증 | cockpit.sh, RUNBOOK §4/8, T18 | Gate 0 적용 여부·재부팅 후 실제 상태 |
| A1 | Gate 0 진행/승인을 재판정하지 않고 실제 적용 증적과 연결 | RUNBOOK §4, README | 기존 증적 ID/해시 |
| A2 | code block 언어·번호·실행 위치·Source 정본 정리 | 전체 문서 | Notion 본문 중복 초안 삭제·교체 |
| 직접 의존 BGP | vxlan backend, IPIP/VXLAN/CIDR/NAT/MTU, BIRD readiness/liveness 수정, digest pinning | prepare-calico.py, RUNBOOK §5 | 설치 전/기설치 migration 구분, 실제 IPPool·경로 |

## 정보 부족으로 임의 확정하지 않은 값

실제 서버 자료가 필요한 값은 코드 입력과 실행 중단 조건으로 반영했다. 값을 비워둔 상태는 보완을 누락한 것이 아니라, 운영 설정을 추정해 확정하지 않기 위한 경계다. 리드는 실측 자료를 채운 후 생성된 정확한 YAML/JSON과 해시를 고정할 수 있다. 서버 접속·원격 변경·Notion/GitHub 기록 변경·타인에게 메시지 전송은 이번 작업에서 수행하지 않았다.
