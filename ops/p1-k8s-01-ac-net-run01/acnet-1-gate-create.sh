#!/bin/bash
# ac-net-run01 step 1+2: baseline record + create fixtures. Run on control01 as root (manifest copied from the reviewed checkout).
# Usage: bash acnet-1-gate-create.sh /root/ac-net-run01/ac-net-run01.yaml
set -u
MAN=${1:?manifest path required}; R=/root/ac-net-run01; NS=ac-net-run01; K="kubectl --kubeconfig /etc/kubernetes/admin.conf"
mkdir -p $R; chmod 700 $R
{
echo "# AC-NET-RUN01 GATE + CREATE"; date -Is; hostname; sha256sum "$MAN"
echo "## nodes (bootID)"; $K get nodes -o custom-columns='NAME:.metadata.name,STATUS:.status.conditions[-1].type,BOOTID:.status.nodeInfo.bootID,UNSCHED:.spec.unschedulable'
echo "## readyz"; $K get --raw='/readyz?verbose' | tail -1
echo "## HEP/GNP/Felix"
for r in hostendpoints globalnetworkpolicies felixconfigurations; do
  $K get $r.crd.projectcalico.org -o custom-columns='NAME:.metadata.name,UID:.metadata.uid,GEN:.metadata.generation,RV:.metadata.resourceVersion'
done
echo "## fds-app pods (restart counts)"; $K -n fds-app get pods -o wide
echo "## namespace must be absent"; $K get ns $NS 2>&1 | head -2
} 2>&1 | tee $R/1-gate-baseline.txt
if $K get ns $NS >/dev/null 2>&1; then echo "STOP: namespace $NS already exists"; exit 1; fi
echo "## dry-run"; $K apply --dry-run=server -f "$MAN" || { echo "STOP: dry-run failed"; exit 1; }
echo "## apply"; $K apply -f "$MAN" | tee $R/2-apply.txt
UID_NS=$($K get ns $NS -o jsonpath='{.metadata.uid}'); echo "$UID_NS" > $R/ns-uid.txt; echo "NS_UID=$UID_NS" | tee -a $R/2-apply.txt
echo "## wait pods Ready (120s)"; $K -n $NS wait --for=condition=Ready pod --all --timeout=120s 2>&1 | tee -a $R/2-apply.txt
echo "## object set"; $K -n $NS get pods,svc -o name | sort | tee -a $R/2-apply.txt
$K -n $NS get pods,svc -o wide | tee -a $R/2-apply.txt
date -Is | tee -a $R/2-apply.txt
