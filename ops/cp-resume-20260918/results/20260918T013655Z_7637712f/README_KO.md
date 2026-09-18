# CP2·CP3 Runtime 재개 결과 — 2026-09-18

제출된 증적은 CP2·CP3의 승인 범위 Runtime 방화벽 적용 및 사후 상태 검증 성공을 입증합니다. 최종 기록은 `CP_FIREWALL_HANDOFF_REVIEW_PENDING`이며, 독립 결과 인수는 `PENDING`입니다. 이 문서는 제출 파일에 대한 사후 검증 결과이며 현재 서버를 실시간 조회한 결과는 아닙니다.

## 원본과 검증 범위

- 전체 원본: `ICR_CP_Resume_20260918T013655Z_7637712f.tar.gz`
- 전체 원본 SHA256: `21261c037d3d34cf7db5775565641136fc921b3b83324cf1f663db11b2bb9427`
- CP1 Job: `c564ae97530dfe80`
- 전체 원본 165개 일반 파일의 해시: `ARCHIVE_FILE_SHA256SUMS`에 기록. 경로는 원본 압축파일의 최상위 디렉터리 내부를 기준으로 합니다.
- 이 디렉터리에는 검토에 필요한 원본 16개 파일을 바이트 변경 없이 선별했습니다. 전체 원본 압축파일은 업로드된 자료로 별도 보존됩니다. 선별 자료·이 문서·전체 원본 목록의 해시는 `SHA256SUMS`에 기록합니다.
- 전체 증적 내 7개 `SHA256SUMS`가 참조하는 63개 파일, 중첩 CNI trial의 2개 파일과 trial 압축파일 해시가 일치합니다.
- 승인된 실행 코드의 authorization, fresh inspection, receipt, zone controls, CP1 session 및 live-state 검증 함수를 실제 증적 시각 기준으로 실행하여 통과했습니다. 신규 서버 명령이나 변경은 실행하지 않았습니다.
- 선별한 평문·JSON의 자격 증명 관련 필드와 개인키·토큰·인증 헤더 패턴을 점검했으며 실제 비밀값을 발견하지 않았습니다. 서버 IP·호스트명·파일 경로 등 내부 운영 정보는 포함됩니다.

## 소스와 승인 연결

