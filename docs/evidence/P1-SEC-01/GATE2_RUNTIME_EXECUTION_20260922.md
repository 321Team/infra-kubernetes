# P1-SEC-01 Gate 2 — Runtime 실행 증적 (2026-09-22)

> PR #7 "검증과 증적" 표 대응. 이 문서는 PR #7의 코드 변경 자체가 아니라, 그 코드가
> 병합 전 상태로 실 클러스터(3CP+3Worker)에 실행된 결과를 기록한다. Runtime PASS/FAIL
> 판정은 이 문서가 최종 승인이 아니며, 이권욱님 최종 승인과 별도의 독립 Reviewer
> 검토를 거쳐야 한다. 이 문서는 "무엇을 실행했고 무엇을 확인했는지"만 사실대로 기록한다.

## 실행 환경

- 실행 위치: k8s-control01(root), k8s-control02/03(sudo 계정)
- 실행 방식: 저장소 clone 불가(CP가 인터넷 미연결, `gh`/외부 HTTPS 접근 불가) —
  로컬 저장소 `manifests/01-namespace.yaml`, `05-limitrange.yaml`, `06-rbac.yaml`,
  `03-networkpolicy.yaml`의 내용을 그대로 각 CP 콘솔에 `cat > ... <<'EOF'`로 재현해 적용.
  파일 줄 수(58/57/160/364)를 원본과 대조해 누락·손상이 없음을 확인했다.
- 사전 확인: HEP 6개(`fds-k8s-k8s-*`, interfaceName=ens160), GNP 6개(`fds-k8s-host-*`)가
  이미 적용돼 있었음(`p1-k8s-01-v4` 패키지, 이 저장소의 `manifests/calico/*`와는 별개
  산출물). firewalld는 6대 모두 disabled/inactive. 이 상태는 이 실행이 만든 것이 아니라
  실행 전부터 존재했다.

## 적용 순서와 결과

| 순서 | 대상 | 명령 | 결과 |
|---|---|---|---|
| 1 | `01-namespace.yaml` | `kubectl apply --dry-run=server` → `kubectl apply` | 4개 Namespace 생성 (fds-app/monitoring-node/monitoring-api/ci-cd). PSA Version Label `<TBD-P1-K8S-01>`을 `kubectl get nodes` 실측값(v1.36.4 → Minor v1.36)으로 확정 반영 |
| 2 | `05-limitrange.yaml` | 동일 | LimitRange 3개 생성 |
| 3 | `06-rbac.yaml` | 동일 | ServiceAccount 8개, ClusterRole/ClusterRoleBinding 2쌍, Role/RoleBinding 1쌍 생성 |
| 4 | `03-networkpolicy.yaml` | 동일 | NetworkPolicy 12개 생성 (Default Deny 3 + 개별 Allow 9). 적용 시점에 fds-app/monitoring-* 워크로드가 0개였으므로 즉시 영향받는 트래픽 없음 |
| 5 | etcd Secretbox 암호화 (SEC-01) | 아래 별도 기술 | CP 3대 순차 적용, 검증 PASS |

적용 전 `kubectl get secrets -A` 결과 0건(서비스 계정 토큰 포함 전부) — 재작성 대상 Secret이
없어 SEC-01 적용 시점상 가장 낮은 위험 조건이었다.

## SEC-01 — etcd Secretbox 암호화 (최초 구성)

`scripts/rotate-secretbox-key.sh`는 기존 `encryption-config.yaml`이 있는 상태에서의
**회전**을 전제로 하며(호스트명 `cp01` 등 placeholder, 무암호 sudo 가정), 이 클러스터는
`--encryption-provider-config` 자체가 없는 **최초 구성** 상태였다. 따라서 이 스크립트를
그대로 쓰지 않고, 아래 절차를 CP 1대씩 수동 확인하며 진행했다.

1. k8s-control01에서 32byte 랜덤 키 생성(`head -c 32 /dev/urandom | base64`).
2. 3개 CP 각각에 `/etc/kubernetes/enc/encryption-config.yaml` 생성
   (provider 순서 `secretbox → identity`, 권한 600, root:root).
   적용 전 3대의 `sha256sum` 일치 확인.
