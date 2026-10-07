#!/usr/bin/env bash
# Edge Cutover(#19) — edge01 nginx upstream `transaction_api`를 옛 E-K3S(10.1.93.50:30080)에서
# 정본 Worker NodePort(10.1.93.105/.107/.108:30080)로 바꾼다. upstream 줄 외에는 바꾸지 않는다.
# 사용: sudo -v 후 `bash edge01-cutover.sh <mode> <RUN>`   (RUN 예: 20261010T1000)
#   check    : 설정 변경 없음. upstream 블록·설정 파일 SHA, reload 반영, 경로 응답, /metrics, E-K3S 상태.
#              관측 방법 사전 검증용 표식 요청을 보낸다(edge 경유 1건 → E-K3S 로그에서 확인,
#              Worker 3대 직접 각 1건 → control01 `trace <RUN> check`로 확인). GET /readyz만 보낸다.
#   apply    : 사전 게이트 → 설정 사본 → upstream 줄 전환(.50 주석, Worker 3줄 주석 해제) → nginx -t → reload
#              → 반영 확인 → 표식 요청 9회 + 표식 smoke 1건 → E-K3S 로그에 표식 0건 확인.
#              실제 새 upstream 경유는 control01 `trace <RUN> apply`로 표식 10개가 K8s Pod 로그에 각각 1번씩
#              찍혔는지 확인해야 C1 완료다(이 스크립트의 OK는 edge 단계 완료).
#   rollback : apply 직전 사본 복원 → nginx -t → reload → 반영 확인 → 표식 요청 3회 + 표식 smoke 1건이
#              E-K3S Pod 로그에 각각 1번씩 찍혔는지 확인(옛 경로 경유의 직접 근거).
# 표식: 요청 쿼리 `cutover=<RUN>-<mode>-<번호>`. transaction-api는 모르는 쿼리를 무시하고, uvicorn 접근 로그에
#   경로·쿼리·client 주소가 남는다. 표식은 RUN·mode·번호로 정해져 다른 요청과 섞이지 않는다.
# 종료 코드: 0=완료(RESULT …=OK), 3=STOP(실패·조건 불충족), 4=UNCONFIRMED(변경은 됐으나 증명 불완전 —
#   완료로 기록하지 않고 Review Owner·실행자가 복구/수용을 판단). 미확정을 OK로 내보내지 않는다.
# E-K3S(k3s)는 이 스크립트에서 바꾸지 않는다(get·logs 조회만. 중지는 #47 창 edge01-k3s.sh 범위).
# 출력은 ~/edge-cutover-<RUN>/에 남는다(원본 로그는 회차별 새 파일). set -e는 쓰지 않는다.
set -uo pipefail

MODE=${1:-}
RUN=${2:-}
case "$RUN" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9]) ;;
  *) echo "usage: bash $0 check|apply|rollback <RUN=yyyymmddTHHMM>" >&2; exit 2 ;;
esac

UPSTREAM=transaction_api
NEW_WORKERS=(10.1.93.105 10.1.93.107 10.1.93.108)
EDGE=http://10.1.93.50
K3S=/usr/local/bin/k3s
LOGDIR=$HOME/edge-cutover-$RUN
BEFORE=$LOGDIR/upstream-file.before
AFTER=$LOGDIR/upstream-file.after
META=$LOGDIR/upstream-file.path
SIM_RUN=${RUN/T/}
STAMP=$(date +%H%M%S)

test "$(hostname -s)" = edge01 || { echo "STOP: edge01이 아님" >&2; exit 2; }
mkdir -p "$LOGDIR" && chmod 700 "$LOGDIR" || { echo "STOP: $LOGDIR 생성 실패" >&2; exit 2; }
LOG=$LOGDIR/edge01-cutover-$MODE-$STAMP.log
exec > >(tee -a "$LOG") 2>&1

