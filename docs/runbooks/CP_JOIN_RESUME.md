# CP2·CP3 및 Worker join 재개 판단

2026-09-10 / #10 이하영 Kubernetes 판단, #14 이권욱 통신 보완. 이 문서는 준비된 재개 절차이며 join/reset을 실행한 결과가 아니다.

## 현재 증적

| 대상 | 마지막 확인 | 의미 |
|---|---|---|
| CP1 | 19:15–19:16 KST mvoxv1pm inspect: 유일한 Node와 etcd voter, API readyz 정상, Calico Running 1/1, HEP/GNP 0 | PR #2 현장 읽기 전용 검증이며 적용·보호 인수 영수증은 아님 |
| CP2 | 같은 수집에서 API/controller/scheduler manifest 존재, etcd manifest·표준 data dir·kubelet 설정 4경로 부재, CP1 member list에 CP2 없음 | 불완전한 이전 진행 흔적. 무조건 join 재실행 또는 reset의 근거가 아님 |
| CP3 | 기존 수집에서 kubelet 재시작 관측 | CP2 잔여 상태를 CP3에 그대로 적용하지 않음. 해당 노드의 현재 메타데이터 확인 필요 |
| Worker 3대 | 15:34 KST 보완: kubelet config.yaml 없음으로 종료 반복, 고정 4경로 모두 부재 | 정상 join이 구성해야 할 파일을 수작업 빈 파일이나 다른 노드의 인증서로 대체하지 않음 |

원래 kubeadm join stdout은 확보되지 않았다. 새 inspect의 표준 경로 메타데이터로 다른 사용자 지정 data-dir 부재까지 단정할 수 없다.

## 한 노드씩 재개

1. CP1 canary 적용 및 인수, 본 방화벽 변경의 검토를 선행한다. 실제 실행 창에서 CP1 API/readiness·Node UID·etcd member ID/name/peerURL/learner를 새로 조회하고 대상 노드 역할·IP·kubeadm/kubelet/containerd 버전과 설정을 대조한다.
2. v2 target inspect로 active zone/NM, 설정 경로 메타데이터, nft, CP1 membership·보호 상태를 확보한다. 잔여 메타데이터와 CP1 상태 해시를 실제 검토 기록에 연결하며 변경 중 drift를 차단한다. 표준 경로 부재가 사용자 지정 데이터 부재를 입증하지 않는다. firewall 단계와 join은 동시에 진행하지 않는다.
3. 다음 분기 표에 따라 #10에서 재개 방법을 기록한 후에만 join 창을 연다. 가입 토큰·certificate key는 담당자가 승인된 경로로 새로 발급·전달한다. PR/이슈/공유 로그에 기록하지 않는다.
4. CP2 완료 후 CP1을 통해 etcd 2개 member 및 양방향 peer 통신과 endpoint health를 확인한다. **2 member는 장애 허용 여유가 없으므로** CP2 검증 후 CP3 추가 창을 이어 확보한다. CP2가 불안정하면 CP3를 맹목적으로 추가하지 않는다.
5. CP3 완료 후 etcd 3 voter, health/status, Node 역할/Ready, LB API/TLS와 backend, CP 간 2379–2380, 각 CP→각 노드 10250을 검증한다. TCP handshake만으로 etcd 건강·API 인증을 판정하지 않는다.
6. Worker01→02→03도 각각 방화벽 변경 검토 후 정상 join으로 kubelet 설정을 생성하고 Ready·리스너·CP→Worker 10250·Worker→LB6443을 확인한다. bootstrap-kubelet.conf는 성공한 TLS bootstrap 뒤 삭제될 수 있으므로 그 파일의 부재만으로 실패 처리하지 않는다.

