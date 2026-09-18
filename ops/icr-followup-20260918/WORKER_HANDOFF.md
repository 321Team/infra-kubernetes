# Worker01→02→03 후속 실행 인계

상태: **현재 상태 검증 완료 / 실행 범위 정리 / Worker Join 미실행**.

입력은 [13:04 현장 결과](results/20260918T040403Z_zgby6ckp/README.md)다. K8S 담당 티켓은 [#10](https://github.com/321Team/fds-project-baseline/issues/10), 담당자는 hayeounglee다. 이번 자료와 기존 3CP 완료를 사용하며 동일한 사전 수집·CP1 재시험·백업 제출 회차를 다시 만들지 않는다.

## 이미 확보한 입력

- 현재 정상 3CP의 이름/IP/UID, Calico node3개와 controller Ready, LB TLS 인증/Node GET
- Worker01 .105 / Worker02 .107 / Worker03 .108의 정확한 hostname/IP, containerd2.3.5 active/running, kubeadm/kubelet1.36.4
- 대상 클러스터 Node 미등록, 확인한 표준 Join 설정과 인증서 없음, manifest/CNI 디렉터리 empty
- Worker → monitor01(.30) source/ens160/gateway.58. AC-14 Runtime PASS
- 기존 CP1 Runtime 유지 인계 계약 및 CP2/3 실제 Join 성공. 이전 timer를 복구 작업처럼 다시 실행하지 않음

## 실행 한 번에 넣을 최소 작업

| 순서 | 수행 범위 | 완료·실패 처리 |
|---|---|---|
| 1 | 현재 3CP/Node identity 유지, Worker의 실제 방화벽·CRI·LB TCP 및 kubeadm 정상 preflight를 변경 직전에 확인 | 바뀐 입력만 판단. 현재 수집 전체의 재업로드를 별도 전제로 두지 않음 |
| 2 | 기존 Worker 정책의 필요한 Runtime 차이만 확인·반영 | CP .102/.103/.104 각각 /32→Worker TCP10250, Runtime cockpit 허용 정리. 기존 SSH/LB/다른 정책 보존 |
| 3 | Worker용 bootstrap token+CA pin을 private 경로로 준비, Worker01→02→03 Join | controlPlane/certificate-key/upload-certs/etcd 변경 없이 정상 Worker Join. 매 노드 결과 확인 후 다음 노드 |
| 4 | Worker별 Ready·Calico Ready 및 6/6, CP3→Worker3 TCP10250, Worker→LB6443 회귀 | Node/Pod Ready와 실제 cross-node Pod/Service 흐름을 구분해 기록 |
| 5 | 이번 실행의 bootstrap token 회수, 결과 한 묶음 인계 | UID/소유권 일치 대상만 회수. 성공한 Node·Runtime 유지. 실패 시 다음 노드 중지, 실패 상태 보존; 자동 reset/회원 삭제/연결 규칙 선제 삭제 없음 |

[기존 Worker 정책](https://github.com/321Team/infra-kubernetes/blob/085b0a3ea0ce9e47b4f52c72bf765e4ba8f8e5ef/policies/icr-d-net-01-01/node-firewall-v2.json)의 범위는 Worker당 CP3→10250과 Runtime cockpit 허용 정리다. 이 인계를 Worker의 etcd2379–2380·6443 listener·NodePort 전체 개방이나 firewalld OFF 승인으로 확대하지 않는다.

## 기존 실행기와의 관계

현재 저장소에는 위 Worker 단일 Join 묶음이 구현돼 있지 않다. 구 Worker 방화벽 실행기는 과거 CP1 receipt/source 계약을 사용하고, [PR16 계약](https://github.com/321Team/infra-kubernetes/blob/bab9608557205b3bf5294b729d0960517d7a2847/ops/cp-join-20260918/CONTRACT.json)은 CP2/CP3만 대상으로 `worker_join=false`다. 따라서 완료한 CP 실행기의 대상 이름만 바꿔 Worker에 실행하지 않는다.

후속 자동화를 구현할 때 위 차이 검사·Worker Join·검증·임시 인증정보 회수를 한 묶음으로 연결하고, 새 Worker 소스/실행 범위의 검토를 기존 CP 승인과 구분한다. 기존 CP 성공 증거를 다시 승인받거나 frozen receipt를 고쳐 맞출 필요는 없다. 이 문서는 구현 완료 또는 새 Worker 실행 승인 기록이 아니다.

## 병행·종결

- 전체 ICR AC 완료와 CP 결과 사후 리뷰 대기를 Worker 가입의 새 전제로 삼지 않는다.
- PR7 HEP/GNP 잔여 수정은 기존 범위로 진행하며, [Lead 결정](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)의 6/6 Ready 이후 최종 보호 전환 조건을 유지한다.
- 현재 Calico CP Pod Ready만으로 Worker cross-node 데이터 통신을 PASS로 선언하지 않는다.
- IF-16 edge/harbor/db 경로 및 IF-06B Monitoring 인수는 Worker/ICR 후속과 병행한다.