die() { echo "STOP: $*"; exit 3; }
unconfirmed() { echo "UNCONFIRMED: $*"; echo "RESULT cutover-$MODE=UNCONFIRMED $(date '+%T') — 완료로 기록하지 않는다"; exit 4; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1"; }
nginx_ok() { test "$(sudo nginx -t 2>&1 | grep -c 'test is successful')" = 1; }
# `upstream transaction_api {`를 정의한 설정 파일. nginx -T의 "# configuration file X:" 머리줄 기준.
upstream_files() {
  sudo nginx -T 2>/dev/null | awk -v u="$UPSTREAM" '
    /^# configuration file .*:$/ { f = substr($0, 22, length($0) - 22); next }
    { line = $0; sub(/#.*/, "", line) }
    line ~ ("^[[:space:]]*upstream[[:space:]]+" u "[[:space:]]*[{]") { print f }' | sort -u
}
# 유효(주석 제외) upstream 블록의 server 목록(디스크 설정 기준)
active_servers() {
  sudo nginx -T 2>/dev/null | sed -e 's/#.*$//' | awk -v u="$UPSTREAM" '
    $0 ~ ("^[[:space:]]*upstream[[:space:]]+" u "[[:space:]]*[{]") { inb = 1; next }
    inb && /^[[:space:]]*}/ { inb = 0 }
    inb && $1 == "server" { print $2 }' | tr -d ';' | sort
}
# 디스크 설정이 실행 중 nginx에 반영됐는지. reload 뒤 정리 중인 옛 worker("shutting down")는 빼고,
# 현재 worker가 모두 설정 파일들의 최종 수정 시각 이후에 시작됐으면 0. 1=아직 옛 worker, 2=판단 불가.
reloaded_after_conf() {
  local master newest wstart pid t args n_old=0 n_cur=0
  master=$(pgrep -o -x nginx) || return 2
  newest=$(sudo nginx -T 2>/dev/null | sed -n 's/^# configuration file \(.*\):$/\1/p' | while IFS= read -r f; do sudo stat -c %Y "$f"; done | sort -n | tail -1)
  wstart=
  for pid in $(pgrep -P "$master" -x nginx); do
    args=$(ps -o args= -p "$pid") || return 2
    case "$args" in *"shutting down"*) n_old=$((n_old + 1)); continue ;; esac
    case "$args" in *"worker process"*) ;; *) continue ;; esac
    t=$(date -d "$(ps -o lstart= -p "$pid")" +%s) || return 2
    n_cur=$((n_cur + 1))
    if test -z "$wstart" || test "$t" -lt "$wstart"; then wstart=$t; fi
  done
  test -n "$newest" && test -n "$wstart" || return 2
  echo "   설정 최종 수정=$(date -d "@$newest" '+%F %T') 현재 worker ${n_cur}개 최초 시작=$(date -d "@$wstart" '+%F %T') 정리 중 옛 worker=${n_old}"
  test "$wstart" -ge "$newest" || return 1
  return 0
}
# reload 직후 판정을 5초 간격으로 최대 7번 본다(마지막 대기 포함 약 35초). 끝까지 0이 아니면 그 rc.
wait_reloaded() {
  local i=0 rc=2
  while test "$i" -lt 7; do
    reloaded_after_conf; rc=$?
    test "$rc" = 0 && return 0
    sleep 5; i=$((i + 1))
  done
  return "$rc"
}

# 표식 요청. 결과 줄 "<표식> <HTTP 코드>"를 nonce 파일에 남긴다.
nonce() { echo "cutover-$RUN-$1-$2"; }
send_nonces() {   # $1=tag $2=개수 $3=기준 URL
  local tag=$1 n=$2 base=$3 i id c file=$LOGDIR/nonces-$1-$STAMP.txt
  : > "$file"
  for i in $(seq 1 "$n"); do
    id=$(nonce "$tag" "$i")
    c=$(code "$base/readyz?cutover=$id")
    echo "$id $c" >> "$file"; echo "   $id http=$c"
    sleep 0.3
  done
  NONCE_FILE=$file
}
# 업무 smoke 1건(#20 최소): 표식 쿼리를 붙여 Pod 로그와 결속한다. 201과 응답 본문을 기록한다.
smoke() {
  local tag=$1 acct="SIM_cut_${SIM_RUN}_$1" id out c
  id=$(nonce "$tag" smoke)
  out=$LOGDIR/smoke-$tag-$STAMP.json
  c=$(curl -s -o "$out" -w '%{http_code}' --max-time 10 -X POST "$EDGE/api/v1/transactions?cutover=$id" \
      -H 'Content-Type: application/json' \
      -d "{\"account_id\":\"$acct\",\"amount\":10000,\"transaction_type\":\"deposit\",\"source_ip\":\"198.51.100.10\"}")
  echo "   smoke $id $acct http=$c body=$(head -c 400 "$out")"
  test "$c" = 201
}

