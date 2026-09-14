# CP1 canary 원본 보존·비상 복구

2026-09-14 / PR #2 후속 R2의 **검토용 절차**. 현장 복구 성공이나 실행 승인을 기록한 문서가 아니다. 현재 기준은 [발행 정본 v3.1.18 §6.6/6.6.1](https://github.com/321Team/fds-project-baseline/blob/72599ae49342b0fb8dbd3d8ee60314afb9f907bb/baseline/FDS_Project_Implementation_Baseline_v3.1.18.md)와 [ADV-20260910](../change/ICR-D-NET-01-01/ADVANCE_CHANGE_RECORD_20260910.md)이다. CP1 통신 canary와 HEP/GNP 보호 인계 순서는 구분한다.

## 1. 적용 전에 원본으로 복귀 가능한 상태 만들기

이권욱은 실제 실행 결정에 원래 source commit, helper·정책·입력·승인 JSON·보호 검토·복구 계획의 SHA-256, run ID, 콘솔 접근 확인 결과, 백업 위치/시각/검증 결과, 원본 키트의 절대 경로를 기록하고 이하영이 대조한다. 백업은 보유 여부뿐 아니라 복구에 필요한 대상·일관성과 실제 사용 경로를 확인한다.

Git 실행은 승인된 commit의 별도 detached checkout을 유지한다. 다음은 **검토 후 실제 값으로 채우는 준비 명령 형식**이다. apply 명령은 포함하지 않는다.

```text
git -C /absolute/path/infra-kubernetes worktree add --detach /absolute/path/cp1-original-checkout REVIEWED_CP1_COMMIT
git -C /absolute/path/cp1-original-checkout rev-parse HEAD
git -C /absolute/path/cp1-original-checkout status --porcelain --untracked-files=no
git -C /absolute/path/cp1-original-checkout remote get-url origin
git -C /absolute/path/cp1-original-checkout bundle create /absolute/path/recovery/source.bundle HEAD
git -C /absolute/path/cp1-original-checkout bundle verify /absolute/path/recovery/source.bundle
sha256sum /absolute/path/recovery/source.bundle
```

출력 HEAD와 승인 HEAD가 같고 tracked 변경이 없으며 origin이 `321Team/infra-kubernetes`여야 한다. 원본 checkout에서 `docs/change/ICR-D-NET-01-01/SOURCE_SHA256SUMS`를 검사한다. 키트가 ZIP 배포본이라면 실제 원본 ZIP·외부 sidecar·내부 manifest·`SOURCE_LOCK.json`을 함께 보존한다. 새 코드로 원래 키트를 덮어쓰거나 lock의 commit/hash를 수동 교체하지 않는다. 원본 checkout과 bundle/ZIP의 별도 접근 가능 사본을 확보하고 실행 전 검증한다.

실행 출력 `REQUEST.json`, **원문 바이트를 보존한** `AUTHORIZATION.json`, `k8s-control01.json`, `RESULT.json`, archive·sidecar 및 root 영수증을 원본 그대로 보존한다. root 영수증 경로는 계속 `/var/lib/fds/icr-d-net-01-01/cp1-canary/<ORIGINAL_RUN_ID>.json`이다. v1.3도 이 디렉터리를 그대로 사용하므로 미해결 이전 실행을 감춘 새 run은 허용되지 않는다.

## 2. 중단 직후와 정상 자동 복구 경로

1. 실패 시각·단계와 마지막 명령을 기록하고 추가 apply/join/reset 및 다른 firewall/NM/Calico 변경을 중단한다. timeout은 미적용의 증거가 아니다. 원격 프로세스/transaction lock의 상태와 영수증을 먼저 확인한다.
2. SSH가 가능하면 **원래 소스 checkout/키트**에서 `python3 scripts/run-cp-canary.py receipt --run-id ORIGINAL_RUN_ID`로 root 영수증을 회수한다. receipt 조회에는 만료된 apply 승인 재사용이 필요 없다. 원격 출력의 해시와 원본 root 파일 해시를 대조한다. SSH가 끊겼다면 §3의 콘솔로 원본 영수증과 로그를 확보한다.
3. source가 바뀌었다면 보존한 원본으로 이동한다. checkout을 잃었으면 bundle에서 새 디렉터리로 복원한다. 아래는 복원 형식이며 기존 디렉터리를 reset하지 않는다.

