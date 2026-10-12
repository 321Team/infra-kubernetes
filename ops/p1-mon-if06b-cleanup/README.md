# IF06B 조건부 회수 판단·영속 기록·요청 연결

CODEX_ASSISTED · 2026-10-12. `P1-MON-01` / [baseline#17](https://github.com/321Team/fds-project-baseline/issues/17) 및 K8S·SEC 협업 범위의 로컬 사전준비다. [기존 canary 카드](../../docs/change/P1-MON-01/P1_MON_01_IF06B_WORKER03_CANARY_20261002.md)의 이름·UID·최신 resourceVersion 조건을 판단 코드로 분리한다. 기존 정책 YAML이나 실행 허용은 변경하지 않는다.

## 완료한 범위

`decision.py`는 파일·네트워크·프로세스·현재 시계에 접근하지 않는 순수 함수 모듈이다. 확인된 생성 응답과 별도로 검토한 spec 해시를 받아 소유 영수증을 만들고, 신뢰된 관측에 대해 WAIT / DELETE_INTENT / ABSENT / HANDOFF를 반환한다. 배포 명령이나 API 실행 CLI는 없다.

- 정확 이름 `p1-mon-if06b-worker03-to-monitor01-canary`의 HTTP201 생성 응답만 소유 근거로 받는다. 생성 timeout 뒤 GET으로 발견한 이름을 이 회차 소유로 채택하지 않는다.
- session·cluster·host boot·guardian epoch가 같은지, 관측이 최신인지 확인한다. 시간은 해당 boot의 monotonic 값이며 생성·만료·회수 종료·관측 유효기간을 호출자가 모두 제공한다. 실제 값이나 기본 예산은 생성하지 않는다.
- UID와 검토한 spec이 같을 때만, 방금 확인한 불투명 문자열 resourceVersion과 UID를 모두 가진 DeleteOptions를 만든다. UID 변경·spec 변경·finalizer·이미 삭제 중인 객체는 HANDOFF다.
- intent를 저장한 후에는 timeout·409·응답 유실 때 DELETE를 재발행하지 않는다. HTTP200/202도 회수 완료가 아니다. 정확 API 대상의 명시적인 NotFound readback만 ABSENT다. 403·빈 응답·잘못된 404는 성공으로 취급하지 않는다.
- ABSENT는 해당 canary의 부재만 뜻한다. 전체 정책·연결성·기준 복귀 또는 AC 성공은 별도 관측이 필요하다.

Kubernetes의 [DeleteOptions](https://kubernetes.io/docs/reference/kubernetes-api/definitions/delete-options-v1-meta/)는 UID/resourceVersion precondition 불일치를409로 거부한다. GET 이후 객체가 바뀌어도 조건 없는 삭제나 이름만으로 된 fallback을 만들지 않는다.

## 영속 기록과 단일 작성자

`journal.py`는 Linux 로컬 파일시스템용 라이브러리다. PR81의 `decision.py`는 변경하지 않는다. CREATE 직전 의도, 확인된 소유 영수증, DELETE 의도, 응답, 명시적 부재 또는 인수 전환을 순서대로 보존한다. 네트워크 호출·credential·실행 CLI·서비스 설치는 없다.

- 호출자는 기존 권한으로 검토한 **cluster/canary별 고정된 보호 디렉터리**의 열린 fd를 공급한다. 유효 UID 소유/0700을 확인하며 디렉터리 생성·chmod·chown은 하지 않는다. 전체 부모 경로·실효 ACL·SELinux·신뢰된 공급 및 durable local storage의 검증은 어댑터/현장 전제다. 임의 임시 경로·NFS·다른 호스트의 사본을 운영 journal로 쓰지 않는다.
- 고정 `writer.lock`을 `flock(LOCK_EX|LOCK_NB)`로 유지한다. 파일은 현재 UID/0600·regular·단일 link·경로/inode 일치를 확인하며 symlink를 따라가지 않는다. 다른 프로세스·동시 스레드·fork 상속 writer를 거부한다. 이 잠금은 해당 로컬 디렉터리의 협조적 작성자 보호이며 다른 호스트까지 잠그는 분산 잠금이나 악의적인 동일 UID/root 방어가 아니다.
- 고정 `journal.jsonl`은 `O_EXCL`로 한 번만 만들고 append한다. CREATE 의도 또는 DELETE 의도의 파일 **fsync → 디렉터리 fsync**가 모두 반환된 뒤에만 호출자에게 정상 반환한다. 구현 의미는 Python의 [fsync](https://docs.python.org/3.12/library/os.html#os.fsync), [flock](https://docs.python.org/3.12/library/fcntl.html#fcntl.flock)를 따른다. 실제 전원 장애 내구성·파일시스템/장치 보장까지 CI로 입증한 것은 아니다.
- 저장·동기화·검증 실패는 해당 인스턴스를 더 이상 사용하지 못하게 한다. 원본을 지우거나 수정·재시도하지 않는다. 프로세스가 죽거나 닫힌 뒤 기존 파일을 다시 쓰는 API도 없다. 유효한 prefix·빈 파일·부분 기록 모두 재기동/재생성의 허용이 아니다.
- `inspect_history()`는 writer가 없는 상태에서 공유 잠금으로 읽고 순서·schema·세대/소유 연결을 대조한다. 반환은 언제나 `automatic_replay_allowed=false`다. ACK/ABSENT도 실제 기준 복귀·인수 성공을 뜻하지 않는다. append-only 기록은 암호학적 인증 증거가 아니며 과거 파일의 진본성은 별도 보존/출처 검증이 필요하다.
- 런타임 예산 기본값은 없다. 64KiB/행·1MiB/파일 상한은 잘못된 파일의 무제한 파싱을 막는 구현 한계이며 실행 시간/회수 예산과 무관하다. 로그에는 검증된 receipt와 조건부 요청의 최소 필드만 저장하고 전체 API 응답·예외 원문·환경·credential은 저장하지 않는다.

어댑터가 지켜야 할 호출 순서는 아래와 같다. 실제 경로·대상·예산이 없는 인터페이스 설명이며 실행 명령/승인된 package가 아니다.

1. 단일 실행 위치와 고정 디렉터리·미사용 회차를 확인하고 `Session(directory_fd, binding=..., reviewed_spec_sha256=...)` 생성. 성공 반환 전에 CREATE를 전송하지 않는다.
2. 별도 어댑터가 검토된 CREATE를 최대1회 전송한다. 정확 HTTP201 응답은 `record_owned(**create_inputs)`로 기록한다. 생성 결과 불명/기록 실패면 인수 전환하며 발견한 동일 이름을 소유로 채택하거나 재생성하지 않는다.
3. 인증된 최신 관측·현재 세대·시간을 받아 `plan_delete(...)` 호출. 반환이 DELETE_INTENT일 때만 외부 어댑터가 전송 직전 세대·기한을 다시 확인하고 해당 요청을 최대1회 전송한다. 함수 반환과 실제 전송 사이 중단/시간 경과를 이 라이브러리가 관측할 수는 없다.
4. 응답은 `record_delete_result(status)`로 기록. 응답 유실은 `None`, DELETE404도 UNKNOWN이다. 명시적 NotFound GET을 이후 `plan_delete(...)`에 공급하면 ABSENT를 별도 기록할 수 있다. ACK 후 GET200은 현행 판단대로 HANDOFF이며 DELETE를 재전송하지 않는다.
5. 종료/실패 시 파일·잠금 파일을 보존한다. `close()`는 fd만 닫고 파일은 제거하지 않는다. 재시작은 `inspect_history()`로 읽어 인수할 뿐이며 새 디렉터리/파일명·claim 삭제로 불확실한 회차를 우회하지 않는다.

## 통합 전 남은 작업

### 생성·회수 요청과 기록의 연결

`coordinator.py`는 미사용 journal을 가진 한 회차에서 `create()`와 `cleanup(trigger=...)`를
기록 모듈에 연결한다. **HTTP 클라이언트·credential 공급·독립 감독/타이머·알림 전송은 포함하지 않는다.**
호출자가 제공한 transport가 실제 요청을 수행하는 인터페이스이며 CLI나 현장 실행 번들이 아니다.

- 생성자에서 대상 이름/API 종류·제출 manifest의 검토 해시·명시적 시간 입력을 확인한 후
  CREATE_INTENT를 영속화한다. 생성 요청은 최대1회이며 timeout 뒤 GET으로 소유권을 채택하지 않는다.
  제출 manifest 해시와 CRD defaulting 후 응답 spec의 검토 해시는 별개 입력이다. 어느 해시도 자동 학습하지 않는다.
- 현재 binding/monotonic clock을 요청 전후 및 DELETE 의도 기록 뒤 다시 확인한다. 생성·회수 종료·관측 유효기간·요청 timeout에는 기본값이 없다.
  GET 시작 시각을 관측 시각으로 사용하므로 느린 응답을 새 관측처럼 취급하지 않는다.
- transport 진입 전 로컬 준비/세대·시계·기한 검사가 실패하면 CREATE_NOT_SENT 또는
  DELETE_NOT_SENT로 바로 HANDOFF한다. 이때 DELETE_RESULT(None)을 남기지 않는다.
  이 사유는 **본 실행기가 transport를 호출하지 않았음**만 뜻하며 객체의 현재 존재/부재를 보장하지 않는다.
  진입 뒤 예외·지연은 결과 불명으로 구분한다. transport가 NOT_SENT 예외를 던져도 결과 불명으로 처리한다.
- `plan_delete()`의 fsync 성공 후에만 정확 이름의 UID/RV 조건부 DELETE를 한 번 전달한다.
  DELETE 응답은 결과 기록일 뿐이며, 다음 명시적 NotFound GET 전에는 READBACK_REQUIRED다.
  ACK 뒤 객체가 남거나 DELETE 유실/409인 경우 자동 DELETE 재전송은 없다.
- transport는 검토한 endpoint/TLS/기존 제한 권한을 인증하고, **DNS·연결·쓰기·읽기 전체의 절대 기한**과
  재시도0·redirect0을 보장해야 한다. 이 모듈이 임의 Python callback을 강제 종료하거나
  이미 보낸 요청을 취소할 수는 없다. 종료 후 관측된 시간 초과는 HANDOFF로 처리한다.
  이 계약을 만족하는 실제 transport/감독 구현·시험이 없으면 현장에 사용할 수 없다.
- HTTP status와 제한된 JSON bytes를 별도로 받는다. 중복 key·잘못된 JSON/encoding·비유한 수·크기 초과는
  고정 오류로 중단한다. credential·API 응답/예외 원문은 journal에 넣지 않는다.
- JournalError와 OSError 모두 추가 요청을 중단한다. 저장 실패로 HANDOFF 자체가 기록되지 못하면
  `handoff_recorded=false`를 반환하고 기존/부분 파일을 보존한다. 인수 알림이나 AC 성공은 뜻하지 않는다.
  감독 측은 이 반환값도 journal과 독립적으로 보존/전달해야 한다. 고정 허용 목록 밖 저장 오류는
  ADAPTER_FAILURE로 반환될 수 있으므로 journal 파일만으로 모든 실패가 기록됐다고 판단하지 않는다.
- fork·동시 호출·종료된 회차를 거부한다. `close()`는 파일을 지우거나 canary를 삭제하지 않으므로,
  호출자는 실제 감독/인수 경로 없이 단순 종료를 정상 회수로 취급하면 안 된다.

PR82 비차단 리뷰의 인수 진단을 보완했다. 새 HANDOFF/ABSENT에는 제한된 reason·관측 monotonic 시각·HTTP status를 기록한다.
`inspect_history()`는 PR82의 빈 terminal payload도 그대로 읽되 `terminal_diagnostic=null`로 사유 부재를 나타낸다.
새 기록은 옛 PR82 reader가 거부할 수 있으므로 생산자/판독기를 같은 검토 버전으로 공급한다.
이전 원본을 바꾸거나 새 schema로 다시 쓰지 않으며 유효 기록도 자동 재생하지 않는다.

PR82의 나머지 통합 메모도 유지한다. **pre-intent GET 실패/오래된 관측은 현행 HANDOFF 그대로**이며
WAIT/재조회로 바꾸는 결정은 하지 않았다. HANDOFF 전달 경로·회차 인수자와 MON abort 관측
출처/주기/지연은 실제 통합 전에 정할 입력이다. OSError/FileExistsError의 원래 예외 종류는 보존하고
연결 코드가 두 종류 모두 안전 중단으로 다룬다.

기한 뒤 도착한201 응답의 UID/RV를 진단 전용으로 보존하는 비차단 제안은 후속 어댑터/감독 통합에
남긴다. 이번 수정은 늦은 응답을 소유로 채택하거나 기록 schema를 추가 확장하지 않는다.
진단 UID가 없다는 이유로 이름만 사용한 무조건 삭제를 허용하지 않는다. 수동 인수도 현재 인증된
조회·대상/spec/UID/RV 대조와 해당 회수 범위를 확인해야 하며, 자동 재생/소유 채택과 구분한다.

**이 모듈만으로 독립 만료 회수가 동작하지 않는다.** MON의 `docs/IF06B_REMAINING_TEST_INPUTS_20261009.md`에 남은 구현·독립 생존 검증 조건은 계속 미완료다.

미구현 어댑터는 다음 계약을 만족해야 한다. 이것은 새 팀 정책/권한이나 실행 승인 요청이 아니라 이 코드의 명시적인 신뢰 전제다.

1. 실제 검토한 manifest·API endpoint·현재 cluster와 관측의 출처를 결속한다. 호출자가 준 해시는 승인 증거를 스스로 만들지 않는다. API 응답의 기본값 적용 뒤 spec pin과 비교할 방법도 실제 서버에서 대조한다.
2. 시험 프로세스와 독립적으로 생존하는 실행 위치·프로세스 감독·연결 경로를 확정하고, 제한된 기존 자격증명으로 실제 create/get/delete 권한을 확인한다. credential을 산출물에 저장하지 않는다.
3. 생성 직전부터 단일 작성자와 보호된 durable journal을 유지한다. 로컬 기록 라이브러리는 구현했지만 실제 실행 위치/고정 저장 경로·장치·권한·감독 통합은 미완료다. 생성 응답 전 유실, 생성 성공 뒤 영수증 저장 실패, intent 저장 뒤 전송 여부 미상은 자동 해결하지 않는다. 원본을 보존하고 회수 인수로 전환한다.
4. `DELETE_INTENT.next_receipt`를 영속화하고 성공을 확인한 뒤에만 한 번 전송한다. 저장 실패·부팅/epoch 변화·기한 경과면 전송하지 않는다. API 요청 timeout과 readback 예산도 현장 값으로 제한한다.
5. ACK 뒤에도 읽기만으로 명시적인 부재를 확인하고, 기존 기준 정책·허용/차단 연결성을 따로 확인한다. 모호한 결과를 강제 삭제·finalizer 제거·무조건 재시도로 해결하지 않는다.

PR81 비차단 리뷰 메모도 실제 통합에 연결한다. MON Lead와 abort 관측의 출처·주기·지연을 정하고, ABSENT 뒤 remote write/Target/heartbeat 정상과 HEP/GNP/firewalld/NAT 불변을 확인한다. 실제 CRD defaulting/spec 비교·NotFound details 및 ACK 후 제한된 읽기 재조회 간격/기한은 현장 입력과 검토가 필요하다. 삭제 재전송은 허용하지 않는다.

따라서 자동 회수·IF06B 실행·MON/SEC 인수는 HOLD다. 실제 관리 환경·관측 지점·권한·회수 예산과 입력 검토 후 어댑터/통합 시험이 필요하다. 기존 카드의 canary 최대180초·관측120초 제안은 이 모듈의 승인 기본값이 아니다.

## 검증

저장소 루트에서 Python3.10+로 다음 대상만 실행한다.

```text
python3 -B -m unittest discover -s ops/p1-mon-if06b-cleanup/tests -v
```

모든 객체·시간·UID는 SYNTHETIC이다. 판단 시험은 정상 만료와 조기 중단, stale/future 관측, 잘못된 입력, boot/epoch/cluster/session 변경, 다른 UID/spec,403/409/유실, finalizer, ACK 후 readback을 검증한다. 추가 journal 시험은 임시 디렉터리에서 실제 Linux 파일·프로세스 잠금/종료와 fsync 순서, 주입한 짧은 쓰기·저장 실패·손상·세대 변경·재전송 금지를 검증한다. 클러스터·소켓·API 호출은 없다. Windows에서는 Linux 전용 journal 시험이 SKIP되므로 전체 성공으로 세지 않는다. Linux CI는 이 범위만 실행하며 Runtime 검증·독립 REVIEW·MERGE·AC와 구분한다.

실서버 적용은 없다. 코드가 이상하면 사용을 보류하고 diff/시험 결과를 검토한다. 이미 생성된 실제 canary나 원본 영수증이 있을 때 이 모듈을 임의로 끼워 넣어 회수를 재개하지 않는다.
