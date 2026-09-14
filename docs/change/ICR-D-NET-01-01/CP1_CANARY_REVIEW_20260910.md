# FDS CP1 Canary 사전 점검 검토서 v1.0

검토일: 2026-09-10. 대상: `FDS_CP1_Canary_2vp6t68c.tar.gz` 및 동일 이름의 `.sha256`.

**판정: 제출 파일 무결성 PASS / 주요 운영 증적 확보 / 방화벽 사전 점검 미완료 / 적용 미실행.**

수집기 v1.0의 firewalld 정책 출력 처리 오류로 CP 3대의 firewall snapshot이 생성되지 않았다. CP1의 `canary_inputs_complete=false`이므로 이 결과를 적용 입력으로 사용할 수 없다. 오류를 수정한 v1.1로 읽기 전용 점검을 다시 수행한다. 기존에 확보된 etcd·로그·TCP 증적은 유효하며 폐기하지 않는다.

## 1. 무결성 및 실행 출처

| 항목 | 검증 결과 |
|---|---|
| 압축파일 SHA-256 | `7fb7bfc6ce3b71505c6bfbef290503b6d59d292d4928605d788be5375879b572` — sidecar와 일치 |
| 내부 체크섬 | REQUEST, RESULT, CP 3개 JSON 총 5/5 일치. manifest가 전체 증적 파일을 포함 |
| 압축 구조 | 디렉터리 1개·일반 파일 6개. 중복·절대경로·경로 이탈·symlink 없음 |
| 수집 시각 | 2026-09-10 03:45:06.547920–03:45:12.791629 UTC / 12:45:06–12:45:12 KST |
| 실행 모드 | `inspect`, `runtime_applied=not_attempted` |
| 소스 commit | infra-kubernetes `0325ee798ef7bdc4e6dc582362043d899c1f3641` |
| 원격 Python 소스 SHA-256 | `2741d5c0c50d5b35d15b7b54dda8d6517d4571515027786367b2ec7827f26398` — 배포 소스와 일치 |
| 기존 Inventory commit | infra-ansible `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab` |
| 관리 접속 | 10.250.10.10 → CP .102/.103/.104:22, svc_ansible. SSH identity 3/3 일치, sudo/root 전제 검사 이후 진단 실행 |
| 일반 진단 조회 | CP1 15개, CP2 9개, CP3 8개 총 32개 rc=0. 별도 snapshot 사전 검사에서는 3대 모두 오류 |
| 출력 제한 | 일반 조회의 byte truncation 표시는 없음. journal·Pod logs에는 아래 줄 수 제한이 적용됨 |

원본 `RESULT.status=COLLECTED_REVIEW_REQUIRED`와 `failed_queries=[]`만으로 전체 점검 성공을 판단하면 안 된다. v1.0은 snapshot 오류를 `gaps`에만 남겼다. 각 CP JSON의 `gaps`, CP1의 `canary_inputs_complete=false`, snapshot 부재를 함께 확인했다.

원본 보관 식별자: archive `libfile_7476c3cc34988191a0ec7f2b819459fb`, sidecar `libfile_e836723202848191bcedb928d952fcae`.

## 2. CP1 — 실제 etcd 구성 확보

출처: `k8s-control01.json`의 `result.queries.etcd_members`, `etcd_status`, `nodes`, `api_readyz`, `listeners`.

| 확인 항목 | 사실 |
|---|---|
| Kubernetes Node | k8s-control01 한 대, Ready=True |
| kubeadm CLI | CP 3대 모두 v1.36.4 |
| etcd member 수 | 1개. CP2·CP3 및 다른 member 항목 없음 |
| member 이름 / ID | k8s-control01 / `1755679419291778130` |
| peer / client URL | `https://10.1.93.102:2380` / `https://10.1.93.102:2379` |
| learner | 원본 응답에 isLearner=true 없음. 정규화 결과 false |
| endpoint status | 127.0.0.1:2379 조회 성공, etcd v3.6.8, leader ID가 CP1 member ID와 일치 |
| raft 상태 | raftIndex=115392, raftAppliedIndex=115392, raftTerm=2. 조회 응답에 errors 항목 없음 |
| API readyz | 37개 검사 ok 및 `readyz check passed` |
| 리스너 | CP1의 2379·2380·10250·6443 확인 |
| 서비스 | kubelet/containerd/firewalld active/running, NRestarts=0 |