```text
git clone --no-checkout /absolute/path/recovery/source.bundle /absolute/path/restored-cp1-original
git -C /absolute/path/restored-cp1-original checkout --detach ORIGINAL_RECEIPT_SOURCE_COMMIT
git -C /absolute/path/restored-cp1-original remote set-url origin https://github.com/321Team/infra-kubernetes.git
```

원래 승인 기록과 bundle 해시, checkout HEAD·helper SHA·정책 SHA·manifest를 다시 대조한다. 이것은 기존 source 일치 검사를 충족시키는 복원이며 검사 우회가 아니다. 원본 확보나 일치 확인에 실패하면 자동 rollback을 진행하지 않는다.

4. 실제 링크·검토 범위를 대조한 **별도 rollback 승인 파일**에 `mode=rollback`, 원래 source commit, 최신 회수 결과 파일의 SHA-256, 복구 작업창·실행 결정·복구 계획 SHA를 기입한다. apply용 mode/input/window를 재사용하지 않는다. 새 HEAD 승인만으로 구 source 복구를 승인한 것으로 간주하지 않는다.
5. API readiness, 원래 단일 CP1 Node/etcd member 및 topology hash, live firewall/NM와 소유권 기록이 모두 일치할 때만 기존 controller의 `rollback`을 실행한다. 기존 SSH/LB·Permanent 규칙을 보존하며 확인된 추가만 역순 회수한다. 명령 형식은 [키트 문서](ICR-D-NET-01-01-CP1-CANARY-KIT.md)를 따른다.

작업창은 `[window_start, window_end)`다. 종료 시각에 새 원격 명령을 시작하지 않는다. 중간 만료는 `PARTIAL_*_REVIEW_REQUIRED`와 마지막 작업 상태로 남는다. 만료가 자동 rollback이나 규칙 자동 삭제를 일으키지는 않는다. 새 복구 창이 필요하면 별도 실행 결정을 기록한다.

## 3. API/SSH 장애 또는 모호한 영수증의 콘솔 분기

`API readiness did not pass`, etcd Pod/exec 조회 실패, 다른 member, source 불일치, drift, `*_pending`/`*_uncertain`, 부분 rollback은 자동 회수를 멈추는 조건이다. 원래 가드를 유지한다. 이하영과 이권욱이 콘솔에서 다음 근거를 확인한다.

| 확인 | 수집·판단 내용 |
| --- | --- |
| 대상·시각 | CP1 hostname, ens160 `10.1.93.102`, UTC/KST 시각·시간 동기화. 다른 노드면 중단 |
| 원본 | 원래 source/승인/입력과 root 영수증 전체, 해시·소유자·권한, controller/remote 중단 시각, lock 보유 프로세스. 미해결 프로세스와 경쟁하지 않음 |
| 방화벽·관리 | Runtime/Permanent public zone, 기존 SSH와 LB .109/32→6443, active/default zone, NM 연결·zone, nft 출력 및 관리 경로를 변경 전 snapshot과 비교 |
| API·etcd | kubelet/containerd/firewalld 상태·로그, static Pod 상태와 실제 etcd data/mount 경로, 마지막 etcd membership와 현재 확인 가능한 membership. API 장애를 방화벽 원인으로 단정하지 않음 |
| 보호·의존성 | Calico 상태·HEP/GNP 기존 기록, CP2/3·Worker join 동결 준수 및 새 member/통신 의존성 여부. 의존성을 배제하지 못하면 규칙을 제거하지 않음 |

콘솔 읽기 전용 명령 예시는 아래와 같다. 각 명령의 UTC 시각·종료 코드·stdout/stderr를 별도로 보존한다. 오류는 정상값으로 대체하지 않는다. 원본 로그 접근은 제한하고 외부 제출본에서 토큰·키 등 민감값을 가린다.

