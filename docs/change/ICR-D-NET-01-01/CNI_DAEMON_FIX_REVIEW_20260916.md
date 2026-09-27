# CNI normal 실패 후 daemon 초기화 보완 — 독립 리뷰 제출

기준: PR #11 HEAD `164f8d5514add1c845657e1abc500ababf37b1e9`.
수정 대상: 커널 컴포넌트 실행기. 공통 Transaction/parser, 운영 node 실행기, node-firewall helper는 동일 바이트를 유지한다.

## 실패 근거

2026-09-16 11:24:42~11:25:16 KST, job `e97d5bbe64efd9cb`의 normal 1회는 `COMPONENT_FAILED_REVIEW_REQUIRED`로 끝났다. start rc=0은 접수 결과이고 실제 worker는 실패했다. 통신 6개는 미측정이며 pcap/trace가 없다.

| 근거 | 확인 사실 |
|---|---|
| audit 2283 / 11:24:44.743 | system_dbusd_t → var_lib_t:file open 거부, bus.conf, permissive=0 |
| audit 2286 / 11:24:44.805 | firewalld_t → var_lib_t:dir write 거부, job 디렉터리, permissive=0 |
| daemon journal | firewalld.log 생성 실패 및 /run/firewalld/temp.*의 Errno 30 |
| RESULT/EXPORT_STATE | cleanup_complete·recovery_disarmed·fixture_namespaces_absent=true, recovery_pending=false |
| 전후 관측 | 수집한 HOST_BEFORE/AFTER fingerprint 일치. VM 전체 불변 또는 독립 timer/재부팅 복구 성공을 뜻하지 않음 |

AVC 재조회는 PID별 rc=0이며 epoch·PID·경로를 실행 구간과 대조했다. 이전 날짜 인자 조회의 parsing error를 AVC 부재로 해석하지 않았다. 원본 요청의 UUID·machine-id·전체 REQUEST는 이 변경에 게시하지 않는다.

전달 자료 SHA256:

- 진단 archive: `4bb4324f4fb8476d1d5579defce1007e4b3037bdadd6b4e7e8b9f5e3e613847c`
- avc-context.txt: `295ce4a77624f3006b202472d63789e3a7c48e08d7105307b051649aa6b5b00d`
- avc-events.txt: `96651f9229ab156a805cf9dbf822c8856919fd3342a5c02079fcc121b39413a2`

## 코드 변경

1. **설치된 SELinux 정책 조회**: libselinux의 process transition과 access vector를 읽어 daemon domain 및 config/socket/log/runtime 접근을 검사한다. 전역 Enforcing과 domain permissive flag를 확인한다. 필요한 권한이 없거나 API/타입/부모 경로가 맞지 않으면 중단하며 정책을 설치·확장하지 않는다. 조회는 inspect 및 실행 시 새 경로 준비에 적용한다.
2. **새 작업 전용 경로**: bus.conf=dbusd_etc_t, dbus/=system_dbusd_var_run_t, firewalld/=firewalld_etc_rw_t, log/=var_log_t, 사전 생성 log/firewalld.log=firewalld_var_log_t, run/=firewalld_var_run_t. 명시 목록의 새 객체에만 라벨을 적용하고 읽어 확인한다. 심볼릭 링크·하드링크 파일·쓰기 가능한 외부 대상은 거절한다. 기존 job 루트와 증적을 재라벨링하지 않는다.
3. **runtime 격리**: 서비스의 `BindPaths=<job>/run:/run/firewalld:norbind`로 호스트 runtime을 숨긴다. ProtectSystem=strict를 유지하고 설정 트리·로그 파일만 별도 ReadWritePaths에 둔다. 준비 완료 시 서비스 view의 device/inode 및 mount/net namespace를 확인한다. 이전 보고서의 tmpfs 단독 diff는 이번 완성 수정안으로 대체한다.
4. **준비 완료 순서**: bus service 상태 → root 소유 socket 및 타입 → 명시적 private bus 주소의 실제 응답/PID 대조 → firewall 시작 → 상태 및 runtime view 확인. bus 실패 시 firewall을 시작하지 않는다. daemon failed/inactive 상태를 30초간 재시도하지 않는다.
5. **진단 보존**: 정리 전에 최초 실패 journal을 저장하고 collect에도 job unit·시간 범위의 journal 및 별도 rc/오류를 포함한다. 수집 실패가 원래 component 실패나 정리 상태를 덮어쓰지 않는다. 최대 2,000줄/2MB의 수집 제한은 별도 메타데이터로 남긴다.
6. **UUID 반출 보완**: 텍스트 증적 복사본의 UUID 형태를 대소문자 정규화 SHA256 표기로 치환한다. VM 원본은 변경하지 않는다. 원본·반출본 해시와 치환 수를 EXPORT_REDACTION.json에 기록한다. 바이너리 UUID 검출 시 자동 반출을 중단한다. 전체 개인정보 익명화나 원본의 삭제를 뜻하지 않는다. inspect 원본은 로컬 바인딩에 필요하므로 오프라인 키트가 별도 검토용 사본만 반출한다.