| 구분 | 정확한 근거 |
|---|---|
| 최초 CP1 소스 | [cfae027fad62a36ee228da3217011fa0787c7486](https://github.com/321Team/infra-kubernetes/commit/cfae027fad62a36ee228da3217011fa0787c7486) |
| 최초 독립 소스 승인 | [PR #14 리뷰 5242208554](https://github.com/321Team/infra-kubernetes/pull/14#pullrequestreview-5242208554), 이하영 `hayeounglee`, APPROVED, 2026-09-18 07:53:09 KST |
| 정확한 소스 실행 위임 | [댓글 5723302935](https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723302935), 동일 리뷰어, 2026-09-18 09:48:11 KST |
| 실제 운영자 작업창 결정 | [댓글 5723463564](https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723463564), `GWONUK`, 2026-09-18 10:08:34 KST |
| 재개용 수정 컨트롤러 소스 | [06286f31735a80982e50e5f94fd0e1f4a027eb8d](https://github.com/321Team/infra-kubernetes/commit/06286f31735a80982e50e5f94fd0e1f4a027eb8d) |
| 수정 소스 독립 승인 | [PR #15 리뷰 5243182569](https://github.com/321Team/infra-kubernetes/pull/15#pullrequestreview-5243182569), 이하영 `hayeounglee`, APPROVED, 2026-09-18 10:22:52 KST |

수정 소스 승인과 기존 실행 위임·작업창을 구분하여 연결했습니다. CP1 source bundle은 `d229b8ba40716ffb4baeacf9ea80a009dc28c8155a9d38f4f84961fa4da28a41`입니다. 재개 진입점의 별도 SHA256은 `dc5f55bf5284860ba218e78e95fb0103cd2d0f834e72060c9aafa6d6d8502165`이며, `ENTRY_PROVENANCE.json`은 이 진입점 자체가 수정 컨트롤러 소스 리뷰에 포함된다고 주장하지 않습니다.

## 실제 적용 결과

| 대상 | 컨트롤러 적용·사후 확인 완료 (KST) | 신규 Runtime 규칙 | 삭제 규칙 | 결과 |
|---|---|---:|---:|---|
| CP2 `k8s-control02` / `10.1.93.103` | 2026-09-18 10:38:44.449407 | 6 | 0 | `RUNTIME_APPLIED_REVIEW_REQUIRED` |
| CP3 `k8s-control03` / `10.1.93.104` | 2026-09-18 10:40:26.711297 | 6 | 0 | `RUNTIME_APPLIED_REVIEW_REQUIRED` |

각 노드의 `public` zone에 아래 여섯 규칙만 신규 적용했습니다. 모든 변경 명령의 반환값은 `0`이며, 기존 `10.1.93.109/32 → TCP 6443` 규칙은 유지했습니다.

```text
rule family="ipv4" source address="10.1.93.102/32" port port="2379-2380" protocol="tcp" accept
rule family="ipv4" source address="10.1.93.102/32" port port="10250" protocol="tcp" accept
rule family="ipv4" source address="10.1.93.103/32" port port="2379-2380" protocol="tcp" accept
rule family="ipv4" source address="10.1.93.103/32" port port="10250" protocol="tcp" accept
rule family="ipv4" source address="10.1.93.104/32" port port="2379-2380" protocol="tcp" accept
rule family="ipv4" source address="10.1.93.104/32" port port="10250" protocol="tcp" accept
```

두 노드 모두 사후 실제 snapshot이 receipt의 after-state와 일치합니다. Permanent 설정, 다른 zone, policy, 잔여 Kubernetes 파일 메타데이터는 적용 전후 동일하며 SELinux는 `Enforcing`입니다. Fresh inspect에서 적용 시작까지 CP2 11.60초, CP3 11.47초로 확인됩니다. 각 작업은 해당 authorization window 안에서 완료됐습니다.

CP2 receipt의 의미상 SHA256은 `1e43af9bd0645e114a5b90e3194f480e01d15e2d6ed86e9f744f3a5ff6290cab`이며 CP3 authorization의 `cp2_result_sha256`와 일치합니다. 이는 `TARGET.json` 전체 파일의 SHA256과 구별됩니다. CP3 receipt의 의미상 SHA256은 `5540d9fc5fba14b7f51cbb52edffad11110f6ad80327ef7c02fa05bbaa3db6a7`입니다.

## CP1 보존 및 최신 증적 시점

CP1의 기존 `CHANGE`, `HANDOFF`, `SESSION_DECISION`, 5-flow 분석, HOST·외부 회귀 출력, trial 압축파일은 이전 증적과 바이트 단위로 동일합니다. 이번 재개에서 CP1 apply와 trial을 다시 실행하지 않았습니다. 기존 5-flow는 5/5 PASS이며 HOST·외부 회귀 출력과 session decision의 해시 연결도 일치합니다.

CP2·CP3 적용 전후 네 차례의 CP1 live-state 검증이 모두 통과했습니다. 최종 CP1 관측은 **2026-09-18 10:40:26.692695 KST**이며, `CP_SESSION_ACTIVE_REVIEW_PENDING`, `recovery_armed=true`입니다. 기존 예약 회수 시각은 **2026-09-18 15:15:00 KST**로 변경되지 않았습니다. 이 시각 이후 복구 여부나 현재 상태는 이 증적만으로 확정할 수 없습니다. CP1의 예약 회수가 CP2·CP3의 별도 Runtime 규칙까지 회수한다는 의미도 아닙니다.

## Kubernetes 담당자 인계 사항

이번 증적에서 확인한 범위는 방화벽 규칙 적용과 설정 일관성입니다. `independent_acceptance=PENDING`, `cp1_accepted=false`를 유지하며, 실제 Join은 실행하지 않았습니다. 교차 노드 패킷 시험, Join 준비 완료, 재부팅 지속성, Permanent 승격은 이 결과로 입증되지 않습니다. 5-flow의 negative 항목은 cleanup·drift·consistency 범위이며 deny·cross-node 시험을 뜻하지 않습니다.

CP2는 적용 전부터 아래 파일 세 개가 존재했고 적용 후에도 그 메타데이터가 동일했습니다. 신규 방화벽 결함은 아니며, Join·재구성 전에 Kubernetes 담당자가 기존 상태를 판단할 항목입니다.

```text
/etc/kubernetes/manifests/kube-apiserver.yaml
/etc/kubernetes/manifests/kube-controller-manager.yaml
/etc/kubernetes/manifests/kube-scheduler.yaml
```

점검 대상인 kubelet 설정·kubeconfig, etcd 디렉터리 및 etcd manifest는 CP2에서 없었습니다. CP3에서는 점검 대상 잔여 파일·디렉터리 모두 없었습니다. 해당 파일을 삭제하거나 `kubeadm join/reset`을 실행한 작업은 이번 증적에 없습니다.
