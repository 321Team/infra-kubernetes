#!/usr/bin/env bash
# P1-SEC-01 AC-05 backup: etcd snapshot 격리 복원 관측 (운영 etcd는 health 조회만)
# REV=v2.0-20261007 — GWONUK lifecycle fixes; see README for provenance/approval.
#
#   k8s-control01 콘솔(root)에서:
#     KC='kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s' \
#       bash backup.sh precheck                         읽기 전용 사전 확인(임시 etcd 기동·복원 없음)
#     PRECHECK_DIR=<위 precheck 출력 폴더> CONFIRM=YES KC='kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s' \
#       bash backup.sh run                              복원·임시 etcd 기동·관측·정리
#
# 격리: 임시 etcd 와 검증 클라이언트는 **새 network namespace + 새 PID namespace**(unshare --net --pid --fork --kill-child) 안에서만 산다.
#       호스트 네트워크·운영 etcd(2379/2380/2381)·다른 로컬 프로세스에서는 접근할 수 없고, 이 namespace 의 init 가 끝나면 안의 프로세스는 모두 종료된다.
#       운영 key·TLS 설정 재사용, 보안 설정 완화, --force-new-cluster, 운영 멤버 변경은 하지 않는다.
# 도구: etcd/etcdutl/etcdctl 은 설치하지 않고, 실행 중인 운영 etcd 컨테이너의 파일시스템(/proc/<pid>/root)에서 읽기 전용으로 복사한다.
#       **precheck 가 세 바이너리의 SHA-256·버전을 기록(12-binaries-sha256.txt)하고, run 은 그 봉인된 precheck 폴더(PRECHECK_DIR)와 현재 값이 같을 때만 진행한다**(스크립트 수정 없이 실제 확인값으로 고정).
# Secret 값은 파일·stdout·stderr·shell trace 어디에도 남기지 않는다. 접두어 집계는 값을 파이프로 직접 개수 계산기에 넘기고 개수만 기록한다.
# 판정하지 않는다: 출력은 관측 요약이며 AC·backup 판정은 Review Owner 몫이다. 복호화·운영 복구(DR)는 범위 밖이다.
# 종료 코드: 0=관측 완료·정리 기록 완료(성공 판정이 아님), 2=STOP(시작 전 중단, precheck 부적합 포함), 3=시작 후 중단·실패(정리 시도함), 12=기록·봉인 실패, 13=정리 실패(수동 확인 필요)
set -uo pipefail
set +x
LIB_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 2
source "$LIB_DIR/common.sh" || exit 2
REV="v2.0-20261007"
MODE="${1:-}"
KC="${KC:-kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=10s}"

# ---- 고정 범위(환경변수는 KC, CONFIRM, PRECHECK_DIR 만 사용) ----
HOST_EXP="k8s-control01"
BK="/root/p1-sec01-secretbox-backup-20261002T054208Z"
SNAP="$BK/etcd-snapshot-sec01.db"
EXPECT_SNAP_SHA="8ace820ca29488fc5ef8eb46d424a58ef6e0dbbba03f6d0f851f929e6e771e2f"   # evidence#99 22-snapshot-SHA256SUMS
EXPECT_REV="3287996"; EXPECT_KEYS="620"; EXPECT_VER="3.6.0"                           # evidence#99 23-etcdutl-snapshot-status.txt
WORKROOT="/root/sec01-restore-work"
CPORT=23790; PPORT=23800
INNER_LIMIT=300      # 임시 etcd 구간 상한(초)
CLEAN_LIMIT=60       # 정리 구간 별도 상한(초)
MIN_FREE_MB=500

