# Join 실패 시 회수 — 대상과 quorum 기준

이 절차는 통합 변경 후보의 일부이며 현재 노드에서 실행한 복구 결과가 아니다. **성공한 3CP는 유지한다.** CP1의 기존 `recover` 또는 CP2/CP3 사전 Join firewall rollback을 가입 후 일괄 실행하지 않는다.

## 공통 보존

실행기가 실패하면 다음 노드 Join을 중단하고 현행 방화벽 연결을 보존한다. `RESULT.json`, 실패 노드 `/var/lib/fds/cp-join/<job>/STATE.json`, 원본 `/etc/kubernetes` 백업·격리본, kubeadm의 마스킹된 로그를 남긴다. SSH 종료/timeout이면 kubeadm 프로세스가 끝났다는 증거 없이 reset하거나 같은 Join을 다시 실행하지 않는다. 자동 credential 회수 결과도 확인한다.

## 상태별 실제 다음 동작

| CP1에서 확인한 현재 상태 | 허용되는 회수 | 금지되는 단축 처리 |
|---|---|---|
| timer transfer가 INTENT에서 중단 | Join 미실행 확인, 정확한 timer/service 상태와 CP1 Runtime 보존. 타이머가 살아 있는지 또는 이미 정지했는지 확인 후 기존 deadline/새 인계 중 하나를 명시적으로 복구 | INTENT 삭제 후 재실행, 무조건 기존 recover 호출 |
| prepare만 수행, kubeadm Join은 미시작 | kubelet 정지 상태와 현행 파일 지문 확인. 이번 격리 manifest만 원래 경로로 복구하고 기존 kubelet 활성 상태에 맞게 되돌림 | 전체 `/etc/kubernetes` 삭제 또는 reset |
| Join 실패, 대상 Node/member 모두 없음 | 실패 시 생성된 파일을 별도 root 전용 디렉터리에 보존. 기록된 백업과 비교해 해당 노드의 신규 구성만 정리하고 기존 상태 복원 | 원래 보관된 PKI/설정을 확인 없이 삭제, 정상 CP 파일 덮어쓰기 |
| 이번 대상 learner 또는 voter 존재, 건강한 quorum 있음 | exact member ID와 peerURL을 기록. **멤버 제거 성공 및 목록 부재를 확인한 다음** 해당 노드 로컬 정리/필요한 reset을 수행 | member 제거보다 먼저 노드 정지/데이터 삭제 |
| 2 voter 중 하나가 죽어 quorum 상실 | 기존 CP1·CP2를 보존하고 실패 노드의 etcd/통신을 복구해 quorum 회복. 그 후 필요 시 member 제거 | `--force-new-cluster`, 무조건 reset/member remove, CP1 VM snapshot 즉시 되돌림 |
| CP2 성공 후 CP3만 실패 | 정상 CP2를 유지. CP3의 정확한 member/로컬 상태만 위 규칙으로 처리 | CP1·CP2까지 일괄 되돌림 |
| CP1 API/etcd 조회 실패 또는 소유권 불명확 | 추가 변경을 멈추고 유지한 연결·콘솔로 상태 판별 | 조회 실패를 member 없음으로 취급 |

## etcd 회수 명령의 실행 기준

기존 CP1의 admin.conf 및 로컬 etcd 건강 인증서로 다음 조회를 수행한다. 토큰이나 개인키 내용을 출력하지 않는다.

```bash
sudo -n kubectl --kubeconfig=/etc/kubernetes/admin.conf -n kube-system exec etcd-k8s-control01 -c etcd -- \
  etcdctl --endpoints=https://127.0.0.1:2379 \
  --cacert=/etc/kubernetes/pki/etcd/ca.crt \
  --cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt \
  --key=/etc/kubernetes/pki/etcd/healthcheck-client.key -w json member list
```

같은 접속 인수로 `endpoint status --cluster`, `endpoint health --cluster`를 조회한다. 출력의 정수 ID는 언어의 부동소수점 변환 없이 보존한다. JSON ID를 명령에 넣을 때는 Python 정수로 읽어 `format(id, 'x')`를 사용하며, CP1 고정 멤버 ID 및 성공한 다른 CP의 ID는 제거 대상으로 허용하지 않는다.

현재 멤버의 과반수가 정상 응답하고 새 실패 대상 ID/peerURL이 이번 실행 기록과 정확히 일치할 때만 `member remove <그 ID의 16진수>`를 실행한다. etcd 응답을 보존하고 다시 `member list`로 부재를 확인한다. 조회/제거가 실패하면 아래 로컬 정리 단계로 진행하지 않는다. kubeadm의 timeout만으로 member 유무 또는 learner/voter 상태를 추측하지 않는다.

대상 member 부재와 실행 종료를 확인한 다음, 해당 노드에서 필요한 경우에만 kubeadm의 로컬 정리 단계를 선택한다. 원래 백업이 완전하고 원본/신규 파일 소유 범위가 구분된 경우에 한한다. `reset` 기본 전체 실행은 member 제거 실패를 경고로 남긴 채 데이터 정리를 계속할 수 있으므로 범용 rollback 명령으로 사용하지 않는다. 로컬 정리·백업 복원 후에는 파일 지문·kubelet 상태·member 부재를 다시 확인하고 회수 결과를 기록한다. 실제 종료 상태가 확인되지 않으면 `RECOVERY_REQUIRED`를 유지한다.

## 왜 모든 실패를 자동 reset하지 않는가

설치 버전 v1.36.4의 [etcd Join 구현](https://github.com/kubernetes/kubernetes/blob/v1.36.4/cmd/kubeadm/app/phases/etcd/local.go)은 learner 추가, 로컬 manifest 생성, voter 승격 및 건강 확인을 수행한다. [reset remove-etcd-member 구현](https://github.com/kubernetes/kubernetes/blob/v1.36.4/cmd/kubeadm/app/cmd/phases/reset/removeetcdmember.go)은 제거 실패 후에도 로컬 데이터 정리를 진행할 수 있다. 자동화 후보가 실패 시 보존하고 정확한 분기를 요구하는 것은 이 데이터 손실 가능성 때문이다.

## 성공 이후 최종 회수

Join 성공 후 토큰과 이번 cert Secret을 정리하고 Runtime을 유지한다. 이 단계는 방화벽/클러스터 철수가 아니다. 실제 철수가 필요하면 현재 3CP의 quorum과 담당자 작업을 반영해 대상 순서·대체 보호를 정한다. 기존 CP1 타이머를 다시 켜거나 세 노드 규칙을 한꺼번에 삭제하는 방법으로 종료하지 않는다.
