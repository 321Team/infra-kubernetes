> 이 문서는 13:56 KST 수집에 대한 원본 검토 이력이다. 이후 정본 정합성·신규 독립 리뷰 및 구현 상태는 [선행 변경 기록](ADVANCE_CHANGE_RECORD_20260910.md)과 [검증 기록](VALIDATION.md)을 따른다. 본문에 남은 당시 Draft/리뷰 0건/차기 canary 안내는 현재 승인 상태가 아니다. 원본 검토서의 내용은 보존했다.

# FDS CP1 Canary v1.1 재점검 검토서 v1.0

검토일: 2026-09-10. 대상: `FDS_CP1_Canary_jvgkgfm9.tar.gz` 및 `.sha256`.

**판정: 수집기 재점검 및 사전 증적 검증 PASS. 방화벽 적용·운영 인수는 미실행.**

이전 `ANY` 정책명 오인 오류가 해소됐다. CP 3대의 firewall/NM snapshot과 조회 원문이 생성됐으며, CP1은 `canary_inputs_complete=true`다. CP2·CP3에서 CP1로 향하는 필수 포트 연결은 여전히 실패한다. 따라서 이 PASS는 오류 수정 및 변경 전 상태 확보에 대한 판정이며 통신 보완 완료를 뜻하지 않는다.

## 1. 검증 대상과 무결성

| 항목 | 확인 결과 |
|---|---|
| 실행 구간 | 2026-09-10 04:56:16.288721–04:56:28.471551 UTC / 13:56:16–13:56:28 KST |
| 실행 ID | `20260910T045616Z-66d22dedc710` |
| 모드·변경 여부 | `inspect`, `runtime_applied=not_attempted` |
| 원본 압축파일 SHA-256 | `45a23e3039a37edf4ee17a540263a1a3c558ab1e0d50b7aa2f783f0fa8f61b55` — sidecar 일치 |
| 내부 체크섬 | REQUEST, RESULT, CP 3개 JSON 총 5/5 일치. manifest 전체 범위 확인 |
| 압축 구조 | 일반 파일 6개와 상위 디렉터리. 중복·절대경로·경로 이탈·특수 파일 없음 |
| 소스 HEAD | infra-kubernetes `068661e076de02669170d3582f3d596d6e1a8eb3` |
| 원격 Python 소스 SHA-256 | `5b1c9bccaf80cafc7f52eaf1ad9799d4a342981784f8fdb484013e27b19ae41b` — v1.1 소스와 일치 |
| 계획 SHA-256 | `2c1e6101724d078a0a8fd58497d4d84ae5453e938529496c3045b96ea0fef7d7` |
| 기존 Inventory HEAD | `5cbf3b657014b32a20cb5816d90d3ea5fe4951ab` |
| SSH identity | 관리 Source 10.250.10.10, svc_ansible, CP 대상 주소·hostname 3/3 일치 |

원본 식별자: archive `libfile_c9ae9f2374808191809e5110fb8c20ba`, sidecar `libfile_733515c4951c8191bfa829609a8352c4`.

## 2. 수집기 오류 해소와 입력 완전성

| 대상 | 일반 조회 | Snapshot 조회 | 조회 오류·잘림 | gaps | canary_inputs_complete |
|---|---:|---:|---|---|---|
| CP1 | 15 | 26 | 없음 | 없음 | true |
| CP2 | 9 | 26 | 없음 | 없음 | false — CP1 전용 입력 설계 |
| CP3 | 8 | 26 | 없음 | 없음 | false — CP1 전용 입력 설계 |
| 합계 | 32 | 78 | 110개 기록 모두 rc=0 | 0건 | CP1 조건 충족 |

조회 수는 수집 JSON에 저장된 명령 결과 기록 수다. TCP 검사는 별도이며, 같은 상태를 일반 조회와 snapshot에서 중복 확인한 항목도 포함한다. stdout/stderr의 byte truncation 표시는 없지만, journal과 Pod logs에는 줄 수 제한이 있다.