## 검증과 한계

- STATIC: Python AST, 기본 PLAN_ONLY, source 4개 SHA256 목록 일치.
- STATIC: systemd 255에서 생성 unit의 지시어 검사 rc=0. **ExecStart를 /usr/bin/true로 대체해 문법만 검사**했으며 VM의 systemd 257·실제 실행 파일·mount 성공을 검증한 것이 아니다.
- MOCK: 기존 170개 및 새 32개, 총 202/202 PASS. 기존 staged-source 회수·실패/정리 분리·통신 기대값·승인 바인딩 회귀를 포함한다.
- 새 시험: 정책 권한 부족/Permissive/API 실패, 제한된 라벨 범위, 라벨 실패 시 namespace 생성 차단, bus 조기 종료·잘못된 PID·시간 초과, runtime 공유·호스트 namespace 거부, journal 실패 분리, UUID 반출 및 최종 archive checksum 검증.
- RUNTIME: 수정본 미실행. 대상 VM의 실제 loaded-policy 조회는 새 inspect로 확인해야 한다. 정책 preflight 통과도 daemon 초기화·모든 SELinux 접근·통신 성공을 보장하지 않는다.
- CI/독립 리뷰/병합/추가 실행 승인: 별도 상태이며 이 기록으로 PASS/완료를 만들지 않는다.
- 기존 `TimeoutStopSec=5`와 self-cleanup 경합은 이번 변경에서 수정하지 않았다. 강제 종료·deadline·reboot의 추가 실행 근거로 사용하지 않는다.

정확한 새 source/bundle digest는 [검증 JSON](CNI_DAEMON_FIX_VALIDATION_20260916.json), 실제 로컬 unittest 출력은 [시험 로그](CNI_DAEMON_FIX_TEST_OUTPUT_20260916.txt)를 따른다.

## 독립 리뷰 확인 항목

1. libselinux ABI/transition 및 실제 설치 정책 조회가 필요한 접근을 정확히 검사하고 실패 시 변경 전에 중단하는가.
2. 새 job 라벨 범위·로그 사전 생성·runtime bind가 기존 job, 호스트 D-Bus/firewalld 및 NM/ifcfg로 쓰기 범위를 넓히지 않는가.
3. bus ready 판정이 정확한 service PID와 private socket으로 묶이며 잘못된/종료된 service에서 흐름 측정으로 진행하지 않는가.
4. 정리·staged-source 회수·unit 소유권 검사가 변경된 unit 정의와도 일관되는가.
5. UUID 치환 및 journal 수집이 원본 증적·실패 판정·checksum·미측정 구분을 보존하는가.

현재 제출된 코드에 대한 독립 리뷰 → 새 오프라인 소스 반입/inspect 및 v2 입력 대조 → 추가 normal 1회 실행 결정 → runtime/packet/trace/정리 검토 순서다. 반입·inspect 자체는 network namespace·daemon·정책 변경을 실행하지 않는다. CP1 최종 인수·CP2 변경·AC credit는 계속 보류한다.

## 구현 근거

- [Fedora SELinux firewalld 정책](https://github.com/fedora-selinux/selinux-policy/blob/rawhide/policy/modules/contrib/firewalld.te): 조회한 blob `cc10ae1917b0e93fe3a829ad8712dc13eb6c6a14`. 표준 역할별 타입의 근거이며 대상 VM의 설치 정책과 동일하다고 가정하지 않는다.
- [Fedora SELinux D-Bus 정책](https://github.com/fedora-selinux/selinux-policy/blob/rawhide/policy/modules/contrib/dbus.te): 조회한 blob `52738b63412db0c47b64fa2c8b1f4e0ab273e8c9`.
- [libselinux 공개 API](https://github.com/SELinuxProject/selinux/blob/main/libselinux/include/selinux/selinux.h): security_compute_create_raw, security_compute_av_flags_raw, av_decision 구조와 permissive flag.
- [systemd v257 exec 명세](https://github.com/systemd/systemd/blob/v257/man/systemd.exec.xml): BindPaths는 unit 전용의 쓰기 가능한 bind mount를 만들며 private mount namespace를 사용한다.
- [firewalld v2.4.3 FileLog](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/logger.py): 로그 파일 open/fchmod 동작을 확인해 사전 생성 파일 쓰기 설계에 반영했다.