이전에는 Node 목록만 있었으나 이번에는 etcd member list를 직접 확보했으므로 **현재 등록된 etcd 멤버는 CP1 하나**라고 판단할 수 있다. 이는 3CP HA 완료 증적이 아니다. `endpoint status`와 API readyz가 성공했지만 별도 `endpoint health`, 장애 전환, 재부팅 시험을 수행한 것은 아니다.

## 3. CP2 잔여 구성과 로그

출처: `k8s-control02.json`의 `result.residual_files`, `queries.join_window_kubelet`, `queries.units`.

| 확인 항목 | 사실 |
|---|---|
| 남은 매니페스트 | kube-apiserver.yaml, kube-controller-manager.yaml, kube-scheduler.yaml — root 소유, 일반 파일, mode 0600 |
| mtime | 2026-09-09 12:52:32.896–.898 UTC / 21:52:32 KST |
| etcd 매니페스트 | `/etc/kubernetes/manifests/etcd.yaml` 없음 |
| kubelet 설정 | `/var/lib/kubelet/config.yaml`, `kubeadm-flags.env`, `/etc/kubernetes/kubelet.conf`, `bootstrap-kubelet.conf` 없음 |
| 표준 etcd 데이터 경로 | `/var/lib/etcd`, `/var/lib/etcd/member` 없음. 다른 위치의 데이터 존재 여부를 검사한 것은 아님 |
| 현재 kubelet | activating/auto-restart, NRestarts=5581. CP3도 같은 상태·횟수 |
| containerd/firewalld | CP2·CP3 모두 active/running |
| CP2·CP3 리스너 | 2379·2380·6443·10250 미관측 |
| 과거 journal | 12:40–13:10 UTC를 요청했으나 최대 800줄로 제한. 실제 범위 12:47:15–13:09:58 UTC |
| 과거 오류 | config.yaml 로딩 실패 133회, KUBELET_KUBEADM_ARGS 미설정 메시지 133회 |

**해석:** CP2의 부분 부트스트랩 상태와 kubelet 설정 부재가 재확인됐다. 파일 mtime은 기존 이미지 Pull 시점과 일치하는 시간대이지만, 작성 명령·실행 주체·정확한 kubeadm 중단 phase를 증명하지 않는다. 현재 CP1 etcd 목록에 CP2가 없으며 CP2 표준 데이터 경로도 없지만, 이것만으로 무조건 reset하거나 join을 재실행하도록 판정하지 않는다.

CP1 etcd 로그는 최근 300줄(9월 9일 19:55:16–9월 10일 03:40:16 UTC)로, 원래 join 시점보다 뒤다. 300줄 모두 info 수준이며 warn/error/fatal은 없었다. API server 로그는 90줄(9월 9일 12:45:16–9월 10일 03:35:16 UTC)이며 수집 범위에서 오류 줄은 관측되지 않았다. 원래 join stdout·API 감사 로그·원래 실패 phase는 여전히 확보되지 않았다.

## 4. 통신 및 방화벽

출처: 각 CP의 `result.tcp_to_cp1`, `queries.public_runtime`, `public_permanent`, `nft`, `listeners`.

| Source → CP1(10.1.93.102) | TCP 2379 | TCP 2380 | TCP 10250 |
|---|---|---|---|
| CP1 .102 | 연결 성공 | 연결 성공 | 연결 성공 |
| CP2 .103 | 실패 errno 113 | 실패 errno 113 | 실패 errno 113 |
| CP3 .104 | 실패 errno 113 | 실패 errno 113 | 실패 errno 113 |

3개 CP의 public Runtime/Permanent 출력은 기존과 같이 `dhcpv6-client ssh` 서비스와 LB `.109/32 → TCP 6443` rich rule 한 건이다. CP Source의 2379–2380 및 10250 허용은 없다. Runtime의 interface는 ens160이고 Permanent interface는 비어 있다.

CP1 nft에는 ens160 ingress가 public 처리로 진입한 뒤 `icmpx admin-prohibited`로 reject되는 규칙 경로가 있다. public allow에는 TCP 22, IPv6 DHCP, LB의 TCP 6443 허용이 있다. 해당 포트의 CP1 리스너와 자기 IP 접속은 성공하지만 CP2/CP3 접근은 실패한다. **현재 방화벽 미허용이 CP 간 통신 차단에 관여한다는 판단을 뒷받침한다.** 실제 실패 패킷의 trace나 원래 join stdout이 없으므로 과거 `context deadline exceeded`의 단일 원인이라고 확정하지 않는다.

