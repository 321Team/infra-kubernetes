# CP1 최소 CNI 변경 — 독립 리뷰용 실행 계약

## 신규 v4 CP 세션 제안

[CP 세션 정책](../change/ICR-D-NET-01-01/CP_SESSION_REVIEW_20260918.md)은 소스와 CP1→CP2→CP3 실행 위임을 한 번에 검토하고, 별도의 결과 리뷰 대기 회수 시각 없이 실제 유인 종료시각까지 한 세션으로 연결한다. [일괄 실행 도구](CP_SESSION.md)가 확인·검사·증적 취합을 처리한다. 새 독립 정책 승인 후 **신규 v4 Job에만** 적용되며 아래 v1/v2/v3 및 실행 중인 Job의 계약을 소급 변경하지 않는다.

## 신규 v3 운영자 예외 제안

담당자 부재 시 개별 Job의 독립 인수를 대기로 남긴 채 **CP1만 제한적으로 유지**하는 새 권한을 추가한다. [정책·권한·검증 범위](../change/ICR-D-NET-01-01/CP1_OPERATOR_EXCEPTION_REVIEW_20260918.md). 새 정책/소스의 독립 승인과 v3 authorization의 명시적 위임이 선행되어야 하며 기존 v1/v2 Job에는 소급 적용하지 않는다.

`retain-by-exception --job ID --exception FILE --exception-sha256 SHA --trial ARCHIVE --trial-sha256 SHA --regression FILE --regression-sha256 SHA`는 기존 실제 5-flow·HOST/외부 결과와 GWONUK의 정확한 Job 결정을 연결한다. 예외 상태는 `PROVISIONAL_RETAINED_REVIEW_REQUIRED`, `cp1_accepted=false`, 독립 인수 `PENDING`이다. 현재 이미 예약된 최종 회수 시각을 바꾸지 않는다. 정상 인수 및 CP2/CP3 승인으로 사용하지 않는다.

입력 양식은 [신규 실행 승인](../../policies/icr-d-net-01-01/cp1-operator-window-authorization.template.json), [운영자 예외 결정](../../policies/icr-d-net-01-01/cp1-operator-exception.template.json)이다. 승인 값은 기본적으로 비어 있거나 false이다. 실제 사후 리뷰는 기록으로 보존하되, 첫 deadline 뒤의 승인을 앞당겨 쓰거나 만료 Job을 되살리지 않는다.

이 문서는 새 코드의 검토·실행 계약이다. 현재 CP1 적용이나 인수를 증명하지 않는다.
`run-cp1-minimal-change.py`의 무인자 실행은 PLAN_ONLY이며, 실제 적용은 새 소스 리뷰와 명시된 작업창 입력이 필요하다.

## 2026-09-17 작업창 개정 — 새 v2 작업에만 적용

교육장 작업을 90분 안에 모두 끝내도록 요구하지 않는다. 소스 검토·자료 준비는 실행창 밖에서 끝내고, **CP1 시험·인수 종료시각과 인수 후 CP2/CP3 작업을 위한 유지 종료시각을 각각 승인 입력으로 정한다.** 고정 90분을 더 큰 임의 상수로 바꾸거나 기존 작업의 타이머를 끄는 변경이 아니다.

| 입력/시각 | 의미 | 적용 조건 |
|---|---|---|
| `window_start` | 실제 실행창 시작 | 준비·리뷰 시간을 미리 소모하지 않음 |
| `window_end` | 미인수 시 복구를 포함한 CP1 작업창 끝 | CP1 v2에는 기존 2시간 상한을 적용하지 않음 |
| `retention_until` | 인수 후 복구를 포함한 전체 유지창 끝 | `window_end`보다 늦고 같은 KST 평일 |
| `recovery_margin_seconds` | 두 종료시각 앞에 확보하는 복구 예산 | 명시적 정수, 최소 180초; 현장 판단으로 늘림 |
| `withdraw_at` | `window_end - recovery_margin_seconds` | 이 시각 전 root 인수 커밋이 없으면 복구 |
| `retained_withdraw_at` | `retention_until - recovery_margin_seconds` | 인수 여부와 무관하게 최종 복구 |

