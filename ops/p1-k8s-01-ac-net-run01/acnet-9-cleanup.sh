#!/bin/bash
# ac-net-run01 cleanup (B3): delete ONLY the namespace this run created, bound to its recorded UID. Run on control01 as root.
# PMO/QA review (GWONUK): the failure code of the whole block is returned to the caller (no tee-masked rc=0), and
# cleanup is "complete" ONLY after an explicit NotFound for the namespace AND a successful zero-result leftover query.
# API errors, timeouts and a namespace still Terminating after the wait exit non-zero with CLEANUP_INCOMPLETE.
# B57-NEW2: a PARTIAL creation (empty namespace, or only some of the 6 fixtures) is cleanable: the objects present must be a
# SUBSET (the empty set included) of the 6 allowed fixtures. Anything else (a foreign object, another namespace) is refused.
# AC_NET_R / AC_NET_K are test hooks (selftest uses a mock kubectl); production runs use the defaults.
set -u
R=${AC_NET_R:-/root/ac-net-run01}; NS=ac-net-run01
K=${AC_NET_K:-"kubectl --kubeconfig /etc/kubernetes/admin.conf"}
WAIT_TRIES=${AC_NET_WAIT_TRIES:-24}
mkdir -p "$R"
stop(){ echo "STOP: $1 (nothing deleted)"; echo "CLEANUP_INCOMPLETE"; exit 1; }
incomplete(){ echo "CLEANUP_INCOMPLETE: $1"; exit 1; }
ALLOWED="pod/probe-a pod/probe-b pod/probe-c service/probe-a-svc service/probe-b-svc service/probe-c-svc"
{
echo "# AC-NET-RUN01 CLEANUP"; date -Is; hostname
[ -s "$R/ns-uid.txt" ] || stop "no recorded namespace UID ($R/ns-uid.txt)"
REC=$(cat "$R/ns-uid.txt")
CURO=$($K get ns $NS -o jsonpath='{.metadata.uid}' 2>&1); CRC=$?
echo "recorded_uid=$REC current_uid=$CURO get_rc=$CRC"
[ "$CRC" = "0" ] || stop "cannot read the namespace UID (rc=$CRC: $CURO)"
[ "$REC" = "$CURO" ] || stop "namespace UID mismatch"
LBL=$($K get ns $NS -o jsonpath='{.metadata.labels.fds\.test/run}' 2>&1); LRC=$?
[ "$LRC" = "0" ] && [ "$LBL" = "ac-net-run01" ] || stop "label check failed (rc=$LRC, value=$LBL)"
echo "## object set (must be a SUBSET of: $ALLOWED; the empty set is allowed for a partial creation)"
GOTO=$($K -n $NS get pods,svc -o name 2>"$R/get-set.err"); GRC=$?
[ "$GRC" = "0" ] || { cat "$R/get-set.err"; stop "cannot list pods/services (rc=$GRC)"; }
GOT=$(echo "$GOTO" | sed '/^$/d' | sort | tr '\n' ' ' | sed 's/ *$//'); echo "got: ${GOT:-<empty>}"
for obj in $GOT; do
  case " $ALLOWED " in
    *" $obj "*) ;;
    *) stop "unexpected object in namespace: $obj";;
  esac
done
echo "## other user resources (excluding defaults)"
ALL=$($K -n $NS get deploy,sts,ds,job,cronjob,cm,secret,sa,role,rolebinding,networkpolicy,pvc,ingress -o name 2>"$R/get-other.err"); ARC=$?
[ "$ARC" = "0" ] || { cat "$R/get-other.err"; stop "resource listing failed (rc=$ARC); cannot confirm the namespace is clean"; }
OTHER=$(echo "$ALL" | sed '/^$/d' | grep -v -E '^configmap/kube-root-ca.crt$|^serviceaccount/default$' || true)
[ -z "$OTHER" ] || { echo "$OTHER"; stop "unexpected resources in namespace"; }
echo "## delete with UID precondition (no gap between the check and the delete)"
echo "{\"kind\":\"DeleteOptions\",\"apiVersion\":\"v1\",\"preconditions\":{\"uid\":\"$REC\"}}" | $K delete --raw /api/v1/namespaces/$NS -f - ; DRC=$?
echo "delete_rc=$DRC"; [ "$DRC" = "0" ] || stop "precondition delete refused (rc=$DRC)"
echo "## wait for explicit NotFound (up to $WAIT_TRIES tries)"
GONE=0
for i in $(seq 1 "$WAIT_TRIES"); do
  OUT=$($K get ns $NS 2>&1); RC=$?
  if [ "$RC" = "0" ]; then sleep 5; continue; fi
  case "$OUT" in
    *NotFound*) GONE=1; break;;
    *) incomplete "namespace lookup failed with something other than NotFound (rc=$RC: $OUT)";;
  esac
done
[ "$GONE" = "1" ] || incomplete "namespace still present (Terminating?) after the wait"
echo "namespace NotFound confirmed"
echo "## leftover with label (query must succeed and return nothing)"
LEFT=$($K get pods,svc -A -l fds.test/run=ac-net-run01 -o name 2>/dev/null); LFRC=$?
[ "$LFRC" = "0" ] || incomplete "leftover query failed (rc=$LFRC)"
[ -z "$LEFT" ] || { echo "$LEFT"; incomplete "leftover resources still exist"; }
echo "leftover=0"
echo "## nodes / readyz / fds-app (informational)"; $K get nodes --no-headers | awk '{print $1,$2}'; $K get --raw='/readyz?verbose' | tail -1; $K -n fds-app get pods --no-headers | awk '{print $1,$3,$4}'
date -Is
echo "CLEANUP_COMPLETE"
} 2>&1 | tee "$R/9-cleanup.txt"
exit "${PIPESTATUS[0]}"
