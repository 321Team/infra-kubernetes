# worker03 kubelet serving TLS 변경 후보 — 2026-09-24

상태: DRAFT / 운영 변경 / 독립 리뷰·실행 전 조건 확인 필요.
책임: K8S/SEC 이하영, MON 검증 이재환, ICR Source/경로 검증 지원 이권욱. 기존 담당 유지.
연계: fds-project-baseline #10/#19/#17/#14. 네트워크 PR27과 분리한다.

## 1. 수신 검증과 실제 확인

- 현장 시각: 2026-09-24T04:11:50.700222+09:00.
- 원본: MON_TLS_Readonly.EXji23b3.tar.gz, SHA256 bb01f484eec9457d2b01f5d938ffa5ef39672a79648dcd390505d098d72ccd1e. 외부 sidecar 일치.
- collect.sh SHA256 0faa23283c8e14e24ae19340f2a9a1587eb51ce3f8ffe09f67c3ea3d46aab007. PR29 source e48da5ee1745db5e1751d9ebec2a71146b8f48ca의 배포본과 일치.
- Ansible 결과 rc0, stderr 빈 값, run.txt와 구조화 stdout 일치. 고정 조회4/4 RC0, TLS_PRECHECK_FAILED=0.
- 이는 순수 조회 성공이다. 인증서 발급·승인·TLS/RBAC/스크레이프 인수 완료가 아니다. 원본 바이트를 재구성하거나 전체 원본 증적 편입 완료를 주장하지 않는다.

| 항목 | 관측 |
|---|---|
| Node | k8s-worker03 / InternalIP 10.1.93.108 / Hostname k8s-worker03 |
| 실제 kubelet configz | serverTLSBootstrap NOT_PRESENT, rotateCertificates true |
| 실제 serving 파일 경로 | /var/lib/kubelet/pki/kubelet.crt 및 kubelet.key |
| kubelet 인증·인가 | client CA /etc/kubernetes/pki/ca.crt, webhook 인증 true, anonymous false, authorization Webhook |
| kubeadm kubelet-config | serverTLSBootstrap와 tlsCertFile/tlsPrivateKeyFile 최상위 값 미명시, rotateCertificates true |
| CP3 controller-manager Pod spec | generic cluster-signing-cert-file=/etc/kubernetes/pki/ca.crt, key-file=/etc/kubernetes/pki/ca.key, controllers=*,bootstrapsigner,tokencleaner |

NOT_PRESENT/NOT_EXPLICIT은 수집기의 누락 표시이며 설정 값 문자열이 아니다. featureGates 누락을 모든 기능 비활성으로 해석하지 않는다.
서명 경로의 설정 존재는 실제 CA 키 가용성·발급 성공·Prometheus trust 검증과 다르다.

01:28 관측의 6대 DNS-only/IP SAN 부재를 재사용한다. 04:11에는 인증서를 재조회하지 않았다.
v1.36.4 InitializeTLS는 bootstrap이 꺼지고 cert/key 경로가 비었을 때 기본 kubelet.crt/key 경로를 채울 수 있다.
따라서 configz에 이 경로가 보인다는 이유만으로 명시적 파일/CLI override를 확정하거나 기존 인증서를 삭제하지 않는다.

## 2. 권장 변경과 범위

대상은 worker03 한 대, /var/lib/kubelet/config.yaml의 최상위 필드 하나다.

```diff
+serverTLSBootstrap: true
```

동반 파일 worker03-server-tls.fields.yaml은 **필드 병합 명세**다. 전체 kubelet 설정이나 Kubernetes API manifest가 아니며 기존 config.yaml을 이 파일로 덮어쓰거나 kubectl apply하지 않는다.
rotateCertificates, authentication, authorization, client CA 및 다른 모든 의미 값은 보존한다.
기존 kubelet.crt/key·kubelet client certificate·kubelet.conf는 삭제/교체/외부 복사하지 않는다.
controller-manager/CA/Trust/GNP/NetworkPolicy/Pod spec은 이번 후보에서 변경하지 않는다.

1차 canary 동안 공유 kubelet-config ConfigMap을 바꾸지 않아 다른 노드의 재가입/업그레이드 입력에 영향을 주지 않는다.
이는 **일시적 로컬/공유 설정 차이**이며 영구 완료가 아니다. 이 창 동안 worker03의 kubeadm upgrade/rejoin을 병행하지 않는다.
성공 수용 후 별도 전체 rollout 결정에 맞춰 공유 ConfigMap과 노드별 설정을 동기화한다. canary 실패 시 원래 로컬 설정으로 복구한다.

## 3. 적용 전에 한 번 확인할 입력

이미 수집한 6대 cert/firewalld/Source/counter를 다시 요구하지 않는다. 다음은 변경 직전의 입력 고정이며 이번 4-GET 묶음에 없는 범위다.