3대 모두 실제 active policy 원문은 다음 형태다.

```text
allow-host-ipv6
  ingress-zones: ANY
  egress-zones: HOST
```

이번에는 `ANY`를 정책명으로 조회한 호출이 없고, 실제 정책 6개를 Runtime/Permanent 각각 정상 조회했다. 이전 버전의 원인 설명이 실제 출력으로 확인됐다. CP2/CP3의 `canary_inputs_complete=false`는 수집 실패가 아니다. 코드는 CP1에서만 topology 및 적용 입력을 완성한다.

각 노드의 snapshot과 CP1 topology를 동일한 canonical JSON 방식으로 다시 해시하여 기록값과 대조했다.

| 입력 | SHA-256 |
|---|---|
| CP1 snapshot | `18d316967c24a16506734833f0bada6bb8febf86b0327bf35ed7a538871e3864` |
| CP2 snapshot | `881f54336ac6e0687720b975bc6e0eb0d719beed81050f96886629f0c752092e` |
| CP3 snapshot | `5a56c0c2cafca98f831eaaddd096ceb43292b3c5e90b6873457eec10261a0baa` |
| CP1 topology | `b4eb4932d00f53396e2fd55e704eb4246ce6a3d4a963d1029472f16c43e0c4af` |
| CP1 결과 JSON 파일 | `5fbbb035e1cbaaefcab61e94a382407e97c70b0b29249e4043bb514cd41afc97` |

## 3. 방화벽·NetworkManager 상태

출처: 각 노드 `result.snapshot`, `snapshot_queries`, `queries.public_runtime`, `public_permanent`.

- SELinux Enforcing, firewalld running, active/default zone은 public이다.
- ens160의 Runtime zone은 public이다. NM 활성 connection UUID를 조회했고 connection.zone 원문은 빈 값이다. 현재 default/실제 interface zone은 public으로 일치한다. 이는 재부팅 지속성 시험을 대신하지 않는다.
- Runtime의 interface는 ens160, Permanent interface는 빈 값이다. 임의로 interface 또는 NM zone을 수정할 근거가 추가된 것은 아니다.
- Runtime/Permanent public 출력은 이전 12:45 KST 제출본과 동일하다. 서비스는 dhcpv6-client·ssh, rich rule은 LB .109/32 → TCP 6443 한 건이다.
- CP Source의 TCP 2379–2380 및 10250 허용은 아직 없다. 기존 계획의 CP1 추가 후보는 6개 규칙 × Runtime/Permanent = 최대 12건이다.
- direct Runtime/Permanent rule 조회는 빈 결과다.
- 활성 정책은 allow-host-ipv6다. 별도 gateway 계열 정책 5개는 Runtime/Permanent에서 disable=yes이며, 해당 정책의 ACCEPT target만 보고 활성된 전체 허용으로 해석하면 안 된다.

## 4. 클러스터 및 통신 상태

| 항목 | 신규 증적 |
|---|---|
| CP1 Node | 유일한 등록 Node, Ready=True |
| CP1 etcd | 단일 member, 이름 k8s-control01, member/leader ID `1755679419291778130`, learner=false |
| endpoint status | etcd 3.6.8, raftIndex=raftAppliedIndex=124381, raftTerm=2, errors 항목 없음 |
| API readyz | 37개 ok 및 readyz check passed |
| CP1 서비스 | kubelet/containerd/firewalld active/running, NRestarts=0 |
| CP2 잔여 파일 | 이전 제출본과 메타데이터 동일. 매니페스트 3개 존재, kubelet 설정·표준 etcd 경로 부재 |
| CP2·CP3 kubelet | activating/auto-restart, NRestarts=5998 |
| CP2·CP3 containerd/firewalld | active/running |

