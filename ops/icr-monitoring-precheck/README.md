# ICR monitoring Pre-check — 2026-09-24

책임: ICR-D-NET-01-01 Lead 이권욱. 연계: baseline #14·#17·#19.
현재 sender(Prometheus)의 Pod UID/노드/IP/SA/imageID와 실제 HEP/GNP·NetworkPolicy를
관측하여 다음 IF-06/IF-06B 점검 대상을 고정한다. 정책 변경·통신 시험·AC 판정은 하지 않는다.

## 분류와 실행 범위

[중앙 REVIEW_AUTHORITY_POLICY §3.2](https://github.com/321Team/fds-project-baseline/blob/main/governance/REVIEW_AUTHORITY_POLICY.md)
및 infra-kubernetes README의 **순수 Pre-check**다. 코드 자체 검토 결과:

- 기존 ansible01 → control01 승인 SSH 계정 `svc_ansible`만 사용한다. control01의 기존
  `sudo -n kubectl --kubeconfig=/etc/kubernetes/admin.conf` 권한이 없으면 STOP한다.
  새 계정·sudoers·RBAC·Secret·접속 예외를 만들지 않는다.
- 기존 Ansible의 임시 모듈 경로 외에 control01에 수집기를 설치/복사하지 않는다.
  Python 소스는 stdin으로 1회 실행한다. 상주 작업·패키지·설정·서비스·방화벽·NAT·route 변경 없음.
- API 서버 주소를 4개 기존 주소와 대조하고 6개 노드 이름/InternalIP가 모두 맞아야 조회를 계속한다.
- kubeconfig는 서버 URL만 출력한다. 총 17개 제한 조회(서버 주소 1 + API GET 16),
  각 프로세스 timeout 15초/API 10초, 각 출력 4 MB 한도. `get --raw` 고정 경로로
  discovery cache 생성을 피한다. Secret·ConfigMap·Pod env/args·개인키·kubeconfig 원문 미출력.
- 결과·실행 로그는 ansible01의 새 `ICR_MON_Precheck_*` 디렉터리에만 저장한다.
  Ansible stdout는 no_log로 보호하고 선택된 필드만 결과에 남긴다.
- 결과를 정책/배포 입력 또는 자동 인수 게이트로 소비하지 않는다.

REVIEW=NOT PERFORMED (Pre-check). 작성자 자체 검사·MOCK와 독립 코드 리뷰는 구분한다.
본 수집기의 병합/실행은 Runtime 적용·IF-06/06B PASS 또는 ICR 종결이 아니다.

## 실행 위치와 선행조건

ansible01의 기존 `server01_ansible` 작업 계정, 현재 사용 중인 `infra-ansible` checkout.
기존 승인 SSH 키와 host key 확인 정책, Ansible 설정을 유지한다. controller에는 기존
ansible-playbook/Python3/git/tar/sha256sum, control01에는 기존 Python3/kubectl/sudo가 필요하다.
원격 계정은 inventory의 `svc_ansible`이다. 입력 inventory를 새 사본으로 교체하지 않는다.

1. 이 디렉터리의 승인된 배포 SHA에서 `collect.py`, `collect.yml`, `run.sh`,
   `SHA256SUMS`를 새 로컬 작업 폴더에 가져온다. `sha256sum -c SHA256SUMS`로 확인한다.
2. 기존 infra-ansible checkout에서 다음을 실행한다. `SOURCE_SHA`는 가져온 전체 commit SHA다.

```bash
bash /가져온경로/run.sh "$PWD/inventory/hosts.yml" SOURCE_SHA
```

실행기는 먼저 Ansible syntax-check를 수행한다. 실패 시 원격 실행하지 않고 오류 묶음을 남긴다.
Git HEAD, tracked 변경 여부, 실제 inventory SHA, 수집기 입력 바이트 SHA, 계정·대상·KST·명령·rc,
선택된 관측값과 stderr를 보존한다. 기존 checkout pull/reset이나 untracked 파일 삭제는 하지 않는다.

## 정상·실패 판정과 반환물

- 정상 수집: `RC=0`, 결과 `COLLECTED_PENDING_ANALYSIS`, `runtime_ac=NOT_ASSESSED`.
- `STOP`, 대상 불일치, 조회 Forbidden/timeout, sender 부재/중복/비Ready, 도구 부재는
  권한/네트워크 변경으로 우회하지 않는다. 원인 분석 전 다음 단계로 진행하지 않는다.
- controller `RC!=0`도 archive·sidecar가 생성되면 그대로 보존한다. SSH 연결 자체 실패처럼
  수집 JSON이 없으면 Ansible 오류 로그만 남을 수 있다. 부분 수집을 PASS로 합산하지 않는다.
- 마지막 `ARCHIVE=...tar.gz`, `SIDECAR=...tar.gz.sha256`를 승인된 증적 전달 경로로 제공한다.
  `SHA256SUMS`는 무결성 확인용이며 통신 성공 증거가 아니다.
- API 응답 전체 원문은 저장하지 않는다. `stdout_sha256`는 수신 응답의 해시이며 결과 JSON은
  제한된 진단 필드다. 필터링된 결과를 전체 원본 로그라고 표현하지 않는다.

Ctrl+C이면 작업을 중단하고 생성된 디렉터리를 보존한다. 서버 변경이 없으므로 서비스/방화벽
복구 명령은 없다. 기존 증적·홈 디렉터리·저장소를 일괄 정리하지 않는다.

## 이후 우리 담당 범위

1. 결과에서 Prometheus Pod UID/노드/IP/SA와 설치된 Calico 이미지 버전, HEP/GNP의
   selector/tier/order/expectedIPs 및 namespaced 정책을 실제 값으로 대조한다.
2. 9100/10250 timeout을 정책에 의한 차단으로 단정하지 않는다. 기존 worker03 TLS IP SAN
   오류는 교차 노드 timeout과 분리한다. Pod CIDR 전체 허용이나 TLS 검증 우회로 해결하지 않는다.
3. IF-06B는 식별된 sender의 제한된 연결 시도와 monitor01 수신 패킷 헤더를 같은 창에
   연결하고, 실제 관측 Source 및 return route를 고정한다. PodIP/hostIP를 수신 Source로 대입하지 않는다.
4. sender·관측 인터페이스가 확인되기 전 패킷 캡처나 능동 시험 명령을 임의 확정하지 않는다.
   monitor01 receiver/TLS/Auth/Payload 입력·서비스 통합은 MON Lead의 기존 업무다.

MON PR7 merge `a885cef2358682214095590baf69b77062978778` 및 main CI 성공은 확인됐다.
ICR AC08/13/14의 기존 PASS는 유지한다. 이번 수집은 Join·route·관리 경로 복구를 반복하지 않는다.
PR21의 NodePort 독립 리뷰와 이 읽기 전용 수집은 서로의 선행 대기조건이 아니다.

## 검증 기록

LOCAL STATIC: Python AST·Bash 구문·YAML 파싱. MOCK: 11개(정상 수집, 비밀필드 미출력,
host/API/node 불일치, sender 부재/중복, timeout, Forbidden, malformed JSON, 실행파일 소유권).
모든 입력은 합성이다. 로컬에 Ansible이 없어 실제 Ansible syntax-check·원격 통합은 NOT RUN.
현장 실행기에서 syntax-check를 먼저 강제한다. CI 및 실제 서버 조회는 별도 상태다.

CLI 근거: [Kubernetes kubectl get --raw](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_get/).
