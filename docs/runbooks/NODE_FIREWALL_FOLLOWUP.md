## 2026-09-18 CP 세션 위임 제안

새 소스/정책을 한 번에 승인한 경우 [CP 세션](CP_SESSION.md)이 CP1 시험 후 `CP_SESSION_ACTIVE_REVIEW_PENDING`을 확인하여 CP2→CP3 Runtime 방화벽을 순서대로 준비한다. 별도의 대상별 수작업 승인 JSON을 요구하지 않는다. source와 현재 상태·실제 결과는 계속 검증한다. `cp1_accepted=false`이며 이 실행 위임을 독립 인수나 Join 승인으로 표시하지 않는다. 기존 Job/legacy 경로는 아래 계약을 유지한다.

## 2026-09-17 CP1 작업창 v2 연계

CP1 최소 구성과 정본의 보호·포트 범위는 유지한다. [CP1 작업창 v2](CP1_MINIMAL_CHANGE.md)는 CP1 시험/인수 시간과 인수 후 CP2/CP3 유지 시간을 분리한다. 새 v2 작업에만 적용하며 과거 CP1 작업의 기한을 연장하지 않는다.

- `run-node-firewall-cni.py`는 실제 trial·v2 인수·CP1 root 유지 커밋이 일치하는 경우에만 `ACCEPTED_RETAINED`를 소비한다. CP1 전체 after-state와 최종 복구 timer가 확인돼야 한다.
- v2에서는 고정 900초 잔여시간 대신 **이번 대상의 승인 `window_end < CP1 retained_withdraw_at`**를 적용 직전에 검증한다. v1 경로의 900초 기준은 그대로다.
- 아래 기존 helper의 **노드별 평일 최대 2시간** 창은 변경하지 않았다. 이는 CP1 준비·리뷰·인수·CP2·CP3 전체를 2시간에 끝내라는 뜻이 아니다.
- 기존 PR #4 대상별 실행 승인 링크/입력, fresh inspect 15분 유효성, 대상별 직렬 실행, CP2 결과 검토 후 CP3, Join/reset 별도 판단을 유지한다. PR #11의 새 소스 리뷰만으로 PR #4 실행 승인이 생기지는 않는다.
- 소스·기간 정책·후속 조건은 한 번에 리뷰 요청할 수 있다. 현재 상태/새 대상에 필요한 검사만 실행 직전에 묶어 수집하고, 동일 복구 증적이나 완료된 자료 제출을 이유 없이 반복하지 않는다.

이 개정으로 실제 CP2/CP3/Join이 실행되거나 인수된 것은 아니다. Runtime 유지가 재부팅 후 지속되거나 정본 HEP/GNP 인계를 대체한다고 주장하지 않는다.

## 2026-09-16 후속 준비 및 인수 경계

[정본·후보 기반 단순화](../change/ICR-D-NET-01-01/CNI_SCOPE_SIMPLIFICATION_20260916.md)에 따라 CP1 운영 보완 준비와 PR #11 도구 qualification 후속을 분리한다. 이 절차의 실제 CP1 Runtime 인수·영수증·새 inspect/변경 창·복구 조건은 유지한다. 새 CNI 규칙을 추가한 상태는 원 receipt.after와 다를 수 있으며, 인수 JSON만 승인으로 바꿔서는 cp1_state_gate를 통과할 수 없다. 승인된 새 변경 상태의 증적/소비자 계약을 먼저 확정한다.

CP2/CP3 추가 범위는 CP /32→TCP2379–2380·10250이다. LB .109→CP TCP6443(IF-12)은 기존 규칙 보존이며 신규 추가 대상이 아니다. CP2 결과 검토 후 CP3로 진행하고 Join/reset은 별도 판단한다. Worker 준비·코드 검토는 병행하되 HEP/GNP 실제 적용은 [Lead 결정](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)에 따라 6/6 Ready 후 별도 승인/실행창에서 진행한다.

---

# Worker·CP2·CP3 방화벽 Runtime 후속 절차 v2

2026-09-10 / **코드 보완·독립 리뷰용, PR #4 실서버 미실행**. baseline #14 이권욱 주관, #10 이하영 Kubernetes 판단, #19 보호 인계와 연계한다.

## 변경 범위

