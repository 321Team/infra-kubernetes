# FDS CP 로그 검토 및 후속 작업 매핑 v1.0

검토일: 2026-09-10 · 대상: `FDS_CP_Log_Collection_glrr0dsq.tar.gz` 및 SHA-256 sidecar

**관리 접속과 CP 3대의 진단 수집은 성공했다. 클러스터는 조회 시점에 CP1만 Ready이며, CP2·CP3는 가입 완료 상태가 아니다. CP 간 방화벽 허용 누락과 CP2의 부분 구성 상태를 함께 처리해야 한다.**

이번 판정은 제출 증적의 기술 검토이다. 원격 서버 변경, 티켓 댓글·담당 변경, 독립 리뷰 승인 및 Bootstrap/Calico 전환 승인을 실행한 기록은 아니다.

## 1. 수집·무결성 판정

| 항목 | 확인 결과 |
|---|---|
| 수집 종료 | 2026-09-10 02:30:59 UTC / 11:30:59 KST |
| 요청한 journal 기간 | 2026-09-09 00:00:00 UTC ~ 2026-09-10 02:30:53 UTC |
| 압축파일 SHA-256 | `265e7d3fe04c8c215b8c76847bac769c5445682d788c3f9ad2bf9afef100c594` — sidecar 일치 |
| 압축파일 구성 | 일반 파일 9개. 경로 이탈·중복 이름·링크 없음 |
| 내부 SHA256SUMS | 8/8 일치. 체크섬 목록 자체를 제외한 모든 파일 포함 |
| 수집기 동일성 | 기록된 v1.1 파일 해시 5/5가 제공한 키트와 일치 |
| Ansible 저장소 HEAD | `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab` |
| SSH / Python 모듈 / sudo | 각각 CP 3/3 통과. 원격 사용자 `svc_ansible`, 승격 UID `0` |
| 수집 상태 | CP 3/3 `COLLECTED_REVIEW_REQUIRED`, Ansible return code `0` |
| 실제 실행된 진단 명령 | CP1 18개 + CP2 11개 + CP3 11개 = 40개, 모두 rc=0 |
| 컨테이너 직접 조회 | 3대 모두 검사한 신뢰 경로에서 `crictl`을 찾지 못해 해당 조회 미실행 |

Ansible recap의 노드별 `changed=1`은 ansible01에 결과 JSON을 저장한 작업이다. 원격 방화벽·서비스 설정 변경을 나타내지 않는다. `query_nonzero=0`은 실행된 조회의 반환 코드이며, 건너뛴 컨테이너 조회나 서비스 건강 상태까지 PASS로 만들지 않는다.

이전 제출본 `_l2ne13q`의 SSH 인증 실패는 이번 실행에서 재현되지 않았다. 승인된 키를 명시하도록 수정한 재실행 절차 이후 SSH·전송·sudo가 실제로 통과한 것을 확인했다. 다만 v1.1은 선택된 SSH 키 경로를 환경변수까지 포함해 기록하지 않는다. `controller_context.json`의 `identity_file_configured=false`는 **inventory 변수에 키 경로가 없다는 뜻**이며 이번 접속 실패의 증거가 아니다.

| 노드 증적 | SHA-256 |
|---|---|
| `k8s-control01.json` | `da88dc5caf53dec238c4d58af98238f0048d21309347e1ddf159093f2b56d609` |
| `k8s-control02.json` | `fbaa06374a6d66e840741b7bf89dd04a69a7c130fb47181c30f6c652c015c3d8` |
| `k8s-control03.json` | `0ec100ac5c927b2cfcc9019560e316454b6040a2c2f221a355075cf95a004164` |

## 2. 노드별 현재 상태

아래 상태는 2026-09-10 11:30:58 KST 전후의 조회 결과이다. 이전 부트스트랩 오류와 현재 상태를 구분했다.

