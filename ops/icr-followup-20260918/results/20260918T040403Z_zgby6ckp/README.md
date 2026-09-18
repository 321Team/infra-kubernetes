# ICR 후속 현장 결과 — 2026-09-18 13:04 KST

**3CP Ready·CP Calico Pod Ready·LB 인증 조회 성공을 확인했다. Worker3대는 현재 대상 클러스터에 미가입이다. Worker3대 monitor01 경로 조회는 AC-14 Runtime PASS로 기록한다.**

## 무결성

- 원본: `ICR_Next_20260918T040403Z_zgby6ckp.tar.gz`
- 외부 SHA256: `17f8b6878cb47ded1ddc45ebd4d4e8d302988954e622ca104dfa002e581b48a4` / sidecar 일치
- 원본 내부 SHA256SUMS: **6/6**. 원본7파일은 byte 그대로 보존했다.
- 실행 소스 SHA256: `0660b90a364e14461ee551b919d05aefac5e9acf24c9e23aef2bc9a8e066db42`, [배포 commit fec790e](https://github.com/321Team/infra-kubernetes/blob/fec790e28668833daef99254c8a699b3ce23802a/ops/icr-followup-20260918/RUN_ICR_NEXT.py)와 동일
- 경로 이동·링크·중복 항목 없는 작은 archive이며 공유 JSON 비밀정보 패턴 검사에서 발견0건
- 별도 증적 검토에서도 무결성과 아래 판정을 확인했다. 운영 독립 최종 인수 승인과는 구분한다.

## 실제 관측

| 항목 | 2026-09-18 13:04:03~05 KST 결과 |
|---|---|
| 대상 클러스터 Node | CP1(.102), CP2(.103), CP3(.104)만 등록, 모두 Ready/v1.36.4. UID는 이전 성공 Join 증거와 동일 |
| Calico | kube-system의 calico-node3개와 kube-controllers1개 Running/Ready. calico-system namespace는 정상적으로 ABSENT |
| LB 인증 | CP1 source .102/ens160 → https://10.1.93.109:6443, CA 검증 강제. whoami=kubernetes-admin, groups=kubeadm:cluster-admins/system:authenticated. Node GET rc0 |
| Worker01 .105 | Node 미등록, kubelet1.36.4 auto-restart, containerd2.3.5 active/running |
| Worker02 .107 | Node 미등록, kubelet1.36.4 auto-restart, containerd2.3.5 active/running |
| Worker03 .108 | Node 미등록, kubelet1.36.4 auto-restart, containerd2.3.5 active/running |
| 수집 | CP1+Worker3 모두 COLLECTED. 모든 기록된 명령 rc0, 설정 변경 없음 |

Worker3대 모두 확인한 kubelet.conf/bootstrap-kubelet.conf/config.yaml/kubeadm-flags.env 및 CA/apiserver 인증서가 없고, manifests·CNI 설정 디렉터리는 비어 있다. **이번 확인 범위에서 정리/reset을 먼저 해야 할 근거는 발견되지 않았다.** 사용자 지정 다른 경로까지 비어 있다고 확대하지 않는다.

kubelet auto-restart와 Join 설정 부재는 함께 관측된 사실이다. 원인 로그를 수집하지 않았으므로 재시작 원인을 확정하지 않는다. 빈 설정 파일을 수동 작성하거나 서비스 재설치를 선행 요구할 근거도 없다.

## AC-14 경로 조회

실행 명령은 모든 Worker에서 `ip -j -4 route get 10.250.10.30`이다.

| Node | source | device | gateway | UTC 완료 시각 | rc |
|---|---|---|---|---|---|
| worker01 | 10.1.93.105 | ens160 | 10.1.93.58 | 04:04:04.100454Z | 0 |
| worker02 | 10.1.93.107 | ens160 | 10.1.93.58 | 04:04:04.594179Z | 0 |
| worker03 | 10.1.93.108 | ens160 | 10.1.93.58 | 04:04:05.041464Z | 0 |

[v19 AC-14 원문](https://github.com/321Team/fds-project-baseline/blob/966fc5365874b1d259a9810fc876b57c3bfa0e08/tickets/v3.1.19/descriptions/ICR-D-NET-01-01.md)은 “Worker 3대에서 동일 경로 조회 회귀가 통과한다.”이다. **현재 이 조건은 충족했다.** 앞서 “최종 보호 전환 이후에만 인정”이라고 붙인 것은 정본에 없는 추가 조건이므로 제거한다.

현재 AC 조건 증거 충족은 **1/20(AC-14 Runtime PASS)**, PARTIAL5, NOT_VERIFIED12, BLOCKED1, 마지막 확인FAIL/DRIFT1이다. 전체 ICR 독립 인수·Lead 승인·Done은 남아 있다. 이후 관련 경로/정책을 실제 변경하면 영향받는 회귀만 수행한다.

## 남은 범위와 인계

- 다음은 Worker01→02→03 정상 Join과 필요한 통신 확인이다. 현재 성공한 CP 정리·Join·CP1 시험·VM 백업을 반복하지 않는다.
- [Worker 후속 실행 범위](../../WORKER_HANDOFF.md)에 새로 필요한 최소 작업과 기존 정책·계약을 연결했다.
- Worker 실제 firewall/CP→Worker10250/Worker→LB TCP는 이번 수집 항목이 아니다. 별도 재수집 회차를 요구하지 않고 Worker 실행 직전 검사에 포함할 항목이다.
- LB 인증 성공은 backend별3/3와 장애 전환/복구 성공을 뜻하지 않는다. LB 자체 Stage B 후속은 남아 있다.
- AC-13의 edge/harbor/db 경로, AC-15 내부SNAT, AC-17 실제 패킷 왕복, AC-18 지속성까지 이 조회로 완료하지 않는다.
- 새 etcd health 및 CP1 timer/전체 방화벽 상태는 재조회하지 않았다. 이전 12:33 Join 증적의 관측 범위로 보존한다.
- 이번 수집과 검토에서 Worker Join·HEP/GNP·방화벽·타이머 변경은 수행하지 않았다.

원본 JSON: [CP1](k8s-control01.json), [Worker01](k8s-worker01.json), [Worker02](k8s-worker02.json), [Worker03](k8s-worker03.json). [검증 요약](VERIFICATION.json), [수집 요약](SUMMARY.json).

