#!/usr/bin/env bash
# Edge Cutover(#19) 요청줄 캡처 — 앱 접근 로그 대신 backend Pod로 들어가는 HTTP 요청줄에서 표식을 찾는다.
# 사용(root): sudo bash cutover-capture.sh start <RUN> <WIN> <초> | status <RUN> <WIN> | report <RUN> <WIN>
#   WIN : check|apply|rollback(뒤에 숫자 1자리 허용, 예: check2). 같은 RUN·WIN은 다시 시작하지 않는다.
#   start  : tcpdump를 백그라운드로 시작하고 "listening on"을 확인한 뒤 CAPTURE_READY를 출력한다(최대 <초> 뒤 자동 종료).
#   status : RUNNING이면 0, 아니면 1.
#   report : 실행 중이면 멈추고(SIGTERM) 표식 요청마다 HIT 줄, 마지막에 CAPTURE 줄을 출력한다.
# 캡처 범위(저장 전에 커널 필터로 제한):
#   공통   : 요청 방향의 첫 세그먼트만 — TCP 본문이 `GET ` 또는 `POST `로 시작하고 목적지 포트가 앱 포트 8000.
#            응답(HTTP/…로 시작), DB 등 다른 포트, 다른 Pod의 트래픽은 들어오지 않는다.
#   k8s-worker01/02/03 : 그리고 출발지 10.1.93.50(edge01) — NodePort DNAT 뒤 그 노드 transaction-api Pod로 들어가는 요청
#   edge01             : 그리고 목적지가 start 시점 E-K3S transaction-api Pod IP(k3s 읽기 조회) — 옛 경로 요청
# 저장 범위: pcap·디코딩 원문을 남기지 않는다. tcpdump 출력은 바로 추출기로 흘려 RUN 표식이 있는 요청의
#   식별 필드(시각·출발/도착·seq·메서드·tag·id·Host·X-Real-IP)만 hits.txt에 적고, 표식 없는 요청은 개수만 센다.
#   요청 헤더의 나머지와 본문은 어디에도 저장하지 않는다(커널은 패킷당 앞 300바이트까지만 넘긴다).
# 원본: /var/tmp/edge-cutover-capture-<RUN>/<WIN>/ (hits.txt, counts.txt, tcpdump.err·rc, extract.rc·err, meta.txt, *.ts)
# 이 스크립트는 설정을 바꾸지 않는다(edge01의 k3s는 get 조회만). set -e는 쓰지 않는다.
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
K3S=/usr/local/bin/k3s
# TCP 본문 첫 4바이트가 "GET " 또는 "POST" — 요청의 첫 세그먼트만(응답·이어지는 본문 세그먼트 제외)
REQ_START='(tcp[((tcp[12]&0xf0)>>2):4] = 0x47455420 or tcp[((tcp[12]&0xf0)>>2):4] = 0x504f5354)'
case "$H" in
  k8s-worker01|k8s-worker02|k8s-worker03|edge01) ;;
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
    command -v python3 >/dev/null || { echo "STOP: python3 없음"; exit 3; }
    if test "$H" = edge01; then
      # E-K3S transaction-api Pod IP(읽기 조회). 하나도 없으면 시작하지 않는다.
      PODS=$("$K3S" kubectl get pods -A -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' 2>&1) \
        || { echo "STOP: E-K3S Pod 조회 실패: $PODS"; exit 3; }
      IPS=$(printf '%s\n' "$PODS" | awk '$1 ~ /transaction-api/ && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $2 }' | sort -u)
      test -n "$IPS" || { echo "STOP: E-K3S transaction-api Pod IP 없음"; exit 3; }
      DST=$(printf '%s\n' "$IPS" | awk '{ printf "%sdst host %s", (NR > 1 ? " or " : ""), $1 }')
      FILTER="tcp dst port 8000 and ($DST) and $REQ_START"
    else
      FILTER="tcp dst port 8000 and src host 10.1.93.50 and $REQ_START"
    fi
    mkdir -p "$DIR" || { echo "STOP: $DIR 생성 실패"; exit 3; }
    {
      echo "host=$H run=$RUN win=$WIN secs=$SECS snaplen=$SNAPLEN"
      echo "filter=$FILTER"
      echo "start=$(date '+%F %T %Z') tcpdump=$(tcpdump --version 2>&1 | head -1)"
      echo "stored=hits.txt(표식 요청 식별 필드만) counts.txt(표식 없는 요청 개수) — pcap·원문 저장 안 함"
    } > "$DIR/meta.txt"
    # 추출기: tcpdump -A 출력을 줄 단위로 받아 표식 요청의 식별 필드만 hits.txt에 쓰고, 나머지는 세기만 한다.
    cat > "$DIR/extract.py" <<'PY'
import os, re, sys
d, host, run, win = sys.argv[1:5]
head = re.compile(r'^(\d+\.\d+) (?:\S+ +)??(?:(?:In|Out|M|P|B) +)?IP (\d+\.\d+\.\d+\.\d+)\.(\d+) > (\d+\.\d+\.\d+\.\d+)\.(\d+): .*?seq (\d+)')
req = re.compile(r'(GET|POST) /[^ ?]*\?(?:[^ ]*&)?cutover=cutover-' + re.escape(run) + r'-([A-Za-z0-9]+)-([A-Za-z0-9]+)(?=[& ])[^ ]* (HTTP/1\.[01])')
def hdr(name, text):
    m = re.search(r'(?mi)^' + name + r': *([^\s]+)', text)
    return m.group(1) if m else '-'
out = open(os.path.join(d, 'hits.txt'), 'a')
seen = set()
blocks = hits = other = 0
def flush(cur):
    global blocks, hits, other
    if cur is None:
        return
    m, body = cur
    blocks += 1
    text = '\n'.join(body)
    r = req.search(text)
    if not r:
        other += 1                 # 표식 없는 요청: 개수만 센다(내용 저장 안 함)
        return
    t, sip, sport, dip, dport, seq = m.groups()
    key = (sip, sport, dip, dport, seq, r.group(2), r.group(3))
    if key in seen:                # 같은 패킷이 두 인터페이스에서 잡히거나 재전송된 경우 1번만 센다
        return
    seen.add(key)
    hits += 1
    out.write('HIT host=%s run=%s win=%s tag=%s id=%s t=%s src=%s:%s dst=%s:%s seq=%s method=%s proto=%s hdr_host=%s xri=%s\n' % (
        host, run, win, r.group(2), r.group(3), t, sip, sport, dip, dport, seq, r.group(1), r.group(4),
        hdr('Host', text), hdr('X-Real-IP', text)))
    out.flush()
cur = None
for line in sys.stdin:
    line = line.rstrip('\n')
    m = head.match(line)
    if m:
        flush(cur)
        cur = (m, [])
    elif cur is not None:
        cur[1].append(line)
flush(cur)
out.close()
with open(os.path.join(d, 'counts.txt'), 'w') as f:
    f.write('blocks=%d hits=%d other=%d\n' % (blocks, hits, other))
PY
    : > "$DIR/hits.txt"
    # runner(sh): timeout→tcpdump를 백그라운드로 두고 pid를 남긴 뒤 종료를 기다린다. 출력은 파이프로 추출기에만 간다.
    # -Z root: RHEL tcpdump의 권한 낮추기로 root 전용 폴더에 쓰지 못하는 것을 막는다. -l: 줄 단위 즉시 출력.
    setsid sh -c '
      echo $$ > "$1/runner.pid"
      { timeout "$2" tcpdump -i any -nn -l -tt -S -A -s "$3" -Z root "$4" 2> "$1/tcpdump.err" &
        echo $! > "$1/timeout.pid"; wait $!; echo $? > "$1/tcpdump.rc.tmp"; } |
        python3 -I -u "$1/extract.py" "$1" "$5" "$6" "$7" > /dev/null 2> "$1/extract.err"
      echo $? > "$1/extract.rc"
      date +%s.%N | cut -c1-14 > "$1/stop.ts"
      mv "$1/tcpdump.rc.tmp" "$1/tcpdump.rc"' \
      sh "$DIR" "$SECS" "$SNAPLEN" "$FILTER" "$H" "$RUN" "$WIN" < /dev/null > /dev/null 2>&1 &
    i=0
    while test "$i" -lt 20; do
      if grep -q 'listening on' "$DIR/tcpdump.err" 2>/dev/null; then
        date +%s.%N | cut -c1-14 > "$DIR/ready.ts"
        echo "CAPTURE_READY host=$H run=$RUN win=$WIN ready=$(cat "$DIR/ready.ts") secs=$SECS at=$(date '+%T')"
        echo "filter=$FILTER"
        exit 0
      fi
      test -e "$DIR/tcpdump.rc" && break
      sleep 0.5; i=$((i + 1))
    done
    test -s "$DIR/timeout.pid" && kill -TERM "$(cat "$DIR/timeout.pid")" 2>/dev/null
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
    if running && test -s "$DIR/timeout.pid"; then
      # timeout에 SIGTERM을 주면 tcpdump에 전달되고 tcpdump는 통계를 쓰고 끝난다. 추출기는 입력 끝에서 counts를 쓴다.
      kill -TERM "$(cat "$DIR/timeout.pid")" 2>/dev/null
    fi
    i=0
    while test ! -e "$DIR/tcpdump.rc" && test "$i" -lt 20; do sleep 0.5; i=$((i + 1)); done
    python3 -I - "$DIR" "$H" "$RUN" "$WIN" > "$DIR/report.txt" 2> "$DIR/report.err" <<'PY'
import hashlib, os, re, sys
d, host, run, win = sys.argv[1:5]

def read(name, default=''):
    try:
        return open(os.path.join(d, name), errors='replace').read()
    except OSError:
        return default

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
if read('extract.rc').strip() != '0':
    errs.append('extract_rc=%s' % (read('extract.rc').strip() or 'none'))
counts = dict(kv.split('=', 1) for kv in read('counts.txt').split() if '=' in kv)
if not counts:
    errs.append('no_counts')
hits_text = read('hits.txt', None)
if hits_text is None:
    errs.append('no_hits_file')
    hits_text = ''
hits = [l for l in hits_text.split('\n') if l.startswith('HIT ')]
if counts and counts.get('hits') != str(len(hits)):
    errs.append('hits_mismatch')
for h in hits:
    print(h)
print('CAPTURE host=%s run=%s win=%s err=%d reason=%s ready=%s stop=%s pkts=%s dropped=%s blocks=%s hits=%d other=%s hits_sha256=%s' % (
    host, run, win, len(errs), ','.join(errs) or '-', read('ready.ts').strip(), read('stop.ts').strip(),
    cap.group(1) if cap else '-', drop.group(1) if drop else '-', counts.get('blocks', '-'), len(hits),
    counts.get('other', '-'), hashlib.sha256(hits_text.encode()).hexdigest()))
PY
    PRC=$?
    cat "$DIR/report.txt"
    cat "$DIR/report.err" >&2
    test "$PRC" = 0 || { echo "CAPTURE host=$H run=$RUN win=$WIN err=1 reason=report_rc_$PRC"; exit 4; }
    grep -q '^CAPTURE .* err=0 ' "$DIR/report.txt" || exit 4
    ;;
esac