# E-K3S transaction-api Pod 로그를 Pod별로 받아(조회마다 rc·stderr 분리, 회차별 새 파일) 표식 줄을 센다.
# 출력 마지막 줄: TRACE ek3s tag=<tag> pods=<n> err=<조회 실패 수> found=<표식 줄 수> ids=<id:횟수,...>
ek3s_trace() {
  local tag=$1 since=$2 dir=$LOGDIR/ek3s-$1-$(date +%H%M%S) pods p rc errs=0 np=0
  mkdir -p "$dir" && chmod 700 "$dir"
  pods=$(sudo "$K3S" kubectl get pods -A --no-headers 2> "$dir/get-pods.err")
  rc=$?
  echo "$rc" > "$dir/get-pods.rc"
  if test "$rc" -ne 0; then echo "TRACE ek3s tag=$tag pods=0 err=1 found=0 ids="; return 1; fi
  printf '%s\n' "$pods" > "$dir/get-pods.out"
  for p in $(printf '%s\n' "$pods" | awk '$2 ~ /transaction-api/ { print $1 "/" $2 }'); do
    np=$((np + 1))
    sudo "$K3S" kubectl logs -n "${p%%/*}" "${p#*/}" --since="${since}s" > "$dir/${p#*/}.log" 2> "$dir/${p#*/}.err"
    rc=$?
    echo "$rc" > "$dir/${p#*/}.rc"
    test "$rc" -eq 0 || errs=$((errs + 1))
  done
  test "$np" -gt 0 || errs=$((errs + 1))
  python3 -I - "$dir" "cutover-$RUN-$tag-" "$tag" "$np" "$errs" <<'PY'
import glob, os, re, sys
d, prefix, tag, np, errs = sys.argv[1:6]
pat = re.compile(r'(\d+\.\d+\.\d+\.\d+):\d+ - "(?:GET|POST) [^ "?]*\?(?:[^ "]*&)?cutover=' + re.escape(prefix) + r'([A-Za-z0-9]+)[& "]')
ids = {}
found = 0
for f in sorted(glob.glob(os.path.join(d, '*.log'))):
    for line in open(f, errors='replace'):
        m = pat.search(line)
        if m:
            found += 1
            ids[m.group(2)] = ids.get(m.group(2), 0) + 1
            print('   %s %s client=%s' % (os.path.basename(f)[:-4], m.group(2), m.group(1)))
print('TRACE ek3s tag=%s pods=%s err=%s found=%d ids=%s' % (
    tag, np, errs, found, ','.join('%s:%d' % (k, ids[k]) for k in sorted(ids))))
PY
  test "$errs" -eq 0
}
tr_get() { printf '%s\n' "$1" | sed -n "s/^TRACE .* $2=\([^ ]*\).*/\1/p"; }
# ids 문자열이 기대 목록(각 1회)과 정확히 같은지
ids_exact() {   # $1=ids 문자열 $2..=기대 id
  local got=$1 want; shift
  want=$(for i in "$@"; do echo "$i:1"; done | sort | tr '\n' ',' | sed 's/,$//')
  test "$got" = "$want"
}

