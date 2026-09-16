# CNI 커널 컴포넌트 시험 — cp1-cni-lab

이 절차는 PR #11의 공통 `cni_diag_core.Transaction`과 parser를 **실제 Linux 커널·firewalld**에 연결해 검증하기 위한 실행 경로다. 네트워크 네임스페이스에 별도 firewalld, 전용 D-Bus, 합성 통신 대상을 만든다. 실행기는 모의 adapter를 사용하지 않는다. 2026-09-16 기존 HEAD 164f8d5의 normal 1회는 daemon 초기화 실패로 종료됐고, 이번 보완 소스의 실제 VM 실행은 미수행이다.

## 2026-09-16 초기화 보완

기존 JOB_ID e97d5bbe64efd9cb의 audit 2283은 system_dbusd_t → var_lib_t:file open 거부, 2286은 firewalld_t → var_lib_t:dir write 거부를 기록했다. 별도로 /run/firewalld의 Errno 30이 journal에 남았다. 통신 6개는 미측정이며 기존 1회 실행 결정은 해당 실패 결과까지 수행됐다.

보완 소스는 다음 경계를 적용한다.

| 자원 | 새 작업 내부 위치 | SELinux 타입 / 쓰기 범위 |
|---|---|---|
| bus 설정 | bus.conf | dbusd_etc_t, daemon 읽기 전용 |
| bus socket | dbus/bus.sock | system_dbusd_var_run_t, bus 전용 디렉터리 |
| firewalld 설정 | firewalld/ | firewalld_etc_rw_t, 해당 하위 트리 |
| firewalld 로그 | log/firewalld.log | var_log_t 부모에 firewalld_var_log_t 파일 사전 생성, 파일만 쓰기 허용 |
| firewalld runtime | run/ | firewalld_var_run_t, 서비스 mount namespace 안에서 /run/firewalld로 BindPaths |

inspect는 libselinux로 **VM에 로드된 정책**의 daemon transition 및 필요한 access vector를 읽어 확인한다. 전역/해당 도메인의 Permissive나 필요한 접근 거부는 준비 실패다. 새 Python SELinux 패키지·allow 모듈·semanage 설정 설치는 하지 않는다. 타입 정의의 upstream 존재만으로 대상 VM의 허용을 가정하지 않는다.

실행 시 새 job의 명시된 객체만 생성·라벨링하고 readback한다. 기존 작업·소스·증적·호스트 경로를 재라벨링하지 않는다. 라벨 확인은 network namespace 생성보다 먼저다. config 디렉터리도 적절한 타입을 부여해 이후 daemon이 생성하는 파일이 일반 var_lib_t를 상속하지 않도록 한다. 작업 중 외부 restorecon/정책 변경이 발생한 환경은 본 시험의 고정 조건으로 취급할 수 없다.

D-Bus service 상태, root 소유 socket 종류·타입, **명시적 private address**의 GetConnectionUnixProcessID 응답과 MainPID를 대조한 뒤 firewalld를 시작한다. daemon이 종료되면 probe 반복을 중단한다. 초기화 완료 시 /proc/MainPID/root/run/firewalld의 device/inode가 job/run과 같고 호스트 runtime과 다른지, mount/net namespace가 전용인지 검사해 RUNTIME_VIEW.json에 기록한다.

SELINUX_LAYOUT.json, 최초 실패 journal, 수집 시점 journal 및 별도 수집 rc를 증적에 포함한다. journal은 이 job의 4개 unit과 시작 시각 이후로 제한하며 최근 최대 2,000줄/2MB다. 수집 실패는 시험·정리 성공으로 바꾸지 않는다.

collect의 텍스트 반출본에서 UUID 형태 문자열은 SHA256 표기로 바꾼다. 원본 job/실행 입력은 VM에 보존하며 EXPORT_REDACTION.json에 원본·반출본 해시와 치환 수를 남긴다. pcap 등 바이너리에 UUID 형태 문자열이 있으면 자동 반출을 중단한다. 이는 UUID 평문 반출 방지이며 모든 식별정보의 익명화를 보장하지 않는다. **반출된 REQUEST/decision은 실행 입력으로 재사용하지 않는다.** inspect·template 원본은 VM 내부 바인딩에 필요하므로 전송 시 별도 사본을 비식별 처리하고 원본은 로컬에 보관한다.

