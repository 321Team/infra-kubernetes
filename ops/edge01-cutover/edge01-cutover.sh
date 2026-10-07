#!/usr/bin/env bash
# Edge Cutover(#19) — edge01 nginx upstream `transaction_api`를 옛 E-K3S(10.1.93.50:30080)에서
# 정본 Worker NodePort(10.1.93.105/.107/.108:30080)로 바꾼다. upstream 줄 외에는 바꾸지 않는다.
# 사용: sudo -v 후 `bash edge01-cutover.sh <mode> <RUN>`   (RUN 예: 20261010T1000)
#   check    : 변경 없음. upstream 블록·설정 파일 SHA, reload 반영 여부, 경로 응답, /metrics, E-K3S 상태, 도구 유무
#   apply    : 사전 게이트 → 설정 사본 → upstream 줄 전환(.50 주석, Worker 3줄 주석 해제) → nginx -t → reload
#              → 반영 확인 → 새 요청 9회의 실제 upstream 목적지 관측 → /metrics 404 → 업무 smoke 1건
#   rollback : apply 직전 사본 복원 → nginx -t → reload → 반영 확인 → 새 요청이 옛 경로로 가는지 관측 → smoke 1건
# 종료 코드: 0=완료(RESULT …=OK), 3=STOP(실패·조건 불충족), 4=UNCONFIRMED(변경은 됐으나 증명 불완전 —
#   완료로 기록하지 않고 Review Owner·실행자가 복구/수용을 판단). 미확정을 OK로 내보내지 않는다.
# 경로 관측(두 가지를 함께 본다):
#   - 요청 구간에만 edge01에서 나가는 TCP SYN 헤더를 tcpdump로 짧게 받아 Worker :30080으로 간 새 연결 수를 센다
#     (payload 없음, 파일은 로그 폴더 600). nginx 기본(upstream keepalive 없음)은 요청마다 새 upstream 연결을 연다.
#     E-K3S Pod 대역 SYN은 kubelet probe와 섞이므로 판정에 쓰지 않고 기록만 한다.
#   - 업무 smoke(POST)가 E-K3S transaction-api Pod 로그에 찍혔는지 본다(kubelet probe는 POST를 하지 않는다).
# E-K3S(k3s)는 이 스크립트에서 건드리지 않는다(중지는 #47 창 edge01-k3s.sh 범위, rollback 경로로 유지).
# 출력은 ~/edge-cutover-<RUN>/에 남는다. set -e는 쓰지 않는다.
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

test "$(hostname -s)" = edge01 || { echo "STOP: edge01이 아님" >&2; exit 2; }
mkdir -p "$LOGDIR" && chmod 700 "$LOGDIR" || { echo "STOP: $LOGDIR 생성 실패" >&2; exit 2; }
LOG=$LOGDIR/edge01-cutover-$MODE-$(date +%H%M%S).log
exec > >(tee -a "$LOG") 2>&1