show() {
  echo "== $(date '+%F %T %Z') edge01 cutover mode=$MODE run=$RUN"
  echo "-- nginx -v: $(sudo nginx -v 2>&1) / unit active=$(systemctl is-active nginx)"
  echo "-- nginx -t: $(sudo nginx -t 2>&1 | tail -1)"
  reloaded_after_conf; echo "-- reload 반영 판정 rc=$? (0=반영, 1=아직 옛 worker, 2=판단 불가)"
  echo "-- upstream $UPSTREAM 정의 파일: $(upstream_files | tr '\n' ' ')"
  for f in $(upstream_files); do sudo sha256sum "$f"; sudo stat -c '%a %U:%G inode=%i' "$f"; sudo stat -c '%C' "$f" 2>/dev/null || true; done
  echo "-- upstream/server/proxy 관련 줄(주석 포함, nginx -T 줄 번호)"
  sudo nginx -T 2>/dev/null | grep -nE "upstream|^[[:space:]]*#?[[:space:]]*server[[:space:]]+[0-9.]+:[0-9]+|proxy_pass|proxy_set_header|proxy_next_upstream|proxy_connect_timeout|proxy_http_version|keepalive|location|listen" || true
  echo "-- 유효 upstream server(디스크): $(active_servers | tr '\n' ' ')"
  echo "-- 응답: edge .50:80/readyz=$(code $EDGE/readyz) /metrics=$(code $EDGE/metrics) E-K3S .50:30080/readyz=$(code http://10.1.93.50:30080/readyz)"
  for w in "${NEW_WORKERS[@]}"; do echo "   Worker $w:30080/readyz=$(code "http://$w:30080/readyz")"; done
  echo "-- k3s(E-K3S, rollback 경로) active=$(systemctl is-active k3s)"
  echo "-- python3: $(python3 --version 2>&1)"
}

case "$MODE" in
  check)
    sudo -v || die "sudo 인증 실패"
    show
    TAG=check$STAMP
    echo "-- 관측 방법 사전 검증(표식 GET /readyz, tag=$TAG)"
    send_nonces "$TAG" 1 "$EDGE"
    for w in "${NEW_WORKERS[@]}"; do
      id=$(nonce "$TAG" "w${w##*.}")
      echo "   $id direct $w:30080 http=$(code "http://$w:30080/readyz?cutover=$id")"
    done
    sleep 2
    T=$(ek3s_trace "$TAG" 300 | tee /dev/stderr | tail -1)
    echo "-- 기대: edge 경유 $TAG-1이 E-K3S 로그에 1번(현재 upstream이 E-K3S), Worker 직접 3건은 control01 \`trace $RUN $TAG\`에서 w105/w107/w108 각 1번"
    if test "$(tr_get "$T" err)" = 0 && ids_exact "$(tr_get "$T" ids)" 1; then
      echo "RESULT cutover-check=TRACE_READY $(date '+%T') (E-K3S 쪽 표식 관측 가능)"
    else
      echo "RESULT cutover-check=TRACE_NOT_READY $(date '+%T') — E-K3S 로그로 표식을 확인하지 못함. C1 시작 전 원인 확인"
    fi
    ;;

  apply)
    sudo -v || die "sudo 인증 실패"
    show
    test -e "$BEFORE" && die "이미 apply한 RUN($BEFORE 있음). 같은 RUN으로 재적용하지 않는다"
    command -v python3 >/dev/null || die "python3 없음"
    nginx_ok || die "nginx -t 실패(적용 전)"
    reloaded_after_conf || die "실행 중 nginx가 디스크 설정과 다를 수 있음(reload 반영 판정 실패). 원인 확인 전 바꾸지 않는다"
    test "$(upstream_files | wc -l)" = 1 || die "upstream $UPSTREAM 정의 파일이 1개가 아님"
    F=$(upstream_files)
    test "$(active_servers | tr '\n' ' ')" = "10.1.93.50:30080 " || die "유효 upstream이 예상(10.1.93.50:30080 하나)과 다름"
    test "$(systemctl is-active k3s)" = active || die "E-K3S가 active가 아님 → rollback 경로 없음"
    test "$(code $EDGE/readyz)" = 200 || die "현재 edge 경로 /readyz가 200이 아님"
    for w in "${NEW_WORKERS[@]}"; do
      test "$(code "http://$w:30080/readyz")" = 200 || die "Worker $w:30080/readyz가 200이 아님(local endpoint·허용 정책 확인). 바꾸지 않는다"
    done
    printf '%s\n' "$F" > "$META"
    sudo cat "$F" > "$BEFORE" || die "설정 사본 저장 실패"
    chmod 600 "$BEFORE"
    # upstream 블록 안에서만: .50:30080 줄은 주석, 주석 처리된 Worker 3줄은 주석 해제. 그 밖의 줄은 그대로.
    python3 -I - "$BEFORE" "$AFTER" "$UPSTREAM" "$RUN" <<'PY' || die "새 설정 생성 실패(파일은 바꾸지 않았다)"
