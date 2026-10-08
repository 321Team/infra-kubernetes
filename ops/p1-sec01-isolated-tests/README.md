# P1-SEC-01 격리 시험 수정본 — Probe v2.0 / Backup v2.1 — GWONUK 구현 / 이하영 현장 시험

책임 티켓: [baseline #19](https://github.com/321Team/fds-project-baseline/issues/19).
정본: `docs-v3.1.21 @ 979facb98de8ca1f1e2ce22f38b27ac3366f3fc3`.
이 디렉터리는 격리 Probe 관측과 기존 etcd snapshot의 격리 복원 도구다.
실제 APP HTTP Probe 인수, 운영 etcd 복원, Secret 복호화, 예방적 rotation, AC 인수·Issue Close·Phase1 Close는 포함하지 않는다.


## Backup v2.1 변경과 현장 근거 (2026-10-08)

10/8 00:01 첫 읽기 전용 수집은 `/proc/<pid>/exe`의 링크를 호스트 경로로
`resolve(strict=True)`해 중단됐다(rc2). 원본은 보존했다. 컨테이너의 링크 대상은
호스트에 없어도 정상이므로 `os.readlink`로 링크 문자열만 읽도록 수집기를 수정했다.
후속 수집은 `sec19-backup-version.VRbii4QQ`에 봉인됐다. 내부 92개 SHA 일치,
운영 PID 2713/starttime 1000 및 Pod UID/imageID 전후 동일, 컨테이너 내부와
호스트 `/proc/PID/root` 경유 버전 6개 명령 모두 rc0/3.6.8이다.
운영 exe SHA는 `backup-binaries.sha256`의 etcd SHA와 같다. 실제 수집 증적은
`fds-project-evidence:evidence/P1-SEC-01/backup-version-20261008/`로 편입한다.

- 프로그램 버전은 **정확히 3.6.8**, 세 바이너리는 수집된 해시로 고정한다.
  snapshot storage VERSION **3.6.0**, SHA/revision/key 수는 evidence#99 값이다.
  서로 다른 개념이며 3.x 전체 허용이나 운영 etcd downgrade는 하지 않는다.
- `backup-tools.sh`는 세 도구 각각의 SHA(전/후), version stdout/stderr/rc와
  운영 exe SHA(전/후)를 **판정 전에** 기록한다. 첫 도구가 실패해도 남은 도구를
  시간 예산 안에서 수집한다. 정상화한 version/hash와 실행 소스는 precheck→run에도 비교한다.
- mismatch, rc127, rc124, 해시 수집 실패, 실행 중 파일 변경, 운영 exe 불일치는 STOP이다.
  버전 stdout을 잃거나 실패한 명령을 rc0으로 덮지 않는다. 쓰기 실패는 rc12다.
- snapshot의 원본/복원본 status에서 storage VERSION을 별도로 검사한다.
  상태 읽기·격리 복원은 **run 승인 뒤** 수행하며 이번 읽기 전용 수집으로 복원 성공을 주장하지 않는다.
- `probe.sh`, `common.sh`, `checks.py`, `backup-inner.sh`는 이전 승인 코드와 동일하다.
  기존 Probe GO를 이 Backup 후보의 GO로 확대하지 않는다.
- standalone `backup-version-capture.sh`는 원인 확인 5항목의 수집 도구다.
  control01 root, 기존 admin.conf 및 도구로 실행하며 운영 상태는 읽기 전용이다.
  각 명령 15초, 전체는 `timeout --signal=TERM --kill-after=5s 180s bash backup-version-capture.sh`.
  새 root 전용 폴더에만 기록하며 tool rc는 개별 파일로 보존한다. capture rc0은 수집 완료이지
  Backup precheck PASS가 아니다. PID/Pod 교체·상한 초과 때 중단하고 자동 재실행하지 않는다.
  `file`/`readelf`가 없으면 설치하지 않으며 누락 rc를 제출한다. Secret·키는 읽지 않는다.
  종료 후 출력된 디렉터리·manifest·외부 종료코드를 보존한다. 서비스 변경이 없어 서비스 원복은 없다.

이번 PR은 코드/실제 읽기 전용 수집을 분리한다. 새 backup precheck와 run은 미실행이며,
정확한 새 HEAD의 독립 리뷰 및 기존 #19 조건 충족 후 판단한다.

## 입력·소유와 변경

- 원본: [이하영 제출 6037381086](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6037381086).
  Probe v1.2 SHA256 `70f0a70cecec6bbe65076179459f1f60947ab41079cfd530f48e5acee36e7468`,
  backup v1.1 SHA256 `b648bd296c8b0719f9e8acf0c870c9582b12b0e14174389b8c74c3404986eadf`.
- 수정 근거: [리뷰 6037581049](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6037581049).
  [직접 인수 6037746768](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6037746768).
- **GWONUK**: 코드 수정·회귀검증·고정 실행본 인계 및 문제 재현/수정 담당.
  **hayeounglee(이하영)**: 현장 사전 점검·승인된 시험 실행·원본 수집 담당.
  **supreme33-kr(이재환)**: 기존 독립 코드/증적 리뷰 경로. 실행자에게 중간 코드 재작성은 요청하지 않는다.
- 기준 저장소 HEAD: `infra-kubernetes @ 80f39bd5339474fc4dc6ceada7c66d8b3d2288b5`.
  기존 IF-12B/AC-04 collector, 운영 manifest, OS/Inventory/배포 경로는 변경하지 않는다.

| 수정 | 결과 |
|---|---|
| INT/TERM 중복 처리 | finish 재진입 금지. 정리 중 추가 신호는 터미널에만 표시하고 남은 정리를 계속한다. |
| 관측·정리 시간 | `/proc/uptime` 단조 시계로 단계/전체 예산을 계산. API 지연 포함, 만료 후 명령 발행/성공 판정 금지. |
| Probe 정리 | create 응답 유실 시 고유 run label·managed-by·image/selector로 UID 회수. UID 조건 삭제만 사용. UNKNOWN/FOREIGN은 rc13 수동 인계. |
| Probe 보호 | API 오류/Quota·LimitRange 조회 실패는 STOP. 정확한 6노드, 운영 Pod·EndpointSlice 주소/UID/port/조건을 비교. T0 및 종료 시 SA token 차단 검증. |
| backup 결과 | count-only에 fields 형식 지정, stdout/stderr/rc 분리, 단일 정수 Count만 수용. Secret 집계 producer/parser 어느 쪽 실패든 중단. |
| backup 소스 | 세 바이너리 버전·64자리 해시·운영 exe 일치를 필수화. 봉인된 precheck와 현재 소스/바이너리, 복사한 세 바이너리 모두 대조. |
| backup 정리 | 프로세스 명령 substring으로 kill하지 않음. PID namespace 종료 후 exact etcd data-dir 잔존 여부 확인. 잔존/UNKNOWN이면 디렉터리 보존·rc13. |
| 복구·증적 | 사후 기준선/health/snapshot 불일치·조회 실패는 rc3. 해시 목록·tar 원본 바이트까지 검증; 봉인 실패 rc12. completion은 봉인 폴더 밖에 원자적으로 작성. |

`etcdctl --count-only` 형식 근거:
[etcd v3.6.0 get_command.go](https://github.com/etcd-io/etcd/blob/v3.6.0/etcdctl/ctlv3/command/get_command.go),
[printer_fields.go](https://github.com/etcd-io/etcd/blob/v3.6.0/etcdctl/ctlv3/command/printer_fields.go).
live count와 snapshot `TOTAL KEYS`는 서로 다른 측정이다. 복원 전후 snapshot status끼리 revision/keys를 비교한다.

## 고정 범위와 사전 조건

| 도구 | 실행 위치/권한·범위 |
|---|---|
| `probe.sh` | 기존 승인 관리 콘솔/계정의 kubectl. `fds-app`, `k8s-worker01`, 고유 시험 Pod+ClusterIP Service 각 1개. imagePullPolicy Never. |
| `backup.sh` | `k8s-control01` root 콘솔. 기존 `/root/p1-sec01-secretbox-backup-20261002T054208Z/etcd-snapshot-sec01.db`, SHA `8ace820ca29488fc5ef8eb46d424a58ef6e0dbbba03f6d0f851f929e6e771e2f`, revision3287996/keys620. |

Probe 이미지: `10.1.93.54/fds/transaction-api@sha256:a7093136d12640da00570c18ed278eacbb8e9ba3637c7d509571346eac45cc33`.
컨테이너 Python `/usr/local/bin/python3.12`, user10001/group999, read-only rootfs, drop ALL,
RuntimeDefault, token automount=false, host network/PID/IPC=false, tmp emptyDir1Mi,
requests20m/32Mi, limits200m/128Mi는 v1.2 범위 그대로다.
고정 노드에서 같은 digest 실행 중인 Pod는 cache의 간접 근거다. 실제 Python·이미지 기동은 T0에서 확인하며 실패 시 중단한다.
Service는 EndpointSlice 관측용이며 HTTP 서버 연결 시험이 아니다.

두 도구 모두 Linux Bash, GNU timeout/coreutils, **호스트 Python 3(표준 라이브러리만)**가 필요하다.
backup은 추가로 `unshare --net --pid --fork --kill-child`, `ip`, `ss`, 컨테이너의 etcd/etcdctl/etcdutl **3.6.8 및 `backup-binaries.sha256`의 정확한 3개 해시**이 필요하다.
없거나 실행 불가하면 STOP; 설치·권한 확대·이미지 pull·SELinux/방화벽/TLS 완화로 우회하지 않는다.
기존 운영 계정·접근 경로만 사용한다. 6노드 Ready, 앞 시험 종료/원복, APP·#13 등 다른 Runtime과 비중첩을 실행 전 확인한다.

## 시간과 승인 경계

| 구간 | Probe | backup |
|---|---:|---:|
| 매 호출 fresh precheck | 180초 | 180초 |
| run 관측 | 900초 | 600초(그 안에 namespace 구간300초) |
| 정리 | 90초 | 60초 |
| 사후 확인 | 90초 | 90초 |
| 실패 진단(실패 경로만) | 30초 | 해당 없음 |
| 봉인 | 30초 | 30초 |
| run의 계획 예산 합계 | **22분** | **16분** |
| 별도 precheck 호출 | **3분30초** | **3분30초** |

위는 외부 명령의 단조 시간 예산이며 작은 로컬 기록/스케줄링 여유는 시험 창에 추가한다.
커널 I/O 정지·SIGKILL·전원 단절에서 정리/봉인을 보증하지 않는다. 정상 종료 기록이 없으면 수동 확인 전 다음 시험 금지.
관측만15분/10분으로 전체 시험 창을 계산하지 않는다. 남은 창이 전체 예산과 정리 여유보다 짧으면 시작하지 않는다.
짧은 외부 `timeout ... 180`으로 v2 run/precheck 전체를 감싸지 않는다. 코드 안에서 정리·봉인 시간을 따로 확보한다.

**이 파일 또는 CI PASS는 현장 RUN GO가 아니다.** 혼합 코드 PR의 exact HEAD 독립 리뷰와 필요한 승인·새 시험 창을 #19에 연결한 뒤 실행한다.
[중앙 정책 §3·§3.2·§4](https://github.com/321Team/fds-project-baseline/blob/main/governance/REVIEW_AUTHORITY_POLICY.md)에 따라
작성자 점검을 독립 승인으로 표시하지 않는다. 별도 격리 후보시험은 해당 조건과 Lead 판단을 기록해야 하며, 운영 클러스터/호스트 실행과 혼동하지 않는다.
10/7 기존 ①② GO, 구버전 ③④ precheck GO, **22:25 상한**, #13의23:00 T0를 v2로 자동 변경·연장하지 않는다.

## 전달·실행 순서

PR/이슈에 적힌 **전체 commit SHA**의 ZIP 또는 로컬 전달 묶음으로 이 디렉터리 전체를 승인된 파일 전달 경로로 옮긴다.
VM 콘솔에 긴 코드를 복사해 재작성하지 않는다. GitHub 로그인/토큰, snapshot·개인키·Secret 원문을 묶음에 추가하지 않는다.
`SHA256SUMS`와 backup.sh가 source-sha256.txt에 기록하는 코드·도구 해시 목록은 같은 commit이어야 한다. checks.py 한 파일만 교체하는 방식은 금지한다.

1. 실행자의 root/본인 전용 새 작업 폴더에서 `umask 077`을 설정한다. 전달된 코드 폴더의 절대 경로를 `CODE_DIR`에 지정하고 `(cd "$CODE_DIR" && sha256sum -c SHA256SUMS)`로 대조한다. 실패 시 중단한다.
2. `bash "$CODE_DIR/probe.sh" render`는 서버 접근 없이 생성 manifest를 보여준다.
3. **해당 버전 사전 점검 승인이 기록된 뒤** 아래 precheck를 각각 실행하고 rc·출력 경로를 남긴다.

```bash
# 기존 승인 계정/콘솔; CODE_DIR은 전달 묶음의 실제 절대 경로
KC='kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s' \
  bash "$CODE_DIR/probe.sh" precheck
rc=$?; printf 'probe_precheck_rc=%s\n' "$rc"

# k8s-control01/root
KC='kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s' \
  bash "$CODE_DIR/backup.sh" precheck
rc=$?; printf 'backup_precheck_rc=%s\n' "$rc"
```

4. 해당 SHA·현장 precheck 원본·시작/종료 시각·비중첩을 #19에 연결해 **범위를 한정한 RUN GO**가 기록된 뒤 아래를 실행한다. `PRECHECK_DIR`에는 v2.1 backup precheck의 실제 출력 폴더를 넣는다. run도 fresh precheck를 반복한다.

```bash
KC='kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s' CONFIRM=YES \
  bash "$CODE_DIR/probe.sh" run
rc=$?; printf 'probe_run_rc=%s\n' "$rc"

# Probe 종료/원복·승인된 순서·충분한 별도 창 확인 후, control01/root
KC='kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s' \
  PRECHECK_DIR="$BACKUP_PRECHECK_DIR" CONFIRM=YES bash "$CODE_DIR/backup.sh" run
rc=$?; printf 'backup_run_rc=%s\n' "$rc"
```

각 명령은 독립적으로 실행한다. 위 두 run을 무조건 연속 실행하는 배치로 쓰지 않는다.
API 오류/주입 실패/UID 교체/예상 밖 restart/노드 또는 기준선 변화/시간 초과는 다음 주입을 중단한다.
backup은 도구·snapshot·revision/keys·집계 오류 시 다음 단계를 중단한다.

## 종료·복구·제출

| rc | 의미와 실행자 조치 |
|---|---|
| 0 | precheck 필수 조건 적합 또는 run 관측·원복 확인·봉인 완료. **AC PASS가 아님**. 원본 제출. |
| 2 | 사전 조건 STOP. 서버 시험 미착수. 사유/원본을 제출하고 코드 수정 필요분은 GWONUK가 처리. |
| 3 | 관측 또는 사후 확인 실패. 다음 시험 중단. 정리 결과/원본 제출. |
| 12 | 기록/봉인/completion 쓰기 실패. 성공 증적으로 쓰지 않고 남은 원본 보존. |
| 13 | 소유권·정리 불명/잔존. 해당 대상·UID 또는 exact 작업 경로를 인계하고 다음 시험 중단. |

Probe는 자신의 UID로 Pod/Service를 삭제하고 두 객체 NotFound, 운영 Pod/EndpointSlice 기준선과 6노드 Ready를 확인한다.
UID 불명·교체 객체는 이름만으로 삭제하지 않는다. 필요한 수동 정리는 GWONUK/SEC Lead가 현재 객체 UID와 원본 run label을 대조한 후 정한다.
backup은 격리 namespace의 종료로 자식 프로세스를 종료한다. 자신의 root700 고유 작업 폴더는 device/inode·실제 경로·부모를 대조하고,
해당 data-dir etcd가 없을 때만 제거한다. 잔존 프로세스는 넓은 `pkill`로 처리하지 않고 원본 경로를 보존해 수동 인계한다.
운영 snapshot hash·etcd health·2379/2380/2381 listener를 사후 확인한다. 원본 snapshot, 운영 etcd member, 운영 manifest를 변경하는 복구 명령은 없다.

출력 폴더, `.tar.gz`, `.tar.gz.sha256`, `.sealcheck.txt`, `.completion.txt`, 호출 rc와 시작/종료 KST를 함께 보존한다.
`SHA256SUMS`와 archive 검증은 무결성 확인이다. 실제 동작 판정은 T0~T3 또는 R0~R3 원본과 복구 확인을 따로 검토한다.
snapshot/복원 DB/Secret 값은 결과 폴더에 넣지 않는다. 성공·실패 모두 동일하게 증적 제출 전 민감정보 여부를 확인한다.
`fds-project-evidence` 편입은 별도 독립 검토 경로를 유지한다.

## 개발 검증

```bash
for f in ops/p1-sec01-isolated-tests/*.sh; do bash -n "$f" || exit; done
python3 -m unittest discover -s ops/p1-sec01-isolated-tests/tests -v
```

테스트는 가짜 kubectl/etcdctl/IP 명령과 임시 폴더만 사용한다. 클러스터·운영 etcd 접속/실제 restore/unshare/root가 필요 없다.
Probe는 실제 전체 entrypoint를 실행하며 정상 T0~T3, precheck 무변경, lost-create UID 정리, stale/quota/API/주입/token/restart/기준선 오류를 시험한다.
backup은 실제 inner script의 정상·count 오류·Secret producer/parser 오류를 시험한다.
공통 함수는 반복 신호·느린 명령·만료 시 성공 거부·정리 실패 우선순위·봉인 무결성을 검증한다.
**backup의 실제 snapshot restore, namespace 격리, 운영 health, 실제 이미지 Probe는 현장 RUNTIME 미실행**이며 MOCK/CI로 대신하지 않는다.

## 2026-10-08 Probe T0 실패 후 수정 후보 v2.2

기존 승인 d6866187 전체 번들의 09:16:45~09:18:16 KST 1회 시험은 T0 rc3으로 종료했다.
Pod 이벤트의 직접 원인은 `/venv/bin/python3` 파일 부재다. 같은 a7093136 digest로 실행 중인
Worker01 APP 컨테이너의 실제 process args는 `python run.py`, exe는 `/usr/local/bin/python3.12`였다.
현재 앱 저장소의 apko Dockerfile을 이 과거 실행 이미지와 같은 것으로 취급한 가정을 정정한다.
운영 이미지·앱 코드·보안 통제는 바꾸지 않는다.

후보는 실행/exec Probe/주입 interpreter를 해당 고정 digest의 실제 경로로 일치시킨다.
T0의 restart>0은 주입 전 즉시 중단하며, 최초 Pending에서 아직 containerStatuses가 없는 경우는
정해진 T0 예산 안에서 기다린다. abort 시 별도 최대30초 내 Pod와 해당 UID 이벤트를 보존한 뒤
기존 UID 정리·기준선 대조·봉인을 수행한다. 진단 조회 실패도 rc/stderr로 남기며 정리를 생략하지 않는다.
실패 경로 계획 예산은 기존21분30초에 진단30초를 더한22분이다.

기존 실행 원본과 승인 SHA는 수정하지 않는다. 이번 후보는 독립 리뷰와 새 exact 번들·별도
재시험 창 확정 전 RUNTIME NOT_RUN이다. Backup 소스·기존 결과/GO는 변경하지 않으며,
이번 디렉터리를 과거 d6866187 승인본으로 표시하거나 14:00에 자동 재실행하지 않는다.
근거: [조기 1회 실행 기록](https://github.com/321Team/fds-project-baseline/issues/19#issuecomment-6049479655).
CODEX_ASSISTED / 코드 수정 GWONUK / 독립 리뷰 별도.
