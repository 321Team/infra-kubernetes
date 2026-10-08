#!/usr/bin/env bash
# Edge Cutover(#19) 요청줄 캡처 — 앱 접근 로그 대신 backend Pod로 들어가는 HTTP 요청줄에서 표식을 찾는다.
# 사용(root): sudo bash cutover-capture.sh start <RUN> <WIN> <초> | status <RUN> <WIN> | report <RUN> <WIN>
#   WIN : check|apply|rollback(뒤에 숫자 1자리 허용, 예: check2). 같은 RUN·WIN은 다시 시작하지 않는다.
#   start  : tcpdump를 백그라운드로 시작하고 "listening on"을 확인한 뒤 CAPTURE_READY를 출력한다(최대 <초> 뒤 자동 종료).
#   status : RUNNING이면 0, 아니면 1.
#   report : 실행 중이면 멈추고(SIGTERM) 원본 pcap을 읽어 표식 요청마다 HIT 줄, 마지막에 CAPTURE 줄을 출력한다.
# 호스트별 캡처 대상(요청 방향만, TCP 본문이 있는 패킷만):
#   k8s-worker01/02/03 : `tcp dst port 8000 and src host 10.1.93.50` — NodePort DNAT 뒤 Pod로 들어가는 edge01발 요청
#   edge01             : `tcp and dst net 10.42.0.0/16`              — E-K3S Pod(k3s 대역)로 들어가는 요청
# 저장 범위: 패킷당 앞 300바이트(snaplen). 요청줄과 앞쪽 헤더(Host, X-Real-IP)까지이며 업무 본문 전체는 저장하지 않는다.
#   smoke 요청은 본문 일부(SIM 시험값)가 들어갈 수 있다. 응답 방향은 저장하지 않는다.
# 원본: /var/tmp/edge-cutover-capture-<RUN>/<WIN>/ (capture.pcap, tcpdump.err·rc, decoded.txt·rc, report.txt, meta.txt)
# 이 스크립트는 설정을 바꾸지 않는다. set -e는 쓰지 않는다.
set -uo pipefail

MODE=${1:-}
RUN=${2:-}
WIN=${3:-}
SECS=${4:-}
usage() { echo "usage: sudo bash $0 start <RUN> <WIN> <초> | status|report <RUN> <WIN>" >&2; exit 2; }
case "$RUN" in [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9]) ;; *) usage ;; esac
case "$WIN" in check|apply|rollback|check[1-9]|apply[1-9]|rollback[1-9]) ;; *) usage ;; esac
case "$MODE" in
  start) case "$SECS" in ''|*[^0-9]*) usage ;; esac
         test "$SECS" -ge 60 && test "$SECS" -le 3600 || usage ;;
  status|report) ;;
  *) usage ;;
esac
test "$(id -u)" = 0 || { echo "STOP: sudo bash로 실행" >&2; exit 2; }

H=$(hostname -s)
SNAPLEN=300
PAYLOAD='(((ip[2:2] - ((ip[0]&0xf)<<2)) - ((tcp[12]&0xf0)>>2)) != 0)'
case "$H" in
  k8s-worker01|k8s-worker02|k8s-worker03) FILTER="tcp dst port 8000 and src host 10.1.93.50 and $PAYLOAD" ;;
  edge01) FILTER="tcp and dst net 10.42.0.0/16 and $PAYLOAD" ;;
  *) echo "STOP: 캡처 대상 호스트가 아님($H)" >&2; exit 2 ;;
esac
DIR=/var/tmp/edge-cutover-capture-$RUN/$WIN
umask 077

running() {   # tcpdump 종료 코드 파일이 아직 없고 실행 기록 pid가 살아 있으면 실행 중
  test -s "$DIR/ready.ts" && test ! -e "$DIR/tcpdump.rc" && test -s "$DIR/runner.pid" && kill -0 "$(cat "$DIR/runner.pid")" 2>/dev/null
}

