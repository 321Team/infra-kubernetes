# v4.0 로컬 검증 기록

검증일: 2026-09-09. 환경: Python 3.12, PyYAML 6.0.3. 실제 Kubernetes·Felix·firewalld 서버에는 접속하지 않았다. 다음은 이 작성 환경에서 수행한 검증 결과다.

| 검증 | 결과 | 한계 |
|---|---|---|
| Python 구문 / bash 구문 | PASS | 실제 OS 명령 동작을 보장하지 않음 |
| 15개 로컬 테스트 | PASS | 통신/호스트/API 호출은 합성 입력 또는 mock. Runtime 시험 아님 |
| 대표 GNP flow 29개 | PASS | 방향별 규칙 매칭. conntrack·DNAT·다른 정책·서비스 상태까지 결합한 네트워크 시험 아님 |
| node별 failsafe | PASS | 모든 net /32, Worker etcd·API ingress 미허용, DNS/179 예외 미포함을 생성 결과에서 확인 |
| 잘못된 입력 | PASS | 미입력·광범위 NTP net·API 상호 경로 누락·증적 해시 불일치·bundle 변조 시 중단 |
| 복구 보호 경계 | PASS | 보호 receipt 없음/다른 노드이면 API 복구 변경 0; 정상 receipt 이후 HEP→node failsafe 복원 순서 mock 확인 |
| UID/RV 조건부 삭제 | PASS | 요청 body의 preconditions와 stdin 전달 확인. 실제 API 처리는 미검증 |
| AVC / Cockpit 오류 처리 | PASS | 윤일 경계 날짜·시간 argv, ss 조회 실패를 PASS로 만들지 않는 동작 |
| GNP 6 + HEP 6 + Felix 6 | PASS | 공식 v3.32.1 CRD의 served API 및 중첩 field/type/required/enum/pattern/bounds/allOf/anyOf 일부 검사 |
| Calico manifest 준비 | PASS | backend, IPPool env/CIDR/NAT/MTU, BIRD readiness/liveness 제거, 승인 digest 반영 구조 |
| 전체 JSON Schema/CEL | NOT RUN | tests/check-crd.py는 전체 검증기 아님 |
| server dry-run / Admission / Calico 의미 검증 | NOT RUN | 실제 설치 API가 필요 |
| Positive/Negative/Regression/Reboot/Rollback/AVC/Cockpit | NOT RUN | TEST_MATRIX.md에 Runtime 수행·판정 기준 제공 |

## 검토 자료의 무결성

| 자료 | SHA-256 / 기준 |
|---|---|
| 새 첨부 ZIP | `03d12d144b9d75852e53864bc63620da10e2f10c8bbc42809a1b4e48bcb36d8c` |
| 새 첨부 주 본문 | `30011b52f84fc26b90c5f34bd43dbe39d69718710c867420b12a03dbeb10ab3b` |
| 확인한 Calico v3.32.1 공식 manifest | `dce66b28fc93cd59fbc8edf98d54d5f62d91362c1a46c85cbc97ea59e78f15b8` |
| 정본 | v3.1.17, 고정 Commit `2d93395a0e19ea67e5be199d7183a7edbaa6cf4c` |
| 최신 정본 안내 재확인 | CURRENT.md의 Current RELEASED version=v3.1.17 |

정적 테스트의 NTP `192.0.2.123/32`, `test.invalid`, 합성 digest는 테스트 전용이며 운영 파일에 반입하면 안 된다. 릴리스 파일은 별도의 SHA256SUMS로 고정한다. 본인의 로컬 검증을 독립 리뷰나 운영 승인으로 표시하지 않는다.

## 1차 근거

- [FDS 정본 v3.1.17](https://github.com/321Team/fds-project-baseline/blob/2d93395a0e19ea67e5be199d7183a7edbaa6cf4c/baseline/FDS_Project_Implementation_Baseline_v3.1.17.md)
- [CURRENT.md](https://github.com/321Team/fds-project-baseline/blob/main/CURRENT.md)
- [Calico v3.32.1 manifest](https://github.com/projectcalico/calico/blob/v3.32.1/manifests/calico.yaml)
- [Calico v3.32.1 startup: NAT_OUTGOING 처리](https://github.com/projectcalico/calico/blob/v3.32.1/node/pkg/lifecycle/startup/startup.go)
- [HostEndpoint](https://docs.tigera.io/calico/latest/reference/resources/hostendpoint), [GNP](https://docs.tigera.io/calico/latest/reference/resources/globalnetworkpolicy)
- [Felix 설정 우선순위](https://docs.tigera.io/calico/latest/reference/felix/configuration), [Felix 리소스](https://docs.tigera.io/calico/latest/reference/resources/felixconfig)
- [failsafe](https://docs.tigera.io/calico/latest/reference/host-endpoints/failsafe), [conntrack](https://docs.tigera.io/calico/latest/reference/host-endpoints/conntrack), [forwarded traffic](https://docs.tigera.io/calico/latest/reference/host-endpoints/forwarded)
- [VXLAN/BGP](https://docs.tigera.io/calico/latest/networking/configuring/vxlan-ipip), [Calico 시스템 요구사항](https://docs.tigera.io/calico/latest/getting-started/kubernetes/requirements)
- [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html)
- [ausearch 공식 매뉴얼](https://github.com/linux-audit/audit-userspace/blob/master/docs/ausearch.8)
- [etcd endpoint health/status](https://etcd.io/docs/v3.6/tasks/operator/how-to-check-cluster-status/)
- [kubectl delete](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_delete/), [delete raw stdin 구현](https://github.com/kubernetes/kubectl/blob/master/pkg/cmd/delete/delete.go)

공식 latest 문서는 조회 시점 Calico 3.32 기준으로 참조했고, 배포 파일 구조는 v3.32.1 고정본으로 대조했다. Kubernetes 도구의 실제 설치 버전과 옵션은 배포 전 확인한다.
