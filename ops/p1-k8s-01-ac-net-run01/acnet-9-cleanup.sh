#!/bin/bash
# ac-net-run01 cleanup (B3): delete ONLY the namespace this run created, bound to its recorded UID. Run on control01 as root.
set -u
R=/root/ac-net-run01; NS=ac-net-run01; K="kubectl --kubeconfig /etc/kubernetes/admin.conf"
stop(){ echo "STOP: $1 (nothing deleted)"; exit 1; }
{
echo "# AC-NET-RUN01 CLEANUP"; date -Is; hostname
[ -s $R/ns-uid.txt ] || stop "no recorded namespace UID ($R/ns-uid.txt)"
REC=$(cat $R/ns-uid.txt); CUR=$($K get ns $NS -o jsonpath='{.metadata.uid}' 2>&1)
echo "recorded_uid=$REC current_uid=$CUR"
[ "$REC" = "$CUR" ] || stop "namespace UID mismatch"
LBL=$($K get ns $NS -o jsonpath='{.metadata.labels.fds\.test/run}'); [ "$LBL" = "ac-net-run01" ] || stop "label mismatch ($LBL)"
echo "## object set (expected: pod/probe-a,b,c + service/probe-a-svc,b-svc,c-svc)"
GOT=$($K -n $NS get pods,svc -o name | sort | tr '\n' ' ' | sed 's/ *$//'); echo "got: $GOT"
WANT="pod/probe-a pod/probe-b pod/probe-c service/probe-a-svc service/probe-b-svc service/probe-c-svc"
[ "$GOT" = "$WANT" ] || stop "unexpected pod/service set"
echo "## other user resources (excluding defaults)"
ALL=$($K -n $NS get deploy,sts,ds,job,cronjob,cm,secret,sa,role,rolebinding,networkpolicy,pvc,ingress -o name 2>&1); LRC=$?
[ "$LRC" = "0" ] || { echo "$ALL"; stop "resource listing failed (rc=$LRC); cannot confirm the namespace is clean"; }
OTHER=$(echo "$ALL" | grep -v -E '^configmap/kube-root-ca.crt$|^serviceaccount/default$' || true)
[ -z "$OTHER" ] || { echo "$OTHER"; stop "unexpected resources in namespace"; }
echo "## delete with UID precondition (no gap between the check and the delete)"
echo "{\"kind\":\"DeleteOptions\",\"apiVersion\":\"v1\",\"preconditions\":{\"uid\":\"$REC\"}}" | $K delete --raw /api/v1/namespaces/$NS -f - ; DRC=$?
echo "delete_rc=$DRC"; [ "$DRC" = "0" ] || stop "precondition delete refused (rc=$DRC)"
for i in $(seq 1 24); do $K get ns $NS >/dev/null 2>&1 || break; sleep 5; done
echo "## after"; $K get ns $NS 2>&1 | head -2
echo "## leftover with label (expect none)"; $K get pods,svc -A -l fds.test/run=ac-net-run01 2>&1 | head -3
echo "## nodes / readyz / fds-app"; $K get nodes --no-headers | awk '{print $1,$2}'; $K get --raw='/readyz?verbose' | tail -1; $K -n fds-app get pods --no-headers | awk '{print $1,$3,$4}'
date -Is
} 2>&1 | tee $R/9-cleanup.txt