| 항목 | CP1 / 10.1.93.102 | CP2 / 10.1.93.103 | CP3 / 10.1.93.104 |
|---|---|---|---|
| Kubernetes Node 목록 | Ready로 표시 | 목록에 없음 | 목록에 없음 |
| kubelet | active/running, NRestarts=0 | activating/auto-restart, NRestarts=5146 | activating/auto-restart, NRestarts=5146 |
| kubelet 최근 종료 원인 | 현재 반복 종료 없음 | `/var/lib/kubelet/config.yaml` 없음 | `/var/lib/kubelet/config.yaml` 없음 |
| containerd | active/running | active/running | active/running |
| firewalld | active/running | active/running | active/running |
| etcd 매니페스트 | 있음 | 없음 | 없음 |
| API / controller-manager / scheduler 매니페스트 | 3개 있음 | 3개 있음 | 모두 없음 |
| 2379 / 2380 / 6443 / 10250 리스너 | 모두 있음 | 모두 없음 | 모두 없음 |

근거: 각 노드 JSON의 `queries.unit_state`, `queries.tcp_listeners`, `static_pod_manifest_presence`; CP1의 `queries.cluster_nodes`.

### CP1: 단일 노드의 동작은 확인, 3CP HA는 미완료

CP1에서 기존 admin kubeconfig로 조회한 Node 목록에는 `k8s-control01` 한 행만 있고 상태는 Ready다. 보고 버전은 Kubernetes `v1.36.4`, containerd `2.3.5`다. 이것은 관측된 서버/런타임 버전이며 CP2·CP3의 kubeadm CLI 버전까지 검증한 결과는 아니다.

`kube-system` Pod 9개는 모두 `1/1 Running`, 재시작 0회다. etcd·API server·controller-manager·scheduler·kube-proxy·Calico node·Calico controllers·CoreDNS 2개가 포함된다. `/readyz?verbose`는 37개 검사에서 `ok`, 마지막에 `readyz check passed`를 반환했다.

이 결과는 해당 API 조회 경로와 CP1의 현재 동작을 뒷받침한다. **etcd member 목록을 수집하지 않았으므로 실제 등록 멤버 수, CP2 잔여 멤버나 learner 존재 여부, 3멤버 건강 상태는 아직 확인하지 못했다.** Node가 한 대라는 이유만으로 etcd 등록 멤버도 반드시 한 개라고 단정하지 않는다.

CP1 journal에는 9월 9일 21:35 KST 이전의 kubelet 설정 부재와 초기 API connection refused가 남아 있다. 21:36:39에는 Calico nodename 부재로 Pod sandbox 생성이 실패했지만 이후 Calico node, CoreDNS, Calico controllers 시작 기록이 있고 현재 Pod 상태도 정상이다. 과거 초기화 오류를 현재의 Calico 장애로 판정하지 않는다.

### CP2·CP3: kubelet 종료의 직접 원인은 설정 파일 부재

두 노드 모두 마지막 1,500줄에 아래 오류가 각각 250회 있다. 마지막 발생 시각은 CP2 11:30:50.128 KST, CP3 11:30:50.304 KST다.

```text
failed to load kubelet config file
open /var/lib/kubelet/config.yaml: no such file or directory
```

`KUBELET_KUBEADM_ARGS` 미설정 경고도 각각 250회이며 프로세스 종료 코드는 1이다. **현재 kubelet이 왜 종료되는지는 확인됐지만, 원래 join이 왜 중단됐는지는 이 오류만으로 확정되지 않는다.** kubeadm은 init/join 과정에서 kubelet 설정을 생성하며, 설정을 받기 전 kubelet이 반복 재시작할 수 있다. [Kubernetes kubelet 통합 문서](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/kubelet-integration/), [kubeadm 설치 문서](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/install-kubeadm/)

CP2는 제어 평면 매니페스트 3개가 존재하고, 9월 9일 21:52:20~32 KST에 API server 등 Kubernetes 이미지 7종의 Pull 성공 기록이 있다. 따라서 CP2는 **일부 부트스트랩 구성이 남은 상태**로 취급해야 한다. 해당 파일을 누가 어떤 실행으로 만들었는지, 정확히 어느 join phase에서 멈췄는지는 현재 증적으로 확인할 수 없다. CP3는 Node 등록과 4개 static Pod 매니페스트 모두 없지만, 이것만으로 과거 join 시도가 전혀 없었다고 단정하지 않는다.

설정 파일 복사나 수동 생성, 부분 파일 삭제, 일괄 `kubeadm reset`, preflight 무시 후 join 재실행은 이 결과에서 도출되는 즉시 조치가 아니다. 먼저 CP2의 기존 구성과 etcd 멤버 상태를 확인해야 한다.