die() { echo "STOP: $*"; exit 3; }
unconfirmed() { echo "UNCONFIRMED: $*"; echo "RESULT cutover-$MODE=UNCONFIRMED $(date '+%T') — 완료로 기록하지 않는다"; exit 4; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1"; }
nginx_ok() { test "$(sudo nginx -t 2>&1 | grep -c 'test is successful')" = 1; }
have_tcpdump() { sudo sh -c 'command -v tcpdump' >/dev/null 2>&1; }
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
# reload 직후 판정은 최대 30초(5초 간격) 다시 본다. 끝까지 0이 아니면 그 rc를 돌려준다.
wait_reloaded() {
  local i=0 rc=2
  while test "$i" -lt 7; do
    reloaded_after_conf; rc=$?
    test "$rc" = 0 && return 0
    sleep 5; i=$((i + 1))
  done
  return "$rc"
}
# E-K3S podCIDR(옛 경로 판정용). k3s가 꺼져 있거나 못 읽으면 k3s 기본값.
k3s_podcidr() {
  local c
  c=$(sudo "$K3S" kubectl get nodes -o jsonpath='{.items[0].spec.podCIDR}' 2>/dev/null)
  echo "${c:-10.42.0.0/16}"
}

# 요청 n회를 보내는 동안 나가는 SYN 헤더를 받아 목적지를 분류한다.
# 출력 마지막 줄: OBS new=<Worker:30080 SYN 수> ek3s=<E-K3S 대역 SYN 수(기록만)> other=<n> http_ok=<n>/<N> capture=<ok|fail>
observe() {
  local n=$1 cap=$LOGDIR/syn-$MODE-$(date +%H%M%S).txt err pid i c ok=0 cidr
  err=$cap.err
  cidr=$(k3s_podcidr)
  ( umask 077; sudo timeout 60 tcpdump -nn -l -i any 'tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn' > "$cap" 2> "$err" ) &
  pid=$!
  sleep 2
  for i in $(seq 1 "$n"); do
    c=$(code "$EDGE/readyz"); echo "   readyz[$i]=$c"
    test "$c" = 200 && ok=$((ok + 1))
    sleep 0.3
  done
  sleep 1
  sudo pkill -INT -f "^timeout 60 tcpdump -nn -l -i any" 2>/dev/null
  wait "$pid" 2>/dev/null
  echo "-- 캡처 파일 $cap ($(wc -l < "$cap")줄), tcpdump stderr: $(tr '\n' ' ' < "$err")"
  python3 -I - "$cap" "$err" "$cidr" "$ok" "$n" "${NEW_WORKERS[@]}" <<'PY'
import ipaddress, re, sys
cap, err, cidr, ok, n = sys.argv[1:6]
workers = set(sys.argv[6:])
net = ipaddress.ip_network(cidr, strict=False)
captured = 'listening on' in open(err, errors='replace').read()
pat = re.compile(r' IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): Flags \[S\]')
new = old = other = 0
dst = {}
for line in open(cap, errors='replace'):
    m = pat.search(line)
    if not m:
        continue
    ip, port = m.group(3), m.group(4)
    key = ip + ':' + port
    dst[key] = dst.get(key, 0) + 1
    if ip in workers and port == '30080':
        new += 1
    elif (ip == '10.1.93.50' and port == '30080') or ipaddress.ip_address(ip) in net:
        old += 1   # E-K3S 대역: kubelet probe 포함, 판정에 쓰지 않음
    else:
        other += 1
for k in sorted(dst):
    print('   SYN dst %s x%d' % (k, dst[k]))
print('   E-K3S podCIDR(기록만, kubelet probe 포함) %s' % cidr)
print('OBS new=%d ek3s=%d other=%d http_ok=%s/%s capture=%s' % (new, old, other, ok, n, 'ok' if captured else 'fail'))
PY
}
obs_get() { printf '%s\n' "$1" | sed -n "s/^OBS .*$2=\([^ ]*\).*/\1/p"; }

# 업무 smoke 1건(#20 최소): 201과 응답 본문을 기록한다. DB 행은 README대로 db01에서 확인한다.
smoke() {
  local acct="SIM_cut_${SIM_RUN}_$1" out c
  out=$LOGDIR/smoke-$1.json
  c=$(curl -s -o "$out" -w '%{http_code}' --max-time 10 -X POST "$EDGE/api/v1/transactions" \
      -H 'Content-Type: application/json' \
      -d "{\"account_id\":\"$acct\",\"amount\":10000,\"transaction_type\":\"deposit\",\"source_ip\":\"198.51.100.10\"}")
  echo "   smoke $acct http=$c body=$(head -c 400 "$out")"
  test "$c" = 201
}

# E-K3S transaction-api Pod 로그에서 최근 N초 안의 smoke POST 줄 수. 조회 실패는 ERR.
ek3s_posts() {
  local since=$1 pods p n=0 c
  pods=$(sudo "$K3S" kubectl get pods -A --no-headers 2>/dev/null | awk '$2 ~ /transaction-api/ { print $1 "/" $2 }') || { echo ERR; return; }
  test -n "$pods" || { echo ERR; return; }
  for p in $pods; do
    c=$(sudo "$K3S" kubectl logs -n "${p%%/*}" "${p#*/}" --since="${since}s" 2>/dev/null | grep -c 'POST /api/v1/transactions') || true
    n=$((n + ${c:-0}))
  done
  echo "$n"
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
  echo "-- k3s(E-K3S, rollback 경로) active=$(systemctl is-active k3s) podCIDR=$(k3s_podcidr)"
  if have_tcpdump; then echo "-- tcpdump: $(sudo tcpdump --version 2>&1 | head -1)"; else echo "-- tcpdump: 없음(새 경로 관측 불가 → apply는 UNCONFIRMED)"; fi
  echo "-- python3: $(python3 --version 2>&1)"
}

case "$MODE" in
  check)
    sudo -v || die "sudo 인증 실패"
    show
    ;;

  apply)
    sudo -v || die "sudo 인증 실패"
    show
    test -e "$BEFORE" && die "이미 apply한 RUN($BEFORE 있음). 같은 RUN으로 재적용하지 않는다"
    command -v python3 >/dev/null || die "python3 없음"
    have_tcpdump || die "tcpdump 없음 — 새 경로 관측을 못 하므로 바꾸지 않는다(대체 관측은 GO 때 별도 결정)"
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
    wait_reloaded || unconfirmed "reload 반영을 30초 안에 확인하지 못함(정리 중 옛 worker 또는 판단 불가) → rollback 판단"
    echo "-- ① 설정 반영: 확인"
    # ② 새 요청 9회와 실제 upstream 목적지
    OBS=$(observe 9 | tee /dev/stderr | tail -1)
    test "$(obs_get "$OBS" http_ok)" = 9/9 || die "edge 경유 /readyz 실패($(obs_get "$OBS" http_ok)) → rollback 판단"
    echo "-- ② HTTP: 9/9 200"
    test "$(obs_get "$OBS" capture)" = ok || unconfirmed "SYN 캡처 실패 — 실제 upstream 경유 미확인 → rollback 판단"
    test "$(obs_get "$OBS" new)" -ge 9 || unconfirmed "요청 9회에 대한 Worker :30080 새 연결이 $(obs_get "$OBS" new)건뿐(keepalive 재사용 등) → rollback 판단"
    echo "-- ③ 실제 upstream 경유: Worker :30080 새 연결 $(obs_get "$OBS" new)건(요청 9회 이상)"
    test "$(code $EDGE/metrics)" = 404 || die "/metrics가 404가 아님 → rollback"
    smoke apply || die "업무 smoke가 201이 아님 → rollback"
    sleep 2
    P=$(ek3s_posts 60)
    echo "-- ④ E-K3S Pod 로그의 최근 60초 smoke POST=$P (기대 0, ERR은 판정 불가로 기록만)"
    test "$P" = ERR || test "$P" = 0 || die "smoke가 E-K3S로 감(POST $P건) → rollback"
    show
    echo "RESULT cutover-apply=OK $(date '+%T') file=$F (DB 행·P2~P4·N1은 README C2)"
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
    wait_reloaded || unconfirmed "복원 설정의 reload 반영을 30초 안에 확인하지 못함"
    echo "-- ① 설정 반영: 확인"
    if have_tcpdump; then
      OBS=$(observe 3 | tee /dev/stderr | tail -1)
      test "$(obs_get "$OBS" http_ok)" = 3/3 || die "복원 후 edge /readyz 실패($(obs_get "$OBS" http_ok)) — E-K3S 상태 확인"
      test "$(obs_get "$OBS" capture)" = ok || unconfirmed "SYN 캡처 실패 — 옛 경로 경유 미확인"
      test "$(obs_get "$OBS" new)" = 0 || unconfirmed "복원 뒤에도 Worker :30080으로 새 연결(new=$(obs_get "$OBS" new))"
      echo "-- ② Worker :30080 새 연결 0건"
    else
      unconfirmed "tcpdump 없음 — Worker 경로 미사용 미확인"
    fi
    smoke rollback || die "복원 후 업무 smoke가 201이 아님"
    sleep 2
    P=$(ek3s_posts 60)
    echo "-- ③ E-K3S Pod 로그의 최근 60초 smoke POST=$P (기대 1 이상)"
    test "$P" = ERR && unconfirmed "E-K3S Pod 로그 조회 실패 — 옛 경로 경유 미확인"
    test "$P" -ge 1 || unconfirmed "smoke가 E-K3S Pod 로그에 보이지 않음 — 옛 경로 경유 미확인"
    show
    echo "RESULT cutover-rollback=OK $(date '+%T')"
    ;;

  *)
    echo "usage: bash $0 check|apply|rollback <RUN>" >&2; exit 2 ;;
esac