case "$MODE" in
  start)
    test -e "$DIR" && { echo "STOP: $DIR 이미 있음 — 같은 RUN·WIN으로 다시 시작하지 않는다(새 WIN 사용)"; exit 3; }
    command -v tcpdump >/dev/null || { echo "STOP: tcpdump 없음"; exit 3; }
    mkdir -p "$DIR" || { echo "STOP: $DIR 생성 실패"; exit 3; }
    {
      echo "host=$H run=$RUN win=$WIN secs=$SECS snaplen=$SNAPLEN"
      echo "filter=$FILTER"
      echo "start=$(date '+%F %T %Z') tcpdump=$(tcpdump --version 2>&1 | head -1)"
    } > "$DIR/meta.txt"
    # -Z root: RHEL tcpdump의 권한 낮추기 때문에 root 전용 폴더에 쓰지 못하는 것을 막는다. -U: 패킷마다 기록.
    # runner(sh)가 자기 pid를 남긴다(setsid가 fork하는 경우에도 실제 pid를 쓰도록).
    setsid sh -c 'echo $$ > "$1/runner.pid"; timeout "$2" tcpdump -i any -nn -s "$3" -U -Z root -w "$1/capture.pcap" "$4" 2> "$1/tcpdump.err"; r=$?; date +%s.%N | cut -c1-14 > "$1/stop.ts"; echo $r > "$1/tcpdump.rc"' \
      sh "$DIR" "$SECS" "$SNAPLEN" "$FILTER" < /dev/null > /dev/null 2>&1 &
    i=0
    while test "$i" -lt 20; do
      if grep -q 'listening on' "$DIR/tcpdump.err" 2>/dev/null; then
        date +%s.%N | cut -c1-14 > "$DIR/ready.ts"
        echo "CAPTURE_READY host=$H run=$RUN win=$WIN ready=$(cat "$DIR/ready.ts") secs=$SECS at=$(date '+%T')"
        exit 0
      fi
      test -e "$DIR/tcpdump.rc" && break
      sleep 0.5; i=$((i + 1))
    done
    test -s "$DIR/runner.pid" && for p in $(pgrep -P "$(cat "$DIR/runner.pid")" -x timeout); do kill -TERM "$p"; done
    echo "CAPTURE_START_FAILED host=$H run=$RUN win=$WIN rc=$(cat "$DIR/tcpdump.rc" 2>/dev/null)"
    cat "$DIR/tcpdump.err" 2>/dev/null
    exit 3
    ;;

  status)
    if running; then echo "CAPTURE_STATUS host=$H run=$RUN win=$WIN state=RUNNING ready=$(cat "$DIR/ready.ts")"; exit 0; fi
    if test -e "$DIR/tcpdump.rc"; then echo "CAPTURE_STATUS host=$H run=$RUN win=$WIN state=STOPPED rc=$(cat "$DIR/tcpdump.rc")"
    else echo "CAPTURE_STATUS host=$H run=$RUN win=$WIN state=ABSENT"; fi
    exit 1
    ;;

  report)
    test -s "$DIR/ready.ts" || { echo "CAPTURE host=$H run=$RUN win=$WIN err=1 reason=not_started"; exit 4; }
    if running; then
      # runner(sh) → timeout → tcpdump. timeout에 SIGTERM을 주면 tcpdump에 전달되고 tcpdump는 통계를 쓰고 끝난다.
      for p in $(pgrep -P "$(cat "$DIR/runner.pid")" -x timeout); do kill -TERM "$p"; done
    fi
    i=0
    while test ! -e "$DIR/tcpdump.rc" && test "$i" -lt 20; do sleep 0.5; i=$((i + 1)); done
    # stop.ts는 runner가 tcpdump 종료 직후 쓴다(자동 종료 뒤 늦게 report해도 실제 종료 시각). 없으면 판정 불가.
    test -e "$DIR/decoded.txt" || {
      tcpdump -nn -S -tt -A -r "$DIR/capture.pcap" > "$DIR/decoded.txt" 2> "$DIR/decoded.err"
      echo $? > "$DIR/decoded.rc"
    }
    python3 -I - "$DIR" "$H" "$RUN" "$WIN" > "$DIR/report.txt" 2> "$DIR/report.err" <<'PY'
