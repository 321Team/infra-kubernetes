# CP2 잔여 정리 → CP2·CP3 Join → 유지·회수 통합 변경 후보

현재 상태는 **신규 코드 검토 후보**다. 실제 노드 정리·Join·타이머 전환은 실행하지 않았다. 기존 방화벽 준비 승인이나 사용자의 착수 요청을 새 코드의 독립 리뷰로 표기하지 않는다. 현장 접속 키는 ansible01에 있으므로 이 작업 환경에서 VM에 직접 접속하지 않았다.

## 왜 기존 명령을 그대로 연결할 수 없는가

기존 CP1 Job `c564ae97530dfe80`의 2026-09-18 15:15 KST 회수 타이머는 Join 완료 뒤에도 CP1의 `cali+` 바인딩을 제거하고 reload를 수행할 수 있다. CP1 회수 함수는 Node/etcd topology를 비교하지 않는다. 반면 CP2·CP3의 기존 사전 Join 방화벽 rollback은 가입된 Node/member와 변경된 residual metadata를 거부한다. 따라서 현재 명령을 단순 연결하면 **가입 성공 뒤 연결이 회수되거나, 실패 뒤 기존 복구기가 작동하지 않는** 문제가 생긴다.

근거는 승인 source의 `cp1_minimal_change.recover()`, `run-cp1-minimal-change.IO.snapshot()/arm_one()`, `run-node-firewall.topology_gate()` 및 `node-firewall.rollback_transaction()`이다. 기존 코드의 `no join/reset` 실행 범위는 [실제 운영자 결정](https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723463564)에 명시되어 있다. 새 계약은 그 범위를 명시적으로 변경하는 후보이며 기존 승인 소스를 수정하지 않는다.

## 한 번에 검토할 정확한 범위

`CONTRACT.json`의 성공 종료 상태는 **3CP 정상 상태와 현재 Runtime 연결 유지**다. 성공한 클러스터를 시험 종료만을 이유로 해체하지 않는다. 회수는 실패한 단계에 한정한다.

| 단계 | 실행 동작 | 중단/실패 처리 |
|---|---|---|
| 시작 | 새 PR의 정확한 HEAD에 대한 hayeounglee APPROVED와 실행파일 바이트 확인. 기존 CP1 topology·Job, CP2/3 변경 직전 상태 확인 | 불일치 시 변경하지 않음 |
| CP1 연결 인계 | 기존 root 잠금과 frozen source/CHANGE/HANDOFF/SESSION 증적을 검증. immutable INTENT 작성, 정확한 기존 타이머만 정지, 실행/대기 중 복구 서비스 부재 및 Runtime 동일 확인 후 COMMITTED 작성 | 불완전 INTENT는 Join 차단. 타이머를 임의 재무장하거나 복구 명령을 덧붙이지 않음 |
| CP2 정리 | `/etc/kubernetes`의 호스트 내부 root 전용 백업 및 복사본 지문 검증. kubelet 정지 후 known SHA manifest 3개만 감시 경로 밖으로 격리 | 백업 실패/파일 변동은 Join 차단. PKI·kubeconfig 일괄 삭제 없음 |
| CP2 Join | 신규 토큰/인증서 키를 root600 임시 config로 전달해 정상 `kubeadm join --config` 실행 | 실패/timeout은 증적과 연결을 보존. CP3 실행 안 함 |
| CP2 확인 | CP1·CP2 Node Ready/역할/UID, 2 voter·peerURL·endpoint health/status 확인 | 불완전한 2-member 상태에서 다음 단계를 무조건 실행하지 않음 |
| CP3 Join | CP3의 빈 manifest 상태를 확인하고 정리 없이 정상 Join | 실패하면 성공한 CP2를 일괄 reset하지 않음 |
| 성공 | 3 Node Ready/3 voter/각 etcd health 확인, 이번 토큰·cert Secret 소유권 확인 후 정리, 통합 archive 생성 | Runtime 유지. 결과 독립 검토는 PENDING |
| 실패 회수 | `RECOVERY.md`의 member/quorum 분기로 실패 대상만 처리 | 자동 reset/member remove/snapshot 복원 없음. quorum 불명확 시 상태 보존 |

기존 CP1 5-flow 및 CP2·CP3 방화벽 적용, 복구 VM 백업 점검을 다시 실행하지 않는다. 실행 직전 변경 여부 확인은 새 실제 정리 대상과 연결을 보호하기 위한 작업 내부 검사다. `crictl`을 다시 설치하거나 별도의 전체 수집 회차를 선행조건으로 추가하지 않는다.

