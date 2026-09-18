# ICR 다음 단계 현재 상태 확인

3CP Join 성공 이후 Worker 후속 작업의 분기를 결정하는 현장 1회 수집입니다. 기존 CP1 시험·VM 백업·NTP 전체 수집을 반복하지 않습니다.

`ansible01`의 `server01_ansible` 계정에서 실행합니다. 기본 실행은 PLAN만 출력하며, `--collect`가 기존 SSH 키로 CP1과 Worker 3대를 조회합니다.

```bash
python3 -I -B RUN_ICR_NEXT.py --collect
```

- CP1: Node 이름·UID·InternalIP·역할·Ready, Calico 관련 Pod의 상태만 수집합니다. 기존 admin.conf의 CA를 사용해 LB `10.1.93.109:6443`에 인증 주체 조회와 Node GET을 각각 수행합니다. proxy 환경을 제거하고 kubeconfig의 proxy/TLS 우회/외부 인증 플러그인은 거부합니다.
- Worker 3대: 호스트/IP, 버전·서비스 상태, kubelet/PKI 파일의 존재·권한, manifest/CNI 설정 파일명, LB와 monitor01로 향하는 route를 조회합니다. monitor01 `10.250.10.30` 경로는 실제 source/device/gateway와 `via 10.1.93.58` 일치를 기록하며 연결 시험은 하지 않습니다. 파일 본문·Secret·인증키·전체 Pod 설정은 출력하지 않습니다.
- 설정 변경, join/reset, firewalld/HEP 조작, 설치, 시간 변경, 자격증명 생성은 없습니다. `auth whoami`는 현재 인증 주체를 조회하는 SelfSubjectReview이며 새 토큰을 발급하지 않습니다.

마지막 `ARCHIVE`와 `SIDECAR` 두 파일을 제출합니다. 한 노드의 오류는 나머지 수집을 중단하지 않습니다. 종료코드 2는 미수집/조회 불가 항목이 있음을 뜻하며 생성된 증적을 먼저 확인합니다.

`six_of_six_ready`는 정확한 6개 Node 이름/IP/역할/Ready의 현재 관측일 뿐입니다. Calico 전체 인수, HEP 실행 승인, LB backend 3/3·장애 전환, ICR 전체 AC를 PASS로 만들지 않습니다. Worker 사전 파일 존재도 정리/Join 자동 승인이나 모든 사용자 지정 경로의 완전 점검을 의미하지 않습니다.

monitor01 경로의 이번 관측은 후속 입력입니다. AC-14의 최종 보호 전환 후 PASS 또는 AC-01의 과거 증거로 자동 판정하지 않습니다.

원격 명령은 최대 20초, 노드별 SSH는 최대 240초입니다. 이는 실패한 조회가 무한 대기하지 않게 하는 명령 timeout이며 Runtime 회수 타이머가 아닙니다.