보완 소스는 독립 리뷰와 새 inspect/실행 입력 대조가 필요하다. 164f8d5의 APPROVED를 새 digest 승인으로 재사용하지 않는다. TimeoutStopSec/self-cleanup 경합은 별도 잔여이며 kill/deadline/cleanup-retry/reboot 실행을 이번 절차가 허용하지 않는다.

전체 격리 통합시험 qualification을 완성하는 도구는 아니다. 운영 node adapter의 API/SSH/Calico Pod 검증과 생산 recovery service 경로는 이 wrapper로 실행되지 않는다. `RUNTIME_ISOLATED` qualification을 만들거나 기존 생산 계약을 우회하지 않는다. 실제 VM NIC·터널·기존 Pod 경로와 운영 복구 검증은 후속 범위다. `PRODUCTION_MATRIX.json`의 흐름 7개·복구 6개는 NOT_TESTED로 유지하고, 이번 실제 관측은 `component_flows`와 별도 scope로 기록한다.

## 확인된 준비와 실행 경계

- 대상은 물리 server04의 별도 `cp1-cni-lab`, 계정 server02다. 기존 server04/CP3 VM과 운영 CP1에서 실행하지 않는다.
- 제출된 18:20:48 KST 준비 증적 `3873_0a1`은 무결성·기존 4개 소스 일치 및 PREPARATION_REVIEW_REQUIRED / blockers=[]로 검토 완료했다. conntrack 추가 설치를 반복하지 않는다.
- `FDS_CNI_LAB_PRE_CONNTRACK_20260915` snapshot 생성은 18:18:15 KST 화면에서 확인했다. 해당 snapshot으로 되돌리면 추가 RPM 설치 이전 상태가 된다. snapshot 복원 시험 완료를 뜻하지 않는다.
- VM NIC는 FDS-CNI-LAB로 유지한다. 공유 폴더 FDS_CNI_TRANSFER는 소스 반입 시 read-only, 결과 반출 시에만 일시적으로 쓰기를 허용한다.
- 실행 경로는 dbus-daemon/firewalld/unshare/mount/busctl/journalctl, libselinux 및 CAP_SYS_ADMIN을 사용하므로 아래 inspect로 이 항목과 정확한 소스·VM 식별 바인딩을 확인한다. 이를 일반 준비 수집 12개나 RPM 재설치와 혼동하지 않는다.
- 변경 실행 전 이 새 소스에 대한 독립 검토 및 **KERNEL_COMPONENT_ONLY** 실행 결정이 필요하다. 이전 준비 수집 승인이나 설계 PR #10 승인을 새 실행 소스 승인으로 기입하지 않는다.

## 실제로 만드는 대상

| 역할 | 구성 | 범위 |
|---|---|---|
| r | 별도 router network namespace, private firewalld/D-Bus | 호스트 네트워크 namespace에 연결된 인터페이스 없음 |
| a / b | 별도 network namespace, TCP 8080·8081 HTTP listener | 합성 주소 10.244.246.80 / .81, calikia / calikib |
| c | 별도 network namespace, TCP 8080·8081 HTTP listener | 합성 outside-pair 주소 .82, calikic |
| ens160 | r 내부 dummy interface | 원 장비 NIC가 아닌 baseline 모델용 내부 이름 |
| private baseline | public zone 및 원 규칙과 같은 문자열의 Runtime 6개 | 합성 fixture임을 기록하며 원 CP1 영수증을 읽거나 수정하지 않음 |

