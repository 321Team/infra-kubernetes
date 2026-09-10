# CP1 Canary Kit v1.1 — 점검·제한 적용·복구

상태: 실행 도구 준비 및 로컬 검증 완료. **실서버 미실행, 독립 리뷰 대기. 현재 실행 단계는 `inspect`다.**

연계: [#14](https://github.com/321Team/fds-project-baseline/issues/14), [#10](https://github.com/321Team/fds-project-baseline/issues/10), [PR #2](https://github.com/321Team/infra-kubernetes/pull/2).
일반 변경·복구 기준은 [CP 포트 절차](ICR-D-NET-01-01-CP-PORTS.md)를 따른다.

## 1. 현재 실행할 읽기 전용 점검

배포 ZIP와 SHA-256 sidecar를 ansible01의 `/home/server01_ansible`에 둔다. **server01_ansible 계정으로 sudo 없이** 실행한다. ZIP 해시를 먼저 검사하고 새 디렉터리에 푼다.

```bash
(
  set -euo pipefail
  cd /home/server01_ansible
  sha256sum --check FDS_CP1_Canary_Kit_v1.1.zip.sha256
  fds_canary_dir=$(mktemp -d /home/server01_ansible/FDS_CP1_Canary_Kit.XXXXXX)
  python3 -m zipfile -e FDS_CP1_Canary_Kit_v1.1.zip "$fds_canary_dir"
  python3 "$fds_canary_dir/FDS_CP1_Canary_Kit_v1.1/scripts/run-cp-canary.py" inspect
)
```

v1.1은 v1.0의 ANY 정책 오인 오류를 수정했다. 새 디렉터리에서 다시 점검하며 기존 v1.0 결과를 적용 입력으로 사용하지 않는다. [제출본 검토서](../change/ICR-D-NET-01-01/CP1_CANARY_REVIEW_20260910.md).

결과의 `ARCHIVE=...tar.gz`, `SIDECAR=...tar.gz.sha256` 두 파일을 제출한다. 실패한 경우에도 가능한 범위의 증적과 실패 단계가 압축파일에 남는다. `COLLECTED_REVIEW_REQUIRED`는 수집 반환 상태이며 서비스 PASS를 뜻하지 않는다. 노드별 `gaps`, `failed_queries`, `snapshot_queries`, TCP 결과를 별도로 검토한다. v1.1에서 gaps/실패 질의가 있으면 전체 상태는 `INCOMPLETE_REVIEW_REQUIRED`다.

`inspect` 수집 범위:

| 대상 | 수집 |
|---|---|
| CP 3대 | SSH source/destination/user/hostname, sudo/root identity, SELinux, firewalld Runtime/Permanent, NM connection UUID·zone, active policies/direct rules, nft ruleset, 리스너, 서비스 상태, kubeadm 버전 |
| CP1 | API readyz, Nodes, etcd Pod, etcd member list 및 endpoint status, etcd·apiserver 최근 로그 |
| CP2 | 9월 9일 12:40–13:10 UTC kubelet journal 최대 800줄, 매니페스트·kubelet·etcd 잔여 경로의 존재/소유/크기/시각 |
| CP 3대 → CP1 | TCP 2379/2380/10250 연결 검사. 실패도 원래 결과로 보존 |

개인키·kubeconfig·인증서·셸 history 내용은 수집하지 않는다. etcd 조회는 기존 CP1 etcd Pod 안의 `etcdctl`과 인증서 **경로**를 사용한다. `member list`와 `endpoint status`만 호출하며 KV 기록이나 member 변경은 하지 않는다. crictl 설치 없이 kubectl logs/exec를 사용한다. Pod exec, SSH/sudo 및 임시 kubectl cache는 감사 기록을 남길 수 있다. 임시 cache는 실행 후 정리한다.

## 2. 고정된 실행 입력

- 기존 infra-ansible `/home/server01_ansible/p1-ans-01`, HEAD `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab`. `ansible.cfg`·Inventory는 수정되지 않아야 한다. Inventory CP group은 .102/.103/.104 세 별칭과 일치해야 한다.
- 승인 키 `/home/server01_ansible/.ssh/p1_ansible_ed25519`, fingerprint `SHA256:VPGV/RGA8TrwO/3FC4YPxGzRNhBWuB+9hCzPV5plkws`. 알려진 host key와 공개키 인증을 강제한다. Agent·접속 공유·패스워드 인증을 사용하지 않는다.
- SSH source `10.250.10.10`, 대상 계정 `svc_ansible`. Python 소스는 SSH 표준입력으로 전달하고 기존 sudo 권한으로 실행한다.
- 배포본 `SOURCE_LOCK.json`에 PR의 전체 commit 및 파일 해시를 기록한다. 실행기가 이를 검사한다. 외부 ZIP 해시 검사를 대체하는 서명이나 서버 원격 증명은 아니다.
- 계획 SHA-256 `2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7`.

입력 불일치는 자동 수정하지 않는다. 에러와 증적을 제출해 실제 변경을 확인한다.

## 3. 독립 리뷰 후 CP1 적용

이 절의 명령은 **지금 실행할 단계가 아니다**. #14에 현재 소스 HEAD의 실제 독립 리뷰 결과, 실행 결정, 작업창·실행자·콘솔 복구 경로를 기록한 뒤 사용한다. 리뷰를 도구가 생성하거나 승인으로 간주하지 않는다. 동일 변경창의 다른 firewall 편집과 join 작업은 중지한다.

적용 직전 `inspect`를 다시 실행한다. **15분 이내** CP1 점검 JSON의 해시와 파일을 입력한다. 아래 경로와 SHA는 실제 최신 결과로 채운다.

```text
python3 /실제/kit/scripts/run-cp-canary.py apply --input /실제/점검출력/k8s-control01.json --input-sha256 점검JSON의64자리SHA256
```

첫 실행기는 **CP1만** 변경한다. CP2/CP3 및 Worker 적용 기능은 없다. Source는 CP 3개의 /32, 포트는 2379–2380·10250으로 고정되어 있다. CP1 Permanent 6건 후 Runtime 6건을 순차 처리하며 기존 규칙은 보존한다. /24, 직접 API 6443 추가, reload, runtime-to-permanent, timeout, firewalld OFF, join/reset, NTP·HEP/GNP 변경을 호출하지 않는다.

적용 시 다음을 다시 확인한다.

1. 점검 파일·소스·계획 해시 및 수집 시각 일치.
2. 현재 identity, SELinux Enforcing, firewalld/NM/zone/기존 SSH·LB 규칙과 점검 snapshot 일치.
3. Node와 etcd member가 CP1 하나이며 member peer URL은 `https://10.1.93.102:2380`, learner가 아님. 다른 구성이면 현재 초기 canary 범위에 맞지 않아 중단.
4. 각 쓰기 전에 영구 작업 기록을 저장하고, 성공 반환과 실제 규칙 존재를 확인한 뒤에만 이번 실행 소유로 기록.
5. 규칙별 상태 변화, 적용 후 API readiness·etcd 조회·멤버 구성 확인.

원격 기록은 CP1 `/var/lib/fds/icr-d-net-01-01/cp1-canary/<run_id>.json`에 root 소유 0600으로 저장한다. `APPLIED_REVIEW_REQUIRED`는 적용 기록이며 운영 인수 PASS가 아니다. 적용 직후 같은 kit의 `inspect`로 **새 SSH 접속, CP2/3→CP1 도달성, 기존 보호·API·etcd 상태**를 확보한다. 이 키트의 TCP 검사는 TLS/Auth 기능 시험이나 비승인 Source 차단 시험을 대체하지 않는다.

## 4. 부분 실패와 복구

실패 시 자동 재시도·전체 rollback은 하지 않는다. `PARTIAL_*`, `STOP`, SSH timeout은 실제 작업 기록과 현 상태를 함께 확인한다. SSH 단절 시 원격 프로세스의 결과가 불명확할 수 있다. 재접속 후 다음 읽기로 기록을 확보한다.

```text
python3 /실제/kit/scripts/run-cp-canary.py receipt --run-id 원래RUN_ID
```

복구가 결정되면 적용/receipt 조회 결과의 CP1 JSON과 그 해시를 입력한다.

```text
python3 /실제/kit/scripts/run-cp-canary.py rollback --input /실제/적용출력/k8s-control01.json --input-sha256 적용JSON의64자리SHA256
```

복구는 같은 소스 commit·계획·원격 기록 해시를 요구한다. 변경 전에 없었고 이번 실행이 확실히 추가한 규칙만 역순 삭제한다. 기존 규칙, ownership 불명확, 동시 firewall 변경, 새로운 Node/etcd member, 조회 실패 또는 이미 불완전하게 진행된 rollback은 자동 제거를 거부한다. 이 경우 수동 검토가 필요하다. 다른 서버의 새 멤버가 통신에 의존한 뒤 규칙을 철회하지 않는다.

복구 후 다시 `inspect`를 실행해 새 SSH·API·etcd 상태와 원래 firewall snapshot 복원을 검토한다. CP2/CP3 확대 및 join 재개는 별도 후속 단계로 기록한다.

## 5. 검증 범위

로컬 자동 테스트 30개 PASS: 기존 계획 11개, 실행·복구·전송/입력 보호 14개, 정책 조회·상태 집계 회귀 5개. v1.0 inspect는 실서버에서 실행했고 SSH·etcd·일반 질의는 확보했으나 정책 파싱 오류로 snapshot 생성에 실패했다. v1.1 실서버 재점검과 apply/rollback 실행 검증은 아직 하지 않았다. [검증 기록](../change/ICR-D-NET-01-01/VALIDATION.md).

기술 기준: [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html), [etcd status](https://etcd.io/docs/v3.6/tasks/operator/how-to-check-cluster-status/), [kubectl exec](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_exec/), [NetworkManager connection.zone](https://networkmanager.dev/docs/api/latest/nm-settings-nmcli.html).
