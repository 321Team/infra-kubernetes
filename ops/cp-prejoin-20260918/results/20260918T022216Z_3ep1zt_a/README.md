# CP Join 사전관측 검토 — 2026-09-18 11:22 KST

이번 제출 증적은 **CP2에 남은 manifest 3개와 현재의 미가입 상태, CP2·CP3의 LB TCP 연결 성공을 확인**한다. `/opt/fds/P1-K8S-01-v4` 패키지 배치와 manifest 생성 사이의 인과관계는 수집파일만으로 입증되지 않는다. Kubernetes 담당자가 확인한 9월 9일 CP2 Join 시도 이력을 함께 인계하되, 파일 생성 주체를 입증하는 감사로그로 취급하지 않는다.

## 증적 무결성

- 원본: `ICR_CP_Prejoin_20260918T022216Z_3ep1zt_a.tar.gz`
- 원본 SHA256: `b1cb34433e61884af5bdfbbd896312915d72164788d76fe62654ef7344b18640`
- 원본 `SHA256SUMS` 5개 항목 모두 일치했다.
- 제출 collector SHA256: `4ae9c5f63ab317bc4c287d5837ed461ad000a89441f2aa1a96f0f8515bdf3e6b`. 로컬 배포본과 바이트 단위로 동일하다. [배포 source](https://github.com/321Team/infra-kubernetes/blob/53786692a0b1de29d7c57c22e427bdc44380f296/ops/cp-prejoin-20260918/READ_CP_PREJOIN.py)
- 수집기간: 2026-09-18 11:22:16–11:22:25 KST. `configuration_mutation=false`, `join_executed=false`, `join_readiness=NOT_ASSESSED`.
- 이 디렉터리의 JSON 4개는 원본에서 변경 없이 복사했다. `ORIGINAL_SHA256SUMS.txt`는 원본의 체크섬 목록이다. 큰 collector 사본은 위 고정 commit의 파일을 참조한다. 별도 `SHA256SUMS`는 이 검토 디렉터리의 파일을 검증한다.

## 관측 사실

| 항목 | CP2 / 10.1.93.103 | CP3 / 10.1.93.104 |
|---|---|---|
| `/etc/kubernetes/manifests` | apiserver, controller-manager, scheduler YAML 3개 | 디렉터리 존재, 파일 0개 |
| kubelet | `activating / auto-restart` | `activating / auto-restart` |
| containerd | `active / running` | `active / running` |
| 기본 kubelet config·kubeadm flags·kubelet kubeconfig·bootstrap kubeconfig | 관측한 표준 경로 모두 없음 | 관측한 표준 경로 모두 없음 |
| `/var/lib/etcd`, 그 아래 `member`, manifest의 `etcd.yaml` | 없음 | 없음 |
| TCP 6443, 2379, 2380, 10250, 10257, 10259 listener | 관측 시점에 없음 | 관측 시점에 없음 |
| 자신의 노드 IP에서 `10.1.93.109:6443` 연결 | TCP PASS | TCP PASS |
| kubeadm / kubelet | v1.36.4 / v1.36.4 | v1.36.4 / v1.36.4 |
| containerd version | v2.3.5 | v2.3.5 |
| CRI container 목록 | `FileNotFoundError`, 수집 불완전 | `FileNotFoundError`, 수집 불완전 |

위 CRI 오류는 collector가 `crictl` 명령을 실행하지 못했다는 뜻이다. 컨테이너가 0개라는 뜻이나 containerd 장애라는 뜻이 아니다. 실효 kubelet 설정도 systemd 환경변수에 의한 재정의를 완전히 해석하지 않아 미확정으로 기록돼 있다. kubelet 재시작의 직접 오류 로그는 이번 증적에 없다.

CP1의 Kubernetes node 목록과 etcd member 목록에는 **k8s-control01만** 존재했다. CP1 API `readyz` 확인이 통과했으며, topology의 canonical SHA256 재계산 결과는 제출값 `b4eb4932d00f53396e2fd55e704eb4246ce6a3d4a963d1029472f16c43e0c4af`와 동일하다. 이는 해당 관측 시점의 목록이며 과거의 모든 가입 시도를 부정하지 않는다.

CP1은 **11:22:24 KST**에 기존 job `c564ae97530dfe80`의 `CP_SESSION_ACTIVE_REVIEW_PENDING`, `recovery_armed=true` 상태였다. 기존 회수시각 **2026-09-18 15:15 KST**와 decision·handoff·change receipt hash를 유지했다. `cp1_accepted=false`, 독립 최종 인수는 `PENDING`이다. 본 관측 작업은 CP1 적용이나 타이머 변경을 수행하지 않았다.

## 패키지와 manifest 관계

양 노드의 `/opt/fds/P1-K8S-01-v4`에서 검사한 **19개 텍스트 파일(스크립트 10개, 설정·문서 9개), 총 131,279바이트의 파일별 SHA256이 모두 동일**했다. 검사 범위 내 스크립트에서는 kubeadm join/init/control-plane-prepare, static manifest 경로·파일명 관련 검색 표지가 발견되지 않았다. `RUNBOOK_FINAL.md`에는 `kubeadm reset` 문자열이 있었다. 문자열 존재는 실행 이력이 아니다.

같은 검사대상 패키지가 있는 CP3의 manifest 디렉터리는 비어 있고 CP2에는 3개가 있다. 따라서 **패키지가 배치돼 있다는 사실만으로 세 manifest를 설명할 수 없다.** 다만 제한된 텍스트 표지 검색 결과는 외부 호출·동적 명령·별도 작업 이력까지 배제하지 못한다. 원본의 판정도 `relationship_to_static_manifests=NOT_ESTABLISHED`다.

CP2 파일별 원본 해시는 다음과 같다.

| 파일 | SHA256 |
|---|---|
| `kube-apiserver.yaml` | `9c6c75d7ee5981c39386c7fde8039a8b9cb9bdcd88f6951c1e4e3815593a09c4` |
| `kube-controller-manager.yaml` | `8acf707b42c2b28f9ea7481082315b63174fb9abf09ba3ecde1b4c7d59978bd6` |
| `kube-scheduler.yaml` | `22a0cf186029c3d93743f6e9838a75b2becc6c937024727d42b2da995147f035` |

세 파일의 mtime은 **2026-09-09 21:52:32 KST**이며, 앞선 CP2 방화벽 적용 전 증적의 크기·mtime·소유자·권한과 동일하다. mtime은 마지막 수정시각이다. 생성시각, 생성자 또는 사용한 명령을 확정하지 않는다. apiserver의 `--advertise-address=10.1.93.103`과 `--etcd-servers=https://127.0.0.1:2379`는 CP2용 구성과 일치하지만 수동 복사·수정 가능성을 배제하는 증거는 아니다.

## 담당자 이력과 다음 작업

[Kubernetes 담당자 회신](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5724159810)은 9월 9일 CP2에서 `kubeadm join --control-plane`을 시도했고 `check-etcd` 오류를 겪었다고 확인한다. 이 회신은 **작업자 확인 이력**으로 사용한다. 현재 첨부에는 당시 원본 실행로그가 없으므로 정확한 실패 지점 및 manifest 생성 명령의 인과관계를 독립적으로 재현·입증한 것은 아니다.

이번 관측 버전 v1.36.4의 공식 소스에서는 `control-plane-prepare → check-etcd → kubelet-start → etcd-join` 순서다. `check-etcd`는 기존 etcd cluster 상태 확인이며, 새 로컬 member 추가 단계인 `etcd-join`과 다르다. 따라서 담당자의 실패 단계 설명을 전제로 하면, manifest 3개가 존재하면서 kubelet 설정이 없는 현재 상태와 순서상 부합한다. "check-etcd에서 실패했으므로 이미 kubelet이 시작됐거나 로컬 etcd member가 추가됐다"고 해석할 근거는 없다. [v1.36.4 단계 등록](https://github.com/kubernetes/kubernetes/blob/v1.36.4/cmd/kubeadm/app/cmd/join.go), [check-etcd 구현](https://github.com/kubernetes/kubernetes/blob/v1.36.4/cmd/kubeadm/app/cmd/phases/join/checketcd.go)

다음 업무는 Kubernetes 담당자의 **기존 실패 Join 잔여 파일 처리 및 재가입 절차 결정**이다. 이 증적은 그 판단에 필요한 현재 상태와 파일 지문을 제공한다. 이미 검증된 CP2·CP3 Runtime 방화벽 적용을 되풀이할 이유로 사용하지 않는다. 아직 미확인인 실제 container 목록·실효 kubelet 설정, manifest가 참조하는 controller-manager·scheduler kubeconfig 및 PKI의 상태는 담당자의 잔여 파일 정리·재가입 계획에서 함께 다루면 된다. 별도 전체 사전관측 재실행을 요구하지 않는다. 본 검토는 파일 삭제·reset·Join 실행을 수행하지 않았다.

LB TCP PASS는 노드에서 LB listener까지 연결됐다는 뜻이다. LB의 CP backend 3/3 정상, TLS·인증, 노드 간 etcd 패킷, 전체 Join readiness를 입증하지 않는다. 전체 ICR 인수나 Kubernetes 3CP 구축 완료로 집계하지 않는다.