주소·링크는 모두 새 namespace 안에 만든다. 원 NIC를 이동하거나 호스트 route/sysctl/firewall을 변경하지 않는다. host firewalld의 명령은 전후 읽기 전용 조회뿐이다. 별도 D-Bus는 root 전용 Unix socket이며 실제 host bus로 연결하지 않는다. firewalld daemon의 파일 시스템은 systemd ProtectSystem=strict로 제한하고, firewall-cmd는 private mount namespace에서 실제 ifcfg 디렉터리를 숨긴다. SELinux Enforcing을 유지하며 거부 시 설정을 완화하지 않고 실패 증적을 검토한다.

## 1. 새 소스 반입 및 추가 조회

독립 리뷰 대상으로 제출된 정확한 GitHub commit ZIP을 Windows 공유 폴더에 내려받아 기존 HGFS 절차로 시험 VM 홈에 복사한다. 기존 cb7fc695 준비 ZIP에는 새 실행기가 없다. 새 소스를 별도 디렉터리에 풀고 **그 저장소 루트**에서 실행한다.

```bash
sha256sum --check CNI_KERNEL_COMPONENT_SHA256SUMS
python3 scripts/run-cni-kernel-integration.py
sudo /usr/bin/python3 -I -B scripts/run-cni-kernel-integration.py inspect
```

첫 명령은 source 4개를 대조한다. 기본 실행은 PLAN_ONLY이며 프로세스 실행·설정 변경이 없다. inspect는 필요한 프로그램·패키지·권한과 VM 식별을 조회하고, root 전용 새 디렉터리에 `INSPECT.json` 및 `lab-decision.template.json`을 만든다. 오류가 나면 출력을 제출한다. 자동 설치/방화벽 변경은 하지 않는다.

두 파일이 표시되면 **INSPECT 파일과 값이 false/빈칸인 결정 초안의 비식별 사본**을 반출해 검토한다. 오프라인 키트의 inspect 절차는 원본을 VM의 session에 보존하고 별도 반출용 사본을 만든다. 원본 template에는 product_uuid, machine-id의 해시와 새 source 4개 해시가 들어 있다. 비식별 사본을 start 입력으로 쓰지 않으며 원문 운영 정보는 PR 댓글에 게시하지 않는다.

## 2. 결정 입력

결정 schema는 `fds.cni-kernel-lab-decision.v2`다. 딥리뷰 보완 전 v1 초안과 이전 소스 inspect는 새 실행 입력으로 재사용하지 않는다. 새 소스 4개 해시를 검증한 뒤 inspect를 다시 수행한다. VM 준비·RPM 설치 전체를 반복할 필요는 없다.

독립 검토 완료 후 실제 사실에 맞게 template의 operator / snapshot_reference / review_url / reviewed_bundle_sha256 / execution_approved를 확정한다. `reviewed_bundle_sha256`는 새 INSPECT의 `executor_bundle_sha256`와 같아야 한다. 이 값은 source 4개의 이름→SHA256 사전을 키 정렬·공백 없는 JSON으로 직렬화한 SHA256이다. 리뷰 기록에 해당 digest와 실행 HEAD를 함께 남겨 사람이 대조할 수 있게 한다. 초안의 digest는 빈칸이고 실행 승인은 false로 생성된다.

review_url은 **infra-kubernetes PR #11의 해당 소스 독립 리뷰 또는 승인 근거 댓글**이어야 한다. 설계 승인·작성자 HOLD 댓글·다른 PR 승인을 넣지 않는다. schema, source hashes, VM product_uuid·machine-id hash, lab_vm=cp1-cni-lab, lan_segment=FDS-CNI-LAB, scope=KERNEL_COMPONENT_ONLY가 모두 일치해야 start가 진행된다. 오프라인 실행기는 URL의 PR/형식과 digest 연결을 검사한다. GitHub 작성자·리뷰 상태·내용의 진위를 조회하거나 독립 승인을 생성하지 않으므로 실제 승인 내용을 별도로 대조한다.

VM 식별과 source hash는 재부팅 전후 동일한 환경에 입력을 묶는 장치다. NIC의 실제 LAN Segment 설정 자체는 VMware 화면/현장 확인 근거로 별도 검토한다. 작업 시작 후 최대 20분, 마지막 3분은 회수용이다. 매번 종료시각을 직접 계산해 적는 방식이 아니다.