이번 검사에는 Worker, LB 연결 재검증, CP 직접 TCP 6443 probe, NTP, HEP/GNP 재조회가 포함되지 않았다. 이전 상태를 이번 신규 검사 결과로 소급 표시하지 않는다.

## 5. 수집기 결함 및 v1.1 수정

세 CP 모두 다음 오류를 기록했다.

```text
FIREWALL_PRECHECK: query failed: firewall-cmd --info-policy=ANY;
rc=140; Error: INVALID_POLICY: ANY
```

v1.0은 `--get-active-policies` 출력을 공백으로 나눠 모든 단어를 정책 이름으로 취급했다. 그 결과 zone 표시의 `ANY`까지 `--info-policy`에 전달했다. 이는 제공한 도구의 출력 처리 결함이며 서버에서 `ANY`라는 정책을 생성해야 한다는 뜻이 아니다. 원래 active-policies 전체 출력은 v1.0 증적에 보존되지 않아 해당 출력 전체를 복원했다고 주장하지 않는다.

v1.1 변경:

1. 정책 이름은 전용 `--get-policies` 결과에서만 읽고, Runtime/Permanent별 실제 정책 정보를 조회한다. `--get-active-policies` 출력은 원문 보관용으로만 사용한다. 이 이름 조회 인터페이스는 [firewall-cmd 공식 문서](https://firewalld.org/documentation/man-pages/firewall-cmd.html)의 Policy Options를 따른다.
2. 모든 snapshot 질의의 반환 코드·출력·오류를 `snapshot_queries`에 보존한다. NM zone이나 정책 조회가 실패해도 앞서 확보한 값이 사라지지 않는다.
3. `gaps` 또는 실패 질의가 있으면 controller 요약을 `INCOMPLETE_REVIEW_REQUIRED`로 표시한다. TCP 연결 실패는 별도 운영 진단 결과로 유지한다.
4. ANY/HOST 오인 방지, 전체 snapshot 생성, 비활성·Permanent 정책 보존, 질의 오류 보존, 잘못된 이름 목록 거부, 미완료 상태 집계에 대한 회귀 검증을 추가했다.

로컬 자동 테스트 **30개 PASS**(기존 25개 + 신규 5개). 회귀 fixture는 합성 출력이며 실서버 재검증을 대체하지 않는다. 첫 적용 범위 CP1, /32 및 포트 제한, 기존 규칙 보존, 변경 기록·중단·복구 조건은 유지한다. 새 소스이므로 v1.0 점검 JSON을 v1.1 적용 입력으로 재사용하지 않는다.

## 6. 티켓 매핑 및 후속 순서

| 작업 | 티켓 / 담당 | 상태·다음 조치 |
|---|---|---|
| etcd member list/status | #10 P1-K8S-01 / 이하영 Lead, 이권욱 Support | 이번 증적 확보 완료. 단일 CP1, CP2 member 없음 |
| CP2 잔여 파일·과거 로그 | #10 / 이하영 Lead, 이권욱 Support | 표준 경로·mtime·제한된 journal 확보. 원래 실패 phase와 재개 절차 판단은 미완료 |
| 수집기 수정 및 CP 통신 보완 | #14 ICR-D-NET-01-01 / 이권욱 Lead, 이하영·이재환 Support | v1.1 재점검 → 현재 HEAD 독립 리뷰·실행 결정 → CP1 적용·전후 검사 |
| CP2/CP3 가입 재개 | #10, #14 연계 | 방화벽 및 잔여 상태 검토 후 순차 결정. 현재 미실행 |
| HEP/GNP·보호 인계 | #19 및 #14/#10 | 이번 결과로 완료 상태 상향하지 않음 |
| 고정 NTP·Worker 증적 | #14 조정, 관련 후속 | 이번 수집 범위 밖. 기존 미완료 유지 |

연결: [#10](https://github.com/321Team/fds-project-baseline/issues/10), [#14](https://github.com/321Team/fds-project-baseline/issues/14), [infra-kubernetes PR #2](https://github.com/321Team/infra-kubernetes/pull/2).

현재 제출본은 15분 유효시간 이전에도 snapshot 부재로 적용 입력에 부적합하다. v1.1의 새 `inspect` 결과와 해시를 먼저 확보한다. PR #2는 이 검토 시점 Draft이며 등록된 독립 리뷰는 0건이다. 본 기술 증적 검토는 독립 리뷰·운영 승인·실제 적용 결과를 대신하지 않는다.