1. 담당 K8S/SEC Lead가 동일 SHA의 독립 리뷰와 작업창·실행자·복구 책임을 기존 PR에 기록한다. 새 ICR 공동 서명을 요구하지 않는다.
2. worker03 실제 kubelet 시작 인자의 config/config-dir/rotate-server-certificates/TLS 관련 override와 선택 feature gate를 확인한다. config 경로가 /var/lib/kubelet/config.yaml이 아니거나 override 충돌이 있으면 이 후보 적용을 중단하고 수정한다. 전체 환경변수/명령행을 증적에 덤프하지 않는다.
3. 해당 파일이 정상 KubeletConfiguration 단일 YAML이고 현재 serverTLSBootstrap이 없거나 false인지, 중복 키·복수 문서·문서 끝 마커·충돌하는 TLS 명시 값이 없는지 확인한다. 전체 설정을 결과에 출력하지 않고 원본 SHA256·소유자/모드/SELinux context와 수정 전 의미 값을 노드의 보호된 작업 경로에 보존한다.
4. Prometheus의 현재 Pod UID/node/SA를 확인하고, 실제 로드된 kubelet 두 job의 CA 파일·server_name·인증 참조와 public CA 지문을 확인한다. 토큰·개인키 값은 수집하지 않는다. 운영 repository YAML만으로 로드된 설정을 확정하지 않는다.
5. 서명에 사용할 public CA의 지문을 Prometheus trust와 대조한다. 컨트롤러 Pod spec의 key 경로만으로 발급 가능을 PASS로 표시하지 않는다.
6. worker03 SSH 복구 경로와 기존 권한이 사용 가능한지 확인한다. Ansible 모듈 전송/become이 기존 권한으로 불가능하면 sudo/RBAC를 확대하지 말고 기존 승인된 운영 실행 경로로 조정한다.
7. 현재 Node/Pod 상태와 두 job의 worker03 대상 lastScrape/error를 기록한다. 승인된 관리 경로 회귀 명령과 CSR 검토자가 준비돼 있어야 한다.

## 4. 검토 대상 실행 순서

아래 명령은 **후보**이며 리뷰 전 실행하지 않는다. 기존 ansible01 checkout/env/inventory를 사용한다.
단일 필드 편집 전에 위 입력과 원본 SHA를 재대조하고, 백업 경로를 수신 즉시 보존한다.

```bash
ANSIBLE_CONFIG=/home/server01_ansible/p1-ans-01/ansible.cfg \
ansible -i /home/server01_ansible/p1-ans-01/inventory/hosts.yml \
  k8s-worker03 -b -e '{"ansible_ssh_use_tty":false}' \
  -m ansible.builtin.lineinfile \
  -a '{"path":"/var/lib/kubelet/config.yaml","regexp":"^serverTLSBootstrap:","line":"serverTLSBootstrap: true","insertafter":"EOF","create":false,"backup":true}'
```

- 파일 diff 및 YAML 의미 대조에서 serverTLSBootstrap=true 하나만 바뀌었는지 검증한다. 검증 실패면 kubelet 재시작 전에 정확한 원본으로 복구한다.
- 소유자/모드/SELinux context를 유지한다. 출력된 backup_file과 수정 후 파일 SHA를 노드별 작업 기록에 보존한다.
- worker03의 kubelet만 기존 sudo로 한 번 재시작한다. Pod 재배포·다른 5대 재시작은 포함하지 않는다.

```bash
ANSIBLE_CONFIG=/home/server01_ansible/p1-ans-01/ansible.cfg \
ansible -i /home/server01_ansible/p1-ans-01/inventory/hosts.yml \
  k8s-worker03 -e '{"ansible_ssh_use_tty":false}' \
  -m ansible.builtin.raw -a 'sudo -n systemctl restart kubelet'
```

새 serving 인증서가 발급되기 전 TCP10250의 TLS 및 logs/exec가 일시적으로 불가할 수 있다. 따라서 같은 kubelet API를 복구 수단으로 삼지 않는다.
CP1의 기존 admin API/Ansible SSH를 이용해 CSR을 확인하고 복구한다. 기존 컨테이너 정상 지속까지 재시작 명령만으로 보장하지 않는다.

작업창은 담당 Lead가 기록하며 CSR 발급/검토/승인을 즉시 수행할 수 있어야 한다. 제안 관측 한도는 재시작 후 5분이다.
한도 내 올바른 CSR이 발급·검토·승인되지 않거나 관리/노드 상태가 악화되면 복구한다. 한도 연장은 기존 창을 무기한 유지하는 방식으로 처리하지 않는다.

## 5. CSR 검토와 단건 승인

신규 CSR 이름을 미리 만들거나 모든 Pending을 승인하지 않는다. 읽은 객체의 name/UID/resourceVersion과 spec.request SHA256을 보존한다.