## 3. 변경본 실행: 추가 normal 한 건 — 새 실행 결정 후

독립 검토·실행 결정이 끝난 **결정 파일의 실제 경로**를 `--decision`에 지정한다.

```bash
sudo /usr/bin/python3 -I -B scripts/run-cni-kernel-integration.py start \
  --decision /home/server02/lab-decision.json --scenario normal
```

- JOB_ID가 출력되면 기록한다. start가 중간 실패하더라도 JOB_ID가 있으면 같은 ID의 status/recover를 사용한다. 새 start를 반복하지 않는다.
- 첫 kernel fixture 변경 전에 root 소스·journal·unit manifest를 저장하고, 부팅 recovery service 및 deadline/retry timer 활성화를 확인한다.
- worker는 systemd service에서 실행한다. 콘솔/호출자의 연결 종료가 진단 프로세스의 수명을 소유하지 않는다.
- 실제 private firewalld의 XML/CLI/compiled nft 형식을 공통 Transaction/parser로 검사한다. 알려지지 않은 상태는 중단하고 실제 반환값을 증적으로 남긴다.
- A↔B TCP8080, A↔B TCP8081, C↔A TCP8080의 6개 관측을 수집한다. listener의 job/role 응답, 새 conntrack tuple, ingress capture, nft trace를 기록한다.
- TCP 성립 여부를 HTTP 처리 결과와 별도로 보존한다. A↔B 8080은 TCP 연결·HTTP 200·해당 job/role 응답을 모두 요구한다. 8081 및 outside pair는 TCP 미연결을 요구한다. 연결 후 HTTP 오류가 나도 TCP 연결 성공은 유지하므로 차단 성공으로 처리하지 않는다.
- 기대 통신 결과 불일치 또는 packet/trace 누락은 COMPONENT_FAILED_REVIEW_REQUIRED로 저장하고 안전한 회수 후 work service가 non-zero로 끝난다. 기대값 일치의 자동 verdict는 OBSERVED_REVIEW_REQUIRED이며, 차단 component attribution은 실제 packet/trace 독립 검토로 남긴다. 송신 timeout만으로 차단 PASS를 출력하지 않는다.
- 정리 시 공통 Transaction.withdraw를 먼저 시도한다. 실패해도 해당 실패 기록을 보존하고 서비스 cgroup 종료·소유 namespace 제거로 시험 자원을 격리 해제한다. 이 대체 정리는 공통 회수 코드의 성공으로 집계하지 않는다.

정상 관측부터 한 건씩 제출한다. 첫 actual compiler/CLI 실패를 해결하기 전에 다른 시나리오를 일괄 실행하지 않는다.

## 4. 조회·회수·반출

아래 JOB_ID는 출력된 **16자리 소문자 hex 값**으로 바꾼다.

```bash
sudo /usr/bin/python3 -I -B scripts/run-cni-kernel-integration.py status JOB_ID
sudo /usr/bin/python3 -I -B scripts/run-cni-kernel-integration.py collect JOB_ID
```

collect는 worker/recovery의 transaction lock을 잠깐 확보해 증적 snapshot을 만든다. 작업 중 lock 충돌이면 status로 진행 상태를 확인한 뒤 다시 collect한다. 회수가 실패한 상태도 archive로 반출할 수 있으며 EXPORT_STATE의 recovery_pending=true를 숨기지 않는다.

수동 회수 필요 시:

```bash
sudo /usr/bin/python3 -I -B scripts/run-cni-kernel-integration.py recover JOB_ID
```

현재 checkout의 recover는 위치 확인·검증만 수행한다. root 소유 job·보관 소스 SHA256·unit 파일을 확인한 뒤 **해당 job에 보관한 실행기의 `_recover`**로 프로세스를 교체한다. 실제 회수는 보관 소스의 unit 생성 규칙과 journal을 재검증한 뒤 진행한다. checkout B에서 작업 A를 회수해도 B의 변경 코드는 실행하지 않는다. 직접 `_work`/`_recover`로 우회하면 보관 경로·실행 소스 불일치 검사에서 변경 전에 거절한다. 보관 소스/manifest를 최신 파일로 덮어쓰지 않는다.