RUN_ID="$(date -u +%Y%m%d%H%M%S)-$RANDOM"
case "$MODE" in precheck|run) ;; *) echo "사용: $0 precheck|run" >&2; exit 2;; esac
[ "$MODE" = run ] && [ "${CONFIRM:-}" != YES ] && { echo "STOP: run 은 CONFIRM=YES 필요" >&2; exit 2; }
stop() { echo "STOP: $*" >&2; if declare -F finish >/dev/null; then finish 2; else exit 2; fi; }
[ "$(id -u)" -eq 0 ] || stop "root 로 실행해야 함"
HN="$(hostname -s 2>/dev/null)"; [ "$HN" = "$HOST_EXP" ] || stop "hostname=$HN 은 예상($HOST_EXP)과 다름"
for c in date sha256sum tar mktemp awk grep sed ss pgrep unshare ip cp rm stat df python3 timeout diff cut head tr sort seq find xargs realpath dirname basename chmod mkdir cat tail mv; do command -v "$c" >/dev/null 2>&1 || stop "$c 명령 없음"; done
phase_begin precheck 180
timeout 10 unshare --net --pid --fork --kill-child true >/dev/null 2>&1 || stop "unshare --net --pid --fork --kill-child 를 쓸 수 없음"

umask 077
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$(mktemp -d "p1-sec01-ac05-restore-$MODE-$TS.XXXXXX")" || stop "배타적 출력 폴더를 만들 수 없음"
START=$(mono); FAIL=0; CLEAN_FAIL=0; CLEANED=0; FINISHED=0; PRE_FAIL=0
S="$OUT/99-summary-provisional.txt"
w() { printf '%s\n' "$2" >> "$1" 2>/dev/null || { echo "쓰기 실패: $1" >&2; FAIL=1; return 12; }; }
say() { echo "$*"; w "$OUT/run.log" "$(date -u +%FT%TZ) $*"; }
elapsed() { echo $(( $(mono) - START )); }
# 운영 etcd 접근은 kubectl exec 한 경로뿐이며 유한 시간으로 제한한다
etcdctl_prod() { timeout 25 $KC -n kube-system exec "etcd-$HN" -- etcdctl --endpoints=https://127.0.0.1:2379 --cacert=/etc/kubernetes/pki/etcd/ca.crt --cert=/etc/kubernetes/pki/etcd/server.crt --key=/etc/kubernetes/pki/etcd/server.key "$@"; }
tbl_val() {   # tbl_val <표 파일> <열 이름>  → 표의 첫 데이터 행에서 해당 열 값
  awk -F'|' -v want="$2" '!h && /HASH|REVISION/ {for(i=1;i<=NF;i++){g=$i; gsub(/^ +| +$/,"",g); if(g==want) idx=i}; h=1; next} h && !done && NF>2 && $0 !~ /^\+/ {v=$idx; gsub(/^ +| +$/,"",v); print v; done=1}' "$1" 2>/dev/null
}

w "$OUT/00-environment.txt" "rev=$REV mode=$MODE host=$HN started_utc=$TS run_id=$RUN_ID"
(cd "$LIB_DIR" && sha256sum probe.sh backup.sh common.sh checks.py backup-inner.sh) > "$OUT/source-sha256.txt" || stop "소스 해시 기록 실패"
w "$OUT/00-environment.txt" "script_sha256=$(sha256sum "$0" 2>/dev/null | cut -c1-64)"
w "$OUT/00-environment.txt" "unshare=$(unshare --version 2>&1 | head -1) python3=$(python3 --version 2>&1)"

# ---------------- 정리(성공·실패·timeout·중단·예기치 않은 종료 공통) ----------------
RUN="$WORKROOT/$RUN_ID"; DD="$RUN/data"; BIN="$RUN/bin"; WSNAP="$RUN/snap.db"
RUN_CREATED=0
cleanup() {
  [ "$CLEANED" -eq 1 ] && return
  phase_begin cleanup "$CLEAN_LIMIT"
  local pids
  # Namespace init exit kills every child. Never kill a process by substring/PID reuse.
  # If a process nevertheless remains, retain its files and hand off; no broad pkill.
  pids="$(timeout 10 python3 "$LIB_DIR/checks.py" pids "$DD")" || { CLEAN_FAIL=1; pids=UNKNOWN; }
  w "$OUT/60-cleanup.txt" "exact_data_dir_pids=${pids:-none}"
  [ -z "$pids" ] || CLEAN_FAIL=1
  if [ "$RUN_CREATED" -eq 1 ] && [ -e "$RUN" ]; then
    if [ "$CLEAN_FAIL" -eq 0 ] && [ -d "$RUN" ] && [ ! -L "$RUN" ] &&
       [ ! -L "$WORKROOT" ] && [ "$(stat -c %U:%a "$WORKROOT")" = root:700 ] &&
       [ "$(realpath -e -- "$RUN")" = "$WORKROOT/$RUN_ID" ] &&
       [ "$(stat -c %d:%i "$RUN")" = "$RUN_IDENTITY" ]; then
      timeout 40 rm -rf --one-file-system -- "$RUN" || CLEAN_FAIL=1
    else CLEAN_FAIL=1; fi
  fi
  if [ "$RUN_CREATED" -eq 1 ] && { [ -e "$RUN" ] || [ -L "$RUN" ]; }; then CLEAN_FAIL=1; fi
  timeout 10 ss -ltn | awk -v a=":$CPORT" -v b=":$PPORT" '$4 ~ a"$" || $4 ~ b"$" {print $4}' > "$OUT/60-test-ports-hostns-after.txt" || CLEAN_FAIL=1
  [ ! -s "$OUT/60-test-ports-hostns-after.txt" ] || CLEAN_FAIL=1
  CLEANED=1
  w "$OUT/60-cleanup.txt" "cleanup_failed=$CLEAN_FAIL run_dir=$RUN"
}
finish() {
  local rc="$1" st sha1
  [ "$FINISHED" -eq 0 ] || return
  FINISHED=1
  if [ "$RUN_CREATED" -eq 1 ]; then
    cleanup
    phase_begin postcheck 90
    sha1="$(timeout 30 sha256sum "$SNAP" | cut -c1-64)" || rc=3
    w "$OUT/61-snapshot-sha-after.txt" "$sha1  $SNAP"
    [ "$sha1" = "$SNAP_SHA0" ] || rc=3
    timeout 10 ss -ltn | awk '$4 ~ /:(2379|2380|2381)$/ {print $4}' | LC_ALL=C sort > "$OUT/62-prod-listeners-after.txt" || rc=3
    etcdctl_prod endpoint health > "$OUT/63-prod-health-after.txt" 2>&1; hrc=$?
    w "$OUT/63-prod-health-after.txt" "rc=$hrc"
    [ "$hrc" -eq 0 ] || rc=3
    diff -q "$OUT/13-prod-listeners-before.txt" "$OUT/62-prod-listeners-after.txt" >/dev/null 2>&1 || rc=3
    w "$S" "snapshot_sha_after=$sha1 prod_health_after_rc=$hrc"
  fi
  w "$S" "observation_exit_code=$rc cleanup_failed=$CLEAN_FAIL elapsed_sec=$(elapsed)"
  seal_out
  if [ "$CLEAN_FAIL" -ne 0 ]; then st=13; elif [ "$FAIL" -ne 0 ]; then st=12; else st="$rc"; fi
  completion "$st"; st=$?
  cat "$S"; exit "$st"
}
trap on_signal INT TERM
trap 'rc=$?; if [ "$FINISHED" -ne 1 ]; then [ "$rc" -ne 0 ] || rc=3; printf "Unexpected exit rc=%s\n" "$rc" >&2; finish "$rc"; fi' EXIT
abort() { say "ABORT: $*"; finish 3; }

# ---------------- 사전 확인(읽기 전용, 모든 외부 명령은 시간 제한) ----------------
[ -f "$SNAP" ] && [ ! -L "$SNAP" ] || stop "snapshot 파일이 없거나 symlink: $SNAP"
SNAP_SHA0="$(timeout 60 sha256sum "$SNAP" | cut -c1-64)"; w "$OUT/10-snapshot-sha-before.txt" "$SNAP_SHA0  $SNAP"
[ "$SNAP_SHA0" = "$EXPECT_SNAP_SHA" ] || stop "snapshot SHA-256 이 증적(evidence#99)과 다름: $SNAP_SHA0"
SNAP_PERM="$(stat -c '%U:%a' "$SNAP")"; w "$OUT/10-snapshot-perm.txt" "$SNAP_PERM"
if [ "$SNAP_PERM" != "root:600" ]; then PRE_FAIL=1; w "$S" "FAIL: snapshot 권한=$SNAP_PERM (root:600 아님)"; [ "$MODE" = run ] && stop "snapshot 권한이 root:600 이 아님($SNAP_PERM)"; fi
FREE_MB=$(df -Pm /root | awk 'NR==2{print $4}'); w "$OUT/11-disk.txt" "free_mb=$FREE_MB snapshot_bytes=$(stat -c %s "$SNAP")"
[ "${FREE_MB:-0}" -ge "$MIN_FREE_MB" ] || stop "/root 여유 공간 ${FREE_MB}MB < ${MIN_FREE_MB}MB"
EPID="$(timeout 10 python3 "$LIB_DIR/checks.py" pids /var/lib/etcd)" || stop "운영 etcd PID 조회 실패"
[[ "$EPID" =~ ^[0-9]+$ ]] || stop "운영 etcd exact data-dir PID 가 하나가 아님"
w "$OUT/12-prod-etcd-process.txt" "pid=$EPID cmdline=$(tr '\0' ' ' < /proc/$EPID/cmdline | head -c 600)"
for b in etcd etcdutl etcdctl; do [ -x "/proc/$EPID/root/usr/local/bin/$b" ] || stop "운영 etcd 컨테이너에 /usr/local/bin/$b 없음"; done
PROD_EXE_SHA="$(timeout 30 sha256sum "/proc/$EPID/exe" 2>/dev/null | cut -c1-64)"; w "$OUT/12-prod-etcd-process.txt" "prod_exe_sha256=$PROD_EXE_SHA"
# 세 바이너리의 경로·SHA-256·버전을 기록한다. run 은 봉인된 precheck 폴더의 이 파일과 현재 값이 같을 때만 진행한다.
: > "$OUT/12-binaries-sha256.txt"
[[ "$PROD_EXE_SHA" =~ ^[0-9a-f]{64}$ ]] || stop "운영 exe 해시 확인 실패"
for b in etcd etcdutl etcdctl; do
  p="/proc/$EPID/root/usr/local/bin/$b"
  v="$(timeout 10 "$p" $([ "$b" = etcd ] && echo --version || echo version) 2>&1)" || stop "$b version 실패"
  printf '%s\n' "$v" | grep -Eq "^(etcd Version:|etcdutl version:|etcdctl version:) ${EXPECT_VER//./\\.}$" || stop "$b 버전 불일치"
  h="$(timeout 30 sha256sum "$p" | cut -c1-64)" || stop "$b SHA 조회 실패"
  [[ "$h" =~ ^[0-9a-f]{64}$ ]] || stop "$b SHA 해석 실패"
  w "$OUT/12-binaries-sha256.txt" "$h  $b"
  w "$OUT/12-binaries-version.txt" "$b: $v"
done
[ "$(awk '$2=="etcd"{print $1}' "$OUT/12-binaries-sha256.txt")" = "$PROD_EXE_SHA" ] || stop "운영 exe 와 컨테이너 etcd 불일치"
w "$S" "prod_exe_sha256=$PROD_EXE_SHA etcd_container_binary_matches_exe=yes"
timeout 10 ss -ltn 2> "$OUT/ss-pre.stderr" | awk '$4 ~ /:(2379|2380|2381)$/ {print $4}' | LC_ALL=C sort > "$OUT/13-prod-listeners-before.txt" || stop "운영 listener 조회 실패"
etcdctl_prod endpoint health > "$OUT/14-prod-health-before.txt" 2>&1; HRC=$?; w "$OUT/14-prod-health-before.txt" "rc=$HRC"
if [ "$HRC" -ne 0 ]; then PRE_FAIL=1; w "$S" "FAIL: 운영 etcd endpoint health rc=$HRC"; [ "$MODE" = run ] && stop "운영 etcd health 실패(rc=$HRC) - 시험하지 않음"; fi
timeout 10 ss -ltn 2> "$OUT/ss-pre.stderr" | awk -v a=":$CPORT" -v b=":$PPORT" '$4 ~ a"$" || $4 ~ b"$" {print $4}' > "$OUT/15-test-ports-hostns.txt" || stop "시험 listener 조회 실패"
[ ! -s "$OUT/15-test-ports-hostns.txt" ] || stop "시험 포트 $CPORT/$PPORT 를 호스트 namespace 에서 이미 쓰는 중"
# 관측만: 백업 스크립트가 snapshot 을 먼저 저장한 운영 data-dir 안의 사본이 지금 있는지(읽기 전용, 삭제·이동 안 함). 과거 제거 보고가 있어 '남아 있음'으로 단정하지 않는다.
if [ -e /var/lib/etcd/snapshot-sec01.db ]; then w "$OUT/16-prod-datadir-snapshot-copy.txt" "present $(stat -c '%U:%a %s bytes mtime=%y' /var/lib/etcd/snapshot-sec01.db)"; else w "$OUT/16-prod-datadir-snapshot-copy.txt" "absent"; fi
[ ! -e "$WORKROOT" ] || { [ -d "$WORKROOT" ] && [ ! -L "$WORKROOT" ] && [ "$(stat -c %U:%a "$WORKROOT")" = root:700 ] || stop "$WORKROOT 가 디렉터리·root 소유가 아니거나 symlink"; }

[ "$PRE_FAIL" -eq 0 ] || stop "사전 조건 부적합"
observation_ok || stop "사전 점검 상한 도달/기록 실패"
if [ "$MODE" = precheck ]; then
  w "$S" "PRECHECK_RESULT=OK"
  finish 0
fi

# ---------------- run 전제: 봉인된 precheck 폴더와 현재 바이너리 값 대조 ----------------
[ -n "${PRECHECK_DIR:-}" ] && [ -d "$PRECHECK_DIR" ] && [ ! -L "$PRECHECK_DIR" ] || stop "run 은 PRECHECK_DIR=<precheck 출력 폴더> 필요"
timeout 20 python3 "$LIB_DIR/checks.py" verify "$PRECHECK_DIR" > "$OUT/precheck-verify.txt" 2>&1 || stop "precheck 폴더 봉인(SHA256SUMS) 불일치"
grep -q "PRECHECK_RESULT=OK" "$PRECHECK_DIR/99-summary-provisional.txt" 2>/dev/null || stop "precheck 결과가 OK 가 아님"
diff -q "$PRECHECK_DIR/12-binaries-sha256.txt" "$OUT/12-binaries-sha256.txt" >/dev/null 2>&1 || stop "세 바이너리 SHA-256/버전이 precheck 기록과 다름(12-binaries-sha256.txt)"
w "$S" "precheck_dir=$PRECHECK_DIR binaries_match_precheck=yes"

diff -q "$PRECHECK_DIR/source-sha256.txt" "$OUT/source-sha256.txt" >/dev/null || stop "precheck 와 실행 소스가 다름"
grep -q '^EXIT_CODE=0 ' "$PRECHECK_DIR.completion.txt" || stop "precheck completion rc0 미확인"
observation_ok || stop "사전 점검 상한 도달/기록 실패"
phase_begin observation 600

# ---------------- 작업 디렉터리(trap 이후: 부분 생성도 cleanup 이 정리) ----------------
w "$S" "PROVISIONAL=true SEAL_STATUS=PENDING (관측 요약이며 판정이 아님)"
w "$S" "snapshot_sha_matches_evidence=yes prod_etcd_pid=$EPID"
observation_ok || abort "작업 폴더 생성 전 시간/기록 확인 실패"
[ -e "$WORKROOT" ] || mkdir -m 700 "$WORKROOT" || abort "작업 루트를 만들 수 없음"
[ ! -L "$WORKROOT" ] && [ "$(stat -c %U:%a "$WORKROOT")" = root:700 ] || abort "작업 루트 재검증 실패"
# Allocation and identity recording are one short critical section. Signals are
# deferred here; pending signal aborts before snapshot copy/restore.
ALLOC_SIGNAL=0
trap 'ALLOC_SIGNAL=1' INT TERM
mkdir -m 700 "$RUN"; mrc=$?
if [ "$mrc" -eq 0 ]; then RUN_CREATED=1; RUN_IDENTITY="$(stat -c %d:%i "$RUN")"; fi
trap on_signal INT TERM
[ "$ALLOC_SIGNAL" -eq 0 ] && [ "$mrc" -eq 0 ] || abort "작업 디렉터리 생성 실패/중단"
mkdir -m 700 "$BIN" || abort "bin 디렉터리 생성 실패"
w "$OUT/00-environment.txt" "run_dir=$RUN data_dir=$DD"

# ---------------- R0: 작업 사본·도구 복사(읽기 전용)·snapshot status ----------------
# 하나뿐인 backup 원본은 status·restore 에 쓰지 않고, 같은 해시를 확인한 작업 사본(root 700 디렉터리)으로만 한다. 사본은 정리 때 run 디렉터리와 함께 삭제된다.
timeout 60 cp -p -- "$SNAP" "$WSNAP" 2> "$OUT/19-copy-snapshot.stderr" || abort "snapshot 작업 사본 생성 실패"
[ "$(timeout 60 sha256sum "$WSNAP" | cut -c1-64)" = "$SNAP_SHA0" ] || abort "작업 사본 SHA-256 불일치"
w "$S" "working_copy_sha_matches=yes (원본은 status·restore 에 사용하지 않음)"
for b in etcd etcdutl etcdctl; do timeout 30 cp -p "/proc/$EPID/root/usr/local/bin/$b" "$BIN/$b" 2> "$OUT/20-copy-$b.stderr" || abort "$b 복사 실패"; done
chmod 700 "$BIN"/* || abort "복사 바이너리 권한 설정 실패"; ( cd "$BIN" && timeout 30 sha256sum etcd etcdutl etcdctl ) > "$OUT/20-binaries-sha256.txt"
diff -q "$OUT/12-binaries-sha256.txt" "$OUT/20-binaries-sha256.txt" >/dev/null || abort "복사한 세 바이너리 SHA 불일치"
[ "$(sha256sum "$BIN/etcd" | cut -c1-64)" = "$PROD_EXE_SHA" ] && w "$S" "etcd_binary_matches_prod_exe=yes" || abort "복사한 etcd 가 운영 프로세스 실행 파일과 다름"
timeout 10 "$BIN/etcdutl" version > "$OUT/21-etcdutl-version.txt" 2>&1 || abort "복사 etcdutl version 실패"
timeout 10 "$BIN/etcd" --version > "$OUT/21-etcd-version.txt" 2>&1 || abort "복사 etcd version 실패"
timeout 60 "$BIN/etcdutl" snapshot status "$WSNAP" -w table > "$OUT/22-orig-status.txt" 2> "$OUT/22-orig-status.stderr"; SRC=$?
O_REV="$(tbl_val "$OUT/22-orig-status.txt" REVISION)"; O_KEYS="$(tbl_val "$OUT/22-orig-status.txt" "TOTAL KEYS")"; O_HASH="$(tbl_val "$OUT/22-orig-status.txt" HASH)"
w "$S" "orig_status rc=$SRC revision=${O_REV:-UNPARSED} total_keys=${O_KEYS:-UNPARSED} hash=${O_HASH:-UNPARSED} (evidence#99: revision=$EXPECT_REV total_keys=$EXPECT_KEYS) matches_evidence=$([ "$O_REV" = "$EXPECT_REV" ] && [ "$O_KEYS" = "$EXPECT_KEYS" ] && echo yes || echo NO)"
{ [ "$SRC" -eq 0 ] && [ "$O_REV" = "$EXPECT_REV" ] && [ "$O_KEYS" = "$EXPECT_KEYS" ]; } || abort "원본 snapshot status 를 읽지 못함(rc=$SRC, 22-orig-status.*)"

# ---------------- R1: 복원(새 cluster identity, 운영과 다른 경로) ----------------
NAME="sec01-restore"; TOKEN="sec01-restore-$RUN_ID"; PURL="http://127.0.0.1:$PPORT"; CURL="http://127.0.0.1:$CPORT"
case "$DD" in "$WORKROOT"/*) ;; *) abort "data-dir 가 작업 루트 아래가 아님";; esac
[ "$DD" != "/var/lib/etcd" ] || abort "data-dir 가 운영 경로"
observation_ok || abort "복원 전 시간/기록 확인 실패"
timeout 120 "$BIN/etcdutl" snapshot restore "$WSNAP" --data-dir "$DD" --name "$NAME" --initial-cluster "$NAME=$PURL" --initial-cluster-token "$TOKEN" --initial-advertise-peer-urls "$PURL" > "$OUT/30-restore.stdout" 2> "$OUT/30-restore.stderr"; RRC=$?
w "$OUT/30-restore.rc" "$RRC"; w "$S" "R1_restore_rc=$RRC"
[ "$RRC" -eq 0 ] || abort "snapshot restore 실패(rc=$RRC)"
observation_ok || abort "임시 etcd 기동 전 시간/기록 확인 실패"

# ---------------- R2·R3: 격리 namespace 안에서 임시 etcd 기동·관측 ----------------
export BIN NAME DD CURL PURL TOKEN OUT LIB_DIR
timeout "$INNER_LIMIT" unshare --net --pid --fork --kill-child bash "$LIB_DIR/backup-inner.sh" > "$OUT/40-inner.stdout" 2> "$OUT/40-inner.stderr"; IRC=$?
w "$OUT/40-inner.rc" "$IRC"; w "$S" "R2_inner_rc=$IRC $(tr '\n' ' ' < "$OUT/41-temp-health.rc" 2>/dev/null)"
[ "$IRC" -eq 0 ] || abort "임시 etcd 구간이 비정상 종료(rc=$IRC, 40-inner.*)"
grep -q "temp_etcd_health_ok=1" "$OUT/41-temp-health.rc" 2>/dev/null || abort "임시 etcd 가 건강 상태가 되지 않음(40-etcd.stderr 참조)"

# 필수 결과를 읽지 못하면 계속하지 않고 명시적으로 중단한다
T_REV="$(timeout 10 python3 "$LIB_DIR/checks.py" revision < "$OUT/42-temp-status.json")" || abort "revision 해석 실패"
[ "$T_REV" = "$O_REV" ] || abort "임시 etcd header revision 을 읽지 못함(42-temp-status.json)"
w "$S" "R3_temp_etcd_header_revision=$T_REV equals_orig_revision=$([ "$T_REV" = "$O_REV" ] && echo yes || echo NO)"
LIVE="$(timeout 10 python3 "$LIB_DIR/checks.py" count < "$OUT/43-temp-livekeys.txt")" || abort "live key 수를 읽지 못함(43-temp-livekeys.txt)"
w "$S" "R3_live_key_count(info, TOTAL KEYS 와 다른 측정)=$LIVE"
SEC="$(cat "$OUT/44-secret-prefix-counts.txt" 2>/dev/null | tr '\n' ' ')"
case "$SEC" in *"total="*) ;; *) abort "Secret 접두어 집계를 읽지 못함(44-*): [$SEC]";; esac
w "$S" "R3_$SEC"
# 복원된 db 의 status(etcd 종료 후, 같은 측정 방식으로 원본과 비교)
timeout 60 "$BIN/etcdutl" snapshot status "$DD/member/snap/db" -w table > "$OUT/46-restored-status.txt" 2> "$OUT/46-restored-status.stderr"; RSRC=$?
R_REV="$(tbl_val "$OUT/46-restored-status.txt" REVISION)"; R_KEYS="$(tbl_val "$OUT/46-restored-status.txt" "TOTAL KEYS")"; R_HASH="$(tbl_val "$OUT/46-restored-status.txt" HASH)"
{ [ "$RSRC" -eq 0 ] && [ "$R_REV" = "$O_REV" ] && [ "$R_KEYS" = "$O_KEYS" ]; } || abort "복원 db status 를 읽지 못함(rc=$RSRC, 46-restored-status.*)"
w "$S" "R3_restored_db_status revision=$R_REV total_keys=$R_KEYS hash=${R_HASH:-UNPARSED} revision_equals_orig=$([ "$R_REV" = "$O_REV" ] && echo yes || echo NO) total_keys_equals_orig=$([ "$R_KEYS" = "$O_KEYS" ] && echo yes || echo NO) (hash 는 복원 과정에서 달라질 수 있어 비교하지 않고 기록만 함)"
budget_ok || abort "관측 상한 도달"
w "$S" "ALL_STAGES_OBSERVED=yes (관측 완료이며 AC·backup 판정이 아님)"

finish 0