import hashlib, os, re, sys
d, host, run, win = sys.argv[1:5]

def read(name, default=''):
    try:
        return open(os.path.join(d, name), errors='replace').read()
    except OSError:
        return default

head = re.compile(r'^(\d+\.\d+) (?:\S+ +)??(?:(?:In|Out|M|P|B) +)?IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): .*?seq (\d+)')
req = re.compile(r'(GET|POST) /[^ ?]*\?(?:[^ ]*&)?cutover=cutover-' + re.escape(run) + r'-([A-Za-z0-9]+)-([A-Za-z0-9]+)(?=[& ])[^ ]* (HTTP/1\.[01])')
hdr = lambda name, text: (re.search(r'(?mi)^' + name + r': *([^\s]+)', text) or [None, '-'])[1]

blocks, cur = [], None
for line in read('decoded.txt').split('\n'):
    m = head.match(line)
    if m:
        cur = [m, []]
        blocks.append(cur)
    elif cur is not None:
        cur[1].append(line)
hits, seen = [], set()
for m, body in blocks:
    text = '\n'.join(body)
    r = req.search(text)
    if not r:
        continue
    t, sip, sport, dip, dport, seq = m.groups()
    key = (sip, sport, dip, dport, seq, r.group(2), r.group(3))
    if key in seen:            # 같은 패킷이 두 인터페이스(브리지·veth)에서 잡히거나 재전송된 경우 1번만 센다
        continue
    seen.add(key)
    hits.append('HIT host=%s run=%s win=%s tag=%s id=%s t=%s src=%s:%s dst=%s:%s seq=%s method=%s proto=%s hdr_host=%s xri=%s' % (
        host, run, win, r.group(2), r.group(3), t, sip, sport, dip, dport, seq, r.group(1), r.group(4),
        hdr('Host', text), hdr('X-Real-IP', text)))

errs = []
rc = read('tcpdump.rc').strip()
if rc not in ('0', '124', '143'):
    errs.append('tcpdump_rc=%s' % (rc or 'none'))
err = read('tcpdump.err')
cap = re.search(r'(\d+) packets? captured', err)
drop = re.search(r'(\d+) packets? dropped by kernel', err)
if not cap or not drop:
    errs.append('no_stats')
elif drop.group(1) != '0':
    errs.append('dropped=%s' % drop.group(1))
if not read('stop.ts').strip():
    errs.append('no_stop_ts')
if read('decoded.rc').strip() != '0':
    errs.append('decode_rc=%s' % (read('decoded.rc').strip() or 'none'))
try:
    sha = hashlib.sha256(open(os.path.join(d, 'capture.pcap'), 'rb').read()).hexdigest()
except OSError:
    sha = 'none'
    errs.append('no_pcap')
for h in hits:
    print(h)
print('CAPTURE host=%s run=%s win=%s err=%d reason=%s ready=%s stop=%s pkts=%s dropped=%s blocks=%d hits=%d pcap_sha256=%s' % (
    host, run, win, len(errs), ','.join(errs) or '-', read('ready.ts').strip(), read('stop.ts').strip(),
    cap.group(1) if cap else '-', drop.group(1) if drop else '-', len(blocks), len(hits), sha))
PY
    PRC=$?
    cat "$DIR/report.txt"
    cat "$DIR/report.err" >&2
    test "$PRC" = 0 || { echo "CAPTURE host=$H run=$RUN win=$WIN err=1 reason=report_rc_$PRC"; exit 4; }
    grep -q '^CAPTURE .* err=0 ' "$DIR/report.txt" || exit 4
    ;;
esac
