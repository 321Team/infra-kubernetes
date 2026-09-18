# CP2 manifest 출처 확인 및 Join 전 관측 수집

2026-09-18. CP2·CP3 Runtime 방화벽 준비 완료 뒤 누락된 현장 관측을 한 번에 수집합니다. 이 실행파일은 새 읽기 전용 운영 보조 코드이며 기존 Runtime 소스의 독립 승인에 포함됐다고 표시하지 않습니다. 새 승인 JSON·작업창 입력·CP1 시험 반복 없이 실행합니다. 실제 Join·정리 작업을 승인하거나 실행하는 도구는 아닙니다.

## 담당자가 배치한 v4 패키지와 manifest의 관계

| 경로 | 확인 가능한 의미 | 아직 확인되지 않은 점 |
|---|---|---|
| `/opt/fds/P1-K8S-01-v4` | 담당자가 전달한 설명상 스크립트·설정·문서 패키지 경로 | 실제 설치 파일, 실행한 명령, manifest 생성·복사 여부 |
| `/etc/kubernetes/manifests` | CP2 기존 증적에 API server/controller-manager/scheduler 파일 3개 존재 | 생성 주체·실패 시점·현재 kubelet이 사용하는 경로와 실행 상태 |

패키지를 복사해 둔 사실만으로 다른 경로의 static Pod manifest가 자동 생성되지는 않습니다. 다만 패키지 스크립트에서 kubeadm 명령이나 파일 생성·복사를 수행했다면 두 경로의 파일이 연관될 수 있습니다. 경로가 다르다는 이유만으로 무관하다고 판단하지 않습니다. kubelet이 사용하는 manifest 경로는 실제 `staticPodPath` 또는 관련 시작 인수로 확인해야 합니다.

CP2의 세 파일은 2026-09-18 제출 증적에서 모두 최종 수정시각(mtime) **2026-09-09 21:52:32 KST**였고, 9월 18일 방화벽 적용 전후에도 메타데이터가 같았습니다. 이는 파일 생성시각이나 실행 주체의 증명이 아닙니다. 보존 복사로 mtime이 유지될 수도 있습니다.

| 파일 | 바이트 | 원본 mtime_ns |
|---|---:|---:|
| kube-apiserver.yaml | 3659 | 1788958352896031827 |
| kube-controller-manager.yaml | 2939 | 1788958352897031813 |
| kube-scheduler.yaml | 1726 | 1788958352898021473 |