한 번의 승인된 **당일 유인 작업창**을 지원한다. 야간·주말·여러 날 유지, 재부팅 후 유지, 무기한 연장, Permanent 승격은 구현하지 않았다. 예전 v1 승인/작업의 최대 2시간·단일 복구 타이머 계약은 그대로다. 기존 `d9c986fcbcd3b201` 작업을 새 소스로 연장·이관하지 않는다.

양식은 [v2 실행 승인](../../policies/icr-d-net-01-01/cp1-work-window-authorization.template.json), [v2 실제 인수](../../policies/icr-d-net-01-01/cp1-work-window-acceptance.template.json)이다. 승인/인수 boolean과 시각 기본값은 비어 있으며 자동 APPROVED로 채우지 않는다. 실제 bundle·직전 상태·검토 링크·승인된 종료시각을 연결한다. [변경·검증·통합 리뷰 범위](../change/ICR-D-NET-01-01/CP1_WORK_WINDOW_REVIEW_20260917.md).

180초는 기존 `TimeoutStartSec=180s`(lock 대기 최대 30초 + 복구 명령 예산 150초)를 수용하는 **구현상 최소 예산**이다. 90분이나 180초가 정본의 교육장 최적값이라는 근거는 없다. systemd 기동 지연·명령 지연까지 포함한 복구 완료 보증이 아니므로 실제 운영자가 예산을 정한다. 작업 시작에는 기존 적용/조회 예산을 반영해 `margin + 420초`보다 많은 시간이 필요하다. 만료 직전 360초 이내에는 복구 lock을 오래 점유할 수 있는 새 status snapshot을 시작하지 않는다.

## 대상과 변경

- 대상: `k8s-control01`, firewalld **2.4.3 / nftables / SELinux Enforcing**, 확인된 단일 local block `10.244.246.64/26`.
- 새 zone `fds-cp1-min`에 **Runtime `cali+`** 하나를 연결한다. `+`는 zone 인터페이스 조건에서 nft의 `*`로 변환된다.
- `fds-cp1-local`: ingress/egress 모두 새 zone, IPv4 source/destination 모두 위 `/26`, 모든 L4 허용. 새 cali veth도 범위에 들어간다. TCP8080 전용 진단 예외와 다른 운영 후보 범위이므로 별도 명시적 검토가 필요하다.
- 새 zone의 **forward=no**로 무제한 `oifname cali* ACCEPT` 생성을 방지한다. 양쪽 인터페이스와 양쪽 주소 조건의 교집합만 local 신규 허용이다.
- HOST에 쓰이던 public의 `ssh`, `dhcpv6-client`, 기존 7개 rich rule을 복제한다. public 자체와 Calico HEP/GNP/Felix는 수정하지 않는다.
- `fds-cp1-egress`: 기존 public의 cali 유입 → **oif ens160** 전달 허용을 보존한다. `hash:net,iface` 목적지 집합을 IPv4/IPv6 각각 두 `/1`로 구성하여 실제 `daddr . oifname` 조건을 사용한다. ANY egress 정책이라는 이유로 다른 oif를 모두 ACCEPT하지 않는다. HOST INPUT/OUTPUT에는 이 정책을 적용하지 않는다.
- 기존 Calico가 앞선 hook에서 DROP하는 패킷을 이 규칙으로 허용한다고 주장하지 않는다. full nft ruleset 전체 동치를 주장하지 않으며, 새 v2 소스의 실제 HOST/외부 통신 보존은 새 실행 결과로 확인한다.

## 원본의 비활성 정책

원 receipt에 있는 gateway 정책 다섯 개는 양쪽 plane의 disable=yes와 활성 목록 부재를 확인하여 그대로 보존한다. 다른 정책·활성화·원본 변경은 중단한다. 실패한 inspect는 별도 진단 schema로 완료된 snapshot을 남기며, apply 입력으로 쓸 수 없다. [현장 중단 원인·수정·회귀검증](../change/ICR-D-NET-01-01/CP1_DISABLED_POLICY_REVIEW_20260917.md).

## 수명과 복원

v1은 KST 평일 최대 2시간이며 종료 180초 전 단일 transient timer가 `recover --job`을 실행한다. v2는 위의 승인된 당일 작업창을 사용하며 **방화벽 변경 전에 두 개의 일회성 transient timer/service**를 예약한다. 두 timer 모두 같은 root 보관 소스의 `deadline --job`을 실행한다.

