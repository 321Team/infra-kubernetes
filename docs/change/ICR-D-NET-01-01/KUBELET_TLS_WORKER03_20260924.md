# worker03 kubelet serving TLS 변경 후보 — 2026-09-24

상태: 독립 리뷰 가능 / 운영 변경 / APPLY HOLD. 사전조회 수신 완료, 작업창·복구·기존 실행 권한 조건은 적용 전에 확정.
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

## 1.1. 05:00 추가 입력 수집 완료 — PR32

- 현장 수집 시작: 2026-09-24T05:00:45 KST, CP3 + worker03, 기존 svc_ansible 계정.
- 원본 MON_TLS_INPUTS.zaZ47pti.tar.gz SHA256: `f36f3f4ed737a6baee43ead37141b4be0b0cd4ac16fb6d077dfd64a96b6d9f3c`. 외부 sidecar 일치, 내부 SHA256SUMS 13/13 검증, 예상 파일 이외·중복 archive member 없음.
- source `f29e8c4a0c290868fa2b9532267e22b2ea3c41fc`의 소스5개와 수신 archive bytes의 Git blob 일치. PR32 merge `f2bb91b04901c43bcc7238517482c0dff5f31ccb`.
- Ansible RC=0, 4호스트 collection_ok=true, 실패/접속 불가0. changed는 위임된 localhost JSON 저장이며 원격 설정 변경이 아니다. 전체 설정/argv는 no_log 메모리 처리되어 원본 전체 YAML을 이 증적에 저장하지 않았다.

| 확인 항목 | 실측 결과·판정 범위 |
|---|---|
| worker03 PID/시작 설정 | PID2948, 선택된 프로세스 인자에 --config=/var/lib/kubelet/config.yaml만 있음. 수집 대상 config-dir/serving TLS CLI override 충돌 없음 |
| 로컬 YAML | 중복 없는 단일 KubeletConfiguration, 문서 끝 마커 없음. serverTLSBootstrap 미명시, rotateCertificates=true, 명시적 tlsCertFile/tlsPrivateKeyFile 없음. 검사 pending=[] |
| 인증·인가 | anonymous=false, webhook=true, authorization=Webhook, client CA=/etc/kubernetes/pki/ca.crt |
| 파일 SHA256 | 4f8474e0e4fe8e46f3decaed9723c8637fa61f3c6bc46d6f79fcc8c17cbd690c |
| 파일 메타데이터 | uid/gid=0/0, mode0644, SELinux=unconfined_u:object_r:container_var_lib_t:s0, 1118bytes. 원본 백업·복구 검증까지 수행한 것은 아님 |
| 실제 Prom 로드 설정 | kubelet /metrics 및 kubelet-cadvisor /metrics/cadvisor, HTTPS, insecure_skip_verify=false, server_name 미설정 |
| 인증 참조 | authorization.type=Bearer, credentials_file=/var/run/secrets/kubernetes.io/serviceaccount/token. 파일 참조만 확인했고 token 내용·권한 검증은 안 함 |
| CA 참조 | 두 job 모두 /var/run/secrets/kubernetes.io/serviceaccount/ca.crt, inline CA/proxy/client cert/key 없음 |
| public CA | CP1/2/3 ca.crt와 Prom 마운트 bundle의 단일 인증서 DER SHA256 동일: e788befe100dbfd1a827af51a489844ed6f30f0b40e06ee465e0238b4a0574d7 |
| Prom 신원 | UID46c00f91-2073-440a-800c-6cedba89d291, worker03 / 10.244.39.219 / SAprometheus, restart_count1. 수집 전후 같은 container 확인 |
| 최신 Targets | 총20 / UP6 / DOWN14 / unknown0. worker03 두 job은 IP_SAN 오류 지속. lastScrape=05:00:44.240671197 및05:00:36.751943365 KST |

실제 로드 설정 SHA256: `445ef6ea921ad3f526e67bf35eca6f4017a271efb0f7d4ee3a4faa3b613dce7a`.
이번 보완에서 설정 충돌·CA 참조 불일치는 발견되지 않았다. 따라서 §2 단일 노드 변경안과 기존 명령·필드 명세를 유지한다.
공개 CA 지문 포함은 인증서 유효기간/CA 제약·signer 개인키 가용성/실제 발급·애플리케이션 메모리 trust·새 leaf chain 검증을 대체하지 않는다.
12개 나머지 DOWN의 이번 오류 원문은 수집하지 않았으므로 과거의 timeout 분류를 새 시각의 확정 원인으로 재표시하지 않는다.

summary의 pending5는 수집기가 기본으로 유지하는 절차 항목이다. 새로 검출한 장애5건이 아니다.
독립 리뷰·변경창/복구 백업·기존 SSH 쓰기 권한/CSR 검토자는 실행 전 조건이며, signer 발급·새 TLS/연속 scrape는 이후 적용 단계에서 검증한다.
PR32 사전조회는 완료로 수용하고 같은 묶음을 반복하지 않는다. 변경창 시작 시 대상/파일 hash/관리 경로 등 변동 가능한 입력만 재대조한다.

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

05:00 PR32 결과로 아래2·4·5의 사전조회와3의 YAML/메타데이터 확인은 완료했다. 6대 cert/firewalld/Source/counter와 PR29/32 전체 수집을 반복하지 않는다. 실제 변경창에는 파일 hash·대상 신원·현재 상태를 재대조하고, 아직 수행하지 않은 복구 백업/권한 확인을 완료한다.

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

04:11 PR29 조회4/4 PASS를 보존한다. 05:00 PR32 추가 조회는 4호스트 성공, Ansible RC0, 외부 archive/내부13hash/소스5파일 정합성 PASS. PR32 실제 Ansible play 실행은 확인했지만 별도 --syntax-check 실행을 소급 주장하지 않는다.
이 운영 변경 후보: YAML/Bash 예시 정적 검사만 수행. 이번 수정은 수신 결과·실행 전 잔여 구분을 보완한 문서 변경이며 기존 실행 명령/필드 명세는 동일하다. PR32 수집기 MOCK9 PASS는 PR31 운영 변경 시험이 아니다. PR31 MOCK/UNIT·운영 변경·CSR 승인·새 TLS 검증 NOT RUN, CI는 최신 HEAD의 실제 상태를 확인한다.
REVIEW=PENDING / MERGE=NOT_DONE / APPLY=NOT_RUN. CODEX_ASSISTED.
운영 설정과 재시작이 포함되므로 중앙 REVIEW_AUTHORITY_POLICY의 순수 Pre-check 예외로 적용하지 않는다.

참고:
- https://kubernetes.io/docs/tasks/administer-cluster/kubeadm/kubeadm-certs/#enabling-signed-kubelet-serving-certificates
- https://kubernetes.io/docs/reference/access-authn-authz/kubelet-tls-bootstrapping/#certificate-rotation
- https://github.com/kubernetes/kubernetes/blob/v1.36.4/cmd/kubelet/app/server.go (InitializeTLS)
- https://github.com/kubernetes/kubernetes/blob/v1.36.4/pkg/kubelet/kubelet.go (server certificate manager)
