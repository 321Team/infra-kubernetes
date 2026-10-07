#!/usr/bin/env bash
# Edge Cutover(#19) — edge01 nginx upstream `transaction_api`를 옛 E-K3S(10.1.93.50:30080)에서
# 정본 Worker NodePort(10.1.93.105/.107/.108:30080)로 바꾼다. upstream 줄 외에는 바꾸지 않는다.
# 사용: sudo -v 후 `bash edge01-cutover.sh <mode> <RUN>`   (RUN 예: 20261010T1000)
#   check    : 변경 없음. upstream 블록·설정 파일 SHA, reload 반영 여부, 경로 응답, /metrics, E-K3S 상태
#   apply    : 사전 게이트 → 설정 사본 → upstream 줄 전환(.50 주석, Worker 3줄 주석 해제) → nginx -t → reload
#              → 새 연결로 적용 확인. nginx -t 실패면 즉시 사본으로 되돌리고 reload하지 않는다.
#   rollback : apply 직전 사본으로 복원 → nginx -t → reload → 옛 경로(E-K3S) 응답 확인
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
LOGDIR=$HOME/edge-cutover-$RUN
BEFORE=$LOGDIR/upstream-file.before
AFTER=$LOGDIR/upstream-file.after
META=$LOGDIR/upstream-file.path

test "$(hostname -s)" = edge01 || { echo "STOP: edge01이 아님" >&2; exit 2; }
mkdir -p "$LOGDIR" && chmod 700 "$LOGDIR" || { echo "STOP: $LOGDIR 생성 실패" >&2; exit 2; }
LOG=$LOGDIR/edge01-cutover-$MODE-$(date +%H%M%S).log
exec > >(tee -a "$LOG") 2>&1