## 3. 방화벽 문제와 timeout의 관계

이번에도 CP 3대 모두 `ens160 → public`이고, public의 Runtime/Permanent 설정은 다음과 같다.

- services: `dhcpv6-client ssh`
- ports: 비어 있음
- rich rule: `10.1.93.109/32 → TCP 6443 accept` 한 건
- CP 소스의 TCP 2379/2380/10250 허용 규칙 없음

근거: 각 노드 JSON의 `queries.firewall_active_zones`, `queries.firewall_public_runtime`, `queries.firewall_public_permanent`.

이전 09:49~09:50 KST 진단에서 CP 상호 2379/2380은 0/12, 직접 6443은 0/6, CP→다른 Node의 10250은 0/15였다. 실패 33건은 `errno 113: No route to host`였고, 당시 nft ruleset의 public 경로에는 `reject with icmpx admin-prohibited`가 있었다. 특히 **CP2·CP3→CP1의 4개 포트 시험 8건은 CP1에 리스너가 있는데도 실패했다.**

이번 11:30 수집은 포트 연결 시험과 nft 전체 조회를 다시 수행하지 않았다. 따라서 위 연결 시험 수치는 이전 시점의 결과로 유지한다. 이번에는 public 규칙의 허용 누락이 계속 남아 있고 CP1 리스너도 존재한다는 사실을 추가 확인했다.

판정은 다음과 같다.

| 판단 | 근거 수준 |
|---|---|
| CP 통신 허용 누락이 남아 있음 | 이번 Runtime/Permanent 출력에서 확인 |
| 이전 CP 간 통신 실패에 host firewall이 관여했을 가능성이 높음 | 이전 실제 nft reject, 유효한 route, 리스너와 TCP 실패의 대조에 근거한 강한 추론 |
| 방화벽이 원래 `context deadline exceeded`의 유일하고 정확한 원인임 | 미확정. 원래 join 출력·phase·대상 endpoint가 없음 |
| CP2·CP3의 kubelet 반복 종료가 전부 방화벽 때문임 | 성립하지 않음. 현재 프로세스의 직접 종료 사유는 로컬 설정 파일 부재 |

