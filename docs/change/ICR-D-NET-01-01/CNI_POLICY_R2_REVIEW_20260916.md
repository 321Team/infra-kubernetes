# CNI inspect KU5482 후속 — 정책 검사 정확성 보완

기준 PR #11 HEAD: `d9ec23354960a6a792058d40c3b79453024f08d7`.
근거 수집 화면: 2026-09-16 12:34 KST. 첨부 검사: 12:35 KST 이후.

## 실제 증거와 판정

`FDS_CNI_DaemonFix_Inspect_KU5482.tar.gz`의 외부 SHA256은
`8afd7c5f4f8f445424e1aebdca7e46f4e9e92fc441f820d5031512d49aa3eace`이며 sidecar 및 내부 파일 3개와 일치한다.
`inspect-exit-code.txt=1`, `REVIEW_SCOPE.json`의 `runtime_started=false`, `execution_approved=false`다.
완성된 INSPECT/decision은 없으며 시험은 시작하지 않았다.

실패는 `scripts/run-cni-kernel-integration.py`의 SELinuxPolicy.check/require에서
`firewall -> firewalld_var_log_t:file open append write getattr setattr` 요구가 충족되지 않아 발생했다.
**기존 실패 로그에는 실제로 거부된 개별 권한과 API 반환값이 없다.** 따라서 VM에서 write만 거부됐다고 확정할 수 없다.
원래 normal 실패(e97d5bbe64efd9cb)의 AVC와 이번 정책 조회 실패는 별도 사건이다.

## 코드 결함과 수정

1. firewalld v2.4.3의 `src/firewalld.in`은 `FileLog(..., "a")`를 사용한다. `FileLog.open()`은
   `O_CREAT|O_WRONLY|O_APPEND`로 열고 `fchmod(0640)`한다. 실행기는 로그를 미리 생성하므로
   preflight 필수값은 `open append getattr setattr`다. 불필요한 일반 `write` 요구만 제거했다.
   fchmod에 필요한 `setattr` 및 실제 append 거부는 계속 차단한다. 정책 허용 모듈은 추가하지 않는다.
2. `unix_stream_socket:connectto`의 대상은 D-Bus peer다. 소켓의 일반 create/connect/read/write 등은
   firewalld 자기 소켓을 대상으로 검사한다. 이전 코드는 peer의 read/write까지 요구했다.
   이는 같은 검사 목록에서 발견한 코드 결함이며, KU5482에서 이 검사까지 실행된 것은 아니다.
3. `access()`가 권한 이름별 거부 목록, source/target context, class, API rc/errno, mask,
   domain permissive flag를 반환한다. API 실패는 권한 거부로 오표기하지 않는다.
4. 모든 선언된 접근과 기존 부모 경로 접근을 조회한 뒤 판정한다. 실패 시
   `SELINUX_POLICY_REPORT=<JSON>`을 로그에 남기고 non-zero로 중단한다.
   Enforcing 변경, domain Permissive, API/unknown permission 오류도 PASS가 될 수 없다.
   이 보고서는 정책 조회 결과이며 실제 daemon/packet 성공 증적이 아니다.

공통 core·운영 node·기존 node-firewall 소스 3개와 unit/namespace/runtime 변경 절차는 이전 제출과 동일하다.
기존 디렉터리 relabel, SELinux/방화벽 해제, RPM 설치, 새 start를 수행하지 않았다.

## 재검증과 잔여

- STATIC: Python AST, PLAN_ONLY, source 4개 checksum PASS.
- MOCK: 기존 202 + 새 7 = **209/209 PASS**. append 전용 허용, append/setattr 거부 유지,
  peer/self socket 구분, 다중 거부 전수 수집, API 오류/Permissive/권한 이름 오류 및 Enforcing 변경을 검증.
- 대상 VM의 수정된 inspect, daemon Runtime, 통신·회수는 미실행이다. 수정 전 MOCK 통과가
  대상 정책 호환성을 증명하지 못했던 한계를 유지한다. 새 테스트 역시 합성 정책 모델이다.
- 새 HEAD 독립 리뷰·새 inspect·v2 입력 대조·추가 normal 1회 실행 결정이 필요하다.
- 기존 1회 승인은 사용 완료. CP1 인수·CP2 변경·AC credit·병합 상태를 올리지 않는다.
- TimeoutStopSec/self-cleanup 경합 및 다른 시나리오는 별도 잔여다.

다음 제출물은 새 패키지의 inspect archive/sidecar다. 실패하더라도 모든 조회 결과와
소스/수집 메타데이터를 검토한다. 이전 KU5482 원본 및 기존 job은 보존한다.

## 공식 소스 근거

- [firewalld v2.4.3 daemon](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewalld.in)
- [firewalld v2.4.3 FileLog](https://github.com/firewalld/firewalld/blob/v2.4.3/src/firewall/core/logger.py)
- [Fedora firewalld 정책](https://github.com/fedora-selinux/selinux-policy/blob/rawhide/policy/modules/contrib/firewalld.te)
- [Fedora 권한 집합](https://github.com/fedora-selinux/selinux-policy/blob/rawhide/policy/support/obj_perm_sets.spt)
- [D-Bus client 정책](https://github.com/fedora-selinux/selinux-policy/blob/rawhide/policy/modules/contrib/dbus.if)
- [stream_connect_pattern](https://github.com/fedora-selinux/selinux-policy/blob/rawhide/policy/support/ipc_patterns.spt)

Fedora upstream은 설계 근거이며 대상 VM의 설치 정책과 동일하다고 가정하지 않는다.
