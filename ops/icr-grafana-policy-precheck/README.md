# Grafana 음성 시험 정책 입력 Pre-check

책임: ICR-D-NET-01-01 / baseline #14, K8S/SEC #10/#19, MON #17. 운영 후보는 infra-kubernetes PR27, 기존 원본은 fds-project-evidence PR23이다. 사용자의 Grafana 권고안 착수 지시에 따라 준비 대상을 고정한다. 운영 실행 수용·독립 리뷰를 대신하지 않는다.

분류: **순수 Pre-check**. 중앙 REVIEW_AUTHORITY_POLICY §3.2/4-1 적용. `REVIEW=NOT PERFORMED (Pre-check)`. 서버 설정·파일·패키지·서비스·정책·권한 변경, 설치, Pod 생성, exec, 접속 probe가 없다. 기존 svc_ansible 접근과 sudo kubectl만 사용한다. 결과 파일은 ansible01 실행자 디렉터리에만 쓴다. 운영 manifest/도구 확인·음성 시험 명령은 이 수집기에 포함하지 않는다.

## 고정 대상과 범위

- `monitoring-api/grafana-7dc57885d4-sk8h2`, UID `00396c87-640c-4704-ac6c-dcbacf6fbc90`.
- worker03, hostIP `10.1.93.108`, PodIP `10.244.39.217`, SA `default`, container `grafana`, label `app=grafana`.
- imageID `10.1.93.54/fds/grafana@sha256:7a1ff54408c90357ff1c76189e5bbc24f898531cb771fbbfe6a477f00de37d5b`.
- 근거: PR23 HEAD `5c13e7c2393df260e0356e5f2b160573624ded64`, `negative-candidates-20260924T052009` 회차. default SA는 Alertmanager와 공유하므로 SA만으로 대상을 정하지 않는다.

기존 ansible01 → control01(`10.1.93.102`, **svc_ansible, become:false**) → admin.conf의 **LB API `https://10.1.93.109:6443`**만 허용한다. CP 직접 API로 전환하지 않는다.

최대 **11회 명령**: 로컬 kubeconfig의 server URL 조회 1회 + API GET 10회.

| 순서 | 조회 |
|---|---|
| 1 | kubeconfig의 현재 API 주소 |
| 2 | 고정 Grafana Pod 신원·Running/Ready·imageID·labels |
| 3–4 | monitoring-api Namespace와 default SA의 metadata/labels만 |
| 5–6 | monitoring-api의 Kubernetes/Calico NetworkPolicy |
| 7–8 | 전체 Calico GlobalNetworkPolicy와 Tier |
| 9–10 | monitoring-api NetworkSet과 GlobalNetworkSet |
| 11 | 동일 Pod 재조회, 투영 필드와 resourceVersion 변경 시 STOP |

각 LIST는 `limit=100`, 한 페이지만 사용한다. continue/remainingItemCount, 100개 초과, 잘못된 kind/namespace/중복 identity는 STOP이다. 요청 timeout 10초, 프로세스 timeout 15초, 각 stdout 허용 상한 4 MB. 오류 즉시 중단하고 다른 계정·권한·명령으로 재시도하지 않는다. NotFound/Forbidden/누락 CRD를 정책이 없다는 뜻으로 취급하지 않는다.

Pod 목록 전체 재수집·과거 packet/counter 반복은 없다. Secret endpoint를 읽지 않는다. Pod env/args/volume와 SA secrets/imagePullSecrets, annotations/managedFields를 결과에 넣지 않는다. 정책 검토에 필요한 labels 및 정책 spec은 보존한다. 수신 API 원문 전체는 저장하지 않으며 stdout hash는 수신 무결성 참고값이다. 투영된 결과만으로 원문 전체 hash를 재생산했다고 주장하지 않는다.

## 실행

기존 `/home/server01_ansible/p1-ans-01`에서 환경 파일을 source하고, 해당 PR에 게시된 **고정 40자리 source SHA**의 `collect.py`, `collect.yml`, `run.sh`, `SHA256SUMS`를 임시 디렉터리에 다운로드한다. hash 확인 후:

```bash
bash "$ICR_RUN/run.sh" "$PWD/inventory/hosts.yml" "$ICR_SHA"
```

run.sh는 정규 `inventory/hosts.yml` 및 같은 checkout의 ansible.cfg를 사용하고 실제 Ansible syntax-check 후 수집한다. source/inventory/config hash·실행자·KST·rc·로그를 포함한 `ICR_Grafana_Policy_*.tar.gz`와 `.sha256`를 만든다. 실패 시에도 출력된 archive와 sidecar를 함께 전달한다. 다운로드/소스 무결성 등 실행 전 실패는 archive 생성 전 중단될 수 있다. 도구나 접근이 없으면 설치·권한 확장 없이 STOP이다.

## 결과와 다음 단계

성공은 `COLLECTED_PENDING_POLICY_REVIEW`이며 **실제 DENY·도구 존재·운영 승인·Runtime AC는 미판정**이다. 서로 다른 API 요청은 원자적 snapshot이 아니다. 시작/끝 Pod 비교도 중간 정책 변화를 증명하지 않는다. 실행 창에서는 선택 대상과 관련 정책의 최신 상태를 확인한다.

K8S/SEC Lead는 Pod/Namespace/SA labels로 선택되는 정책, additive Kubernetes egress 허용, Calico tier/order/Allow/Deny/Pass·network set을 실제 설치 버전에 맞춰 검토한다. 이 수집기는 selector engine이나 전체 정책 유효성 증명기가 아니다. 수집되지 않은 endpoint/profile 또는 native admin-policy 등 추가 입력이 결정에 필요하면 해당 입력이 확보될 때까지 HOLD한다. 임의로 '목록에 없음=차단'으로 결론 내리지 않는다.

PR27의 독립 운영 리뷰 후 기존 컨테이너 도구와 bounded 명령을 확인하고, 정상 positive endpoint·실제 정책 차단 귀속을 갖춘 before/during 음성 시험을 수행한다. 권한 없는 접근 성공은 FAIL이며, 단순 timeout/도구 없음/exec 실패/비정상 listener의 refusal은 PASS가 아니다. 검토 전 exec나 probe는 실행하지 않는다.

검증: STATIC PASS / 로컬 synthetic unittest **18/18 PASS** (collector 14 + fake-ansible runner 4). Fake runner 시험은 실제 Ansible syntax/integration 시험이 아니다. 작성 환경에는 Ansible이 없으며 현장 실행·실제 syntax-check는 **NOT RUN**이다. CI 상태는 게시 SHA에 연결된 PR 기록을 따른다.

공식 의미 참고(설치 버전의 증거가 아님):
- https://kubernetes.io/docs/concepts/services-networking/network-policies/
- https://docs.tigera.io/calico/latest/reference/resources/tier
- https://docs.tigera.io/calico/latest/reference/resources/networkset

AI assistance: CODEX_ASSISTED.