```text
hostnamectl --static
ip -j -4 addr show dev ens160
date --iso-8601=seconds
timedatectl show
systemctl is-active firewalld kubelet containerd
firewall-cmd --get-default-zone
firewall-cmd --get-zone-of-interface=ens160
firewall-cmd --zone=public --list-all
firewall-cmd --permanent --zone=public --list-all
nmcli -g GENERAL.CON-UUID device show ens160
nft list ruleset
journalctl -u firewalld -u kubelet -u containerd --since ACTUAL_INCIDENT_START --no-pager
```

API를 사용할 수 없으면 이미 설치된 CRI 도구와 **실제 확인한 runtime endpoint·실행 중인 CP1 etcd container**로 local etcd 조회 가능 여부를 판단한다. 예: `crictl --runtime-endpoint VERIFIED_ENDPOINT ps -a` 후 식별한 container에서 `etcdctl member list`·`endpoint status`를 읽기 전용으로 조회한다. 원래 helper와 같은 local endpoint `https://127.0.0.1:2379`, 검증된 etcd CA/healthcheck-client 인증서 경로를 사용하되 실행 중 container의 mount와 일치하는지 확인한다. 미확인 container ID·socket·data 경로를 추측하거나 도구를 임의 설치하지 않는다. API 우회 조회는 새 Node 목록·기능 인수를 대신하지 않으며, 충분한 현재 topology를 입증하지 못하면 자동 복구로 돌아갈 수 없다.

## 4. 콘솔 복구 결정과 종료

- API/관리 장애를 다른 원인으로 복구해 기존 source·topology·snapshot·소유권 조건이 다시 충족되면 §2의 별도 승인 자동 rollback으로 돌아간다.
- 자동 조건을 충족할 수 없으면 **실제 상태에 한정된 수동 복구 계획**을 기록한다. 대상 rule의 원문, 변경 전 부재 근거, 이번 run이 추가했다는 확정 근거, 현재 의존성, 되돌릴 단일 명령, 확인 조회, 중단 기준, 별도 복구 창과 이권욱 실행 결정·이하영 검토를 표로 남긴다. `pending/uncertain`은 그 자체로 제거 가능한 소유권 증거가 아니다.
- 승인한 수동 Runtime 회수가 있다면 각 rule을 정확히 query → 승인한 단일 remove → query하고 매 단계 결과를 기록한다. 형식은 `firewall-cmd --zone=public --remove-rich-rule=EXACT_REVIEWED_RULE`이다. 예시를 그대로 실행하거나 모든 receipt 작업을 일괄 삭제하지 않는다. 증거가 없는 rule, 기존 SSH/LB, Permanent, 광역 Source를 수정하지 않는다.
- API 장애라는 이유만으로 `--force-rollback`, receipt/lock 삭제·변조, `firewalld OFF/reload/restart`, `kubeadm reset`, member remove, 데이터 디렉터리 삭제/복원을 실행하지 않는다. 별도 보호 인계/재해 복구 범위가 필요하면 기존 ICR·관련 티켓의 실제 결정으로 연결한다.
- 수동 복구 후에도 원래 영수증은 보존하고 별도의 recovery 기록과 연결한다. 원래 `PARTIAL_*`를 임의로 성공 상태로 바꾸지 않는다. 자동 신규 run 차단을 풀기 위한 가드 우회는 이 절차에 없다. reconciliation이 필요하면 증거에 맞는 별도 검토 가능한 변경으로 처리한다.

종료 증적은 원래 기준과 현재 Runtime/Permanent·NM 비교, 새 관리 SSH, API readyz·etcd member/status, CP 통신 및 관련 기능/Negative/Regression, 원본/제출본 해시, 실제 실행자·리뷰어 판정이다. `ROLLED_BACK_REVIEW_REQUIRED`나 콘솔에서 접속 회복만으로 ICR의 AC·Done, Permanent 승격 또는 HEP/GNP 최종 보호 인수를 완료로 바꾸지 않는다.
