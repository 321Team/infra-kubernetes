# CNI 격리 Linux 통합시험 착수 절차

2026-09-15. [PR #11 검토 댓글](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5675589656)에 따라 실제 플랫폼·패킷 경로·독립 회수 검증을 준비한다. 이 댓글은 상황 및 코드 검토이며 정식 APPROVED가 아니다.

## 현장 배치 확정 — server04

2026-09-15 사용자가 자원 여유를 확인하고 **물리 server04 사용**을 선택했다. 앞선 server03 우선 권고를 대체한다. 여유 자원 확인을 반복 요청하지 않는다. 이는 배치 선택이며 VM 복사·격리·부팅 또는 통합시험이 완료됐다는 기록은 아니다.

| 항목 | 적용할 구성 |
|---|---|
| 물리 호스트 | server04 — 정본 Windows Host IP `10.1.93.22` |
| 기존 VM | k8s-control03 유지. 이 VM을 시험 대상으로 전환하지 않음 |
| 시험 VM 표시 이름 | `cp1-cni-lab` |
| 새 복제본 위치 | server04의 `C:\Project321team\lab\cp1-cni-lab` — 기존 폴더와 분리된 새 경로 |
| 초기 할당 | 4 vCPU, RAM 8GB. 원 가상 디스크 용량·가상 NIC 모델 유지 |
| 시험 네트워크 | 전용 LAN Segment `FDS-CNI-LAB`; 복제본의 모든 NIC를 이 구간에만 연결 |
| 관리 방법 | server04의 VMware 콘솔. 운영용 ansible inventory에 복제본을 등록하지 않음 |
| 추가 peer | NIC/터널 시험 단계에 같은 server04의 별도 `cni-peer-lab`을 계획. 초기안 2 vCPU·RAM 2GB, 아직 생성 미확인 |

### 복사부터 첫 부팅까지

1. **기존 백업이 실제로 있는 PC**의 `D:\server02` 폴더에서 별도 복사본을 만들어 server04의 위 새 경로로 전송한다. `.vmx` 하나만 옮기지 않고 가상 디스크와 snapshot chain을 포함한 VM 폴더 전체를 옮긴다. server04에도 같은 `D:` 경로가 있다고 가정하지 않는다.
2. 원본 `C:\Project321team\server02`, 원 백업 `D:\server02`, 복구 자료 `C:\Project321team\FDS_CP1_Recovery`는 보존한다. 새 복제본의 디스크가 원 백업이나 운영 폴더의 파일을 참조하지 않는지 확인한다. 외부 parent 디스크 참조가 있으면 독립 복사가 완성되지 않은 상태이므로 먼저 해당 의존성을 해결한다.
3. server04 Workstation에서 **새 복제 폴더 안의 `.vmx`**를 열고, 전원을 켜기 전에 표시 이름을 `cp1-cni-lab`, RAM 8GB, CPU 총 4개(예: 1 processor × 4 cores)로 설정한다. VMware 표시 이름과 Linux hostname은 별개다. 이 준비 단계에서 게스트 hostname·IP·Kubernetes 설정은 변경하지 않는다.
4. **전원 OFF 상태에서 모든 Network Adapter**를 확인한다. `LAN segment`에 새 `FDS-CNI-LAB`을 만들고 각 NIC에 선택한다. Bridged/NAT/기존 VMnet 연결이나 다른 네트워크로 연결된 추가 NIC를 남기지 않는다. 기존 CP3의 NIC 설정은 건드리지 않는다.
5. 이 구성을 확인한 뒤 시험 복제본만 부팅한다. 이동/복사 여부를 묻는 창이 나오면 **I copied it**을 선택한다. 콘솔 로그인 후 `hostnamectl`과 `ip -br address`로 관측한다. 기존 이름/IP가 표시되더라도 격리된 복제본의 관측이며 운영 CP1의 새 증적이 아니다.
6. 현재 문서와 준비 소스를 검증된 파일 전송으로 넣고 아래 읽기 전용 수집을 수행한다. 파일 전달을 위해 운영망 NIC를 추가하지 않는다. 준비 수집 결과를 확보한 다음, 실제 변경 시험 전에 시험 VM의 복원점을 보관한다.

백업 부팅은 현재 CP1 상태를 재현했다는 증거가 아니다. 특히 Runtime-only 방화벽 규칙은 디스크 백업의 부팅만으로 복원됐다고 가정하지 않는다. 복제된 원 영수증이나 hostname이 생산 실행기의 검사를 통과하게 만들기 위해 값을 변경하지 않는다. 실제 통합시험용 진입점과 데이터 출처는 아래 3절의 잔여 구현 범위다.

## 지금 진행할 범위

`collect-cni-integration-preflight.py`로 **격리 시험 후보 VM의 현재 환경만 조회**한다. 패키지 설치, 서비스 시작/중지, firewall reload, 정책·라우팅 변경, Kubernetes 접속, SSH 접속, 패킷 송신은 없다. 로컬 새 증적 디렉터리와 archive/checksum만 작성한다. 기본 실행은 PLAN_ONLY다.

수집 성공은 통합시험 성공이 아니다. 모든 흐름·회수 시험은 NOT_TESTED로 남으며 qualification.json, 실행 승인 또는 AC 인수를 생성하지 않는다. VM으로 감지되더라도 실제 네트워크 격리는 자동 증명되지 않는다.

**아직 격리 시험 VM이 없다면 VM 확보와 연결 구성을 먼저 정한다.** 운영 CP1·ansible01에서 시험 VM 대신 수집할 필요는 없다. 기존 CP1의 백업 폴더를 직접 부팅하지 않는다. 복제본을 사용하는 경우 원 백업을 보존하고, 운영 네트워크와 연결되지 않는 별도 VM 및 가상망임을 확인한 뒤 시작한다. 기존 CP1 백업·콘솔 확인은 유효한 이전 기록이며 다시 제출하도록 요구하지 않는다.

## 1. 후보 VM 사양과 확인 범위

| 대상 | 자동 조회 또는 별도 확인 |
|---|---|
| Linux VM | PID 1 systemd, VM/container 감지, OS/kernel/Python 버전 |
| CP1 관측 패키지 | firewalld-2.4.3-4.el10.noarch, nftables-1.1.5-6.el10.x86_64, NetworkManager-1.58.0-1.el10.x86_64, iptables-nft-1.8.11-16.el10.x86_64 정확히 대조 |
| 호스트 기능 | root 수집, CAP_NET_ADMIN·CAP_NET_RAW, firewalld/NM active, SELinux Enforcing, systemd-run/nft/nmcli/ip/conntrack/tcpdump 등 도구 존재 |
| 격리 | VM 이름·가상 NIC 연결·관리 경로·운영망 분리·복원점은 현장 별도 확인 |
| 시험 소스 | core/node/기존 helper의 각 SHA256 및 bundle SHA256을 기록. 파일 존재·해시는 실제 리뷰 승인 사실을 증명하지 않음 |

VM 및 패키지가 다르거나 도구가 없으면 PREPARATION_BLOCKED다. 자동 설치하지 않는다. 동일 패키지를 확보하기 전 다른 버전의 결과를 CP1의 실행 자격으로 제출하지 않는다. systemd/kernel/가상 NIC·NM 차이는 별도 검토 대상으로 기록한다.

## 2. 읽기 전용 수집

PR의 현재 준비 소스를 별도 checkout 또는 검증된 파일 전송으로 후보 VM에 놓는다. 다음 명령은 **그 checkout의 루트에서, 후보 VM 콘솔로** 실행한다. 외부 GitHub 연결이 없는 VM에 파일을 전달하기 위해 운영망 NIC를 추가하지 않는다.

```bash
python3 scripts/collect-cni-integration-preflight.py
sudo /usr/bin/python3 -I -B scripts/collect-cni-integration-preflight.py --collect
```

`STATUS`, `BLOCKERS`, `ARCHIVE`, `SIDECAR`가 출력된다. 조회는 명령별 최대 4초, 전체 조회 예산 50초로 제한한다. 출력 잘림, 시간 초과, 명령 실패를 숨기지 않는다. PREPARATION_BLOCKED의 종료코드는 2이며 두 결과 파일을 그대로 제출하면 된다. 실패를 해결하기 위해 이전 CP1 실행기나 방화벽 변경 명령을 대신 실행하지 않는다.

파일은 root 전용 `/var/tmp/FDS_CNI_Integration_Preflight_…/` 아래에 생성된다. 일반 사용자로 반출하려면 출력된 **해당 archive와 sidecar 파일만** 다음 형태로 복사한다. 아래 경로는 실제 출력값으로 바꾼다.

```bash
sudo install -o "$(id -un)" -g "$(id -gn)" -m 0600 \
  /var/tmp/FDS_CNI_Integration_Preflight_ACTUAL/FDS_CNI_Integration_Preflight_ACTUAL.tar.gz \
  /var/tmp/FDS_CNI_Integration_Preflight_ACTUAL/FDS_CNI_Integration_Preflight_ACTUAL.tar.gz.sha256 \
  "$PWD/"
```

결과에는 플랫폼 식별 정보가 있으므로 원문 archive를 PR 댓글에 게시하지 않는다. PR에는 판정·해시·검토 결과만 남긴다.

## 3. 실제 시험 전 해결할 실행 진입점

현재 생산 실행기는 `ansible01`/`k8s-control01`, 원 CP1 영수증, 실제 qualification을 요구한다. **일반 시험 VM에서 생산용 `--execute`를 바로 실행할 수 있는 범용 통합시험 harness는 아직 없다.** 준비 수집기는 이 제한을 우회하지 않는다.

후속 구현에서는 격리 시험의 진입점, 승인 대상 소스, 시험 데이터 출처를 먼저 명확히 한다. 실제 firewalld 상태/compiled nft를 읽는 adapter와 독립 systemd service/timer 경로를 시험할 수 있어야 한다. 모의 adapter를 실제 시험으로 기록하면 안 된다. 격리 시험을 위해 source를 변경했다면 생산 bundle과 다른 대상으로 기록하고 검증 범위를 재검토한다.

호스트 이름 변경, 원 CP1 영수증 복제/조작, qualification PASS 값 수동 작성으로 차단을 우회하지 않는다. hostname·원 영수증 등 생산 계약을 어떻게 격리 시험에서 보존하거나 분리할지는 아직 검토할 구현 항목이다. 이 항목이 해결되기 전에는 아래 표는 시험 설계이며 변경 실행 지시가 아니다.

## 4. 시험 순서와 증적

| 순서 | 실제 시험 | 완료 판정에 필요한 자료 |
|---|---|---|
| A | 플랫폼·소스·시험 진입점 확정 | 준비 archive, VM/연결 확인, 실제 시험 코드와 소스 해시의 검토 기록 |
| B | 진단 대상 없이 독립 회수 lifecycle | service/timer 설치·예약·실행·재시도·해제 및 재부팅 후 회수 journal. 검증된 원상 복구 결과 |
| C | 비활성 zone/policy와 실제 compiled nft | 실제 XML/CLI 반환, Runtime/Permanent snapshot, dispatch의 양쪽 veth 조건·정확한 /32·TCP8080 확인. 알려지지 않은 형식은 중단 |
| D | peer_8080 양방향 허용 | 신규 비DNAT tuple, 실제 ingress 도착, 양방향 응답·pcap·nft trace. Service 성공으로 직접 경로를 대신하지 않음 |
| E | peer_8081 및 non-peer 5개 경로 | 각 경로의 목적 listener/경로 준비 확인, 새 tuple·ingress 도착, 결과·pcap·trace. timeout만으로 PASS 금지 |
| F | disconnect / killed_after_mutation / deadline / reboot / cleanup_retry | 실제 프로세스·systemd·kernel 경로 중단 증거, 영속 intent 및 회수 journal, 원6개 복원과 소유 객체 부재 |
| G | nm_ifcfg_unchanged·최종 정리 | NM profile·ifcfg 전후 대조, policy/zone/binding 및 임시 자원 부재, 원본 상태 대조, 시간 초과 여부 |
| H | 증적 독립 리뷰 | source bundle과 각 case의 packet/trace hash, 실제 결과, 남은 NOT_TESTED·실패, qualification 검토 및 실행 결정 |

non-peer 경로는 outside_pair / physical_spoof_ingress / physical_egress / tunnel_ingress / existing_pod_egress다. 물리·터널 경로는 격리 VM NIC/시험 peer에서 재현하고 운영 CP1에 spoof 트래픽을 보내지 않는다. 커널 netns로만 시험한 경로는 실제 VM NIC/터널 재현과 구분해 한계를 기록한다.

실제 순방향 차단을 주장하려면 ingress 도착뿐 아니라 해당 nft chain/rule 경로의 trace가 필요하다. 임시 추적 규칙도 시험 자원으로 전후 기록하고 회수한다. 누적 카운터나 compiled JSON만으로 특정 시험 패킷의 hit를 추정하지 않는다.

## 5. 결과를 전달한 다음

준비 결과를 대조해 필요한 플랫폼 보완·시험 진입점 작업을 확정하고, 실제 통합시험을 진행한다. 모든 필수 증적을 검토한 뒤에만 production qualification 및 실행 입력을 작성한다. 별도 실행 결정 전에는 CP1 `--execute`, CP1 ACCEPTED, CP2 확대를 진행하지 않는다. Worker HEP/GNP 작업은 기존 합의대로 병행한다.

관련: [기존 실행기 가이드](CP1_CNI_DIAGNOSTIC.md), [firewall-cmd 공식 문서](https://firewalld.org/documentation/man-pages/firewall-cmd.html). `--state`는 daemon 상태 조회이며, reload는 Runtime-only 규칙을 소실시킬 수 있는 별도 변경 동작이다.
