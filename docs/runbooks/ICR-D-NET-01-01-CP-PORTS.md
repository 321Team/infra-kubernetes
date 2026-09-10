# ICR-D-NET-01-01 CP 포트 보완 실행·복구 절차

상태: **검토용 실행 절차 / 실서버 미실행**. 연결: [변경안](../change/ICR-D-NET-01-01/CP_FIREWALL_START.md).

이 절차의 범위는 CP 3대 public zone에서 승인된 CP Source /32의 etcd 2379–2380과 kubelet 10250 허용을 보완하는 것이다. join/reset·NTP 설정·HEP/GNP·firewalld OFF는 별도 작업이다.

## 1. 고정 입력과 변경 전 확인

| 입력 | 기준 |
|---|---|
| 소스 | PR에서 검토한 infra-kubernetes 전체 HEAD |
| 관리 제어 서버 | ansible01 10.250.10.10 / server01_ansible |
| 원격 계정 | 기존 svc_ansible 및 검증된 sudo |
| Inventory | infra-ansible `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`의 기존 Inventory |
| 초기 증적 | FDS_CP_Log_Collection_glrr0dsq.tar.gz, SHA-256 `265e7d3fe04c8c215b8c76847bac769c5445682d788c3f9ad2bf9afef100c594` |
| 변경안 | cp-firewall-plan-20260910.json, SHA-256 `2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7` |
| 실행 입력 | 대상 노드, 고정 HEAD/계획 해시, 변경창, 실행자, 독립 리뷰·실행 결정의 실제 기록 |

기존 승인 키 `/home/server01_ansible/.ssh/p1_ansible_ed25519`를 명시하고 host key 검증을 유지한다. 최근 CP 수집에서 성공한 transport 설정을 재사용한다. 키·authorized_keys·known_hosts·sudoers 변경을 이 포트 작업에 섞지 않는다.

각 노드에서 적용 직전 hostname/IP, ens160의 zone, firewalld 상태, public Runtime/Permanent 전체 설정을 다시 확인한다. 계획의 `before`와 다른 경우 오래된 계획으로 실행하지 않고 변경 차이를 먼저 반영한다. 계획의 `before_sha256`은 원래 증적 스냅샷의 해시이며 실시간 동등성을 자동 검증하는 기능은 아니다.

NetworkManager 활성 connection의 zone도 확인한다. Permanent 출력에 interfaces가 비어 있다는 이유만으로 zone을 임의 지정하지 않는다. 실제 connection zone 또는 default zone이 재부팅 후에도 public으로 귀결되는지 확인한다. 현재 관리 접속과 콘솔 복구 경로를 유지하고, 같은 변경창에 다른 방화벽 편집을 병행하지 않는다.

변경 전 보관 항목: UTC 시각, hostname/IP, active/default zone, NM 연결/zone, Runtime/Permanent 설정, nft ruleset, 필요한 리스너, CP1 API readiness/etcd 현재 상태, 기존 관리 SSH 연결 결과. 민감정보를 제외한 증적의 SHA-256을 기록한다.

## 2. 계획 재현 — 오프라인

저장소 루트에서 실행한다. 입력은 이미 검토된 두 파일이며 새로운 출력 파일 이름을 사용한다.

```bash
python3 scripts/build-cp-firewall-plan.py \
  --policy policies/icr-d-net-01-01/cp-firewall-v1.json \
  --archive /path/to/FDS_CP_Log_Collection_glrr0dsq.tar.gz \
  --sidecar /path/to/FDS_CP_Log_Collection_glrr0dsq.tar.gz.sha256 \
  --out /path/to/new-cp-firewall-plan.json
```

정상 결과는 `DRAFT_PLAN_REVIEW_REQUIRED`, 추가 후보 36건, 위 계획 해시, `RUNTIME_APPLIED=false`다. 새 수집본을 사용하려면 정책·생성기의 증적 고정값을 해당 증적 검토와 함께 변경한다. 기존 해시를 임의 입력으로 우회하지 않는다.

## 3. CP1 canary 적용과 작업 기록

첫 대상은 k8s-control01(10.1.93.102)이다. 승인된 계획의 해당 노드 `operations.permanent`, 이어서 `operations.runtime` 순서로 처리한다. 노드별 각 설정에 필요한 rich rule은 다음 6개다.

| Source | 목적지 노드 | TCP 포트 |
|---|---|---|
| 10.1.93.102/32 | 해당 CP | 2379–2380 |
| 10.1.93.102/32 | 해당 CP | 10250 |
| 10.1.93.103/32 | 해당 CP | 2379–2380 |
| 10.1.93.103/32 | 해당 CP | 10250 |
| 10.1.93.104/32 | 해당 CP | 2379–2380 |
| 10.1.93.104/32 | 해당 CP | 10250 |

계획에 담긴 인자의 예는 다음과 같다. 이 예시 한 건이 전체 적용을 대신하지 않는다.

```text
firewall-cmd --permanent --zone=public --query-rich-rule='rule family="ipv4" source address="10.1.93.103/32" port port="2379-2380" protocol="tcp" accept'
firewall-cmd --permanent --zone=public --add-rich-rule='rule family="ipv4" source address="10.1.93.103/32" port port="2379-2380" protocol="tcp" accept'
```

각 후보의 적용 순서:

1. 쓰기 전에 node/plane/rule과 `query_argv` 결과를 기록한다. query rc=0이면 이미 존재하므로 보존하고 추가하지 않는다. rc=1이면 부재다. 다른 반환 코드는 조회 오류로 중단한다.
2. 실제 부재인 규칙만 작업 기록에 추가 예정으로 먼저 남긴 뒤 정확한 `add_argv`를 실행한다. `eval`이나 임의 Source/Port 치환을 사용하지 않는다.
3. 반환 코드·시각과 추가 후 query 결과를 저장한다. 처음에는 없었고 add가 성공했으며 query가 존재를 확인한 항목만 `added_by_this_run=true`로 기록한다.
4. 실패 또는 응답 불명확이면 뒤의 규칙·노드로 진행하지 않는다. 현재 상태를 재조회해 결과가 불명확한 항목을 남긴다. 성공 여부가 불명확한 항목을 일괄 삭제하지 않는다.

Runtime과 Permanent를 각각 명시적으로 추가하므로 전체 `--reload`나 `--runtime-to-permanent`가 필요하지 않다. 전체 재적재/전체 복사는 관련 없는 규칙의 상태까지 바꿀 수 있어 이 절차에서 사용하지 않는다. 2시간 timeout을 사용하지 않으며 기존 SSH/LB 허용을 삭제하지 않는다. [firewalld 공식 문서](https://firewalld.org/documentation/man-pages/firewall-cmd.html)

## 4. CP1 검증과 다음 노드

CP1 적용 후 다음 결과를 확보한다.

- Runtime/Permanent의 대상 규칙 6개씩 존재, 기존 SSH 서비스·LB rich rule 보존, 새 관리 SSH 접속 성공.
- CP2·CP3에서 CP1의 2379/2380 TCP 도달성. 현재 CP1에는 두 포트 리스너가 있다는 증적이 있으므로 canary의 유효한 목적지다.
- CP1 API readiness와 etcd 상태의 변경 전후 비교. TCP 연결 성공만으로 TLS/etcd 건강 상태를 대체하지 않는다.
- CP→CP1:10250의 승인된 CA/Auth와 logs/exec 등 기능 검증. 비승인 Source 검증은 기존 승인된 시험 호스트에서 수행한다.

CP 직접 6443이 계속 막히는 경우 이 계획에 임의로 포트를 더하지 않는다. #10에서 실제 필요한 API 경로를 확인해 #14의 변경 범위를 갱신한다.

CP1 canary가 통과하면 CP2, CP3 순서로 같은 적용 기록 절차를 수행한다. CP2·CP3는 현재 etcd/API/kubelet 리스너가 없으므로 가입 전 모든 목적지 TCP 시험이 성공할 것을 요구하지 않는다. 규칙 상태와 관리 접속을 확인하고, 서비스 리스너가 생성된 뒤 해당 Positive/Negative 및 기능 시험을 마무리한다.

**CP2 join 재개 전에는 #10에서 etcd member list/status와 CP2 잔여 구성을 확인한다.** 이미 남아 있는 매니페스트나 etcd 멤버를 검사 없이 삭제하거나, preflight 오류를 무시하고 join을 반복하지 않는다. 포트 보완 완료와 join 재개 결정은 각각 기록한다.

## 5. 중단·복구와 보호 인계

중단 조건은 관리 접속 상실, 현재 identity/zone 불일치, 다른 주체의 동시 규칙 변경, API/etcd 상태 악화, 조회/추가의 예상 밖 반환 코드다. 뒤의 노드로 확장하지 않고 마지막 성공 단계와 현재 Runtime/Permanent 상태를 보관한다.

복구 시 이번 실행의 작업 기록에서 `added_by_this_run=true`인 규칙만 검토한다. 원래 존재한 규칙은 삭제하지 않는다. 불명확한 작업 결과는 별도 조사하고 자동 회수 대상으로 삼지 않는다. 계획에 `rollback_argv`가 있다는 사실만으로 해당 add의 성공이 증명되는 것은 아니다.

join 이후 새 etcd 멤버가 해당 경로에 의존할 수 있다. 따라서 추가 규칙의 삭제가 클러스터 가용성을 악화시키지 않는지, 기존 보호 경로 또는 승인된 복구 상태로 돌아갈 수 있는지 먼저 확인한다. 회수가 결정된 항목은 성공한 추가 순서의 역순으로 해당 plane의 정확한 remove 인자만 실행하고 관리/API/etcd 상태를 재검증한다. firewalld 전체 중지, zone 통째 복원, 전체 규칙 flush는 이 복구의 수단이 아니다.

최종 Calico 인계에서는 필수 GNP와 named HEP, 실제 Source, Positive/Negative, canary 및 복구 경로를 검증한 후 firewalld 역할을 넘긴다. 임시 보완 규칙의 Permanent 잔존·서비스 재활성화 시 영향까지 인계 기록에 포함한다. CP1만 Ready인 현재 상태에서 HA 검증을 위한 임의 재부팅을 하지 않는다. 3CP가 안정화된 뒤 승인된 순서로 reboot 지속성 시험을 수행한다.

## 6. 실행 결과의 필수 항목

실행별 기록에는 고정 HEAD/계획 해시, 노드별 전후 상태 해시, 각 rule/plane의 query/add 결과와 소유 여부, 실패·불명확 항목, canary/기능/Negative 결과, 복구 또는 유지 결정, 생성한 증적의 SHA-256을 포함한다. 비밀키·kubeconfig 본문·토큰은 저장하지 않는다.

완료 판정은 본 CP 범위의 필수 통신과 보존·복구 증적에 한정한다. Worker, NTP, HEP/GNP, IF-06B/IF-16 등 #14 전체 AC는 각 후속 증적과 독립 리뷰를 확보해야 한다.