| 대상 | public Runtime에서만 수행 | 최대 후보 수 |
|---|---|---:|
| Worker01·02·03 | CP 3개 /32→TCP 10250 추가, cockpit service 허용 제거 | 노드당 4 |
| CP2·CP3 | CP 3개 /32→TCP 2379–2380 및 10250 추가 | 노드당 6 |

이미 목표 상태인 항목과 Permanent, SSH·dhcpv6-client, CP의 LB→6443 예외, NM/public binding, SELinux Enforcing, Worker Cockpit mask/inactive를 보존한다. CP1 변경, join/reset, NTP 설정, Calico 정책 생성, firewalld 정지·reload·runtime-to-permanent는 수행하지 않는다.

실행 정책은 `policies/icr-d-net-01-01/node-firewall-v2.json`이다. Controller와 원격 helper가 내용을 고정 범위와 대조한다. 사용하지 않는 v1 정책 파일은 현재 배포본에서 제거했고 Git 이력에 보존한다. v2는 Runtime 인수 전 Permanent가 변경되던 문제를 제거한다. **Permanent 승격은 미구현이며 별도 검토·구현 대상**이다.

## 정본과 실행 조건

발행 정본 v3.1.18(72599ae49342b0fb8dbd3d8ee60314afb9f907bb) §6.6/6.6.1, D34, named HEP/GNP 보호 통일 및 6대 firewalld disabled/inactive 목표, 기존 AC/Done을 유지한다. 09-10 선행 준비 이력과 사용자 허용 범위는 [PR #2 선행 변경 이력](https://github.com/321Team/infra-kubernetes/blob/f0388b1a372d19b5079e94ec6949ef443d774d2f/docs/change/ICR-D-NET-01-01/ADVANCE_CHANGE_RECORD_20260910.md)을 따른다. CP 방화벽 보완 순서가 정본 HEP Bootstrap Allow→canary Worker→Worker 확대→CP 인계 순서를 대체하지 않는다.

1. CP1 실행 소스 `264b273ab8ad79209bc0fec4a85c4497c946dbe3`의 독립 리뷰와 CP1 Runtime 실제 적용·Positive/Negative/Regression 인수가 선행한다. onsite/inspect는 적용 영수증이 아니다.
2. 본 PR 새 HEAD의 이하영(hayeounglee) 독립 리뷰와 실제 보호·백업·콘솔·복구·대상 상태 판단이 필요하다. 승인 JSON은 이 판단을 입력에 묶는 기록이며 GitHub 승인의 진위를 인증하지 않는다. 실제 링크의 작성자·결정·검토 HEAD를 사람이 대조한다.
3. 평일 최대 2시간 창, 한 대상씩 진행한다. 다른 firewall/NM/Calico 변경, join/reset/member 변경, reload/reboot를 동결하고 직전 결과를 검토한 후 확대한다.
4. 실제 창에서 새 inspect를 수집하고 15분 이내 입력을 사용한다. 잔여 구성·CP1 상태 검토를 해당 해시에 연결한다.
5. CP2→CP3→Worker01→02→03 확대와 별도 join 창은 각각 실제 결과로 결정한다. join 전 Runtime 유지/회수와 보호 인계의 종료 계획을 정한다.

동일 controller 계정의 변경은 대상 간 공통 lock으로 직렬화한다. node-local lock과 미해결 journal 차단도 유지한다. 외부 관리자·다른 controller·D-Bus/nft까지 잠그는 분산 lock은 아니다.

인가 JSON의 신뢰 경계와 실제 승인 대조는 [인가 기록 모델](NODE_AUTHORIZATION_MODEL.md)을 따른다.

## 배포본 확인과 inspect

외부 SHA-256을 대조해 새 디렉터리에 푼다. ansible01의 server01_ansible로 실행하며 controller에 sudo를 붙이지 않는다.

```bash
sha256sum --check NODE_FIREWALL_SOURCE_SHA256SUMS
python3 -I scripts/run-node-firewall.py --help
python3 -I scripts/run-node-firewall.py inspect --node k8s-worker01
```

infra-ansible HEAD `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`, inventory·키·known_hosts·송신 IP 실제 보유 여부를 검사한다. 원격 svc_ansible/sudo Python 경로를 유지하고 Python -I를 사용한다. 실제 sudo 정책의 -I 인수 허용 여부는 새 버전 읽기 전용 검사로 확인하며, 실패를 없애기 위해 권한을 임의 확장하지 않는다. root 소유·경로 권한 검사 아래 /usr/local/bin의 kubectl도 찾는다.

TARGET, CP1_TOPOLOGY, CP1_STATE, POLICY, INSPECTION, RESULT JSON을 검토한다. CP1_STATE는 CP1 방화벽과 Calico Pod·HEP·GNP·IPPool·Felix 설정을 수집한다. spec·UID, Pod 준비 상태·재시작 정보는 해시에 포함하고 resourceVersion은 제외한다. 이는 상태 관측·변경 감지이며 보호의 의미적 완전성 인증은 아니다.

대상 snapshot에는 kubelet 4경로, etcd 2경로, static manifest 4경로의 존재·권한·소유자·크기·mtime 메타데이터를 포함한다. 내용은 읽지 않으며 사용자 지정 data-dir의 부재를 입증하지 않는다. 메타데이터 해시는 승인과 변경 중 snapshot 대조에 포함한다. raw nft는 사람이 검토한다. 기존 전체 snapshot 비교는 완화하지 않았다.

## CP1 영수증 및 실제 인수 기록

허용 생산자는 고정 커밋 `264b273ab8ad79209bc0fec4a85c4497c946dbe3`이며 helper SHA-256은 `a09efb38dbbc313124f6d42858c3ee3e7d676e933abe8ed0550bf8937fb18dcc`이다. `node-firewall.py`의 `CP1_CONTRACT`, 생산자 lock, 배포 인수 양식이 같은 값을 사용한다. v2 Runtime 6개 연산의 순서·범위·기존 소유권·실행 결과·전후 상태·Permanent 보존·시각을 검사한다. v1/12개 연산 또는 inspect는 거부한다. 새 생산자의 authorization_window와 각 연산 시작 시각도 검사한다. 작업창은 [start, end), 최대 2시간이며 종료 시각의 연산을 허용하지 않는다. receipt ended_at은 결과 보존 시각이므로 창 밖일 수 있고, 전체 성공 상태와 기존 시각 검증을 별도로 적용한다. 계약 시험은 main에서 통합된 `scripts/cp-canary-node.py` 실제 파일의 해시를 확인한 뒤 fake backend로 실행한다. 배포 양식을 실제로 읽고 현장 입력 필드만 채워 검증하므로, 시험 helper가 양식의 오래된 pin을 덮어 가리지 않는다. 실행 source pin은 승인된 CP1 생산자와 영수증 호환성을 유지하기 위한 값이며 PR #4 또는 PR #11 HEAD로 교체하지 않는다.

`cp1-runtime-acceptance.template.json`을 실제 인수 기록으로 채운다. receipt_input_sha256은 CP1 제출 노드 JSON 파일 해시, receipt_sha256은 내부 영수증 내용 해시다. source/helper/reference plan/execution plan, run ID, 이하영 리뷰 링크·결정·시각, Positive/Negative/Regression 각각의 실제 증적 해시와 PASS를 연결한다. 인수 시각은 영수증 종료 이후여야 한다. 모의 시험이나 형식상 APPROVED를 운영 인수로 사용하지 않는다.

## Runtime apply

`node-firewall-authorization.template.json`을 별도 파일로 복사한다. 실제 source/helper/plan/policy, 대상·mode, inspection 파일·snapshot·잔여 메타데이터·CP1 상태 해시, 시간 창·리뷰·보호·백업·콘솔·복구 판단을 채운다. Runtime/Permanent의 forward·masquerade는 실제 검토한 yes/no와 같아야 한다. 임의 기본값을 넣지 않는다. 적용에는 CP1 영수증 파일과 인수 기록 해시도 필요하다.

```bash
python3 -I scripts/run-node-firewall.py apply --node k8s-worker01 \
  --inspection /absolute/path/INSPECTION.json \
  --inspection-sha256 INSPECTION_FILE_SHA256 \
  --authorization /absolute/path/worker01-apply-authorization.json \
  --authorization-sha256 AUTHORIZATION_FILE_SHA256 \
  --cp1-receipt /absolute/path/CP1_APPLY_TARGET.json \
  --cp1-receipt-sha256 CP1_APPLY_TARGET_FILE_SHA256 \
  --cp1-acceptance /absolute/path/CP1_ACCEPTANCE.json \
  --cp1-acceptance-sha256 CP1_ACCEPTANCE_FILE_SHA256
```

대문자 값은 해당 파일의 실제 SHA-256으로 바꾼다. 노드 이름만 바꿔 다른 노드 입력을 재사용할 수 없다. CP2/CP3도 각 새 입력을 쓴다. 변경 직전·직후 CP1 topology·보호 상태와 인수된 CP1 Runtime 규칙 존속을 대조하고, 새 SSH 연결로 대상 상태를 확인한다.

`RUNTIME_APPLIED_REVIEW_REQUIRED`는 도구의 변경·전후 검사 통과이며 Runtime 운영 인수 완료가 아니다. Node Ready, etcd 전체 건강, CA/Auth, 애플리케이션, 정본 AC는 별도 실제 검증이다. 항상 ARCHIVE와 SIDECAR를 함께 제출한다.

## 영수증 회수와 Runtime 복구

journal 경로 `/var/lib/fds/icr-d-net-01-01/node-firewall-v1/`를 유지해 이전 미해결 실행을 숨기지 않는다. root 소유/fsync, 변경 전 intent·결과·소유권 기록을 유지한다. 전송 실패 뒤 자동 재시도 대신 원래 RUN_ID로 회수한다.

```bash
python3 -I scripts/run-node-firewall.py receipt --node k8s-worker01 \
  --run-id ORIGINAL_RUN_ID
```

v1 영수증은 읽기 전용 회수만 가능하며 v2 자동 rollback 입력으로 거부한다. journal 삭제·경로 교체로 우회하지 않는다. timeout, 경고, 결과 저장 실패, snapshot/membership drift는 별도 복구 판단이 필요하다.

복구도 새 inspect, mode=rollback 승인 기록, 원래 TARGET 영수증 파일 해시가 필요하다.

```bash
python3 -I scripts/run-node-firewall.py inspect --node k8s-worker01
python3 -I scripts/run-node-firewall.py rollback --node k8s-worker01 \
  --inspection /absolute/path/NEW_INSPECTION.json \
  --inspection-sha256 NEW_INSPECTION_FILE_SHA256 \
  --receipt /absolute/path/ORIGINAL_TARGET.json \
  --receipt-sha256 ORIGINAL_TARGET_FILE_SHA256 \
  --authorization /absolute/path/worker01-rollback-authorization.json \
  --authorization-sha256 ROLLBACK_AUTHORIZATION_FILE_SHA256
```

확실히 이 실행이 바꾼 Runtime 항목만 역순 원복한다. 기존 rule과 Cockpit unit mask를 보존한다. 이미 가입했거나 membership이 바뀐 경우 이 사전 join 복구 경로를 사용할 수 없다.

## 실행 예산과 미완료 범위

원격 프로세스별 최대 480초·1,200회 명령 예산을 적용한다. 개별 subprocess timeout은 남은 예산과 변경 창으로 제한하며 명령 수·timeout·예산 소진·실행 시간을 결과에 남긴다. 외부 SSH 제한 720초 안에서 무제한 명령 누적을 막는 중단 장치다. 실제 RHEL/firewalld 완료 성능을 보증한 값은 아니며 현장 metrics가 필요하다.

`promote-permanent`는 preflight/SSH 전에 NOT_IMPLEMENTED로 중단한다. Runtime 실제 시험·독립 인수 뒤 Permanent 반영 또는 보호 인계에 따른 회수 중 필요한 종료 절차를 별도 결정한다. Runtime은 reload/reboot로 사라질 수 있으므로 창 내 동결·종료 계획이 필요하다. 영속성·재부팅 인수는 미완료다.

공식 근거: [Runtime과 Permanent](https://firewalld.org/documentation/configuration/runtime-versus-permanent.html), [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html). rc=0만으로 소유권을 단정하지 않으며 경고·query·전후 상태를 함께 검증한다.

2026-09-14 변경: [생산자 갱신 검증](../change/ICR-D-NET-01-01/NODE_FIREWALL_PRODUCER_FOLLOWUP_20260914.md). 09-13 구 영수증/Permanent 두 수정의 CLOSED_STATIC 수용은 당시 범위로 유지하며 이번 새 pin/window 검증의 독립 리뷰와 실제 적용은 별도다.

2026-09-14 locale 보완: controller 자식 환경은 Ansible이 요구하는 `C.UTF-8`을 사용한다. plain `C`로 인한 원격 접속 전 인벤토리 조회 중단을 수정했다. [원인·검증·재개 절차](../change/ICR-D-NET-01-01/CONTROLLER_UTF8_FIX_20260914.md).