CP1의 Node UID·member ID·peer/client URL을 포함한 topology 해시는 이전 제출본과 동일하다. 실제 증적으로 단일 멤버 상태를 확인했으며 3CP HA 완료로 판정하지 않는다.

| Source → CP1 | TCP 2379 | TCP 2380 | TCP 10250 |
|---|---|---|---|
| CP1 | 성공 | 성공 | 성공 |
| CP2 | 실패 errno 113 | 실패 errno 113 | 실패 errno 113 |
| CP3 | 실패 errno 113 | 실패 errno 113 | 실패 errno 113 |

따라서 외부 CP에서 CP1로 향하는 6개 검사는 모두 실패했고, CP1 자체 접속 3개는 성공했다. 기존 허용 누락에 대한 통신 보완 필요성이 유지된다. 수집기 오류 수정 자체가 서버 포트를 연 것은 아니다.

로그 범위: CP1 etcd 최근 300줄은 9월 10일 06:10:16–13:55:16 KST이며 모두 info다. API server 로그는 98줄이며 수집 범위에 오류 줄이 관측되지 않았다. CP2 과거 journal은 이전과 같은 최대 800줄 범위다. 원래 kubeadm join stdout과 정확한 실패 phase는 여전히 미확보다. NTP·Worker·HEP/GNP·LB·직접 API 6443의 신규 검증은 이 수집 범위에 없다.

## 5. 후속 판정과 인계

| 작업 | 담당 티켓 | 판정 |
|---|---|---|
| v1.1 수집기 및 firewall/NM snapshot 재점검 | #14 / 이권욱 Lead, 이하영·이재환 Support | 이번 제출 증적으로 완료 |
| CP1 etcd·CP2 잔여 상태 확보 | #10 / 이하영 Lead, 이권욱 Support | 확보 상태 재확인. 가입 재개 절차 판단은 미완료 |
| 현 HEAD 독립 리뷰 | infra-kubernetes PR #2 / #14 | 조회 시점 Draft, 리뷰 0건·지정 리뷰어 없음 |
| CP1 canary 적용 및 전후 증적 | #14, #10 연계 | 미실행. 독립 리뷰와 실행 결정 후 수행 |
| CP2/CP3 적용·join, 6/6 Ready, 보호 인계 | #10/#14/#19 | 후속 미완료 유지 |

새 코드 변경이나 키트 교체를 요구하는 수집 결함은 이번 결과에서 발견되지 않았다. 검토 대상 소스는 **`068661e076de02669170d3582f3d596d6e1a8eb3` 그대로**다. 본 결과는 실제 독립 리뷰를 대신하지 않는다.

다음 순서는 현 HEAD 독립 리뷰·실행 결정 → CP1 한 대 canary → 새 관리 SSH·API·etcd·CP2/3→CP1 통신 검증이다. 성공을 확인한 뒤 후속 노드와 가입 재개를 별도로 결정한다. 이번 결과만으로 전체 firewall 중지, reset 또는 join 반복을 수행하지 않는다.

적용 입력은 수집 후 15분 이내여야 한다. CP1 수집 시각은 13:56:17.117807 KST이며 이 제출본의 시간 조건은 **2026-09-10 14:11:17.117807 KST까지**다. 그 이후 적용할 때에는 승인된 동일 v1.1 키트로 inspect를 새로 실행하고 최신 결과 파일·해시를 사용한다. 현재 상태/구성/소스 일치 검사는 실행기에서 다시 수행한다.

출처: 제출 압축파일 내 `REQUEST.json`, `RESULT.json`, 각 CP JSON 및 `SHA256SUMS`; [고정 소스와 실행 절차](https://github.com/321Team/infra-kubernetes/blob/068661e076de02669170d3582f3d596d6e1a8eb3/docs/runbooks/ICR-D-NET-01-01-CP1-CANARY-KIT.md); [PR #2](https://github.com/321Team/infra-kubernetes/pull/2).