- signerName: kubernetes.io/kubelet-serving.
- spec.username: system:node:k8s-worker03, groups에 system:nodes 포함.
- CSR 서명 자체 유효성, subject CN=system:node:k8s-worker03/O=system:nodes 대조.
- SAN은 Node 실측과 일치: IP 10.1.93.108, DNS k8s-worker03. 다른 IP/DNS/URI/email 또는 wildcard는 이 후보에서 허용하지 않는다.
- usages는 server auth 필수, digital signature/key encipherment만 추가 허용. client auth 등 불필요한 권한은 거부한다.
- 창 시작 뒤 worker03가 생성한 요청인지 대조한다. 충돌/기존 다른 CSR/대상 변경은 승인하지 않는다.
- 승인 직전 같은 UID/요청 바이트인지 확인한 정확한 CSR 한 건만 승인한다. CSR name 목록 전체에 approve를 실행하지 않는다.
- 발급된 certificate의 SAN·EKU·유효기간·public-key/CSR 일치·CA chain을 검증한다. Approved 표시만으로 발급 성공이나 TLS 성공을 선언하지 않는다.

kube-controller-manager 기본 승인기는 serving CSR을 자동 승인하지 않는다. 임의 auto-approver 배포나 cluster CA 개인키 반출을 해결책으로 추가하지 않는다.

## 6. 성공 기준과 복구

| 검증 | 성공 기준 |
|---|---|
| 설정/노드 | worker03 실제 configz serverTLSBootstrap=true, 기존 인증·인가 보존, Node Ready·기존 Pod/관리 경로 정상 |
| TLS | 10.1.93.108:10250의 실제 presented certificate가 IP SAN·CA 검증에 성공. insecure_skip_verify/인증 우회 없음 |
| Prometheus | 기존 SA로 kubelet /metrics와 kubelet-cadvisor /metrics/cadvisor의 해당 worker03 target이 서로 다른 post-change lastScrape 2회에서 UP, lastError 없음 |
| 보안 | anonymous=false/Webhook 유지, 승인된 무자격·잘못된 CA 음성 시험은 기대대로 거부. 단순 timeout을 인증 거부로 판정하지 않음 |

인증서 검증 성공 뒤 401/403이면 SA/RBAC를 별도로 분석한다. 이번에 익명 허용·cluster-admin 부여·Trust 완화를 하지 않는다.
두 target UP은 worker03 canary 성공일 뿐 다른 5대·ICR AC06 전체 PASS가 아니다. 네트워크 PR27 적용을 이 TLS canary의 선행조건으로 추가하지 않는다.

복구는 같은 worker03의 정확한 backup_file과 원본 SHA를 사용한다. 현재 설정이 이 창의 수정 후 SHA와 다르면 다른 작업을 덮어쓰지 않고 중단한다.
원본 bytes·소유자/모드/SELinux context를 복구하고 kubelet 한 대를 재시작한다. 원본 설정·old serving certificate 지문·Node/Pod/관리 경로 복원을 확인한다.
원래 실패하던 IP SAN 검증 상태로 복귀하는 것은 복구 결과이지 TLS 성공이 아니다.
기존 또는 새로 생성된 pki 파일을 wildcard로 삭제하지 않는다. Pending/발급 CSR의 name/UID와 잔여 상태를 기록하고 필요 시 그 객체만 담당자가 회수 판단한다.
CSR 객체 삭제는 이미 발급된 인증서의 암호학적 폐기를 뜻하지 않는다.

성공 유지 또는 복구 여부를 창 종료 시 기록한다. 성공 시 serving CSR 갱신의 검토·승인/만료 감시 담당과 공유 ConfigMap 동기화 및 나머지 노드 순차 rollout을 후속에 남긴다. 이번 파일로 전체 배포를 선언하지 않는다.

## 7. 검증 수준

수신 archive·배포 collector·Ansible stdout 정합성 PASS, 현장 순수 조회4/4 PASS.
이 변경 후보: YAML/Bash 예시 정적 검사만 수행. MOCK/UNIT·CI·운영 변경·CSR 승인·새 TLS 검증 NOT RUN.
REVIEW=PENDING / MERGE=NOT_DONE / APPLY=NOT_RUN. CODEX_ASSISTED.
운영 설정과 재시작이 포함되므로 중앙 REVIEW_AUTHORITY_POLICY의 순수 Pre-check 예외로 적용하지 않는다.

참고:
- https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#enabling-signed-kubelet-serving-certificates
- https://kubernetes.io/docs/reference/access-authn-authz/kubelet-tls-bootstrapping/#certificate-rotation
- https://github.com/kubernetes/kubernetes/blob/v1.36.4/cmd/kubelet/app/server.go (InitializeTLS)
- https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/kubelet/kubelet.go (server certificate manager)