## 기존 증적 재사용

- [11:22 KST 사전 관측](https://github.com/321Team/infra-kubernetes/blob/57500a4a265b3d4065b67db785f8ddf8270eab02/ops/cp-prejoin-20260918/results/20260918T022216Z_3ep1zt_a/README.md): CP1 단독 Node/member, CP2 known manifest 3개, CP3 빈 경로, 양쪽 LB TCP PASS.
- [Kubernetes 담당자 원인 확인](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5724159810): 9월 9일 CP2의 기존 Join 실패 이력.
- [CP2·CP3 Runtime 적용 결과](https://github.com/321Team/infra-kubernetes/blob/8f3bd984da07702b6a908e36cbb38d456a0335f1/ops/cp-resume-20260918/results/20260918T013655Z_7637712f/README_KO.md): 노드별 6개 규칙, 기존 LB 규칙 보존.

## 새 유지 방식과 한계

새 후보는 기존 15:15 자동 회수 예약을 **Join 작업으로의 명시적 소유권 인계**로 바꾼다. 이는 단순 시간 연장이 아니다. 승인 후 실제 transfer가 COMMITTED 된 경우에만 기존 타이머가 정지한다. 기존 `CHANGE.json`을 RESTORED/ACCEPTED로 수정하거나 결과 승인을 만들어내지 않는다. 인계 뒤의 상태는 새 root receipt/status로 확인하며, 기존 frozen status가 `recovery_armed` 검사를 실패하는 것은 이 새 계약과 구분한다.

인계 완료 이후의 종료는 **명시적인 대체 보호 인계 또는 현재 membership을 고려한 철수**다. 시간만으로 CNI/etcd 연결을 제거하지 않는다. 이 유지 방식 자체를 이번 통합 리뷰에서 승인해야 한다. 실제 승인 전에는 기존 타이머가 그대로다. 기존 frozen `recover` 명령을 Join 후 수동으로 실행하지 않는다.

Runtime 유지가 reboot/reload 지속성을 뜻하지 않는다. Permanent 승격, HEP/GNP 적용, Worker Join, 6/6 구축 완료, LB 3/3 통계 및 LB 장애 전환 시험, ICR 전체 인수는 이 실행기의 완료 판정에 포함되지 않는다. 제어면 및 etcd의 검증 완료와 전체 인프라 AC 완료를 구분한다. `kubectl /readyz`는 현재 admin.conf의 API 경로 검사이며 전용 LB TLS/백엔드 검증을 대신하지 않는다.

CP1의 시스템 Python에 PyYAML이 있어야 실제 kubeadm ClusterConfiguration을 안전하게 파싱할 수 있다. 없거나 사용자 지정 CRI/kubelet/etcd 경로가 발견되면 변경 전에 중단한다. 이 환경 조건과 승인 코드의 실제 노드 실행은 아직 검증하지 않았다.

## 실행 진입점

`RUN_CP_JOIN.py`는 helper 3개와 계약을 포함한 단일 파일이다. 인수 없이 실행하면 PLAN_ONLY다. `--execute --review-pr <이 PR 번호> --source-commit <승인된 정확한 HEAD>`는 GitHub의 새 검토 및 자기 파일 바이트를 확인한다. Draft PR, 승인 부재, 다른 HEAD에서는 실행하지 않는다. 현장 실행 명령은 새 리뷰 완료 뒤 그 HEAD에 고정해 배포한다. 이 문서의 후보를 이미 실행 승인된 배포본으로 취급하지 않는다.

원격 토큰·certificate-key·관리 kubeconfig·PKI 백업은 Git/공유 archive에 포함하지 않는다. 현장 root 백업과 격리본은 노드의 `/var/lib/fds/cp-join/<job>/`에 남으며 공유 결과에는 지문과 상태만 담긴다. 실패 시 archive는 자동 생성하되 SSH 불확실 상태를 성공/복구 완료로 표시하지 않는다.

## 검증 범위

`VALIDATION.json`에 실제 로컬 STATIC/MOCK 결과를 기록한다. 모의검증은 VM의 kubeadm Join, etcd quorum 복구 또는 systemd 실제 전환 성공을 뜻하지 않는다. 리뷰에서 정리·Join·성공 유지·실패 회수 범위를 한 번에 검토하며 각 노드마다 새 승인 대기를 추가하지 않는다.