[해당 원본 inspection](https://github.com/321Team/infra-kubernetes/blob/8f3bd984da07702b6a908e36cbb38d456a0335f1/ops/cp-resume-20260918/results/20260918T013655Z_7637712f/CP2_APPLY/INPUT_INSPECTION.json). 원본 archive SHA256은 `21261c037d3d34cf7db5775565641136fc921b3b83324cf1f663db11b2bb9427`입니다.

세 manifest와 기본 kubelet 설정 부재는 불완전한 CP 구성과 일치하는 정황입니다. **실패한 kubeadm join의 잔재라고 확정한 사실은 아닙니다.** kubeadm에는 manifest만 생성하는 별도 단계도 있습니다. 원래 실행 명령과 stdout/stderr는 이번에 제공된 이미지에 없습니다.

## 한 번에 수집하는 범위

- CP1: 승인된 helper 원본의 topology 조회와 기존 Job `c564ae97530dfe80` status 조회. Node·etcd 상태와 현재 회수 예약을 관측합니다. 기존 회수시각이나 결정을 변경하지 않습니다.
- CP2: manifest 디렉터리 전체 파일 목록(확장자 무관, 제한 초과 표시), 해시와 허용한 설정값, 기본·발견된 사용자 지정 static Pod 경로. manifest 원문·kubeconfig·인증서·토큰은 제출하지 않습니다.
- CP2·CP3: 버전, kubelet/containerd 서비스 상태, 제한된 시작 인수, 알려진 잔여 경로, containerd 컨테이너 목록과 주요 포트 listener, 노드 IP를 출발지로 한 LB `10.1.93.109:6443` TCP 연결.
- CP2·CP3: `/opt/fds/P1-K8S-01-v4`의 제한된 파일 목록·해시·시각 및 kubeadm/manifest 관련 문자열 존재 여부. 스크립트는 실행하지 않으며 원문·비밀값을 출력하지 않습니다. 문자열 존재는 실행 증거가 아니고, 부재는 무관함의 증명이 아닙니다.

원격에서 설정·manifest·서비스·방화벽·타이머를 수정하지 않습니다. CP1 기존 status는 기존 lock을 잠시 사용하고 임시 조회 cache를 만들 수 있습니다. 새 대상 파일을 배포하지 않고 Python 표준입력으로 조회합니다. ansible01에 결과 JSON과 압축파일을 만듭니다.

실패한 조회는 `INCOMPLETE` 또는 partial로 남기고 가능한 나머지 수집을 계속합니다. 비정상 CRI 응답을 빈 정상 목록으로 간주하지 않습니다. TCP 성공은 API/TLS/etcd/LB backend 건강이나 Join 준비 완료가 아닙니다. 결과의 `join_readiness`는 계속 `NOT_ASSESSED`입니다. systemd 환경변수·사용자 지정 CRI endpoint 전체를 해석한 도구도 아닙니다.

파일 읽기는 root 소유·신뢰 가능한 일반 파일로 제한하고 symlink·예상 밖 권한·크기 초과는 내용 수집을 생략해 명시합니다. 패키지 검색은 깊이 4, 파일 128개, 디렉터리 32개, 파일별 256 KiB, 전체 4 MiB로 제한합니다. 수집 범위 밖 파일이나 실행 이력의 부재를 단정하지 않습니다.

## 현장 실행

`ansible01 / server01_ansible`에서 `READ_CP_PREJOIN.py`의 SHA256을 확인한 뒤 한 번 실행합니다. controller에 sudo를 붙이지 않습니다. 기존 SSH key·known_hosts·송신 IP를 사용하며 추가 승인 문구를 받지 않습니다.

```bash
python3 -I -B READ_CP_PREJOIN.py
```

마지막의 `ARCHIVE=...tar.gz`, `SIDECAR=...tar.gz.sha256` 두 파일을 전달합니다. partial 결과도 그대로 전달합니다. manifest 삭제·kubeadm reset/join·타이머 변경을 추가 실행하지 않습니다. 이 수집은 실제 정리 대상과 다음 Join 계획을 확정할 입력입니다.

검토 시에는 CP1 Node/member 존재, CP2 기존 manifest의 구성·해시·컨테이너 실행 상태, 패키지의 생성 경로 단서를 함께 봅니다. 기존 Node/member가 있으면 신규 Join이 아니라 기존 구성 확인 대상으로 분류합니다. 패키지 문자열·mtime만으로 삭제나 reset을 결정하지 않습니다.

## 검증·출처

- 새 관측기 구문 검사 및 실패 수집 보존·SSH 신원 고정·비밀값 제한·symlink·문자열 증거 구분을 확인하는 **8/8 STATIC/MOCK PASS**. `test_prejoin_collection.py`, `VALIDATION.json` 참조. 실제 서버 실행·CI·Join 준비 판정은 미실행입니다.
- CP1 helper·policy는 승인된 `06286f31735a80982e50e5f94fd0e1f4a027eb8d` 소스에서 바이트 변경 없이 포함하며 해시를 확인합니다. CP1 Job의 원본 5파일도 실행 전 해시를 확인합니다.
- [Kubernetes static Pod 문서](https://kubernetes.io/docs/tasks/configure-pod-container/static-pod/): kubelet의 staticPodPath와 확장자 무관 파일 처리.
- [kubeadm join 단계 문서](https://kubernetes.io/docs/reference/setup-tools/kubeadm/kubeadm-join-phase/): `control-plane-prepare control-plane`은 control-plane manifest 생성 단계.
- [Kubernetes v1.34 preflight 구현](https://github.com/kubernetes/kubernetes/blob/v1.34.0/cmd/kubeadm/app/cmd/phases/join/preflight.go): control-plane preflight는 dry-run이 아니면 이미지 다운로드를 할 수 있어 이 읽기 전용 수집에서 실행하지 않습니다. 실제 설치 버전은 현장 관측값을 따릅니다.
- [완료된 방화벽 인계](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5723876092), [기존 통합 사후 결과 검토 요청](https://github.com/321Team/infra-kubernetes/pull/14#issuecomment-5723874084). 같은 승인·시험을 다시 요청하지 않습니다.

빌드는 저장소 루트 소스가 위 승인본과 같은 helper·policy 해시일 때 `python3 -I -B ops/cp-prejoin-20260918/build.py`로 재현합니다. 원격 실행 없이 단일 파일을 생성합니다.
