#!/bin/bash
# ac-net-run01 step 1+2: baseline record + create fixtures. Run on control01 as root (manifest copied from the reviewed checkout).
# Usage: bash acnet-1-gate-create.sh /root/ac-net-run01/ac-net-run01.yaml
# PMO/QA review (GWONUK, P1-1): the Namespace is created CREATE-ONLY. A lookup that times out or is Forbidden is NOT treated as
# "absent", an existing namespace is never touched, and only the UID returned by THIS run's successful create is recorded.
# AC_NET_R / AC_NET_K are test hooks (selftest uses a mock kubectl); production runs use the defaults.
set -u
MAN=${1:?manifest path required}
R=${AC_NET_R:-/root/ac-net-run01}; NS=ac-net-run01
K=${AC_NET_K:-"kubectl --kubeconfig /etc/kubernetes/admin.conf"}
mkdir -p "$R"; chmod 700 "$R"
stop(){ echo "STOP: $1"; exit 1; }
# split: document 1 = Namespace only, the rest = Pods/Services
# Only the FIRST document boundary is consumed; every later `---` is kept so the fixtures stay 6 separate documents (B57-NEW1).
awk -v ns="$R/ns.yaml" -v rest="$R/rest.yaml" 'BEGIN{d=1; printf "" > ns; printf "" > rest} /^---[ \t]*$/{d++; if(d>2) print "---" > rest; next} {print > (d==1 ? ns : rest)}' "$MAN" || stop "cannot split manifest"
NNS=$(grep -c '^kind: Namespace$' "$R/ns.yaml"); NPOD=$(grep -c '^kind: Pod$' "$R/rest.yaml"); NSVC=$(grep -c '^kind: Service$' "$R/rest.yaml"); NSEP=$(grep -c '^---[ \t]*$' "$R/rest.yaml"); NNS2=$(grep -c '^kind: Namespace$' "$R/rest.yaml")
[ "$NNS" = "1" ] && [ "$NNS2" = "0" ] && [ "$NPOD" = "3" ] && [ "$NSVC" = "3" ] && [ "$NSEP" = "5" ] || stop "manifest split check failed (namespace=$NNS extra_namespace=$NNS2 pods=$NPOD services=$NSVC separators=$NSEP; expected 1/0/3/3/5)"
grep -q '^  name: ac-net-run01$' "$R/ns.yaml" || stop "Namespace name is not ac-net-run01"
for nm in probe-a probe-b probe-c probe-a-svc probe-b-svc probe-c-svc; do
  grep -q "^  name: $nm\$" "$R/rest.yaml" || stop "fixture $nm is missing after the split"
done
{
echo "# AC-NET-RUN01 GATE + CREATE"; date -Is; hostname; sha256sum "$MAN" "$R/ns.yaml" "$R/rest.yaml"
echo "## nodes (bootID)"; $K get nodes -o custom-columns='NAME:.metadata.name,STATUS:.status.conditions[-1].type,BOOTID:.status.nodeInfo.bootID,UNSCHED:.spec.unschedulable'
echo "## readyz"; $K get --raw='/readyz?verbose' | tail -1
echo "## HEP/GNP/Felix"
for r in hostendpoints globalnetworkpolicies felixconfigurations; do
  $K get $r.crd.projectcalico.org -o custom-columns='NAME:.metadata.name,UID:.metadata.uid,GEN:.metadata.generation,RV:.metadata.resourceVersion'
done
echo "## fds-app pods (restart counts)"; $K -n fds-app get pods -o wide
} 2>&1 | tee "$R/1-gate-baseline.txt"
echo "## dry-run namespace (create-only)"
$K create --dry-run=server -f "$R/ns.yaml" || stop "namespace dry-run failed (AlreadyExists, timeout or Forbidden); nothing created or recorded"
echo "## create namespace (create-only)"
NSOUT=$($K create -f "$R/ns.yaml" -o jsonpath='{.metadata.uid}' 2>&1); RC=$?
echo "create_ns_rc=$RC response=$NSOUT" | tee "$R/2-apply.txt"
[ "$RC" = "0" ] || stop "namespace create failed (AlreadyExists, timeout or Forbidden); nothing created or recorded"
case "$NSOUT" in
  ????????-????-????-????-????????????) ;;
  *) stop "namespace create returned an unexpected UID format ($NSOUT); not recording it";;
esac
echo "$NSOUT" > "$R/ns-uid.txt"; echo "NS_UID=$NSOUT (from this run's create response)" | tee -a "$R/2-apply.txt"
echo "## dry-run fixtures"
$K create --dry-run=server -f "$R/rest.yaml" 2>&1 | tee -a "$R/2-apply.txt"
[ "${PIPESTATUS[0]}" = "0" ] || stop "fixtures dry-run failed. Namespace $NS WAS created (UID recorded): run acnet-9-cleanup.sh, then report to #10"
echo "## create fixtures (create-only)"
$K create -f "$R/rest.yaml" 2>&1 | tee -a "$R/2-apply.txt"
[ "${PIPESTATUS[0]}" = "0" ] || stop "fixtures create failed (partial creation possible). Namespace $NS WAS created (UID recorded): run acnet-9-cleanup.sh, then report to #10"
echo "## wait pods Ready (120s)"
$K -n $NS wait --for=condition=Ready pod --all --timeout=120s 2>&1 | tee -a "$R/2-apply.txt"
WAIT_RC=${PIPESTATUS[0]}; echo "WAIT_RC=$WAIT_RC" | tee -a "$R/2-apply.txt"
if [ "$WAIT_RC" != "0" ]; then
  echo "STOP: test Pods are not all Ready. Do NOT create a pull secret or pull the image. Record pod state, then run acnet-9-cleanup.sh and report to #10."
  $K -n $NS get pods -o wide | tee -a "$R/2-apply.txt"; $K -n $NS describe pods 2>&1 | grep -E 'Name:|State:|Reason:|Message:|Events:' | head -40 | tee -a "$R/2-apply.txt"
  exit 1
fi
echo "## object set"
SET=$($K -n $NS get pods,svc -o name 2>&1); SRC=$?
[ "$SRC" = "0" ] || { echo "$SET"; stop "cannot list the created objects (rc=$SRC)"; }
echo "$SET" | sort | tee -a "$R/2-apply.txt"
$K -n $NS get pods,svc -o wide | tee -a "$R/2-apply.txt"
date -Is | tee -a "$R/2-apply.txt"
echo "CREATE_COMPLETE" | tee -a "$R/2-apply.txt"
