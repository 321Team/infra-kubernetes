# D-Bus 설정 문법 수정 — 2026-09-16 normal 후속

## 판정

HEAD `5f9dd61f551b3a88029d6112e2b66b9cefa91634`의 승인된 추가 normal 1회는 job `3271fd1489888551`로 실행됐고, 전용 D-Bus 설정 파싱 단계에서 실패했다. 상태는 `COMPONENT_FAILED_REVIEW_REQUIRED`이며 통신 6개는 모두 미측정이다. 새 수정안은 코드·로컬 검증까지 수행했으며 **새 소스 독립 리뷰·대상 VM inspect·추가 실행 결정 및 Runtime 검증이 필요**하다.

## 증적 검증

- 제출물: `FDS_CNI_Kernel_Component_a5qduh17.tar.gz` 및 sidecar.
- 외부 SHA256: `52908e90636fbfb838160e42e8c9b813ee7cf8ff8fa54bd3b18111a54994bd18`.
- 내부 SHA256SUMS **18/18 일치**. REQUEST의 실행 소스 4개·bundle digest·리뷰 URL·normal 시나리오를 대조했다.
- 실제 시작: **2026-09-16 15:22:17 KST**. D-Bus 종료: **15:22:19 KST**. 화면 업로드 시각을 실행 시각으로 사용하지 않는다.
- 실행 근거: [이하영 최종 리뷰](https://github.com/321Team/infra-kubernetes/pull/11#issuecomment-5692799169)와 사용자의 조건부 착수 결정.
- `SELINUX_LAYOUT.json`의 loaded-policy 조회는 Enforcing, 23 checks / 0 failed였다. 이것은 모든 daemon 접근이나 통신 성공의 증명이 아니다.
- 최초 실패 journal과 collect journal 모두 아래 오류를 기록했고 수집 rc=0이다.

```text
Failed to start message bus: <allow> rule cannot be per-user because it has bus-global semantics
```

`cleanup_complete=true`, `recovery_disarmed=true`, `fixture_namespaces_absent=true`, `host_configuration_unchanged=true`, `EXPORT_STATE.recovery_pending=false`를 대조했다. HOST_BEFORE/AFTER JSON은 동일하다. `recovery_attempts=0`이므로 독립 timer/reboot 회수 성공으로 확대하지 않는다.

`component_flows={}`, pcap/trace 파일 없음, PRIVATE_FIREWALL.log는 0바이트다. firewalld 시작 전 D-Bus 준비 검사에서 중단됐으므로 firewalld 수정과 CNI 통신의 Runtime 성공은 미확인이다. 추가 AVC 수집은 이번 문법 오류 확정에 필요하지 않다.

## 원인과 수정

기존 `prepare_daemon_files()`는 `<policy user="root">` 안에 `<allow user="root"/>`를 넣었다. 연결 admission 규칙은 bus-global이므로 이 위치가 유효하지 않다. 같은 문법은 초기 `164f8d5` 소스에도 존재했다. 앞선 시험에서는 설정 파일 접근 거부가 먼저 발생했다.

[D-Bus 공식 매뉴얼](https://dbus.freedesktop.org/doc/dbus-daemon.1.html)의 policy/admission 설명에 따라 연결 허용 규칙을 default 정책으로 옮긴다. 순서는 전체 연결 거부 후 root만 허용이다. root의 name ownership·메시지 송수신 권한은 기존처럼 사용자별 정책에 남긴다.

```xml
<policy context="default">
  <deny user="*"/>
  <allow user="root"/>
</policy>
<policy user="root">
  <allow own="*"/>
  <allow send_destination="*"/>
  <allow receive_sender="*"/>
</policy>
```

`private_bus_config()`로 실제 설정 생성부를 추출해 프로세스 시험도 같은 생성 결과를 사용한다. 기존 EXTERNAL 인증·전용 Unix 주소·root 전용 경로·SELinux·서비스 격리·회수 동작은 유지한다. 기존 실패 job의 설정을 직접 편집하거나 재시작하지 않는다. 실행 소스 변경은 커널 컴포넌트 실행기 1개다. 공통 core·운영 node·node-firewall은 이전 HEAD와 동일하다.

새 kernel bundle SHA256: `e3930449699c963b367109f87191122f47e9e20b306a60f0dbd091a9144794c6`.

## 검증과 한계

| 구분 | 결과 |
|---|---|
| STATIC | Python AST·기본 PLAN_ONLY·소스 체크섬 PASS |
| MOCK/구조 검증 | **211/211 PASS**, 기존 209 + admission 경계 2 |
| 로컬 실제 dbus-daemon 1.14.10 | 3건 중 **1 PASS / 2 SKIP** |
| 실제 프로세스로 확인한 것 | 원래 per-user admission 문법의 거부 재현 |
| 수정본 프로세스 검사 | 문법 거부를 지나 socket 생성 단계 도달. 로컬 AF_UNIX 생성 EPERM으로 기동·readiness/name ownership·admission 차단 실측은 미검증 |
| 최초 로컬 기동 검사 이력 | 문법 오류 재현 1건 통과, 나머지 2건은 socket EPERM으로 실패. 환경 제한을 별도 확인한 뒤 SKIP으로 명시하는 검사로 보완 |
| 실제 non-root 접속 거부 | 로컬 setuid EPERM으로 미검증 |
| 새 소스 대상 VM inspect / Runtime | 미실행 |
| 새 소스 REVIEW / MERGE | 대기 / 미수행 |
| CI | 이 로컬 검증으로 CI PASS를 생성하지 않음 |

`unittest`의 `OK (skipped=2)`를 3 PASS로 보고하지 않는다. 로컬 D-Bus 검사는 SELinux Enforcing 대상 VM·systemd mount/net namespace·firewalld 기동·실제 packet/trace를 검증하지 않는다. 23/23 정책 조회와 211개 모의시험이 D-Bus 설정 파싱·실제 daemon 기동 검증을 대신하지 못한 점을 이번 프로세스 검사로 분리했다.

## 후속 및 리뷰 범위

1. 이 변경의 admission 규칙 위치·순서·root 제한 및 실제 생성 설정을 사용하는 테스트를 독립 리뷰한다.
2. 새 소스 고정 후 새 디렉터리로 오프라인 반입하고 inspect를 대조한다. 기존 5f9dd61 실행 결정·inspect는 새 소스 승인으로 재사용하지 않는다.
3. 새 리뷰가 normal 진행 가능을 명시하고 실행 결정이 충족되면 추가 normal 1회를 수행한다. 기존 작업·증적은 보존한다.
4. daemon/6-flow/packet/trace/cleanup/disarm를 대조한다. 성공 판정 전까지 Runtime·qualification·AC를 갱신하지 않는다.

이번 실행 1회는 이미 실패 결과까지 수행됐다. 현재 VM에서는 공유 폴더를 읽기 전용으로 복원하고 증적을 보존한다. `start` 반복·SELinux 비활성화·전체 재라벨링·운영 CP1/CP2 변경은 수행하지 않는다. stop/self-cleanup 경합과 다른 시나리오는 별도 잔여다.
