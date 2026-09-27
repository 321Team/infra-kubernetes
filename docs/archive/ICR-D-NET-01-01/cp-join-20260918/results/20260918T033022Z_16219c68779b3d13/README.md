# CP2·CP3 Join 실제 성공 — 2026-09-18

**제출 증적에서 CP2 정리·Join, CP3 Join, 3CP Ready·3개 etcd voter 건강 상태, CP1 Runtime 인계와 임시 인증정보 회수 성공을 확인했다.** 검토자가 서버를 새로 조회한 결과가 아니라 사용자가 실행해 제출한 원본과 승인된 실행 코드의 대조 결과다. 마지막 클러스터 관측은 **2026-09-18 12:33:36.720 KST**다.

## 실행과 검증 근거

- 실행 Job: `16219c68779b3d13`
- 실행 시각: **12:30:22.442–12:33:37.219 KST**, 약 **3분 15초**
- 원본: `ICR_CP_Join_20260918T033022Z_16219c68779b3d13.tar.gz`
- archive SHA256: `3108ecacc750a8b830e31f558c2cf363f00ea877beed46759389c7d610ba3262`
- 원본 내부 **15/15 SHA256 일치**, 제출 실행파일은 승인된 source와 byte 동일
- source: `bab9608557205b3bf5294b729d0960517d7a2847`
- 실행파일 SHA256: `460b5f8603fe2798e6f379b85efd5cb56926fd9f37308fb961c204e14c6d8147`
- [독립 코드 승인](https://github.com/321Team/infra-kubernetes/pull/16#pullrequestreview-5243927101), [운영자 결정 및 리뷰 조건 연결](https://github.com/321Team/infra-kubernetes/pull/16#issuecomment-5724697399)

기존 `SHA256SUMS`와 15개 원본 payload를 그대로 보존했다. 새 검토 문서는 `REVIEW_SHA256SUMS`로 별도 확인한다. 첨부의 private backup/토큰/인증서 키 원문은 공유 파일에 포함되지 않는다.

## 단계별 실제 결과

| 단계 | 결과 | 근거 |
|---|---|---|
| CP1 시작 상태 | CP1 단독 Ready·etcd voter·API ready 정상, 기존 UID/member ID 보존 | `CP1_BEFORE.json` |
| CP2·CP3 기존 방화벽 | 각 CP 허용 6개와 LB 허용 1개 존재, 변경 없이 조회 | 각 `BEFORE.json` |
| CP1 Runtime 인계 | 기존 회수 timer active→inactive, Runtime 해시 동일, 인계 성공 | `TRANSFER_BEFORE.json`, `TRANSFER.json` |
| CP2 정리 | root 전용 백업·지문 검증 후 기존 manifest 3개 격리, PKI/kubeconfig 보존, reset 없음 | CP2 `PREPARE.json`, `JOIN.json` |
| CP2 Join | **12:31:09 KST rc=0**, timeout 없음. 기존 인증서·kubeconfig 사용, static manifest 및 kubelet 설정 생성 | CP2 `JOIN.json` |
| CP2 확인 | **12:31:54 KST** CP1·CP2 Ready, 2 voter·2 endpoint health 정상 | CP2 `AFTER.json` |
| CP3 정리 | 빈 manifest 경로 확인, 초기 정리 생략 | CP3 `PREPARE.json` |
| CP3 Join | **12:32:44 KST rc=0**, timeout 없음. 인증서·kubeconfig·static manifest 생성 | CP3 `JOIN.json` |
| 최종 클러스터 | **12:33:36 KST 3CP Ready, 3 voter, learner 0, endpoint health 3/3 정상** | CP3 `AFTER.json` |
| 임시 인증정보 회수 | 승인된 helper가 `REVOKED` 반환 | `CREDENTIAL_REVOCATION.json` |
| 전체 종료 | `THREE_CP_READY_RUNTIME_RETAINED_REVIEW_REQUIRED` | `RESULT.json` |

## 최종 Node·etcd 식별

| 노드 | Node UID | etcd member ID | 상태 |
|---|---|---|---|
| k8s-control01 / .102 | `75735bd5-937a-463f-a93f-a8e7e51fa7a1` | `1755679419291778130` | control-plane Ready / voter / healthy |
| k8s-control02 / .103 | `dc01d1a4-037a-4870-8349-46f8ac8cd666` | `13644800064703665051` | control-plane Ready / voter / healthy |
| k8s-control03 / .104 | `999f59ae-63fa-4693-aa2a-dea0504c3c7d` | `8667064845340811211` | control-plane Ready / voter / healthy |

각 member의 peerURL은 자신의 `https://10.1.93.10[2-4]:2380`, clientURL은 `:2379`다. 최종 세 endpoint의 cluster ID `4016449463138308410`, leader CP1, revision `742034`, raft index/applied index `1113661`, term `6`이 일치한다. 정수 ID를 부동소수점으로 변환하지 않고 원본 그대로 대조했다.

## 타이머·회수의 의미

기존 CP1 Job `c564ae97530dfe80`의 **15:15 KST 자동 회수 예약은 이 실행의 인계 단계에서 정지됐다.** 인계 전후 Runtime SHA256은 `5bd288c327fd1224b9bc60f2973cd6456d4681b8007a952e37632ee101e71457`로 같다. 새 root 인계 영수증의 반환 해시는 `9029c16573132813cb63b125fc099d7ceeace7868652087693e4189b8a6af6e1`다. 성공한 3CP와 Runtime 연결을 유지하는 계약으로 넘어갔으므로 과거의 “15:15 자동 회수 예정”을 최신 실행 상태에 다시 적용하지 않는다.

이 archive에는 root `TRANSFER_COMMITTED.json` 원문 대신 승인 helper가 반환한 영수증 해시·성공 상태가 있다. 타이머와 Runtime 동일성 확인 시점은 Join 이전의 인계 단계이며, Join 후 전체 방화벽/인계 status를 다시 수집한 결과는 아니다. 현재 시점의 새 원격 조회나 reboot/reload 지속성을 주장하지 않는다.

임시 인증정보 회수는 이번 Join 토큰, `kubeadm-certs` 및 해당 upload-certs TTL 소유 Secret의 정확한 UID/resourceVersion 조건을 사용하는 승인 코드의 `REVOKED` 결과다. 각 삭제 API 응답 또는 별도 재조회 목록은 첨부되지 않았다. node helper의 root600 임시 Join 설정 파일은 `finally`에서 제거된다. 성공 경로였으므로 클러스터 rollback/reset/etcd member 제거 시험은 수행하지 않았으며 `recovery_complete=null`은 그 의미다.

## 경고와 인수 범위

두 Join 로그에는 KubeProxyConfiguration `bindAddress` 권고값 `::` 대비 설정값 `0.0.0.0`, firewalld 활성 시 6443/10250 허용 확인 경고가 있다. 경고를 숨기지 않고 원본에 보존했다. 두 명령 rc=0과 실제 제어면/etcd 건강 확인은 통과했으므로 이 경고를 Join 실패로 판정하지 않는다. 반대로 이를 모든 Service/CNI 경로의 정상 증거로 확대하지 않는다.

**CP2·CP3 미가입으로 인한 구축 차단은 이번 증적으로 해소됐다.** 다음은 Kubernetes 담당자의 Worker 가입 및 클러스터 후속 작업, LB 담당자의 3/3 backend·인증 API·전환/복구 검증이다. 기존 CP 정리·Join·CP1 5-flow·VM 복구 자산 점검을 반복할 필요는 없다.

Worker 가입, LB backend 3/3 실측, 노드 간 CNI 흐름, HEP/GNP 인계, Permanent 승격, reboot 지속성, 전체 6/6 구축 및 ICR AC 완료는 이번 결과의 범위 밖이다. `independent_acceptance=PENDING`, `cp1_accepted=false`를 실제 결과와 구분해 유지하고 이 제한된 CP 완료로 티켓 전체를 Done 처리하지 않는다.