| 실행 직전 상태 | 재개 판단 |
|---|---|
| target Node/member 모두 없고 표준 경로가 깨끗하며 설치/버전 조건 충족 | #10이 정상 join 계획과 명령을 확정 |
| target Node/member가 없으나 CP manifest나 data 흔적 존재 | 현재 CP2에 해당하는 후보. 로컬에서 민감정보 보호하에 기존 구성과 경로를 확인하고, 보존할 자료·정리 대상·복구 방법을 지정한 변경안을 별도 검토 |
| target Node 또는 member(이름이 비어 있어도 peerURL 일치)가 존재 | 이 사전 join 방화벽 단계 중단. 기존 노드/etcd 복구 경로로 판단하며 중복 member 생성 금지 |
| membership·API 조회 실패, 예상 밖 경로/버전, 데이터 소유 불명확 | 수집된 실패 증적을 #10/#14에 연결하고 join 중단 |

잔여 파일 삭제, kubeadm reset, etcd member remove, 인증서 복사/재발급은 본 도구의 동작이 아니다. CP2의 실제 상태와 원래 join 구성을 검토해 필요한 작업만 명시해야 한다. 임의 `--ignore-preflight-errors`로 남은 manifest 충돌을 넘기지 않는다.

## 단계별 제출물

| 단계 | 필요한 증적 |
|---|---|
| 방화벽 | v2 Runtime apply/rollback 영수증, Permanent 무변경, 새 관리 SSH, CP1 topology·보호 상태 전후, 잔여 메타데이터, 원시 nft, 명령 수/시간/timeout |
| join | 실행 버전·대상·시각·종료 코드, 비밀값을 제거한 stdout/stderr, kubelet/containerd 상태 및 journal |
| CP | Node UID/역할/Ready, etcd member/learner와 endpoint health/status, peer 통신 |
| Worker | kubelet config/flags/kubelet.conf 메타데이터, Ready, 10250 listener, CP 출발 TCP 결과 |
| 최종 인수 | 6/6 Ready, 3 voter health, 승인 NTP 조건, Positive/Negative, 관리 경로 및 복구·재부팅 증적, 독립 리뷰 |

공식 근거: [kubelet과 kubeadm의 설정 흐름](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/kubelet-integration/), [kubeadm 클러스터 구성](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/create-cluster-kubeadm/), [etcd quorum과 장애 허용](https://etcd.io/docs/v3.6/faq/). 실제 설치 버전의 명령/구성 API를 별도로 확인한다.

## v2 보완에 따른 실행·종료 조건

PR #2 계약은 HEAD `f0388b1a372d19b5079e94ec6949ef443d774d2f`의 v2 Runtime 6개 연산이다. CP1 실제 적용 영수증과 Positive/Negative/Regression 독립 인수 기록이 있어야 PR #4 확대 입력이 성립한다. 새 HEAD 리뷰와 실제 보호·콘솔·백업·복구 조건도 별도 충족해야 한다. 발행 정본 v3.1.18 §6.6/6.6.1·D34·HEP 인계 순서와 기존 AC는 유지한다.

PR #4는 Runtime만 변경하며 Permanent 승격은 미구현이다. reload/reboot 시 Runtime이 사라질 수 있다. join을 재개하기 전에 #10/#14/#19에서 Runtime 유지/회수·별도 영속화 또는 보호 인계의 종료 계획과 필요한 승인을 확정한다. join 뒤에는 현재 사전 join rollback을 사용할 수 없으므로 종료 계획 없이 다음 창을 열지 않는다.

도구는 join 명령을 실행하지 않으며 대상 Node/member 존재와 잔여 경로·CP1 상태 변화는 차단한다. 다른 터미널의 join 실행 자체를 기술적으로 금지하지 못하므로 유지보수 동결은 실제 운영 통제다. 표준 경로만 조회한 결과로 CP2 잔여 파일 삭제/reset을 승인하지 않는다.

2026-09-14: 현재 CP1 생산자와 발행 정본 참조를 갱신했다. 위 09-10 관측값은 과거 실측이며 새 source의 현장 결과가 아니다. 최신 실행 입력은 NODE_FIREWALL_FOLLOWUP.md와 실제 승인 기록을 따른다.
