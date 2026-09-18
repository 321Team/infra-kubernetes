# Kubernetes·ICR 담당 분리 및 Worker 인계 — 2026-09-18

확인된 오너는 **K8S #10·SEC #19 이하영(hayeounglee), ICR #14·LB #9 이권욱(GWONUK)**이다. 아래 세부 분담은 기존 오너에 맞춘 인계안이며, 이하영님의 새로운 승인이나 담당 변경을 의미하지 않는다.

## 최신 상태 — 15:56 KST

[새 제출 증적](results/20260918T065614Z_zzi65aal/README.md)에서 **3CP+3Worker 6/6 Ready**, Calico-node6개+controller1개 Ready, Worker3 kubelet/containerd running을 확인했다. 이전 미가입 상태는 해소됐으며 아래 13:04 상태는 당시 인계 이력이다. **Worker Join을 반복하지 않는다.** 실제 Join 실행자·시각·토큰 회수는 이번 자료에 없어 추정하지 않는다.

이제 K8S/SEC 담당은 남은 CoreDNS·Pod/Service·스토리지 검증과 보호 정책 정리를 진행하고, 우리는 LB backend/장애복구 및 ICR IF16·IF06B 경로를 진행한다. 6/6 선행 상태는 확보됐으며 HEP/GNP 정책 적용과 firewalld 전환의 실제 수행·판정은 공동 작업으로 남는다. 같은 조회 결과를 다시 제출하도록 요구하지 않는다.

## 담당별 실행 범위

| 작업 | 이권욱 / ICR·LB | 이하영 / K8S·SEC |
|---|---|---|
| 완료한 CP2·CP3 정리 및 Join | 사용자 요청에 따른 차단 해소 지원 결과와 Runtime·복구 인계 증적 전달 | 정상 3CP를 후속 구축 입력으로 사용. 재가입 없이 기존 K8S 오너 유지 |
| Worker01·02·03 가입 후 확인 | 6/6 증거 재사용, 확인된 호스트 방화벽·경로 차이 보완 | 가입 완료 상태를 유지하고 기존 Join 실행 기록·임시 자격증명 회수 기록 연결 |
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

## 중복을 없앤 현재 후속 순서

1. **가입 완료 상태를 유지한다.** 이하영님은 기존 Join 실행·임시 인증정보 회수 기록을 연결하고, 아직 확인하지 않은 CoreDNS·cross-node Pod/Service·스토리지 검증을 진행한다. 6/6 Ready와 Calico Ready 수집을 반복하거나 Worker를 재가입시키지 않는다.
2. 이권욱은 LB backend3/3·장애복구, IF-16·IF-06B 잔여를 진행한다. 같은 API·경로 증거를 재사용하고 확인된 호스트 통신 차단만 보완한다.
3. [기존 Lead 결정](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)의 6/6 선행 상태는 충족됐다. 이하영님이 기존 SEC-06 정책 잔여를 수정하고, HEP/GNP 적용과 firewalld 전환을 공동 작업창에서 한 번 연결한다. 각 담당이 자기 영역을 실행하고 공통 결과 한 묶음을 사용한다.

전체 ICR AC 완료를 Kubernetes 후속 검증의 새 전제로 만들지 않는다. 이미 완료한 CP1 시험·백업·CP Join도 반복하지 않는다. 코드 외 실행·결과 판단은 기존 중앙 권한 정책에 따라 담당 Lead가 진행한다.

### SEC-06 현재 소스 확인

[PR7 HEAD 7a840cf](https://github.com/321Team/infra-kubernetes/pull/7)의 [Bootstrap 정책](https://github.com/321Team/infra-kubernetes/blob/7a840cf013be61e5891c4848f8732d4358208423/manifests/calico/01-bootstrap-allow.yaml)에는 `order: 0`/`applyOnForward: true`, LB .109 명시적 허용 누락, Node6 대상 포트 범위가 남아 있다. 6/6 Ready만으로 이 코드 지적이 해소되지는 않는다. 기존 관리 SSH·DNS 제한, NIC ens160 및 미승인 NTP .10 규칙 제거는 반영돼 있으므로 다시 요구하지 않는다.

[13:46 KST APPROVED 리뷰](https://github.com/321Team/infra-kubernetes/pull/7#pullrequestreview-5244346682)는 API 메타데이터상 현재 HEAD 대상이다. 다만 본문의 검토 HEAD는 4454098로 남고 SEC-01/02 부분 검토이며 Calico YAML은 NOT VERIFIED로 명시한다. 승인 기록의 존재와 SEC-06 검증 완료를 구분한다. [기존 SEC-06 수정 요청](https://github.com/321Team/infra-kubernetes/pull/7#issuecomment-5709871246)의 남은 범위만 연결하고 전체 PR 재검토·새 Join 절차를 추가하지 않는다.

## 별도 Worker 실행기 계획 정정

앞선 안내의 “새 Worker 단일 실행기 구현·검토가 필요하다”는 내용은 **우리 측 자동화를 새로 만들 때의 조건**이었다. 이를 이하영님의 기존 Kubernetes Join 절차에 필요한 선행조건으로 적용하지 않는다.

이번에 준비하던 우리 측 별도 Worker Join 실행기 작업은 중단했다. 새 Worker 실행 소스의 게시·현장 실행은 없었다. 향후 담당자가 자동화 지원을 요청할 때 지원 범위를 정하면 된다.

[PR16 계약](https://github.com/321Team/infra-kubernetes/blob/bab9608557205b3bf5294b729d0960517d7a2847/ops/cp-join-20260918/CONTRACT.json)의 `worker_join=false`는 **그 CP 실행기의 적용 범위 제한**이다. K8S 담당자가 자기 Worker 절차로 진행하지 못한다는 뜻이 아니다. 구 Worker 방화벽 실행기의 과거 CP1 receipt 계약 역시 그대로 재실행할 수 없다는 도구 제한이며, 새 공통 승인 단계를 만들 근거가 아니다.

현재 확인된 상태는 3CP+3Worker 6/6 Ready와 위 조회 결과다. Worker Join의 실제 실행 로그·임시 인증정보 회수 및 HEP/GNP 전환·ICR 최종 인수는 이번 증적에 포함되지 않았다. [기존 통합 사후 리뷰](https://github.com/321Team/infra-kubernetes/pull/16#issuecomment-5724813354)는 유지하며 새로운 독립 결과 승인을 받은 것으로 기록하지 않는다.
