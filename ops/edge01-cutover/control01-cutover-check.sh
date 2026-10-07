#!/usr/bin/env bash
# Edge Cutover(#19) 전후 control01 읽기 전용 점검. 클러스터·정책을 바꾸지 않는다(get·logs와 Prometheus 조회만).
# 사용:
#   sudo bash control01-cutover-check.sh pre|post <RUN>
#     - transaction-api / transaction-api-nodeport Service(nodePort 30080, externalTrafficPolicy Local)
#     - Worker별 transaction-api-nodeport Ready·non-terminating local endpoint(기대 1·1·1)
#     - GNP nodeport-30080-allow(.50/32 → 30080 허용, preDNAT/applyOnForward)
#     - 전체 Service의 NodePort 목록(32085 등 metrics NodePort가 없어야 함)
#     - MON: Prometheus count(up)·up==0 (port-forward 19093, 끝나면 자기 프로세스만 종료)
#   sudo bash control01-cutover-check.sh trace <RUN> <TAG>
#     - fds-app transaction-api Pod 로그(Pod별 조회, rc·stderr 분리, 회차별 새 폴더)에서 edge01이 보낸
#       표식 `cutover=cutover-<RUN>-<TAG>-<번호>` 줄을 찾아 번호별 횟수·Pod·노드·client 주소를 출력하고
#       TAG 종류별 기대값과 비교한다(apply: 1~9·smoke 각 1번 / check…: w105·w107·w108이 각 Worker에서 1번 /
#       rollback…: 0건).
# 출력은 /root/edge-cutover-<RUN>/에 남는다. 값이 있는 Secret은 읽지 않는다. set -e는 쓰지 않는다.
set -uo pipefail

PHASE=${1:-}
RUN=${2:-}
TAG=${3:-}
usage() { echo "usage: sudo bash $0 pre|post <RUN> | trace <RUN> <TAG>" >&2; exit 2; }
case "$PHASE" in pre|post) ;; trace) case "$TAG" in ''|*[^A-Za-z0-9]*) usage ;; esac ;; *) usage ;; esac
case "$RUN" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9]) ;;
  *) usage ;;
esac
test "$(id -u)" = 0 || { echo "STOP: sudo bash로 실행" >&2; exit 2; }
test "$(hostname -s)" = k8s-control01 || { echo "STOP: k8s-control01이 아님" >&2; exit 2; }

K=(kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=15s)
PFPORT=19093
DIR=/root/edge-cutover-$RUN
STAMP=$(date +%H%M%S)
umask 077
mkdir -p "$DIR" || { echo "STOP: $DIR 생성 실패" >&2; exit 2; }
LOG=$DIR/control01-$PHASE${TAG:+-$TAG}-$STAMP.log
exec > >(tee -a "$LOG") 2>&1
RC_BAD=0
run() { "$@"; local rc=$?; test "$rc" = 0 || { echo "   (rc=$rc)"; RC_BAD=$((RC_BAD + 1)); }; return 0; }

if test "$PHASE" = trace; then
  echo "== $(date '+%F %T %Z') control01 trace run=$RUN tag=$TAG"
  T=$DIR/k8s-$TAG-$STAMP
  mkdir -p "$T"
  "${K[@]}" -n fds-app get pods -l app=transaction-api \
    -o jsonpath='{range .items[*]}{.metadata.name} {.spec.nodeName}{"\n"}{end}' > "$T/pods.txt" 2> "$T/pods.err"
  echo "$?" > "$T/pods.rc"
  ERRS=0
  test "$(cat "$T/pods.rc")" = 0 || ERRS=$((ERRS + 1))
  test -s "$T/pods.txt" || ERRS=$((ERRS + 1))
  while read -r pod node; do
    test -n "$pod" || continue
    "${K[@]}" -n fds-app logs "$pod" -c transaction-api --since=60m > "$T/$pod@$node.log" 2> "$T/$pod.err"
    rc=$?
    echo "$rc" > "$T/$pod.rc"
    test "$rc" = 0 || ERRS=$((ERRS + 1))
  done < "$T/pods.txt"
  python3 -I - "$T" "cutover-$RUN-$TAG-" "$TAG" "$ERRS" <<'PY'
import glob, os, re, sys
d, prefix, tag, errs = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
pat = re.compile(r'(\d+\.\d+\.\d+\.\d+):\d+ - "(?:GET|POST) [^ "?]*\?(?:[^ "]*&)?cutover=' + re.escape(prefix) + r'([A-Za-z0-9]+)[& "]')
hits = {}
clients = set()
for f in sorted(glob.glob(os.path.join(d, '*@*.log'))):
    pod, node = os.path.basename(f)[:-4].split('@', 1)
    for line in open(f, errors='replace'):
        m = pat.search(line)
        if m:
            hits.setdefault(m.group(2), []).append(node)
            clients.add(m.group(1))
            print('   %s pod=%s node=%s client=%s' % (m.group(2), pod, node, m.group(1)))