- 첫 deadline: 유효한 root 인수 커밋과 최종 timer가 있으면 방화벽을 바꾸지 않고 유지한다. 미인수·인수 기록 불완전/불일치·최종 timer 누락이면 기존 복구 경로를 실행한다.
- 최종 deadline: 인수됐어도 기존 복구 경로를 실행한다.
- 적용 실패: 기존 복구를 즉시 시도한다. timer 예약이 부분 실패하면 방화벽 변경 전 예약 정리·원 상태를 확인하며, 확인 실패를 RESTORED로 표시하지 않는다.

인수 시 timer를 재예약하거나 취소하지 않는다. 상시 watcher/부팅 service, #13 서비스 확장, 자동 연장은 추가하지 않는다. transient timer는 재부팅 내구성이나 외부 관리자 변경 방지 장치가 아니다.

새 객체를 생성하기 위해 **Permanent에 비활성 정의 5개를 잠시 쓰는 단계가 있다**. Runtime-only 활성화와 Permanent 무변경은 같은 뜻이 아니다. `inert_permanent_staging_acknowledged=true`를 포함한 검토·실행 결정 없이는 시작하지 않는다.

1. 원 CP1 receipt·fresh inspect·소스·실제 전후 대조, root 전용 journal/소스 보관, 독립 회수 예약.
2. 인터페이스·source binding 없는 새 zone/정책/ipset을 Permanent에 준비한다.
3. reload 후 기존 public Runtime 6개를 복원하고 전체 상태를 대조한다. 다른 Runtime 객체나 public service 정의가 Permanent와 다르면 **첫 변경 전에 중단**한다.
4. 새 Permanent 정의를 의존성 역순으로 삭제하고, Permanent가 원래 상태와 같은지 검증한다. 이후 reload 없이 Runtime `cali+`만 활성화한다.
5. 복원은 Runtime binding 제거 → 남은 소유 Permanent staging 삭제 → reload/원 6개 복원 → whole baseline 일치 → timer 해제 순서다.

부분 변경과 응답 유실은 같은 journal로 복원한다. foreign drift 시 맹목적으로 reload하지 않는다. 소유 Runtime 객체가 정확히 일치하면 그 binding만 먼저 내리고, 나머지는 콘솔 검토로 남긴다. 소유 객체가 변했거나 보호 상태 조회 자체가 실패하면 자동 복원 성공을 주장하지 않는다. 재부팅 시 cali binding은 활성화되지 않지만, boot identity가 바뀐 원 6개 복원/정리 판정은 콘솔 검토가 필요하다. 주기적 재시도/부팅 자동 복원은 이 최소 구현 범위에 없다.

## 실행 입력과 계정

일반 계정으로 접속하고 필요한 명령에 `sudo /usr/bin/python3 -I -B`를 사용한다. root 계정 로그인은 필요 없다. 원 receipt, 승인 JSON, 검사 JSON은 일반 파일과 그 SHA256을 함께 제공한다. 중복 JSON key·심볼릭 링크·해시 불일치는 차단한다.

검토 전 가능한 로컬 명령:

```bash
python3 -I -B scripts/run-cp1-minimal-change.py
python3 -m unittest discover -s tests -v
```

소스 승인 후 실행 순서:

- `inspect --original FILE --original-sha256 SHA`: root로 읽기만 수행, 원 receipt.after와 전체 현재 상태가 같은지 검사한다. 생성 시각 15분 이내 검사만 apply에서 수용한다.
- `apply`: 위 두 원본 옵션 + `--inspection FILE --inspection-sha256 SHA` + `--authorization FILE --authorization-sha256 SHA`. 빈 승인 양식은 실행되지 않는다. 새 `JOB_ID`와 root journal을 보존한다. 이전 미회수 작업이 있으면 새 작업을 차단한다.
- `status --job ID`, `recover --job ID`: 동일 보관 소스만 허용한다. 새 checkout으로 다른 버전의 작업을 회수하지 않는다.
- root 작업 경로: `/var/lib/fds/cp1-minimal/ID/`. `CHANGE.json`, `SOURCE.json`, `ORIGINAL.json`, `AUTHORIZATION.json`, `commands.jsonl`을 보존한다. 원문 NM/boot 식별자는 공개 GitHub에 게시하지 않는다.
- 적용 후 생성되는 `expected-firewall.json`을 기존 p3dmjivl trial kit의 **새 입력 사본**으로 사용한다. 기존 trial Python 코드는 유지하고 새 입력 해시를 기록한다. 과거 REQUEST/RESULT/receipt는 수정하지 않는다.
- 실제 기존 5개 흐름 종료·namespace 삭제 확인 후 `issue-receipt --job ID --trial ARCHIVE --trial-sha256 SHA`. 검증 완료 archive만 새 `HANDOFF.json`으로 연결한다. 출력은 인수 대기이며 자동 APPROVED/ACCEPTED가 아니다.
- v2에서 실제 Positive/Negative/Regression 증적과 독립 인수 결정을 확정하면 `accept-retention --job ID --acceptance FILE --acceptance-sha256 SHA --trial ARCHIVE --trial-sha256 SHA`를 **해당 job의 root 보관 소스**로 호출한다. 별도 방화벽 변경이나 새 사람 승인 라운드가 아니라, 같은 인수 단계에서 결정된 결과를 root 기록에 결합하는 작업이다. `expires_at`은 `retained_withdraw_at`과 같아야 한다.
- `ACCEPTANCE.json` 뒤 `RETENTION.json`을 마지막 커밋 표시로 기록한다. 기존 `CHANGE.json`/`HANDOFF.json`을 덮어쓰거나 `cp1_accepted`를 바꾸지 않는다. 실제 status의 `ACCEPTED_RETAINED`와 인수/handoff hash·최종 timer가 후속 작업의 근거다. 늦은 커밋은 유지 성공으로 처리하지 않는다. 동일 인수 기록의 재조회는 새 기한을 만들지 않는다.

### 절차를 줄이는 실행 묶음

1. 새 소스·기간 정책·조건부 CP2/CP3 순서를 한 리뷰 요청으로 검토한다. 코드/키트 준비, 기존 백업·콘솔 증적 확인은 실제 실행창 전에 마친다. 동일 백업을 다시 만들거나 완료된 검사를 이유 없이 반복하지 않는다.
2. 실제 착수 시 기존 job의 회수 완료 여부를 읽기 전용 preflight에 합치고, fresh inspect → 입력 해시 결합 → CP1 apply를 연속 수행한다. 회수 완료가 확인되지 않으면 자동 만료됐다고 추정하지 않는다. 이 관측을 별도 승인 문서로 만들지는 않는다.
3. 같은 작업에서 기존 5-flow·HOST/외부 회귀·범위에 맞는 negative 증적 → handoff → 실제 독립 인수 → root 유지 커밋을 연결한다. 증적 파일을 재사용하며 형식 때문에 동일 시험을 반복하지 않는다. 다만 **새 소스/새 job의 전후 상태를 과거 PASS로 대체하지 않는다.**
4. 승인된 유지창 안에서 CP2 대상별 직전 inspect/실행/결과 검토 후 CP3로 진행한다. 대상별 상태 해시와 원래 PR #4 승인 입력은 남기되 관련 검토 요청은 한 묶음으로 준비할 수 있다. CP2 결과를 보기 전 CP3를 자동 실행하거나 PR #11 승인 링크를 PR #4 승인으로 바꾸지 않는다.

코드 리뷰와 실행 범위 승인을 묶는 것과 아직 없는 실측 결과를 미리 인수하는 것은 다르다. drift·잔여 구성 검사의 PASS를 실제 deny 시험이나 cross-node/정본 AC 통과로 확대하지 않는다.

배포 ZIP은 코드·실행 범위 리뷰 후 고정 소스로 준비한다. 현재 문서의 검토 후보를 즉시 VM에 실행하도록 안내하는 패키지는 만들지 않았다.

## 새 receipt와 후속 소비자

원 CP1 생산자 `cp-canary-node.py`, 기존 `node-firewall.py`/`run-node-firewall.py`는 동일 바이트다.

`fds.cp1-minimal-handoff.v1`은 원 receipt hash, 새 변경 before/after/hash, 검증된 trial report와 원 wrapper hash를 연결한다. 실제 probe stdout/HTTP 본문/DNS/Pod UID/Service/정리 시각/전후 일치를 재계산하고, 두 Pod 주소가 검토된 `/26` 안에 있어야 한다. 과거 3/5 증적으로 발급하지 않는다.

