# worker03 serving TLS 변경 전 누락 입력 조회

분류: **순수 Pre-check**. 책임 티켓: baseline #10/#19/#17/#14.
운영 후보 [PR31](https://github.com/321Team/infra-kubernetes/pull/31)의 실행 전 입력을 보완한다.
기존 04:11 4-GET 수집, 6대 인증서, Source/packet/counter를 반복하지 않는다.

## 읽는 내용

| 대상 | 추가 조회 | 보관 결과 |
|---|---|---|
| worker03 | ens160 IP, kubelet MainPID/실제 argv, 로컬 config.yaml, 소유자·모드·SELinux·크기·inode·mtime/ctime | 선택된 TLS/인증 설정, 중복 키 없는 단일 YAML 판정, 설정 SHA256·메타데이터·CLI 충돌 여부 |
| control01 | 같은 Prom Pod UID/SA/node/IP/container 상태, localhost status/config·targets API, 마운트된 공개 CA | 실제 로드된 두 kubelet job의 CA/인증 파일 참조·TLS 값·구성 hash, 현재 전체 target 수와 worker03 두 target 요약 |
| control01/02/03 | /etc/kubernetes/pki/ca.crt 공개 인증서 | DER SHA256 지문과 Prom CA bundle 포함 여부 |

Pod는 기존 UID `46c00f91-2073-440a-800c-6cedba89d291`로 고정했다. 수집 중 container/UID/Ready가 바뀌면 실패한다.
worker03의 PID와 설정 메타데이터도 전후 대조한다. 이는 모든 입력의 원자적 snapshot을 보장하지 않는다.
Prom CA는 기존 후보의 `/var/run/secrets/kubernetes.io/serviceaccount/ca.crt` 한 파일만 읽는다.
로드 설정이 다른 CA/inline CA/proxy/client 인증서를 사용하면 해당 파일을 임의 추적하지 않고 HOLD로 기록한다.
service-account token·개인키·kubelet.conf·Secret·환경 파일 내용은 읽거나 저장하지 않는다.

## 실행 및 데이터 처리

ansible01의 기존 server01_ansible 계정·checkout·env·inventory·svc_ansible SSH와 기존 sudo -n 권한을 사용한다.
원격 실행은 raw 안의 Python 표준 라이브러리와 읽기 명령뿐이다. Ansible module 설치/전송, 설정 쓰기, 재시작, CSR 생성·승인, CA/정책 변경은 없다.
kubectl cache-dir은 /dev/null이고 Pod GET은 고정 raw API 경로다. 캐시 저장 실패 경고는 수집 데이터로 출력하지 않는다.
각 원격 subprocess는 25초 제한, kubectl 15초, wget 10초다. 기존 권한이 없거나 도구가 없으면 실패하며 권한·패키지를 추가하지 않는다.

완전한 로컬 설정·실제 argv·Prom 로드 설정은 `no_log: true` 태스크의 메모리에서만 처리한다.
컨트롤러의 기존 PyYAML로 duplicate key/복수 문서/alias를 거부하고 선택된 공개 필드만 보관한다.
원격 호스트에 PyYAML은 필요 없다. 예외 메시지나 원본 stderr를 결과에 복사하지 않는다.
fact cache는 사용하지 않는다. no_log를 무시하는 사용자 callback이 있다면 이 방식으로 실행하지 않는다.
Ansible DEBUG/verbose/debug registered-variable dump 또는 원격 remote.sh 단독 실행을 하지 않는다.
`remote.sh` stdout에는 민감할 수 있는 구성 전송 데이터가 있으므로 반드시 동반 playbook을 사용한다.

실행 전 담당 Lead는 위 읽기 범위를 확인한다. 중앙 REVIEW_AUTHORITY_POLICY §3.2 4-1 조건의 순수 검증이다.
`run.sh <병합 PR의 40자리 source commit>`은 ansible01의 새 MON_TLS_INPUTS.* 폴더에 동일 SHA의 5개 소스 파일을 받고,
기존 inventory로 실행한 뒤 JSON·실행 로그·소스/결과 hash와 tar.gz/sha256을 만든다.
run.sh는 호출한 자기 bytes와 다운로드한 해당 SHA의 run.sh가 같은지 먼저 비교한다.
서버 변경은 없으며 컨트롤러 결과와 임시 파일은 새 작업 디렉터리에만 남는다.
기존 결과를 덮어쓰거나 삭제하지 않는다. 원격 설정/전체 argv/토큰/개인키/Ansible temp는 archive에 넣지 않는다.

## 결과 해석

- `collection_ok=true`: 네 호스트 읽기·파싱이 성공했다는 뜻이다. pending=0 또는 운영 적용 가능이라는 뜻이 아니다.
- `all_three_signer_ca_in_prom_bundle=true`: CP3 공개 CA가 동일한 단일 인증서이고 Prom 마운트 bundle 안에 DER 지문이 있다는 뜻이다.
  CA 유효기간·BasicConstraints·서명 개인키 가용성·실제 signer 선택/발급·애플리케이션이 메모리에 보관한 trust·새 serving leaf TLS 검증까지 증명하지 않는다.
- 실제 CA 설정, CLI override, YAML 형태가 후보와 다르면 pending으로 남기고 PR31을 수정한다. 누락 값을 활성화로 추정하지 않는다.
- current target 결과는 새 관측으로 기록하며, 이전 6 UP/14 DOWN을 덮어써 과거 증적을 바꾸지 않는다. 수집은 1회이며 연속 scrape 인수 시험이 아니다.
- 수집 실패도 결과를 보존한다. summary에서 어떤 호스트가 실패했는지 해당 JSON/Ansible 상태와 대조한다. 재수집은 실패한 원인 해결에 필요한 범위로 한정한다.

아직 별도인 항목: PR31의 독립 운영 코드 리뷰, 실제 변경창/복구 원본 고정, 기존 SSH 복구 쓰기 권한과 CSR 검토자,
단건 CSR 발급/승인 및 새 인증서/TLS/RBAC/연속 scrape 검증. 이 도구는 이들을 자동 통과시키지 않는다.
PR27 네트워크 canary와 evidence PR23은 다른 작업이며 변경하지 않는다. 기존 담당 K8S/SEC 이하영·MON 이재환·ICR 지원 이권욱을 유지한다.

## 검증

- STATIC: Bash/Python/YAML 파싱 및 원격 raw/메모리 처리·로컬 저장 경계 대조.
- MOCK/UNIT: tests/test_inputs.py, 구성 비출력·중복 YAML·CLI 충돌·호스트 실패·CA 불일치·잘못된 인증서·로드된 job 참조를 검사.
- Ansible --syntax-check / 실제 SSH/권한/Prom API: 이 개발 환경에는 Ansible이 없어 NOT RUN. 배포 환경의 play 실행 결과를 별도로 수신한다.
- CI: PR의 실제 run 참조. FIELD COLLECTION: NOT RUN. RUNTIME: NOT RUN.
- REVIEW=NOT PERFORMED (Pre-check). 기존 PR31 운영 코드 리뷰 의무를 대체하지 않는다.

공식 참고:
- https://docs.ansible.com/projects/ansible/latest/reference_appendices/logging.html#protecting-sensitive-data-with-no-log
- https://prometheus.io/docs/prometheus/latest/querying/api/#config
- https://kubernetes.io/docs/tasks/administer-cluster/kubelet-config-file/

AI 사용: CODEX_ASSISTED.
