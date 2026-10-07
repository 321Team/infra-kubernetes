#!/usr/bin/env bash
# Edge Cutover(#19) 전후 control01 읽기 전용 점검. 클러스터·정책을 바꾸지 않는다(get과 Prometheus 조회만).
# 사용: `sudo bash control01-cutover-check.sh <pre|post> <RUN>`
#   - transaction-api / transaction-api-nodeport Service(nodePort 30080, externalTrafficPolicy Local)
#   - Worker별 transaction-api-nodeport Ready·non-terminating local endpoint(기대 1·1·1)
#   - GNP nodeport-30080-allow(.50/32 → 30080 허용, preDNAT/applyOnForward)
#   - 전체 Service의 NodePort 목록(32085 등 metrics NodePort가 없어야 함)
#   - MON: Prometheus count(up)·up==0 (port-forward 19093, 끝나면 자기 프로세스만 종료)
# 출력은 /root/edge-cutover-<RUN>/에 남는다. 값이 있는 Secret은 읽지 않는다. set -e는 쓰지 않는다.
set -uo pipefail

PHASE=${1:-}
RUN=${2:-}
case "$PHASE" in pre|post) ;; *) echo "usage: sudo bash $0 pre|post <RUN=yyyymmddTHHMM>" >&2; exit 2 ;; esac
case "$RUN" in
  [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9]) ;;
  *) echo "usage: sudo bash $0 pre|post <RUN=yyyymmddTHHMM>" >&2; exit 2 ;;
esac
test "$(id -u)" = 0 || { echo "STOP: sudo bash로 실행" >&2; exit 2; }
test "$(hostname -s)" = k8s-control01 || { echo "STOP: k8s-control01이 아님" >&2; exit 2; }

K=(kubectl --kubeconfig=/etc/kubernetes/admin.conf --request-timeout=15s)
PFPORT=19093
DIR=/root/edge-cutover-$RUN
umask 077
mkdir -p "$DIR" || { echo "STOP: $DIR 생성 실패" >&2; exit 2; }
LOG=$DIR/control01-$PHASE-$(date +%H%M%S).log
exec > >(tee -a "$LOG") 2>&1
RC_BAD=0
run() { "$@"; local rc=$?; test "$rc" = 0 || { echo "   (rc=$rc)"; RC_BAD=$((RC_BAD + 1)); }; return 0; }

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
q() { curl -s --max-time 5 "http://127.0.0.1:$PFPORT/api/v1/query" --data-urlencode "query=$1" | python3 -c '
import json, sys
d = json.load(sys.stdin)
r = d.get("data", {}).get("result", [])
print(r[0]["value"][1] if r else "0")' 2>/dev/null || echo ERR; }
echo "   count(up)=$(q 'count(up)') count(up==1)=$(q 'count(up == 1)') count(up==0)=$(q 'count(up == 0)') (기대 23/23/0)"
kill "$PF" 2>/dev/null; wait "$PF" 2>/dev/null
echo "-- 조회 실패 수=$RC_BAD"
echo "RESULT control01-$PHASE=DONE $(date '+%T') (판정은 위 기대값과 대조)"