die() { echo "STOP: $*"; exit 3; }
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1"; }
nginx_ok() { test "$(sudo nginx -t 2>&1 | grep -c 'test is successful')" = 1; }
# `upstream transaction_api {`를 정의한 설정 파일. nginx -T의 "# configuration file X:" 머리줄 기준.
upstream_files() {
  sudo nginx -T 2>/dev/null | awk -v u="$UPSTREAM" '
    /^# configuration file .*:$/ { f = substr($0, 22, length($0) - 22); next }
    { line = $0; sub(/#.*/, "", line) }
    line ~ ("^[[:space:]]*upstream[[:space:]]+" u "[[:space:]]*[{]") { print f }' | sort -u
}
# 유효(주석 제외) upstream 블록의 server 목록
active_servers() {
  sudo nginx -T 2>/dev/null | sed -e 's/#.*$//' | awk -v u="$UPSTREAM" '
    $0 ~ ("^[[:space:]]*upstream[[:space:]]+" u "[[:space:]]*[{]") { inb = 1; next }
    inb && /^[[:space:]]*}/ { inb = 0 }
    inb && $1 == "server" { print $2 }' | tr -d ';' | sort
}
# 디스크 설정이 실행 중 nginx에 반영됐는지(edge01-k3s.sh와 같은 판정). 0=반영 1=reload 뒤 설정 변경 2=판단 불가
reloaded_after_conf() {
  local master newest wstart pid t
  master=$(pgrep -o -x nginx) || return 2
  newest=$(sudo nginx -T 2>/dev/null | sed -n 's/^# configuration file \(.*\):$/\1/p' | while IFS= read -r f; do sudo stat -c %Y "$f"; done | sort -n | tail -1)
  wstart=
  for pid in $(pgrep -P "$master" -x nginx); do
    t=$(date -d "$(ps -o lstart= -p "$pid")" +%s) || return 2
    if test -z "$wstart" || test "$t" -lt "$wstart"; then wstart=$t; fi
  done
  test -n "$newest" && test -n "$wstart" || return 2
  echo "   nginx 설정 최종 수정=$(date -d "@$newest" '+%F %T') worker 최초 시작=$(date -d "@$wstart" '+%F %T')"
  test "$wstart" -ge "$newest" || return 1
  return 0
}
# :30080으로 나간 소켓(상태 무관)을 목적지별로 센다. ss 실패는 ERR.
sock30080() {
  local out
  out=$(sudo ss -Htan '( dport = :30080 )') || { echo ERR; return 1; }
  printf '%s\n' "$out" | awk 'NF { split($5, a, ":"); n[a[1]]++ } END { for (k in n) printf "%s=%d ", k, n[k]; print "" }'
}

show() {
  echo "== $(date '+%F %T %Z') edge01 cutover mode=$MODE run=$RUN"
  echo "-- nginx -v: $(sudo nginx -v 2>&1) / unit active=$(systemctl is-active nginx)"
  echo "-- nginx -t: $(sudo nginx -t 2>&1 | tail -1)"
  reloaded_after_conf; echo "-- reload 반영 판정 rc=$? (0=반영, 1=reload 뒤 설정 변경, 2=판단 불가)"
  echo "-- upstream $UPSTREAM 정의 파일: $(upstream_files | tr '\n' ' ')"
  for f in $(upstream_files); do sudo sha256sum "$f"; sudo stat -c '%a %U:%G inode=%i' "$f"; sudo stat -c '%C' "$f" 2>/dev/null || true; done
  echo "-- upstream/server/proxy 관련 줄(주석 포함, nginx -T 줄 번호)"
  sudo nginx -T 2>/dev/null | grep -nE "upstream|^[[:space:]]*#?[[:space:]]*server[[:space:]]+[0-9.]+:[0-9]+|proxy_pass|proxy_set_header|proxy_next_upstream|proxy_connect_timeout|keepalive|location|listen" || true
  echo "-- 유효 upstream server: $(active_servers | tr '\n' ' ')"
  echo "-- 응답: edge .50:80/readyz=$(code $EDGE/readyz) /metrics=$(code $EDGE/metrics) E-K3S .50:30080/readyz=$(code http://10.1.93.50:30080/readyz)"
  for w in "${NEW_WORKERS[@]}"; do echo "   Worker $w:30080/readyz=$(code "http://$w:30080/readyz")"; done
  echo "-- :30080 소켓(목적지별): $(sock30080)"
  echo "-- k3s(E-K3S, rollback 경로) active=$(systemctl is-active k3s)"
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
    T_RELOAD=$(date '+%F %T')
    sudo systemctl reload nginx || die "nginx reload 실패 → rollback"
    sleep 2
    echo "-- reload $T_RELOAD"
    reloaded_after_conf || die "reload 반영 판정 실패 → rollback 판단"
    test "$(active_servers | tr '\n' ' ')" = "10.1.93.105:30080 10.1.93.107:30080 10.1.93.108:30080 " || die "유효 upstream이 Worker 3대가 아님 → rollback"
    # 새 연결로 확인: 9회 호출 모두 200, :30080 소켓 목적지에 Worker가 나타나야 한다.
    FAIL=0
    for i in 1 2 3 4 5 6 7 8 9; do c=$(code $EDGE/readyz); echo "   readyz[$i]=$c"; test "$c" = 200 || FAIL=$((FAIL + 1)); done
    S=$(sock30080); echo "-- :30080 소켓(목적지별): $S"
    test "$FAIL" = 0 || die "edge 경유 /readyz 실패 $FAIL/9 → rollback 판단"
    for w in "${NEW_WORKERS[@]}"; do
      case "$S" in *"$w="*) ;; *) echo "   참고: $w로 간 소켓이 이번 관측에 없음(분배·TIME_WAIT 정리 시점 차이일 수 있음)";; esac
    done
    test "$(code $EDGE/metrics)" = 404 || die "/metrics가 404가 아님 → rollback"
    show
    echo "RESULT cutover-apply=OK $(date '+%T') file=$F"
    ;;

  rollback)
    sudo -v || die "sudo 인증 실패"
    test -s "$BEFORE" && test -s "$META" || die "사본 없음($BEFORE)"
    F=$(cat "$META")
    show
    sudo sh -c 'cat "$1" > "$2"' sh "$BEFORE" "$F" || die "설정 복원 실패"
    nginx_ok || die "복원 설정 nginx -t 실패 → 즉시 확인"
    sudo systemctl reload nginx || die "nginx reload 실패"
    sleep 2
    reloaded_after_conf || echo "   참고: reload 반영 판정 실패"
    test "$(active_servers | tr '\n' ' ')" = "10.1.93.50:30080 " || die "복원 후 유효 upstream이 .50:30080이 아님"
    for i in 1 2 3; do echo "   readyz[$i]=$(code $EDGE/readyz)"; done
    test "$(code $EDGE/readyz)" = 200 || die "복원 후 edge /readyz가 200이 아님(E-K3S 상태 확인)"
    show
    echo "RESULT cutover-rollback=OK $(date '+%T')"
    ;;

  *)
    echo "usage: bash $0 check|apply|rollback <RUN>" >&2; exit 2 ;;
esac
