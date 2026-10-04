#!/bin/bash
# ac-net-run01 step 0 (READ-ONLY): Fresh Gate precheck. Run on ansible01 from the reviewed checkout.
# Verifies: test namespace absent, probe image digest cached on all 3 workers (exact), nodes Ready, readyz.
# Usage: bash acnet-0-precheck.sh [path/to/manifests/ac-net-run01.yaml]
set -u
HERE=$(cd "$(dirname "$0")" && pwd); MAN=${1:-$HERE/manifests/ac-net-run01.yaml}
source ~/.config/ansible/p1-ans-01.env && export ANSIBLE_PRIVATE_KEY_FILE
cd ~/infra-ansible || exit 1
IMAGE=$(grep -m1 -E '^\s+image: ' "$MAN" | awk '{print $2}'); DIGEST=${IMAGE#*@}
KC="kubectl --kubeconfig /etc/kubernetes/admin.conf"
fail=0
{
echo "# AC-NET-RUN01 PRECHECK (read-only)"; date -Is; hostname
echo "manifest=$MAN"; sha256sum "$MAN"
echo "IMAGE=$IMAGE"; echo "DIGEST=$DIGEST"
case "$IMAGE" in *@sha256:*) ;; *) echo "FAIL: image is not digest-pinned"; fail=1;; esac
echo "## namespace must be NotFound"
ns=$(ansible -i inventory/hosts.yml k8s-control01 -b -m shell -a "$KC get ns ac-net-run01 2>&1" 2>&1)
echo "$ns" | tail -2; echo "$ns" | grep -q "NotFound" || { echo "FAIL: namespace already exists"; fail=1; }
echo "## nodes / readyz"
ansible -i inventory/hosts.yml k8s-control01 -b -m shell -a "$KC get nodes --no-headers | awk '{print \$1,\$2}'; $KC get --raw='/readyz?verbose' | tail -1" 2>&1 | tail -8
echo "## digest cached on each worker (exact match required)"
for w in k8s-worker01 k8s-worker02 k8s-worker03; do
  r=$(ansible -i inventory/hosts.yml $w -b -m shell -a "crictl images --digests --no-trunc 2>&1 | grep -c '$DIGEST'" 2>&1 | tail -1)
  echo "$w digest_rows=$r"; [ "${r:-0}" -ge 1 ] 2>/dev/null || { echo "FAIL: $w does not have $DIGEST"; fail=1; }
  # kubelet resolves the exact reference; the grep above only shows the digest exists somewhere (supreme33-kr review point 1)
  i=$(ansible -i inventory/hosts.yml $w -b -m shell -a "crictl inspecti '$IMAGE' >/dev/null 2>&1; echo inspecti_rc=\$?" 2>&1 | grep -o 'inspecti_rc=[0-9]*' | head -1)
  echo "$w $i"; [ "$i" = "inspecti_rc=0" ] || { echo "FAIL: $w cannot resolve $IMAGE (crictl inspecti)"; fail=1; }
done
[ "$fail" = 0 ] && echo "PRECHECK_PASS" || echo "PRECHECK_FAIL (do NOT start; do not pull or create a secret to work around)"
} 2>&1 | tee ~/ac-net-run01-precheck.txt
grep -q PRECHECK_PASS ~/ac-net-run01-precheck.txt