정식 인수는 v1 작업에 `fds.cp1-minimal-acceptance.v1`, 새 v2 작업에 `fds.cp1-minimal-acceptance.v2`를 사용한다. 독립 리뷰어, source bundle, 새 handoff hash, positive/negative/regression 증적 hash, 인수 시각과 만료 시각을 결합한다. v2는 첫 deadline 전에 실제 인수되고 root 커밋된 작업만 최종 deadline까지 소비할 수 있다. 체크섬과 JSON의 이름/boolean은 GitHub 승인 사실의 인증 수단이 아니다. 운영자와 리뷰어가 실제 댓글·증적을 확인해야 한다.

`run-node-firewall-cni.py`는 기존 controller의 별도 어댑터다. `--cp1-receipt`, `--cp1-acceptance`에 더해 새 `--cp1-handoff`, `--cp1-trial`과 각각 SHA256을 받는다. 원본 receipt validator와 기존 대상별 승인·정책·SSH identity·global lock·topology·whole CP1 fingerprint 검사를 유지한다.

적용 전/후에 새 인수와 원본 trial을 연결한 계약을 확인하고, CP1 root 보관 소스의 read-only status에서 **전체 after-state·job/source/record hash·timer·만료**를 대조한다. v1은 기존 적용 전 900초 여유 검사를 유지한다. v2는 대신 **대상 노드의 승인된 `window_end`가 CP1 `retained_withdraw_at`보다 앞인지** 확인하고 root 인수/handoff hash까지 대조한다. CP2/CP3 각 노드 작업창의 기존 최대 2시간 제한은 전체 CP 작업의 시간 제한이 아니며 이번에 변경하지 않았다. 적용 중 CP1 보호·소유 객체·수명이 바뀌면 최종 성공 판정을 중단한다. CP2/CP3 작업의 원래 PR4 승인 입력, Join 금지, Permanent promotion 미구현 조건도 유지한다. 이 어댑터가 후속 작업을 자동 시작하지 않는다.

## 현재 검증 범위

- 새 작업창 개정의 관련 unittest **156/156 STATIC/MOCK PASS**(기존 선택 범위 119 + 새 작업창/인수/실제 어댑터 함수 경계시험 37). 아래 과거 수치와 합산하지 않는다. [로그·검증 범위](../change/ICR-D-NET-01-01/CP1_WORK_WINDOW_REVIEW_20260917.md).
- 새 v2의 실제 systemd/방화벽 Runtime 적용·자동 deadline·현장 인수·CP2/CP3·Join·CI·병합은 미수행이다. 아래는 이전 소스 당시의 검증 기록이며 현재 서버 상태나 새 소스의 현장 성공을 뜻하지 않는다.

### 작업창 개정 이전의 검증 이력

- 관련 145/145 STATIC/MOCK PASS: 기존 130 + 비활성 정책/실패 진단 15. 실제 과거 정책 6개와 zone 10개 투영을 포함한다. 불완전/응답 유실 변경 지점 12개, 복원·drift·window·receipt·실제 어댑터 함수 흐름을 대역으로 실행.
- firewalld v2.4.3 hash-pinned 함수 실행 + 로컬 패킷 모델 21조건 PASS.
- 보충 검사: 실제 XML parser/config 검사/저장 후 재읽기와 선택된 Settings·CLI formatter 대조 **18/18 PASS**. [범위·재현·제한](../change/ICR-D-NET-01-01/CP1_MINIMAL_XML_REVIEW_20260917.md). D-Bus daemon/nft kernel/서비스 timer는 미실행이다.
- 과거 실제 p3dmjivl 자료 재생: **3/5, INCOMPLETE_REVIEW_REQUIRED**, consistency issue 0. 새로운 실측 결과가 아니다.
- 위 정적 검증 기록 작성 당시에는 실제 적용 전이었다. 이후 제출된 구 버전 CP1 실측/인수 증적은 그 job과 기한에만 유효하며, 이번 새 소스의 Runtime PASS나 현시점 유지·회수 완료로 자동 승계하지 않는다.

필요한 다음 검토는 이 최소 변경의 소스/범위/임시 staging/유지·복원/새 인수 계약 한 묶음이다. 전체 #11 qualification이나 #13 확장을 새 필수조건으로 추가하지 않는다.