import re, sys
src, out, up, run = sys.argv[1:5]
lines = open(src, encoding='utf-8', newline='').read().split('\n')
start = [i for i, l in enumerate(lines) if re.match(r'^\s*upstream\s+%s\s*\{' % re.escape(up), l)]
if not len(start) == 1:
    sys.exit('upstream 시작 줄이 1개가 아님')
s = start[0]
e = next((i for i in range(s + 1, len(lines)) if re.match(r'^\s*\}', lines[i])), None)
if e is None:
    sys.exit('upstream 끝 줄을 찾지 못함')
old_re = re.compile(r'^(\s*)(server\s+10\.1\.93\.50:30080\b[^;]*;.*)$')
new_re = re.compile(r'^(\s*)#\s*(server\s+10\.1\.93\.(105|107|108):30080\b[^;]*;.*)$')
act_re = re.compile(r'^\s*server\s')
olds, news = [], {}
for i in range(s + 1, e):
    # CRLF 파일이면 줄 끝 \r을 떼고 처리한 뒤 다시 붙인다.
    cr = '\r' if lines[i].endswith('\r') else ''
    l = lines[i][:len(lines[i]) - len(cr)]
    m = old_re.match(l)
    if m:
        olds.append(i)
        lines[i] = '%s# %s  # E-K3S, Edge Cutover %s%s' % (m.group(1), m.group(2), run, cr)
        continue
    m = new_re.match(l)
    if m:
        news.setdefault(m.group(3), []).append(i)
        lines[i] = m.group(1) + m.group(2) + cr
        continue
    if act_re.match(l):
        sys.exit('예상 밖 유효 server 줄: %s' % l.strip())
if not len(olds) == 1:
    sys.exit('.50:30080 유효 줄이 1개가 아님')
if not (sorted(news) == ['105', '107', '108'] and all(len(v) == 1 for v in news.values())):
    sys.exit('주석 처리된 Worker 3줄(.105/.107/.108:30080)이 각각 1개씩 있지 않음')