확인된 소유 unit/cgroup만 중지하고 같은 journal을 따라 정리한다. namespace 이름 재사용/inode 불일치, 생성 결과 미확정 또는 unit 내용 변경은 자동 삭제하지 않는다. `RESOURCES.json`, `UNITS.json`, `RESULT.json`, command log를 근거로 콘솔에서 소유를 먼저 확인한다. 임의의 다른 namespace/프로세스를 삭제·종료하거나 원 VM의 firewall reload로 대체하지 않는다.

work의 진단 실패와 recover의 정리 성공은 별도 결과다. 실패한 시험을 안전하게 회수하면 recover는 성공 종료할 수 있지만 RESULT의 COMPONENT_FAILED_REVIEW_REQUIRED는 보존한다. start 성공은 비동기 실행 요청 접수이며, collect 성공은 증적 반출 성공이다. 둘 다 시험 성공을 의미하지 않는다.

archive는 `/var/lib/fds/cni-kernel-lab/exports/FDS_CNI_Kernel_Component_.../`에 생성되고 ARCHIVE/SIDECAR 경로가 출력된다. 앞서 사용한 `sudo install -m0600 -o UID -g GID`로 해당 두 파일을 사용자 홈에 복사한 뒤 HGFS로 반출한다. 결과 파일의 전후 SHA256을 확인하고 공유 폴더를 read-only로 복원한다.

## 5. 후속 시나리오와 한계

| scenario | 실제 코드 동작 | 해석 제한 |
|---|---|---|
| normal | 공통 prepare/withdraw 및 6개 실제 커널 흐름 관측 | Calico/물리 NIC의 통합 결과는 아님 |
| kill | private policy 활성화 후 자기 worker에 SIGKILL, 독립 회수 | 이 wrapper의 cgroup/영속 ledger 경로 |
| deadline | private policy 활성화 후 timer에 의한 회수 대기 | 90초 가속 timer이며 production 20분 검증 아님 |
| cleanup-retry | SIGKILL 후 첫 회수에 의도적 실패, timer 재시도 | 이 wrapper의 retry 검증, 생산 복구 adapter 검증 아님 |
| reboot | WAIT_REBOOT 기록 후 operator의 실제 VM 재부팅 대기 | VM을 코드가 재부팅하지 않음; namespace 소멸/부팅 정리이며 원 CP1 6개 복원 증명 아님 |

kill/cleanup-retry도 회수 timer는 90초다. 준비가 그보다 늦으면 목표 mutation 이전에 회수될 수 있으며 해당 목표 시험을 완료로 집계하지 않는다. 각 시나리오는 별도 실행·증적이다. reboot는 승인된 별도 시험 창에서 수행한다.

## 다음 통합 범위

이 컴포넌트 결과로 실제 XML/CLI/compiled nft 가정을 검토한 다음, 실제 Calico Pod·VM NIC/터널/기존 Pod fixture 및 생산 node adapter의 API·SSH·재부팅·원6개 복구 통합 경로를 구현·검토해야 한다. 전체 7개 흐름·6개 복구의 실제 증적과 독립 인수가 갖춰져야 기존 qualification을 작성할 수 있다. CP1 최종 인수·CP2 확대·AC와 Worker 병행 조건은 기존 합의대로 유지한다.

공식 근거: [firewalld private instance 옵션](https://firewalld.org/documentation/man-pages/firewalld.html), [D-Bus의 별도 bus 설정](https://dbus.freedesktop.org/doc/dbus-daemon.1.html), [unshare의 network/mount namespace와 private propagation](https://man7.org/linux/man-pages/man1/unshare.1.html). 이 근거는 옵션의 의미이며 현장 실행 성공 증거가 아니다.