3. CP1 → CP2 → CP3 순서로, 각 CP의 `/etc/kubernetes/manifests/kube-apiserver.yaml`에
   `--encryption-provider-config` 플래그와 `enc-config` volume/volumeMount를 추가.
   변경 전 각 CP에서 원본을 `kube-apiserver.yaml.bak-preenc`로 백업.
   kubelet이 static Pod 변경을 감지해 자동 재기동. 매 CP마다 `1/1 Running` +
   `kubectl get --raw='/healthz?verbose'` PASS 확인 후 다음 CP로 진행.
4. 3대 전체 반영 후 `kubectl get nodes` 6대 Ready 확인.
5. 검증: 테스트 Secret(`enc-test-secret`, default ns) 생성 후
   `kubectl -n kube-system exec etcd-k8s-control01 -- etcdctl ... get /registry/secrets/default/enc-test-secret`
   결과에서 `k8s:enc:secretbox:v1:key1:` 접두어를 확인. 평문(`hello-world`, `probe`)은
   출력 어디에도 없었다. 확인 후 테스트 Secret 삭제.

### 결과

- CP 3대 모두 `--encryption-provider-config` 반영, apiserver 재시작 후 정상 기동.
- 신규 Secret은 secretbox로 암호화되어 저장됨을 raw etcd 조회로 실측 확인(PASS).
- 적용 전 클러스터에 기존 Secret이 0건이었으므로, 기존 데이터 재작성(`rewrite-secrets`
  단계)은 대상이 없어 수행하지 않았다.

### 재실행 시 주의 — 미해결 항목

- **암호화 키 노출 범위**: CP 간 SSH가 IF-07(ansible01 단일 관리 경로)로 제한돼 있어
  키 파일을 `scp`로 배포할 수 없었다. 그래서 키 값을 각 CP 콘솔에 수동으로 입력하는
  방식을 썼다. 이 키 값은 3개 CP의 shell history와 이 세션의 대화 기록에 평문으로
  남아 있다. **후속 조치 필요**: 각 CP에서 해당 명령이 포함된 history 항목 삭제
  (`history -d <라인번호>` 또는 history 파일에서 직접 제거), 가능하면 이번 세션 종료
  후 키 로테이션을 1회 수행해 "노출된 적 있는 키"를 "노출된 적 없는 키"로 교체하는
  방안을 고려한다. 이 문서 자체에는 키 값을 기록하지 않는다.
- 이 방식(수동 입력)은 이번 1회성 최초 구성에 한정한 예외 조치다. 앞으로 키 로테이션이
  필요할 때는 ansible01을 경유하는 배포 경로를 사용하거나, `scripts/rotate-secretbox-key.sh`를
  이 클러스터의 실제 호스트명·계정·sudo 정책에 맞게 재작성해야 한다(SSH_USER=ansible,
  cp01/cp02/cp03, 무암호 sudo 가정이 실제 환경과 다름).

## 적용하지 않은 항목과 이유

- `manifests/02-securitycontext.yaml` (SEC-04): `<확인된-UID>`, `<확인된-GID>`,
  `<approved-digest>`, `<실제-버전>` placeholder가 남아있어 미적용. Harbor 배포 및
  이미지 확정 후 별도 진행.
- `manifests/04-storage.yaml`: k8s-worker03의 `/home/fds-local-pv/*` 디렉터리·권한·
  SELinux fcontext 사전 준비가 선행되지 않아 미적용.
- `manifests/calico/*`: 클러스터에 이미 별도 패키지(`p1-k8s-01-v4`, `fds-k8s-host-*`
  GNP 6개)로 HEP/GNP가 적용돼 있어 이 저장소 파일을 적용하지 않았다. 두 산출물의
  관계(대체·병합 여부)는 별도로 정리가 필요하다 — 특히 이 저장소의
  `05-nodeport-allow.yaml` selector(`role == 'k8s-worker'`)가 실제 적용된 HEP 라벨
  (`fds.example/role: worker`)과 일치하지 않아, 그대로 적용해도 집행되지 않는다.
