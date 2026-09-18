# 3CP+3Worker 6/6 Ready 실측 검증 — 2026-09-18 15:56 KST

**이전 13:04의 Worker 미가입 상태는 해소됐다.** 이번 제출본은 15:56:14.820–15:56:16.251 KST의 읽기 전용 관측이다. 검토자는 제출 원본을 검증했으며 새 서버 명령을 실행하지 않았다.

| 노드 | InternalIP | 역할 | Ready |
|---|---|---|---|
| k8s-control01 | 10.1.93.102 | control-plane | True |
| k8s-control02 | 10.1.93.103 | control-plane | True |
| k8s-control03 | 10.1.93.104 | control-plane | True |
| k8s-worker01 | 10.1.93.105 | Worker / control-plane 표식 없음 | True |
| k8s-worker02 | 10.1.93.107 | Worker / control-plane 표식 없음 | True |
| k8s-worker03 | 10.1.93.108 | Worker / control-plane 표식 없음 | True |

## 확인 범위

- 외부 SHA256·내부6/6·기존 수집기 바이트 일치, 원본 JSON으로 summary 재계산 일치.
- 기존 CP3의 UID/IP/역할/Ready/버전은 이전 관측과 동일. Worker3의 등록·Ready를 새로 확인.
- kube-system의 calico-node6개와 calico-kube-controllers1개 모두 Running/Ready. calico-system namespace 부재는 이전과 같음.
- Worker3 kubelet·containerd active/running, kubelet/CA 설정과 Calico CNI 파일 존재. static Pod manifest 디렉터리는 비어 있음.
- Worker3 → monitor01 .30 경로가 via10.1.93.58, ens160, 각자의 source .105/.107/.108로 유지. ICR AC14 Runtime PASS 유지.
- CP1 → LB .109:6443, CA 검증·kubernetes-admin 인증·Node GET 성공.

## 해석과 후속

`join_executed:false`는 이 수집기가 Join을 실행하지 않았다는 뜻이다. Worker 미가입을 뜻하지 않는다. 실제 Join 수행자·정확한 시각·사용 소스·임시 토큰 회수는 이번 조회로 확인할 수 없으므로 추정하지 않는다. Worker Join을 다시 수행하지 않는다.

6/6 Ready 선행 상태는 확보됐다. CoreDNS·cross-node Pod/Service·스토리지·HEP/GNP/firewalld 전환·전체 K8S/ICR 인수는 이 자료로 완료하지 않는다. 새 etcd health·기존 CP1 timer·방화벽·NTP 조회도 포함되지 않았다.

이 자료는 ICR_Next 조회 결과이며 **LB backend3/3·failover는 NOT_TESTED**다. 기존 LB 전용 승인 수집기를 사용해 그 미확인 범위만 이어간다. 같은 6/6 수집·CP1 시험·Worker Join을 반복할 필요가 없다.

이하영님은 K8S/SEC 소관의 남은 구축·보호 정책을 진행하고, 이권욱은 LB 및 ICR 경로를 진행한다. HEP 정책 적용과 호스트 firewalld 전환은 공동 작업창에서 연결한다. 6/6 확인 자체가 미해소 코드 지적이나 실제 보호 전환을 대신하지 않는다.

- [직전 13:04 비교 자료](../20260918T040403Z_zgby6ckp/README.md)
- [역할·후속 인계](../../WORKER_HANDOFF.md)
- [ICR AC 판정](../../AC_REVIEW.md)

Archive SHA256: `5f743db12061f47bac8b5cf4fcc41014e6c749159cf938371d7d56e51ced90a4`.

원본 SHA256SUMS는 제출된 6개 payload의 해시를 그대로 보존한다. 검토 메타데이터는 REVIEW_SHA256SUMS로 별도 검증한다.
