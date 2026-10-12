# IF06B 실행 통합 — 실제 HTTPS·독립 만료 회수 감독·인수 기록

CLAUDE_ASSISTED · 이권욱 · 2026-10-12 KST. `P1-MON-01` / [baseline#17](https://github.com/321Team/fds-project-baseline/issues/17). PR81~83의 판단·journal·coordinator를 실제 실행으로 잇는 묶음이다. [README](README.md)의 계약과 [canary 카드](../../docs/change/P1-MON-01/P1_MON_01_IF06B_WORKER03_CANARY_20261002.md), MON [잔여 시험 입력](https://github.com/321Team/infra-monitoring/blob/0dd0235b07d44ba7965490e3808e0a67a3bd903b/docs/IF06B_REMAINING_TEST_INPUTS_20261009.md)을 바꾸지 않는다.

**상태: STATIC·MOCK·CI까지.** 실제 클러스터·control01·credential에 대한 실행은 0건이다. REVIEW/MERGE/RUNTIME/AC는 각각 별도이며 이 문서나 병합이 정책 생성·삭제, 서버 파일 배치, 자동 회수 실행을 허용하지 않는다.

## 1. 구성

| 파일 | 역할 | 하지 않는 것 |
|---|---|---|
| `transport.py` | coordinator용 실제 HTTPS 요청 1회. IP literal 접속·고정 CA·검토한 이름 검증·절대 기한 | 재시도·redirect 추종·DNS 조회·시스템 신뢰 저장소 사용 |
| `supervisor.py` | 한 회차 감독 루프(생성1 → 만료/trigger 대기 → 조건부 DELETE1 → readback), journal과 분리된 결과 기록, 읽기 전용 watcher, 비영속 preflight | 재개·소유 채택·DELETE 재전송·주기적 폴링 |
| `guardian.py` | CLI: `validate`·`preflight`·`run`·`watch`·`survival-check` | 기본 endpoint/경로/예산/credential 제공, 디렉터리 생성·권한 변경, 서비스 설치 |
| `coordinator.py` | (최소 변경) `*_NOT_SENT` 인수의 하위 원인을 **반환값**에만 추가 | journal schema·REASONS 확장 |

`decision.py`·`journal.py`·정책 YAML은 변경하지 않았다. 요청은 고정 집합만 허용한다. 회차: `POST` 컬렉션, `GET`/`DELETE` 정확 이름, `GET kube-system`. preflight: `GET` 2종, `POST ?dryRun=All`, `POST SelfSubjectAccessReview`.

## 2. 계약 → 구현 → 시험

시험은 모두 SYNTHETIC이다. `LOOPBACK`은 127.0.0.1의 가짜 TLS 서버와 회차마다 만들어 버리는 시험용 PKI에 대한 실제 소켓·TLS·프로세스 시험, `MOCK`은 합성 transport + 실제 Linux journal 시험이다.

| 기존 계약 / 요구 | 구현 | 정상 시험 | 실패 시험 |
|---|---|---|---|
| 검토한 endpoint·TLS 인증, 인증서 검증 유지 (README transport 계약, 어댑터 1) | `ApiTransport`: IP literal만 접속, `CERT_REQUIRED`+hostname 검증 context만 수용, CA 파일 SHA-256 고정, 시스템 CA 미사용 | `test_get_is_one_request…`, `test_dns_san_is_verified…` (LOOPBACK) | 다른 CA·다른 이름 → HTTP 요청 0건 `test_untrusted_ca_or_wrong_name…`; 이름 접속·검증 없는 context 거부 `test_endpoint_must_be_an_ip_literal…`; CA 고정 불일치 `test_ca_pin_must_match…` |
| 기존 제한 자격증명 소비, 비밀값 비출력 (어댑터 2) | `build()`: bearer token 파일 또는 client cert/key 파일. 절대경로·regular·symlink 거부·타인 쓰기/읽기 금지, 검사한 fd로 로드. 오류·기록은 고정 코드만 | `test_client_certificate_authenticates`, `test_post_sends_canonical_body_and_bearer_token_once` | `test_credential_files_must_be_private…`, `test_missing_client_certificate…`; 증적·journal에 token 없음 `test_foreground_session_reclaims…` |
| DNS~응답 절대 기한, 응답 크기 제한 | 연결·handshake·송신·수신마다 남은 시간만 부여, DNS 단계 없음, 본문 1MiB·헤더 64KiB 상한 | `test_chunked_response_is_decoded` | `test_stalled_response_stops_at_the_absolute_deadline`, `test_slow_drip_cannot_extend…`, `test_expired_or_invalid_deadline_does_not_connect`, `test_oversized_or_truncated_body…` |
| redirect 0·자동 쓰기 재전송 0 | 요청당 연결 1개, 3xx는 상태 그대로 반환, 고정 요청 집합 밖 거부 | — | `test_redirect_is_returned_and_never_followed`, `test_server_error_is_not_retried`, `test_request_outside_the_fixed_set…`, `test_lost_delete_response_is_not_resent` (LOOPBACK) |
| cluster·관측 출처 결속, 후보 명시적 NotFound (어댑터 1, 카드 4절 3) | `check_target`: kube-system UID가 검토값과 같고 canary가 명시적 NotFound일 때만 journal 생성. binding은 요청마다 boot_id·pid/시작시각 재확인 | `test_start_gate_requires…` | `CLUSTER_CHANGED`·`CANARY_ALREADY_PRESENT`·`CANARY_ABSENCE_UNVERIFIED`, `test_nothing_is_created_for_an_unverified_or_unreachable_cluster` |
| 기본값 적용 후 spec pin 대조, create/get/delete 권한 확인 (어댑터 1·2, 카드 4절 3 dry-run) | `preflight`: SelfSubjectAccessReview 3건 + 서버 dry-run 응답 spec 해시 비교. 영속 쓰기 0. get/delete는 정확 이름으로, create는 이름 없이 컬렉션 단위로 묻는다(RBAC가 최상위 create를 이름으로 제한하지 못하므로) | `test_preflight_persists_nothing…`, `test_preflight_checks_access…` (LOOPBACK) | 권한 거부·해시 불일치 FAIL, 미검증 cluster에는 POST 0건 |
| 생성 최대 1회, timeout 뒤 채택·재생성 금지 (README 호출 순서 2) | `guard`: `Coordinator.create()` 1회. 사용된 회차 디렉터리 재시작 불가 | `test_lease_expiry_reclaims_once…` | `test_conflict_or_lost_create…`, `test_used_session_directory_is_never_restarted`, `test_killed_guardian…`(재실행 POST 0) |
| **관측 프로세스가 종료돼도 만료 회수** (MON 3절 5, 인계 2) | `guardian run`: 이중 fork·setsid로 시작 세션을 떠난 단일 프로세스가 만료를 기다려 회수. SIGHUP 무시 | `test_detached_guardian_reclaims_after_its_launcher_exited`, `test_survival_check…` (LOOPBACK 프로세스) | `test_operator_signal…hangup_is_ignored` |
| 조기 중단(abort)·완료 trigger. 신호가 대기·회수를 멈추게 해서는 안 됨 | SIGUSR1/TERM/INT=abort, SIGUSR2=complete. handler는 잠금 없이 첫 trigger만 기록하고 nonblocking pipe에 1바이트를 쓰며, 대기는 그 pipe를 `select`한다. handler 밖으로 예외를 내보내지 않는다. 대기 실패도 abort로 회수 | `test_operator_triggers_reclaim_before_expiry`, `test_operator_signal_reclaims_early…`, `test_signal_at_every_line_of_wait_returns_its_trigger`, `test_timeout_returns_nothing_and_a_late_signal_is_still_seen` | `test_probe_detects_the_old_event_based_deadlock`(이전 구현은 같은 주입에서 멈춤), `test_second_signal_inside_the_handler_cannot_block_or_raise`, `test_signal_storm_keeps_the_first_trigger_and_close_releases_cleanly`, `test_lost_descriptors_never_raise…`, `test_repeated_signals_through_reclaim_and_exit_delete_exactly_once`, `test_signals_racing_the_lease_expiry_delete_exactly_once`, `test_broken_wait_reclaims…`, `test_early_wakeup_does_not_read_or_delete_before_expiry` |
| UID+최신 RV 조건부 DELETE 1회, 409/403에 force·이름삭제 금지 (카드 5절 4) | coordinator 그대로. 감독은 재전송 경로 없음 | `test_changed_resource_version_is_the_delete_precondition` | `test_forbidden_or_conflicting_delete_is_handoff_without_resend`, `test_forbidden_delete_ends_in_recorded_handoff`(LOOPBACK), `test_foreign_uid_or_drift_never_deletes` |
| DELETE ACK ≠ 부재, 명시적 NotFound readback만 ABSENT | ACK 뒤 `readback_delay` 후 GET 1회. 결과는 항상 `baseline_verified=false` | `test_lease_expiry…`, `test_explicit_not_found_before_delete…` | `test_delete_ack_is_not_absence`, `test_lost_delete_response_is_uncertain_and_terminal` |
| 세대 변경·기한 경과 시 미전송 (어댑터 4) | coordinator 그대로 + 하위 원인 반환 | — | `test_not_sent_cause_separates_generation_from_time`, `test_generation_change_after_intent_reports_delete_not_sent_cause` |
| journal에 못 남은 반환값을 독립 보존. 증적 완전성을 회수 결과와 따로 보고 (README, PR83 비차단) | `Evidence`: journal과 다른 보호 디렉터리의 append-only `supervisor.jsonl`. 기록 실패가 회수를 막지 않음. 저장되는 행은 자기 자신의 write·fsync 성공을 선언하지 않는다: FINAL 행에는 `evidence_complete_before_write`만 저장하고, 이번 쓰기 결과까지 반영한 `evidence_complete`는 반환값과 stream 출력에만 넣는다 | `test_foreground_session…`(stream = 저장 행 + `evidence_complete`), `test_lease_expiry…` | `test_final_row_cannot_vouch_for_its_own_failed_write`(FINAL의 write 오류·짧은 쓰기·fsync 오류 각각), `test_failure_before_final_is_carried_into_the_final_row`, `test_unrecorded_handoff_is_preserved_outside_the_journal`, `test_evidence_failure_does_not_stop_the_reclaim`, `test_evidence_must_not_share_the_journal_directory` |
| 감독 실패 시 인수, 자동 재생 금지 (README 호출 순서 5) | `watch`: 읽기 전용. writer 잠금이 풀렸는데 terminal 기록이 없으면 `WRITER_GONE_WITHOUT_TERMINAL` | `test_watch_follows_a_live_session_to_its_end` | `test_killed_guardian_leaves_a_handoff_and_is_never_replayed`, `test_watch_reports_a_writer_that_left…` |
| 시간·예산 기본값 없음 (README, 인계 5) | 입력 파일 5개 예산 모두 필수, 정합성만 검사 | `test_validate_reads_local_inputs_and_sends_nothing` | `test_budgets_must_fit_and_have_no_defaults`, `test_input_file_is_strict_and_has_no_default_budget` |

## 3. PR83 비차단 리뷰 3건의 처리

| 의견 | 판단 | 구현 |
|---|---|---|
| journal 저장 실패로 남지 못한 반환값 | **구현.** README가 감독 측 의무로 둔 항목 | 모든 단계 결과와 `terminal_recorded`를 별도 디렉터리에 fsync. 그 기록도 실패하면 반환값·stream의 `evidence_complete=false`로 표시하고 회수는 계속 |
| `*_NOT_SENT` 하위 원인 | **구현, 반환값 한정.** 인수자가 "세대가 바뀌었나 / 시간만 지났나"를 구분해야 함 | `not_sent_cause` ∈ `GENERATION_CHANGED`·`CLOCK_INVALID`·`REQUEST_WINDOW_EXPIRED`·`ADAPTER_FAILURE`. journal reason·schema는 그대로이므로 옛 판독기 영향 없음 |
| 늦은 201의 UID/RV | **구현, 진단 전용.** 채택·조회·삭제에 쓰지 않음 | 감독의 통과 관측(`_Tap`)이 create 응답의 상태·UID·RV만 `unowned_create_response`(`DIAGNOSTIC_ONLY_NOT_OWNERSHIP`)로 남김. coordinator는 이 값을 보지 못함. spec drift인 201에도 같이 적용 |

삭제 전 일시 읽기 실패를 WAIT로 완화하는 결정은 하지 않았다(현행 HANDOFF 유지). 그래서 감독은 대기 중 객체를 주기적으로 읽지 않고 trigger 시점에 GET을 1회만 보낸다.

## 4. 실행 구조와 부족한 사실

control01(`10.1.93.102`, EL10·systemd 257·`/usr/bin/python3.12`·Enforcing, 10/12 읽기 관측)의 기존 `svc_ansible`+`sudo` 경로에서 새 unit·RBAC 없이 쓸 수 있는 방식으로 구현했다. 다만 아래는 **관측되지 않은 사실**이므로 선택이 필요하다.

| 선택안 | 새로 설치되는 것 | 확인이 필요한 사실 | 비고 |
|---|---|---|---|
| A. `run` 기본(세션 이탈 프로세스) | 없음 | logind `KillUserProcesses` 실제값, 세션 종료 뒤 생존. `survival-check`로 정책·API 접촉 없이 확인 가능 | 권장. `svc_ansible` Linger=no는 관측됐고 생존은 미검증 |
| B. VMware 콘솔에서 `run --foreground` | 없음 | 콘솔 세션 유지·담당자 | SSH/관리망과 독립. A의 인수 경로로 병행 가능 |
| C. `systemd-run` transient unit 아래 `run --foreground` | root transient unit(파일 설치는 없음) | 별도 허용 | 가장 견고하나 새 systemd 단위이므로 임의로 쓰지 않음 |

어느 안이든 host 재부팅·전원 장애·SIGKILL에서는 감독이 회수하지 못한다. 그때 남는 것은 journal·증적과 인수이며, 재기동해도 이어서 실행하지 않는다. 독립된 두 번째 자동 회수기는 만들지 않았다.

## 5. 절차

아래는 인터페이스 설명이며 실행 승인이 아니다. `<...>`는 6절의 현장 입력이다. 모든 명령은 보호 디렉터리 소유자(예: 기존 sudo 경로의 root)로 실행한다.

```text
G=/usr/bin/python3.12\ -B\ <검토 commit의 공급 경로>/ops/p1-mon-if06b-cleanup/guardian.py
1. $G validate --config <입력.json>            # 오프라인. network_requests=0
2. $G survival-check --evidence-dir <별도 디렉터리> --seconds <n> --interval-seconds <n>
                                                # 로그아웃→재접속 뒤 survival.jsonl의 FINAL 확인(선택안 A일 때)
3. $G preflight --config <입력.json>           # 읽기2 + 접근검토3 + dry-run1, persisted_writes=0
4. $G run --config <입력.json>                 # ARMED 한 줄을 출력하고 launcher는 종료
5. $G watch --journal-dir <journal> --poll-seconds <n> --window-seconds <n>   # ARMED 뒤에 시작
```

- **실행:** `run`은 kube-system UID·canary 명시적 NotFound 확인 → CREATE_INTENT fsync → `ARMED` → POST 1회 순서다. `ARMED` 전의 실패는 `NOT_STARTED`(POST 0건)다. 임대는 arm 시각부터 센다.
- **중단/완료:** `kill -USR1 <pid>`(조기 회수), `kill -USR2 <pid>`(시험 완료 회수). pid는 `ARMING` 행 `guardian_epoch`의 앞부분이다. SIGTERM도 조기 회수다. SIGKILL은 회수 없이 인수로 간다.
- **회수:** 만료 또는 trigger에서 GET 1회 → UID·spec 일치 시 DELETE_INTENT fsync → UID+방금 읽은 RV 조건부 DELETE 1회 → `readback_delay` 뒤 GET 1회. 명시적 NotFound면 `ABSENT_BASELINE_UNVERIFIED`. 기존 HEP/GNP/firewalld/NAT 불변과 새 연결 기준 Deny 복귀, remote write·Target·heartbeat는 **이 도구가 확인하지 않는다**(카드 5절 4).
- **종료 코드:** 0=ABSENT 또는 검사 통과, 3=인수 필요, 4=시작 안 함, 5=검사 실패, 2=입력/도구 오류. 종료 코드는 **회수 결과만** 나타내며 증적 완전성은 담지 않는다.
- **증적:** `<journal>/journal.jsonl`·`writer.lock`, `<evidence>/supervisor.jsonl`을 지우거나 고치지 않고 보존한다. 고정 코드·식별자(UID/RV/세션)·시각만 들어 있고 token·응답 본문·예외 원문은 없다.
- **증적 완전성:** stream 출력과 반환값의 `evidence_complete`는 "이 행과 앞의 모든 행이 끝까지 쓰이고 fsync됐다"는 뜻이다. 파일에는 이 값을 저장하지 않는다. 파일의 FINAL 행에 있는 `evidence_complete_before_write`는 FINAL을 쓰기 **직전**까지의 상태다. 파일에 FINAL 행이 없거나, 마지막 줄이 잘렸거나, `evidence_complete_before_write=false`이면 증적은 불완전한 것으로 다루고 journal을 기준으로 인수한다. 분리 실행(`run` 기본)에서는 `ARMED` 뒤 stream이 닫히므로 파일로만 판단한다.

### 인수 (exit 3 / `handoff_required=true`)

자동 재시도는 없다. 인수자는 **현재 인증된 GET으로 대상·spec·UID·RV를 다시 확인**한 뒤 카드 5절 4의 조건부 raw DELETE를 판단한다. 409·403을 force나 이름만 쓴 삭제로 우회하지 않는다.

| 결과 | 뜻 | 먼저 볼 것 |
|---|---|---|
| `CREATE_NOT_SENT` / `DELETE_NOT_SENT` + `not_sent_cause` | 이 실행기가 요청을 보내지 않음. 객체 존재·부재의 증거는 아님 | 원인이 `GENERATION_CHANGED`면 host/cluster 재확인, 시간 원인이면 canary 잔존 여부 |
| 생성 단계 `TRANSPORT_UNCERTAIN`·`REQUEST_WINDOW_EXPIRED`·`CREATE_SPEC_DRIFT`·`CREATE_NOT_CONFIRMED` | 소유 미확정. 객체가 생겼을 수 있음 | `unowned_create_response`(있으면 진단 UID). 이름만으로 삭제 금지 |
| `API_READ_FAILED`·`OBSERVATION_STALE`·`CLEANUP_WINDOW_EXPIRED` | 삭제 전 판단 불가. canary가 남아 있을 가능성이 큼 | 즉시 수동 회수 경로 |
| `FOREIGN_UID`·`POLICY_DRIFT`·`UNEXPECTED_DELETION_OR_FINALIZERS` | 같은 이름이지만 이 회차 객체가 아니거나 변경됨 | 삭제하지 말고 출처 조사 |
| 삭제 단계 `TRANSPORT_UNCERTAIN`·`REQUEST_WINDOW_EXPIRED` | DELETE가 도달했을 수 있음 | GET으로 부재 확인 |
| `DELETE_ALREADY_CLAIMED_OR_OUTCOME_UNRESOLVED` | 403/409 또는 ACK 뒤에도 객체가 남음 | `phase`(`CONFLICT`·`DELETE_UNKNOWN`·`DELETE_ACK`)와 현재 RV |
| watch `WRITER_GONE_WITHOUT_TERMINAL` | 감독이 terminal 기록 없이 사라짐 | `last_event`·`uid`·`phase`. OWNED면 canary 잔존 |
| watch `WRITER_STILL_HOLDING_AFTER_WINDOW`·`HISTORY_UNREADABLE`·`NO_SESSION_FILES` | 감시 창 초과 / 기록 판독 불가 / 회차 파일 없음 | 감독 프로세스와 디렉터리 상태 |

## 6. 최소 현장 입력

입력 파일은 JSON 하나다. 키는 고정이며 빠지거나 남으면 거부한다.

```text
{"schema":1, "session_id":"<회차 식별자>", "expected_cluster_uid":"<kube-system UID>",
 "api":{"connect_ip":"<IP>", "port":<port>, "tls_server_name":"<인증서 SAN>", "ca_file":"<절대경로>", "ca_sha256":"<hex>"},
 "credential":{"kind":"bearer_token_file","token_file":"<절대경로>"}      또는 {"kind":"client_cert_files","cert_file":"…","key_file":"…"},
 "manifest_file":"<검토 manifest의 JSON 절대경로>", "reviewed_manifest_sha256":"<hex>", "reviewed_spec_sha256":"<hex>",
 "journal_dir":"<0700 디렉터리>", "evidence_dir":"<다른 0700 디렉터리>",
 "budgets":{"lease_seconds":…, "cleanup_window_seconds":…, "observation_max_age_seconds":…, "request_timeout_seconds":…, "readback_delay_seconds":…}}
```

**이미 받은 사실(재요청하지 않음, 실행 직전 재확인만):** control01 식별·OS·Python 경로, kube-system UID `e1669cff-1435-423f-a6c4-28bc44f488de`와 canary 이름의 CLI NotFound(10/12 11:23~11:33 읽기), 부모 후보 `/var/lib/fds-change`(root 0700·local XFS), MON 요구 수준은 gateway 구간 귀속(H04 회신), 검토 후보 manifest YAML(카드 동봉).

**미확정(이 값 없이는 실행할 수 없음):**

1. **API endpoint** — 접속 IP·포트, 검증할 인증서 이름, CA 파일 경로와 SHA-256.
2. **자격증명** — 이 회차가 쓸 기존 제한 자격증명. 관리 kubeconfig 읽기 성공은 create/delete 권한의 증거가 아니다. get/delete는 정확 이름으로 제한할 수 있으나, 최상위 create는 RBAC만으로 같은 이름 제한을 강제할 수 없다. 실제 자격증명에 부여된 권한과 실행기 내부의 요청 제한(고정 요청 집합)을 구분해 선택한다. 서버 측 생성 이름 제한이 필요하면 기존 admission 통제의 적용 가능성을 확인한다. 새 RBAC/admission 또는 관리 인증서 추출(credential 사본 생성)은 별도 변경 범위이며, 이 문서가 새 정책 구축을 요구하거나 허용하는 것은 아니다.
3. **두 보호 디렉터리** — journal용·증적용 고정 경로. 생성은 control01 파일 배치이므로 별도 범위다.
4. **실행 구조** — 4절 A/B/C와 `KillUserProcesses` 실제값.
5. **시간 예산** — 위 5개와 watch 2개. 카드의 120/180초는 제안값이며 채워 넣지 않았다. 제약은 `lease > request_timeout`, `cleanup_window > 3×request_timeout + readback_delay`뿐이다.
6. **manifest JSON과 해시** — YAML 후보와 내용이 같은 JSON, `validate`가 계산하는 manifest 해시의 검토, `preflight`가 보고하는 기본값 적용 후 spec 해시의 검토.
7. **인수·알림** — 회차의 회수 인수자(콘솔), HANDOFF 전달 경로, abort를 누가 어떤 MON 관측으로 보내는지. 원격 알림 전송(Alertmanager/Discord 등)은 경로가 정해지지 않아 **구현하지 않았다.** 현재 전달 수단은 `watch`의 출력·종료 코드와 증적 파일이다.
8. **공급·회차** — 검토 commit의 파일을 control01에 두는 방법과 해시 대조, 다른 K8S/네트워크/APP·Harbor 작업과 겹치지 않는 창.

## 7. 한계

- 가짜 서버는 Kubernetes가 아니다. 실제 apiserver의 CRD 기본값·dry-run 응답 형태·NotFound details·인증서 SAN은 `preflight`와 첫 실제 회차에서만 확인된다.
- 절대 기한은 이 프로세스가 기다리는 시간을 제한할 뿐, 이미 보낸 요청을 서버에서 취소하지 못한다. 그래서 기한 초과는 결과 불명으로 처리한다.
- fsync 순서는 시험했지만 실제 전원 장애 내구성, 부모 경로·ACL·SELinux label, 장치 보장은 현장 전제다.
- `watch`가 `ARMED` 전에 같은 순간 잠금을 읽으면 감독이 시작을 포기할 수 있다(POST 0건, 안전 측). `ARMED` 뒤에 시작한다.
- trigger가 `ARMED`와 POST 사이에 도착하면 생성 후 즉시 회수한다. 생성 자체를 취소하지는 않는다.
- 신호 시험은 대기 함수의 각 행과 handler 내부에 신호를 주입하는 자식 프로세스 probe와 loopback 프로세스 시험이다. 커널이 임의 시점에 전달하는 모든 경우를 열거한 것은 아니며, 실제 control01에서의 신호 전달은 실행 전 `survival-check`·첫 회차에서만 관측된다.
- FINAL 기록 뒤에는 신호를 무시한다. 그 뒤의 SIGTERM은 이미 끝난 회수에 영향을 주지 않는다.
- ABSENT는 이 canary의 부재만 뜻한다. 기준 정책 복귀·연결성·AC 판정은 MON/SEC의 별도 관측이다.
