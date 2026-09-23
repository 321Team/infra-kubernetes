## 2026-09-16 후속 우선순위 조정

사용자 지시에 따라 [정본·후보 기반 단순화 및 운영 보완 준비](../change/ICR-D-NET-01-01/CNI_SCOPE_SIMPLIFICATION_20260916.md)를 적용한다. PR #11의 추가 범용 기능·daemon 확장·VM 패키지/시험은 보류하고, 완료된 R5 증적과 R6 수정본을 보존한다. 아래 실행기를 실제 사용하는 경우 기존 qualification·독립 회수 검사는 유지한다. 별도 운영 보완안은 해당 변경의 범위·Positive/Negative/Regression·복구·영수증 호환성을 함께 확정하며 PR #11 전체 완성을 모든 대안의 공통 선행조건으로 두지 않는다.

Worker 준비·코드 검토는 병행한다. [최신 Lead 입력](https://github.com/321Team/fds-project-baseline/issues/10#issuecomment-5697547621)에 따라 실제 HEP/GNP 적용은 6/6 Ready 이후 별도 승인/실행창으로 유지한다. 기존 자료의 5/5 통신 시험이나 격리 normal 성공을 CP1 인수·CP2 변경 승인으로 전환하지 않는다.

---

## 2026-09-16 R6 — 운영 recovery 종료 후 unit 정리

R5의 실제 normal은 kernel wrapper 범위이며 운영 실행기 qualification을 대체하지 않는다. R6는 운영 disarm에서 실행 중인 recovery unit을 삭제하지 않도록 보완한다. 변경된 운영 소스는 새 exact-source qualification과 실행 결정이 필요하다.

- `recovery_disarmed=true`는 timer 비활성·대기 job 부재와 service/timer disabled 확인 후 기록한다. 실행 중인 recovery service의 unit 파일은 보존한다.
- 새 job의 `unit_cleanup_protocol=1`을 확인한 controller는 기존 `--collect`와 execute의 최종 수집에서 **해당 job의 staged node**로 종료 후 정리를 요청한다. 이전 job에 protocol이 없으면 status 조회만 수행하며 새 node 소스를 덮어쓰지 않는다.
- 정리는 source/manifest/파일 소유권을 다시 확인하고 work/recovery/timer의 종료·PID·queued job 및 fragment/drop-in을 대조한다. 활성 service를 중지하지 않는다. 다른 unit은 제거하지 않는다.
- `UNIT_EXIT_STATE.json`에 최초 terminal 상태를 보존한 뒤 owned unit 파일을 삭제/reload한다. 도중 실패는 `unit_cleanup_pending=true`로 반출하며 동일 job collect로 재시도한다.
- `--execute` rc=0은 진단 성공·작업창과 함께 `unit_cleanup_complete=true`, `unit_cleanup_pending=false`까지 요구한다. `--collect` rc=0은 pending/실패 상태를 포함한 증적 수집 성공이며 Runtime 성공이 아니다. 최종 정리 응답 유실 시 먼저 받은 증적과 오류를 보존한다.
- 새 install은 이전 job의 cleanup/disarm과 새 protocol의 unit 정리까지 확인한다. direct run/recover/collect도 staged source 검증 전에 STOP 또는 systemctl 변경을 하지 않는다.

[R6 자체 점검·검증과 미검증 범위](../change/ICR-D-NET-01-01/CNI_RECOVERY_R6_REVIEW_20260916.md). 현재 R6는 STATIC/MOCK 검증 단계이며 CP1/CP2 적용 지시나 qualification 증적이 아니다.

# CP1 CNI 진단 실행기 — 실행 코드 리뷰용

2026-09-15 구현. **현재 검증은 STATIC/MOCK이며 실서버 실행 지시가 아니다.** 코드 독립 리뷰, 관측 패키지와 일치하는 격리 Linux 통합시험, 실행 결정이 남아 있다. 아래 `--execute`는 이 조건 충족 후 사용하는 인터페이스다.

## 승인 범위

[설계 PR #10](https://github.com/321Team/infra-kubernetes/pull/10)은 HEAD `c073f837b324b50e14bfeda92b01e8ed150d4d3d`에 [정식 APPROVED](https://github.com/321Team/infra-kubernetes/pull/10#pullrequestreview-5205541379)와 [인터페이스 제한 수용](https://github.com/321Team/infra-kubernetes/pull/10#issuecomment-5674822164)을 확보하고 merge `085b0a3ea0ce9e47b4f52c72bf765e4ba8f8e5ef`로 병합됐다. 이 승인은 설계에 대한 기록이다. 새 실행 소스의 승인을 대신하지 않는다.

기존 proposal JSON의 `implementation_started=false`, `formal_pr_approved_review=false` 등은 설계 제출 당시 기록으로 보존한다. 구현·시험 현황은 이 실행기 문서와 해당 PR을 기준으로 한다.

- CP1 한 대, 동일 노드의 시험 Pod 2개만 사용한다. Worker 준비·코드 검토와 병행하며 선행조건으로 두지 않는다. 실제 HEP/GNP 적용은 최신 Lead 입력에 따라 6/6 Ready 이후 별도 승인으로 진행한다.
- 권고를 반영해 `/26`은 **CP1 IPAM 소유 확인 범위**로만 사용한다. 실제 ACCEPT는 실행 중 확인된 A `/32`→B `/32`, B `/32`→A `/32`의 TCP8080 두 규칙이다.
- `fds-cni-trial` zone의 정확한 host veth 2개만 Runtime으로 바인딩한다. zone은 `target=default`, `forward=no`; source, 서비스, 포트, NAT, Permanent binding은 비운다.
- 진단 결과로 K8S/SEC/ICR AC, CP1 최종 인수 또는 CP2 변경을 승인하지 않는다. 성공해도 policy·zone·binding을 회수한다. 기존 CP1 영수증과 후속 consumer는 변경하지 않는다.

## 파일과 역할

| 파일 | 역할 |
|---|---|
| `scripts/run-cp1-cni-diagnostic.py` | 기본 계획 출력, 승인 입력·격리 통합 증적 검증, 기존 controller/SSH 검증 재사용, 작업 설치·감시·결과 회수 |
| `scripts/cp1-cni-diagnostic-node.py` | CP1 root 전용 실행, systemd 작업·독립 복구 설치, Pod 생성·신원 확인·시험·증적·자원 정리 |
| `scripts/cni_diag_core.py` | `/32`·veth·compiled nft 범위 검증, wall/BOOTTIME 제한, 변경 의도 저장·상태 대조·회수 |
| `tests/test_cni_diagnostic.py` | 호스트 변경 없는 모의 firewall-cmd 및 실패 경로 시험 |

기존 `node-firewall.py`, `run-node-firewall.py` 및 node policy는 수정하지 않았다. 원 CP1 최종 영수증의 root 보관 파일·SHA256과 `receipt.after`를 직접 대조한다. 원본을 덮어쓰거나 진단 상태를 후속 노드 인수 영수증으로 제출하지 않는다.

## 시간·연결 단절·회수

### 실패 판정과 종료코드

- firewall 변경 전 Service HTTP 및 양쪽 DNS control flow가 모두 PASS여야 한다. 장애 진단 대상인 변경 전 direct Pod HTTP의 실패는 허용한다. 변경 후 direct HTTP 양방향·Service HTTP·DNS 양쪽의 5개 positive가 모두 PASS여야 진단 성공으로 기록한다.
- node는 `diagnostic_outcome`과 `cleanup_complete`/`recovery_disarmed`를 따로 저장한다. 진단 오류·미시험·positive 실패·작업창 초과는 안전한 회수가 끝나도 `DIAGNOSTIC_FAILED_REVIEW_REQUIRED`를 유지하고 work service는 non-zero로 종료한다. 성공한 회수 service는 반복 실패로 처리하지 않는다.
- controller `--execute`는 안전한 COMPLETE 상태, 진단 성공, positive 5/5, 작업창 내 완료 및 unit 정리 완료를 확인해야 rc=0이다. qualification/SSH/ACK/전달 해시 오류·poll 기한 초과는 증적 마감 후 non-zero다. 최초 원격 identity 조회/JSON 오류도 RESULT/archive에 남긴다. 로컬 source 검증·사전 점검을 통과하기 전에는 원격 실행과 증적 디렉터리 생성을 시작하지 않는다.
- `--collect`의 rc=0 / OPERATION_STATUS=EVIDENCE_COLLECTED는 실패 상태를 포함한 증적 수집 성공이다. 원격 진단 결과는 별도로 읽는다. `--recover`의 rc=0 / RECOVERY_REQUESTED는 비동기 회수 요청 접수이며 회수 완료가 아니다. 같은 RUN_ID를 collect하여 cleanup/disarm을 확인한다.

### 회수 순서

1. 컨트롤러 사전 점검 후 시작 시각을 기록한다. 최대 20분 중 마지막 3분을 회수에 예약한다. 과거 18:00/19:00 등의 날짜별 작업창을 재사용하지 않는다.
2. 방화벽/Kubernetes 변경 전 root 소유 실행 소스·입력·상태 기록을 보관하고, 독립 **복구 service + timer**를 설치·활성 상태로 확인한다. 서비스는 다음 부팅에도 회수부터 수행한다. 기본 상태 확인을 마치지 못하면 변경을 시작하지 않는다.
3. 진단 본체는 별도 systemd service에서 실행한다. SSH 종료가 본체 수명을 결정하지 않는다. timer는 17분 경계에서 본체 cgroup을 중지하고 같은 lock을 얻은 뒤 회수한다. 실패 시 30초 뒤 재시도하며 새 진단으로 돌아가지 않는다.
4. 각 방화벽 명령 전 `STATE.json`에 전후 예상 상태와 명령 의도를 fsync한다. 결과를 못 받으면 재조회하여 변경 전/후 중 어느 상태인지 확인한다. 설명할 수 없는 변경을 덮어쓰지 않는다.
5. Permanent 빈 zone 및 disabled policy를 만들고 reload한다. **reload는 원 Runtime 규칙 6개를 일시 제거하므로 가용성 영향이 있을 수 있다.** 먼저 정확한 6개를 복원하고 API/시스템 Pod/NM 상태를 확인한다.
6. 정책 활성화 전에 컨트롤러가 **새 SSH 연결로 요청 nonce를 확인·응답**해야 한다. 45초 안에 이 확인을 못 받으면 정책을 활성화하지 않고 회수한다.
7. 원복은 Runtime policy disable → 소유 veth unbind → Permanent policy 삭제 → Permanent zone 삭제 → 필요 reload → 원 Runtime 6개 복원이다. 원 영수증·전체 firewall 상태·Namespace 부재·관리/API/NM 상태를 확인한 뒤에만 복구 예약을 해제한다.

시계 역행이나 suspend가 작업 시간을 늘리지 않도록 wall time과 `CLOCK_BOOTTIME` 중 작은 잔여시간을 사용한다. 부팅이 바뀌면 진단을 재개하지 않는다. 20분 뒤에도 회수가 실패했다면 허용을 연장하는 대신 **RECOVERY_REQUIRED·시간 초과**를 남기고 회수만 재시도한다. 재부팅 때문에 시스템 Pod UID/재시작 수가 달라지면 방화벽 복원이 끝나도 인수와 동일하게 취급하지 않고 사후 검토 대상으로 남긴다.

다른 주체의 변경을 발견하면 확인 가능한 자기 Runtime policy만 우선 disable하며, 무조건 reload하거나 타 객체를 덮어쓰지 않는다. API/NetworkManager 조회가 실패해도 영속 journal과 Runtime policy가 일치하는 경우 해당 policy disable을 시도한다. 일치가 확인되지 않으면 자동 삭제를 거부하고 실제 남은 상태를 기록한다.

## 실제 시험과 판독 한계

- 같은 새 Pod에서 변경 전·활성화 후의 직접 HTTP 양방향, Service HTTP, DNS 양쪽을 기록한다. 직접 TCP 흐름은 사용 전 해당 tuple의 conntrack 부재를 확인하고 지정한 source port를 사용한다. 전역 conntrack flush는 없다.
- TCP8081 Negative는 수신 Pod의 localhost 리스너를 먼저 확인한다. 송신 Pod의 정확한 veth에서 source/destination/source port가 일치하는 새 SYN을 수집하고 접속 결과와 연결한다. **timeout만으로 PASS를 만들지 않는다.** 이 결과는 흐름 차단 관측이며 Calico/firewalld 중 어느 규칙이 차단했는지를 단정하지 않는다.
- 생성된 nft JSON에서 해당 policy로 들어가는 **양쪽 interface 제한**과 두 `/32` TCP8080 ACCEPT를 확인한다. 모르는 dispatch 형식은 중단·회수한다. 문자열/구조 확인은 실제 packet trace를 대체하지 않는다.
- 물리 NIC spoof, 터널, 기존 Pod, 주소 범위 밖 경로의 CP1 실측은 이 실행기에서 자동 생성하지 않는다. 다른 실행 주체/경로가 준비되지 않은 항목은 **NOT_TESTED**다. 전체 Negative 또는 CP1 Runtime 인수 완료를 출력하지 않는다.
- 임시 namespace 생성 응답이 유실되어 UID를 확보하지 못한 경우에는 label만으로 삭제 권한을 추정하지 않는다. 방화벽 변경 전의 이 실패는 namespace 소유 확인·정리가 필요하며, 자동 완료로 기록하지 않는다.

## 사용 순서

2026-09-15 독립 검토 후속으로 [격리 Linux 통합시험 착수 절차](CNI_ISOLATED_INTEGRATION.md)와 읽기 전용 `collect-cni-integration-preflight.py`를 추가했다. 이 도구는 시험 후보 VM의 플랫폼을 조회하며 qualification을 만들지 않는다. 일반 시험 VM용 실제 통합시험 진입점은 아직 별도 구현·검토가 필요하다.

로컬 검토와 현재 사용할 수 있는 명령:

```bash
python3 -m unittest discover -s tests -p test_cni_diagnostic.py -v
python3 scripts/run-cp1-cni-diagnostic.py
```

두 번째 명령은 **PLAN_ONLY**이며 서버에 접속하지 않는다. 출력된 실행 파일별 hash와 bundle hash를 통합시험 대상에 고정한다.

이후 독립 Linux 통합시험에서 아래 표의 실제 증적을 확보한다. 현재 작업 환경은 PID 1이 systemd가 아니며 유효 capability가 0이므로 이 시험을 수행하지 않았다. `tests`의 fixture나 MOCK 결과를 `RUNTIME_ISOLATED` 증적으로 바꾸면 안 된다.

| 격리 환경 검증 | 필요한 증적 |
|---|---|
| 같은 veth 집합·두 `/32` TCP8080 | 양방향 허용 및 실제 nft dispatch·packet trace |
| TCP8081, 주소 pair 밖, 물리 ingress spoof/egress, tunnel ingress, 기존 Pod egress | 신규 비DNAT 흐름의 ingress 도착 + packet/trace + 허용 범위 밖 여부 |
| 연결 단절, 변경 직후 kill, 시간 만료, 재부팅, 회수 중 실패·재시도 | systemd 상태/journal, 영속 intent, policy·zone·binding 부재, 원 6개 복원 |
| NM/ifcfg | 파일/프로필 변경 없음, trial interface의 managed=no, Permanent binding 없음 |

실행 입력은 독립 검토된 증적을 가리켜야 한다. `execution_inputs()`는 형식·고정 소스·필수 결과를 대조하고 `verify_evidence_files()`는 실제 첨부 파일 해시를 확인한다. 이 검사가 GitHub의 승인 사실을 자동 조회하거나 증적의 진실성을 독립 판정하는 것은 아니다. **실행 HEAD·시험 증적·실행 결정의 실제 리뷰 회신을 별도로 확인한다.**

`decision.json` 필수 필드:

```json
{
  "schema": "fds.cp1-cni-diagnostic-decision.v1",
  "source_commit": "REVIEWED_EXECUTION_HEAD",
  "execution_approved": false,
  "review_url": "ACTUAL_EXECUTION_HEAD_REVIEW_URL",
  "execution_decision_url": "ACTUAL_EXECUTION_DECISION_URL",
  "qualification_review_url": "ACTUAL_QUALIFICATION_REVIEW_URL",
  "operator": "ACTUAL_OPERATOR",
  "backup_reference": "EXISTING_VERIFIED_BACKUP_REFERENCE",
  "console_reference": "EXISTING_VERIFIED_CONSOLE_REFERENCE",
  "cp1_hep_transition_not_started": false,
  "same_cp1_change_conflict_clear": false
}
```

이는 실행 불가 예시다. 이미 확인한 백업/콘솔의 근거를 연결하며 같은 자료를 반복 제출하도록 요구하지 않는다. 실제 실행 결정이 없는데 값을 true로 바꾸지 않는다.

`qualification.json` 계약은 `fds.cp1-cni-qualification.v1`, stage `RUNTIME_ISOLATED`, 실행 bundle SHA256, review URL, 관측 순서의 4개 package 문자열을 포함한다. `cases` 키는 `peer_8080`, `peer_8081`, `outside_pair`, `physical_spoof_ingress`, `physical_egress`, `tunnel_ingress`, `existing_pod_egress`다. 각 항목은 `verdict`, `fresh_direct_flow`, `ingress_arrival_observed`, `packet_capture_sha256`, `nft_trace_sha256`을 갖는다. 실제 파일은 같은 디렉터리에 보관하고 상대경로→SHA256을 `files_sha256`에 기록한다. `recovery`의 `disconnect`, `killed_after_mutation`, `deadline`, `reboot`, `cleanup_retry`, `nm_ifcfg_unchanged` 결과와 `all_owned_objects_absent`도 요구한다. 미시험 값으로 실행 자격을 생성하지 않는다.

위 조건 충족 후 **ansible01/server01_ansible**, 승인된 실행 commit의 깨끗한 checkout에서:

```bash
python3 scripts/run-cp1-cni-diagnostic.py --execute \
  --decision /path/to/reviewed/decision.json \
  --qualification /path/to/reviewed/qualification.json
```

기존 승인 inventory·SSH key·known_hosts 검증을 재사용한다. 필요한 `tcpdump`/`conntrack`가 없거나 플랫폼이 다르면 자동 설치하지 않고 중단한다. 네트워크 정책 변경 전에 독립 회수를 예약하며, 명령 출력은 단계·run ID·ARCHIVE/SIDECAR로 제공한다.

연결이 끊겼을 때는 같은 run을 먼저 수집한다. 새 진단을 반복하지 않는다.

```bash
python3 scripts/run-cp1-cni-diagnostic.py --collect RUN_ID
python3 scripts/run-cp1-cni-diagnostic.py --recover RUN_ID
```

`--recover`는 기존 CP1 로컬 복구 서비스를 시작한다. 로컬 source/API 문제로 컨트롤러 복구를 사용할 수 없으면 **CP1 콘솔에서 이미 설치된** `fds-cni-recover-RUN_ID.service`를 시작하고 해당 service journal 및 보호된 job의 `STATE.json`을 확인한다. 새 소스로 이전 job의 원본을 덮어쓰거나 원 영수증·정책 상태를 임의 수정하지 않는다. 상태가 `RECOVERY_REQUIRED`인 동안에는 원인과 잔여 객체를 확인한다.

ARCHIVE/SIDECAR에는 전달 무결성과 실제 상태 기록이 포함된다. raw 내부 운영 증적은 별도 검토 파일로 취급하며 PR/티켓에는 요약·hash·검토 링크만 게시한다.

## 참고 근거

- [firewalld policy XML](https://firewalld.org/documentation/man-pages/firewalld.policy.html): disabled·zone 선택·rich rule 정의.
- [zone 정의](https://firewalld.org/documentation/man-pages/firewalld.zone.html): target와 intra-zone forwarding.
- [firewall-cmd](https://firewalld.org/documentation/man-pages/firewall-cmd.html): Permanent 정의·Runtime 활성화·binding·reload 동작.
- [conntrack 공식 manual](https://netfilter.org/projects/conntrack-tools/conntrack-manpage.html): tuple별 읽기 전용 조회.
