# Kubernetes·ICR 담당 분리 및 Worker 인계 — 2026-09-18

확인된 오너는 **K8S #10·SEC #19 이하영(hayeounglee), ICR #14·LB #9 이권욱(GWONUK)**이다. 아래 세부 분담은 기존 오너에 맞춘 인계안이며, 이하영님의 새로운 승인이나 담당 변경을 의미하지 않는다.

## 담당별 실행 범위

| 작업 | 이권욱 / ICR·LB | 이하영 / K8S·SEC |
|---|---|---|
| 완료한 CP2·CP3 정리 및 Join | 사용자 요청에 따른 차단 해소 지원 결과와 Runtime·복구 인계 증적 전달 | 정상 3CP를 후속 구축 입력으로 사용. 재가입 없이 기존 K8S 오너 유지 |
| Worker01·02·03 Join | 실제 확인된 호스트 방화벽·경로 차이와 통신 차단 보완 | 기존 Kubernetes 절차의 preflight, Join 자격증명 준비, Worker Join, 이번 임시 자격증명 회수 |
| 6노드 구축 검증 | 네트워크 문제 분석·보완, 같은 결과를 ICR 증적으로 재사용 | 6/6 Ready, Calico·CoreDNS, cross-node Pod/Service 통신 및 K8S 티켓의 스토리지 검증 |
| named HEP/GNP 보호 전환 | 호스트 firewalld 전환, 관리 접속·반환 경로 및 ICR 허용/차단·복구 확인 | Calico/SEC 정책·manifest 준비 및 Kubernetes API 적용 |
| LB 후속 | backend 3/3와 장애 전환·복구 검증 | API/CP 상태 제공 및 영향 작업 협조 |
| ICR 관리·모니터링 경로 | IF-16 경로, mgmt-gw, IF-06B 네트워크 입력과 관련 Monitoring 담당 연계 | K8S/SEC 소관의 Pod·인증·정책 입력 및 시험 환경 제공 |
| 증적·인수 | ICR·LB 조건과 증거 연결 | K8S·SEC 조건과 증거 연결. 공통 시험 결과는 같은 묶음 사용 |

근거: [K8S #10](https://github.com/321Team/fds-project-baseline/issues/10), [SEC #19](https://github.com/321Team/fds-project-baseline/issues/19), [ICR #14](https://github.com/321Team/fds-project-baseline/issues/14), [LB #9](https://github.com/321Team/fds-project-baseline/issues/9).

## 완료한 입력을 그대로 사용

- [CP2·CP3 실제 Join 결과](https://github.com/321Team/infra-kubernetes/blob/5e9e09e7a1820c230bb5724ce05897c7689ef065/ops/cp-join-20260918/results/20260918T033022Z_16219c68779b3d13/README.md): 12:31:09 / 12:32:44 KST 각각 rc0. 12:33:36 KST 3CP Ready·etcd voter/health 3/3. 임시 Join 인증정보 REVOKED, 성공 Runtime 유지 인계 완료.
- [13:04 현장 결과](results/20260918T040403Z_zgby6ckp/README.md): 3CP Ready 유지, CP Calico node3개와 controller Ready, CP1→LB CA 검증·인증 Node GET 성공.
- Worker01 .105 / Worker02 .107 / Worker03 .108은 당시 대상 클러스터 미가입. kubeadm/kubelet1.36.4, containerd2.3.5 active/running. 확인한 표준 Join 설정·인증서 없음, manifests/CNI 디렉터리 empty. 이 관측만으로 reset·정리를 선행 요구할 근거는 없다.
- Worker3 → monitor01(.30) 경로는 source/ens160/gateway.58 일치로 **AC-14 Runtime PASS**. 같은 자료의 재제출을 요구하지 않는다.
- Worker의 실제 방화벽, Worker→LB TCP 연결, CP→Worker TCP10250은 해당 수집에서 시험하지 않았다. 담당자의 정상 preflight와 같은 작업창에서 필요한 부분만 확인한다.

## 중복을 없앤 후속 순서

1. 이하영님이 위 상태를 입력으로 기존 Worker Join 절차를 진행한다. 변경 직전의 identity·CRI·LB 연결·kubeadm preflight를 한 번 수행하며, 확인된 네트워크 문제는 이권욱이 같은 작업창에서 보완한다.
2. Worker01→02→03 Join, 노드별 결과와 6/6·Calico·실제 Pod/Service 통신 확인, 이번 임시 인증정보 회수를 하나의 실행 흐름으로 묶는다. 실패한 노드 상태를 보존하고 다음 노드 진행을 멈춰 원인을 확인한다.
3. 공통 결과 한 묶음을 K8S·ICR에서 재사용한다. 이권욱은 LB backend/장애복구와 IF-16·IF-06B 잔여를 병행한다.
4. [기존 Lead 결정](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)에 따라 6/6 Ready 이후 보호 전환을 공동 작업창에서 한 번 수행한다. 정책 적용·firewalld 전환 순서, 성공 기준, 실패 시 회수 동작을 함께 맞추고 각 담당이 자기 영역을 실행한다.

전체 ICR AC 완료나 기존 CP 결과 사후 리뷰 대기를 Worker 준비·가입의 새 전제로 만들지 않는다. 이미 완료한 CP1 시험·백업·CP Join도 반복하지 않는다. 실제 변경은 해당 기존 승인 범위와 확인된 기술적 조건을 따른다.

## 별도 Worker 실행기 계획 정정

앞선 안내의 “새 Worker 단일 실행기 구현·검토가 필요하다”는 내용은 **우리 측 자동화를 새로 만들 때의 조건**이었다. 이를 이하영님의 기존 Kubernetes Join 절차에 필요한 선행조건으로 적용하지 않는다.

이번에 준비하던 우리 측 별도 Worker Join 실행기 작업은 중단했다. 새 Worker 실행 소스의 게시·현장 실행은 없었다. 향후 담당자가 자동화 지원을 요청할 때 지원 범위를 정하면 된다.

[PR16 계약](https://github.com/321Team/infra-kubernetes/blob/bab9608557205b3bf5294b729d0960517d7a2847/ops/cp-join-20260918/CONTRACT.json)의 `worker_join=false`는 **그 CP 실행기의 적용 범위 제한**이다. K8S 담당자가 자기 Worker 절차로 진행하지 못한다는 뜻이 아니다. 구 Worker 방화벽 실행기의 과거 CP1 receipt 계약 역시 그대로 재실행할 수 없다는 도구 제한이며, 새 공통 승인 단계를 만들 근거가 아니다.

현재 확인된 완료 범위는 3CP Join과 위 조회 결과다. Worker Join·6/6·HEP/GNP 전환·ICR 최종 인수는 아직 완료 증거가 없다. [기존 통합 사후 리뷰](https://github.com/321Team/infra-kubernetes/pull/16#issuecomment-5724813354)는 유지하며 새로운 독립 결과 승인을 받은 것으로 기록하지 않는다.