ids = ','.join('%s:%d' % (k, len(v)) for k, v in sorted(hits.items()))
print('   ids=%s clients=%s' % (ids, ','.join(sorted(clients))))
if errs:
    verdict = 'ERR'                      # 조회 실패는 판정 불가(0건 정상으로 읽지 않는다)
elif tag == 'apply':
    want = {str(i) for i in range(1, 10)} | {'smoke'}
    ok = set(hits) == want and all(len(v) == 1 for v in hits.values())
    verdict = 'TRACED' if ok else 'UNCONFIRMED'
elif tag.startswith('check'):
    want = {'w105': 'k8s-worker01', 'w107': 'k8s-worker02', 'w108': 'k8s-worker03'}
    ok = set(hits) == set(want) and all(hits[k] == [n] for k, n in want.items())
    verdict = 'TRACE_READY' if ok else 'TRACE_NOT_READY'
elif tag.startswith('rollback'):
    verdict = 'NONE_OK' if not hits else 'UNEXPECTED_HITS'
else:
    verdict = 'COLLECTED'
print('TRACE k8s tag=%s err=%d verdict=%s source_50=%s' % (
    tag, errs, verdict, 'yes' if clients == {'10.1.93.50'} else ('none' if not clients else 'no')))
PY
  echo "RESULT control01-trace=$(sed -n 's/^.*verdict=\([A-Z_]*\).*/\1/p' "$LOG" | tail -1) $(date '+%T') 원본 $T"
  exit 0
fi

echo "== $(date '+%F %T %Z') control01 cutover check phase=$PHASE run=$RUN"
echo "-- Service"
run "${K[@]}" -n fds-app get svc transaction-api transaction-api-nodeport -o wide
run "${K[@]}" -n fds-app get svc transaction-api-nodeport -o jsonpath='type={.spec.type} etp={.spec.externalTrafficPolicy} ports={range .spec.ports[*]}{.port}->{.targetPort}:{.nodePort} {end}selector={.spec.selector}{"\n"}'
echo "-- Worker별 Ready·non-terminating local endpoint(transaction-api-nodeport)"
run "${K[@]}" -n fds-app get endpointslice -l kubernetes.io/service-name=transaction-api-nodeport \
  -o go-template='{{range .items}}{{range .endpoints}}{{.nodeName}} {{index .addresses 0}} ready={{.conditions.ready}} terminating={{.conditions.terminating}}{{"\n"}}{{end}}{{end}}'
echo "-- transaction-api Pod 배치"
run "${K[@]}" -n fds-app get pods -l app=transaction-api -o wide
echo "-- GNP nodeport-30080-allow"
run "${K[@]}" get globalnetworkpolicy.crd.projectcalico.org nodeport-30080-allow \
  -o jsonpath='order={.spec.order} selector={.spec.selector} preDNAT={.spec.preDNAT} applyOnForward={.spec.applyOnForward}{"\n"}ingress={.spec.ingress}{"\n"}'
echo "-- 전체 NodePort/LoadBalancer Service(32085 등 metrics NodePort가 없어야 함)"
run "${K[@]}" get svc -A -o go-template='{{range .items}}{{if or (eq .spec.type "NodePort") (eq .spec.type "LoadBalancer")}}{{.metadata.namespace}}/{{.metadata.name}} {{.spec.type}}{{range .spec.ports}} {{.port}}:{{.nodePort}}{{end}}{{"\n"}}{{end}}{{end}}'
N32085=$("${K[@]}" get svc -A -o jsonpath='{range .items[*]}{range .spec.ports[*]}{.nodePort}{"\n"}{end}{end}' | grep -c '^32085$')
echo "-- nodePort 32085 사용 Service 수=$N32085 (기대 0)"

echo "-- MON (Prometheus port-forward 127.0.0.1:$PFPORT)"
"${K[@]}" -n monitoring-api port-forward svc/prometheus "$PFPORT":9090 >/dev/null 2>&1 &
PF=$!
sleep 3
kill -0 "$PF" 2>/dev/null || { echo "   port-forward 실패 → MON 판정 불가"; RC_BAD=$((RC_BAD + 1)); }
# ERR=조회 실패(판정 불가), EMPTY=조회 성공·결과 없음. count(up == 0)의 EMPTY만 '0개'로 읽는다.
q() { curl -s --max-time 5 "http://127.0.0.1:$PFPORT/api/v1/query" --data-urlencode "query=$1" | python3 -c '
import json, sys
d = json.load(sys.stdin)
if d.get("status") == "success":
    r = d["data"]["result"]
    print(r[0]["value"][1] if r else "EMPTY")
else:
    print("ERR")' 2>/dev/null || echo ERR; }
echo "   count(up)=$(q 'count(up)') count(up==1)=$(q 'count(up == 1)') count(up==0)=$(q 'count(up == 0)') (기대 23/23/EMPTY 또는 0)"
kill "$PF" 2>/dev/null; wait "$PF" 2>/dev/null
echo "-- 조회 실패 수=$RC_BAD (0이 아니면 해당 항목은 판정 불가)"
echo "RESULT control01-$PHASE=COLLECTED $(date '+%T') — 수집 완료이며 C0/C2 판정이 아니다. README 판정표에 사람이 기록한다"