포트의 역할은 TCP 6443=API server, 2379/2380=etcd, 10250=kubelet API다. 실제 허용 Source와 목적지는 프로젝트 Interface 계약에 맞춘다. [Kubernetes Ports and Protocols](https://kubernetes.io/docs/reference/networking/ports-and-protocols/)

## 4. Calico 보호 전환과 NTP

CP1의 Calico node/컨트롤러는 실행 중이다. 그러나 Kubernetes API 조회에서 다음 두 리소스는 모두 `items: []`였다.

| 조회 | 결과 |
|---|---|
| `hostendpoints.crd.projectcalico.org` | 0개 |
| `globalnetworkpolicies.crd.projectcalico.org` | 0개 |

즉 **이번에 조회한 클러스터에는 계획된 HEP/GNP가 아직 구성되지 않았다.** Calico Pod의 Running만으로 호스트 방화벽 보호 전환 완료를 선언할 수 없다. `FelixConfiguration/default` 한 개는 존재하지만 HEP/GNP 적용이나 실제 데이터 경로의 합격 증적을 대체하지 않는다.

HEP를 추가하고 정책이 없는 상태에서도 기본 차단이 발생할 수 있으므로, 관리·클러스터 필수 Allow와 canary 검증을 갖춘 기존 전환 절차를 적용해야 한다. 지금 firewalld를 일괄 중지하면 현재 남아 있는 호스트 보호를 먼저 제거하게 된다. [Calico 호스트 보호 문서](https://docs.tigera.io/calico/latest/network-policy/hosts/protect-hosts)

NTP는 이번에도 CP 3대의 선택 서버가 모두 `1.230.135.74`, Leap status는 `Normal`이다.

| 노드 | Last offset | 마지막 참조 시각 UTC |
|---|---|---|
| CP1 | +0.000365183초 | 02:01:10 |
| CP2 | +0.000177886초 | 02:13:58 |
| CP3 | +0.000145982초 | 02:20:56 |

이 값들은 각 노드가 보고한 NTP 상태다. 노드 간 시각 차이 실측이나 프로젝트의 합격 기준을 새로 정의하지 않는다. 이번 수집에는 chrony 설정 파일 재조회 및 Worker 3대 자료가 없다. **선택 서버가 같다는 관측은 해당 공인 IP가 고정 NTP로 승인됐다는 뜻이 아니다.** 고정 목적지·장애 시 대체 서버·UDP 123 정책과 공통 chrony 설정을 확정하는 후속 작업은 남아 있다.

## 5. 증적의 남은 범위

| 미확보 항목 | 이번 수집 결과와 해석 | 다음 확인 목적 |
|---|---|---|
| 원래 kubeadm join 터미널 출력 | 3대 `_COMM=kubeadm` 조회는 `-- No entries --`; 제한된 저장 로그 검색도 후보 0개 | 원래 실패 phase와 endpoint 특정. 저장되지 않은 stdout은 이번 수집으로 복구할 수 없음 |
| etcd/API/Calico 컨테이너 로그 | 검사 경로에서 `crictl`을 찾지 못해 조회 생략 | 현재 동작하는 CP1의 기존 kubectl 경로를 활용하거나 실제 설치 경로를 확인해 필요한 로그만 보완 |
| etcd 등록 상태 | member list / endpoint status 미수집 | CP2의 잔여 멤버·learner, peer/client URL, 실제 멤버 수 확인 |
| CP2 기존 구성의 세부 상태 | 매니페스트 존재 여부만 수집 | 파일 메타데이터, 비밀정보를 제외한 endpoint, kubeadm CLI 버전, etcd 데이터 존재 여부 확인 |
| Worker 방화벽·NTP | 이번 대상에 포함되지 않음 | 6대 포트·시간 정책을 완결 |

각 kubelet journal은 마지막 1,500줄로 제한된다. CP2·CP3의 실제 포함 범위는 약 10:48:17~11:30:50 KST이므로 전날 join 시점을 포함하지 않는다. containerd journal은 CP1 415줄, CP2 153줄, CP3 104줄이다. 문자열 크기 제한에 따른 잘림 표시는 없지만, 줄 수 제한 자체는 남는다. 포함 로그에서 `context deadline exceeded`나 `no route to host`가 발견되지 않았다는 이유로 과거 오류가 없었다고 판단하지 않는다.

`crictl` 검색 실패는 노드 전체의 미설치 확정이나 containerd 장애 판정이 아니다. 이번에는 sudo의 기본 PATH 밖도 일부 확인했지만, 수집기가 검사하지 않은 위치 또는 신뢰 조건을 통과하지 못한 실행 파일까지 조사한 결과는 아니다.

## 6. 담당 티켓과 후속 순서

아래 담당은 2026-09-10 GitHub 티켓 본문을 다시 읽어 확인했다. 세부 진단 항목은 해당 Scope에 매핑한 작업안이다.

| 순서 | 작업 | 담당 및 티켓 | 완료를 판단할 증적 |
|---|---|---|---|
| 1 | CP2의 부분 구성과 CP1의 etcd 등록 상태 확인 | [P1-K8S-01 #10](https://github.com/321Team/fds-project-baseline/issues/10): 이하영 Lead, 이권욱 Support/Review Owner | member list·endpoint 상태, CP2 기존 구성 메타데이터, 가능한 실패 시점 로그. CP2 재개/재구성 절차를 선택할 근거 |
| 2 | CP 통신 누락의 최소 허용 변경안 고정 | [ICR-D-NET-01-01 #14](https://github.com/321Team/fds-project-baseline/issues/14): 이권욱 Lead, 이하영·이재환 Support; 실제 K8S 시험은 #10 | 대상·Source /32·zone·규칙·유지/회수 조건·복구 방법을 명시한 변경안 |
| 3 | 변경 후 CP1 도달성 확인, CP2부터 순차 가입 처리 | #10 이하영, #14 이권욱 연계 | CP2/3→CP1 2379/2380 도달성 및 필요한 API 경로, etcd 등록·건강, CP2 Ready 이후 CP3 진행 |
| 4 | Worker 3대 상태 보완 및 클러스터 완성 | #10 이하영, 공통 OS/수집 지원 이권욱 | Worker 방화벽·10250·NTP, 6/6 Ready, etcd 3 Member Healthy, cross-node DNS/통신 |
| 5 | 고정 NTP 목적지와 공통 설정 확정 | #14 이권욱 조정, [P1-ANS-01 #65](https://github.com/321Team/fds-project-baseline/issues/65) 계열 후속 변경, #10 정책 반영 | 승인 endpoint/대체 서버, 적용 설정과 6대 시간 동기·UDP 123 회귀. #65는 현재 closed/Done이며 새 작업을 자동 종결/재오픈하지 않음 |
| 6 | HEP/GNP 보호 인계와 회귀 검증 | #14 이권욱 / #10 이하영, [P1-SEC-01 #19](https://github.com/321Team/fds-project-baseline/issues/19) 보안 회귀 | 보호 정책 선행, canary, 관리·클러스터 Positive/Negative, firewalld 인계, reboot/rollback 증적 |

1번 상태 확인과 2번 변경안 준비는 병행할 수 있다. 실제 CP2 가입 재시도는 부분 구성 처리 방법과 필요한 통신 경로가 정리된 뒤 진행한다. 담당자 부재 중 이권욱 측에서 읽기 전용 증적을 보완해도 기존 Lead의 담당 자체가 변경되는 것은 아니다.

### 포트 변경안에 반영할 구체 범위

| 경로 | Source | Destination / Port | 조건 |
|---|---|---|---|
| IF-12A | CP .102/.103/.104 각각 /32 | CP 3대 TCP 2379–2380 | 현재 CP active zone은 public. 상호 etcd 통신과 필요한 client 경로 유지 |
| IF-12B | CP .102/.103/.104 각각 /32 | Node 6대 .102/.103/.104/.105/.107/.108 TCP 10250 | Worker의 실제 zone과 리스너 확인 후 포함 |
| IF-11A / IF-12 | 기존 6 Node→LB / LB→CP 계약 | LB .109:6443 / CP:6443 | 기존 계약에 따라 관리. 이번 readyz 결과로 모든 LB backend/인증 경로가 검증됐다고 하지 않음 |
| CP 직접 API | 실제 필요한 CP Source /32 | 해당 CP:6443 | join/System API의 실제 경로와 승인 근거를 #10/#14에 연결해 판단 |

Source 계약 기준: [정본 v3.1.17 §15.1](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md). 기존 검토서 `FDS_CP_NTP_Port_Diagnostic_Review_20260910_v1.0.md`의 매핑을 이어받았다.

제안됐던 `10.1.93.0/24` 전체 허용과 2시간 후 일괄 소멸을 완료 상태로 채택하지 않는다. etcd와 kubelet 통신은 join 이후에도 필요하므로 Calico 보호로 인계될 때까지 필요한 경로를 유지해야 한다. Runtime/Permanent 및 NetworkManager zone 연결을 함께 확인하고, 변경에서 추가한 규칙만 정확히 회수할 수 있도록 기록한다. `--timeout` 규칙은 정해진 시간이 지나면 제거되며 `--permanent`와 함께 사용할 수 없다. [firewalld 공식 문서](https://firewalld.org/documentation/man-pages/firewall-cmd.html)

## 7. 이번 결과로 처리할 판정

| 항목 | 판정 |
|---|---|
| 수집 경로의 SSH 인증 실패 | 이번 실행에서는 해소 확인 |
| CP 3대 unit/journal/firewall/NTP 증적 확보 | 완료, 컨테이너 로그와 원래 join 출력의 한계 명시 |
| CP2·CP3 kubelet 반복 종료의 직접 원인 | 설정 파일 부재 확인 |
| CP 통신 방화벽 보완 | 후속 필요 |
| 원래 kubeadm timeout의 단일 원인 확정 | 미완료 |
| Kubernetes 3CP+3Worker / etcd 3 Member AC | 미충족 |
| HEP/GNP 보호 전환 AC | 미충족 |
| 고정 NTP 승인·6대 반영 | 이번 자료로 입증되지 않음 |
| 독립 리뷰·운영 승인 | 이 기술 검토로 대체하지 않음 |

이번 파일과 원본 압축파일의 고정 해시를 #10의 가입 상태 확인 및 #14의 변경 전 증적에 연결할 수 있다. 동일한 전체 수집기를 다시 돌리는 것보다, 다음에는 **etcd 등록 상태와 CP2의 잔여 구성**을 우선 보완하는 것이 필요하다.