open(out, 'w', encoding='utf-8', newline='').write('\n'.join(lines))
PY
    chmod 600 "$AFTER"
    echo "-- 변경 diff(설정 파일 $F)"
    diff "$BEFORE" "$AFTER"
    test "$(diff "$BEFORE" "$AFTER" | grep -c '^[<>]')" = 8 || die "diff가 4줄 교체(8줄)가 아님. 파일은 아직 바꾸지 않았다"
    # cat >로 덮어써 inode·권한·소유자·SELinux 문맥을 유지한다.
    sudo sh -c 'cat "$1" > "$2"' sh "$AFTER" "$F" || die "설정 쓰기 실패 → rollback"
    if nginx_ok; then :; else
      sudo nginx -t
      sudo sh -c 'cat "$1" > "$2"' sh "$BEFORE" "$F"
      nginx_ok && die "새 설정 nginx -t 실패 → 사본으로 되돌림(reload 안 함)"
      die "새 설정 nginx -t 실패, 되돌린 뒤에도 실패 → 즉시 확인"
    fi
    echo "-- reload $(date '+%F %T')"
    sudo systemctl reload nginx || die "nginx reload 실패 → rollback"
    sleep 2
    test "$(active_servers | tr '\n' ' ')" = "10.1.93.105:30080 10.1.93.107:30080 10.1.93.108:30080 " || die "디스크 유효 upstream이 Worker 3대가 아님 → rollback"
    # ① 설정 반영
    wait_reloaded || unconfirmed "reload 반영을 약 35초 안에 확인하지 못함(정리 중 옛 worker 또는 판단 불가) → rollback 판단"
    echo "-- ① 설정 반영: 확인"
    # ② 표식 요청 9회 + 표식 smoke
    send_nonces apply 9 "$EDGE"
    test "$(grep -c ' 200$' "$NONCE_FILE")" = 9 || die "edge 경유 표식 요청 중 200이 아닌 것이 있음 → rollback 판단"
    echo "-- ② HTTP: 9/9 200 ($NONCE_FILE)"
    smoke apply || die "업무 smoke가 201이 아님 → rollback"
    test "$(code $EDGE/metrics)" = 404 || die "/metrics가 404가 아님 → rollback"
    sleep 2
    # ③ 옛 경로 미경유: E-K3S Pod 로그에 이번 apply 표식이 하나도 없어야 한다(조회 실패는 판정 불가)
    T=$(ek3s_trace apply 600 | tee /dev/stderr | tail -1)
    test "$(tr_get "$T" err)" = 0 || unconfirmed "E-K3S 로그 조회 실패 — 옛 경로 미경유 미확인"
    test "$(tr_get "$T" found)" = 0 || die "apply 표식이 E-K3S 로그에 있음(옛 경로 경유) → rollback"
    echo "-- ③ E-K3S 로그의 apply 표식 0건"
    show
    echo "RESULT cutover-apply=EDGE_OK $(date '+%T') file=$F — ④ control01 \`trace $RUN apply\`가 표식 10개(1~9, smoke) 각 1번을 K8s Pod 로그에서 찾아야 C1 완료"
    ;;

  rollback)
    sudo -v || die "sudo 인증 실패"
    test -s "$BEFORE" && test -s "$META" || die "사본 없음($BEFORE)"
    F=$(cat "$META")
    show
    test "$(systemctl is-active k3s)" = active || die "E-K3S가 active가 아님 — 옛 경로로 복구할 수 없음(#47 W1 이후면 #47 rollback 먼저)"
    sudo sh -c 'cat "$1" > "$2"' sh "$BEFORE" "$F" || die "설정 복원 실패"
    nginx_ok || die "복원 설정 nginx -t 실패 → 즉시 확인"
    echo "-- reload $(date '+%F %T')"
    sudo systemctl reload nginx || die "nginx reload 실패"
    sleep 2
    test "$(active_servers | tr '\n' ' ')" = "10.1.93.50:30080 " || die "복원 후 디스크 유효 upstream이 .50:30080이 아님"
    wait_reloaded || unconfirmed "복원 설정의 reload 반영을 약 35초 안에 확인하지 못함"
    echo "-- ① 설정 반영: 확인"
    TAG=rollback$STAMP
    send_nonces "$TAG" 3 "$EDGE"
    test "$(grep -c ' 200$' "$NONCE_FILE")" = 3 || die "복원 후 edge /readyz 실패 — E-K3S 상태 확인"
    smoke "$TAG" || die "복원 후 업무 smoke가 201이 아님"
    sleep 2
    # ② 옛 경로 경유: 이번 rollback 표식 4개(1~3, smoke)가 E-K3S Pod 로그에 각각 정확히 1번
    T=$(ek3s_trace "$TAG" 600 | tee /dev/stderr | tail -1)
    test "$(tr_get "$T" err)" = 0 || unconfirmed "E-K3S 로그 조회 실패 — 옛 경로 경유 미확인"
    ids_exact "$(tr_get "$T" ids)" 1 2 3 smoke || unconfirmed "rollback 표식이 E-K3S 로그에 각 1번씩 있지 않음(ids=$(tr_get "$T" ids)) — 옛 경로 경유 미확인"
    echo "-- ② E-K3S 로그에서 rollback 표식 1~3·smoke 각 1번 확인"
    show
    echo "RESULT cutover-rollback=OK $(date '+%T') tag=$TAG (control01 \`trace $RUN $TAG\`는 0건이어야 한다 — 보조)"
    ;;

  *)
    echo "usage: bash $0 check|apply|rollback <RUN>" >&2; exit 2 ;;
esac
